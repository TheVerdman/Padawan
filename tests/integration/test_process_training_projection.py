from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select, update

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberReleaseClass, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import SamplingConfiguration, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactReferenceRow,
    ProcessContentAdmissionRow,
    ProcessObservationDecisionRow,
    ProcessObservationRow,
    ProcessRolloutRow,
    ProcessTrainingProjectionRow,
    ProcessWorkerInvocationRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import (
    OutcomeDisposition,
    ProcessEventKind,
    ProcessLearningLane,
    ProcessOutcomeAssessment,
    ProcessOutcomeComponent,
    ProcessTrainingEligibilityDecision,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError, ProcessEvidenceStore
from padawan.pprl.evidence_contracts import EvidenceAdmissionPolicy, ProcessEvidenceUse
from padawan.pprl.generation import ProcessGenerationExecutor
from padawan.pprl.observations import ProcessObservationStore
from padawan.pprl.store import ProcessForkChildPlan, ProcessStore
from padawan.training import TrainingCompiler
from padawan.training.contracts import TrainingProductKind
from padawan.training.process_projection import (
    ProcessTrainingProjectionDeniedError,
    ProcessTrainingProjectionStore,
    RegisteredProcessTrainingTask,
)
from padawan.training.process_projection_contracts import ProcessTrainingProjectionReceipt
from tests.helpers import CallbackGenerationClient
from tests.pprl_evidence_helpers import EvidenceContext, _review
from tests.pprl_helpers import (
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
)


class LearningTask(StrictRecord):
    seed: int
    split: ProjectSplit
    target: int


async def _campaign(
    database,
    tmp_path,
    clock,
    *,
    observations=True,
    forks=False,
    evidence_use=None,
    instance_count=2,
    replicates=2,
    split=ProjectSplit.TRAIN,
    release_permitted=False,
    invocations=False,
    bad_preference=False,
    task_marker=False,
):
    """Two instances with repeated synthetic transitions; no actual model or trainer."""
    registry, amber = ProcessDistributionRegistry(), AmberStore()
    artifacts = LocalArtifactStore(tmp_path / "projection-artifacts")
    catalog = ArtifactCatalog(artifacts)
    evidence = ProcessEvidenceStore(
        catalog=catalog,
        amber=amber,
        policy=EvidenceAdmissionPolicy(
            policy_id="test.training-evidence",
            version="1.0.0",
            reviewer_ids=("reviewer-a",),
            maximum_bytes=100_000,
            maximum_forensic_sources=4,
            maximum_forensic_source_bytes=1_000_000,
        ),
    )
    process = ProcessStore(amber, evidence=evidence)
    observer = ProcessObservationStore(process)
    manifest = distribution()
    secret = (
        artifacts.put_text("RAW_FORENSIC_MARKER", restricted=True, raw_data=True)
        if (bad_preference or task_marker)
        else None
    )

    class MarkedGenerator(DeterministicProjectGenerator):
        def generate(self, **kwargs):
            generated = super().generate(**kwargs)
            return replace(generated, task={**generated.task, "evidence": secret.artifact_id})

    generator = MarkedGenerator() if task_marker else DeterministicProjectGenerator()
    executions, instances, reviews = {}, {}, []
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, manifest)
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        authorization = envelope(
            program_digest=program_digest, distribution_digest=distribution_digest
        )
        if release_permitted:
            authorization = authorization.model_copy(
                update={
                    "required_reviewers": ("reviewer-a", "reviewer-b"),
                    "checkpoint_policy": authorization.checkpoint_policy.model_copy(
                        update={
                            "checkpoint_export_permitted": True,
                            "release_class": AmberReleaseClass.RELEASE_APPROVED,
                        }
                    ),
                }
            )
        await amber.prepare(session, envelope=authorization, actor_id="preparer")
        for status in (AmberStatus.AUTHORIZED, AmberStatus.ACTIVE):
            await amber.transition(
                session,
                authorization_digest=authorization.digest,
                to_status=status,
                actor_id="reviewer-a",
                reason="synthetic offline projection fixture",
                evidence_refs=("review:projection",),
                occurred_at=clock(),
            )
        for index in range(instance_count):
            instance = await registry.sample(
                session,
                distribution_digest=distribution_digest,
                split=split,
                seed=61 + index,
                generator=generator,
            )
            for replicate in range(1 if forks else replicates):
                rollout_id = f"projection-rollout-{index}-{replicate}"
                instances[rollout_id] = instance
                executions[rollout_id] = execution(
                    program_digest=program_digest,
                    distribution_digest=distribution_digest,
                    instance=instance,
                    authorization_digest=authorization.digest,
                    execution_id=f"execution-{rollout_id}",
                    seed=100 + 10 * index + replicate,
                )
                await process.register_execution(session, executions[rollout_id])
    for index, (rollout_id, process_execution) in enumerate(executions.items()):
        references = ()
        if evidence_use is not None:
            ctx = EvidenceContext(
                database,
                artifacts,
                catalog,
                ArtifactInformationStore(catalog),
                evidence,
                amber,
                process,
                process_execution,
                process_program,
                split,
                clock,
            )
            review = await _review(ctx, training=evidence_use == "training")
            async with database.transaction() as session:
                await evidence.admit(session, review=review, now=clock())
            reviews.append(review)
            references = (review.process_reference,)
        async with database.transaction() as session:
            await process.create_rollout(
                session,
                execution_digest=sha256_digest(process_execution),
                replication_index=0 if forks else index % replicates,
                initial_state=ProjectStatePayload(
                    objective="Compute the checked result; café",
                    artifact_refs=references,
                    memory_refs=tuple(ref.process_artifact_id for ref in references),
                    budget_usage=ProjectBudgetUsage(
                        artifact_bytes=sum(ref.size_bytes for ref in references)
                    ),
                ),
                rollout_id=rollout_id,
                created_at=clock(),
            )
    ctx = SimpleNamespace(
        database=database,
        artifacts=artifacts,
        catalog=catalog,
        amber=amber,
        process=process,
        observer=observer,
        manifest=manifest,
        authorization=authorization,
        program=process_program,
        clock=clock,
        executions=executions,
        instances=instances,
        reviews=reviews,
        events=[],
        delivered=[],
        bindings=[],
        outcomes=[],
        forks=[],
        invocations=invocations,
        bad_preference=bad_preference,
    )
    ctx.secret = secret
    if ctx.secret is not None:
        async with database.transaction() as session:
            await ArtifactInformationStore(catalog).classify(
                session,
                artifact=ctx.secret,
                information_class=InformationClass.FORENSIC,
                classified_by="test.researcher",
                reason="synthetic inaccessible source",
                classified_at=clock(),
            )
    if forks:
        for _ in range(instance_count):
            async with database.transaction() as session:
                claimed = await process.claim_next(
                    session, worker_id="fork-worker", lease_for=timedelta(minutes=5), now=clock()
                )
                assert claimed is not None
                decision = await _decision(
                    ctx,
                    session,
                    claimed,
                    "fork-worker",
                    ProcessEventKind.ROLLOUT_FORKED,
                    observations,
                )
                parent_id = claimed.rollout.rollout_id
                children = []
                for index in range(2):
                    child_id = f"{parent_id}-child-{index}"
                    child_execution = executions[parent_id].model_copy(
                        update={
                            "execution_id": f"execution-{child_id}",
                            "seed": 300 + index,
                        }
                    )
                    executions[child_id] = child_execution
                    instances[child_id] = instances[parent_id]
                    children.append(
                        ProcessForkChildPlan(
                            condition_id=f"condition-{index}",
                            rollout_id=child_id,
                            replication_index=index,
                            execution=child_execution,
                        )
                    )
                ctx.forks.append(
                    await process.fork_rollout(
                        session,
                        parent_rollout_id=parent_id,
                        lease_token=claimed.lease_token,
                        amber_decision_id=decision.decision_id,
                        actor_id="fork-worker",
                        children=tuple(children),
                        intervention={"description": "vary sampling seed"},
                        occurred_at=clock(),
                    )
                )
                # Fork append releases its lease. Keep the unfinished fixture parent
                # out of the claim queue while its child continuations complete.
                parent_row = await session.get(ProcessRolloutRow, parent_id)
                parent_row.paused = True
    for index in range(instance_count * (2 if forks else replicates)):
        async with database.transaction() as session:
            worker = f"private-worker-{index}"
            claimed = await process.claim_next(
                session, worker_id=worker, lease_for=timedelta(minutes=5), now=clock()
            )
            assert claimed is not None
            decision = await _decision(
                ctx, session, claimed, worker, ProcessEventKind.PROJECT_COMPLETED, observations
            )
        usage = claimed.state.payload.budget_usage.model_copy(
            update={
                "actions": claimed.state.payload.budget_usage.actions + 1,
            }
        )
        invocation_id = None
        added_references = ()
        if invocations:
            client = CallbackGenerationClient(
                lambda _request: "RAW_FORENSIC_MARKER", "test-open-weight"
            )
            generation = ProcessGenerationExecutor(
                database=database,
                executor=IdempotentGenerationExecutor(
                    database=database,
                    artifacts=artifacts,
                    client=client,
                ),
            )
            result = await generation.execute(
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                invocation_id=f"private-invocation-{index}",
                role_id="researcher",
                worker_model_digest=sha256_digest(
                    executions[claimed.rollout.rollout_id].worker_models[0]
                ),
                purpose="process_worker",
                provider="test-open-weight",
                request=GenerationRequest(
                    request_id=f"private-request-{index}",
                    instructions="synthetic fixture",
                    input=ctx.delivered[-1].observation_bytes.decode(),
                    sampling=SamplingConfiguration(max_output_tokens=12),
                    store=False,
                ),
            )
            invocation_id = result.invocation_id
            usage = usage.model_copy(
                update={
                    "input_tokens": 64,
                    "output_tokens": 64,
                    "wall_time_seconds": 30.0,
                    "artifact_bytes": 1_000_000,
                }
            )
            assert len(client.calls) == 1
            if evidence_use == "training":
                information = ArtifactInformationStore(catalog)
                async with database.transaction() as session:
                    invocation = await session.get(ProcessWorkerInvocationRow, invocation_id)
                    sources = tuple(
                        [
                            await information.forensic_reference(session, artifact_id=artifact_id)
                            for artifact_id in sorted(
                                (invocation.request_artifact_id, invocation.response_artifact_id)
                            )
                        ]
                    )
                evidence_ctx = EvidenceContext(
                    database,
                    artifacts,
                    catalog,
                    information,
                    evidence,
                    amber,
                    process,
                    executions[claimed.rollout.rollout_id],
                    process_program,
                    split,
                    clock,
                )
                review = await _review(
                    evidence_ctx,
                    content="Reviewed derivative from synthetic invocation.",
                    sources=sources,
                    training=True,
                )
                async with database.transaction() as session:
                    await evidence.admit(session, review=review, now=clock())
                reviews.append(review)
                added_references = (review.process_reference,)
        async with database.transaction() as session:
            event, _ = await process.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.PROJECT_COMPLETED,
                actor_id=worker,
                payload={"summary": "verified"},
                artifact_refs=added_references,
                resulting_state=claimed.state.payload.model_copy(
                    update={
                        "budget_usage": usage,
                        "artifact_refs": tuple(
                            sorted(
                                (*claimed.state.payload.artifact_refs, *added_references),
                                key=lambda reference: reference.process_artifact_id,
                            )
                        ),
                    }
                ),
                to_status=RolloutStatus.COMPLETE,
                event_id=f"private-event-{index}",
                worker_invocation_id=invocation_id,
                occurred_at=clock(),
            )
            ctx.events.append(event)
            await _outcome_and_eligibility(
                ctx,
                session,
                event,
                1.0,
                "trajectory",
                (
                    ProcessLearningLane.TRAJECTORY,
                    ProcessLearningLane.VERIFIABLE_REPLAY,
                ),
            )
            if forks:
                await _outcome_and_eligibility(
                    ctx,
                    session,
                    event,
                    float(index % 2) * 2,
                    "preference",
                    (ProcessLearningLane.FORK_PREFERENCE,),
                )
    ctx.compiler = TrainingCompiler(artifacts)
    ctx.tasks = (
        RegisteredProcessTrainingTask(
            manifest.generator.digest,
            "test.project-task",
            "1.0.0",
            LearningTask,
        ),
    )
    ctx.projection = ProcessTrainingProjectionStore(
        compiler=ctx.compiler, process=process, tasks=ctx.tasks
    )
    async with database.transaction() as session:
        ctx.build = await ctx.compiler.compile(session, as_of=clock())
    return ctx


async def _decision(ctx, session, claimed, worker, kind, observe):
    scope = dict(
        rollout_id=claimed.rollout.rollout_id, lease_token=claimed.lease_token, worker_id=worker
    )
    if observe:
        delivered = await ctx.observer.observe_claim(session, **scope, now=ctx.clock())
        ctx.delivered.append(delivered)
    manifest = ctx.executions[claimed.rollout.rollout_id]
    usage = claimed.state.payload.budget_usage.model_copy(
        update={
            "actions": claimed.state.payload.budget_usage.actions + 1,
        }
    )
    if ctx.invocations:
        usage = usage.model_copy(
            update={
                "input_tokens": 64,
                "output_tokens": 64,
                "wall_time_seconds": 30.0,
                "artifact_bytes": 1_000_000,
            }
        )
    decision = await ctx.amber.admit(
        session,
        request=AmberActionRequest(
            authorization_digest=ctx.authorization.digest,
            rollout_id=claimed.rollout.rollout_id,
            rollout_sequence=claimed.rollout.sequence,
            state_digest=claimed.state.state_digest,
            lease_token_digest=sha256_digest(claimed.lease_token),
            program_digest=manifest.program_digest,
            distribution_digest=manifest.distribution_digest,
            split=ctx.instances[claimed.rollout.rollout_id].split,
            persistence_mode=ctx.program.persistence_mode,
            event_kind=kind,
            role_id="researcher",
            worker_model_digest=sha256_digest(manifest.worker_models[0]),
            target_class="scientific_math",
            environment_fingerprint=manifest.environment_fingerprint,
            projected_usage=usage,
            projected_artifact_bytes=usage.artifact_bytes,
            requested_at=ctx.clock(),
        ),
        active_workers=0,
    )
    if observe:
        ctx.bindings.append(
            await ctx.observer.bind_decision(
                session,
                **scope,
                observation_id=delivered.observation_id,
                decision_id=decision.decision_id,
                now=ctx.clock(),
            )
        )
    return decision


async def _outcome_and_eligibility(ctx, session, event, value, suffix, lanes):
    outcome = ProcessOutcomeAssessment(
        assessment_id=f"{event.event_id}-{suffix}-outcome",
        rollout_id=event.rollout_id,
        authority=ctx.program.reward_authority,
        components=(
            ProcessOutcomeComponent(
                component_id=(
                    ctx.secret.artifact_id
                    if ctx.bad_preference
                    and suffix == "preference"
                    and event.rollout_id.startswith("projection-rollout-0-")
                    else "correctness"
                ),
                disposition=OutcomeDisposition.SUCCESS,
                deterministic=True,
                value=value,
                evidence_refs=(event.event_id,),
            ),
        ),
        scalar_return=value,
        eligible_for_learning=True,
        created_at=ctx.clock(),
    )
    ctx.outcomes.append(outcome)
    await ctx.process.record_outcome(session, outcome)
    await ctx.process.record_training_eligibility(
        session,
        ProcessTrainingEligibilityDecision(
            decision_id=f"{event.event_id}-{suffix}-eligibility",
            rollout_id=event.rollout_id,
            outcome_assessment_ids=(outcome.assessment_id,),
            policy_id="test.projection-eligibility",
            policy_version="1.0.0",
            eligible=True,
            allowed_lanes=tuple(sorted(lanes, key=lambda lane: lane.value)),
            rights_digests=(sha256_digest(ctx.manifest.rights),),
            evidence_refs=(outcome.assessment_id,),
            reason="synthetic reviewed learning eligibility",
            decided_by="training-reviewer",
            created_at=ctx.clock(),
        ),
    )


async def _compile(ctx, boundary=None):
    async with ctx.database.transaction() as session:
        return await (boundary or ctx.projection).compile(
            session, source_bundle_id=ctx.build.manifest.bundle_id, now=ctx.clock()
        )


async def _read(ctx, receipt, kind="pprl_trajectory", boundary=None, now=None):
    async with ctx.database.transaction() as session:
        return await (boundary or ctx.projection).read_product(
            session, projection_id=receipt.projection_id, kind=kind, now=now or ctx.clock()
        )


def _counts(receipt):
    return {product.kind: len(product.rows) for product in receipt.products}


def _uniform(failure):
    assert str(failure.value) == "process training projection denied"
    assert failure.value.__context__ is None and failure.value.__cause__ is None


async def test_public_products_reconstruct_exact_observations_and_preserve_duplicate_samples(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    receipt = await _compile(ctx)
    assert _counts(receipt) == {
        "pprl_trajectory": 4,
        "pprl_verifiable": 4,
        "pprl_fork_preference": 0,
    }
    assert receipt.parameter_training_ready is False
    public = await _read(ctx, receipt)
    lines = public.splitlines()
    assert len(lines) == 4 and len(set(lines)) == 1
    row = json.loads(lines[0])
    assert row["steps"][0]["observation"] == json.loads(ctx.delivered[0].observation_bytes)
    assert set(row) == {"schema_version", "initial_state", "steps", "outcomes"}
    assert row["outcomes"][0]["scalar_return"] == 1.0
    private = [
        ctx.authorization.digest,
        ctx.build.manifest.bundle_id,
        *(event.event_id for event in ctx.events),
        *(event.actor_id for event in ctx.events),
        *(event.lease_token_digest for event in ctx.events),
        *(source.rollout_id for source in receipt.sources),
        *(outcome.assessment_id for outcome in ctx.outcomes),
    ]
    assert all(value.encode() not in public for value in private)
    assert len({source.archive_row_digest for source in receipt.sources}) == 4
    assert await _compile(ctx) == receipt
    assert (
        ProcessTrainingProjectionReceipt.model_validate_json(receipt.model_dump_json()) == receipt
    )
    async with database.transaction() as session:
        assert (await ctx.compiler.verify(session, bundle_id=ctx.build.manifest.bundle_id)).valid
        assert (
            await session.scalar(select(func.count()).select_from(ProcessTrainingProjectionRow))
            == 1
        )
        for artifact in receipt.archive_artifacts:
            info = await ctx.projection.information.get(session, artifact_id=artifact.artifact_id)
            assert info.information_class == InformationClass.FORENSIC


async def test_fork_preference_uses_its_selected_outcomes_and_keeps_fork_identity_private(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now, forks=True)
    receipt = await _compile(ctx)
    assert _counts(receipt)["pprl_fork_preference"] == 2
    public = await _read(ctx, receipt, "pprl_fork_preference")
    for row in map(json.loads, public.splitlines()):
        assert row["chosen"]["outcomes"][0]["scalar_return"] == 2.0
        assert row["rejected"]["outcomes"][0]["scalar_return"] == 0.0
    assert all(fork.fork_id.encode() not in public for fork in ctx.forks)


@pytest.mark.parametrize("missing", ["all_observations", "one_binding", "one_content_receipt"])
async def test_legacy_or_incomplete_projection_is_excluded_then_replication_is_rechecked(
    database,
    tmp_path,
    pprl_now,
    missing,
):
    ctx = await _campaign(database, tmp_path, pprl_now, observations=missing != "all_observations")
    async with database.transaction() as session:
        if missing == "one_binding":
            await session.execute(
                delete(ProcessObservationDecisionRow).where(
                    ProcessObservationDecisionRow.decision_id == ctx.events[0].amber_decision_id
                )
            )
        elif missing == "one_content_receipt":
            await session.execute(
                delete(ProcessContentAdmissionRow).where(
                    ProcessContentAdmissionRow.record_id == ctx.events[0].resulting_state_id
                )
            )
        assert (await ctx.compiler.verify(session, bundle_id=ctx.build.manifest.bundle_id)).valid
    receipt = await _compile(ctx)
    assert not receipt.sources and not any(_counts(receipt).values())
    assert "projection_not_admitted" in {item.reason for item in receipt.exclusions}
    if missing != "all_observations":
        assert "replication_insufficient" in {item.reason for item in receipt.exclusions}
    assert await _read(ctx, receipt) == b""


async def test_task_schema_is_explicit_and_verifiable_minima_follow_task_exclusions(
    database,
    tmp_path,
    pprl_now,
):
    from pydantic import model_validator

    class OneInstanceOnly(LearningTask):
        @model_validator(mode="after")
        def declared_population(self):
            if self.seed != 61:
                raise ValueError("task not in this declared projection population")
            return self

    ctx = await _campaign(database, tmp_path, pprl_now)
    unregistered = ProcessTrainingProjectionStore(compiler=ctx.compiler, process=ctx.process)
    empty = await _compile(ctx, unregistered)
    assert _counts(empty)["pprl_trajectory"] == 4
    assert _counts(empty)["pprl_verifiable"] == 0
    subset = ProcessTrainingProjectionStore(
        compiler=ctx.compiler,
        process=ctx.process,
        tasks=(
            RegisteredProcessTrainingTask(
                ctx.manifest.generator.digest, "subset", "1.0.0", OneInstanceOnly
            ),
        ),
    )
    receipt = await _compile(ctx, subset)
    assert _counts(receipt)["pprl_trajectory"] == 4
    assert _counts(receipt)["pprl_verifiable"] == 0
    reasons = {item.reason for item in receipt.exclusions if item.kind == "pprl_verifiable"}
    assert reasons == {"task_schema_not_admitted", "replication_insufficient"}


@pytest.mark.parametrize("split", [ProjectSplit.ADAPTIVE_DEVELOPMENT, ProjectSplit.VALIDATION])
async def test_nontraining_partitions_never_gain_projection_authority(
    database, tmp_path, pprl_now, split
):
    ctx = await _campaign(database, tmp_path, pprl_now, split=split)
    receipt = await _compile(ctx)
    assert not any(_counts(receipt).values())


@pytest.mark.parametrize("evidence_use", ["process", "training"])
async def test_process_evidence_requires_separate_training_use_and_independent_ownership(
    database,
    tmp_path,
    pprl_now,
    evidence_use,
):
    ctx = await _campaign(database, tmp_path, pprl_now, evidence_use=evidence_use)
    receipt = await _compile(ctx)
    if evidence_use == "process":
        assert not receipt.sources
        assert await _read(ctx, receipt) == b""
        return
    assert _counts(receipt)["pprl_trajectory"] == 4
    public = await _read(ctx, receipt)
    assert all(
        review.process_reference.process_artifact_id.encode() in public for review in ctx.reviews
    )
    assert all(review.candidate_artifact_id.encode() not in public for review in ctx.reviews)
    async with database.transaction() as session:
        for source in receipt.sources:
            review = next(
                value
                for value in ctx.reviews
                if value.process_reference.execution_digest == source.execution_digest
            )
            admission = await ctx.process.evidence.inspect_admission(
                session, process_artifact_id=review.process_reference.process_artifact_id
            )
            assert source.evidence_admission_digests == (admission.digest,)
            assert sha256_digest(review.rights) in source.rights_digests
        pins = list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_training_projection"
                )
            )
        )
        assert len(pins) == 4
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.reference_id == pins[0].reference_id
            )
        )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt)
    _uniform(failure)


@pytest.mark.parametrize("status", [AmberStatus.PAUSED, AmberStatus.RELEASE_APPROVED])
async def test_training_read_can_follow_pause_with_a_new_receipt_without_enabling_process_use(
    database,
    tmp_path,
    pprl_now,
    status,
):
    ctx = await _campaign(
        database,
        tmp_path,
        pprl_now,
        evidence_use="training",
        release_permitted=status == AmberStatus.RELEASE_APPROVED,
    )
    original = await _compile(ctx)
    async with database.transaction() as session:
        for target in (
            (AmberStatus.PAUSED,)
            if status == AmberStatus.PAUSED
            else (AmberStatus.PAUSED, AmberStatus.RELEASE_APPROVED)
        ):
            await ctx.amber.transition(
                session,
                authorization_digest=ctx.authorization.digest,
                to_status=target,
                actor_id="reviewer-b" if target == AmberStatus.RELEASE_APPROVED else "reviewer-a",
                reason="offline training lifecycle test",
                evidence_refs=("review:training",),
                occurred_at=ctx.clock(),
            )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, original)
    _uniform(failure)
    current = await _compile(ctx)
    assert current.projection_id != original.projection_id
    assert _counts(current)["pprl_trajectory"] == 4
    assert await _read(ctx, current)
    async with database.transaction() as session:
        review = ctx.reviews[0]
        scope = dict(
            reference=review.process_reference,
            execution_digest=review.process_reference.execution_digest,
            now=ctx.clock(),
        )
        assert await ctx.process.evidence.read(
            session, **scope, use=ProcessEvidenceUse.TRAINING_PROJECTION
        )
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.process.evidence.read(session, **scope, use=ProcessEvidenceUse.PROCESS)
        with pytest.raises(PermissionError):
            await ctx.process.evidence.admit(session, review=review, now=ctx.clock())


@pytest.mark.parametrize(
    "change", ["expiry", "quarantine", "archive_pin", "classification", "binding", "receipt"]
)
async def test_previously_projected_bytes_require_current_authority_integrity_and_retention(
    database,
    tmp_path,
    pprl_now,
    change,
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    receipt = await _compile(ctx)
    now = ctx.clock()
    async with database.transaction() as session:
        if change == "expiry":
            now = ctx.authorization.expires_at
        elif change == "quarantine":
            await ctx.amber.transition(
                session,
                authorization_digest=ctx.authorization.digest,
                to_status=AmberStatus.QUARANTINED,
                actor_id="reviewer-a",
                reason="offline quarantine test",
                evidence_refs=("incident:test",),
                occurred_at=ctx.clock(),
            )
            now = ctx.clock()
        elif change == "archive_pin":
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_training_archive",
                    ArtifactReferenceRow.artifact_id == receipt.archive_artifacts[0].artifact_id,
                )
            )
        elif change == "classification":
            await session.execute(
                delete(ArtifactInformationRow).where(
                    ArtifactInformationRow.artifact_id == receipt.archive_artifacts[0].artifact_id
                )
            )
        elif change == "binding":
            await session.execute(
                delete(ProcessObservationDecisionRow).where(
                    ProcessObservationDecisionRow.decision_id == ctx.events[0].amber_decision_id
                )
            )
        elif change == "receipt":
            await session.execute(
                update(ProcessTrainingProjectionRow)
                .where(ProcessTrainingProjectionRow.projection_id == receipt.projection_id)
                .values(policy_digest=sha256_digest("corrupted"))
            )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt, now=now)
    _uniform(failure)


@pytest.mark.parametrize(
    "limit", ["maximum_examples", "maximum_product_bytes", "maximum_source_artifact_bytes"]
)
async def test_bounded_products_fail_before_any_projection_is_published(
    database, tmp_path, pprl_now, limit
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    boundary = ProcessTrainingProjectionStore(
        compiler=ctx.compiler,
        process=ctx.process,
        tasks=ctx.tasks,
        policy=ctx.projection.policy.model_copy(update={limit: 1}),
    )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _compile(ctx, boundary)
    _uniform(failure)
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessTrainingProjectionRow))
            == 0
        )
        assert await session.scalar(select(func.count()).select_from(ArtifactInformationRow)) == 0


@pytest.mark.parametrize("failure_point", ["second_pin", "after_receipt", "cancelled"])
async def test_caught_projection_failure_rolls_back_classification_pins_and_receipt(
    database,
    tmp_path,
    pprl_now,
    monkeypatch,
    failure_point,
):
    ctx = await _campaign(database, tmp_path, pprl_now)

    async def counts(session):
        return tuple(
            [
                await session.scalar(select(func.count()).select_from(table))
                for table in (
                    ArtifactInformationRow,
                    ArtifactReferenceRow,
                    ProcessTrainingProjectionRow,
                )
            ]
        )

    original = ctx.catalog.reference
    calls = 0

    async def reference(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = await original(*args, **kwargs)
        if calls == 2:
            if failure_point == "cancelled":
                raise asyncio.CancelledError("private forensic failure content")
            raise RuntimeError("private forensic failure content")
        return result

    async def after_receipt(session, *_args, **_kwargs):
        assert (
            await session.scalar(select(func.count()).select_from(ProcessTrainingProjectionRow))
            == 1
        )
        raise RuntimeError("private forensic failure content")

    async with database.transaction() as session:
        before = await counts(session)
        with monkeypatch.context() as patch:
            if failure_point == "after_receipt":
                patch.setattr(ctx.projection, "_validate_retention", after_receipt)
            else:
                patch.setattr(ctx.projection.catalog, "reference", reference)
            exception = (
                asyncio.CancelledError
                if failure_point == "cancelled"
                else ProcessTrainingProjectionDeniedError
            )
            with pytest.raises(exception) as failure:
                await ctx.projection.compile(
                    session, source_bundle_id=ctx.build.manifest.bundle_id, now=ctx.clock()
                )
            assert failure.value.__context__ is None and failure.value.__cause__ is None
            assert "private forensic" not in str(failure.value)
        assert await counts(session) == before
    assert _counts(await _compile(ctx))["pprl_trajectory"] == 4


async def test_selected_preference_labels_cannot_leak_forensic_references_and_pairs_recheck_minima(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now, forks=True, bad_preference=True)
    assert ctx.build.manifest.included_counts[TrainingProductKind.PPRL_FORK_PREFERENCE] == 2
    receipt = await _compile(ctx)
    assert _counts(receipt)["pprl_trajectory"] == 4
    assert _counts(receipt)["pprl_fork_preference"] == 0
    assert {item.reason for item in receipt.exclusions if item.kind == "pprl_fork_preference"} == {
        "fork_pair_not_admitted",
        "replication_insufficient",
    }


async def test_invocation_traffic_is_privately_retained_and_never_becomes_learning_content(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now, invocations=True)
    receipt = await _compile(ctx)
    assert _counts(receipt)["pprl_trajectory"] == 4
    public = await _read(ctx, receipt)
    assert b"RAW_FORENSIC_MARKER" not in public
    assert all(len(source.forensic_artifacts) == 2 for source in receipt.sources)
    for source in receipt.sources:
        for reference in source.forensic_artifacts:
            assert reference.artifact.uri.encode() not in public
            assert reference.artifact.artifact_id.encode() not in public
            assert reference.classification_digest.encode() not in public
    async with database.transaction() as session:
        pins = list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_training_forensic"
                )
            )
        )
        expected = {
            ref.artifact.artifact_id
            for source in receipt.sources
            for ref in source.forensic_artifacts
        }
        assert {pin.artifact_id for pin in pins} == expected
        invocation = await session.get(
            ProcessWorkerInvocationRow, ctx.events[0].worker_invocation_id
        )
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_worker_invocation",
                ArtifactReferenceRow.owner_id == invocation.invocation_id,
            )
        )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt)
    _uniform(failure)


async def test_projection_reconstructs_archive_rows_even_when_archive_hash_and_schema_checks_pass(
    database,
    tmp_path,
    pprl_now,
    monkeypatch,
):
    import padawan.training.compiler as compiler_module

    ctx = await _campaign(database, tmp_path, pprl_now)
    original = compiler_module.compile_pprl_snapshot

    async def wrong_rows(*args, **kwargs):
        snapshot = await original(*args, **kwargs)
        products = dict(snapshot.products)
        rows = list(products[TrainingProductKind.PPRL_TRAJECTORY])
        initial = json.loads(canonical_json_bytes(rows[0].initial_state))
        initial["payload"]["objective"] = "Content absent from the retained source."
        rows[0] = rows[0].model_copy(update={"initial_state": initial})
        products[TrainingProductKind.PPRL_TRAJECTORY] = tuple(rows)
        return replace(snapshot, products=products)

    with monkeypatch.context() as patch:
        patch.setattr(compiler_module, "compile_pprl_snapshot", wrong_rows)
        async with database.transaction() as session:
            ctx.build = await ctx.compiler.compile(session, as_of=ctx.clock())
    async with database.transaction() as session:
        assert (await ctx.compiler.verify(session, bundle_id=ctx.build.manifest.bundle_id)).valid
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _compile(ctx)
    _uniform(failure)


async def test_closed_task_schema_does_not_admit_an_embedded_forensic_reference(
    database,
    tmp_path,
    pprl_now,
):
    class MarkedTask(LearningTask):
        evidence: str

    ctx = await _campaign(database, tmp_path, pprl_now, task_marker=True)
    boundary = ProcessTrainingProjectionStore(
        compiler=ctx.compiler,
        process=ctx.process,
        tasks=(
            RegisteredProcessTrainingTask(
                ctx.manifest.generator.digest, "marked-task", "1.0.0", MarkedTask
            ),
        ),
    )
    receipt = await _compile(ctx, boundary)
    assert _counts(receipt)["pprl_trajectory"] == 4
    assert _counts(receipt)["pprl_verifiable"] == 0
    assert ctx.secret.artifact_id.encode() not in await _read(ctx, receipt, boundary=boundary)


async def test_projection_owns_reviewed_derivatives_and_their_complete_forensic_dependencies(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now, invocations=True, evidence_use="training")
    receipt = await _compile(ctx)
    assert _counts(receipt)["pprl_trajectory"] == 4
    public = await _read(ctx, receipt)
    assert b"RAW_FORENSIC_MARKER" not in public
    async with database.transaction() as session:
        pins = list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_training_projection"
                )
            )
        )
        expected = {review.candidate_artifact_id for review in ctx.reviews}
        expected.update(
            source.artifact.artifact_id
            for review in ctx.reviews
            for source in review.forensic_sources
        )
        assert {pin.artifact_id for pin in pins} == expected
        assert len({pin.owner_id for pin in pins}) == 4
        # Admission and original state/event/invocation owners remain independently required.
        source_review = next(review for review in ctx.reviews if review.forensic_sources)
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_training_projection",
                ArtifactReferenceRow.artifact_id
                == source_review.forensic_sources[0].artifact.artifact_id,
            )
        )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt)
    _uniform(failure)


async def test_resigned_public_jsonl_cannot_replace_source_derived_bytes(
    database, tmp_path, pprl_now
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    receipt = await _compile(ctx)
    record = receipt.model_dump(mode="json")
    product = next(
        product for product in record["products"] if product["kind"] == "pprl_trajectory"
    )
    lines = product["public_jsonl"].splitlines()
    payload = json.loads(lines[0])
    payload["initial_state"]["objective"] = "Unadmitted substituted content"
    lines[0] = canonical_json_bytes(payload).decode()
    product["rows"][0]["public_row_digest"] = sha256_digest(lines[0].encode())
    product["public_jsonl"] = "\n".join(lines) + "\n"
    product["content_digest"] = sha256_digest(product["public_jsonl"].encode())
    forged = ProcessTrainingProjectionReceipt.model_validate(record, strict=False)
    async with database.transaction() as session:
        await session.execute(
            update(ProcessTrainingProjectionRow)
            .where(ProcessTrainingProjectionRow.projection_id == receipt.projection_id)
            .values(record_json=record, record_digest=forged.digest)
        )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt)
    _uniform(failure)


async def test_projection_receipt_is_immutable_and_outer_rollback_does_not_publish_it(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    with pytest.raises(RuntimeError, match="outer caller failed"):
        async with database.transaction() as session:
            await ctx.projection.compile(
                session, source_bundle_id=ctx.build.manifest.bundle_id, now=ctx.clock()
            )
            raise RuntimeError("outer caller failed")
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessTrainingProjectionRow))
            == 0
        )
    receipt = await _compile(ctx)
    with pytest.raises(ValueError, match="immutable"):
        async with database.transaction() as session:
            row = await session.get(ProcessTrainingProjectionRow, receipt.projection_id)
            row.policy_digest = sha256_digest("changed")
            await session.flush()
    assert await _read(ctx, receipt)


async def test_changed_projection_policy_does_not_reinterpret_an_existing_receipt(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    receipt = await _compile(ctx)
    policy = ctx.projection.policy
    policy.task_schemas[0].json_schema["description"] = "external mutation"
    assert ctx.projection.policy != policy
    changed = ProcessTrainingProjectionStore(
        compiler=ctx.compiler,
        process=ctx.process,
        tasks=ctx.tasks,
        policy=ctx.projection.policy.model_copy(update={"version": "2.0.0"}),
    )
    with pytest.raises(ProcessTrainingProjectionDeniedError) as failure:
        await _read(ctx, receipt, boundary=changed)
    _uniform(failure)
    new_receipt = await _compile(ctx, changed)
    assert new_receipt.projection_id != receipt.projection_id
    assert await _read(ctx, new_receipt, boundary=changed) == await _read(ctx, receipt)


async def test_unknown_observation_policy_is_excluded_without_historical_backfill(
    database,
    tmp_path,
    pprl_now,
):
    ctx = await _campaign(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await session.execute(
            update(ProcessObservationRow)
            .where(ProcessObservationRow.observation_id == ctx.delivered[0].observation_id)
            .values(policy_digest=sha256_digest("unknown observation policy"))
        )
    receipt = await _compile(ctx)
    assert not any(_counts(receipt).values())
