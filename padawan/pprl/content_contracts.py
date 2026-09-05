from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class ProcessContentSurface(StrEnum):
    STATE_EXTENSION = "state_extension"
    EVENT = "event"
    FORK_INTERVENTION = "fork_intervention"


class ProcessContentSchema(StrictRecord):
    namespace: NonEmpty
    version: NonEmpty
    surface: ProcessContentSurface
    json_schema: dict[str, Any]
    schema_digest: Sha256

    @model_validator(mode="after")
    def schema_is_bound(self) -> ProcessContentSchema:
        if sha256_digest(self.json_schema) != self.schema_digest:
            raise ValueError("process content schema digest differs from its definition")
        return self


class ProcessContentPolicy(StrictRecord):
    """Broker-owned structural policy; this is not a semantic safety attestation."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    policy_id: NonEmpty
    version: NonEmpty
    core_schema_digest: Sha256
    schemas: tuple[ProcessContentSchema, ...] = ()
    maximum_state_bytes: Annotated[int, Field(gt=0)] = 1_048_576
    maximum_event_bytes: Annotated[int, Field(gt=0)] = 262_144
    maximum_depth: Annotated[int, Field(ge=1, le=64)] = 16
    maximum_nodes: Annotated[int, Field(gt=0)] = 16_384
    maximum_string_bytes: Annotated[int, Field(gt=0)] = 65_536
    maximum_digest_candidates: Annotated[int, Field(gt=0)] = 4_096

    @model_validator(mode="after")
    def schemas_are_canonical(self) -> ProcessContentPolicy:
        identities = tuple((schema.surface, schema.namespace) for schema in self.schemas)
        if tuple(sorted(set(identities))) != identities:
            raise ValueError("process content schemas require unique canonical surface/namespaces")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessContentAdmission(StrictRecord):
    """Privileged structural receipt, separate from the historical state/event envelope."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    record_kind: Literal["state", "event"]
    record_id: NonEmpty
    record_digest: Sha256
    execution_digest: Sha256
    policy: ProcessContentPolicy
    policy_digest: Sha256
    admitted_at: datetime

    @model_validator(mode="after")
    def admission_is_bound(self) -> ProcessContentAdmission:
        if self.admitted_at.tzinfo is None or self.policy.digest != self.policy_digest:
            raise ValueError("content admission requires exact policy and timezone-aware time")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
