"""Bounded runtime control envelopes; only reply.observation is a model input."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator

from padawan.governance.amber import AmberAdmissionDisposition
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import ProcessEventKind, ProjectBudgetUsage
from padawan.pprl.coordinator import ProcessActionProposal
from padawan.pprl.observation_contracts import ProcessWorkerObservation


class ProcessWorkerProposal(StrictRecord):
    event_kind: ProcessEventKind
    target_class: NonEmpty
    incremental_usage: ProjectBudgetUsage
    tool_id: NonEmpty | None = None
    tool_digest: Sha256 | None = None
    tool_operation: NonEmpty | None = None
    requested_destination: NonEmpty | None = None
    triggered_stop_conditions: tuple[NonEmpty, ...] = ()

    def assigned(self, role_id: str, worker_model_digest: str) -> ProcessActionProposal:
        return ProcessActionProposal(
            role_id=role_id,
            worker_model_digest=worker_model_digest,
            **{name: getattr(self, name) for name in type(self).model_fields},
        )


class ProcessWorkerRequestPayload(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_id: Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_.:-]+$")]
    kind: Literal["claim", "observe", "propose"]
    worker_id: Annotated[str, Field(pattern=r"^process-worker-[0-9a-f]{32}$")]
    broker_audience: Annotated[str, Field(min_length=1, max_length=192)]
    assignment_id: Annotated[str, Field(pattern=r"^worker-assignment-[0-9a-f]{32}$")] | None = None
    observation_request_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    proposal: ProcessWorkerProposal | None = None

    @model_validator(mode="after")
    def shape(self) -> ProcessWorkerRequestPayload:
        if self.kind == "claim":
            valid = (
                self.assignment_id is None
                and self.observation_request_id is None
                and self.proposal is None
            )
        elif self.kind == "observe":
            valid = (
                self.assignment_id is not None
                and self.observation_request_id is None
                and self.proposal is None
            )
        else:
            valid = (
                self.assignment_id is not None
                and self.observation_request_id is not None
                and self.proposal is not None
            )
        if not valid:
            raise ValueError("worker request has fields for a different operation")
        return self


class ProcessWorkerRequest(ProcessWorkerRequestPayload):
    credential: SecretStr = Field(exclude=True, repr=False)


class ProcessWorkerReply(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    request_id: NonEmpty
    assignment_id: NonEmpty
    observation: ProcessWorkerObservation | None = None
    disposition: AmberAdmissionDisposition | None = None


class ProcessWorkerRequestReceipt(StrictRecord):
    """Private authenticated lineage. The credential is intentionally unreconstructible."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    receipt_id: Annotated[str, Field(pattern=r"^worker-request-[0-9a-f]{32}$")]
    worker_id: NonEmpty
    registration_digest: Sha256
    request_id: NonEmpty
    request_json: Annotated[str, Field(min_length=1, max_length=65_536)]
    request_digest: Sha256
    assignment_id: NonEmpty
    assignment_digest: Sha256
    observation_id: NonEmpty
    observation_receipt_digest: Sha256
    decision_id: NonEmpty | None = None
    decision_binding_digest: Sha256 | None = None
    reply_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def bound(self) -> ProcessWorkerRequestReceipt:
        if (
            self.created_at.tzinfo is None
            or sha256_digest(self.request_json) != self.request_digest
        ):
            raise ValueError("worker request requires exact sanitized bytes and aware time")
        if (self.decision_id is None) != (self.decision_binding_digest is None):
            raise ValueError("worker request must retain both decision and assignment binding")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
