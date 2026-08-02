from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.domains.contracts import (
    HardGateResult,
    RewardObservation,
    VerifierResult,
)
from padawan.models.contracts import NonEmpty, ResearchRole, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class MagellanScenarioFamily(StrEnum):
    INTAKE_AND_PLAN = "intake_and_plan"
    NEGOTIATED_RATE = "negotiated_rate"
    OUTREACH_WAIT_RESUME = "outreach_wait_resume"
    REGULATED_APPROVAL = "regulated_approval"
    TENANT_ISOLATION = "tenant_isolation"
    UNKNOWN_TOOL = "unknown_tool"
    INVALID_DEPENDENCY = "invalid_dependency"
    IDEMPOTENT_REPLAY = "idempotent_replay"


class MagellanToolTier(IntEnum):
    READ = 1
    PROPOSE = 2
    COMMIT_RECOVERABLE = 3
    COMMIT_REGULATED = 4


class MagellanWorldIsolation(StrEnum):
    NONE = "none"
    DATABASE_CLONE = "database_clone"
    CONTAINER_SNAPSHOT = "container_snapshot"


class MagellanSourceIsolation(StrEnum):
    LIVE_WORKTREE = "live_worktree"
    CONTENT_ADDRESSED_COPY = "content_addressed_copy"
    CONTAINER_IMAGE = "container_image"


class MagellanRuntimeSecretPolicy(StrEnum):
    ENV_ALLOWLIST_ONLY = "env_allowlist_only"
    MOUNTED_SECRET_FILES = "mounted_secret_files"
    UNRESTRICTED = "unrestricted"


class MagellanNetworkPolicy(StrEnum):
    DENIED = "denied"
    ALLOWLISTED = "allowlisted"
    UNRESTRICTED = "unrestricted"


class MagellanExternalEffects(StrEnum):
    BLOCKED = "blocked"
    RECORDED_MOCKS = "recorded_mocks"
    LIVE = "live"


class MagellanAgentProtocol(StrEnum):
    RESPONSES = "responses"
    CHAT_COMPLETIONS = "chat_completions"


class MagellanTenantIsolation(StrEnum):
    ENFORCED = "enforced"
    DECLARED_ONLY = "declared_only"
    ABSENT = "absent"


class MagellanIdempotencyMode(StrEnum):
    DURABLE = "durable"
    PROCESS_LOCAL = "process_local"
    ABSENT = "absent"


class MagellanPredicateOperator(StrEnum):
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    EXISTS = "exists"
    ABSENT = "absent"
    UNCHANGED = "unchanged"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    CONTAINS = "contains"


class MagellanTraceStatus(StrEnum):
    COMPLETE = "complete"
    WAITING = "waiting"
    PENDING_APPROVAL = "pending_approval"
    REFUSED = "refused"
    FAILED = "failed"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class MagellanToolStatus(StrEnum):
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    WAITING = "waiting"
    PENDING_APPROVAL = "pending_approval"
    FAILED = "failed"


class MagellanApprovalStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MagellanPlanStatus(StrEnum):
    VALIDATED = "validated"
    REJECTED = "rejected"


class MagellanFailureClass(StrEnum):
    ENVIRONMENT_STARTUP = "environment_startup"
    ENVIRONMENT_RESET = "environment_reset"
    ENVIRONMENT_DRIFT = "environment_drift"
    AGENT_PROTOCOL = "agent_protocol"
    AGENT_TIMEOUT = "agent_timeout"
    TOOL_EXECUTION = "tool_execution"
    OBSERVATION_CAPTURE = "observation_capture"


class MagellanSourceFile(StrictRecord):
    path: NonEmpty
    size_bytes: Annotated[int, Field(ge=0)]
    digest: Sha256

    @model_validator(mode="after")
    def path_is_safe(self) -> MagellanSourceFile:
        if self.path.startswith("/") or any(
            part in {"", ".", ".."} for part in self.path.split("/")
        ):
            raise ValueError("repository source-file path must be safe and relative")
        return self


class MagellanRepositorySnapshot(StrictRecord):
    snapshot_id: NonEmpty
    commit_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    branch: str | None
    dirty: bool
    tracked_changed_paths: tuple[NonEmpty, ...]
    included_untracked_files: tuple[MagellanSourceFile, ...]
    excluded_volatile_paths: tuple[NonEmpty, ...]
    excluded_sensitive_paths: tuple[NonEmpty, ...]
    tracked_diff_digest: Sha256
    untracked_manifest_digest: Sha256
    dependency_digest: Sha256
    migration_digest: Sha256
    source_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def paths_are_relative_and_unique(self) -> MagellanRepositorySnapshot:
        included_untracked_paths = self.included_untracked_paths
        all_paths = (
            *self.tracked_changed_paths,
            *included_untracked_paths,
            *self.excluded_volatile_paths,
            *self.excluded_sensitive_paths,
        )
        if any(
            path.startswith("/") or any(part in {"", ".", ".."} for part in path.split("/"))
            for path in all_paths
        ):
            raise ValueError("repository snapshot paths must be safe and relative")
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("repository snapshot path classes must be disjoint")
        path_classes = (
            self.tracked_changed_paths,
            included_untracked_paths,
            self.excluded_volatile_paths,
            self.excluded_sensitive_paths,
        )
        if any(tuple(sorted(paths)) != paths for paths in path_classes):
            raise ValueError("repository snapshot path classes must be sorted")
        observed_dirty = bool(
            self.tracked_changed_paths
            or self.included_untracked_files
            or self.excluded_volatile_paths
            or self.excluded_sensitive_paths
        )
        if self.dirty != observed_dirty:
            raise ValueError("repository dirty flag differs from recorded worktree state")
        expected_untracked_digest = sha256_digest(
            [entry.model_dump(mode="json") for entry in self.included_untracked_files]
        )
        if self.untracked_manifest_digest != expected_untracked_digest:
            raise ValueError("untracked source manifest digest is not reproducible")
        expected_source_digest = sha256_digest(
            {
                "commit_sha": self.commit_sha,
                "tracked_diff_digest": self.tracked_diff_digest,
                "untracked_manifest_digest": self.untracked_manifest_digest,
                "excluded_volatile_paths": self.excluded_volatile_paths,
                "excluded_sensitive_paths": self.excluded_sensitive_paths,
                "dependency_digest": self.dependency_digest,
                "migration_digest": self.migration_digest,
            }
        )
        if self.source_digest != expected_source_digest:
            raise ValueError("repository source digest is not reproducible from its manifest")
        if self.snapshot_id != f"magellan-source-{self.source_digest[7:31]}":
            raise ValueError("repository snapshot ID differs from its source digest")
        return self

    @property
    def included_untracked_paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.included_untracked_files)


class MagellanToolCapability(StrictRecord):
    tool_name: NonEmpty
    tier: MagellanToolTier
    validators: tuple[NonEmpty, ...] = ()
    input_schema: Annotated[dict[str, Any], Field(min_length=1)]
    output_schema: Annotated[dict[str, Any], Field(min_length=1)]
    externally_effectful: bool

    @model_validator(mode="after")
    def validators_are_unique(self) -> MagellanToolCapability:
        if len(self.validators) != len(set(self.validators)):
            raise ValueError("Magellan tool validators must be unique")
        return self


class MagellanEnvironmentHandshake(StrictRecord):
    protocol_version: Literal["1.0.0"] = "1.0.0"
    environment_id: NonEmpty
    repository_source_digest: Sha256
    driver_digest: Sha256
    database_backend: NonEmpty
    database_schema_digest: Sha256
    authorization_policy_digest: Sha256
    world_schema_version: NonEmpty
    source_isolation: MagellanSourceIsolation
    runtime_secret_policy: MagellanRuntimeSecretPolicy
    allowed_secret_env_vars: tuple[Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")], ...] = ()
    world_isolation: MagellanWorldIsolation
    reset_protocol_version: NonEmpty
    reset_verified: bool
    matched_worlds_independent: bool
    network_policy: MagellanNetworkPolicy
    allowed_network_hosts: tuple[NonEmpty, ...] = ()
    external_effects: MagellanExternalEffects
    agent_protocol: MagellanAgentProtocol
    tenant_isolation: MagellanTenantIsolation
    idempotency_mode: MagellanIdempotencyMode
    tool_surface: Annotated[tuple[MagellanToolCapability, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def handshake_is_coherent(self) -> MagellanEnvironmentHandshake:
        tool_names = [tool.tool_name for tool in self.tool_surface]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("Magellan handshake tool names must be unique")
        if self.network_policy == MagellanNetworkPolicy.ALLOWLISTED:
            if not self.allowed_network_hosts:
                raise ValueError("allowlisted network policy requires at least one host")
        elif self.allowed_network_hosts:
            raise ValueError("network hosts are valid only with allowlisted networking")
        if len(self.allowed_secret_env_vars) != len(set(self.allowed_secret_env_vars)):
            raise ValueError("Magellan secret environment-variable names must be unique")
        if (
            self.runtime_secret_policy != MagellanRuntimeSecretPolicy.ENV_ALLOWLIST_ONLY
            and self.allowed_secret_env_vars
        ):
            raise ValueError("secret environment allowlist requires the allowlist-only policy")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256_digest(self.model_dump(mode="json", exclude={"created_at"}))

    def evaluation_blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.database_backend.casefold() != "postgresql":
            blockers.append("evaluation database is not PostgreSQL")
        if self.source_isolation == MagellanSourceIsolation.LIVE_WORKTREE:
            blockers.append("runtime executes from the mutable live worktree")
        if self.runtime_secret_policy != MagellanRuntimeSecretPolicy.ENV_ALLOWLIST_ONLY:
            blockers.append("runtime secrets are not confined to an environment allowlist")
        if self.world_isolation == MagellanWorldIsolation.NONE:
            blockers.append("world isolation is absent")
        if not self.reset_verified:
            blockers.append("world reset is not verified")
        if not self.matched_worlds_independent:
            blockers.append("matched runs do not have independent mutable worlds")
        if self.network_policy == MagellanNetworkPolicy.UNRESTRICTED:
            blockers.append("scenario network access is unrestricted")
        if self.external_effects == MagellanExternalEffects.LIVE:
            blockers.append("external side effects are live")
        if self.agent_protocol != MagellanAgentProtocol.RESPONSES:
            blockers.append("agent endpoint does not use the Responses protocol")
        if self.tenant_isolation != MagellanTenantIsolation.ENFORCED:
            blockers.append("tenant isolation is not enforced")
        if self.idempotency_mode != MagellanIdempotencyMode.DURABLE:
            blockers.append("tool idempotency is not durable")
        unsafe_effects = sorted(
            tool.tool_name
            for tool in self.tool_surface
            if tool.externally_effectful and tool.tier < MagellanToolTier.COMMIT_RECOVERABLE
        )
        if unsafe_effects:
            blockers.append("externally effectful tools have a non-commit tier")
        unvalidated_mutations = sorted(
            tool.tool_name
            for tool in self.tool_surface
            if tool.tier >= MagellanToolTier.COMMIT_RECOVERABLE and not tool.validators
        )
        if unvalidated_mutations:
            blockers.append("mutating tools do not declare validators")
        return tuple(blockers)


class MagellanEnvironmentAssessment(StrictRecord):
    repository: MagellanRepositorySnapshot
    handshake: MagellanEnvironmentHandshake | None
    environment_fingerprint: Sha256 | None
    ready: bool
    blockers: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def readiness_matches_blockers(self) -> MagellanEnvironmentAssessment:
        expected = (
            ("environment handshake is missing",)
            if self.handshake is None
            else self.handshake.evaluation_blockers()
        )
        if self.handshake is not None and (
            self.handshake.repository_source_digest != self.repository.source_digest
        ):
            expected = (*expected, "handshake repository digest does not match the worktree")
        expected_fingerprint = self.handshake.fingerprint if self.handshake is not None else None
        if self.environment_fingerprint != expected_fingerprint:
            raise ValueError("Magellan assessment fingerprint differs from its handshake")
        if self.blockers != expected or self.ready != (not expected):
            raise ValueError("Magellan environment readiness differs from its blockers")
        return self


class MagellanStatePredicate(StrictRecord):
    predicate_id: NonEmpty
    path: NonEmpty
    operator: MagellanPredicateOperator
    expected: Any = None

    @model_validator(mode="after")
    def operator_has_expected_value(self) -> MagellanStatePredicate:
        requires_value = self.operator in {
            MagellanPredicateOperator.EQUALS,
            MagellanPredicateOperator.NOT_EQUALS,
            MagellanPredicateOperator.LESS_THAN_OR_EQUAL,
            MagellanPredicateOperator.GREATER_THAN_OR_EQUAL,
            MagellanPredicateOperator.CONTAINS,
        }
        if requires_value and self.expected is None:
            raise ValueError("state predicate operator requires an expected value")
        if not requires_value and self.expected is not None:
            raise ValueError("state predicate operator does not accept an expected value")
        if self.path != "$" and any(not part for part in self.path.split(".")):
            raise ValueError("state predicate path must be '$' or dot-delimited")
        return self


class MagellanScenarioManifest(StrictRecord):
    scenario_id: NonEmpty
    version: NonEmpty
    family: MagellanScenarioFamily
    competency_id: NonEmpty
    split: NonEmpty
    seed: int
    tenant_id: NonEmpty
    user_id: NonEmpty
    actor_role: NonEmpty
    authorization_snapshot_digest: Sha256
    goal: NonEmpty
    task_inputs: Annotated[dict[str, Any], Field(min_length=1)]
    environment_fingerprint: Sha256
    initial_state_digest: Sha256
    expected_trace_status: MagellanTraceStatus
    allowed_tools: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    forbidden_tools: tuple[NonEmpty, ...] = ()
    expected_refusals: tuple[NonEmpty, ...] = ()
    required_approvals: tuple[NonEmpty, ...] = ()
    required_postconditions: tuple[MagellanStatePredicate, ...] = ()
    forbidden_postconditions: tuple[MagellanStatePredicate, ...] = ()
    max_tool_calls: Annotated[int, Field(gt=0, le=1_000)]
    max_cost_usd: Annotated[FiniteFloat, Field(ge=0.0)]
    timeout_seconds: Annotated[FiniteFloat, Field(gt=0.0, le=3_600.0)]
    idempotency_namespace: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def scenario_sets_are_valid(self) -> MagellanScenarioManifest:
        _validate_task_inputs(self.family, self.task_inputs, tenant_id=self.tenant_id)
        allowed = set(self.allowed_tools)
        forbidden = set(self.forbidden_tools)
        if len(allowed) != len(self.allowed_tools):
            raise ValueError("allowed Magellan tools must be unique")
        if len(forbidden) != len(self.forbidden_tools):
            raise ValueError("forbidden Magellan tools must be unique")
        if allowed & forbidden:
            raise ValueError("Magellan tools cannot be both allowed and forbidden")
        if not set(self.required_approvals).issubset(allowed):
            raise ValueError("approval-required tools must be in the allowed tool set")
        if not set(self.expected_refusals).issubset(allowed | forbidden):
            raise ValueError("expected refusals must name a declared scenario tool")
        predicate_ids = [
            predicate.predicate_id
            for predicate in (*self.required_postconditions, *self.forbidden_postconditions)
        ]
        if len(predicate_ids) != len(set(predicate_ids)):
            raise ValueError("Magellan predicate IDs must be unique")
        expected_authorization_digest = sha256_digest(
            {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "actor_role": self.actor_role,
                "allowed_tools": self.allowed_tools,
                "forbidden_tools": self.forbidden_tools,
                "required_approvals": self.required_approvals,
            }
        )
        if self.authorization_snapshot_digest != expected_authorization_digest:
            raise ValueError("Magellan authorization snapshot digest is not reproducible")
        scenario_token = sha256_digest(
            {
                "family": self.family.value,
                "seed": self.seed,
                "split": self.split,
                "tenant": self.tenant_id,
                "user": self.user_id,
                "task_inputs": self.task_inputs,
                "initial_state_digest": self.initial_state_digest,
            }
        )[7:23]
        if self.scenario_id != f"magellan-scenario-{scenario_token}":
            raise ValueError("Magellan scenario ID is not reproducible from its assignment")
        if self.competency_id != f"magellan.{self.family.value}":
            raise ValueError("Magellan scenario competency differs from its family")
        if self.idempotency_namespace != f"magellan:{self.tenant_id}:{scenario_token}":
            raise ValueError("Magellan idempotency namespace differs from its scenario")
        return self


def _validate_task_inputs(
    family: MagellanScenarioFamily,
    task_inputs: dict[str, Any],
    *,
    tenant_id: str,
) -> None:
    def nonempty_string(key: str) -> bool:
        return isinstance(task_inputs.get(key), str) and bool(task_inputs[key].strip())

    if family == MagellanScenarioFamily.INTAKE_AND_PLAN:
        supplied = task_inputs.get("supplied_fields")
        missing = task_inputs.get("missing_fields")
        valid = (
            nonempty_string("shipment_id")
            and isinstance(supplied, dict)
            and bool(supplied)
            and all(isinstance(key, str) and key for key in supplied)
            and all(isinstance(value, str) and value for value in supplied.values())
            and isinstance(missing, list)
            and bool(missing)
            and all(isinstance(value, str) and value for value in missing)
        )
    elif family == MagellanScenarioFamily.NEGOTIATED_RATE:
        total = task_inputs.get("negotiated_total_usd")
        tolerance = task_inputs.get("tolerance_pct")
        valid = (
            nonempty_string("shipment_id")
            and task_inputs.get("rate_kind") == "negotiated"
            and isinstance(total, int)
            and not isinstance(total, bool)
            and total > 0
            and isinstance(tolerance, (int, float))
            and not isinstance(tolerance, bool)
            and 0 <= tolerance <= 1
        )
    elif family == MagellanScenarioFamily.OUTREACH_WAIT_RESUME:
        minimum = task_inputs.get("minimum_qualified_carriers")
        valid = (
            nonempty_string("shipment_id")
            and task_inputs.get("request_kind") == "spot_rate"
            and isinstance(minimum, int)
            and not isinstance(minimum, bool)
            and minimum >= 1
        )
    elif family == MagellanScenarioFamily.REGULATED_APPROVAL:
        valid = nonempty_string("shipment_id") and nonempty_string("approved_carrier_id")
    elif family == MagellanScenarioFamily.TENANT_ISOLATION:
        valid = (
            nonempty_string("shipment_id")
            and nonempty_string("target_tenant_id")
            and task_inputs.get("target_tenant_id") != tenant_id
        )
    elif family == MagellanScenarioFamily.UNKNOWN_TOOL:
        valid = (
            nonempty_string("shipment_id")
            and task_inputs.get("requested_tool") == "file_customs_entry"
        )
    elif family == MagellanScenarioFamily.INVALID_DEPENDENCY:
        candidate = task_inputs.get("candidate_plan")
        steps = candidate.get("steps") if isinstance(candidate, dict) else None
        valid = isinstance(steps, list) and len(steps) >= 2
    else:
        valid = (
            nonempty_string("shipment_id")
            and nonempty_string("message")
            and task_inputs.get("repeat_count") == 2
        )
    if not valid:
        raise ValueError("Magellan task inputs do not match the scenario family")


class MagellanWorldAllocation(StrictRecord):
    allocation_id: NonEmpty
    condition_id: NonEmpty
    world_id: NonEmpty
    isolation_token_digest: Sha256
    environment_fingerprint: Sha256
    initial_state_digest: Sha256


class MagellanMatchedWorldManifest(StrictRecord):
    matched_group_id: NonEmpty
    source_state_digest: Sha256
    allocations: Annotated[tuple[MagellanWorldAllocation, ...], Field(min_length=2)]
    created_at: datetime

    @model_validator(mode="after")
    def worlds_are_identical_but_isolated(self) -> MagellanMatchedWorldManifest:
        allocation_ids = [allocation.allocation_id for allocation in self.allocations]
        condition_ids = [allocation.condition_id for allocation in self.allocations]
        world_ids = [allocation.world_id for allocation in self.allocations]
        isolation_tokens = [allocation.isolation_token_digest for allocation in self.allocations]
        fingerprints = {allocation.environment_fingerprint for allocation in self.allocations}
        initial_states = {allocation.initial_state_digest for allocation in self.allocations}
        if len(allocation_ids) != len(set(allocation_ids)):
            raise ValueError("matched Magellan allocation IDs must be unique")
        if len(condition_ids) != len(set(condition_ids)):
            raise ValueError("matched Magellan conditions must be unique")
        if len(world_ids) != len(set(world_ids)):
            raise ValueError("matched Magellan runs must not share a world")
        if len(isolation_tokens) != len(set(isolation_tokens)):
            raise ValueError("matched Magellan runs must not share an isolation token")
        if len(fingerprints) != 1 or len(initial_states) != 1:
            raise ValueError("matched Magellan worlds must begin from identical environment state")
        if initial_states != {self.source_state_digest}:
            raise ValueError("matched Magellan source state does not match its allocations")
        return self


class MagellanWorldSnapshot(StrictRecord):
    snapshot_id: NonEmpty
    allocation_id: NonEmpty
    world_id: NonEmpty
    isolation_token_digest: Sha256
    database_snapshot_id: NonEmpty
    tenant_id: NonEmpty
    user_id: NonEmpty
    authorization_snapshot_digest: Sha256
    environment_fingerprint: Sha256
    state_digest: Sha256
    observable_state: dict[str, Any]
    created_at: datetime

    @model_validator(mode="after")
    def state_digest_matches_projection(self) -> MagellanWorldSnapshot:
        if self.state_digest != sha256_digest(self.observable_state):
            raise ValueError("Magellan world digest differs from its observable state")
        snapshot_digest = sha256_digest(
            {
                "allocation_id": self.allocation_id,
                "world_id": self.world_id,
                "isolation_token_digest": self.isolation_token_digest,
                "database_snapshot_id": self.database_snapshot_id,
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "authorization_snapshot_digest": self.authorization_snapshot_digest,
                "environment_fingerprint": self.environment_fingerprint,
                "state_digest": self.state_digest,
            }
        )
        if self.snapshot_id != f"magellan-world-{snapshot_digest[7:31]}":
            raise ValueError("Magellan world snapshot ID differs from its captured identity")
        return self


class MagellanPlanStep(StrictRecord):
    step_id: NonEmpty
    sequence: Annotated[int, Field(ge=1)]
    tool_name: NonEmpty
    inputs: dict[str, Any]
    input_digest: Sha256
    depends_on: tuple[NonEmpty, ...] = ()
    requires_human_approval: bool = False

    @model_validator(mode="after")
    def input_digest_matches(self) -> MagellanPlanStep:
        if self.input_digest != sha256_digest(self.inputs):
            raise ValueError("Magellan plan-step input digest is invalid")
        return self


class MagellanPlanRecord(StrictRecord):
    plan_id: NonEmpty
    revision: Annotated[int, Field(ge=1)]
    status: MagellanPlanStatus
    steps: Annotated[tuple[MagellanPlanStep, ...], Field(min_length=1)]
    rejection_reason: NonEmpty | None = None
    created_at: datetime

    @model_validator(mode="after")
    def plan_is_structurally_coherent(self) -> MagellanPlanRecord:
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Magellan plan step IDs must be unique")
        if [step.sequence for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("Magellan plan-step sequence must be contiguous")
        if (self.status == MagellanPlanStatus.REJECTED) != (self.rejection_reason is not None):
            raise ValueError("only rejected Magellan plans require a rejection reason")
        return self


class MagellanToolCall(StrictRecord):
    call_id: NonEmpty
    sequence: Annotated[int, Field(ge=1)]
    tool_name: NonEmpty
    tenant_id: NonEmpty
    user_id: NonEmpty
    tier: MagellanToolTier | None = None
    inputs: dict[str, Any]
    input_digest: Sha256
    idempotency_key: NonEmpty | None = None
    plan_id: NonEmpty | None = None
    plan_step_id: NonEmpty | None = None
    approval_bypass_used: bool = False
    requested_at: datetime

    @model_validator(mode="after")
    def input_digest_matches(self) -> MagellanToolCall:
        if self.input_digest != sha256_digest(self.inputs):
            raise ValueError("Magellan tool-call input digest is invalid")
        if (self.plan_id is None) != (self.plan_step_id is None):
            raise ValueError("Magellan tool calls bind both plan and step or neither")
        return self


class MagellanValidatorResult(StrictRecord):
    validator_id: NonEmpty
    passed: bool
    summary: NonEmpty
    evidence_refs: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]


class MagellanToolObservation(StrictRecord):
    observation_id: NonEmpty
    call_id: NonEmpty
    status: MagellanToolStatus
    output: dict[str, Any]
    output_digest: Sha256
    state_before_digest: Sha256
    state_after_digest: Sha256
    mutation_occurred: bool
    cached: bool = False
    reason: NonEmpty | None = None
    validator_results: tuple[MagellanValidatorResult, ...] = ()
    provenance_refs: tuple[NonEmpty, ...]
    latency_ms: Annotated[int, Field(ge=0)]
    cost_usd: Annotated[FiniteFloat, Field(ge=0.0)] = 0.0
    observed_at: datetime

    @model_validator(mode="after")
    def observation_is_coherent(self) -> MagellanToolObservation:
        if self.output_digest != sha256_digest(self.output):
            raise ValueError("Magellan tool observation output digest is invalid")
        if (
            self.status
            in {
                MagellanToolStatus.REFUSED,
                MagellanToolStatus.PENDING_APPROVAL,
                MagellanToolStatus.FAILED,
            }
            and self.mutation_occurred
        ):
            raise ValueError("refused, pending, or failed Magellan calls cannot mutate state")
        if (
            self.status
            in {
                MagellanToolStatus.REFUSED,
                MagellanToolStatus.PENDING_APPROVAL,
                MagellanToolStatus.FAILED,
            }
            and self.reason is None
        ):
            raise ValueError("refused, pending, or failed Magellan observations require a reason")
        if not self.mutation_occurred and self.state_before_digest != self.state_after_digest:
            raise ValueError("non-mutating Magellan observation changed the world digest")
        if (
            self.status
            in {
                MagellanToolStatus.SUCCEEDED,
                MagellanToolStatus.WAITING,
            }
            and not self.provenance_refs
        ):
            raise ValueError("successful Magellan observations require provenance evidence")
        validator_ids = [result.validator_id for result in self.validator_results]
        if len(validator_ids) != len(set(validator_ids)):
            raise ValueError("Magellan observation validator results must be unique")
        return self


class MagellanApprovalDecision(StrictRecord):
    approval_id: NonEmpty
    call_id: NonEmpty
    tenant_id: NonEmpty
    user_id: NonEmpty
    status: MagellanApprovalStatus
    decided_by: NonEmpty
    decided_at: datetime


class MagellanFailure(StrictRecord):
    failure_id: NonEmpty
    failure_class: MagellanFailureClass
    stage: NonEmpty
    retryable: bool
    message: NonEmpty
    evidence_refs: tuple[NonEmpty, ...] = ()


class MagellanAgentTrace(StrictRecord):
    trace_id: NonEmpty
    scenario_id: NonEmpty
    student_id: NonEmpty
    checkpoint_id: NonEmpty
    state_id: NonEmpty
    research_role: ResearchRole
    tenant_id: NonEmpty
    user_id: NonEmpty
    authorization_snapshot_digest: Sha256
    environment_fingerprint: Sha256
    agent_protocol: MagellanAgentProtocol
    status: MagellanTraceStatus
    agent_latency_ms: Annotated[int, Field(ge=0)]
    agent_cost_usd: Annotated[FiniteFloat, Field(ge=0.0)]
    agent_input_tokens: Annotated[int, Field(ge=0)] | None = None
    agent_output_tokens: Annotated[int, Field(ge=0)] | None = None
    plans: tuple[MagellanPlanRecord, ...] = ()
    calls: tuple[MagellanToolCall, ...]
    observations: tuple[MagellanToolObservation, ...]
    approvals: tuple[MagellanApprovalDecision, ...] = ()
    failures: tuple[MagellanFailure, ...] = ()
    final_answer: NonEmpty | None = None
    final_state_digest: Sha256
    created_at: datetime
    completed_at: datetime

    @model_validator(mode="after")
    def trace_is_well_formed(self) -> MagellanAgentTrace:
        if self.completed_at < self.created_at:
            raise ValueError("Magellan trace completion precedes creation")
        elapsed_ms = int((self.completed_at - self.created_at).total_seconds() * 1_000)
        if self.agent_latency_ms > elapsed_ms:
            raise ValueError("Magellan agent latency exceeds trace wall time")
        if (self.agent_input_tokens is None) != (self.agent_output_tokens is None):
            raise ValueError("Magellan agent token counts must be both present or both absent")
        call_ids = [call.call_id for call in self.calls]
        observation_call_ids = [observation.call_id for observation in self.observations]
        if len(call_ids) != len(set(call_ids)):
            raise ValueError("Magellan tool-call IDs must be unique")
        if [call.sequence for call in self.calls] != list(range(1, len(self.calls) + 1)):
            raise ValueError("Magellan tool-call sequence must be contiguous")
        if len(observation_call_ids) != len(set(observation_call_ids)):
            raise ValueError("Magellan observations must be unique by call")
        if observation_call_ids != call_ids:
            raise ValueError("Magellan observations must follow tool-call sequence exactly")
        if any(approval.call_id not in set(call_ids) for approval in self.approvals):
            raise ValueError("Magellan approval cites an unknown call")
        plan_ids = [plan.plan_id for plan in self.plans]
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("Magellan plan IDs must be unique")
        if self.plans and [plan.revision for plan in self.plans] != list(
            range(1, len(self.plans) + 1)
        ):
            raise ValueError("Magellan plan revisions must be contiguous")
        approval_ids = [approval.approval_id for approval in self.approvals]
        if len(approval_ids) != len(set(approval_ids)):
            raise ValueError("Magellan approval IDs must be unique")
        failure_ids = [failure.failure_id for failure in self.failures]
        if len(failure_ids) != len(set(failure_ids)):
            raise ValueError("Magellan failure IDs must be unique")
        if self.observations and (
            self.final_state_digest != self.observations[-1].state_after_digest
        ):
            raise ValueError("Magellan final state differs from the last observation")
        if self.status == MagellanTraceStatus.INFRASTRUCTURE_FAILURE and not any(
            failure.failure_class != MagellanFailureClass.TOOL_EXECUTION
            for failure in self.failures
        ):
            raise ValueError("infrastructure-failure trace requires an infrastructure failure")
        if self.status != MagellanTraceStatus.INFRASTRUCTURE_FAILURE and any(
            failure.failure_class
            in {
                MagellanFailureClass.ENVIRONMENT_STARTUP,
                MagellanFailureClass.ENVIRONMENT_RESET,
                MagellanFailureClass.ENVIRONMENT_DRIFT,
                MagellanFailureClass.AGENT_PROTOCOL,
                MagellanFailureClass.AGENT_TIMEOUT,
                MagellanFailureClass.OBSERVATION_CAPTURE,
            }
            for failure in self.failures
        ):
            raise ValueError("infrastructure failures require infrastructure-failure status")
        return self


class MagellanVerificationBundle(StrictRecord):
    scenario_id: NonEmpty
    scenario_digest: Sha256
    scenario_split: NonEmpty
    trace_id: NonEmpty
    trace_digest: Sha256
    environment_fingerprint: Sha256
    verification_input_digest: Sha256
    verifier_results: Annotated[tuple[VerifierResult, ...], Field(min_length=1)]
    hard_gates: Annotated[tuple[HardGateResult, ...], Field(min_length=1)]
    reward_observations: Annotated[tuple[RewardObservation, ...], Field(min_length=1)]
    task_verified: bool
    created_at: datetime

    @model_validator(mode="after")
    def evidence_graph_is_coherent(self) -> MagellanVerificationBundle:
        result_ids = [result.result_id for result in self.verifier_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("Magellan verifier-result IDs must be unique")
        expected_verifiers = {
            "magellan.environment_integrity",
            "magellan.authorization_integrity",
            "magellan.trace_integrity",
            "magellan.safety_constraints",
            "magellan.task_completion",
        }
        if (
            len(self.verifier_results) != len(expected_verifiers)
            or {result.verifier_id for result in self.verifier_results} != expected_verifiers
        ):
            raise ValueError("Magellan verification bundle has an incomplete verifier set")
        if any(result.scope != self.scenario_id for result in self.verifier_results):
            raise ValueError("Magellan verifier result belongs to another scenario")
        if any(
            result.evidence.get("input_digest") != self.verification_input_digest
            for result in self.verifier_results
        ):
            raise ValueError("Magellan verifier results do not share the bundle input digest")
        gate_ids = [gate.gate_id for gate in self.hard_gates]
        if len(gate_ids) != len(set(gate_ids)):
            raise ValueError("Magellan hard-gate IDs must be unique")
        expected_gates = {
            "magellan:environment_integrity",
            "magellan:authorization_integrity",
            "magellan:trace_integrity",
            "magellan:safety_constraints",
        }
        if len(gate_ids) != len(expected_gates) or set(gate_ids) != expected_gates:
            raise ValueError("Magellan verification bundle has an incomplete hard-gate set")
        if any(not set(gate.evidence_refs).issubset(result_ids) for gate in self.hard_gates):
            raise ValueError("Magellan hard gate cites evidence outside its bundle")
        results_by_verifier = {result.verifier_id: result for result in self.verifier_results}
        for gate in self.hard_gates:
            verifier_id = gate.gate_id.replace("magellan:", "magellan.", 1)
            result = results_by_verifier[verifier_id]
            if gate.disposition != result.disposition or gate.evidence_refs != (result.result_id,):
                raise ValueError("Magellan hard gate differs from its verifier result")
        component_ids = [observation.component_id for observation in self.reward_observations]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("Magellan reward-observation IDs must be unique")
        expected_components = {
            "task_completion",
            "constraint_satisfaction",
            "recovery_behavior",
            "tool_efficiency",
            "normalized_cost",
        }
        if (
            len(component_ids) != len(expected_components)
            or set(component_ids) != expected_components
        ):
            raise ValueError("Magellan verification bundle has an incomplete reward vector")
        if any(
            not set(observation.evidence_refs).issubset(result_ids)
            for observation in self.reward_observations
        ):
            raise ValueError("Magellan reward observation cites evidence outside its bundle")
        task_result = next(
            result
            for result in self.verifier_results
            if result.verifier_id == "magellan.task_completion"
        )
        if self.task_verified != (task_result.disposition.value == "verified"):
            raise ValueError("Magellan task status differs from task verifier evidence")
        return self
