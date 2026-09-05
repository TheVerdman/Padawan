from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select

from padawan.artifacts.information import ProcessArtifactRef
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAdmissionDisposition,
)
from padawan.governance.amber_store import AmberStore
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ProcessExecutionRow,
    ProcessRolloutRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectBudgetUsage,
    ProjectStatePayload,
    RolloutStatus,
    stored_process_reference_id,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.observation_contracts import ProcessWorkerObservation
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ClaimedProcessRollout, ProcessStore


@dataclass(frozen=True)
class ProcessActionProposal:
    """Side-effect-free proposal evaluated before the action crosses its boundary."""

    event_kind: ProcessEventKind
    role_id: str
    worker_model_digest: str
    target_class: str
    incremental_usage: ProjectBudgetUsage
    tool_id: str | None = None
    tool_digest: str | None = None
    tool_operation: str | None = None
    requested_destination: str | None = None
    triggered_stop_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.role_id.strip() or not self.worker_model_digest.strip():
            raise ValueError("process proposal role and worker model must be non-empty")
        if not self.target_class.strip():
            raise ValueError("process proposal target class must be non-empty")
        if self.incremental_usage.actions != 1:
            raise ValueError("one process proposal must reserve exactly one action")
        if float(self.incremental_usage.wall_time_seconds) <= 0:
            raise ValueError("process proposal must reserve positive wall time")
        tool_fields = (self.tool_id, self.tool_digest, self.tool_operation)
        if any(value is None for value in tool_fields) != all(
            value is None for value in tool_fields
        ):
            raise ValueError(
                "process proposal tool identity, digest, and operation must be supplied together"
            )


@dataclass(frozen=True)
class ProcessActionResult:
    event_payload: dict[str, Any]
    resulting_state: ProjectStatePayload
    artifact_refs: tuple[ProcessArtifactRef, ...] = ()
    worker_invocation_id: str | None = None
    research_execution_digest: str | None = None
    to_status: RolloutStatus = RolloutStatus.ACTIVE


class ProcessPlanner(Protocol):
    def propose(self, observation: ProcessWorkerObservation) -> ProcessActionProposal:
        """Return a proposal without network, tool, filesystem, or model side effects."""
        ...


class ProcessActionExecutor(Protocol):
    """Trusted effect adapter; its broker arguments must never be sent to a worker."""

    async def execute(
        self,
        claimed: ClaimedProcessRollout,
        proposal: ProcessActionProposal,
        admission: AmberAdmissionDecision,
        observation: ProcessWorkerObservation,
    ) -> ProcessActionResult: ...


class ProcessCoordinator:
    """One admitted, persisted action per lease for project-scale rollouts."""

    def __init__(
        self,
        *,
        database: Database,
        store: ProcessStore,
        amber: AmberStore,
        planner: ProcessPlanner,
        executor: ProcessActionExecutor,
        worker_id: str,
        lease_for: timedelta = timedelta(minutes=5),
        observations: ProcessObservationStore | None = None,
    ) -> None:
        self.database = database
        self.store = store
        self.amber = amber
        self.planner = planner
        self.executor = executor
        self.observations = observations or ProcessObservationStore(store)
        if self.observations.store is not store:
            raise ValueError("coordinator observation boundary must use its process store")
        self.worker_id = worker_id
        self.lease_for = lease_for

    async def run(self, *, budget: int = 1) -> int:
        if budget < 0:
            raise ValueError("process action budget cannot be negative")
        completed = 0
        while completed < budget:
            async with self.database.transaction() as session:
                claimed = await self.store.claim_next(
                    session,
                    worker_id=self.worker_id,
                    lease_for=self.lease_for,
                )
                if claimed is None:
                    break
                # A failed projection must roll back the newly acquired claim too.
                observed = await self.observations.observe_claim(
                    session,
                    rollout_id=claimed.rollout.rollout_id,
                    lease_token=claimed.lease_token,
                    worker_id=self.worker_id,
                    now=datetime.now(UTC),
                )
            try:
                proposal = self.planner.propose(observed.observation)
                now = datetime.now(UTC)
                async with self.database.transaction() as session:
                    execution_row = await session.get(
                        ProcessExecutionRow, claimed.rollout.execution_digest
                    )
                    if execution_row is None:
                        raise RuntimeError("claimed rollout lost its process execution")
                    execution = ProcessExecutionManifest.model_validate(
                        execution_row.record_json, strict=False
                    )
                    if sha256_digest(execution) != claimed.rollout.execution_digest:
                        raise RuntimeError("claimed rollout process execution digest is invalid")
                    program = await ProcessDistributionRegistry().get_program(
                        session, program_digest=claimed.rollout.program_digest
                    )
                    active_workers = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(ProcessWorkerInvocationRow)
                            .join(
                                ProcessRolloutRow,
                                ProcessRolloutRow.rollout_id
                                == ProcessWorkerInvocationRow.rollout_id,
                            )
                            .where(
                                ProcessRolloutRow.authorization_digest
                                == execution.amber_authorization_digest,
                                ProcessWorkerInvocationRow.status == "running",
                            )
                        )
                        or 0
                    )
                    request = _action_request(
                        claimed=claimed,
                        proposal=proposal,
                        execution=execution,
                        program=program,
                        requested_at=now,
                    )
                    decision = await self.amber.admit(
                        session,
                        request=request,
                        active_workers=active_workers,
                        decision_id=f"amber-decision-{uuid4()}",
                    )
                # Keep the exact admission or denial even if later observation
                # binding fails. An unbound decision never reaches this executor.
                async with self.database.transaction() as session:
                    await self.observations.bind_decision(
                        session,
                        observation_id=observed.observation_id,
                        decision_id=decision.decision_id,
                        rollout_id=claimed.rollout.rollout_id,
                        lease_token=claimed.lease_token,
                        worker_id=self.worker_id,
                        now=datetime.now(UTC),
                    )
                    if decision.disposition != AmberAdmissionDisposition.ADMITTED:
                        target = (
                            RolloutStatus.QUARANTINED
                            if "stop_condition_triggered" in decision.reason_codes
                            else RolloutStatus.REVIEW_REQUIRED
                        )
                        await self.store.release_claim(
                            session,
                            rollout_id=claimed.rollout.rollout_id,
                            lease_token=claimed.lease_token,
                            to_status=target,
                            error={
                                "classification": "amber_admission",
                                "decision_id": decision.decision_id,
                                "disposition": decision.disposition.value,
                                "reason_codes": list(decision.reason_codes),
                            },
                            occurred_at=now,
                        )
                        continue
                    rollout_row = await session.scalar(
                        select(ProcessRolloutRow)
                        .where(
                            ProcessRolloutRow.rollout_id == claimed.rollout.rollout_id,
                            ProcessRolloutRow.lease_token == claimed.lease_token,
                        )
                        .with_for_update()
                    )
                    if rollout_row is None:
                        raise RuntimeError("process action lost its rollout lease before execution")
                    reserved_until = (
                        now
                        + timedelta(seconds=float(proposal.incremental_usage.wall_time_seconds))
                        + self.lease_for
                    )
                    current_expiry = rollout_row.lease_expires_at
                    if current_expiry is None or _as_utc(current_expiry) < reserved_until:
                        rollout_row.lease_expires_at = reserved_until
                    # Rebuild from retained bytes, not from the planner's mutable nested values.
                    observation = await self.observations.read(
                        session,
                        observation_id=observed.observation_id,
                        rollout_id=claimed.rollout.rollout_id,
                        lease_token=claimed.lease_token,
                        worker_id=self.worker_id,
                        now=datetime.now(UTC),
                    )
                async with asyncio.timeout(float(proposal.incremental_usage.wall_time_seconds)):
                    result = await self.executor.execute(claimed, proposal, decision, observation)
                _validate_result_usage(claimed, proposal, result)
                async with self.database.transaction() as session:
                    await self.store.append_event(
                        session,
                        rollout_id=claimed.rollout.rollout_id,
                        lease_token=claimed.lease_token,
                        amber_decision_id=decision.decision_id,
                        kind=proposal.event_kind,
                        actor_id=self.worker_id,
                        payload=result.event_payload,
                        resulting_state=result.resulting_state,
                        artifact_refs=result.artifact_refs,
                        worker_invocation_id=result.worker_invocation_id,
                        research_execution_digest=result.research_execution_digest,
                        to_status=result.to_status,
                        occurred_at=datetime.now(UTC),
                    )
                completed += 1
            except Exception as exc:
                async with self.database.transaction() as session:
                    await self.store.release_claim(
                        session,
                        rollout_id=claimed.rollout.rollout_id,
                        lease_token=claimed.lease_token,
                        to_status=RolloutStatus.FAILED,
                        error={
                            "classification": "process_action_failure",
                            "error_class": exc.__class__.__name__,
                            "message": str(exc),
                        },
                    )
                raise
        return completed


def _action_request(
    *,
    claimed: ClaimedProcessRollout,
    proposal: ProcessActionProposal,
    execution: ProcessExecutionManifest,
    program: ProcessProgram,
    requested_at: datetime,
) -> AmberActionRequest:
    role = next((item for item in program.worker_roles if item.role_id == proposal.role_id), None)
    if role is None:
        raise ValueError("process proposal refers to an undeclared worker role")
    if proposal.tool_id is not None and proposal.tool_id not in role.allowed_tool_ids:
        raise ValueError("process proposal tool is unavailable to its worker role")
    if proposal.tool_id is not None:
        tool = next(
            (item for item in program.tools if item.component_id == proposal.tool_id),
            None,
        )
        if tool is None or tool.digest != proposal.tool_digest:
            raise ValueError("process proposal tool identity differs from its program")
    worker_digests = {sha256_digest(model) for model in execution.worker_models}
    if proposal.worker_model_digest not in worker_digests:
        raise ValueError("process proposal refers to a worker outside its execution")
    projected = _add_usage(
        claimed.state.payload.budget_usage,
        proposal.incremental_usage,
    )
    return AmberActionRequest(
        authorization_digest=execution.amber_authorization_digest,
        rollout_id=claimed.rollout.rollout_id,
        rollout_sequence=claimed.rollout.sequence,
        state_digest=claimed.state.state_digest,
        lease_token_digest=sha256_digest(claimed.lease_token),
        program_digest=claimed.rollout.program_digest,
        distribution_digest=claimed.rollout.distribution_digest,
        split=claimed.rollout.split,
        persistence_mode=program.persistence_mode,
        event_kind=proposal.event_kind,
        role_id=proposal.role_id,
        worker_model_digest=proposal.worker_model_digest,
        tool_id=proposal.tool_id,
        tool_digest=proposal.tool_digest,
        tool_operation=proposal.tool_operation,
        target_class=proposal.target_class,
        environment_fingerprint=execution.environment_fingerprint,
        projected_usage=projected,
        projected_artifact_bytes=projected.artifact_bytes,
        requested_destination=proposal.requested_destination,
        triggered_stop_conditions=tuple(sorted(proposal.triggered_stop_conditions)),
        requested_at=requested_at,
    )


def _add_usage(left: ProjectBudgetUsage, right: ProjectBudgetUsage) -> ProjectBudgetUsage:
    return ProjectBudgetUsage(
        actions=left.actions + right.actions,
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        artifact_bytes=left.artifact_bytes + right.artifact_bytes,
        wall_time_seconds=float(left.wall_time_seconds) + float(right.wall_time_seconds),
        cost=float(left.cost) + float(right.cost),
    )


def _validate_result_usage(
    claimed: ClaimedProcessRollout,
    proposal: ProcessActionProposal,
    result: ProcessActionResult,
) -> None:
    expected = _add_usage(
        claimed.state.payload.budget_usage,
        proposal.incremental_usage,
    )
    if result.resulting_state.budget_usage != expected:
        raise ValueError("process result budget usage differs from its admitted projection")
    prior_artifact_ids = {
        stored_process_reference_id(reference) for reference in claimed.state.payload.artifact_refs
    }
    new_artifacts = {
        stored_process_reference_id(reference): reference
        for reference in (*result.artifact_refs, *result.resulting_state.artifact_refs)
        if stored_process_reference_id(reference) not in prior_artifact_ids
    }
    if sum(reference.size_bytes for reference in new_artifacts.values()) > (
        proposal.incremental_usage.artifact_bytes
    ):
        raise ValueError("process result artifacts exceed their admitted byte reservation")


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
