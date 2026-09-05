import asyncio

import pytest

from padawan.pprl.containers import ProcessContainerExecutor, ProcessContainerUnavailableError
from tests.container_helpers import SyntheticContainerDriver, container_context
from tests.integration.test_postgres_concurrency import postgres_database as postgres_database

pytestmark = pytest.mark.postgres


async def test_postgres_independent_container_brokers_dispatch_once(
    postgres_database, tmp_path, pprl_now
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now)
    drivers = [SyntheticContainerDriver(ctx.profile, pprl_now) for _ in range(4)]
    executors = [
        ProcessContainerExecutor(
            database=postgres_database,
            observations=ctx.observations,
            store=ctx.records,
            driver=driver,
        )
        for driver in drivers
    ]
    outcomes = await asyncio.gather(
        *(executor.execute(**ctx.kwargs) for executor in executors), return_exceptions=True
    )
    assert sum(isinstance(value, ProcessContainerUnavailableError) for value in outcomes) == 3
    assert sum(len(driver.calls) for driver in drivers) == 1
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    async with postgres_database.transaction() as session:
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert journal[-1].charged.actions == 1 and journal[-1].open_reservations == 0


async def test_postgres_container_reconciliation_retries_charge_once(
    postgres_database, tmp_path, pprl_now
):
    ctx = await container_context(postgres_database, tmp_path, pprl_now)
    receipt = await ctx.service.execute(**ctx.kwargs)

    async def reconcile():
        async with postgres_database.transaction() as session:
            return await ctx.records.reconcile(
                session, invocation_id=receipt.invocation_id, now=pprl_now()
            )

    results = await asyncio.gather(*(reconcile() for _ in range(4)))
    assert len({result.digest for result in results}) == 1
    assert results[0].charged.actions == 1 and results[0].open_reservations == 0
    assert len(ctx.driver.calls) == 1
