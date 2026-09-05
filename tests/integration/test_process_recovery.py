"""Disposable simulated workers; recovery makes no model or scientific claim."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from padawan.governance.amber import AmberStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ProcessEventRow,
    ProcessRecoveryRow,
    ProcessRolloutRow,
)
from padawan.pprl.content import ProcessContentDeniedError
from padawan.pprl.contracts import ProjectStatePayload, RolloutStatus
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.recovery_contracts import ProcessRecoveryRequest
from tests.container_helpers import container_context


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


@pytest.mark.parametrize("enrolled", [False, True])
async def test_reviewed_unstarted_recovery_fences_and_fresh_broker_hydrates_only_state(
    database, tmp_path, pprl_now, enrolled
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=enrolled)
    await expire(ctx)
    request = reviewed(ctx)
    receipt = await recover(ctx, request)
    assert receipt.disposition == "ready"
    assert receipt.effects[0].disposition == "released_unstarted"
    assert not receipt.effects[0].sources
    async with database.transaction() as session:
        assert await recovery(ctx).read(session, recovery_id=request.recovery_id) == receipt
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert account.held.actions == account.open_reservations == 0
        assert account.charged.actions == 0
        access, worker_id = None, "fresh-broker"
        if enrolled:
            _, retired = await ctx.process.workers.registration(session, ctx.worker_id)
            assert retired.status == "revoked"
            _, access = await ctx.process.workers.issue(
                session,
                execution_digest=receipt.execution_digest,
                role_id=ctx.profile.role_id,
                worker_model_digest=ctx.profile.worker_model_digest,
                declared_capabilities=ctx.program.worker_roles[0].required_capabilities,
                issued_by="reviewer-a",
                evidence="replacement fixture",
                expires_at=pprl_now() + timedelta(minutes=30),
                now=pprl_now(),
            )
            worker_id = access.worker_id
        fresh = type(ctx.process)(type(ctx.amber)())
        claim = await fresh.claim_next(
            session,
            worker_id=worker_id,
            worker_access=access,
            lease_for=timedelta(minutes=5),
            now=pprl_now(),
        )
        assert claim is not None and claim.state == ctx.claim.state
        assert claim.lease_token != ctx.claim.lease_token
        assert await recovery(ctx).recover(session, request, now=pprl_now()) == receipt
        row = await session.get(ProcessRolloutRow, claim.rollout.rollout_id)
        assert row.lease_token == claim.lease_token  # old retry cannot fence successor
        from padawan.pprl.observations import ProcessObservationStore

        observed = await ProcessObservationStore(fresh).observe_claim(
            session,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            worker_id=worker_id,
            worker_access=access.assigned(claim.worker_assignment_id) if access else None,
            now=pprl_now(),
        )
        public = observed.observation.model_dump_json()
        assert request.recovery_id not in public and receipt.digest not in public
        assert request.reason not in public and "no_retained_intent" not in public
    with pytest.raises(PermissionError):
        async with database.transaction() as session:
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                now=pprl_now(),
                worker_access=ctx.worker_access,
            )
    assert not ctx.driver.calls


@pytest.mark.parametrize("bad_field", ["lease", "state", "reviewer", "active"])
async def test_stale_unauthorized_or_still_active_review_changes_nothing(
    database, tmp_path, pprl_now, bad_field
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    if bad_field != "active":
        await expire(ctx)
    request = reviewed(ctx)
    updates = {
        "lease": {"expected_lease_token_digest": sha256_digest("wrong")},
        "state": {"expected_state_digest": sha256_digest("wrong")},
        "reviewer": {"reviewer_id": ctx.worker_id},
        "active": {},
    }[bad_field]
    request = request.model_copy(update=updates)
    before = await snapshot(ctx)
    with pytest.raises(PermissionError):
        await recover(ctx, request)
    assert await snapshot(ctx) == before


@pytest.mark.parametrize(
    "status", [AmberStatus.PAUSED, AmberStatus.QUARANTINED, AmberStatus.REVOKED]
)
async def test_recovery_does_not_reactivate_amber_even_when_resume_requested(
    database, tmp_path, pprl_now, status
):
    ctx = await container_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=status,
            actor_id="reviewer-a",
            reason="fixture stop",
            occurred_at=pprl_now(),
        )
    receipt = await recover(ctx, reviewed(ctx, resume=True))
    assert receipt.disposition == "paused" and receipt.status_after == RolloutStatus.PAUSED
    async with database.transaction() as session:
        assert (
            await ctx.amber.status(session, authorization_digest=ctx.authorization.digest) == status
        )
        assert (
            await ctx.process.claim_next(
                session, worker_id="fresh", lease_for=timedelta(minutes=5), now=pprl_now()
            )
            is None
        )


@pytest.mark.parametrize("resume", [False, True])
async def test_explicit_rollout_pause_requires_explicit_resume(
    database, tmp_path, pprl_now, resume
):
    ctx = await container_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await session.execute(
            update(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == ctx.claim.rollout.rollout_id)
            .values(paused=True, status=RolloutStatus.PAUSED.value)
        )
    receipt = await recover(ctx, reviewed(ctx, resume=resume))
    assert receipt.disposition == ("ready" if resume else "paused")


async def test_revoked_worker_can_be_fenced_before_lease_expiry(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    async with database.transaction() as session:
        revoked = await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="fixture revoked",
            now=pprl_now(),
        )
    receipt = await recover(ctx)
    assert receipt.retired_worker_digest == revoked.digest and receipt.disposition == "ready"


async def test_started_unknown_action_retains_hold_and_requires_review(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    async with database.transaction() as session:
        await ctx.amber.resources.start(
            session,
            decision_id=ctx.decision.decision_id,
            now=pprl_now(),
            worker_access=ctx.worker_access,
        )
    await expire(ctx)
    receipt = await recover(ctx)
    assert receipt.disposition == "review_required" and receipt.effects[0].after_phase == "started"
    assert receipt.account_before_digest == receipt.account_after_digest
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
        assert row.paused and row.lease_token is None
        assert row.current_state_id == ctx.claim.state.state_id and row.last_error is None
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.held.actions == balance.open_reservations == 1
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt


async def test_known_completed_container_without_transition_never_becomes_a_retry(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    result = await ctx.service.execute(**ctx.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    assert receipt.disposition == "review_required"
    effect = receipt.effects[0]
    assert effect.disposition == "completed_unadmitted" and effect.result_digest == result.digest
    assert effect.before_phase == effect.after_phase == "settled"
    assert len(effect.sources) == 6
    async with database.transaction() as session:
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
        retained = list(
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_recovery_receipt"
                )
            )
        )
        assert len(retained) == 6
        for text in (receipt.digest, receipt.request.digest, receipt.request.recovery_id):
            with pytest.raises(ProcessContentDeniedError):
                await ctx.process.content.check_state(session, ProjectStatePayload(objective=text))
    assert len(ctx.driver.calls) == 1


@pytest.mark.parametrize(
    "failure", ["source_pin", "own_pin", "bytes", "byte_bound", "receipt_rollback"]
)
async def test_recovery_source_failures_never_heal_pins_or_publish_partial_recovery(
    database, tmp_path, pprl_now, monkeypatch, failure
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    result = await ctx.service.execute(**ctx.kwargs)
    await expire(ctx)
    request = reviewed(ctx)
    if failure == "own_pin":
        await recover(ctx, request)
        async with database.transaction() as session:
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_recovery_receipt"
                )
            )
    elif failure == "source_pin":
        async with database.transaction() as session:
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_container_result"
                )
            )
    elif failure == "bytes":
        path = ctx.records.catalog.backend._path_for_hex(
            result.evidence_artifact.artifact.digest[7:]
        )
        path.write_bytes(b"corrupt fixture")
    elif failure == "byte_bound":
        request = request.model_copy(update={"maximum_source_bytes": 1})
    else:
        original = ctx.records.catalog.reference

        async def fail_after_pin(*args, **kwargs):
            value = await original(*args, **kwargs)
            if kwargs.get("owner_type") == "process_recovery_receipt":
                raise RuntimeError("injected recovery pin failure")
            return value

        monkeypatch.setattr(ctx.records.catalog, "reference", fail_after_pin)
    before = await snapshot(ctx)
    with pytest.raises((ValueError, PermissionError, RuntimeError)):
        await recover(ctx, request)
    assert await snapshot(ctx) == before
    async with database.transaction() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactReferenceRow)
                .where(ArtifactReferenceRow.owner_type == "process_recovery_receipt")
            )
            == 0
        )
        _, worker = await ctx.process.workers.registration(session, ctx.worker_id)
        assert worker.status == ("revoked" if failure == "own_pin" else "active")


async def test_changed_recovery_retry_is_not_a_new_review(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now)
    await expire(ctx)
    receipt = await recover(ctx)
    before = await snapshot(ctx)
    with pytest.raises(PermissionError, match="different reviewed request"):
        await recover(ctx, receipt.request.model_copy(update={"resume": True}))
    assert await snapshot(ctx) == before


async def test_authority_expiring_during_review_never_publishes_ready_state(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.pprl import recovery as recovery_module

    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    await expire(ctx)
    original = ctx.amber.resources.release_unstarted

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ctx.authorization.expires_at + timedelta(seconds=1)

    async def slow_review(*args, **kwargs):
        result = await original(*args, **kwargs)
        monkeypatch.setattr(recovery_module, "datetime", ExpiredClock)
        return result

    monkeypatch.setattr(ctx.amber.resources, "release_unstarted", slow_review)
    receipt = await recover(ctx, reviewed(ctx, resume=True))
    assert receipt.disposition == "paused" and receipt.created_at > ctx.authorization.expires_at
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, ctx.claim.rollout.rollout_id)
        assert row.paused and row.status == RolloutStatus.PAUSED.value
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
