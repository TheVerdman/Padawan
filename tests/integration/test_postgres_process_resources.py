import asyncio

import pytest

from padawan.governance.amber import AmberAdmissionDisposition
from tests.integration.test_postgres_concurrency import postgres_database as postgres_database
from tests.integration.test_process_generation_workloads import _ready
from tests.pprl_resource_helpers import resource_action, resource_context

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("capacity, expected", [(70, 1), (200, 4)])
async def test_postgres_independent_brokers_conserve_shared_capacity(
    postgres_database, tmp_path, pprl_now, capacity, expected
):
    ctx = await resource_context(
        postgres_database, tmp_path, pprl_now, rollouts=4, caps={"input_tokens": capacity}
    )
    actions = [(await resource_action(ctx, worker=f"worker-{i}"))[1] for i in range(4)]

    async def admit(action):
        async with postgres_database.transaction() as session:
            return await ctx.amber.admit(session, request=action, active_workers=0)

    decisions = await asyncio.gather(*(admit(action) for action in reversed(actions)))
    assert sum(d.disposition == AmberAdmissionDisposition.ADMITTED for d in decisions) == expected
    async with postgres_database.transaction() as session:
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert journal[-1].held.input_tokens == expected * 40
        assert journal[-1].open_reservations == expected


async def test_postgres_settlement_retries_charge_once(postgres_database, tmp_path, pprl_now):
    ctx = await resource_context(postgres_database, tmp_path, pprl_now)
    ready = await _ready(ctx)
    await ready.service.execute(**ready.kwargs)

    async def reconcile():
        async with postgres_database.transaction() as session:
            return await ready.service.resources.reconcile(
                session, invocation_id=ready.kwargs["invocation_id"], now=pprl_now()
            )

    records = await asyncio.gather(*(reconcile() for _ in range(4)))
    assert len({r.digest for r in records}) == 1
    async with postgres_database.transaction() as session:
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert journal[-1].charged.actions == 1
        assert journal[-1].charged.input_tokens == 10
        assert journal[-1].open_reservations == 0
    assert len(ready.client.calls) == 1


async def test_postgres_dispatch_and_refund_race_has_one_winner(
    postgres_database, tmp_path, pprl_now
):
    ctx = await resource_context(postgres_database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    async with postgres_database.transaction() as session:
        decision = await ctx.amber.admit(session, request=action, active_workers=0)

    async def start():
        async with postgres_database.transaction() as session:
            await ctx.amber.resources.start(
                session, decision_id=decision.decision_id, now=pprl_now()
            )
        return "started"

    async def refund():
        async with postgres_database.transaction() as session:
            await ctx.amber.resources.release_unstarted(
                session,
                decision_id=decision.decision_id,
                reviewer_id="reviewer-a",
                evidence="explicit fixture cancellation",
                now=pprl_now(),
            )
        return "released"

    results = await asyncio.gather(start(), refund(), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
    async with postgres_database.transaction() as session:
        _, phase, _ = await ctx.amber.resources.reservation(session, decision.decision_id)
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert journal[-1].held.input_tokens == (40 if phase.status == "started" else 0)
