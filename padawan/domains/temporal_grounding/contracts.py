from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, StrictRecord
from padawan.temporal.contracts import (
    DurationForecast,
    TemporalAction,
    TemporalContinuity,
    TemporalFrame,
)


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


class TemporalScenarioFamily(StrEnum):
    GAP_CONTINUITY = "gap_continuity"
    ACTIVITY_HONESTY = "activity_honesty"
    OBSERVATION_FRESHNESS = "observation_freshness"
    DURATION_CALIBRATION = "duration_calibration"
    ETA_REVISION = "eta_revision"


class TemporalConversationMessage(StrictRecord):
    message_id: NonEmpty
    role: Literal["user", "assistant"]
    content: NonEmpty
    sent_at: datetime

    @model_validator(mode="after")
    def aware_time(self) -> TemporalConversationMessage:
        _aware(self.sent_at, "conversation message sent_at")
        return self


class TemporalScenarioOracle(StrictRecord):
    continuity: TemporalContinuity
    acknowledge_gap: bool
    claims_continuous_activity: Literal[False] = False
    required_actions: Annotated[tuple[TemporalAction, ...], Field(min_length=1)]
    required_duration_forecasts: tuple[DurationForecast, ...] = ()
    next_check_at: datetime | None = None
    forbidden_response_fragments: tuple[Annotated[str, Field(min_length=2)], ...] = ()

    @model_validator(mode="after")
    def canonical_oracle(self) -> TemporalScenarioOracle:
        if self.next_check_at is not None:
            _aware(self.next_check_at, "oracle next_check_at")
        action_keys = [
            (action.kind.value, action.subject, action.execute_at, action.rationale_code)
            for action in self.required_actions
        ]
        if len(action_keys) != len(set(action_keys)):
            raise ValueError("temporal oracle contains duplicate actions")
        operation_ids = [item.operation_id for item in self.required_duration_forecasts]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("temporal oracle contains duplicate forecasts")
        normalized = tuple(fragment.casefold() for fragment in self.forbidden_response_fragments)
        if len(normalized) != len(set(normalized)):
            raise ValueError("temporal oracle forbidden fragments must be unique")
        return self


class TemporalScenarioManifest(StrictRecord):
    scenario_id: NonEmpty
    family: TemporalScenarioFamily
    frame: TemporalFrame
    prior_conversation: tuple[TemporalConversationMessage, ...]
    current_user_message: TemporalConversationMessage
    oracle: TemporalScenarioOracle
    split: NonEmpty
    created_at: datetime

    @model_validator(mode="after")
    def coherent_scenario(self) -> TemporalScenarioManifest:
        _aware(self.created_at, "scenario created_at")
        if self.current_user_message.role != "user":
            raise ValueError("temporal scenario current message must be a user message")
        if self.current_user_message.sent_at != self.frame.current_event_at:
            raise ValueError("temporal frame is not anchored to the current user message")
        ordered = tuple(
            sorted(self.prior_conversation, key=lambda item: (item.sent_at, item.message_id))
        )
        if ordered != self.prior_conversation:
            raise ValueError("temporal scenario conversation must be chronological")
        if any(
            message.sent_at >= self.current_user_message.sent_at
            for message in self.prior_conversation
        ):
            raise ValueError("prior conversation must precede the current user message")
        identifiers = [message.message_id for message in self.prior_conversation]
        identifiers.append(self.current_user_message.message_id)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("temporal scenario message identifiers must be unique")
        return self
