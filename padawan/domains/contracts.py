from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.models.contracts import (
    ArtifactRef,
    NonEmpty,
    Sha256,
    StrictRecord,
    TeacherMode,
)


class DomainSpec(StrictRecord):
    domain_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]+$")]
    version: NonEmpty
    title: NonEmpty
    description: NonEmpty
    evidence_hierarchy: tuple[NonEmpty, ...]
    deterministic_verifiers: tuple[NonEmpty, ...]
    permissible_teacher_modes: tuple[TeacherMode, ...]
    supports_tools: bool


class EnvironmentSnapshot(StrictRecord):
    environment_id: NonEmpty
    domain_id: NonEmpty
    domain_version: NonEmpty
    fingerprint: Sha256
    dependencies: dict[str, str]
    tool_surface: tuple[dict[str, Any], ...] = ()
    network_enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    created_at: datetime


class TaskManifest(StrictRecord):
    task_id: NonEmpty
    domain_id: NonEmpty
    domain_version: NonEmpty
    competency_id: NonEmpty
    split: NonEmpty
    source: NonEmpty
    license: NonEmpty
    environment_fingerprint: Sha256
    corpus_lineage: dict[str, str]
    freshness_scope: NonEmpty
    metadata: dict[str, Any] = Field(default_factory=dict)


class VerifierDisposition(StrEnum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNKNOWN = "unknown"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class VerifierResult(StrictRecord):
    result_id: NonEmpty
    verifier_id: NonEmpty
    verifier_version: NonEmpty
    scope: NonEmpty
    disposition: VerifierDisposition
    deterministic: bool
    summary: NonEmpty
    evidence: dict[str, Any]
    artifact_refs: tuple[ArtifactRef, ...] = ()
    created_at: datetime


class HardGateResult(StrictRecord):
    gate_id: NonEmpty
    passed: bool
    disposition: VerifierDisposition
    evidence_refs: tuple[NonEmpty, ...]
    reason: NonEmpty

    @model_validator(mode="after")
    def only_verified_gates_pass(self) -> HardGateResult:
        if self.passed != (self.disposition == VerifierDisposition.VERIFIED):
            raise ValueError("a hard gate passes if and only if its disposition is verified")
        return self


class RewardMissingAction(StrEnum):
    INVALIDATE_UTILITY = "invalidate_utility"
    OMIT = "omit"


class RewardNormalization(StrictRecord):
    offset: FiniteFloat = 0.0
    scale: FiniteFloat = 1.0
    clamp_min: FiniteFloat | None = None
    clamp_max: FiniteFloat | None = None

    @model_validator(mode="after")
    def valid_transform(self) -> RewardNormalization:
        if self.scale == 0:
            raise ValueError("reward normalization scale cannot be zero")
        if (
            self.clamp_min is not None
            and self.clamp_max is not None
            and self.clamp_min > self.clamp_max
        ):
            raise ValueError("reward normalization minimum exceeds maximum")
        return self

    def apply(self, value: float) -> float:
        normalized = (value - self.offset) / self.scale
        if self.clamp_min is not None:
            normalized = max(normalized, self.clamp_min)
        if self.clamp_max is not None:
            normalized = min(normalized, self.clamp_max)
        return normalized


class RewardComponentPolicy(StrictRecord):
    component_id: NonEmpty
    weight: FiniteFloat
    normalization: RewardNormalization = Field(default_factory=RewardNormalization)
    missing_action: RewardMissingAction = RewardMissingAction.INVALIDATE_UTILITY


class RewardPolicy(StrictRecord):
    policy_id: NonEmpty
    version: NonEmpty
    description: NonEmpty
    aggregation: Literal["weighted_sum"] = "weighted_sum"
    components: Annotated[tuple[RewardComponentPolicy, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def component_ids_are_unique(self) -> RewardPolicy:
        identifiers = [component.component_id for component in self.components]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("reward policy component IDs must be unique")
        return self


class RewardObservation(StrictRecord):
    component_id: NonEmpty
    value: FiniteFloat | None
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_value_has_reason(self) -> RewardObservation:
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing reward observations require exactly one missing reason")
        return self


class RewardComponent(StrictRecord):
    component_id: NonEmpty
    value: FiniteFloat | None
    normalized_value: FiniteFloat | None
    weight: FiniteFloat
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_value_has_reason(self) -> RewardComponent:
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing reward values require exactly one missing reason")
        if (self.value is None) != (self.normalized_value is None):
            raise ValueError("raw and normalized reward values must be missing together")
        return self


class RewardRecord(StrictRecord):
    reward_id: NonEmpty
    policy_id: NonEmpty
    policy_version: NonEmpty
    policy_digest: Sha256
    input_digest: Sha256
    hard_gates: Annotated[tuple[HardGateResult, ...], Field(min_length=1)]
    components: Annotated[tuple[RewardComponent, ...], Field(min_length=1)]
    derived_utility: FiniteFloat | None
    created_at: datetime

    @model_validator(mode="after")
    def hard_failures_have_no_scalar_utility(self) -> RewardRecord:
        if any(not gate.passed for gate in self.hard_gates) and self.derived_utility is not None:
            raise ValueError("hard-gate failures cannot receive scalar utility")
        gate_ids = [gate.gate_id for gate in self.hard_gates]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("reward hard-gate IDs must be unique")
        component_ids = [component.component_id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("reward component IDs must be unique")
        return self

    @property
    def eligible(self) -> bool:
        return bool(self.hard_gates) and all(gate.passed for gate in self.hard_gates)


class TrainingLane(StrEnum):
    CONTINUED_PRETRAINING = "continued_pretraining"
    SFT = "sft"
    PREFERENCE = "preference"
    RLVR = "rlvr"
    PROCESS = "process"
    EVALUATION_ONLY = "evaluation_only"


class TrainingEligibilityDecision(StrictRecord):
    decision_id: NonEmpty
    policy_id: NonEmpty
    policy_version: NonEmpty
    allowed_lanes: tuple[TrainingLane, ...]
    excluded_lanes: dict[TrainingLane, NonEmpty]
    evidence_refs: tuple[NonEmpty, ...]
    created_at: datetime

    @model_validator(mode="after")
    def lanes_are_disjoint(self) -> TrainingEligibilityDecision:
        if len(self.allowed_lanes) != len(set(self.allowed_lanes)):
            raise ValueError("allowed training lanes must be unique")
        overlap = set(self.allowed_lanes).intersection(self.excluded_lanes)
        if overlap:
            raise ValueError(f"training lanes cannot be allowed and excluded: {sorted(overlap)}")
        return self
