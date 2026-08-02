from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import Field, FiniteFloat, model_validator

from padawan.domains.contracts import HardGateResult
from padawan.models.contracts import (
    ArtifactRef,
    NonEmpty,
    ResearchRole,
    Sha256,
    StrictRecord,
)

Probability = Annotated[FiniteFloat, Field(gt=0.0, le=1.0)]
NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]


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
