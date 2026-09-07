"""Shared process effects fixture setup."""

from types import SimpleNamespace


async def model_context(ready):
    ctx = ready.ctx
    async with ctx.database.transaction() as session:
        authorization = await ctx.amber.get(
            session, authorization_digest=ctx.execution.amber_authorization_digest
        )
    return SimpleNamespace(
        database=ctx.database,
        process=ctx.process,
        amber=ctx.amber,
        authorization=authorization,
        claim=ready.claimed,
        clock=ctx.clock,
        records=SimpleNamespace(catalog=ready.external.catalog),
    )
