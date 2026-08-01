from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import LessonRecord, LessonStatus


class UnsupportedCapabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConsolidationDecision:
    proposal_id: str
    accepted: bool
    reason: str
    before_snapshot_id: str | None
    after_snapshot_id: str | None
    lesson_id: str | None


class ConsolidationBackend(Protocol):
    async def propose(self, session: AsyncSession, **kwargs: object) -> ConsolidationDecision: ...

    async def rollback(
        self, session: AsyncSession, *, snapshot_id: str, source_episode_id: str
    ) -> tuple[str, ...]: ...


class MemoryConsolidationBackend:
    """Operational, evidence-gated consolidation into versioned lesson memory."""

    def __init__(self, memory: LessonMemory) -> None:
        self.memory = memory

    async def propose(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        state_lineage_id: str,
        branch_id: str,
        competency_id: str,
        error_class: str,
        general_rule: str,
        applicability: str,
        exclusions: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        source_episode_ids: tuple[str, ...],
        successful_transfer_count: int,
        failed_transfer_count: int,
        confidence: float,
        teacher_id: str | None = None,
    ) -> ConsolidationDecision:
        proposal_id = f"consolidation-{uuid4()}"
        if not evidence_ids or not source_episode_ids:
            return ConsolidationDecision(
                proposal_id, False, "insufficient evidence", None, None, None
            )
        if successful_transfer_count < 1:
            return ConsolidationDecision(
                proposal_id, False, "no successful unseen transfer", None, None, None
            )
        if failed_transfer_count > successful_transfer_count * 2:
            return ConsolidationDecision(
                proposal_id, False, "transfer failures dominate", None, None, None
            )
        before = await self.memory.snapshot(
            session,
            student_id=student_id,
            state_lineage_id=state_lineage_id,
            branch_id=branch_id,
            reason=f"before {proposal_id}",
        )
        lesson_id = f"lesson-{uuid4()}"
        lesson = LessonRecord(
            lesson_id=lesson_id,
            version=1,
            competency_id=competency_id,
            error_class=error_class,
            general_rule=general_rule,
            applicability=applicability,
            exclusions=exclusions,
            evidence_ids=evidence_ids,
            source_episode_ids=source_episode_ids,
            teacher_id=teacher_id,
            confidence=confidence,
            successful_transfer_count=successful_transfer_count,
            failed_transfer_count=failed_transfer_count,
            harmful_retrieval_count=0,
            state_lineage_id=state_lineage_id,
            branch_id=branch_id,
            status=LessonStatus.VALIDATED,
            supersedes_version=None,
            created_at=datetime.now(UTC),
        )
        stored = await self.memory.write(session, lesson)
        if stored.status != LessonStatus.VALIDATED:
            return ConsolidationDecision(
                proposal_id,
                False,
                "conflicting lesson requires review",
                before,
                None,
                stored.lesson_id,
            )
        after = await self.memory.snapshot(
            session,
            student_id=student_id,
            state_lineage_id=state_lineage_id,
            branch_id=branch_id,
            reason=f"after {proposal_id}",
        )
        return ConsolidationDecision(
            proposal_id, True, "validated transferable lesson", before, after, lesson_id
        )

    async def rollback(
        self, session: AsyncSession, *, snapshot_id: str, source_episode_id: str
    ) -> tuple[str, ...]:
        return await self.memory.rollback(
            session, snapshot_id=snapshot_id, source_episode_id=source_episode_id
        )


class UnsupportedParameterUpdateBackend:
    """Explicit refusal for parameter updates when no real backend is configured."""

    async def propose(self, session: AsyncSession, **kwargs: object) -> ConsolidationDecision:
        del session, kwargs
        raise UnsupportedCapabilityError(
            "parameter updates require a backend that can isolate, evaluate, commit, and restore"
        )

    async def rollback(
        self, session: AsyncSession, *, snapshot_id: str, source_episode_id: str
    ) -> tuple[str, ...]:
        del session, snapshot_id, source_episode_id
        raise UnsupportedCapabilityError("no parameter update state exists to restore")
