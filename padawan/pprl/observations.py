"""Allowlisted worker input and its separately retained, privileged broker lineage.

Only the observation DTO crosses to the planner. This service, its receipts, and
its caller-supplied scope belong to the trusted broker, not to a worker sandbox.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import TYPE_CHECKING, Concatenate

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import ProcessArtifactRef
from padawan.governance.amber import AmberActionRequest, AmberAdmissionDecision, AmberStatus
from padawan.models.contracts import RightsUse
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ProcessContentAdmissionRow,
    ProcessExecutionRow,
    ProcessObservationDecisionRow,
    ProcessObservationRow,
    ProcessRolloutRow,
    ProjectInstanceRow,
)
from padawan.pprl.contracts import (
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectInstance,
    ProjectStateVersion,
    RolloutStatus,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.observation_contracts import (
    ProcessObservationDecisionBinding,
    ProcessObservationPolicy,
    ProcessObservationReceipt,
    ProcessWorkerObservation,
    ProcessWorkerState,
)
from padawan.pprl.worker_contracts import ProcessWorkerAccess

if TYPE_CHECKING:
    from padawan.pprl.store import ProcessStore


class ProcessObservationDeniedError(PermissionError):
    """One public error, with no underlying privileged exception or record."""


@dataclass(frozen=True)
class ProcessObservationResult:
    """Broker result; pass only a fresh ``observation`` to a planner."""

    observation_id: str
    observation_bytes: bytes

    @property
    def observation(self) -> ProcessWorkerObservation:
        # Frozen Pydantic objects can still contain mutable nested dictionaries.
        return ProcessWorkerObservation.model_validate_json(self.observation_bytes)


@dataclass(frozen=True)
class _ObservationContext:
    rollout: ProcessRolloutRow
    execution: ProcessExecutionManifest
    program: ProcessProgram
    state: ProjectStateVersion
    content_receipt_digest: str
    authorization_sequence: int
    rights_digests: tuple[str, ...]


def _guard_observation[**P, R](
    operation: Callable[Concatenate[ProcessObservationStore, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[ProcessObservationStore, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(
        self: ProcessObservationStore, session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        cancelled = False
        try:
            return await operation(self, session, *args, **kwargs)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            pass
        if cancelled:
            raise asyncio.CancelledError("process observation cancelled")
        raise ProcessObservationDeniedError("process observation denied")

    return wrapped


class ProcessObservationStore:
    def __init__(
        self, store: ProcessStore, *, policy: ProcessObservationPolicy | None = None
    ) -> None:
        self.store = store
        expected_schema = sha256_digest(ProcessWorkerObservation.model_json_schema())
        self._policy = ProcessObservationPolicy.model_validate_json(
            (
                policy
                or ProcessObservationPolicy(
                    policy_id="padawan.worker-observation",
                    version="1.0.0",
                    observation_schema_digest=expected_schema,
                    content_policy_digest=store.content.policy.digest,
                )
            ).model_dump_json()
        )
        if (
            self._policy.observation_schema_digest != expected_schema
            or self._policy.content_policy_digest != store.content.policy.digest
        ):
            raise ValueError("observation policy differs from configured schemas")

    @property
    def policy(self) -> ProcessObservationPolicy:
        return self._policy.model_copy(deep=True)

    @_guard_observation
    async def observe_claim(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime | None = None,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> ProcessObservationResult:
        timestamp = _timestamp(now)
        async with session.begin_nested():
            context = await self._context(
                session,
                rollout_id=rollout_id,
                lease_token=lease_token,
                worker_id=worker_id,
                now=timestamp,
                worker_access=worker_access,
            )
            receipt = self._project(context, worker_id, lease_token, timestamp)
            existing = await session.get(ProcessObservationRow, receipt.observation_id)
            if existing is not None:
                stored = _receipt(existing)
                _same_observation(stored, receipt)
                await self._validate_ownership(session, stored, now=timestamp)
                return _result(stored)
            references = _public_references(context.state)
            for reference in references:
                assert self.store.evidence is not None
                await self.store.evidence.retain_for_process(
                    session,
                    reference=reference,
                    execution_digest=receipt.execution_digest,
                    owner_type="process_observation",
                    owner_id=receipt.observation_id,
                    now=timestamp,
                )
            session.add(
                ProcessObservationRow(
                    observation_id=receipt.observation_id,
                    rollout_id=receipt.rollout_id,
                    state_id=receipt.state_id,
                    execution_digest=receipt.execution_digest,
                    content_receipt_digest=receipt.content_receipt_digest,
                    worker_id=worker_id,
                    lease_token_digest=receipt.lease_token_digest,
                    authorization_sequence=receipt.authorization_sequence,
                    policy_digest=receipt.policy_digest,
                    observation_digest=receipt.observation_digest,
                    record_digest=receipt.digest,
                    record_json=receipt.model_dump(mode="json"),
                    created_at=timestamp,
                )
            )
            await session.flush()
            await self._validate_ownership(session, receipt, now=timestamp)
            return _result(receipt)

    @_guard_observation
    async def read(
        self,
        session: AsyncSession,
        *,
        observation_id: str,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime | None = None,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> ProcessWorkerObservation:
        receipt, _context = await self._current_receipt(
            session,
            observation_id=observation_id,
            rollout_id=rollout_id,
            lease_token=lease_token,
            worker_id=worker_id,
            now=_timestamp(now),
            worker_access=worker_access,
        )
        return _result(receipt).observation

    async def inspect_receipt(
        self, session: AsyncSession, *, observation_id: str
    ) -> ProcessObservationReceipt:
        """Privileged historical reconstruction; no permission for current delivery."""
        row = await session.get(ProcessObservationRow, observation_id)
        if row is None:
            raise ValueError("observation receipt is missing")
        return _receipt(row)

    @_guard_observation
    async def bind_decision(
        self,
        session: AsyncSession,
        *,
        observation_id: str,
        decision_id: str,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime | None = None,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> ProcessObservationDecisionBinding:
        """Retain declared proposal lineage, including denied proposals; grants no effect."""
        timestamp = _timestamp(now)
        async with session.begin_nested():
            receipt, context = await self._current_receipt(
                session,
                observation_id=observation_id,
                rollout_id=rollout_id,
                lease_token=lease_token,
                worker_id=worker_id,
                now=timestamp,
                worker_access=worker_access,
            )
            row = await session.get(AmberAdmissionDecisionRow, decision_id)
            if row is None:
                raise ValueError("observation decision is missing")
            request, decision = _decision(row)
            assignment = await self.store.workers.require(
                session,
                rollout=context.rollout,
                access=worker_access,
                now=timestamp,
                worker_id=worker_id,
                role_id=request.role_id,
                worker_model_digest=request.worker_model_digest,
            )
            await self.store.workers.require_decision(
                session, assignment=assignment, decision_id=decision_id
            )
            if (
                request.authorization_digest != receipt.authorization_digest
                or decision.authorization_sequence != receipt.authorization_sequence
                or request.rollout_id != receipt.rollout_id
                or request.rollout_sequence != context.state.sequence
                or request.state_digest != receipt.state_digest
                or request.lease_token_digest != receipt.lease_token_digest
                or request.program_digest != context.execution.program_digest
                or request.distribution_digest != context.execution.distribution_digest
                or request.environment_fingerprint != context.execution.environment_fingerprint
                or request.split.value != context.rollout.split
                or request.persistence_mode != context.program.persistence_mode
                or not receipt.created_at
                <= request.requested_at
                <= decision.decided_at
                <= timestamp
            ):
                raise ValueError("decision does not bind the delivered observation context")
            binding = ProcessObservationDecisionBinding(
                observation_id=observation_id,
                observation_receipt_digest=receipt.digest,
                observation_digest=receipt.observation_digest,
                decision_id=decision_id,
                request_digest=decision.request_digest,
                decision_digest=sha256_digest(decision),
                disposition=decision.disposition,
                declared_role_id=request.role_id,
                declared_worker_model_digest=request.worker_model_digest,
                bound_at=timestamp,
            )
            existing = await session.get(ProcessObservationDecisionRow, decision_id)
            if existing is not None:
                stored = _binding(existing)
                if stored.bound_at > timestamp or stored != binding.model_copy(
                    update={"bound_at": stored.bound_at}
                ):
                    raise ValueError("decision observation binding is immutable")
                return stored
            session.add(
                ProcessObservationDecisionRow(
                    decision_id=decision_id,
                    observation_id=observation_id,
                    record_digest=binding.digest,
                    record_json=binding.model_dump(mode="json"),
                    created_at=timestamp,
                )
            )
            await session.flush()
            return binding

    async def inspect_decision_binding(
        self, session: AsyncSession, *, decision_id: str
    ) -> ProcessObservationDecisionBinding:
        """Privileged integrity check; reconstructible after lease release or pause."""
        row = await session.get(ProcessObservationDecisionRow, decision_id)
        decision_row = await session.get(AmberAdmissionDecisionRow, decision_id)
        if row is None or decision_row is None:
            raise ValueError("decision observation binding is missing")
        binding = _binding(row)
        receipt = await self.inspect_receipt(session, observation_id=binding.observation_id)
        request, decision = _decision(decision_row)
        identity = await self.store.workers.inspect_decision(session, decision_id=decision_id)
        if identity is not None:
            assignment = await self.store.workers.assignment(session, identity.assignment_id)
            if (
                identity.worker_id != receipt.worker_id
                or assignment.lease_token_digest != receipt.lease_token_digest
                or assignment.state_digest != receipt.state_digest
            ):
                raise ValueError("observation binding lost its authenticated source")
        if (
            binding.observation_receipt_digest != receipt.digest
            or binding.observation_digest != receipt.observation_digest
            or binding.request_digest != decision.request_digest
            or binding.decision_digest != sha256_digest(decision)
            or binding.disposition != decision.disposition
            or binding.declared_role_id != request.role_id
            or binding.declared_worker_model_digest != request.worker_model_digest
            or not receipt.created_at
            <= request.requested_at
            <= decision.decided_at
            <= binding.bound_at
        ):
            raise ValueError("decision binding has inconsistent lineage")
        return binding

    async def _current_receipt(
        self,
        session: AsyncSession,
        *,
        observation_id: str,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> tuple[ProcessObservationReceipt, _ObservationContext]:
        context = await self._context(
            session,
            rollout_id=rollout_id,
            lease_token=lease_token,
            worker_id=worker_id,
            now=now,
            worker_access=worker_access,
        )
        receipt = await self.inspect_receipt(session, observation_id=observation_id)
        _same_observation(receipt, self._project(context, worker_id, lease_token, now))
        await self._validate_ownership(session, receipt, now=now)
        return receipt, context

    async def _context(
        self,
        session: AsyncSession,
        *,
        rollout_id: str,
        lease_token: str,
        worker_id: str,
        now: datetime,
        worker_access: ProcessWorkerAccess | None = None,
    ) -> _ObservationContext:
        if self.store.content.policy.digest != self._policy.content_policy_digest:
            raise ValueError("configured content policy changed")
        rollout = await session.scalar(
            select(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == rollout_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            rollout is None
            or not worker_id.strip()
            or not lease_token.strip()
            or rollout.status != RolloutStatus.ACTIVE.value
            or rollout.paused
            or rollout.lease_owner != worker_id
            or rollout.lease_token != lease_token
            or rollout.lease_expires_at is None
            or not _utc(rollout.updated_at) <= now < _utc(rollout.lease_expires_at)
        ):
            raise ValueError("observation requires a current owned lease")
        from padawan.pprl.tasks import ProcessTaskStore

        await ProcessTaskStore().check_rollout(session, rollout)
        await self.store.workers.require(
            session, rollout=rollout, access=worker_access, now=now, worker_id=worker_id
        )
        execution_row = await session.get(ProcessExecutionRow, rollout.execution_digest)
        instance_row = await session.get(ProjectInstanceRow, rollout.instance_id)
        if execution_row is None or instance_row is None:
            raise ValueError("observation lost its execution or instance")
        execution = ProcessExecutionManifest.model_validate(execution_row.record_json, strict=False)
        instance = ProjectInstance.model_validate(instance_row.record_json, strict=False)
        registry = ProcessDistributionRegistry()
        program = await registry.get_program(session, program_digest=rollout.program_digest)
        distribution = await registry.get_distribution(
            session, distribution_digest=rollout.distribution_digest
        )
        envelope = await self.store.amber.get(
            session, authorization_digest=rollout.authorization_digest
        )
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == rollout.authorization_digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            head is None
            or head.status != AmberStatus.ACTIVE.value
            or not envelope.created_at <= _utc(head.updated_at) <= now < envelope.expires_at
        ):
            raise ValueError("observation requires current Amber authority")
        if (
            sha256_digest(execution_row.record_json) != rollout.execution_digest
            or sha256_digest(execution) != rollout.execution_digest
            or execution.execution_id != execution_row.execution_id
            or execution.program_digest != execution_row.program_digest
            or execution.program_digest != rollout.program_digest
            or execution.program_digest != envelope.program_digest
            or sha256_digest(program) != execution.program_digest
            or program.distribution_digest != rollout.distribution_digest
            or program.persistence_mode not in envelope.persistence_modes
            or execution.distribution_digest != execution_row.distribution_digest
            or execution.distribution_digest != rollout.distribution_digest
            or execution.distribution_digest != envelope.distribution_digest
            or sha256_digest(distribution) != execution.distribution_digest
            or execution.instance_digest != execution_row.instance_digest
            or execution.instance_digest != instance_row.instance_digest
            or sha256_digest(instance_row.record_json) != execution.instance_digest
            or sha256_digest(instance) != execution.instance_digest
            or instance.instance_id != rollout.instance_id
            or instance.distribution_digest != rollout.distribution_digest
            or instance.distribution_digest != instance_row.distribution_digest
            or instance.split.value != instance_row.split
            or instance.split.value != rollout.split
            or instance.split not in envelope.allowed_splits
            or len([p for p in distribution.partitions if p.split == instance.split]) != 1
            or instance.seed != instance_row.seed
            or execution.seed != rollout.seed
            or execution.amber_authorization_digest != rollout.authorization_digest
            or execution.amber_authorization_digest != execution_row.authorization_digest
            or execution.environment_fingerprint != execution_row.environment_fingerprint
            or execution.environment_fingerprint != instance.environment_fingerprint
            or instance.environment_fingerprint != instance_row.environment_fingerprint
            or execution.environment_fingerprint != envelope.environment.environment_fingerprint
            or execution.created_at != _utc(execution_row.created_at)
            or instance.created_at != _utc(instance_row.created_at)
            or max(
                execution.created_at,
                instance.created_at,
                distribution.created_at,
                program.created_at,
            )
            > now
            or not {sha256_digest(m) for m in execution.worker_models}.issubset(
                envelope.allowed_worker_model_digests
            )
        ):
            raise ValueError("observation has inconsistent experiment identity")
        rights = (execution.output_rights, distribution.rights)
        for item in rights:
            if (
                not item.permits(RightsUse.INTERNAL_RESEARCH)
                or not item.permits(RightsUse.EVIDENCE_RETENTION)
                or item.reviewed_at is None
                or item.reviewed_at > now
            ):
                raise ValueError("observation lacks research and retention rights")
        state = await self.store.get_state(session, state_id=rollout.current_state_id)
        if state.rollout_id != rollout_id or state.sequence != rollout.sequence:
            raise ValueError("observation state differs from the current rollout head")
        await self.store._validate_state_references(
            session, state, execution_digest=rollout.execution_digest, now=now
        )
        content = await session.get(ProcessContentAdmissionRow, ("state", state.state_id))
        assert content is not None  # verified, including its full source/receipt digest, above
        return _ObservationContext(
            rollout,
            execution,
            program,
            state,
            content.record_digest,
            head.sequence,
            tuple(sorted({sha256_digest(item) for item in rights})),
        )

    def _project(
        self,
        context: _ObservationContext,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> ProcessObservationReceipt:
        public = _public_observation(context.state)
        encoded = canonical_json_bytes(public)
        if len(encoded) > self._policy.maximum_bytes:
            raise ValueError("observation exceeds its byte bound")
        lease_digest = sha256_digest(lease_token)
        identity = sha256_digest(
            {
                "rollout_id": context.rollout.rollout_id,
                "state_id": context.state.state_id,
                "worker_id": worker_id,
                "lease_token_digest": lease_digest,
                "authorization_sequence": context.authorization_sequence,
                "policy_digest": self._policy.digest,
            }
        )
        return ProcessObservationReceipt(
            observation_id=f"process-observation-{identity[7:]}",
            rollout_id=context.rollout.rollout_id,
            state_id=context.state.state_id,
            state_digest=context.state.state_digest,
            execution_digest=context.rollout.execution_digest,
            content_receipt_digest=context.content_receipt_digest,
            worker_id=worker_id,
            lease_token_digest=lease_digest,
            authorization_digest=context.rollout.authorization_digest,
            authorization_sequence=context.authorization_sequence,
            rights_digests=context.rights_digests,
            policy=self._policy,
            policy_digest=self._policy.digest,
            observation_json=encoded.decode("utf-8"),
            observation_digest=sha256_digest(encoded),
            created_at=now,
        )

    async def _validate_ownership(
        self, session: AsyncSession, receipt: ProcessObservationReceipt, *, now: datetime
    ) -> None:
        references = _result(receipt).observation.state.artifact_refs
        if self.store.evidence is not None:
            await self.store.evidence.validate_process_ownership(
                session,
                references=references,
                execution_digest=receipt.execution_digest,
                owner_type="process_observation",
                owner_id=receipt.observation_id,
                now=now,
            )
        elif (
            references
            or await session.scalar(
                select(ArtifactReferenceRow.artifact_id)
                .where(
                    ArtifactReferenceRow.owner_type == "process_observation",
                    ArtifactReferenceRow.owner_id == receipt.observation_id,
                )
                .limit(1)
            )
            is not None
        ):
            raise ValueError("observation retention has no configured evidence boundary")


def _public_observation(state: ProjectStateVersion) -> ProcessWorkerObservation:
    """Pure allowlist shared by brokers *after* source/use admission; grants no authority."""
    source = state.payload
    return ProcessWorkerObservation(
        state=ProcessWorkerState(
            objective=source.objective,
            plan=source.plan,
            hypotheses=source.hypotheses,
            claims=source.claims,
            artifact_refs=_public_references(state),
            dependencies=source.dependencies,
            budget_usage=source.budget_usage,
            worker_assignments=source.worker_assignments,
            unresolved_risks=source.unresolved_risks,
            memory_refs=source.memory_refs,
            extension_state=source.extension_state,
        )
    )


def _public_references(state: ProjectStateVersion) -> tuple[ProcessArtifactRef, ...]:
    references = state.payload.artifact_refs
    if any(not isinstance(reference, ProcessArtifactRef) for reference in references):
        raise ValueError("legacy storage reference cannot be projected")
    return tuple(reference for reference in references if isinstance(reference, ProcessArtifactRef))


def _same_observation(
    stored: ProcessObservationReceipt, current: ProcessObservationReceipt
) -> None:
    if stored.created_at > current.created_at or stored != current.model_copy(
        update={"created_at": stored.created_at}
    ):
        raise ValueError("observation differs from its current source or authority")


def _result(receipt: ProcessObservationReceipt) -> ProcessObservationResult:
    return ProcessObservationResult(
        receipt.observation_id, receipt.observation_json.encode("utf-8")
    )


def _receipt(row: ProcessObservationRow) -> ProcessObservationReceipt:
    record = ProcessObservationReceipt.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or record.digest != row.record_digest
        or record.observation_id != row.observation_id
        or record.rollout_id != row.rollout_id
        or record.state_id != row.state_id
        or record.execution_digest != row.execution_digest
        or record.content_receipt_digest != row.content_receipt_digest
        or record.worker_id != row.worker_id
        or record.lease_token_digest != row.lease_token_digest
        or record.authorization_sequence != row.authorization_sequence
        or record.policy_digest != row.policy_digest
        or record.observation_digest != row.observation_digest
        or record.created_at != _utc(row.created_at)
    ):
        raise ValueError("observation receipt is inconsistent")
    return record


def _binding(row: ProcessObservationDecisionRow) -> ProcessObservationDecisionBinding:
    record = ProcessObservationDecisionBinding.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or record.digest != row.record_digest
        or record.decision_id != row.decision_id
        or record.observation_id != row.observation_id
        or record.bound_at != _utc(row.created_at)
    ):
        raise ValueError("observation decision binding is inconsistent")
    return record


def _decision(row: AmberAdmissionDecisionRow) -> tuple[AmberActionRequest, AmberAdmissionDecision]:
    request = AmberActionRequest.model_validate(row.request_json, strict=False)
    record = AmberAdmissionDecision.model_validate(row.record_json, strict=False)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or sha256_digest(record) != row.record_digest
        or sha256_digest(row.request_json) != row.request_digest
        or sha256_digest(request) != row.request_digest
        or record.request_digest != row.request_digest
        or record.decision_id != row.decision_id
        or record.authorization_digest != row.authorization_digest
        or request.authorization_digest != row.authorization_digest
        or record.authorization_sequence != row.authorization_sequence
        or record.rollout_id != row.rollout_id
        or request.rollout_id != row.rollout_id
        or record.disposition.value != row.disposition
        or record.decided_at != _utc(row.decided_at)
    ):
        raise ValueError("observation decision is inconsistent")
    return request, record


def _timestamp(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("observation time must be timezone-aware")
    return timestamp.astimezone(UTC)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
