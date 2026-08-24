from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAuthorizationEnvelope,
    AmberAuthorizationEvent,
    AmberPolicy,
    AmberStatus,
    assert_amber_transition,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationEventRow,
    AmberAuthorizationHeadRow,
    AmberAuthorizationRow,
    ProcessDistributionRow,
    ProcessExecutionRow,
    ProcessProgramRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.contracts import (
    ProcessDistributionManifest,
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectStateVersion,
    RolloutStatus,
    project_state_digest,
)


class AmberStore:
    """Immutable envelopes and events with one transactional mutable status head."""

    def __init__(self, policy: AmberPolicy | None = None) -> None:
        self.policy = policy or AmberPolicy()

    async def prepare(
        self,
        session: AsyncSession,
        *,
        envelope: AmberAuthorizationEnvelope,
        actor_id: str,
        evidence_refs: tuple[str, ...] = (),
    ) -> str:
        program = await session.get(ProcessProgramRow, envelope.program_digest)
        distribution = await session.get(ProcessDistributionRow, envelope.distribution_digest)
        if program is None or distribution is None:
            raise ValueError("Amber envelope cites an unregistered program or distribution")
        if program.distribution_digest != envelope.distribution_digest:
            raise ValueError("Amber program and distribution identities disagree")
        program_record = ProcessProgram.model_validate(program.record_json, strict=False)
        distribution_record = ProcessDistributionManifest.model_validate(
            distribution.record_json, strict=False
        )
        if set(envelope.persistence_modes) != {program_record.persistence_mode}:
            raise ValueError("Amber persistence authority differs from the process program")
        if envelope.budgets.concurrent_workers > program_record.maximum_concurrent_workers:
            raise ValueError("Amber worker concurrency exceeds the process program")
        partition_splits = {partition.split for partition in distribution_record.partitions}
        if not set(envelope.allowed_splits).issubset(partition_splits):
            raise ValueError("Amber authorizes a split outside the process distribution")
        roles = {role.role_id: role for role in program_record.worker_roles}
        tools = {tool.component_id: tool for tool in program_record.tools}
        for grant in envelope.tool_grants:
            role = roles.get(grant.role_id)
            if role is None:
                raise ValueError("Amber tool grant refers to an unknown worker role")
            if grant.tool_id not in role.allowed_tool_ids:
                raise ValueError("Amber tool grant exceeds the worker role")
            tool = tools.get(grant.tool_id)
            if tool is None or tool.digest != grant.tool_digest:
                raise ValueError("Amber tool grant differs from the program tool identity")
        digest = envelope.digest
        existing = await session.get(AmberAuthorizationRow, digest)
        if existing is not None:
            if existing.record_json != envelope.model_dump(mode="json"):
                raise ValueError("Amber authorization digest collision")
            return digest
        version_row = await session.scalar(
            select(AmberAuthorizationRow).where(
                AmberAuthorizationRow.authorization_id == envelope.authorization_id,
                AmberAuthorizationRow.version == envelope.version,
            )
        )
        if version_row is not None:
            raise ValueError("Amber authorization version already has different content")
        event = AmberAuthorizationEvent(
            event_id=f"amber-event-{uuid4()}",
            authorization_digest=digest,
            sequence=0,
            from_status=None,
            to_status=AmberStatus.PREPARED,
            actor_id=actor_id,
            reason="authorization envelope prepared",
            evidence_refs=tuple(sorted(evidence_refs)),
            created_at=envelope.created_at,
        )
        session.add(
            AmberAuthorizationRow(
                authorization_digest=digest,
                authorization_id=envelope.authorization_id,
                version=envelope.version,
                program_digest=envelope.program_digest,
                distribution_digest=envelope.distribution_digest,
                record_json=envelope.model_dump(mode="json"),
                created_at=envelope.created_at,
                expires_at=envelope.expires_at,
            )
        )
        await session.flush()
        session.add(
            AmberAuthorizationHeadRow(
                authorization_digest=digest,
                status=AmberStatus.PREPARED.value,
                sequence=0,
                updated_at=envelope.created_at,
            )
        )
        session.add(_event_row(event))
        await session.flush()
        return digest

    async def get(
        self, session: AsyncSession, *, authorization_digest: str
    ) -> AmberAuthorizationEnvelope:
        row = await session.get(AmberAuthorizationRow, authorization_digest)
        if row is None:
            raise KeyError(authorization_digest)
        if sha256_digest(row.record_json) != row.authorization_digest:
            raise ValueError("stored Amber envelope digest is invalid")
        return AmberAuthorizationEnvelope.model_validate(row.record_json, strict=False)

    async def status(self, session: AsyncSession, *, authorization_digest: str) -> AmberStatus:
        head = await session.get(AmberAuthorizationHeadRow, authorization_digest)
        if head is None:
            raise KeyError(authorization_digest)
        return AmberStatus(head.status)

    async def transition(
        self,
        session: AsyncSession,
        *,
        authorization_digest: str,
        to_status: AmberStatus,
        actor_id: str,
        reason: str,
        evidence_refs: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        event_id: str | None = None,
    ) -> AmberAuthorizationEvent:
        timestamp = occurred_at or datetime.now(UTC)
        envelope = await self.get(session, authorization_digest=authorization_digest)
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == authorization_digest)
            .with_for_update()
        )
        if head is None:
            raise KeyError(authorization_digest)
        current = AmberStatus(head.status)
        assert_amber_transition(current, to_status)
        if _as_utc(timestamp) <= _as_utc(head.updated_at):
            raise ValueError("Amber lifecycle event time must advance monotonically")
        if timestamp >= envelope.expires_at and to_status != AmberStatus.EXPIRED:
            raise ValueError("expired Amber authorization can transition only to expired")
        if to_status in {AmberStatus.AUTHORIZED, AmberStatus.RELEASE_APPROVED}:
            if actor_id not in envelope.required_reviewers:
                raise PermissionError("Amber authorization transition requires a named reviewer")
            if not evidence_refs:
                raise ValueError("reviewed Amber transition requires evidence")
        if to_status == AmberStatus.RELEASE_APPROVED:
            policy = envelope.checkpoint_policy
            if not policy.checkpoint_export_permitted:
                raise PermissionError("Amber envelope does not permit checkpoint release")
            if not policy.independent_review_required:
                raise ValueError("release approval requires an independent-review policy")
            prior_authorizers = set(
                await session.scalars(
                    select(AmberAuthorizationEventRow.actor_id).where(
                        AmberAuthorizationEventRow.authorization_digest == authorization_digest,
                        AmberAuthorizationEventRow.to_status == AmberStatus.AUTHORIZED.value,
                    )
                )
            )
            if actor_id in prior_authorizers:
                raise PermissionError(
                    "checkpoint release reviewer must be independent of authorization"
                )
        event = AmberAuthorizationEvent(
            event_id=event_id or f"amber-event-{uuid4()}",
            authorization_digest=authorization_digest,
            sequence=head.sequence + 1,
            from_status=current,
            to_status=to_status,
            actor_id=actor_id,
            reason=reason,
            evidence_refs=tuple(sorted(evidence_refs)),
            created_at=timestamp,
        )
        existing = await session.get(AmberAuthorizationEventRow, event.event_id)
        if existing is not None:
            stored = AmberAuthorizationEvent.model_validate(existing.record_json, strict=False)
            if stored != event or sha256_digest(stored) != existing.record_digest:
                raise ValueError("Amber event ID reused with different content")
            return stored
        session.add(_event_row(event))
        head.status = to_status.value
        head.sequence = event.sequence
        head.updated_at = timestamp
        await session.flush()
        return event

    async def admit(
        self,
        session: AsyncSession,
        *,
        request: AmberActionRequest,
        active_workers: int,
        decision_id: str | None = None,
    ) -> AmberAdmissionDecision:
        envelope = await self.get(session, authorization_digest=request.authorization_digest)
        rollout = await session.get(ProcessRolloutRow, request.rollout_id)
        if rollout is None:
            raise KeyError(request.rollout_id)
        execution_row = await session.get(ProcessExecutionRow, rollout.execution_digest)
        program_row = await session.get(ProcessProgramRow, rollout.program_digest)
        state_row = await session.get(ProcessStateRow, rollout.current_state_id)
        if execution_row is None or program_row is None or state_row is None:
            raise RuntimeError("Amber admission lost its process context")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        program = ProcessProgram.model_validate(program_row.record_json, strict=False)
        state = ProjectStateVersion.model_validate(state_row.record_json, strict=False)
        if (
            sha256_digest(execution) != rollout.execution_digest
            or sha256_digest(program) != rollout.program_digest
        ):
            raise RuntimeError("Amber admission found corrupted process identity")
        expected_state_digest = project_state_digest(
            state_id=state.state_id,
            rollout_id=state.rollout_id,
            sequence=state.sequence,
            parent_state_id=state.parent_state_id,
            triggering_event_id=state.triggering_event_id,
            payload=state.payload,
            created_at=state.created_at,
        )
        if (
            state.state_digest != state_row.state_digest
            or state.state_digest != expected_state_digest
        ):
            raise RuntimeError("Amber admission found a corrupted project state")
        boundary_reasons = _action_context_reasons(
            request=request,
            rollout=rollout,
            execution=execution,
            program=program,
            state=state,
            envelope=envelope,
        )
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == request.authorization_digest)
            .with_for_update()
        )
        if head is None:
            raise KeyError(request.authorization_digest)
        observed_active_workers = int(
            await session.scalar(
                select(func.count())
                .select_from(ProcessWorkerInvocationRow)
                .join(
                    ProcessRolloutRow,
                    ProcessRolloutRow.rollout_id == ProcessWorkerInvocationRow.rollout_id,
                )
                .where(
                    ProcessRolloutRow.authorization_digest == request.authorization_digest,
                    ProcessWorkerInvocationRow.status == "running",
                )
            )
            or 0
        )
        status = AmberStatus(head.status)
        assigned_id = decision_id or f"amber-decision-{uuid4()}"
        decision = self.policy.decide(
            envelope=envelope,
            status=status,
            authorization_sequence=head.sequence,
            authorization_updated_at=_as_utc(head.updated_at),
            request=request,
            active_workers=max(active_workers, observed_active_workers),
            decision_id=assigned_id,
            boundary_reasons=boundary_reasons,
        )
        existing = await session.get(AmberAdmissionDecisionRow, assigned_id)
        if existing is not None:
            stored = AmberAdmissionDecision.model_validate(existing.record_json, strict=False)
            if (
                stored != decision
                or existing.request_json != request.model_dump(mode="json")
                or sha256_digest(existing.request_json) != stored.request_digest
            ):
                raise ValueError("Amber decision ID reused with different content")
            return stored
        session.add(
            AmberAdmissionDecisionRow(
                decision_id=decision.decision_id,
                authorization_digest=decision.authorization_digest,
                authorization_sequence=decision.authorization_sequence,
                rollout_id=decision.rollout_id,
                disposition=decision.disposition.value,
                request_digest=decision.request_digest,
                request_json=request.model_dump(mode="json"),
                record_digest=sha256_digest(decision),
                record_json=decision.model_dump(mode="json"),
                decided_at=decision.decided_at,
            )
        )
        await session.flush()
        return decision

    async def history(
        self, session: AsyncSession, *, authorization_digest: str
    ) -> tuple[AmberAuthorizationEvent, ...]:
        rows = (
            await session.scalars(
                select(AmberAuthorizationEventRow)
                .where(AmberAuthorizationEventRow.authorization_digest == authorization_digest)
                .order_by(AmberAuthorizationEventRow.sequence)
            )
        ).all()
        events: list[AmberAuthorizationEvent] = []
        for row in rows:
            event = AmberAuthorizationEvent.model_validate(row.record_json, strict=False)
            if (
                sha256_digest(event) != row.record_digest
                or event.event_id != row.event_id
                or event.authorization_digest != row.authorization_digest
                or event.sequence != row.sequence
            ):
                raise ValueError("stored Amber authorization event digest is invalid")
            events.append(event)
        return tuple(events)


def _event_row(event: AmberAuthorizationEvent) -> AmberAuthorizationEventRow:
    return AmberAuthorizationEventRow(
        event_id=event.event_id,
        authorization_digest=event.authorization_digest,
        sequence=event.sequence,
        from_status=event.from_status.value if event.from_status else None,
        to_status=event.to_status.value,
        actor_id=event.actor_id,
        record_digest=sha256_digest(event),
        record_json=event.model_dump(mode="json"),
        created_at=event.created_at,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _action_context_reasons(
    *,
    request: AmberActionRequest,
    rollout: ProcessRolloutRow,
    execution: ProcessExecutionManifest,
    program: ProcessProgram,
    state: ProjectStateVersion,
    envelope: AmberAuthorizationEnvelope,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if rollout.status != RolloutStatus.ACTIVE.value:
        reasons.append("rollout_not_active")
    if rollout.lease_token is None or rollout.lease_expires_at is None:
        reasons.append("rollout_not_leased")
    elif request.requested_at >= _as_utc(rollout.lease_expires_at):
        reasons.append("rollout_lease_expired")
    elif request.lease_token_digest != sha256_digest(rollout.lease_token):
        reasons.append("rollout_lease_identity_mismatch")
    if request.requested_at < _as_utc(rollout.updated_at):
        reasons.append("request_precedes_rollout_claim")
    if request.authorization_digest != rollout.authorization_digest:
        reasons.append("rollout_authorization_mismatch")
    if request.rollout_sequence != rollout.sequence:
        reasons.append("rollout_sequence_mismatch")
    if request.state_digest != state.state_digest:
        reasons.append("rollout_state_mismatch")
    if request.program_digest != rollout.program_digest:
        reasons.append("rollout_program_mismatch")
    if request.distribution_digest != rollout.distribution_digest:
        reasons.append("rollout_distribution_mismatch")
    if request.split.value != rollout.split:
        reasons.append("rollout_split_mismatch")
    if request.persistence_mode != program.persistence_mode:
        reasons.append("rollout_persistence_mismatch")
    if request.environment_fingerprint != execution.environment_fingerprint:
        reasons.append("rollout_environment_mismatch")
    if request.worker_model_digest not in {
        sha256_digest(model) for model in execution.worker_models
    }:
        reasons.append("worker_model_not_in_execution")
    role = next((item for item in program.worker_roles if item.role_id == request.role_id), None)
    if role is None:
        reasons.append("worker_role_not_in_program")
    elif request.tool_id is not None and request.tool_id not in role.allowed_tool_ids:
        reasons.append("tool_not_available_to_role")
    if request.tool_id is not None:
        tool = next(
            (item for item in program.tools if item.component_id == request.tool_id),
            None,
        )
        if tool is None or tool.digest != request.tool_digest:
            reasons.append("tool_identity_not_in_program")
    current = state.payload.budget_usage
    projected = request.projected_usage
    if projected.actions != current.actions + 1:
        reasons.append("action_projection_not_sequential")
    if (
        projected.input_tokens < current.input_tokens
        or projected.output_tokens < current.output_tokens
        or projected.artifact_bytes < current.artifact_bytes
        or float(projected.wall_time_seconds) < float(current.wall_time_seconds)
        or float(projected.cost) < float(current.cost)
    ):
        reasons.append("budget_projection_not_monotonic")
    reserved_wall_time = float(projected.wall_time_seconds) - float(current.wall_time_seconds)
    if request.requested_at + timedelta(seconds=max(0.0, reserved_wall_time)) >= (
        envelope.expires_at
    ):
        reasons.append("action_exceeds_authorization_lifetime")
    return tuple(sorted(set(reasons)))
