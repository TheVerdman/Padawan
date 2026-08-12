from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime

from padawan.atlas.contracts import (
    AtlasTrialRequest,
    AtlasTrialResult,
    FailureAssignment,
    FailureCluster,
    FailureOrigin,
    OntologyManifest,
    ReviewStatus,
    TrialStatus,
)
from padawan.models.hashing import sha256_digest

_FAILURE_STATUSES = {
    TrialStatus.VERIFIED_FAILURE,
    TrialStatus.PARTIAL,
    TrialStatus.MALFORMED,
    TrialStatus.TIMEOUT,
    TrialStatus.INFRASTRUCTURE_FAILURE,
    TrialStatus.CONTAMINATED,
}


def propose_failure_clusters(
    *,
    campaign_digest: str,
    ontology: OntologyManifest,
    results: Sequence[AtlasTrialResult],
    requests: Mapping[str, AtlasTrialRequest],
    proposed_by: str,
    created_at: datetime,
) -> tuple[FailureCluster, ...]:
    """Propose deterministic, review-required clusters without converting labels to truth."""

    nodes = {node.node_id: node for node in ontology.nodes}
    grouped: dict[tuple[str, tuple[str, ...]], list[AtlasTrialResult]] = defaultdict(list)
    for result in results:
        if result.status not in _FAILURE_STATUSES:
            continue
        origin = (result.failure_origin or FailureOrigin.UNKNOWN).value
        codes = tuple(sorted(set(result.failure_codes))) or (f"{origin}.unclassified",)
        grouped[(origin, codes)].append(result)
    clusters: list[FailureCluster] = []
    for (origin_value, codes), members in sorted(grouped.items()):
        ordered = sorted(members, key=lambda value: value.result_digest)
        member_digests = tuple(result.result_digest for result in ordered)
        node_ids = _node_candidates(
            origin=FailureOrigin(origin_value),
            codes=codes,
            nodes=nodes,
        )
        assignments = tuple(
            FailureAssignment(
                node_id=node_id,
                confidence=_assignment_confidence(node_id=node_id, codes=codes),
                evidence_result_digests=member_digests,
                proposed_by=proposed_by,
                automated=True,
            )
            for node_id in node_ids
        )
        reproducibility, stability = _reproducibility(
            members=ordered,
            requests=requests,
        )
        suspected_axes = tuple(
            sorted(
                {axis for node_id in node_ids for axis in nodes[node_id].changed_axes},
                key=lambda axis: axis.value,
            )
        )
        cluster_key = sha256_digest(
            {
                "campaign_digest": campaign_digest,
                "origin": origin_value,
                "codes": codes,
                "members": member_digests,
                "ontology": ontology.manifest_digest,
            }
        )
        provisional = FailureCluster.model_construct(
            cluster_id=f"failure-cluster-{cluster_key[7:31]}",
            campaign_digest=campaign_digest,
            ontology_digest=ontology.manifest_digest,
            title=f"{origin_value}: {', '.join(codes)}",
            assignments=assignments,
            member_result_digests=member_digests,
            exemplar_result_digests=member_digests[: min(3, len(member_digests))],
            reproducibility=reproducibility,
            stability=stability,
            severity=_severity(ordered),
            suspected_changed_axes=suspected_axes,
            status=ReviewStatus.PROPOSED,
            reviewed_by=None,
            review_reason=None,
            probe_set_digest=None,
            cluster_digest=sha256_digest("pending"),
            created_at=created_at,
        )
        identity = provisional.model_dump(mode="json", exclude={"cluster_digest", "created_at"})
        clusters.append(
            FailureCluster(
                **{
                    **provisional.model_dump(mode="python"),
                    "cluster_digest": sha256_digest(identity),
                }
            )
        )
    return tuple(clusters)


def review_failure_cluster(
    *,
    proposal: FailureCluster,
    ontology: OntologyManifest,
    admitted_node_ids: tuple[str, ...],
    reviewer: str,
    reason: str,
    admitted: bool,
    created_at: datetime,
    probe_set_digest: str | None = None,
) -> FailureCluster:
    """Create a new immutable reviewed cluster; the automated proposal remains unchanged."""

    if proposal.status != ReviewStatus.PROPOSED:
        raise ValueError("failure-cluster review requires an immutable proposal")
    known = {node.node_id for node in ontology.nodes}
    if not admitted_node_ids or not set(admitted_node_ids).issubset(known):
        raise ValueError("reviewed failure labels must be non-empty ontology nodes")
    assignments = tuple(
        FailureAssignment(
            node_id=node_id,
            confidence=1.0,
            evidence_result_digests=proposal.member_result_digests,
            proposed_by="human_review",
            automated=False,
            reviewed_by=reviewer,
            reviewed_at=created_at,
        )
        for node_id in sorted(set(admitted_node_ids))
    )
    nodes = {node.node_id: node for node in ontology.nodes}
    axes = tuple(
        sorted(
            {axis for node_id in admitted_node_ids for axis in nodes[node_id].changed_axes},
            key=lambda axis: axis.value,
        )
    )
    status = ReviewStatus.ADMITTED if admitted else ReviewStatus.REJECTED
    cluster_id = f"{proposal.cluster_id}-review-{sha256_digest((reviewer, reason))[7:19]}"
    provisional = proposal.model_copy(
        update={
            "cluster_id": cluster_id,
            "assignments": assignments,
            "status": status,
            "reviewed_by": reviewer,
            "review_reason": reason,
            "suspected_changed_axes": axes,
            "probe_set_digest": probe_set_digest if admitted else None,
            "cluster_digest": sha256_digest("pending"),
            "created_at": created_at,
        }
    )
    identity = provisional.model_dump(mode="json", exclude={"cluster_digest", "created_at"})
    return FailureCluster(
        **{
            **provisional.model_dump(mode="python"),
            "cluster_digest": sha256_digest(identity),
        }
    )


def _node_candidates(
    *,
    origin: FailureOrigin,
    codes: tuple[str, ...],
    nodes: Mapping[str, object],
) -> tuple[str, ...]:
    identifiers = tuple(sorted(str(node_id) for node_id in nodes))
    exact = tuple(node_id for node_id in identifiers if node_id in codes)
    if exact:
        return exact
    prefixed = tuple(
        node_id
        for node_id in identifiers
        if any(code.startswith(f"{node_id}.") or node_id.startswith(f"{code}.") for code in codes)
    )
    if prefixed:
        return prefixed
    origin_nodes = tuple(
        node_id for node_id in identifiers if getattr(nodes[node_id], "origin", None) == origin
    )
    if origin_nodes:
        return (origin_nodes[0],)
    unknown = tuple(
        node_id
        for node_id in identifiers
        if getattr(nodes[node_id], "origin", None) == FailureOrigin.UNKNOWN
    )
    if not unknown:
        raise ValueError("ontology has no node for the proposed failure origin")
    return (unknown[0],)


def _assignment_confidence(*, node_id: str, codes: tuple[str, ...]) -> float:
    if node_id in codes:
        return 0.9
    if any(code.startswith(f"{node_id}.") or node_id.startswith(f"{code}.") for code in codes):
        return 0.7
    return 0.5


def _reproducibility(
    *,
    members: Sequence[AtlasTrialResult],
    requests: Mapping[str, AtlasTrialRequest],
) -> tuple[float, float]:
    by_item: dict[str, list[TrialStatus]] = defaultdict(list)
    for result in members:
        request = requests.get(result.request_id)
        if request is None or request.request_digest != result.request_digest:
            raise ValueError("failure clustering requires exact trial-request evidence")
        by_item[request.item_digest].append(result.status)
    repeated = [statuses for statuses in by_item.values() if len(statuses) >= 2]
    reproducible = [
        statuses
        for statuses in repeated
        if sum(status in _FAILURE_STATUSES for status in statuses) == len(statuses)
    ]
    reproducibility = len(reproducible) / len(repeated) if repeated else 0.0
    agreements: list[float] = []
    for statuses in by_item.values():
        counts = Counter(statuses)
        agreements.append(max(counts.values()) / len(statuses))
    stability = sum(agreements) / len(agreements) if agreements else 0.0
    return reproducibility, stability


def _severity(results: Sequence[AtlasTrialResult]) -> int:
    if any(result.status == TrialStatus.INFRASTRUCTURE_FAILURE for result in results):
        return 2
    if any(result.status == TrialStatus.TIMEOUT for result in results):
        return 3
    if any(result.status == TrialStatus.VERIFIED_FAILURE for result in results):
        return 4
    return 2
