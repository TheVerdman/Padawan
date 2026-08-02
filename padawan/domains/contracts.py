from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any

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


class RewardComponent(StrictRecord):
    component_id: NonEmpty
    value: FiniteFloat | None
    weight: FiniteFloat
    evidence_refs: tuple[NonEmpty, ...]
    missing_reason: str | None = None

    @model_validator(mode="after")
    def missing_value_has_reason(self) -> RewardComponent:
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing reward values require exactly one missing reason")
        return self


class RewardRecord(StrictRecord):
    reward_id: NonEmpty
    policy_id: NonEmpty
    policy_version: NonEmpty
    hard_gates: tuple[HardGateResult, ...]
    components: tuple[RewardComponent, ...]
    derived_utility: FiniteFloat | None
    created_at: datetime

    @model_validator(mode="after")
    def hard_failures_have_no_scalar_utility(self) -> RewardRecord:
        if any(not gate.passed for gate in self.hard_gates) and self.derived_utility is not None:
            raise ValueError("hard-gate failures cannot receive scalar utility")
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
        overlap = set(self.allowed_lanes).intersection(self.excluded_lanes)
        if overlap:
            raise ValueError(f"training lanes cannot be allowed and excluded: {sorted(overlap)}")
        return self
