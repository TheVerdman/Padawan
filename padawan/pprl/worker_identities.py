"""Broker-issued worker capability authority and one-action lease assignments.

This authenticates a scoped bearer credential inside the trusted broker boundary.
It does not attest a model, process, host, reviewer or credential-delivery channel.
"""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAuthorizationEnvelope,
    AmberStatus,
)
from padawan.models.contracts import StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    AmberAuthorizationRow,
    ProcessExecutionRow,
    ProcessProgramRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessWorkerDecisionBindingRow,
    ProcessWorkerHeadRow,
    ProcessWorkerLeaseAssignmentRow,
    ProcessWorkerRegistrationRow,
    ProcessWorkerRevocationRow,
    ProcessWorkerScopeRow,
)
from padawan.pprl.contracts import (
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectStateVersion,
    RolloutStatus,
    project_state_digest,
)
from padawan.pprl.resources import atomic_resource_write
from padawan.pprl.worker_contracts import (
    ProcessWorkerAccess,
    ProcessWorkerDecisionBinding,
    ProcessWorkerLeaseAssignment,
    ProcessWorkerRegistration,
    ProcessWorkerRevocation,
    ProcessWorkerScope,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _record[R: StrictRecord](model: type[R], row: Any) -> R:
    if row is None:
        raise PermissionError("worker authority record is missing")
    record = model.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or sha256_digest(record) != row.record_digest
    ):
        raise ValueError("worker authority record is corrupt")
    return record


class ProcessWorkerIdentityStore:
    """Privileged issuance/inspection; give workers only the bounded request interface."""

    async def _context(
        self,
        session: AsyncSession,
        execution_digest: str,
        *,
        now: datetime,
        active: bool = True,
        lock: bool = False,
    ) -> tuple[ProcessExecutionManifest, ProcessProgram, AmberAuthorizationEnvelope]:
        execution_row = await session.get(ProcessExecutionRow, execution_digest)
        if execution_row is None:
            raise PermissionError("worker authority lost its execution")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        program_row = await session.get(ProcessProgramRow, execution.program_digest)
        auth_row = await session.get(AmberAuthorizationRow, execution.amber_authorization_digest)
        if program_row is None or auth_row is None:
            raise PermissionError("worker authority lost its program or authorization")
        program = ProcessProgram.model_validate(program_row.record_json, strict=False)
        envelope = AmberAuthorizationEnvelope.model_validate(auth_row.record_json, strict=False)
        if (
            sha256_digest(execution_row.record_json) != execution_digest
            or sha256_digest(execution) != execution_digest
            or execution.execution_id != execution_row.execution_id
            or execution.program_digest != execution_row.program_digest
            or execution.amber_authorization_digest != execution_row.authorization_digest
            or sha256_digest(program_row.record_json) != execution.program_digest
            or sha256_digest(program) != execution.program_digest
            or sha256_digest(auth_row.record_json) != envelope.digest
            or envelope.digest != execution.amber_authorization_digest
            or envelope.program_digest != execution.program_digest
            or program.distribution_digest != execution.distribution_digest
            or envelope.distribution_digest != execution.distribution_digest
            or execution.environment_fingerprint != envelope.environment.environment_fingerprint
            or not {sha256_digest(m) for m in execution.worker_models}.issubset(
                envelope.allowed_worker_model_digests
            )
            or now.tzinfo is None
            or max(execution.created_at, program.created_at, envelope.created_at) > now
        ):
            raise ValueError("worker authority has inconsistent execution lineage")
        if active:
            query = select(AmberAuthorizationHeadRow).where(
                AmberAuthorizationHeadRow.authorization_digest == envelope.digest
            )
            head = await session.scalar(
                (query.with_for_update() if lock else query).execution_options(
                    populate_existing=True
                )
            )
            if (
                head is None
                or head.status != AmberStatus.ACTIVE.value
                or not _utc(head.updated_at) <= now < envelope.expires_at
            ):
                raise PermissionError("worker authority is not currently active")
        return execution, program, envelope

    async def scope(
        self, session: AsyncSession, execution_digest: str
    ) -> ProcessWorkerScope | None:
        row = await session.get(ProcessWorkerScopeRow, execution_digest)
        if row is None:
            return None
        record = _record(ProcessWorkerScope, row)
        if (
            record.execution_digest != row.execution_digest
            or record.authorization_digest != row.authorization_digest
        ):
            raise ValueError("worker scope differs from its lookup identity")
        return record

    @atomic_resource_write
    async def enroll(self, session: AsyncSession, record: ProcessWorkerScope) -> ProcessWorkerScope:
        record = ProcessWorkerScope.model_validate_json(record.model_dump_json())
        # create_rollout must take the same execution lock before creating the first rollout.
        await session.scalar(
            select(ProcessExecutionRow)
            .where(ProcessExecutionRow.execution_digest == record.execution_digest)
            .with_for_update()
        )
        _, program, envelope = await self._context(
            session, record.execution_digest, now=record.created_at
        )
        if (
            record.authorization_digest != envelope.digest
            or record.reviewed_by not in envelope.required_reviewers
            or record.maximum_registered_workers
            > sum(r.maximum_instances for r in program.worker_roles)
            or record.maximum_registered_workers > envelope.budgets.concurrent_workers
        ):
            raise PermissionError("worker enrollment exceeds reviewed authority")
        existing = await self.scope(session, record.execution_digest)
        if existing is not None:
            if existing != record:
                raise PermissionError("worker execution scope cannot be replaced")
            return existing
        if (
            await session.scalar(
                select(ProcessRolloutRow.rollout_id)
                .where(ProcessRolloutRow.execution_digest == record.execution_digest)
                .limit(1)
            )
            is not None
        ):
            raise PermissionError("worker enrollment requires an execution with no rollouts")
        session.add(
            ProcessWorkerScopeRow(
                execution_digest=record.execution_digest,
                authorization_digest=record.authorization_digest,
                record_digest=record.digest,
                record_json=record.model_dump(mode="json"),
            )
        )
        await session.flush()
        return record

    async def registration(
        self, session: AsyncSession, worker_id: str, *, lock: bool = False
    ) -> tuple[ProcessWorkerRegistration, ProcessWorkerHeadRow]:
        row = await session.get(ProcessWorkerRegistrationRow, worker_id)
        record = _record(ProcessWorkerRegistration, row)
        assert row is not None
        scope = await self.scope(session, record.execution_digest)
        execution, program, envelope = await self._context(
            session, record.execution_digest, now=record.created_at, active=False
        )
        role = next((r for r in program.worker_roles if r.role_id == record.role_id), None)
        query = select(ProcessWorkerHeadRow).where(ProcessWorkerHeadRow.worker_id == worker_id)
        head = await session.scalar(
            (query.with_for_update() if lock else query).execution_options(populate_existing=True)
        )
        if (
            record.worker_id != row.worker_id
            or record.execution_digest != row.execution_digest
            or record.role_id != row.role_id
            or record.credential_digest != row.credential_digest
            or record.expires_at != _utc(row.expires_at)
            or scope is None
            or record.scope_digest != scope.digest
            or record.broker_audience != scope.broker_audience
            or record.authorization_digest != scope.authorization_digest
            or record.created_at < scope.created_at
            or record.expires_at
            > record.created_at + timedelta(seconds=scope.maximum_credential_seconds)
            or record.expires_at > envelope.expires_at
            or record.issued_by not in envelope.required_reviewers
            or role is None
            or not set(role.required_capabilities).issubset(record.declared_capabilities)
            or record.worker_model_digest not in {sha256_digest(m) for m in execution.worker_models}
            or head is None
        ):
            raise ValueError("worker registration differs from its retained scope")
        revoked_row = await session.get(ProcessWorkerRevocationRow, worker_id)
        if head.status == "active":
            if head.record_digest != record.digest or revoked_row is not None:
                raise ValueError("active worker head lost its immutable issuance")
        elif head.status == "revoked":
            revoked = _record(ProcessWorkerRevocation, revoked_row)
            if (
                revoked.worker_id != worker_id
                or revoked.registration_digest != record.digest
                or head.record_digest != revoked.digest
                or revoked.created_at < record.created_at
            ):
                raise ValueError("worker revocation lost its original issuance")
        else:
            raise ValueError("unknown worker lifecycle state")
        return record, head

    @atomic_resource_write
    async def issue(
        self,
        session: AsyncSession,
        *,
        execution_digest: str,
        role_id: str,
        worker_model_digest: str,
        declared_capabilities: tuple[str, ...],
        issued_by: str,
        evidence: str,
        expires_at: datetime,
        now: datetime,
    ) -> tuple[ProcessWorkerRegistration, ProcessWorkerAccess]:
        await session.scalar(
            select(ProcessWorkerScopeRow)
            .where(ProcessWorkerScopeRow.execution_digest == execution_digest)
            .with_for_update()
        )
        scope = await self.scope(session, execution_digest)
        execution, program, envelope = await self._context(session, execution_digest, now=now)
        role = next((r for r in program.worker_roles if r.role_id == role_id), None)
        if (
            scope is None
            or issued_by not in envelope.required_reviewers
            or role is None
            or not evidence.strip()
            or expires_at.tzinfo is None
            or not now < expires_at
            or expires_at > envelope.expires_at
            or expires_at > now + timedelta(seconds=scope.maximum_credential_seconds)
            or worker_model_digest not in {sha256_digest(m) for m in execution.worker_models}
            or not set(role.required_capabilities).issubset(declared_capabilities)
        ):
            raise PermissionError("worker issuance exceeds reviewed execution scope")
        rows = await session.scalars(
            select(ProcessWorkerRegistrationRow).where(
                ProcessWorkerRegistrationRow.execution_digest == execution_digest
            )
        )
        current: list[ProcessWorkerRegistration] = []
        for row in rows:
            registered, head = await self.registration(session, row.worker_id)
            if head.status == "active" and now < registered.expires_at:
                current.append(registered)
        if (
            len(current) >= scope.maximum_registered_workers
            or sum(r.role_id == role_id for r in current) >= role.maximum_instances
        ):
            raise PermissionError("worker registration capacity is exhausted")
        secret = secrets.token_hex(32)
        record = ProcessWorkerRegistration(
            worker_id="process-worker-" + uuid4().hex,
            execution_digest=execution_digest,
            authorization_digest=envelope.digest,
            scope_digest=scope.digest,
            broker_audience=scope.broker_audience,
            role_id=role_id,
            worker_model_digest=worker_model_digest,
            declared_capabilities=declared_capabilities,
            credential_digest=sha256_digest(bytes.fromhex(secret)),
            issued_by=issued_by,
            issuance_evidence=evidence,
            created_at=now,
            expires_at=expires_at,
        )
        session.add(
            ProcessWorkerRegistrationRow(
                worker_id=record.worker_id,
                execution_digest=execution_digest,
                role_id=role_id,
                expires_at=expires_at,
                credential_digest=record.credential_digest,
                record_digest=record.digest,
                record_json=record.model_dump(mode="json"),
            )
        )
        await session.flush()
        session.add(
            ProcessWorkerHeadRow(
                worker_id=record.worker_id, status="active", record_digest=record.digest
            )
        )
        await session.flush()
        return record, ProcessWorkerAccess(
            record.worker_id, record.broker_audience, SecretStr(secret)
        )

    @atomic_resource_write
    async def revoke(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        revoked_by: str,
        evidence: str,
        now: datetime,
    ) -> ProcessWorkerRevocation:
        original, _ = await self.registration(session, worker_id)
        await session.scalar(
            select(ProcessWorkerScopeRow)
            .where(ProcessWorkerScopeRow.execution_digest == original.execution_digest)
            .with_for_update()
        )
        original, head = await self.registration(session, worker_id, lock=True)
        _, _, envelope = await self._context(
            session, original.execution_digest, now=now, active=False
        )
        if revoked_by not in envelope.required_reviewers or now < original.created_at:
            raise PermissionError("worker revocation requires reviewed authority")
        record = ProcessWorkerRevocation(
            worker_id=worker_id,
            registration_digest=original.digest,
            revoked_by=revoked_by,
            evidence=evidence,
            created_at=now,
        )
        prior = await session.get(ProcessWorkerRevocationRow, worker_id)
        if prior is not None:
            if _record(ProcessWorkerRevocation, prior) != record:
                raise PermissionError("worker revocation is already retained differently")
            return record
        session.add(
            ProcessWorkerRevocationRow(
                worker_id=worker_id,
                record_digest=record.digest,
                record_json=record.model_dump(mode="json"),
            )
        )
        head.status, head.record_digest = "revoked", record.digest
        await session.flush()
        return record

    async def identify(
        self,
        session: AsyncSession,
        access: ProcessWorkerAccess,
        *,
        now: datetime,
        lock: bool = False,
    ) -> ProcessWorkerRegistration:
        # A trusted historical timestamp must not revive an expired live capability.
        now = max(_utc(now), datetime.now(UTC))
        if not isinstance(access, ProcessWorkerAccess):
            raise PermissionError("worker credential is required")
        secret = access.credential.get_secret_value()
        if re.fullmatch(r"[0-9a-f]{64}", secret) is None:
            raise PermissionError("worker credential is invalid")
        record, head = await self.registration(session, access.worker_id, lock=lock)
        if (
            not hmac.compare_digest(record.credential_digest, sha256_digest(bytes.fromhex(secret)))
            or access.broker_audience != record.broker_audience
            or head.status != "active"
            or not record.created_at <= now < record.expires_at
        ):
            raise PermissionError("worker credential is invalid")
        await self._context(session, record.execution_digest, now=now, lock=lock)
        return record

    async def bind_claim(
        self,
        session: AsyncSession,
        *,
        rollout: ProcessRolloutRow,
        access: ProcessWorkerAccess,
        now: datetime,
    ) -> ProcessWorkerLeaseAssignment:
        now = max(_utc(now), datetime.now(UTC))
        registration = await self.identify(session, access, now=now, lock=True)
        # Claims lock rollout then worker head. Do not lock another rollout here: a
        # competing claim may hold it while waiting for this same worker head.
        other_lease = await session.scalar(
            select(ProcessRolloutRow.rollout_id)
            .where(
                ProcessRolloutRow.lease_owner == registration.worker_id,
                ProcessRolloutRow.rollout_id != rollout.rollout_id,
                ProcessRolloutRow.lease_expires_at > now,
            )
            .limit(1)
        )
        state = await session.get(ProcessStateRow, rollout.current_state_id)
        if (
            access.assignment_id is not None
            or other_lease is not None
            or state is None
            or rollout.execution_digest != registration.execution_digest
            or rollout.lease_owner != registration.worker_id
            or not rollout.lease_token
            or rollout.lease_expires_at is None
            or now >= _utc(rollout.lease_expires_at)
            or _utc(rollout.lease_expires_at) > registration.expires_at
        ):
            raise PermissionError("worker assignment exceeds its credential or lease")
        record = ProcessWorkerLeaseAssignment(
            assignment_id="worker-assignment-" + uuid4().hex,
            worker_id=registration.worker_id,
            registration_digest=registration.digest,
            execution_digest=registration.execution_digest,
            authorization_digest=registration.authorization_digest,
            role_id=registration.role_id,
            worker_model_digest=registration.worker_model_digest,
            rollout_id=rollout.rollout_id,
            rollout_sequence=rollout.sequence,
            state_id=state.state_id,
            state_digest=state.state_digest,
            lease_token_digest=sha256_digest(rollout.lease_token),
            created_at=now,
            expires_at=_utc(rollout.lease_expires_at),
        )
        session.add(
            ProcessWorkerLeaseAssignmentRow(
                assignment_id=record.assignment_id,
                worker_id=record.worker_id,
                rollout_id=record.rollout_id,
                lease_token_digest=record.lease_token_digest,
                record_digest=record.digest,
                record_json=record.model_dump(mode="json"),
            )
        )
        await session.flush()
        return record

    async def assignment(
        self, session: AsyncSession, assignment_id: str
    ) -> ProcessWorkerLeaseAssignment:
        row = await session.get(ProcessWorkerLeaseAssignmentRow, assignment_id)
        record = _record(ProcessWorkerLeaseAssignment, row)
        assert row is not None
        registration, _ = await self.registration(session, record.worker_id)
        state_row = await session.get(ProcessStateRow, record.state_id)
        if state_row is None:
            raise ValueError("worker assignment lost its original state")
        state = ProjectStateVersion.model_validate(state_row.record_json, strict=False)
        if (
            record.assignment_id != row.assignment_id
            or record.worker_id != row.worker_id
            or record.rollout_id != row.rollout_id
            or record.lease_token_digest != row.lease_token_digest
            or record.registration_digest != registration.digest
            or record.execution_digest != registration.execution_digest
            or record.authorization_digest != registration.authorization_digest
            or record.role_id != registration.role_id
            or record.worker_model_digest != registration.worker_model_digest
            or state.state_id != record.state_id
            or state.rollout_id != record.rollout_id
            or state.sequence != record.rollout_sequence
            or state.state_digest != record.state_digest
            or state_row.state_digest != record.state_digest
            or project_state_digest(
                state_id=state.state_id,
                rollout_id=state.rollout_id,
                sequence=state.sequence,
                parent_state_id=state.parent_state_id,
                triggering_event_id=state.triggering_event_id,
                payload=state.payload,
                created_at=state.created_at,
            )
            != record.state_digest
            or state.created_at > record.created_at
            or not registration.created_at
            <= record.created_at
            < record.expires_at
            <= registration.expires_at
        ):
            raise ValueError("worker assignment lost its original registration")
        return record

    async def inspect_decision(
        self, session: AsyncSession, *, decision_id: str
    ) -> ProcessWorkerDecisionBinding | None:
        """Historical identity evidence remains required after revocation or lease release."""
        decision = await session.get(AmberAdmissionDecisionRow, decision_id)
        if decision is None:
            raise ValueError("worker identity evidence lost its decision")
        rollout = await session.get(ProcessRolloutRow, decision.rollout_id)
        if rollout is None:
            raise ValueError("worker identity evidence lost its execution")
        scoped = await self.scope(session, rollout.execution_digest)
        row = await session.get(ProcessWorkerDecisionBindingRow, decision_id)
        if scoped is None:
            return await self.require_decision(session, assignment=None, decision_id=decision_id)
        if row is None:
            raise ValueError("enrolled decision lost its authenticated assignment")
        assignment = await self.assignment(session, row.assignment_id)
        if assignment.execution_digest != rollout.execution_digest:
            raise ValueError("worker decision crossed its execution scope")
        return await self.require_decision(session, assignment=assignment, decision_id=decision_id)

    async def require(
        self,
        session: AsyncSession,
        *,
        rollout: ProcessRolloutRow,
        access: ProcessWorkerAccess | None,
        now: datetime,
        worker_id: str | None = None,
        role_id: str | None = None,
        worker_model_digest: str | None = None,
    ) -> ProcessWorkerLeaseAssignment | None:
        now = max(_utc(now), datetime.now(UTC))
        scope = await self.scope(session, rollout.execution_digest)
        if scope is None:
            if access is not None:
                raise PermissionError("worker access cannot target an unenrolled execution")
            return None
        if access is None or access.assignment_id is None:
            raise PermissionError("enrolled execution requires assigned worker authority")
        registered = await self.identify(session, access, now=now, lock=True)
        assignment = await self.assignment(session, access.assignment_id)
        if (
            assignment.worker_id != registered.worker_id
            or assignment.execution_digest != rollout.execution_digest
            or assignment.rollout_id != rollout.rollout_id
            or assignment.rollout_sequence != rollout.sequence
            or assignment.state_id != rollout.current_state_id
            or assignment.lease_token_digest != sha256_digest(rollout.lease_token or "")
            or rollout.lease_owner != registered.worker_id
            or not assignment.created_at <= now < assignment.expires_at
            or rollout.lease_expires_at is None
            or now >= _utc(rollout.lease_expires_at)
            or rollout.status != RolloutStatus.ACTIVE.value
            or rollout.paused
            or (worker_id is not None and worker_id != registered.worker_id)
            or (role_id is not None and role_id != registered.role_id)
            or (
                worker_model_digest is not None
                and worker_model_digest != registered.worker_model_digest
            )
        ):
            raise PermissionError("worker request differs from its current assignment")
        state = await session.get(ProcessStateRow, rollout.current_state_id)
        if state is None or state.state_digest != assignment.state_digest:
            raise ValueError("worker assignment lost its original state")
        return assignment

    async def bind_decision(
        self,
        session: AsyncSession,
        *,
        assignment: ProcessWorkerLeaseAssignment,
        request: AmberActionRequest,
        decision: AmberAdmissionDecision,
    ) -> ProcessWorkerDecisionBinding:
        record = ProcessWorkerDecisionBinding(
            decision_id=decision.decision_id,
            decision_digest=sha256_digest(decision),
            request_digest=sha256_digest(request),
            assignment_id=assignment.assignment_id,
            assignment_digest=assignment.digest,
            registration_digest=assignment.registration_digest,
            worker_id=assignment.worker_id,
            created_at=decision.decided_at,
        )
        session.add(
            ProcessWorkerDecisionBindingRow(
                decision_id=record.decision_id,
                assignment_id=record.assignment_id,
                record_digest=record.digest,
                record_json=record.model_dump(mode="json"),
            )
        )
        await session.flush()
        return record

    async def require_decision(
        self,
        session: AsyncSession,
        *,
        assignment: ProcessWorkerLeaseAssignment | None,
        decision_id: str,
    ) -> ProcessWorkerDecisionBinding | None:
        row = await session.get(ProcessWorkerDecisionBindingRow, decision_id)
        if assignment is None:
            if row is not None:
                raise PermissionError("worker decision cannot lose its assigned scope")
            return None
        record = _record(ProcessWorkerDecisionBinding, row)
        assert row is not None
        decision = await session.get(AmberAdmissionDecisionRow, decision_id)
        if (
            record.decision_id != row.decision_id
            or record.assignment_id != row.assignment_id
            or record.assignment_id != assignment.assignment_id
            or record.assignment_digest != assignment.digest
            or record.registration_digest != assignment.registration_digest
            or record.worker_id != assignment.worker_id
            or decision is None
            or sha256_digest(decision.record_json) != record.decision_digest
            or sha256_digest(decision.request_json) != record.request_digest
        ):
            raise ValueError("worker decision lost its original authenticated assignment")
        request = AmberActionRequest.model_validate(decision.request_json, strict=False)
        admitted = AmberAdmissionDecision.model_validate(decision.record_json, strict=False)
        if (
            sha256_digest(request) != record.request_digest
            or sha256_digest(admitted) != record.decision_digest
            or decision.record_digest != record.decision_digest
            or decision.request_digest != record.request_digest
            or admitted.request_digest != record.request_digest
            or admitted.decision_id != decision_id
            or admitted.rollout_id != assignment.rollout_id
            or request.rollout_id != assignment.rollout_id
            or request.rollout_sequence != assignment.rollout_sequence
            or request.state_digest != assignment.state_digest
            or request.lease_token_digest != assignment.lease_token_digest
            or request.authorization_digest != assignment.authorization_digest
            or request.role_id != assignment.role_id
            or request.worker_model_digest != assignment.worker_model_digest
            or record.created_at != admitted.decided_at
            or not assignment.created_at
            <= request.requested_at
            <= admitted.decided_at
            < assignment.expires_at
        ):
            raise ValueError("worker decision differs from its original lease and role")
        return record
