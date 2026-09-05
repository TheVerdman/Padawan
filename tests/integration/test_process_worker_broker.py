from datetime import timedelta

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, func, select, update

from padawan.governance.amber import AmberAdmissionDisposition, AmberStatus
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ProcessObservationRow,
    ProcessRolloutRow,
    ProcessWorkerDecisionBindingRow,
    ProcessWorkerLeaseAssignmentRow,
    ProcessWorkerRequestRow,
)
from padawan.pprl.content import ProcessContentDeniedError
from padawan.pprl.contracts import ProjectStatePayload, RolloutStatus
from padawan.pprl.worker_broker import ProcessWorkerBroker, ProcessWorkerRequestDeniedError
from padawan.pprl.worker_protocol_contracts import ProcessWorkerRequest
from tests.container_helpers import commit_container_action
from tests.worker_helpers import (
    broker_context,
    prepare_broker_action,
    worker_proposal,
    worker_request,
)


async def test_disposable_native_clients_and_new_broker_reconstruct_an_exact_action(
    database, tmp_path, pprl_now
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    claimed, proposed, observed, disposition = await prepare_broker_action(ctx, native=True)
    assert disposition.disposition == AmberAdmissionDisposition.ADMITTED
    assert disposition.observation is None
    ctx.broker = ProcessWorkerBroker(
        database=database, store=ctx.process, broker_audience="fixture-broker"
    )
    assert await ctx.broker.request(claimed) == observed
    assert await ctx.broker.request(proposed) == disposition
    private = (
        ctx.worker_id,
        ctx.worker_access.credential.get_secret_value(),
        ctx.worker_access.assignment_id,
        ctx.claim.lease_token,
        ctx.decision.decision_id,
    )
    assert all(
        value.encode() not in canonical_json_bytes(observed.observation) for value in private
    )
    async with database.transaction() as session:
        rows = list(await session.scalars(select(ProcessWorkerRequestRow)))
        assert len(rows) == 2
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 1
        )
        assert all(
            ctx.worker_access.credential.get_secret_value() not in str(row.record_json)
            for row in rows
        )
    await ctx.service.execute(**ctx.kwargs)
    event, state = await commit_container_action(ctx)
    assert event.actor_id == ctx.worker_id and state.sequence == 1
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(proposed)
    async with database.transaction() as session:
        _, historical = await ctx.broker.inspect_request(
            session, worker_id=ctx.worker_id, request_id=proposed.request_id
        )
        assert historical == disposition


@pytest.mark.parametrize("change", ["credential", "audience", "worker", "assignment", "body"])
async def test_changed_request_or_identity_cannot_reuse_a_request_id(
    database, tmp_path, pprl_now, change
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    _, proposed, _, _ = await prepare_broker_action(ctx)
    changed = {
        "credential": {"credential": SecretStr("f" * 64)},
        "audience": {"broker_audience": "another-broker"},
        "worker": {"worker_id": "process-worker-" + "f" * 32},
        "assignment": {"assignment_id": "worker-assignment-" + "f" * 32},
        "body": {"proposal": proposed.proposal.model_copy(update={"target_class": "changed"})},
    }
    with pytest.raises(ProcessWorkerRequestDeniedError) as failure:
        await ctx.broker.request(proposed.model_copy(update=changed[change]))
    assert str(failure.value) == "worker request denied"
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessWorkerRequestRow)) == 2
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 1
        )


@pytest.mark.parametrize("field", ["role_id", "worker_model_digest", "lease_token", "metadata"])
async def test_wire_schema_does_not_offer_privileged_fields(database, tmp_path, pprl_now, field):
    ctx = await broker_context(database, tmp_path, pprl_now)
    data = worker_request(ctx).model_dump()
    data["credential"] = ctx.worker_access.credential
    data[field] = "untrusted"
    with pytest.raises(ValidationError):
        ProcessWorkerRequest.model_validate(data)


@pytest.mark.parametrize("limit", ["records", "bytes"])
async def test_request_quota_rolls_back_admission_and_retention(
    database, tmp_path, pprl_now, limit
):
    scope = (
        {"maximum_request_records": 1}
        if limit == "records"
        else {"maximum_request_history_bytes": 4096}
    )
    ctx = await broker_context(database, tmp_path, pprl_now, scope_updates=scope)
    request = worker_request(ctx)
    reply = await ctx.broker.request(request)
    for index in range(10):
        next_request = worker_request(
            ctx,
            f"proposal-{index}",
            "propose",
            assignment_id=reply.assignment_id,
            observation_request_id=request.request_id,
            proposal=worker_proposal(ctx),
        )
        async with database.transaction() as session:
            before = await session.scalar(
                select(func.count()).select_from(AmberAdmissionDecisionRow)
            )
            held = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        try:
            await ctx.broker.request(next_request)
        except ProcessWorkerRequestDeniedError:
            break
    else:
        pytest.fail("configured history bound was not enforced")
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow))
            == before
        )
        assert await ctx.amber.resources.inspect(session, ctx.authorization.digest) == held
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 1
    assert await ctx.broker.request(request) == reply  # Exact replay consumes no extra quota.


async def test_failed_claim_receipt_rolls_back_lease_assignment_and_observation(
    database, tmp_path, pprl_now, monkeypatch
):
    ctx = await broker_context(database, tmp_path, pprl_now)

    async def unavailable(*args, **kwargs):
        raise OSError("private evidence failure")

    monkeypatch.setattr(ctx.broker, "inspect_request", unavailable)
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(worker_request(ctx))
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.rollout.rollout_id)
        assert row.lease_owner is None and row.lease_token is None
        for table in (
            ProcessWorkerRequestRow,
            ProcessWorkerLeaseAssignmentRow,
            ProcessObservationRow,
        ):
            assert await session.scalar(select(func.count()).select_from(table)) == 0


@pytest.mark.parametrize("operation", ["observe", "propose", "replay"])
async def test_revocation_blocks_every_worker_read_but_keeps_historical_lineage(
    database, tmp_path, pprl_now, operation
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    claim, proposal, _, _ = await prepare_broker_action(ctx)
    async with database.transaction() as session:
        await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="retained fixture revocation",
            now=pprl_now(),
        )
    request = {
        "observe": worker_request(
            ctx, "observe-2", "observe", assignment_id=ctx.worker_access.assignment_id
        ),
        "propose": proposal.model_copy(update={"request_id": "proposal-2"}),
        "replay": claim,
    }[operation]
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(request)
    async with database.transaction() as session:
        receipt, _ = await ctx.broker.inspect_request(
            session, worker_id=ctx.worker_id, request_id=proposal.request_id
        )
        assert receipt.decision_id == ctx.decision.decision_id


async def test_one_worker_cannot_hold_two_current_leases(database, tmp_path, pprl_now):
    ctx = await broker_context(database, tmp_path, pprl_now)
    first = await ctx.broker.request(worker_request(ctx))
    async with database.transaction() as session:
        second = await ctx.process.create_rollout(
            session,
            execution_digest=sha256_digest(ctx.execution),
            replication_index=1,
            initial_state=ProjectStatePayload(objective="another rollout"),
            created_at=pprl_now(),
        )
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(worker_request(ctx, "claim-2"))
    async with database.transaction() as session:
        assert (await session.get(ProcessRolloutRow, second.rollout_id)).lease_token is None
        assert await ctx.process.workers.assignment(session, first.assignment_id)


@pytest.mark.parametrize("kind", ["worker", "assignment", "request", "scope", "verifier", "secret"])
async def test_private_worker_material_is_not_admissible_process_content(
    database, tmp_path, pprl_now, kind
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    _, proposed, _, _ = await prepare_broker_action(ctx)
    async with database.transaction() as session:
        record, _ = await ctx.process.workers.registration(session, ctx.worker_id)
        scope = await ctx.process.workers.scope(session, record.execution_digest)
        receipt, _ = await ctx.broker.inspect_request(
            session, worker_id=ctx.worker_id, request_id=proposed.request_id
        )
        value = {
            "worker": ctx.worker_id,
            "assignment": ctx.worker_access.assignment_id,
            "request": receipt.receipt_id,
            "scope": scope.digest,
            "verifier": record.credential_digest,
            "secret": ctx.worker_access.credential.get_secret_value(),
        }[kind]
        with pytest.raises(ProcessContentDeniedError):
            await ctx.process.content.check_state(
                session, ProjectStatePayload(objective=f"persist {value}")
            )


async def test_missing_identity_binding_blocks_forensic_and_accounting_reconstruction(
    database, tmp_path, pprl_now
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    _, proposal, _, _ = await prepare_broker_action(ctx)
    receipt = await ctx.service.execute(**ctx.kwargs)
    async with database.transaction() as session:
        # Deliberate privileged SQL corruption, outside the worker API.
        await session.execute(
            delete(ProcessWorkerDecisionBindingRow).where(
                ProcessWorkerDecisionBindingRow.decision_id == ctx.decision.decision_id
            )
        )
    async with database.transaction() as session:
        with pytest.raises(ValueError, match="authenticated"):
            await ctx.amber.resources.reservation(session, ctx.decision.decision_id)
        with pytest.raises(ValueError, match="authenticated"):
            await ctx.records.inspect(session, receipt.invocation_id)
        with pytest.raises(PermissionError):
            await ctx.broker.inspect_request(
                session, worker_id=ctx.worker_id, request_id=proposal.request_id
            )


async def test_paused_authority_and_backdated_expired_lease_do_not_reenable_access(
    database, tmp_path, pprl_now
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    reply = await ctx.broker.request(worker_request(ctx))
    async with database.transaction() as session:
        now = pprl_now()
        await session.execute(
            update(ProcessRolloutRow).values(lease_expires_at=now - timedelta(seconds=1))
        )
        rollout = await session.get(ProcessRolloutRow, ctx.rollout.rollout_id)
        with pytest.raises(PermissionError):
            await ctx.process.workers.require(
                session,
                rollout=rollout,
                access=ctx.worker_access.assigned(reply.assignment_id),
                now=now - timedelta(seconds=2),
            )
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=AmberStatus.PAUSED,
            actor_id="reviewer-a",
            reason="explicit fixture pause",
            evidence_refs=("fixture:pause",),
            occurred_at=pprl_now(),
        )
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(worker_request(ctx))


async def test_reviewed_new_incarnation_recovers_same_observation_without_old_authority(
    database, tmp_path, pprl_now
):
    ctx = await broker_context(database, tmp_path, pprl_now)
    original_request = worker_request(ctx)
    before = await ctx.broker.request(original_request)
    async with database.transaction() as session:
        rollout = await session.get(ProcessRolloutRow, ctx.rollout.rollout_id)
        await ctx.process.release_claim(
            session,
            rollout_id=rollout.rollout_id,
            lease_token=rollout.lease_token,
            to_status=RolloutStatus.ACTIVE,
            occurred_at=pprl_now(),
            worker_access=ctx.worker_access.assigned(before.assignment_id),
        )
        await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="explicit fixture replacement",
            now=pprl_now(),
        )
        now = pprl_now()
        registration, access = await ctx.process.workers.issue(
            session,
            execution_digest=sha256_digest(ctx.execution),
            role_id=ctx.profile.role_id,
            worker_model_digest=ctx.profile.worker_model_digest,
            declared_capabilities=("reasoning",),
            issued_by="reviewer-a",
            evidence="explicit replacement issuance",
            expires_at=now + timedelta(minutes=30),
            now=now,
        )
    ctx.worker_id, ctx.worker_access = registration.worker_id, access
    after = await ctx.broker.request(worker_request(ctx))
    assert canonical_json_bytes(before.observation) == canonical_json_bytes(after.observation)
    assert before.assignment_id != after.assignment_id
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(original_request)
    with pytest.raises(ProcessWorkerRequestDeniedError):
        await ctx.broker.request(
            worker_request(ctx, "wrong-assignment", "observe", assignment_id=before.assignment_id)
        )


async def test_enrolled_fork_cannot_create_unauthenticated_children(database, tmp_path, pprl_now):
    ctx = await broker_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="child worker-scope"):
            await ctx.process.fork_rollout(
                session,
                parent_rollout_id=ctx.rollout.rollout_id,
                lease_token="copied",
                amber_decision_id="untrusted",
                actor_id=ctx.worker_id,
                children=(),
                intervention={},
                occurred_at=pprl_now(),
            )
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 1
