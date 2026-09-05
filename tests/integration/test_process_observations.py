from __future__ import annotations

import asyncio
import json
from datetime import UTC, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from padawan.governance.amber import AmberActionRequest, AmberAdmissionDisposition, AmberStatus
from padawan.models.contracts import RightsUse
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    AmberAuthorizationHeadRow,
    ArtifactReferenceRow,
    ProcessContentAdmissionRow,
    ProcessObservationDecisionRow,
    ProcessObservationRow,
    ProcessRolloutRow,
    ProcessStateRow,
)
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.observation_contracts import ProcessWorkerObservation
from padawan.pprl.observations import ProcessObservationDeniedError, ProcessObservationStore
from tests.pprl_evidence_helpers import _admit, _counts, _model_sources
from tests.pprl_evidence_helpers import evidence_context as evidence_context
from tests.pprl_helpers import NOW


async def _claim(ctx, *, worker_id="observation-worker"):
    async with ctx.database.transaction() as session:
        claimed = await ctx.process.claim_next(
            session, worker_id=worker_id, lease_for=timedelta(minutes=5), now=ctx.clock()
        )
        assert claimed is not None
        return claimed


def _scope(claimed, worker_id="observation-worker"):
    return {
        "rollout_id": claimed.rollout.rollout_id,
        "lease_token": claimed.lease_token,
        "worker_id": worker_id,
    }


async def _observe(ctx, claimed, *, boundary=None, worker_id="observation-worker"):
    boundary = boundary or ProcessObservationStore(ctx.process)
    async with ctx.database.transaction() as session:
        return await boundary.observe_claim(session, **_scope(claimed, worker_id), now=ctx.clock())


async def _reviewed_claim(ctx):
    sources = await _model_sources(ctx)  # synthetic callback, no inference; root stays leased
    review = await _admit(ctx, sources=sources)
    async with ctx.database.transaction() as session:
        await ctx.process.create_rollout(
            session,
            execution_digest=ctx.execution_digest,
            replication_index=1,
            rollout_id="observation-with-evidence",
            created_at=ctx.clock(),
            initial_state=ProjectStatePayload(
                objective="Continue the checked calculation; café",
                plan=("Use the reviewed result",),
                artifact_refs=(review.process_reference,),
                memory_refs=(review.process_reference.process_artifact_id,),
                budget_usage=ProjectBudgetUsage(artifact_bytes=review.process_reference.size_bytes),
            ),
        )
    claimed = await _claim(ctx)
    assert claimed.rollout.rollout_id == "observation-with-evidence"
    return claimed, review, sources


def _request(ctx, claimed, *, target="scientific_math", **changes):
    previous = claimed.state.payload.budget_usage
    usage = previous.model_copy(
        update={
            "actions": previous.actions + 1,
            "wall_time_seconds": float(previous.wall_time_seconds) + 1.0,
        }
    )
    request = AmberActionRequest(
        authorization_digest=ctx.execution.amber_authorization_digest,
        rollout_id=claimed.rollout.rollout_id,
        rollout_sequence=claimed.rollout.sequence,
        state_digest=claimed.state.state_digest,
        lease_token_digest=sha256_digest(claimed.lease_token),
        program_digest=ctx.execution.program_digest,
        distribution_digest=ctx.execution.distribution_digest,
        split=ctx.split,
        persistence_mode=ctx.program.persistence_mode,
        event_kind=ProcessEventKind.CLAIM_UPDATED,
        role_id="researcher",
        worker_model_digest=sha256_digest(ctx.execution.worker_models[0]),
        target_class=target,
        environment_fingerprint=ctx.execution.environment_fingerprint,
        projected_usage=usage,
        projected_artifact_bytes=usage.artifact_bytes,
        requested_at=ctx.clock(),
    )
    return request.model_copy(update=changes)


def _uniform(failure):
    assert str(failure.value) == "process observation denied"
    assert failure.value.__context__ is None and failure.value.__cause__ is None


async def test_public_observation_and_privileged_receipt_have_separate_surfaces(evidence_context):
    ctx = evidence_context
    claimed, review, sources = await _reviewed_claim(ctx)
    boundary = ProcessObservationStore(ctx.process)
    delivered = await _observe(ctx, claimed, boundary=boundary)
    public = delivered.observation_bytes
    assert public == canonical_json_bytes(delivered.observation)
    assert set(json.loads(public)) == {"schema_version", "state"}
    assert set(json.loads(public)["state"]) == {
        "objective",
        "plan",
        "hypotheses",
        "claims",
        "artifact_refs",
        "dependencies",
        "budget_usage",
        "worker_assignments",
        "unresolved_risks",
        "memory_refs",
        "extension_state",
    }
    assert delivered.observation.state.artifact_refs == (review.process_reference,)
    async with ctx.database.transaction() as session:
        receipt = await boundary.inspect_receipt(session, observation_id=delivered.observation_id)
        content = await session.get(ProcessContentAdmissionRow, ("state", claimed.state.state_id))
        assert receipt.content_receipt_digest == content.record_digest
        assert receipt.state_digest == claimed.state.state_digest
        assert receipt.execution_digest == ctx.execution_digest
        assert receipt.policy == boundary.policy
        assert receipt.authorization_sequence == 2
        assert receipt.worker_id == "observation-worker"
        assert receipt.lease_token_digest == sha256_digest(claimed.lease_token)
        assert receipt.observation_digest == sha256_digest(public)
        assert receipt.observation_json.encode("utf-8") == public
        retained = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_observation",
                    ArtifactReferenceRow.owner_id == delivered.observation_id,
                )
            )
        )
        assert retained == {
            review.candidate_artifact_id,
            *(s.artifact.artifact_id for s in sources),
        }
        private = (
            claimed.state.state_id,
            claimed.state.state_digest,
            claimed.lease_token,
            claimed.rollout.rollout_id,
            "observation-worker",
            receipt.policy_digest,
            receipt.content_receipt_digest,
            receipt.authorization_digest,
            review.candidate_artifact_id,
            *(s.artifact.uri for s in sources),
            *(s.artifact.digest for s in sources),
            *(s.classification_digest for s in sources),
        )
        assert all(value.encode() not in public for value in private)
        before = await _counts(session)
        retry = await boundary.observe_claim(session, **_scope(claimed), now=ctx.clock())
        assert retry == delivered
        assert await _counts(session) == before
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProcessObservationRow)
                .where(
                    ProcessObservationRow.rollout_id == claimed.rollout.rollout_id,
                    ProcessObservationRow.worker_id == "observation-worker",
                )
            )
            == 1
        )


async def test_replacement_changes_private_receipt_but_preserves_public_bytes(evidence_context):
    ctx = evidence_context
    first = await _claim(ctx, worker_id="first-worker")
    observed = await _observe(ctx, first, worker_id="first-worker")
    async with ctx.database.transaction() as session:
        await ctx.process.release_claim(
            session,
            rollout_id=first.rollout.rollout_id,
            lease_token=first.lease_token,
            to_status=RolloutStatus.ACTIVE,
            occurred_at=ctx.clock(),
        )
    replacement = await _claim(ctx, worker_id="replacement-worker")
    fresh = await _observe(ctx, replacement, worker_id="replacement-worker")
    assert fresh.observation_bytes == observed.observation_bytes
    assert fresh.observation_id != observed.observation_id
    boundary = ProcessObservationStore(ctx.process)
    async with ctx.database.transaction() as session:
        for scope in (_scope(first, "first-worker"), _scope(replacement, "replacement-worker")):
            with pytest.raises(ProcessObservationDeniedError) as failure:
                await boundary.read(
                    session, observation_id=observed.observation_id, **scope, now=ctx.clock()
                )
            _uniform(failure)
        assert await boundary.inspect_receipt(session, observation_id=observed.observation_id)
        assert (
            await boundary.read(
                session,
                observation_id=fresh.observation_id,
                **_scope(replacement, "replacement-worker"),
                now=ctx.clock(),
            )
            == fresh.observation
        )


@pytest.mark.parametrize(
    "change",
    [
        "owner",
        "token",
        "rollout",
        "expired_lease",
        "future_claim",
        "paused_rollout",
        "stale_state",
        "amber_paused",
        "amber_sequence",
        "expired_amber",
        "backdated",
        "naive",
    ],
)
async def test_delivery_rechecks_current_context(evidence_context, change):
    ctx = evidence_context
    claimed = await _claim(ctx)
    observed = await _observe(ctx, claimed)
    boundary = ProcessObservationStore(ctx.process)
    scope = _scope(claimed)
    now = ctx.clock()
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
        if change == "owner":
            scope["worker_id"] = "another-worker"
        elif change == "token":
            scope["lease_token"] = "another-lease"
        elif change == "rollout":
            scope["rollout_id"] = "another-rollout"
        elif change == "expired_lease":
            now = row.lease_expires_at.replace(tzinfo=UTC)
        elif change == "future_claim":
            row.updated_at = now + timedelta(seconds=1)
        elif change == "paused_rollout":
            row.paused = True
        elif change == "stale_state":
            row.sequence += 1
        elif change == "amber_paused":
            await ctx.amber.transition(
                session,
                authorization_digest=ctx.execution.amber_authorization_digest,
                to_status=AmberStatus.PAUSED,
                actor_id="operator",
                reason="test pause",
                occurred_at=now,
            )
        elif change == "amber_sequence":
            head = await session.get(
                AmberAuthorizationHeadRow, ctx.execution.amber_authorization_digest
            )
            head.sequence += 1
        elif change == "expired_amber":
            authority = await ctx.amber.get(
                session, authorization_digest=ctx.execution.amber_authorization_digest
            )
            now = authority.expires_at
            row.lease_expires_at = now + timedelta(seconds=1)
        elif change == "backdated":
            now = NOW
        else:
            now = now.replace(tzinfo=None)
        await session.flush()
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessObservationDeniedError) as failure:
            await boundary.read(session, observation_id=observed.observation_id, **scope, now=now)
        _uniform(failure)
        assert await boundary.inspect_receipt(session, observation_id=observed.observation_id)


@pytest.mark.parametrize("corruption", ["receipt", "source", "missing_content", "policy"])
async def test_corruption_or_missing_admission_never_falls_back_to_full_state(
    evidence_context, corruption
):
    ctx = evidence_context
    claimed = await _claim(ctx)
    boundary = ProcessObservationStore(ctx.process)
    observed = await _observe(ctx, claimed, boundary=boundary)
    async with ctx.database.transaction() as session:
        if corruption == "receipt":
            row = await session.get(ProcessObservationRow, observed.observation_id)
            value = {**row.record_json, "SYNTHETIC_PRIVATE_MARKER": "never expose"}
            await session.execute(
                update(ProcessObservationRow)
                .where(ProcessObservationRow.observation_id == observed.observation_id)
                .values(record_json=value)
            )
        elif corruption == "source":
            row = await session.get(ProcessStateRow, claimed.state.state_id)
            value = {
                **row.record_json,
                "payload": {**row.record_json["payload"], "objective": "SYNTHETIC_PRIVATE_MARKER"},
            }
            await session.execute(
                update(ProcessStateRow)
                .where(ProcessStateRow.state_id == row.state_id)
                .values(record_json=value)
            )
        elif corruption == "missing_content":
            # This receipt is already referenced: simulate an unknown current policy,
            # not a DB deletion that would violate its foreign key.
            await session.execute(
                update(ProcessContentAdmissionRow)
                .where(
                    ProcessContentAdmissionRow.record_id == claimed.state.state_id,
                    ProcessContentAdmissionRow.record_kind == "state",
                )
                .values(record_json={"SYNTHETIC_PRIVATE_MARKER": "missing admission"})
            )
        else:
            boundary = ProcessObservationStore(
                ctx.process, policy=boundary.policy.model_copy(update={"version": "2.0.0"})
            )
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessObservationDeniedError) as failure:
            await boundary.read(
                session, observation_id=observed.observation_id, **_scope(claimed), now=ctx.clock()
            )
        _uniform(failure)


async def test_legacy_state_without_content_receipt_cannot_get_an_observation(evidence_context):
    ctx = evidence_context
    claimed = await _claim(ctx)
    async with ctx.database.transaction() as session:
        await session.execute(
            delete(ProcessContentAdmissionRow).where(
                ProcessContentAdmissionRow.record_kind == "state",
                ProcessContentAdmissionRow.record_id == claimed.state.state_id,
            )
        )
        before = await _counts(session)
        with pytest.raises(ProcessObservationDeniedError):
            await ProcessObservationStore(ctx.process).observe_claim(
                session, **_scope(claimed), now=ctx.clock()
            )
        assert await _counts(session) == before
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 0


@pytest.mark.parametrize("right", ["research", "retention", "future_review"])
async def test_empty_observation_still_requires_research_and_retention_rights(
    evidence_context, right
):
    ctx = evidence_context
    await _claim(ctx)  # keep the original execution out of claim selection
    removed = RightsUse.INTERNAL_RESEARCH if right == "research" else RightsUse.EVIDENCE_RETENTION
    changes = {
        "permitted_uses": tuple(
            r for r in ctx.execution.output_rights.permitted_uses if r != removed
        )
    }
    if right == "future_review":
        changes = {"reviewed_at": ctx.clock() + timedelta(hours=1)}
    execution = ctx.execution.model_copy(
        update={
            "execution_id": "rights-constrained-execution",
            "output_rights": ctx.execution.output_rights.model_copy(update=changes),
        }
    )
    async with ctx.database.transaction() as session:
        digest = await ctx.process.register_execution(session, execution)
        await ctx.process.create_rollout(
            session,
            execution_digest=digest,
            replication_index=1,
            initial_state=ProjectStatePayload(objective="test explicit use rights"),
            created_at=ctx.clock(),
        )
    claimed = await _claim(ctx)
    with pytest.raises(ProcessObservationDeniedError):
        await _observe(ctx, claimed)


async def test_policy_size_and_schema_are_enforced_before_delivery(evidence_context):
    ctx = evidence_context
    claimed = await _claim(ctx)
    original = ProcessObservationStore(ctx.process).policy
    with pytest.raises(ValueError, match="configured schemas"):
        ProcessObservationStore(
            ctx.process,
            policy=original.model_copy(
                update={"observation_schema_digest": sha256_digest("wrong")}
            ),
        )
    small = ProcessObservationStore(
        ctx.process, policy=original.model_copy(update={"maximum_bytes": 10})
    )
    with pytest.raises(ProcessObservationDeniedError):
        await _observe(ctx, claimed, boundary=small)
    async with ctx.database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 0


async def test_missing_observation_ownership_is_not_replaced_by_admission_or_state_pins(
    evidence_context,
):
    ctx = evidence_context
    claimed, review, _sources = await _reviewed_claim(ctx)
    observed = await _observe(ctx, claimed)
    async with ctx.database.transaction() as session:
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_observation",
                ArtifactReferenceRow.owner_id == observed.observation_id,
                ArtifactReferenceRow.artifact_id != review.candidate_artifact_id,
            )
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ArtifactReferenceRow)
                .where(
                    ArtifactReferenceRow.owner_type == "process_state",
                    ArtifactReferenceRow.owner_id == claimed.state.state_id,
                )
            )
            == 3
        )
        with pytest.raises(ProcessObservationDeniedError) as failure:
            await ProcessObservationStore(ctx.process).read(
                session, observation_id=observed.observation_id, **_scope(claimed), now=ctx.clock()
            )
        _uniform(failure)
        with pytest.raises(ProcessObservationDeniedError):
            await ProcessObservationStore(ctx.process).observe_claim(
                session, **_scope(claimed), now=ctx.clock()
            )


@pytest.mark.parametrize("phase", ["during_pins", "after_receipt"])
async def test_caught_retention_failure_rolls_back_all_observation_writes(
    evidence_context, monkeypatch, phase
):
    ctx = evidence_context
    claimed, _review, _sources = await _reviewed_claim(ctx)
    reference = ctx.catalog.reference
    boundary = ProcessObservationStore(ctx.process)
    validate = boundary._validate_ownership
    calls = 0

    async def fail_after_pin(session, artifact, *, owner_type, owner_id):
        nonlocal calls
        await reference(session, artifact, owner_type=owner_type, owner_id=owner_id)
        if owner_type == "process_observation":
            calls += 1
            if calls == 2 and phase == "during_pins":
                raise RuntimeError("SYNTHETIC_PRIVATE_PIN_FAILURE")

    async def fail_after_receipt(session, receipt, *, now):
        await validate(session, receipt, now=now)
        assert await session.get(ProcessObservationRow, receipt.observation_id) is not None
        raise RuntimeError("SYNTHETIC_PRIVATE_RECEIPT_FAILURE")

    monkeypatch.setattr(ctx.catalog, "reference", fail_after_pin)
    if phase == "after_receipt":
        monkeypatch.setattr(boundary, "_validate_ownership", fail_after_receipt)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(ProcessObservationDeniedError) as failure:
            await boundary.observe_claim(session, **_scope(claimed), now=ctx.clock())
        _uniform(failure)
        assert calls == (2 if phase == "during_pins" else 3)
        assert await _counts(session) == before
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProcessObservationRow)
                .where(
                    ProcessObservationRow.rollout_id == claimed.rollout.rollout_id,
                    ProcessObservationRow.worker_id == "observation-worker",
                )
            )
            == 0
        )
    async with ctx.database.transaction() as session:
        assert await _counts(session) == before


async def test_outer_rollback_removes_observation_and_claim(evidence_context):
    ctx = evidence_context
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with ctx.database.transaction() as session:
            claimed = await ctx.process.claim_next(
                session,
                worker_id="rollback-worker",
                lease_for=timedelta(minutes=5),
                now=ctx.clock(),
            )
            assert claimed is not None
            await ProcessObservationStore(ctx.process).observe_claim(
                session, **_scope(claimed, "rollback-worker"), now=ctx.clock()
            )
            raise RuntimeError("outer rollback")
    async with ctx.database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 0
        row = await session.get(ProcessRolloutRow, "evidence-rollout")
        assert row.lease_token is None and row.lease_owner is None


@pytest.mark.parametrize("cancelled", [False, True])
async def test_private_failure_and_cancellation_payloads_do_not_cross_delivery(
    evidence_context, monkeypatch, cancelled
):
    ctx = evidence_context
    claimed = await _claim(ctx)
    observed = await _observe(ctx, claimed)

    async def failure(*_args, **_kwargs):
        if cancelled:
            raise asyncio.CancelledError("SYNTHETIC_PRIVATE_CANCELLATION")
        raise ValueError("SYNTHETIC_PRIVATE_SOURCE")

    monkeypatch.setattr(ctx.process.content, "verify_state", failure)
    async with ctx.database.transaction() as session:
        with pytest.raises(
            asyncio.CancelledError if cancelled else ProcessObservationDeniedError
        ) as caught:
            await ProcessObservationStore(ctx.process).read(
                session, observation_id=observed.observation_id, **_scope(claimed), now=ctx.clock()
            )
        assert "SYNTHETIC" not in str(caught.value)
        assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("target", ["scientific_math", "undeclared-target"])
async def test_admitted_and_denied_decisions_retain_immutable_observation_lineage(
    evidence_context, target
):
    ctx = evidence_context
    claimed = await _claim(ctx)
    boundary = ProcessObservationStore(ctx.process)
    observed = await _observe(ctx, claimed, boundary=boundary)
    request = _request(ctx, claimed, target=target)
    async with ctx.database.transaction() as session:
        decision = await ctx.amber.admit(session, request=request, active_workers=0)
        binding = await boundary.bind_decision(
            session,
            observation_id=observed.observation_id,
            decision_id=decision.decision_id,
            **_scope(claimed),
            now=ctx.clock(),
        )
        assert binding.disposition == decision.disposition
        assert (binding.disposition == AmberAdmissionDisposition.ADMITTED) == (
            target == "scientific_math"
        )
        assert binding.observation_digest == sha256_digest(observed.observation_bytes)
        assert binding.request_digest == sha256_digest(request)
        assert binding.declared_role_id == request.role_id
        assert (
            await boundary.bind_decision(
                session,
                observation_id=observed.observation_id,
                decision_id=decision.decision_id,
                **_scope(claimed),
                now=ctx.clock(),
            )
            == binding
        )
        # No observation fields were inserted into the historical Amber request envelope.
        decision_row = await session.get(AmberAdmissionDecisionRow, decision.decision_id)
        assert decision_row.request_json == request.model_dump(mode="json")
        await ctx.process.release_claim(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            to_status=RolloutStatus.REVIEW_REQUIRED,
            occurred_at=ctx.clock(),
        )
    async with ctx.database.transaction() as session:
        assert (
            await boundary.inspect_decision_binding(session, decision_id=decision.decision_id)
            == binding
        )
    for table, key in (
        (ProcessObservationRow, observed.observation_id),
        (ProcessObservationDecisionRow, decision.decision_id),
    ):
        with pytest.raises(ValueError, match="immutable"):
            async with ctx.database.transaction() as session:
                row = await session.get(table, key)
                row.record_json = {**row.record_json, "tampered": True}
                await session.flush()


@pytest.mark.parametrize("change", ["state", "lease", "time", "observation"])
async def test_decision_cannot_be_reassigned_to_another_observation(evidence_context, change):
    ctx = evidence_context
    claimed = await _claim(ctx)
    boundary = ProcessObservationStore(ctx.process)
    request = _request(ctx, claimed) if change == "time" else None
    observed = await _observe(ctx, claimed, boundary=boundary)
    if request is None:
        request = _request(ctx, claimed)
    if change == "state":
        request = request.model_copy(update={"state_digest": sha256_digest("other state")})
    elif change == "lease":
        request = request.model_copy(update={"lease_token_digest": sha256_digest("other lease")})
    async with ctx.database.transaction() as session:
        decision = await ctx.amber.admit(session, request=request, active_workers=0)
        observation_id = (
            "unknown-observation" if change == "observation" else observed.observation_id
        )
        with pytest.raises(ProcessObservationDeniedError) as failure:
            await boundary.bind_decision(
                session,
                observation_id=observation_id,
                decision_id=decision.decision_id,
                **_scope(claimed),
                now=ctx.clock(),
            )
        _uniform(failure)
        assert (
            await session.scalar(select(func.count()).select_from(ProcessObservationDecisionRow))
            == 0
        )
        assert await session.get(AmberAdmissionDecisionRow, decision.decision_id) is not None


async def test_observation_returns_detached_nested_values(evidence_context):
    ctx = evidence_context
    claimed = await _claim(ctx)
    delivered = await _observe(ctx, claimed)
    copy = delivered.observation
    copy.state.extension_state["unreviewed"] = {"private": "SYNTHETIC_MUTATION"}
    assert delivered.observation.state.extension_state == {}
    assert b"SYNTHETIC_MUTATION" not in delivered.observation_bytes
    async with ctx.database.transaction() as session:
        source = await ctx.process.get_state(session, state_id=claimed.state.state_id)
        assert source.payload.extension_state == {}
        result = await ProcessObservationStore(ctx.process).read(
            session, observation_id=delivered.observation_id, **_scope(claimed), now=ctx.clock()
        )
        assert isinstance(result, ProcessWorkerObservation) and result.state.extension_state == {}
