"""Private funding and accounting contracts; never a worker-visible budget projection."""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import ProjectBudgetUsage

MAX_RESOURCE = 2**63 - 1
ResourceInteger = Annotated[int, Field(ge=0, le=MAX_RESOURCE)]


class ProcessResources(StrictRecord):
    actions: ResourceInteger = 0
    input_tokens: ResourceInteger = 0
    output_tokens: ResourceInteger = 0
    artifact_bytes: ResourceInteger = 0
    action_microseconds: ResourceInteger = 0
    micro_usd: ResourceInteger = 0

    def plus(self, other: ProcessResources) -> ProcessResources:
        return ProcessResources(
            **{k: v + other.model_dump()[k] for k, v in self.model_dump().items()}
        )

    def minus(self, other: ProcessResources) -> ProcessResources:
        return ProcessResources(
            **{k: v - other.model_dump()[k] for k, v in self.model_dump().items()}
        )

    def covers(self, other: ProcessResources) -> bool:
        return all(v >= other.model_dump()[k] for k, v in self.model_dump().items())


def resources_from_usage(
    usage: ProjectBudgetUsage,
    *,
    previous: ProjectBudgetUsage | None = None,
    ceiling: bool = True,
) -> ProcessResources:
    before = previous or ProjectBudgetUsage()
    values = {
        k: getattr(usage, k) - getattr(before, k)
        for k in ("actions", "input_tokens", "output_tokens", "artifact_bytes")
    }
    # Decimal context must cover the entire finite-float exponent range, not just money cents.
    with localcontext() as context:
        context.prec = 800
        for source, target in (
            ("wall_time_seconds", "action_microseconds"),
            ("cost", "micro_usd"),
        ):
            value = Decimal(str(getattr(usage, source))) - Decimal(str(getattr(before, source)))
            if value < 0:
                raise ValueError("resource declarations must be monotone")
            values[target] = int(
                (value * 1_000_000).to_integral_value(
                    rounding=ROUND_CEILING if ceiling else ROUND_FLOOR
                )
            )
    return ProcessResources(**values)


class ProcessModelRate(StrictRecord):
    rate_id: NonEmpty
    worker_model_digest: Sha256
    input_micro_usd_per_million_tokens: ResourceInteger
    output_micro_usd_per_million_tokens: ResourceInteger
    request_micro_usd: ResourceInteger

    def price(self, input_tokens: int, output_tokens: int) -> int:
        value = (
            input_tokens * self.input_micro_usd_per_million_tokens
            + output_tokens * self.output_micro_usd_per_million_tokens
        )
        result = self.request_micro_usd + (value + 999_999) // 1_000_000
        if min(input_tokens, output_tokens, result) < 0 or result > MAX_RESOURCE:
            raise ValueError("model accounting exceeds the integer envelope")
        return result


class ProcessResourceGrant(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    grant_id: NonEmpty
    authorization_digest: Sha256
    capacity: ProcessResources
    concurrent_reservations: Annotated[int, Field(ge=1, le=MAX_RESOURCE)]
    currency: Literal["USD"] = "USD"
    token_evidence: Literal["provider_reported"] = "provider_reported"
    artifact_accounting: Literal["logical_allowance"] = "logical_allowance"
    time_accounting: Literal["reserved_action_time"] = "reserved_action_time"
    model_rates: tuple[ProcessModelRate, ...]
    reviewed_by: NonEmpty
    review_evidence: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def canonical(self) -> ProcessResourceGrant:
        _aware(self.created_at)
        identities = tuple(rate.worker_model_digest for rate in self.model_rates)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("resource model rates must be unique and sorted")
        if self.capacity.actions == 0 or self.capacity.action_microseconds == 0:
            raise ValueError("resource grants require action and time capacity")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessResourceReservation(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    decision_id: NonEmpty
    decision_digest: Sha256
    request_digest: Sha256
    grant_digest: Sha256
    authorization_digest: Sha256
    authorization_sequence: ResourceInteger
    rollout_id: NonEmpty
    execution_digest: Sha256
    state_digest: Sha256
    lease_token_digest: Sha256
    worker_model_digest: Sha256
    amount: ProcessResources
    created_at: datetime

    @model_validator(mode="after")
    def canonical(self) -> ProcessResourceReservation:
        _aware(self.created_at)
        if self.amount.actions != 1:
            raise ValueError("each resource reservation admits exactly one action")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


ResourceEventKind = Literal["grant", "initial", "reserve", "start", "settle", "release", "stop"]


class ProcessResourceEvent(StrictRecord):
    """Hash-linked accounting journal with a checked materialized balance."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    authorization_digest: Sha256
    sequence: ResourceInteger
    previous_digest: Sha256 | None
    grant_digest: Sha256
    kind: ResourceEventKind
    subject_id: NonEmpty
    reservation_digest: Sha256 | None = None
    amount: ProcessResources
    held: ProcessResources
    charged: ProcessResources
    open_reservations: ResourceInteger
    stopped: bool
    source_digests: tuple[Sha256, ...] = ()
    basis: Literal["grant", "reservation", "committed_ceiling", "provider_reported", "reviewed"]
    actor_id: NonEmpty
    reason: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def canonical(self) -> ProcessResourceEvent:
        _aware(self.created_at)
        if (self.sequence == 0) != (self.previous_digest is None):
            raise ValueError("resource chain must have exactly one origin")
        if (self.sequence == 0) != (self.kind == "grant"):
            raise ValueError("only the initial resource event can grant capacity")
        if self.source_digests != tuple(sorted(set(self.source_digests))):
            raise ValueError("resource source digests must be unique and sorted")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


def _aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("resource evidence requires timezone-aware timestamps")
