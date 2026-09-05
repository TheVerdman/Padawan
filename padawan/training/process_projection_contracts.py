"""Public institutional learning payloads and separate privileged projection receipts."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, FiniteFloat, model_validator

from padawan.artifacts.information import ForensicArtifactRef, ProcessArtifactRef
from padawan.models.contracts import (
    ArtifactRef,
    NonEmpty,
    Sha256,
    StrictRecord,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.pprl.contracts import NonNegativeFinite, OutcomeDisposition, ProcessEventKind
from padawan.pprl.observation_contracts import ProcessWorkerObservation, ProcessWorkerState

ProcessProjectionKind = Literal["pprl_trajectory", "pprl_verifiable", "pprl_fork_preference"]


class ProcessLearningOutcomeComponent(StrictRecord):
    component_id: NonEmpty
    disposition: OutcomeDisposition
    deterministic: bool
    value: FiniteFloat | None
    uncertainty: NonNegativeFinite | None


class ProcessLearningOutcome(StrictRecord):
    components: Annotated[tuple[ProcessLearningOutcomeComponent, ...], Field(min_length=1)]
    scalar_return: FiniteFloat | None
    preference_rank: Annotated[int, Field(ge=1)] | None


class ProcessLearningStep(StrictRecord):
    observation: ProcessWorkerObservation
    event_kind: ProcessEventKind
    public_action: dict[str, Any]
    artifact_refs: tuple[ProcessArtifactRef, ...]
    resulting_state: ProcessWorkerState


class ProcessTrajectoryPayload(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    initial_state: ProcessWorkerState
    steps: Annotated[tuple[ProcessLearningStep, ...], Field(min_length=1)]
    outcomes: Annotated[tuple[ProcessLearningOutcome, ...], Field(min_length=1)]


class ProcessVerifiablePayload(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    task: dict[str, Any]
    steps: Annotated[tuple[ProcessLearningStep, ...], Field(min_length=1)]
    outcomes: Annotated[tuple[ProcessLearningOutcome, ...], Field(min_length=1)]


class ProcessForkPreferencePayload(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    chosen: ProcessTrajectoryPayload
    rejected: ProcessTrajectoryPayload


class ProcessTrainingTaskSchema(StrictRecord):
    generator_digest: Sha256
    namespace: NonEmpty
    version: NonEmpty
    json_schema: dict[str, Any]
    schema_digest: Sha256

    @model_validator(mode="after")
    def schema_is_bound(self) -> ProcessTrainingTaskSchema:
        if sha256_digest(self.json_schema) != self.schema_digest:
            raise ValueError("training task schema digest is inconsistent")
        return self


class ProcessTrainingProjectionPolicy(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    use: Literal["institutional_training_projection"] = "institutional_training_projection"
    content_policy_digest: Sha256
    observation_policy_digest: Sha256
    public_schema_digest: Sha256
    task_schemas: tuple[ProcessTrainingTaskSchema, ...] = ()
    maximum_rollout_steps: Annotated[int, Field(gt=0)] = 1024
    maximum_examples: Annotated[int, Field(gt=0)] = 4096
    maximum_product_bytes: Annotated[int, Field(gt=0)] = 16_777_216
    maximum_source_artifact_bytes: Annotated[int, Field(gt=0)] = 268_435_456

    @model_validator(mode="after")
    def tasks_are_canonical(self) -> ProcessTrainingProjectionPolicy:
        identities = tuple(schema.generator_digest for schema in self.task_schemas)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("training task generators must be unique and canonical")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessProjectionSource(StrictRecord):
    """Private source and authority joins, never part of public JSONL."""

    rollout_id: NonEmpty
    execution_digest: Sha256
    distribution_digest: Sha256
    instance_id: NonEmpty
    authorization_digest: Sha256
    authorization_sequence: Annotated[int, Field(ge=0)]
    archive_row_digest: Sha256
    source_record_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    content_receipt_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    observation_receipt_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    observation_binding_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    rights_digests: Annotated[tuple[Sha256, ...], Field(min_length=1)]
    evidence_admission_digests: tuple[Sha256, ...]
    artifact_refs: tuple[ProcessArtifactRef, ...]
    forensic_artifacts: tuple[ForensicArtifactRef, ...]


class ProcessProjectionRowLink(StrictRecord):
    row_index: Annotated[int, Field(ge=0)]
    public_row_digest: Sha256
    source_rollout_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    archive_row_digest: Sha256


class ProcessProjectionProduct(StrictRecord):
    kind: ProcessProjectionKind
    public_jsonl: str
    content_digest: Sha256
    rows: tuple[ProcessProjectionRowLink, ...]

    @model_validator(mode="after")
    def bytes_and_rows_are_bound(self) -> ProcessProjectionProduct:
        data = self.public_jsonl.encode("utf-8")
        if sha256_digest(data) != self.content_digest:
            raise ValueError("projection product digest is inconsistent")
        lines = data.splitlines()
        if data and not data.endswith(b"\n"):
            raise ValueError("projection JSONL must end in a newline")
        if tuple(row.row_index for row in self.rows) != tuple(range(len(lines))):
            raise ValueError("projection rows must bind every line exactly once")
        if any(
            sha256_digest(line) != row.public_row_digest
            for line, row in zip(lines, self.rows, strict=True)
        ):
            raise ValueError("projection row bytes differ from private lineage")
        models: dict[ProcessProjectionKind, type[StrictRecord]] = {
            "pprl_trajectory": ProcessTrajectoryPayload,
            "pprl_verifiable": ProcessVerifiablePayload,
            "pprl_fork_preference": ProcessForkPreferencePayload,
        }
        model = models[self.kind]
        if any(canonical_json_bytes(model.model_validate_json(line)) != line for line in lines):
            raise ValueError("projection public bytes must use their exact canonical schema")
        return self


class ProcessProjectionExclusion(StrictRecord):
    """Private failure evidence; no rejected content is returned to a learner."""

    candidate_id: NonEmpty
    kind: ProcessProjectionKind
    reason: Literal[
        "archive_ineligible",
        "projection_not_admitted",
        "task_schema_not_admitted",
        "replication_insufficient",
        "fork_pair_not_admitted",
        "projection_bounds_exceeded",
    ]


class ProcessTrainingProjectionReceipt(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    projection_id: NonEmpty
    source_bundle_id: NonEmpty
    source_manifest_digest: Sha256
    source_snapshot_digest: Sha256
    source_as_of: datetime
    created_at: datetime
    policy: ProcessTrainingProjectionPolicy
    policy_digest: Sha256
    parameter_training_ready: Literal[False] = False
    sources: tuple[ProcessProjectionSource, ...]
    products: Annotated[tuple[ProcessProjectionProduct, ...], Field(min_length=3, max_length=3)]
    exclusions: tuple[ProcessProjectionExclusion, ...]
    archive_artifacts: tuple[ArtifactRef, ...]

    @model_validator(mode="after")
    def receipt_is_bound(self) -> ProcessTrainingProjectionReceipt:
        if (
            self.created_at.tzinfo is None
            or self.source_as_of.tzinfo is None
            or self.created_at < self.source_as_of
        ):
            raise ValueError("projection times must be aware and follow the source snapshot")
        if self.policy.digest != self.policy_digest:
            raise ValueError("projection policy is inconsistent")
        kinds = tuple(product.kind for product in self.products)
        if kinds != ("pprl_fork_preference", "pprl_trajectory", "pprl_verifiable"):
            raise ValueError("projection must retain each product in canonical order")
        if any(
            len(product.public_jsonl.encode("utf-8")) > self.policy.maximum_product_bytes
            or len(product.rows) > self.policy.maximum_examples
            for product in self.products
        ):
            raise ValueError("projection products exceed policy bounds")
        source_ids = tuple(source.rollout_id for source in self.sources)
        if source_ids != tuple(sorted(set(source_ids))):
            raise ValueError("projection source rollouts must be unique and canonical")
        if any(
            not set(row.source_rollout_ids).issubset(source_ids)
            for product in self.products
            for row in product.rows
        ):
            raise ValueError("projection row lacks its retained source context")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
