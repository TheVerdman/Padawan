import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from padawan.models.tables import ProcessEventRow, ProcessRecoveryRow, ProcessRolloutRow
from padawan.pprl.contracts import RolloutStatus
from padawan.pprl.store import ProcessInvariantError
from tests.container_helpers import commit_container_action, container_context
from tests.integration.test_postgres_concurrency import postgres_database as postgres_database
from tests.integration.test_process_recovery import expire, recover, recovery, reviewed

pytestmark = pytest.mark.postgres


async def test_postgres_duplicate_recovery_serializes_one_refund_and_fencing(
    postgres_database, tmp_path, pprl_now
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now, enrolled=True)
    await expire(ctx)
    request = reviewed(ctx)
    receipts = await asyncio.wait_for(
        asyncio.gather(*(recover(ctx, request) for _ in range(4))), 10
    )
    assert len({receipt.digest for receipt in receipts}) == 1
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessRecoveryRow)) == 1
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "release" for event in journal) == 1
        assert journal[-1].open_reservations == 0 and journal[-1].charged.actions == 0


@pytest.mark.parametrize("rollback_start", [False, True])
async def test_postgres_dispatch_boundary_race_retains_only_committed_effect_state(
    postgres_database, tmp_path, pprl_now, rollback_start
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now, enrolled=True)
    started = asyncio.Event()
    release_start = asyncio.Event()

    async def dispatch_boundary():
        try:
            async with postgres_database.transaction() as session:
                await ctx.amber.resources.start(
                    session,
                    decision_id=ctx.decision.decision_id,
                    now=pprl_now(),
                    worker_access=ctx.worker_access,
                )
                row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
                row.lease_expires_at = pprl_now() - timedelta(seconds=1)
                started.set()
                await release_start.wait()
                if rollback_start:
                    raise RuntimeError("simulated crash before dispatch transaction commit")
        except RuntimeError:
            assert rollback_start

    dispatch = asyncio.create_task(dispatch_boundary())
    await asyncio.wait_for(started.wait(), 5)
    # If start rolls back, the original lease remains active. The waiting review
    # must fail; only a later expired lease can support a new recovery request.
    attempt = asyncio.create_task(recover(ctx))
    await asyncio.sleep(0.05)
    assert not attempt.done()
    release_start.set()
    await asyncio.wait_for(dispatch, 5)
    if rollback_start:
        with pytest.raises(PermissionError, match="still-active lease"):
            await asyncio.wait_for(attempt, 5)
        await expire(ctx)
        receipt = await recover(ctx)
        assert receipt.effects[0].disposition == "released_unstarted"
    else:
        receipt = await asyncio.wait_for(attempt, 5)
        assert receipt.effects[0].disposition == "unknown"
        assert receipt.effects[0].after_phase == "started"


async def test_postgres_commit_waits_for_recovery_then_rejects_fenced_worker(
    postgres_database, tmp_path, pprl_now
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now, enrolled=True)
    await ctx.service.execute(**ctx.kwargs)
    async with postgres_database.transaction() as session:
        row = await session.scalar(select(ProcessRolloutRow).with_for_update())
        row.paused, row.status = True, RolloutStatus.PAUSED.value
        await session.flush()
        receipt = await recovery(ctx).recover(session, reviewed(ctx), now=pprl_now())
        competing = asyncio.create_task(commit_container_action(ctx))
        await asyncio.sleep(0.05)
        assert not competing.done()
    with pytest.raises(ProcessInvariantError, match="lease is missing or stale"):
        await asyncio.wait_for(competing, 5)
    assert receipt.disposition == "review_required"
    async with postgres_database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "settle" for event in journal) == 1


async def test_postgres_claim_skips_recovery_lock_then_reads_released_admission(
    postgres_database, tmp_path, pprl_now
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now)
    await expire(ctx)

    async def claim():
        async with postgres_database.transaction() as session:
            return await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=pprl_now()
            )

    async with postgres_database.transaction() as session:
        await recovery(ctx).recover(session, reviewed(ctx), now=pprl_now())
        assert await asyncio.wait_for(claim(), 5) is None
    replacement = await claim()
    assert replacement is not None and replacement.state == ctx.claim.state
