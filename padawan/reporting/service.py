from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import func, or_, select

from padawan.checkpoints import CheckpointRegistry
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
    RewardRow,
    RunRow,
    StudyExperimentRow,
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

    async def experiment(self, experiment_id: str) -> dict[str, Any]:
        async with self.database.transaction() as session:
            report = await self.experiments.analyze(session, experiment_id=experiment_id)
            experiment = await session.get(ExperimentRow, experiment_id)
            if experiment is None:
                raise KeyError(experiment_id)
            blocks = (
                await session.scalars(
                    select(ExperimentBlockRow)
                    .where(ExperimentBlockRow.experiment_id == experiment_id)
                    .order_by(ExperimentBlockRow.block_index)
                )
            ).all()
            return {
                "format": "padawan.experiment_report",
                "version": 1,
                "design": experiment.design,
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
                "causal_claim_permitted": bool(report.analyzed_blocks)
                and not report.excluded_contaminated,
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
            return {
                "format": "padawan.study_report",
                "version": 1,
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
                        "assignment_propensity": binding.assignment_propensity,
                    }
                    for binding in bindings
                ],
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
            return {
                "format": "padawan.operations_report",
                "version": 1,
                "runs_by_state": run_counts,
                "episodes_by_status": episode_counts,
                "studies_by_status": study_counts,
                "evaluation_trials_by_status": trial_counts,
                "checkpoints_by_status": checkpoint_counts,
                "rewards": int(reward_count or 0),
                "rejected_teacher_interventions": int(rejected_teachers or 0),
            }
