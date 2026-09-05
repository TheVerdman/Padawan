"""Privileged terminal dispositions, never worker actions or domain outcomes."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class ProcessAbandonmentRequest(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    abandonment_id: Annotated[str, Field(pattern=r"^process-abandonment-[0-9a-f]{32}$")]
    rollout_id: NonEmpty
    recovery_id: Annotated[str, Field(pattern=r"^process-recovery-[0-9a-f]{32}$")]
    recovery_digest: Sha256
    expected_state_digest: Sha256
    expected_authorization_sequence: Annotated[int, Field(ge=0)]
    expected_account_digest: Sha256
    reviewer_id: NonEmpty
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    exclusion: Literal["infrastructure_interruption", "unresolved_external_effect"]
    reviewed_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bounded_review(self) -> "ProcessAbandonmentRequest":
        if (
            not self.reason.strip()
            or self.reviewed_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or not 0 < (self.expires_at - self.reviewed_at).total_seconds() <= 300
        ):
            raise ValueError("abandonment needs an aware review valid for at most five minutes")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessAbandonmentReceipt(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request: ProcessAbandonmentRequest
    request_digest: Sha256
    execution_digest: Sha256
    authorization_digest: Sha256
    authorization_event_digest: Sha256
    state_id: NonEmpty
    state_sequence: Annotated[int, Field(ge=0)]
    status_before: Literal["review_required"] = "review_required"
    status_after: Literal["cancelled"] = "cancelled"
    parameter_training_eligible: Literal[False] = False
    created_at: datetime

    @model_validator(mode="after")
    def exact_review(self) -> "ProcessAbandonmentReceipt":
        if (
            self.request_digest != self.request.digest
            or self.created_at.tzinfo is None
            or not self.request.reviewed_at <= self.created_at < self.request.expires_at
        ):
            raise ValueError("abandonment receipt differs from its bounded review")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
