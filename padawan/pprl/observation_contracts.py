from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from padawan.artifacts.information import ProcessArtifactRef
from padawan.governance.amber import AmberAdmissionDisposition
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.pprl.contracts import (
    ProjectBudgetUsage,
    ProjectClaim,
    ProjectDependency,
    ProjectHypothesis,
    WorkerAssignment,
)


class ProcessWorkerState(StrictRecord):
    """Explicit allowlist, deliberately independent of the broker state envelope."""

    objective: NonEmpty
    plan: tuple[NonEmpty, ...]
    hypotheses: tuple[ProjectHypothesis, ...]
    claims: tuple[ProjectClaim, ...]
    artifact_refs: tuple[ProcessArtifactRef, ...]
    dependencies: tuple[ProjectDependency, ...]
    budget_usage: ProjectBudgetUsage
    worker_assignments: tuple[WorkerAssignment, ...]
    unresolved_risks: tuple[NonEmpty, ...]
    memory_refs: tuple[NonEmpty, ...]
    extension_state: dict[NonEmpty, Any]


class ProcessWorkerObservation(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    state: ProcessWorkerState


class ProcessObservationPolicy(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    use: Literal["worker_observation"] = "worker_observation"
    observation_schema_digest: Sha256
    content_policy_digest: Sha256
    maximum_bytes: Annotated[int, Field(gt=0)] = 1_100_000

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessObservationReceipt(StrictRecord):
    """Privileged linkage and exact public bytes; never serialize this into a prompt."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    observation_id: NonEmpty
    rollout_id: NonEmpty
    state_id: NonEmpty
    state_digest: Sha256
    execution_digest: Sha256
    content_receipt_digest: Sha256
    worker_id: NonEmpty
    lease_token_digest: Sha256
    authorization_digest: Sha256
    authorization_sequence: Annotated[int, Field(ge=0)]
    rights_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    policy: ProcessObservationPolicy
    policy_digest: Sha256
    observation_json: NonEmpty
    observation_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def receipt_is_bound(self) -> ProcessObservationReceipt:
        if self.created_at.tzinfo is None or self.policy.digest != self.policy_digest:
            raise ValueError("observation receipt requires exact policy and timezone-aware time")
        if tuple(sorted(set(self.rights_digests))) != self.rights_digests:
            raise ValueError("observation rights digests must be canonical and unique")
        encoded = self.observation_json.encode("utf-8")
        if (
            len(encoded) > self.policy.maximum_bytes
            or sha256_digest(encoded) != self.observation_digest
        ):
            raise ValueError("observation bytes differ from their declared bound or digest")
        observation = ProcessWorkerObservation.model_validate_json(encoded)
        if canonical_json_bytes(observation) != encoded:
            raise ValueError("observation receipt must retain exact canonical public bytes")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessObservationDecisionBinding(StrictRecord):
    """Declared observation/action lineage; no claim of attested model execution."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    observation_id: NonEmpty
    observation_receipt_digest: Sha256
    observation_digest: Sha256
    decision_id: NonEmpty
    request_digest: Sha256
    decision_digest: Sha256
    disposition: AmberAdmissionDisposition
    declared_role_id: NonEmpty
    declared_worker_model_digest: Sha256
    bound_at: datetime

    @model_validator(mode="after")
    def binding_time_is_aware(self) -> ProcessObservationDecisionBinding:
        if self.bound_at.tzinfo is None:
            raise ValueError("observation decision binding time must be timezone-aware")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
