"""Synthetic terminal resolution; no provider, live worker or physical container launch."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update

from padawan.artifacts.store import ArtifactIntegrityError
from padawan.governance.amber import AmberStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ProcessAbandonmentRow,
    ProcessEventRow,
    ProcessRecoveryRow,
    ProcessRolloutRow,
)
from padawan.pprl.abandonment import ProcessAbandonmentStore
from padawan.pprl.abandonment_contracts import ProcessAbandonmentRequest
from padawan.pprl.content import ProcessContentDeniedError
from padawan.pprl.contracts import ProjectStatePayload, RolloutStatus
from padawan.pprl.store import ProcessInvariantError
from padawan.training.contracts import EvidenceSourceKind, TrainingExclusionReason
from padawan.training.pprl import compile_pprl_snapshot
from tests.container_helpers import commit_container_action, container_context
from tests.integration.test_process_recovery import expire, recover, recovery, reviewed
from tests.pprl_evidence_helpers import evidence_context as evidence_context


async def stopped(database, tmp_path, clock, *, completed=True):
    ctx = await container_context(database, tmp_path, clock, enrolled=True)
    if completed:
        await ctx.service.execute(**ctx.kwargs)
    else:
        async with database.transaction() as session:
            await ctx.amber.resources.start(
                session,
                decision_id=ctx.decision.decision_id,
                now=clock(),
                worker_access=ctx.worker_access,
            )
    await expire(ctx)
    ctx.recovered = await recover(ctx)
    ctx.abandonment = ProcessAbandonmentStore(recovery(ctx))
    return ctx


async def review(ctx, **changes):
    async with ctx.database.transaction() as session:
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        history = await ctx.amber.history(session, authorization_digest=ctx.authorization.digest)
    instant = ctx.clock()
    data = dict(
        abandonment_id="process-abandonment-" + uuid4().hex,
        rollout_id=ctx.recovered.request.rollout_id,
        recovery_id=ctx.recovered.request.recovery_id,
        recovery_digest=ctx.recovered.digest,
        expected_state_digest=ctx.recovered.state_digest,
        expected_authorization_sequence=history[-1].sequence,
        expected_account_digest=account.digest,
        reviewer_id="reviewer-a",
        reason="synthetic infrastructure loss; do not infer a domain result",
        exclusion="unresolved_external_effect"
        if any(e.disposition == "unknown" for e in ctx.recovered.effects)
        else "infrastructure_interruption",
        reviewed_at=instant,
        expires_at=instant + timedelta(minutes=1),
    )
    return ProcessAbandonmentRequest.model_validate({**data, **changes})


async def abandon(ctx, request):
    async with ctx.database.transaction() as session:
        return await ctx.abandonment.abandon(session, request, now=ctx.clock())


async def snapshot(ctx):
    async with ctx.database.transaction() as session:
        head = await session.get(ProcessRolloutRow, ctx.recovered.request.rollout_id)
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        return (
            head.current_state_id,
            head.sequence,
            head.status,
            head.paused,
            head.terminal_abandonment_id,
            head.updated_at,
            account.digest,
            await session.scalar(select(func.count()).select_from(ProcessAbandonmentRow)),
            tuple(
                (r.artifact_id, r.owner_type, r.owner_id)
                for r in await session.scalars(
                    select(ArtifactReferenceRow).order_by(
                        ArtifactReferenceRow.artifact_id,
                        ArtifactReferenceRow.owner_type,
                        ArtifactReferenceRow.owner_id,
                    )
                )
            ),
        )


@pytest.mark.parametrize("completed", [True, False])
async def test_abandonment_is_terminal_without_state_action_refund_or_reexecution(
    database, tmp_path, pprl_now, completed
):
    ctx = await stopped(database, tmp_path, pprl_now, completed=completed)
    request = await review(ctx)
    before = await snapshot(ctx)
    receipt = await abandon(ctx, request)
    after = await snapshot(ctx)
    assert after[:2] == before[:2]  # no invented state transition
    assert after[2:5] == ("cancelled", True, request.abandonment_id)
    assert after[6] == before[6]  # exactly the original hold/charge; no accounting event
    assert await abandon(ctx, request) == receipt
    assert await snapshot(ctx) == after
    async with database.transaction() as session:
        assert (
            await ProcessAbandonmentStore(recovery(ctx)).read(
                session, abandonment_id=request.abandonment_id
            )
            == receipt
        )
        assert (
            await ctx.process.get_rollout(session, rollout_id=request.rollout_id)
        ).status == RolloutStatus.CANCELLED
        initial, events = await ctx.process.replay(session, rollout_id=request.rollout_id)
        assert initial == ctx.claim.state and events == ()
        assert await session.scalar(select(func.count()).select_from(ProcessEventRow)) == 0
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert account.held.actions == (0 if completed else 1)
        assert account.charged.actions == (1 if completed else 0)
        assert (
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=1), now=pprl_now()
            )
            is None
        )
        with pytest.raises(PermissionError, match="abandoned"):
            await ctx.amber.resources.assert_rollout_recoverable(
                session, rollout_id=request.rollout_id
            )
    with pytest.raises(PermissionError, match="terminal"):
        await recover(
            ctx, reviewed(ctx, resume=True).model_copy(update={"expected_lease_token_digest": None})
        )
    with pytest.raises(PermissionError):
        await abandon(ctx, await review(ctx))
    with pytest.raises(ProcessInvariantError, match="lease is missing or stale"):
        await commit_container_action(ctx)
    assert len(ctx.driver.calls) == (1 if completed else 0)


@pytest.mark.parametrize(
    "field",
    [
        "state",
        "recovery_digest",
        "recovery_id",
        "reviewer",
        "sequence",
        "account",
        "exclusion",
        "expired",
    ],
)
async def test_stale_or_wrong_review_rolls_back_all_mutations(database, tmp_path, pprl_now, field):
    ctx = await stopped(database, tmp_path, pprl_now)
    changes = {
        "state": {"expected_state_digest": sha256_digest("other")},
        "recovery_digest": {"recovery_digest": sha256_digest("other")},
        "recovery_id": {"recovery_id": "process-recovery-" + uuid4().hex},
        "reviewer": {"reviewer_id": ctx.worker_id},
        "sequence": {"expected_authorization_sequence": 99},
        "account": {"expected_account_digest": sha256_digest("other")},
        "exclusion": {"exclusion": "unresolved_external_effect"},
        "expired": {
            "reviewed_at": pprl_now() - timedelta(minutes=2),
            "expires_at": pprl_now() - timedelta(minutes=1),
        },
    }[field]
    request = await review(ctx, **changes)
    before = await snapshot(ctx)
    with pytest.raises((ValueError, PermissionError)):
        await abandon(ctx, request)
    assert await snapshot(ctx) == before


@pytest.mark.parametrize("outer", [False, True])
async def test_publication_failure_and_outer_rollback_preserve_head_sources_and_account(
    database, tmp_path, pprl_now, monkeypatch, outer
):
    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    before = await snapshot(ctx)
    original = ctx.records.catalog.reference

    async def fail(session, reference, **kwargs):
        await original(session, reference, **kwargs)
        if kwargs["owner_type"] == "process_abandonment":
            raise RuntimeError("simulated lost disposition publication")

    if not outer:
        monkeypatch.setattr(ctx.records.catalog, "reference", fail)
        # Caller catches the failure and commits its outer transaction.
        async with database.transaction() as session:
            with pytest.raises(RuntimeError, match="publication"):
                await ctx.abandonment.abandon(session, request, now=pprl_now())
    else:
        with pytest.raises(RuntimeError, match="outer"):
            async with database.transaction() as session:
                await ctx.abandonment.abandon(session, request, now=pprl_now())
                raise RuntimeError("outer transaction lost")
    assert await snapshot(ctx) == before


@pytest.mark.parametrize(
    "damage", ["marker", "status", "receipt", "recovery", "own_pin", "source_pin", "bytes"]
)
async def test_reopened_disposition_and_replay_fail_closed_on_lost_evidence(
    database, tmp_path, pprl_now, damage
):
    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    await abandon(ctx, request)
    async with database.transaction() as session:
        if damage in {"marker", "status"}:
            await session.execute(
                update(ProcessRolloutRow)
                .where(ProcessRolloutRow.rollout_id == request.rollout_id)
                .values(
                    **(
                        {"terminal_abandonment_id": "process-abandonment-" + uuid4().hex}
                        if damage == "marker"
                        else {"status": "active"}
                    )
                )
            )
        elif damage in {"receipt", "recovery"}:
            await session.execute(
                update(ProcessAbandonmentRow if damage == "receipt" else ProcessRecoveryRow).values(
                    record_digest=sha256_digest("corrupt fixture")
                )
            )
        elif damage in {"own_pin", "source_pin"}:
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type
                    == (
                        "process_abandonment" if damage == "own_pin" else "process_recovery_receipt"
                    )
                )
            )
    if damage == "bytes":
        source = ctx.recovered.effects[0].sources[0].reference.artifact
        ctx.records.catalog.backend._path_for_hex(source.digest[7:]).write_bytes(
            b"corrupted fixture"
        )
    with pytest.raises((ValueError, PermissionError, ArtifactIntegrityError)):
        async with database.transaction() as session:
            await ProcessAbandonmentStore(recovery(ctx)).read(
                session, abandonment_id=request.abandonment_id
            )


async def test_compiler_keeps_population_and_private_disposition_at_original_watermark(
    database, tmp_path, pprl_now
):
    ctx = await stopped(database, tmp_path, pprl_now)
    before_time = pprl_now()
    async with database.transaction() as session:
        before = await compile_pprl_snapshot(session, as_of=before_time)
    request = await review(ctx)
    receipt = await abandon(ctx, request)
    async with database.transaction() as session:
        historical = await compile_pprl_snapshot(session, as_of=before_time)
        current = await compile_pprl_snapshot(session, as_of=pprl_now())
    assert historical == before
    assert current.rollout_ids == before.rollout_ids == (request.rollout_id,)
    assert current.source_snapshot_digest != before.source_snapshot_digest
    assert not any(current.products.values())
    evidence = {
        entry.source_kind: entry
        for entry in current.evidence_rows
        if entry.source_kind
        in {EvidenceSourceKind.PROCESS_ABANDONMENT, EvidenceSourceKind.PROCESS_RECOVERY}
    }
    assert evidence.keys() == {
        EvidenceSourceKind.PROCESS_ABANDONMENT,
        EvidenceSourceKind.PROCESS_RECOVERY,
    }
    assert receipt.digest in {
        digest for entry in evidence.values() for digest in entry.source_record_digests
    }
    exclusions = [
        e
        for e in current.exclusions
        if TrainingExclusionReason.PROCESS_ROLLOUT_ABANDONED in e.reason_codes
    ]
    assert len(exclusions) == 2
    assert all(receipt.digest in e.source_record_digests for e in exclusions)


@pytest.mark.parametrize("status", [AmberStatus.PAUSED, AmberStatus.REVOKED, AmberStatus.EXPIRED])
async def test_stop_authority_is_not_execution_authority_and_expired_retry_is_read_only(
    database, tmp_path, pprl_now, status
):
    ctx = await stopped(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=status,
            actor_id="reviewer-a",
            reason="synthetic stop",
            occurred_at=pprl_now(),
        )
    request = await review(ctx)
    receipt = await abandon(ctx, request)
    async with database.transaction() as session:
        assert (
            await ctx.abandonment.abandon(
                session, request, now=request.expires_at + timedelta(days=1)
            )
            == receipt
        )
        assert (
            await ctx.amber.status(session, authorization_digest=ctx.authorization.digest) == status
        )


async def test_private_disposition_identifiers_never_become_state_content(
    database, tmp_path, pprl_now
):
    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    receipt = await abandon(ctx, request)
    for value in (request.abandonment_id, request.digest, receipt.digest):
        async with database.transaction() as session:
            with pytest.raises(ProcessContentDeniedError):
                await ctx.process.content.check_state(session, ProjectStatePayload(objective=value))


async def test_native_restart_retains_terminal_disposition_without_old_python_objects(
    database, tmp_path
):
    from datetime import UTC, datetime

    from tests.integration.test_process_recovery_restart import run_broker

    ctx = await stopped(database, tmp_path, lambda: datetime.now(UTC))
    request = await review(ctx)
    config = dict(database_url=database.url, artifact_root=str(ctx.records.catalog.backend.root))
    disposed = await run_broker(
        dict(**config, mode="abandon", request=request.model_dump(mode="json"))
    )
    reopened = await run_broker(
        dict(**config, mode="inspect_abandonment", abandonment_id=request.abandonment_id)
    )
    assert disposed == reopened
    assert disposed["status_after"] == "cancelled"
    assert (await snapshot(ctx))[6] == request.expected_account_digest


async def test_validation_deadline_expiry_rolls_back_terminal_marker_and_pins(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.pprl import abandonment as module

    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    before = await snapshot(ctx)
    original = ctx.abandonment.read

    async def expire_during_read(session, **kwargs):
        result = await original(session, **kwargs)
        monkeypatch.setattr(
            module,
            "datetime",
            type("LateClock", (), {"now": staticmethod(lambda tz: request.expires_at)}),
        )
        return result

    monkeypatch.setattr(ctx.abandonment, "read", expire_during_read)
    with pytest.raises(PermissionError, match="expired"):
        await abandon(ctx, request)
    assert await snapshot(ctx) == before


@pytest.mark.parametrize("forgery", ["omitted_effect", "old_account"])
async def test_self_consistent_outer_hashes_do_not_replace_native_effect_or_account_frontier(
    database, tmp_path, pprl_now, forgery
):
    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    receipt = await abandon(ctx, request)
    async with database.transaction() as session:
        if forgery == "omitted_effect":
            forged = ctx.recovered.model_copy(update={"effects": ()})
            await session.execute(
                update(ProcessRecoveryRow).values(
                    record_digest=forged.digest, record_json=forged.model_dump(mode="json")
                )
            )
            changed = request.model_copy(update={"recovery_digest": forged.digest})
        else:
            journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
            changed = request.model_copy(update={"expected_account_digest": journal[0].digest})
        replacement = receipt.model_copy(
            update={"request": changed, "request_digest": changed.digest}
        )
        await session.execute(
            update(ProcessAbandonmentRow).values(
                request_digest=changed.digest,
                record_digest=replacement.digest,
                record_json=replacement.model_dump(mode="json"),
            )
        )
    with pytest.raises(ValueError, match="unresolved effect|resource frontier"):
        async with database.transaction() as session:
            await ctx.abandonment.read(session, abandonment_id=request.abandonment_id)


async def test_late_result_after_terminal_abandonment_is_retained_charged_and_never_reopens(
    evidence_context,
):
    import asyncio

    from padawan.pprl.generation import ProcessGenerationUnavailableError
    from tests.helpers import CallbackGenerationClient
    from tests.integration.test_process_generation_workloads import _ready
    from tests.integration.test_process_recovery_effects import model_context

    entered, finish = asyncio.Event(), asyncio.Event()

    class Delayed(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            entered.set()
            await finish.wait()
            return await super().generate_prepared(request, prepared)

    ready = await _ready(
        evidence_context,
        client=Delayed(lambda request: "late fixture candidate", "test-open-weight"),
    )
    ctx = await model_context(ready)
    task = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await expire(ctx)
        ctx.recovered = await recover(ctx)
        ctx.abandonment = ProcessAbandonmentStore(recovery(ctx))
        request = await review(ctx)
        receipt = await abandon(ctx, request)
    finally:
        finish.set()
    with pytest.raises(ProcessGenerationUnavailableError):
        await asyncio.wait_for(task, 5)
    async with ctx.database.transaction() as session:
        assert await ctx.abandonment.read(session, abandonment_id=request.abandonment_id) == receipt
        account = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        assert account.held.actions == 0 and account.charged.actions == 1
        assert (
            await ctx.process.get_rollout(session, rollout_id=request.rollout_id)
        ).status == RolloutStatus.CANCELLED
    assert len(ready.client.calls) == 1


async def test_migration_refuses_to_remove_populated_terminal_history(
    database, tmp_path, pprl_now, monkeypatch
):
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    await abandon(ctx, request)
    before = await snapshot(ctx)
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database.url)
    monkeypatch.setenv("PADAWAN_DATABASE_URL", database.url)
    # Exercise this migration's refusal independently of later additive migrations.
    # SQLite DDL is not transactional: an empty later migration could otherwise
    # downgrade successfully before this populated history correctly refuses.
    await asyncio.to_thread(command.stamp, config, "a7c82e41d906")
    with pytest.raises(RuntimeError, match="populated abandonment"):
        await asyncio.to_thread(command.downgrade, config, "f4d63b18a920")
    assert await snapshot(ctx) == before


async def test_later_success_or_eligibility_declarations_cannot_override_abandonment(
    database, tmp_path, pprl_now
):
    from padawan.pprl.contracts import (
        OutcomeDisposition,
        ProcessLearningLane,
        ProcessOutcomeAssessment,
        ProcessOutcomeComponent,
        ProcessTrainingEligibilityDecision,
    )

    ctx = await stopped(database, tmp_path, pprl_now)
    request = await review(ctx)
    await abandon(ctx, request)
    outcome = ProcessOutcomeAssessment(
        assessment_id="test.later-success",
        rollout_id=request.rollout_id,
        authority=ctx.program.reward_authority,
        components=(
            ProcessOutcomeComponent(
                component_id="correctness",
                disposition=OutcomeDisposition.SUCCESS,
                deterministic=True,
                value=1.0,
                evidence_refs=("test.declared-evidence",),
            ),
        ),
        scalar_return=1.0,
        eligible_for_learning=True,
        created_at=pprl_now(),
    )
    eligible = ProcessTrainingEligibilityDecision(
        decision_id="test.later-eligibility",
        rollout_id=request.rollout_id,
        outcome_assessment_ids=(outcome.assessment_id,),
        policy_id="test.eligibility",
        policy_version="1.0.0",
        eligible=True,
        allowed_lanes=(ProcessLearningLane.TRAJECTORY,),
        rights_digests=(sha256_digest(ctx.execution.output_rights),),
        evidence_refs=("test.declared-evidence",),
        reason="synthetic attempted override",
        decided_by="reviewer-a",
        created_at=pprl_now(),
    )
    before = await snapshot(ctx)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="abandoned"):
            await ctx.process.record_outcome(session, outcome)
        with pytest.raises(PermissionError, match="abandoned"):
            await ctx.process.record_training_eligibility(session, eligible)
        # Retaining a separately declared non-learning outcome remains possible.
        await ctx.process.record_outcome(
            session, outcome.model_copy(update={"eligible_for_learning": False})
        )
    assert await snapshot(ctx) == before
