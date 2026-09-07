import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ProcessRolloutRow,
    ProcessWorkerLeaseAssignmentRow,
    ProcessWorkerRegistrationRow,
    ProcessWorkerRequestRow,
)
from padawan.pprl.containers import ProcessContainerUnavailableError
from padawan.pprl.contracts import ProjectStatePayload
from padawan.pprl.worker_broker import ProcessWorkerBroker, ProcessWorkerRequestDeniedError
from tests.support.postgres import postgres_database as postgres_database
from tests.worker_helpers import (
    broker_context,
    prepare_broker_action,
    worker_proposal,
    worker_request,
)

pytestmark = pytest.mark.postgres


async def test_postgres_one_worker_has_one_assignment_across_competing_rollouts(
    postgres_database, tmp_path, pprl_now
):
    ctx = await broker_context(postgres_database, tmp_path, pprl_now)
    async with postgres_database.transaction() as session:
        await ctx.process.create_rollout(
            session,
            execution_digest=sha256_digest(ctx.execution),
            replication_index=1,
            initial_state=ProjectStatePayload(objective="second candidate"),
            created_at=pprl_now(),
        )
    brokers = [
        ProcessWorkerBroker(
            database=postgres_database, store=ctx.process, broker_audience="fixture-broker"
        )
        for _ in range(4)
    ]
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                broker.request(worker_request(ctx, f"claim-{index}"))
                for index, broker in enumerate(brokers)
            ),
            return_exceptions=True,
        ),
        10,
    )
    assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
    assert sum(isinstance(value, ProcessWorkerRequestDeniedError) for value in outcomes) == 3
    async with postgres_database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessWorkerLeaseAssignmentRow))
            == 1
        )
        owners = list(await session.scalars(select(ProcessRolloutRow.lease_owner)))
        assert owners.count(ctx.worker_id) == 1 and owners.count(None) == 1


async def test_postgres_concurrent_duplicate_proposals_return_one_durable_admission(
    postgres_database, tmp_path, pprl_now
):
    ctx = await broker_context(postgres_database, tmp_path, pprl_now)
    claim = await ctx.broker.request(worker_request(ctx))
    request = worker_request(
        ctx,
        "proposal-1",
        "propose",
        assignment_id=claim.assignment_id,
        observation_request_id="claim-1",
        proposal=worker_proposal(ctx),
    )
    outcomes = await asyncio.wait_for(
        asyncio.gather(
            *(
                ProcessWorkerBroker(
                    database=postgres_database, store=ctx.process, broker_audience="fixture-broker"
                ).request(request)
                for _ in range(4)
            )
        ),
        10,
    )
    assert len({sha256_digest(value) for value in outcomes}) == 1
    async with postgres_database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 1
        )
        assert await session.scalar(select(func.count()).select_from(ProcessWorkerRequestRow)) == 2
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.held.actions == 1 and balance.open_reservations == 1


async def test_postgres_revocation_wins_before_dispatch_without_refunding_a_hold(
    postgres_database, tmp_path, pprl_now
):
    ctx = await broker_context(postgres_database, tmp_path, pprl_now)
    await prepare_broker_action(ctx)
    async with postgres_database.transaction() as session:
        await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="concurrent revocation fixture",
            now=pprl_now(),
        )
        pending = asyncio.create_task(ctx.service.execute(**ctx.kwargs))
        await asyncio.sleep(0.1)
        assert not pending.done()  # Must wait on the revocation head lock.
    with pytest.raises(ProcessContainerUnavailableError):
        await asyncio.wait_for(pending, 10)
    assert not ctx.driver.calls
    async with postgres_database.transaction() as session:
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.held.actions == 1 and balance.open_reservations == 1


async def test_postgres_concurrent_registration_replacement_cannot_exceed_capacity(
    postgres_database, tmp_path, pprl_now
):
    ctx = await broker_context(postgres_database, tmp_path, pprl_now)
    async with postgres_database.transaction() as session:
        await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="replace lost fixture credential",
            now=pprl_now(),
        )

    async def issue():
        async with postgres_database.transaction() as session:
            now = pprl_now()
            return await ctx.process.workers.issue(
                session,
                execution_digest=sha256_digest(ctx.execution),
                role_id=ctx.profile.role_id,
                worker_model_digest=ctx.profile.worker_model_digest,
                declared_capabilities=("reasoning",),
                issued_by="reviewer-a",
                evidence="replacement fixture",
                expires_at=now + timedelta(minutes=30),
                now=now,
            )

    outcomes = await asyncio.wait_for(
        asyncio.gather(*(issue() for _ in range(4)), return_exceptions=True), 10
    )
    successes = [value for value in outcomes if not isinstance(value, BaseException)]
    assert len(successes) == 1 and successes[0][0].worker_id != ctx.worker_id
    assert sum(isinstance(value, PermissionError) for value in outcomes) == 3
    async with postgres_database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessWorkerRegistrationRow))
            == 2
        )
