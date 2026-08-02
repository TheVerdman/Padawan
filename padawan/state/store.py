from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import (
    LifecycleStatus,
    ResearchRole,
    StateForkRecord,
    StudentStateRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import StateForkRow, StudentRow, StudentStateRow


class StateInvariantError(RuntimeError):
    pass


class StateStore:
    """Creates immutable student states and transactionally symmetric forks."""

    async def create_student(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        checkpoint_id: str,
        runtime_id: str,
        research_role: ResearchRole = ResearchRole.TARGET,
        initial_working_state: dict[str, Any] | None = None,
    ) -> StudentStateRecord:
        if await session.get(StudentRow, student_id) is not None:
            raise StateInvariantError(f"student already exists: {student_id}")
        timestamp = datetime.now(UTC)
        student = StudentRow(
            student_id=student_id,
            research_role=research_role.value,
            canonical_state_id=None,
            created_at=timestamp,
        )
        session.add(student)
        await session.flush()
        branch_id = f"branch-main-{uuid4()}"
        record = self._build_record(
            student_id=student_id,
            checkpoint_id=checkpoint_id,
            runtime_id=runtime_id,
            research_role=research_role,
            parent_state_id=None,
            branch_id=branch_id,
            compacted_working_state=initial_working_state or {},
            lesson_memory_refs=(),
            unresolved_hypotheses=(),
            competency_estimates=(),
            active_experiment_id=None,
            creation_reason="initial checkpoint state",
            lifecycle_status=LifecycleStatus.CANONICAL,
            created_at=timestamp,
        )
        session.add(_record_to_row(record))
        student.canonical_state_id = record.state_id
        await session.flush()
        return record

    async def append_state(
        self,
        session: AsyncSession,
        *,
        parent_state_id: str,
        compacted_working_state: dict[str, Any] | None = None,
        lesson_memory_refs: tuple[str, ...] | None = None,
        unresolved_hypotheses: tuple[dict[str, Any], ...] | None = None,
        competency_estimates: tuple[dict[str, Any], ...] | None = None,
        creation_reason: str,
        lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE,
    ) -> StudentStateRecord:
        parent = await session.get(StudentStateRow, parent_state_id)
        if parent is None:
            raise KeyError(parent_state_id)
        record = self._build_record(
            student_id=parent.student_id,
            checkpoint_id=parent.checkpoint_id,
            runtime_id=parent.runtime_id,
            research_role=ResearchRole(parent.research_role),
            parent_state_id=parent.state_id,
            branch_id=parent.branch_id,
            compacted_working_state=(
                compacted_working_state
                if compacted_working_state is not None
                else dict(parent.compacted_working_state)
            ),
            lesson_memory_refs=(
                lesson_memory_refs
                if lesson_memory_refs is not None
                else tuple(parent.lesson_memory_refs)
            ),
            unresolved_hypotheses=(
                unresolved_hypotheses
                if unresolved_hypotheses is not None
                else tuple(parent.unresolved_hypotheses)
            ),
            competency_estimates=(
                competency_estimates
                if competency_estimates is not None
                else tuple(parent.competency_estimates)
            ),
            active_experiment_id=parent.active_experiment_id,
            creation_reason=creation_reason,
            lifecycle_status=lifecycle_status,
            created_at=datetime.now(UTC),
        )
        session.add(_record_to_row(record))
        await session.flush()
        return record

    async def fork(
        self,
        session: AsyncSession,
        *,
        parent_state_id: str,
        experiment_id: str,
        intervention: dict[str, Any],
        fork_id: str | None = None,
    ) -> StateForkRecord:
        parent = await session.scalar(
            select(StudentStateRow)
            .where(StudentStateRow.state_id == parent_state_id)
            .with_for_update()
        )
        if parent is None:
            raise KeyError(parent_state_id)
        assigned_fork_id = fork_id or f"fork-{uuid4()}"
        if await session.get(StateForkRow, assigned_fork_id) is not None:
            existing = await session.get(StateForkRow, assigned_fork_id)
            if existing is None:
                raise AssertionError("fork disappeared during transaction")
            if existing.parent_state_id != parent_state_id:
                raise StateInvariantError("fork ID reused for another parent")
            return _fork_row_to_record(existing)

        timestamp = datetime.now(UTC)
        treatment_branch = f"branch-treatment-{uuid4()}"
        control_branch = f"branch-control-{uuid4()}"
        treatment = self._build_record(
            student_id=parent.student_id,
            checkpoint_id=parent.checkpoint_id,
            runtime_id=parent.runtime_id,
            research_role=ResearchRole(parent.research_role),
            parent_state_id=parent.state_id,
            branch_id=treatment_branch,
            compacted_working_state=dict(parent.compacted_working_state),
            lesson_memory_refs=tuple(parent.lesson_memory_refs),
            unresolved_hypotheses=tuple(parent.unresolved_hypotheses),
            competency_estimates=tuple(parent.competency_estimates),
            active_experiment_id=experiment_id,
            creation_reason=f"treatment branch for {assigned_fork_id}",
            lifecycle_status=LifecycleStatus.EXPERIMENTAL,
            created_at=timestamp,
        )
        control = self._build_record(
            student_id=parent.student_id,
            checkpoint_id=parent.checkpoint_id,
            runtime_id=parent.runtime_id,
            research_role=ResearchRole(parent.research_role),
            parent_state_id=parent.state_id,
            branch_id=control_branch,
            compacted_working_state=dict(parent.compacted_working_state),
            lesson_memory_refs=tuple(parent.lesson_memory_refs),
            unresolved_hypotheses=tuple(parent.unresolved_hypotheses),
            competency_estimates=tuple(parent.competency_estimates),
            active_experiment_id=experiment_id,
            creation_reason=f"control branch for {assigned_fork_id}",
            lifecycle_status=LifecycleStatus.EXPERIMENTAL,
            created_at=timestamp,
        )
        if _inherited_digest(treatment) != _inherited_digest(control):
            raise StateInvariantError("fork branches do not inherit identical cognition")
        session.add_all([_record_to_row(treatment), _record_to_row(control)])
        await session.flush()
        fork = StateForkRow(
            fork_id=assigned_fork_id,
            parent_state_id=parent.state_id,
            treatment_state_id=treatment.state_id,
            control_state_id=control.state_id,
            treatment_branch_id=treatment.branch_id,
            control_branch_id=control.branch_id,
            intervention={
                **intervention,
                "inherited_digest": _inherited_digest(treatment),
                "parent_state_hash": parent.state_hash,
            },
            created_at=timestamp,
        )
        session.add(fork)
        await session.flush()
        return _fork_row_to_record(fork)

    async def promote_canonical(self, session: AsyncSession, *, state_id: str) -> None:
        state = await session.get(StudentStateRow, state_id)
        if state is None:
            raise KeyError(state_id)
        student = await session.scalar(
            select(StudentRow).where(StudentRow.student_id == state.student_id).with_for_update()
        )
        if student is None:
            raise StateInvariantError("state has no student")
        student.canonical_state_id = state.state_id
        await session.flush()

    async def lineage(self, session: AsyncSession, *, state_id: str) -> tuple[str, ...]:
        result: list[str] = []
        current = await session.get(StudentStateRow, state_id)
        while current is not None:
            result.append(current.state_id)
            current = (
                await session.get(StudentStateRow, current.parent_state_id)
                if current.parent_state_id
                else None
            )
        return tuple(result)

    async def get(self, session: AsyncSession, *, state_id: str) -> StudentStateRecord:
        row = await session.get(StudentStateRow, state_id)
        if row is None:
            raise KeyError(state_id)
        return _row_to_record(row)

    @staticmethod
    def assert_branch(state: StudentStateRecord, branch_id: str) -> None:
        if state.branch_id != branch_id:
            raise StateInvariantError(
                f"cross-branch write denied: {branch_id} cannot write {state.branch_id}"
            )

    def _build_record(
        self,
        *,
        student_id: str,
        checkpoint_id: str,
        runtime_id: str,
        research_role: ResearchRole,
        parent_state_id: str | None,
        branch_id: str,
        compacted_working_state: dict[str, Any],
        lesson_memory_refs: tuple[str, ...],
        unresolved_hypotheses: tuple[Any, ...],
        competency_estimates: tuple[Any, ...],
        active_experiment_id: str | None,
        creation_reason: str,
        lifecycle_status: LifecycleStatus,
        created_at: datetime,
    ) -> StudentStateRecord:
        state_id = f"state-{uuid4()}"
        semantic = {
            "state_id": state_id,
            "student_id": student_id,
            "checkpoint_id": checkpoint_id,
            "runtime_id": runtime_id,
            "research_role": research_role.value,
            "parent_state_id": parent_state_id,
            "branch_id": branch_id,
            "compacted_working_state": compacted_working_state,
            "lesson_memory_refs": list(lesson_memory_refs),
            "unresolved_hypotheses": list(unresolved_hypotheses),
            "competency_estimates": list(competency_estimates),
            "active_experiment_id": active_experiment_id,
            "creation_reason": creation_reason,
            "lifecycle_status": lifecycle_status.value,
            "created_at": created_at.isoformat(),
        }
        return StudentStateRecord.model_validate(
            {
                **semantic,
                "state_hash": sha256_digest(semantic),
            },
            strict=False,
        )


def _record_to_row(record: StudentStateRecord) -> StudentStateRow:
    return StudentStateRow(
        state_id=record.state_id,
        student_id=record.student_id,
        checkpoint_id=record.checkpoint_id,
        runtime_id=record.runtime_id,
        research_role=record.research_role.value,
        parent_state_id=record.parent_state_id,
        branch_id=record.branch_id,
        compacted_working_state=record.compacted_working_state,
        lesson_memory_refs=list(record.lesson_memory_refs),
        unresolved_hypotheses=[
            item.model_dump(mode="json") for item in record.unresolved_hypotheses
        ],
        competency_estimates=[item.model_dump(mode="json") for item in record.competency_estimates],
        active_experiment_id=record.active_experiment_id,
        state_hash=record.state_hash,
        creation_reason=record.creation_reason,
        lifecycle_status=record.lifecycle_status.value,
        created_at=record.created_at,
    )


def _row_to_record(row: StudentStateRow) -> StudentStateRecord:
    return StudentStateRecord.model_validate(
        {
            "state_id": row.state_id,
            "student_id": row.student_id,
            "checkpoint_id": row.checkpoint_id,
            "runtime_id": row.runtime_id,
            "research_role": row.research_role,
            "parent_state_id": row.parent_state_id,
            "branch_id": row.branch_id,
            "compacted_working_state": row.compacted_working_state,
            "lesson_memory_refs": row.lesson_memory_refs,
            "unresolved_hypotheses": row.unresolved_hypotheses,
            "competency_estimates": row.competency_estimates,
            "active_experiment_id": row.active_experiment_id,
            "state_hash": row.state_hash,
            "creation_reason": row.creation_reason,
            "lifecycle_status": row.lifecycle_status,
            "created_at": row.created_at,
        },
        strict=False,
    )


def _fork_row_to_record(row: StateForkRow) -> StateForkRecord:
    return StateForkRecord.model_validate(
        {
            "fork_id": row.fork_id,
            "parent_state_id": row.parent_state_id,
            "treatment_state_id": row.treatment_state_id,
            "control_state_id": row.control_state_id,
            "treatment_branch_id": row.treatment_branch_id,
            "control_branch_id": row.control_branch_id,
            "intervention": row.intervention,
            "created_at": row.created_at,
        },
        strict=False,
    )


def _inherited_digest(state: StudentStateRecord) -> str:
    return sha256_digest(
        {
            "student_id": state.student_id,
            "checkpoint_id": state.checkpoint_id,
            "runtime_id": state.runtime_id,
            "research_role": state.research_role.value,
            "parent_state_id": state.parent_state_id,
            "compacted_working_state": state.compacted_working_state,
            "lesson_memory_refs": state.lesson_memory_refs,
            "unresolved_hypotheses": state.unresolved_hypotheses,
            "competency_estimates": state.competency_estimates,
            "active_experiment_id": state.active_experiment_id,
        }
    )
