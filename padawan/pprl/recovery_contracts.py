"""Private reviewed recovery records; no field is a replacement-worker observation."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.artifacts.information import ForensicArtifactRef
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import RolloutStatus


class ProcessRecoveryRequest(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    recovery_id: Annotated[str, Field(pattern=r"^process-recovery-[0-9a-f]{32}$")]
    rollout_id: NonEmpty
    expected_state_digest: Sha256
    expected_lease_token_digest: Sha256 | None
    reviewer_id: NonEmpty
    reason: Annotated[str, Field(min_length=1, max_length=4096)]
    resume: bool = False
    retire_worker: bool = True
    maximum_effects: Annotated[int, Field(ge=1, le=1024)] = 64
    maximum_source_bytes: Annotated[int, Field(ge=1, le=134_217_728)] = 16_777_216
    reviewed_at: datetime

    @model_validator(mode="after")
    def aware(self) -> "ProcessRecoveryRequest":
        if self.reviewed_at.tzinfo is None or not self.reason.strip():
            raise ValueError("recovery requires a bounded reason and aware review")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessRecoverySource(StrictRecord):
    reference: ForensicArtifactRef
    owner_type: NonEmpty
    owner_id: NonEmpty


class ProcessRecoveryEffect(StrictRecord):
    decision_id: NonEmpty
    decision_digest: Sha256
    reservation_digest: Sha256
    before_phase: Literal["reserved", "started", "settled", "released"]
    after_phase: Literal["reserved", "started", "settled", "released"]
    before_resource_event_digest: Sha256
    after_resource_event_digest: Sha256
    disposition: Literal["released_unstarted", "completed_unadmitted", "unknown"]
    kind: Literal["generic", "model", "container"]
    invocation_id: NonEmpty | None = None
    observed_status: NonEmpty
    workload_digest: Sha256 | None = None
    result_digest: Sha256 | None = None
    observation_binding_digest: Sha256 | None = None
    worker_binding_digest: Sha256 | None = None
    sources: tuple[ProcessRecoverySource, ...] = ()

    @model_validator(mode="after")
    def conservative(self) -> "ProcessRecoveryEffect":
        external = self.kind != "generic"
        if (
            external != (self.invocation_id is not None)
            or external != (self.workload_digest is not None)
            or external != (self.observation_binding_digest is not None)
            or (not external and (self.sources or self.result_digest is not None))
            or len(self.sources) > (6 if self.kind == "container" else 3)
            or len({(s.owner_type, s.owner_id) for s in self.sources}) != len(self.sources)
        ):
            raise ValueError("recovery effect has inconsistent source identity")
        if self.disposition == "released_unstarted" and (
            external or self.before_phase != "reserved" or self.after_phase != "released"
        ):
            raise ValueError("recovery refund requires an untouched reservation")
        if self.disposition == "completed_unadmitted" and (
            not external or self.after_phase != "settled" or self.result_digest is None
        ):
            raise ValueError("completed recovery effect lacks independently settled evidence")
        if self.disposition == "unknown" and self.before_phase != self.after_phase:
            raise ValueError("unknown recovery effects cannot silently change funding phase")
        return self


class ProcessRecoveryReceipt(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request: ProcessRecoveryRequest
    request_digest: Sha256
    execution_digest: Sha256
    authorization_digest: Sha256
    authorization_sequence: Annotated[int, Field(ge=0)]
    authorization_event_digest: Sha256
    state_id: NonEmpty
    state_digest: Sha256
    previous_worker_id: NonEmpty | None
    previous_assignment_digest: Sha256 | None = None
    retired_worker_digest: Sha256 | None = None
    status_before: RolloutStatus
    status_after: RolloutStatus
    disposition: Literal["ready", "paused", "review_required"]
    effects: tuple[ProcessRecoveryEffect, ...]
    account_before_digest: Sha256
    account_after_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def exact(self) -> "ProcessRecoveryReceipt":
        if (
            self.created_at.tzinfo is None
            or self.created_at < self.request.reviewed_at
            or self.request_digest != self.request.digest
            or self.state_digest != self.request.expected_state_digest
            or len(self.effects) > self.request.maximum_effects
            or len({e.decision_id for e in self.effects}) != len(self.effects)
            or self.status_after
            != {
                "ready": RolloutStatus.ACTIVE,
                "paused": RolloutStatus.PAUSED,
                "review_required": RolloutStatus.REVIEW_REQUIRED,
            }[self.disposition]
            or (
                self.disposition != "review_required"
                and any(e.disposition in {"unknown", "completed_unadmitted"} for e in self.effects)
            )
            or (
                self.previous_worker_id is None
                and (
                    self.previous_assignment_digest is not None
                    or self.retired_worker_digest is not None
                )
            )
        ):
            raise ValueError("recovery receipt has inconsistent reviewed scope")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
