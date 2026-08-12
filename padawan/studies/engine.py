from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import StudyManifest, StudyStatus
from padawan.models.tables import (
    ExperimentBlockRow,
    ExperimentRow,
    ResearchExecutionRow,
    StudentStateRow,
    StudyExperimentRow,
    StudyRow,
)


@dataclass(frozen=True)
class ConditionAggregate:
    condition_id: str
    experiment_count: int
    total_blocks: int
    analyzed_blocks: int
    missing_blocks: int
    excluded_contaminated: int
    excluded_infrastructure: int
    treatment_success_rate: float | None
    control_success_rate: float | None
    paired_gain: float | None


@dataclass(frozen=True)
class StudyAggregateReport:
    study_id: str
    manifest_digest: str
    status: StudyStatus
    experiment_count: int
    total_blocks: int
    analyzed_blocks: int
    missing_blocks: int
    excluded_contaminated: int
    excluded_infrastructure: int
    conditions: tuple[ConditionAggregate, ...]
    research_controls_complete: bool
    research_execution_digests: tuple[str, ...]
    differing_research_axes: tuple[str, ...]
    declared_comparison_axes: tuple[str, ...]
    blocking_research_differences: tuple[str, ...]
    research_provenance_gaps: tuple[str, ...]
    causal_claim_permitted: bool


class StudyEngine:
    """Immutable study definitions with block-level, missingness-preserving aggregation."""

    def __init__(self, controls: ResearchControlRegistry | None = None) -> None:
        self.controls = controls or ResearchControlRegistry()

    async def create(
        self,
        session: AsyncSession,
        manifest: StudyManifest,
        *,
        status: StudyStatus = StudyStatus.PLANNED,
    ) -> StudyRow:
        payload = manifest.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(StudyRow, manifest.study_id)
        if existing is not None:
            if (
                existing.version != manifest.version
                or existing.manifest_digest != digest
                or existing.record_json != payload
            ):
                raise ValueError("study ID conflicts with persisted manifest")
            return existing
        for binding in manifest.experiments:
            experiment = await session.get(ExperimentRow, binding.experiment_id)
            if experiment is None:
                raise ValueError(f"study cites unknown experiment: {binding.experiment_id}")
            state = await session.get(StudentStateRow, experiment.parent_state_id)
            if state is None:
                raise ValueError(
                    f"experiment has no persisted parent state: {binding.experiment_id}"
                )
            if state.checkpoint_id != binding.checkpoint_id:
                raise ValueError(
                    f"study checkpoint differs from experiment state: {binding.experiment_id}"
                )
            if state.research_role != binding.research_role.value:
                raise ValueError(
                    f"study research role differs from experiment state: {binding.experiment_id}"
                )
            if experiment.research_execution_digest != binding.research_execution_digest:
                raise ValueError(
                    f"study research execution differs from experiment: {binding.experiment_id}"
                )
            if binding.research_execution_digest is not None:
                if (
                    experiment.design.get("research_execution_digest")
                    != binding.research_execution_digest
                ):
                    raise ValueError(
                        f"experiment design differs from research execution: "
                        f"{binding.experiment_id}"
                    )
                execution = await session.get(
                    ResearchExecutionRow, binding.research_execution_digest
                )
                if execution is None:
                    raise ValueError(
                        f"study cites unknown research execution: {binding.experiment_id}"
                    )
                if execution.checkpoint_id != binding.checkpoint_id:
                    raise ValueError(
                        f"study checkpoint differs from research execution: {binding.experiment_id}"
                    )
                if execution.environment_fingerprint != binding.environment_fingerprint:
                    raise ValueError(
                        f"study environment differs from research execution: "
                        f"{binding.experiment_id}"
                    )
        row = StudyRow(
            study_id=manifest.study_id,
            version=manifest.version,
            manifest_digest=digest,
            suite_manifest_digest=manifest.suite_manifest_digest,
            status=status.value,
            record_json=payload,
            created_at=manifest.created_at,
            completed_at=None,
        )
        session.add(row)
        await session.flush()
        for binding in manifest.experiments:
            binding_digest = sha256_digest(
                {"study_id": manifest.study_id, "binding": binding.model_dump(mode="json")}
            )
            session.add(
                StudyExperimentRow(
                    binding_id=f"study-binding-{binding_digest[7:31]}",
                    study_id=manifest.study_id,
                    experiment_id=binding.experiment_id,
                    condition_id=binding.condition_id,
                    checkpoint_id=binding.checkpoint_id,
                    research_role=binding.research_role.value,
                    suite_manifest_digest=binding.suite_manifest_digest,
                    environment_fingerprint=binding.environment_fingerprint,
                    research_execution_digest=binding.research_execution_digest,
                    factor_values=dict(binding.factor_values),
                    assignment_propensity=binding.assignment_propensity,
                )
            )
        await session.flush()
        return row

    async def transition(
        self,
        session: AsyncSession,
        *,
        study_id: str,
        to_status: StudyStatus,
        completed_at: datetime | None = None,
    ) -> None:
        row = await session.scalar(
            select(StudyRow).where(StudyRow.study_id == study_id).with_for_update()
        )
        if row is None:
            raise KeyError(study_id)
        current = StudyStatus(row.status)
        allowed = {
            StudyStatus.PLANNED: {
                StudyStatus.ACTIVE,
                StudyStatus.CANCELLED,
                StudyStatus.INVALID,
            },
            StudyStatus.ACTIVE: {
                StudyStatus.COMPLETE,
                StudyStatus.CANCELLED,
                StudyStatus.INVALID,
            },
            StudyStatus.COMPLETE: set(),
            StudyStatus.CANCELLED: set(),
            StudyStatus.INVALID: set(),
        }
        if to_status == current:
            return
        if to_status not in allowed[current]:
            raise ValueError(f"invalid study transition: {current.value} -> {to_status.value}")
        row.status = to_status.value
        if to_status in {StudyStatus.COMPLETE, StudyStatus.CANCELLED, StudyStatus.INVALID}:
            row.completed_at = completed_at or datetime.now(UTC)
        await session.flush()

    async def aggregate(self, session: AsyncSession, *, study_id: str) -> StudyAggregateReport:
        study = await session.get(StudyRow, study_id)
        if study is None:
            raise KeyError(study_id)
        bindings = (
            await session.scalars(
                select(StudyExperimentRow)
                .where(StudyExperimentRow.study_id == study_id)
                .order_by(StudyExperimentRow.condition_id, StudyExperimentRow.experiment_id)
            )
        ).all()
        by_condition: dict[str, list[ExperimentBlockRow]] = {}
        for binding in bindings:
            blocks = (
                await session.scalars(
                    select(ExperimentBlockRow)
                    .where(ExperimentBlockRow.experiment_id == binding.experiment_id)
                    .order_by(ExperimentBlockRow.block_index)
                )
            ).all()
            by_condition.setdefault(binding.condition_id, []).extend(blocks)
        conditions = tuple(
            _aggregate_condition(
                condition_id, blocks, experiment_count=_experiment_count(bindings, condition_id)
            )
            for condition_id, blocks in sorted(by_condition.items())
        )
        controls = await self.controls.assess_study(session, study_id=study_id)
        return StudyAggregateReport(
            study_id=study_id,
            manifest_digest=study.manifest_digest,
            status=StudyStatus(study.status),
            experiment_count=len(bindings),
            total_blocks=sum(condition.total_blocks for condition in conditions),
            analyzed_blocks=sum(condition.analyzed_blocks for condition in conditions),
            missing_blocks=sum(condition.missing_blocks for condition in conditions),
            excluded_contaminated=sum(condition.excluded_contaminated for condition in conditions),
            excluded_infrastructure=sum(
                condition.excluded_infrastructure for condition in conditions
            ),
            conditions=conditions,
            research_controls_complete=controls.provenance_complete,
            research_execution_digests=controls.execution_digests,
            differing_research_axes=tuple(axis.value for axis in controls.differing_axes),
            declared_comparison_axes=tuple(
                axis.value for axis in controls.declared_comparison_axes
            ),
            blocking_research_differences=tuple(
                axis.value for axis in controls.blocking_differences
            ),
            research_provenance_gaps=controls.provenance_gaps,
            causal_claim_permitted=bool(conditions)
            and all(condition.analyzed_blocks > 0 for condition in conditions)
            and not any(condition.excluded_contaminated for condition in conditions)
            and controls.comparable,
        )


def _experiment_count(bindings: Sequence[StudyExperimentRow], condition_id: str) -> int:
    return sum(binding.condition_id == condition_id for binding in bindings)


def _aggregate_condition(
    condition_id: str,
    blocks: list[ExperimentBlockRow],
    *,
    experiment_count: int,
) -> ConditionAggregate:
    contaminated = sum(block.contamination_detected for block in blocks)
    infrastructure = sum(block.infrastructure_failure for block in blocks)
    missing = sum(
        block.outcomes is None
        or (
            not block.contamination_detected
            and not block.infrastructure_failure
            and (
                block.outcomes.get("treatment_success") is None
                or block.outcomes.get("control_success") is None
            )
        )
        for block in blocks
    )
    analyzed = [
        block
        for block in blocks
        if block.outcomes is not None
        and not block.contamination_detected
        and not block.infrastructure_failure
        and block.outcomes.get("treatment_success") is not None
        and block.outcomes.get("control_success") is not None
    ]
    if not analyzed:
        return ConditionAggregate(
            condition_id=condition_id,
            experiment_count=experiment_count,
            total_blocks=len(blocks),
            analyzed_blocks=0,
            missing_blocks=missing,
            excluded_contaminated=contaminated,
            excluded_infrastructure=infrastructure,
            treatment_success_rate=None,
            control_success_rate=None,
            paired_gain=None,
        )
    pairs = [
        (
            bool(cast(dict[str, Any], block.outcomes)["treatment_success"]),
            bool(cast(dict[str, Any], block.outcomes)["control_success"]),
        )
        for block in analyzed
    ]
    differences = [float(treatment) - float(control) for treatment, control in pairs]
    return ConditionAggregate(
        condition_id=condition_id,
        experiment_count=experiment_count,
        total_blocks=len(blocks),
        analyzed_blocks=len(analyzed),
        missing_blocks=missing,
        excluded_contaminated=contaminated,
        excluded_infrastructure=infrastructure,
        treatment_success_rate=mean(float(treatment) for treatment, _ in pairs),
        control_success_rate=mean(float(control) for _, control in pairs),
        paired_gain=mean(differences),
    )
