from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.models.contracts import (
    SCHEMA_VERSION,
    ArtifactRef,
    NonEmpty,
    Sha256,
    SourceRights,
    StrictRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    ModelServingIdentity,
    VersionedComponentIdentity,
)

NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]
Probability = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
PositiveProbability = Annotated[FiniteFloat, Field(gt=0.0, le=1.0)]


class PersistenceMode(StrEnum):
    EPISODIC = "episodic"
    CONTINUAL = "continual"


class RewardAuthorityKind(StrEnum):
    VERIFIABLE = "verifiable"
    EMPIRICAL = "empirical"
    ADJUDICATED = "adjudicated"
    HYBRID = "hybrid"


class ProjectSplit(StrEnum):
    TRAIN = "train"
    ADAPTIVE_DEVELOPMENT = "adaptive_development"
    VALIDATION = "validation"
    SEALED = "sealed"


class RolloutStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    PAUSED = "paused"
    REVIEW_REQUIRED = "review_required"
    COMPLETE = "complete"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    CANCELLED = "cancelled"


class ProcessEventKind(StrEnum):
    PROJECT_STARTED = "project_started"
    WORK_PLANNED = "work_planned"
    WORKER_INVOKED = "worker_invoked"
    TOOL_INVOKED = "tool_invoked"
    ARTIFACT_ADMITTED = "artifact_admitted"
    HYPOTHESIS_UPDATED = "hypothesis_updated"
    CLAIM_UPDATED = "claim_updated"
    EVIDENCE_UPDATED = "evidence_updated"
    STATE_CHECKPOINTED = "state_checkpointed"
    ROLLOUT_FORKED = "rollout_forked"
    OUTCOME_ASSESSED = "outcome_assessed"
    STOP_REQUESTED = "stop_requested"
    PROJECT_COMPLETED = "project_completed"
    PROJECT_FAILED = "project_failed"


class HypothesisStatus(StrEnum):
    OPEN = "open"
    SUPPORTED = "supported"
    REJECTED = "rejected"
    RETIRED = "retired"


class OutcomeDisposition(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class UncertaintyAction(StrEnum):
    REJECT_CANDIDATE = "reject_candidate"
    REQUIRE_REVIEW = "require_review"
    USE_UPPER_CONFIDENCE_BOUND = "use_upper_confidence_bound"


class ProcessLearningLane(StrEnum):
    TRAJECTORY = "trajectory"
    FORK_PREFERENCE = "fork_preference"
    VERIFIABLE_REPLAY = "verifiable_replay"


class DistributionPartition(StrictRecord):
    split: ProjectSplit
    sampling_weight: PositiveProbability
    generator_parameters: dict[NonEmpty, Any] = Field(default_factory=dict)
    contamination_scope: NonEmpty

    @model_validator(mode="after")
    def parameters_are_canonical(self) -> DistributionPartition:
        if list(self.generator_parameters) != sorted(self.generator_parameters):
            raise ValueError("distribution generator parameters must use canonical lexical order")
        return self


class ReplicationPolicy(StrictRecord):
    """Minimum evidence shape for a learning or comparative claim."""

    minimum_unique_instances: Annotated[int, Field(ge=2)]
    minimum_rollouts_per_instance: Annotated[int, Field(ge=2)]
    maximum_rollouts_per_instance: Annotated[int, Field(ge=2)]
    paired_checkpoint_forks: bool = True

    @model_validator(mode="after")
    def bounds_are_ordered(self) -> ReplicationPolicy:
        if self.maximum_rollouts_per_instance < self.minimum_rollouts_per_instance:
            raise ValueError("maximum rollouts cannot be below the replication minimum")
        return self


class ProcessDistributionManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    distribution_id: NonEmpty
    version: NonEmpty
    title: NonEmpty
    population: NonEmpty
    generator: VersionedComponentIdentity
    rights: SourceRights
    partitions: Annotated[tuple[DistributionPartition, ...], Field(min_length=4)]
    replication: ReplicationPolicy
    difficulty_strata: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    seed_namespace: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def distribution_is_complete(self) -> ProcessDistributionManifest:
        _require_timezone(self.created_at, "distribution creation time")
        splits = tuple(partition.split for partition in self.partitions)
        if len(splits) != len(set(splits)):
            raise ValueError("distribution partitions must have unique splits")
        if set(splits) != set(ProjectSplit):
            raise ValueError("distribution must declare train, adaptive, validation, and sealed")
        if tuple(sorted(splits, key=lambda split: split.value)) != splits:
            raise ValueError("distribution partitions must use canonical lexical order")
        if abs(sum(float(item.sampling_weight) for item in self.partitions) - 1.0) > 1e-9:
            raise ValueError("distribution partition weights must sum to one")
        _require_canonical_unique(self.difficulty_strata, "difficulty strata")
        return self


class WorkerRoleSpec(StrictRecord):
    role_id: NonEmpty
    description: NonEmpty
    required_capabilities: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    allowed_tool_ids: tuple[NonEmpty, ...] = ()
    minimum_instances: Annotated[int, Field(ge=0)] = 0
    maximum_instances: Annotated[int, Field(ge=1)] = 1
    replaceable: bool = True

    @model_validator(mode="after")
    def role_is_canonical(self) -> WorkerRoleSpec:
        if self.minimum_instances > self.maximum_instances:
            raise ValueError("worker role minimum exceeds its maximum")
        _require_canonical_unique(self.required_capabilities, "worker capabilities")
        _require_canonical_unique(self.allowed_tool_ids, "worker tools")
        return self


class RewardAuthoritySpec(StrictRecord):
    authority_id: NonEmpty
    version: NonEmpty
    kind: RewardAuthorityKind
    description: NonEmpty
    component_authorities: tuple[VersionedComponentIdentity, ...] = ()
    blinded: bool = False
    replication_required: bool = False

    @model_validator(mode="after")
    def authority_matches_kind(self) -> RewardAuthoritySpec:
        identities = tuple(
            (component.component_id, component.version) for component in self.component_authorities
        )
        if len(identities) != len(set(identities)):
            raise ValueError("reward component authorities must be unique")
        if tuple(sorted(identities)) != identities:
            raise ValueError("reward component authorities must use canonical lexical order")
        if self.kind == RewardAuthorityKind.HYBRID and len(self.component_authorities) < 2:
            raise ValueError("hybrid reward authority requires at least two components")
        if self.kind != RewardAuthorityKind.HYBRID and self.component_authorities:
            raise ValueError("only hybrid reward authority declares component authorities")
        if self.kind == RewardAuthorityKind.ADJUDICATED and not self.blinded:
            raise ValueError("adjudicated process outcomes must use blinded assessment")
        if self.kind == RewardAuthorityKind.EMPIRICAL and not self.replication_required:
            raise ValueError("empirical process outcomes must require replication")
        return self


class RegretBudgetContract(StrictRecord):
    """Bounded local slack used to select among collectively useful actions."""

    contract_id: NonEmpty
    version: NonEmpty
    epsilon: NonNegativeFinite
    local_utility_estimator: VersionedComponentIdentity
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)]
    uncertainty_action: UncertaintyAction
    minimum_baseline_samples: Annotated[int, Field(ge=1)]
    selection_rule: Literal["lexicographic_feasible_set"] = "lexicographic_feasible_set"


class ProcessProgram(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    program_id: NonEmpty
    version: NonEmpty
    title: NonEmpty
    objective_family: NonEmpty
    persistence_mode: PersistenceMode
    distribution_digest: Sha256
    reward_authority: RewardAuthoritySpec
    regret_budget: RegretBudgetContract | None = None
    worker_roles: Annotated[tuple[WorkerRoleSpec, ...], Field(min_length=1)]
    tools: tuple[VersionedComponentIdentity, ...]
    maximum_concurrent_workers: Annotated[int, Field(ge=1)]
    created_at: datetime

    @model_validator(mode="after")
    def program_is_canonical(self) -> ProcessProgram:
        _require_timezone(self.created_at, "program creation time")
        role_ids = tuple(role.role_id for role in self.worker_roles)
        _require_canonical_unique(role_ids, "worker role IDs")
        tool_ids = tuple(tool.component_id for tool in self.tools)
        _require_canonical_unique(tool_ids, "program tool IDs")
        tool_identities = tuple(
            (tool.component_id, tool.version, tool.digest) for tool in self.tools
        )
        if tuple(sorted(tool_identities)) != tool_identities:
            raise ValueError("program tool identities must use canonical lexical order")
        if sum(role.maximum_instances for role in self.worker_roles) < 1:
            raise ValueError("program must permit at least one worker")
        if self.maximum_concurrent_workers > sum(
            role.maximum_instances for role in self.worker_roles
        ):
            raise ValueError("worker concurrency exceeds the declared role capacity")
        role_tools = {tool for role in self.worker_roles for tool in role.allowed_tool_ids}
        if not role_tools.issubset(set(tool_ids)):
            raise ValueError("worker role refers to a tool outside the program tool set")
        return self


class ProcessExecutionManifest(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    execution_id: NonEmpty
    program_digest: Sha256
    distribution_digest: Sha256
    instance_digest: Sha256
    amber_authorization_digest: Sha256
    process_policy: VersionedComponentIdentity
    worker_models: Annotated[tuple[ModelServingIdentity, ...], Field(min_length=1)]
    output_rights: SourceRights
    environment: VersionedComponentIdentity
    environment_parameters: dict[NonEmpty, NonEmpty]
    environment_fingerprint: Sha256
    seed: int
    created_at: datetime

    @model_validator(mode="after")
    def execution_is_bound(self) -> ProcessExecutionManifest:
        _require_timezone(self.created_at, "process execution creation time")
        if self.environment.digest != self.environment_fingerprint:
            raise ValueError("process environment identity must carry its fingerprint")
        if list(self.environment_parameters) != sorted(self.environment_parameters):
            raise ValueError("process environment parameters must use canonical lexical order")
        if sha256_digest(self.environment_parameters) != self.environment_fingerprint:
            raise ValueError("process environment fingerprint disagrees with its parameters")
        identities = tuple(
            (model.purpose, model.research_role.value, model.model_id)
            for model in self.worker_models
        )
        if len(identities) != len(set(identities)):
            raise ValueError("process worker model identities must be unique")
        if tuple(sorted(identities)) != identities:
            raise ValueError("process worker model identities must use canonical lexical order")
        return self


class ProjectInstance(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    instance_id: NonEmpty
    distribution_digest: Sha256
    split: ProjectSplit
    seed: int
    difficulty: Probability
    difficulty_stratum: NonEmpty
    task_digest: Sha256
    environment_fingerprint: Sha256
    task: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @model_validator(mode="after")
    def instance_is_canonical(self) -> ProjectInstance:
        _require_timezone(self.created_at, "project instance creation time")
        if list(self.metadata) != sorted(self.metadata):
            raise ValueError("project instance metadata must use canonical lexical order")
        if sha256_digest(self.task) != self.task_digest:
            raise ValueError("project task digest disagrees with the task payload")
        return self


class ProjectHypothesis(StrictRecord):
    hypothesis_id: NonEmpty
    statement: NonEmpty
    status: HypothesisStatus
    evidence_refs: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> ProjectHypothesis:
        _require_canonical_unique(self.evidence_refs, "hypothesis evidence")
        return self


class ProjectClaim(StrictRecord):
    claim_id: NonEmpty
    statement: NonEmpty
    confidence: Probability
    evidence_refs: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> ProjectClaim:
        _require_canonical_unique(self.evidence_refs, "claim evidence")
        return self


class ProjectDependency(StrictRecord):
    source_id: NonEmpty
    target_id: NonEmpty
    relation: NonEmpty

    @model_validator(mode="after")
    def no_self_dependency(self) -> ProjectDependency:
        if self.source_id == self.target_id:
            raise ValueError("project dependency cannot be a self-edge")
        return self


class ProcessWorkerOutput(StrictRecord):
    """Normalized public model output with bounded, explicit usage fields."""

    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    output_text: str
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]


class WorkerAssignment(StrictRecord):
    assignment_id: NonEmpty
    role_id: NonEmpty
    worker_identity: NonEmpty
    objective: NonEmpty
    status: NonEmpty


class ProjectBudgetUsage(StrictRecord):
    actions: Annotated[int, Field(ge=0)] = 0
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    artifact_bytes: Annotated[int, Field(ge=0)] = 0
    wall_time_seconds: NonNegativeFinite = 0.0
    cost: NonNegativeFinite = 0.0


class ProjectStatePayload(StrictRecord):
    objective: NonEmpty
    plan: tuple[NonEmpty, ...] = ()
    hypotheses: tuple[ProjectHypothesis, ...] = ()
    claims: tuple[ProjectClaim, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()
    dependencies: tuple[ProjectDependency, ...] = ()
    budget_usage: ProjectBudgetUsage = Field(default_factory=ProjectBudgetUsage)
    worker_assignments: tuple[WorkerAssignment, ...] = ()
    unresolved_risks: tuple[NonEmpty, ...] = ()
    memory_refs: tuple[NonEmpty, ...] = ()
    extension_state: dict[NonEmpty, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def state_is_canonical(self) -> ProjectStatePayload:
        _require_canonical_unique(
            tuple(item.hypothesis_id for item in self.hypotheses), "hypothesis IDs"
        )
        _require_canonical_unique(tuple(item.claim_id for item in self.claims), "claim IDs")
        _require_canonical_unique(
            tuple(item.artifact_id for item in self.artifact_refs), "state artifacts"
        )
        dependency_keys = tuple(
            (item.source_id, item.target_id, item.relation) for item in self.dependencies
        )
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError("project dependencies must be unique")
        if tuple(sorted(dependency_keys)) != dependency_keys:
            raise ValueError("project dependencies must use canonical lexical order")
        _require_canonical_unique(
            tuple(item.assignment_id for item in self.worker_assignments), "worker assignments"
        )
        _require_canonical_unique(self.unresolved_risks, "unresolved risks")
        _require_canonical_unique(self.memory_refs, "process memory references")
        if list(self.extension_state) != sorted(self.extension_state):
            raise ValueError("process extension state must use canonical lexical order")
        return self


class ProjectStateVersion(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    state_id: NonEmpty
    rollout_id: NonEmpty
    sequence: Annotated[int, Field(ge=0)]
    parent_state_id: NonEmpty | None
    triggering_event_id: NonEmpty | None
    payload: ProjectStatePayload
    state_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def digest_is_valid(self) -> ProjectStateVersion:
        _require_timezone(self.created_at, "project state creation time")
        expected = project_state_digest(
            state_id=self.state_id,
            rollout_id=self.rollout_id,
            sequence=self.sequence,
            parent_state_id=self.parent_state_id,
            triggering_event_id=self.triggering_event_id,
            payload=self.payload,
            created_at=self.created_at,
        )
        if self.state_digest != expected:
            raise ValueError("project state digest is invalid")
        if self.sequence == 0 and (self.parent_state_id or self.triggering_event_id):
            raise ValueError("initial project state cannot have a parent or triggering event")
        if self.sequence > 0 and (not self.parent_state_id or not self.triggering_event_id):
            raise ValueError("non-initial project state needs a parent and triggering event")
        return self


class ProcessEventRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    event_id: NonEmpty
    rollout_id: NonEmpty
    sequence: Annotated[int, Field(ge=1)]
    kind: ProcessEventKind
    actor_id: NonEmpty
    lease_token_digest: Sha256
    parent_state_id: NonEmpty
    resulting_state_id: NonEmpty
    worker_invocation_id: str | None = None
    research_execution_digest: Sha256 | None = None
    amber_authorization_digest: Sha256
    amber_decision_id: NonEmpty
    rollout_status: RolloutStatus
    payload: dict[str, Any]
    artifact_refs: tuple[ArtifactRef, ...] = ()
    event_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def digest_is_valid(self) -> ProcessEventRecord:
        _require_timezone(self.created_at, "process event creation time")
        _require_canonical_unique(
            tuple(item.artifact_id for item in self.artifact_refs), "event artifacts"
        )
        expected = process_event_digest(
            event_id=self.event_id,
            rollout_id=self.rollout_id,
            sequence=self.sequence,
            kind=self.kind,
            actor_id=self.actor_id,
            lease_token_digest=self.lease_token_digest,
            parent_state_id=self.parent_state_id,
            resulting_state_id=self.resulting_state_id,
            worker_invocation_id=self.worker_invocation_id,
            research_execution_digest=self.research_execution_digest,
            amber_authorization_digest=self.amber_authorization_digest,
            amber_decision_id=self.amber_decision_id,
            rollout_status=self.rollout_status,
            payload=self.payload,
            artifact_refs=self.artifact_refs,
            created_at=self.created_at,
        )
        if self.event_digest != expected:
            raise ValueError("process event digest is invalid")
        return self


class ProcessRolloutRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    rollout_id: NonEmpty
    execution_digest: Sha256
    program_digest: Sha256
    distribution_digest: Sha256
    instance_id: NonEmpty
    split: ProjectSplit
    replication_index: Annotated[int, Field(ge=0)]
    seed: int
    status: RolloutStatus
    initial_state_id: NonEmpty
    current_state_id: NonEmpty
    sequence: Annotated[int, Field(ge=0)]
    parent_rollout_id: str | None = None
    fork_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def rollout_times_are_valid(self) -> ProcessRolloutRecord:
        _require_timezone(self.created_at, "rollout creation time")
        _require_timezone(self.updated_at, "rollout update time")
        if self.updated_at < self.created_at:
            raise ValueError("rollout update time precedes creation")
        if (self.parent_rollout_id is None) != (self.fork_id is None):
            raise ValueError("forked rollout needs both parent rollout and fork identity")
        return self


class ProcessForkChild(StrictRecord):
    rollout_id: NonEmpty
    condition_id: NonEmpty
    seed: int


class ProcessForkRecord(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    fork_id: NonEmpty
    parent_rollout_id: NonEmpty
    parent_state_id: NonEmpty
    intervention: dict[NonEmpty, Any]
    children: Annotated[tuple[ProcessForkChild, ...], Field(min_length=2)]
    created_at: datetime

    @model_validator(mode="after")
    def fork_is_symmetric(self) -> ProcessForkRecord:
        _require_timezone(self.created_at, "process fork creation time")
        if list(self.intervention) != sorted(self.intervention):
            raise ValueError("process fork intervention must use canonical lexical order")
        rollout_ids = tuple(child.rollout_id for child in self.children)
        conditions = tuple(child.condition_id for child in self.children)
        if len(rollout_ids) != len(set(rollout_ids)):
            raise ValueError("fork child rollouts must be unique")
        if len(conditions) != len(set(conditions)):
            raise ValueError("fork child conditions must be unique")
        if tuple(sorted(conditions)) != conditions:
            raise ValueError("fork children must use canonical condition order")
        return self


class ProcessOutcomeComponent(StrictRecord):
    component_id: NonEmpty
    disposition: OutcomeDisposition
    deterministic: bool
    value: FiniteFloat | None = None
    uncertainty: NonNegativeFinite | None = None
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def component_is_supported(self) -> ProcessOutcomeComponent:
        _require_canonical_unique(self.evidence_refs, "outcome evidence")
        if (
            self.disposition
            in {
                OutcomeDisposition.UNKNOWN,
                OutcomeDisposition.INFRASTRUCTURE_FAILURE,
            }
            and self.value is not None
        ):
            raise ValueError("unknown or infrastructure outcome cannot carry a value")
        if self.deterministic and self.uncertainty is not None:
            raise ValueError("deterministic outcome cannot claim statistical uncertainty")
        return self


class ProcessOutcomeAssessment(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    assessment_id: NonEmpty
    rollout_id: NonEmpty
    authority: RewardAuthoritySpec
    components: Annotated[tuple[ProcessOutcomeComponent, ...], Field(min_length=1)]
    scalar_return: FiniteFloat | None = None
    preference_rank: Annotated[int, Field(ge=1)] | None = None
    eligible_for_learning: bool
    created_at: datetime

    @model_validator(mode="after")
    def assessment_matches_authority(self) -> ProcessOutcomeAssessment:
        _require_timezone(self.created_at, "outcome assessment time")
        component_ids = tuple(component.component_id for component in self.components)
        _require_canonical_unique(component_ids, "outcome component IDs")
        if self.authority.kind == RewardAuthorityKind.VERIFIABLE and not all(
            component.deterministic for component in self.components
        ):
            raise ValueError("verifiable outcome authority requires deterministic components")
        unresolved = any(
            component.disposition
            in {OutcomeDisposition.UNKNOWN, OutcomeDisposition.INFRASTRUCTURE_FAILURE}
            for component in self.components
        )
        if unresolved and self.eligible_for_learning:
            raise ValueError("unresolved outcomes cannot be eligible for learning")
        if (
            self.eligible_for_learning
            and self.scalar_return is None
            and self.preference_rank is None
        ):
            raise ValueError("learning-eligible outcome needs return or preference evidence")
        return self


class ProcessTrainingEligibilityDecision(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    decision_id: NonEmpty
    rollout_id: NonEmpty
    outcome_assessment_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    policy_id: NonEmpty
    policy_version: NonEmpty
    eligible: bool
    allowed_lanes: tuple[ProcessLearningLane, ...]
    rights_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    reason: NonEmpty
    decided_by: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def eligibility_is_canonical(self) -> ProcessTrainingEligibilityDecision:
        _require_timezone(self.created_at, "process training eligibility time")
        _require_canonical_unique(self.outcome_assessment_ids, "process eligibility outcomes")
        lane_values = tuple(lane.value for lane in self.allowed_lanes)
        _require_canonical_unique(lane_values, "process learning lanes")
        _require_canonical_unique(self.rights_digests, "process eligibility rights")
        _require_canonical_unique(self.evidence_refs, "process eligibility evidence")
        if self.eligible != bool(self.allowed_lanes):
            raise ValueError("process eligibility and allowed lanes disagree")
        return self


def project_state_digest(
    *,
    state_id: str,
    rollout_id: str,
    sequence: int,
    parent_state_id: str | None,
    triggering_event_id: str | None,
    payload: ProjectStatePayload,
    created_at: datetime,
) -> str:
    return sha256_digest(
        {
            "state_id": state_id,
            "rollout_id": rollout_id,
            "sequence": sequence,
            "parent_state_id": parent_state_id,
            "triggering_event_id": triggering_event_id,
            "payload": payload,
            "created_at": created_at,
        }
    )


def process_event_digest(
    *,
    event_id: str,
    rollout_id: str,
    sequence: int,
    kind: ProcessEventKind,
    actor_id: str,
    lease_token_digest: str,
    parent_state_id: str,
    resulting_state_id: str,
    worker_invocation_id: str | None,
    research_execution_digest: str | None,
    amber_authorization_digest: str,
    amber_decision_id: str,
    rollout_status: RolloutStatus,
    payload: dict[str, Any],
    artifact_refs: tuple[ArtifactRef, ...],
    created_at: datetime,
) -> str:
    return sha256_digest(
        {
            "event_id": event_id,
            "rollout_id": rollout_id,
            "sequence": sequence,
            "kind": kind,
            "actor_id": actor_id,
            "lease_token_digest": lease_token_digest,
            "parent_state_id": parent_state_id,
            "resulting_state_id": resulting_state_id,
            "worker_invocation_id": worker_invocation_id,
            "research_execution_digest": research_execution_digest,
            "amber_authorization_digest": amber_authorization_digest,
            "amber_decision_id": amber_decision_id,
            "rollout_status": rollout_status,
            "payload": payload,
            "artifact_refs": artifact_refs,
            "created_at": created_at,
        }
    )


def _require_canonical_unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    if tuple(sorted(values)) != values:
        raise ValueError(f"{label} must use canonical lexical order")


def _require_timezone(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
