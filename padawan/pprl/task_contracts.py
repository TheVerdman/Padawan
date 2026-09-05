"""Private finite task ownership; a plan does not establish scientific independence."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class ProcessTask(StrictRecord):
    instance_digest: Sha256
    condition_id: Annotated[str, Field(min_length=1, max_length=128)]
    replication_index: Annotated[int, Field(ge=0)]
    execution_digest: Sha256
    rollout_id: Annotated[str, Field(min_length=1, max_length=192)]
    initial_payload_digest: Sha256

    @property
    def coordinate(self) -> tuple[str, str, int]:
        return self.instance_digest, self.condition_id, self.replication_index


class ProcessTaskPlan(StrictRecord):
    schema_version: Literal["1.0.0"] = "1.0.0"
    plan_id: Annotated[str, Field(pattern=r"^process-task-plan-[0-9a-f]{32}$")]
    authorization_digest: Sha256
    authorization_sequence: Annotated[int, Field(ge=0)]
    authorization_event_digest: Sha256
    resource_grant_digest: Sha256
    tasks: Annotated[tuple[ProcessTask, ...], Field(min_length=1, max_length=128)]
    reviewed_by: NonEmpty
    review_evidence: Annotated[str, Field(min_length=1, max_length=4096)]
    created_at: datetime
    enrollment_expires_at: datetime

    @model_validator(mode="after")
    def bounded(self) -> "ProcessTaskPlan":
        coordinates = tuple(task.coordinate for task in self.tasks)
        if (
            not self.review_evidence.strip()
            or self.created_at.tzinfo is None
            or self.enrollment_expires_at.tzinfo is None
            or not timedelta(0)
            < self.enrollment_expires_at - self.created_at
            <= timedelta(minutes=5)
            or coordinates != tuple(sorted(set(coordinates)))
            or len({task.rollout_id for task in self.tasks}) != len(self.tasks)
            or len({(task.execution_digest, task.replication_index) for task in self.tasks})
            != len(self.tasks)
        ):
            raise ValueError("task plan requires unique canonical ownership and bounded review")
        return self

    @property
    def digest(self) -> str:
        return sha256_digest(self)
