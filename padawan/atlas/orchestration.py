"""Predeclared Atlas run planning atop Padawan's authoritative generation executor.

This module performs no I/O.  It freezes allocations and model requests, creates an immutable
run envelope, and translates one frozen request into Padawan's existing ``GenerationRequest``.
The latter can then be executed through ``IdempotentGenerationExecutor`` so Atlas does not grow
a second external-call or retry system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import Field, FiniteFloat, model_validator

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.atlas.adapters import AdapterEvaluation
from padawan.atlas.boundary import (
    BoundaryCandidate,
    StopAssessment,
    StopDecision,
    allocate_near_boundary,
    assess_stop_rule,
)
from padawan.atlas.contracts import (
    AdapterDescriptor,
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasRunKind,
    AtlasRunManifest,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    AtlasTrialResult,
    CampaignCondition,
    CampaignExecutionBinding,
    CampaignSuiteBinding,
    EvaluationClass,
    FailureOrigin,
    OutcomeEvidence,
    StopRule,
    SuiteStatus,
    TokenAccounting,
    TrialAllocation,
    TrialStatus,
    content_id,
)
from padawan.domains.contracts import VerifierDisposition
from padawan.domains.temporal_grounding.contracts import TemporalScenarioManifest
from padawan.models.contracts import (
    ArtifactRef,
    NonEmpty,
    SamplingConfiguration,
    Sha256,
    StrictRecord,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.research_contracts import HarnessProfile, ResearchExecutionManifest
from padawan.orchestration.external_calls import _serialize_result
from padawan.temporal.renderer import render_temporal_frame


class FixedRunConfiguration(StrictRecord):
    """Wire-level controls for one fixed suite run.

    ``reasoning_effort_wire_value`` is deliberately separate from the campaign's scientific
    effort label.  A vendor-reported 0.99 label must not be sent to a serving endpoint until an
    endpoint-specific mapping has been validated and recorded here.
    """

    instructions: NonEmpty
    max_output_tokens_per_request: int = Field(gt=0)
    action_budget_per_request: int = Field(gt=0)
    max_cost_usd_per_request: FiniteFloat = Field(ge=0.0)
    max_retry_requests: int = Field(default=0, ge=0)
    temperature: FiniteFloat | None = None
    top_p: FiniteFloat | None = None
    reasoning_effort_wire_value: NonEmpty | None = None
    scientific_effort: FiniteFloat | None = Field(default=None, ge=0.0)
    effort_mapping_evidence_digest: Sha256 | None = None
    edge_preflight_evidence_digest: Sha256
    tool_preflight_evidence_digest: Sha256 | None = None
    base_seed: int
    tool_ids: tuple[NonEmpty, ...] = ()
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: NonEmpty | dict[str, Any] | None = None
    schema_name: NonEmpty | None = None
    json_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def tools_are_bound(self) -> FixedRunConfiguration:
        if tuple(sorted(self.tool_ids)) != self.tool_ids or len(self.tool_ids) != len(
            set(self.tool_ids)
        ):
            raise ValueError("fixed-run tool IDs must be unique and canonical")
        if bool(self.tool_ids) != bool(self.tools):
            raise ValueError("tool identities and wire manifests must be present together")
        if self.tool_choice is not None and not self.tools:
            raise ValueError("tool choice requires a declared tool surface")
        if (self.schema_name is None) != (self.json_schema is None):
            raise ValueError("response schema name and JSON schema must be present together")
        if self.tools and self.tool_preflight_evidence_digest is None:
            raise ValueError("tool-enabled runs require immutable edge preflight evidence")
        if self.temperature is not None and self.temperature < 0.0:
            raise ValueError("temperature cannot be negative")
        if self.top_p is not None and not 0.0 < self.top_p <= 1.0:
            raise ValueError("top_p must lie in (0, 1]")
        return self

    @property
    def tool_manifest_digest(self) -> str:
        return sha256_digest({"tools": self.tools, "tool_choice": self.tool_choice})

    @property
    def instructions_digest(self) -> str:
        return sha256_digest(self.instructions)

    @property
    def response_format_digest(self) -> str:
        return sha256_digest({"schema_name": self.schema_name, "json_schema": self.json_schema})


@dataclass(frozen=True)
class FixedAtlasRunPlan:
    manifest: AtlasRunManifest
    allocations: tuple[TrialAllocation, ...]
    requests: tuple[AtlasTrialRequest, ...]


@dataclass(frozen=True)
class AdaptiveAtlasRunPlan:
    """A frozen adaptive envelope; future prompts remain policy-selected, never predeclared."""

    manifest: AtlasRunManifest
    stop_rule: StopRule
    trials_per_item: int
    boundary_probability: float


@dataclass(frozen=True)
class AdaptiveTrialMaterialization:
    stop_assessment: StopAssessment
    allocation: TrialAllocation
    request: AtlasTrialRequest


def plan_fixed_suite_run(
    *,
    campaign: AtlasCampaignManifest,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    binding: CampaignExecutionBinding,
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
    run_id: str,
    adapter: AdapterDescriptor,
    configuration: FixedRunConfiguration,
    created_at: datetime,
) -> FixedAtlasRunPlan:
    """Freeze every item/replicate before a fixed or promotion run sees model output."""

    campaign_binding = _validate_run_coordinates(
        campaign=campaign,
        condition=condition,
        suite=suite,
        binding=binding,
        execution=execution,
        profile=profile,
    )
    if campaign_binding.adaptive:
        raise ValueError("fixed run planning cannot use an adaptive suite binding")
    if adapter.kind != suite.adapter_kind:
        raise ValueError("Atlas adapter kind differs from the frozen suite")
    item_modalities = {modality for item in suite.items for modality in item.modalities}
    if not item_modalities.issubset(adapter.supported_modalities):
        raise ValueError("Atlas adapter does not support every frozen item modality")
    if campaign_binding.planned_item_count != len(suite.item_digests):
        raise ValueError("fixed run planning must include every frozen suite item")
    planned_requests = campaign_binding.planned_item_count * campaign_binding.trials_per_item
    if planned_requests > condition.max_requests:
        raise ValueError("fixed trial plan exceeds the condition request ceiling")
    context_limit = profile.context.effective_input_limit_tokens
    if context_limit is None:
        raise ValueError("Atlas requests require an evidence-backed effective context limit")
    input_allocation = context_limit
    if any(
        tool.component_id == "tool.compile_and_run" and tool.version == "2"
        for tool in profile.tools
    ):
        # A v2 root owns a multi-turn episode; per-turn context remains separately bound.
        input_allocation = int(profile.budgets.input_tokens.value or context_limit)
    request_capacity = planned_requests + configuration.max_retry_requests
    if request_capacity > condition.max_requests:
        raise ValueError("fixed run including retries exceeds the condition request ceiling")
    if request_capacity * input_allocation > condition.max_input_tokens:
        raise ValueError("fixed trial plan exceeds the condition input-token ceiling")
    if request_capacity * configuration.max_output_tokens_per_request > condition.max_output_tokens:
        raise ValueError("fixed trial plan exceeds the condition output-token ceiling")
    if request_capacity * configuration.action_budget_per_request > condition.max_actions:
        raise ValueError("fixed trial plan exceeds the condition action ceiling")
    if request_capacity * configuration.max_cost_usd_per_request > condition.max_cost_usd:
        raise ValueError("fixed trial plan exceeds the condition cost ceiling")
    _validate_wire_configuration(
        condition=condition,
        profile=profile,
        configuration=configuration,
    )

    items = {item.item_digest: item for item in suite.items}
    allocations: list[TrialAllocation] = []
    requests: list[AtlasTrialRequest] = []
    for item_position, item_digest in enumerate(suite.item_digests):
        item = items.get(item_digest)
        if item is None:
            raise ValueError("fixed suite item content is unavailable")
        for trial_index in range(campaign_binding.trials_per_item):
            allocation = _fixed_allocation(
                campaign_digest=campaign.manifest_digest,
                condition_id=condition.condition_id,
                suite_digest=suite.content_digest,
                item_digest=item.item_digest,
                trial_index=trial_index,
                decision_sequence=(item_position * campaign_binding.trials_per_item + trial_index),
                created_at=created_at,
            )
            request = build_trial_request(
                run_id=run_id,
                campaign_digest=campaign.manifest_digest,
                condition_id=condition.condition_id,
                suite=suite,
                item=item,
                execution_digest=binding.research_execution_digest,
                allocation=allocation,
                adapter=adapter,
                context_limit=context_limit,
                configuration=configuration,
                profile=profile,
                created_at=created_at,
            )
            allocations.append(allocation)
            requests.append(request)

    request_digests = tuple(sorted(request.request_digest for request in requests))
    provisional = AtlasRunManifest.model_construct(
        run_manifest_id="pending",
        run_id=run_id,
        run_kind=AtlasRunKind.MODEL_EVALUATION,
        campaign_digest=campaign.manifest_digest,
        campaign_execution_binding_digest=binding.binding_digest,
        condition_id=condition.condition_id,
        suite_digest=suite.content_digest,
        research_execution_digest=binding.research_execution_digest,
        harness_profile_digest=binding.harness_profile_digest,
        evaluation_class=suite.evaluation_class,
        adaptive=False,
        allocation_policy_id="padawan.atlas.fixed_complete_suite",
        allocation_policy_version="1.0.0",
        stop_rule_id=None,
        request_template_digest=None,
        request_template_configuration=None,
        request_template_adapter=None,
        planned_request_count=planned_requests,
        max_retry_requests=configuration.max_retry_requests,
        predeclared_request_digests=request_digests,
        max_input_tokens=request_capacity * input_allocation,
        max_output_tokens=request_capacity * configuration.max_output_tokens_per_request,
        max_actions=request_capacity * configuration.action_budget_per_request,
        max_cost_usd=request_capacity * configuration.max_cost_usd_per_request,
        external_execution=condition.externally_gated,
        external_authorization_ref=binding.external_authorization_ref,
        manifest_digest=sha256_digest("pending"),
        created_at=created_at,
    )
    canonical_manifest_identity = provisional.model_dump(
        mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
    )
    manifest = AtlasRunManifest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "run_manifest_id": content_id("atlas-run", canonical_manifest_identity),
            "manifest_digest": sha256_digest(canonical_manifest_identity),
        }
    )
    return FixedAtlasRunPlan(
        manifest=manifest,
        allocations=tuple(allocations),
        requests=tuple(requests),
    )


def plan_adaptive_suite_run(
    *,
    campaign: AtlasCampaignManifest,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    binding: CampaignExecutionBinding,
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
    run_id: str,
    adapter: AdapterDescriptor,
    configuration: FixedRunConfiguration,
    stop_rule_id: str,
    created_at: datetime,
) -> AdaptiveAtlasRunPlan:
    """Freeze an adaptive policy, request template, stop rule, and maximum trial envelope."""

    campaign_binding = _validate_run_coordinates(
        campaign=campaign,
        condition=condition,
        suite=suite,
        binding=binding,
        execution=execution,
        profile=profile,
    )
    if not campaign_binding.adaptive:
        raise ValueError("adaptive run planning requires an adaptive campaign suite binding")
    if suite.evaluation_class != EvaluationClass.ADAPTIVE_SEARCH:
        raise ValueError("adaptive runs are restricted to adaptive-search suites")
    if adapter.kind != suite.adapter_kind:
        raise ValueError("Atlas adapter kind differs from the frozen suite")
    item_modalities = {modality for item in suite.items for modality in item.modalities}
    if not item_modalities.issubset(adapter.supported_modalities):
        raise ValueError("Atlas adapter does not support every frozen item modality")
    if campaign_binding.planned_item_count != len(suite.item_digests):
        raise ValueError("adaptive run planning requires every frozen candidate item")
    stop_rule = next(
        (rule for rule in campaign.stop_rules if rule.rule_id == stop_rule_id),
        None,
    )
    if stop_rule is None:
        raise ValueError("adaptive run cites an unknown predeclared stop rule")
    planned_requests = campaign_binding.planned_item_count * campaign_binding.trials_per_item
    if stop_rule.minimum_trials > planned_requests:
        raise ValueError("adaptive stop-rule minimum exceeds the frozen trial design")
    context_limit = profile.context.effective_input_limit_tokens
    if context_limit is None:
        raise ValueError("Atlas requests require an evidence-backed effective context limit")
    request_capacity = planned_requests + configuration.max_retry_requests
    if request_capacity > condition.max_requests:
        raise ValueError("adaptive run including retries exceeds the condition request ceiling")
    if request_capacity * context_limit > condition.max_input_tokens:
        raise ValueError("adaptive trial plan exceeds the condition input-token ceiling")
    if request_capacity * configuration.max_output_tokens_per_request > condition.max_output_tokens:
        raise ValueError("adaptive trial plan exceeds the condition output-token ceiling")
    if request_capacity * configuration.action_budget_per_request > condition.max_actions:
        raise ValueError("adaptive trial plan exceeds the condition action ceiling")
    if request_capacity * configuration.max_cost_usd_per_request > condition.max_cost_usd:
        raise ValueError("adaptive trial plan exceeds the condition cost ceiling")
    _validate_wire_configuration(
        condition=condition,
        profile=profile,
        configuration=configuration,
    )
    request_template_configuration = configuration.model_dump(mode="json")
    request_template_digest = sha256_digest(
        {"configuration": request_template_configuration, "adapter": adapter}
    )
    provisional = AtlasRunManifest.model_construct(
        run_manifest_id="pending",
        run_id=run_id,
        run_kind=AtlasRunKind.MODEL_EVALUATION,
        campaign_digest=campaign.manifest_digest,
        campaign_execution_binding_digest=binding.binding_digest,
        condition_id=condition.condition_id,
        suite_digest=suite.content_digest,
        research_execution_digest=binding.research_execution_digest,
        harness_profile_digest=binding.harness_profile_digest,
        evaluation_class=suite.evaluation_class,
        adaptive=True,
        allocation_policy_id="atlas.boundary.uncertainty_proximity",
        allocation_policy_version="1.0.0",
        stop_rule_id=stop_rule.rule_id,
        request_template_digest=request_template_digest,
        request_template_configuration=request_template_configuration,
        request_template_adapter=adapter,
        planned_request_count=planned_requests,
        max_retry_requests=configuration.max_retry_requests,
        predeclared_request_digests=(),
        max_input_tokens=request_capacity * context_limit,
        max_output_tokens=request_capacity * configuration.max_output_tokens_per_request,
        max_actions=request_capacity * configuration.action_budget_per_request,
        max_cost_usd=request_capacity * configuration.max_cost_usd_per_request,
        external_execution=condition.externally_gated,
        external_authorization_ref=binding.external_authorization_ref,
        manifest_digest=sha256_digest("pending"),
        created_at=created_at,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
    )
    manifest = AtlasRunManifest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "run_manifest_id": content_id("atlas-run", identity),
            "manifest_digest": sha256_digest(identity),
        }
    )
    return AdaptiveAtlasRunPlan(
        manifest=manifest,
        stop_rule=stop_rule,
        trials_per_item=campaign_binding.trials_per_item,
        boundary_probability=stop_rule.boundary_probability,
    )


def materialize_adaptive_trial(
    *,
    plan: AdaptiveAtlasRunPlan,
    campaign: AtlasCampaignManifest,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    profile: HarnessProfile,
    adapter: AdapterDescriptor,
    configuration: FixedRunConfiguration,
    candidates: tuple[BoundaryCandidate, ...],
    created_at: datetime,
) -> AdaptiveTrialMaterialization:
    """Select and freeze exactly one next adaptive request from disclosed prior outcomes."""

    manifest = plan.manifest
    if (
        manifest.campaign_digest != campaign.manifest_digest
        or manifest.condition_id != condition.condition_id
        or manifest.suite_digest != suite.content_digest
        or manifest.harness_profile_digest != sha256_digest(profile.model_dump(mode="json"))
    ):
        raise ValueError("adaptive materialization coordinates differ from the frozen run")
    campaign_rule = next(
        (rule for rule in campaign.stop_rules if rule.rule_id == manifest.stop_rule_id),
        None,
    )
    if campaign_rule is None or campaign_rule != plan.stop_rule:
        raise ValueError("adaptive materialization stop rule differs from the campaign")
    if manifest.request_template_digest != sha256_digest(
        {"configuration": configuration.model_dump(mode="json"), "adapter": adapter}
    ):
        raise ValueError("adaptive wire configuration differs from the frozen request template")
    _validate_wire_configuration(
        condition=condition,
        profile=profile,
        configuration=configuration,
    )
    if adapter.kind != suite.adapter_kind:
        raise ValueError("adaptive adapter kind differs from the frozen suite")
    candidate_digests = tuple(sorted(candidate.item_digest for candidate in candidates))
    if candidate_digests != tuple(sorted(suite.item_digests)):
        raise ValueError("adaptive candidates must exactly cover the frozen suite")
    if any(candidate.planned_trials != plan.trials_per_item for candidate in candidates):
        raise ValueError("adaptive candidate trial budgets differ from the campaign design")
    allocated = sum(candidate.allocated_trials for candidate in candidates)
    if allocated >= manifest.planned_request_count:
        raise ValueError("adaptive trial design is exhausted")
    successes = sum(candidate.successes for candidate in candidates)
    failures = sum(candidate.failures for candidate in candidates)
    infrastructure = sum(candidate.infrastructure_failures for candidate in candidates)
    contaminated = sum(candidate.contaminated_trials for candidate in candidates)
    other_missing = sum(
        candidate.missing_trials - candidate.infrastructure_failures - candidate.contaminated_trials
        for candidate in candidates
    )
    assessment = assess_stop_rule(
        plan.stop_rule,
        successes=successes,
        failures=failures,
        infrastructure_failures=infrastructure,
        contaminated_trials=contaminated,
        other_missing_trials=other_missing,
    )
    if assessment.decision != StopDecision.CONTINUE:
        raise ValueError(f"adaptive run is closed by stop rule: {assessment.decision.value}")
    allocation = allocate_near_boundary(
        campaign_digest=manifest.campaign_digest,
        condition_id=manifest.condition_id,
        suite_digest=manifest.suite_digest,
        suite_status=suite.status,
        evaluation_class=suite.evaluation_class,
        candidates=candidates,
        boundary_probability=plan.boundary_probability,
        created_at=created_at,
    )
    if allocation.decision_sequence != allocated:
        raise ValueError("adaptive allocation decision sequence is not globally contiguous")
    item = next(
        (value for value in suite.items if value.item_digest == allocation.item_digest),
        None,
    )
    if item is None:
        raise ValueError("adaptive allocation selected unavailable item content")
    context_limit = profile.context.effective_input_limit_tokens
    if context_limit is None:
        raise ValueError("adaptive Atlas requests require an evidence-backed context limit")
    request = build_trial_request(
        run_id=manifest.run_id,
        campaign_digest=manifest.campaign_digest,
        condition_id=manifest.condition_id,
        suite=suite,
        item=item,
        execution_digest=manifest.research_execution_digest,
        allocation=allocation,
        adapter=adapter,
        context_limit=context_limit,
        configuration=configuration,
        profile=profile,
        created_at=created_at,
    )
    return AdaptiveTrialMaterialization(
        stop_assessment=assessment,
        allocation=allocation,
        request=request,
    )


def generation_request_for(
    *,
    request: AtlasTrialRequest,
    item: AtlasItemManifest,
    configuration: FixedRunConfiguration,
    profile: HarnessProfile,
) -> GenerationRequest:
    """Translate frozen Atlas intent into the existing idempotent generation contract."""

    if request.item_id != item.item_id or request.item_digest != item.item_digest:
        raise ValueError("Atlas request and item identities differ")
    if request.prompt_digest != item.prompt_digest or item.prompt is None:
        raise ValueError("Atlas generation requires the frozen prompt content")
    if request.instructions_digest != configuration.instructions_digest:
        raise ValueError("Atlas request instructions differ from the wire configuration")
    if request.response_format_digest != configuration.response_format_digest:
        raise ValueError("Atlas request response format differs from the wire configuration")
    if request.tool_ids != configuration.tool_ids:
        raise ValueError("Atlas request tool identities differ from the wire configuration")
    if request.tool_manifest_digest != configuration.tool_manifest_digest:
        raise ValueError("Atlas request tool manifest differs from the wire configuration")
    expected_sampling = _sampling_manifest(configuration, trial_index=request.trial_index)
    if request.sampling != expected_sampling:
        raise ValueError("Atlas request sampling controls differ from the wire configuration")
    rendered_input = _rendered_input(item=item, profile=profile)
    if request.rendered_input_digest != sha256_digest(rendered_input):
        raise ValueError("Atlas request rendered input differs from the harness condition")
    generation = _generation_request(
        request_id=request.request_id,
        campaign_digest=request.campaign_digest,
        condition_id=request.condition_id,
        suite_digest=request.suite_digest,
        item_digest=request.item_digest,
        execution_digest=request.research_execution_digest,
        rendered_input=rendered_input,
        trial_index=request.trial_index,
        configuration=configuration,
    )
    if request.wire_request_digest != sha256_digest(generation.model_dump(mode="json")):
        raise ValueError("Atlas request differs from the complete wire request")
    return generation


def build_trial_result(
    *,
    request: AtlasTrialRequest,
    generation: GenerationResult,
    execution: ResearchExecutionManifest,
    generation_request: GenerationRequest,
    response_artifact: ArtifactRef,
    external_call_artifact: ArtifactRef,
    evaluation: AdapterEvaluation,
    contamination_checks: dict[str, bool],
    completed_at: datetime,
    wall_time_ms: float | None = None,
    cost_usd: float | None = None,
    retry_count: int = 0,
    confidence: float | None = None,
    grader_artifacts: tuple[ArtifactRef, ...] = (),
    tool_calls: tuple[dict[str, Any], ...] = (),
) -> AtlasTrialResult:
    """Bind one captured generation and authoritative adapter outcome into Atlas evidence."""

    if generation.request_id != request.request_id:
        raise ValueError("generation result belongs to another Atlas request")
    execution_digest = sha256_digest(execution.model_dump(mode="json"))
    if execution_digest != request.research_execution_digest:
        raise ValueError("generation execution differs from the frozen Atlas request")
    if sha256_digest(generation_request.model_dump(mode="json")) != request.wire_request_digest:
        raise ValueError("captured generation request differs from frozen wire intent")
    if generation.model_id != execution.student_model.model_id:
        raise ValueError("generation model differs from the research execution")
    if generation.protocol != execution.student_model.protocol:
        raise ValueError("generation protocol differs from the research execution")
    expected_provider = _expected_generation_provider(execution)
    if generation.provider != expected_provider:
        raise ValueError("generation provider differs from the research execution")
    if response_artifact.digest != sha256_digest(generation.raw_response):
        raise ValueError("response artifact does not retain the captured raw response")
    if external_call_artifact.media_type != "application/vnd.padawan.generation-result+json":
        raise ValueError("external-call artifact is not a captured generation envelope")
    if not external_call_artifact.restricted or not external_call_artifact.raw_data:
        raise ValueError("external-call generation envelope must retain restricted raw evidence")
    if external_call_artifact.digest != sha256_digest(_serialize_result(generation)):
        raise ValueError("external-call artifact digest differs from captured generation envelope")
    if evaluation.adapter_id != request.adapter_id:
        raise ValueError("adapter evaluation belongs to another Atlas adapter")
    if evaluation.adapter_version != request.adapter_version:
        raise ValueError("adapter evaluation version differs from the frozen request")
    if retry_count < 0:
        raise ValueError("Atlas retry count cannot be negative")
    if not contamination_checks:
        raise ValueError("Atlas results require explicit contamination checks")

    failed_contamination = not all(contamination_checks.values())
    failure_origin: FailureOrigin | None
    if failed_contamination:
        status = TrialStatus.CONTAMINATED
        score = None
        success = None
        failure_origin = FailureOrigin.CONTAMINATION
        primary_authority = None
        failure_codes = tuple(
            sorted(
                {
                    *evaluation.failure_codes,
                    "contamination_check_failed",
                }
            )
        )
    else:
        status, failure_origin = _trial_disposition(evaluation)
        score = evaluation.score
        success = evaluation.success
        primary_authority = evaluation.primary_authority
        failure_codes = evaluation.failure_codes

    evidence = tuple(
        OutcomeEvidence(
            evidence_id=item.result.result_id,
            authority=item.authority,
            verifier_id=item.result.verifier_id,
            verifier_version=item.result.verifier_version,
            disposition=item.result.disposition.value,
            score=evaluation.score,
            success=evaluation.success,
            evaluated_output_digest=(
                sha256_digest(generation.output_text)
                if item.result.disposition
                in {VerifierDisposition.VERIFIED, VerifierDisposition.REJECTED}
                else None
            ),
            deterministic=item.result.deterministic,
            evidence_digest=sha256_digest(item.result.model_dump(mode="json")),
        )
        for item in evaluation.evidence
    )
    tokens = _token_accounting(generation.usage)
    provisional = AtlasTrialResult.model_construct(
        result_id="pending",
        request_id=request.request_id,
        request_digest=request.request_digest,
        research_execution_digest=request.research_execution_digest,
        generation_provider=generation.provider,
        generation_model_id=generation.model_id,
        generation_protocol=generation.protocol,
        raw_request_digest=sha256_digest(generation.raw_request),
        raw_response_digest=sha256_digest(generation.raw_response),
        capabilities_digest=sha256_digest(generation.capabilities.model_dump(mode="json")),
        external_call_artifact=external_call_artifact,
        status=status,
        score=score,
        success=success,
        confidence=confidence,
        abstained=status == TrialStatus.ABSTAINED,
        failure_origin=failure_origin,
        failure_codes=failure_codes,
        verifier_evidence=evidence,
        primary_authority=primary_authority,
        response_digest=response_artifact.digest,
        response_artifact=response_artifact,
        grader_artifacts=grader_artifacts,
        tokens=tokens,
        latency_ms=generation.latency_ms,
        wall_time_ms=wall_time_ms,
        cost_usd=cost_usd,
        tool_calls=tool_calls,
        retry_count=retry_count,
        contamination_checks=dict(sorted(contamination_checks.items())),
        result_digest=sha256_digest("pending"),
        completed_at=completed_at,
    )
    identity = provisional.model_dump(
        mode="json", exclude={"result_id", "result_digest", "completed_at"}
    )
    return AtlasTrialResult.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "result_id": content_id("atlas-result", identity),
            "result_digest": sha256_digest(identity),
        }
    )


def _validate_run_coordinates(
    *,
    campaign: AtlasCampaignManifest,
    condition: CampaignCondition,
    suite: AtlasSuiteManifest,
    binding: CampaignExecutionBinding,
    execution: ResearchExecutionManifest,
    profile: HarnessProfile,
) -> CampaignSuiteBinding:
    if suite.status not in {SuiteStatus.READY, SuiteStatus.SEALED}:
        raise ValueError("Atlas runs require a ready or sealed suite")
    registered_condition = next(
        (item for item in campaign.conditions if item.condition_id == condition.condition_id),
        None,
    )
    if registered_condition != condition:
        raise ValueError("Atlas run condition is absent or differs from the campaign")
    campaign_binding = next(
        (item for item in campaign.suite_bindings if item.suite_digest == suite.content_digest),
        None,
    )
    if campaign_binding is None or condition.condition_id not in campaign_binding.condition_ids:
        raise ValueError("Atlas suite/condition is absent from the campaign")
    execution_digest = sha256_digest(execution.model_dump(mode="json"))
    profile_digest = sha256_digest(profile.model_dump(mode="json"))
    expected = (
        campaign.manifest_digest,
        condition.condition_id,
        suite.content_digest,
        execution_digest,
        profile_digest,
        condition.factor_levels,
    )
    observed = (
        binding.campaign_digest,
        binding.condition_id,
        binding.suite_digest,
        binding.research_execution_digest,
        binding.harness_profile_digest,
        binding.factor_levels,
    )
    if observed != expected:
        raise ValueError("Atlas execution binding differs from the requested run coordinates")
    if execution.harness_profile_digest != profile_digest:
        raise ValueError("Atlas execution and harness-profile digests differ")
    if condition.externally_gated and binding.external_authorization_ref is None:
        raise ValueError("externally gated Atlas run lacks authorization evidence")
    if execution.task.task_manifest_digest not in suite.task_manifest_digests:
        raise ValueError("Atlas run task is outside the frozen suite")
    if execution.task.corpus_digest not in suite.corpus_digests:
        raise ValueError("Atlas run corpus is outside the frozen suite")
    if suite.environment_fingerprints and (
        execution.environment_fingerprint not in suite.environment_fingerprints
    ):
        raise ValueError("Atlas run environment is outside the frozen suite")
    return campaign_binding


def _expected_generation_provider(execution: ResearchExecutionManifest) -> str:
    provider = execution.student_model.runtime_parameters.get("provider")
    if provider is not None:
        return provider
    if execution.student_model.serving_artifact.component_id == "inkling-small-ampere":
        return "inkling"
    component = execution.student_model.serving_artifact.component_id
    if component.endswith(".responses_service") or component.endswith(".managed_service"):
        return component.split(".", maxsplit=1)[0]
    raise ValueError("research execution does not bind a generation provider identity")


def _fixed_allocation(
    *,
    campaign_digest: str,
    condition_id: str,
    suite_digest: str,
    item_digest: str,
    trial_index: int,
    decision_sequence: int,
    created_at: datetime,
) -> TrialAllocation:
    identity = {
        "campaign_digest": campaign_digest,
        "condition_id": condition_id,
        "suite_digest": suite_digest,
        "item_digest": item_digest,
        "trial_index": trial_index,
        "decision_sequence": decision_sequence,
        "allocation_policy_id": "padawan.atlas.fixed_complete_suite",
        "allocation_policy_version": "1.0.0",
    }
    decision_digest = sha256_digest(identity)
    return TrialAllocation(
        allocation_id=content_id("atlas-allocation", identity),
        campaign_digest=campaign_digest,
        condition_id=condition_id,
        suite_digest=suite_digest,
        item_digest=item_digest,
        trial_index=trial_index,
        decision_sequence=decision_sequence,
        selection_probability=1.0,
        allocation_policy_id="padawan.atlas.fixed_complete_suite",
        allocation_policy_version="1.0.0",
        decision_evidence_digest=decision_digest,
        created_at=created_at,
    )


def build_trial_request(
    *,
    run_id: str,
    campaign_digest: str,
    condition_id: str,
    suite: AtlasSuiteManifest,
    item: AtlasItemManifest,
    execution_digest: str,
    allocation: TrialAllocation,
    adapter: AdapterDescriptor,
    context_limit: int,
    configuration: FixedRunConfiguration,
    profile: HarnessProfile,
    created_at: datetime,
) -> AtlasTrialRequest:
    request_seed = {
        "run_id": run_id,
        "campaign_digest": campaign_digest,
        "condition_id": condition_id,
        "suite_digest": suite.content_digest,
        "item_digest": item.item_digest,
        "allocation_id": allocation.allocation_id,
        "trial_index": allocation.trial_index,
        "attempt_index": 0,
    }
    request_id = content_id("atlas-request", request_seed)
    sampling = _sampling_manifest(configuration, trial_index=allocation.trial_index)
    rendered_input = _rendered_input(item=item, profile=profile)
    generation = _generation_request(
        request_id=request_id,
        campaign_digest=campaign_digest,
        condition_id=condition_id,
        suite_digest=suite.content_digest,
        item_digest=item.item_digest,
        execution_digest=execution_digest,
        rendered_input=rendered_input,
        trial_index=allocation.trial_index,
        configuration=configuration,
    )
    provisional = AtlasTrialRequest.model_construct(
        request_id=request_id,
        run_id=run_id,
        campaign_digest=campaign_digest,
        research_execution_digest=execution_digest,
        condition_id=condition_id,
        suite_digest=suite.content_digest,
        item_id=item.item_id,
        item_digest=item.item_digest,
        allocation_id=allocation.allocation_id,
        trial_index=allocation.trial_index,
        attempt_index=0,
        parent_request_id=None,
        prompt_digest=item.prompt_digest,
        rendered_input_digest=sha256_digest(rendered_input),
        instructions_digest=configuration.instructions_digest,
        response_format_digest=configuration.response_format_digest,
        wire_request_digest=sha256_digest(generation.model_dump(mode="json")),
        adapter_id=adapter.adapter_id,
        adapter_version=adapter.version,
        adapter_descriptor_digest=adapter.implementation_digest,
        effort=configuration.scientific_effort,
        effort_mapping_evidence_digest=configuration.effort_mapping_evidence_digest,
        edge_preflight_evidence_digest=configuration.edge_preflight_evidence_digest,
        tool_preflight_evidence_digest=configuration.tool_preflight_evidence_digest,
        context_limit_tokens=context_limit,
        max_output_tokens=configuration.max_output_tokens_per_request,
        action_budget=configuration.action_budget_per_request,
        tool_ids=configuration.tool_ids,
        tool_manifest_digest=configuration.tool_manifest_digest,
        sampling=sampling,
        request_digest=sha256_digest("pending"),
        created_at=created_at,
    )
    identity = provisional.model_dump(mode="json", exclude={"request_digest", "created_at"})
    return AtlasTrialRequest.model_validate(
        {
            **provisional.model_dump(mode="python"),
            "request_digest": sha256_digest(identity),
        }
    )


def _sampling_manifest(configuration: FixedRunConfiguration, *, trial_index: int) -> dict[str, str]:
    values = {
        "edge_preflight_evidence_digest": configuration.edge_preflight_evidence_digest,
        "effort_mapping_evidence_digest": configuration.effort_mapping_evidence_digest
        or "not-required",
        "reasoning_effort_wire_value": configuration.reasoning_effort_wire_value or "unspecified",
        "seed": str(configuration.base_seed + trial_index),
        "temperature": (
            "unspecified" if configuration.temperature is None else str(configuration.temperature)
        ),
        "top_p": "unspecified" if configuration.top_p is None else str(configuration.top_p),
        "tool_preflight_evidence_digest": configuration.tool_preflight_evidence_digest
        or "not-required",
    }
    return dict(sorted(values.items()))


def _generation_request(
    *,
    request_id: str,
    campaign_digest: str,
    condition_id: str,
    suite_digest: str,
    item_digest: str,
    execution_digest: str,
    rendered_input: str | list[dict[str, Any]],
    trial_index: int,
    configuration: FixedRunConfiguration,
) -> GenerationRequest:
    from padawan.atlas.coding_tool_contracts import (
        COMPILER_TOOL_V2,
        SUBMIT_TOOL,
        V2_PER_TURN_TOKENS,
    )

    output_allowance = configuration.max_output_tokens_per_request
    if configuration.tools == (COMPILER_TOOL_V2, SUBMIT_TOOL):
        # The native allocation holds the whole episode. Its first prepared
        # model effect spends only the versioned per-turn allowance.
        output_allowance = min(output_allowance, V2_PER_TURN_TOKENS)
    return GenerationRequest(
        request_id=request_id,
        instructions=configuration.instructions,
        input=rendered_input,
        sampling=SamplingConfiguration(
            temperature=configuration.temperature,
            top_p=configuration.top_p,
            max_output_tokens=output_allowance,
            seed=configuration.base_seed + trial_index,
            reasoning_effort=configuration.reasoning_effort_wire_value,
        ),
        schema_name=configuration.schema_name,
        json_schema=configuration.json_schema,
        metadata={
            "atlas_campaign_digest": campaign_digest,
            "atlas_condition_id": condition_id,
            "atlas_item_digest": item_digest,
            "atlas_suite_digest": suite_digest,
            "research_execution_digest": execution_digest,
        },
        tools=configuration.tools,
        tool_choice=configuration.tool_choice,
        previous_response_id=None,
        store=False,
    )


def _rendered_input(
    *, item: AtlasItemManifest, profile: HarnessProfile
) -> str | list[dict[str, Any]]:
    if item.prompt is None:
        raise ValueError("Atlas model execution requires materialized prompt content")
    if profile.continuation.continuation_mode == "none":
        return item.prompt
    if tuple(tool.component_id for tool in profile.tools) == ("tool.compile_and_run",):
        # A compiler trajectory begins with this exact independent statement. Later
        # public/tool turns are reconstructed by its separate finite admission boundary.
        return item.prompt
    parameters = item.verifier_payload.get("parameters")
    scenario = parameters.get("scenario") if isinstance(parameters, dict) else None
    if not isinstance(scenario, dict):
        raise ValueError("explicit-history Atlas conditions require a frozen scenario history")
    prior = scenario.get("prior_conversation")
    current = scenario.get("current_user_message")
    if not isinstance(prior, list) or not isinstance(current, dict):
        raise ValueError("explicit-history scenario is missing its frozen messages")
    current_content = current.get("content")
    if current_content != item.prompt:
        raise ValueError("explicit-history current message differs from the frozen prompt")
    retain_assistant = profile.continuation.reasoning_retention_enabled
    history: list[dict[str, Any]] = []
    for message in prior:
        if not isinstance(message, dict):
            raise ValueError("explicit-history scenario contains an invalid message")
        role = message.get("role")
        content = message.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
            raise ValueError("explicit-history scenario contains an invalid role or content")
        if role == "assistant" and not retain_assistant:
            continue
        history.append({"role": role, "content": content})
    if profile.context.compaction_enabled:
        temporal_scenario = TemporalScenarioManifest.model_validate(scenario)
        compacted = {
            "contract": "padawan.atlas.explicit_history_compaction.v1",
            "source_digest": sha256_digest(history),
            "summary_kind": "authoritative_temporal_frame",
            "summary": render_temporal_frame(temporal_scenario.frame),
        }
        compacted_content = canonical_json_bytes(compacted).decode("utf-8")
        source_content = canonical_json_bytes(history).decode("utf-8")
        if len(compacted_content) >= len(source_content):
            raise ValueError(
                "declared compaction does not reduce this frozen history; condition is blocked"
            )
        history = [
            {
                "role": "developer",
                "content": "Deterministic compacted public history: " + compacted_content,
            }
        ]
    history.append({"role": "user", "content": item.prompt})
    return history


def _validate_wire_configuration(
    *,
    condition: CampaignCondition,
    profile: HarnessProfile,
    configuration: FixedRunConfiguration,
) -> None:
    sampling_level = condition.factor_levels.get("sampling", "deterministic")
    if sampling_level == "deterministic" and configuration.temperature not in {None, 0.0}:
        raise ValueError("deterministic sampling condition cannot use a nonzero temperature")
    if sampling_level == "stochastic" and configuration.temperature in {None, 0.0}:
        raise ValueError("stochastic sampling condition requires a nonzero temperature")

    effort_level = condition.factor_levels.get("effort")
    expected_effort = (
        {"e0": 0.0, "e50": 0.5, "e99": 0.99, "max": 0.99}.get(effort_level)
        if effort_level is not None
        else None
    )
    if expected_effort is not None and configuration.scientific_effort != expected_effort:
        raise ValueError("wire configuration misstates the scientific effort factor")
    if effort_level == "e0":
        if configuration.reasoning_effort_wire_value != "none":
            raise ValueError("effort e0 requires the validated wire value 'none'")
    elif expected_effort is not None:
        if configuration.reasoning_effort_wire_value in {None, "none", "mapping-required"}:
            raise ValueError("nonzero effort requires a registered endpoint wire mapping")
        if configuration.effort_mapping_evidence_digest is None:
            raise ValueError("nonzero effort requires immutable mapping evidence")

    profile_tool_ids = tuple(
        sorted(
            tool.component_id
            for tool in profile.tools
            if "no_tool" not in tool.component_id.casefold().replace("-", "_")
            and tool.component_id.casefold() not in {"none", "tool.none"}
        )
    )
    if configuration.tool_ids != profile_tool_ids:
        raise ValueError("wire tool identities differ from the materialized harness")
    if bool(configuration.tools) != (
        condition.factor_levels.get("tool_access") not in {None, "disabled"}
    ):
        raise ValueError("wire tool configuration disagrees with the tool-access factor")

    if "budget" in condition.factor_levels:
        expected_output = {
            "constrained": 4_096,
            "standard": 16_384,
            "extended": 32_768,
        }[condition.factor_levels["budget"]]
        if configuration.max_output_tokens_per_request != expected_output:
            raise ValueError("wire output budget differs from the campaign budget factor")
        if profile.budgets.actions.value != configuration.action_budget_per_request:
            raise ValueError("wire action budget differs from the materialized harness")
        if profile.budgets.cost.value != configuration.max_cost_usd_per_request:
            raise ValueError("wire cost budget differs from the materialized harness")


def _trial_disposition(
    evaluation: AdapterEvaluation,
) -> tuple[TrialStatus, FailureOrigin | None]:
    if evaluation.disposition == VerifierDisposition.VERIFIED:
        return TrialStatus.VERIFIED_SUCCESS, None
    if evaluation.disposition == VerifierDisposition.REJECTED:
        malformed = any("malformed" in code or "parse" in code for code in evaluation.failure_codes)
        return (
            TrialStatus.MALFORMED if malformed else TrialStatus.VERIFIED_FAILURE,
            FailureOrigin.MODEL,
        )
    if evaluation.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE:
        return TrialStatus.VERIFIER_FAILURE, FailureOrigin.VERIFIER
    if evaluation.evidence:
        return TrialStatus.VERIFIER_FAILURE, FailureOrigin.VERIFIER
    return TrialStatus.UNSCORABLE, FailureOrigin.UNKNOWN


def _token_accounting(usage: dict[str, int]) -> TokenAccounting:
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    total_tokens = usage.get("total_tokens")
    complete = (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and input_tokens >= 0
        and isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
        and output_tokens >= 0
        and isinstance(total_tokens, int)
        and not isinstance(total_tokens, bool)
        and total_tokens >= 0
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
