"""One finite, reviewed task namespace per original shared funding authority.

The rollout owns the entire task. Ordinary commits advance it; replacement does
not create a new owner. Unresolved effects still require existing recovery and
terminal disposition. There is no semantic deduplicator or scheduling loop here.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.governance.amber import AmberStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAuthorizationHeadRow,
    ProcessRolloutRow,
    ProcessTaskPlanRow,
    ProjectInstanceRow,
)
from padawan.pprl.contracts import ProcessExecutionManifest, ProjectInstance
from padawan.pprl.resources import atomic_resource_write
from padawan.pprl.task_contracts import ProcessTask, ProcessTaskPlan


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ProcessTaskStore:
    async def read(
        self, session: AsyncSession, *, authorization_digest: str
    ) -> ProcessTaskPlan | None:
        from padawan.governance.amber_store import AmberStore

        row = await session.get(ProcessTaskPlanRow, authorization_digest)
        if row is None:
            if (
                await session.scalar(
                    select(ProcessRolloutRow.rollout_id)
                    .where(
                        ProcessRolloutRow.authorization_digest == authorization_digest,
                        ProcessRolloutRow.task_plan_digest.is_not(None),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise ValueError("task ownership lost its retained plan")
            return None  # explicitly legacy authorization, not implicit enrollment
        plan = ProcessTaskPlan.model_validate(row.record_json, strict=False)
        if (
            sha256_digest(row.record_json) != row.record_digest
            or plan.digest != row.record_digest
            or plan.authorization_digest != authorization_digest
            or plan.plan_id != row.plan_id
            or plan.created_at != _utc(row.created_at)
        ):
            raise ValueError("task ownership plan is corrupt")
        amber = AmberStore()
        envelope = await amber.get(session, authorization_digest=authorization_digest)
        grant = await amber.resources.grant(session, authorization_digest)
        history = await amber.history(session, authorization_digest=authorization_digest)
        sequence = plan.authorization_sequence
        if (
            plan.reviewed_by not in envelope.required_reviewers
            or plan.resource_grant_digest != grant.digest
            or grant.created_at > plan.created_at
            or sequence >= len(history)
            or any(
                event.sequence != i or (i and event.from_status != history[i - 1].to_status)
                for i, event in enumerate(history)
            )
            or sha256_digest(history[sequence]) != plan.authorization_event_digest
            or history[sequence].to_status != AmberStatus.ACTIVE
            or history[sequence].created_at > plan.created_at
            or (sequence + 1 < len(history) and history[sequence + 1].created_at <= plan.created_at)
            or plan.created_at >= envelope.expires_at
        ):
            raise ValueError("task plan lost its original review and funding authority")
        for task in plan.tasks:
            await self._check_task(session, plan, task)
        return plan

    async def _check_task(
        self, session: AsyncSession, plan: ProcessTaskPlan, task: ProcessTask
    ) -> tuple[ProcessExecutionManifest, ProjectInstance]:
        from padawan.pprl.worker_identities import ProcessWorkerIdentityStore

        execution, _, envelope = await ProcessWorkerIdentityStore()._context(
            session, task.execution_digest, now=plan.created_at, active=False
        )
        if (
            execution.instance_digest != task.instance_digest
            or envelope.digest != plan.authorization_digest
        ):
            raise ValueError("task plan substitutes an execution or instance")
        row = await session.scalar(
            select(ProjectInstanceRow).where(
                ProjectInstanceRow.instance_digest == task.instance_digest
            )
        )
        if row is None:
            raise ValueError("task plan lost its exact domain instance")
        instance = ProjectInstance.model_validate(row.record_json, strict=False)
        if (
            sha256_digest(row.record_json) != task.instance_digest
            or sha256_digest(instance) != task.instance_digest
            or instance.instance_id != row.instance_id
            or instance.distribution_digest != execution.distribution_digest
            or instance.created_at > plan.created_at
            or instance.split not in envelope.allowed_splits
        ):
            raise ValueError("task plan substitutes its declared domain instance")
        return execution, instance

    @atomic_resource_write
    async def enroll(
        self, session: AsyncSession, plan: ProcessTaskPlan, *, now: datetime
    ) -> ProcessTaskPlan:
        plan = ProcessTaskPlan.model_validate_json(plan.model_dump_json())
        head = await session.scalar(
            select(AmberAuthorizationHeadRow)
            .where(AmberAuthorizationHeadRow.authorization_digest == plan.authorization_digest)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        existing = await self.read(session, authorization_digest=plan.authorization_digest)
        if existing is not None:
            if existing != plan:
                raise PermissionError("task plan cannot be replaced or expanded")
            return existing  # historical retry; no new root or fresh execution authority
        if now.tzinfo is None:
            raise ValueError("task enrollment requires an aware clock")
        now = max(now, datetime.now(UTC))
        if (
            now.tzinfo is None
            or not plan.created_at <= now < plan.enrollment_expires_at
            or head is None
            or head.status != AmberStatus.ACTIVE.value
            or head.sequence != plan.authorization_sequence
            or not _utc(head.updated_at) <= plan.created_at
        ):
            raise PermissionError("task enrollment requires fresh current reviewed authority")
        from padawan.governance.amber_store import AmberStore

        envelope = await AmberStore().get(session, authorization_digest=plan.authorization_digest)
        if now >= envelope.expires_at:
            raise PermissionError("task enrollment authority has expired")
        if (
            await session.scalar(
                select(ProcessRolloutRow.rollout_id)
                .where(ProcessRolloutRow.authorization_digest == plan.authorization_digest)
                .limit(1)
            )
            is not None
        ):
            raise PermissionError("task enrollment must precede every rollout in its authority")
        session.add(
            ProcessTaskPlanRow(
                authorization_digest=plan.authorization_digest,
                plan_id=plan.plan_id,
                record_digest=plan.digest,
                record_json=plan.model_dump(mode="json"),
                created_at=plan.created_at,
            )
        )
        await session.flush()
        checked = await self.read(session, authorization_digest=plan.authorization_digest)
        assert checked is not None
        if max(now, datetime.now(UTC)) >= min(plan.enrollment_expires_at, envelope.expires_at):
            raise PermissionError("task enrollment review expired during validation")
        return checked

    async def creation(
        self,
        session: AsyncSession,
        *,
        authorization_digest: str,
        execution_digest: str,
        rollout_id: str,
        replication_index: int,
        initial_payload_digest: str,
        parent_rollout_id: str | None,
        now: datetime,
    ) -> str | None:
        plan = await self.read(session, authorization_digest=authorization_digest)
        if plan is None:
            return None
        self._creation(
            plan,
            execution_digest=execution_digest,
            rollout_id=rollout_id,
            replication_index=replication_index,
            initial_payload_digest=initial_payload_digest,
            parent_rollout_id=parent_rollout_id,
            now=now,
        )
        return plan.digest

    @staticmethod
    def _creation(
        plan: ProcessTaskPlan,
        *,
        execution_digest: str,
        rollout_id: str,
        replication_index: int,
        initial_payload_digest: str,
        parent_rollout_id: str | None,
        now: datetime,
    ) -> None:
        task = next((task for task in plan.tasks if task.rollout_id == rollout_id), None)
        if (
            task is None
            or parent_rollout_id is not None
            or now < plan.created_at
            or task.execution_digest != execution_digest
            or task.replication_index != replication_index
            or task.initial_payload_digest != initial_payload_digest
        ):
            raise PermissionError("rollout is outside its exact reviewed task ownership")

    async def check_rollout(
        self, session: AsyncSession, rollout: ProcessRolloutRow
    ) -> ProcessTaskPlan | None:
        from padawan.models.tables import ProcessStateRow
        from padawan.pprl.store import _state_from_row

        plan = await self.read(session, authorization_digest=rollout.authorization_digest)
        if plan is None:
            if rollout.task_plan_digest is not None:
                raise ValueError("rollout lost its task ownership")
            return None
        initial = await session.get(ProcessStateRow, rollout.initial_state_id)
        if initial is None:
            raise ValueError("task ownership lost its initial state")
        state = _state_from_row(initial)
        self._creation(
            plan,
            execution_digest=rollout.execution_digest,
            rollout_id=rollout.rollout_id,
            replication_index=rollout.replication_index,
            initial_payload_digest=sha256_digest(state.payload),
            parent_rollout_id=rollout.parent_rollout_id,
            now=_utc(rollout.created_at),
        )
        if (
            plan.digest != rollout.task_plan_digest
            or state.rollout_id != rollout.rollout_id
            or state.sequence != 0
            or rollout.fork_id is not None
        ):
            raise ValueError("rollout differs from its immutable task owner")
        task = next(task for task in plan.tasks if task.rollout_id == rollout.rollout_id)
        execution, instance = await self._check_task(session, plan, task)
        if (
            rollout.instance_id != instance.instance_id
            or rollout.split != instance.split.value
            or rollout.program_digest != execution.program_digest
            or rollout.distribution_digest != execution.distribution_digest
            or rollout.seed != execution.seed
        ):
            raise ValueError("task owner substitutes its execution metadata")
        return plan
