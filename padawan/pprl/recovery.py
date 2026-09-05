"""Explicit reviewed lease fencing and effect assessment inside the trusted broker.

Recovery returns private history, never a worker observation or dispatch capability.
It launches nothing, does not infer a lost process update, and cannot resolve an
unknown external effect by trusting a replacement worker's assertion.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog
from padawan.governance.amber import AmberAuthorizationEvent, AmberStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ProcessRecoveryRow,
    ProcessRolloutRow,
    ProcessWorkerLeaseAssignmentRow,
    ProcessWorkerScopeRow,
)
from padawan.pprl.containers import ProcessContainerStore
from padawan.pprl.contracts import RolloutStatus
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.recovery_contracts import (
    ProcessRecoveryEffect,
    ProcessRecoveryReceipt,
    ProcessRecoveryRequest,
)
from padawan.pprl.recovery_evidence import RecoveryEvidenceReader
from padawan.pprl.resource_generation import ProcessGenerationResources
from padawan.pprl.resources import atomic_resource_write, unresolved_action_query
from padawan.pprl.store import ProcessStore


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class ProcessRecoveryStore:
    def __init__(self, processes: ProcessStore, catalog: ArtifactCatalog) -> None:
        self.processes, self.catalog = processes, catalog
        self.resources, self.workers = processes.amber.resources, processes.workers
        self.containers = ProcessContainerStore(catalog, self.resources)
        self.models = ProcessGenerationResources(store=self.resources, catalog=catalog)
        self.observations = ProcessObservationStore(processes)

    async def _authority_history(
        self, session: AsyncSession, digest: str
    ) -> tuple[AmberAuthorizationEvent, ...]:
        history = await self.processes.amber.history(session, authorization_digest=digest)
        if not history or any(
            event.sequence != sequence
            or (sequence and event.from_status != history[sequence - 1].to_status)
            for sequence, event in enumerate(history)
        ):
            raise ValueError("recovery requires intact Amber lifecycle evidence")
        return history

    @atomic_resource_write
    async def recover(
        self, session: AsyncSession, request: ProcessRecoveryRequest, *, now: datetime
    ) -> ProcessRecoveryReceipt:
        request = ProcessRecoveryRequest.model_validate_json(request.model_dump_json())
        if now.tzinfo is None or now < request.reviewed_at:
            raise ValueError("recovery must follow an aware review")
        row = await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == request.rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise PermissionError("recovery rollout is missing")
        existing = await session.get(ProcessRecoveryRow, request.recovery_id)
        if existing is not None:
            receipt = await self.read(session, recovery_id=request.recovery_id)
            if receipt.request != request:
                raise PermissionError("recovery id already records a different reviewed request")
            # A historical retry cannot fence a successor or restore prior readiness.
            return receipt
        state = await self.processes.get_state(session, state_id=row.current_state_id)
        lease_digest = sha256_digest(row.lease_token) if row.lease_token else None
        if (
            state.state_digest != request.expected_state_digest
            or lease_digest != request.expected_lease_token_digest
            or state.rollout_id != row.rollout_id
            or state.sequence != row.sequence
            or now < max(state.created_at, _utc(row.updated_at))
            or bool(row.lease_token) != bool(row.lease_owner)
            or bool(row.lease_token) != (row.lease_expires_at is not None)
        ):
            raise PermissionError("recovery review does not match the current state and lease")
        before_status = RolloutStatus(row.status)
        if before_status not in {
            RolloutStatus.ACTIVE,
            RolloutStatus.PAUSED,
            RolloutStatus.REVIEW_REQUIRED,
        }:
            raise PermissionError("recovery cannot reopen a terminal scientific rollout")
        execution, _, envelope = await self.workers._context(
            session, row.execution_digest, now=now, active=False
        )
        if (
            row.authorization_digest != envelope.digest
            or row.program_digest != execution.program_digest
            or row.distribution_digest != execution.distribution_digest
            or request.reviewer_id not in envelope.required_reviewers
        ):
            raise PermissionError("recovery requires the original execution's named reviewer")
        # Match issuance/revocation's scope-before-worker order. No code below
        # acquires another rollout lock, so replacement and dispatch can serialize.
        await session.scalar(
            select(ProcessWorkerScopeRow)
            .where(ProcessWorkerScopeRow.execution_digest == row.execution_digest)
            .with_for_update()
        )
        scope = await self.workers.scope(session, row.execution_digest)
        registration = worker_head = assignment = None
        if scope is not None and row.lease_owner is not None:
            registration, worker_head = await self.workers.registration(
                session, row.lease_owner, lock=True
            )
            assigned = await session.scalar(
                select(ProcessWorkerLeaseAssignmentRow).where(
                    ProcessWorkerLeaseAssignmentRow.rollout_id == row.rollout_id,
                    ProcessWorkerLeaseAssignmentRow.lease_token_digest == lease_digest,
                )
            )
            if assigned is None:
                raise ValueError("recovery lease lost its original worker assignment")
            assignment = await self.workers.assignment(session, assigned.assignment_id)
            if (
                assignment.worker_id != registration.worker_id
                or registration.execution_digest != row.execution_digest
                or assignment.state_digest != state.state_digest
                or assignment.state_id != state.state_id
                or assignment.rollout_sequence != row.sequence
            ):
                raise ValueError("recovery worker assignment differs from the fenced lease")
        authority = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == envelope.digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        history = await self._authority_history(session, envelope.digest)
        if (
            authority is None
            or history[-1].sequence != authority.sequence
            or history[-1].to_status.value != authority.status
            or history[-1].created_at != _utc(authority.updated_at)
            or now < _utc(authority.updated_at)
        ):
            raise ValueError("recovery Amber head differs from retained lifecycle evidence")
        worker_unavailable = bool(
            registration
            and worker_head
            and (worker_head.status != "active" or now >= registration.expires_at)
        )
        authority_active = (
            authority.status == AmberStatus.ACTIVE.value and now < envelope.expires_at
        )
        if (
            row.lease_expires_at is not None
            and now < _utc(row.lease_expires_at)
            and before_status == RolloutStatus.ACTIVE
            and not row.paused
            and authority_active
            and not worker_unavailable
        ):
            raise PermissionError("recovery cannot take over a still-active lease without a pause")
        before = await self.resources.inspect(session, envelope.digest, lock=True)
        decisions = list(
            await session.scalars(
                unresolved_action_query()
                .where(AmberAdmissionDecisionRow.rollout_id == row.rollout_id)
                .order_by(AmberAdmissionDecisionRow.decision_id)
                .limit(request.maximum_effects + 1)
            )
        )
        if len(decisions) > request.maximum_effects:
            raise PermissionError("recovery exceeds its reviewed effect limit")
        reader = RecoveryEvidenceReader(
            self.containers, self.observations, maximum_bytes=request.maximum_source_bytes
        )
        effects: list[ProcessRecoveryEffect] = []
        for decision_id in decisions:
            reservation, phase, prior = await self.resources.reservation(session, decision_id)
            before_phase = phase.status
            evidence = await reader.inspect(session, reservation, now=now)
            identity = await self.workers.inspect_decision(session, decision_id=decision_id)
            observed_binding = None
            if evidence.kind != "generic":
                observed_binding = await self.observations.inspect_decision_binding(
                    session, decision_id=decision_id
                )
            disposition = "unknown"
            if before_phase == "released":
                raise ValueError("recovery candidate phase changed while its rollout was locked")
            if before_phase == "reserved" and evidence.kind == "generic":
                await self.resources.release_unstarted(
                    session,
                    decision_id=decision_id,
                    reviewer_id=request.reviewer_id,
                    evidence=f"{request.recovery_id}: {request.reason}",
                    now=now,
                )
                disposition = "released_unstarted"
            elif evidence.completed:
                assert evidence.invocation_id is not None
                if evidence.kind == "container":
                    await self.containers.reconcile(
                        session, invocation_id=evidence.invocation_id, now=now
                    )
                else:
                    await self.models.reconcile(
                        session, invocation_id=evidence.invocation_id, now=now
                    )
                _, current_phase, _ = await self.resources.reservation(session, decision_id)
                if current_phase.status == "settled":
                    disposition = "completed_unadmitted"
            _, after_phase, after_event = await self.resources.reservation(session, decision_id)
            effects.append(
                ProcessRecoveryEffect.model_validate(
                    dict(
                        decision_id=decision_id,
                        decision_digest=reservation.decision_digest,
                        reservation_digest=reservation.digest,
                        before_phase=before_phase,
                        after_phase=after_phase.status,
                        before_resource_event_digest=prior.digest,
                        after_resource_event_digest=after_event.digest,
                        disposition=disposition,
                        kind=evidence.kind,
                        invocation_id=evidence.invocation_id,
                        observed_status=evidence.observed_status,
                        workload_digest=evidence.workload_digest,
                        result_digest=evidence.result_digest,
                        observation_binding_digest=observed_binding.digest
                        if observed_binding
                        else None,
                        worker_binding_digest=identity.digest if identity else None,
                        sources=evidence.sources,
                    )
                )
            )
        unresolved = any(
            effect.disposition in {"unknown", "completed_unadmitted"} for effect in effects
        )
        if not unresolved:
            await self.resources.assert_rollout_recoverable(session, rollout_id=row.rollout_id)
        after = await self.resources.inspect(session, envelope.digest)
        # Evidence reads may outlast authority. Recheck time before publishing any
        # ready state; a timestamp supplied before waiting is not current authority.
        completed_at = max(now, datetime.now(UTC), after.created_at)
        authority_active = (
            authority.status == AmberStatus.ACTIVE.value and completed_at < envelope.expires_at
        )
        retired_digest = None
        if registration is not None and worker_head is not None and request.retire_worker:
            if worker_head.status == "revoked":
                retired_digest = worker_head.record_digest
            else:
                retired = await self.workers.revoke(
                    session,
                    worker_id=registration.worker_id,
                    revoked_by=request.reviewer_id,
                    evidence=f"{request.recovery_id}: {request.reason}",
                    now=completed_at,
                )
                retired_digest = retired.digest
        if unresolved:
            status, disposition = RolloutStatus.REVIEW_REQUIRED, "review_required"
        elif (
            authority_active
            and not after.stopped
            and (request.resume or (before_status == RolloutStatus.ACTIVE and not row.paused))
        ):
            status, disposition = RolloutStatus.ACTIVE, "ready"
        else:
            status, disposition = RolloutStatus.PAUSED, "paused"
        previous_worker = row.lease_owner
        row.lease_owner = row.lease_token = row.lease_expires_at = None
        row.status, row.paused, row.updated_at = (
            status.value,
            status != RolloutStatus.ACTIVE,
            completed_at,
        )
        # Do not copy the private review, IDs, source excerpts or diagnostics here.
        receipt = ProcessRecoveryReceipt.model_validate(
            dict(
                request=request,
                request_digest=request.digest,
                execution_digest=row.execution_digest,
                authorization_digest=envelope.digest,
                authorization_sequence=authority.sequence,
                authorization_event_digest=sha256_digest(history[-1]),
                state_id=state.state_id,
                state_digest=state.state_digest,
                previous_worker_id=previous_worker,
                previous_assignment_digest=assignment.digest if assignment else None,
                retired_worker_digest=retired_digest,
                status_before=before_status,
                status_after=status,
                disposition=disposition,
                effects=tuple(effects),
                account_before_digest=before.digest,
                account_after_digest=after.digest,
                created_at=completed_at,
            )
        )
        for effect in receipt.effects:
            for source in effect.sources:
                await self.catalog.reference(
                    session,
                    source.reference.artifact,
                    owner_type="process_recovery_receipt",
                    owner_id=request.recovery_id,
                )
        session.add(
            ProcessRecoveryRow(
                recovery_id=request.recovery_id,
                rollout_id=row.rollout_id,
                execution_digest=row.execution_digest,
                request_digest=request.digest,
                record_digest=receipt.digest,
                record_json=receipt.model_dump(mode="json"),
                created_at=completed_at,
            )
        )
        await session.flush()
        # Publish only a receipt that its checked historical reader can reconstruct.
        # A failed join rolls back fencing, accounting and pins with this savepoint.
        checked = await self.read(session, recovery_id=request.recovery_id)
        if checked.disposition == "ready" and datetime.now(UTC) >= envelope.expires_at:
            raise PermissionError("recovery authority expired during final source validation")
        return checked

    async def read(self, session: AsyncSession, *, recovery_id: str) -> ProcessRecoveryReceipt:
        row = await session.get(ProcessRecoveryRow, recovery_id)
        if row is None:
            raise PermissionError("recovery receipt is missing")
        receipt = ProcessRecoveryReceipt.model_validate(row.record_json, strict=False)
        if sha256_digest(row.record_json) != row.record_digest or (
            receipt.digest,
            receipt.request.recovery_id,
            receipt.request.rollout_id,
            receipt.execution_digest,
            receipt.request_digest,
            receipt.created_at,
        ) != (
            row.record_digest,
            row.recovery_id,
            row.rollout_id,
            row.execution_digest,
            row.request_digest,
            _utc(row.created_at),
        ):
            raise ValueError("recovery receipt is corrupt")
        state = await self.processes.get_state(session, state_id=receipt.state_id)
        execution, _, envelope = await self.workers._context(
            session, receipt.execution_digest, now=receipt.created_at, active=False
        )
        history = await self._authority_history(session, receipt.authorization_digest)
        if (
            receipt.authorization_sequence >= len(history)
            or sha256_digest(history[receipt.authorization_sequence])
            != receipt.authorization_event_digest
            or envelope.digest != receipt.authorization_digest
            or execution.amber_authorization_digest != receipt.authorization_digest
            or receipt.request.reviewer_id not in envelope.required_reviewers
            or state.state_digest != receipt.state_digest
            or state.rollout_id != receipt.request.rollout_id
        ):
            raise ValueError("recovery lost its original state or reviewed authority")
        for digest in (receipt.account_before_digest, receipt.account_after_digest):
            event = await self.resources._event(session, digest)
            if event.authorization_digest != receipt.authorization_digest:
                raise ValueError("recovery account snapshot crossed authorization")
        if receipt.previous_assignment_digest is not None:
            assigned = await session.scalar(
                select(ProcessWorkerLeaseAssignmentRow).where(
                    ProcessWorkerLeaseAssignmentRow.record_digest
                    == receipt.previous_assignment_digest
                )
            )
            if assigned is None:
                raise ValueError("recovery lost its fenced worker assignment")
            assignment = await self.workers.assignment(session, assigned.assignment_id)
            if (
                assignment.worker_id != receipt.previous_worker_id
                or assignment.lease_token_digest != receipt.request.expected_lease_token_digest
            ):
                raise ValueError("recovery fenced worker evidence was substituted")
        if receipt.retired_worker_digest is not None:
            assert receipt.previous_worker_id is not None
            _, head = await self.workers.registration(session, receipt.previous_worker_id)
            if head.status != "revoked" or head.record_digest != receipt.retired_worker_digest:
                raise ValueError("recovery lost its original worker retirement")
        reader = RecoveryEvidenceReader(
            self.containers, self.observations, maximum_bytes=receipt.request.maximum_source_bytes
        )
        expected_sources = {
            source.reference.artifact.artifact_id
            for effect in receipt.effects
            for source in effect.sources
        }
        retained_sources = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id)
                .where(
                    ArtifactReferenceRow.owner_type == "process_recovery_receipt",
                    ArtifactReferenceRow.owner_id == recovery_id,
                )
                .limit(len(expected_sources) + 1)
            )
        )
        if retained_sources != expected_sources:
            raise ValueError("recovery receipt has inconsistent source retention ownership")
        for effect in receipt.effects:
            reservation, _, _ = await self.resources.reservation(session, effect.decision_id)
            if (
                reservation.digest != effect.reservation_digest
                or reservation.rollout_id != receipt.request.rollout_id
                or reservation.decision_digest != effect.decision_digest
            ):
                raise ValueError("recovery lost its exact reservation")
            for digest, phase in (
                (effect.before_resource_event_digest, effect.before_phase),
                (effect.after_resource_event_digest, effect.after_phase),
            ):
                event = await self.resources._event(session, digest)
                if (
                    event.reservation_digest != reservation.digest
                    or event.subject_id != effect.decision_id
                    or event.authorization_digest != receipt.authorization_digest
                    or event.created_at > receipt.created_at
                    or {
                        "reserve": "reserved",
                        "start": "started",
                        "settle": "settled",
                        "release": "released",
                    }.get(event.kind)
                    != phase
                ):
                    raise ValueError("recovery phase snapshot differs from its reservation")
            identity = await self.workers.inspect_decision(session, decision_id=effect.decision_id)
            if (identity.digest if identity else None) != effect.worker_binding_digest:
                raise ValueError("recovery lost its original worker decision binding")
            if effect.observation_binding_digest is not None:
                observed = await self.observations.inspect_decision_binding(
                    session, decision_id=effect.decision_id
                )
                if observed.digest != effect.observation_binding_digest:
                    raise ValueError("recovery lost its original observation binding")
            await reader.verify_retained(session, effect, recovery_id=recovery_id)
            if effect.disposition == "completed_unadmitted":
                await reader.verify_settlement(session, effect, event)
        return receipt
