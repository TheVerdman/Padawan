"""Fresh, explicitly funded synthetic institutions for resource-boundary tests."""

from datetime import timedelta
from types import SimpleNamespace

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.resource_contracts import ProcessModelRate
from padawan.pprl.store import ProcessStore
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    fund_resources,
    program,
)


async def resource_context(
    database, tmp_path, clock, *, rollouts=1, caps=None, fund=True, rate=None
):
    registry, amber = ProcessDistributionRegistry(), AmberStore()
    process = ProcessStore(amber)
    async with database.transaction() as session:
        dd = await registry.register_distribution(session, distribution())
        pp = program(dd)
        pp = pp.model_copy(
            update={
                "maximum_concurrent_workers": 4,
                "worker_roles": (pp.worker_roles[0].model_copy(update={"maximum_instances": 4}),),
            }
        )
        pd = await registry.register_program(session, pp)
        instance = await registry.sample(
            session,
            distribution_digest=dd,
            split=ProjectSplit.TRAIN,
            seed=81,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(program_digest=pd, distribution_digest=dd)
        authorization = authorization.model_copy(
            update={
                "budgets": authorization.budgets.model_copy(
                    update={"concurrent_workers": 4, **(caps or {})}
                )
            }
        )
        await amber.prepare(session, envelope=authorization, actor_id="preparer")
        for status, minute in ((AmberStatus.AUTHORIZED, 1), (AmberStatus.ACTIVE, 2)):
            await amber.transition(
                session,
                authorization_digest=authorization.digest,
                to_status=status,
                actor_id="reviewer-a",
                reason="offline resource fixture",
                evidence_refs=("review:resource-fixture",),
                occurred_at=NOW + timedelta(minutes=minute),
            )
        if fund:
            await fund_resources(
                session,
                amber,
                authorization,
                rates=None
                if rate is None
                else (
                    ProcessModelRate(
                        worker_model_digest=authorization.allowed_worker_model_digests[0], **rate
                    ),
                ),
            )
        manifest = execution(
            program_digest=pd,
            distribution_digest=dd,
            instance=instance,
            authorization_digest=authorization.digest,
        )
        await process.register_execution(session, manifest)
        for index in range(rollouts):
            await process.create_rollout(
                session,
                execution_digest=sha256_digest(manifest),
                replication_index=index,
                initial_state=ProjectStatePayload(objective="measure resource conservation"),
                rollout_id=f"resource-rollout-{index}",
                created_at=NOW + timedelta(minutes=3),
            )
    artifacts = LocalArtifactStore(tmp_path / "resource-artifacts")
    return SimpleNamespace(
        database=database,
        amber=amber,
        process=process,
        program=pp,
        execution=manifest,
        authorization=authorization,
        clock=clock,
        split=ProjectSplit.TRAIN,
        artifacts=artifacts,
        catalog=ArtifactCatalog(artifacts),
    )


async def resource_action(
    ctx, *, tokens=40, kind=ProcessEventKind.STATE_CHECKPOINTED, worker="worker"
):
    async with ctx.database.transaction() as session:
        claim = await ctx.process.claim_next(
            session, worker_id=worker, lease_for=timedelta(minutes=5), now=ctx.clock()
        )
        assert claim is not None
    previous = claim.state.payload.budget_usage
    usage = ProjectBudgetUsage(
        actions=previous.actions + 1,
        input_tokens=previous.input_tokens + tokens,
        output_tokens=previous.output_tokens,
        artifact_bytes=previous.artifact_bytes,
        wall_time_seconds=float(previous.wall_time_seconds) + 1,
        cost=previous.cost,
    )
    action = AmberActionRequest(
        authorization_digest=ctx.authorization.digest,
        rollout_id=claim.rollout.rollout_id,
        rollout_sequence=claim.rollout.sequence,
        state_digest=claim.state.state_digest,
        lease_token_digest=sha256_digest(claim.lease_token),
        program_digest=ctx.execution.program_digest,
        distribution_digest=ctx.execution.distribution_digest,
        split=ctx.split,
        persistence_mode=ctx.program.persistence_mode,
        event_kind=kind,
        role_id="researcher",
        worker_model_digest=ctx.authorization.allowed_worker_model_digests[0],
        target_class="scientific_math",
        environment_fingerprint=ctx.execution.environment_fingerprint,
        projected_usage=usage,
        projected_artifact_bytes=usage.artifact_bytes,
        requested_at=ctx.clock(),
    )
    return claim, action


async def commit_resource_action(ctx, claim, action, decision):
    async with ctx.database.transaction() as session:
        return await ctx.process.append_event(
            session,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            amber_decision_id=decision.decision_id,
            kind=action.event_kind,
            actor_id="worker",
            payload={"summary": "synthetic completed action"},
            resulting_state=claim.state.payload.model_copy(
                update={"budget_usage": action.projected_usage}
            ),
            occurred_at=ctx.clock(),
        )
