from __future__ import annotations

import json
from datetime import timedelta

import pytest

from padawan.artifacts.store import LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
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
from padawan.pprl.store import ProcessStore
from padawan.training import TrainingCompiler
from padawan.training.contracts import TrainingExclusionReason, TrainingProductKind
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


@pytest.mark.asyncio
async def test_compiler_emits_macro_trajectories_only_after_distribution_replication(
    database, tmp_path
) -> None:
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    process_store = ProcessStore(amber)
    model = worker_model()
    manifest = distribution()
    process_program = None
    instances = []
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, manifest)
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        for seed in (61, 62):
            instances.append(
                await registry.sample(
                    session,
                    distribution_digest=distribution_digest,
                    split=ProjectSplit.TRAIN,
                    seed=seed,
                    generator=DeterministicProjectGenerator(),
                )
            )
        authorization = envelope(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            model=model,
        )
        authorization_digest = await amber.prepare(
            session, envelope=authorization, actor_id="preparer"
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.AUTHORIZED,
            actor_id="reviewer-a",
            reason="PPRL training fixture approved",
            evidence_refs=("review:pprl-training",),
            occurred_at=NOW + timedelta(minutes=1),
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.ACTIVE,
            actor_id="operator",
            reason="PPRL training fixture activated",
            occurred_at=NOW + timedelta(minutes=2),
        )
        for instance_index, instance in enumerate(instances):
            for replication_index in range(2):
                rollout_id = f"training-rollout-{instance_index}-{replication_index}"
                process_execution = execution(
                    program_digest=program_digest,
                    distribution_digest=distribution_digest,
                    instance=instance,
                    authorization_digest=authorization_digest,
                    model=model,
                    execution_id=f"execution-{rollout_id}",
                    seed=100 + instance_index * 10 + replication_index,
                )
                execution_digest = await process_store.register_execution(
                    session, process_execution
                )
                await process_store.create_rollout(
                    session,
                    execution_digest=execution_digest,
                    replication_index=replication_index,
                    initial_state=ProjectStatePayload(
                        objective=f"solve instance {instance.instance_id}"
                    ),
                    rollout_id=rollout_id,
                    created_at=NOW + timedelta(minutes=3 + instance_index),
                )

    completed_rollouts: list[str] = []
    for action_index in range(4):
        async with database.transaction() as session:
            claimed = await process_store.claim_next(
                session,
                worker_id=f"training-worker-{action_index}",
                lease_for=timedelta(minutes=5),
                now=NOW + timedelta(minutes=10 + action_index),
            )
            assert claimed is not None
            completed_rollouts.append(claimed.rollout.rollout_id)
            decision = await amber.admit(
                session,
                request=AmberActionRequest(
                    authorization_digest=authorization_digest,
                    rollout_id=claimed.rollout.rollout_id,
                    rollout_sequence=claimed.rollout.sequence,
                    state_digest=claimed.state.state_digest,
                    lease_token_digest=sha256_digest(claimed.lease_token),
                    program_digest=program_digest,
                    distribution_digest=distribution_digest,
                    split=ProjectSplit.TRAIN,
                    persistence_mode=process_program.persistence_mode,
                    event_kind=ProcessEventKind.PROJECT_COMPLETED,
                    role_id="researcher",
                    worker_model_digest=sha256_digest(model),
                    target_class="scientific_math",
                    environment_fingerprint=instances[0].environment_fingerprint,
                    projected_usage=ProjectBudgetUsage(actions=1),
                    requested_at=NOW + timedelta(minutes=10 + action_index),
                ),
                active_workers=0,
                decision_id=f"training-amber-{action_index}",
            )
            await process_store.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.PROJECT_COMPLETED,
                actor_id=f"training-worker-{action_index}",
                payload={"result": "verified"},
                resulting_state=claimed.state.payload.model_copy(
                    update={"budget_usage": ProjectBudgetUsage(actions=1)}
                ),
                to_status=RolloutStatus.COMPLETE,
                event_id=f"training-event-{action_index}",
                occurred_at=NOW + timedelta(minutes=11 + action_index),
            )
            assessment = ProcessOutcomeAssessment(
                assessment_id=f"training-outcome-{action_index}",
                rollout_id=claimed.rollout.rollout_id,
                authority=process_program.reward_authority,
                components=(
                    ProcessOutcomeComponent(
                        component_id="correctness",
                        disposition=OutcomeDisposition.SUCCESS,
                        deterministic=True,
                        value=1.0,
                        evidence_refs=(f"training-event-{action_index}",),
                    ),
                ),
                scalar_return=1.0 + action_index / 10,
                eligible_for_learning=True,
                created_at=NOW + timedelta(minutes=12 + action_index),
            )
            await process_store.record_outcome(session, assessment)
            rights_digest = sha256_digest(manifest.rights)
            eligibility = ProcessTrainingEligibilityDecision(
                decision_id=f"training-eligibility-{action_index}",
                rollout_id=claimed.rollout.rollout_id,
                outcome_assessment_ids=(assessment.assessment_id,),
                policy_id="test.pprl.eligibility",
                policy_version="1.0.0",
                eligible=True,
                allowed_lanes=tuple(sorted(ProcessLearningLane, key=lambda lane: lane.value)),
                rights_digests=(rights_digest,),
                evidence_refs=(assessment.assessment_id,),
                reason="deterministic outcome and rights admit internal process learning",
                decided_by="training-reviewer",
                created_at=NOW + timedelta(minutes=13 + action_index),
            )
            await process_store.record_training_eligibility(session, eligibility)

    artifact_store = LocalArtifactStore(tmp_path / "pprl-training-artifacts")
    compiler = TrainingCompiler(artifact_store)
    async with database.transaction() as session:
        build = await compiler.compile(session)
    async with database.transaction() as session:
        verification = await compiler.verify(session, bundle_id=build.manifest.bundle_id)

    assert verification.valid, verification.errors
    assert build.manifest.source_process_rollout_ids == tuple(sorted(completed_rollouts))
    assert build.manifest.included_counts[TrainingProductKind.PPRL_TRAJECTORY] == 4
    assert build.manifest.included_counts[TrainingProductKind.PPRL_VERIFIABLE] == 4
    trajectory_product = next(
        product
        for product in build.manifest.products
        if product.kind == TrainingProductKind.PPRL_TRAJECTORY
    )
    rows = [
        json.loads(line)
        for line in artifact_store.read_text(
            trajectory_product.artifact, allow_restricted=True
        ).splitlines()
    ]
    assert len(rows) == 4
    assert all(len(row["events"]) == 1 for row in rows)
    assert all(row["outcomes"][0]["authority"]["kind"] == "verifiable" for row in rows)

    evidence_product = next(
        product
        for product in build.manifest.products
        if product.kind == TrainingProductKind.EVIDENCE_LEDGER
    )
    evidence_rows = [
        json.loads(line)
        for line in artifact_store.read_text(
            evidence_product.artifact, allow_restricted=True
        ).splitlines()
    ]
    evidence_kinds = {row["source_kind"] for row in evidence_rows}
    assert {
        "amber_admission_decision",
        "amber_authorization",
        "amber_authorization_event",
    }.issubset(evidence_kinds)

    async with database.transaction() as session:
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.QUARANTINED,
            actor_id="safety-operator",
            reason="exercise post-collection quarantine gating",
            evidence_refs=("incident:test-quarantine",),
            occurred_at=NOW + timedelta(minutes=30),
        )
    async with database.transaction() as session:
        with pytest.raises(
            PermissionError,
            match="lifecycle does not permit process training",
        ):
            await process_store.record_training_eligibility(
                session,
                eligibility.model_copy(
                    update={
                        "decision_id": "training-eligibility-after-quarantine",
                        "created_at": NOW + timedelta(minutes=31),
                    }
                ),
            )
    async with database.transaction() as session:
        quarantined_build = await compiler.compile(session)
    async with database.transaction() as session:
        quarantined_verification = await compiler.verify(
            session, bundle_id=quarantined_build.manifest.bundle_id
        )
        historical_verification = await compiler.verify(session, bundle_id=build.manifest.bundle_id)

    assert quarantined_verification.valid, quarantined_verification.errors
    assert historical_verification.valid, historical_verification.errors
    assert quarantined_build.manifest.included_counts[TrainingProductKind.PPRL_TRAJECTORY] == 0
    assert quarantined_build.manifest.included_counts[TrainingProductKind.PPRL_VERIFIABLE] == 0
    assert (
        quarantined_build.manifest.exclusion_counts[
            TrainingExclusionReason.PROCESS_TRAINING_NOT_AUTHORIZED
        ]
        == 8
    )
