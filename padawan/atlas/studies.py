from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Annotated, Literal

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasRunManifest,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    CampaignExecutionBinding,
    EvaluationClass,
    SuiteStatus,
    TrialAllocation,
    TrialStatus,
)
from padawan.atlas.registry import CapabilityAtlasRegistry
from padawan.models.contracts import ArtifactRef, NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    ResearchExecutionManifest,
    StudyBlockEvidence,
    StudyResultMetric,
    StudyResultRecord,
)
from padawan.models.tables import (
    ArtifactRow,
    AtlasAllocationRow,
    AtlasCampaignExecutionBindingRow,
    AtlasCampaignRow,
    AtlasCampaignSuiteConditionRow,
    AtlasCampaignSuiteRow,
    AtlasItemRow,
    AtlasRunManifestRow,
    AtlasSuiteItemRow,
    AtlasSuiteRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    ExperimentBlockRow,
    ExperimentRow,
    ResearchExecutionRow,
    StudyExperimentRow,
    VerifierResultRow,
)
from padawan.studies.engine import StudySealingContext

ATLAS_FIXED_TRIALS_POLICY_ID = "padawan.atlas.fixed_trials.v1"
ATLAS_FIXED_TRIALS_POLICY_VERSION = "1.0.0"

_ANALYZED_STATUSES = {
    TrialStatus.VERIFIED_SUCCESS,
    TrialStatus.VERIFIED_FAILURE,
    TrialStatus.PARTIAL,
    TrialStatus.ABSTAINED,
    TrialStatus.MALFORMED,
}
_INFRASTRUCTURE_STATUSES = {
    TrialStatus.TIMEOUT,
    TrialStatus.INFRASTRUCTURE_FAILURE,
    TrialStatus.NOT_RUN,
}
_MISSING_STATUSES = {
    TrialStatus.UNSCORABLE,
    TrialStatus.VERIFIER_FAILURE,
    TrialStatus.PARSER_FAILURE,
}


class AtlasFixedTrialDesign(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["atlas_fixed_trials"] = "atlas_fixed_trials"
    campaign_digest: Sha256
    suite_digest: Sha256
    evaluation_suite_manifest_digest: Sha256
    condition_id: NonEmpty
    checkpoint_id: NonEmpty
    research_execution_digest: Sha256
    item_digests: tuple[Sha256, ...]
    trials_per_item: Annotated[int, Field(gt=0)]
    planned_coordinates_digest: Sha256
    treatment_condition: NonEmpty
    control_condition: NonEmpty
    design_digest: Sha256

    @model_validator(mode="after")
    def content_is_predeclared(self) -> AtlasFixedTrialDesign:
        if not self.item_digests:
            raise ValueError("Atlas fixed-trial design requires frozen suite items")
        if tuple(sorted(self.item_digests)) != self.item_digests:
            raise ValueError("Atlas fixed-trial items must be unique and canonical")
        if len(self.item_digests) != len(set(self.item_digests)):
            raise ValueError("Atlas fixed-trial items must be unique and canonical")
        coordinates = _coordinates(self.item_digests, self.trials_per_item)
        if self.planned_coordinates_digest != sha256_digest(coordinates):
            raise ValueError("Atlas planned-coordinate digest is invalid")
        identity = self.model_dump(mode="json", exclude={"design_digest"})
        if self.design_digest != sha256_digest(identity):
            raise ValueError("Atlas fixed-trial design digest is invalid")
        return self


class AtlasFixedTrialAssignment(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["atlas_fixed_trial"] = "atlas_fixed_trial"
    campaign_digest: Sha256
    suite_digest: Sha256
    evaluation_suite_manifest_digest: Sha256
    condition_id: NonEmpty
    checkpoint_id: NonEmpty
    research_execution_digest: Sha256
    item_digest: Sha256
    trial_index: Annotated[int, Field(ge=0)]
    coordinate_digest: Sha256

    @model_validator(mode="after")
    def coordinate_is_bound(self) -> AtlasFixedTrialAssignment:
        identity = self.model_dump(mode="json", exclude={"coordinate_digest"})
        if self.coordinate_digest != sha256_digest(identity):
            raise ValueError("Atlas fixed-trial coordinate digest is invalid")
        return self


class AtlasFixedTrialOutcome(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    kind: Literal["atlas_fixed_trial_result"] = "atlas_fixed_trial_result"
    result_id: NonEmpty
    result_digest: Sha256
    request_id: NonEmpty
    request_digest: Sha256
    status: TrialStatus
    outcome_digest: Sha256

    @model_validator(mode="after")
    def result_is_bound(self) -> AtlasFixedTrialOutcome:
        identity = self.model_dump(mode="json", exclude={"outcome_digest"})
        if self.outcome_digest != sha256_digest(identity):
            raise ValueError("Atlas fixed-trial outcome digest is invalid")
        return self


@dataclass(frozen=True)
class AtlasStudyExperiment:
    experiment_id: str
    block_ids: tuple[str, ...]
    design_digest: str


@dataclass(frozen=True)
class _PromotionContext:
    campaign: AtlasCampaignManifest
    suite: AtlasSuiteManifest
    item_digests: tuple[str, ...]
    trials_per_item: int
    execution: ResearchExecutionManifest


class AtlasFixedTrialStudyBridge:
    """Bridge frozen Atlas trial coordinates into the authoritative Study ledger."""

    def __init__(self, *, artifacts: AtlasArtifactBoundary | None = None) -> None:
        self._atlas = CapabilityAtlasRegistry(artifacts=artifacts)

    async def create_experiment(
        self,
        session: AsyncSession,
        *,
        experiment_id: str,
        parent_state_id: str,
        campaign_digest: str,
        suite_digest: str,
        condition_id: str,
        research_execution_digest: str,
        created_at: datetime | None = None,
    ) -> AtlasStudyExperiment:
        promotion = await _load_promotion_context(
            session,
            campaign_digest=campaign_digest,
            suite_digest=suite_digest,
            condition_id=condition_id,
            research_execution_digest=research_execution_digest,
        )
        execution = promotion.execution
        if execution.parent_state.state_id != parent_state_id:
            raise ValueError("Atlas study parent state differs from research execution")
        treatment = execution.harness_parameters.get("treatment_condition")
        control = execution.harness_parameters.get("control_condition")
        if not isinstance(treatment, str) or not treatment:
            raise ValueError("Atlas research execution lacks a treatment condition")
        if not isinstance(control, str) or not control:
            raise ValueError("Atlas research execution lacks a control condition")
        evaluation_suite_digest = promotion.suite.evaluation_suite_manifest_digest
        assert evaluation_suite_digest is not None
        design = _design(
            campaign_digest=campaign_digest,
            suite_digest=suite_digest,
            evaluation_suite_manifest_digest=evaluation_suite_digest,
            condition_id=condition_id,
            checkpoint_id=execution.student_model.checkpoint.component_id,
            research_execution_digest=research_execution_digest,
            item_digests=promotion.item_digests,
            trials_per_item=promotion.trials_per_item,
            treatment_condition=treatment,
            control_condition=control,
        )
        assignments = tuple(
            _assignment(design, item_digest=item_digest, trial_index=trial_index)
            for item_digest, trial_index in _coordinates(
                design.item_digests, design.trials_per_item
            )
        )
        block_ids = tuple(_block_id(experiment_id, assignment) for assignment in assignments)
        existing = await session.get(ExperimentRow, experiment_id)
        if existing is not None:
            if (
                existing.parent_state_id != parent_state_id
                or existing.research_execution_digest != research_execution_digest
                or existing.seed != execution.seed
                or existing.design != design.model_dump(mode="json")
            ):
                raise ValueError("Atlas study experiment replay has a different design")
            rows = tuple(
                (
                    await session.scalars(
                        select(ExperimentBlockRow)
                        .where(ExperimentBlockRow.experiment_id == experiment_id)
                        .order_by(ExperimentBlockRow.block_index)
                    )
                ).all()
            )
            if len(rows) != len(assignments) or any(
                row.block_id != block_id
                or row.block_index != index
                or row.assignment != assignment.model_dump(mode="json")
                for index, (row, block_id, assignment) in enumerate(
                    zip(rows, block_ids, assignments, strict=True)
                )
            ):
                raise ValueError("Atlas study experiment replay has substituted fixed blocks")
            return AtlasStudyExperiment(experiment_id, block_ids, design.design_digest)

        session.add(
            ExperimentRow(
                experiment_id=experiment_id,
                parent_state_id=parent_state_id,
                research_execution_digest=research_execution_digest,
                seed=execution.seed,
                design=design.model_dump(mode="json"),
                status="active",
                created_at=created_at or datetime.now(UTC),
                completed_at=None,
            )
        )
        await session.flush()
        session.add_all(
            ExperimentBlockRow(
                block_id=block_id,
                experiment_id=experiment_id,
                block_index=index,
                assignment=assignment.model_dump(mode="json"),
                outcomes=None,
                contamination_detected=False,
                infrastructure_failure=False,
            )
            for index, (block_id, assignment) in enumerate(zip(block_ids, assignments, strict=True))
        )
        await session.flush()
        return AtlasStudyExperiment(experiment_id, block_ids, design.design_digest)

    async def record_result(
        self,
        session: AsyncSession,
        *,
        block_id: str,
        result_digest: str,
    ) -> None:
        block = await session.scalar(
            select(ExperimentBlockRow)
            .where(ExperimentBlockRow.block_id == block_id)
            .with_for_update()
        )
        if block is None:
            raise KeyError(block_id)
        experiment = await session.get(ExperimentRow, block.experiment_id)
        if experiment is None:
            raise ValueError("Atlas study block has no experiment")
        design = AtlasFixedTrialDesign.model_validate(experiment.design, strict=False)
        assignment = AtlasFixedTrialAssignment.model_validate(block.assignment, strict=False)
        _require_assignment_in_design(assignment, design)
        result_row = await session.scalar(
            select(AtlasTrialResultRow).where(AtlasTrialResultRow.result_digest == result_digest)
        )
        if result_row is None:
            raise KeyError(result_digest)
        result, request = await _validated_result_request(session, result_row)
        await self._atlas.validate_trial_artifacts(session, result_id=result.result_id)
        _require_trial_matches_assignment(result, request, assignment)
        await _require_latest_attempt(session, request)
        outcome = _outcome(result)
        payload = outcome.model_dump(mode="json")
        contaminated = result.status == TrialStatus.CONTAMINATED
        infrastructure = result.status in _INFRASTRUCTURE_STATUSES
        if block.outcomes is not None:
            if (
                block.outcomes != payload
                or block.contamination_detected != contaminated
                or block.infrastructure_failure != infrastructure
            ):
                raise ValueError("Atlas study block outcome conflicts with immutable result")
            return
        block.outcomes = payload
        block.contamination_detected = contaminated
        block.infrastructure_failure = infrastructure
        await session.flush()


class AtlasFixedTrialsStudyPolicy:
    """Seal complete, non-adaptive promotion suites from immutable Atlas evidence."""

    policy_id = ATLAS_FIXED_TRIALS_POLICY_ID
    policy_version = ATLAS_FIXED_TRIALS_POLICY_VERSION

    def __init__(self, *, artifacts: AtlasArtifactBoundary | None = None) -> None:
        self._atlas = CapabilityAtlasRegistry(artifacts=artifacts)

    async def seal_results(
        self,
        session: AsyncSession,
        context: StudySealingContext,
    ) -> tuple[StudyResultRecord, ...]:
        grouped: dict[tuple[str, str], list[StudyExperimentRow]] = {}
        for binding in context.bindings:
            grouped.setdefault((binding.condition_id, binding.checkpoint_id), []).append(binding)
        sealed: list[StudyResultRecord] = []
        used_result_digests: set[str] = set()
        for (condition_id, checkpoint_id), bindings in sorted(grouped.items()):
            if len(bindings) != 1:
                raise ValueError(
                    "Atlas fixed-trial policy permits one predeclared experiment per condition"
                )
            binding = bindings[0]
            experiment = await session.get(ExperimentRow, binding.experiment_id)
            if experiment is None:
                raise ValueError("Atlas study experiment is missing")
            design = AtlasFixedTrialDesign.model_validate(experiment.design, strict=False)
            if (
                design.condition_id != condition_id
                or design.checkpoint_id != checkpoint_id
                or design.research_execution_digest != binding.research_execution_digest
                or design.evaluation_suite_manifest_digest != context.manifest.suite_manifest_digest
                or binding.suite_manifest_digest != design.evaluation_suite_manifest_digest
            ):
                raise ValueError("Atlas experiment differs from exact Study coordinates")
            promotion = await _load_promotion_context(
                session,
                campaign_digest=design.campaign_digest,
                suite_digest=design.suite_digest,
                condition_id=design.condition_id,
                research_execution_digest=design.research_execution_digest,
            )
            if (
                promotion.suite.evaluation_suite_manifest_digest
                != design.evaluation_suite_manifest_digest
                or promotion.item_digests != design.item_digests
                or promotion.trials_per_item != design.trials_per_item
                or promotion.execution.student_model.checkpoint.component_id != design.checkpoint_id
            ):
                raise ValueError("Atlas experiment design substitutes promotion evidence")
            blocks = context.blocks_by_experiment.get(binding.experiment_id, ())
            expected = set(_coordinates(design.item_digests, design.trials_per_item))
            if len(blocks) != len(expected):
                raise ValueError("Atlas promotion requires every fixed suite item and trial")
            outcomes: list[AtlasTrialResult] = []
            actual: set[tuple[str, int]] = set()
            for block in blocks:
                assignment = AtlasFixedTrialAssignment.model_validate(
                    block.assignment, strict=False
                )
                _require_assignment_in_design(assignment, design)
                coordinate = (assignment.item_digest, assignment.trial_index)
                if coordinate in actual:
                    raise ValueError("Atlas fixed-trial blocks contain a duplicate coordinate")
                actual.add(coordinate)
                if block.outcomes is None:
                    raise ValueError("Atlas promotion has an unaccounted fixed trial")
                outcome = AtlasFixedTrialOutcome.model_validate(block.outcomes, strict=False)
                result_row = await session.scalar(
                    select(AtlasTrialResultRow).where(
                        AtlasTrialResultRow.result_digest == outcome.result_digest
                    )
                )
                if result_row is None:
                    raise ValueError("Atlas block cites a missing trial result")
                result, request = await _validated_result_request(session, result_row)
                await self._atlas.validate_trial_artifacts(session, result_id=result.result_id)
                if (
                    outcome != _outcome(result)
                    or outcome.result_id != result_row.result_id
                    or outcome.request_id != result_row.request_id
                ):
                    raise ValueError("Atlas block outcome differs from immutable trial result")
                _require_trial_matches_assignment(result, request, assignment)
                await _require_latest_attempt(session, request)
                if result.result_digest in used_result_digests:
                    raise ValueError("Atlas trial result cannot inflate multiple fixed blocks")
                used_result_digests.add(result.result_digest)
                if block.contamination_detected != (
                    result.status == TrialStatus.CONTAMINATED
                ) or block.infrastructure_failure != (result.status in _INFRASTRUCTURE_STATUSES):
                    raise ValueError("Atlas block exclusion flags differ from trial status")
                outcomes.append(result)
            if actual != expected:
                raise ValueError("Atlas promotion omitted or substituted fixed trial coordinates")
            sealed.append(
                _study_result(
                    context=context,
                    condition_id=condition_id,
                    checkpoint_id=checkpoint_id,
                    binding=binding,
                    blocks=blocks,
                    outcomes=outcomes,
                )
            )
        return tuple(sealed)


async def _load_promotion_context(
    session: AsyncSession,
    *,
    campaign_digest: str,
    suite_digest: str,
    condition_id: str,
    research_execution_digest: str,
) -> _PromotionContext:
    campaign_row = await session.get(AtlasCampaignRow, campaign_digest)
    if campaign_row is None:
        raise ValueError("Atlas promotion campaign is not registered")
    if campaign_row.record_digest != sha256_digest(campaign_row.record_json):
        raise ValueError("Atlas promotion campaign record digest is invalid")
    campaign = AtlasCampaignManifest.model_validate(campaign_row.record_json, strict=False)
    if (
        campaign.manifest_digest != campaign_digest
        or campaign_row.campaign_id != campaign.campaign_id
        or campaign_row.version != campaign.version
        or campaign_row.status != campaign.status.value
        or campaign_row.ontology_digest != campaign.ontology_digest
    ):
        raise ValueError("Atlas promotion campaign columns disagree with immutable content")
    if suite_digest not in campaign.promotion_suite_digests:
        raise ValueError("only a campaign's sealed promotion partition may enter a Study")
    if suite_digest in (
        set(campaign.adaptive_suite_digests) | set(campaign.training_candidate_suite_digests)
    ):
        raise ValueError("adaptive/development/training suites cannot enter promotion")

    suite_row = await session.get(AtlasSuiteRow, suite_digest)
    if suite_row is None or suite_row.record_digest != sha256_digest(suite_row.record_json):
        raise ValueError("Atlas promotion suite is missing or has an invalid record digest")
    suite = AtlasSuiteManifest.model_validate(suite_row.record_json, strict=False)
    if (
        suite.content_digest != suite_digest
        or suite.status != SuiteStatus.SEALED
        or suite.evaluation_class != EvaluationClass.SEALED_PROMOTION
        or suite_row.status != suite.status.value
        or suite_row.evaluation_class != suite.evaluation_class.value
        or suite_row.item_count != len(suite.item_digests)
        or suite_row.evaluation_suite_manifest_digest != suite.evaluation_suite_manifest_digest
    ):
        raise ValueError("Atlas promotion requires an exact sealed-promotion suite")

    campaign_binding = await session.get(AtlasCampaignSuiteRow, (campaign_digest, suite_digest))
    declared_binding = next(
        (value for value in campaign.suite_bindings if value.suite_digest == suite_digest),
        None,
    )
    if campaign_binding is None or declared_binding is None:
        raise ValueError("Atlas promotion suite lacks a campaign binding")
    if (
        campaign_binding.evaluation_class != EvaluationClass.SEALED_PROMOTION.value
        or campaign_binding.adaptive
        or declared_binding.evaluation_class != EvaluationClass.SEALED_PROMOTION
        or declared_binding.adaptive
        or campaign_binding.planned_item_count != len(suite.item_digests)
        or declared_binding.planned_item_count != len(suite.item_digests)
        or campaign_binding.trials_per_item != declared_binding.trials_per_item
        or condition_id not in declared_binding.condition_ids
    ):
        raise ValueError("Atlas promotion campaign binding is not a full fixed design")
    suite_condition = await session.get(
        AtlasCampaignSuiteConditionRow,
        (campaign_digest, suite_digest, condition_id),
    )
    if suite_condition is None:
        raise ValueError("Atlas promotion condition is not bound to the suite")

    item_rows = tuple(
        (
            await session.scalars(
                select(AtlasSuiteItemRow)
                .where(AtlasSuiteItemRow.suite_digest == suite_digest)
                .order_by(AtlasSuiteItemRow.position)
            )
        ).all()
    )
    item_digests = tuple(row.item_digest for row in item_rows)
    if item_digests != suite.item_digests or tuple(row.position for row in item_rows) != tuple(
        range(len(item_rows))
    ):
        raise ValueError("Atlas promotion suite items differ from its frozen manifest")

    execution_row = await session.get(ResearchExecutionRow, research_execution_digest)
    if execution_row is None or execution_row.execution_digest != sha256_digest(
        execution_row.record_json
    ):
        raise ValueError("Atlas promotion research execution is missing or invalid")
    execution = ResearchExecutionManifest.model_validate(execution_row.record_json, strict=False)
    condition = next(
        (value for value in campaign.conditions if value.condition_id == condition_id), None
    )
    if condition is None or condition.required_execution_digest not in {
        None,
        research_execution_digest,
    }:
        raise ValueError("Atlas promotion condition differs from research execution")
    execution_binding_row = await session.scalar(
        select(AtlasCampaignExecutionBindingRow).where(
            AtlasCampaignExecutionBindingRow.campaign_digest == campaign_digest,
            AtlasCampaignExecutionBindingRow.suite_digest == suite_digest,
            AtlasCampaignExecutionBindingRow.condition_id == condition_id,
            AtlasCampaignExecutionBindingRow.research_execution_digest == research_execution_digest,
        )
    )
    if execution_binding_row is None:
        raise ValueError("Atlas promotion lacks an exact campaign execution binding")
    execution_binding = CampaignExecutionBinding.model_validate(
        execution_binding_row.record_json, strict=False
    )
    if (
        execution_binding.binding_digest != execution_binding_row.binding_digest
        or execution_binding.campaign_digest != campaign_digest
        or execution_binding.suite_digest != suite_digest
        or execution_binding.condition_id != condition_id
        or execution_binding.research_execution_digest != research_execution_digest
        or execution_binding.harness_profile_digest != execution_row.harness_profile_digest
    ):
        raise ValueError("Atlas promotion execution binding has drifted")
    if (
        execution.task.task_manifest_digest not in suite.task_manifest_digests
        or execution.task.corpus_digest not in suite.corpus_digests
        or execution.environment_fingerprint not in suite.environment_fingerprints
    ):
        raise ValueError("Atlas promotion execution is outside the frozen suite")
    return _PromotionContext(
        campaign=campaign,
        suite=suite,
        item_digests=item_digests,
        trials_per_item=campaign_binding.trials_per_item,
        execution=execution,
    )


async def _validated_result_request(
    session: AsyncSession, result_row: AtlasTrialResultRow
) -> tuple[AtlasTrialResult, AtlasTrialRequest]:
    if result_row.record_digest != sha256_digest(result_row.record_json):
        raise ValueError("Atlas trial-result record digest is invalid")
    result = AtlasTrialResult.model_validate(result_row.record_json, strict=False)
    if (
        result_row.result_id != result.result_id
        or result_row.request_id != result.request_id
        or result_row.research_execution_digest != result.research_execution_digest
        or result_row.status != result.status.value
        or result_row.success != result.success
        or result_row.score != result.score
        or result_row.result_digest != result.result_digest
        or result_row.response_artifact_id
        != (result.response_artifact.artifact_id if result.response_artifact else None)
        or not _same_timestamp(result_row.completed_at, result.completed_at)
    ):
        raise ValueError("Atlas trial-result columns disagree with immutable content")
    request_row = await session.get(AtlasTrialRequestRow, result.request_id)
    if request_row is None or request_row.record_digest != sha256_digest(request_row.record_json):
        raise ValueError("Atlas trial request is missing or has an invalid digest")
    request = AtlasTrialRequest.model_validate(request_row.record_json, strict=False)
    if (
        request_row.request_id != request.request_id
        or request_row.request_digest != request.request_digest
        or request_row.allocation_id != request.allocation_id
        or request_row.campaign_digest != request.campaign_digest
        or request_row.condition_id != request.condition_id
        or request_row.suite_digest != request.suite_digest
        or request_row.item_digest != request.item_digest
        or request_row.trial_index != request.trial_index
        or request_row.attempt_index != request.attempt_index
        or request_row.research_execution_digest != request.research_execution_digest
    ):
        raise ValueError("Atlas trial-request columns disagree with immutable content")
    item_row = await session.get(AtlasItemRow, request.item_digest)
    if item_row is None or item_row.record_digest != sha256_digest(item_row.record_json):
        raise ValueError("Atlas trial request item is missing or invalid")
    item = AtlasItemManifest.model_validate(item_row.record_json, strict=False)
    if (
        item_row.item_digest != item.item_digest
        or item_row.item_id != item.item_id
        or item_row.prompt_digest != item.prompt_digest
        or request.item_id != item.item_id
        or request.prompt_digest != item.prompt_digest
    ):
        raise ValueError("Atlas trial request substitutes frozen item content")
    run_row = await session.scalar(
        select(AtlasRunManifestRow).where(AtlasRunManifestRow.run_id == request.run_id)
    )
    if run_row is None:
        raise ValueError("Atlas trial request has no immutable run envelope")
    run = AtlasRunManifest.model_validate(run_row.record_json, strict=False)
    if (
        run_row.manifest_digest != run.manifest_digest
        or run_row.run_manifest_id != run.run_manifest_id
        or run_row.run_id != run.run_id
        or run_row.campaign_digest != run.campaign_digest
        or run_row.condition_id != run.condition_id
        or run_row.suite_digest != run.suite_digest
        or run_row.research_execution_digest != run.research_execution_digest
        or run_row.evaluation_class != run.evaluation_class.value
        or run_row.adaptive != run.adaptive
        or run_row.binding_digest != run.campaign_execution_binding_digest
        or run_row.harness_profile_digest != run.harness_profile_digest
        or run_row.planned_request_count != run.planned_request_count
        or run.evaluation_class != EvaluationClass.SEALED_PROMOTION
        or run.adaptive
        or request.request_digest not in set(run.predeclared_request_digests)
        or (
            run.campaign_digest,
            run.condition_id,
            run.suite_digest,
            run.research_execution_digest,
        )
        != (
            request.campaign_digest,
            request.condition_id,
            request.suite_digest,
            request.research_execution_digest,
        )
    ):
        raise ValueError("Atlas trial request differs from its fixed promotion run envelope")
    if (
        result.request_digest != request.request_digest
        or result.research_execution_digest != request.research_execution_digest
        or result.retry_count != request.attempt_index
    ):
        raise ValueError("Atlas trial result differs from its exact request attempt")
    if result.response_artifact is not None:
        await _require_artifact(session, result.response_artifact)
        if result.response_digest != result.response_artifact.digest:
            raise ValueError("Atlas response digest differs from retained artifact")
    for artifact in result.grader_artifacts:
        await _require_artifact(session, artifact)
    for evidence in result.verifier_evidence:
        verifier = await session.get(VerifierResultRow, evidence.evidence_id)
        if verifier is None or (
            verifier.verifier_id != evidence.verifier_id
            or verifier.verifier_version != evidence.verifier_version
            or verifier.disposition != evidence.disposition
            or verifier.deterministic != evidence.deterministic
            or verifier.record_digest != evidence.evidence_digest
            or verifier.record_digest != sha256_digest(verifier.record_json)
        ):
            raise ValueError("Atlas trial result verifier evidence is missing or invalid")
        for artifact in evidence.artifact_refs:
            await _require_artifact(session, artifact)
    allocation_row = await session.get(AtlasAllocationRow, request.allocation_id)
    if allocation_row is None or allocation_row.record_digest != sha256_digest(
        allocation_row.record_json
    ):
        raise ValueError("Atlas trial allocation is missing or invalid")
    allocation = TrialAllocation.model_validate(allocation_row.record_json, strict=False)
    if (
        allocation_row.allocation_id != allocation.allocation_id
        or allocation_row.campaign_digest != allocation.campaign_digest
        or allocation_row.condition_id != allocation.condition_id
        or allocation_row.suite_digest != allocation.suite_digest
        or allocation_row.item_digest != allocation.item_digest
        or allocation_row.trial_index != allocation.trial_index
        or (
            allocation.campaign_digest,
            allocation.condition_id,
            allocation.suite_digest,
            allocation.item_digest,
            allocation.trial_index,
        )
        != (
            request.campaign_digest,
            request.condition_id,
            request.suite_digest,
            request.item_digest,
            request.trial_index,
        )
    ):
        raise ValueError("Atlas trial request differs from its predeclared allocation")
    if allocation.prior_result_digests:
        raise ValueError("fixed promotion allocations cannot depend on prior candidate results")
    return result, request


async def _require_artifact(session: AsyncSession, reference: ArtifactRef) -> None:
    row = await session.get(ArtifactRow, reference.artifact_id)
    if row is None or (
        row.digest,
        row.uri,
        row.media_type,
        row.size_bytes,
        row.restricted,
        row.raw_data,
    ) != (
        reference.digest,
        reference.uri,
        reference.media_type,
        reference.size_bytes,
        reference.restricted,
        reference.raw_data,
    ):
        raise ValueError("Atlas trial artifact evidence is missing or invalid")


async def _require_latest_attempt(session: AsyncSession, request: AtlasTrialRequest) -> None:
    latest = await session.scalar(
        select(AtlasTrialRequestRow)
        .where(AtlasTrialRequestRow.allocation_id == request.allocation_id)
        .order_by(AtlasTrialRequestRow.attempt_index.desc())
        .limit(1)
    )
    if latest is None or latest.request_id != request.request_id:
        raise ValueError("Atlas block must cite the latest declared retry attempt")


def _require_trial_matches_assignment(
    result: AtlasTrialResult,
    request: AtlasTrialRequest,
    assignment: AtlasFixedTrialAssignment,
) -> None:
    if (
        request.campaign_digest,
        request.condition_id,
        request.suite_digest,
        request.item_digest,
        request.trial_index,
        request.research_execution_digest,
    ) != (
        assignment.campaign_digest,
        assignment.condition_id,
        assignment.suite_digest,
        assignment.item_digest,
        assignment.trial_index,
        assignment.research_execution_digest,
    ) or result.research_execution_digest != assignment.research_execution_digest:
        raise ValueError("Atlas result substitutes a fixed trial coordinate or execution")


def _require_assignment_in_design(
    assignment: AtlasFixedTrialAssignment, design: AtlasFixedTrialDesign
) -> None:
    if (
        assignment.campaign_digest,
        assignment.suite_digest,
        assignment.evaluation_suite_manifest_digest,
        assignment.condition_id,
        assignment.checkpoint_id,
        assignment.research_execution_digest,
    ) != (
        design.campaign_digest,
        design.suite_digest,
        design.evaluation_suite_manifest_digest,
        design.condition_id,
        design.checkpoint_id,
        design.research_execution_digest,
    ) or (
        assignment.item_digest,
        assignment.trial_index,
    ) not in set(_coordinates(design.item_digests, design.trials_per_item)):
        raise ValueError("Atlas fixed-trial assignment is outside its predeclared design")


def _study_result(
    *,
    context: StudySealingContext,
    condition_id: str,
    checkpoint_id: str,
    binding: StudyExperimentRow,
    blocks: Sequence[ExperimentBlockRow],
    outcomes: Sequence[AtlasTrialResult],
) -> StudyResultRecord:
    analyzed = [result for result in outcomes if result.status in _ANALYZED_STATUSES]
    infrastructure = [result for result in outcomes if result.status in _INFRASTRUCTURE_STATUSES]
    contaminated = [result for result in outcomes if result.status == TrialStatus.CONTAMINATED]
    missing = [result for result in outcomes if result.status in _MISSING_STATUSES]
    scores = [float(result.score) for result in analyzed if result.score is not None]
    if len(scores) != len(analyzed):
        raise ValueError("analyzable Atlas trials require explicit scores")
    successes = sum(result.status == TrialStatus.VERIFIED_SUCCESS for result in analyzed)
    timeout_count = sum(result.status == TrialStatus.TIMEOUT for result in infrastructure)
    infra_failure_count = sum(
        result.status == TrialStatus.INFRASTRUCTURE_FAILURE for result in infrastructure
    )
    not_run_count = sum(result.status == TrialStatus.NOT_RUN for result in infrastructure)
    partial_count = sum(result.status == TrialStatus.PARTIAL for result in analyzed)
    total = len(outcomes)
    observed_reason = "no analyzable Atlas trial outcomes"
    metrics = tuple(
        sorted(
            (
                StudyResultMetric(metric_id="analyzed_trials", value=float(len(analyzed))),
                StudyResultMetric(metric_id="completion_rate", value=len(analyzed) / total),
                StudyResultMetric(metric_id="contaminated_trials", value=float(len(contaminated))),
                StudyResultMetric(
                    metric_id="infrastructure_failure_trials",
                    value=float(infra_failure_count),
                ),
                StudyResultMetric(
                    metric_id="mean_score",
                    value=mean(scores) if scores else None,
                    missing_reason=None if analyzed else observed_reason,
                ),
                StudyResultMetric(metric_id="missing_trials", value=float(len(missing))),
                StudyResultMetric(metric_id="not_run_trials", value=float(not_run_count)),
                StudyResultMetric(metric_id="partial_trials", value=float(partial_count)),
                StudyResultMetric(metric_id="planned_trials", value=float(total)),
                StudyResultMetric(metric_id="timeout_trials", value=float(timeout_count)),
                StudyResultMetric(
                    metric_id="verified_success_rate",
                    value=successes / len(analyzed) if analyzed else None,
                    missing_reason=None if analyzed else observed_reason,
                ),
                StudyResultMetric(
                    metric_id="verified_success_rate_fixed_denominator",
                    value=successes / total,
                ),
                StudyResultMetric(metric_id="verified_successes", value=float(successes)),
            ),
            key=lambda metric: metric.metric_id,
        )
    )
    source_blocks = tuple(
        StudyBlockEvidence(
            block_id=block.block_id,
            experiment_id=block.experiment_id,
            block_digest=_study_block_digest(block),
        )
        for block in sorted(blocks, key=lambda value: (value.experiment_id, value.block_id))
    )
    identity = {
        "study_id": context.study.study_id,
        "study_manifest_digest": context.study.manifest_digest,
        "suite_manifest_digest": context.study.suite_manifest_digest,
        "condition_id": condition_id,
        "checkpoint_id": checkpoint_id,
        "aggregation_policy_id": context.manifest.aggregation_policy_id,
        "aggregation_policy_version": context.manifest.aggregation_policy_version,
        "source_blocks": [block.model_dump(mode="json") for block in source_blocks],
        "total_blocks": total,
        "analyzed_blocks": len(analyzed),
        "missing_blocks": len(missing),
        "excluded_contaminated": len(contaminated),
        "excluded_infrastructure": len(infrastructure),
        "metrics": [metric.model_dump(mode="json") for metric in metrics],
    }
    complete = (
        len(analyzed) == total
        and partial_count == 0
        and not missing
        and not contaminated
        and not infrastructure
    )
    execution_digests = (
        (binding.research_execution_digest,)
        if binding.research_execution_digest is not None
        else ()
    )
    return StudyResultRecord(
        result_id=f"study-result-{sha256_digest(identity)[7:31]}",
        study_id=context.study.study_id,
        study_manifest_digest=context.study.manifest_digest,
        suite_manifest_digest=context.study.suite_manifest_digest,
        condition_id=condition_id,
        checkpoint_id=checkpoint_id,
        aggregation_policy_id=context.manifest.aggregation_policy_id,
        aggregation_policy_version=context.manifest.aggregation_policy_version,
        experiment_ids=(binding.experiment_id,),
        research_execution_digests=execution_digests,
        source_blocks=source_blocks,
        total_blocks=total,
        analyzed_blocks=len(analyzed),
        missing_blocks=len(missing),
        excluded_contaminated=len(contaminated),
        excluded_infrastructure=len(infrastructure),
        metrics=metrics,
        research_controls_complete=context.research_controls_complete,
        causal_claim_permitted=(
            complete and context.research_controls_complete and context.research_controls_comparable
        ),
        created_at=context.created_at,
    )


def _design(
    *,
    campaign_digest: str,
    suite_digest: str,
    evaluation_suite_manifest_digest: str,
    condition_id: str,
    checkpoint_id: str,
    research_execution_digest: str,
    item_digests: tuple[str, ...],
    trials_per_item: int,
    treatment_condition: str,
    control_condition: str,
) -> AtlasFixedTrialDesign:
    planned_coordinates_digest = sha256_digest(_coordinates(item_digests, trials_per_item))
    provisional = AtlasFixedTrialDesign.model_construct(
        campaign_digest=campaign_digest,
        suite_digest=suite_digest,
        evaluation_suite_manifest_digest=evaluation_suite_manifest_digest,
        condition_id=condition_id,
        checkpoint_id=checkpoint_id,
        research_execution_digest=research_execution_digest,
        item_digests=item_digests,
        trials_per_item=trials_per_item,
        planned_coordinates_digest=planned_coordinates_digest,
        treatment_condition=treatment_condition,
        control_condition=control_condition,
        design_digest=sha256_digest("pending"),
    )
    identity = provisional.model_dump(mode="json", exclude={"design_digest"})
    return AtlasFixedTrialDesign(
        campaign_digest=campaign_digest,
        suite_digest=suite_digest,
        evaluation_suite_manifest_digest=evaluation_suite_manifest_digest,
        condition_id=condition_id,
        checkpoint_id=checkpoint_id,
        research_execution_digest=research_execution_digest,
        item_digests=item_digests,
        trials_per_item=trials_per_item,
        planned_coordinates_digest=planned_coordinates_digest,
        treatment_condition=treatment_condition,
        control_condition=control_condition,
        design_digest=sha256_digest(identity),
    )


def _assignment(
    design: AtlasFixedTrialDesign, *, item_digest: str, trial_index: int
) -> AtlasFixedTrialAssignment:
    provisional = AtlasFixedTrialAssignment.model_construct(
        campaign_digest=design.campaign_digest,
        suite_digest=design.suite_digest,
        evaluation_suite_manifest_digest=design.evaluation_suite_manifest_digest,
        condition_id=design.condition_id,
        checkpoint_id=design.checkpoint_id,
        research_execution_digest=design.research_execution_digest,
        item_digest=item_digest,
        trial_index=trial_index,
        coordinate_digest=sha256_digest("pending"),
    )
    identity = provisional.model_dump(mode="json", exclude={"coordinate_digest"})
    return AtlasFixedTrialAssignment(
        campaign_digest=design.campaign_digest,
        suite_digest=design.suite_digest,
        evaluation_suite_manifest_digest=design.evaluation_suite_manifest_digest,
        condition_id=design.condition_id,
        checkpoint_id=design.checkpoint_id,
        research_execution_digest=design.research_execution_digest,
        item_digest=item_digest,
        trial_index=trial_index,
        coordinate_digest=sha256_digest(identity),
    )


def _outcome(result: AtlasTrialResult) -> AtlasFixedTrialOutcome:
    provisional = AtlasFixedTrialOutcome.model_construct(
        result_id=result.result_id,
        result_digest=result.result_digest,
        request_id=result.request_id,
        request_digest=result.request_digest,
        status=result.status,
        outcome_digest=sha256_digest("pending"),
    )
    identity = provisional.model_dump(mode="json", exclude={"outcome_digest"})
    return AtlasFixedTrialOutcome(
        result_id=result.result_id,
        result_digest=result.result_digest,
        request_id=result.request_id,
        request_digest=result.request_digest,
        status=result.status,
        outcome_digest=sha256_digest(identity),
    )


def _coordinates(item_digests: Sequence[str], trials_per_item: int) -> tuple[tuple[str, int], ...]:
    return tuple(
        (item_digest, trial_index)
        for item_digest in item_digests
        for trial_index in range(trials_per_item)
    )


def _block_id(experiment_id: str, assignment: AtlasFixedTrialAssignment) -> str:
    digest = sha256_digest(
        {
            "experiment_id": experiment_id,
            "coordinate_digest": assignment.coordinate_digest,
        }
    )
    return f"atlas-study-block-{digest[7:31]}"


def _study_block_digest(block: ExperimentBlockRow) -> str:
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


def _same_timestamp(left: datetime, right: datetime) -> bool:
    """Compare timestamps across SQLite's timezone-naive round trip."""

    normalized_left = left.replace(tzinfo=UTC) if left.tzinfo is None else left.astimezone(UTC)
    normalized_right = right.replace(tzinfo=UTC) if right.tzinfo is None else right.astimezone(UTC)
    return normalized_left == normalized_right
