"""Shared process generation fixture setup."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import func, select

from padawan.governance.amber import AmberActionRequest
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ExternalCallRow,
    ProcessGenerationWorkloadRow,
    ProcessWorkerInvocationRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import ProcessEventKind, ProjectBudgetUsage
from padawan.pprl.generation import ProcessGenerationExecutor
from padawan.pprl.generation_contracts import ProcessGenerationWorkload
from padawan.pprl.observations import ProcessObservationStore
from tests.helpers import CallbackGenerationClient
from tests.pprl_generation_helpers import generation_boundary


async def _ready(ctx, *, client=None, bind=True, destination=None):
    observations = ProcessObservationStore(ctx.process)
    async with ctx.database.transaction() as session:
        claimed = await ctx.process.claim_next(
            session, worker_id="prepared-worker", lease_for=timedelta(minutes=5), now=ctx.clock()
        )
        observed = await observations.observe_claim(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            worker_id="prepared-worker",
            now=ctx.clock(),
        )
        action = AmberActionRequest(
            authorization_digest=ctx.execution.amber_authorization_digest,
            rollout_id=claimed.rollout.rollout_id,
            rollout_sequence=claimed.rollout.sequence,
            state_digest=claimed.state.state_digest,
            lease_token_digest=sha256_digest(claimed.lease_token),
            program_digest=ctx.execution.program_digest,
            distribution_digest=ctx.execution.distribution_digest,
            split=ctx.split,
            persistence_mode=ctx.program.persistence_mode,
            event_kind=ProcessEventKind.CLAIM_UPDATED,
            role_id="researcher",
            worker_model_digest=sha256_digest(ctx.execution.worker_models[0]),
            target_class="scientific_math",
            environment_fingerprint=ctx.execution.environment_fingerprint,
            projected_usage=ProjectBudgetUsage(
                actions=1,
                input_tokens=64,
                output_tokens=64,
                artifact_bytes=1_000_000,
                wall_time_seconds=30.0,
                cost=1.0,
            ),
            projected_artifact_bytes=1_000_000,
            requested_destination=destination,
            requested_at=ctx.clock(),
        )
        decision = await ctx.amber.admit(session, request=action, active_workers=0)
        if bind:
            await observations.bind_decision(
                session,
                observation_id=observed.observation_id,
                decision_id=decision.decision_id,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                worker_id="prepared-worker",
                now=ctx.clock(),
            )
    client = client or CallbackGenerationClient(lambda request: "candidate", "test-open-weight")
    external = IdempotentGenerationExecutor(
        database=ctx.database, artifacts=ctx.artifacts, client=client
    )
    boundary = generation_boundary(ctx.process, external)
    service = ProcessGenerationExecutor(database=ctx.database, executor=external, boundary=boundary)
    request = boundary.request(
        request_id="prepared-process-request", observation=observed.observation
    )
    kwargs = dict(
        rollout_id=claimed.rollout.rollout_id,
        lease_token=claimed.lease_token,
        amber_decision_id=decision.decision_id,
        invocation_id="prepared-process-invocation",
        role_id="researcher",
        worker_model_digest=sha256_digest(ctx.execution.worker_models[0]),
        purpose="process_worker",
        provider=boundary.policy.provider,
        request=request,
    )
    return SimpleNamespace(
        ctx=ctx,
        claimed=claimed,
        observed=observed,
        action=action,
        decision=decision,
        client=client,
        external=external,
        boundary=boundary,
        service=service,
        request=request,
        kwargs=kwargs,
    )


async def _receipt(ready):
    async with ready.ctx.database.transaction() as session:
        row = await session.get(ProcessGenerationWorkloadRow, ready.kwargs["invocation_id"])
        assert row is not None
        return ProcessGenerationWorkload.model_validate(row.record_json, strict=False)


async def _no_intent(ready):
    assert ready.client.calls == []
    async with ready.ctx.database.transaction() as session:
        for table in (ProcessGenerationWorkloadRow, ProcessWorkerInvocationRow, ExternalCallRow):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert not list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_generation_workload"
                )
            )
        )
