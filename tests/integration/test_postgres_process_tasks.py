"""Task enrollment serializes with the original authorization and root writes."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from padawan.models.tables import ProcessRolloutRow, ProcessTaskPlanRow
from tests.support.postgres import postgres_database as postgres_database
from tests.support.process_tasks import create, planned_context

pytestmark = pytest.mark.postgres


async def test_postgres_duplicate_and_competing_plans_cannot_create_two_namespaces(
    postgres_database, tmp_path, pprl_now
):
    ctx = await planned_context(postgres_database, tmp_path, pprl_now, enroll=False)

    async def enroll(plan):
        async with postgres_database.transaction() as session:
            return await ctx.tasks.enroll(session, plan, now=pprl_now())

    result = await asyncio.wait_for(asyncio.gather(*(enroll(ctx.plan) for _ in range(4))), 15)
    assert all(plan == ctx.plan for plan in result)
    with pytest.raises(PermissionError, match="cannot be replaced"):
        await enroll(ctx.plan.model_copy(update={"plan_id": "process-task-plan-" + uuid4().hex}))
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessTaskPlanRow)) == 1


@pytest.mark.parametrize("planned", [True, False])
async def test_postgres_root_waits_for_enrollment_and_cannot_pass_as_legacy(
    postgres_database, tmp_path, pprl_now, planned
):
    ctx = await planned_context(postgres_database, tmp_path, pprl_now, enroll=False)

    async def root():
        async with postgres_database.transaction() as session:
            return await create(ctx, session, **({} if planned else {"rollout_id": "unlisted"}))

    async with postgres_database.transaction() as session:
        await ctx.tasks.enroll(session, ctx.plan, now=pprl_now())
        competitor = asyncio.create_task(root())
        await asyncio.sleep(0.05)
        assert not competitor.done()
    if planned:
        rollout = await asyncio.wait_for(competitor, 10)
        assert rollout.rollout_id == ctx.plan.tasks[0].rollout_id
    else:
        with pytest.raises(PermissionError, match="task ownership"):
            await asyncio.wait_for(competitor, 10)


async def test_postgres_enrollment_waits_for_existing_legacy_root_then_refuses_backfill(
    postgres_database, tmp_path, pprl_now
):
    ctx = await planned_context(postgres_database, tmp_path, pprl_now, enroll=False)

    async def enroll():
        async with postgres_database.transaction() as session:
            return await ctx.tasks.enroll(session, ctx.plan, now=pprl_now())

    async with postgres_database.transaction() as session:
        await create(ctx, session)
        competitor = asyncio.create_task(enroll())
        await asyncio.sleep(0.05)
        assert not competitor.done()
    with pytest.raises(PermissionError, match="precede"):
        await asyncio.wait_for(competitor, 10)
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessTaskPlanRow)) == 0


async def test_postgres_concurrent_root_creation_charges_initial_state_once(
    postgres_database, tmp_path, pprl_now
):
    ctx = await planned_context(postgres_database, tmp_path, pprl_now)

    async def root():
        async with postgres_database.transaction() as session:
            return await create(ctx, session)

    result = await asyncio.wait_for(asyncio.gather(*(root() for _ in range(3))), 15)
    assert all(item == result[0] for item in result)
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 1
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(item.kind == "initial" for item in journal) == 1
