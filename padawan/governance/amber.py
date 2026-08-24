from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.models.contracts import SCHEMA_VERSION, NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import (
    PersistenceMode,
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
)

NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]


class AmberStatus(StrEnum):
    PREPARED = "prepared"
    AUTHORIZED = "authorized"
    ACTIVE = "active"
    PAUSED = "paused"
    QUARANTINED = "quarantined"
    RELEASE_APPROVED = "release_approved"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AmberAdmissionDisposition(StrEnum):
    ADMITTED = "admitted"
    DENIED = "denied"
    REVIEW_REQUIRED = "review_required"


class AmberEgressMode(StrEnum):
    DISABLED = "disabled"
    ALLOWLIST = "allowlist"


class AmberReleaseClass(StrEnum):
    RESTRICTED_INTERNAL = "restricted_internal"
    REVIEW_READY = "review_ready"
    RELEASE_APPROVED = "release_approved"


class AmberToolGrant(StrictRecord):
    role_id: NonEmpty
    tool_id: NonEmpty
    tool_digest: Sha256
    operations: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    target_classes: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def grant_is_canonical(self) -> AmberToolGrant:
        _require_canonical_unique(self.operations, "tool operations")
        _require_canonical_unique(self.target_classes, "tool target classes")
        return self


class AmberBudgetCaps(StrictRecord):
    actions: Annotated[int, Field(ge=1)]
    input_tokens: Annotated[int, Field(ge=1)]
    output_tokens: Annotated[int, Field(ge=1)]
    wall_time_seconds: Annotated[FiniteFloat, Field(gt=0.0)]
    cost: NonNegativeFinite
    artifact_bytes: Annotated[int, Field(ge=1)]
    concurrent_workers: Annotated[int, Field(ge=1)]


class AmberEnvironmentBoundary(StrictRecord):
    environment_fingerprint: Sha256
    sandbox_id: NonEmpty
    sandbox_version: NonEmpty
    sandbox_digest: Sha256
    reset_between_rollouts: bool
    network_enabled: bool
    egress_mode: AmberEgressMode
    allowed_destinations: tuple[NonEmpty, ...] = ()
    filesystem_scopes: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def boundary_is_consistent(self) -> AmberEnvironmentBoundary:
        _require_canonical_unique(self.allowed_destinations, "allowed destinations")
        _require_canonical_unique(self.filesystem_scopes, "filesystem scopes")
        if not self.network_enabled and self.egress_mode != AmberEgressMode.DISABLED:
            raise ValueError("network-disabled boundary must disable egress")
        if self.egress_mode == AmberEgressMode.DISABLED and self.allowed_destinations:
            raise ValueError("disabled egress cannot declare destinations")
        if self.egress_mode == AmberEgressMode.ALLOWLIST and not self.allowed_destinations:
            raise ValueError("allowlisted egress needs at least one destination")
        return self


class AmberCheckpointPolicy(StrictRecord):
    training_permitted: bool
    checkpoint_retention_permitted: bool
    checkpoint_export_permitted: bool
    independent_review_required: bool
    release_class: AmberReleaseClass

    @model_validator(mode="after")
    def release_is_consistent(self) -> AmberCheckpointPolicy:
        if self.checkpoint_export_permitted and not self.checkpoint_retention_permitted:
            raise ValueError("checkpoint export requires checkpoint retention")
        if self.checkpoint_export_permitted and not self.independent_review_required:
            raise ValueError("checkpoint export requires independent review")
        if (
            self.checkpoint_export_permitted
            and self.release_class != AmberReleaseClass.RELEASE_APPROVED
        ):
            raise ValueError("checkpoint export requires release-approved classification")
        if (
            self.release_class == AmberReleaseClass.RELEASE_APPROVED
            and not self.independent_review_required
        ):
            raise ValueError("release-approved checkpoints require independent review")
        return self


class AmberAuthorizationEnvelope(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    authorization_id: NonEmpty
    version: NonEmpty
    program_digest: Sha256
    distribution_digest: Sha256
    allowed_splits: Annotated[tuple[ProjectSplit, ...], Field(min_length=1)]
    allowed_target_classes: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    allowed_worker_model_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    tool_grants: tuple[AmberToolGrant, ...]
    environment: AmberEnvironmentBoundary
    budgets: AmberBudgetCaps
    persistence_modes: Annotated[tuple[PersistenceMode, ...], Field(min_length=1)]
    cross_project_memory_permitted: bool
    checkpoint_policy: AmberCheckpointPolicy
    required_reviewers: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    stop_conditions: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def envelope_is_canonical(self) -> AmberAuthorizationEnvelope:
        _require_timezone(self.created_at, "Amber envelope creation time")
        _require_timezone(self.expires_at, "Amber envelope expiration time")
        if self.expires_at <= self.created_at:
            raise ValueError("Amber envelope must expire after creation")
        _require_canonical_unique(
            tuple(split.value for split in self.allowed_splits), "allowed splits"
        )
        _require_canonical_unique(self.allowed_target_classes, "allowed target classes")
        _require_canonical_unique(self.allowed_worker_model_digests, "allowed worker model digests")
        grant_keys = tuple(
            (grant.role_id, grant.tool_id, grant.tool_digest, grant.operations)
            for grant in self.tool_grants
        )
        if len(grant_keys) != len(set(grant_keys)):
            raise ValueError("Amber tool grants must be unique")
        if tuple(sorted(grant_keys)) != grant_keys:
            raise ValueError("Amber tool grants must use canonical lexical order")
        _require_canonical_unique(
            tuple(mode.value for mode in self.persistence_modes), "persistence modes"
        )
        _require_canonical_unique(self.required_reviewers, "Amber reviewers")
        _require_canonical_unique(self.stop_conditions, "Amber stop conditions")
        if (
            PersistenceMode.CONTINUAL not in self.persistence_modes
            and self.cross_project_memory_permitted
        ):
            raise ValueError("cross-project memory requires continual persistence authority")
        if self.checkpoint_policy.checkpoint_export_permitted and len(self.required_reviewers) < 2:
            raise ValueError("checkpoint release authority requires at least two reviewers")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class AmberAuthorizationEvent(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    event_id: NonEmpty
    authorization_digest: Sha256
    sequence: Annotated[int, Field(ge=0)]
    from_status: AmberStatus | None
    to_status: AmberStatus
    actor_id: NonEmpty
    reason: NonEmpty
    evidence_refs: tuple[NonEmpty, ...] = ()
    created_at: datetime

    @model_validator(mode="after")
    def transition_is_valid(self) -> AmberAuthorizationEvent:
        _require_timezone(self.created_at, "Amber authorization event time")
        _require_canonical_unique(self.evidence_refs, "Amber event evidence")
        if self.sequence == 0:
            if self.from_status is not None or self.to_status != AmberStatus.PREPARED:
                raise ValueError("first Amber event must prepare the authorization")
        elif self.from_status is None:
            raise ValueError("later Amber event requires a source status")
        return self


class AmberActionRequest(StrictRecord):
    authorization_digest: Sha256
    rollout_id: NonEmpty
    rollout_sequence: Annotated[int, Field(ge=0)]
    state_digest: Sha256
    lease_token_digest: Sha256
    program_digest: Sha256
    distribution_digest: Sha256
    split: ProjectSplit
    persistence_mode: PersistenceMode
    event_kind: ProcessEventKind
    role_id: NonEmpty
    worker_model_digest: Sha256
    tool_id: NonEmpty | None = None
    tool_digest: Sha256 | None = None
    tool_operation: NonEmpty | None = None
    target_class: NonEmpty
    environment_fingerprint: Sha256
    projected_usage: ProjectBudgetUsage
    projected_artifact_bytes: Annotated[int, Field(ge=0)] = 0
    requested_destination: str | None = None
    triggered_stop_conditions: tuple[NonEmpty, ...] = ()
    requested_at: datetime

    @model_validator(mode="after")
    def request_is_consistent(self) -> AmberActionRequest:
        _require_timezone(self.requested_at, "Amber action request time")
        tool_fields = (self.tool_id, self.tool_digest, self.tool_operation)
        if any(value is None for value in tool_fields) != all(
            value is None for value in tool_fields
        ):
            raise ValueError("tool identity, digest, and operation must be requested together")
        if self.projected_artifact_bytes != self.projected_usage.artifact_bytes:
            raise ValueError("Amber artifact projection must match cumulative budget usage")
        _require_canonical_unique(self.triggered_stop_conditions, "triggered Amber stop conditions")
        return self


class AmberAdmissionDecision(StrictRecord):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    decision_id: NonEmpty
    authorization_digest: Sha256
    authorization_sequence: Annotated[int, Field(ge=0)]
    rollout_id: NonEmpty
    disposition: AmberAdmissionDisposition
    reason_codes: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    request_digest: Sha256
    decided_at: datetime

    @model_validator(mode="after")
    def decision_is_canonical(self) -> AmberAdmissionDecision:
        _require_timezone(self.decided_at, "Amber admission decision time")
        _require_canonical_unique(self.reason_codes, "Amber decision reasons")
        return self


class AmberPolicy:
    """Pure fail-closed action admission over one immutable envelope."""

    def decide(
        self,
        *,
        envelope: AmberAuthorizationEnvelope,
        status: AmberStatus,
        authorization_sequence: int,
        authorization_updated_at: datetime,
        request: AmberActionRequest,
        active_workers: int,
        decision_id: str,
        boundary_reasons: tuple[str, ...] = (),
    ) -> AmberAdmissionDecision:
        reasons: list[str] = list(boundary_reasons)
        review_reasons: list[str] = []
        if status != AmberStatus.ACTIVE:
            reasons.append("authorization_not_active")
        if request.requested_at < authorization_updated_at:
            reasons.append("authorization_state_changed_after_request")
        if request.authorization_digest != envelope.digest:
            reasons.append("authorization_digest_mismatch")
        if request.requested_at >= envelope.expires_at:
            reasons.append("authorization_expired")
        if request.program_digest != envelope.program_digest:
            reasons.append("program_not_authorized")
        if request.distribution_digest != envelope.distribution_digest:
            reasons.append("distribution_not_authorized")
        if request.split not in envelope.allowed_splits:
            reasons.append("split_not_authorized")
        if request.persistence_mode not in envelope.persistence_modes:
            reasons.append("persistence_mode_not_authorized")
        if request.worker_model_digest not in envelope.allowed_worker_model_digests:
            reasons.append("worker_model_not_authorized")
        if request.target_class not in envelope.allowed_target_classes:
            reasons.append("target_class_not_authorized")
        if request.environment_fingerprint != envelope.environment.environment_fingerprint:
            reasons.append("environment_identity_mismatch")
        if active_workers >= envelope.budgets.concurrent_workers:
            reasons.append("worker_concurrency_exhausted")
        reasons.extend(_budget_reasons(envelope.budgets, request))
        if request.triggered_stop_conditions:
            reasons.append("stop_condition_triggered")
        if request.tool_id is not None:
            matching_grants = tuple(
                grant
                for grant in envelope.tool_grants
                if grant.role_id == request.role_id
                and grant.tool_id == request.tool_id
                and grant.tool_digest == request.tool_digest
                and request.tool_operation in grant.operations
            )
            if not matching_grants:
                reasons.append("tool_operation_not_authorized")
            elif not any(
                not grant.target_classes or request.target_class in grant.target_classes
                for grant in matching_grants
            ):
                reasons.append("tool_target_not_authorized")
        if request.requested_destination is not None:
            if envelope.environment.egress_mode == AmberEgressMode.DISABLED:
                reasons.append("egress_disabled")
            elif request.requested_destination not in envelope.environment.allowed_destinations:
                reasons.append("egress_destination_not_authorized")
        if (
            request.event_kind == ProcessEventKind.OUTCOME_ASSESSED
            and envelope.checkpoint_policy.independent_review_required
        ):
            review_reasons.append("independent_outcome_review_required")

        disposition = (
            AmberAdmissionDisposition.DENIED
            if reasons
            else AmberAdmissionDisposition.REVIEW_REQUIRED
            if review_reasons
            else AmberAdmissionDisposition.ADMITTED
        )
        reason_codes = tuple(sorted(set(reasons or review_reasons or ["authorized"])))
        return AmberAdmissionDecision(
            decision_id=decision_id,
            authorization_digest=envelope.digest,
            authorization_sequence=authorization_sequence,
            rollout_id=request.rollout_id,
            disposition=disposition,
            reason_codes=reason_codes,
            request_digest=sha256_digest(request),
            decided_at=request.requested_at,
        )


_AMBER_TRANSITIONS: dict[AmberStatus, frozenset[AmberStatus]] = {
    AmberStatus.PREPARED: frozenset(
        {AmberStatus.AUTHORIZED, AmberStatus.REVOKED, AmberStatus.EXPIRED}
    ),
    AmberStatus.AUTHORIZED: frozenset(
        {AmberStatus.ACTIVE, AmberStatus.REVOKED, AmberStatus.EXPIRED}
    ),
    AmberStatus.ACTIVE: frozenset(
        {
            AmberStatus.PAUSED,
            AmberStatus.QUARANTINED,
            AmberStatus.REVOKED,
            AmberStatus.EXPIRED,
        }
    ),
    AmberStatus.PAUSED: frozenset(
        {
            AmberStatus.ACTIVE,
            AmberStatus.QUARANTINED,
            AmberStatus.RELEASE_APPROVED,
            AmberStatus.REVOKED,
            AmberStatus.EXPIRED,
        }
    ),
    AmberStatus.QUARANTINED: frozenset({AmberStatus.REVOKED}),
    AmberStatus.RELEASE_APPROVED: frozenset({AmberStatus.REVOKED}),
    AmberStatus.EXPIRED: frozenset(),
    AmberStatus.REVOKED: frozenset(),
}


def assert_amber_transition(current: AmberStatus, target: AmberStatus) -> None:
    if target not in _AMBER_TRANSITIONS[current]:
        raise ValueError(f"invalid Amber transition: {current.value} -> {target.value}")


def _budget_reasons(caps: AmberBudgetCaps, request: AmberActionRequest) -> list[str]:
    usage = request.projected_usage
    reasons: list[str] = []
    if usage.actions > caps.actions:
        reasons.append("action_budget_exhausted")
    if usage.input_tokens > caps.input_tokens:
        reasons.append("input_token_budget_exhausted")
    if usage.output_tokens > caps.output_tokens:
        reasons.append("output_token_budget_exhausted")
    if float(usage.wall_time_seconds) > float(caps.wall_time_seconds):
        reasons.append("wall_time_budget_exhausted")
    if float(usage.cost) > float(caps.cost):
        reasons.append("cost_budget_exhausted")
    if request.projected_artifact_bytes > caps.artifact_bytes:
        reasons.append("artifact_budget_exhausted")
    return reasons


def _require_canonical_unique(values: tuple[Any, ...], label: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
    if tuple(sorted(values)) != values:
        raise ValueError(f"{label} must use canonical lexical order")


def _require_timezone(value: datetime, label: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
