from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDecision,
    AmberAdmissionDisposition,
    AmberAuthorizationEnvelope,
    AmberStatus,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    AmberAuthorizationRow,
    ArtifactRow,
    ExternalCallRow,
    ProcessEventRow,
    ProcessExecutionRow,
    ProcessRolloutRow,
    ProcessStateRow,
    ProcessWorkerInvocationRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import (
    ProcessExecutionManifest,
    ProjectStateVersion,
    project_state_digest,
)


@dataclass(frozen=True)
class ProcessGenerationResult:
    invocation_id: str
    generation: GenerationResult
    artifact_refs: tuple[ArtifactRef, ...]


class ProcessGenerationExecutor:
    """Bind crash-safe model I/O to one admitted process action and rollout lease."""

    def __init__(
        self,
        *,
        database: Database,
        executor: IdempotentGenerationExecutor,
    ) -> None:
        self.database = database
        self.executor = executor

    async def execute(
        self,
        *,
        rollout_id: str,
        lease_token: str,
        amber_decision_id: str,
        invocation_id: str,
        role_id: str,
        worker_model_digest: str,
        purpose: str,
        provider: str,
        request: GenerationRequest,
        research_execution_digest: str | None = None,
    ) -> ProcessGenerationResult:
        timestamp = datetime.now(UTC)
        async with self.database.transaction() as session:
            rollout = await session.get(ProcessRolloutRow, rollout_id)
            if rollout is None:
                raise KeyError(rollout_id)
            if (
                rollout.lease_token != lease_token
                or rollout.lease_expires_at is None
                or timestamp >= _as_utc(rollout.lease_expires_at)
            ):
                raise PermissionError("process generation requires the current rollout lease")
            execution_row = await session.get(ProcessExecutionRow, rollout.execution_digest)
            if execution_row is None:
                raise RuntimeError("process generation lost its process execution")
            execution = ProcessExecutionManifest.model_validate(
                execution_row.record_json, strict=False
            )
            if sha256_digest(execution) != rollout.execution_digest:
                raise RuntimeError("process generation found a corrupted process execution")
            matching_models = tuple(
                model
                for model in execution.worker_models
                if sha256_digest(model) == worker_model_digest
            )
            if len(matching_models) != 1 or matching_models[0].purpose != purpose:
                raise PermissionError("process generation differs from its bound worker identity")
            serving_identity = matching_models[0]
            if request.store or request.previous_response_id is not None or request.tools:
                raise PermissionError(
                    "process generation requires local state and separately admitted tools"
                )
            state_row = await session.get(ProcessStateRow, rollout.current_state_id)
            if state_row is None:
                raise RuntimeError("process generation lost its current project state")
            state = ProjectStateVersion.model_validate(state_row.record_json, strict=False)
            if state.state_digest != state_row.state_digest or (
                state.state_digest
                != project_state_digest(
                    state_id=state.state_id,
                    rollout_id=state.rollout_id,
                    sequence=state.sequence,
                    parent_state_id=state.parent_state_id,
                    triggering_event_id=state.triggering_event_id,
                    payload=state.payload,
                    created_at=state.created_at,
                )
            ):
                raise RuntimeError("process generation found a corrupted project state")
            decision_row = await session.get(AmberAdmissionDecisionRow, amber_decision_id)
            if decision_row is None:
                raise PermissionError("process generation requires an Amber decision")
            decision = AmberAdmissionDecision.model_validate(decision_row.record_json, strict=False)
            action = AmberActionRequest.model_validate(decision_row.request_json, strict=False)
            if (
                sha256_digest(decision) != decision_row.record_digest
                or sha256_digest(action) != decision_row.request_digest
                or decision.request_digest != decision_row.request_digest
                or decision.authorization_sequence != decision_row.authorization_sequence
                or decision.disposition.value != decision_row.disposition
                or decision.authorization_digest != decision_row.authorization_digest
                or decision.rollout_id != decision_row.rollout_id
                or _as_utc(decision.decided_at) != _as_utc(decision_row.decided_at)
                or _as_utc(decision.decided_at) != _as_utc(action.requested_at)
            ):
                raise RuntimeError("process generation cites a corrupted Amber decision")
            consumed_event = await session.scalar(
                select(ProcessEventRow).where(
                    ProcessEventRow.amber_decision_id == amber_decision_id
                )
            )
            if consumed_event is not None:
                raise PermissionError("process generation Amber decision is already consumed")
            if (
                decision.disposition != AmberAdmissionDisposition.ADMITTED
                or decision.rollout_id != rollout_id
                or decision.authorization_digest != rollout.authorization_digest
                or action.rollout_id != rollout_id
                or action.authorization_digest != rollout.authorization_digest
                or action.rollout_sequence != rollout.sequence
                or action.state_digest != state.state_digest
                or action.lease_token_digest != sha256_digest(lease_token)
                or action.program_digest != rollout.program_digest
                or action.distribution_digest != rollout.distribution_digest
                or action.split.value != rollout.split
                or action.environment_fingerprint != execution.environment_fingerprint
                or action.role_id != role_id
                or action.worker_model_digest != worker_model_digest
            ):
                raise PermissionError("process generation differs from its Amber admission")
            authorization_head = await session.scalar(
                select(AmberAuthorizationHeadRow)
                .where(
                    AmberAuthorizationHeadRow.authorization_digest == rollout.authorization_digest
                )
                .with_for_update()
            )
            authorization_row = await session.get(
                AmberAuthorizationRow, rollout.authorization_digest
            )
            if authorization_head is None or authorization_row is None:
                raise PermissionError("process generation lost its Amber authorization")
            authorization = AmberAuthorizationEnvelope.model_validate(
                authorization_row.record_json, strict=False
            )
            if (
                authorization.digest != rollout.authorization_digest
                or authorization_row.authorization_digest != rollout.authorization_digest
                or authorization_head.status != AmberStatus.ACTIVE.value
                or authorization_head.sequence != decision.authorization_sequence
                or timestamp >= authorization.expires_at
            ):
                raise PermissionError(
                    "process generation requires active unexpired Amber authority"
                )
            reserved_seconds = float(action.projected_usage.wall_time_seconds) - float(
                state.payload.budget_usage.wall_time_seconds
            )
            reserved_input_tokens = (
                action.projected_usage.input_tokens - state.payload.budget_usage.input_tokens
            )
            reserved_output_tokens = (
                action.projected_usage.output_tokens - state.payload.budget_usage.output_tokens
            )
            reserved_artifact_bytes = (
                action.projected_usage.artifact_bytes - state.payload.budget_usage.artifact_bytes
            )
            action_deadline = action.requested_at + timedelta(seconds=reserved_seconds)
            remaining_seconds = (action_deadline - datetime.now(UTC)).total_seconds()
            if (
                reserved_seconds <= 0
                or remaining_seconds <= 0
                or reserved_input_tokens < 0
                or reserved_output_tokens < request.sampling.max_output_tokens
                or reserved_artifact_bytes < 0
                or action.projected_usage.actions != state.payload.budget_usage.actions + 1
                or _as_utc(action.requested_at) < _as_utc(state.created_at)
            ):
                raise PermissionError(
                    "process generation has no valid remaining admitted reservation"
                )
            existing = await session.get(ProcessWorkerInvocationRow, invocation_id)
            decision_invocation = await session.scalar(
                select(ProcessWorkerInvocationRow).where(
                    ProcessWorkerInvocationRow.amber_decision_id == amber_decision_id
                )
            )
            if (
                decision_invocation is not None
                and decision_invocation.invocation_id != invocation_id
            ):
                raise PermissionError("Amber decision is bound to another process invocation")
            if existing is not None:
                if (
                    existing.request_id != request.request_id
                    or existing.rollout_id != rollout_id
                    or existing.amber_decision_id != amber_decision_id
                    or existing.role_id != role_id
                    or existing.worker_model_digest != worker_model_digest
                    or existing.research_execution_digest != research_execution_digest
                ):
                    raise ValueError("process invocation ID reused with different content")
                if existing.status in {"failed", "cancelled"}:
                    raise RuntimeError(f"cannot resume {existing.status} process invocation")
                existing.status = "running"
                existing.error = None
                existing.completed_at = None
            else:
                active_workers = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(ProcessWorkerInvocationRow)
                        .join(
                            ProcessRolloutRow,
                            ProcessRolloutRow.rollout_id == ProcessWorkerInvocationRow.rollout_id,
                        )
                        .where(
                            ProcessRolloutRow.authorization_digest == rollout.authorization_digest,
                            ProcessWorkerInvocationRow.status == "running",
                        )
                    )
                    or 0
                )
                if active_workers >= authorization.budgets.concurrent_workers:
                    raise PermissionError("Amber worker concurrency is exhausted")
                session.add(
                    ProcessWorkerInvocationRow(
                        invocation_id=invocation_id,
                        request_id=request.request_id,
                        rollout_id=rollout_id,
                        role_id=role_id,
                        worker_model_digest=worker_model_digest,
                        amber_decision_id=amber_decision_id,
                        research_execution_digest=research_execution_digest,
                        status="running",
                        request_artifact_id=None,
                        response_artifact_id=None,
                        usage={},
                        error=None,
                        created_at=timestamp,
                        completed_at=None,
                    )
                )

        try:
            async with asyncio.timeout(remaining_seconds):
                generation = await self.executor.execute(
                    process_rollout_id=rollout_id,
                    purpose=purpose,
                    provider=provider,
                    request=request,
                )
                if (
                    generation.request_id != request.request_id
                    or generation.provider != provider
                    or generation.model_id != serving_identity.model_id
                    or generation.protocol != serving_identity.protocol
                    or _usage(generation, "input_tokens") > reserved_input_tokens
                    or _usage(generation, "output_tokens") > reserved_output_tokens
                ):
                    raise PermissionError(
                        "process generation result exceeds or differs from its admission"
                    )
        except asyncio.CancelledError:
            await self._mark_terminal(
                invocation_id=invocation_id,
                request_id=request.request_id,
                status="cancelled",
                error={"error_class": "CancelledError", "message": "generation cancelled"},
            )
            raise
        except Exception as exc:
            await self._mark_terminal(
                invocation_id=invocation_id,
                request_id=request.request_id,
                status="failed",
                error={"error_class": type(exc).__name__, "message": str(exc)},
            )
            raise

        try:
            async with self.database.transaction() as session:
                invocation = await session.get(ProcessWorkerInvocationRow, invocation_id)
                call = await session.get(ExternalCallRow, request.request_id)
                if invocation is None or call is None:
                    raise RuntimeError("completed process generation lost its durable intent")
                if call.process_rollout_id != rollout_id or call.status != "completed":
                    raise RuntimeError("process generation ledger owner or status is invalid")
                if call.response_artifact_id is None:
                    raise RuntimeError("completed process generation has no response artifact")
                request_artifact = await session.get(ArtifactRow, call.request_artifact_id)
                response_artifact = await session.get(ArtifactRow, call.response_artifact_id)
                if request_artifact is None or response_artifact is None:
                    raise RuntimeError("process generation artifact is missing")
                references = tuple(
                    sorted(
                        (
                            _artifact_reference(request_artifact),
                            _artifact_reference(response_artifact),
                        ),
                        key=lambda item: item.artifact_id,
                    )
                )
                if sum(reference.size_bytes for reference in references) > (
                    reserved_artifact_bytes
                ):
                    raise PermissionError("process generation artifacts exceed their admission")
                for reference in references:
                    await ArtifactInformationStore(self.executor.catalog).classify(
                        session,
                        artifact=reference,
                        information_class=InformationClass.FORENSIC,
                        classified_by="padawan.pprl.generation",
                        reason="raw request or response for a governed process invocation",
                        classified_at=datetime.now(UTC),
                    )
                    await self.executor.catalog.reference(
                        session,
                        reference,
                        owner_type="process_worker_invocation",
                        owner_id=invocation_id,
                    )
                invocation.request_artifact_id = request_artifact.artifact_id
                invocation.response_artifact_id = response_artifact.artifact_id
                invocation.usage = generation.usage
                invocation.status = "completed"
                invocation.error = None
                invocation.completed_at = datetime.now(UTC)
        except Exception as exc:
            await self._mark_terminal(
                invocation_id=invocation_id,
                request_id=request.request_id,
                status="failed",
                error={"error_class": type(exc).__name__, "message": str(exc)},
            )
            raise
        return ProcessGenerationResult(
            invocation_id=invocation_id,
            generation=generation,
            artifact_refs=references,
        )

    async def _mark_terminal(
        self,
        *,
        invocation_id: str,
        request_id: str,
        status: str,
        error: dict[str, str],
    ) -> None:
        async with self.database.transaction() as session:
            invocation = await session.get(ProcessWorkerInvocationRow, invocation_id)
            call = await session.get(ExternalCallRow, request_id)
            if invocation is None or invocation.status == "completed":
                return
            invocation.status = status
            invocation.error = error
            invocation.completed_at = datetime.now(UTC)
            if call is not None:
                invocation.request_artifact_id = call.request_artifact_id
                invocation.response_artifact_id = call.response_artifact_id


def _artifact_reference(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef.model_validate(
        {
            "artifact_id": row.artifact_id,
            "uri": row.uri,
            "digest": row.digest,
            "media_type": row.media_type,
            "size_bytes": row.size_bytes,
            "restricted": row.restricted,
            "raw_data": row.raw_data,
        },
        strict=False,
    )


def _usage(generation: GenerationResult, key: str) -> int:
    value = generation.usage.get(key)
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"process generation result has invalid {key} usage")
    return value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value
