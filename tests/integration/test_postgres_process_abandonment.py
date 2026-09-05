"""Independent broker transactions against a disposable local PostgreSQL database."""

import asyncio

import pytest
from sqlalchemy import func, select

from padawan.governance.amber import AmberStatus
from padawan.models.tables import ProcessAbandonmentRow, ProcessRolloutRow
from padawan.pprl.store import ProcessInvariantError
from tests.container_helpers import commit_container_action
from tests.integration.test_postgres_concurrency import postgres_database as postgres_database
from tests.integration.test_process_abandonment import abandon, review, snapshot, stopped
from tests.integration.test_process_recovery import recover, reviewed

pytestmark = pytest.mark.postgres


async def test_postgres_duplicate_abandonment_changes_head_once_and_never_charges(
    postgres_database, tmp_path, pprl_now
):
    ctx = await stopped(postgres_database, tmp_path, pprl_now)
    request = await review(ctx)
    before = await snapshot(ctx)
    receipts = await asyncio.wait_for(
        asyncio.gather(*(abandon(ctx, request) for _ in range(4))), 15
    )
    assert len({r.digest for r in receipts}) == 1
    after = await snapshot(ctx)
    assert after[6] == before[6] and after[7] == 1


async def test_postgres_competing_review_ids_cannot_replace_terminal_disposition(
    postgres_database, tmp_path, pprl_now
):
    ctx = await stopped(postgres_database, tmp_path, pprl_now)
    first, second = await review(ctx), await review(ctx)
    results = await asyncio.wait_for(
        asyncio.gather(abandon(ctx, first), abandon(ctx, second), return_exceptions=True), 15
    )
    assert sum(isinstance(result, PermissionError) for result in results) == 1
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessAbandonmentRow)) == 1


@pytest.mark.parametrize("competitor", ["commit", "recovery", "pause"])
async def test_postgres_terminal_publication_serializes_with_commit_recovery_and_authority(
    postgres_database, tmp_path, pprl_now, competitor
):
    ctx = await stopped(postgres_database, tmp_path, pprl_now)
    request = await review(ctx)

    async def pause():
        async with postgres_database.transaction() as session:
            return await ctx.amber.transition(
                session,
                authorization_digest=ctx.authorization.digest,
                to_status=AmberStatus.PAUSED,
                actor_id="reviewer-a",
                reason="concurrent fixture stop",
                occurred_at=pprl_now(),
            )

    async with postgres_database.transaction() as session:
        receipt = await ctx.abandonment.abandon(session, request, now=pprl_now())
        if competitor == "commit":
            operation = commit_container_action(ctx)
        elif competitor == "recovery":
            operation = recover(
                ctx, reviewed(ctx).model_copy(update={"expected_lease_token_digest": None})
            )
        else:
            operation = pause()
        task = asyncio.create_task(operation)
        await asyncio.sleep(0.05)
        if competitor != "commit":
            assert not task.done()
        # Recovery already fenced the lease in an earlier transaction. An old
        # commit may reject immediately without matching/locking the rollout row.
    if competitor == "pause":
        await asyncio.wait_for(task, 10)
    else:
        with pytest.raises(ProcessInvariantError if competitor == "commit" else PermissionError):
            await asyncio.wait_for(task, 10)
    async with postgres_database.transaction() as session:
        assert await ctx.abandonment.read(session, abandonment_id=request.abandonment_id) == receipt
        row = await session.get(ProcessRolloutRow, request.rollout_id)
        assert row.status == "cancelled"


async def test_postgres_authority_change_before_review_publication_rejects_without_mutation(
    postgres_database, tmp_path, pprl_now
):
    ctx = await stopped(postgres_database, tmp_path, pprl_now)
    request = await review(ctx)
    async with postgres_database.transaction() as session:
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=AmberStatus.PAUSED,
            actor_id="reviewer-a",
            reason="fixture pause wins",
            occurred_at=pprl_now(),
        )
    before = await snapshot(ctx)
    with pytest.raises(PermissionError):
        await abandon(ctx, request)
    assert await snapshot(ctx) == before
