import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from padawan.models.tables import ArtifactReferenceRow, ExternalCallRow, ProcessEventRow
from padawan.pprl.containers import ProcessContainerUnavailableError
from padawan.pprl.generation import ProcessGenerationUnavailableError
from padawan.pprl.recovery import ProcessRecoveryStore
from tests.container_helpers import container_context
from tests.helpers import CallbackGenerationClient
from tests.integration.test_process_generation_workloads import _ready
from tests.integration.test_process_recovery import expire, recover, recovery, reviewed, snapshot
from tests.pprl_evidence_helpers import evidence_context as evidence_context


@pytest.mark.parametrize("capture", ["runtime", "created", "terminal", "cleanup"])
async def test_partial_container_capture_cannot_be_refunded_or_retried(
    database, tmp_path, pprl_now, monkeypatch, capture
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    original = ctx.records.put

    async def fail_capture(session, data, **kwargs):
        result = await original(session, data, **kwargs)
        if kwargs["owner_type"] == "process_container_capture" and kwargs["owner_id"].endswith(
            ":" + capture
        ):
            raise RuntimeError("simulated broker death at capture commit")
        return result

    monkeypatch.setattr(ctx.records, "put", fail_capture)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    await expire(ctx)
    async with database.transaction() as session:
        with pytest.raises(PermissionError):
            await ctx.amber.resources.release_unstarted(
                session,
                decision_id=ctx.decision.decision_id,
                reviewer_id="reviewer-a",
                evidence="cannot assert no physical effect",
                now=pprl_now(),
            )
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    assert effect.disposition == "unknown" and receipt.disposition == "review_required"
    assert (
        effect.before_phase
        == effect.after_phase
        == ("reserved" if capture in {"runtime", "created"} else "started")
    )
    assert len(effect.sources) == {"runtime": 1, "created": 2, "terminal": 3, "cleanup": 4}[capture]
    assert receipt.account_before_digest == receipt.account_after_digest
    async with database.transaction() as session:
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert len(ctx.driver.calls) == 1


async def test_fresh_recovery_reconciles_container_after_broker_died_before_accounting(
    database, tmp_path, pprl_now, monkeypatch
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)

    async def die(*args, **kwargs):
        raise RuntimeError("simulated broker loss before reconciliation")

    monkeypatch.setattr(ctx.records, "reconcile", die)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    assert effect.before_phase == "started" and effect.after_phase == "settled"
    assert effect.disposition == "completed_unadmitted" and receipt.disposition == "review_required"
    async with database.transaction() as session:
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "settle" for event in journal) == 1
        assert journal[-1].charged.actions == 1 and journal[-1].held.actions == 0
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
    assert len(ctx.driver.calls) == 1


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


@pytest.mark.parametrize("lost_accounting", [False, True])
async def test_model_recovery_uses_original_bytes_and_accounts_once(
    evidence_context, monkeypatch, lost_accounting
):
    ready = await _ready(evidence_context)
    ctx = await model_context(ready)
    if lost_accounting:

        async def die(*args, **kwargs):
            raise RuntimeError("simulated broker loss after retained response")

        monkeypatch.setattr(ready.service.resources, "reconcile", die)
        with pytest.raises(ProcessGenerationUnavailableError):
            await ready.service.execute(**ready.kwargs)
    else:
        await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    assert effect.kind == "model" and effect.disposition == "completed_unadmitted"
    assert effect.before_phase == ("started" if lost_accounting else "settled")
    assert effect.after_phase == "settled" and len(effect.sources) == 3
    assert receipt.disposition == "review_required"
    async with ctx.database.transaction() as session:
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "settle" for event in journal) == 1
        assert journal[-1].charged.actions == 1
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
    assert len(ready.client.calls) == 1


async def test_late_model_result_remains_private_after_recovery_fences_worker(evidence_context):
    entered, finish = asyncio.Event(), asyncio.Event()

    class Delayed(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            entered.set()
            await finish.wait()
            return await super().generate_prepared(request, prepared)

    client = Delayed(lambda request: "late private candidate", "test-open-weight")
    ready = await _ready(evidence_context, client=client)
    ctx = await model_context(ready)
    pending = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await expire(ctx)
        unknown = await recover(ctx)
        assert unknown.effects[0].disposition == "unknown"
        assert unknown.effects[0].after_phase == "started"
        assert len(unknown.effects[0].sources) == 2
    finally:
        finish.set()
    with pytest.raises(ProcessGenerationUnavailableError):
        await asyncio.wait_for(pending, 5)
    async with ctx.database.transaction() as session:
        call = await session.get(ExternalCallRow, ready.request.request_id)
        assert call.status == "completed" and call.response_artifact_id is not None
        assert await recovery(ctx).read(session, recovery_id=unknown.request.recovery_id) == unknown
    request = reviewed(ctx).model_copy(update={"expected_lease_token_digest": None, "resume": True})
    completed = await recover(ctx, request)
    assert completed.disposition == "review_required"
    assert completed.effects[0].disposition == "completed_unadmitted"
    assert completed.state_digest == unknown.state_digest
    async with ctx.database.transaction() as session:
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "settle" for event in journal) == 1
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
    assert len(client.calls) == 1


async def test_reconciliation_and_fencing_roll_back_together_when_recovery_pin_fails(
    evidence_context, monkeypatch
):
    ready = await _ready(evidence_context)
    ctx = await model_context(ready)

    async def die(*args, **kwargs):
        raise RuntimeError("broker died before accounting")

    monkeypatch.setattr(ready.service.resources, "reconcile", die)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    before = await snapshot(ctx)
    service = ProcessRecoveryStore(ctx.process, ready.external.catalog)
    original = ready.external.catalog.reference

    async def fail_pin(*args, **kwargs):
        await original(*args, **kwargs)
        if kwargs["owner_type"] == "process_recovery_receipt":
            raise RuntimeError("rollback complete recovery transaction")

    monkeypatch.setattr(ready.external.catalog, "reference", fail_pin)
    request = reviewed(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises(RuntimeError, match="complete recovery transaction"):
            await service.recover(session, request, now=ctx.clock())
    assert await snapshot(ctx) == before
    async with ctx.database.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactReferenceRow)
                .where(
                    ArtifactReferenceRow.owner_type.in_(
                        ("process_resource_event", "process_recovery_receipt")
                    )
                )
            )
            == 0
        )
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert journal[-1].held.actions == 1 and journal[-1].charged.actions == 0
