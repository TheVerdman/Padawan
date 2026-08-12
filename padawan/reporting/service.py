from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import func, or_, select

from padawan.checkpoints import CheckpointRegistry
from padawan.experiments.controls import ResearchControlRegistry
from padawan.experiments.engine import ExperimentEngine
from padawan.models.database import Database
from padawan.models.tables import (
    CheckpointComparisonRow,
    CheckpointDecisionRow,
    CheckpointEvaluationRow,
    CheckpointRow,
    EpisodeRow,
    EvaluationTrialRow,
    ExperimentBlockRow,
    ExperimentRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    RewardRow,
    RunRow,
    StudentStateRow,
    StudyExperimentRow,
    StudyResultRow,
    StudyRow,
    TeacherInterventionRow,
    TrainingEligibilityRow,
    VerifierResultRow,
)
from padawan.rewards import RewardEngine
from padawan.studies import StudyEngine


class ReportingService:
    """Read-only reports that expose attrition and never synthesize missing outcomes."""

    def __init__(
        self,
        database: Database,
        experiments: ExperimentEngine,
        *,
        rewards: RewardEngine | None = None,
        studies: StudyEngine | None = None,
        checkpoints: CheckpointRegistry | None = None,
    ) -> None:
        self.database = database
        self.experiments = experiments
        self.rewards = rewards or RewardEngine()
        self.studies = studies or StudyEngine()
        self.checkpoints = checkpoints or CheckpointRegistry()
        self.controls = ResearchControlRegistry()

    async def experiment(self, experiment_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            report = await self.experiments.analyze(session, experiment_id=experiment_id)
            experiment = await session.get(ExperimentRow, experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            parent_state = await session.get(StudentStateRow, experiment.parent_state_id)
            blocks = (
                await session.scalars(
                    select(ExperimentBlockRow)
                    .where(ExperimentBlockRow.experiment_id == experiment_id)
                    .order_by(ExperimentBlockRow.block_index)
                )
            ).all()
            execution = (
                await session.get(ResearchExecutionRow, experiment.research_execution_digest)
                if experiment.research_execution_digest is not None
                else None
            )
            profile = (
                await session.get(HarnessProfileRow, execution.harness_profile_digest)
                if execution is not None
                else None
            )
            control_assessment = (
                (
                    await self.controls.compare(
                        session,
                        left_execution_digest=experiment.research_execution_digest,
                        right_execution_digest=experiment.research_execution_digest,
                    )
                ).model_dump(mode="json")
                if experiment.research_execution_digest is not None
                else {
                    "disposition": "insufficient_provenance",
                    "comparable": False,
                    "provenance_complete": False,
                    "provenance_gaps": ("experiment has no research execution",),
                }
            )
            binding_consistent = (
                experiment.research_execution_digest is not None
                and experiment.design.get("research_execution_digest")
                == experiment.research_execution_digest
                and execution is not None
                and parent_state is not None
                and execution.checkpoint_id == parent_state.checkpoint_id
                and execution.runtime_id == parent_state.runtime_id
                and execution.research_role == parent_state.research_role
                and execution.parent_state_id == parent_state.state_id
                and execution.parent_state_hash == parent_state.state_hash
                and execution.seed == experiment.seed
                and experiment.design.get("treatment_condition")
                == execution.record_json.get("harness_parameters", {}).get("treatment_condition")
                and experiment.design.get("control_condition")
                == execution.record_json.get("harness_parameters", {}).get("control_condition")
            )
            if experiment.research_execution_digest is not None and not binding_consistent:
                recorded_gaps = control_assessment.get("provenance_gaps")
                gaps = (
                    tuple(str(item) for item in recorded_gaps)
                    if isinstance(recorded_gaps, (list, tuple))
                    else ()
                ) + ("experiment binding differs from its research execution",)
                control_assessment = {
                    **control_assessment,
                    "disposition": "insufficient_provenance",
                    "comparable": False,
                    "provenance_complete": False,
                    "provenance_gaps": gaps,
                }
            return {
                "format": "padawan.experiment_report",
                "version": 2,
                "design": experiment.design,
                "parent_state": (
                    {
                        "state_id": parent_state.state_id,
                        "state_hash": parent_state.state_hash,
                        "student_id": parent_state.student_id,
                        "checkpoint_id": parent_state.checkpoint_id,
                        "runtime_id": parent_state.runtime_id,
                        "research_role": parent_state.research_role,
                    }
                    if parent_state is not None
                    else None
                ),
                "research_execution_digest": experiment.research_execution_digest,
                "research_execution": execution.record_json if execution is not None else None,
                "harness_profile": profile.record_json if profile is not None else None,
                "research_binding_consistent": binding_consistent,
                "research_control_assessment": control_assessment,
                "statistics": asdict(report),
                "blocks": [
                    {
                        "block_id": row.block_id,
                        "assignment": row.assignment,
                        "outcomes": row.outcomes,
                        "excluded_contamination": row.contamination_detected,
                        "excluded_infrastructure": row.infrastructure_failure,
                    }
                    for row in blocks
                ],
                "causal_claim_permitted": report.total_blocks > 0
                and report.analyzed_blocks == report.total_blocks
                and not report.excluded_contaminated
                and not report.excluded_infrastructure
                and binding_consistent
                and bool(control_assessment["comparable"]),
            }

    async def study(self, study_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            row = await session.get(StudyRow, study_id)
            if row is None:
                raise KeyError(study_id)
            aggregate = await self.studies.aggregate(session, study_id=study_id)
            bindings = (
                await session.scalars(
                    select(StudyExperimentRow)
                    .where(StudyExperimentRow.study_id == study_id)
                    .order_by(
                        StudyExperimentRow.condition_id,
                        StudyExperimentRow.experiment_id,
                    )
                )
            ).all()
            trials = (
                await session.scalars(
                    select(EvaluationTrialRow)
                    .where(EvaluationTrialRow.study_id == study_id)
                    .order_by(EvaluationTrialRow.due_at, EvaluationTrialRow.trial_id)
                )
            ).all()
            results = (
                await session.scalars(
                    select(StudyResultRow)
                    .where(StudyResultRow.study_id == study_id)
                    .order_by(StudyResultRow.condition_id, StudyResultRow.checkpoint_id)
                )
            ).all()
            return {
                "format": "padawan.study_report",
                "version": 3,
                "manifest": row.record_json,
                "manifest_digest": row.manifest_digest,
                "status": row.status,
                "completed_at": row.completed_at,
                "statistics": asdict(aggregate),
                "experiments": [
                    {
                        "experiment_id": binding.experiment_id,
                        "condition_id": binding.condition_id,
                        "checkpoint_id": binding.checkpoint_id,
                        "research_role": binding.research_role,
                        "suite_manifest_digest": binding.suite_manifest_digest,
                        "environment_fingerprint": binding.environment_fingerprint,
                        "research_execution_digest": binding.research_execution_digest,
                        "factor_values": binding.factor_values,
                        "assignment_propensity": binding.assignment_propensity,
                    }
                    for binding in bindings
                ],
                "immutable_results": [result.record_json for result in results],
                "evaluation_trials": [
                    {
                        "schedule": trial.record_json,
                        "status": trial.status,
                        "outcome": trial.outcome_json,
                        "completed_at": trial.completed_at,
                    }
                    for trial in trials
                ],
            }

    async def reward(self, reward_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            row = await session.get(RewardRow, reward_id)
            if row is None:
                raise KeyError(reward_id)
            record = await self.rewards.get(session, reward_id=reward_id)
            recomputation = await self.rewards.recompute(session, reward_id=reward_id)
            result_ids = sorted(
                {result_id for gate in record.hard_gates for result_id in gate.evidence_refs}
            )
            verifier_rows = (
                (
                    await session.scalars(
                        select(VerifierResultRow)
                        .where(VerifierResultRow.result_id.in_(result_ids))
                        .order_by(VerifierResultRow.result_id)
                    )
                ).all()
                if result_ids
                else []
            )
            eligibility = (
                await session.scalars(
                    select(TrainingEligibilityRow)
                    .where(TrainingEligibilityRow.reward_id == reward_id)
                    .order_by(TrainingEligibilityRow.created_at, TrainingEligibilityRow.decision_id)
                )
            ).all()
            eligibility_checks = [
                asdict(
                    await self.rewards.verify_training_eligibility(
                        session, decision_id=item.decision_id
                    )
                )
                for item in eligibility
            ]
            return {
                "format": "padawan.reward_report",
                "version": 1,
                "reward": record.model_dump(mode="json"),
                "record_digest": row.record_digest,
                "recomputation": asdict(recomputation),
                "verifier_evidence": [item.record_json for item in verifier_rows],
                "training_eligibility": [item.record_json for item in eligibility],
                "training_eligibility_verifications": eligibility_checks,
            }

    async def checkpoint(self, checkpoint_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            checkpoint = await session.get(CheckpointRow, checkpoint_id)
            if checkpoint is None:
                raise KeyError(checkpoint_id)
            evaluations = (
                await session.scalars(
                    select(CheckpointEvaluationRow)
                    .where(CheckpointEvaluationRow.checkpoint_id == checkpoint_id)
                    .order_by(
                        CheckpointEvaluationRow.created_at,
                        CheckpointEvaluationRow.evaluation_id,
                    )
                )
            ).all()
            evaluation_ids = [row.evaluation_id for row in evaluations]
            comparisons = (
                (
                    await session.scalars(
                        select(CheckpointComparisonRow)
                        .where(
                            or_(
                                CheckpointComparisonRow.baseline_evaluation_id.in_(evaluation_ids),
                                CheckpointComparisonRow.candidate_evaluation_id.in_(evaluation_ids),
                            )
                        )
                        .order_by(
                            CheckpointComparisonRow.created_at,
                            CheckpointComparisonRow.comparison_id,
                        )
                    )
                ).all()
                if evaluation_ids
                else []
            )
            decisions = (
                await session.scalars(
                    select(CheckpointDecisionRow)
                    .where(CheckpointDecisionRow.checkpoint_id == checkpoint_id)
                    .order_by(
                        CheckpointDecisionRow.created_at,
                        CheckpointDecisionRow.decision_id,
                    )
                )
            ).all()
            comparison_checks = [
                asdict(
                    await self.checkpoints.recompute_comparison(
                        session, comparison_id=row.comparison_id
                    )
                )
                for row in comparisons
            ]
            decision_checks = [
                asdict(await self.checkpoints.verify_decision(session, decision_id=row.decision_id))
                for row in decisions
            ]
            return {
                "format": "padawan.checkpoint_report",
                "version": 1,
                "manifest": checkpoint.record_json,
                "manifest_digest": checkpoint.manifest_digest,
                "status": checkpoint.status,
                "evaluations": [row.record_json for row in evaluations],
                "comparisons": [row.record_json for row in comparisons],
                "comparison_recomputations": comparison_checks,
                "decisions": [row.record_json for row in decisions],
                "decision_verifications": decision_checks,
            }

    async def operations(self) -> dict[str, Any]:
        async with self.database.transaction() as session:
            run_counts = dict(
                (await session.execute(select(RunRow.state, func.count()).group_by(RunRow.state)))
                .tuples()
                .all()
            )
            episode_counts = dict(
                (
                    await session.execute(
                        select(EpisodeRow.status, func.count()).group_by(EpisodeRow.status)
                    )
                )
                .tuples()
                .all()
            )
            rejected_teachers = await session.scalar(
                select(func.count())
                .select_from(TeacherInterventionRow)
                .where(TeacherInterventionRow.validation_status != "accepted")
            )
            study_counts = dict(
                (
                    await session.execute(
                        select(StudyRow.status, func.count()).group_by(StudyRow.status)
                    )
                )
                .tuples()
                .all()
            )
            trial_counts = dict(
                (
                    await session.execute(
                        select(EvaluationTrialRow.status, func.count()).group_by(
                            EvaluationTrialRow.status
                        )
                    )
                )
                .tuples()
                .all()
            )
            checkpoint_counts = dict(
                (
                    await session.execute(
                        select(CheckpointRow.status, func.count()).group_by(CheckpointRow.status)
                    )
                )
                .tuples()
                .all()
            )
            reward_count = await session.scalar(select(func.count()).select_from(RewardRow))
            harness_profile_count = await session.scalar(
                select(func.count()).select_from(HarnessProfileRow)
            )
            research_execution_count = await session.scalar(
                select(func.count()).select_from(ResearchExecutionRow)
            )
            return {
                "format": "padawan.operations_report",
                "version": 2,
                "runs_by_state": run_counts,
                "episodes_by_status": episode_counts,
                "studies_by_status": study_counts,
                "evaluation_trials_by_status": trial_counts,
                "checkpoints_by_status": checkpoint_counts,
                "rewards": int(reward_count or 0),
                "harness_profiles": int(harness_profile_count or 0),
                "research_executions": int(research_execution_count or 0),
                "rejected_teacher_interventions": int(rejected_teachers or 0),
            }
