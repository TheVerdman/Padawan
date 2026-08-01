from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import func, select

from padawan.experiments.engine import ExperimentEngine
from padawan.models.database import Database
from padawan.models.tables import (
    EpisodeRow,
    ExperimentBlockRow,
    ExperimentRow,
    RunRow,
    TeacherInterventionRow,
)


class ReportingService:
    """Read-only reports that expose attrition and never synthesize missing outcomes."""

    def __init__(self, database: Database, experiments: ExperimentEngine) -> None:
        self.database = database
        self.experiments = experiments

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
            return {
                "format": "padawan.operations_report",
                "version": 1,
                "runs_by_state": run_counts,
                "episodes_by_status": episode_counts,
                "rejected_teacher_interventions": int(rejected_teachers or 0),
            }
