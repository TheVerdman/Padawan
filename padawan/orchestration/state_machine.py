from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import ResearchRole, RunState
from padawan.models.tables import RunRow, RunTransitionRow, WorkerRow

_HAPPY_PATH: tuple[RunState, ...] = (
    RunState.CREATED,
    RunState.ITEMS_LEASED,
    RunState.BASE_STATE_SNAPSHOTTED,
    RunState.BRANCHES_CREATED,
    RunState.COLD_ATTEMPT_RUNNING,
    RunState.COLD_ATTEMPT_STORED,
    RunState.COLD_GRADED,
    RunState.TEACHER_REQUESTED,
    RunState.TEACHER_RESPONSE_STORED,
    RunState.COMMENT_VALIDATED,
    RunState.REVISION_RUNNING,
    RunState.REVISION_STORED,
    RunState.REVISION_GRADED,
    RunState.TRANSFER_RUNNING,
    RunState.TRANSFER_STORED,
    RunState.TRANSFER_GRADED,
    RunState.MEMORY_DECIDED,
    RunState.EXPOSURES_RECORDED,
    RunState.ITEMS_RETIRED,
    RunState.EPISODE_COMMITTED,
    RunState.COMPLETE,
)
_NEXT = {left: right for left, right in zip(_HAPPY_PATH, _HAPPY_PATH[1:], strict=False)}
_TERMINAL = {RunState.COMPLETE, RunState.FAILED_TERMINAL, RunState.REVIEW_REQUIRED}


class InvalidTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedRun:
    run_id: str
    state: RunState
    research_role: ResearchRole
    payload: dict[str, Any]
    lease_token: str
    retry_count: int
    retry_budget: int


class RunStore:
    """Crash-resumable state authority with leases and append-only transition history."""

    async def create(
        self,
        session: AsyncSession,
        *,
        payload: dict[str, Any],
        retry_budget: int = 3,
        run_id: str | None = None,
    ) -> str:
        if retry_budget < 0:
            raise ValueError("retry budget cannot be negative")
        assigned = run_id or f"run-{uuid4()}"
        student_id_value = payload.get("student_id")
        student_id = str(student_id_value) if student_id_value else None
        research_role = ResearchRole(str(payload.get("research_role", ResearchRole.TARGET.value)))
        existing_by_id = await session.get(RunRow, assigned)
        if existing_by_id is not None:
            if existing_by_id.research_role != research_role.value:
                raise ValueError("existing run ID has a different research role")
            return assigned
        if student_id is not None:
            existing = await session.scalar(
                select(RunRow).where(RunRow.active_student_id == student_id)
            )
            if existing is not None:
                if existing.research_role != research_role.value:
                    raise ValueError("active run has a different research role")
                return existing.run_id
        timestamp = datetime.now(UTC)
        row = RunRow(
            run_id=assigned,
            episode_id=None,
            student_id=student_id,
            active_student_id=student_id,
            research_role=research_role.value,
            state=RunState.CREATED.value,
            sequence=0,
            payload=payload,
            retry_count=0,
            retry_budget=retry_budget,
            paused=False,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            last_error=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        if student_id is None:
            session.add(row)
            await session.flush()
            return assigned
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            existing = await session.scalar(
                select(RunRow).where(RunRow.active_student_id == student_id)
            )
            if existing is None:
                raise
            if existing.research_role != research_role.value:
                raise ValueError("concurrent active run has a different research role") from None
            return str(existing.run_id)
        return assigned

    async def claim_next(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        lease_for: timedelta,
        now: datetime | None = None,
    ) -> ClaimedRun | None:
        timestamp = now or datetime.now(UTC)
        selectable_states = [state.value for state in RunState if state not in _TERMINAL]
        query = (
            select(RunRow)
            .where(
                RunRow.state.in_(selectable_states),
                RunRow.paused.is_(False),
                or_(RunRow.lease_expires_at.is_(None), RunRow.lease_expires_at <= timestamp),
            )
            .order_by(RunRow.updated_at, RunRow.run_id)
        )
        dialect = session.bind.dialect.name if session.bind is not None else "unknown"
        token = f"runlease-{uuid4()}"
        expires = timestamp + lease_for
        if dialect == "postgresql":
            row = await session.scalar(query.with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            row.lease_owner = worker_id
            row.lease_token = token
            row.lease_expires_at = expires
            row.updated_at = timestamp
            await session.flush()
        else:
            candidate = query.with_only_columns(RunRow.run_id).limit(1).scalar_subquery()
            row = await session.scalar(
                update(RunRow)
                .where(
                    RunRow.run_id == candidate,
                    or_(RunRow.lease_expires_at.is_(None), RunRow.lease_expires_at <= timestamp),
                )
                .values(
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires,
                    updated_at=timestamp,
                )
                .returning(RunRow)
            )
            if row is None:
                return None
        return ClaimedRun(
            run_id=row.run_id,
            state=RunState(row.state),
            research_role=ResearchRole(row.research_role),
            payload=dict(row.payload),
            lease_token=token,
            retry_count=row.retry_count,
            retry_budget=row.retry_budget,
        )

    async def transition(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        lease_token: str,
        actor: str,
        to_state: RunState,
        payload_updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> RunRow:
        row = await session.scalar(
            select(RunRow)
            .where(RunRow.run_id == run_id, RunRow.lease_token == lease_token)
            .with_for_update()
        )
        if row is None:
            raise InvalidTransitionError("run lease is missing or stale")
        from_state = RunState(row.state)
        self._validate_transition(from_state, to_state, row)
        if (
            payload_updates
            and "research_role" in payload_updates
            and str(payload_updates["research_role"]) != row.research_role
        ):
            raise InvalidTransitionError("a run transition cannot change research role")
        row.sequence += 1
        row.state = to_state.value
        row.payload = {**row.payload, **(payload_updates or {})}
        if payload_updates and payload_updates.get("episode_id"):
            row.episode_id = str(payload_updates["episode_id"])
        row.updated_at = datetime.now(UTC)
        row.last_error = (
            None
            if to_state not in {RunState.FAILED_RETRYABLE, RunState.FAILED_TERMINAL}
            else row.last_error
        )
        if to_state in _TERMINAL:
            row.active_student_id = None
        session.add(
            RunTransitionRow(
                transition_id=f"transition-{uuid4()}",
                run_id=run_id,
                sequence=row.sequence,
                from_state=from_state.value,
                to_state=to_state.value,
                actor=actor,
                details=details or {},
                created_at=row.updated_at,
            )
        )
        # A claim protects exactly one durable action. Releasing it after every
        # persisted transition lets this or another worker safely claim the next
        # action without waiting for lease expiry.
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        await session.flush()
        return row

    async def advance(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        lease_token: str,
        actor: str,
        payload_updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> RunRow:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        current = RunState(row.state)
        next_state = _NEXT.get(current)
        if next_state is None:
            raise InvalidTransitionError(f"{current.value} has no automatic successor")
        return await self.transition(
            session,
            run_id=run_id,
            lease_token=lease_token,
            actor=actor,
            to_state=next_state,
            payload_updates=payload_updates,
            details=details,
        )

    async def fail(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        lease_token: str,
        actor: str,
        error_class: str,
        message: str,
        retryable: bool,
        infrastructure: bool,
    ) -> RunState:
        row = await session.scalar(
            select(RunRow)
            .where(RunRow.run_id == run_id, RunRow.lease_token == lease_token)
            .with_for_update()
        )
        if row is None:
            raise InvalidTransitionError("run lease is missing or stale")
        original_state = RunState(row.state)
        row.retry_count += 1
        target = (
            RunState.FAILED_RETRYABLE
            if retryable and row.retry_count <= row.retry_budget
            else RunState.FAILED_TERMINAL
        )
        row.last_error = {
            "error_class": error_class,
            "message": message,
            "infrastructure": infrastructure,
            "failed_from_state": original_state.value,
            "attempt": row.retry_count,
        }
        row.payload = {**row.payload, "retry_from_state": original_state.value}
        await self.transition(
            session,
            run_id=run_id,
            lease_token=lease_token,
            actor=actor,
            to_state=target,
            details=row.last_error,
        )
        return target

    async def resume_retry(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        lease_token: str,
        actor: str,
    ) -> RunRow:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        if RunState(row.state) != RunState.FAILED_RETRYABLE:
            raise InvalidTransitionError("only a retryable failure can resume")
        retry_from = RunState(str(row.payload["retry_from_state"]))
        return await self.transition(
            session,
            run_id=run_id,
            lease_token=lease_token,
            actor=actor,
            to_state=retry_from,
            details={"retry": row.retry_count},
        )

    async def pause(self, session: AsyncSession, *, run_id: str) -> None:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        row.paused = True
        row.updated_at = datetime.now(UTC)
        await session.flush()

    async def resume(self, session: AsyncSession, *, run_id: str) -> None:
        row = await session.get(RunRow, run_id)
        if row is None:
            raise KeyError(run_id)
        row.paused = False
        row.updated_at = datetime.now(UTC)
        await session.flush()

    async def heartbeat_worker(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        current_run_id: str | None,
        capabilities: dict[str, Any],
    ) -> None:
        timestamp = datetime.now(UTC)
        row = await session.get(WorkerRow, worker_id)
        if row is None:
            session.add(
                WorkerRow(
                    worker_id=worker_id,
                    status="active",
                    current_run_id=current_run_id,
                    capabilities=capabilities,
                    started_at=timestamp,
                    heartbeat_at=timestamp,
                )
            )
        else:
            row.status = "active"
            row.current_run_id = current_run_id
            row.capabilities = capabilities
            row.heartbeat_at = timestamp
        await session.flush()

    async def recover_stale_workers(
        self,
        session: AsyncSession,
        *,
        stale_before: datetime,
    ) -> tuple[str, ...]:
        rows = (
            await session.scalars(
                select(WorkerRow).where(
                    WorkerRow.status == "active", WorkerRow.heartbeat_at < stale_before
                )
            )
        ).all()
        identifiers: list[str] = []
        for row in rows:
            row.status = "stale"
            identifiers.append(row.worker_id)
            await session.execute(
                update(RunRow)
                .where(RunRow.lease_owner == row.worker_id)
                .values(lease_owner=None, lease_token=None, lease_expires_at=None)
            )
        await session.flush()
        return tuple(sorted(identifiers))

    @staticmethod
    def _validate_transition(from_state: RunState, to_state: RunState, row: RunRow) -> None:
        if from_state in _TERMINAL:
            raise InvalidTransitionError(f"terminal state {from_state.value} cannot transition")
        if to_state in {
            RunState.FAILED_RETRYABLE,
            RunState.FAILED_TERMINAL,
            RunState.REVIEW_REQUIRED,
        }:
            return
        if from_state == RunState.FAILED_RETRYABLE:
            expected = row.payload.get("retry_from_state")
            if to_state.value != expected:
                raise InvalidTransitionError(f"retry must resume at {expected}")
            return
        expected_state = _NEXT.get(from_state)
        if to_state != expected_state:
            raise InvalidTransitionError(
                f"invalid transition {from_state.value} -> {to_state.value}; "
                f"expected {expected_state}"
            )
