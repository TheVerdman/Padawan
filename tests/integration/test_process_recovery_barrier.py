from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ProcessResourceReservationHeadRow,
    ProcessResourceReservationRow,
    ProcessRolloutRow,
)
from tests.container_helpers import commit_container_action, container_context


@pytest.mark.parametrize("phase", ["reserved", "started", "settled"])
@pytest.mark.parametrize("enrolled", [False, True])
async def test_expired_lease_does_not_erase_an_uncommitted_effect(
    database, tmp_path, pprl_now, phase, enrolled
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=enrolled)
    if phase == "settled":
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        if phase == "started":
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                now=pprl_now(),
                worker_access=ctx.worker_access,
            )
        await session.execute(
            update(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == ctx.claim.rollout.rollout_id)
            .values(lease_expires_at=pprl_now() - timedelta(seconds=1))
        )
    access = None
    if ctx.worker_access is not None:
        from dataclasses import replace

        access = replace(ctx.worker_access, assignment_id=None)
    async with database.transaction() as session:
        replacement = await ctx.process.claim_next(
            session,
            worker_id=ctx.worker_id,
            lease_for=timedelta(minutes=5),
            worker_access=access,
            now=pprl_now(),
        )
        assert replacement is None


async def test_exact_reviewed_unstarted_release_resolves_the_barrier(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await ctx.amber.resources.release_unstarted(
            session,
            decision_id=ctx.decision.decision_id,
            reviewer_id="reviewer-a",
            evidence="verified no dispatch in this fixture",
            now=pprl_now(),
        )
        await session.execute(
            update(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == ctx.claim.rollout.rollout_id)
            .values(lease_expires_at=pprl_now() - timedelta(seconds=1))
        )
    async with database.transaction() as session:
        assert (
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=pprl_now()
            )
            is not None
        )


async def test_committed_container_transition_allows_next_claim(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now)
    await ctx.service.execute(**ctx.kwargs)
    _, state = await commit_container_action(ctx)
    async with database.transaction() as session:
        claim = await ctx.process.claim_next(
            session, worker_id="replacement", lease_for=timedelta(minutes=5), now=pprl_now()
        )
        assert claim is not None and claim.state == state


async def test_forged_released_head_rolls_back_claim_before_assignment(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        head = await session.get(ProcessResourceReservationHeadRow, ctx.decision.decision_id)
        head.status = "released"  # mutable head is not evidence of a reviewed release
        row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
        row.lease_expires_at = pprl_now() - timedelta(seconds=1)
    async with database.transaction() as session:
        with pytest.raises(ValueError, match="phase differs"):
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=pprl_now()
            )
        token = await session.scalar(select(ProcessRolloutRow.lease_token))
        assert token == ctx.claim.lease_token


async def test_spare_global_capacity_does_not_admit_a_second_uncommitted_effect(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now)
    await ctx.service.execute(**ctx.kwargs)
    # Model a permissive legacy claimant. Direct admission must enforce its own
    # barrier, independent of the claim API and the now-open global resource slot.
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
        row.lease_token = "test-replacement-lease"
        row.lease_owner = "replacement"
        row.lease_expires_at = pprl_now() + timedelta(minutes=5)
        await session.flush()
        action = ctx.action.model_copy(
            update={
                "lease_token_digest": sha256_digest(row.lease_token),
                "requested_at": pprl_now(),
            }
        )
        decision = await ctx.amber.admit(session, request=action, active_workers=0)
        assert "rollout_recovery_required" in decision.reason_codes
        assert "shared_resource_capacity_exhausted" not in decision.reason_codes
        assert "shared_resource_concurrency_exhausted" not in decision.reason_codes


@pytest.mark.parametrize("missing", ["head", "reservation"])
async def test_missing_accounting_record_cannot_erase_admitted_effect(
    database, tmp_path, pprl_now, missing
):
    from tests.support.process_recovery import expire, recover, snapshot

    ctx = await container_context(database, tmp_path, pprl_now)
    await expire(ctx)
    async with database.transaction() as session:
        await session.execute(delete(ProcessResourceReservationHeadRow))
        if missing == "reservation":
            await session.execute(delete(ProcessResourceReservationRow))
    before = await snapshot(ctx)
    async with database.transaction() as session:
        assert (
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=pprl_now()
            )
            is None
        )
    with pytest.raises((ValueError, PermissionError)):
        await recover(ctx)
    assert await snapshot(ctx) == before
