import asyncio

import pytest
from sqlalchemy import delete, func, select

from padawan.models.hashing import canonical_json_bytes
from padawan.models.tables import (
    ArtifactReferenceRow,
    ProcessContainerHeadRow,
    ProcessContainerWorkloadRow,
    ProcessEventRow,
)
from padawan.pprl.container_driver import DockerContainerDriver
from padawan.pprl.containers import ProcessContainerExecutor, ProcessContainerUnavailableError
from tests.container_helpers import (
    SyntheticContainerDriver,
    commit_container_action,
    container_context,
)


async def test_bound_container_intent_private_evidence_accounting_and_event_commit(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now)
    receipt = await ctx.service.execute(**ctx.kwargs)
    assert ctx.driver.calls == [canonical_json_bytes(ctx.observed.observation)]
    assert receipt.effect_status == "terminated" and receipt.complete_capture
    async with database.transaction() as session:
        workload, retained, data = await ctx.records.inspect(session, receipt.invocation_id)
        assert retained == receipt and data["stdout"]
        first = await ctx.records.reconcile(
            session, invocation_id=receipt.invocation_id, now=pprl_now()
        )
        second = await ctx.records.reconcile(
            session, invocation_id=receipt.invocation_id, now=pprl_now()
        )
        assert first == second and first.charged.actions == 1 and first.open_reservations == 0
        assert first.basis == "container_evidence"
        assert workload.observation_id == ctx.observed.observation_id
    event, state = await commit_container_action(ctx)
    assert not event.artifact_refs and not state.payload.artifact_refs
    assert "raw privileged" not in event.model_dump_json()
    assert receipt.invocation_id not in event.model_dump_json()
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert len(ctx.driver.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("argv", ("/usr/bin/another-command",)),
        ("tool_operation", "other-operation"),
        ("worker_model_digest", "sha256:" + "1" * 64),
        ("socket_uri", "unix:///another/socket"),
    ],
)
async def test_changed_container_profile_never_launches(database, tmp_path, pprl_now, field, value):
    ctx = await container_context(database, tmp_path, pprl_now)
    driver = SyntheticContainerDriver(ctx.profile.model_copy(update={field: value}), pprl_now)
    service = ProcessContainerExecutor(
        database=database, observations=ctx.observations, store=ctx.records, driver=driver
    )
    with pytest.raises(ProcessContainerUnavailableError):
        await service.execute(**ctx.kwargs)
    assert not driver.calls
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessContainerWorkloadRow)) == 0
        )


async def test_unknown_container_effect_cannot_retry_or_commit(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now)

    async def crashed():
        raise TimeoutError("unknown fixture control effect")

    ctx.driver.after_start = crashed
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        head = await session.scalar(select(ProcessContainerHeadRow))
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert head.status == "unknown" and balance.open_reservations == 1
        assert balance.held.actions == 1
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    with pytest.raises(PermissionError):
        await commit_container_action(ctx)
    assert len(ctx.driver.calls) == 1
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0


async def test_missing_independent_container_pin_blocks_reconciliation_and_commit(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now)
    receipt = await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_resource_event"
            )
        )
    async with database.transaction() as session:
        with pytest.raises(ValueError, match="ownership"):
            await ctx.records.reconcile(
                session, invocation_id=receipt.invocation_id, now=pprl_now()
            )
    with pytest.raises(ValueError, match="ownership"):
        await commit_container_action(ctx)
    async with database.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactReferenceRow)
                .where(ArtifactReferenceRow.owner_type == "process_resource_event")
            )
            == 0
        )


async def test_container_intent_failure_rolls_back_sources_before_any_launch(
    database, tmp_path, pprl_now, monkeypatch
):
    ctx = await container_context(database, tmp_path, pprl_now)
    original = ctx.records.put

    async def fail_after_put(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected evidence failure")

    monkeypatch.setattr(ctx.records, "put", fail_after_put)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert not ctx.driver.calls
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessContainerWorkloadRow)) == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactReferenceRow)
                .where(ArtifactReferenceRow.owner_type == "process_container_workload")
            )
            == 0
        )


async def test_application_construction_does_not_spawn_containers(tmp_path, monkeypatch):
    from padawan.config.settings import Settings
    from padawan.pprl.composition import build_pprl_application

    def forbidden(*args, **kwargs):
        raise AssertionError("inert composition tried to execute")

    monkeypatch.setattr(DockerContainerDriver, "_spawn", forbidden)
    app = build_pprl_application(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/inert.sqlite3",
            artifact_root=tmp_path / "inert-artifacts",
        )
    )
    assert app.processes.container_evidence is not None
    await app.close()


@pytest.mark.parametrize("identity", ["lease_token", "worker_id", "rollout_id", "decision_id"])
async def test_substituted_container_identity_fails_before_dispatch(
    database, tmp_path, pprl_now, identity
):
    ctx = await container_context(database, tmp_path, pprl_now)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**{**ctx.kwargs, identity: "substituted-fixture-identity"})
    assert not ctx.driver.calls


@pytest.mark.parametrize("kind", ["runtime", "created", "terminal", "cleanup"])
async def test_capture_commit_failure_leaves_no_retry_and_no_silent_release(
    database, tmp_path, pprl_now, monkeypatch, kind
):
    ctx = await container_context(database, tmp_path, pprl_now)
    original = ctx.records.put

    async def fail_capture(session, data, **kwargs):
        result = await original(session, data, **kwargs)
        if kwargs["owner_type"] == "process_container_capture" and kwargs["owner_id"].endswith(
            ":" + kind
        ):
            raise RuntimeError("injected capture transaction failure")
        return result

    monkeypatch.setattr(ctx.records, "put", fail_capture)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.open_reservations == 1 and balance.held.actions == 1
        pins = await session.scalars(
            select(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_container_capture"
            )
        )
        assert all(not pin.owner_id.endswith(":" + kind) for pin in pins)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert len(ctx.driver.calls) == 1


async def test_final_evidence_transaction_failure_preserves_started_hold(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.models.tables import ProcessContainerReceiptRow

    ctx = await container_context(database, tmp_path, pprl_now)
    original = ctx.records.put

    async def fail_result(session, data, **kwargs):
        result = await original(session, data, **kwargs)
        if kwargs["owner_type"] == "process_container_result":
            raise RuntimeError("injected receipt commit failure")
        return result

    monkeypatch.setattr(ctx.records, "put", fail_result)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessContainerReceiptRow)) == 0
        )
        _, phase, balance = await ctx.amber.resources.reservation(session, ctx.decision.decision_id)
        assert phase.status == "started" and balance.open_reservations == 1
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    assert len(ctx.driver.calls) == 1


async def test_resource_commit_failure_keeps_receipt_for_explicit_reconciliation(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.models.tables import ProcessContainerReceiptRow

    ctx = await container_context(database, tmp_path, pprl_now)
    original = ctx.records.reconcile

    async def failed(*args, **kwargs):
        raise RuntimeError("injected accounting failure")

    monkeypatch.setattr(ctx.records, "reconcile", failed)
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        receipt = await session.scalar(select(ProcessContainerReceiptRow))
        assert receipt is not None
        invocation_id = receipt.invocation_id
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.open_reservations == 1
    monkeypatch.setattr(ctx.records, "reconcile", original)
    async with database.transaction() as session:
        recovered = await ctx.records.reconcile(
            session, invocation_id=invocation_id, now=pprl_now()
        )
        assert recovered.open_reservations == 0 and recovered.charged.actions == 1
    await commit_container_action(ctx)
    assert len(ctx.driver.calls) == 1


async def test_synthetic_cancellation_retains_unknown_effect_and_blocks_retry(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now)

    async def cancelled():
        raise asyncio.CancelledError()

    ctx.driver.after_start = cancelled
    with pytest.raises(asyncio.CancelledError):
        await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        head = await session.scalar(select(ProcessContainerHeadRow))
        assert head.status == "unknown"
        _, phase, balance = await ctx.amber.resources.reservation(session, ctx.decision.decision_id)
        assert phase.status == "started" and balance.open_reservations == 1
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
