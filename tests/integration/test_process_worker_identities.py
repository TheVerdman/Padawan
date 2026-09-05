import dataclasses
import pickle
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from padawan.models.hashing import canonical_json_bytes
from padawan.models.tables import ProcessContainerWorkloadRow, ProcessWorkerDecisionBindingRow
from padawan.pprl.containers import ProcessContainerUnavailableError
from padawan.pprl.contracts import RolloutStatus
from padawan.pprl.observations import ProcessObservationDeniedError
from tests.container_helpers import commit_container_action, container_context


@pytest.mark.parametrize("operation", ["admit", "start", "release", "commit", "observe", "claim"])
async def test_legacy_apis_do_not_bypass_enrolled_authority(
    database, tmp_path, pprl_now, operation
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    async with database.transaction() as session:
        if operation == "claim":
            assert (
                await ctx.process.claim_next(
                    session, worker_id=ctx.worker_id, lease_for=timedelta(minutes=1), now=pprl_now()
                )
                is None
            )
            return
        if operation == "commit":
            ctx.worker_access = None
            with pytest.raises(PermissionError):
                await commit_container_action(ctx)
            return
        calls = {
            "admit": lambda: ctx.amber.admit(session, request=ctx.action, active_workers=0),
            "start": lambda: ctx.amber.resources.start(
                session, decision_id=ctx.decision.decision_id, now=pprl_now()
            ),
            "release": lambda: ctx.process.release_claim(
                session,
                rollout_id=ctx.claim.rollout.rollout_id,
                lease_token=ctx.claim.lease_token,
                occurred_at=pprl_now(),
                to_status=RolloutStatus.ACTIVE,
            ),
            "observe": lambda: ctx.observations.read(
                session,
                observation_id=ctx.observed.observation_id,
                rollout_id=ctx.claim.rollout.rollout_id,
                lease_token=ctx.claim.lease_token,
                worker_id=ctx.worker_id,
                now=pprl_now(),
            ),
        }
        with pytest.raises((PermissionError, ProcessObservationDeniedError)):
            await calls[operation]()


@pytest.mark.parametrize("change", ["role_id", "worker_model_digest"])
async def test_admission_cannot_change_assigned_role_or_model(database, tmp_path, pprl_now, change):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    altered = "another-role" if change == "role_id" else "sha256:" + "a" * 64
    async with database.transaction() as session:
        with pytest.raises(PermissionError):
            await ctx.amber.admit(
                session,
                request=ctx.action.model_copy(update={change: altered}),
                active_workers=0,
                worker_access=ctx.worker_access,
            )


async def test_actor_is_not_a_caller_selected_attribution(database, tmp_path, pprl_now):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    await ctx.service.execute(**ctx.kwargs)
    ctx.worker_id = "someone-else"
    with pytest.raises(PermissionError, match="actor"):
        await commit_container_action(ctx)


async def test_expired_credential_cannot_be_revived_by_a_backdated_request(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.pprl import worker_identities

    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    original_time = pprl_now()

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return original_time + timedelta(hours=2)

    monkeypatch.setattr(worker_identities, "datetime", Later)
    async with database.transaction() as session:
        with pytest.raises(PermissionError):
            await ctx.process.workers.identify(session, ctx.worker_access, now=original_time)
        record, head = await ctx.process.workers.registration(session, ctx.worker_id)
        assert head.status == "active" and record.expires_at < Later.now(UTC)


@pytest.mark.parametrize("invalid", ["issuer", "model", "capability", "expiry", "capacity"])
async def test_issuance_is_reviewed_scoped_and_capacity_bounded(
    database, tmp_path, pprl_now, invalid
):
    from padawan.models.hashing import sha256_digest

    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    now = pprl_now()
    async with database.transaction() as session:
        if invalid != "capacity":
            await ctx.process.workers.revoke(
                session,
                worker_id=ctx.worker_id,
                revoked_by="reviewer-a",
                evidence="fixture freeing registration capacity",
                now=now,
            )
        fields = dict(
            execution_digest=sha256_digest(ctx.execution),
            role_id=ctx.profile.role_id,
            worker_model_digest=ctx.profile.worker_model_digest,
            declared_capabilities=("reasoning",),
            issued_by="reviewer-a",
            evidence="fixture issuance probe",
            expires_at=now + timedelta(minutes=30),
            now=now,
        )
        fields.update(
            {
                "issuer": {"issued_by": "worker"},
                "model": {"worker_model_digest": "sha256:" + "f" * 64},
                "capability": {"declared_capabilities": ()},
                "expiry": {"expires_at": now + timedelta(hours=2)},
                "capacity": {},
            }[invalid]
        )
        with pytest.raises(PermissionError):
            await ctx.process.workers.issue(session, **fields)


async def test_no_implicit_enrollment_or_legacy_backfill(database, tmp_path, pprl_now):
    from padawan.models.hashing import sha256_digest
    from padawan.pprl.worker_contracts import ProcessWorkerScope

    ctx = await container_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="no rollouts"):
            await ctx.process.workers.enroll(
                session,
                ProcessWorkerScope(
                    execution_digest=sha256_digest(ctx.execution),
                    authorization_digest=ctx.authorization.digest,
                    broker_audience="fixture-broker",
                    maximum_credential_seconds=300,
                    maximum_registered_workers=1,
                    reviewed_by="reviewer-a",
                    review_evidence="late enrollment",
                    created_at=pprl_now(),
                ),
            )


async def test_issued_worker_owns_observed_admission_and_synthetic_effect(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    receipt = await ctx.service.execute(**ctx.kwargs)
    assert receipt.effect_status == "terminated" and receipt.complete_capture
    event, _ = await commit_container_action(ctx)
    assert event.actor_id == ctx.worker_id
    async with database.transaction() as session:
        assignment = await ctx.process.workers.assignment(session, ctx.worker_access.assignment_id)
        binding = await ctx.process.workers.require_decision(
            session, assignment=assignment, decision_id=ctx.decision.decision_id
        )
        assert binding.worker_id == ctx.worker_id
        assert assignment.worker_model_digest == ctx.profile.worker_model_digest
        record, _ = await ctx.process.workers.registration(session, ctx.worker_id)
    secret = ctx.worker_access.credential.get_secret_value()
    assert secret not in record.model_dump_json() + event.model_dump_json() + repr(
        ctx.worker_access
    )
    observation_bytes = canonical_json_bytes(ctx.observed.observation)
    assert (
        secret.encode() not in observation_bytes and ctx.worker_id.encode() not in observation_bytes
    )
    assert ctx.worker_access.assignment_id.encode() not in observation_bytes
    with pytest.raises(TypeError):
        canonical_json_bytes(ctx.worker_access)
    with pytest.raises(TypeError):
        pickle.dumps(ctx.worker_access)


@pytest.mark.parametrize("changed", ["missing", "credential", "audience", "worker", "assignment"])
async def test_enrolled_execution_refuses_forged_access_before_container_intent(
    database, tmp_path, pprl_now, changed
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    updates = {
        "credential": {"credential": SecretStr("f" * 64)},
        "audience": {"broker_audience": "other-broker"},
        "worker": {"worker_id": "process-worker-" + "f" * 32},
        "assignment": {"assignment_id": "worker-assignment-" + "f" * 32},
    }
    access = (
        None if changed == "missing" else dataclasses.replace(ctx.worker_access, **updates[changed])
    )
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**{**ctx.kwargs, "worker_access": access})
    assert not ctx.driver.calls
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessContainerWorkloadRow)) == 0
        )
        with pytest.raises(ProcessObservationDeniedError):
            await ctx.observations.read(
                session,
                observation_id=ctx.observed.observation_id,
                rollout_id=ctx.claim.rollout.rollout_id,
                lease_token=ctx.claim.lease_token,
                worker_id=ctx.worker_id,
                now=pprl_now(),
                worker_access=access,
            )


async def test_worker_revocation_preserves_admission_and_blocks_dispatch(
    database, tmp_path, pprl_now
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    async with database.transaction() as session:
        revoked = await ctx.process.workers.revoke(
            session,
            worker_id=ctx.worker_id,
            revoked_by="reviewer-a",
            evidence="fixture revocation",
            now=pprl_now(),
        )
        assert revoked.worker_id == ctx.worker_id
        assert (
            await session.get(ProcessWorkerDecisionBindingRow, ctx.decision.decision_id) is not None
        )
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)
    with pytest.raises(PermissionError):
        await commit_container_action(ctx)
    async with database.transaction() as session:
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert balance.open_reservations == 1 and balance.held.actions == 1
    assert not ctx.driver.calls
