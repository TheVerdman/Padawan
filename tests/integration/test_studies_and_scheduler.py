from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.models.contracts import CorpusPool, ExposureRecord, ExposureType, ItemStatus
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    EvaluationOutcome,
    EvaluationSchedule,
    EvaluationTrialStatus,
    EvaluationTrialType,
    StudyExperimentBinding,
    StudyManifest,
    StudyStatus,
)
from padawan.models.tables import CorpusItemRow, EpisodeRow, EvaluationTrialRow
from padawan.reporting import ReportingService
from padawan.rewards import RewardEngine
from padawan.state.store import StateStore
from padawan.studies import EvaluationScheduler, StudyEngine

_NOW = datetime(2026, 8, 1, 20, 0, tzinfo=UTC)
_ENVIRONMENT = sha256_digest("study-scheduler-environment")
_SUITE = sha256_digest("study-scheduler-suite")


async def test_study_aggregation_preserves_missingness_and_attrition(database) -> None:
    states = StateStore()
    experiments = ExperimentEngine(states)
    studies = StudyEngine()
    async with database.transaction() as session:
        candidate_state = await states.create_student(
            session,
            student_id="study-candidate",
            checkpoint_id="checkpoint-candidate",
            runtime_id="inkling-runtime",
        )
        baseline_state = await states.create_student(
            session,
            student_id="study-baseline",
            checkpoint_id="checkpoint-baseline",
            runtime_id="baseline-runtime",
        )
        candidate_id, candidate_blocks = await experiments.create(
            session,
            parent_state_id=candidate_state.state_id,
            seed=11,
            blocks=(
                MatchedBlock("candidate-g0", "candidate-a0", "candidate-b0"),
                MatchedBlock("candidate-g1", "candidate-a1", "candidate-b1"),
                MatchedBlock("candidate-g2", "candidate-a2", "candidate-b2"),
            ),
            treatment_condition="lesson",
            control_condition="none",
            experiment_id="study-experiment-candidate",
        )
        baseline_id, baseline_blocks = await experiments.create(
            session,
            parent_state_id=baseline_state.state_id,
            seed=12,
            blocks=(
                MatchedBlock("baseline-g0", "baseline-a0", "baseline-b0"),
                MatchedBlock("baseline-g1", "baseline-a1", "baseline-b1"),
            ),
            treatment_condition="lesson",
            control_condition="none",
            experiment_id="study-experiment-baseline",
        )
        await experiments.record_block(
            session,
            block_id=candidate_blocks[0].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"fresh": True, "matched": True},
        )
        await experiments.record_block(
            session,
            block_id=candidate_blocks[1].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"fresh": False, "matched": True},
        )
        await experiments.record_block(
            session,
            block_id=baseline_blocks[0].block_id,
            treatment_success=False,
            control_success=False,
            treatment_score=0.0,
            control_score=0.0,
            contamination_checks={"fresh": True, "matched": True},
        )
        await experiments.record_block(
            session,
            block_id=baseline_blocks[1].block_id,
            treatment_success=None,
            control_success=None,
            treatment_score=None,
            control_score=None,
            contamination_checks={"fresh": True, "matched": True},
            infrastructure_failures=("provider_timeout",),
        )
        manifest = StudyManifest(
            study_id="multi-block-study",
            version=1,
            title="Candidate and baseline paired blocks",
            description="Aggregate immutable experiment blocks without imputing attrition.",
            seed=20260801,
            suite_manifest_digest=_SUITE,
            aggregation_policy_id="paired-blocks",
            aggregation_policy_version="1",
            experiments=(
                StudyExperimentBinding(
                    experiment_id=candidate_id,
                    condition_id="candidate",
                    checkpoint_id=candidate_state.checkpoint_id,
                    research_role=candidate_state.research_role,
                    suite_manifest_digest=_SUITE,
                    environment_fingerprint=_ENVIRONMENT,
                    assignment_propensity=0.5,
                ),
                StudyExperimentBinding(
                    experiment_id=baseline_id,
                    condition_id="baseline",
                    checkpoint_id=baseline_state.checkpoint_id,
                    research_role=baseline_state.research_role,
                    suite_manifest_digest=_SUITE,
                    environment_fingerprint=_ENVIRONMENT,
                    assignment_propensity=0.5,
                ),
            ),
            created_at=_NOW,
        )
        await studies.create(session, manifest, status=StudyStatus.ACTIVE)
        report = await studies.aggregate(session, study_id=manifest.study_id)
        await studies.transition(
            session,
            study_id=manifest.study_id,
            to_status=StudyStatus.COMPLETE,
            completed_at=_NOW + timedelta(hours=1),
        )
        with pytest.raises(ValueError, match="invalid study transition"):
            await studies.transition(
                session,
                study_id=manifest.study_id,
                to_status=StudyStatus.ACTIVE,
            )
    durable_report = await ReportingService(database, experiments).study(manifest.study_id)

    by_condition = {condition.condition_id: condition for condition in report.conditions}
    assert report.total_blocks == 5
    assert report.analyzed_blocks == 2
    assert report.missing_blocks == 1
    assert report.excluded_contaminated == 1
    assert report.excluded_infrastructure == 1
    assert not report.causal_claim_permitted
    assert by_condition["candidate"].paired_gain == 1.0
    assert by_condition["candidate"].missing_blocks == 1
    assert by_condition["baseline"].paired_gain == 0.0
    assert durable_report["format"] == "padawan.study_report"
    assert durable_report["status"] == StudyStatus.COMPLETE.value


async def test_retention_and_interference_trials_are_fresh_leased_and_recoverable(
    database,
) -> None:
    generator = AlgebraCorpusGenerator()
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    studies = StudyEngine()
    scheduler = EvaluationScheduler()
    rewards = RewardEngine()
    curriculum = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=301,
        groups_per_family=1,
        siblings_per_group=2,
        families=(AlgebraFamily.LINEAR_BOTH_SIDES, AlgebraFamily.POLYNOMIAL_FACTORING),
        created_at=_NOW,
    )
    shadow = generator.generate(
        pool=CorpusPool.ROTATING_SHADOW,
        seed=401,
        groups_per_family=2,
        siblings_per_group=2,
        families=(AlgebraFamily.LINEAR_BOTH_SIDES,),
        created_at=_NOW,
    )
    source_items = [
        item for item in curriculum if item.competency_id == "algebra.linear_both_sides"
    ]
    interfering_item = next(
        item for item in curriculum if item.competency_id == "algebra.polynomial_factoring"
    )
    retention_item = shadow[0]
    interference_item = next(
        item for item in shadow if item.instance_group_id != retention_item.instance_group_id
    )

    async with database.transaction() as session:
        for competency in generator.competencies(created_at=_NOW):
            await registry.register_competency(session, competency)
        await registry.register_items(session, [*curriculum, *shadow])
        initial = await states.create_student(
            session,
            student_id="scheduler-student",
            checkpoint_id="checkpoint-scheduler",
            runtime_id="inkling-runtime",
        )
        source_after = await states.append_state(
            session,
            parent_state_id=initial.state_id,
            compacted_working_state={"learned": ["linear_both_sides"]},
            creation_reason="source competency learned",
        )
        interference_after = await states.append_state(
            session,
            parent_state_id=source_after.state_id,
            compacted_working_state={"learned": ["linear_both_sides", "polynomial_factoring"]},
            creation_reason="different competency learned",
        )
        experiment_id, _ = await experiments.create(
            session,
            parent_state_id=initial.state_id,
            seed=19,
            blocks=(
                MatchedBlock(
                    source_items[0].instance_group_id,
                    source_items[0].item_id,
                    source_items[1].item_id,
                ),
            ),
            treatment_condition="curriculum",
            control_condition="none",
            experiment_id="scheduler-study-experiment",
        )
        session.add_all(
            [
                EpisodeRow(
                    episode_id="source-episode",
                    student_id=initial.student_id,
                    state_before_id=initial.state_id,
                    state_after_id=source_after.state_id,
                    item_id=source_items[0].item_id,
                    status="complete",
                    record_json={"kind": "source"},
                    created_at=_NOW,
                    completed_at=_NOW,
                ),
                EpisodeRow(
                    episode_id="interfering-episode",
                    student_id=initial.student_id,
                    state_before_id=source_after.state_id,
                    state_after_id=interference_after.state_id,
                    item_id=interfering_item.item_id,
                    status="complete",
                    record_json={"kind": "interference"},
                    created_at=_NOW,
                    completed_at=_NOW,
                ),
            ]
        )
        await session.flush()
        manifest = StudyManifest(
            study_id="scheduler-study",
            version=1,
            title="Delayed retention and interference",
            description="Probe a prior competency with fresh evaluation-only items.",
            seed=19,
            suite_manifest_digest=_SUITE,
            aggregation_policy_id="paired-blocks",
            aggregation_policy_version="1",
            experiments=(
                StudyExperimentBinding(
                    experiment_id=experiment_id,
                    condition_id="target",
                    checkpoint_id=initial.checkpoint_id,
                    research_role=initial.research_role,
                    suite_manifest_digest=_SUITE,
                    environment_fingerprint=_ENVIRONMENT,
                    assignment_propensity=1.0,
                ),
            ),
            created_at=_NOW,
        )
        await studies.create(session, manifest, status=StudyStatus.ACTIVE)
        await scheduler.schedule(
            session,
            EvaluationSchedule(
                trial_id="a-retention-trial",
                study_id=manifest.study_id,
                trial_type=EvaluationTrialType.RETENTION,
                source_episode_id="source-episode",
                student_id=initial.student_id,
                checkpoint_id=initial.checkpoint_id,
                state_snapshot_id=source_after.state_id,
                competency_id=source_items[0].competency_id,
                item_id=retention_item.item_id,
                environment_fingerprint=_ENVIRONMENT,
                assignment_seed=20,
                assignment_propensity=0.5,
                due_at=_NOW,
                created_at=_NOW,
            ),
        )
        await scheduler.schedule(
            session,
            EvaluationSchedule(
                trial_id="b-interference-trial",
                study_id=manifest.study_id,
                trial_type=EvaluationTrialType.INTERFERENCE,
                source_episode_id="source-episode",
                interfering_episode_id="interfering-episode",
                student_id=initial.student_id,
                checkpoint_id=initial.checkpoint_id,
                state_snapshot_id=interference_after.state_id,
                competency_id=source_items[0].competency_id,
                item_id=interference_item.item_id,
                environment_fingerprint=_ENVIRONMENT,
                assignment_seed=21,
                assignment_propensity=0.5,
                due_at=_NOW,
                created_at=_NOW,
            ),
        )
        verifier = VerifierResult(
            result_id="retention-verifier-result",
            verifier_id="algebra.symbolic",
            verifier_version="1",
            scope="a-retention-trial",
            disposition=VerifierDisposition.VERIFIED,
            deterministic=True,
            summary="fresh retention answer verified",
            evidence={"expected_answer_matched": True},
            created_at=_NOW,
        )
        await rewards.record_verifier_result(session, verifier)

        claimed = await scheduler.claim_due(
            session,
            worker_id="evaluation-worker-one",
            lease_for=timedelta(minutes=1),
            now=_NOW,
        )
        assert claimed is not None
        assert claimed.schedule.trial_id == "a-retention-trial"
        await registry.record_exposure(
            session,
            exposure=ExposureRecord(
                exposure_id="retention-prompt-exposure",
                student_id=initial.student_id,
                checkpoint_id=initial.checkpoint_id,
                state_id=source_after.state_id,
                item_id=retention_item.item_id,
                template_family_id=retention_item.template_family_id,
                instance_group_id=retention_item.instance_group_id,
                exposure_type=ExposureType.PROMPT,
                prompt_exposed=True,
                answer_exposed=False,
                critique_exposed=False,
                repair_exposed=False,
                metadata_exposed=False,
                episode_id=claimed.schedule.trial_id,
                created_at=_NOW,
            ),
            lease_token=claimed.lease_token,
            lease_owner=claimed.lease_owner,
        )
        await scheduler.complete(
            session,
            outcome=EvaluationOutcome(
                trial_id=claimed.schedule.trial_id,
                exposure_id="retention-prompt-exposure",
                success=True,
                score=1.0,
                missing_reasons={},
                verifier_result_ids=(verifier.result_id,),
                contamination_checks={"fresh_instance_group": True},
                completed_at=_NOW + timedelta(seconds=1),
            ),
            lease_token=claimed.lease_token,
            lease_owner=claimed.lease_owner,
        )
        completed_trial = await session.get(EvaluationTrialRow, claimed.schedule.trial_id)
        completed_item = await session.get(CorpusItemRow, retention_item.item_id)
        assert completed_trial is not None
        assert completed_trial.status == EvaluationTrialStatus.COMPLETE.value
        assert completed_item is not None and completed_item.status == ItemStatus.ACTIVE.value

        expiring = await scheduler.claim_due(
            session,
            worker_id="evaluation-worker-two",
            lease_for=timedelta(seconds=1),
            now=_NOW,
        )
        assert expiring is not None
        assert expiring.schedule.trial_id == "b-interference-trial"

    async with database.transaction() as session:
        recovered = await scheduler.recover_expired(session, now=_NOW + timedelta(seconds=2))
        recovered_trial = await session.get(EvaluationTrialRow, "b-interference-trial")
        recovered_item = await session.get(CorpusItemRow, interference_item.item_id)
        assert recovered == 1
        assert recovered_trial is not None
        assert recovered_trial.status == EvaluationTrialStatus.SCHEDULED.value
        assert recovered_item is not None and recovered_item.status == ItemStatus.ACTIVE.value
        reclaimed = await scheduler.claim_due(
            session,
            worker_id="evaluation-worker-three",
            lease_for=timedelta(minutes=1),
            now=_NOW + timedelta(seconds=2),
        )
        assert reclaimed is not None
        assert reclaimed.schedule.trial_id == "b-interference-trial"
