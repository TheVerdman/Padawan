from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog
from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    EvaluationSuiteManifest,
    StudyBlockEvidence,
    StudyManifest,
    StudyResultMetric,
    StudyResultRecord,
    StudyStatus,
)
from padawan.models.tables import (
    EvaluationSuiteRow,
    ExperimentBlockRow,
    ExperimentRow,
    ResearchExecutionRow,
    StudentStateRow,
    StudyExperimentRow,
    StudyResultRow,
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


@dataclass(frozen=True)
class StudySealingContext:
    """Read-only inputs available to a registered study aggregation policy."""

    study: StudyRow
    manifest: StudyManifest
    bindings: tuple[StudyExperimentRow, ...]
    blocks_by_experiment: Mapping[str, tuple[ExperimentBlockRow, ...]]
    research_controls_complete: bool
    research_controls_comparable: bool
    created_at: datetime


class StudyAggregationPolicy(Protocol):
    """A named policy that seals ordinary, content-addressed study results."""

    policy_id: str
    policy_version: str

    async def seal_results(
        self,
        session: AsyncSession,
        context: StudySealingContext,
    ) -> tuple[StudyResultRecord, ...]: ...


class StudyEngine:
    """Immutable study definitions with block-level, missingness-preserving aggregation."""

    def __init__(
        self,
        controls: ResearchControlRegistry | None = None,
        *,
        aggregation_policies: Sequence[StudyAggregationPolicy] = (),
        artifacts: ArtifactCatalog | None = None,
    ) -> None:
        self.controls = controls or ResearchControlRegistry()
        self._aggregation_policies: dict[tuple[str, str], StudyAggregationPolicy] = {}
        # Import lazily so Atlas can implement the protocol without making the
        # core Study module depend on Atlas at module-import time.
        from padawan.atlas.artifacts import AtlasArtifactBoundary
        from padawan.atlas.studies import AtlasFixedTrialsStudyPolicy

        self.register_aggregation_policy(
            AtlasFixedTrialsStudyPolicy(
                artifacts=AtlasArtifactBoundary(artifacts) if artifacts is not None else None,
            )
        )
        for policy in aggregation_policies:
            self.register_aggregation_policy(policy)

    def register_aggregation_policy(self, policy: StudyAggregationPolicy) -> None:
        key = (policy.policy_id, policy.policy_version)
        if key in self._aggregation_policies:
            raise ValueError(f"study aggregation policy is already registered: {key[0]}@{key[1]}")
        self._aggregation_policies[key] = policy

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
        suite_manifest: EvaluationSuiteManifest | None = None
        if any(binding.research_execution_digest is not None for binding in manifest.experiments):
            suite = await session.get(EvaluationSuiteRow, manifest.suite_manifest_digest)
            if suite is None:
                raise ValueError("controlled study requires a registered evaluation suite")
            if suite.manifest_digest != sha256_digest(suite.record_json):
                raise ValueError("controlled study evaluation suite digest is invalid")
            suite_manifest = EvaluationSuiteManifest.model_validate(suite.record_json, strict=False)
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
                if execution.parent_state_id != state.state_id:
                    raise ValueError(
                        f"study parent state differs from research execution: "
                        f"{binding.experiment_id}"
                    )
                if execution.parent_state_hash != state.state_hash:
                    raise ValueError(
                        f"study parent-state hash differs from research execution: "
                        f"{binding.experiment_id}"
                    )
                if execution.environment_fingerprint != binding.environment_fingerprint:
                    raise ValueError(
                        f"study environment differs from research execution: "
                        f"{binding.experiment_id}"
                    )
                treatment_condition = experiment.design.get("treatment_condition")
                control_condition = experiment.design.get("control_condition")
                if not isinstance(treatment_condition, str) or not isinstance(
                    control_condition, str
                ):
                    raise ValueError(
                        f"study experiment has invalid controlled conditions: "
                        f"{binding.experiment_id}"
                    )
                await self.controls.validate_experiment_conditions(
                    session,
                    execution_digest=binding.research_execution_digest,
                    treatment_condition=treatment_condition,
                    control_condition=control_condition,
                )
                if (
                    suite_manifest is None
                    or execution.task_manifest_digest not in suite_manifest.task_manifest_digests
                ):
                    raise ValueError(
                        f"study task is outside evaluation suite: {binding.experiment_id}"
                    )
                if execution.environment_fingerprint not in suite_manifest.environment_fingerprints:
                    raise ValueError(
                        f"study environment is outside evaluation suite: {binding.experiment_id}"
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
        terminal_at = completed_at or datetime.now(UTC)
        if to_status == StudyStatus.COMPLETE:
            await self._seal_results(session, study=row, created_at=terminal_at)
        row.status = to_status.value
        if to_status in {StudyStatus.COMPLETE, StudyStatus.CANCELLED, StudyStatus.INVALID}:
            row.completed_at = terminal_at
        await session.flush()

    async def result(
        self,
        session: AsyncSession,
        *,
        study_id: str,
        condition_id: str,
        checkpoint_id: str,
    ) -> StudyResultRecord:
        row = await session.scalar(
            select(StudyResultRow).where(
                StudyResultRow.study_id == study_id,
                StudyResultRow.condition_id == condition_id,
                StudyResultRow.checkpoint_id == checkpoint_id,
            )
        )
        if row is None:
            raise KeyError((study_id, condition_id, checkpoint_id))
        if row.record_digest != sha256_digest(row.record_json):
            raise ValueError("study result digest is invalid")
        result = StudyResultRecord.model_validate(row.record_json, strict=False)
        if (
            row.result_id,
            row.study_id,
            row.study_manifest_digest,
            row.suite_manifest_digest,
            row.condition_id,
            row.checkpoint_id,
        ) != (
            result.result_id,
            result.study_id,
            result.study_manifest_digest,
            result.suite_manifest_digest,
            result.condition_id,
            result.checkpoint_id,
        ):
            raise ValueError("study result columns disagree with immutable content")
        return result

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
            and all(_condition_is_complete_and_analyzable(condition) for condition in conditions)
            and controls.comparable,
        )

    async def _seal_results(
        self,
        session: AsyncSession,
        *,
        study: StudyRow,
        created_at: datetime,
    ) -> tuple[StudyResultRecord, ...]:
        if study.manifest_digest != sha256_digest(study.record_json):
            raise ValueError("study manifest digest is invalid")
        manifest = StudyManifest.model_validate(study.record_json, strict=False)
        bindings = (
            await session.scalars(
                select(StudyExperimentRow)
                .where(StudyExperimentRow.study_id == study.study_id)
                .order_by(
                    StudyExperimentRow.condition_id,
                    StudyExperimentRow.checkpoint_id,
                    StudyExperimentRow.experiment_id,
                )
            )
        ).all()
        grouped: dict[tuple[str, str], list[StudyExperimentRow]] = {}
        blocks_by_experiment: dict[str, list[ExperimentBlockRow]] = {}
        unfinished: list[str] = []
        for binding in bindings:
            grouped.setdefault((binding.condition_id, binding.checkpoint_id), []).append(binding)
            experiment_blocks = list(
                (
                    await session.scalars(
                        select(ExperimentBlockRow)
                        .where(ExperimentBlockRow.experiment_id == binding.experiment_id)
                        .order_by(ExperimentBlockRow.block_index)
                    )
                ).all()
            )
            blocks_by_experiment[binding.experiment_id] = experiment_blocks
            unfinished.extend(
                block.block_id for block in experiment_blocks if block.outcomes is None
            )
        if unfinished:
            raise ValueError(
                "study cannot complete with unfinished blocks: " + ", ".join(sorted(unfinished))
            )
        controls = await self.controls.assess_study(session, study_id=study.study_id)
        policy = self._aggregation_policies.get(
            (manifest.aggregation_policy_id, manifest.aggregation_policy_version)
        )
        if policy is not None:
            policy_results = await policy.seal_results(
                session,
                StudySealingContext(
                    study=study,
                    manifest=manifest,
                    bindings=tuple(bindings),
                    blocks_by_experiment={
                        experiment_id: tuple(blocks)
                        for experiment_id, blocks in blocks_by_experiment.items()
                    },
                    research_controls_complete=controls.provenance_complete,
                    research_controls_comparable=controls.comparable,
                    created_at=created_at,
                ),
            )
            _validate_policy_results(
                study=study,
                manifest=manifest,
                bindings=bindings,
                blocks_by_experiment=blocks_by_experiment,
                results=policy_results,
                research_controls_complete=controls.provenance_complete,
                research_controls_comparable=controls.comparable,
            )
            await _persist_results(session, policy_results)
            return policy_results

        results: list[StudyResultRecord] = []
        for (condition_id, checkpoint_id), group in sorted(grouped.items()):
            blocks: list[ExperimentBlockRow] = []
            for binding in group:
                blocks.extend(blocks_by_experiment[binding.experiment_id])
            aggregate = _aggregate_condition(
                condition_id,
                blocks,
                experiment_count=len(group),
            )
            missing_reason = "no analyzable paired blocks"
            metrics = tuple(
                sorted(
                    (
                        StudyResultMetric(
                            metric_id="control_success_rate",
                            value=aggregate.control_success_rate,
                            missing_reason=(
                                missing_reason if aggregate.control_success_rate is None else None
                            ),
                        ),
                        StudyResultMetric(
                            metric_id="paired_gain",
                            value=aggregate.paired_gain,
                            missing_reason=(
                                missing_reason if aggregate.paired_gain is None else None
                            ),
                        ),
                        StudyResultMetric(
                            metric_id="treatment_success_rate",
                            value=aggregate.treatment_success_rate,
                            missing_reason=(
                                missing_reason if aggregate.treatment_success_rate is None else None
                            ),
                        ),
                    ),
                    key=lambda metric: metric.metric_id,
                )
            )
            source_blocks = tuple(
                StudyBlockEvidence(
                    block_id=block.block_id,
                    experiment_id=block.experiment_id,
                    block_digest=_block_digest(block),
                )
                for block in sorted(blocks, key=lambda item: (item.experiment_id, item.block_id))
            )
            identity = {
                "study_id": study.study_id,
                "study_manifest_digest": study.manifest_digest,
                "suite_manifest_digest": study.suite_manifest_digest,
                "condition_id": condition_id,
                "checkpoint_id": checkpoint_id,
                "aggregation_policy_id": manifest.aggregation_policy_id,
                "aggregation_policy_version": manifest.aggregation_policy_version,
                "source_blocks": [block.model_dump(mode="json") for block in source_blocks],
                "total_blocks": aggregate.total_blocks,
                "analyzed_blocks": aggregate.analyzed_blocks,
                "missing_blocks": aggregate.missing_blocks,
                "excluded_contaminated": aggregate.excluded_contaminated,
                "excluded_infrastructure": aggregate.excluded_infrastructure,
                "metrics": [metric.model_dump(mode="json") for metric in metrics],
            }
            result = StudyResultRecord(
                result_id=f"study-result-{sha256_digest(identity)[7:31]}",
                study_id=study.study_id,
                study_manifest_digest=study.manifest_digest,
                suite_manifest_digest=study.suite_manifest_digest,
                condition_id=condition_id,
                checkpoint_id=checkpoint_id,
                aggregation_policy_id=manifest.aggregation_policy_id,
                aggregation_policy_version=manifest.aggregation_policy_version,
                experiment_ids=tuple(sorted(binding.experiment_id for binding in group)),
                research_execution_digests=tuple(
                    sorted(
                        {
                            binding.research_execution_digest
                            for binding in group
                            if binding.research_execution_digest is not None
                        }
                    )
                ),
                source_blocks=source_blocks,
                total_blocks=aggregate.total_blocks,
                analyzed_blocks=aggregate.analyzed_blocks,
                missing_blocks=aggregate.missing_blocks,
                excluded_contaminated=aggregate.excluded_contaminated,
                excluded_infrastructure=aggregate.excluded_infrastructure,
                metrics=metrics,
                research_controls_complete=controls.provenance_complete,
                causal_claim_permitted=(
                    _condition_is_complete_and_analyzable(aggregate) and controls.comparable
                ),
                created_at=created_at,
            )
            results.append(result)
        await _persist_results(session, tuple(results))
        return tuple(results)


def _validate_policy_results(
    *,
    study: StudyRow,
    manifest: StudyManifest,
    bindings: Sequence[StudyExperimentRow],
    blocks_by_experiment: Mapping[str, Sequence[ExperimentBlockRow]],
    results: tuple[StudyResultRecord, ...],
    research_controls_complete: bool,
    research_controls_comparable: bool,
) -> None:
    expected_groups: dict[tuple[str, str], list[StudyExperimentRow]] = {}
    for binding in bindings:
        expected_groups.setdefault((binding.condition_id, binding.checkpoint_id), []).append(
            binding
        )
    actual_keys = [(result.condition_id, result.checkpoint_id) for result in results]
    if len(actual_keys) != len(set(actual_keys)):
        raise ValueError("study aggregation policy returned duplicate condition results")
    if set(actual_keys) != set(expected_groups):
        raise ValueError("study aggregation policy omitted or substituted a condition result")

    for result in results:
        key = (result.condition_id, result.checkpoint_id)
        group = expected_groups[key]
        expected_experiments = tuple(sorted(binding.experiment_id for binding in group))
        expected_executions = tuple(
            sorted(
                {
                    binding.research_execution_digest
                    for binding in group
                    if binding.research_execution_digest is not None
                }
            )
        )
        expected_blocks = {
            (block.experiment_id, block.block_id, _block_digest(block))
            for binding in group
            for block in blocks_by_experiment[binding.experiment_id]
        }
        actual_blocks = {
            (block.experiment_id, block.block_id, block.block_digest)
            for block in result.source_blocks
        }
        if (
            result.study_id != study.study_id
            or result.study_manifest_digest != study.manifest_digest
            or result.suite_manifest_digest != study.suite_manifest_digest
            or result.aggregation_policy_id != manifest.aggregation_policy_id
            or result.aggregation_policy_version != manifest.aggregation_policy_version
        ):
            raise ValueError("study aggregation policy changed immutable study coordinates")
        if result.experiment_ids != expected_experiments:
            raise ValueError("study aggregation policy omitted or substituted an experiment")
        if result.research_execution_digests != expected_executions:
            raise ValueError("study aggregation policy changed research execution evidence")
        if result.research_controls_complete != research_controls_complete:
            raise ValueError("study aggregation policy misstated research-control completeness")
        if result.causal_claim_permitted and not research_controls_comparable:
            raise ValueError("study aggregation policy bypassed research-control comparability")
        if actual_blocks != expected_blocks or len(actual_blocks) != len(expected_blocks):
            raise ValueError("study aggregation policy omitted or substituted source blocks")


async def _persist_results(session: AsyncSession, results: tuple[StudyResultRecord, ...]) -> None:
    for result in results:
        payload = result.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(StudyResultRow, result.result_id)
        if existing is not None:
            if existing.record_digest != digest or existing.record_json != payload:
                raise ValueError("study result ID conflicts with immutable content")
            continue
        session.add(
            StudyResultRow(
                result_id=result.result_id,
                study_id=result.study_id,
                study_manifest_digest=result.study_manifest_digest,
                suite_manifest_digest=result.suite_manifest_digest,
                condition_id=result.condition_id,
                checkpoint_id=result.checkpoint_id,
                record_digest=digest,
                record_json=payload,
                created_at=result.created_at,
            )
        )
    await session.flush()


def _experiment_count(bindings: Sequence[StudyExperimentRow], condition_id: str) -> int:
    return sum(binding.condition_id == condition_id for binding in bindings)


def _condition_is_complete_and_analyzable(condition: ConditionAggregate) -> bool:
    return (
        condition.total_blocks > 0
        and condition.analyzed_blocks == condition.total_blocks
        and condition.missing_blocks == 0
        and condition.excluded_contaminated == 0
        and condition.excluded_infrastructure == 0
    )


def _block_digest(block: ExperimentBlockRow) -> str:
    return sha256_digest(
        {
            "block_id": block.block_id,
            "experiment_id": block.experiment_id,
            "block_index": block.block_index,
            "assignment": block.assignment,
            "outcomes": block.outcomes,
            "contamination_detected": block.contamination_detected,
            "infrastructure_failure": block.infrastructure_failure,
        }
    )


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
