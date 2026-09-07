"""Shared process abandonment fixture setup."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from padawan.models.tables import (
    ArtifactReferenceRow,
    ProcessAbandonmentRow,
    ProcessRolloutRow,
)
from padawan.pprl.abandonment import ProcessAbandonmentStore
from padawan.pprl.abandonment_contracts import ProcessAbandonmentRequest
from tests.container_helpers import container_context
from tests.support.process_recovery import expire, recover, recovery


async def stopped(database, tmp_path, clock, *, completed=True):
    ctx = await container_context(database, tmp_path, clock, enrolled=True)
    if completed:
        await ctx.service.execute(**ctx.kwargs)
    else:
        async with database.transaction() as session:
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                now=clock(),
                worker_access=ctx.worker_access,
            )
    await expire(ctx)
    ctx.recovered = await recover(ctx)
    ctx.abandonment = ProcessAbandonmentStore(recovery(ctx))
    return ctx


async def review(ctx, **changes):
    async with ctx.database.transaction() as session:
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        history = await ctx.amber.history(session, authorization_digest=ctx.authorization.digest)
    instant = ctx.clock()
    data = dict(
        abandonment_id="process-abandonment-" + uuid4().hex,
        rollout_id=ctx.recovered.request.rollout_id,
        recovery_id=ctx.recovered.request.recovery_id,
        recovery_digest=ctx.recovered.digest,
        expected_state_digest=ctx.recovered.state_digest,
        expected_authorization_sequence=history[-1].sequence,
        expected_account_digest=account.digest,
        reviewer_id="reviewer-a",
        reason="synthetic infrastructure loss; do not infer a domain result",
        exclusion="unresolved_external_effect"
        if any(e.disposition == "unknown" for e in ctx.recovered.effects)
        else "infrastructure_interruption",
        reviewed_at=instant,
        expires_at=instant + timedelta(minutes=1),
    )
    return ProcessAbandonmentRequest.model_validate({**data, **changes})


async def abandon(ctx, request):
    async with ctx.database.transaction() as session:
        return await ctx.abandonment.abandon(session, request, now=ctx.clock())


async def snapshot(ctx):
    async with ctx.database.transaction() as session:
        head = await session.get(ProcessRolloutRow, ctx.recovered.request.rollout_id)
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        return (
            head.current_state_id,
            head.sequence,
            head.status,
            head.paused,
            head.terminal_abandonment_id,
            head.updated_at,
            account.digest,
            await session.scalar(select(func.count()).select_from(ProcessAbandonmentRow)),
            tuple(
                (r.artifact_id, r.owner_type, r.owner_id)
                for r in await session.scalars(
                    select(ArtifactReferenceRow).order_by(
                        ArtifactReferenceRow.artifact_id,
                        ArtifactReferenceRow.owner_type,
                        ArtifactReferenceRow.owner_id,
                    )
                )
            ),
        )
