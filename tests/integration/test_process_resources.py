import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select, update

from padawan.governance.amber import AmberAdmissionDisposition, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ArtifactReferenceRow,
    ProcessResourceAccountRow,
    ProcessResourceEventRow,
    ProcessResourceGrantRow,
    ProcessResourceReservationRow,
    ProcessRolloutRow,
)
from padawan.pprl.content import ProcessContentDeniedError
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.generation import ProcessGenerationUnavailableError
from padawan.pprl.store import ProcessForkChildPlan, ProcessStore
from tests.helpers import CallbackGenerationClient
from tests.integration.test_process_generation_workloads import _ready
from tests.pprl_helpers import NOW, fund_resources
from tests.pprl_resource_helpers import commit_resource_action, resource_action, resource_context


async def _admit(ctx, action, *, decision_id=None):
    async with ctx.database.transaction() as session:
        return await ctx.amber.admit(
            session, request=action, active_workers=0, decision_id=decision_id
        )


async def _balance(ctx):
    async with ctx.database.transaction() as session:
        return await ctx.amber.resources.inspect(session, ctx.authorization.digest)


async def test_no_implicit_funding_and_no_legacy_balance_reset(database, tmp_path, pprl_now):
    ctx = await resource_context(database, tmp_path, pprl_now, rollouts=0, fund=False)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="resource account"):
            await ctx.process.create_rollout(
                session,
                execution_digest=sha256_digest(ctx.execution),
                replication_index=0,
                initial_state=ProjectStatePayload(objective="unfunded"),
                created_at=pprl_now(),
            )
        assert await session.scalar(select(func.count()).select_from(ProcessRolloutRow)) == 0


async def test_duplicate_admission_and_other_rollout_share_one_capacity(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(
        database, tmp_path, pprl_now, rollouts=2, caps={"input_tokens": 70}
    )
    _, first = await resource_action(ctx, tokens=40, worker="first")
    _, second = await resource_action(ctx, tokens=40, worker="replacement")
    admitted = await _admit(ctx, first, decision_id="resource-first")
    assert admitted.disposition == AmberAdmissionDisposition.ADMITTED
    assert await _admit(ctx, first, decision_id="resource-first") == admitted
    duplicate = await _admit(ctx, first, decision_id="relabeled-attempt")
    assert "resource_action_already_reserved" in duplicate.reason_codes
    denied = await _admit(ctx, second)
    assert "shared_resource_capacity_exhausted" in denied.reason_codes
    balance = await _balance(ctx)
    assert balance.held.input_tokens == 40 and balance.charged.input_tokens == 0
    assert balance.open_reservations == 1
    with pytest.raises(ValueError, match="different content"):
        await _admit(ctx, second, decision_id="resource-first")


async def test_generic_commit_is_charged_once_and_full_chain_replays(database, tmp_path, pprl_now):
    ctx = await resource_context(database, tmp_path, pprl_now)
    claim, action = await resource_action(ctx)
    decision = await _admit(ctx, action)
    event, state = await commit_resource_action(ctx, claim, action, decision)
    async with database.transaction() as session:
        await ctx.amber.resources.settle_event(
            session,
            decision_id=decision.decision_id,
            event_digest=event.event_digest,
            now=pprl_now(),
        )
        journal = await ctx.amber.resources.replay(session, ctx.authorization.digest)
    assert [e.kind for e in journal] == ["grant", "initial", "reserve", "settle"]
    assert journal[-1].charged.input_tokens == 40
    assert journal[-1].held.actions == 0 and journal[-1].open_reservations == 0
    assert state.payload.budget_usage.input_tokens == 40


async def test_unstarted_release_is_reviewed_and_replacement_cannot_refund_started_work(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now)
    claim, action = await resource_action(ctx)
    decision = await _admit(ctx, action)
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="reviewer"):
            await ctx.amber.resources.release_unstarted(
                session,
                decision_id=decision.decision_id,
                reviewer_id="worker",
                evidence="please refund",
                now=pprl_now(),
            )
        released = await ctx.amber.resources.release_unstarted(
            session,
            decision_id=decision.decision_id,
            reviewer_id="reviewer-a",
            evidence="reviewed cancellation before effect",
            now=pprl_now(),
        )
        repeated = await ctx.amber.resources.release_unstarted(
            session,
            decision_id=decision.decision_id,
            reviewer_id="reviewer-a",
            evidence="reviewed cancellation before effect",
            now=pprl_now(),
        )
        assert released == repeated
        with pytest.raises(PermissionError, match="another effect"):
            await ctx.amber.resources.start(
                session, decision_id=decision.decision_id, now=pprl_now()
            )
        await ctx.process.release_claim(
            session,
            rollout_id=claim.rollout.rollout_id,
            lease_token=claim.lease_token,
            to_status=RolloutStatus.ACTIVE,
            occurred_at=pprl_now(),
        )
    _, replacement = await resource_action(ctx, worker="entirely-new-worker")
    replacement_decision = await _admit(ctx, replacement)
    assert replacement_decision.disposition == AmberAdmissionDisposition.ADMITTED
    async with database.transaction() as session:
        await ctx.amber.resources.start(
            session, decision_id=replacement_decision.decision_id, now=pprl_now()
        )
        with pytest.raises(PermissionError, match="uncertain"):
            await ctx.amber.resources.release_unstarted(
                session,
                decision_id=replacement_decision.decision_id,
                reviewer_id="reviewer-a",
                evidence="worker died",
                now=pprl_now(),
            )
    assert (await _balance(ctx)).held.input_tokens == 40


@pytest.mark.parametrize("status", [AmberStatus.PAUSED, AmberStatus.REVOKED])
async def test_pause_and_revocation_prevent_dispatch_without_refunding(
    database, tmp_path, pprl_now, status
):
    ctx = await resource_context(database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    decision = await _admit(ctx, action)
    before = await _balance(ctx)
    async with database.transaction() as session:
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.authorization.digest,
            to_status=status,
            actor_id="reviewer-a",
            reason="fixture stop",
            occurred_at=pprl_now(),
        )
        with pytest.raises(PermissionError, match="authority"):
            await ctx.amber.resources.start(
                session, decision_id=decision.decision_id, now=pprl_now()
            )
    assert await _balance(ctx) == before


async def test_forks_inherit_history_and_compete_for_remaining_funding(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now, caps={"input_tokens": 90})
    parent, action = await resource_action(
        ctx, tokens=40, kind=ProcessEventKind.ROLLOUT_FORKED, worker="operator"
    )
    decision = await _admit(ctx, action)
    children = tuple(
        ProcessForkChildPlan(
            condition_id=f"condition-{n}",
            rollout_id=f"child-{n}",
            replication_index=n,
            execution=ctx.execution.model_copy(
                update={"execution_id": f"child-execution-{n}", "seed": 90 + n}
            ),
        )
        for n in (1, 2)
    )
    async with database.transaction() as session:
        fork = await ctx.process.fork_rollout(
            session,
            parent_rollout_id=parent.rollout.rollout_id,
            lease_token=parent.lease_token,
            amber_decision_id=decision.decision_id,
            actor_id="operator",
            children=children,
            intervention={"description": "matched resource fixture"},
            occurred_at=pprl_now(),
        )
        for child in fork.children:
            row = await ctx.process.get_rollout(session, rollout_id=child.rollout_id)
            state = await ctx.process.get_state(session, state_id=row.initial_state_id)
            assert state.payload.budget_usage.input_tokens == 40
        assert (
            await ctx.amber.resources.inspect(session, ctx.authorization.digest)
        ).charged.input_tokens == 40
        # Keep the parent out of the next claims; no resource money is restored by pausing it.
        await session.execute(
            update(ProcessRolloutRow)
            .where(ProcessRolloutRow.rollout_id == parent.rollout.rollout_id)
            .values(paused=True)
        )
    _, first_child = await resource_action(ctx, tokens=30, worker="new-child-worker-1")
    _, second_child = await resource_action(ctx, tokens=30, worker="new-child-worker-2")
    assert (await _admit(ctx, first_child)).disposition == AmberAdmissionDisposition.ADMITTED
    assert "shared_resource_capacity_exhausted" in (await _admit(ctx, second_child)).reason_codes
    balance = await _balance(ctx)
    assert (balance.charged.input_tokens, balance.held.input_tokens) == (40, 30)


async def test_caught_reservation_failure_and_outer_rollback_are_atomic(
    database, tmp_path, pprl_now, monkeypatch
):
    ctx = await resource_context(database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    before = await _balance(ctx)
    original = ctx.amber.resources.reserve

    async def fail_after_reservation(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected reservation failure")

    with monkeypatch.context() as patch:
        patch.setattr(ctx.amber.resources, "reserve", fail_after_reservation)
        async with database.transaction() as session:
            with pytest.raises(RuntimeError, match="injected"):
                await ctx.amber.admit(
                    session, request=action, active_workers=0, decision_id="caught"
                )
    assert await _balance(ctx) == before
    with pytest.raises(RuntimeError, match="outer"):
        async with database.transaction() as session:
            await ctx.amber.admit(session, request=action, active_workers=0, decision_id="outer")
            raise RuntimeError("outer rollback")
    async with database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessResourceReservationRow))
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 0
        )
    assert await _balance(ctx) == before


async def test_initial_state_charge_rolls_back_and_cannot_be_disguised_as_fork(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now)
    before = await _balance(ctx)
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await ctx.process.create_rollout(
                session,
                execution_digest=sha256_digest(ctx.execution),
                replication_index=2,
                initial_state=ProjectStatePayload(
                    objective="new root", budget_usage=ProjectBudgetUsage(input_tokens=40)
                ),
                created_at=pprl_now(),
            )
            raise RuntimeError("outer rollback")
    assert await _balance(ctx) == before
    async with database.transaction() as session:
        with pytest.raises(PermissionError, match="committed fork"):
            await ctx.process.create_rollout(
                session,
                execution_digest=sha256_digest(ctx.execution),
                replication_index=2,
                initial_state=ProjectStatePayload(objective="fake child"),
                parent_rollout_id="resource-rollout-0",
                fork_id="forged",
                created_at=pprl_now(),
            )


async def test_model_settlement_uses_retained_counts_and_rate_once(database, tmp_path, pprl_now):
    ctx = await resource_context(
        database,
        tmp_path,
        pprl_now,
        rate={
            "rate_id": "fixture.rate.v1",
            "input_micro_usd_per_million_tokens": 1_000_000,
            "output_micro_usd_per_million_tokens": 2_000_000,
            "request_micro_usd": 7,
        },
    )
    ready = await _ready(ctx)
    await ready.service.execute(**ready.kwargs)
    before = await _balance(ctx)
    assert before.charged.input_tokens == 10 and before.charged.output_tokens == 12
    assert before.charged.micro_usd == 41
    assert before.charged.artifact_bytes == 1_000_000
    assert before.charged.action_microseconds == 30_000_000
    assert before.open_reservations == 0
    await ready.service.execute(**ready.kwargs)
    assert await _balance(ctx) == before and len(ready.client.calls) == 1
    async with database.transaction() as session:
        pins = (
            await session.scalars(
                select(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_resource_event",
                    ArtifactReferenceRow.owner_id == before.digest,
                )
            )
        ).all()
        assert len(pins) == 3
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.reference_id == pins[0].reference_id
            )
        )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert await _balance(ctx) == before


async def test_underpriced_model_reservation_cannot_dispatch(database, tmp_path, pprl_now):
    ctx = await resource_context(
        database,
        tmp_path,
        pprl_now,
        rate={
            "rate_id": "expensive.fixture",
            "input_micro_usd_per_million_tokens": 1_000_000_000_000,
            "output_micro_usd_per_million_tokens": 1,
            "request_micro_usd": 0,
        },
    )
    ready = await _ready(ctx)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert ready.client.calls == [] and (await _balance(ctx)).held.actions == 1


async def test_known_overage_is_charged_and_stops_account_even_when_output_denied(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now, rollouts=2)

    class OverageClient(CallbackGenerationClient):
        async def generate(self, request):
            return replace(
                await super().generate(request), usage={"input_tokens": 200, "output_tokens": 12}
            )

    ready = await _ready(ctx, client=OverageClient(lambda _: "result", "test-open-weight"))
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    balance = await _balance(ctx)
    assert (
        balance.stopped and balance.charged.input_tokens == 200 and balance.open_reservations == 0
    )
    _, another = await resource_action(ctx)
    assert "resource_account_stopped" in (await _admit(ctx, another)).reason_codes


@pytest.mark.parametrize("failure", ["timeout", "missing_usage", "coerced_usage", "cancelled"])
async def test_unknown_effects_keep_money_and_concurrency_held(
    database, tmp_path, pprl_now, failure
):
    ctx = await resource_context(
        database, tmp_path, pprl_now, rollouts=2, caps={"concurrent_workers": 1}
    )

    class BrokenClient(CallbackGenerationClient):
        async def generate(self, request):
            result = await super().generate(request)
            if failure == "timeout":
                raise TimeoutError("unknown external effect")
            if failure == "cancelled":
                raise asyncio.CancelledError("unknown external effect")
            return replace(
                result,
                usage={}
                if failure == "missing_usage"
                else {"input_tokens": "10", "output_tokens": 12},
            )

    ready = await _ready(ctx, client=BrokenClient(lambda _: "result", "test-open-weight"))
    with pytest.raises(
        asyncio.CancelledError if failure == "cancelled" else ProcessGenerationUnavailableError
    ):
        await ready.service.execute(**ready.kwargs)
    balance = await _balance(ctx)
    assert balance.held.input_tokens == 64 and balance.held.micro_usd == 1_000_000
    assert balance.charged.actions == 0 and balance.open_reservations == 1
    _, another = await resource_action(ctx, worker="replacement")
    assert "shared_resource_concurrency_exhausted" in (await _admit(ctx, another)).reason_codes


@pytest.mark.parametrize("damage", ["grant", "head", "journal", "grant-json", "journal-json"])
async def test_corrupt_resource_evidence_denies_new_admission(database, tmp_path, pprl_now, damage):
    ctx = await resource_context(database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    async with database.transaction() as session:
        if damage == "grant":
            await session.execute(
                update(ProcessResourceGrantRow).values(record_digest=sha256_digest("bad"))
            )
        elif damage == "head":
            await session.execute(update(ProcessResourceAccountRow).values(sequence=999))
        elif damage == "grant-json":
            row = await session.get(ProcessResourceGrantRow, ctx.authorization.digest)
            altered = {
                **row.record_json,
                "capacity": {
                    **row.record_json["capacity"],
                    "input_tokens": str(row.record_json["capacity"]["input_tokens"]),
                },
            }
            await session.execute(update(ProcessResourceGrantRow).values(record_json=altered))
        elif damage == "journal-json":
            row = await session.scalar(
                select(ProcessResourceEventRow)
                .order_by(ProcessResourceEventRow.sequence.desc())
                .limit(1)
            )
            await session.execute(
                update(ProcessResourceEventRow)
                .where(ProcessResourceEventRow.record_digest == row.record_digest)
                .values(
                    record_json={**row.record_json, "sequence": str(row.record_json["sequence"])}
                )
            )
        else:
            await session.execute(update(ProcessResourceEventRow).values(record_json={}))
    denied = await _admit(ctx, action)
    assert denied.disposition == AmberAdmissionDisposition.DENIED
    assert "resource_accounting_invalid" in denied.reason_codes


async def test_funding_is_immutable_reviewed_and_atomic(database, tmp_path, pprl_now, monkeypatch):
    ctx = await resource_context(database, tmp_path, pprl_now, rollouts=0, fund=False)
    original = ctx.amber.resources._append

    async def fail_after_write(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("injected funding failure")

    with monkeypatch.context() as patch:
        patch.setattr(ctx.amber.resources, "_append", fail_after_write)
        async with database.transaction() as session:
            with pytest.raises(RuntimeError):
                await fund_resources(session, ctx.amber, ctx.authorization)
    async with database.transaction() as session:
        for table in (ProcessResourceGrantRow, ProcessResourceAccountRow, ProcessResourceEventRow):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        grant = await fund_resources(session, ctx.amber, ctx.authorization)
        assert await ctx.amber.resources.fund(session, grant) == grant
        with pytest.raises(PermissionError, match="reviewer"):
            await ctx.amber.resources.fund(
                session, grant.model_copy(update={"reviewed_by": "worker"})
            )
        with pytest.raises(PermissionError, match="review evidence"):
            await ctx.amber.resources.fund(
                session, grant.model_copy(update={"review_evidence": "  "})
            )
        with pytest.raises(PermissionError, match="already funded"):
            await ctx.amber.resources.fund(
                session, grant.model_copy(update={"review_evidence": "replacement funding"})
            )


async def test_legacy_rollout_cannot_receive_fresh_unaccounted_funding(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now)
    async with database.transaction() as session:
        grant = await ctx.amber.resources.grant(session, ctx.authorization.digest)
        # A historical store has project state but none of the new accounting rows.
        for table in (ProcessResourceEventRow, ProcessResourceAccountRow, ProcessResourceGrantRow):
            await session.execute(delete(table))
        with pytest.raises(PermissionError, match="legacy"):
            await ctx.amber.resources.fund(session, grant)
        assert await session.scalar(select(func.count()).select_from(ProcessResourceGrantRow)) == 0


async def test_all_brokers_replaced_after_lease_expiry_keep_unknown_spend(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now, caps={"input_tokens": 70})
    original, action = await resource_action(ctx, worker="original-broker")
    decision = await _admit(ctx, action)
    async with database.transaction() as session:
        await ctx.amber.resources.start(session, decision_id=decision.decision_id, now=pprl_now())
    ctx.amber = AmberStore()
    ctx.process = ProcessStore(ctx.amber)
    ctx.clock = lambda: NOW + timedelta(minutes=16)
    async with database.transaction() as session:
        replacement = await ctx.process.claim_next(
            session, worker_id="fresh-broker", lease_for=timedelta(minutes=15), now=ctx.clock()
        )
        assert replacement is None
        retained = await ctx.process.get_state(session, state_id=original.state.state_id)
        assert retained == original.state
    assert (await _balance(ctx)).held.input_tokens == 40


async def test_forged_completion_digest_cannot_release_a_started_slot(database, tmp_path, pprl_now):
    ctx = await resource_context(database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    decision = await _admit(ctx, action)
    async with database.transaction() as session:
        await ctx.amber.resources.start(session, decision_id=decision.decision_id, now=pprl_now())
        with pytest.raises(PermissionError, match="committed process event"):
            await ctx.amber.resources.settle_event(
                session,
                decision_id=decision.decision_id,
                event_digest=sha256_digest("worker claims done"),
                now=pprl_now(),
            )
    assert (await _balance(ctx)).open_reservations == 1


async def test_resource_receipt_digests_cannot_enter_process_state(database, tmp_path, pprl_now):
    ctx = await resource_context(database, tmp_path, pprl_now)
    _, action = await resource_action(ctx)
    decision = await _admit(ctx, action)
    async with database.transaction() as session:
        grant = await ctx.amber.resources.grant(session, ctx.authorization.digest)
        reservation, _, event = await ctx.amber.resources.reservation(session, decision.decision_id)
        for digest in (grant.digest, reservation.digest, event.digest):
            with pytest.raises(ProcessContentDeniedError):
                await ctx.process.content.check_state(
                    session, ProjectStatePayload(objective=f"inspect {digest}")
                )


async def test_unrepresentable_usage_stops_account_and_retains_unresolved_hold(
    database, tmp_path, pprl_now
):
    ctx = await resource_context(database, tmp_path, pprl_now, rollouts=2)

    class OverflowClient(CallbackGenerationClient):
        async def generate(self, request):
            return replace(
                await super().generate(request),
                usage={"input_tokens": 10**100, "output_tokens": 12},
            )

    ready = await _ready(ctx, client=OverflowClient(lambda _: "result", "test-open-weight"))
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    balance = await _balance(ctx)
    assert balance.stopped and balance.kind == "stop"
    assert balance.open_reservations == 1 and balance.held.input_tokens == 64
    assert balance.charged.input_tokens == 0
    async with database.transaction() as session:
        assert len(await ctx.amber.resources.replay(session, ctx.authorization.digest)) >= 4
