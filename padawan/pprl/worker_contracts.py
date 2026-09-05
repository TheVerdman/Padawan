"""Private worker authority records; none of these records is a model observation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Annotated, Literal, NoReturn

from pydantic import Field, SecretStr, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class ProcessWorkerScope(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    execution_digest: Sha256
    authorization_digest: Sha256
    broker_audience: Annotated[str, Field(min_length=1, max_length=192)]
    maximum_credential_seconds: Annotated[int, Field(ge=1, le=86_400)]
    maximum_registered_workers: Annotated[int, Field(ge=1, le=4096)]
    maximum_request_records: Annotated[int, Field(ge=1, le=1_000_000)] = 64
    maximum_request_history_bytes: Annotated[int, Field(ge=4096, le=1_073_741_824)] = 2_097_152
    reviewed_by: NonEmpty
    review_evidence: Annotated[str, Field(min_length=1, max_length=4096)]
    created_at: datetime

    @model_validator(mode="after")
    def aware(self) -> ProcessWorkerScope:
        if self.created_at.tzinfo is None:
            raise ValueError("worker scope requires an aware timestamp")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessWorkerRegistration(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    worker_id: Annotated[str, Field(pattern=r"^process-worker-[0-9a-f]{32}$")]
    execution_digest: Sha256
    authorization_digest: Sha256
    scope_digest: Sha256
    broker_audience: NonEmpty
    role_id: NonEmpty
    worker_model_digest: Sha256
    declared_capabilities: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    credential_digest: Sha256
    issued_by: NonEmpty
    issuance_evidence: Annotated[str, Field(min_length=1, max_length=4096)]
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bound(self) -> ProcessWorkerRegistration:
        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.created_at
            or self.declared_capabilities != tuple(sorted(set(self.declared_capabilities)))
        ):
            raise ValueError("worker registration requires bounded time and canonical capabilities")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessWorkerRevocation(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    worker_id: NonEmpty
    registration_digest: Sha256
    revoked_by: NonEmpty
    evidence: Annotated[str, Field(min_length=1, max_length=4096)]
    created_at: datetime

    @model_validator(mode="after")
    def aware(self) -> ProcessWorkerRevocation:
        if self.created_at.tzinfo is None:
            raise ValueError("worker revocation requires an aware timestamp")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessWorkerLeaseAssignment(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    assignment_id: Annotated[str, Field(pattern=r"^worker-assignment-[0-9a-f]{32}$")]
    worker_id: NonEmpty
    registration_digest: Sha256
    execution_digest: Sha256
    authorization_digest: Sha256
    role_id: NonEmpty
    worker_model_digest: Sha256
    rollout_id: NonEmpty
    rollout_sequence: Annotated[int, Field(ge=0)]
    state_id: NonEmpty
    state_digest: Sha256
    lease_token_digest: Sha256
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def bounded(self) -> ProcessWorkerLeaseAssignment:
        if (
            self.created_at.tzinfo is None
            or self.expires_at.tzinfo is None
            or self.expires_at <= self.created_at
        ):
            raise ValueError("worker assignment requires a bounded aware lease")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessWorkerDecisionBinding(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    decision_id: NonEmpty
    decision_digest: Sha256
    request_digest: Sha256
    assignment_id: NonEmpty
    assignment_digest: Sha256
    registration_digest: Sha256
    worker_id: NonEmpty
    created_at: datetime

    @property
    def digest(self) -> str:
        return sha256_digest(self)


@dataclass(frozen=True, slots=True, repr=False)
class ProcessWorkerAccess:
    """Ephemeral broker control input. Explicitly excluded from durable serialization."""

    worker_id: str
    broker_audience: str
    credential: SecretStr
    assignment_id: str | None = None

    def assigned(self, assignment_id: str) -> ProcessWorkerAccess:
        return replace(self, assignment_id=assignment_id)

    def __repr__(self) -> str:
        return "<ProcessWorkerAccess redacted>"

    def __reduce__(self) -> NoReturn:
        raise TypeError("worker access is not a durable record")

    def __getstate__(self) -> NoReturn:
        raise TypeError("worker access is not a durable record")
