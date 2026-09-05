from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from padawan.domains.contracts import TrainingLane
from padawan.models.contracts import (
    ArtifactRef,
    NonEmpty,
    ResearchRole,
    Sha256,
    SourceRights,
    StrictRecord,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest

TRAINING_COMPILER_VERSION: Literal["1.0.0"] = "1.0.0"
TRAINING_ROW_SCHEMA_VERSION: Literal["1.0.0"] = "1.0.0"
RIGHTS_POLICY_VERSION: Literal["1.0.0"] = "1.0.0"


class TrainingSourceStatus(StrEnum):
    ACTIVE = "active"
    REVIEW_REQUIRED = "review_required"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class TrainingSourceDocument(StrictRecord):
    schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    document_id: NonEmpty
    source_id: NonEmpty
    source_version: NonEmpty
    supersedes_document_id: str | None = None
    title: NonEmpty
    language: NonEmpty
    media_type: NonEmpty
    content_ref: ArtifactRef
    content_digest: Sha256
    rights: SourceRights
    rights_digest: Sha256
    quality_evidence_refs: tuple[NonEmpty, ...]
    admitted_by: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def source_is_governed(self) -> TrainingSourceDocument:
        if self.content_digest != self.content_ref.digest:
            raise ValueError("source content digest differs from its immutable artifact")
        if self.media_type != self.content_ref.media_type:
            raise ValueError("source media type differs from its immutable artifact")
        if self.rights_digest != sha256_digest(self.rights.model_dump(mode="json")):
            raise ValueError("source rights digest is invalid")
        if len(self.quality_evidence_refs) != len(set(self.quality_evidence_refs)):
            raise ValueError("quality evidence references must be unique")
        if self.supersedes_document_id == self.document_id:
            raise ValueError("a source document cannot supersede itself")
        return self


class TrainingSourceDecision(StrictRecord):
    schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    decision_id: NonEmpty
    document_id: NonEmpty
    status: TrainingSourceStatus
    contaminated: bool
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...]
    decided_by: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def decision_has_evidence(self) -> TrainingSourceDecision:
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise ValueError("source-decision evidence references must be unique")
        if self.contaminated and not self.evidence_refs:
            raise ValueError("contaminated source decisions require evidence")
        if self.status == TrainingSourceStatus.ACTIVE and self.contaminated:
            raise ValueError("contaminated sources cannot be active")
        return self


class AuthoredMessage(StrictRecord):
    role: Literal["developer", "system", "user", "assistant", "tool"]
    content: NonEmpty


class AuthoredTargetEvent(StrictRecord):
    type: Literal["assistant_message"] = "assistant_message"
    role: Literal["assistant"] = "assistant"
    content: NonEmpty


class AuthoredDemonstration(StrictRecord):
    """Governed gold behavior that is explicitly not an observed student attempt."""

    schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    demonstration_id: NonEmpty
    domain_id: NonEmpty
    competency_id: NonEmpty
    source_item_id: NonEmpty
    verification_scope: NonEmpty
    messages: Annotated[tuple[AuthoredMessage, ...], Field(min_length=1)]
    prompt: NonEmpty
    target_events: Annotated[tuple[AuthoredTargetEvent, ...], Field(min_length=1)]
    final_answer: dict[str, Any] | str
    verifier_result_id: NonEmpty
    verifier_result_digest: Sha256
    rights: SourceRights
    rights_digest: Sha256
    quality_evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    authored_by: NonEmpty
    reviewed_by: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def demonstration_is_governed(self) -> AuthoredDemonstration:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("authored demonstration creation time must be timezone-aware")
        if self.rights_digest != sha256_digest(self.rights.model_dump(mode="json")):
            raise ValueError("authored demonstration rights digest is invalid")
        if len(self.quality_evidence_refs) != len(set(self.quality_evidence_refs)):
            raise ValueError("authored demonstration evidence references must be unique")
        if tuple(sorted(self.quality_evidence_refs)) != self.quality_evidence_refs:
            raise ValueError(
                "authored demonstration evidence references must be canonically ordered"
            )
        if self.verifier_result_id not in self.quality_evidence_refs:
            raise ValueError("authored demonstration must cite its verifier result")
        if not any(message.role == "user" for message in self.messages):
            raise ValueError("authored demonstration requires at least one user message")
        expected_content = (
            self.final_answer
            if isinstance(self.final_answer, str)
            else canonical_json_bytes(self.final_answer).decode("utf-8")
        )
        if self.target_events[-1].content != expected_content:
            raise ValueError(
                "authored demonstration final target event differs from its final answer"
            )
        return self


class TrainingProductKind(StrEnum):
    AUTHORED_SFT = "authored_sft"
    EVIDENCE_LEDGER = "evidence_ledger"
    NORMALIZED_EPISODES = "normalized_episodes"
    SFT = "sft"
    PREFERENCE = "preference"
    PPRL_FORK_PREFERENCE = "pprl_fork_preference"
    PPRL_TRAJECTORY = "pprl_trajectory"
    PPRL_VERIFIABLE = "pprl_verifiable"
    RLVR = "rlvr"
    NEGATIVE_PROCESS = "negative_process"
    CONTINUED_PRETRAINING = "continued_pretraining"
    SEALED_EVALUATION = "sealed_evaluation"
    EXCLUSIONS = "exclusions"


class EvidenceSourceKind(StrEnum):
    AMBER_ADMISSION_DECISION = "amber_admission_decision"
    AMBER_AUTHORIZATION = "amber_authorization"
    AMBER_AUTHORIZATION_EVENT = "amber_authorization_event"
    AUTHORED_DEMONSTRATION = "authored_demonstration"
    CORPUS_ITEM = "corpus_item"
    EPISODE = "episode"
    ATTEMPT = "attempt"
    GRADE = "grade"
    TEACHER_INTERVENTION = "teacher_intervention"
    REVISION = "revision"
    TRANSFER_TRIAL = "transfer_trial"
    VERIFIER_RESULT = "verifier_result"
    REWARD = "reward"
    TRAINING_ELIGIBILITY = "training_eligibility"
    TRAINING_SOURCE_DOCUMENT = "training_source_document"
    TRAINING_SOURCE_DECISION = "training_source_decision"
    CHECKPOINT = "checkpoint"
    PROCESS_DISTRIBUTION = "process_distribution"
    PROCESS_PROGRAM = "process_program"
    PROJECT_INSTANCE = "project_instance"
    PROCESS_EXECUTION = "process_execution"
    PROCESS_ROLLOUT = "process_rollout"
    PROCESS_STATE = "process_state"
    PROCESS_EVENT = "process_event"
    PROCESS_RECOVERY = "process_recovery"
    PROCESS_ABANDONMENT = "process_abandonment"
    PROCESS_FORK = "process_fork"
    PROCESS_OUTCOME = "process_outcome"
    PROCESS_TRAINING_ELIGIBILITY = "process_training_eligibility"


class TrainingExclusionReason(StrEnum):
    BASELINE_OUTPUT = "baseline_output"
    TEACHER_OUTPUT = "teacher_output"
    NON_TARGET_OUTPUT = "non_target_output"
    TEACHER_INFLUENCED = "teacher_influenced"
    SEALED_SOURCE = "sealed_source"
    EVALUATION_SOURCE = "evaluation_source"
    QUARANTINED_SOURCE = "quarantined_source"
    CONTAMINATED_LINEAGE = "contaminated_lineage"
    RIGHTS_REVIEW_REQUIRED = "rights_review_required"
    RIGHTS_USE_NOT_PERMITTED = "rights_use_not_permitted"
    OUTPUT_RIGHTS_MISSING = "output_rights_missing"
    OUTPUT_RIGHTS_REVIEW_REQUIRED = "output_rights_review_required"
    OUTPUT_RIGHTS_USE_NOT_PERMITTED = "output_rights_use_not_permitted"
    MISSING_ELIGIBILITY_DECISION = "missing_eligibility_decision"
    ELIGIBILITY_LANE_EXCLUDED = "eligibility_lane_excluded"
    ELIGIBILITY_CONFLICT = "eligibility_conflict"
    HARD_GATE_FAILED = "hard_gate_failed"
    MISSING_CHECKPOINT_MANIFEST = "missing_checkpoint_manifest"
    CHECKPOINT_IDENTITY_CONFLICT = "checkpoint_identity_conflict"
    MISSING_VALID_GRADE = "missing_valid_grade"
    UNSUCCESSFUL_SFT_TRAJECTORY = "unsuccessful_sft_trajectory"
    NO_STRONGER_PREFERENCE_PAIR = "no_stronger_preference_pair"
    MISSING_RLVR_ENVIRONMENT = "missing_rlvr_environment"
    PRIVATE_REASONING_RESTRICTED = "private_reasoning_restricted"
    EPISODE_NOT_SOURCE_CORPUS = "episode_not_source_corpus"
    SOURCE_DOCUMENT_NOT_ACTIVE = "source_document_not_active"
    SOURCE_DOCUMENT_CONTAMINATED = "source_document_contaminated"
    DUPLICATE_SOURCE_CONTENT = "duplicate_source_content"
    PROCESS_ROLLOUT_NOT_COMPLETE = "process_rollout_not_complete"
    PROCESS_ROLLOUT_ABANDONED = "process_rollout_abandoned"
    PROCESS_OUTCOME_UNRESOLVED = "process_outcome_unresolved"
    PROCESS_ELIGIBILITY_MISSING = "process_eligibility_missing"
    PROCESS_REPLICATION_INSUFFICIENT = "process_replication_insufficient"
    PROCESS_TRAINING_NOT_AUTHORIZED = "process_training_not_authorized"
    PROCESS_FORK_PAIR_MISSING = "process_fork_pair_missing"


class CompilerInvocation(StrictRecord):
    compiler_version: Literal["1.0.0"] = TRAINING_COMPILER_VERSION
    rights_policy_version: Literal["1.0.0"] = RIGHTS_POLICY_VERSION
    as_of: datetime
    internal_only: Literal[True] = True
    eligibility_policy_id: str | None = None
    eligibility_policy_version: str | None = None
    checkpoint_ids: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def selection_is_canonical(self) -> CompilerInvocation:
        if (self.eligibility_policy_id is None) != (self.eligibility_policy_version is None):
            raise ValueError("eligibility policy ID and version must be selected together")
        if len(self.checkpoint_ids) != len(set(self.checkpoint_ids)):
            raise ValueError("checkpoint selection must be unique")
        if tuple(sorted(self.checkpoint_ids)) != self.checkpoint_ids:
            raise ValueError("checkpoint selection must use canonical lexical order")
        if self.as_of.tzinfo is None:
            raise ValueError("compiler snapshot time must be timezone-aware")
        return self


class CompiledRow(StrictRecord):
    schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    row_id: NonEmpty
    compiler_version: Literal["1.0.0"] = TRAINING_COMPILER_VERSION
    source_evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    source_record_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    rights_digests: tuple[Sha256, ...] = ()

    @model_validator(mode="after")
    def lineage_is_canonical(self) -> CompiledRow:
        for name, values in (
            ("source evidence", self.source_evidence_refs),
            ("source record digests", self.source_record_digests),
            ("rights digests", self.rights_digests),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must be unique")
            if tuple(sorted(values)) != values:
                raise ValueError(f"{name} must use canonical lexical order")
        return self


class EvidenceLedgerEntry(CompiledRow):
    source_kind: EvidenceSourceKind
    source_id: NonEmpty
    research_role: ResearchRole | None = None
    record: dict[str, Any]
    artifact_refs: tuple[ArtifactRef, ...] = ()


class NormalizedEpisodeEntry(CompiledRow):
    episode_id: NonEmpty
    item_id: NonEmpty
    research_roles: tuple[ResearchRole, ...]
    corpus_item: dict[str, Any]
    episode: dict[str, Any]
    attempts: tuple[dict[str, Any], ...]
    grades: tuple[dict[str, Any], ...]
    teacher_interventions: tuple[dict[str, Any], ...]
    revisions: tuple[dict[str, Any], ...]
    transfer_trials: tuple[dict[str, Any], ...]
    rewards: tuple[dict[str, Any], ...]
    eligibility_decisions: tuple[dict[str, Any], ...]


class SFTTrainingRow(CompiledRow):
    episode_id: NonEmpty
    attempt_id: NonEmpty
    checkpoint_id: NonEmpty
    messages: tuple[dict[str, Any], ...]
    prompt: NonEmpty
    public_derivation: dict[str, Any] | None
    final_answer: dict[str, Any] | str
    grade: dict[str, Any]
    reward_records: tuple[dict[str, Any], ...]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class AuthoredSFTTrainingRow(CompiledRow):
    demonstration_id: NonEmpty
    domain_id: NonEmpty
    competency_id: NonEmpty
    source_item_id: NonEmpty
    messages: Annotated[tuple[AuthoredMessage, ...], Field(min_length=1)]
    prompt: NonEmpty
    target_events: Annotated[tuple[AuthoredTargetEvent, ...], Field(min_length=1)]
    final_answer: dict[str, Any] | str
    verification: dict[str, Any]
    quality_evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def target_matches_answer(self) -> AuthoredSFTTrainingRow:
        expected_content = (
            self.final_answer
            if isinstance(self.final_answer, str)
            else canonical_json_bytes(self.final_answer).decode("utf-8")
        )
        if self.target_events[-1].content != expected_content:
            raise ValueError("authored SFT target differs from its final answer")
        if len(self.quality_evidence_refs) != len(set(self.quality_evidence_refs)):
            raise ValueError("authored SFT evidence references must be unique")
        if tuple(sorted(self.quality_evidence_refs)) != self.quality_evidence_refs:
            raise ValueError("authored SFT evidence references must be canonically ordered")
        return self


class PreferenceTrainingRow(CompiledRow):
    episode_id: NonEmpty
    checkpoint_id: NonEmpty
    prompt: NonEmpty
    chosen_attempt_id: NonEmpty
    rejected_attempt_id: NonEmpty
    chosen: dict[str, Any] | str
    rejected: dict[str, Any] | str
    chosen_grade: dict[str, Any]
    rejected_grade: dict[str, Any]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class RLVRTrainingRow(CompiledRow):
    episode_id: NonEmpty
    attempt_id: NonEmpty
    checkpoint_id: NonEmpty
    task: dict[str, Any]
    environment_fingerprint: Sha256
    verifier_spec: dict[str, Any]
    observed_output: dict[str, Any] | str | None
    grade: dict[str, Any] | None
    reward_records: tuple[dict[str, Any], ...]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class ProcessTrainingRow(CompiledRow):
    episode_id: NonEmpty
    attempt_id: NonEmpty
    checkpoint_id: NonEmpty
    prompt: NonEmpty
    public_derivation: dict[str, Any] | None
    final_answer: dict[str, Any] | str | None
    grade: dict[str, Any] | None
    outcome_class: NonEmpty
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class PPRLTrajectoryTrainingRow(CompiledRow):
    rollout_id: NonEmpty
    execution_digest: Sha256
    program_digest: Sha256
    distribution_digest: Sha256
    instance_id: NonEmpty
    split: NonEmpty
    replication_index: Annotated[int, Field(ge=0)]
    process_policy: dict[str, Any]
    worker_models: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    initial_state: dict[str, Any]
    events: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    terminal_state: dict[str, Any]
    outcomes: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class PPRLForkPreferenceTrainingRow(CompiledRow):
    fork_id: NonEmpty
    parent_state_id: NonEmpty
    chosen_rollout_id: NonEmpty
    rejected_rollout_id: NonEmpty
    chosen_trajectory_digest: Sha256
    rejected_trajectory_digest: Sha256
    chosen_outcome: dict[str, Any]
    rejected_outcome: dict[str, Any]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=2)]

    @model_validator(mode="after")
    def preference_is_distinct(self) -> PPRLForkPreferenceTrainingRow:
        if self.chosen_rollout_id == self.rejected_rollout_id:
            raise ValueError("PPRL preference needs distinct rollout continuations")
        return self


class PPRLVerifiableTrainingRow(CompiledRow):
    rollout_id: NonEmpty
    execution_digest: Sha256
    program_digest: Sha256
    distribution_digest: Sha256
    instance_id: NonEmpty
    task: dict[str, Any]
    environment_fingerprint: Sha256
    process_policy: dict[str, Any]
    trajectory: Annotated[tuple[dict[str, Any], ...], Field(min_length=1)]
    outcome: dict[str, Any]
    eligibility_decision_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class ContinuedPretrainingRow(CompiledRow):
    document_id: NonEmpty
    source_id: NonEmpty
    source_version: NonEmpty
    language: NonEmpty
    media_type: NonEmpty
    content_ref: ArtifactRef
    content_digest: Sha256


class SealedEvaluationRow(CompiledRow):
    item_id: NonEmpty
    competency_id: NonEmpty
    task_manifest: dict[str, Any]
    verifier_spec: dict[str, Any]
    expected_answer: dict[str, Any] | None


class TrainingExclusionRecord(CompiledRow):
    candidate_id: NonEmpty
    candidate_kind: NonEmpty
    product_kind: TrainingProductKind
    training_lane: TrainingLane | None = None
    reason_codes: Annotated[tuple[TrainingExclusionReason, ...], Field(min_length=1)]
    details: dict[TrainingExclusionReason, NonEmpty]

    @model_validator(mode="after")
    def reasons_are_complete(self) -> TrainingExclusionRecord:
        if len(self.reason_codes) != len(set(self.reason_codes)):
            raise ValueError("exclusion reason codes must be unique")
        if tuple(sorted(self.reason_codes, key=lambda reason: reason.value)) != self.reason_codes:
            raise ValueError("exclusion reasons must use canonical lexical order")
        if set(self.details) != set(self.reason_codes):
            raise ValueError("exclusion details must exactly match reason codes")
        return self


class CheckpointSourceIdentity(StrictRecord):
    checkpoint_id: NonEmpty
    model_id: NonEmpty
    tokenizer_id: str | None = None
    model_digest: Sha256 | None = None
    tokenizer_digest: Sha256 | None = None
    manifest_digest: Sha256 | None = None
    registered: bool


class PolicyIdentity(StrictRecord):
    policy_id: NonEmpty
    policy_version: NonEmpty


class TrainingProductManifest(StrictRecord):
    kind: TrainingProductKind
    training_lane: TrainingLane | None
    row_schema: NonEmpty
    row_schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    artifact: ArtifactRef
    row_count: Annotated[int, Field(ge=0)]
    content_digest: Sha256

    @model_validator(mode="after")
    def artifact_matches_content(self) -> TrainingProductManifest:
        if self.content_digest != self.artifact.digest:
            raise ValueError("training product digest differs from its artifact")
        if not self.artifact.restricted or not self.artifact.raw_data:
            raise ValueError("internal training products must be restricted raw-data artifacts")
        return self


class TrainingBundleManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = TRAINING_ROW_SCHEMA_VERSION
    bundle_id: NonEmpty
    compiler_version: Literal["1.0.0"] = TRAINING_COMPILER_VERSION
    invocation: CompilerInvocation
    source_snapshot_digest: Sha256
    internal_only: Literal[True] = True
    checkpoint_identities: tuple[CheckpointSourceIdentity, ...]
    source_episode_ids: tuple[NonEmpty, ...]
    source_document_ids: tuple[NonEmpty, ...]
    source_demonstration_ids: tuple[NonEmpty, ...]
    source_process_rollout_ids: tuple[NonEmpty, ...] = ()
    source_artifact_digests: tuple[Sha256, ...]
    verifier_fingerprints: tuple[Sha256, ...]
    environment_fingerprints: tuple[Sha256, ...]
    rights_manifest_digests: tuple[Sha256, ...]
    eligibility_policies: tuple[PolicyIdentity, ...]
    products: Annotated[tuple[TrainingProductManifest, ...], Field(min_length=1)]
    included_counts: dict[TrainingProductKind, Annotated[int, Field(ge=0)]]
    exclusion_counts: dict[TrainingExclusionReason, Annotated[int, Field(ge=0)]]
    created_at: datetime

    @model_validator(mode="after")
    def bundle_is_canonical(self) -> TrainingBundleManifest:
        if self.created_at != self.invocation.as_of:
            raise ValueError("bundle creation time must equal its reproducible snapshot time")
        kinds = tuple(product.kind for product in self.products)
        if len(kinds) != len(set(kinds)):
            raise ValueError("training product kinds must be unique")
        if tuple(sorted(kinds, key=lambda kind: kind.value)) != kinds:
            raise ValueError("training products must use canonical lexical order")
        expected_counts = {product.kind: product.row_count for product in self.products}
        if self.included_counts != expected_counts:
            raise ValueError("bundle included counts differ from product manifests")
        for name, values in (
            ("source episodes", self.source_episode_ids),
            ("source documents", self.source_document_ids),
            ("source demonstrations", self.source_demonstration_ids),
            ("source process rollouts", self.source_process_rollout_ids),
            ("source artifacts", self.source_artifact_digests),
            ("verifier fingerprints", self.verifier_fingerprints),
            ("environment fingerprints", self.environment_fingerprints),
            ("rights manifests", self.rights_manifest_digests),
        ):
            if len(values) != len(set(values)) or tuple(sorted(values)) != values:
                raise ValueError(f"{name} must be unique and canonically ordered")
        checkpoint_ids = tuple(item.checkpoint_id for item in self.checkpoint_identities)
        if (
            len(checkpoint_ids) != len(set(checkpoint_ids))
            or tuple(sorted(checkpoint_ids)) != checkpoint_ids
        ):
            raise ValueError("checkpoint identities must be unique and canonically ordered")
        policy_keys = tuple(
            (policy.policy_id, policy.policy_version) for policy in self.eligibility_policies
        )
        if len(policy_keys) != len(set(policy_keys)) or tuple(sorted(policy_keys)) != policy_keys:
            raise ValueError("eligibility policies must be unique and canonically ordered")
        return self


class TrainingBundleVerification(StrictRecord):
    bundle_id: NonEmpty
    manifest_digest: Sha256
    valid: bool
    checked_products: tuple[TrainingProductKind, ...]
    errors: tuple[NonEmpty, ...]
