from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.models.contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    NonEmpty,
    RightsUse,
    Sha256,
    SourceRights,
    StrictRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import ResearchAxis

NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]
Probability = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
PositiveInt = Annotated[int, Field(gt=0)]
HttpsUrl = Annotated[str, Field(pattern=r"^https://[^\s]+$")]


def content_id(prefix: str, value: Any) -> str:
    """Create a stable identifier from canonical content."""

    return f"{prefix}-{sha256_digest(value)[7:31]}"


class ClaimSourceKind(StrEnum):
    VENDOR = "vendor"
    UPSTREAM = "upstream"
    COMMUNITY = "community"


class AccessClassification(StrEnum):
    LOCAL = "local"
    REGISTRATION_ONLY = "registration_only"
    REQUIRES_APPROVAL = "requires_approval"
    UNAVAILABLE = "unavailable"
    LEGALLY_UNCLEAR = "legally_unclear"


class RedistributionClassification(StrEnum):
    PERMITTED = "permitted"
    METADATA_ONLY = "metadata_only"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class ContaminationClassification(StrEnum):
    UNASSESSED = "unassessed"
    NO_KNOWN_EXPOSURE = "no_known_exposure"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"


class EvaluationClass(StrEnum):
    DEVELOPMENT = "development"
    ADAPTIVE_SEARCH = "adaptive_search"
    CHALLENGE = "challenge"
    SEALED_PROMOTION = "sealed_promotion"


class SuiteStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    SEALED = "sealed"
    BLOCKED = "blocked"
    RETIRED = "retired"


class AdapterKind(StrEnum):
    STATIC_QA = "static_qa"
    GENERATED_VERIFIER = "generated_verifier"
    CODING_AGENTIC = "coding_agentic"
    INTERACTIVE_ENVIRONMENT = "interactive_environment"
    CONTEXT_MEMORY = "context_memory"
    MULTIMODAL = "multimodal"


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class AuthorityKind(StrEnum):
    DETERMINISTIC = "deterministic"
    KERNEL = "kernel"
    ENVIRONMENT = "environment"
    HUMAN = "human"
    MODEL = "model"


class TrialStatus(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    PARTIAL = "partial"
    ABSTAINED = "abstained"
    MALFORMED = "malformed"
    UNSCORABLE = "unscorable"
    VERIFIER_FAILURE = "verifier_failure"
    PARSER_FAILURE = "parser_failure"
    TIMEOUT = "timeout"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    CONTAMINATED = "contaminated"
    NOT_RUN = "not_run"


class FailureOrigin(StrEnum):
    MODEL = "model"
    HARNESS = "harness"
    VERIFIER = "verifier"
    PARSER = "parser"
    INFRASTRUCTURE = "infrastructure"
    CONTAMINATION = "contamination"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    PROPOSED = "proposed"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CampaignStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    EXTERNALLY_GATED = "externally_gated"
    COMPLETE = "complete"
    INVALID = "invalid"


class AtlasRunKind(StrEnum):
    OFFLINE_VERIFICATION = "offline_verification"
    MODEL_EVALUATION = "model_evaluation"
    REPLAY_GRADING = "replay_grading"


class ModalityGateStatus(StrEnum):
    UNVALIDATED = "unvalidated"
    PASSED = "passed"
    FAILED = "failed"


class ExtractionProvenance(StrictRecord):
    method: NonEmpty
    extracted_by: NonEmpty
    extracted_at: datetime
    source_excerpt_digest: Sha256
    source_artifact: ArtifactRef | None = None
    notes: tuple[NonEmpty, ...] = ()


class BenchmarkClaim(StrictRecord):
    """A reported external claim. It is never a Padawan observation."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    claim_id: NonEmpty
    source_kind: ClaimSourceKind
    source_url: HttpsUrl
    publication_title: NonEmpty
    source_revision: NonEmpty
    source_published_at: datetime
    model_id: NonEmpty
    model_revision: NonEmpty
    benchmark_id: NonEmpty
    benchmark_version: NonEmpty
    split: NonEmpty
    metric_id: NonEmpty
    reported_value: FiniteFloat
    reported_unit: NonEmpty
    effort: NonNegativeFinite | None = None
    harness_assumptions: tuple[NonEmpty, ...]
    tool_assumptions: tuple[NonEmpty, ...]
    context_assumptions: tuple[NonEmpty, ...]
    budget_assumptions: tuple[NonEmpty, ...]
    contamination_caveats: tuple[NonEmpty, ...]
    extraction: ExtractionProvenance
    supersedes_claim_id: NonEmpty | None = None
    correction_reason: NonEmpty | None = None
    created_at: datetime

    @model_validator(mode="after")
    def correction_is_explicit(self) -> BenchmarkClaim:
        if (self.supersedes_claim_id is None) != (self.correction_reason is None):
            raise ValueError("claim supersession requires both predecessor and correction reason")
        if self.supersedes_claim_id == self.claim_id:
            raise ValueError("a source claim cannot supersede itself")
        return self


class DatasetGovernance(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    governance_id: NonEmpty
    benchmark_id: NonEmpty
    benchmark_version: NonEmpty
    dataset_revision: NonEmpty
    source_url: HttpsUrl
    rights: SourceRights
    access: AccessClassification
    redistribution: RedistributionClassification
    contamination: ContaminationClassification
    evaluation_class: EvaluationClass
    license_expression: NonEmpty | None = None
    access_requirements: tuple[NonEmpty, ...] = ()
    redistribution_notes: tuple[NonEmpty, ...] = ()
    contamination_evidence: tuple[NonEmpty, ...] = ()
    sealed_handling: tuple[NonEmpty, ...] = ()
    reviewed_at: datetime

    @property
    def executable(self) -> bool:
        return (
            self.access == AccessClassification.LOCAL
            and self.rights.permits(RightsUse.EVALUATION)
            and self.contamination != ContaminationClassification.CONFIRMED
        )


class ModalityValidationEvidence(StrictRecord):
    modality: Modality
    status: ModalityGateStatus
    gate_id: NonEmpty
    gate_revision: NonEmpty
    evidence_digest: Sha256 | None = None
    evidence_refs: tuple[NonEmpty, ...] = ()
    validated_at: datetime | None = None

    @model_validator(mode="after")
    def passed_gate_has_evidence(self) -> ModalityValidationEvidence:
        if self.status == ModalityGateStatus.PASSED:
            if self.evidence_digest is None or not self.evidence_refs or self.validated_at is None:
                raise ValueError("a passed modality gate requires immutable evidence and time")
        elif self.evidence_digest is not None or self.validated_at is not None:
            raise ValueError("unpassed modality gates cannot carry validating evidence")
        if self.modality == Modality.TEXT and self.status == ModalityGateStatus.UNVALIDATED:
            raise ValueError("text modality must have an explicit passed or failed gate")
        return self


class AtlasItemManifest(StrictRecord):
    item_id: NonEmpty
    item_digest: Sha256
    family_id: NonEmpty
    difficulty: FiniteFloat
    adapter_kind: AdapterKind
    modalities: Annotated[tuple[Modality, ...], Field(min_length=1)]
    prompt: NonEmpty | None
    prompt_digest: Sha256
    verifier_id: NonEmpty
    verifier_version: NonEmpty
    verifier_payload: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    pair_id: NonEmpty | None = None
    variant_id: NonEmpty | None = None

    @model_validator(mode="after")
    def content_is_bound(self) -> AtlasItemManifest:
        if len(self.modalities) != len(set(self.modalities)):
            raise ValueError("item modalities must be unique")
        if tuple(sorted(self.modalities, key=lambda value: value.value)) != self.modalities:
            raise ValueError("item modalities must use canonical lexical order")
        if (self.pair_id is None) != (self.variant_id is None):
            raise ValueError("paired items require both pair and variant IDs")
        prompt_digest = sha256_digest(self.prompt if self.prompt is not None else "withheld")
        if prompt_digest != self.prompt_digest:
            raise ValueError("item prompt digest disagrees with its content")
        identity = {
            "family_id": self.family_id,
            "difficulty": self.difficulty,
            "adapter_kind": self.adapter_kind,
            "modalities": self.modalities,
            "prompt_digest": self.prompt_digest,
            "verifier_id": self.verifier_id,
            "verifier_version": self.verifier_version,
            "verifier_payload": self.verifier_payload,
            "metadata": self.metadata,
            "pair_id": self.pair_id,
            "variant_id": self.variant_id,
        }
        expected_digest = sha256_digest(identity)
        if self.item_digest != expected_digest:
            raise ValueError("item digest disagrees with immutable content")
        if self.item_id != content_id("atlas-item", identity):
            raise ValueError("item ID disagrees with immutable content")
        return self


class AtlasSuiteManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    suite_id: NonEmpty
    version: NonEmpty
    title: NonEmpty
    benchmark_id: NonEmpty
    benchmark_version: NonEmpty
    split: NonEmpty
    governance_id: NonEmpty
    adapter_kind: AdapterKind
    status: SuiteStatus
    evaluation_class: EvaluationClass
    item_digests: tuple[Sha256, ...]
    items: tuple[AtlasItemManifest, ...] = ()
    modality_gates: Annotated[tuple[ModalityValidationEvidence, ...], Field(min_length=1)]
    task_manifest_digests: tuple[Sha256, ...] = ()
    corpus_digests: tuple[Sha256, ...] = ()
    environment_fingerprints: tuple[Sha256, ...]
    evaluation_suite_manifest_digest: Sha256 | None = None
    content_digest: Sha256
    blocking_reasons: tuple[NonEmpty, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def suite_is_frozen_and_honest(self) -> AtlasSuiteManifest:
        if tuple(sorted(self.item_digests)) != self.item_digests:
            raise ValueError("suite item digests must use canonical lexical order")
        if len(self.item_digests) != len(set(self.item_digests)):
            raise ValueError("suite item digests must be unique")
        for values, label in (
            (self.task_manifest_digests, "task-manifest digests"),
            (self.corpus_digests, "corpus digests"),
            (self.environment_fingerprints, "environment fingerprints"),
        ):
            if tuple(sorted(values)) != values or len(values) != len(set(values)):
                raise ValueError(f"suite {label} must be unique and canonical")
        embedded = tuple(sorted(item.item_digest for item in self.items))
        if embedded and embedded != self.item_digests:
            raise ValueError("embedded items disagree with the suite item digest manifest")
        gate_modalities = [gate.modality for gate in self.modality_gates]
        if len(gate_modalities) != len(set(gate_modalities)):
            raise ValueError("suite modality gates must be unique")
        if tuple(sorted(gate_modalities, key=lambda value: value.value)) != tuple(gate_modalities):
            raise ValueError("suite modality gates must use canonical lexical order")
        required_modalities = {modality for item in self.items for modality in item.modalities}
        gates = {gate.modality: gate for gate in self.modality_gates}
        if not required_modalities.issubset(gates):
            raise ValueError("every suite modality requires an explicit validation gate")
        unvalidated_media = [
            modality.value
            for modality in required_modalities - {Modality.TEXT}
            if gates[modality].status != ModalityGateStatus.PASSED
        ]
        if self.status in {SuiteStatus.READY, SuiteStatus.SEALED} and unvalidated_media:
            raise ValueError("ready suites cannot claim unvalidated media capability")
        if self.status in {SuiteStatus.READY, SuiteStatus.SEALED}:
            if not self.item_digests or not self.items:
                raise ValueError("executable suites require frozen item content")
            if not self.task_manifest_digests or not self.corpus_digests:
                raise ValueError(
                    "executable suites require explicit task-manifest and corpus identities"
                )
            if self.blocking_reasons:
                raise ValueError("executable suites cannot retain blocking reasons")
        elif self.status == SuiteStatus.BLOCKED and not self.blocking_reasons:
            raise ValueError("blocked suites require explicit blocking reasons")
        if self.status == SuiteStatus.SEALED and self.evaluation_suite_manifest_digest is None:
            raise ValueError("sealed Atlas suites must bind the checkpoint evaluation suite")
        identity = {
            "suite_id": self.suite_id,
            "version": self.version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "split": self.split,
            "governance_id": self.governance_id,
            "adapter_kind": self.adapter_kind,
            "evaluation_class": self.evaluation_class,
            "item_digests": self.item_digests,
            "modality_gates": self.modality_gates,
            "task_manifest_digests": self.task_manifest_digests,
            "corpus_digests": self.corpus_digests,
            "environment_fingerprints": self.environment_fingerprints,
            "evaluation_suite_manifest_digest": self.evaluation_suite_manifest_digest,
        }
        if self.content_digest != sha256_digest(identity):
            raise ValueError("suite content digest disagrees with its manifest")
        return self


class AdapterDescriptor(StrictRecord):
    adapter_id: NonEmpty
    version: NonEmpty
    kind: AdapterKind
    supported_modalities: Annotated[tuple[Modality, ...], Field(min_length=1)]
    authority_order: Annotated[tuple[AuthorityKind, ...], Field(min_length=1)]
    deterministic_authority_required: bool
    supports_tools: bool
    supports_interaction: bool
    supports_repeated_trials: bool
    implementation_digest: Sha256

    @model_validator(mode="after")
    def authority_is_safe(self) -> AdapterDescriptor:
        if len(self.authority_order) != len(set(self.authority_order)):
            raise ValueError("adapter authority order must be unique")
        if self.deterministic_authority_required:
            authoritative = {
                AuthorityKind.DETERMINISTIC,
                AuthorityKind.KERNEL,
                AuthorityKind.ENVIRONMENT,
            }
            first_authority = next(
                (value for value in self.authority_order if value in authoritative), None
            )
            if first_authority is None:
                raise ValueError("adapter requires a deterministic or environment authority")
            if AuthorityKind.MODEL in self.authority_order and self.authority_order.index(
                AuthorityKind.MODEL
            ) < self.authority_order.index(first_authority):
                raise ValueError("a model grader cannot outrank deterministic authority")
        return self


class FactorLevel(StrictRecord):
    level_id: NonEmpty
    parameters: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]

    @model_validator(mode="after")
    def parameters_are_canonical(self) -> FactorLevel:
        if list(self.parameters) != sorted(self.parameters):
            raise ValueError("factor parameters must use canonical lexical order")
        return self


class Factor(StrictRecord):
    factor_id: NonEmpty
    axis: ResearchAxis
    levels: Annotated[tuple[FactorLevel, ...], Field(min_length=2)]

    @model_validator(mode="after")
    def levels_are_unique(self) -> Factor:
        identifiers = [level.level_id for level in self.levels]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("factor level IDs must be unique")
        return self


class StopRule(StrictRecord):
    rule_id: NonEmpty
    minimum_trials: PositiveInt
    maximum_trials: PositiveInt
    target_interval_width: Annotated[FiniteFloat, Field(gt=0.0, le=1.0)]
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)] = 0.95
    boundary_probability: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)] = 0.5
    maximum_infrastructure_failure_rate: Probability = 0.05
    fixed_before_results: bool = True

    @model_validator(mode="after")
    def limits_are_ordered(self) -> StopRule:
        if self.minimum_trials > self.maximum_trials:
            raise ValueError("stop-rule minimum exceeds its maximum")
        if not self.fixed_before_results:
            raise ValueError("Atlas stop rules must be fixed before candidate results")
        return self


class CampaignCondition(StrictRecord):
    condition_id: NonEmpty
    title: NonEmpty
    harness_tier: NonEmpty
    required_execution_digest: Sha256 | None = None
    required_checkpoint_id: NonEmpty | None = None
    required_quantization_id: NonEmpty | None = None
    required_protocol: NonEmpty
    factor_levels: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]
    max_requests: PositiveInt
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_actions: PositiveInt
    max_cost_usd: NonNegativeFinite
    expected_runtime_minutes: PositiveInt
    externally_gated: bool
    blocking_reasons: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def condition_is_canonical(self) -> CampaignCondition:
        if list(self.factor_levels) != sorted(self.factor_levels):
            raise ValueError("condition factor levels must use canonical lexical order")
        if self.externally_gated != bool(self.blocking_reasons):
            raise ValueError("external gating and blocking reasons must agree")
        return self


class CampaignSuiteBinding(StrictRecord):
    suite_digest: Sha256
    evaluation_class: EvaluationClass
    planned_item_count: PositiveInt
    trials_per_item: PositiveInt
    condition_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    adaptive: bool = False


class AtlasCampaignManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    campaign_id: NonEmpty
    version: NonEmpty
    title: NonEmpty
    description: NonEmpty
    status: CampaignStatus
    ontology_digest: Sha256
    source_claim_ids: tuple[NonEmpty, ...]
    suite_bindings: Annotated[tuple[CampaignSuiteBinding, ...], Field(min_length=1)]
    conditions: Annotated[tuple[CampaignCondition, ...], Field(min_length=1)]
    factors: tuple[Factor, ...]
    stop_rules: Annotated[tuple[StopRule, ...], Field(min_length=1)]
    randomization_seed: int
    analysis_policy_id: NonEmpty
    analysis_policy_version: NonEmpty
    promotion_suite_digests: tuple[Sha256, ...]
    adaptive_suite_digests: tuple[Sha256, ...]
    training_candidate_suite_digests: tuple[Sha256, ...]
    manifest_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def campaign_is_predeclared_and_disjoint(self) -> AtlasCampaignManifest:
        condition_ids = [condition.condition_id for condition in self.conditions]
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("campaign condition IDs must be unique")
        factor_ids = [factor.factor_id for factor in self.factors]
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("campaign factor IDs must be unique")
        stop_ids = [rule.rule_id for rule in self.stop_rules]
        if len(stop_ids) != len(set(stop_ids)):
            raise ValueError("campaign stop-rule IDs must be unique")
        known = set(condition_ids)
        selected_levels = {
            factor.factor_id: {level.level_id for level in factor.levels} for factor in self.factors
        }
        for condition in self.conditions:
            if set(condition.factor_levels) != set(factor_ids):
                raise ValueError("every campaign condition must select every declared factor")
            if any(
                level_id not in selected_levels[factor_id]
                for factor_id, level_id in condition.factor_levels.items()
            ):
                raise ValueError("campaign condition selects an unknown factor level")
        for binding in self.suite_bindings:
            if not set(binding.condition_ids).issubset(known):
                raise ValueError("suite binding cites an unknown condition")
            if len(binding.condition_ids) != len(set(binding.condition_ids)):
                raise ValueError("suite binding condition IDs must be unique")
        promotion = set(self.promotion_suite_digests)
        adaptive = set(self.adaptive_suite_digests)
        training = set(self.training_candidate_suite_digests)
        if promotion & adaptive or promotion & training or adaptive & training:
            raise ValueError("promotion, adaptive-search, and training suites must be disjoint")
        suite_digests = [binding.suite_digest for binding in self.suite_bindings]
        if len(suite_digests) != len(set(suite_digests)):
            raise ValueError("campaign suite bindings must be unique")
        for condition in self.conditions:
            planned_requests = sum(
                binding.planned_item_count * binding.trials_per_item
                for binding in self.suite_bindings
                if condition.condition_id in binding.condition_ids
            )
            if planned_requests > condition.max_requests:
                raise ValueError("campaign condition request ceiling is below its trial design")
        bound = {binding.suite_digest for binding in self.suite_bindings}
        if not (promotion | adaptive | training).issubset(bound):
            raise ValueError("campaign partitions must reference bound suites")
        bindings = {binding.suite_digest: binding for binding in self.suite_bindings}
        if any(
            bindings[digest].evaluation_class != EvaluationClass.SEALED_PROMOTION
            or bindings[digest].adaptive
            for digest in promotion
        ):
            raise ValueError("promotion partitions require fixed sealed-promotion suites")
        if any(
            bindings[digest].evaluation_class != EvaluationClass.ADAPTIVE_SEARCH
            or not bindings[digest].adaptive
            for digest in adaptive
        ):
            raise ValueError("adaptive partitions require adaptive-search allocation")
        if any(
            bindings[digest].evaluation_class != EvaluationClass.DEVELOPMENT
            or bindings[digest].adaptive
            for digest in training
        ):
            raise ValueError("training-candidate partitions require fixed development suites")
        identity = self.model_dump(mode="json", exclude={"manifest_digest", "status", "created_at"})
        if self.manifest_digest != sha256_digest(identity):
            raise ValueError("campaign digest disagrees with its predeclared design")
        return self


class CampaignExecutionBinding(StrictRecord):
    """One non-circular activation of a campaign condition under exact research controls."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    binding_id: NonEmpty
    campaign_digest: Sha256
    condition_id: NonEmpty
    suite_digest: Sha256
    research_execution_digest: Sha256
    harness_profile_digest: Sha256
    factor_levels: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)]
    external_authorization_ref: NonEmpty | None = None
    bound_by: NonEmpty
    binding_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def binding_is_content_addressed(self) -> CampaignExecutionBinding:
        if list(self.factor_levels) != sorted(self.factor_levels):
            raise ValueError("execution-binding factors must use canonical lexical order")
        identity = self.model_dump(
            mode="json", exclude={"binding_id", "binding_digest", "created_at"}
        )
        if self.binding_digest != sha256_digest(identity):
            raise ValueError("campaign execution binding digest disagrees with its content")
        if self.binding_id != content_id("atlas-binding", identity):
            raise ValueError("campaign execution binding ID disagrees with its content")
        return self


class AtlasRunManifest(StrictRecord):
    """Immutable envelope for an Atlas run using Padawan's existing run state machine."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    run_manifest_id: NonEmpty
    run_id: NonEmpty
    run_kind: AtlasRunKind
    campaign_digest: Sha256
    campaign_execution_binding_digest: Sha256
    condition_id: NonEmpty
    suite_digest: Sha256
    research_execution_digest: Sha256
    harness_profile_digest: Sha256
    evaluation_class: EvaluationClass
    adaptive: bool
    allocation_policy_id: NonEmpty
    allocation_policy_version: NonEmpty
    stop_rule_id: NonEmpty | None = None
    request_template_digest: Sha256 | None = None
    request_template_configuration: dict[NonEmpty, Any] | None = None
    request_template_adapter: AdapterDescriptor | None = None
    planned_request_count: PositiveInt
    max_retry_requests: Annotated[int, Field(ge=0)] = 0
    predeclared_request_digests: tuple[Sha256, ...] = ()
    max_input_tokens: PositiveInt
    max_output_tokens: PositiveInt
    max_actions: PositiveInt
    max_cost_usd: NonNegativeFinite
    external_execution: bool
    external_authorization_ref: NonEmpty | None = None
    manifest_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def envelope_is_predeclared_and_content_addressed(self) -> AtlasRunManifest:
        if tuple(sorted(self.predeclared_request_digests)) != self.predeclared_request_digests:
            raise ValueError("predeclared request digests must be unique and canonical")
        if len(self.predeclared_request_digests) != len(set(self.predeclared_request_digests)):
            raise ValueError("predeclared request digests must be unique")
        if self.adaptive:
            if self.evaluation_class != EvaluationClass.ADAPTIVE_SEARCH:
                raise ValueError("adaptive runs are restricted to adaptive-search suites")
            if self.predeclared_request_digests:
                raise ValueError("adaptive runs bind allocation policy, not future request content")
            if self.stop_rule_id is None:
                raise ValueError("adaptive runs require a predeclared campaign stop rule")
            if self.request_template_digest is None:
                raise ValueError("adaptive runs require a frozen request template")
            if self.request_template_configuration is None or self.request_template_adapter is None:
                raise ValueError("adaptive runs require the complete frozen request template")
            expected_template_digest = sha256_digest(
                {
                    "configuration": self.request_template_configuration,
                    "adapter": self.request_template_adapter,
                }
            )
            if self.request_template_digest != expected_template_digest:
                raise ValueError("adaptive request-template digest disagrees with its content")
        elif len(self.predeclared_request_digests) != self.planned_request_count:
            raise ValueError("fixed runs must predeclare every request digest")
        elif self.stop_rule_id is not None:
            raise ValueError("fixed runs cannot stop from candidate outcomes")
        elif self.request_template_digest is not None:
            raise ValueError("fixed runs bind complete request digests, not a template")
        elif (
            self.request_template_configuration is not None
            or self.request_template_adapter is not None
        ):
            raise ValueError("fixed runs cannot retain an adaptive request template")
        if self.evaluation_class == EvaluationClass.SEALED_PROMOTION and self.adaptive:
            raise ValueError("sealed promotion runs cannot allocate adaptively")
        if self.external_execution != (self.external_authorization_ref is not None):
            raise ValueError("external Atlas execution requires an authorization reference")
        identity = self.model_dump(
            mode="json", exclude={"run_manifest_id", "manifest_digest", "created_at"}
        )
        if self.manifest_digest != sha256_digest(identity):
            raise ValueError("Atlas run manifest digest disagrees with its envelope")
        if self.run_manifest_id != content_id("atlas-run", identity):
            raise ValueError("Atlas run manifest ID disagrees with its envelope")
        return self


class TrialAllocation(StrictRecord):
    allocation_id: NonEmpty
    campaign_digest: Sha256
    condition_id: NonEmpty
    suite_digest: Sha256
    item_digest: Sha256
    trial_index: Annotated[int, Field(ge=0)]
    decision_sequence: Annotated[int, Field(ge=0)]
    selection_probability: Annotated[FiniteFloat, Field(gt=0.0, le=1.0)]
    allocation_policy_id: NonEmpty
    allocation_policy_version: NonEmpty
    decision_evidence_digest: Sha256
    prior_result_digests: tuple[Sha256, ...] = ()
    created_at: datetime


class AtlasTrialRequest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    request_id: NonEmpty
    run_id: NonEmpty
    campaign_digest: Sha256
    research_execution_digest: Sha256
    condition_id: NonEmpty
    suite_digest: Sha256
    item_id: NonEmpty
    item_digest: Sha256
    allocation_id: NonEmpty
    trial_index: Annotated[int, Field(ge=0)]
    attempt_index: Annotated[int, Field(ge=0)]
    parent_request_id: NonEmpty | None = None
    prompt_digest: Sha256
    rendered_input_digest: Sha256
    instructions_digest: Sha256
    response_format_digest: Sha256
    wire_request_digest: Sha256
    adapter_id: NonEmpty
    adapter_version: NonEmpty
    adapter_descriptor_digest: Sha256
    effort: NonNegativeFinite | None = None
    effort_mapping_evidence_digest: Sha256 | None = None
    edge_preflight_evidence_digest: Sha256
    tool_preflight_evidence_digest: Sha256 | None = None
    context_limit_tokens: PositiveInt
    max_output_tokens: PositiveInt
    action_budget: PositiveInt
    tool_ids: tuple[NonEmpty, ...] = ()
    tool_manifest_digest: Sha256
    sampling: dict[NonEmpty, NonEmpty]
    request_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def request_identity_is_bound(self) -> AtlasTrialRequest:
        if list(self.sampling) != sorted(self.sampling):
            raise ValueError("trial sampling parameters must use canonical lexical order")
        if tuple(sorted(self.tool_ids)) != self.tool_ids or len(self.tool_ids) != len(
            set(self.tool_ids)
        ):
            raise ValueError("trial tool IDs must be unique and canonical")
        identity = self.model_dump(mode="json", exclude={"request_digest", "created_at"})
        if self.request_digest != sha256_digest(identity):
            raise ValueError("trial request digest disagrees with its immutable content")
        return self


class OutcomeEvidence(StrictRecord):
    evidence_id: NonEmpty
    authority: AuthorityKind
    verifier_id: NonEmpty
    verifier_version: NonEmpty
    disposition: NonEmpty
    score: FiniteFloat | None = None
    success: bool | None = None
    evaluated_output_digest: Sha256 | None = None
    deterministic: bool
    evidence_digest: Sha256
    artifact_refs: tuple[ArtifactRef, ...] = ()

    @model_validator(mode="after")
    def authoritative_outcome_is_explicit(self) -> OutcomeEvidence:
        terminal = self.disposition in {"verified", "rejected"}
        if terminal != (self.score is not None and self.success is not None):
            raise ValueError("terminal outcome evidence requires explicit score and success")
        if self.disposition == "verified" and self.success is not True:
            raise ValueError("verified outcome evidence requires success=true")
        if self.disposition == "rejected" and self.success is not False:
            raise ValueError("rejected outcome evidence requires success=false")
        if terminal != (self.evaluated_output_digest is not None):
            raise ValueError("terminal outcome evidence requires the evaluated output digest")
        return self


class TokenAccounting(StrictRecord):
    input_tokens: Annotated[int, Field(ge=0)] | None
    output_tokens: Annotated[int, Field(ge=0)] | None
    total_tokens: Annotated[int, Field(ge=0)] | None
    counting_mode: NonEmpty
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def totals_are_honest(self) -> TokenAccounting:
        missing = (
            self.input_tokens is None or self.output_tokens is None or self.total_tokens is None
        )
        if missing != (self.missing_reason is not None):
            raise ValueError("missing token accounting requires exactly one reason")
        if not missing:
            assert self.input_tokens is not None
            assert self.output_tokens is not None
            assert self.total_tokens is not None
            if self.total_tokens != self.input_tokens + self.output_tokens:
                raise ValueError("reported token total disagrees with input and output")
        return self


class AtlasTrialResult(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    result_id: NonEmpty
    request_id: NonEmpty
    request_digest: Sha256
    research_execution_digest: Sha256
    generation_provider: NonEmpty | None = None
    generation_model_id: NonEmpty | None = None
    generation_protocol: NonEmpty | None = None
    raw_request_digest: Sha256 | None = None
    raw_response_digest: Sha256 | None = None
    capabilities_digest: Sha256 | None = None
    external_call_artifact: ArtifactRef | None = None
    status: TrialStatus
    score: FiniteFloat | None
    success: bool | None
    confidence: Probability | None = None
    abstained: bool = False
    failure_origin: FailureOrigin | None = None
    failure_codes: tuple[NonEmpty, ...] = ()
    verifier_evidence: tuple[OutcomeEvidence, ...] = ()
    primary_authority: AuthorityKind | None = None
    response_digest: Sha256 | None = None
    response_artifact: ArtifactRef | None = None
    grader_artifacts: tuple[ArtifactRef, ...] = ()
    tokens: TokenAccounting
    latency_ms: NonNegativeFinite | None = None
    wall_time_ms: NonNegativeFinite | None = None
    cost_usd: NonNegativeFinite | None = None
    tool_calls: tuple[dict[str, Any], ...] = ()
    retry_count: Annotated[int, Field(ge=0)] = 0
    contamination_checks: Annotated[dict[NonEmpty, bool], Field(min_length=1)]
    result_digest: Sha256
    completed_at: datetime

    @model_validator(mode="after")
    def outcome_is_complete_and_authoritative(self) -> AtlasTrialResult:
        generation_values = (
            self.generation_provider,
            self.generation_model_id,
            self.generation_protocol,
            self.raw_request_digest,
            self.raw_response_digest,
            self.capabilities_digest,
            self.external_call_artifact,
        )
        if any(value is None for value in generation_values) != all(
            value is None for value in generation_values
        ):
            raise ValueError("generation attribution evidence must be wholly present or absent")
        observed = self.status in {
            TrialStatus.VERIFIED_SUCCESS,
            TrialStatus.VERIFIED_FAILURE,
            TrialStatus.PARTIAL,
            TrialStatus.ABSTAINED,
            TrialStatus.MALFORMED,
        }
        post_response = observed or self.status in {
            TrialStatus.UNSCORABLE,
            TrialStatus.VERIFIER_FAILURE,
            TrialStatus.PARSER_FAILURE,
            TrialStatus.CONTAMINATED,
        }
        if post_response and all(value is None for value in generation_values):
            raise ValueError("post-response outcomes require captured generation attribution")
        if not post_response and any(value is not None for value in generation_values):
            raise ValueError("pre-response outcomes cannot claim captured generation evidence")
        if observed and (self.success is None or self.score is None):
            raise ValueError("observed model outcomes require explicit success and score")
        if not observed and (self.success is not None or self.score is not None):
            raise ValueError("missing/infrastructure outcomes cannot receive model scores")
        if self.status == TrialStatus.VERIFIED_SUCCESS and self.success is not True:
            raise ValueError("verified success requires success=true")
        if self.status == TrialStatus.VERIFIED_FAILURE and self.success is not False:
            raise ValueError("verified failure requires success=false")
        if self.status == TrialStatus.ABSTAINED and not self.abstained:
            raise ValueError("abstained status requires an explicit abstention")
        infra = self.status in {
            TrialStatus.TIMEOUT,
            TrialStatus.INFRASTRUCTURE_FAILURE,
            TrialStatus.NOT_RUN,
        }
        if infra and self.failure_origin != FailureOrigin.INFRASTRUCTURE:
            raise ValueError("infrastructure outcomes require infrastructure failure origin")
        if (
            self.status == TrialStatus.CONTAMINATED
            and self.failure_origin != FailureOrigin.CONTAMINATION
        ):
            raise ValueError("contaminated outcomes require contamination failure origin")
        expected_missing_origins = {
            TrialStatus.UNSCORABLE: FailureOrigin.UNKNOWN,
            TrialStatus.VERIFIER_FAILURE: FailureOrigin.VERIFIER,
            TrialStatus.PARSER_FAILURE: FailureOrigin.PARSER,
        }
        if (
            self.status in expected_missing_origins
            and self.failure_origin != expected_missing_origins[self.status]
        ):
            raise ValueError("unscorable outcomes require their non-model failure origin")
        if observed:
            if not self.verifier_evidence or self.primary_authority is None:
                raise ValueError("observed outcomes require verifier evidence and authority")
            authorities = [evidence.authority for evidence in self.verifier_evidence]
            if self.primary_authority not in authorities:
                raise ValueError("primary authority is absent from verifier evidence")
            non_model = {
                AuthorityKind.DETERMINISTIC,
                AuthorityKind.KERNEL,
                AuthorityKind.ENVIRONMENT,
            }
            if (
                non_model.intersection(authorities)
                and self.primary_authority == AuthorityKind.MODEL
            ):
                raise ValueError("model grading cannot outrank available deterministic authority")
            if self.response_digest is None or self.response_artifact is None:
                raise ValueError("observed outcomes require retained response evidence")
        if self.status in {
            TrialStatus.UNSCORABLE,
            TrialStatus.VERIFIER_FAILURE,
            TrialStatus.PARSER_FAILURE,
            TrialStatus.CONTAMINATED,
        } and (self.response_digest is None or self.response_artifact is None):
            raise ValueError("post-response missing outcomes require retained response evidence")
        if (
            not self.contamination_checks or not all(self.contamination_checks.values())
        ) and self.status != TrialStatus.CONTAMINATED:
            raise ValueError("failed contamination checks require contaminated status")
        if self.status == TrialStatus.CONTAMINATED and all(self.contamination_checks.values()):
            raise ValueError("contaminated outcomes require at least one failed check")
        identity = self.model_dump(
            mode="json", exclude={"result_id", "result_digest", "completed_at"}
        )
        if self.result_digest != sha256_digest(identity):
            raise ValueError("trial result digest disagrees with immutable evidence")
        if self.result_id != content_id("atlas-result", identity):
            raise ValueError("trial result ID disagrees with immutable evidence")
        return self


class MetricEstimate(StrictRecord):
    metric_id: NonEmpty
    value: FiniteFloat | None
    lower: FiniteFloat | None
    upper: FiniteFloat | None
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)] | None
    planned_trials: Annotated[int, Field(ge=0)]
    observed_trials: Annotated[int, Field(ge=0)]
    missing_trials: Annotated[int, Field(ge=0)]
    infrastructure_failures: Annotated[int, Field(ge=0)]
    contaminated_trials: Annotated[int, Field(ge=0)]
    evidence_result_digests: tuple[Sha256, ...]
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def denominators_are_fixed(self) -> MetricEstimate:
        if self.observed_trials + self.missing_trials != self.planned_trials:
            raise ValueError("metric outcomes must account for every planned trial")
        if self.infrastructure_failures + self.contaminated_trials > self.missing_trials:
            raise ValueError("excluded outcome counts exceed missing trials")
        missing_value = self.value is None
        if missing_value != (self.missing_reason is not None):
            raise ValueError("missing metric value requires exactly one reason")
        interval_missing = self.lower is None or self.upper is None or self.confidence_level is None
        if missing_value != interval_missing:
            raise ValueError("metric interval must be complete exactly when the value is present")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("metric confidence bounds are reversed")
        if len(self.evidence_result_digests) != len(set(self.evidence_result_digests)):
            raise ValueError("metric evidence digests must be unique")
        if len(self.evidence_result_digests) != self.observed_trials:
            raise ValueError("metric evidence must account for every observed trial")
        return self


class CapabilityCurvePoint(StrictRecord):
    difficulty: FiniteFloat
    success_probability: Probability | None
    lower: Probability | None
    upper: Probability | None
    trials: Annotated[int, Field(ge=0)]
    missing: Annotated[int, Field(ge=0)]


class CapabilityCurve(StrictRecord):
    curve_id: NonEmpty
    family_id: NonEmpty
    condition_id: NonEmpty
    boundary_probability: Probability
    estimated_boundary: FiniteFloat | None
    points: Annotated[tuple[CapabilityCurvePoint, ...], Field(min_length=1)]
    monotonic_violations: Annotated[int, Field(ge=0)]
    evidence_result_digests: tuple[Sha256, ...]


class OntologyNode(StrictRecord):
    node_id: NonEmpty
    title: NonEmpty
    description: NonEmpty
    origin: FailureOrigin
    parent_ids: tuple[NonEmpty, ...] = ()
    changed_axes: tuple[ResearchAxis, ...] = ()
    examples: tuple[NonEmpty, ...] = ()


class OntologyManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    ontology_id: NonEmpty
    version: NonEmpty
    nodes: Annotated[tuple[OntologyNode, ...], Field(min_length=1)]
    manifest_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def graph_is_valid(self) -> OntologyManifest:
        identifiers = [node.node_id for node in self.nodes]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("ontology node IDs must be unique")
        if tuple(sorted(identifiers)) != tuple(identifiers):
            raise ValueError("ontology nodes must use canonical lexical order")
        known = set(identifiers)
        for node in self.nodes:
            if not set(node.parent_ids).issubset(known):
                raise ValueError("ontology node cites an unknown parent")
            if node.node_id in node.parent_ids:
                raise ValueError("ontology node cannot parent itself")
        identity = self.model_dump(mode="json", exclude={"manifest_digest", "created_at"})
        if self.manifest_digest != sha256_digest(identity):
            raise ValueError("ontology digest disagrees with its content")
        return self


class FailureAssignment(StrictRecord):
    node_id: NonEmpty
    confidence: Probability
    evidence_result_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    proposed_by: NonEmpty
    automated: bool
    reviewed_by: NonEmpty | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def automated_labels_are_reviewed(self) -> FailureAssignment:
        if (self.reviewed_by is None) != (self.reviewed_at is None):
            raise ValueError("failure assignment review identity and time must be complete")
        return self


class FailureCluster(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    cluster_id: NonEmpty
    campaign_digest: Sha256
    ontology_digest: Sha256
    title: NonEmpty
    assignments: Annotated[tuple[FailureAssignment, ...], Field(min_length=1)]
    member_result_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    exemplar_result_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    reproducibility: Probability
    stability: Probability
    severity: Annotated[int, Field(ge=1, le=5)]
    suspected_changed_axes: tuple[ResearchAxis, ...]
    status: ReviewStatus
    reviewed_by: NonEmpty | None = None
    review_reason: NonEmpty | None = None
    probe_set_digest: Sha256 | None = None
    cluster_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def cluster_admission_is_reviewable(self) -> FailureCluster:
        if tuple(sorted(self.member_result_digests)) != self.member_result_digests:
            raise ValueError("cluster members must use canonical lexical order")
        if not set(self.exemplar_result_digests).issubset(self.member_result_digests):
            raise ValueError("cluster exemplars must be cluster members")
        if self.status == ReviewStatus.ADMITTED:
            if self.reviewed_by is None or self.review_reason is None:
                raise ValueError("admitted failure clusters require human review provenance")
            if any(
                assignment.automated and assignment.reviewed_by is None
                for assignment in self.assignments
            ):
                raise ValueError("automated labels must be reviewed before cluster admission")
        identity = self.model_dump(mode="json", exclude={"cluster_digest", "created_at"})
        if self.cluster_digest != sha256_digest(identity):
            raise ValueError("failure cluster digest disagrees with its evidence")
        return self


class PhenomenonManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    phenomenon_id: NonEmpty
    ontology_digest: Sha256
    title: NonEmpty
    description: NonEmpty
    ontology_node_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    behavioral_definition: NonEmpty
    inclusion_rules: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    exclusion_rules: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    manifest_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def phenomenon_id_is_stable(self) -> PhenomenonManifest:
        identity = self.model_dump(
            mode="json", exclude={"phenomenon_id", "manifest_digest", "created_at"}
        )
        digest = sha256_digest(identity)
        if self.manifest_digest != digest:
            raise ValueError("phenomenon digest disagrees with its definition")
        if self.phenomenon_id != content_id("phenomenon", identity):
            raise ValueError("phenomenon ID disagrees with its definition")
        return self


class ProbeSetManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    probe_set_id: NonEmpty
    phenomenon_id: NonEmpty
    phenomenon_digest: Sha256
    suite_digest: Sha256
    item_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    matched_dimensions: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    outcome_result_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    outcome_digest: Sha256
    manifest_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def probe_set_is_joinable(self) -> ProbeSetManifest:
        if tuple(sorted(self.item_digests)) != self.item_digests:
            raise ValueError("probe item digests must use canonical lexical order")
        expected_outcome = sha256_digest(self.outcome_result_digests)
        if self.outcome_digest != expected_outcome:
            raise ValueError("probe-set outcome digest disagrees with its results")
        identity = self.model_dump(
            mode="json", exclude={"probe_set_id", "manifest_digest", "created_at"}
        )
        if self.manifest_digest != sha256_digest(identity):
            raise ValueError("probe-set manifest digest disagrees with its content")
        if self.probe_set_id != content_id("probe-set", identity):
            raise ValueError("probe-set ID disagrees with its content")
        return self


class LocalObservation(StrictRecord):
    metric: MetricEstimate
    condition_id: NonEmpty
    suite_digest: Sha256
    research_execution_digest: Sha256
    extrapolated: bool = False
    extrapolation_method: NonEmpty | None = None

    @model_validator(mode="after")
    def extrapolation_is_labelled(self) -> LocalObservation:
        if self.extrapolated != (self.extrapolation_method is not None):
            raise ValueError("extrapolated observations require an explicit method")
        return self


class AtlasSnapshot(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    snapshot_id: NonEmpty
    campaign_digest: Sha256
    research_execution_digest: Sha256
    harness_profile_digest: Sha256
    ontology_digest: Sha256
    upstream_claim_ids: tuple[NonEmpty, ...]
    local_observations: tuple[LocalObservation, ...]
    curves: tuple[CapabilityCurve, ...]
    failure_cluster_digests: tuple[Sha256, ...]
    unknowns: tuple[NonEmpty, ...]
    complete: bool
    promotion_eligible: bool
    promotion_checkpoint_decision_ids: tuple[NonEmpty, ...] = ()
    snapshot_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def snapshot_keeps_claim_types_separate(self) -> AtlasSnapshot:
        if self.promotion_eligible and (not self.complete or self.unknowns):
            raise ValueError("promotion-eligible snapshots must be complete without unknowns")
        if self.promotion_eligible != bool(self.promotion_checkpoint_decision_ids):
            raise ValueError(
                "promotion eligibility requires exactly the cited checkpoint decisions"
            )
        if tuple(
            sorted(self.promotion_checkpoint_decision_ids)
        ) != self.promotion_checkpoint_decision_ids or len(
            self.promotion_checkpoint_decision_ids
        ) != len(set(self.promotion_checkpoint_decision_ids)):
            raise ValueError("promotion checkpoint decisions must be unique and canonical")
        if any(
            observation.research_execution_digest != self.research_execution_digest
            for observation in self.local_observations
        ):
            raise ValueError("snapshot observations must share one exact research execution")
        identity = self.model_dump(
            mode="json", exclude={"snapshot_id", "snapshot_digest", "created_at"}
        )
        if self.snapshot_digest != sha256_digest(identity):
            raise ValueError("Atlas snapshot digest disagrees with its evidence")
        if self.snapshot_id != content_id("atlas-snapshot", identity):
            raise ValueError("Atlas snapshot ID disagrees with its evidence")
        return self


class ComparisonMetricDelta(StrictRecord):
    left_condition_id: NonEmpty
    left_suite_digest: Sha256
    right_condition_id: NonEmpty
    right_suite_digest: Sha256
    metric_id: NonEmpty
    left_value: FiniteFloat
    right_value: FiniteFloat
    delta: FiniteFloat
    lower: FiniteFloat
    upper: FiniteFloat
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)]
    evidence_result_digests: tuple[Sha256, ...]

    @model_validator(mode="after")
    def delta_is_canonical(self) -> ComparisonMetricDelta:
        if not math.isclose(self.delta, self.right_value - self.left_value, abs_tol=1e-12):
            raise ValueError("comparison delta disagrees with left and right values")
        if self.lower > self.upper:
            raise ValueError("comparison delta confidence bounds are reversed")
        if tuple(sorted(self.evidence_result_digests)) != self.evidence_result_digests or len(
            self.evidence_result_digests
        ) != len(set(self.evidence_result_digests)):
            raise ValueError("comparison delta evidence must be unique and canonical")
        return self

    @property
    def coordinate(self) -> str:
        return (
            f"{self.left_condition_id}|{self.left_suite_digest}->"
            f"{self.right_condition_id}|{self.right_suite_digest}|{self.metric_id}"
        )


class AtlasComparison(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    comparison_id: NonEmpty
    left_snapshot_digest: Sha256
    right_snapshot_digest: Sha256
    allowed_axes: tuple[ResearchAxis, ...]
    observed_axes: tuple[ResearchAxis, ...]
    comparability_evidence_digest: Sha256
    metric_deltas: tuple[ComparisonMetricDelta, ...]
    regressions: tuple[NonEmpty, ...]
    improvements: tuple[NonEmpty, ...]
    unknowns: tuple[NonEmpty, ...]
    causal_claim_permitted: bool
    comparison_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def causal_claim_respects_comparability(self) -> AtlasComparison:
        if self.left_snapshot_digest == self.right_snapshot_digest:
            raise ValueError("Atlas comparison snapshots must be distinct")
        for axes, label in (
            (self.allowed_axes, "allowed axes"),
            (self.observed_axes, "observed axes"),
        ):
            if tuple(sorted(axes, key=lambda axis: axis.value)) != axes or len(axes) != len(
                set(axes)
            ):
                raise ValueError(f"comparison {label} must be unique and canonical")
        undeclared = set(self.observed_axes) - set(self.allowed_axes)
        permitted = not undeclared and not self.unknowns
        if self.causal_claim_permitted != permitted:
            raise ValueError("comparison causal claim disagrees with declared axes or unknowns")
        if self.causal_claim_permitted and not self.metric_deltas:
            raise ValueError("causal comparisons require at least one declared metric pair")
        delta_coordinates = tuple(delta.coordinate for delta in self.metric_deltas)
        if delta_coordinates != tuple(sorted(delta_coordinates)) or len(delta_coordinates) != len(
            set(delta_coordinates)
        ):
            raise ValueError("comparison metric deltas must use unique canonical coordinates")
        for labels, name in (
            (self.regressions, "regressions"),
            (self.improvements, "improvements"),
        ):
            if labels != tuple(sorted(labels)) or len(labels) != len(set(labels)):
                raise ValueError(f"comparison {name} must be unique and canonical")
        identity = self.model_dump(
            mode="json", exclude={"comparison_id", "comparison_digest", "created_at"}
        )
        if self.comparison_digest != sha256_digest(identity):
            raise ValueError("Atlas comparison digest disagrees with immutable evidence")
        if self.comparison_id != content_id("atlas-comparison", identity):
            raise ValueError("Atlas comparison ID disagrees with immutable evidence")
        return self


class ExploratoryFailureProposal(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    proposal_id: NonEmpty
    consent_evidence_digest: Sha256
    consent_lane: NonEmpty
    source_trace_digest: Sha256
    source_trace_artifact: ArtifactRef
    redacted_excerpt_digest: Sha256
    proposed_phenomenon: NonEmpty
    proposed_failure_node_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    deduplication_key: Sha256
    raw_chat_promoted: Literal[False] = False
    created_at: datetime


class ExploratoryReproduction(StrictRecord):
    reproduction_id: NonEmpty
    proposal_id: NonEmpty
    research_execution_digest: Sha256
    independent_item_digest: Sha256
    result_digests: Annotated[tuple[Sha256, ...], Field(min_length=2)]
    reproduced: bool
    stability: Probability
    reviewer: NonEmpty
    created_at: datetime


class ChallengeAdmissionDecision(StrictRecord):
    decision_id: NonEmpty
    proposal_id: NonEmpty
    reproduction_id: NonEmpty
    action: Literal["admit", "reject"]
    challenge_suite_digest: Sha256 | None = None
    admitted_item_digest: Sha256 | None = None
    reviewer: NonEmpty
    reason: NonEmpty
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def admission_has_destination(self) -> ChallengeAdmissionDecision:
        admitted = self.action == "admit"
        if admitted != (
            self.challenge_suite_digest is not None and self.admitted_item_digest is not None
        ):
            raise ValueError("challenge admission requires both destination suite and item")
        return self


class TrainingFailureEligibility(StrictRecord):
    assessment_id: NonEmpty
    failure_cluster_digest: Sha256
    source_suite_digest: Sha256
    source_evaluation_class: EvaluationClass
    independently_reproduced: bool
    stable: bool
    model_failure_confirmed: bool
    harness_effects_ruled_out: bool
    verifier_authority_confirmed: bool
    rights_permit_training: bool
    contamination_cleared: bool
    promotion_suite_excluded: bool
    adaptive_search_excluded: bool
    eligible: bool
    allowed_lanes: tuple[NonEmpty, ...]
    blocking_reasons: tuple[NonEmpty, ...]
    direct_compiler_ingestion_permitted: Literal[False] = False
    requires_governed_corpus_materialization: Literal[True] = True
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def training_boundary_fails_closed(self) -> TrainingFailureEligibility:
        allowed = (
            self.independently_reproduced
            and self.stable
            and self.model_failure_confirmed
            and self.harness_effects_ruled_out
            and self.verifier_authority_confirmed
            and self.rights_permit_training
            and self.contamination_cleared
            and self.promotion_suite_excluded
            and self.adaptive_search_excluded
            and self.source_evaluation_class
            not in {EvaluationClass.CHALLENGE, EvaluationClass.SEALED_PROMOTION}
        )
        if self.eligible != allowed:
            raise ValueError("training eligibility disagrees with governed failure evidence")
        if self.eligible != bool(self.allowed_lanes):
            raise ValueError(
                "eligible failures require lanes; ineligible failures cannot have lanes"
            )
        if self.eligible == bool(self.blocking_reasons):
            raise ValueError("blocking reasons must exist exactly for ineligible failures")
        return self


class MemoryInterventionEligibility(StrictRecord):
    assessment_id: NonEmpty
    failure_cluster_digest: Sha256
    research_execution_digest: Sha256
    source_suite_digest: Sha256
    source_evaluation_class: EvaluationClass
    independently_reproduced: bool
    stable: bool
    model_failure_confirmed: bool
    harness_effects_ruled_out: bool
    verifier_authority_confirmed: bool
    rights_permit_internal_research: bool
    contamination_cleared: bool
    sealed_content_excluded: bool
    eligible: bool
    blocking_reasons: tuple[NonEmpty, ...]
    direct_memory_write_permitted: Literal[False] = False
    requires_developmental_episode: Literal[True] = True
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def memory_boundary_fails_closed(self) -> MemoryInterventionEligibility:
        allowed = (
            self.independently_reproduced
            and self.stable
            and self.model_failure_confirmed
            and self.harness_effects_ruled_out
            and self.verifier_authority_confirmed
            and self.rights_permit_internal_research
            and self.contamination_cleared
            and self.sealed_content_excluded
            and self.source_evaluation_class != EvaluationClass.SEALED_PROMOTION
        )
        if self.eligible != allowed:
            raise ValueError("memory eligibility disagrees with governed failure evidence")
        if self.eligible == bool(self.blocking_reasons):
            raise ValueError("blocking reasons must exist exactly for ineligible memory candidates")
        return self


class OfflineVerificationRecord(StrictRecord):
    campaign_digest: Sha256
    ontology_digest: Sha256
    suite_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    generated_items: Annotated[int, Field(ge=0)]
    structurally_validated_matched_neighborhoods: Annotated[int, Field(ge=0)]
    matched_neighborhood_evidence_digests: tuple[Sha256, ...]
    verified_oracles: Annotated[int, Field(ge=0)]
    oracle_evidence_digests: tuple[Sha256, ...]
    external_requests_made: Literal[0] = 0
    external_cost_usd: NonNegativeFinite = 0.0
    checks: Annotated[dict[NonEmpty, bool], Field(min_length=1)]
    unknowns: tuple[NonEmpty, ...]
    record_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def record_is_content_bound(self) -> OfflineVerificationRecord:
        if self.external_cost_usd != 0.0:
            raise ValueError("offline verification cannot report external cost")
        if (
            len(self.matched_neighborhood_evidence_digests)
            != self.structurally_validated_matched_neighborhoods
        ):
            raise ValueError("matched-neighborhood count differs from retained evidence")
        if len(self.oracle_evidence_digests) != self.verified_oracles:
            raise ValueError("oracle execution count differs from retained evidence")
        for digests, label in (
            (self.matched_neighborhood_evidence_digests, "matched-neighborhood evidence"),
            (self.oracle_evidence_digests, "oracle evidence"),
        ):
            if tuple(sorted(digests)) != digests or len(digests) != len(set(digests)):
                raise ValueError(f"{label} must be unique and canonical")
        identity = self.model_dump(mode="json", exclude={"record_digest", "created_at"})
        if self.record_digest != sha256_digest(identity):
            raise ValueError("offline verification digest disagrees with its checks")
        return self
