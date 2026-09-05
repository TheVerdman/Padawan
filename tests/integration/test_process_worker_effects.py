import pytest
from sqlalchemy import delete, func, select, update

from padawan.artifacts.information import ArtifactInformationStore
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ExternalCallRow,
    ProcessGenerationWorkloadRow,
    ProcessRolloutRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.containers import ProcessContainerUnavailableError
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.evidence import ProcessEvidenceStore
from padawan.pprl.evidence_contracts import EvidenceAdmissionPolicy
from padawan.pprl.generation import ProcessGenerationExecutor, ProcessGenerationUnavailableError
from padawan.pprl.worker_broker import ProcessWorkerRequestDeniedError
from tests.helpers import CallbackGenerationClient
from tests.pprl_evidence_helpers import EvidenceContext, _admit
from tests.pprl_generation_helpers import generation_boundary
from tests.worker_helpers import (
    broker_context,
    prepare_broker_action,
    worker_proposal,
    worker_request,
)


async def revoke(ctx):
    async with ctx.database.transaction() as session:
        await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="disposable effect interruption",
            now=ctx.clock(),
        )


@pytest.mark.parametrize("phase", ["success", "before", "during"])
async def test_model_callback_obeys_credentials_and_preserves_revoked_accounting(
    database, tmp_path, pprl_now, phase
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    proposal = worker_proposal(
        ctx,
        event_kind=ProcessEventKind.CLAIM_UPDATED,
        tool_id=None,
        tool_digest=None,
        tool_operation=None,
        incremental_usage=ProjectBudgetUsage(
            actions=1,
            input_tokens=64,
            output_tokens=64,
            artifact_bytes=1_000_000,
            wall_time_seconds=30.0,
            cost=1.0,
        ),
    )
    await prepare_broker_action(ctx, proposal=proposal)

    class Client(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            result = await super().generate_prepared(request, prepared)
            if phase == "during":
                await revoke(ctx)
            return result

    client = Client(lambda request: "synthetic answer", "test-open-weight")
    external = IdempotentGenerationExecutor(
        database=database, artifacts=ctx.records.catalog.backend, client=client
    )
    boundary = generation_boundary(ctx.process, external)
    executor = ProcessGenerationExecutor(database=database, executor=external, boundary=boundary)
    kwargs = dict(
        rollout_id=ctx.claim.rollout.rollout_id,
        lease_token=ctx.claim.lease_token,
        amber_decision_id=ctx.decision.decision_id,
        invocation_id="worker-model-fixture",
        role_id=ctx.profile.role_id,
        worker_model_digest=ctx.profile.worker_model_digest,
        purpose="process_worker",
        provider="test-open-weight",
        request=boundary.request(
            request_id="worker-model-request", observation=ctx.observed.observation
        ),
        worker_access=None if phase == "before" else ctx.worker_access,
    )
    if phase == "success":
        assert (await executor.execute(**kwargs)).output.output_text == "synthetic answer"
    else:
        with pytest.raises(ProcessGenerationUnavailableError):
            await executor.execute(**kwargs)
    assert len(client.calls) == (0 if phase == "before" else 1)
    if client.calls:
        wire = canonical_json_bytes(client.calls[0])
        assert ctx.worker_access.credential.get_secret_value().encode() not in wire
        assert ctx.worker_access.assignment_id.encode() not in wire
    async with database.transaction() as session:
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        if phase == "before":
            assert (
                await session.scalar(select(func.count()).select_from(ProcessGenerationWorkloadRow))
                == 0
            )
            assert balance.held.input_tokens == 64
        else:
            assert balance.charged.input_tokens == 10 and balance.charged.output_tokens == 12
            assert balance.open_reservations == 0
            call = await session.get(ExternalCallRow, "worker-model-request")
            assert call.status == "completed" and call.response_artifact_id is not None


async def test_container_revocation_during_effect_retains_unknown_effect_hold(
    database, tmp_path, pprl_now
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    await prepare_broker_action(ctx)
    ctx.driver.after_start = lambda: revoke(ctx)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert len(ctx.driver.calls) == 1
    async with database.transaction() as session:
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.held.actions == 1 and balance.open_reservations == 1
        await ctx.process.workers.inspect_decision(session, decision_id=ctx.decision.decision_id)


@pytest.mark.parametrize("owner", ["process_worker_request", "process_observation"])
async def test_authenticated_request_requires_original_and_independent_evidence_owners(
    database, tmp_path, pprl_now, owner
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    catalog = ctx.records.catalog
    evidence = ProcessEvidenceStore(
        catalog=catalog,
        amber=ctx.amber,
        policy=EvidenceAdmissionPolicy(
            policy_id="worker-evidence",
            version="1.0.0",
            reviewer_ids=("reviewer-a",),
            maximum_bytes=100_000,
            maximum_forensic_sources=4,
            maximum_forensic_source_bytes=1_000_000,
        ),
    )
    ctx.process.evidence = evidence
    fixture = EvidenceContext(
        database,
        catalog.backend,
        catalog,
        ArtifactInformationStore(catalog),
        evidence,
        ctx.amber,
        ctx.process,
        ctx.execution,
        ctx.program,
        ProjectSplit.TRAIN,
        pprl_now,
    )
    review = await _admit(fixture)
    async with database.transaction() as session:
        await session.execute(update(ProcessRolloutRow).values(paused=True))
        await ctx.process.create_rollout(
            session,
            execution_digest=sha256_digest(ctx.execution),
            replication_index=1,
            initial_state=ProjectStatePayload(
                objective="use admitted evidence",
                artifact_refs=(review.process_reference,),
                budget_usage=ProjectBudgetUsage(artifact_bytes=review.process_reference.size_bytes),
            ),
            created_at=pprl_now(),
        )
    request = worker_request(ctx)
    reply = await ctx.broker.request(request)
    assert reply.observation.state.artifact_refs == (review.process_reference,)
    async with database.transaction() as session:
        rows = list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.artifact_id == review.candidate_artifact_id
                )
            )
        )
        assert {"process_state", "process_observation", "process_worker_request"} <= {
            row.owner_type for row in rows
        }
        await session.execute(
            delete(ArtifactReferenceRow).where(ArtifactReferenceRow.owner_type == owner)
        )
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(request)
