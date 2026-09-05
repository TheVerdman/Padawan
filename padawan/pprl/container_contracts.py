"""Privileged contracts for an explicitly composed, local CPU execution boundary."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.artifacts.information import ForensicArtifactRef
from padawan.models.contracts import NonEmpty, Sha256, SourceRights, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest


class ContainerRuntimeIdentity(StrictRecord):
    engine_id: NonEmpty
    server_version: NonEmpty
    kernel_version: NonEmpty
    operating_system: Literal["linux"] = "linux"
    architecture: NonEmpty
    security_options: tuple[NonEmpty, ...]

    @model_validator(mode="after")
    def canonical_options(self) -> ContainerRuntimeIdentity:
        if self.security_options != tuple(sorted(set(self.security_options))):
            raise ValueError("runtime security options must be unique and sorted")
        return self


class ProcessContainerProfile(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    profile_id: NonEmpty
    version: NonEmpty
    role_id: NonEmpty
    worker_model_digest: Sha256
    tool_id: NonEmpty
    tool_digest: Sha256
    tool_operation: NonEmpty
    docker_binary: NonEmpty
    docker_binary_digest: Sha256
    socket_uri: NonEmpty
    runtime: ContainerRuntimeIdentity
    image_id: Sha256
    image_architecture: NonEmpty
    python_executable: NonEmpty
    supervisor_digest: Sha256
    argv: Annotated[tuple[NonEmpty, ...], Field(min_length=1, max_length=128)]
    cpu_millicores: Annotated[int, Field(ge=1, le=64_000)]
    memory_bytes: Annotated[int, Field(ge=16_777_216, le=68_719_476_736)]
    pids_limit: Annotated[int, Field(ge=4, le=4096)]
    tmpfs_bytes: Annotated[int, Field(ge=4096, le=1_073_741_824)]
    maximum_wall_ms: Annotated[int, Field(ge=1, le=86_400_000)]
    maximum_input_bytes: Annotated[int, Field(ge=1, le=7_000_000)]
    maximum_output_bytes: Annotated[int, Field(ge=1, le=16_000_000)]
    maximum_control_bytes: Annotated[int, Field(ge=16_384, le=16_000_000)] = 262_144
    input_projection: Literal["canonical_worker_observation"] = "canonical_worker_observation"
    network: Literal["none"] = "none"
    filesystem_scopes: tuple[Literal["/work"], ...] = ("/work",)
    reviewed_by: NonEmpty
    reviewed_at: datetime
    command_rights: SourceRights

    @model_validator(mode="after")
    def explicit_local_profile(self) -> ProcessContainerProfile:
        if self.reviewed_at.tzinfo is None:
            raise ValueError("container profile requires a timezone-aware review")
        if not self.socket_uri.startswith("unix:///") or any(
            value in self.socket_uri for value in ("\0", "?", "#")
        ):
            raise ValueError("container execution requires an explicit local Unix socket")
        for path in (self.docker_binary, self.python_executable, self.argv[0]):
            if not PurePosixPath(path).is_absolute() or "\0" in path:
                raise ValueError("container executable paths must be absolute")
        if any("\0" in value for value in self.argv):
            raise ValueError("container command contains a NUL")
        if self.filesystem_scopes != ("/work",):
            raise ValueError(
                "container filesystem scope must be exactly its private work directory"
            )
        if "name=seccomp,profile=builtin" not in self.runtime.security_options:
            raise ValueError("container profile requires the declared built-in seccomp boundary")
        if "name=cgroupns" not in self.runtime.security_options:
            raise ValueError("container profile requires cgroup namespace support")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)

    @property
    def maximum_wire_bytes(self) -> int:
        return 2 * self.maximum_input_bytes + 100_000

    @property
    def maximum_retention_bytes(self) -> int:
        return (
            10 * self.maximum_control_bytes
            + 2 * self.maximum_output_bytes
            + self.maximum_wire_bytes
            + len(canonical_json_bytes(self))
            + 65_536
        )


class ProcessContainerWorkload(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    invocation_id: NonEmpty
    container_name: NonEmpty
    decision_id: NonEmpty
    decision_digest: Sha256
    observation_id: NonEmpty
    observation_receipt_digest: Sha256
    rollout_id: NonEmpty
    execution_digest: Sha256
    authorization_digest: Sha256
    lease_token_digest: Sha256
    worker_id: NonEmpty
    profile: ProcessContainerProfile
    input_artifact: ForensicArtifactRef
    wire_digest: Sha256
    deadline: datetime
    created_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> ProcessContainerWorkload:
        if (
            self.created_at.tzinfo is None
            or self.deadline.tzinfo is None
            or self.deadline <= self.created_at
        ):
            raise ValueError("container workload requires an unexpired aware deadline")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class ProcessContainerReceipt(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    invocation_id: NonEmpty
    workload_digest: Sha256
    evidence_artifact: ForensicArtifactRef
    capture_artifacts: tuple[ForensicArtifactRef, ...]
    effect_status: Literal["not_started", "terminated", "unknown"]
    removed: bool
    complete_capture: bool
    finished_at: datetime

    @property
    def digest(self) -> str:
        return sha256_digest(self)
