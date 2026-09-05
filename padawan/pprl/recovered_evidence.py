"""Explicit, reviewed process disclosure from private completed-effect snapshots."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import ForensicArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ProcessRecoveryRow
from padawan.pprl.evidence_contracts import ProcessEvidenceAdmission, ProcessEvidenceUse
from padawan.pprl.recovered_evidence_contracts import (
    RecoveredEffectSource,
    RecoveredEvidenceOriginReview,
    RecoveryEvidenceDisclosurePolicy,
)
from padawan.pprl.recovery_contracts import ProcessRecoveryReceipt

if TYPE_CHECKING:
    from padawan.pprl.recovery import ProcessRecoveryStore


@dataclass(frozen=True)
class RecoveredEvidenceSources:
    """Privileged review preparation, never a worker or training projection."""

    source: RecoveredEffectSource
    forensic_sources: tuple[ForensicArtifactRef, ...]
    forbidden_identifiers: frozenset[str]
    recovered_at: datetime


class RecoveryEvidenceSourceBoundary:
    def __init__(
        self, *, recovery: ProcessRecoveryStore, policy: RecoveryEvidenceDisclosurePolicy
    ) -> None:
        self.recovery = recovery
        self.catalog = recovery.containers.catalog
        self.amber = recovery.processes.amber
        self.policy = RecoveryEvidenceDisclosurePolicy.model_validate_json(policy.model_dump_json())

    async def describe(
        self, session: AsyncSession, *, recovery_id: str, decision_id: str
    ) -> RecoveredEvidenceSources:
        """Inspect one exact source without granting disclosure or effect authority."""
        row = await session.get(ProcessRecoveryRow, recovery_id)
        if row is None:
            raise PermissionError("recovered evidence source is missing")
        snapshot = ProcessRecoveryReceipt.model_validate(row.record_json, strict=False)
        unique: dict[str, ForensicArtifactRef] = {}
        for effect in snapshot.effects:
            for source in effect.sources:
                reference = source.reference
                previous = unique.setdefault(reference.artifact.artifact_id, reference)
                if previous != reference:
                    raise ValueError("recovery source has ambiguous artifact metadata")
        if (
            snapshot.execution_digest != self.policy.target_execution_digest
            or len(snapshot.effects) > self.policy.maximum_recovery_effects
            or sum(ref.artifact.size_bytes for ref in unique.values())
            > self.policy.maximum_recovery_source_bytes
        ):
            raise PermissionError("recovery source exceeds its pinned execution or read bounds")
        receipt = await self.recovery.read(session, recovery_id=recovery_id)
        effects = [effect for effect in receipt.effects if effect.decision_id == decision_id]
        if len(effects) != 1 or effects[0].disposition != "completed_unadmitted":
            raise PermissionError(
                "recovered disclosure requires one independently completed effect"
            )
        effect = effects[0]
        refs = {s.reference.artifact.artifact_id: s.reference for s in effect.sources}
        observed = await self.recovery.observations.inspect_decision_binding(
            session, decision_id=decision_id
        )
        identifiers = _private_identifiers(receipt.model_dump(mode="json"))
        identifiers.update(
            (
                receipt.digest,
                sha256_digest(effect),
                observed.observation_id,
                observed.digest,
                observed.observation_receipt_digest,
            )
        )
        return RecoveredEvidenceSources(
            source=RecoveredEffectSource(
                recovery_id=recovery_id,
                recovery_digest=receipt.digest,
                decision_id=decision_id,
                effect_digest=sha256_digest(effect),
            ),
            forensic_sources=tuple(refs[key] for key in sorted(refs)),
            forbidden_identifiers=frozenset(identifiers),
            recovered_at=receipt.created_at,
        )

    async def validate_review(
        self,
        session: AsyncSession,
        *,
        review: ProcessEvidenceAdmission,
        origin: RecoveredEvidenceOriginReview,
        candidate_bytes: bytes,
        authority_expires_at: datetime,
        now: datetime,
    ) -> None:
        policy = self.policy
        if (
            origin.disclosure_policy != policy
            or origin.policy_digest != policy.digest
            or origin.candidate_review_digest != review.digest
            or review.process_reference.execution_digest != policy.target_execution_digest
            or review.contamination_scope != policy.target_contamination_scope
            or review.reviewer_id not in policy.reviewer_ids
            or review.allowed_uses != (ProcessEvidenceUse.PROCESS,)
            or policy.content_policy_digest != self.recovery.processes.content.policy.digest
            or not policy.created_at <= review.reviewed_at <= now
        ):
            raise PermissionError("recovery disclosure lacks exact reviewed process-use authority")
        self.check_deadline(now=now, authority_expires_at=authority_expires_at)
        described = await self.describe(
            session, recovery_id=origin.source.recovery_id, decision_id=origin.source.decision_id
        )
        if (
            described.source != origin.source
            or described.forensic_sources != review.forensic_sources
            or described.recovered_at > review.reviewed_at
        ):
            raise PermissionError("recovery review substitutes or predates its exact source set")
        media = review.process_reference.media_type.split(";", 1)[0].strip().lower()
        if media not in {"application/json", "text/plain", "text/markdown"}:
            raise PermissionError("recovered disclosure requires reviewed UTF-8 text or JSON")
        text = candidate_bytes.decode("utf-8")
        content = (
            json.loads(text, object_pairs_hook=_unique_json_object)
            if media == "application/json"
            else text
        )
        boundary = self.recovery.processes.content
        strings = boundary._bounded_strings(
            content, maximum_bytes=review.admission_policy.maximum_bytes
        )
        identifiers = described.forbidden_identifiers | {
            origin.digest,
            policy.digest,
            policy.policy_id,
        }
        for identifier in identifiers:
            variants = (identifier, identifier.removeprefix("sha256:"))
            if any(value in part for value in variants for part in strings):
                raise PermissionError("recovered derivative contains a private source identifier")
        await boundary._check_identifiers(session, strings, allowed=set(), admitted_digests=set())
        self.check_deadline(now=now, authority_expires_at=authority_expires_at)

    def check_deadline(self, *, now: datetime, authority_expires_at: datetime) -> None:
        # Caller-supplied historical time cannot revive a present disclosure grant.
        if max(now, datetime.now(UTC)) >= min(self.policy.expires_at, authority_expires_at):
            raise PermissionError("recovered disclosure expired during source validation")


def _private_identifiers(value: Any) -> set[str]:
    identifiers: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and (
                key.endswith("digest")
                or key
                in {
                    "recovery_id",
                    "decision_id",
                    "invocation_id",
                    "owner_id",
                    "previous_worker_id",
                    "state_id",
                    "rollout_id",
                    "uri",
                    "artifact_id",
                }
            ):
                identifiers.add(item)
            else:
                identifiers.update(_private_identifiers(item))
    elif isinstance(value, list):
        for item in value:
            identifiers.update(_private_identifiers(item))
    return identifiers


def _unique_json_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("recovered disclosure contains duplicate JSON keys")
        result[key] = value
    return result
