from __future__ import annotations

from datetime import UTC, datetime

from padawan.atlas.clustering import propose_failure_clusters, review_failure_cluster
from padawan.atlas.contracts import (
    AtlasTrialRequest,
    AtlasTrialResult,
    AuthorityKind,
    FailureOrigin,
    OntologyManifest,
    OntologyNode,
    OutcomeEvidence,
    ReviewStatus,
    TokenAccounting,
    TrialStatus,
    content_id,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import ResearchAxis

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _ontology() -> OntologyManifest:
    node = OntologyNode(
        node_id="reasoning.planning",
        title="Planning failure",
        description="The model fails to construct or maintain a valid plan.",
        origin=FailureOrigin.MODEL,
        changed_axes=(ResearchAxis.TOOLS,),
    )
    provisional = OntologyManifest.model_construct(
        ontology_id="padawan.capability-failures",
        version="1.0.0",
        nodes=(node,),
        manifest_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(mode="json", exclude={"manifest_digest", "created_at"})
    return OntologyManifest(
        **{
            **provisional.model_dump(mode="python"),
            "manifest_digest": sha256_digest(identity),
        }
    )


def _request(index: int) -> AtlasTrialRequest:
    provisional = AtlasTrialRequest.model_construct(
        request_id=f"request-{index}",
        run_id="run-atlas",
        campaign_digest=sha256_digest("campaign"),
        research_execution_digest=sha256_digest("execution"),
        condition_id="standardized",
        suite_digest=sha256_digest("suite"),
        item_id="item-planning",
        item_digest=sha256_digest("item-planning"),
        allocation_id=f"allocation-{index}",
        trial_index=index,
        attempt_index=0,
        parent_request_id=None,
        prompt_digest=sha256_digest("prompt"),
        rendered_input_digest=sha256_digest("prompt"),
        instructions_digest=sha256_digest("clustering instructions"),
        response_format_digest=sha256_digest({"schema_name": None, "json_schema": None}),
        wire_request_digest=sha256_digest(f"wire-{index}"),
        adapter_id="interactive-test",
        adapter_version="1",
        adapter_descriptor_digest=sha256_digest("interactive-test descriptor"),
        effort=0.99,
        effort_mapping_evidence_digest=sha256_digest("clustering effort mapping"),
        edge_preflight_evidence_digest=sha256_digest("clustering edge preflight"),
        context_limit_tokens=4096,
        max_output_tokens=512,
        action_budget=8,
        tool_ids=(),
        tool_manifest_digest=sha256_digest(()),
        sampling={"temperature": "1.0"},
        request_digest=sha256_digest("pending"),
        created_at=NOW,
    )
    identity = provisional.model_dump(mode="json", exclude={"request_digest", "created_at"})
    return AtlasTrialRequest(
        **{
            **provisional.model_dump(mode="python"),
            "request_digest": sha256_digest(identity),
        }
    )


def _failure(request: AtlasTrialRequest) -> AtlasTrialResult:
    response_digest = sha256_digest(f"response-{request.request_id}")
    response = ArtifactRef(
        artifact_id=f"artifact-{request.request_id}",
        uri=f"artifact://sha256/{response_digest[7:]}",
        digest=response_digest,
        media_type="application/json",
        size_bytes=10,
        restricted=True,
        raw_data=True,
    )
    evidence = OutcomeEvidence(
        evidence_id=f"environment-{request.request_id}",
        authority=AuthorityKind.ENVIRONMENT,
        verifier_id="planning-environment",
        verifier_version="1",
        disposition="rejected",
        score=0.0,
        success=False,
        evaluated_output_digest=sha256_digest("cluster output"),
        deterministic=True,
        evidence_digest=sha256_digest(f"evidence-{request.request_id}"),
    )
    provisional = AtlasTrialResult.model_construct(
        result_id="pending",
        request_id=request.request_id,
        request_digest=request.request_digest,
        research_execution_digest=request.research_execution_digest,
        generation_provider="test-provider",
        generation_model_id="test-model",
        generation_protocol="responses",
        raw_request_digest=sha256_digest(f"raw-request-{request.request_id}"),
        raw_response_digest=response.digest,
        capabilities_digest=sha256_digest("capabilities"),
        external_call_artifact=ArtifactRef(
            artifact_id=f"external-envelope-{request.request_id}",
            uri=(f"artifact://sha256/{sha256_digest(f'external-{request.request_id}')[7:]}"),
            digest=sha256_digest(f"external-{request.request_id}"),
            media_type="application/vnd.padawan.generation-result+json",
            size_bytes=8,
            restricted=True,
            raw_data=True,
        ),
        status=TrialStatus.VERIFIED_FAILURE,
        score=0.0,
        success=False,
        confidence=None,
        abstained=False,
        failure_origin=FailureOrigin.MODEL,
        failure_codes=("reasoning.planning",),
        verifier_evidence=(evidence,),
        primary_authority=AuthorityKind.ENVIRONMENT,
        response_digest=response.digest,
        response_artifact=response,
        grader_artifacts=(),
        tokens=TokenAccounting(
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            counting_mode="provider_reported",
        ),
        latency_ms=10.0,
        wall_time_ms=10.0,
        cost_usd=None,
        tool_calls=(),
        retry_count=0,
        contamination_checks={"suite_membership": True},
        result_digest=sha256_digest("pending"),
        completed_at=NOW,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"result_id", "result_digest", "completed_at"}
    )
    return AtlasTrialResult(
        **{
            **provisional.model_dump(mode="python"),
            "result_id": content_id("atlas-result", identity),
            "result_digest": sha256_digest(identity),
        }
    )


def test_failure_clustering_is_deterministic_and_review_required() -> None:
    requests = tuple(_request(index) for index in range(2))
    results = tuple(_failure(request) for request in requests)
    ontology = _ontology()
    clusters = propose_failure_clusters(
        campaign_digest=sha256_digest("campaign"),
        ontology=ontology,
        results=results,
        requests={request.request_id: request for request in requests},
        proposed_by="atlas.exact-code-clusterer-v1",
        created_at=NOW,
    )
    assert len(clusters) == 1
    proposal = clusters[0]
    assert proposal.status == ReviewStatus.PROPOSED
    assert proposal.reproducibility == 1.0
    assert proposal.stability == 1.0
    assert proposal.assignments[0].automated
    assert proposal.reviewed_by is None

    reviewed = review_failure_cluster(
        proposal=proposal,
        ontology=ontology,
        admitted_node_ids=("reasoning.planning",),
        reviewer="researcher@example",
        reason="independent repeated environment failures match the planning definition",
        admitted=True,
        created_at=NOW,
    )
    assert reviewed.status == ReviewStatus.ADMITTED
    assert reviewed.reviewed_by == "researcher@example"
    assert not reviewed.assignments[0].automated
    assert proposal.status == ReviewStatus.PROPOSED
