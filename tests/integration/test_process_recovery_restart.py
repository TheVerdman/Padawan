from datetime import UTC, datetime

from padawan.pprl.recovery_contracts import ProcessRecoveryReceipt
from padawan.pprl.worker_protocol_contracts import ProcessWorkerReply
from tests.support.process_recovery import expire, reviewed
from tests.support.process_restart import (
    run_broker,
)
from tests.worker_helpers import broker_context, prepare_broker_action


async def test_native_broker_restart_and_complete_simulated_worker_replacement(database, tmp_path):
    ctx = await broker_context(database, tmp_path, lambda: datetime.now(UTC))
    await prepare_broker_action(ctx, native=True)
    original = ctx.observed.observation
    await expire(ctx)
    request = reviewed(ctx)
    configuration = dict(
        database_url=database.url, artifact_root=str(ctx.records.catalog.backend.root)
    )
    # The recovering broker and replacement broker have no original Python objects,
    # worker context, local cache, capability or stack. Only institutional stores remain.
    recovered = ProcessRecoveryReceipt.model_validate(
        await run_broker(
            dict(
                **configuration,
                mode="recover",
                request=request.model_dump(mode="json"),
            )
        ),
        strict=False,
    )
    assert recovered.disposition == "ready"
    replacement = await run_broker(
        dict(**configuration, mode="replace", recovery_id=request.recovery_id)
    )
    reply = ProcessWorkerReply.model_validate(replacement["reply"], strict=False)
    assert reply.observation == original
    assert replacement["worker_id"] != ctx.worker_id
    assert recovered.digest not in reply.model_dump_json()
    assert recovered.request.recovery_id not in reply.model_dump_json()
    async with database.transaction() as session:
        _, retired = await ctx.process.workers.registration(session, ctx.worker_id)
        assert retired.status == "revoked"
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
        assert sum(event.kind == "release" for event in journal) == 1
        assert journal[-1].charged.actions == 0
