"""Explicit synthetic plan declarations; no worker or domain outcome is generated."""

from datetime import timedelta
from uuid import uuid4

from padawan.models.hashing import sha256_digest
from padawan.pprl.task_contracts import ProcessTask, ProcessTaskPlan


async def task_plan(session, amber, execution, initial, now, *, count=1):
    authority = execution.amber_authorization_digest
    history = await amber.history(session, authorization_digest=authority)
    grant = await amber.resources.grant(session, authority)
    return ProcessTaskPlan(
        plan_id="process-task-plan-" + uuid4().hex,
        authorization_digest=authority,
        authorization_sequence=history[-1].sequence,
        authorization_event_digest=sha256_digest(history[-1]),
        resource_grant_digest=grant.digest,
        tasks=tuple(
            ProcessTask(
                instance_digest=execution.instance_digest,
                condition_id="synthetic-control",
                replication_index=index,
                execution_digest=sha256_digest(execution),
                rollout_id=f"planned-rollout-{index}",
                initial_payload_digest=sha256_digest(initial),
            )
            for index in range(count)
        ),
        reviewed_by="reviewer-a",
        review_evidence="bounded synthetic ownership; not a scientific preregistration",
        created_at=now,
        enrollment_expires_at=now + timedelta(minutes=1),
    )
