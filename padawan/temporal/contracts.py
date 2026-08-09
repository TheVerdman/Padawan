from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, FiniteFloat, model_validator

from padawan.models.contracts import NonEmpty, Score, Sha256, StrictRecord

NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0.0)]
PositiveFinite = Annotated[FiniteFloat, Field(gt=0.0)]


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class TemporalEventKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_OBSERVATION = "tool_observation"
    OPERATION_STATUS = "operation_status"
    SYSTEM = "system"


class FreshnessDisposition(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"
    IMMUTABLE = "immutable"


class OperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    @property
    def terminal(self) -> bool:
        return self in {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.TIMED_OUT,
        }


class TemporalContinuity(StrEnum):
    IMMEDIATE_CONTINUATION = "immediate_continuation"
    RESUME_AFTER_GAP = "resume_after_gap"
    REESTABLISH_CONTEXT = "reestablish_context"


class TemporalActionKind(StrEnum):
    CONTINUE = "continue"
    WAIT = "wait"
    POLL = "poll"
    REVALIDATE = "revalidate"
    INSPECT = "inspect"
    RETRY = "retry"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    NOTIFY_USER = "notify_user"


class TemporalEvent(StrictRecord):
    event_id: NonEmpty
    kind: TemporalEventKind
    occurred_at: datetime
    completed_at: datetime | None = None
    subject: NonEmpty
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_times(self) -> TemporalEvent:
        _require_aware(self.occurred_at, "event occurred_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "event completed_at")
            if self.completed_at < self.occurred_at:
                raise ValueError("event completion precedes occurrence")
        return self


class ActivityInterval(StrictRecord):
    activity_id: NonEmpty
    operation_id: str | None = None
    started_at: datetime
    ended_at: datetime
    kind: NonEmpty

    @model_validator(mode="after")
    def valid_interval(self) -> ActivityInterval:
        _require_aware(self.started_at, "activity started_at")
        _require_aware(self.ended_at, "activity ended_at")
        if self.ended_at < self.started_at:
            raise ValueError("activity ends before it starts")
        return self


class TemporalObservation(StrictRecord):
    observation_id: NonEmpty
    subject: NonEmpty
    observed_at: datetime
    version: str | None = None
    freshness: FreshnessDisposition
    freshness_policy: NonEmpty
    valid_until: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_observation(self) -> TemporalObservation:
        _require_aware(self.observed_at, "observation observed_at")
        if self.valid_until is not None:
            _require_aware(self.valid_until, "observation valid_until")
            if self.valid_until < self.observed_at:
                raise ValueError("observation validity ends before observation")
        if self.freshness == FreshnessDisposition.IMMUTABLE and self.valid_until is not None:
            raise ValueError("immutable observations do not expire")
        return self


class DurationProfile(StrictRecord):
    profile_id: NonEmpty
    operation_type: NonEmpty
    environment_fingerprint: Sha256
    workload_class: NonEmpty
    sample_count: Annotated[int, Field(ge=1)]
    success_count: Annotated[int, Field(ge=0)]
    timeout_count: Annotated[int, Field(ge=0)]
    p50_seconds: PositiveFinite | None = None
    p90_seconds: PositiveFinite | None = None
    p95_seconds: PositiveFinite | None = None
    timeout_probability: Score
    source_span_ids: tuple[NonEmpty, ...]
    as_of: datetime
    created_at: datetime

    @model_validator(mode="after")
    def coherent_profile(self) -> DurationProfile:
        _require_aware(self.as_of, "duration profile as_of")
        _require_aware(self.created_at, "duration profile created_at")
        if self.created_at < self.as_of:
            raise ValueError("duration profile creation precedes its evidence cutoff")
        if self.sample_count != len(self.source_span_ids):
            raise ValueError("duration profile sample count differs from source spans")
        if self.success_count + self.timeout_count > self.sample_count:
            raise ValueError("duration profile outcomes exceed sample count")
        if len(self.source_span_ids) != len(set(self.source_span_ids)):
            raise ValueError("duration profile source spans must be unique")
        if tuple(sorted(self.source_span_ids)) != self.source_span_ids:
            raise ValueError("duration profile source spans must be canonically sorted")
        expected_timeout_probability = self.timeout_count / self.sample_count
        if abs(self.timeout_probability - expected_timeout_probability) > 1e-12:
            raise ValueError("duration profile timeout probability is not reproducible")
        quantiles = (self.p50_seconds, self.p90_seconds, self.p95_seconds)
        if self.success_count == 0:
            if any(value is not None for value in quantiles):
                raise ValueError("duration quantiles require successful spans")
        elif any(value is None for value in quantiles):
            raise ValueError("successful spans require all duration quantiles")
        else:
            p50 = self.p50_seconds
            p90 = self.p90_seconds
            p95 = self.p95_seconds
            if p50 is None or p90 is None or p95 is None:
                raise ValueError("successful spans require all duration quantiles")
            if not (p50 <= p90 <= p95):
                raise ValueError("duration quantiles must be monotonic")
        return self


class ActiveOperation(StrictRecord):
    operation_id: NonEmpty
    operation_type: NonEmpty
    status: OperationStatus
    enqueued_at: datetime
    started_at: datetime | None = None
    last_progress_at: datetime | None = None
    progress: Score | None = None
    duration_profile: DurationProfile | None = None
    expected_completion_p50: datetime | None = None
    expected_completion_p90: datetime | None = None
    next_safe_poll_at: datetime | None = None
    timeout_at: datetime | None = None
    cancellable: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_operation(self) -> ActiveOperation:
        _require_aware(self.enqueued_at, "operation enqueued_at")
        for name, value in (
            ("started_at", self.started_at),
            ("last_progress_at", self.last_progress_at),
            ("expected_completion_p50", self.expected_completion_p50),
            ("expected_completion_p90", self.expected_completion_p90),
            ("next_safe_poll_at", self.next_safe_poll_at),
            ("timeout_at", self.timeout_at),
        ):
            if value is not None:
                _require_aware(value, f"operation {name}")
        if self.started_at is not None and self.started_at < self.enqueued_at:
            raise ValueError("operation starts before it is enqueued")
        if self.status == OperationStatus.QUEUED and self.started_at is not None:
            raise ValueError("queued operation cannot already have a start time")
        if self.status == OperationStatus.RUNNING and self.started_at is None:
            raise ValueError("running operation requires a start time")
        if self.last_progress_at is not None and (
            self.started_at is None or self.last_progress_at < self.started_at
        ):
            raise ValueError("operation progress requires a prior start")
        if self.progress is not None and self.last_progress_at is None:
            raise ValueError("operation progress requires a progress timestamp")
        if (self.expected_completion_p50 is None) != (self.expected_completion_p90 is None):
            raise ValueError("operation completion forecast requires p50 and p90")
        if (
            self.expected_completion_p50 is not None
            and self.expected_completion_p90 is not None
            and self.expected_completion_p90 < self.expected_completion_p50
        ):
            raise ValueError("operation p90 completion precedes p50")
        if self.status.terminal:
            raise ValueError("active operation cannot have a terminal status")
        if (
            self.duration_profile is not None
            and self.duration_profile.operation_type != self.operation_type
        ):
            raise ValueError("active operation type differs from its duration profile")
        return self


class TemporalFrame(StrictRecord):
    frame_id: NonEmpty
    sequence: Annotated[int, Field(ge=0)]
    now_utc: datetime
    user_timezone: NonEmpty
    conversation_started_at: datetime
    current_event_at: datetime
    previous_user_message_at: datetime | None = None
    previous_assistant_message_at: datetime | None = None
    elapsed_since_previous_exchange_seconds: NonNegativeFinite | None = None
    events: tuple[TemporalEvent, ...] = ()
    agent_activity_since_last_exchange: tuple[ActivityInterval, ...] = ()
    observations: tuple[TemporalObservation, ...] = ()
    active_operations: tuple[ActiveOperation, ...] = ()

    @model_validator(mode="after")
    def coherent_frame(self) -> TemporalFrame:
        _require_aware(self.now_utc, "temporal frame now_utc")
        _require_aware(self.conversation_started_at, "conversation_started_at")
        _require_aware(self.current_event_at, "current_event_at")
        try:
            ZoneInfo(self.user_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("temporal frame has an unknown IANA timezone") from exc
        if self.conversation_started_at > self.current_event_at:
            raise ValueError("conversation starts after the temporal frame")
        if self.current_event_at > self.now_utc:
            raise ValueError("current event occurs after the temporal frame")
        anchors = (self.previous_user_message_at, self.previous_assistant_message_at)
        for anchor in anchors:
            if anchor is not None:
                _require_aware(anchor, "last message timestamp")
                if anchor > self.current_event_at:
                    raise ValueError("previous message occurs after the current event")
        most_recent = max((anchor for anchor in anchors if anchor is not None), default=None)
        if self.elapsed_since_previous_exchange_seconds is None:
            if most_recent is not None:
                raise ValueError("last-message timestamps require a derived elapsed duration")
        elif most_recent is None:
            raise ValueError("elapsed duration requires at least one last-message timestamp")
        else:
            expected = (self.current_event_at - most_recent).total_seconds()
            if abs(float(self.elapsed_since_previous_exchange_seconds) - expected) > 1e-6:
                raise ValueError("elapsed duration differs from authoritative timestamps")
        if any(event.occurred_at > self.now_utc for event in self.events):
            raise ValueError("temporal frame contains a future event")
        ordered_events = tuple(
            sorted(self.events, key=lambda item: (item.occurred_at, item.event_id))
        )
        if ordered_events != self.events:
            raise ValueError("temporal events must be in canonical chronological order")
        if any(
            interval.ended_at > self.now_utc for interval in self.agent_activity_since_last_exchange
        ):
            raise ValueError("temporal frame contains future agent activity")
        activity_floor = most_recent or self.conversation_started_at
        if any(
            interval.started_at < activity_floor
            for interval in self.agent_activity_since_last_exchange
        ):
            raise ValueError("agent activity predates the previous exchange")
        if any(item.observed_at > self.now_utc for item in self.observations):
            raise ValueError("temporal frame contains a future observation")
        if any(
            item.freshness == FreshnessDisposition.FRESH
            and item.valid_until is not None
            and item.valid_until < self.now_utc
            for item in self.observations
        ):
            raise ValueError("temporal frame labels an expired observation as fresh")
        for operation in self.active_operations:
            operation_times = (
                operation.enqueued_at,
                operation.started_at,
                operation.last_progress_at,
            )
            if any(value is not None and value > self.now_utc for value in operation_times):
                raise ValueError("temporal frame contains a future operation event")
            if (
                operation.duration_profile is not None
                and operation.duration_profile.as_of > self.now_utc
            ):
                raise ValueError("temporal frame contains a future duration profile")
        canonical_groups = (
            (
                "agent activity",
                self.agent_activity_since_last_exchange,
                lambda item: (item.started_at, item.activity_id),
            ),
            (
                "observations",
                self.observations,
                lambda item: (item.observed_at, item.observation_id),
            ),
            (
                "active operations",
                self.active_operations,
                lambda item: (item.enqueued_at, item.operation_id),
            ),
        )
        for label, values, key in canonical_groups:
            if tuple(sorted(values, key=key)) != values:
                raise ValueError(f"temporal frame {label} must be canonically ordered")
        identifier_groups = {
            "event": [item.event_id for item in self.events],
            "activity": [item.activity_id for item in self.agent_activity_since_last_exchange],
            "observation": [item.observation_id for item in self.observations],
            "active operation": [item.operation_id for item in self.active_operations],
        }
        for label, identifiers in identifier_groups.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"temporal frame contains duplicate {label} identifiers")
        return self


class TemporalAction(StrictRecord):
    kind: TemporalActionKind
    subject: NonEmpty
    execute_at: datetime | None = None
    rationale_code: NonEmpty

    @model_validator(mode="after")
    def aware_execution_time(self) -> TemporalAction:
        if self.execute_at is not None:
            _require_aware(self.execute_at, "temporal action execute_at")
        return self


class DurationForecast(StrictRecord):
    operation_id: NonEmpty
    p50_completed_at: datetime
    p90_completed_at: datetime
    source_profile_id: NonEmpty

    @model_validator(mode="after")
    def ordered_forecast(self) -> DurationForecast:
        _require_aware(self.p50_completed_at, "forecast p50_completed_at")
        _require_aware(self.p90_completed_at, "forecast p90_completed_at")
        if self.p90_completed_at < self.p50_completed_at:
            raise ValueError("forecast p90 completion precedes p50")
        return self


class TemporalDecision(StrictRecord):
    continuity: TemporalContinuity
    acknowledge_gap: bool
    claims_continuous_activity: bool
    actions: Annotated[tuple[TemporalAction, ...], Field(min_length=1)]
    duration_forecasts: tuple[DurationForecast, ...] = ()
    next_check_at: datetime | None = None
    response: NonEmpty

    @model_validator(mode="after")
    def canonical_decision(self) -> TemporalDecision:
        if self.next_check_at is not None:
            _require_aware(self.next_check_at, "temporal decision next_check_at")
        action_keys = [
            (action.kind.value, action.subject, action.execute_at, action.rationale_code)
            for action in self.actions
        ]
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("temporal decision contains duplicate actions")
        operation_ids = [forecast.operation_id for forecast in self.duration_forecasts]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("temporal decision contains duplicate duration forecasts")
        return self


class OperationSpanEventRecord(StrictRecord):
    event_id: NonEmpty
    operation_id: NonEmpty
    sequence: Annotated[int, Field(ge=1)]
    status: OperationStatus
    occurred_at: datetime
    progress: Score | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def aware_event_time(self) -> OperationSpanEventRecord:
        _require_aware(self.occurred_at, "operation event occurred_at")
        if self.progress is not None and self.status not in {
            OperationStatus.RUNNING,
            OperationStatus.WAITING,
        }:
            raise ValueError("operation progress is valid only while running or waiting")
        return self


class OperationSpanRecord(StrictRecord):
    operation_id: NonEmpty
    parent_operation_id: str | None = None
    run_id: str | None = None
    source_ref: str | None = None
    operation_type: NonEmpty
    environment_fingerprint: Sha256
    workload_class: NonEmpty
    workload: dict[str, Any]
    status: OperationStatus
    enqueued_at: datetime
    started_at: datetime | None = None
    first_progress_at: datetime | None = None
    completed_at: datetime | None = None
    events: Annotated[tuple[OperationSpanEventRecord, ...], Field(min_length=1)]
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def coherent_span(self) -> OperationSpanRecord:
        for name, value in (
            ("enqueued_at", self.enqueued_at),
            ("started_at", self.started_at),
            ("first_progress_at", self.first_progress_at),
            ("completed_at", self.completed_at),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if value is not None:
                _require_aware(value, f"operation span {name}")
        if self.started_at is not None and self.started_at < self.enqueued_at:
            raise ValueError("operation span starts before enqueue")
        if self.first_progress_at is not None and (
            self.started_at is None or self.first_progress_at < self.started_at
        ):
            raise ValueError("operation span progress precedes start")
        if self.status.terminal != (self.completed_at is not None):
            raise ValueError("only terminal operation spans have completion times")
        if self.completed_at is not None and self.completed_at < self.enqueued_at:
            raise ValueError("operation span completes before enqueue")
        if self.updated_at < self.created_at:
            raise ValueError("operation span update precedes creation")
        expected_sequences = tuple(range(1, len(self.events) + 1))
        if tuple(event.sequence for event in self.events) != expected_sequences:
            raise ValueError("operation span events must have contiguous sequence numbers")
        if any(event.operation_id != self.operation_id for event in self.events):
            raise ValueError("operation span contains an event for another operation")
        if tuple(event.occurred_at for event in self.events) != tuple(
            sorted(event.occurred_at for event in self.events)
        ):
            raise ValueError("operation span events must be chronological")
        if self.events and self.events[-1].status != self.status:
            raise ValueError("operation span status differs from its latest event")
        if self.events[-1].occurred_at != self.updated_at:
            raise ValueError("operation span update time differs from its latest event")
        if self.completed_at is not None and self.events[-1].occurred_at != self.completed_at:
            raise ValueError("operation span completion differs from its terminal event")
        return self

    @property
    def wall_seconds(self) -> float | None:
        if self.completed_at is None:
            return None
        return (self.completed_at - self.enqueued_at).total_seconds()

    @property
    def service_seconds(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()
