from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any, Concatenate, Literal
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import (
    InformationClass,
    ProcessArtifactRef,
    information_record_from_row,
)
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAdmissionDisposition,
    AmberStatus,
)
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import ArtifactRef, RightsUse
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    AmberAuthorizationRow,
    ArtifactInformationRow,
    ArtifactReferenceRow,
    ArtifactRow,
    ProcessDistributionRow,
    ProcessEventRow,
    ProcessExecutionRow,
    ProcessForkChildRow,
    ProcessForkRow,
    ProcessOutcomeRow,
    ProcessProgramRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessTrainingEligibilityRow,
    ProcessWorkerInvocationRow,
    ProjectInstanceRow,
)
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessEventKind,
    ProcessEventRecord,
    ProcessExecutionManifest,
    ProcessForkChild,
    ProcessForkRecord,
    ProcessLearningLane,
    ProcessOutcomeAssessment,
    ProcessProgram,
    ProcessRolloutRecord,
    ProcessTrainingEligibilityDecision,
    ProjectBudgetUsage,
    ProjectInstance,
    ProjectSplit,
    ProjectStatePayload,
    ProjectStateVersion,
    RewardAuthorityKind,
    RolloutStatus,
    StoredProcessArtifactRef,
    process_event_digest,
    project_state_digest,
    stored_process_reference_id,
)
from padawan.pprl.evidence import ProcessEvidenceStore
from padawan.pprl.evidence_contracts import ProcessEvidenceUse


class ProcessInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedProcessRollout:
    rollout: ProcessRolloutRecord
    state: ProjectStateVersion
    lease_token: str


@dataclass(frozen=True)
class ProcessForkChildPlan:
    condition_id: str
    rollout_id: str
    replication_index: int
    execution: ProcessExecutionManifest


_TERMINAL_ROLLOUTS = {
    RolloutStatus.COMPLETE,
    RolloutStatus.FAILED,
    RolloutStatus.QUARANTINED,
    RolloutStatus.CANCELLED,
}


def _atomic_process_write[**P, R](
    operation: Callable[Concatenate[ProcessStore, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[ProcessStore, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(
        self: ProcessStore, session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        # Keep mutations atomic even when a caller catches an error and commits
        # unrelated work in its outer transaction.
        async with session.begin_nested():
            return await operation(self, session, *args, **kwargs)

    return wrapped


class ProcessStore:
    """Durable macro-rollouts with immutable state/event lineage and leased actions."""

    def __init__(
        self, amber: AmberStore | None = None, *, evidence: ProcessEvidenceStore | None = None
    ) -> None:
        self.amber = amber or AmberStore()
        self.evidence = evidence

    async def register_execution(
        self,
        session: AsyncSession,
        execution: ProcessExecutionManifest,
    ) -> str:
        digest = sha256_digest(execution)
        existing = await session.get(ProcessExecutionRow, digest)
        if existing is not None:
            if existing.record_json != execution.model_dump(mode="json"):
                raise ProcessInvariantError("process execution digest collision")
            return digest
        duplicate = await session.scalar(
            select(ProcessExecutionRow).where(
                ProcessExecutionRow.execution_id == execution.execution_id
            )
        )
        if duplicate is not None:
            raise ProcessInvariantError("process execution ID already has different content")
        program_row = await session.get(ProcessProgramRow, execution.program_digest)
        instance_row = await session.scalar(
            select(ProjectInstanceRow).where(
                ProjectInstanceRow.instance_digest == execution.instance_digest
            )
        )
        if program_row is None or instance_row is None:
            raise ProcessInvariantError("execution cites an unknown program or instance")
        program = ProcessProgram.model_validate(program_row.record_json, strict=False)
        instance = ProjectInstance.model_validate(instance_row.record_json, strict=False)
        envelope = await self.amber.get(
            session,
            authorization_digest=execution.amber_authorization_digest,
        )
        if program.distribution_digest != execution.distribution_digest:
            raise ProcessInvariantError("execution distribution differs from its program")
        if instance.distribution_digest != execution.distribution_digest:
            raise ProcessInvariantError("execution distribution differs from its instance")
        if sha256_digest(instance) != execution.instance_digest:
            raise ProcessInvariantError("execution instance digest is invalid")
        if execution.environment_fingerprint != instance.environment_fingerprint:
            raise ProcessInvariantError("execution environment differs from its instance")
        if execution.environment_fingerprint != envelope.environment.environment_fingerprint:
            raise ProcessInvariantError("execution environment differs from Amber")
        if envelope.program_digest != execution.program_digest:
            raise ProcessInvariantError("execution program differs from Amber")
        if envelope.distribution_digest != execution.distribution_digest:
            raise ProcessInvariantError("execution distribution differs from Amber")
        worker_digests = tuple(sha256_digest(worker) for worker in execution.worker_models)
        if not set(worker_digests).issubset(set(envelope.allowed_worker_model_digests)):
            raise ProcessInvariantError("execution worker pool exceeds Amber authority")
        session.add(
            ProcessExecutionRow(
                execution_digest=digest,
                execution_id=execution.execution_id,
                program_digest=execution.program_digest,
                distribution_digest=execution.distribution_digest,
                instance_digest=execution.instance_digest,
                authorization_digest=execution.amber_authorization_digest,
                environment_fingerprint=execution.environment_fingerprint,
                record_json=execution.model_dump(mode="json"),
                created_at=execution.created_at,
            )
        )
        await session.flush()
        return digest

    @_atomic_process_write
    async def create_rollout(
        self,
        session: AsyncSession,
        *,
        execution_digest: str,
        replication_index: int,
        initial_state: ProjectStatePayload,
        rollout_id: str | None = None,
        parent_rollout_id: str | None = None,
        fork_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ProcessRolloutRecord:
        initial_state = ProjectStatePayload.model_validate_json(initial_state.model_dump_json())
        initial_references = _new_process_references(initial_state.artifact_refs)
        if replication_index < 0:
            raise ValueError("replication index cannot be negative")
        if (parent_rollout_id is None) != (fork_id is None):
            raise ValueError("forked rollout requires parent rollout and fork identities")
        execution_row = await session.get(ProcessExecutionRow, execution_digest)
        if execution_row is None:
            raise ProcessInvariantError("rollout cites an unregistered process execution")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        instance_row = await session.scalar(
            select(ProjectInstanceRow).where(
                ProjectInstanceRow.instance_digest == execution.instance_digest
            )
        )
        if instance_row is None:
            raise ProcessInvariantError("process execution lost its project instance")
        instance = ProjectInstance.model_validate(instance_row.record_json, strict=False)
        timestamp = _as_utc(created_at or datetime.now(UTC))
        envelope = await self.amber.get(
            session, authorization_digest=execution.amber_authorization_digest
        )
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(
                AmberAuthorizationHeadRow.authorization_digest
                == execution.amber_authorization_digest
            )
            .with_for_update()
        )
        if head is None or AmberStatus(head.status) != AmberStatus.ACTIVE:
            raise PermissionError("process rollout requires active Amber authorization")
        if timestamp < _as_utc(head.updated_at) or timestamp >= envelope.expires_at:
            raise PermissionError("process rollout creation falls outside active Amber authority")
        if instance.split not in envelope.allowed_splits:
            raise PermissionError("process rollout split is not authorized by Amber")
        await self._validate_references(
            session, initial_references, execution_digest=execution_digest, now=timestamp
        )
        if (
            sum(reference.size_bytes for reference in initial_references)
            > initial_state.budget_usage.artifact_bytes
            or initial_state.budget_usage.artifact_bytes > envelope.budgets.artifact_bytes
        ):
            raise PermissionError("initial process artifacts exceed their declared byte budget")
        assigned_id = rollout_id or f"process-rollout-{uuid4()}"
        existing = await session.get(ProcessRolloutRow, assigned_id)
        if existing is not None:
            record = _rollout_from_row(existing)
            if (
                record.execution_digest != execution_digest
                or record.replication_index != replication_index
                or record.parent_rollout_id != parent_rollout_id
                or record.fork_id != fork_id
            ):
                raise ProcessInvariantError("rollout ID reused with different execution")
            previous = await self.get_state(session, state_id=record.initial_state_id)
            if previous.payload != initial_state:
                raise ProcessInvariantError("rollout ID reused with different initial state")
            await self._validate_state_references(
                session, previous, execution_digest=execution_digest, now=timestamp
            )
            return record
        duplicate = await session.scalar(
            select(ProcessRolloutRow).where(
                ProcessRolloutRow.execution_digest == execution_digest,
                ProcessRolloutRow.replication_index == replication_index,
            )
        )
        if duplicate is not None:
            record = _rollout_from_row(duplicate)
            previous = await self.get_state(session, state_id=record.initial_state_id)
            if (
                (rollout_id is not None and record.rollout_id != rollout_id)
                or record.parent_rollout_id != parent_rollout_id
                or record.fork_id != fork_id
                or previous.payload != initial_state
            ):
                raise ProcessInvariantError("replication reused with different rollout content")
            await self._validate_state_references(
                session, previous, execution_digest=execution_digest, now=timestamp
            )
            return record
        state_id = f"process-state-{uuid4()}"
        state = _build_state(
            state_id=state_id,
            rollout_id=assigned_id,
            sequence=0,
            parent_state_id=None,
            triggering_event_id=None,
            payload=initial_state,
            created_at=timestamp,
        )
        row = ProcessRolloutRow(
            rollout_id=assigned_id,
            execution_digest=execution_digest,
            program_digest=execution.program_digest,
            distribution_digest=execution.distribution_digest,
            instance_id=instance.instance_id,
            authorization_digest=execution.amber_authorization_digest,
            split=instance.split.value,
            replication_index=replication_index,
            seed=execution.seed,
            status=RolloutStatus.ACTIVE.value,
            initial_state_id=state.state_id,
            current_state_id=state.state_id,
            sequence=0,
            parent_rollout_id=parent_rollout_id,
            fork_id=fork_id,
            paused=False,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            last_error=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(row)
        await session.flush()
        session.add(_state_row(state))
        await session.flush()
        await self._retain_references(
            session,
            initial_references,
            execution_digest=execution_digest,
            owner_type="process_state",
            owner_id=state.state_id,
            now=timestamp,
        )
        return _rollout_from_row(row)

    async def get_rollout(self, session: AsyncSession, *, rollout_id: str) -> ProcessRolloutRecord:
        row = await session.get(ProcessRolloutRow, rollout_id)
        if row is None:
            raise KeyError(rollout_id)
        return _rollout_from_row(row)

    async def get_state(self, session: AsyncSession, *, state_id: str) -> ProjectStateVersion:
        row = await session.get(ProcessStateRow, state_id)
        if row is None:
            raise KeyError(state_id)
        return _state_from_row(row)

    @_atomic_process_write
    async def claim_next(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        lease_for: timedelta,
        now: datetime | None = None,
    ) -> ClaimedProcessRollout | None:
        if lease_for <= timedelta(0):
            raise ValueError("process lease duration must be positive")
        timestamp = _as_utc(now or datetime.now(UTC))
        query = (
            select(ProcessRolloutRow)
            .join(
                AmberAuthorizationHeadRow,
                AmberAuthorizationHeadRow.authorization_digest
                == ProcessRolloutRow.authorization_digest,
            )
            .join(
                AmberAuthorizationRow,
                AmberAuthorizationRow.authorization_digest
                == ProcessRolloutRow.authorization_digest,
            )
            .where(
                ProcessRolloutRow.status == RolloutStatus.ACTIVE.value,
                ProcessRolloutRow.paused.is_(False),
                AmberAuthorizationHeadRow.status == AmberStatus.ACTIVE.value,
                AmberAuthorizationRow.expires_at > timestamp,
                or_(
                    ProcessRolloutRow.lease_expires_at.is_(None),
                    ProcessRolloutRow.lease_expires_at <= timestamp,
                ),
            )
            .order_by(ProcessRolloutRow.updated_at, ProcessRolloutRow.rollout_id)
        )
        dialect = session.bind.dialect.name if session.bind is not None else "unknown"
        token = f"process-lease-{uuid4()}"
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
            candidate = query.with_only_columns(ProcessRolloutRow.rollout_id).limit(1)
            row = await session.scalar(
                update(ProcessRolloutRow)
                .where(
                    ProcessRolloutRow.rollout_id == candidate.scalar_subquery(),
                    or_(
                        ProcessRolloutRow.lease_expires_at.is_(None),
                        ProcessRolloutRow.lease_expires_at <= timestamp,
                    ),
                )
                .values(
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires,
                    updated_at=timestamp,
                )
                .returning(ProcessRolloutRow)
            )
            if row is None:
                return None
        state = await self.get_state(session, state_id=row.current_state_id)
        await self._validate_state_references(
            session, state, execution_digest=row.execution_digest, now=timestamp
        )
        return ClaimedProcessRollout(rollout=_rollout_from_row(row), state=state, lease_token=token)

    @_atomic_process_write
    async def append_event(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        lease_token: str,
        amber_decision_id: str,
        kind: ProcessEventKind,
        actor_id: str,
        payload: dict[str, Any],
        resulting_state: ProjectStatePayload,
        artifact_refs: tuple[ProcessArtifactRef, ...] = (),
        worker_invocation_id: str | None = None,
        research_execution_digest: str | None = None,
        to_status: RolloutStatus = RolloutStatus.ACTIVE,
        event_id: str | None = None,
        resulting_state_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> tuple[ProcessEventRecord, ProjectStateVersion]:
        resulting_state = ProjectStatePayload.model_validate_json(resulting_state.model_dump_json())
        artifact_refs = _new_process_references(artifact_refs)
        state_references = _new_process_references(resulting_state.artifact_refs)
        cited_artifacts = _new_process_references((*artifact_refs, *state_references))
        timestamp = _as_utc(occurred_at or datetime.now(UTC))
        assigned_event_id = event_id or f"process-event-{uuid4()}"
        existing_event = await session.get(ProcessEventRow, assigned_event_id)
        if existing_event is not None:
            event = _event_from_row(existing_event)
            state = await self.get_state(session, state_id=event.resulting_state_id)
            if (
                event.rollout_id != rollout_id
                or event.kind != kind
                or event.actor_id != actor_id
                or event.amber_decision_id != amber_decision_id
                or event.worker_invocation_id != worker_invocation_id
                or event.research_execution_digest != research_execution_digest
                or event.rollout_status != to_status
                or event.payload != payload
                or event.artifact_refs != artifact_refs
                or state.payload != resulting_state
                or (resulting_state_id is not None and state.state_id != resulting_state_id)
                or (occurred_at is not None and event.created_at != occurred_at)
            ):
                raise ProcessInvariantError("process event ID reused with different content")
            rollout = await self.get_rollout(session, rollout_id=rollout_id)
            await self._validate_state_references(
                session, state, execution_digest=rollout.execution_digest, now=timestamp
            )
            await self._validate_owned_references(
                session,
                artifact_refs,
                execution_digest=rollout.execution_digest,
                owner_type="process_event",
                owner_id=event.event_id,
                now=timestamp,
            )
            return event, state
        decision = await session.get(AmberAdmissionDecisionRow, amber_decision_id)
        if decision is None:
            raise PermissionError("process event has no Amber admission decision")
        decision_record = AmberAdmissionDecision.model_validate(decision.record_json, strict=False)
        request_record = AmberActionRequest.model_validate(decision.request_json, strict=False)
        if (
            sha256_digest(decision_record) != decision.record_digest
            or sha256_digest(request_record) != decision.request_digest
            or decision_record.request_digest != decision.request_digest
            or decision_record.authorization_sequence != decision.authorization_sequence
            or decision_record.disposition.value != decision.disposition
            or decision_record.authorization_digest != decision.authorization_digest
            or decision_record.rollout_id != decision.rollout_id
        ):
            raise ProcessInvariantError("process event cites a corrupted Amber decision")
        if (
            decision.rollout_id != rollout_id
            or decision.disposition != AmberAdmissionDisposition.ADMITTED.value
            or request_record.rollout_id != rollout_id
            or request_record.authorization_digest != decision.authorization_digest
            or _as_utc(request_record.requested_at) != _as_utc(decision_record.decided_at)
        ):
            raise PermissionError("process event does not cite an admitted Amber decision")
        row = await session.scalar(
            select(ProcessRolloutRow)
            .where(
                ProcessRolloutRow.rollout_id == rollout_id,
                ProcessRolloutRow.lease_token == lease_token,
            )
            .with_for_update()
        )
        if row is None:
            raise ProcessInvariantError("process rollout lease is missing or stale")
        if row.lease_expires_at is None or timestamp >= _as_utc(row.lease_expires_at):
            raise ProcessInvariantError("process rollout lease expired before event commit")
        if request_record.lease_token_digest != sha256_digest(lease_token):
            raise PermissionError("Amber request belongs to a different rollout lease")
        current_status = RolloutStatus(row.status)
        if current_status != RolloutStatus.ACTIVE:
            raise ProcessInvariantError("only an active process rollout may append an event")
        if to_status not in {
            RolloutStatus.ACTIVE,
            RolloutStatus.PAUSED,
            RolloutStatus.REVIEW_REQUIRED,
            RolloutStatus.COMPLETE,
            RolloutStatus.FAILED,
            RolloutStatus.QUARANTINED,
            RolloutStatus.CANCELLED,
        }:
            raise ProcessInvariantError("invalid process rollout target status")
        if decision.authorization_digest != row.authorization_digest:
            raise PermissionError("Amber decision belongs to a different authorization")
        if (
            request_record.program_digest != row.program_digest
            or request_record.distribution_digest != row.distribution_digest
            or request_record.split.value != row.split
        ):
            raise PermissionError("Amber request differs from the current rollout identity")
        authorization_head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == row.authorization_digest)
            .with_for_update()
        )
        if (
            authorization_head is None
            or authorization_head.status != AmberStatus.ACTIVE.value
            or authorization_head.sequence != decision_record.authorization_sequence
        ):
            raise PermissionError("Amber authority changed after action admission")
        parent_state = await self.get_state(session, state_id=row.current_state_id)
        await self._validate_state_references(
            session, parent_state, execution_digest=row.execution_digest, now=timestamp
        )
        if (
            request_record.rollout_sequence != row.sequence
            or request_record.state_digest != parent_state.state_digest
        ):
            raise PermissionError("Amber request belongs to a different project-state head")
        if request_record.event_kind != kind:
            raise PermissionError("process event kind differs from its Amber admission")
        if request_record.projected_usage != resulting_state.budget_usage:
            raise PermissionError("process event budget differs from its Amber admission")
        if (
            request_record.projected_usage.actions != parent_state.payload.budget_usage.actions + 1
            or not _budget_is_monotonic(
                parent_state.payload.budget_usage,
                request_record.projected_usage,
            )
            or _as_utc(request_record.requested_at) < _as_utc(parent_state.created_at)
        ):
            raise PermissionError("Amber request is stale for the current project state")
        if timestamp < _as_utc(decision_record.decided_at):
            raise ProcessInvariantError("process event predates its Amber admission")
        envelope = await self.amber.get(session, authorization_digest=row.authorization_digest)
        if timestamp >= envelope.expires_at:
            raise PermissionError("process event completed after Amber authorization expired")
        await self._validate_references(
            session, cited_artifacts, execution_digest=row.execution_digest, now=timestamp
        )
        artifact_reservation = (
            request_record.projected_usage.artifact_bytes
            - parent_state.payload.budget_usage.artifact_bytes
        )
        prior_artifact_ids = {
            stored_process_reference_id(reference)
            for reference in parent_state.payload.artifact_refs
        }
        new_artifacts = {
            reference.process_artifact_id: reference
            for reference in cited_artifacts
            if reference.process_artifact_id not in prior_artifact_ids
        }
        forensic_bytes = 0
        bound_invocation_id = await session.scalar(
            select(ProcessWorkerInvocationRow.invocation_id).where(
                ProcessWorkerInvocationRow.amber_decision_id == amber_decision_id
            )
        )
        if bound_invocation_id is not None and worker_invocation_id != bound_invocation_id:
            raise ProcessInvariantError("process event must bind its admitted worker invocation")
        if worker_invocation_id is not None:
            invocation = await session.get(ProcessWorkerInvocationRow, worker_invocation_id)
            if invocation is None:
                raise ProcessInvariantError("process event cites an unknown worker invocation")
            if (
                invocation.rollout_id != rollout_id
                or invocation.amber_decision_id != amber_decision_id
                or invocation.role_id != request_record.role_id
                or invocation.worker_model_digest != request_record.worker_model_digest
                or invocation.status != "completed"
                or invocation.research_execution_digest != research_execution_digest
            ):
                raise ProcessInvariantError(
                    "process event worker invocation differs from admission"
                )
            forensic_bytes = await _invocation_forensic_bytes(session, invocation, timestamp)
        if sum(reference.size_bytes for reference in new_artifacts.values()) + forensic_bytes > (
            artifact_reservation
        ):
            raise PermissionError("process event artifacts exceed their Amber reservation")
        sequence = row.sequence + 1
        assigned_state_id = resulting_state_id or f"process-state-{uuid4()}"
        state = _build_state(
            state_id=assigned_state_id,
            rollout_id=rollout_id,
            sequence=sequence,
            parent_state_id=parent_state.state_id,
            triggering_event_id=assigned_event_id,
            payload=resulting_state,
            created_at=timestamp,
        )
        event = _build_event(
            event_id=assigned_event_id,
            rollout_id=rollout_id,
            sequence=sequence,
            kind=kind,
            actor_id=actor_id,
            lease_token_digest=sha256_digest(lease_token),
            parent_state_id=parent_state.state_id,
            resulting_state_id=state.state_id,
            worker_invocation_id=worker_invocation_id,
            research_execution_digest=research_execution_digest,
            amber_authorization_digest=row.authorization_digest,
            amber_decision_id=amber_decision_id,
            rollout_status=to_status,
            payload=payload,
            artifact_refs=artifact_refs,
            created_at=timestamp,
        )
        session.add(_state_row(state))
        await session.flush()
        session.add(_event_row(event))
        row.current_state_id = state.state_id
        row.sequence = sequence
        row.status = to_status.value
        row.paused = to_status == RolloutStatus.PAUSED
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = timestamp
        await session.flush()
        await self._retain_references(
            session,
            state_references,
            execution_digest=row.execution_digest,
            owner_type="process_state",
            owner_id=state.state_id,
            now=timestamp,
        )
        await self._retain_references(
            session,
            artifact_refs,
            execution_digest=row.execution_digest,
            owner_type="process_event",
            owner_id=event.event_id,
            now=timestamp,
        )
        return event, state

    async def _validate_references(
        self,
        session: AsyncSession,
        references: tuple[ProcessArtifactRef, ...],
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        if references and self.evidence is None:
            raise ProcessInvariantError("process references require an evidence admission boundary")
        for reference in references:
            assert self.evidence is not None
            await self.evidence.read(
                session,
                reference=reference,
                execution_digest=execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=now,
            )

    async def _retain_references(
        self,
        session: AsyncSession,
        references: tuple[ProcessArtifactRef, ...],
        *,
        execution_digest: str,
        owner_type: Literal["process_state", "process_event"],
        owner_id: str,
        now: datetime,
    ) -> None:
        if references and self.evidence is None:
            raise ProcessInvariantError("process references require an evidence admission boundary")
        for reference in references:
            assert self.evidence is not None
            await self.evidence.retain_for_process(
                session,
                reference=reference,
                execution_digest=execution_digest,
                owner_type=owner_type,
                owner_id=owner_id,
                now=now,
            )

    async def _validate_owned_references(
        self,
        session: AsyncSession,
        references: tuple[ProcessArtifactRef, ...],
        *,
        execution_digest: str,
        owner_type: Literal["process_state", "process_event"],
        owner_id: str,
        now: datetime,
    ) -> None:
        await self._validate_references(
            session, references, execution_digest=execution_digest, now=now
        )
        if self.evidence is not None:
            await self.evidence.validate_process_ownership(
                session,
                references=references,
                execution_digest=execution_digest,
                owner_type=owner_type,
                owner_id=owner_id,
                now=now,
            )

    async def _validate_state_references(
        self,
        session: AsyncSession,
        state: ProjectStateVersion,
        *,
        execution_digest: str,
        now: datetime,
    ) -> None:
        await self._validate_owned_references(
            session,
            _new_process_references(state.payload.artifact_refs),
            execution_digest=execution_digest,
            owner_type="process_state",
            owner_id=state.state_id,
            now=now,
        )

    async def record_outcome(
        self,
        session: AsyncSession,
        assessment: ProcessOutcomeAssessment,
    ) -> str:
        rollout = await session.get(ProcessRolloutRow, assessment.rollout_id)
        if rollout is None:
            raise KeyError(assessment.rollout_id)
        program_row = await session.get(ProcessProgramRow, rollout.program_digest)
        if program_row is None:
            raise ProcessInvariantError("rollout lost its process program")
        program = ProcessProgram.model_validate(program_row.record_json, strict=False)
        if assessment.authority != program.reward_authority:
            raise ProcessInvariantError("outcome authority differs from the process program")
        existing = await session.get(ProcessOutcomeRow, assessment.assessment_id)
        if existing is not None:
            if existing.record_json != assessment.model_dump(mode="json"):
                raise ProcessInvariantError("outcome assessment ID reused with different content")
            return existing.record_digest
        digest = sha256_digest(assessment)
        session.add(
            ProcessOutcomeRow(
                assessment_id=assessment.assessment_id,
                rollout_id=assessment.rollout_id,
                authority_kind=assessment.authority.kind.value,
                eligible_for_learning=assessment.eligible_for_learning,
                scalar_return=(
                    float(assessment.scalar_return)
                    if assessment.scalar_return is not None
                    else None
                ),
                record_digest=digest,
                record_json=assessment.model_dump(mode="json"),
                created_at=assessment.created_at,
            )
        )
        await session.flush()
        return digest

    async def record_training_eligibility(
        self,
        session: AsyncSession,
        decision: ProcessTrainingEligibilityDecision,
    ) -> str:
        rollout = await session.get(ProcessRolloutRow, decision.rollout_id)
        if rollout is None:
            raise KeyError(decision.rollout_id)
        distribution_row = await session.get(ProcessDistributionRow, rollout.distribution_digest)
        if distribution_row is None:
            raise ProcessInvariantError("rollout lost its process distribution")
        distribution = ProcessDistributionManifest.model_validate(
            distribution_row.record_json, strict=False
        )
        execution_row = await session.get(ProcessExecutionRow, rollout.execution_digest)
        if execution_row is None:
            raise ProcessInvariantError("rollout lost its process execution")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        authorization = await self.amber.get(
            session, authorization_digest=rollout.authorization_digest
        )
        authorization_head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == rollout.authorization_digest)
            .with_for_update()
        )
        if authorization_head is None:
            raise ProcessInvariantError("rollout lost its Amber lifecycle state")
        authorization_status = AmberStatus(authorization_head.status)
        required_rights = {
            sha256_digest(distribution.rights),
            sha256_digest(execution.output_rights),
        }
        if not required_rights.issubset(set(decision.rights_digests)):
            raise ProcessInvariantError("process eligibility omits source or output rights")
        outcomes: list[ProcessOutcomeAssessment] = []
        for assessment_id in decision.outcome_assessment_ids:
            row = await session.get(ProcessOutcomeRow, assessment_id)
            if row is None or row.rollout_id != decision.rollout_id:
                raise ProcessInvariantError("process eligibility cites an invalid outcome")
            outcomes.append(ProcessOutcomeAssessment.model_validate(row.record_json, strict=False))
        if decision.eligible:
            if not authorization.checkpoint_policy.training_permitted:
                raise PermissionError("Amber authorization does not permit process training")
            if authorization_status not in {
                AmberStatus.ACTIVE,
                AmberStatus.PAUSED,
                AmberStatus.RELEASE_APPROVED,
            }:
                raise PermissionError(
                    "Amber authorization lifecycle does not permit process training"
                )
            if not all(outcome.eligible_for_learning for outcome in outcomes):
                raise ProcessInvariantError("ineligible outcome cannot admit process training")
            if not distribution.rights.permits(RightsUse.PROCESS):
                raise PermissionError("distribution rights do not permit process training")
            if not execution.output_rights.permits(RightsUse.PROCESS):
                raise PermissionError("process output rights do not permit process training")
            if ProcessLearningLane.VERIFIABLE_REPLAY in decision.allowed_lanes:
                if not (
                    distribution.rights.permits(RightsUse.RLVR)
                    and execution.output_rights.permits(RightsUse.RLVR)
                ):
                    raise PermissionError("process source or output rights do not permit RLVR")
                if not all(
                    outcome.authority.kind == RewardAuthorityKind.VERIFIABLE for outcome in outcomes
                ):
                    raise ProcessInvariantError(
                        "verifiable replay lane requires verifiable outcome authority"
                    )
        existing = await session.get(ProcessTrainingEligibilityRow, decision.decision_id)
        if existing is not None:
            if existing.record_json != decision.model_dump(mode="json"):
                raise ProcessInvariantError("process eligibility ID reused with different content")
            return existing.record_digest
        digest = sha256_digest(decision)
        session.add(
            ProcessTrainingEligibilityRow(
                decision_id=decision.decision_id,
                rollout_id=decision.rollout_id,
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                eligible=decision.eligible,
                record_digest=digest,
                record_json=decision.model_dump(mode="json"),
                created_at=decision.created_at,
            )
        )
        await session.flush()
        return digest

    async def release_claim(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        lease_token: str,
        to_status: RolloutStatus,
        error: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> ProcessRolloutRecord:
        if to_status not in {
            RolloutStatus.ACTIVE,
            RolloutStatus.PAUSED,
            RolloutStatus.REVIEW_REQUIRED,
            RolloutStatus.FAILED,
            RolloutStatus.QUARANTINED,
            RolloutStatus.CANCELLED,
        }:
            raise ProcessInvariantError("invalid release status for a process claim")
        row = await session.scalar(
            select(ProcessRolloutRow)
            .where(
                ProcessRolloutRow.rollout_id == rollout_id,
                ProcessRolloutRow.lease_token == lease_token,
            )
            .with_for_update()
        )
        if row is None:
            raise ProcessInvariantError("process rollout lease is missing or stale")
        row.status = to_status.value
        row.paused = to_status == RolloutStatus.PAUSED
        row.last_error = error
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.updated_at = occurred_at or datetime.now(UTC)
        await session.flush()
        return _rollout_from_row(row)

    @_atomic_process_write
    async def fork_rollout(
        self,
        session: AsyncSession,
        *,
        parent_rollout_id: str,
        lease_token: str,
        amber_decision_id: str,
        actor_id: str,
        children: tuple[ProcessForkChildPlan, ...],
        intervention: dict[str, Any],
        fork_id: str | None = None,
        event_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ProcessForkRecord:
        if len(children) < 2:
            raise ValueError("process fork requires at least two continuations")
        ordered = tuple(sorted(children, key=lambda child: child.condition_id))
        condition_ids = tuple(child.condition_id for child in ordered)
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("process fork conditions must be unique")
        assigned_fork_id = fork_id or f"process-fork-{uuid4()}"
        existing = await session.get(ProcessForkRow, assigned_fork_id)
        if existing is not None:
            record = ProcessForkRecord.model_validate(existing.record_json, strict=False)
            if (
                sha256_digest(record) != existing.record_digest
                or record.parent_rollout_id != parent_rollout_id
                or record.intervention != intervention
                or record.children
                != tuple(
                    ProcessForkChild(
                        rollout_id=child.rollout_id,
                        condition_id=child.condition_id,
                        seed=child.execution.seed,
                    )
                    for child in ordered
                )
                or (occurred_at is not None and record.created_at != occurred_at)
            ):
                raise ProcessInvariantError("process fork ID reused with different content")
            for child in ordered:
                rollout = await self.get_rollout(session, rollout_id=child.rollout_id)
                if (
                    rollout.execution_digest != sha256_digest(child.execution)
                    or rollout.replication_index != child.replication_index
                    or rollout.parent_rollout_id != parent_rollout_id
                    or rollout.fork_id != assigned_fork_id
                ):
                    raise ProcessInvariantError("process fork retry differs from its child rollout")
                state = await self.get_state(session, state_id=rollout.initial_state_id)
                await self._validate_state_references(
                    session,
                    state,
                    execution_digest=rollout.execution_digest,
                    now=_as_utc(occurred_at or datetime.now(UTC)),
                )
            return record
        parent_row = await session.get(ProcessRolloutRow, parent_rollout_id)
        if parent_row is None:
            raise KeyError(parent_rollout_id)
        if parent_row.lease_token != lease_token:
            raise ProcessInvariantError("process fork requires the parent rollout lease")
        parent_state = await self.get_state(session, state_id=parent_row.current_state_id)
        admission_row = await session.get(AmberAdmissionDecisionRow, amber_decision_id)
        if admission_row is None:
            raise PermissionError("process fork requires an Amber admission")
        fork_request = AmberActionRequest.model_validate(admission_row.request_json, strict=False)
        fork_state_payload = parent_state.payload.model_copy(
            update={"budget_usage": fork_request.projected_usage}
        )
        child_execution_digests: list[str] = []
        for child in ordered:
            execution = child.execution
            if (
                execution.program_digest != parent_row.program_digest
                or execution.distribution_digest != parent_row.distribution_digest
                or execution.amber_authorization_digest != parent_row.authorization_digest
            ):
                raise ProcessInvariantError("fork child crosses the parent execution boundary")
            execution_row = await session.get(ProcessExecutionRow, parent_row.execution_digest)
            if execution_row is None:
                raise ProcessInvariantError("parent rollout lost its process execution")
            if execution.instance_digest != execution_row.instance_digest:
                raise ProcessInvariantError("fork child changes the parent project instance")
            child_execution_digests.append(await self.register_execution(session, execution))
        fork_event, fork_state = await self.append_event(
            session,
            rollout_id=parent_rollout_id,
            lease_token=lease_token,
            amber_decision_id=amber_decision_id,
            kind=ProcessEventKind.ROLLOUT_FORKED,
            actor_id=actor_id,
            payload={
                "conditions": list(condition_ids),
                "fork_id": assigned_fork_id,
                "intervention": {key: intervention[key] for key in sorted(intervention)},
            },
            resulting_state=fork_state_payload,
            to_status=RolloutStatus.ACTIVE,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        child_records: list[ProcessForkChild] = []
        for child, execution_digest in zip(ordered, child_execution_digests, strict=True):
            rollout = await self.create_rollout(
                session,
                execution_digest=execution_digest,
                replication_index=child.replication_index,
                initial_state=fork_state.payload,
                rollout_id=child.rollout_id,
                parent_rollout_id=parent_rollout_id,
                fork_id=assigned_fork_id,
                created_at=fork_event.created_at,
            )
            child_records.append(
                ProcessForkChild(
                    rollout_id=rollout.rollout_id,
                    condition_id=child.condition_id,
                    seed=child.execution.seed,
                )
            )
        record = ProcessForkRecord(
            fork_id=assigned_fork_id,
            parent_rollout_id=parent_rollout_id,
            parent_state_id=fork_state.state_id,
            intervention={key: intervention[key] for key in sorted(intervention)},
            children=tuple(child_records),
            created_at=fork_event.created_at,
        )
        session.add(
            ProcessForkRow(
                fork_id=record.fork_id,
                parent_rollout_id=record.parent_rollout_id,
                parent_state_id=record.parent_state_id,
                record_digest=sha256_digest(record),
                record_json=record.model_dump(mode="json"),
                created_at=record.created_at,
            )
        )
        await session.flush()
        session.add_all(
            [
                ProcessForkChildRow(
                    fork_id=record.fork_id,
                    rollout_id=child.rollout_id,
                    condition_id=child.condition_id,
                    seed=child.seed,
                )
                for child in record.children
            ]
        )
        await session.flush()
        return record

    async def replay(
        self, session: AsyncSession, *, rollout_id: str
    ) -> tuple[ProjectStateVersion, tuple[ProcessEventRecord, ...]]:
        rollout = await self.get_rollout(session, rollout_id=rollout_id)
        state_rows = (
            await session.scalars(
                select(ProcessStateRow)
                .where(ProcessStateRow.rollout_id == rollout_id)
                .order_by(ProcessStateRow.sequence)
            )
        ).all()
        event_rows = (
            await session.scalars(
                select(ProcessEventRow)
                .where(ProcessEventRow.rollout_id == rollout_id)
                .order_by(ProcessEventRow.sequence)
            )
        ).all()
        states = tuple(_state_from_row(row) for row in state_rows)
        events = tuple(_event_from_row(row) for row in event_rows)
        if len(states) != len(events) + 1:
            raise ProcessInvariantError("process replay has a state/event cardinality gap")
        if not states or states[0].state_id != rollout.initial_state_id:
            raise ProcessInvariantError("process replay lost its initial state")
        for index, event in enumerate(events, start=1):
            if event.sequence != index or states[index].sequence != index:
                raise ProcessInvariantError("process replay sequence is discontinuous")
            if event.parent_state_id != states[index - 1].state_id:
                raise ProcessInvariantError("process event parent state is invalid")
            if event.resulting_state_id != states[index].state_id:
                raise ProcessInvariantError("process event result state is invalid")
        if states[-1].state_id != rollout.current_state_id:
            raise ProcessInvariantError("process replay head differs from rollout head")
        if len(events) != rollout.sequence:
            raise ProcessInvariantError("process replay sequence differs from rollout sequence")
        return states[0], events


def _build_state(
    *,
    state_id: str,
    rollout_id: str,
    sequence: int,
    parent_state_id: str | None,
    triggering_event_id: str | None,
    payload: ProjectStatePayload,
    created_at: datetime,
) -> ProjectStateVersion:
    digest = project_state_digest(
        state_id=state_id,
        rollout_id=rollout_id,
        sequence=sequence,
        parent_state_id=parent_state_id,
        triggering_event_id=triggering_event_id,
        payload=payload,
        created_at=created_at,
    )
    return ProjectStateVersion(
        state_id=state_id,
        rollout_id=rollout_id,
        sequence=sequence,
        parent_state_id=parent_state_id,
        triggering_event_id=triggering_event_id,
        payload=payload,
        state_digest=digest,
        created_at=created_at,
    )


def _build_event(
    *,
    event_id: str,
    rollout_id: str,
    sequence: int,
    kind: ProcessEventKind,
    actor_id: str,
    lease_token_digest: str,
    parent_state_id: str,
    resulting_state_id: str,
    worker_invocation_id: str | None,
    research_execution_digest: str | None,
    amber_authorization_digest: str,
    amber_decision_id: str,
    rollout_status: RolloutStatus,
    payload: dict[str, Any],
    artifact_refs: tuple[StoredProcessArtifactRef, ...],
    created_at: datetime,
) -> ProcessEventRecord:
    digest = process_event_digest(
        event_id=event_id,
        rollout_id=rollout_id,
        sequence=sequence,
        kind=kind,
        actor_id=actor_id,
        lease_token_digest=lease_token_digest,
        parent_state_id=parent_state_id,
        resulting_state_id=resulting_state_id,
        worker_invocation_id=worker_invocation_id,
        research_execution_digest=research_execution_digest,
        amber_authorization_digest=amber_authorization_digest,
        amber_decision_id=amber_decision_id,
        rollout_status=rollout_status,
        payload=payload,
        artifact_refs=artifact_refs,
        created_at=created_at,
    )
    return ProcessEventRecord(
        event_id=event_id,
        rollout_id=rollout_id,
        sequence=sequence,
        kind=kind,
        actor_id=actor_id,
        lease_token_digest=lease_token_digest,
        parent_state_id=parent_state_id,
        resulting_state_id=resulting_state_id,
        worker_invocation_id=worker_invocation_id,
        research_execution_digest=research_execution_digest,
        amber_authorization_digest=amber_authorization_digest,
        amber_decision_id=amber_decision_id,
        rollout_status=rollout_status,
        payload=payload,
        artifact_refs=artifact_refs,
        event_digest=digest,
        created_at=created_at,
    )


def _rollout_from_row(row: ProcessRolloutRow) -> ProcessRolloutRecord:
    return ProcessRolloutRecord(
        rollout_id=row.rollout_id,
        execution_digest=row.execution_digest,
        program_digest=row.program_digest,
        distribution_digest=row.distribution_digest,
        instance_id=row.instance_id,
        split=ProjectSplit(row.split),
        replication_index=row.replication_index,
        seed=row.seed,
        status=RolloutStatus(row.status),
        initial_state_id=row.initial_state_id,
        current_state_id=row.current_state_id,
        sequence=row.sequence,
        parent_rollout_id=row.parent_rollout_id,
        fork_id=row.fork_id,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )


def _state_row(state: ProjectStateVersion) -> ProcessStateRow:
    return ProcessStateRow(
        state_id=state.state_id,
        rollout_id=state.rollout_id,
        sequence=state.sequence,
        parent_state_id=state.parent_state_id,
        triggering_event_id=state.triggering_event_id,
        state_digest=state.state_digest,
        record_json=state.model_dump(mode="json"),
        created_at=state.created_at,
    )


def _state_from_row(row: ProcessStateRow) -> ProjectStateVersion:
    state = ProjectStateVersion.model_validate(row.record_json, strict=False)
    expected = project_state_digest(
        state_id=state.state_id,
        rollout_id=state.rollout_id,
        sequence=state.sequence,
        parent_state_id=state.parent_state_id,
        triggering_event_id=state.triggering_event_id,
        payload=state.payload,
        created_at=state.created_at,
    )
    if (
        state.state_digest != row.state_digest
        or state.state_digest != expected
        or state.state_id != row.state_id
        or state.rollout_id != row.rollout_id
        or state.sequence != row.sequence
        or state.parent_state_id != row.parent_state_id
        or state.triggering_event_id != row.triggering_event_id
        or _as_utc(state.created_at) != _as_utc(row.created_at)
    ):
        raise ProcessInvariantError("stored project state digest is invalid")
    return state


def _event_row(event: ProcessEventRecord) -> ProcessEventRow:
    return ProcessEventRow(
        event_id=event.event_id,
        rollout_id=event.rollout_id,
        sequence=event.sequence,
        kind=event.kind.value,
        actor_id=event.actor_id,
        parent_state_id=event.parent_state_id,
        resulting_state_id=event.resulting_state_id,
        worker_invocation_id=event.worker_invocation_id,
        research_execution_digest=event.research_execution_digest,
        authorization_digest=event.amber_authorization_digest,
        amber_decision_id=event.amber_decision_id,
        event_digest=event.event_digest,
        record_json=event.model_dump(mode="json"),
        created_at=event.created_at,
    )


def _event_from_row(row: ProcessEventRow) -> ProcessEventRecord:
    event = ProcessEventRecord.model_validate(row.record_json, strict=False)
    expected = process_event_digest(
        event_id=event.event_id,
        rollout_id=event.rollout_id,
        sequence=event.sequence,
        kind=event.kind,
        actor_id=event.actor_id,
        lease_token_digest=event.lease_token_digest,
        parent_state_id=event.parent_state_id,
        resulting_state_id=event.resulting_state_id,
        worker_invocation_id=event.worker_invocation_id,
        research_execution_digest=event.research_execution_digest,
        amber_authorization_digest=event.amber_authorization_digest,
        amber_decision_id=event.amber_decision_id,
        rollout_status=event.rollout_status,
        payload=event.payload,
        artifact_refs=event.artifact_refs,
        created_at=event.created_at,
    )
    if (
        event.event_digest != row.event_digest
        or event.event_digest != expected
        or event.event_id != row.event_id
        or event.rollout_id != row.rollout_id
        or event.sequence != row.sequence
        or event.kind.value != row.kind
        or event.actor_id != row.actor_id
        or event.parent_state_id != row.parent_state_id
        or event.resulting_state_id != row.resulting_state_id
        or event.worker_invocation_id != row.worker_invocation_id
        or event.research_execution_digest != row.research_execution_digest
        or event.amber_authorization_digest != row.authorization_digest
        or event.amber_decision_id != row.amber_decision_id
        or _as_utc(event.created_at) != _as_utc(row.created_at)
    ):
        raise ProcessInvariantError("stored process event digest is invalid")
    return event


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _new_process_references(
    references: tuple[StoredProcessArtifactRef, ...],
) -> tuple[ProcessArtifactRef, ...]:
    admitted: dict[str, ProcessArtifactRef] = {}
    for reference in references:
        if not isinstance(reference, ProcessArtifactRef):
            raise ProcessInvariantError(
                "new process references require reviewed admission; "
                "forensic/raw and legacy refs denied"
            )
        reference = ProcessArtifactRef.model_validate_json(reference.model_dump_json())
        previous = admitted.get(reference.process_artifact_id)
        if previous is not None and previous != reference:
            raise ProcessInvariantError("process reference ID has conflicting content")
        admitted[reference.process_artifact_id] = reference
    return tuple(admitted[key] for key in sorted(admitted))


async def _invocation_forensic_bytes(
    session: AsyncSession, invocation: ProcessWorkerInvocationRow, timestamp: datetime
) -> int:
    if invocation.completed_at is None or _as_utc(invocation.completed_at) > timestamp:
        raise ProcessInvariantError("process event predates its completed worker invocation")
    if invocation.request_artifact_id is None or invocation.response_artifact_id is None:
        raise ProcessInvariantError("worker invocation has incomplete forensic records")
    if invocation.request_artifact_id == invocation.response_artifact_id:
        raise ProcessInvariantError(
            "worker invocation request and response records are not distinct"
        )
    artifact_ids = {invocation.request_artifact_id, invocation.response_artifact_id}
    total = 0
    for artifact_id in artifact_ids:
        artifact = await session.get(ArtifactRow, artifact_id)
        information = await session.get(ArtifactInformationRow, artifact_id)
        if artifact is None or information is None:
            raise ProcessInvariantError("worker invocation lost its forensic classification")
        classified = information_record_from_row(information)
        if (
            classified.information_class != InformationClass.FORENSIC
            or not _artifact_matches(classified.artifact, artifact)
            or classified.classified_at > timestamp
        ):
            raise ProcessInvariantError("worker invocation has invalid forensic classification")
        pin = await session.scalar(
            select(ArtifactReferenceRow.reference_id).where(
                ArtifactReferenceRow.owner_type == "process_worker_invocation",
                ArtifactReferenceRow.owner_id == invocation.invocation_id,
                ArtifactReferenceRow.artifact_id == artifact_id,
            )
        )
        if pin is None:
            raise ProcessInvariantError("worker invocation lost forensic retention ownership")
        total += artifact.size_bytes
    return total


def _artifact_matches(reference: ArtifactRef, row: ArtifactRow) -> bool:
    return (
        reference.artifact_id == row.artifact_id
        and reference.uri == row.uri
        and reference.digest == row.digest
        and reference.media_type == row.media_type
        and reference.size_bytes == row.size_bytes
        and reference.restricted == row.restricted
        and reference.raw_data == row.raw_data
    )


def _budget_is_monotonic(previous: ProjectBudgetUsage, projected: ProjectBudgetUsage) -> bool:
    return (
        projected.input_tokens >= previous.input_tokens
        and projected.output_tokens >= previous.output_tokens
        and projected.artifact_bytes >= previous.artifact_bytes
        and float(projected.wall_time_seconds) >= float(previous.wall_time_seconds)
        and float(projected.cost) >= float(previous.cost)
    )
