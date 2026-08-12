from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.domains.contracts import HardGateResult
from padawan.models.contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    NonEmpty,
    ResearchRole,
    Sha256,
    StrictRecord,
)
from padawan.models.hashing import sha256_digest

Probability = Annotated[FiniteFloat, Field(gt=0.0, le=1.0)]
NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]


class IdentityEvidenceStatus(StrEnum):
    PINNED = "pinned"
    DECLARED = "declared"
    UNKNOWN = "unknown"


class VersionedComponentIdentity(StrictRecord):
    """Content-bound component identity without embedding the component itself."""

    component_id: NonEmpty
    version: NonEmpty
    digest: Sha256
    evidence_status: IdentityEvidenceStatus
    evidence: NonEmpty


class ContinuationPolicy(StrictRecord):
    """Cross-request state semantics, separate from observational trace capture."""

    continuation_mode: NonEmpty
    response_storage_enabled: bool
    previous_response_id_enabled: bool
    reasoning_retention_enabled: bool
    reasoning_retention_mode: NonEmpty
    private_reasoning_capture_enabled: bool
    private_reasoning_used_as_context: bool

    @model_validator(mode="after")
    def semantics_are_explicit(self) -> ContinuationPolicy:
        if self.continuation_mode == "none" and (
            self.previous_response_id_enabled or self.reasoning_retention_enabled
        ):
            raise ValueError("continuation mode none cannot enable continuation or retention")
        if self.previous_response_id_enabled and not self.response_storage_enabled:
            raise ValueError("previous_response_id requires response storage")
        if not self.reasoning_retention_enabled and self.reasoning_retention_mode != "none":
            raise ValueError("disabled reasoning retention must use mode none")
        if self.reasoning_retention_enabled and self.reasoning_retention_mode == "none":
            raise ValueError("enabled reasoning retention requires an explicit mode")
        if self.private_reasoning_used_as_context and not (
            self.private_reasoning_capture_enabled and self.reasoning_retention_enabled
        ):
            raise ValueError(
                "private reasoning can be reused only when capture and retention are enabled"
            )
        return self


class ContextPolicy(StrictRecord):
    policy_id: NonEmpty
    version: NonEmpty
    configured_context_window_tokens: Annotated[int, Field(gt=0)] | None
    effective_input_limit_tokens: Annotated[int, Field(gt=0)] | None
    context_limit_evidence: NonEmpty
    token_counting_mode: NonEmpty
    history_selection: NonEmpty
    truncation_enabled: bool
    truncation_strategy: NonEmpty
    compaction_enabled: bool
    compaction_strategy: NonEmpty
    compactor: VersionedComponentIdentity | None = None

    @model_validator(mode="after")
    def context_operations_are_bound(self) -> ContextPolicy:
        if (
            self.configured_context_window_tokens is not None
            and self.effective_input_limit_tokens is not None
            and self.effective_input_limit_tokens > self.configured_context_window_tokens
        ):
            raise ValueError("effective input limit exceeds the configured context window")
        if self.truncation_enabled == (self.truncation_strategy == "none"):
            raise ValueError("truncation enablement and strategy disagree")
        if self.compaction_enabled:
            if self.compaction_strategy == "none" or self.compactor is None:
                raise ValueError("enabled compaction requires a strategy and compactor identity")
        elif self.compaction_strategy != "none" or self.compactor is not None:
            raise ValueError("disabled compaction cannot claim a strategy or compactor")
        return self


class BudgetDisposition(StrEnum):
    CAPPED = "capped"
    UNBOUNDED = "unbounded"
    NOT_APPLICABLE = "not_applicable"


class BudgetLimit(StrictRecord):
    disposition: BudgetDisposition
    scope: NonEmpty
    unit: NonEmpty
    value: NonNegativeFinite | None = None

    @model_validator(mode="after")
    def cap_has_exact_value(self) -> BudgetLimit:
        if (self.disposition == BudgetDisposition.CAPPED) != (self.value is not None):
            raise ValueError("only a capped budget has a numeric value")
        return self


class HarnessBudgets(StrictRecord):
    actions: BudgetLimit
    input_tokens: BudgetLimit
    output_tokens: BudgetLimit
    latency: BudgetLimit
    wall_time: BudgetLimit
    retries: BudgetLimit
    cost: BudgetLimit


class ResearchInstrumentationSeams(StrictRecord):
    """Typed extension points; absence means the later phase is not implemented."""

    capability_atlas_schema: VersionedComponentIdentity | None = None
    interactive_trajectory_schema: VersionedComponentIdentity | None = None
    mechanistic_telemetry_schema: VersionedComponentIdentity | None = None
    checkpoint_evaluation_schema: VersionedComponentIdentity | None = None


class HarnessProfile(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    profile_id: NonEmpty
    version: NonEmpty
    tier: NonEmpty
    purpose: NonEmpty
    continuation: ContinuationPolicy
    context: ContextPolicy
    prompt_templates: Annotated[tuple[VersionedComponentIdentity, ...], Field(min_length=1)]
    tools: Annotated[tuple[VersionedComponentIdentity, ...], Field(min_length=1)]
    budgets: HarnessBudgets
    instrumentation: ResearchInstrumentationSeams = Field(
        default_factory=ResearchInstrumentationSeams
    )
    created_at: datetime

    @model_validator(mode="after")
    def components_are_canonical(self) -> HarnessProfile:
        for label, components in (
            ("prompt templates", self.prompt_templates),
            ("tools", self.tools),
        ):
            identities = [(item.component_id, item.version) for item in components]
            if len(identities) != len(set(identities)):
                raise ValueError(f"harness {label} must be unique")
            if tuple(sorted(identities)) != tuple(identities):
                raise ValueError(f"harness {label} must use canonical lexical order")
        return self


class ModelServingIdentity(StrictRecord):
    purpose: NonEmpty
    research_role: ResearchRole
    model_id: NonEmpty
    checkpoint: VersionedComponentIdentity
    quantization: VersionedComponentIdentity
    runtime: VersionedComponentIdentity
    serving_artifact: VersionedComponentIdentity
    protocol: NonEmpty
    runtime_parameters: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]
    runtime_parameters_digest: Sha256

    @model_validator(mode="after")
    def runtime_configuration_is_bound(self) -> ModelServingIdentity:
        if list(self.runtime_parameters) != sorted(self.runtime_parameters):
            raise ValueError("runtime parameters must use canonical lexical order")
        if sha256_digest(self.runtime_parameters) != self.runtime_parameters_digest:
            raise ValueError("runtime parameter digest disagrees with recorded parameters")
        return self


class TaskCorpusIdentity(StrictRecord):
    task_id: NonEmpty
    task_version: NonEmpty
    task_manifest_digest: Sha256
    corpus_id: NonEmpty
    corpus_version: NonEmpty
    corpus_digest: Sha256
    split: NonEmpty
    evidence_status: IdentityEvidenceStatus


class ResearchExecutionManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    execution_id: NonEmpty
    harness_profile_id: NonEmpty
    harness_profile_version: NonEmpty
    harness_profile_digest: Sha256
    student_model: ModelServingIdentity
    auxiliary_models: tuple[ModelServingIdentity, ...] = ()
    task: TaskCorpusIdentity
    harness_parameters: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]
    environment: VersionedComponentIdentity
    environment_parameters: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]
    environment_fingerprint: Sha256
    seed: int
    created_at: datetime

    @model_validator(mode="after")
    def execution_identity_is_canonical(self) -> ResearchExecutionManifest:
        if self.environment.digest != self.environment_fingerprint:
            raise ValueError("environment identity must carry the effective fingerprint")
        if list(self.environment_parameters) != sorted(self.environment_parameters):
            raise ValueError("environment parameters must use canonical lexical order")
        if sha256_digest(self.environment_parameters) != self.environment_fingerprint:
            raise ValueError("environment fingerprint disagrees with recorded parameters")
        identities = [
            (item.purpose, item.research_role.value, item.model_id)
            for item in self.auxiliary_models
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("auxiliary model identities must be unique")
        if tuple(sorted(identities)) != tuple(identities):
            raise ValueError("auxiliary model identities must use canonical lexical order")
        if list(self.harness_parameters) != sorted(self.harness_parameters):
            raise ValueError("harness parameters must use canonical lexical order")
        return self


class ResearchAxis(StrEnum):
    CHECKPOINT = "checkpoint"
    QUANTIZATION = "quantization"
    TASK = "task"
    HARNESS = "harness"
    CONTINUATION = "continuation"
    CONTEXT_POLICY = "context_policy"
    PROMPTS = "prompts"
    TOOLS = "tools"
    BUDGET = "budget"
    SERVING = "serving"
    AUXILIARY_MODELS = "auxiliary_models"
    ENVIRONMENT = "environment"
    INSTRUMENTATION = "instrumentation"
    SEED = "seed"


class ComparabilityDisposition(StrEnum):
    IDENTICAL_CONTROLS = "identical_controls"
    CONTROLLED_DIFFERENCE = "controlled_difference"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_PROVENANCE = "insufficient_provenance"


class ResearchComparabilityAssessment(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    left_execution_digest: Sha256
    right_execution_digest: Sha256
    disposition: ComparabilityDisposition
    comparable: bool
    provenance_complete: bool
    differing_axes: tuple[ResearchAxis, ...]
    allowed_differences: tuple[ResearchAxis, ...]
    blocking_differences: tuple[ResearchAxis, ...]
    provenance_gaps: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def disposition_matches_evidence(self) -> ResearchComparabilityAssessment:
        if self.comparable != (self.provenance_complete and not self.blocking_differences):
            raise ValueError("comparability boolean disagrees with provenance or differences")
        expected = (
            ComparabilityDisposition.INSUFFICIENT_PROVENANCE
            if not self.provenance_complete
            else ComparabilityDisposition.NOT_COMPARABLE
            if self.blocking_differences
            else ComparabilityDisposition.CONTROLLED_DIFFERENCE
            if self.differing_axes
            else ComparabilityDisposition.IDENTICAL_CONTROLS
        )
        if self.disposition != expected:
            raise ValueError("comparability disposition disagrees with its evidence")
        return self


class StudyStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    INVALID = "invalid"


class StudyExperimentBinding(StrictRecord):
    experiment_id: NonEmpty
    condition_id: NonEmpty
    checkpoint_id: NonEmpty
    research_role: ResearchRole
    suite_manifest_digest: Sha256
    environment_fingerprint: Sha256
    research_execution_digest: Sha256 | None = None
    factor_values: dict[NonEmpty, NonEmpty] = Field(default_factory=dict)
    assignment_propensity: Probability | None = None


class StudyManifest(StrictRecord):
    study_id: NonEmpty
    version: Annotated[int, Field(ge=1)]
    title: NonEmpty
    description: NonEmpty
    seed: int
    suite_manifest_digest: Sha256
    aggregation_policy_id: NonEmpty
    aggregation_policy_version: NonEmpty
    comparison_axes: tuple[ResearchAxis, ...] = ()
    experiments: Annotated[tuple[StudyExperimentBinding, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def bindings_match_manifest(self) -> StudyManifest:
        identifiers = [binding.experiment_id for binding in self.experiments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("study experiment IDs must be unique")
        if any(
            binding.suite_manifest_digest != self.suite_manifest_digest
            for binding in self.experiments
        ):
            raise ValueError("every study experiment must use the study suite manifest")
        if len(self.comparison_axes) != len(set(self.comparison_axes)):
            raise ValueError("study comparison axes must be unique")
        if tuple(sorted(self.comparison_axes, key=lambda axis: axis.value)) != self.comparison_axes:
            raise ValueError("study comparison axes must use canonical lexical order")
        for binding in self.experiments:
            if list(binding.factor_values) != sorted(binding.factor_values):
                raise ValueError("study factor values must use canonical lexical order")
        return self


class EvaluationTrialType(StrEnum):
    RETENTION = "retention"
    INTERFERENCE = "interference"


class EvaluationTrialStatus(StrEnum):
    SCHEDULED = "scheduled"
    LEASED = "leased"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    INVALID = "invalid"


class EvaluationSchedule(StrictRecord):
    trial_id: NonEmpty
    study_id: NonEmpty
    trial_type: EvaluationTrialType
    source_episode_id: NonEmpty
    interfering_episode_id: str | None = None
    student_id: NonEmpty
    checkpoint_id: NonEmpty
    state_snapshot_id: NonEmpty
    competency_id: NonEmpty
    item_id: NonEmpty
    environment_fingerprint: Sha256
    assignment_seed: int
    assignment_propensity: Probability | None = None
    due_at: datetime
    created_at: datetime

    @model_validator(mode="after")
    def valid_schedule(self) -> EvaluationSchedule:
        if self.due_at < self.created_at:
            raise ValueError("evaluation due time precedes creation")
        has_interfering_episode = self.interfering_episode_id is not None
        if has_interfering_episode != (self.trial_type == EvaluationTrialType.INTERFERENCE):
            raise ValueError("only interference trials require an interfering episode")
        return self


class EvaluationOutcome(StrictRecord):
    trial_id: NonEmpty
    exposure_id: str | None = None
    success: bool | None
    score: FiniteFloat | None
    missing_reasons: dict[NonEmpty, NonEmpty]
    verifier_result_ids: tuple[NonEmpty, ...]
    contamination_checks: dict[NonEmpty, bool]
    infrastructure_failures: tuple[NonEmpty, ...] = ()
    completed_at: datetime

    @model_validator(mode="after")
    def missingness_is_explicit(self) -> EvaluationOutcome:
        expected = {
            metric
            for metric, value in (("success", self.success), ("score", self.score))
            if value is None
        }
        if set(self.missing_reasons) != expected:
            raise ValueError("evaluation missing reasons must exactly match missing outcomes")
        if self.infrastructure_failures and (self.success is not None or self.score is not None):
            raise ValueError("infrastructure failures cannot receive student outcomes")
        if not self.infrastructure_failures and self.exposure_id is None:
            raise ValueError("completed student outcomes require a persisted prompt exposure")
        if not self.infrastructure_failures and not self.verifier_result_ids:
            raise ValueError("completed student outcomes require verifier evidence")
        if not self.infrastructure_failures and not self.contamination_checks:
            raise ValueError("completed student outcomes require contamination checks")
        if len(self.verifier_result_ids) != len(set(self.verifier_result_ids)):
            raise ValueError("evaluation verifier result IDs must be unique")
        return self


class CheckpointStatus(StrEnum):
    CANDIDATE = "candidate"
    EVALUATING = "evaluating"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    REVOKED = "revoked"


class CheckpointManifest(StrictRecord):
    checkpoint_id: NonEmpty
    model_id: NonEmpty
    tokenizer_id: NonEmpty
    model_digest: Sha256
    tokenizer_digest: Sha256
    parent_checkpoint_id: str | None = None
    architecture: dict[str, Any]
    runtime_compatibility: dict[str, Any]
    training_bundle_manifest_digest: Sha256 | None = None
    trainer_attestations: tuple[ArtifactRef, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def parent_is_distinct(self) -> CheckpointManifest:
        if self.parent_checkpoint_id == self.checkpoint_id:
            raise ValueError("checkpoint cannot be its own parent")
        return self


class EvaluationSuiteManifest(StrictRecord):
    suite_id: NonEmpty
    version: NonEmpty
    task_manifest_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    environment_fingerprints: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    sealed: bool
    created_at: datetime

    @model_validator(mode="after")
    def entries_are_unique(self) -> EvaluationSuiteManifest:
        if len(self.task_manifest_digests) != len(set(self.task_manifest_digests)):
            raise ValueError("evaluation suite task manifests must be unique")
        if len(self.environment_fingerprints) != len(set(self.environment_fingerprints)):
            raise ValueError("evaluation suite environments must be unique")
        return self


class MetricObservation(StrictRecord):
    metric_id: NonEmpty
    value: FiniteFloat | None
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_value_has_reason(self) -> MetricObservation:
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing checkpoint metrics require exactly one reason")
        return self


class CheckpointEvaluationRecord(StrictRecord):
    evaluation_id: NonEmpty
    checkpoint_id: NonEmpty
    study_id: NonEmpty
    suite_manifest_digest: Sha256
    metrics: Annotated[tuple[MetricObservation, ...], Field(min_length=1)]
    hard_gates: Annotated[tuple[HardGateResult, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> CheckpointEvaluationRecord:
        metric_ids = [metric.metric_id for metric in self.metrics]
        gate_ids = [gate.gate_id for gate in self.hard_gates]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("checkpoint metric IDs must be unique")
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("checkpoint hard-gate IDs must be unique")
        return self


class PromotionMetricRule(StrictRecord):
    metric_id: NonEmpty
    required: bool = True
    max_regression: NonNegativeFinite = 0.0
    minimum_delta: FiniteFloat | None = None


class CheckpointPromotionPolicy(StrictRecord):
    policy_id: NonEmpty
    version: NonEmpty
    description: NonEmpty
    metric_rules: Annotated[tuple[PromotionMetricRule, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def metric_ids_are_unique(self) -> CheckpointPromotionPolicy:
        identifiers = [rule.metric_id for rule in self.metric_rules]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("promotion policy metric IDs must be unique")
        return self


class CheckpointComparisonRecord(StrictRecord):
    comparison_id: NonEmpty
    baseline_evaluation_id: NonEmpty
    candidate_evaluation_id: NonEmpty
    suite_manifest_digest: Sha256
    policy_id: NonEmpty
    policy_version: NonEmpty
    policy_digest: Sha256
    metric_deltas: Annotated[tuple[MetricObservation, ...], Field(min_length=1)]
    regression_breaches: tuple[NonEmpty, ...]
    hard_gate_failures: tuple[NonEmpty, ...]
    missing_required_metrics: tuple[NonEmpty, ...]
    promotion_recommended: bool
    created_at: datetime

    @model_validator(mode="after")
    def recommendation_respects_failures(self) -> CheckpointComparisonRecord:
        if self.promotion_recommended and (
            self.regression_breaches or self.hard_gate_failures or self.missing_required_metrics
        ):
            raise ValueError("checkpoint promotion cannot be recommended with unresolved failures")
        return self


class CheckpointDecisionAction(StrEnum):
    START_EVALUATION = "start_evaluation"
    PROMOTE = "promote"
    REJECT = "reject"
    QUARANTINE = "quarantine"
    REVOKE = "revoke"


class CheckpointDecisionRecord(StrictRecord):
    decision_id: NonEmpty
    checkpoint_id: NonEmpty
    action: CheckpointDecisionAction
    from_status: CheckpointStatus
    to_status: CheckpointStatus
    actor: NonEmpty
    reason: NonEmpty
    comparison_id: str | None = None
    policy_id: str | None = None
    policy_version: str | None = None
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def action_matches_transition(self) -> CheckpointDecisionRecord:
        expected = {
            CheckpointDecisionAction.START_EVALUATION: (
                CheckpointStatus.CANDIDATE,
                CheckpointStatus.EVALUATING,
            ),
            CheckpointDecisionAction.PROMOTE: (
                CheckpointStatus.EVALUATING,
                CheckpointStatus.PROMOTED,
            ),
            CheckpointDecisionAction.REJECT: (
                CheckpointStatus.EVALUATING,
                CheckpointStatus.REJECTED,
            ),
            CheckpointDecisionAction.REVOKE: (
                CheckpointStatus.PROMOTED,
                CheckpointStatus.REVOKED,
            ),
        }
        if self.action in expected and expected[self.action] != (
            self.from_status,
            self.to_status,
        ):
            raise ValueError("checkpoint decision action does not match its status transition")
        if self.action == CheckpointDecisionAction.QUARANTINE and (
            self.to_status != CheckpointStatus.QUARANTINED
            or self.from_status
            not in {
                CheckpointStatus.CANDIDATE,
                CheckpointStatus.EVALUATING,
                CheckpointStatus.PROMOTED,
            }
        ):
            raise ValueError("checkpoint quarantine transition is invalid")
        if (
            self.action
            in {
                CheckpointDecisionAction.PROMOTE,
                CheckpointDecisionAction.REJECT,
            }
            and self.comparison_id is None
        ):
            raise ValueError("promotion and rejection decisions require a comparison")
        if (self.policy_id is None) != (self.policy_version is None):
            raise ValueError("checkpoint decision policy identity must be complete")
        if (
            self.action
            in {
                CheckpointDecisionAction.PROMOTE,
                CheckpointDecisionAction.REJECT,
            }
            and self.policy_id is None
        ):
            raise ValueError("promotion and rejection decisions require a policy identity")
        return self
