from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import wraps
from typing import Any, Concatenate

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.information import ForensicArtifactRef
from padawan.atlas.adapters import builtin_adapter_descriptors
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.boundary import (
    BoundaryCandidate,
    DifficultyBin,
    StopDecision,
    allocate_near_boundary,
    assess_stop_rule,
    build_capability_curve,
    wilson_interval,
)
from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AtlasComparison,
    AtlasItemManifest,
    AtlasRunManifest,
    AtlasSnapshot,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    AuthorityKind,
    BenchmarkClaim,
    CampaignCondition,
    CampaignExecutionBinding,
    CapabilityCurve,
    ChallengeAdmissionDecision,
    ComparisonMetricDelta,
    DatasetGovernance,
    EvaluationClass,
    ExploratoryFailureProposal,
    ExploratoryReproduction,
    FailureCluster,
    FailureOrigin,
    MemoryInterventionEligibility,
    MetricEstimate,
    OntologyManifest,
    PhenomenonManifest,
    ProbeSetManifest,
    ReviewStatus,
    SuiteStatus,
    TokenAccounting,
    TrainingFailureEligibility,
    TrialAllocation,
    TrialStatus,
)
from padawan.atlas.harness import validate_profile_against_condition
from padawan.atlas.orchestration import FixedRunConfiguration, build_trial_request
from padawan.checkpoints.registry import CheckpointRegistry
from padawan.domains.contracts import TrainingLane, VerifierResult
from padawan.experiments.controls import ResearchControlRegistry
from padawan.models.contracts import ArtifactRef, RightsUse, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import (
    EvaluationSuiteManifest,
    HarnessProfile,
    ResearchExecutionManifest,
)
from padawan.models.tables import (
    ArtifactRow,
    AtlasAllocationRow,
    AtlasBenchmarkClaimRow,
    AtlasCampaignClaimRow,
    AtlasCampaignConditionRow,
    AtlasCampaignExecutionBindingRow,
    AtlasCampaignRow,
    AtlasCampaignSuiteConditionRow,
    AtlasCampaignSuiteRow,
    AtlasChallengeAdmissionRow,
    AtlasComparisonRow,
    AtlasDatasetGovernanceRow,
    AtlasExploratoryProposalRow,
    AtlasExploratoryReproductionResultRow,
    AtlasExploratoryReproductionRow,
    AtlasFailureClusterResultRow,
    AtlasFailureClusterRow,
    AtlasItemRow,
    AtlasMemoryEligibilityRow,
    AtlasOntologyRow,
    AtlasPhenomenonRow,
    AtlasProbeItemRow,
    AtlasProbeResultRow,
    AtlasProbeSetRow,
    AtlasRunManifestRow,
    AtlasSnapshotRow,
    AtlasSuiteItemRow,
    AtlasSuiteRow,
    AtlasTrainingEligibilityRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    Base,
    CheckpointComparisonRow,
    CheckpointDecisionRow,
    CheckpointEvaluationRow,
    EvaluationSuiteRow,
    ExternalCallRow,
    HarnessProfileRow,
    ResearchExecutionRow,
    RunRow,
    VerifierResultRow,
)


class AtlasRegistryError(ValueError):
    """An immutable Atlas record failed provenance or admission checks."""


def _atomic_atlas_write[**P, R](
    operation: Callable[Concatenate[CapabilityAtlasRegistry, AsyncSession, P], Awaitable[R]],
) -> Callable[Concatenate[CapabilityAtlasRegistry, AsyncSession, P], Awaitable[R]]:
    @wraps(operation)
    async def wrapped(
        self: CapabilityAtlasRegistry, session: AsyncSession, /, *args: P.args, **kwargs: P.kwargs
    ) -> R:
        async with session.begin_nested():
            return await operation(self, session, *args, **kwargs)

    return wrapped


def _expected_generation_provider(execution: ResearchExecutionManifest) -> str:
    provider = execution.student_model.runtime_parameters.get("provider")
    if provider is not None:
        return provider
    component = execution.student_model.serving_artifact.component_id
    if component == "inkling-small-ampere":
        return "inkling"
    if component.endswith(".responses_service") or component.endswith(".managed_service"):
        return component.split(".", maxsplit=1)[0]
    raise AtlasRegistryError("research execution does not bind a generation provider identity")


class CapabilityAtlasRegistry:
    """Persist Atlas evidence without creating a second research-control authority."""

    def __init__(self, *, artifacts: AtlasArtifactBoundary | None = None) -> None:
        self.artifacts = artifacts

    def _artifact_boundary(self) -> AtlasArtifactBoundary:
        if self.artifacts is None:
            raise AtlasRegistryError(
                "Atlas artifact evidence requires a configured forensic boundary"
            )
        return self.artifacts

    async def validate_trial_artifacts(
        self, session: AsyncSession, *, result_id: str
    ) -> tuple[ForensicArtifactRef, ...]:
        """Privileged evidence validation for downstream research; grants no model admission."""
        row = await _require(session, AtlasTrialResultRow, result_id, "trial evidence")
        result = _validated(AtlasTrialResult, row.record_json, "trial evidence")
        if (
            row.record_digest != sha256_digest(result)
            or row.result_id != result.result_id
            or row.result_digest != result.result_digest
            or row.request_id != result.request_id
            or row.research_execution_digest != result.research_execution_digest
            or row.status != result.status.value
            or row.success != result.success
            or row.score != result.score
            or row.response_artifact_id
            != (result.response_artifact.artifact_id if result.response_artifact else None)
            or not _same_timestamp(row.completed_at, result.completed_at)
        ):
            raise AtlasRegistryError("trial evidence has inconsistent source identity")
        request = await self._validate_request_artifacts(session, result.request_id)
        if (
            request.request_digest != result.request_digest
            or request.research_execution_digest != result.research_execution_digest
            or result.completed_at < request.created_at
            or result.retry_count != request.attempt_index
        ):
            raise AtlasRegistryError("trial evidence substitutes its request source")
        refs = await _result_artifacts(session, result, request)
        return await self._artifact_boundary().validate(
            session,
            owner_type="atlas_trial_result",
            owner_id=result.result_id,
            references=refs,
            recorded_at=result.completed_at,
        )

    async def _validate_request_artifacts(
        self, session: AsyncSession, request_id: str
    ) -> AtlasTrialRequest:
        row = await _require(session, AtlasTrialRequestRow, request_id, "trial request evidence")
        request = _validated(AtlasTrialRequest, row.record_json, "trial request evidence")
        if (
            row.record_digest != sha256_digest(request)
            or row.request_id != request.request_id
            or row.request_digest != request.request_digest
            or row.research_execution_digest != request.research_execution_digest
            or not _same_timestamp(row.created_at, request.created_at)
            or any(
                getattr(row, field) != getattr(request, field)
                for field in (
                    "allocation_id",
                    "parent_request_id",
                    "run_id",
                    "campaign_digest",
                    "condition_id",
                    "suite_digest",
                    "item_digest",
                    "trial_index",
                    "attempt_index",
                    "prompt_digest",
                    "tool_manifest_digest",
                    "adapter_id",
                    "adapter_version",
                )
            )
        ):
            raise AtlasRegistryError("trial request evidence has inconsistent source identity")
        await self._artifact_boundary().validate(
            session,
            owner_type="atlas_trial_request",
            owner_id=request.request_id,
            references=await _request_artifacts(session, request),
            recorded_at=request.created_at,
        )
        return request

    async def register_claim(
        self, session: AsyncSession, claim: BenchmarkClaim
    ) -> AtlasBenchmarkClaimRow:
        payload = _payload(claim)
        digest = sha256_digest(payload)
        existing = await session.get(AtlasBenchmarkClaimRow, claim.claim_id)
        if existing is not None:
            _require_same(existing.record_digest, digest, existing.record_json, payload, "claim")
            return existing

        predecessor: BenchmarkClaim | None = None
        if claim.supersedes_claim_id is not None:
            predecessor_row = await _require(
                session,
                AtlasBenchmarkClaimRow,
                claim.supersedes_claim_id,
                "superseded benchmark claim",
            )
            predecessor = _validated(BenchmarkClaim, predecessor_row.record_json, "source claim")
            if _claim_coordinates(predecessor) != _claim_coordinates(claim):
                raise AtlasRegistryError("claim corrections cannot change benchmark coordinates")
            if claim.created_at <= predecessor.created_at:
                raise AtlasRegistryError("claim correction must postdate its predecessor")
            successor = await session.scalar(
                select(AtlasBenchmarkClaimRow).where(
                    AtlasBenchmarkClaimRow.supersedes_claim_id == claim.supersedes_claim_id
                )
            )
            if successor is not None:
                raise AtlasRegistryError("a benchmark claim already has a correction successor")

        artifact_id = None
        if claim.extraction.source_artifact is not None:
            artifact = await _require_artifact(session, claim.extraction.source_artifact)
            artifact_id = artifact.artifact_id
        row = AtlasBenchmarkClaimRow(
            claim_id=claim.claim_id,
            supersedes_claim_id=claim.supersedes_claim_id,
            source_kind=claim.source_kind.value,
            source_url=claim.source_url,
            source_revision=claim.source_revision,
            model_id=claim.model_id,
            model_revision=claim.model_revision,
            benchmark_id=claim.benchmark_id,
            benchmark_version=claim.benchmark_version,
            split=claim.split,
            metric_id=claim.metric_id,
            source_artifact_id=artifact_id,
            record_digest=digest,
            record_json=payload,
            created_at=claim.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_dataset_governance(
        self, session: AsyncSession, governance: DatasetGovernance
    ) -> AtlasDatasetGovernanceRow:
        payload = _payload(governance)
        digest = sha256_digest(payload)
        existing = await session.get(AtlasDatasetGovernanceRow, governance.governance_id)
        if existing is not None:
            _require_same(
                existing.record_digest, digest, existing.record_json, payload, "dataset governance"
            )
            return existing
        owner = await session.scalar(
            select(AtlasDatasetGovernanceRow).where(
                AtlasDatasetGovernanceRow.benchmark_id == governance.benchmark_id,
                AtlasDatasetGovernanceRow.benchmark_version == governance.benchmark_version,
                AtlasDatasetGovernanceRow.dataset_revision == governance.dataset_revision,
            )
        )
        if owner is not None:
            raise AtlasRegistryError("a dataset revision cannot be registered under another record")
        row = AtlasDatasetGovernanceRow(
            governance_id=governance.governance_id,
            benchmark_id=governance.benchmark_id,
            benchmark_version=governance.benchmark_version,
            dataset_revision=governance.dataset_revision,
            access_classification=governance.access.value,
            redistribution_classification=governance.redistribution.value,
            contamination_classification=governance.contamination.value,
            evaluation_class=governance.evaluation_class.value,
            executable=governance.executable,
            rights_digest=sha256_digest(governance.rights),
            record_digest=digest,
            record_json=payload,
            reviewed_at=governance.reviewed_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_suite(
        self, session: AsyncSession, manifest: AtlasSuiteManifest
    ) -> AtlasSuiteRow:
        payload = _payload(manifest)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasSuiteRow, manifest.content_digest)
        if existing is not None:
            _require_same(
                existing.record_digest, record_digest, existing.record_json, payload, "Atlas suite"
            )
            return existing
        owner = await session.scalar(
            select(AtlasSuiteRow).where(
                AtlasSuiteRow.suite_id == manifest.suite_id,
                AtlasSuiteRow.version == manifest.version,
            )
        )
        if owner is not None:
            raise AtlasRegistryError("Atlas suite version cannot be rewritten or substituted")

        governance_row = await _require(
            session,
            AtlasDatasetGovernanceRow,
            manifest.governance_id,
            "dataset governance",
        )
        governance = _validated(DatasetGovernance, governance_row.record_json, "dataset governance")
        if (
            governance.benchmark_id != manifest.benchmark_id
            or governance.benchmark_version != manifest.benchmark_version
            or governance.evaluation_class != manifest.evaluation_class
        ):
            raise AtlasRegistryError("suite identity disagrees with dataset governance")
        if manifest.status in {SuiteStatus.READY, SuiteStatus.SEALED} and not governance.executable:
            raise AtlasRegistryError("inaccessible or unlicensed suites fail closed")

        evaluation_suite_digest = manifest.evaluation_suite_manifest_digest
        if manifest.status == SuiteStatus.SEALED:
            assert evaluation_suite_digest is not None
            core_row = await _require(
                session, EvaluationSuiteRow, evaluation_suite_digest, "core evaluation suite"
            )
            core = _validated(
                EvaluationSuiteManifest, core_row.record_json, "core evaluation suite"
            )
            if (
                sha256_digest(core_row.record_json) != evaluation_suite_digest
                or not core_row.sealed
            ):
                raise AtlasRegistryError(
                    "sealed Atlas suite cites an invalid or unsealed core suite"
                )
            if not core.sealed:
                raise AtlasRegistryError("core evaluation suite payload is not sealed")
            if tuple(sorted(core.environment_fingerprints)) != manifest.environment_fingerprints:
                raise AtlasRegistryError("Atlas and core suite environment identities differ")
            if tuple(sorted(core.task_manifest_digests)) != manifest.task_manifest_digests:
                raise AtlasRegistryError("Atlas and core suite task identities differ")

        embedded = {item.item_digest: item for item in manifest.items}
        for item_digest in manifest.item_digests:
            item = embedded.get(item_digest)
            item_row = await session.get(AtlasItemRow, item_digest)
            if item is not None:
                item_payload = _payload(item)
                item_record_digest = sha256_digest(item_payload)
                if item_row is None:
                    identity_owner = await session.scalar(
                        select(AtlasItemRow).where(AtlasItemRow.item_id == item.item_id)
                    )
                    if identity_owner is not None:
                        raise AtlasRegistryError("Atlas item ID cannot be rebound to new content")
                    item_row = AtlasItemRow(
                        item_digest=item.item_digest,
                        item_id=item.item_id,
                        family_id=item.family_id,
                        difficulty=item.difficulty,
                        adapter_kind=item.adapter_kind.value,
                        prompt_digest=item.prompt_digest,
                        record_digest=item_record_digest,
                        record_json=item_payload,
                        created_at=manifest.created_at,
                    )
                    session.add(item_row)
                    await session.flush()
                else:
                    _require_same(
                        item_row.record_digest,
                        item_record_digest,
                        item_row.record_json,
                        item_payload,
                        "Atlas item",
                    )
                if item.adapter_kind != manifest.adapter_kind:
                    raise AtlasRegistryError("suite contains an item for another adapter kind")
            elif item_row is None:
                raise AtlasRegistryError("suite item content is unavailable and fails closed")

        row = AtlasSuiteRow(
            suite_digest=manifest.content_digest,
            suite_id=manifest.suite_id,
            version=manifest.version,
            governance_id=manifest.governance_id,
            benchmark_id=manifest.benchmark_id,
            benchmark_version=manifest.benchmark_version,
            split=manifest.split,
            status=manifest.status.value,
            evaluation_class=manifest.evaluation_class.value,
            adapter_kind=manifest.adapter_kind.value,
            item_count=len(manifest.item_digests),
            evaluation_suite_manifest_digest=evaluation_suite_digest,
            record_digest=record_digest,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        session.add_all(
            AtlasSuiteItemRow(
                suite_digest=manifest.content_digest,
                item_digest=item_digest,
                position=position,
            )
            for position, item_digest in enumerate(manifest.item_digests)
        )
        await session.flush()
        return row

    async def register_ontology(
        self, session: AsyncSession, manifest: OntologyManifest
    ) -> AtlasOntologyRow:
        _require_acyclic_ontology(manifest)
        payload = _payload(manifest)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasOntologyRow, manifest.manifest_digest)
        if existing is not None:
            _require_same(
                existing.record_digest, record_digest, existing.record_json, payload, "ontology"
            )
            return existing
        owner = await session.scalar(
            select(AtlasOntologyRow).where(
                AtlasOntologyRow.ontology_id == manifest.ontology_id,
                AtlasOntologyRow.version == manifest.version,
            )
        )
        if owner is not None:
            raise AtlasRegistryError("ontology version cannot be rewritten")
        row = AtlasOntologyRow(
            ontology_digest=manifest.manifest_digest,
            ontology_id=manifest.ontology_id,
            version=manifest.version,
            record_digest=record_digest,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_campaign(
        self, session: AsyncSession, manifest: AtlasCampaignManifest
    ) -> AtlasCampaignRow:
        payload = _payload(manifest)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasCampaignRow, manifest.manifest_digest)
        if existing is not None:
            _require_same(
                existing.record_digest, record_digest, existing.record_json, payload, "campaign"
            )
            return existing
        owner = await session.scalar(
            select(AtlasCampaignRow).where(
                AtlasCampaignRow.campaign_id == manifest.campaign_id,
                AtlasCampaignRow.version == manifest.version,
            )
        )
        if owner is not None:
            raise AtlasRegistryError("campaign version cannot be rewritten")
        await _require(session, AtlasOntologyRow, manifest.ontology_digest, "campaign ontology")
        if len(manifest.source_claim_ids) != len(set(manifest.source_claim_ids)):
            raise AtlasRegistryError("campaign source claims must be unique")
        for claim_id in manifest.source_claim_ids:
            await _require(session, AtlasBenchmarkClaimRow, claim_id, "campaign source claim")

        factors = {factor.factor_id: factor for factor in manifest.factors}
        conditions = {condition.condition_id: condition for condition in manifest.conditions}
        if len(conditions) != len(manifest.conditions):
            raise AtlasRegistryError("campaign condition IDs must be unique")
        for condition in manifest.conditions:
            if set(condition.factor_levels) != set(factors):
                raise AtlasRegistryError("campaign condition must select every declared factor")
            for factor_id, level_id in condition.factor_levels.items():
                if level_id not in {level.level_id for level in factors[factor_id].levels}:
                    raise AtlasRegistryError("campaign condition selects an unknown factor level")
            if condition.required_execution_digest is not None:
                execution_row = await _require_execution(
                    session, condition.required_execution_digest
                )
                profile_row = await _require_profile(session, execution_row.harness_profile_digest)
                _validate_condition_controls(condition, execution_row, profile_row)

        suite_bindings = {binding.suite_digest: binding for binding in manifest.suite_bindings}
        if len(suite_bindings) != len(manifest.suite_bindings):
            raise AtlasRegistryError("campaign suite bindings must be unique")
        suite_items: dict[str, frozenset[str]] = {}
        for binding in manifest.suite_bindings:
            suite_row = await _require(
                session, AtlasSuiteRow, binding.suite_digest, "campaign suite"
            )
            suite = _validated(AtlasSuiteManifest, suite_row.record_json, "Atlas suite")
            suite_items[binding.suite_digest] = frozenset(suite.item_digests)
            if binding.evaluation_class != suite.evaluation_class:
                raise AtlasRegistryError("campaign suite evaluation class disagrees with suite")
            if suite.status in {SuiteStatus.READY, SuiteStatus.SEALED}:
                if binding.planned_item_count > suite_row.item_count:
                    raise AtlasRegistryError(
                        "campaign plans more items than the frozen suite contains"
                    )
                if not binding.adaptive and binding.planned_item_count != suite_row.item_count:
                    raise AtlasRegistryError("fixed campaigns cannot silently omit suite items")

        partition_items = {
            "promotion": frozenset(
                item for digest in manifest.promotion_suite_digests for item in suite_items[digest]
            ),
            "adaptive": frozenset(
                item for digest in manifest.adaptive_suite_digests for item in suite_items[digest]
            ),
            "training": frozenset(
                item
                for digest in manifest.training_candidate_suite_digests
                for item in suite_items[digest]
            ),
        }
        for left, right in (
            ("promotion", "adaptive"),
            ("promotion", "training"),
            ("adaptive", "training"),
        ):
            if partition_items[left] & partition_items[right]:
                raise AtlasRegistryError(
                    f"campaign {left} and {right} partitions share frozen item content"
                )

        row = AtlasCampaignRow(
            campaign_digest=manifest.manifest_digest,
            campaign_id=manifest.campaign_id,
            version=manifest.version,
            ontology_digest=manifest.ontology_digest,
            status=manifest.status.value,
            record_digest=record_digest,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        session.add_all(
            AtlasCampaignClaimRow(campaign_digest=manifest.manifest_digest, claim_id=claim_id)
            for claim_id in manifest.source_claim_ids
        )
        session.add_all(
            AtlasCampaignConditionRow(
                campaign_digest=manifest.manifest_digest,
                condition_id=condition.condition_id,
                required_execution_digest=condition.required_execution_digest,
                externally_gated=condition.externally_gated,
                condition_digest=sha256_digest(condition),
                record_json=_payload(condition),
            )
            for condition in manifest.conditions
        )
        session.add_all(
            AtlasCampaignSuiteRow(
                campaign_digest=manifest.manifest_digest,
                suite_digest=binding.suite_digest,
                evaluation_class=binding.evaluation_class.value,
                planned_item_count=binding.planned_item_count,
                trials_per_item=binding.trials_per_item,
                adaptive=binding.adaptive,
            )
            for binding in manifest.suite_bindings
        )
        await session.flush()
        session.add_all(
            AtlasCampaignSuiteConditionRow(
                campaign_digest=manifest.manifest_digest,
                suite_digest=binding.suite_digest,
                condition_id=condition_id,
            )
            for binding in manifest.suite_bindings
            for condition_id in binding.condition_ids
        )
        await session.flush()
        return row

    async def register_execution_binding(
        self, session: AsyncSession, binding: CampaignExecutionBinding
    ) -> AtlasCampaignExecutionBindingRow:
        payload = _payload(binding)
        existing = await session.get(AtlasCampaignExecutionBindingRow, binding.binding_digest)
        if existing is not None:
            if existing.record_json != payload:
                raise AtlasRegistryError("execution-binding digest conflicts with stored content")
            return existing
        id_owner = await session.scalar(
            select(AtlasCampaignExecutionBindingRow).where(
                AtlasCampaignExecutionBindingRow.binding_id == binding.binding_id
            )
        )
        if id_owner is not None:
            raise AtlasRegistryError("execution-binding ID cannot be rewritten")
        coordinate_owner = await session.scalar(
            select(AtlasCampaignExecutionBindingRow).where(
                AtlasCampaignExecutionBindingRow.campaign_digest == binding.campaign_digest,
                AtlasCampaignExecutionBindingRow.condition_id == binding.condition_id,
                AtlasCampaignExecutionBindingRow.research_execution_digest
                == binding.research_execution_digest,
            )
        )
        if coordinate_owner is not None:
            raise AtlasRegistryError("campaign condition execution cannot be rebound")

        campaign_row = await _require(
            session, AtlasCampaignRow, binding.campaign_digest, "execution-binding campaign"
        )
        campaign = _validated(AtlasCampaignManifest, campaign_row.record_json, "Atlas campaign")
        condition = next(
            (value for value in campaign.conditions if value.condition_id == binding.condition_id),
            None,
        )
        if condition is None:
            raise AtlasRegistryError("execution binding cites an unknown campaign condition")
        suite_condition = await session.get(
            AtlasCampaignSuiteConditionRow,
            (binding.campaign_digest, binding.suite_digest, binding.condition_id),
        )
        if suite_condition is None:
            raise AtlasRegistryError("execution binding cites an unbound suite condition")
        suite_row = await _require(
            session, AtlasSuiteRow, binding.suite_digest, "execution-binding suite"
        )
        suite = _validated(AtlasSuiteManifest, suite_row.record_json, "Atlas suite")
        execution_row = await _require_execution(session, binding.research_execution_digest)
        profile_row = await _require_profile(session, binding.harness_profile_digest)
        if execution_row.harness_profile_digest != binding.harness_profile_digest:
            raise AtlasRegistryError("execution binding harness differs from research execution")
        if binding.factor_levels != condition.factor_levels:
            raise AtlasRegistryError("execution-binding factors differ from campaign condition")
        if condition.required_execution_digest not in {
            None,
            binding.research_execution_digest,
        }:
            raise AtlasRegistryError("execution binding violates the condition execution pin")
        if condition.externally_gated and binding.external_authorization_ref is None:
            raise AtlasRegistryError("externally gated execution requires authorization evidence")
        _validate_condition_controls(condition, execution_row, profile_row)
        if execution_row.task_manifest_digest not in suite.task_manifest_digests:
            raise AtlasRegistryError("research execution task is outside the frozen Atlas suite")
        if execution_row.corpus_digest not in suite.corpus_digests:
            raise AtlasRegistryError("research execution corpus is outside the frozen Atlas suite")
        if (
            suite.environment_fingerprints
            and execution_row.environment_fingerprint not in suite.environment_fingerprints
        ):
            raise AtlasRegistryError("research execution environment is outside the frozen suite")

        row = AtlasCampaignExecutionBindingRow(
            binding_digest=binding.binding_digest,
            binding_id=binding.binding_id,
            campaign_digest=binding.campaign_digest,
            condition_id=binding.condition_id,
            suite_digest=binding.suite_digest,
            research_execution_digest=binding.research_execution_digest,
            harness_profile_digest=binding.harness_profile_digest,
            external_authorization_ref=binding.external_authorization_ref,
            record_json=payload,
            created_at=binding.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def record_allocation(
        self, session: AsyncSession, allocation: TrialAllocation
    ) -> AtlasAllocationRow:
        payload = _payload(allocation)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasAllocationRow, allocation.allocation_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "trial allocation",
            )
            return existing
        coordinate_owner = await session.scalar(
            select(AtlasAllocationRow).where(
                AtlasAllocationRow.campaign_digest == allocation.campaign_digest,
                AtlasAllocationRow.condition_id == allocation.condition_id,
                AtlasAllocationRow.suite_digest == allocation.suite_digest,
                AtlasAllocationRow.item_digest == allocation.item_digest,
                AtlasAllocationRow.trial_index == allocation.trial_index,
            )
        )
        if coordinate_owner is not None:
            raise AtlasRegistryError("a trial coordinate already has an allocation")
        suite_condition = await session.get(
            AtlasCampaignSuiteConditionRow,
            (allocation.campaign_digest, allocation.suite_digest, allocation.condition_id),
        )
        if suite_condition is None:
            raise AtlasRegistryError("allocation cites an unbound suite condition")
        suite_row = await _require(
            session, AtlasSuiteRow, allocation.suite_digest, "allocation suite"
        )
        if suite_row.status not in {SuiteStatus.READY.value, SuiteStatus.SEALED.value}:
            raise AtlasRegistryError("allocations require a frozen executable suite")
        suite_item = await session.get(
            AtlasSuiteItemRow, (allocation.suite_digest, allocation.item_digest)
        )
        if suite_item is None:
            raise AtlasRegistryError("allocation substitutes an item outside the frozen suite")
        campaign_suite = await session.get(
            AtlasCampaignSuiteRow, (allocation.campaign_digest, allocation.suite_digest)
        )
        assert campaign_suite is not None
        if allocation.trial_index >= campaign_suite.trials_per_item:
            raise AtlasRegistryError("allocation exceeds predeclared repeated trials")
        if not campaign_suite.adaptive and allocation.prior_result_digests:
            raise AtlasRegistryError("fixed allocations cannot depend on candidate results")
        if campaign_suite.adaptive:
            prior_allocation_count = await session.scalar(
                select(func.count())
                .select_from(AtlasAllocationRow)
                .where(
                    AtlasAllocationRow.campaign_digest == allocation.campaign_digest,
                    AtlasAllocationRow.condition_id == allocation.condition_id,
                    AtlasAllocationRow.suite_digest == allocation.suite_digest,
                )
            )
            if allocation.decision_sequence != int(prior_allocation_count or 0):
                raise AtlasRegistryError(
                    "adaptive allocations must use one contiguous global decision sequence"
                )
        else:
            expected_sequence = (
                suite_item.position * campaign_suite.trials_per_item + allocation.trial_index
            )
            if allocation.decision_sequence != expected_sequence:
                raise AtlasRegistryError(
                    "fixed allocation decision sequence differs from frozen suite order"
                )

        prior = tuple(allocation.prior_result_digests)
        if len(prior) != len(set(prior)) or prior != tuple(sorted(prior)):
            raise AtlasRegistryError("allocation prior results must be unique and canonical")
        if campaign_suite.adaptive:
            stored_prior = await _prior_result_digests(
                session,
                campaign_digest=allocation.campaign_digest,
                condition_id=allocation.condition_id,
                suite_digest=allocation.suite_digest,
                before_decision_sequence=allocation.decision_sequence,
            )
            if prior != stored_prior:
                raise AtlasRegistryError(
                    "adaptive allocation must disclose every available prior result"
                )
            expected_allocation = await _adaptive_expected_allocation(
                session,
                allocation=allocation,
                suite_row=suite_row,
                campaign_suite=campaign_suite,
            )
            if allocation != expected_allocation:
                raise AtlasRegistryError(
                    "adaptive allocation differs from the registered policy decision"
                )

        row = AtlasAllocationRow(
            allocation_id=allocation.allocation_id,
            campaign_digest=allocation.campaign_digest,
            condition_id=allocation.condition_id,
            suite_digest=allocation.suite_digest,
            item_digest=allocation.item_digest,
            trial_index=allocation.trial_index,
            decision_sequence=allocation.decision_sequence,
            decision_evidence_digest=allocation.decision_evidence_digest,
            record_digest=record_digest,
            record_json=payload,
            created_at=allocation.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_run_manifest(
        self, session: AsyncSession, manifest: AtlasRunManifest
    ) -> AtlasRunManifestRow:
        payload = _payload(manifest)
        existing = await session.get(AtlasRunManifestRow, manifest.manifest_digest)
        if existing is not None:
            if existing.record_json != payload:
                raise AtlasRegistryError("run manifest digest conflicts with stored content")
            return existing
        owner = await session.scalar(
            select(AtlasRunManifestRow).where(
                (AtlasRunManifestRow.run_manifest_id == manifest.run_manifest_id)
                | (AtlasRunManifestRow.run_id == manifest.run_id)
            )
        )
        if owner is not None:
            raise AtlasRegistryError("Padawan run already has an immutable Atlas envelope")
        run = await _require(session, RunRow, manifest.run_id, "Padawan run")
        if run.research_execution_digest != manifest.research_execution_digest:
            raise AtlasRegistryError("Atlas run envelope differs from Padawan run execution")
        binding = await _require(
            session,
            AtlasCampaignExecutionBindingRow,
            manifest.campaign_execution_binding_digest,
            "campaign execution binding",
        )
        if (
            binding.campaign_digest,
            binding.condition_id,
            binding.suite_digest,
            binding.research_execution_digest,
            binding.harness_profile_digest,
        ) != (
            manifest.campaign_digest,
            manifest.condition_id,
            manifest.suite_digest,
            manifest.research_execution_digest,
            manifest.harness_profile_digest,
        ):
            raise AtlasRegistryError("Atlas run envelope differs from execution binding")
        campaign_suite = await _require(
            session,
            AtlasCampaignSuiteRow,
            (manifest.campaign_digest, manifest.suite_digest),
            "campaign suite",
        )
        if (
            campaign_suite.evaluation_class != manifest.evaluation_class.value
            or campaign_suite.adaptive != manifest.adaptive
        ):
            raise AtlasRegistryError("Atlas run envelope misstates campaign suite design")
        expected_request_count = campaign_suite.planned_item_count * campaign_suite.trials_per_item
        if manifest.planned_request_count != expected_request_count:
            raise AtlasRegistryError(
                "Atlas run request count differs from the campaign suite trial design"
            )
        campaign_row = await _require(
            session, AtlasCampaignRow, manifest.campaign_digest, "Atlas run campaign"
        )
        campaign = _validated(AtlasCampaignManifest, campaign_row.record_json, "Atlas campaign")
        stop_rules = {rule.rule_id: rule for rule in campaign.stop_rules}
        if manifest.adaptive:
            rule = stop_rules.get(manifest.stop_rule_id or "")
            if rule is None:
                raise AtlasRegistryError("adaptive Atlas run cites an unknown stop rule")
        condition_row = await _require(
            session,
            AtlasCampaignConditionRow,
            (manifest.campaign_digest, manifest.condition_id),
            "campaign condition",
        )
        condition = _campaign_condition(condition_row)
        if manifest.external_execution != condition.externally_gated:
            raise AtlasRegistryError("Atlas run external flag differs from campaign condition")
        if manifest.external_execution and (
            manifest.external_authorization_ref != binding.external_authorization_ref
        ):
            raise AtlasRegistryError("Atlas run authorization differs from execution binding")
        if (
            manifest.max_input_tokens > condition.max_input_tokens
            or manifest.max_output_tokens > condition.max_output_tokens
            or manifest.max_actions > condition.max_actions
            or manifest.max_cost_usd > condition.max_cost_usd
            or manifest.planned_request_count + manifest.max_retry_requests > condition.max_requests
        ):
            raise AtlasRegistryError("Atlas run exceeds predeclared campaign ceilings")
        aggregates = (
            await session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            AtlasRunManifestRow.planned_request_count
                            + AtlasRunManifestRow.max_retry_requests
                        ),
                        0,
                    ),
                    func.coalesce(func.sum(AtlasRunManifestRow.max_input_tokens), 0),
                    func.coalesce(func.sum(AtlasRunManifestRow.max_output_tokens), 0),
                    func.coalesce(func.sum(AtlasRunManifestRow.max_actions), 0),
                    func.coalesce(func.sum(AtlasRunManifestRow.max_cost_usd), 0.0),
                ).where(
                    AtlasRunManifestRow.campaign_digest == manifest.campaign_digest,
                    AtlasRunManifestRow.condition_id == manifest.condition_id,
                )
            )
        ).one()
        aggregate_with_candidate = (
            int(aggregates[0]) + manifest.planned_request_count + manifest.max_retry_requests,
            int(aggregates[1]) + manifest.max_input_tokens,
            int(aggregates[2]) + manifest.max_output_tokens,
            int(aggregates[3]) + manifest.max_actions,
            float(aggregates[4]) + manifest.max_cost_usd,
        )
        aggregate_ceiling = (
            condition.max_requests,
            condition.max_input_tokens,
            condition.max_output_tokens,
            condition.max_actions,
            condition.max_cost_usd,
        )
        if any(
            consumed > ceiling
            for consumed, ceiling in zip(aggregate_with_candidate, aggregate_ceiling, strict=True)
        ):
            raise AtlasRegistryError("aggregate Atlas runs exceed campaign condition ceilings")
        row = AtlasRunManifestRow(
            manifest_digest=manifest.manifest_digest,
            run_manifest_id=manifest.run_manifest_id,
            run_id=manifest.run_id,
            run_kind=manifest.run_kind.value,
            binding_digest=manifest.campaign_execution_binding_digest,
            campaign_digest=manifest.campaign_digest,
            condition_id=manifest.condition_id,
            suite_digest=manifest.suite_digest,
            research_execution_digest=manifest.research_execution_digest,
            harness_profile_digest=manifest.harness_profile_digest,
            evaluation_class=manifest.evaluation_class.value,
            adaptive=manifest.adaptive,
            allocation_policy_id=manifest.allocation_policy_id,
            allocation_policy_version=manifest.allocation_policy_version,
            stop_rule_id=manifest.stop_rule_id,
            request_template_digest=manifest.request_template_digest,
            planned_request_count=manifest.planned_request_count,
            max_retry_requests=manifest.max_retry_requests,
            max_input_tokens=manifest.max_input_tokens,
            max_output_tokens=manifest.max_output_tokens,
            max_actions=manifest.max_actions,
            max_cost_usd=manifest.max_cost_usd,
            external_execution=manifest.external_execution,
            external_authorization_ref=manifest.external_authorization_ref,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    @_atomic_atlas_write
    async def record_trial_request(
        self, session: AsyncSession, request: AtlasTrialRequest
    ) -> AtlasTrialRequestRow:
        payload = _payload(request)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasTrialRequestRow, request.request_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "trial request",
            )
            await self._validate_request_artifacts(session, request.request_id)
            return existing
        digest_owner = await session.scalar(
            select(AtlasTrialRequestRow).where(
                AtlasTrialRequestRow.request_digest == request.request_digest
            )
        )
        if digest_owner is not None:
            raise AtlasRegistryError("trial request digest already belongs to another request")

        allocation_row = await _require(
            session, AtlasAllocationRow, request.allocation_id, "trial allocation"
        )
        allocation = _validated(TrialAllocation, allocation_row.record_json, "trial allocation")
        expected_coordinates = (
            allocation.campaign_digest,
            allocation.condition_id,
            allocation.suite_digest,
            allocation.item_digest,
            allocation.trial_index,
        )
        actual_coordinates = (
            request.campaign_digest,
            request.condition_id,
            request.suite_digest,
            request.item_digest,
            request.trial_index,
        )
        if expected_coordinates != actual_coordinates:
            raise AtlasRegistryError("trial request coordinates differ from its allocation")
        execution_row = await _require_execution(session, request.research_execution_digest)
        binding = await session.scalar(
            select(AtlasCampaignExecutionBindingRow).where(
                AtlasCampaignExecutionBindingRow.campaign_digest == request.campaign_digest,
                AtlasCampaignExecutionBindingRow.condition_id == request.condition_id,
                AtlasCampaignExecutionBindingRow.suite_digest == request.suite_digest,
                AtlasCampaignExecutionBindingRow.research_execution_digest
                == request.research_execution_digest,
            )
        )
        if binding is None:
            raise AtlasRegistryError("trial request lacks an admitted execution binding")
        condition_row = await _require(
            session,
            AtlasCampaignConditionRow,
            (request.campaign_digest, request.condition_id),
            "trial request condition",
        )
        condition = _campaign_condition(condition_row)
        profile_row = await _require_profile(session, binding.harness_profile_digest)
        _validate_trial_request_factors(
            request=request,
            condition=condition,
            execution_row=execution_row,
            profile_row=profile_row,
        )
        item_row = await _require(session, AtlasItemRow, request.item_digest, "Atlas item")
        if item_row.item_id != request.item_id or item_row.prompt_digest != request.prompt_digest:
            raise AtlasRegistryError("trial request substitutes item or prompt content")
        descriptors = {
            descriptor.implementation_digest: descriptor
            for descriptor in builtin_adapter_descriptors()
        }
        descriptor = descriptors.get(request.adapter_descriptor_digest)
        if descriptor is None:
            raise AtlasRegistryError("trial request cites an unregistered adapter implementation")
        if (
            descriptor.adapter_id != request.adapter_id
            or descriptor.version != request.adapter_version
            or descriptor.kind.value != item_row.adapter_kind
        ):
            raise AtlasRegistryError("trial request adapter differs from frozen implementation")
        await _require_preflight_evidence(
            session,
            digest=request.edge_preflight_evidence_digest,
            kind="edge",
            request=request,
            execution_row=execution_row,
        )
        if request.effort_mapping_evidence_digest is not None:
            await _require_preflight_evidence(
                session,
                digest=request.effort_mapping_evidence_digest,
                kind="effort_mapping",
                request=request,
                execution_row=execution_row,
            )
        if request.tool_preflight_evidence_digest is not None:
            await _require_preflight_evidence(
                session,
                digest=request.tool_preflight_evidence_digest,
                kind="tool",
                request=request,
                execution_row=execution_row,
            )
        run = await session.scalar(
            select(AtlasRunManifestRow).where(AtlasRunManifestRow.run_id == request.run_id)
        )
        if run is None:
            raise AtlasRegistryError("Atlas run manifest is not registered")
        run_manifest = _validated(AtlasRunManifest, run.record_json, "Atlas run manifest")
        if (
            run.research_execution_digest != request.research_execution_digest
            or run.campaign_digest != request.campaign_digest
            or run.condition_id != request.condition_id
            or run.suite_digest != request.suite_digest
        ):
            raise AtlasRegistryError("trial request differs from its immutable run envelope")
        if (
            not run_manifest.adaptive
            and request.attempt_index == 0
            and request.request_digest not in set(run_manifest.predeclared_request_digests)
        ):
            raise AtlasRegistryError("fixed run request was not predeclared")
        if run_manifest.adaptive and request.attempt_index == 0:
            if (
                run_manifest.request_template_configuration is None
                or run_manifest.request_template_adapter is None
            ):
                raise AtlasRegistryError("adaptive run lacks its frozen request template")
            try:
                configuration = FixedRunConfiguration.model_validate(
                    run_manifest.request_template_configuration
                )
            except ValidationError as error:
                raise AtlasRegistryError(
                    "adaptive run request template fails contract validation"
                ) from error
            suite_row = await _require(
                session, AtlasSuiteRow, request.suite_digest, "adaptive request suite"
            )
            suite = _validated(AtlasSuiteManifest, suite_row.record_json, "adaptive suite")
            profile = _validated(HarnessProfile, profile_row.record_json, "adaptive harness")
            context_limit = profile.context.effective_input_limit_tokens
            if context_limit is None:
                raise AtlasRegistryError("adaptive request harness lacks a context limit")
            item = _validated(AtlasItemManifest, item_row.record_json, "adaptive item")
            expected_request = build_trial_request(
                run_id=run_manifest.run_id,
                campaign_digest=run_manifest.campaign_digest,
                condition_id=run_manifest.condition_id,
                suite=suite,
                item=item,
                execution_digest=run_manifest.research_execution_digest,
                allocation=allocation,
                adapter=run_manifest.request_template_adapter,
                context_limit=context_limit,
                configuration=configuration,
                profile=profile,
                created_at=request.created_at,
            )
            if request != expected_request:
                raise AtlasRegistryError("adaptive request differs from its frozen wire template")
        if (
            allocation.allocation_policy_id != run_manifest.allocation_policy_id
            or allocation.allocation_policy_version != run_manifest.allocation_policy_version
        ):
            raise AtlasRegistryError("trial allocation policy differs from its run envelope")
        if (
            request.context_limit_tokens > run_manifest.max_input_tokens
            or request.max_output_tokens > run_manifest.max_output_tokens
            or request.action_budget > run_manifest.max_actions
        ):
            raise AtlasRegistryError("trial request exceeds its run envelope")
        request_count = await session.scalar(
            select(func.count())
            .select_from(AtlasTrialRequestRow)
            .where(AtlasTrialRequestRow.run_id == request.run_id)
        )
        initial_request_count = await session.scalar(
            select(func.count())
            .select_from(AtlasTrialRequestRow)
            .where(
                AtlasTrialRequestRow.run_id == request.run_id,
                AtlasTrialRequestRow.attempt_index == 0,
            )
        )
        if (
            request.attempt_index == 0
            and initial_request_count is not None
            and initial_request_count >= run_manifest.planned_request_count
        ):
            raise AtlasRegistryError("Atlas run exceeds its predeclared initial requests")
        if (
            request.attempt_index > 0
            and request_count is not None
            and request_count
            >= run_manifest.planned_request_count + run_manifest.max_retry_requests
        ):
            raise AtlasRegistryError("Atlas run exceeds its predeclared retry allowance")

        if request.attempt_index == 0:
            if request.parent_request_id is not None:
                raise AtlasRegistryError("initial trial request cannot cite a retry parent")
        else:
            if request.parent_request_id is None:
                raise AtlasRegistryError("retry trial request requires its immediate parent")
            parent = await _require(
                session, AtlasTrialRequestRow, request.parent_request_id, "parent trial request"
            )
            parent_request = _validated(
                AtlasTrialRequest, parent.record_json, "parent trial request"
            )
            if (
                parent_request.allocation_id != request.allocation_id
                or parent_request.attempt_index + 1 != request.attempt_index
            ):
                raise AtlasRegistryError("trial retries must form one contiguous request chain")
            retry_allowed_differences = {
                "request_id",
                "attempt_index",
                "parent_request_id",
                "request_digest",
                "wire_request_digest",
                "created_at",
            }
            parent_wire_identity = parent_request.model_dump(
                mode="json", exclude=retry_allowed_differences
            )
            retry_wire_identity = request.model_dump(mode="json", exclude=retry_allowed_differences)
            if parent_wire_identity != retry_wire_identity:
                raise AtlasRegistryError("trial retry drifts from its frozen wire configuration")
            parent_result = await session.scalar(
                select(AtlasTrialResultRow).where(
                    AtlasTrialResultRow.request_id == parent_request.request_id
                )
            )
            if parent_result is None:
                raise AtlasRegistryError("a retry cannot hide a missing parent outcome")
            if parent_result.status not in {
                TrialStatus.TIMEOUT.value,
                TrialStatus.INFRASTRUCTURE_FAILURE.value,
            }:
                raise AtlasRegistryError(
                    "verified model outcomes cannot be retried for score inflation"
                )
            parent_call = await _require(
                session, ExternalCallRow, parent_request.request_id, "retryable external call"
            )
            if parent_call.status != "failed_retryable" or not parent_call.error:
                raise AtlasRegistryError("trial retry lacks a captured retryable provider failure")

        await self._artifact_boundary().retain(
            session,
            owner_type="atlas_trial_request",
            owner_id=request.request_id,
            references=await _request_artifacts(session, request),
            recorded_at=request.created_at,
        )
        row = AtlasTrialRequestRow(
            request_id=request.request_id,
            allocation_id=request.allocation_id,
            parent_request_id=request.parent_request_id,
            run_id=request.run_id,
            campaign_digest=request.campaign_digest,
            research_execution_digest=request.research_execution_digest,
            condition_id=request.condition_id,
            suite_digest=request.suite_digest,
            item_digest=request.item_digest,
            trial_index=request.trial_index,
            attempt_index=request.attempt_index,
            request_digest=request.request_digest,
            prompt_digest=request.prompt_digest,
            tool_manifest_digest=request.tool_manifest_digest,
            adapter_id=request.adapter_id,
            adapter_version=request.adapter_version,
            record_digest=record_digest,
            record_json=payload,
            created_at=request.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    @_atomic_atlas_write
    async def record_trial_result(
        self, session: AsyncSession, result: AtlasTrialResult
    ) -> AtlasTrialResultRow:
        payload = _payload(result)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasTrialResultRow, result.result_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "trial result",
            )
            await self.validate_trial_artifacts(session, result_id=result.result_id)
            return existing
        request_row = await _require(
            session, AtlasTrialRequestRow, result.request_id, "trial result request"
        )
        request = _validated(AtlasTrialRequest, request_row.record_json, "trial request")
        if (
            request.request_digest != result.request_digest
            or request.research_execution_digest != result.research_execution_digest
        ):
            raise AtlasRegistryError("trial result is attributed to the wrong request or execution")
        if result.completed_at < request.created_at:
            raise AtlasRegistryError("trial result cannot predate its request")
        if result.retry_count != request.attempt_index:
            raise AtlasRegistryError("trial result retry count differs from request lineage")
        result_owner = await session.scalar(
            select(AtlasTrialResultRow).where(
                (AtlasTrialResultRow.request_id == result.request_id)
                | (AtlasTrialResultRow.result_digest == result.result_digest)
            )
        )
        if result_owner is not None:
            raise AtlasRegistryError("a trial request cannot receive an alternate result")

        post_response = result.status in {
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.VERIFIED_FAILURE,
            TrialStatus.PARTIAL,
            TrialStatus.ABSTAINED,
            TrialStatus.MALFORMED,
            TrialStatus.UNSCORABLE,
            TrialStatus.VERIFIER_FAILURE,
            TrialStatus.PARSER_FAILURE,
            TrialStatus.CONTAMINATED,
        }
        if result.status in {TrialStatus.TIMEOUT, TrialStatus.INFRASTRUCTURE_FAILURE}:
            failed_call = await _require(
                session, ExternalCallRow, result.request_id, "failed generation call"
            )
            if (
                failed_call.run_id != request.run_id
                or failed_call.purpose != "capability_atlas"
                or failed_call.request_hash != request.wire_request_digest
                or failed_call.status not in {"failed_retryable", "failed_terminal"}
                or not failed_call.error
                or failed_call.response_artifact_id is not None
            ):
                raise AtlasRegistryError(
                    "infrastructure result disagrees with captured provider failure"
                )
            if (
                result.status == TrialStatus.TIMEOUT
                and failed_call.error.get("classification") != "timeout"
            ):
                raise AtlasRegistryError("timeout result lacks captured timeout evidence")
        elif result.status == TrialStatus.NOT_RUN:
            dispatched = await session.get(ExternalCallRow, result.request_id)
            if dispatched is not None or not any(
                code.startswith("not_run:") for code in result.failure_codes
            ):
                raise AtlasRegistryError(
                    "not-run result requires a governed no-dispatch reason and no call"
                )
        if post_response:
            external_call = await _require(
                session, ExternalCallRow, result.request_id, "captured generation call"
            )
            run_manifest = await session.scalar(
                select(AtlasRunManifestRow).where(AtlasRunManifestRow.run_id == request.run_id)
            )
            if run_manifest is None:
                raise AtlasRegistryError("captured generation lacks an Atlas run envelope")
            execution_row = await _require_execution(session, result.research_execution_digest)
            execution = _validated(
                ResearchExecutionManifest,
                execution_row.record_json,
                "trial result research execution",
            )
            expected_provider = _expected_generation_provider(execution)
            external_artifact = result.external_call_artifact
            if external_artifact is None:
                raise AtlasRegistryError("post-response result lacks captured generation envelope")
            if (
                external_call.run_id != request.run_id
                or external_call.status != "completed"
                or external_call.purpose != "capability_atlas"
                or external_call.provider != expected_provider
                or external_call.provider != result.generation_provider
                or external_call.request_hash != request.wire_request_digest
                or external_call.response_artifact_id != external_artifact.artifact_id
                or external_call.result_envelope_digest != external_artifact.digest
                or result.generation_model_id != execution.student_model.model_id
                or external_call.result_model_id != result.generation_model_id
                or result.generation_protocol != execution.student_model.protocol
                or external_call.result_protocol != result.generation_protocol
                or external_call.result_raw_request_digest != result.raw_request_digest
                or external_call.result_raw_response_digest != result.raw_response_digest
                or external_call.result_raw_response_digest != result.response_digest
                or external_call.result_capabilities_digest != result.capabilities_digest
                or external_call.result_latency_ms != result.latency_ms
                or _token_accounting_from_usage(external_call.result_usage) != result.tokens
            ):
                raise AtlasRegistryError(
                    "captured generation disagrees with run, request, or serving identity"
                )
            await _require_artifact(session, external_artifact)
            output_digest = external_call.result_output_text_digest
            if output_digest is None or any(
                evidence.evaluated_output_digest != output_digest
                for evidence in result.verifier_evidence
            ):
                raise AtlasRegistryError(
                    "verifier evidence was not evaluated against the captured model output"
                )
            if result.cost_usd not in {None, 0.0}:
                raise AtlasRegistryError(
                    "nonzero trial cost requires a registered provider billing authority"
                )

        if result.response_artifact is not None:
            response_artifact = await _require_artifact(session, result.response_artifact)
            if result.response_digest != response_artifact.digest:
                raise AtlasRegistryError("response digest differs from retained artifact")
        for artifact in result.grader_artifacts:
            await _require_artifact(session, artifact)
        item_row = await _require(session, AtlasItemRow, request.item_digest, "trial result item")
        item = _validated(AtlasItemManifest, item_row.record_json, "trial result Atlas item")
        authoritative = {
            AuthorityKind.DETERMINISTIC,
            AuthorityKind.KERNEL,
            AuthorityKind.ENVIRONMENT,
        }
        frozen_evidence = tuple(
            evidence
            for evidence in result.verifier_evidence
            if evidence.verifier_id == item.verifier_id
            and evidence.verifier_version == item.verifier_version
            and evidence.authority in authoritative
        )
        if result.success is not None:
            if not frozen_evidence:
                raise AtlasRegistryError(
                    "trial outcome lacks the frozen item's authoritative verifier"
                )
            if any(
                evidence.success != result.success or evidence.score != result.score
                for evidence in frozen_evidence
            ):
                raise AtlasRegistryError(
                    "trial score or success contradicts the frozen verifier outcome"
                )
            expected_disposition = "verified" if result.success else "rejected"
            if any(evidence.disposition != expected_disposition for evidence in frozen_evidence):
                raise AtlasRegistryError("trial status contradicts the frozen verifier disposition")
        for evidence in result.verifier_evidence:
            verifier = await _require(
                session, VerifierResultRow, evidence.evidence_id, "verifier evidence"
            )
            verifier_record = _validated(
                VerifierResult, verifier.record_json, "trial verifier evidence"
            )
            if (
                verifier.record_digest != sha256_digest(verifier.record_json)
                or verifier_record.result_id != evidence.evidence_id
                or verifier_record.verifier_id != verifier.verifier_id
                or verifier_record.verifier_version != verifier.verifier_version
                or verifier_record.disposition.value != verifier.disposition
                or verifier_record.deterministic != verifier.deterministic
                or verifier.verifier_id != evidence.verifier_id
                or verifier.verifier_version != evidence.verifier_version
                or verifier.disposition != evidence.disposition
                or verifier.deterministic != evidence.deterministic
                or verifier.record_digest != evidence.evidence_digest
            ):
                raise AtlasRegistryError("trial evidence disagrees with authoritative verifier")
            for artifact in evidence.artifact_refs:
                await _require_artifact(session, artifact)

        await self._validate_request_artifacts(session, request.request_id)
        await self._artifact_boundary().retain(
            session,
            owner_type="atlas_trial_result",
            owner_id=result.result_id,
            references=await _result_artifacts(session, result, request),
            recorded_at=result.completed_at,
        )
        row = AtlasTrialResultRow(
            result_id=result.result_id,
            request_id=result.request_id,
            research_execution_digest=result.research_execution_digest,
            status=result.status.value,
            success=result.success,
            score=result.score,
            response_artifact_id=(
                result.response_artifact.artifact_id
                if result.response_artifact is not None
                else None
            ),
            result_digest=result.result_digest,
            record_digest=record_digest,
            record_json=payload,
            completed_at=result.completed_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_phenomenon(
        self, session: AsyncSession, manifest: PhenomenonManifest
    ) -> AtlasPhenomenonRow:
        payload = _payload(manifest)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasPhenomenonRow, manifest.manifest_digest)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "phenomenon",
            )
            return existing
        owner = await session.scalar(
            select(AtlasPhenomenonRow).where(
                AtlasPhenomenonRow.phenomenon_id == manifest.phenomenon_id
            )
        )
        if owner is not None:
            raise AtlasRegistryError("phenomenon ID cannot be rebound")
        ontology_row = await _require(
            session, AtlasOntologyRow, manifest.ontology_digest, "phenomenon ontology"
        )
        ontology = _validated(OntologyManifest, ontology_row.record_json, "ontology")
        known_nodes = {node.node_id for node in ontology.nodes}
        if not set(manifest.ontology_node_ids).issubset(known_nodes):
            raise AtlasRegistryError("phenomenon cites an unknown ontology node")
        row = AtlasPhenomenonRow(
            phenomenon_digest=manifest.manifest_digest,
            phenomenon_id=manifest.phenomenon_id,
            ontology_digest=manifest.ontology_digest,
            record_digest=record_digest,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_probe_set(
        self, session: AsyncSession, manifest: ProbeSetManifest
    ) -> AtlasProbeSetRow:
        payload = _payload(manifest)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasProbeSetRow, manifest.manifest_digest)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "probe set",
            )
            return existing
        owner = await session.scalar(
            select(AtlasProbeSetRow).where(AtlasProbeSetRow.probe_set_id == manifest.probe_set_id)
        )
        if owner is not None:
            raise AtlasRegistryError("probe-set ID cannot be rebound")
        phenomenon_row = await _require(
            session,
            AtlasPhenomenonRow,
            manifest.phenomenon_digest,
            "probe-set phenomenon",
        )
        if phenomenon_row.phenomenon_id != manifest.phenomenon_id:
            raise AtlasRegistryError("probe set phenomenon ID and digest disagree")
        for item_digest in manifest.item_digests:
            if await session.get(AtlasSuiteItemRow, (manifest.suite_digest, item_digest)) is None:
                raise AtlasRegistryError("probe set substitutes an item outside its suite")
        result_digests = tuple(manifest.outcome_result_digests)
        if len(result_digests) != len(set(result_digests)):
            raise AtlasRegistryError("probe-set outcome results must be unique")
        for result_digest in result_digests:
            result_row = await _result_by_digest(session, result_digest)
            request_row = await _require(
                session, AtlasTrialRequestRow, result_row.request_id, "probe trial request"
            )
            if (
                request_row.suite_digest != manifest.suite_digest
                or request_row.item_digest not in manifest.item_digests
            ):
                raise AtlasRegistryError("probe outcome is outside its matched item set")
        row = AtlasProbeSetRow(
            probe_set_digest=manifest.manifest_digest,
            probe_set_id=manifest.probe_set_id,
            phenomenon_digest=manifest.phenomenon_digest,
            suite_digest=manifest.suite_digest,
            outcome_digest=manifest.outcome_digest,
            record_digest=record_digest,
            record_json=payload,
            created_at=manifest.created_at,
        )
        session.add(row)
        await session.flush()
        session.add_all(
            AtlasProbeItemRow(
                probe_set_digest=manifest.manifest_digest,
                suite_digest=manifest.suite_digest,
                item_digest=item_digest,
            )
            for item_digest in manifest.item_digests
        )
        session.add_all(
            AtlasProbeResultRow(
                probe_set_digest=manifest.manifest_digest, result_digest=result_digest
            )
            for result_digest in result_digests
        )
        await session.flush()
        return row

    async def register_failure_cluster(
        self, session: AsyncSession, cluster: FailureCluster
    ) -> AtlasFailureClusterRow:
        payload = _payload(cluster)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasFailureClusterRow, cluster.cluster_digest)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "failure cluster",
            )
            return existing
        owner = await session.scalar(
            select(AtlasFailureClusterRow).where(
                AtlasFailureClusterRow.cluster_id == cluster.cluster_id
            )
        )
        if owner is not None:
            raise AtlasRegistryError("failure-cluster ID cannot be rebound")
        campaign_row = await _require(
            session, AtlasCampaignRow, cluster.campaign_digest, "failure-cluster campaign"
        )
        campaign = _validated(AtlasCampaignManifest, campaign_row.record_json, "Atlas campaign")
        if campaign.ontology_digest != cluster.ontology_digest:
            raise AtlasRegistryError("failure cluster uses another campaign ontology")
        ontology_row = await _require(
            session, AtlasOntologyRow, cluster.ontology_digest, "failure-cluster ontology"
        )
        ontology = _validated(OntologyManifest, ontology_row.record_json, "ontology")
        known_nodes = {node.node_id for node in ontology.nodes}
        members = set(cluster.member_result_digests)
        assignment_evidence: set[str] = set()
        for assignment in cluster.assignments:
            if assignment.node_id not in known_nodes:
                raise AtlasRegistryError("failure assignment cites an unknown ontology node")
            if not set(assignment.evidence_result_digests).issubset(members):
                raise AtlasRegistryError("failure assignment cites evidence outside its cluster")
            assignment_evidence.update(assignment.evidence_result_digests)
        if assignment_evidence != members:
            raise AtlasRegistryError("every cluster member must support a failure assignment")
        if cluster.probe_set_digest is not None:
            probe = await _require(
                session, AtlasProbeSetRow, cluster.probe_set_digest, "failure-cluster probe set"
            )
            phenomenon = await _require(
                session, AtlasPhenomenonRow, probe.phenomenon_digest, "probe phenomenon"
            )
            if phenomenon.ontology_digest != cluster.ontology_digest:
                raise AtlasRegistryError("cluster probe belongs to another ontology")
        for result_digest in cluster.member_result_digests:
            result_row = await _result_by_digest(session, result_digest)
            request_row = await _require(
                session, AtlasTrialRequestRow, result_row.request_id, "cluster trial request"
            )
            if request_row.campaign_digest != cluster.campaign_digest:
                raise AtlasRegistryError("cluster member belongs to another campaign")
        row = AtlasFailureClusterRow(
            cluster_digest=cluster.cluster_digest,
            cluster_id=cluster.cluster_id,
            campaign_digest=cluster.campaign_digest,
            ontology_digest=cluster.ontology_digest,
            probe_set_digest=cluster.probe_set_digest,
            status=cluster.status.value,
            severity=cluster.severity,
            record_digest=record_digest,
            record_json=payload,
            created_at=cluster.created_at,
        )
        session.add(row)
        await session.flush()
        exemplars = set(cluster.exemplar_result_digests)
        session.add_all(
            AtlasFailureClusterResultRow(
                cluster_digest=cluster.cluster_digest,
                result_digest=result_digest,
                exemplar=result_digest in exemplars,
            )
            for result_digest in cluster.member_result_digests
        )
        await session.flush()
        return row

    async def register_snapshot(
        self, session: AsyncSession, snapshot: AtlasSnapshot
    ) -> AtlasSnapshotRow:
        payload = _payload(snapshot)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasSnapshotRow, snapshot.snapshot_digest)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "Atlas snapshot",
            )
            return existing
        owner = await session.scalar(
            select(AtlasSnapshotRow).where(AtlasSnapshotRow.snapshot_id == snapshot.snapshot_id)
        )
        if owner is not None:
            raise AtlasRegistryError("snapshot ID cannot be rebound")
        campaign_row = await _require(
            session, AtlasCampaignRow, snapshot.campaign_digest, "snapshot campaign"
        )
        campaign = _validated(AtlasCampaignManifest, campaign_row.record_json, "Atlas campaign")
        if campaign.ontology_digest != snapshot.ontology_digest:
            raise AtlasRegistryError("snapshot uses another campaign ontology")
        execution_row = await _require_execution(session, snapshot.research_execution_digest)
        await _require_profile(session, snapshot.harness_profile_digest)
        if execution_row.harness_profile_digest != snapshot.harness_profile_digest:
            raise AtlasRegistryError("snapshot harness differs from research execution")
        for claim_id in snapshot.upstream_claim_ids:
            await _require(session, AtlasBenchmarkClaimRow, claim_id, "snapshot source claim")
        if not set(snapshot.upstream_claim_ids).issubset(set(campaign.source_claim_ids)):
            raise AtlasRegistryError("snapshot imports claims outside its predeclared campaign")
        for cluster_digest in snapshot.failure_cluster_digests:
            cluster_row = await _require(
                session, AtlasFailureClusterRow, cluster_digest, "snapshot failure cluster"
            )
            if cluster_row.campaign_digest != snapshot.campaign_digest:
                raise AtlasRegistryError("snapshot cluster belongs to another campaign")

        observed_coordinates: set[tuple[str, str]] = set()
        observation_keys: set[tuple[str, str, str]] = set()
        for observation in snapshot.local_observations:
            coordinate = (observation.condition_id, observation.suite_digest)
            observed_coordinates.add(coordinate)
            observation_key = (*coordinate, observation.metric.metric_id)
            if observation_key in observation_keys:
                raise AtlasRegistryError("snapshot repeats a suite-condition metric")
            observation_keys.add(observation_key)
            if observation.extrapolated:
                raise AtlasRegistryError(
                    "extrapolated observations require a registered analysis producer"
                )
            binding = await session.scalar(
                select(AtlasCampaignExecutionBindingRow).where(
                    AtlasCampaignExecutionBindingRow.campaign_digest == snapshot.campaign_digest,
                    AtlasCampaignExecutionBindingRow.condition_id == observation.condition_id,
                    AtlasCampaignExecutionBindingRow.suite_digest == observation.suite_digest,
                    AtlasCampaignExecutionBindingRow.research_execution_digest
                    == snapshot.research_execution_digest,
                )
            )
            if binding is None:
                raise AtlasRegistryError("snapshot observation lacks an execution binding")
            counts = await _terminal_outcomes(
                session,
                campaign_digest=snapshot.campaign_digest,
                condition_id=observation.condition_id,
                suite_digest=observation.suite_digest,
                execution_digest=snapshot.research_execution_digest,
            )
            metric = observation.metric
            recomputed = _success_metric(metric.metric_id, counts)
            if metric != recomputed:
                raise AtlasRegistryError(
                    "snapshot metric omits, substitutes, inflates, or misstates outcomes"
                )

        curve_keys: set[tuple[str, str]] = set()
        declared_boundaries = {rule.boundary_probability for rule in campaign.stop_rules}
        for curve in snapshot.curves:
            curve_key = (curve.family_id, curve.condition_id)
            if curve_key in curve_keys:
                raise AtlasRegistryError("snapshot repeats a family-condition capability curve")
            curve_keys.add(curve_key)
            if curve.boundary_probability not in declared_boundaries:
                raise AtlasRegistryError(
                    "snapshot curve boundary differs from every predeclared stop rule"
                )
            suites = tuple(
                sorted(
                    {
                        observation.suite_digest
                        for observation in snapshot.local_observations
                        if observation.condition_id == curve.condition_id
                    }
                )
            )
            if not suites:
                raise AtlasRegistryError("snapshot curve lacks a reported suite condition")
            recomputed_curve = await _recompute_curve(
                session,
                campaign_digest=snapshot.campaign_digest,
                execution_digest=snapshot.research_execution_digest,
                family_id=curve.family_id,
                condition_id=curve.condition_id,
                suite_digests=suites,
                boundary_probability=curve.boundary_probability,
            )
            if curve != recomputed_curve:
                raise AtlasRegistryError(
                    "snapshot capability curve disagrees with immutable trial outcomes"
                )

        bound_coordinates = set(
            (
                await session.execute(
                    select(
                        AtlasCampaignExecutionBindingRow.condition_id,
                        AtlasCampaignExecutionBindingRow.suite_digest,
                    ).where(
                        AtlasCampaignExecutionBindingRow.campaign_digest
                        == snapshot.campaign_digest,
                        AtlasCampaignExecutionBindingRow.research_execution_digest
                        == snapshot.research_execution_digest,
                    )
                )
            ).all()
        )
        if snapshot.complete and observed_coordinates != bound_coordinates:
            raise AtlasRegistryError("complete snapshot must report every bound suite condition")
        if snapshot.complete:
            for condition_id, suite_digest in bound_coordinates:
                counts = await _terminal_outcomes(
                    session,
                    campaign_digest=snapshot.campaign_digest,
                    condition_id=condition_id,
                    suite_digest=suite_digest,
                    execution_digest=snapshot.research_execution_digest,
                )
                if (
                    counts.planned == 0
                    or counts.missing_terminal
                    or counts.infrastructure
                    or counts.contaminated
                    or counts.other_excluded
                ):
                    raise AtlasRegistryError(
                        "complete snapshot contains unexecuted or missing trials"
                    )
        if snapshot.promotion_eligible:
            promotion = set(campaign.promotion_suite_digests)
            if not promotion or not promotion.issubset(
                {suite_digest for _, suite_digest in observed_coordinates}
            ):
                raise AtlasRegistryError("promotion snapshot omits a sealed promotion suite")
            for suite_digest in promotion:
                suite_row = await _require(session, AtlasSuiteRow, suite_digest, "promotion suite")
                if (
                    suite_row.status != SuiteStatus.SEALED.value
                    or suite_row.evaluation_class != EvaluationClass.SEALED_PROMOTION.value
                ):
                    raise AtlasRegistryError("promotion evidence must use a sealed promotion suite")
            promoted_core_suites: set[str] = set()
            execution = _validated(
                ResearchExecutionManifest, execution_row.record_json, "research execution"
            )
            for decision_id in snapshot.promotion_checkpoint_decision_ids:
                decision_row = await _require(
                    session, CheckpointDecisionRow, decision_id, "checkpoint promotion decision"
                )
                if decision_row.action != "promote":
                    raise AtlasRegistryError("snapshot cites a non-promotion checkpoint decision")
                verification = await CheckpointRegistry().verify_decision(
                    session, decision_id=decision_id
                )
                if not verification.valid:
                    raise AtlasRegistryError(
                        "checkpoint promotion decision fails closed on recomputation"
                    )
                if decision_row.checkpoint_id != execution.student_model.checkpoint.component_id:
                    raise AtlasRegistryError(
                        "checkpoint promotion decision evaluates another checkpoint"
                    )
                if decision_row.comparison_id is None:
                    raise AtlasRegistryError("checkpoint promotion decision lacks a comparison")
                checkpoint_comparison = await _require(
                    session,
                    CheckpointComparisonRow,
                    decision_row.comparison_id,
                    "checkpoint promotion comparison",
                )
                if not checkpoint_comparison.recommended:
                    raise AtlasRegistryError(
                        "checkpoint promotion comparison does not recommend promotion"
                    )
                candidate_evaluation = await _require(
                    session,
                    CheckpointEvaluationRow,
                    checkpoint_comparison.candidate_evaluation_id,
                    "checkpoint candidate evaluation",
                )
                if candidate_evaluation.checkpoint_id != decision_row.checkpoint_id:
                    raise AtlasRegistryError(
                        "checkpoint decision and candidate evaluation disagree"
                    )
                promoted_core_suites.add(candidate_evaluation.suite_manifest_digest)
            expected_core_suites: set[str] = set()
            for suite_digest in promotion:
                persisted_suite = await _require(
                    session, AtlasSuiteRow, suite_digest, "promotion suite"
                )
                suite = _validated(
                    AtlasSuiteManifest, persisted_suite.record_json, "promotion suite"
                )
                if suite.evaluation_suite_manifest_digest is not None:
                    expected_core_suites.add(suite.evaluation_suite_manifest_digest)
            if promoted_core_suites != expected_core_suites:
                raise AtlasRegistryError(
                    "checkpoint promotion decisions do not exactly cover the sealed "
                    "promotion suites"
                )

        row = AtlasSnapshotRow(
            snapshot_digest=snapshot.snapshot_digest,
            snapshot_id=snapshot.snapshot_id,
            campaign_digest=snapshot.campaign_digest,
            research_execution_digest=snapshot.research_execution_digest,
            harness_profile_digest=snapshot.harness_profile_digest,
            ontology_digest=snapshot.ontology_digest,
            complete=snapshot.complete,
            promotion_eligible=snapshot.promotion_eligible,
            record_digest=record_digest,
            record_json=payload,
            created_at=snapshot.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def register_comparison(
        self, session: AsyncSession, comparison: AtlasComparison
    ) -> AtlasComparisonRow:
        payload = _payload(comparison)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasComparisonRow, comparison.comparison_digest)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "Atlas comparison",
            )
            return existing
        owner = await session.scalar(
            select(AtlasComparisonRow).where(
                AtlasComparisonRow.comparison_id == comparison.comparison_id
            )
        )
        if owner is not None:
            raise AtlasRegistryError("Atlas comparison ID cannot be rebound")
        left_row = await _require(
            session,
            AtlasSnapshotRow,
            comparison.left_snapshot_digest,
            "left Atlas snapshot",
        )
        right_row = await _require(
            session,
            AtlasSnapshotRow,
            comparison.right_snapshot_digest,
            "right Atlas snapshot",
        )
        left = _validated(AtlasSnapshot, left_row.record_json, "left Atlas snapshot")
        right = _validated(AtlasSnapshot, right_row.record_json, "right Atlas snapshot")
        assessment = await ResearchControlRegistry().compare(
            session,
            left_execution_digest=left.research_execution_digest,
            right_execution_digest=right.research_execution_digest,
            allowed_differences=comparison.allowed_axes,
        )
        if comparison.observed_axes != assessment.differing_axes:
            raise AtlasRegistryError("comparison observed axes differ from research controls")
        if comparison.comparability_evidence_digest != sha256_digest(assessment):
            raise AtlasRegistryError("comparison evidence digest differs from recomputed controls")
        if comparison.causal_claim_permitted and (
            not assessment.comparable or not left.complete or not right.complete
        ):
            raise AtlasRegistryError(
                "causal comparison requires complete snapshots and comparable controls"
            )
        expected_deltas, regressions, improvements = await _comparison_deltas(
            session, left, right, comparison.metric_deltas
        )
        if comparison.metric_deltas != expected_deltas:
            raise AtlasRegistryError(
                "comparison metric deltas disagree with recomputed snapshot observations"
            )
        if comparison.regressions != regressions or comparison.improvements != improvements:
            raise AtlasRegistryError(
                "comparison regression labels disagree with recomputed uncertainty"
            )
        row = AtlasComparisonRow(
            comparison_digest=comparison.comparison_digest,
            comparison_id=comparison.comparison_id,
            left_snapshot_digest=comparison.left_snapshot_digest,
            right_snapshot_digest=comparison.right_snapshot_digest,
            causal_claim_permitted=comparison.causal_claim_permitted,
            record_digest=record_digest,
            record_json=payload,
            created_at=comparison.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    @_atomic_atlas_write
    async def register_exploratory_proposal(
        self, session: AsyncSession, proposal: ExploratoryFailureProposal
    ) -> AtlasExploratoryProposalRow:
        payload = _payload(proposal)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasExploratoryProposalRow, proposal.proposal_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "exploratory proposal",
            )
            if (
                existing.deduplication_key != proposal.deduplication_key
                or existing.consent_evidence_digest != proposal.consent_evidence_digest
                or existing.source_trace_digest != proposal.source_trace_digest
                or existing.source_trace_artifact_id != proposal.source_trace_artifact.artifact_id
                or existing.raw_chat_promoted
                or not _same_timestamp(existing.created_at, proposal.created_at)
            ):
                raise AtlasRegistryError("exploratory evidence has inconsistent source identity")
            await self._artifact_boundary().validate(
                session,
                owner_type="atlas_exploratory_proposal",
                owner_id=proposal.proposal_id,
                references=(proposal.source_trace_artifact,),
                recorded_at=proposal.created_at,
            )
            return existing
        duplicate = await session.scalar(
            select(AtlasExploratoryProposalRow).where(
                AtlasExploratoryProposalRow.deduplication_key == proposal.deduplication_key
            )
        )
        if duplicate is not None:
            raise AtlasRegistryError("exploratory failure was already proposed")
        trace = await _require_artifact(session, proposal.source_trace_artifact)
        if trace.digest != proposal.source_trace_digest:
            raise AtlasRegistryError("exploratory trace digest differs from retained evidence")
        if not trace.restricted or not trace.raw_data:
            raise AtlasRegistryError("consented raw traces must remain restricted raw artifacts")
        if proposal.redacted_excerpt_digest == proposal.source_trace_digest:
            raise AtlasRegistryError("redacted excerpt cannot alias the raw source trace")
        await self._artifact_boundary().retain(
            session,
            owner_type="atlas_exploratory_proposal",
            owner_id=proposal.proposal_id,
            references=(proposal.source_trace_artifact,),
            recorded_at=proposal.created_at,
        )
        row = AtlasExploratoryProposalRow(
            proposal_id=proposal.proposal_id,
            deduplication_key=proposal.deduplication_key,
            consent_evidence_digest=proposal.consent_evidence_digest,
            source_trace_digest=proposal.source_trace_digest,
            source_trace_artifact_id=trace.artifact_id,
            raw_chat_promoted=False,
            record_digest=record_digest,
            record_json=payload,
            created_at=proposal.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def record_exploratory_reproduction(
        self, session: AsyncSession, reproduction: ExploratoryReproduction
    ) -> AtlasExploratoryReproductionRow:
        payload = _payload(reproduction)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasExploratoryReproductionRow, reproduction.reproduction_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "exploratory reproduction",
            )
            return existing
        proposal_row = await _require(
            session,
            AtlasExploratoryProposalRow,
            reproduction.proposal_id,
            "exploratory proposal",
        )
        await _require_execution(session, reproduction.research_execution_digest)
        item_row = await _require(
            session, AtlasItemRow, reproduction.independent_item_digest, "independent item"
        )
        if proposal_row.source_trace_digest in {item_row.item_digest, item_row.prompt_digest}:
            raise AtlasRegistryError("raw exploratory trace cannot become a reproduction item")
        if reproduction.created_at <= proposal_row.created_at:
            raise AtlasRegistryError("independent reproduction must postdate its proposal")
        if len(set(reproduction.result_digests)) != len(reproduction.result_digests):
            raise AtlasRegistryError("reproduction trial results must be unique")
        verified_failures = 0
        for result_digest in reproduction.result_digests:
            result_row = await _result_by_digest(session, result_digest)
            request_row = await _require(
                session, AtlasTrialRequestRow, result_row.request_id, "reproduction request"
            )
            if (
                request_row.research_execution_digest != reproduction.research_execution_digest
                or request_row.item_digest != reproduction.independent_item_digest
            ):
                raise AtlasRegistryError("reproduction evidence uses another execution or item")
            result = _validated(AtlasTrialResult, result_row.record_json, "trial result")
            if (
                result.status == TrialStatus.VERIFIED_FAILURE
                and result.failure_origin == FailureOrigin.MODEL
            ):
                verified_failures += 1
        if reproduction.reproduced and verified_failures != len(reproduction.result_digests):
            raise AtlasRegistryError("admitted reproduction requires only verified model failures")
        row = AtlasExploratoryReproductionRow(
            reproduction_id=reproduction.reproduction_id,
            proposal_id=reproduction.proposal_id,
            research_execution_digest=reproduction.research_execution_digest,
            independent_item_digest=reproduction.independent_item_digest,
            reproduced=reproduction.reproduced,
            record_digest=record_digest,
            record_json=payload,
            created_at=reproduction.created_at,
        )
        session.add(row)
        await session.flush()
        session.add_all(
            AtlasExploratoryReproductionResultRow(
                reproduction_id=reproduction.reproduction_id, result_digest=result_digest
            )
            for result_digest in reproduction.result_digests
        )
        await session.flush()
        return row

    async def record_challenge_admission(
        self, session: AsyncSession, decision: ChallengeAdmissionDecision
    ) -> AtlasChallengeAdmissionRow:
        payload = _payload(decision)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasChallengeAdmissionRow, decision.decision_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "challenge admission",
            )
            return existing
        reproduction_row = await _require(
            session,
            AtlasExploratoryReproductionRow,
            decision.reproduction_id,
            "exploratory reproduction",
        )
        if reproduction_row.proposal_id != decision.proposal_id:
            raise AtlasRegistryError("challenge decision proposal and reproduction disagree")
        proposal_row = await _require(
            session,
            AtlasExploratoryProposalRow,
            decision.proposal_id,
            "exploratory proposal",
        )
        if decision.action == "admit":
            if not reproduction_row.reproduced:
                raise AtlasRegistryError("unreproduced exploratory failures cannot be admitted")
            assert decision.challenge_suite_digest is not None
            assert decision.admitted_item_digest is not None
            suite_row = await _require(
                session, AtlasSuiteRow, decision.challenge_suite_digest, "challenge suite"
            )
            if suite_row.evaluation_class != EvaluationClass.CHALLENGE.value:
                raise AtlasRegistryError("exploratory failures may enter only challenge suites")
            if (
                await session.get(
                    AtlasSuiteItemRow,
                    (decision.challenge_suite_digest, decision.admitted_item_digest),
                )
                is None
            ):
                raise AtlasRegistryError("challenge admission item is not in the frozen suite")
            if reproduction_row.independent_item_digest != decision.admitted_item_digest:
                raise AtlasRegistryError("challenge admission substitutes the reproduced item")
            item_row = await _require(
                session, AtlasItemRow, decision.admitted_item_digest, "challenge item"
            )
            if proposal_row.source_trace_digest in {item_row.item_digest, item_row.prompt_digest}:
                raise AtlasRegistryError("raw chat cannot be directly promoted to evaluation")
        row = AtlasChallengeAdmissionRow(
            decision_id=decision.decision_id,
            proposal_id=decision.proposal_id,
            reproduction_id=decision.reproduction_id,
            action=decision.action,
            challenge_suite_digest=decision.challenge_suite_digest,
            admitted_item_digest=decision.admitted_item_digest,
            record_digest=record_digest,
            record_json=payload,
            created_at=decision.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def record_training_eligibility(
        self, session: AsyncSession, assessment: TrainingFailureEligibility
    ) -> AtlasTrainingEligibilityRow:
        payload = _payload(assessment)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasTrainingEligibilityRow, assessment.assessment_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "training eligibility",
            )
            return existing
        cluster_row, cluster, suite_row, governance = await _eligibility_scope(
            session, assessment.failure_cluster_digest, assessment.source_suite_digest
        )
        if assessment.source_evaluation_class.value != suite_row.evaluation_class:
            raise AtlasRegistryError("training assessment misstates suite evaluation class")
        evidence = await _cluster_evidence(session, cluster)
        independently_reproduced = (
            len({result.research_execution_digest for result in evidence.results}) >= 2
        )
        model_failure_confirmed = _model_failure_confirmed(evidence.exemplar_results)
        harness_effects_ruled_out = len(evidence.harness_profile_digests) >= 2
        verifier_authority_confirmed = _verifier_authority_confirmed(evidence.exemplar_results)
        if assessment.independently_reproduced != independently_reproduced:
            raise AtlasRegistryError("training assessment misstates independent reproduction")
        if assessment.model_failure_confirmed != model_failure_confirmed:
            raise AtlasRegistryError("training assessment misstates model-failure evidence")
        if assessment.harness_effects_ruled_out != harness_effects_ruled_out:
            raise AtlasRegistryError("training assessment misstates harness ablation evidence")
        if assessment.verifier_authority_confirmed != verifier_authority_confirmed:
            raise AtlasRegistryError("training assessment misstates verifier authority")
        if assessment.stable and cluster.stability < 0.8:
            raise AtlasRegistryError("stable training candidate lacks cluster stability")
        lanes = _training_lanes(assessment.allowed_lanes)
        if TrainingLane.EVALUATION_ONLY in lanes:
            raise AtlasRegistryError("evaluation-only material cannot enter a training lane")
        rights_permit = bool(lanes) and all(
            governance.rights.permits(_rights_use_for_lane(lane)) for lane in lanes
        )
        if assessment.rights_permit_training != rights_permit:
            raise AtlasRegistryError("training assessment misstates source rights")
        contamination_cleared = all(
            all(result.contamination_checks.values()) for result in evidence.results
        )
        if assessment.contamination_cleared != contamination_cleared:
            raise AtlasRegistryError("training assessment misstates contamination evidence")
        memberships = await _campaign_partitions(session, assessment.source_suite_digest)
        promotion_excluded = not memberships.promotion
        adaptive_excluded = not memberships.adaptive
        if assessment.promotion_suite_excluded != promotion_excluded:
            raise AtlasRegistryError("training assessment misstates promotion-suite exclusion")
        if assessment.adaptive_search_excluded != adaptive_excluded:
            raise AtlasRegistryError("training assessment misstates adaptive-suite exclusion")
        if cluster_row.status != ReviewStatus.ADMITTED.value and assessment.eligible:
            raise AtlasRegistryError(
                "only reviewed failure clusters can become training candidates"
            )
        _require_evidence_refs(
            assessment.evidence_refs,
            {assessment.failure_cluster_digest, assessment.source_suite_digest},
            "training eligibility",
        )
        row = AtlasTrainingEligibilityRow(
            assessment_id=assessment.assessment_id,
            failure_cluster_digest=assessment.failure_cluster_digest,
            source_suite_digest=assessment.source_suite_digest,
            eligible=assessment.eligible,
            record_digest=record_digest,
            record_json=payload,
            created_at=assessment.created_at,
        )
        session.add(row)
        await session.flush()
        return row

    async def record_memory_eligibility(
        self, session: AsyncSession, assessment: MemoryInterventionEligibility
    ) -> AtlasMemoryEligibilityRow:
        payload = _payload(assessment)
        record_digest = sha256_digest(payload)
        existing = await session.get(AtlasMemoryEligibilityRow, assessment.assessment_id)
        if existing is not None:
            _require_same(
                existing.record_digest,
                record_digest,
                existing.record_json,
                payload,
                "memory eligibility",
            )
            return existing
        cluster_row, cluster, suite_row, governance = await _eligibility_scope(
            session, assessment.failure_cluster_digest, assessment.source_suite_digest
        )
        await _require_execution(session, assessment.research_execution_digest)
        if assessment.source_evaluation_class.value != suite_row.evaluation_class:
            raise AtlasRegistryError("memory assessment misstates suite evaluation class")
        evidence = await _cluster_evidence(session, cluster)
        execution_results = tuple(
            result
            for result in evidence.results
            if result.research_execution_digest == assessment.research_execution_digest
        )
        if not execution_results:
            raise AtlasRegistryError("memory assessment execution has no cluster evidence")
        independently_reproduced = (
            len({result.research_execution_digest for result in evidence.results}) >= 2
        )
        model_failure_confirmed = _model_failure_confirmed(evidence.exemplar_results)
        harness_effects_ruled_out = len(evidence.harness_profile_digests) >= 2
        verifier_authority_confirmed = _verifier_authority_confirmed(evidence.exemplar_results)
        if assessment.independently_reproduced != independently_reproduced:
            raise AtlasRegistryError("memory assessment misstates independent reproduction")
        if assessment.model_failure_confirmed != model_failure_confirmed:
            raise AtlasRegistryError("memory assessment misstates model-failure evidence")
        if assessment.harness_effects_ruled_out != harness_effects_ruled_out:
            raise AtlasRegistryError("memory assessment misstates harness ablation evidence")
        if assessment.verifier_authority_confirmed != verifier_authority_confirmed:
            raise AtlasRegistryError("memory assessment misstates verifier authority")
        if assessment.stable and cluster.stability < 0.8:
            raise AtlasRegistryError("stable memory candidate lacks cluster stability")
        rights_permit = governance.rights.permits(RightsUse.INTERNAL_RESEARCH)
        if assessment.rights_permit_internal_research != rights_permit:
            raise AtlasRegistryError("memory assessment misstates internal-research rights")
        contamination_cleared = all(
            all(result.contamination_checks.values()) for result in execution_results
        )
        if assessment.contamination_cleared != contamination_cleared:
            raise AtlasRegistryError("memory assessment misstates contamination evidence")
        sealed_excluded = suite_row.evaluation_class != EvaluationClass.SEALED_PROMOTION.value
        if assessment.sealed_content_excluded != sealed_excluded:
            raise AtlasRegistryError("memory assessment misstates sealed-content exclusion")
        if cluster_row.status != ReviewStatus.ADMITTED.value and assessment.eligible:
            raise AtlasRegistryError("only reviewed failure clusters can become memory candidates")
        _require_evidence_refs(
            assessment.evidence_refs,
            {
                assessment.failure_cluster_digest,
                assessment.source_suite_digest,
                assessment.research_execution_digest,
            },
            "memory eligibility",
        )
        row = AtlasMemoryEligibilityRow(
            assessment_id=assessment.assessment_id,
            failure_cluster_digest=assessment.failure_cluster_digest,
            source_suite_digest=assessment.source_suite_digest,
            research_execution_digest=assessment.research_execution_digest,
            eligible=assessment.eligible,
            record_digest=record_digest,
            record_json=payload,
            created_at=assessment.created_at,
        )
        session.add(row)
        await session.flush()
        return row


AtlasRegistry = CapabilityAtlasRegistry


def _payload(value: Any) -> dict[str, Any]:
    payload: dict[str, Any] = value.model_dump(mode="json")
    return payload


def _token_accounting_from_usage(usage: dict[str, int] | None) -> TokenAccounting:
    values = usage or {}
    input_tokens = values.get("input_tokens", values.get("prompt_tokens"))
    output_tokens = values.get("output_tokens", values.get("completion_tokens"))
    total_tokens = values.get("total_tokens")
    complete = (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and input_tokens >= 0
        and isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
        and output_tokens >= 0
        and isinstance(total_tokens, int)
        and not isinstance(total_tokens, bool)
        and total_tokens == input_tokens + output_tokens
    )
    if not complete:
        return TokenAccounting(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            counting_mode="provider_reported_unusable",
            missing_reason="provider usage was missing or internally inconsistent",
        )
    return TokenAccounting(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        counting_mode="provider_reported",
    )


def _require_same(
    stored_digest: str,
    candidate_digest: str,
    stored_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    label: str,
) -> None:
    if stored_digest != candidate_digest or stored_payload != candidate_payload:
        raise AtlasRegistryError(f"{label} identity conflicts with persisted content")


async def _require[RowT: Base](
    session: AsyncSession, row_type: type[RowT], key: Any, label: str
) -> RowT:
    row = await session.get(row_type, key)
    if row is None:
        raise AtlasRegistryError(f"{label} is not registered")
    return row


def _validated[RecordT: StrictRecord](
    model_type: type[RecordT], payload: dict[str, Any], label: str
) -> RecordT:
    try:
        return model_type.model_validate_json(_json_bytes(payload))
    except ValidationError as error:
        raise AtlasRegistryError(f"persisted {label} fails contract validation") from error


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return canonical_json_bytes(payload)


async def _require_artifact(session: AsyncSession, reference: ArtifactRef) -> ArtifactRow:
    row = await _require(session, ArtifactRow, reference.artifact_id, "artifact evidence")
    expected = (
        reference.digest,
        reference.uri,
        reference.media_type,
        reference.size_bytes,
        reference.restricted,
        reference.raw_data,
    )
    actual = (
        row.digest,
        row.uri,
        row.media_type,
        row.size_bytes,
        row.restricted,
        row.raw_data,
    )
    if actual != expected:
        raise AtlasRegistryError("artifact reference conflicts with the immutable catalog")
    return row


def _catalog_reference(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=row.artifact_id,
        digest=row.digest,
        uri=row.uri,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        restricted=row.restricted,
        raw_data=row.raw_data,
    )


def _same_timestamp(left: datetime, right: datetime) -> bool:
    """Compare source columns across SQLite's timezone-naive round trip."""
    return (left.replace(tzinfo=UTC) if left.tzinfo is None else left) == right


async def _request_artifacts(
    session: AsyncSession, request: AtlasTrialRequest
) -> tuple[ArtifactRef, ...]:
    execution_row = await _require_execution(session, request.research_execution_digest)
    refs = []
    for kind, digest in (
        ("edge", request.edge_preflight_evidence_digest),
        ("effort_mapping", request.effort_mapping_evidence_digest),
        ("tool", request.tool_preflight_evidence_digest),
    ):
        if digest is not None:
            refs.append(
                _catalog_reference(
                    await _require_preflight_evidence(
                        session,
                        digest=digest,
                        kind=kind,
                        request=request,
                        execution_row=execution_row,
                    )
                )
            )
    return tuple(refs)


async def _result_artifacts(
    session: AsyncSession, result: AtlasTrialResult, request: AtlasTrialRequest
) -> tuple[ArtifactRef, ...]:
    refs = list(await _request_artifacts(session, request))
    if result.response_artifact is not None:
        refs.append(result.response_artifact)
    if result.external_call_artifact is not None:
        call = await _require(session, ExternalCallRow, request.request_id, "captured Atlas call")
        if (
            call.status != "completed"
            or call.purpose != "capability_atlas"
            or call.run_id != request.run_id
            or call.request_hash != request.wire_request_digest
            or call.response_artifact_id != result.external_call_artifact.artifact_id
            or call.result_envelope_digest != result.external_call_artifact.digest
            or call.provider != result.generation_provider
            or call.result_model_id != result.generation_model_id
            or call.result_protocol != result.generation_protocol
            or call.result_raw_request_digest != result.raw_request_digest
            or call.result_raw_response_digest != result.raw_response_digest
            or call.result_capabilities_digest != result.capabilities_digest
            or call.result_latency_ms != result.latency_ms
            or _token_accounting_from_usage(call.result_usage) != result.tokens
            or call.request_artifact_id is None
            or call.result_output_text_digest is None
            or any(
                evidence.evaluated_output_digest != call.result_output_text_digest
                for evidence in result.verifier_evidence
            )
        ):
            raise AtlasRegistryError("Atlas retained call differs from its source trial")
        refs.extend(
            (
                result.external_call_artifact,
                _catalog_reference(
                    await _require(
                        session, ArtifactRow, call.request_artifact_id, "captured Atlas request"
                    )
                ),
            )
        )
    elif result.status in {TrialStatus.TIMEOUT, TrialStatus.INFRASTRUCTURE_FAILURE}:
        call = await _require(session, ExternalCallRow, request.request_id, "failed Atlas call")
        if (
            call.run_id != request.run_id
            or call.purpose != "capability_atlas"
            or call.request_hash != request.wire_request_digest
            or call.status not in {"failed_retryable", "failed_terminal"}
            or not call.error
            or call.response_artifact_id is not None
            or call.request_artifact_id is None
            or (
                result.status == TrialStatus.TIMEOUT
                and call.error.get("classification") != "timeout"
            )
        ):
            raise AtlasRegistryError("Atlas retained failure differs from its source trial")
        refs.append(
            _catalog_reference(
                await _require(
                    session, ArtifactRow, call.request_artifact_id, "failed Atlas request"
                )
            )
        )
        error_artifact_id = call.error.get("response_artifact_id")
        if error_artifact_id is not None:
            if not isinstance(error_artifact_id, str):
                raise AtlasRegistryError("Atlas failed call has an invalid error artifact identity")
            error_artifact = _catalog_reference(
                await _require(session, ArtifactRow, error_artifact_id, "failed Atlas response")
            )
            if error_artifact.digest != call.error.get("response_digest"):
                raise AtlasRegistryError("Atlas failed call error artifact differs from its digest")
            refs.append(error_artifact)
    elif result.status == TrialStatus.NOT_RUN:
        if await session.get(ExternalCallRow, request.request_id) is not None or not any(
            code.startswith("not_run:") for code in result.failure_codes
        ):
            raise AtlasRegistryError("Atlas not-run source disagrees with its no-dispatch record")
    refs.extend(result.grader_artifacts)
    for evidence in result.verifier_evidence:
        verifier = await _require(
            session, VerifierResultRow, evidence.evidence_id, "trial verifier evidence"
        )
        verifier_record = _validated(
            VerifierResult, verifier.record_json, "trial verifier evidence"
        )
        if (
            verifier.record_digest != sha256_digest(verifier.record_json)
            or verifier.record_digest != evidence.evidence_digest
            or verifier_record.result_id != evidence.evidence_id
            or verifier_record.verifier_id != verifier.verifier_id
            or verifier_record.verifier_version != verifier.verifier_version
            or verifier_record.disposition.value != verifier.disposition
            or verifier_record.deterministic != verifier.deterministic
            or verifier.verifier_id != evidence.verifier_id
            or verifier.verifier_version != evidence.verifier_version
            or verifier.disposition != evidence.disposition
            or verifier.deterministic != evidence.deterministic
            or verifier_record.scope != verifier.scope
            or not _same_timestamp(verifier.created_at, verifier_record.created_at)
        ):
            raise AtlasRegistryError("Atlas retained verifier differs from its source trial")
        refs.extend(evidence.artifact_refs)
    return tuple(refs)


async def _require_preflight_evidence(
    session: AsyncSession,
    *,
    digest: str,
    kind: str,
    request: AtlasTrialRequest,
    execution_row: ResearchExecutionRow,
) -> ArtifactRow:
    artifact = await session.scalar(select(ArtifactRow).where(ArtifactRow.digest == digest))
    if artifact is None or not artifact.raw_data:
        raise AtlasRegistryError(f"trial request {kind} preflight evidence is not registered")
    execution = _validated(
        ResearchExecutionManifest, execution_row.record_json, "preflight research execution"
    )
    metadata = artifact.metadata_json
    expected = {
        "kind": kind,
        "passed": True,
        "research_execution_digest": request.research_execution_digest,
        "provider": _expected_generation_provider(execution),
        "model_id": execution.student_model.model_id,
        "protocol": execution.student_model.protocol,
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise AtlasRegistryError(f"trial request {kind} preflight scope or disposition differs")
    if kind == "effort_mapping" and (
        metadata.get("scientific_effort") != request.effort
        or metadata.get("wire_effort") != request.sampling.get("reasoning_effort_wire_value")
    ):
        raise AtlasRegistryError("trial request effort preflight differs from wire mapping")
    if kind == "tool" and metadata.get("tool_manifest_digest") != request.tool_manifest_digest:
        raise AtlasRegistryError("trial request tool preflight differs from tool manifest")
    return artifact


async def _require_execution(session: AsyncSession, execution_digest: str) -> ResearchExecutionRow:
    row = await _require(session, ResearchExecutionRow, execution_digest, "research execution")
    _validated(ResearchExecutionManifest, row.record_json, "research execution")
    if sha256_digest(row.record_json) != execution_digest:
        raise AtlasRegistryError("research execution digest conflicts with stored manifest")
    return row


async def _require_profile(session: AsyncSession, profile_digest: str) -> HarnessProfileRow:
    row = await _require(session, HarnessProfileRow, profile_digest, "harness profile")
    _validated(HarnessProfile, row.record_json, "harness profile")
    if sha256_digest(row.record_json) != profile_digest:
        raise AtlasRegistryError("harness profile digest conflicts with stored manifest")
    return row


def _claim_coordinates(claim: BenchmarkClaim) -> tuple[str, ...]:
    return (
        claim.source_kind.value,
        claim.source_url,
        claim.publication_title,
        claim.model_id,
        claim.model_revision,
        claim.benchmark_id,
        claim.benchmark_version,
        claim.split,
        claim.metric_id,
        claim.reported_unit,
    )


def _require_acyclic_ontology(manifest: OntologyManifest) -> None:
    parents = {node.node_id: set(node.parent_ids) for node in manifest.nodes}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise AtlasRegistryError("ontology parent graph contains a cycle")
        if node_id in visited:
            return
        visiting.add(node_id)
        for parent_id in parents[node_id]:
            visit(parent_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in parents:
        visit(node_id)


def _campaign_condition(row: AtlasCampaignConditionRow) -> CampaignCondition:
    return _validated(CampaignCondition, row.record_json, "campaign condition")


def _validate_condition_controls(
    condition: CampaignCondition,
    execution_row: ResearchExecutionRow,
    profile_row: HarnessProfileRow,
) -> None:
    execution = _validated(
        ResearchExecutionManifest, execution_row.record_json, "research execution"
    )
    profile = _validated(HarnessProfile, profile_row.record_json, "harness profile")
    if condition.required_execution_digest not in {None, execution_row.execution_digest}:
        raise AtlasRegistryError("campaign condition requires another research execution")
    if profile.tier != condition.harness_tier:
        raise AtlasRegistryError("campaign condition harness tier differs from execution")
    if (
        condition.required_checkpoint_id is not None
        and execution.student_model.checkpoint.component_id != condition.required_checkpoint_id
    ):
        raise AtlasRegistryError("campaign condition checkpoint differs from execution")
    if (
        condition.required_quantization_id is not None
        and execution.student_model.quantization.component_id != condition.required_quantization_id
    ):
        raise AtlasRegistryError("campaign condition quantization differs from execution")
    if execution.student_model.protocol != condition.required_protocol:
        raise AtlasRegistryError("campaign condition protocol differs from execution")
    try:
        validate_profile_against_condition(profile=profile, condition=condition)
    except ValueError as error:
        raise AtlasRegistryError("campaign factors differ from the materialized harness") from error


def _validate_trial_request_factors(
    *,
    request: AtlasTrialRequest,
    condition: CampaignCondition,
    execution_row: ResearchExecutionRow,
    profile_row: HarnessProfileRow,
) -> None:
    execution = _validated(
        ResearchExecutionManifest, execution_row.record_json, "research execution"
    )
    profile = _validated(HarnessProfile, profile_row.record_json, "harness profile")
    profile_context = profile.context.effective_input_limit_tokens
    context_mismatch = (
        request.context_limit_tokens != profile_context
        if "context" in condition.factor_levels
        else profile_context is not None and request.context_limit_tokens > profile_context
    )
    if context_mismatch:
        raise AtlasRegistryError(
            "trial request context differs from the materialized harness: "
            f"request={request.context_limit_tokens}, profile={profile_context}"
        )
    for observed, limit, label in (
        (request.max_output_tokens, profile.budgets.output_tokens.value, "output-token"),
        (request.action_budget, profile.budgets.actions.value, "action"),
    ):
        mismatch = (
            observed != limit if "budget" in condition.factor_levels else observed > (limit or 0)
        )
        if limit is not None and mismatch:
            raise AtlasRegistryError(f"trial request {label} budget differs from the harness")
    expected_tool_ids = tuple(
        sorted(
            tool.component_id
            for tool in profile.tools
            if "no_tool" not in tool.component_id.casefold().replace("-", "_")
            and tool.component_id.casefold() not in {"none", "tool.none"}
        )
    )
    if request.tool_ids != expected_tool_ids:
        raise AtlasRegistryError("trial request tools differ from the materialized harness")
    sampling_level = condition.factor_levels.get("sampling")
    temperature = request.sampling.get("temperature")
    if sampling_level == "deterministic" and temperature not in {"0", "0.0"}:
        raise AtlasRegistryError("trial request violates deterministic sampling")
    if sampling_level == "stochastic" and temperature in {None, "0", "0.0", "unspecified"}:
        raise AtlasRegistryError("trial request violates stochastic sampling")
    effort_level = condition.factor_levels.get("effort")
    expected_effort = (
        {"e0": 0.0, "e50": 0.5, "e99": 0.99, "max": 0.99}.get(effort_level)
        if effort_level is not None
        else None
    )
    if expected_effort is not None and request.effort != expected_effort:
        raise AtlasRegistryError("trial request scientific effort differs from the condition")
    wire_effort = request.sampling.get("reasoning_effort_wire_value")
    if effort_level == "e0" and wire_effort != "none":
        raise AtlasRegistryError("trial request e0 effort must use the wire value none")
    if effort_level in {"e50", "e99", "max"} and (
        wire_effort in {None, "none", "mapping-required", "unspecified"}
        or request.effort_mapping_evidence_digest is None
    ):
        raise AtlasRegistryError("trial request nonzero effort lacks a validated wire mapping")
    if execution.harness_parameters.get("reasoning_effort", effort_level) != effort_level:
        raise AtlasRegistryError("trial request effort differs from the research execution")


async def _prior_result_digests(
    session: AsyncSession,
    *,
    campaign_digest: str,
    condition_id: str,
    suite_digest: str,
    before_decision_sequence: int,
) -> tuple[str, ...]:
    rows = (
        await session.execute(
            select(AtlasTrialRequestRow, AtlasTrialResultRow)
            .join(
                AtlasAllocationRow,
                AtlasAllocationRow.allocation_id == AtlasTrialRequestRow.allocation_id,
            )
            .outerjoin(
                AtlasTrialResultRow,
                AtlasTrialResultRow.request_id == AtlasTrialRequestRow.request_id,
            )
            .where(
                AtlasAllocationRow.campaign_digest == campaign_digest,
                AtlasAllocationRow.condition_id == condition_id,
                AtlasAllocationRow.suite_digest == suite_digest,
                AtlasAllocationRow.decision_sequence < before_decision_sequence,
            )
        )
    ).all()
    latest: dict[str, tuple[int, AtlasTrialResultRow | None]] = {}
    for request, result in rows:
        current = latest.get(request.allocation_id)
        if current is None or request.attempt_index > current[0]:
            latest[request.allocation_id] = (request.attempt_index, result)
    if len(latest) != before_decision_sequence or any(
        result is None for _, result in latest.values()
    ):
        raise AtlasRegistryError(
            "adaptive allocation requires one terminal latest result per prior decision"
        )
    return tuple(
        sorted(result.result_digest for _, result in latest.values() if result is not None)
    )


async def _adaptive_expected_allocation(
    session: AsyncSession,
    *,
    allocation: TrialAllocation,
    suite_row: AtlasSuiteRow,
    campaign_suite: AtlasCampaignSuiteRow,
) -> TrialAllocation:
    run_rows = (
        await session.scalars(
            select(AtlasRunManifestRow).where(
                AtlasRunManifestRow.campaign_digest == allocation.campaign_digest,
                AtlasRunManifestRow.condition_id == allocation.condition_id,
                AtlasRunManifestRow.suite_digest == allocation.suite_digest,
                AtlasRunManifestRow.adaptive.is_(True),
            )
        )
    ).all()
    if len(run_rows) != 1:
        raise AtlasRegistryError("adaptive allocation requires exactly one frozen run envelope")
    run_manifest = _validated(AtlasRunManifest, run_rows[0].record_json, "adaptive run")
    campaign_row = await _require(
        session, AtlasCampaignRow, allocation.campaign_digest, "adaptive campaign"
    )
    campaign = _validated(AtlasCampaignManifest, campaign_row.record_json, "adaptive campaign")
    stop_rule = next(
        (rule for rule in campaign.stop_rules if rule.rule_id == run_manifest.stop_rule_id),
        None,
    )
    if stop_rule is None:
        raise AtlasRegistryError("adaptive run stop rule is not registered in its campaign")

    prior_allocations = (
        await session.scalars(
            select(AtlasAllocationRow).where(
                AtlasAllocationRow.campaign_digest == allocation.campaign_digest,
                AtlasAllocationRow.condition_id == allocation.condition_id,
                AtlasAllocationRow.suite_digest == allocation.suite_digest,
                AtlasAllocationRow.decision_sequence < allocation.decision_sequence,
            )
        )
    ).all()
    prior_ids = {row.allocation_id for row in prior_allocations}
    latest: dict[str, tuple[int, AtlasTrialResultRow | None]] = {}
    if prior_ids:
        rows = (
            await session.execute(
                select(AtlasTrialRequestRow, AtlasTrialResultRow)
                .outerjoin(
                    AtlasTrialResultRow,
                    AtlasTrialResultRow.request_id == AtlasTrialRequestRow.request_id,
                )
                .where(AtlasTrialRequestRow.allocation_id.in_(prior_ids))
            )
        ).all()
        for request, result in rows:
            current = latest.get(request.allocation_id)
            if current is None or request.attempt_index > current[0]:
                latest[request.allocation_id] = (request.attempt_index, result)
    if set(latest) != prior_ids or any(result is None for _, result in latest.values()):
        raise AtlasRegistryError(
            "adaptive allocation requires a terminal latest result for every prior decision"
        )

    results_by_item: dict[str, list[AtlasTrialResult]] = {}
    for prior in prior_allocations:
        result_row = latest[prior.allocation_id][1]
        assert result_row is not None
        result = _validated(AtlasTrialResult, result_row.record_json, "adaptive prior result")
        results_by_item.setdefault(prior.item_digest, []).append(result)
    item_rows = (
        await session.execute(
            select(AtlasSuiteItemRow, AtlasItemRow)
            .join(AtlasItemRow, AtlasItemRow.item_digest == AtlasSuiteItemRow.item_digest)
            .where(AtlasSuiteItemRow.suite_digest == allocation.suite_digest)
        )
    ).all()
    candidates: list[BoundaryCandidate] = []
    for _, item_row in item_rows:
        results = results_by_item.get(item_row.item_digest, [])
        successes = sum(result.success is True for result in results)
        failures = sum(result.success is False for result in results)
        missing = len(results) - successes - failures
        infrastructure = sum(
            result.status in {TrialStatus.TIMEOUT, TrialStatus.INFRASTRUCTURE_FAILURE}
            for result in results
        )
        contaminated = sum(result.status == TrialStatus.CONTAMINATED for result in results)
        candidates.append(
            BoundaryCandidate(
                item_digest=item_row.item_digest,
                difficulty=item_row.difficulty,
                planned_trials=campaign_suite.trials_per_item,
                successes=successes,
                failures=failures,
                missing_trials=missing,
                infrastructure_failures=infrastructure,
                contaminated_trials=contaminated,
                prior_result_digests=tuple(sorted(result.result_digest for result in results)),
            )
        )
    if len(candidates) != campaign_suite.planned_item_count:
        raise AtlasRegistryError("adaptive run candidates differ from the frozen suite")
    assessment = assess_stop_rule(
        stop_rule,
        successes=sum(candidate.successes for candidate in candidates),
        failures=sum(candidate.failures for candidate in candidates),
        infrastructure_failures=sum(candidate.infrastructure_failures for candidate in candidates),
        contaminated_trials=sum(candidate.contaminated_trials for candidate in candidates),
        other_missing_trials=sum(
            candidate.missing_trials
            - candidate.infrastructure_failures
            - candidate.contaminated_trials
            for candidate in candidates
        ),
    )
    if assessment.decision != StopDecision.CONTINUE:
        raise AtlasRegistryError(
            f"adaptive allocation is closed by stop rule: {assessment.decision.value}"
        )
    suite = _validated(AtlasSuiteManifest, suite_row.record_json, "adaptive suite")
    return allocate_near_boundary(
        campaign_digest=allocation.campaign_digest,
        condition_id=allocation.condition_id,
        suite_digest=allocation.suite_digest,
        suite_status=suite.status,
        evaluation_class=suite.evaluation_class,
        candidates=tuple(candidates),
        boundary_probability=stop_rule.boundary_probability,
        created_at=allocation.created_at,
    )


async def _result_by_digest(session: AsyncSession, result_digest: str) -> AtlasTrialResultRow:
    row = await session.scalar(
        select(AtlasTrialResultRow).where(AtlasTrialResultRow.result_digest == result_digest)
    )
    if row is None:
        raise AtlasRegistryError("trial result evidence is not registered")
    result = _validated(AtlasTrialResult, row.record_json, "trial result")
    if result.result_digest != result_digest:
        raise AtlasRegistryError("trial result digest conflicts with stored content")
    return row


@dataclass(frozen=True)
class _OutcomeCounts:
    planned: int
    observed_results: tuple[AtlasTrialResult, ...]
    observed_digests: tuple[str, ...]
    infrastructure: int
    contaminated: int
    other_excluded: int
    missing_terminal: int


async def _terminal_outcomes(
    session: AsyncSession,
    *,
    campaign_digest: str,
    condition_id: str,
    suite_digest: str,
    execution_digest: str,
) -> _OutcomeCounts:
    campaign_suite = await _require(
        session,
        AtlasCampaignSuiteRow,
        (campaign_digest, suite_digest),
        "snapshot campaign suite",
    )
    expected_planned = campaign_suite.planned_item_count * campaign_suite.trials_per_item
    allocations = (
        await session.execute(
            select(AtlasAllocationRow).where(
                AtlasAllocationRow.campaign_digest == campaign_digest,
                AtlasAllocationRow.condition_id == condition_id,
                AtlasAllocationRow.suite_digest == suite_digest,
            )
        )
    ).scalars()
    allocation_ids = {allocation.allocation_id for allocation in allocations}
    if len(allocation_ids) > expected_planned:
        raise AtlasRegistryError("snapshot allocations exceed the frozen campaign trial design")
    latest: dict[str, tuple[int, AtlasTrialResultRow | None]] = {}
    if allocation_ids:
        rows = (
            await session.execute(
                select(AtlasTrialRequestRow, AtlasTrialResultRow)
                .outerjoin(
                    AtlasTrialResultRow,
                    AtlasTrialResultRow.request_id == AtlasTrialRequestRow.request_id,
                )
                .where(
                    AtlasTrialRequestRow.allocation_id.in_(allocation_ids),
                    AtlasTrialRequestRow.research_execution_digest == execution_digest,
                )
            )
        ).all()
        for request, result in rows:
            current = latest.get(request.allocation_id)
            if current is None or request.attempt_index > current[0]:
                latest[request.allocation_id] = (request.attempt_index, result)
    observed_statuses = {
        TrialStatus.VERIFIED_SUCCESS.value,
        TrialStatus.VERIFIED_FAILURE.value,
        TrialStatus.PARTIAL.value,
        TrialStatus.ABSTAINED.value,
        TrialStatus.MALFORMED.value,
    }
    infrastructure_statuses = {
        TrialStatus.TIMEOUT.value,
        TrialStatus.INFRASTRUCTURE_FAILURE.value,
        TrialStatus.NOT_RUN.value,
    }
    terminal = [value[1] for value in latest.values()]
    observed_results = tuple(
        sorted(
            (
                _validated(AtlasTrialResult, result.record_json, "terminal trial result")
                for result in terminal
                if result is not None and result.status in observed_statuses
            ),
            key=lambda value: value.result_digest,
        )
    )
    infrastructure = sum(
        result is not None and result.status in infrastructure_statuses for result in terminal
    )
    contaminated = sum(
        result is not None and result.status == TrialStatus.CONTAMINATED.value
        for result in terminal
    )
    other_excluded_statuses = {
        TrialStatus.UNSCORABLE.value,
        TrialStatus.VERIFIER_FAILURE.value,
        TrialStatus.PARSER_FAILURE.value,
    }
    other_excluded = sum(
        result is not None and result.status in other_excluded_statuses for result in terminal
    )
    missing_terminal = len(allocation_ids) - sum(result is not None for result in terminal)
    return _OutcomeCounts(
        planned=expected_planned,
        observed_results=observed_results,
        observed_digests=tuple(result.result_digest for result in observed_results),
        infrastructure=infrastructure,
        contaminated=contaminated,
        other_excluded=other_excluded,
        missing_terminal=missing_terminal + expected_planned - len(allocation_ids),
    )


def _success_metric(metric_id: str, counts: _OutcomeCounts) -> MetricEstimate:
    if metric_id not in {"success_rate", "verified_success_rate"}:
        raise AtlasRegistryError(
            "Atlas v0 only admits registry-produced binary success-rate observations"
        )
    observed = len(counts.observed_results)
    successes = sum(result.success is True for result in counts.observed_results)
    if observed:
        lower, upper = wilson_interval(successes, observed, confidence_level=0.95)
        value: float | None = successes / observed
        confidence: float | None = 0.95
        missing_reason: str | None = None
    else:
        value = None
        lower = None
        upper = None
        confidence = None
        missing_reason = "no scorable binary outcomes were observed"
    return MetricEstimate(
        metric_id=metric_id,
        value=value,
        lower=lower,
        upper=upper,
        confidence_level=confidence,
        planned_trials=counts.planned,
        observed_trials=observed,
        missing_trials=counts.planned - observed,
        infrastructure_failures=counts.infrastructure,
        contaminated_trials=counts.contaminated,
        evidence_result_digests=counts.observed_digests,
        missing_reason=missing_reason,
    )


async def _recompute_curve(
    session: AsyncSession,
    *,
    campaign_digest: str,
    execution_digest: str,
    family_id: str,
    condition_id: str,
    suite_digests: tuple[str, ...],
    boundary_probability: float,
) -> CapabilityCurve:
    design_rows = (
        await session.execute(
            select(AtlasSuiteItemRow, AtlasItemRow, AtlasCampaignSuiteRow)
            .join(AtlasItemRow, AtlasItemRow.item_digest == AtlasSuiteItemRow.item_digest)
            .join(
                AtlasCampaignSuiteRow,
                AtlasCampaignSuiteRow.suite_digest == AtlasSuiteItemRow.suite_digest,
            )
            .where(
                AtlasCampaignSuiteRow.campaign_digest == campaign_digest,
                AtlasSuiteItemRow.suite_digest.in_(suite_digests),
                AtlasItemRow.family_id == family_id,
            )
        )
    ).all()
    if not design_rows:
        raise AtlasRegistryError("snapshot curve family has no frozen suite items")
    bins: dict[float, dict[str, Any]] = {}
    for _, item_row, campaign_suite in design_rows:
        values = bins.setdefault(
            item_row.difficulty,
            {"planned": 0, "successes": 0, "failures": 0, "evidence": []},
        )
        values["planned"] += campaign_suite.trials_per_item
    allocation_rows = (
        await session.execute(
            select(AtlasAllocationRow, AtlasItemRow)
            .join(AtlasItemRow, AtlasItemRow.item_digest == AtlasAllocationRow.item_digest)
            .where(
                AtlasAllocationRow.campaign_digest == campaign_digest,
                AtlasAllocationRow.condition_id == condition_id,
                AtlasAllocationRow.suite_digest.in_(suite_digests),
                AtlasItemRow.family_id == family_id,
            )
        )
    ).all()
    allocation_ids = {allocation.allocation_id for allocation, _ in allocation_rows}
    latest: dict[str, tuple[int, AtlasTrialResultRow | None]] = {}
    if allocation_ids:
        rows = (
            await session.execute(
                select(AtlasTrialRequestRow, AtlasTrialResultRow)
                .outerjoin(
                    AtlasTrialResultRow,
                    AtlasTrialResultRow.request_id == AtlasTrialRequestRow.request_id,
                )
                .where(
                    AtlasTrialRequestRow.allocation_id.in_(allocation_ids),
                    AtlasTrialRequestRow.research_execution_digest == execution_digest,
                )
            )
        ).all()
        for request_row, result_row in rows:
            current = latest.get(request_row.allocation_id)
            if current is None or request_row.attempt_index > current[0]:
                latest[request_row.allocation_id] = (request_row.attempt_index, result_row)
    for allocation, item_row in allocation_rows:
        values = bins[item_row.difficulty]
        result_row = latest.get(allocation.allocation_id, (0, None))[1]
        if result_row is None:
            continue
        result = _validated(AtlasTrialResult, result_row.record_json, "curve trial result")
        if result.success is True:
            values["successes"] += 1
            values["evidence"].append(result.result_digest)
        elif result.success is False:
            values["failures"] += 1
            values["evidence"].append(result.result_digest)
    difficulty_bins = tuple(
        DifficultyBin(
            difficulty=difficulty,
            planned_trials=int(values["planned"]),
            successes=int(values["successes"]),
            failures=int(values["failures"]),
            missing_trials=(
                int(values["planned"]) - int(values["successes"]) - int(values["failures"])
            ),
            evidence_result_digests=tuple(sorted(values["evidence"])),
        )
        for difficulty, values in sorted(bins.items())
    )
    return build_capability_curve(
        family_id=family_id,
        condition_id=condition_id,
        bins=difficulty_bins,
        boundary_probability=boundary_probability,
        confidence_level=0.95,
    )


async def _comparison_deltas(
    session: AsyncSession,
    left: AtlasSnapshot,
    right: AtlasSnapshot,
    declared: tuple[ComparisonMetricDelta, ...],
) -> tuple[
    tuple[ComparisonMetricDelta, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    def observations(snapshot: AtlasSnapshot) -> dict[tuple[str, str, str], MetricEstimate]:
        return {
            (item.condition_id, item.suite_digest, item.metric.metric_id): item.metric
            for item in snapshot.local_observations
        }

    left_metrics = observations(left)
    right_metrics = observations(right)
    deltas: list[ComparisonMetricDelta] = []
    regressions: list[str] = []
    improvements: list[str] = []
    for declaration in declared:
        left_key = (
            declaration.left_condition_id,
            declaration.left_suite_digest,
            declaration.metric_id,
        )
        right_key = (
            declaration.right_condition_id,
            declaration.right_suite_digest,
            declaration.metric_id,
        )
        left_metric = left_metrics.get(left_key)
        right_metric = right_metrics.get(right_key)
        if left_metric is None or right_metric is None:
            raise AtlasRegistryError("comparison declares a metric pair absent from its snapshots")
        if declaration.left_suite_digest != declaration.right_suite_digest:
            left_items = set(
                (
                    await session.scalars(
                        select(AtlasSuiteItemRow.item_digest).where(
                            AtlasSuiteItemRow.suite_digest == declaration.left_suite_digest
                        )
                    )
                ).all()
            )
            right_items = set(
                (
                    await session.scalars(
                        select(AtlasSuiteItemRow.item_digest).where(
                            AtlasSuiteItemRow.suite_digest == declaration.right_suite_digest
                        )
                    )
                ).all()
            )
            if not left_items or left_items != right_items:
                raise AtlasRegistryError(
                    "comparison metric pairs require identical frozen item content"
                )
        values = (
            left_metric.value,
            left_metric.lower,
            left_metric.upper,
            left_metric.confidence_level,
            right_metric.value,
            right_metric.lower,
            right_metric.upper,
            right_metric.confidence_level,
        )
        if any(value is None for value in values):
            continue
        assert left_metric.value is not None
        assert left_metric.lower is not None
        assert left_metric.upper is not None
        assert left_metric.confidence_level is not None
        assert right_metric.value is not None
        assert right_metric.lower is not None
        assert right_metric.upper is not None
        assert right_metric.confidence_level is not None
        if left_metric.confidence_level != right_metric.confidence_level:
            raise AtlasRegistryError("comparison metrics use different confidence levels")
        coordinate = declaration.coordinate
        delta = ComparisonMetricDelta(
            left_condition_id=declaration.left_condition_id,
            left_suite_digest=declaration.left_suite_digest,
            right_condition_id=declaration.right_condition_id,
            right_suite_digest=declaration.right_suite_digest,
            metric_id=declaration.metric_id,
            left_value=left_metric.value,
            right_value=right_metric.value,
            delta=right_metric.value - left_metric.value,
            lower=right_metric.lower - left_metric.upper,
            upper=right_metric.upper - left_metric.lower,
            confidence_level=left_metric.confidence_level,
            evidence_result_digests=tuple(
                sorted(
                    {
                        *left_metric.evidence_result_digests,
                        *right_metric.evidence_result_digests,
                    }
                )
            ),
        )
        deltas.append(delta)
        if delta.upper < 0.0:
            regressions.append(coordinate)
        elif delta.lower > 0.0:
            improvements.append(coordinate)
    return tuple(deltas), tuple(sorted(regressions)), tuple(sorted(improvements))


@dataclass(frozen=True)
class _ClusterEvidence:
    results: tuple[AtlasTrialResult, ...]
    exemplar_results: tuple[AtlasTrialResult, ...]
    harness_profile_digests: frozenset[str]


async def _eligibility_scope(
    session: AsyncSession, cluster_digest: str, suite_digest: str
) -> tuple[
    AtlasFailureClusterRow,
    FailureCluster,
    AtlasSuiteRow,
    DatasetGovernance,
]:
    cluster_row = await _require(
        session, AtlasFailureClusterRow, cluster_digest, "eligibility failure cluster"
    )
    cluster = _validated(FailureCluster, cluster_row.record_json, "failure cluster")
    suite_row = await _require(session, AtlasSuiteRow, suite_digest, "eligibility source suite")
    governance_row = await _require(
        session,
        AtlasDatasetGovernanceRow,
        suite_row.governance_id,
        "source-suite governance",
    )
    governance = _validated(DatasetGovernance, governance_row.record_json, "dataset governance")
    member_suites: set[str] = set()
    for result_digest in cluster.member_result_digests:
        result_row = await _result_by_digest(session, result_digest)
        request_row = await _require(
            session, AtlasTrialRequestRow, result_row.request_id, "cluster trial request"
        )
        member_suites.add(request_row.suite_digest)
    if suite_digest not in member_suites:
        raise AtlasRegistryError("eligibility source suite has no cluster evidence")
    return cluster_row, cluster, suite_row, governance


async def _cluster_evidence(session: AsyncSession, cluster: FailureCluster) -> _ClusterEvidence:
    results: list[AtlasTrialResult] = []
    exemplars: list[AtlasTrialResult] = []
    profiles: set[str] = set()
    exemplar_digests = set(cluster.exemplar_result_digests)
    for result_digest in cluster.member_result_digests:
        result_row = await _result_by_digest(session, result_digest)
        result = _validated(AtlasTrialResult, result_row.record_json, "trial result")
        results.append(result)
        if result_digest in exemplar_digests:
            exemplars.append(result)
        execution = await _require_execution(session, result.research_execution_digest)
        profiles.add(execution.harness_profile_digest)
    return _ClusterEvidence(
        results=tuple(results),
        exemplar_results=tuple(exemplars),
        harness_profile_digests=frozenset(profiles),
    )


def _model_failure_confirmed(results: tuple[AtlasTrialResult, ...]) -> bool:
    return bool(results) and all(
        result.status == TrialStatus.VERIFIED_FAILURE
        and result.failure_origin == FailureOrigin.MODEL
        for result in results
    )


def _verifier_authority_confirmed(results: tuple[AtlasTrialResult, ...]) -> bool:
    allowed = {"deterministic", "kernel", "environment", "human"}
    return bool(results) and all(
        result.primary_authority is not None and result.primary_authority.value in allowed
        for result in results
    )


def _training_lanes(values: tuple[str, ...]) -> tuple[TrainingLane, ...]:
    try:
        lanes = tuple(TrainingLane(value) for value in values)
    except ValueError as error:
        raise AtlasRegistryError("training eligibility cites an unknown compiler lane") from error
    if len(lanes) != len(set(lanes)):
        raise AtlasRegistryError("training eligibility lanes must be unique")
    if tuple(sorted(lanes, key=lambda lane: lane.value)) != lanes:
        raise AtlasRegistryError("training eligibility lanes must use canonical order")
    return lanes


def _rights_use_for_lane(lane: TrainingLane) -> RightsUse:
    mapping = {
        TrainingLane.CONTINUED_PRETRAINING: RightsUse.CONTINUED_PRETRAINING,
        TrainingLane.SFT: RightsUse.SFT,
        TrainingLane.PREFERENCE: RightsUse.PREFERENCE,
        TrainingLane.RLVR: RightsUse.RLVR,
        TrainingLane.PROCESS: RightsUse.PROCESS,
        TrainingLane.EVALUATION_ONLY: RightsUse.EVALUATION,
    }
    return mapping[lane]


@dataclass(frozen=True)
class _CampaignPartitions:
    promotion: bool
    adaptive: bool


async def _campaign_partitions(session: AsyncSession, suite_digest: str) -> _CampaignPartitions:
    rows = (
        await session.execute(
            select(AtlasCampaignRow)
            .join(
                AtlasCampaignSuiteRow,
                AtlasCampaignSuiteRow.campaign_digest == AtlasCampaignRow.campaign_digest,
            )
            .where(AtlasCampaignSuiteRow.suite_digest == suite_digest)
        )
    ).scalars()
    promotion = False
    adaptive = False
    for row in rows:
        campaign = _validated(AtlasCampaignManifest, row.record_json, "Atlas campaign")
        promotion = promotion or suite_digest in campaign.promotion_suite_digests
        adaptive = adaptive or suite_digest in campaign.adaptive_suite_digests
    return _CampaignPartitions(promotion=promotion, adaptive=adaptive)


def _require_evidence_refs(evidence_refs: tuple[str, ...], required: set[str], label: str) -> None:
    if not required.issubset(set(evidence_refs)):
        raise AtlasRegistryError(f"{label} omits required immutable evidence references")
