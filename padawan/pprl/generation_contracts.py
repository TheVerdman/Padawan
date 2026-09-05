"""Private admission of exact process model inputs and configured transport effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field, model_validator

from padawan.adapters.base import GenerationRequest
from padawan.adapters.prepared import PreparedGeneration
from padawan.artifacts.information import ForensicArtifactRef
from padawan.models.contracts import (
    NonEmpty,
    SamplingConfiguration,
    Sha256,
    SourceRights,
    StrictRecord,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest

if TYPE_CHECKING:
    from padawan.models.tables import ProcessGenerationWorkloadRow


class ProcessGenerationPolicy(StrictRecord):
    """Explicit trusted composition; reviewer identity is declared, not authenticated."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    role_id: NonEmpty
    worker_model_digest: Sha256
    provider: NonEmpty
    transport_configuration_digest: Sha256
    instructions: NonEmpty
    sampling: SamplingConfiguration
    schema_name: NonEmpty | None = None
    json_schema: dict[str, Any] | None = None
    input_projection: Literal["canonical_worker_observation"] = "canonical_worker_observation"
    maximum_request_bytes: Annotated[int, Field(gt=0)] = 2_000_000
    maximum_prepared_bytes: Annotated[int, Field(gt=0)] = 4_000_000
    reviewed_by: NonEmpty
    reviewed_at: datetime
    instructions_rights: SourceRights

    @model_validator(mode="after")
    def policy_is_explicit(self) -> ProcessGenerationPolicy:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("generation policy requires a timezone-aware review")
        if (self.schema_name is None) != (self.json_schema is None):
            raise ValueError("generation policy schema name and content must be supplied together")
        canonical_json_bytes(self.model_dump(mode="json"))
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessGenerationWorkload(StrictRecord):
    """Retained before I/O. Never deliver this receipt or its references to a worker."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    invocation_id: NonEmpty
    request_id: NonEmpty
    rollout_id: NonEmpty
    execution_digest: Sha256
    worker_id: NonEmpty
    role_id: NonEmpty
    worker_model_digest: Sha256
    purpose: NonEmpty
    lease_token_digest: Sha256
    observation_id: NonEmpty
    observation_receipt_digest: Sha256
    observation_decision_digest: Sha256
    decision_id: NonEmpty
    decision_digest: Sha256
    policy: ProcessGenerationPolicy
    policy_digest: Sha256
    request_json: NonEmpty
    request_digest: Sha256
    prepared: PreparedGeneration
    prepared_artifact: ForensicArtifactRef
    admitted_at: datetime

    @model_validator(mode="after")
    def source_bytes_are_exact(self) -> ProcessGenerationWorkload:
        if self.admitted_at.tzinfo is None or self.policy.reviewed_at > self.admitted_at:
            raise ValueError("generation admission must follow its timezone-aware policy review")
        request = GenerationRequest.model_validate_json(self.request_json)
        if (
            self.policy.digest != self.policy_digest
            or canonical_json_bytes(request).decode("utf-8") != self.request_json
            or sha256_digest(request) != self.request_digest
            or request.request_id != self.request_id
            or self.prepared.request_id != self.request_id
            or self.prepared.request_digest != self.request_digest
            or self.prepared_artifact.artifact.digest != sha256_digest(self.prepared)
            or self.policy.worker_model_digest != self.worker_model_digest
            or self.policy.role_id != self.role_id
            or self.policy.provider != self.prepared.provider
            or self.policy.transport_configuration_digest != self.prepared.configuration_digest
            or len(self.request_json.encode("utf-8")) > self.policy.maximum_request_bytes
            or len(canonical_json_bytes(self.prepared)) > self.policy.maximum_prepared_bytes
        ):
            raise ValueError("generation workload has inconsistent inputs, policy or transport")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


def generation_workload_from_row(row: ProcessGenerationWorkloadRow) -> ProcessGenerationWorkload:
    """Privileged historical integrity check; this grants no dispatch or learning authority."""
    receipt = ProcessGenerationWorkload.model_validate(row.record_json, strict=False)
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if (
        sha256_digest(row.record_json) != row.record_digest
        or receipt.invocation_id != row.invocation_id
        or receipt.request_id != row.request_id
        or receipt.decision_id != row.decision_id
        or receipt.observation_id != row.observation_id
        or receipt.prepared_artifact.artifact.artifact_id != row.prepared_artifact_id
        or receipt.admitted_at != created_at
    ):
        raise ValueError("generation workload receipt is inconsistent")
    return receipt
