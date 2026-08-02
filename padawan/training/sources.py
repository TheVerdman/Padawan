from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog
from padawan.models.contracts import RightsUse
from padawan.models.hashing import sha256_digest
from padawan.models.tables import TrainingSourceDecisionRow, TrainingSourceDocumentRow
from padawan.training.contracts import (
    TrainingSourceDecision,
    TrainingSourceDocument,
    TrainingSourceStatus,
)


@dataclass(frozen=True)
class GovernedTrainingSource:
    document: TrainingSourceDocument
    decision: TrainingSourceDecision


class TrainingSourceRegistry:
    """Append-only admission and lifecycle decisions for continued-pretraining sources."""

    def __init__(self, artifacts: ArtifactCatalog) -> None:
        self.artifacts = artifacts

    async def admit(
        self,
        session: AsyncSession,
        *,
        document: TrainingSourceDocument,
        initial_decision: TrainingSourceDecision,
    ) -> GovernedTrainingSource:
        if initial_decision.document_id != document.document_id:
            raise ValueError("initial source decision refers to a different document")
        if initial_decision.created_at < document.created_at:
            raise ValueError("initial source decision predates document admission")
        await self.artifacts.reference(
            session,
            document.content_ref,
            owner_type="training_source_document",
            owner_id=document.document_id,
        )
        payload = document.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(TrainingSourceDocumentRow, document.document_id)
        if existing is not None:
            if existing.record_digest != digest or existing.record_json != payload:
                raise ValueError("training source document ID conflicts with stored evidence")
        else:
            version_owner = await session.scalar(
                select(TrainingSourceDocumentRow).where(
                    TrainingSourceDocumentRow.source_id == document.source_id,
                    TrainingSourceDocumentRow.source_version == document.source_version,
                )
            )
            if version_owner is not None:
                raise ValueError("training source version is already admitted")
            if document.supersedes_document_id is not None:
                superseded = await session.get(
                    TrainingSourceDocumentRow, document.supersedes_document_id
                )
                if superseded is None:
                    raise ValueError("training source supersedes an unknown document")
                if superseded.source_id != document.source_id:
                    raise ValueError("training source versions must retain source identity")
            session.add(
                TrainingSourceDocumentRow(
                    document_id=document.document_id,
                    source_id=document.source_id,
                    source_version=document.source_version,
                    supersedes_document_id=document.supersedes_document_id,
                    content_artifact_id=document.content_ref.artifact_id,
                    content_digest=document.content_digest,
                    rights_digest=document.rights_digest,
                    record_digest=digest,
                    record_json=payload,
                    created_at=document.created_at,
                )
            )
            await session.flush()
        await self.decide(session, decision=initial_decision)
        return GovernedTrainingSource(document=document, decision=initial_decision)

    async def decide(
        self, session: AsyncSession, *, decision: TrainingSourceDecision
    ) -> TrainingSourceDecision:
        source_row = await session.get(TrainingSourceDocumentRow, decision.document_id)
        if source_row is None:
            raise KeyError(decision.document_id)
        source = TrainingSourceDocument.model_validate(source_row.record_json, strict=False)
        if decision.created_at < source.created_at:
            raise ValueError("training source decision predates document admission")
        existing = await session.get(TrainingSourceDecisionRow, decision.decision_id)
        payload = decision.model_dump(mode="json")
        digest = sha256_digest(payload)
        if existing is not None:
            if existing.record_digest != digest or existing.record_json != payload:
                raise ValueError("training source decision ID conflicts with stored evidence")
            return TrainingSourceDecision.model_validate(existing.record_json, strict=False)
        latest = await session.scalar(
            select(TrainingSourceDecisionRow)
            .where(TrainingSourceDecisionRow.document_id == decision.document_id)
            .order_by(
                TrainingSourceDecisionRow.created_at.desc(),
                TrainingSourceDecisionRow.decision_id.desc(),
            )
            .limit(1)
        )
        if latest is not None:
            prior = TrainingSourceDecision.model_validate(latest.record_json, strict=False)
            if decision.created_at <= prior.created_at:
                raise ValueError("training source decisions must advance monotonically")
            if (
                prior.status
                in {
                    TrainingSourceStatus.QUARANTINED,
                    TrainingSourceStatus.RETIRED,
                }
                and decision.status == TrainingSourceStatus.ACTIVE
            ):
                raise ValueError("closed source documents require a new admitted version")
        if decision.status == TrainingSourceStatus.ACTIVE:
            if not source.quality_evidence_refs:
                raise ValueError("active training sources require quality-gate evidence")
            if not source.rights.permits(RightsUse.CONTINUED_PRETRAINING):
                raise ValueError(
                    "active training sources require confirmed continued-pretraining rights"
                )
        session.add(
            TrainingSourceDecisionRow(
                decision_id=decision.decision_id,
                document_id=decision.document_id,
                status=decision.status.value,
                contaminated=decision.contaminated,
                record_digest=digest,
                record_json=payload,
                created_at=decision.created_at,
            )
        )
        await session.flush()
        return decision

    async def get(self, session: AsyncSession, *, document_id: str) -> GovernedTrainingSource:
        source_row = await session.get(TrainingSourceDocumentRow, document_id)
        if source_row is None:
            raise KeyError(document_id)
        decision_row = await session.scalar(
            select(TrainingSourceDecisionRow)
            .where(TrainingSourceDecisionRow.document_id == document_id)
            .order_by(
                TrainingSourceDecisionRow.created_at.desc(),
                TrainingSourceDecisionRow.decision_id.desc(),
            )
            .limit(1)
        )
        if decision_row is None:
            raise ValueError("training source has no governance decision")
        return GovernedTrainingSource(
            document=TrainingSourceDocument.model_validate(source_row.record_json, strict=False),
            decision=TrainingSourceDecision.model_validate(decision_row.record_json, strict=False),
        )
