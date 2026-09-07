"""Finite task ownership under synthetic actions and disposable worker identities."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from padawan.governance.amber import AmberStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ProcessRolloutRow, ProcessTaskPlanRow
from padawan.pprl.abandonment import ProcessAbandonmentStore
from padawan.pprl.content import ProcessContentDeniedError
from padawan.pprl.contracts import ProjectStatePayload
from padawan.pprl.task_contracts import ProcessTaskPlan
from padawan.pprl.tasks import ProcessTaskStore
from padawan.training.contracts import EvidenceSourceKind
from padawan.training.pprl import compile_pprl_snapshot
from tests.container_helpers import container_context
from tests.support.process_abandonment import review as abandonment_review
from tests.support.process_recovery import expire, recover, recovery, reviewed
from tests.support.process_tasks import (
    account,
    create,
    planned_context,
)


async def test_plan_keeps_one_owner_and_one_budget_without_creating_workers(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    before = await account(ctx)
    async with database.transaction() as session:
        assert await ctx.tasks.enroll(session, ctx.plan, now=pprl_now()) == ctx.plan
        root = await create(ctx, session)
        assert await create(ctx, session) == root
        row = await session.get(ProcessRolloutRow, root.rollout_id)
        assert row.task_plan_digest == ctx.plan.digest
        assert await ctx.tasks.check_rollout(session, row) == ctx.plan
    after = await account(ctx)
    assert after.grant_digest == before.grant_digest
    assert after.held == before.held and after.charged == before.charged
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 1
        assert [
            e.kind for e in await ctx.amber.resources.replay(session, ctx.authorization.digest)
        ] == ["grant", "initial"]


@pytest.mark.parametrize(
    "change",
    [
        {"rollout_id": "renamed-owner"},
        {"replication_index": 4},
        {"initial_state": ProjectStatePayload(objective="substituted task")},
        {"parent_rollout_id": "fake-parent", "fork_id": "fake-fork"},
    ],
)
async def test_new_labels_replicas_payloads_and_children_cannot_expand_plan(
    database, tmp_path, pprl_now, change
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    before = await account(ctx)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="task ownership"):
            await create(ctx, session, **change)
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 0
    assert await account(ctx) == before


async def test_changed_execution_cannot_rename_same_task(database, tmp_path, pprl_now):
    ctx = await planned_context(database, tmp_path, pprl_now)
    clone = ctx.execution.model_copy(update={"execution_id": "renamed-execution", "seed": 42})
    async with database.transaction() as session:
        digest = await ctx.process.register_execution(session, clone)
        with pytest.raises(PermissionError, match="task ownership"):
            await create(ctx, session, execution_digest=digest)


async def test_logical_coordinate_and_execution_slot_are_unique_despite_new_rollout_ids(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now, enroll=False)
    task = ctx.plan.tasks[0]
    for changed in (
        task.model_copy(update={"rollout_id": "other", "execution_digest": sha256_digest("other")}),
        task.model_copy(update={"rollout_id": "other", "condition_id": "other-condition"}),
    ):
        with pytest.raises(ValueError, match="canonical ownership"):
            ProcessTaskPlan.model_validate({**ctx.plan.model_dump(), "tasks": (task, changed)})


@pytest.mark.parametrize(
    "field", ["reviewer", "sequence", "authority_event", "grant", "instance", "expired"]
)
async def test_invalid_review_has_no_plan_or_funding_side_effect(
    database, tmp_path, pprl_now, field
):
    ctx = await planned_context(database, tmp_path, pprl_now, enroll=False)
    changes = {
        "reviewer": {"reviewed_by": "unprivileged-worker"},
        "sequence": {"authorization_sequence": 123},
        "authority_event": {"authorization_event_digest": sha256_digest("substitution")},
        "grant": {"resource_grant_digest": sha256_digest("substitution")},
        "instance": {
            "tasks": (
                ctx.plan.tasks[0].model_copy(
                    update={"instance_digest": sha256_digest("substitution")}
                ),
            )
        },
        "expired": {
            "created_at": pprl_now() - timedelta(minutes=3),
            "enrollment_expires_at": pprl_now() - timedelta(minutes=2),
        },
    }[field]
    before = await account(ctx)
    async with database.transaction() as session:
        with pytest.raises((PermissionError, ValueError)):
            await ctx.tasks.enroll(session, ctx.plan.model_copy(update=changes), now=pprl_now())
        assert await session.scalar(select(func.count()).select_from(ProcessTaskPlanRow)) == 0
    assert await account(ctx) == before


async def test_existing_plan_cannot_expand_and_legacy_rollouts_cannot_be_backfilled(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now, enroll=False)
    async with database.transaction() as session:
        await create(ctx, session)
        with pytest.raises(PermissionError, match="precede"):
            await ctx.tasks.enroll(session, ctx.plan, now=pprl_now())
        assert await ctx.tasks.read(session, authorization_digest=ctx.authorization.digest) is None


async def test_enrollment_and_root_publication_respect_outer_rollback(database, tmp_path, pprl_now):
    ctx = await planned_context(database, tmp_path, pprl_now, enroll=False)
    before = await account(ctx)
    with pytest.raises(RuntimeError, match="fixture"):
        async with database.transaction() as session:
            await ctx.tasks.enroll(session, ctx.plan, now=pprl_now())
            await create(ctx, session)
            raise RuntimeError("fixture crash")
    async with database.transaction() as session:
        assert await ctx.tasks.read(session, authorization_digest=ctx.authorization.digest) is None
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 0
    assert await account(ctx) == before


@pytest.mark.parametrize("damage", ["marker", "plan", "digest", "initial", "metadata"])
async def test_damaged_ownership_cannot_fall_back_to_legacy_claim_or_compilation(
    database, tmp_path, pprl_now, damage
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        root = await create(ctx, session)
        if damage == "plan":
            await session.execute(delete(ProcessTaskPlanRow))
        elif damage == "digest":
            await session.execute(
                update(ProcessTaskPlanRow).values(record_digest=sha256_digest("forged"))
            )
        else:
            await session.execute(
                update(ProcessRolloutRow).values(
                    **{
                        "marker": {"task_plan_digest": None},
                        "initial": {"initial_state_id": "absent"},
                        "metadata": {"seed": 999},
                    }[damage]
                )
            )
    async with database.transaction() as session:
        with pytest.raises((ValueError, PermissionError)):
            await ctx.process.get_rollout(session, rollout_id=root.rollout_id)
        with pytest.raises((ValueError, PermissionError)):
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=1), now=pprl_now()
            )
        with pytest.raises((ValueError, PermissionError)):
            await compile_pprl_snapshot(session, as_of=pprl_now())
        row = await session.get(ProcessRolloutRow, root.rollout_id)
        assert row.lease_token is None


async def test_private_plan_references_are_denied_but_private_compiler_retains_lineage(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
        for value in (ctx.plan.plan_id, ctx.plan.digest):
            with pytest.raises(ProcessContentDeniedError):
                await ctx.process.content.check_state(session, ProjectStatePayload(objective=value))
        snapshot = await compile_pprl_snapshot(session, as_of=pprl_now())
        assert any(
            e.source_kind == EvidenceSourceKind.PROCESS_TASK_PLAN for e in snapshot.evidence_rows
        )
        assert all(ctx.plan.digest in e.source_record_digests for e in snapshot.exclusions)
        assert snapshot.rollout_ids == (ctx.plan.tasks[0].rollout_id,)


async def test_complete_worker_replacement_keeps_task_owner_and_refuses_old_capability(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True, planned=True)
    await expire(ctx)
    receipt = await recover(ctx, reviewed(ctx, resume=True))
    assert receipt.disposition == "ready"
    async with database.transaction() as session:
        _, access = await ctx.process.workers.issue(
            session,
            execution_digest=receipt.execution_digest,
            role_id=ctx.profile.role_id,
            worker_model_digest=ctx.profile.worker_model_digest,
            declared_capabilities=ctx.program.worker_roles[0].required_capabilities,
            issued_by="reviewer-a",
            evidence="replacement synthetic worker",
            expires_at=pprl_now() + timedelta(minutes=20),
            now=pprl_now(),
        )
        with pytest.raises(PermissionError):
            await ctx.process.claim_next(
                session,
                worker_id=ctx.worker_id,
                worker_access=ctx.worker_access,
                lease_for=timedelta(minutes=1),
                now=pprl_now(),
            )
        claim = await ctx.process.claim_next(
            session,
            worker_id=access.worker_id,
            worker_access=access,
            lease_for=timedelta(minutes=1),
            now=pprl_now(),
        )
        assert claim.rollout.rollout_id == ctx.rollout.rollout_id
        assert claim.state == ctx.claim.state
        row = await session.get(ProcessRolloutRow, claim.rollout.rollout_id)
        assert await ProcessTaskStore().check_rollout(session, row) == ctx.task_plan
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.held.actions == balance.charged.actions == 0
        assert balance.grant_digest == ctx.task_plan.resource_grant_digest
    assert ctx.driver.calls == []


@pytest.mark.parametrize("completed", [False, True])
async def test_abandoned_task_cannot_be_restarted_by_extra_replica_or_new_plan(
    database, tmp_path, pprl_now, completed
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True, planned=True)
    if completed:
        await ctx.service.execute(**ctx.kwargs)
    else:
        async with database.transaction() as session:
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                now=pprl_now(),
                worker_access=ctx.worker_access,
            )
    await expire(ctx)
    ctx.recovered = await recover(ctx)
    request = await abandonment_review(ctx)
    async with database.transaction() as session:
        await ProcessAbandonmentStore(recovery(ctx)).abandon(session, request, now=pprl_now())
        before = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        root = await ctx.process.create_rollout(
            session,
            execution_digest=sha256_digest(ctx.execution),
            replication_index=0,
            rollout_id=ctx.rollout.rollout_id,
            initial_state=ctx.claim.state.payload,
            created_at=pprl_now(),
        )
        assert root.status.value == "cancelled"
        for index in (0, 1):
            with pytest.raises(PermissionError, match="task ownership"):
                await ctx.process.create_rollout(
                    session,
                    execution_digest=sha256_digest(ctx.execution),
                    replication_index=index,
                    rollout_id="new-name",
                    initial_state=ctx.claim.state.payload,
                    created_at=pprl_now(),
                )
        with pytest.raises(PermissionError, match="cannot be replaced"):
            await ProcessTaskStore().enroll(
                session,
                ctx.task_plan.model_copy(update={"plan_id": "process-task-plan-" + uuid4().hex}),
                now=pprl_now(),
            )
        assert await ctx.amber.resources.inspect(session, ctx.authorization.digest) == before
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 1


async def test_historical_plan_read_and_retry_survive_revocation_without_granting_new_authority(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=AmberStatus.REVOKED,
            actor_id="reviewer-a",
            reason="stop fixture",
            occurred_at=pprl_now(),
        )
        assert (
            await ctx.tasks.enroll(session, ctx.plan, now=ctx.plan.enrollment_expires_at)
            == ctx.plan
        )
        with pytest.raises(PermissionError, match="active Amber"):
            await create(ctx, session)


async def test_fresh_native_broker_reconstructs_task_without_old_objects(
    database, tmp_path, pprl_now
):
    from tests.support.process_restart import run_broker

    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
    recovered = await run_broker(
        {
            "mode": "inspect_task",
            "database_url": database.url,
            "artifact_root": str(ctx.artifacts.root),
            "rollout_id": ctx.plan.tasks[0].rollout_id,
        }
    )
    assert ProcessTaskPlan.model_validate(recovered, strict=False) == ctx.plan


async def test_ordinary_commits_advance_one_task_without_new_root_or_grant(
    database, tmp_path, pprl_now
):
    from tests.pprl_resource_helpers import commit_resource_action, resource_action

    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
    for sequence in (1, 2):
        claim, action = await resource_action(ctx)
        async with database.transaction() as session:
            decision = await ctx.amber.admit(session, request=action, active_workers=0)
        _, state = await commit_resource_action(ctx, claim, action, decision)
        async with database.transaction() as session:
            row = await session.get(ProcessRolloutRow, claim.rollout.rollout_id)
            assert await ctx.tasks.check_rollout(session, row) == ctx.plan
            assert row.current_state_id == state.state_id and row.sequence == sequence
    balance = await account(ctx)
    assert balance.charged.actions == 2 and balance.charged.input_tokens == 80
    assert balance.grant_digest == ctx.plan.resource_grant_digest


async def test_observation_and_dispatch_recheck_damaged_task_membership(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True, planned=True)
    async with database.transaction() as session:
        observed = await ctx.observations.observe_claim(
            session,
            rollout_id=ctx.rollout.rollout_id,
            lease_token=ctx.claim.lease_token,
            worker_id=ctx.worker_id,
            worker_access=ctx.worker_access,
            now=pprl_now(),
        )
        public = observed.observation.model_dump_json()
        assert ctx.task_plan.digest not in public and ctx.task_plan.plan_id not in public
        await session.execute(update(ProcessRolloutRow).values(task_plan_digest=None))
    async with database.transaction() as session:
        with pytest.raises((ValueError, PermissionError)):
            await ctx.observations.observe_claim(
                session,
                rollout_id=ctx.rollout.rollout_id,
                lease_token=ctx.claim.lease_token,
                worker_id=ctx.worker_id,
                worker_access=ctx.worker_access,
                now=pprl_now(),
            )
        with pytest.raises((ValueError, PermissionError)):
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                worker_access=ctx.worker_access,
                now=pprl_now(),
            )
    assert ctx.driver.calls == []


async def test_self_consistent_plan_cannot_substitute_native_source_identities(
    database, tmp_path, pprl_now
):
    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
        forged = ctx.plan.model_copy(
            update={
                "tasks": (
                    ctx.plan.tasks[0].model_copy(
                        update={"instance_digest": sha256_digest("other-instance")}
                    ),
                )
            }
        )
        await session.execute(
            update(ProcessTaskPlanRow).values(
                record_json=forged.model_dump(mode="json"), record_digest=forged.digest
            )
        )
        await session.execute(update(ProcessRolloutRow).values(task_plan_digest=forged.digest))
    async with database.transaction() as session:
        with pytest.raises(ValueError, match="instance"):
            await ctx.tasks.read(session, authorization_digest=ctx.authorization.digest)


async def test_review_expiring_during_validation_rolls_back_plan(
    database, tmp_path, pprl_now, monkeypatch
):
    from datetime import datetime

    from padawan.pprl import tasks

    ctx = await planned_context(database, tmp_path, pprl_now, enroll=False)
    original = ctx.tasks._check_task

    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            return ctx.plan.enrollment_expires_at

    async def checking(*args, **kwargs):
        result = await original(*args, **kwargs)
        monkeypatch.setattr(tasks, "datetime", Expired)
        return result

    monkeypatch.setattr(ctx.tasks, "_check_task", checking)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="expired during"):
            await ctx.tasks.enroll(session, ctx.plan, now=pprl_now())
        assert await session.scalar(select(func.count()).select_from(ProcessTaskPlanRow)) == 0


async def test_populated_task_ownership_cannot_be_downgraded_away(
    database, tmp_path, pprl_now, monkeypatch
):
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    ctx = await planned_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await create(ctx, session)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url)
    monkeypatch.setenv("PADAWAN_DATABASE_URL", database.url)
    await asyncio.to_thread(command.stamp, config, "head")
    with pytest.raises(RuntimeError, match="populated task ownership"):
        await asyncio.to_thread(command.downgrade, config, "a7c82e41d906")
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.plan.tasks[0].rollout_id)
        assert await ctx.tasks.check_rollout(session, row) == ctx.plan
