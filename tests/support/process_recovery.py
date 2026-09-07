"""Shared process recovery fixture setup."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, update

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ProcessRecoveryRow,
    ProcessRolloutRow,
)
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.recovery_contracts import ProcessRecoveryRequest


def reviewed(ctx, **updates):
    return ProcessRecoveryRequest.model_validate(
        dict(
            recovery_id="process-recovery-" + uuid4().hex,
            rollout_id=ctx.claim.rollout.rollout_id,
            expected_state_digest=ctx.claim.state.state_digest,
            expected_lease_token_digest=sha256_digest(ctx.claim.lease_token),
            reviewer_id="reviewer-a",
            reason="reviewed disposable crash fixture; no scientific admission",
            reviewed_at=ctx.clock(),
            **updates,
        )
    )


def recovery(ctx):
    return ProcessRecoveryStore(ctx.process, ctx.records.catalog)


async def expire(ctx):
    async with ctx.database.transaction() as session:
        await session.execute(
            update(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == ctx.claim.rollout.rollout_id)
            .values(lease_expires_at=ctx.clock() - timedelta(seconds=1))
        )


async def recover(ctx, request=None):
    request = request or reviewed(ctx)
    async with ctx.database.transaction() as session:
        return await recovery(ctx).recover(session, request, now=ctx.clock())


async def snapshot(ctx):
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        return (
            row.current_state_id,
            row.sequence,
            row.lease_token,
            row.lease_owner,
            row.lease_expires_at,
            row.status,
            row.paused,
            account.digest,
            await session.scalar(select(func.count()).select_from(ProcessRecoveryRow)),
        )
