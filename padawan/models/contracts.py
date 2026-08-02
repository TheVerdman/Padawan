from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
NonEmpty = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Score = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)


class CorpusPool(StrEnum):
    CURRICULUM = "curriculum"
    ROTATING_SHADOW = "rotating_shadow"
    SEALED_ANCHOR = "sealed_anchor"
    QUARANTINE = "quarantine"


class VisibilityClass(StrEnum):
    TRAINING = "training"
    EVALUATION = "evaluation"
    SEALED = "sealed"


class ItemStatus(StrEnum):
    ACTIVE = "active"
    LEASED = "leased"
    RETIRED = "retired"
    QUARANTINED = "quarantined"


class ExposureType(StrEnum):
    PROMPT = "prompt"
    ANSWER = "answer"
    CRITIQUE = "critique"
    REPAIR = "repair"
    METADATA = "metadata"
    RETRIEVAL = "retrieval"
    TRAINING = "training"


class LifecycleStatus(StrEnum):
    ACTIVE = "active"
    EXPERIMENTAL = "experimental"
    CANONICAL = "canonical"
    ARCHIVED = "archived"
    REVOKED = "revoked"


class ResearchRole(StrEnum):
    TARGET = "target"
    BASELINE = "baseline"
    TEACHER = "teacher"
    VERIFIER = "verifier"
    ADJUDICATOR = "adjudicator"


class CapabilityAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class GradeOutcome(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    INVALID_PROCESS = "invalid_process"
    MALFORMED = "malformed"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    GRADER_FAILURE = "grader_failure"


class TeacherMode(StrEnum):
    SOCRATIC_HINT = "socratic_hint"
    DIAGNOSTIC_CRITIQUE = "diagnostic_critique"
    GENERAL_PRINCIPLE = "general_principle"
    MINIMAL_REPAIR = "minimal_repair"
    FULL_DEMONSTRATION = "full_demonstration"
    CONTRASTIVE_EXPLANATION = "contrastive_explanation"
    MICRO_CURRICULUM = "micro_curriculum"
    METACOGNITIVE_FEEDBACK = "metacognitive_feedback"


class CommentValidationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVIEW_REQUIRED = "review_required"


class StudentOutcome(StrEnum):
    ROBUST_SUCCESS = "robust_success"
    CORRECT_INVALID_PROCESS = "correct_with_invalid_process"
    CORRECT_SHORTCUT = "correct_by_shortcut"
    CORRECT_GUESS = "correct_by_guess"
    PARTIAL_PROGRESS = "partial_progress"
    EXECUTION_ERROR = "execution_error"
    REASONING_ERROR = "reasoning_error"
    TASK_MISUNDERSTANDING = "task_misunderstanding"
    TOOL_USE_ERROR = "tool_use_error"
    ANSWER_EXTRACTION_ERROR = "answer_extraction_error"
    REFUSAL = "refusal"
    TIMEOUT = "timeout"
    MALFORMED_OUTPUT = "malformed_output"


class TeachingOutcome(StrEnum):
    SUCCESSFUL_DIAGNOSIS = "successful_diagnosis"
    SUCCESSFUL_REPAIR = "successful_repair"
    SUCCESSFUL_TRANSFER = "successful_transfer"
    REVISION_ONLY_SUCCESS = "revision_only_success"
    NO_EFFECT = "no_effect"
    MISUNDERSTOOD = "misunderstood_intervention"
    COPIED_WITHOUT_TRANSFER = "copied_answer_without_transfer"
    HARMFUL = "harmful_intervention"
    UNSUPPORTED_CRITIQUE = "unsupported_critique"
    CONTRADICTION = "contradiction_of_evidence"
    EXCESSIVE_LEAKAGE = "excessive_answer_leakage"


class SystemOutcome(StrEnum):
    GRADER_DEFECT = "grader_defect"
    ITEM_DEFECT = "item_defect"
    SIBLING_MISMATCH = "sibling_mismatch"
    CONTAMINATION = "contamination"
    RETRIEVAL_DEFECT = "retrieval_defect"
    COMPACTION_LOSS = "compaction_loss"
    PROVIDER_FAILURE = "provider_failure"
    RUNTIME_FAILURE = "runtime_failure"
    STATE_CORRUPTION = "state_corruption"
    RETIREMENT_FAILURE = "retirement_failure"
    UPDATE_REGRESSION = "update_regression"


class RunState(StrEnum):
    CREATED = "CREATED"
    ITEMS_LEASED = "ITEMS_LEASED"
    BASE_STATE_SNAPSHOTTED = "BASE_STATE_SNAPSHOTTED"
    BRANCHES_CREATED = "BRANCHES_CREATED"
    COLD_ATTEMPT_RUNNING = "COLD_ATTEMPT_RUNNING"
    COLD_ATTEMPT_STORED = "COLD_ATTEMPT_STORED"
    COLD_GRADED = "COLD_GRADED"
    TEACHER_REQUESTED = "TEACHER_REQUESTED"
    TEACHER_RESPONSE_STORED = "TEACHER_RESPONSE_STORED"
    COMMENT_VALIDATED = "COMMENT_VALIDATED"
    REVISION_RUNNING = "REVISION_RUNNING"
    REVISION_STORED = "REVISION_STORED"
    REVISION_GRADED = "REVISION_GRADED"
    TRANSFER_RUNNING = "TRANSFER_RUNNING"
    TRANSFER_STORED = "TRANSFER_STORED"
    TRANSFER_GRADED = "TRANSFER_GRADED"
    MEMORY_DECIDED = "MEMORY_DECIDED"
    EXPOSURES_RECORDED = "EXPOSURES_RECORDED"
    ITEMS_RETIRED = "ITEMS_RETIRED"
    EPISODE_COMMITTED = "EPISODE_COMMITTED"
    COMPLETE = "COMPLETE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class LessonStatus(StrEnum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    INVALIDATED = "invalidated"
    RETIRED = "retired"
    REVIEW_REQUIRED = "review_required"


class ArtifactRef(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    artifact_id: NonEmpty
    uri: Annotated[str, Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")]
    digest: Sha256
    media_type: NonEmpty
    size_bytes: Annotated[int, Field(ge=0)]
    restricted: bool = False
    raw_data: bool = False


class CompetencyRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    competency_id: NonEmpty
    title: NonEmpty
    description: NonEmpty
    parent_competency_id: str | None = None
    prerequisite_competency_ids: tuple[str, ...] = ()
    grader_requirements: tuple[str, ...]
    permissible_teacher_modes: tuple[TeacherMode, ...]
    difficulty_calibration: dict[str, Any]
    version: Annotated[int, Field(ge=1)] = 1
    created_at: datetime


class VerifierSpec(StrictRecord):
    verifier_type: NonEmpty
    verifier_version: NonEmpty
    parameters: dict[str, Any]


class CorpusItemRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    competency_id: NonEmpty
    template_family_id: NonEmpty
    instance_group_id: NonEmpty
    item_id: NonEmpty
    generation_seed: int
    generator_version: NonEmpty
    difficulty: Score
    prompt: NonEmpty
    expected_answer: dict[str, Any] | None = None
    verifier_spec: VerifierSpec
    pool: CorpusPool
    status: ItemStatus = ItemStatus.ACTIVE
    source: NonEmpty
    license: NonEmpty
    contamination_scope: Literal["item", "instance_group", "template_family", "lineage"]
    created_at: datetime
    retired_at: datetime | None = None
    retirement_reason: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()


class ExposureRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    exposure_id: NonEmpty
    student_id: NonEmpty
    checkpoint_id: NonEmpty
    state_id: NonEmpty
    item_id: NonEmpty
    template_family_id: NonEmpty
    instance_group_id: NonEmpty
    exposure_type: ExposureType
    prompt_exposed: bool
    answer_exposed: bool
    critique_exposed: bool
    repair_exposed: bool
    metadata_exposed: bool
    episode_id: str | None = None
    created_at: datetime


class CompetencyEstimate(StrictRecord):
    competency_id: NonEmpty
    mean: Score
    confidence: Score
    observations: Annotated[int, Field(ge=0)]


class Hypothesis(StrictRecord):
    hypothesis_id: NonEmpty
    statement: NonEmpty
    confidence: Score
    evidence_refs: tuple[str, ...] = ()


class StudentStateRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    state_id: NonEmpty
    student_id: NonEmpty
    checkpoint_id: NonEmpty
    runtime_id: NonEmpty
    research_role: ResearchRole = ResearchRole.TARGET
    parent_state_id: str | None = None
    branch_id: NonEmpty
    compacted_working_state: dict[str, Any]
    lesson_memory_refs: tuple[str, ...] = ()
    unresolved_hypotheses: tuple[Hypothesis, ...] = ()
    competency_estimates: tuple[CompetencyEstimate, ...] = ()
    active_experiment_id: str | None = None
    state_hash: Sha256
    creation_reason: NonEmpty
    lifecycle_status: LifecycleStatus
    created_at: datetime


class StateForkRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    fork_id: NonEmpty
    parent_state_id: NonEmpty
    treatment_state_id: NonEmpty
    control_state_id: NonEmpty
    treatment_branch_id: NonEmpty
    control_branch_id: NonEmpty
    intervention: dict[str, Any]
    created_at: datetime


class Capability(StrictRecord):
    availability: CapabilityAvailability
    reason: NonEmpty


class RuntimeCapabilities(StrictRecord):
    responses_api: Capability
    streaming: Capability
    cancellation: Capability
    logprobs: Capability
    token_ids: Capability
    private_reasoning: Capability
    reasoning_boundaries: Capability
    gpu_telemetry: Capability
    router_telemetry: Capability


class SamplingConfiguration(StrictRecord):
    temperature: float | None = None
    top_p: float | None = None
    max_output_tokens: Annotated[int, Field(gt=0)]
    seed: int | None = None
    top_logprobs: Annotated[int, Field(ge=0, le=20)] | None = None
    stop: tuple[str, ...] = ()


class ChannelSpan(StrictRecord):
    channel: Literal["private_reasoning", "public_derivation", "final_answer", "tool"]
    start_token: Annotated[int, Field(ge=0)]
    end_token: Annotated[int, Field(ge=0)]
    source: Literal["runtime", "provider", "parser"]

    @model_validator(mode="after")
    def ordered(self) -> ChannelSpan:
        if self.end_token < self.start_token:
            raise ValueError("end_token must not precede start_token")
        return self


class ToolCallRecord(StrictRecord):
    call_id: NonEmpty
    name: NonEmpty
    arguments: dict[str, Any]
    raw_arguments: str | None = None


class ObservationRecord(StrictRecord):
    call_id: NonEmpty
    content: str
    success: bool
    artifact_refs: tuple[ArtifactRef, ...] = ()


class AttemptRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    attempt_id: NonEmpty
    episode_id: NonEmpty
    item_id: NonEmpty
    state_before_id: NonEmpty
    state_after_id: str | None = None
    rendered_messages: tuple[dict[str, Any], ...]
    rendered_prompt: NonEmpty
    input_token_ids: tuple[int, ...] | None = None
    raw_generation_ref: ArtifactRef
    output_token_ids: tuple[int, ...] | None = None
    token_logprobs: tuple[float, ...] | None = None
    channel_spans: tuple[ChannelSpan, ...] = ()
    private_reasoning_ref: ArtifactRef | None = None
    public_derivation: dict[str, Any] | None = None
    final_answer: dict[str, Any] | str | None = None
    tool_calls: tuple[ToolCallRecord, ...] = ()
    observations: tuple[ObservationRecord, ...] = ()
    timing_ms: dict[str, float]
    gpu_telemetry: dict[str, Any] | None = None
    stop_reason: NonEmpty
    request_id: NonEmpty
    response_id: str | None = None
    model_id: NonEmpty
    checkpoint_id: NonEmpty
    runtime_id: NonEmpty
    runtime_version: NonEmpty
    research_role: ResearchRole = ResearchRole.TARGET
    sampling: SamplingConfiguration
    capabilities: RuntimeCapabilities
    artifacts: tuple[ArtifactRef, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def trace_channels_are_honest_and_aligned(self) -> AttemptRecord:
        if self.channel_spans and self.output_token_ids is None:
            raise ValueError("channel spans require output token IDs")
        if self.output_token_ids is not None:
            token_count = len(self.output_token_ids)
            if self.token_logprobs is not None and len(self.token_logprobs) != token_count:
                raise ValueError("token IDs and token logprobs are not aligned")
            ordered = sorted(
                self.channel_spans, key=lambda item: (item.start_token, item.end_token)
            )
            prior_end = 0
            for index, span in enumerate(ordered):
                if span.end_token > token_count:
                    raise ValueError("channel span exceeds output token IDs")
                if index and span.start_token < prior_end:
                    raise ValueError("private and public channel spans may not overlap")
                prior_end = span.end_token
        private_spans = [span for span in self.channel_spans if span.channel == "private_reasoning"]
        if private_spans and self.private_reasoning_ref is None:
            raise ValueError("private reasoning span has no restricted artifact")
        if (
            self.private_reasoning_ref is not None
            and self.capabilities.private_reasoning.availability != CapabilityAvailability.AVAILABLE
        ):
            raise ValueError("private reasoning artifact contradicts runtime capability")
        return self


class EvidenceRecord(StrictRecord):
    evidence_id: NonEmpty
    kind: Literal["deterministic", "tool", "trace", "public_step", "teacher", "system"]
    statement: NonEmpty
    payload: dict[str, Any]
    artifact_refs: tuple[ArtifactRef, ...] = ()


class GradeRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    grade_id: NonEmpty
    attempt_id: NonEmpty
    grader_type: NonEmpty
    grader_version: NonEmpty
    outcome: GradeOutcome
    score: Score
    deterministic: bool
    evidence: tuple[EvidenceRecord, ...]
    parsed_answer: dict[str, Any] | None = None
    public_step_validation: tuple[dict[str, Any], ...] = ()
    first_invalid_step_id: str | None = None
    error_class: str | None = None
    confidence: Score | None = None
    unsupported_claims: tuple[str, ...] = ()
    infrastructure_failure: bool = False
    student_failure: bool = False
    artifacts: tuple[ArtifactRef, ...] = ()
    created_at: datetime


class EvidenceCitation(StrictRecord):
    evidence_id: NonEmpty
    public_step_id: str | None = None
    private_span_start: Annotated[int, Field(ge=0)] | None = None
    private_span_end: Annotated[int, Field(ge=0)] | None = None
    prior_lesson_id: str | None = None

    @model_validator(mode="after")
    def complete_span(self) -> EvidenceCitation:
        if (self.private_span_start is None) != (self.private_span_end is None):
            raise ValueError("private trace citation must have both span bounds")
        if (
            self.private_span_start is not None
            and self.private_span_end is not None
            and self.private_span_end < self.private_span_start
        ):
            raise ValueError("private trace citation end precedes start")
        return self


class TeacherInterventionRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    intervention_id: NonEmpty
    episode_id: NonEmpty
    targeted_attempt_id: NonEmpty
    provider: NonEmpty
    model_id: NonEmpty
    mode: TeacherMode
    exact_prompt_ref: ArtifactRef
    raw_response_ref: ArtifactRef
    citations: tuple[EvidenceCitation, ...]
    claimed_first_consequential_error: str | None = None
    error_class: str | None = None
    lesson: NonEmpty
    repair: NonEmpty
    expected_transfer_scope: NonEmpty
    confidence: Score
    validation_status: CommentValidationStatus
    validation_errors: tuple[str, ...] = ()
    request_id: NonEmpty
    response_id: str | None = None
    token_usage: dict[str, int]
    latency_ms: float
    estimated_cost_usd: Annotated[float, Field(ge=0)] | None = None
    created_at: datetime


class RevisionRecord(StrictRecord):
    revision_id: NonEmpty
    original_attempt_id: NonEmpty
    intervention_id: NonEmpty
    revised_attempt_id: NonEmpty
    revised_grade_id: NonEmpty
    created_at: datetime


class TransferTrialRecord(StrictRecord):
    transfer_trial_id: NonEmpty
    source_lesson_id: str | None = None
    source_intervention_id: str | None = None
    inherited_state_id: NonEmpty
    item_id: NonEmpty
    treatment_condition: NonEmpty
    control_condition: NonEmpty
    treatment_attempt_id: NonEmpty
    control_attempt_id: NonEmpty
    treatment_grade_id: NonEmpty
    control_grade_id: NonEmpty
    delayed_retest_at: datetime | None = None
    contamination_checks: dict[str, bool]
    created_at: datetime


class MemoryWriteRecord(StrictRecord):
    lesson_id: NonEmpty
    action: Literal["write", "update", "invalidate", "rollback", "none"]
    reason: NonEmpty


class DevelopmentalEpisode(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    episode_id: NonEmpty
    student_state_before_id: NonEmpty
    task_item_id: NonEmpty
    research_role: ResearchRole = ResearchRole.TARGET
    initial_attempt_id: str | None = None
    grade_id: str | None = None
    diagnosis_ids: tuple[str, ...] = ()
    intervention_id: str | None = None
    revision_id: str | None = None
    transfer_trial_ids: tuple[str, ...] = ()
    student_state_after_id: str | None = None
    memory_writes: tuple[MemoryWriteRecord, ...] = ()
    exposure_ids: tuple[str, ...] = ()
    retirement_ids: tuple[str, ...] = ()
    student_outcome: StudentOutcome | None = None
    teaching_outcome: TeachingOutcome | None = None
    system_outcomes: tuple[SystemOutcome, ...] = ()
    pedagogical_metrics: dict[str, float]
    provenance_event_ids: tuple[str, ...]
    consolidation_proposal_ids: tuple[str, ...] = ()
    status: Literal["active", "complete", "failed", "review_required"]
    created_at: datetime
    completed_at: datetime | None = None


class LessonRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    lesson_id: NonEmpty
    version: Annotated[int, Field(ge=1)]
    competency_id: NonEmpty
    error_class: NonEmpty
    general_rule: NonEmpty
    applicability: NonEmpty
    exclusions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_episode_ids: tuple[str, ...]
    teacher_id: str | None = None
    confidence: Score
    successful_transfer_count: Annotated[int, Field(ge=0)] = 0
    failed_transfer_count: Annotated[int, Field(ge=0)] = 0
    harmful_retrieval_count: Annotated[int, Field(ge=0)] = 0
    state_lineage_id: NonEmpty
    branch_id: NonEmpty
    status: LessonStatus
    supersedes_version: int | None = None
    created_at: datetime


class CompactedStateRecord(StrictRecord):
    compacted_state_id: NonEmpty
    state_id: NonEmpty
    source_episode_ids: tuple[str, ...]
    validated_lessons: tuple[str, ...]
    unresolved_hypotheses: tuple[Hypothesis, ...]
    failed_strategies: tuple[dict[str, Any], ...]
    confidence_notes: tuple[str, ...]
    provenance_refs: tuple[str, ...]
    dropped: tuple[dict[str, Any], ...]
    trigger: Literal["token_budget", "episode_count", "memory_pressure", "manual", "experiment"]
    replay_input_digest: Sha256
    created_at: datetime
