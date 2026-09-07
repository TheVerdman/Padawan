"""Shared process tasks fixture setup."""

from padawan.pprl.contracts import ProjectStatePayload
from padawan.pprl.tasks import ProcessTaskStore
from tests.pprl_resource_helpers import resource_context
from tests.pprl_task_helpers import task_plan


async def planned_context(database, tmp_path, clock, *, enroll=True, count=1):
    ctx = await resource_context(database, tmp_path, clock, rollouts=0)
    ctx.initial = ProjectStatePayload(objective="one stable synthetic task")
    ctx.tasks = ProcessTaskStore()
    async with database.transaction() as session:
        ctx.plan = await task_plan(
            session, ctx.amber, ctx.execution, ctx.initial, clock(), count=count
        )
        if enroll:
            await ctx.tasks.enroll(session, ctx.plan, now=clock())
    return ctx


async def create(ctx, session, index=0, **changes):
    task = ctx.plan.tasks[index]
    return await ctx.process.create_rollout(
        session,
        **{
            "execution_digest": task.execution_digest,
            "replication_index": task.replication_index,
            "initial_state": ctx.initial,
            "rollout_id": task.rollout_id,
            "created_at": ctx.clock(),
            **changes,
        },
    )


async def account(ctx):
    async with ctx.database.transaction() as session:
        return await ctx.amber.resources.inspect(session, ctx.authorization.digest)
