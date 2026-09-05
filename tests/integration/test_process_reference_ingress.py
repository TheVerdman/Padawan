from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

from padawan.artifacts.store import ArtifactIntegrityError
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ArtifactRow,
    ProcessEvidenceAdmissionRow,
    ProcessExecutionRow,
    ProcessRolloutRow,
    ProcessStateRow,
)
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectStatePayload,
    ProjectStateVersion,
    project_state_digest,
)
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError
from padawan.pprl.store import ProcessForkChildPlan, ProcessInvariantError, ProcessStore
from tests.pprl_evidence_helpers import _admit, _counts, _model_sources, _prepare_action, _review
from tests.pprl_evidence_helpers import evidence_context as evidence_context


@pytest.mark.parametrize("reference_kind", ["raw", "unclassified", "unreviewed", "forged"])
async def test_initial_state_denies_unadmitted_references(evidence_context, reference_kind):
    ctx = evidence_context
    if reference_kind in {"raw", "unclassified"}:
        reference = ctx.artifacts.put_text(
            "SYNTHETIC_UNADMITTED_RECORD", raw_data=reference_kind == "raw", restricted=True
        )
    else:
        review = await _review(ctx)
        reference = review.process_reference
        if reference_kind == "forged":
            async with ctx.database.transaction() as session:
                await ctx.evidence.admit(session, review=review, now=ctx.clock())
            reference = reference.model_copy(update={"content_digest": sha256_digest("forged")})
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises((ProcessInvariantError, ProcessEvidenceReadDeniedError)):
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective="deny unadmitted initial evidence",
                    artifact_refs=(reference,),
                    budget_usage=ProjectBudgetUsage(artifact_bytes=100_000),
                ),
                created_at=ctx.clock(),
            )
        assert await _counts(session) == before


async def test_reference_use_requires_boundary_even_when_reviewed(evidence_context):
    ctx = evidence_context
    review = await _admit(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessInvariantError, match="admission boundary"):
            await ProcessStore(ctx.amber).create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective="requires configured broker",
                    artifact_refs=(review.process_reference,),
                    budget_usage=ProjectBudgetUsage(artifact_bytes=100_000),
                ),
                created_at=ctx.clock(),
            )


async def test_initial_admission_pins_private_dependencies_without_serializing_them(
    evidence_context,
):
    ctx = evidence_context
    sources = await _model_sources(ctx)
    review = await _admit(ctx, sources=sources)
    async with ctx.database.transaction() as session:
        initial = ProjectStatePayload(
            objective="use independently reviewed evidence",
            artifact_refs=(review.process_reference,),
            budget_usage=ProjectBudgetUsage(artifact_bytes=review.process_reference.size_bytes),
        )
        rollout = await ctx.process.create_rollout(
            session,
            execution_digest=ctx.execution_digest,
            replication_index=1,
            initial_state=initial,
            rollout_id="reviewed-initial",
            created_at=ctx.clock(),
        )
        state = await ctx.process.get_state(session, state_id=rollout.initial_state_id)
        retained = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_state",
                    ArtifactReferenceRow.owner_id == state.state_id,
                )
            )
        )
        assert retained == {
            review.candidate_artifact_id,
            *(source.artifact.artifact_id for source in sources),
        }
        encoded = canonical_json_bytes(state)
        for source in sources:
            assert source.artifact.artifact_id.encode() not in encoded
            assert source.artifact.uri.encode() not in encoded
            assert source.classification_digest.encode() not in encoded
        duplicate = await ctx.process.create_rollout(
            session,
            execution_digest=ctx.execution_digest,
            replication_index=1,
            initial_state=initial,
            rollout_id="reviewed-initial",
            created_at=ctx.clock(),
        )
        assert duplicate == rollout
        with pytest.raises(ProcessInvariantError, match="different initial state"):
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=initial.model_copy(update={"objective": "changed objective"}),
                rollout_id="reviewed-initial",
                created_at=ctx.clock(),
            )


async def test_initial_state_pin_failure_rolls_back_but_preserves_outer_work(
    evidence_context, monkeypatch
):
    ctx = evidence_context
    sources = await _model_sources(ctx)
    review = await _admit(ctx, sources=sources)
    reference_method = ctx.catalog.reference
    pinned = 0

    async def fail_after_one_pin(session, reference, **kwargs):
        nonlocal pinned
        if kwargs["owner_type"] == "process_state":
            pinned += 1
            if pinned == 2:
                raise OSError("synthetic pin failure")
        return await reference_method(session, reference, **kwargs)

    monkeypatch.setattr(ctx.catalog, "reference", fail_after_one_pin)
    unrelated = ctx.artifacts.put_text("unrelated outer-transaction work")
    async with ctx.database.transaction() as session:
        await ctx.catalog.register(session, unrelated)
        before = await _counts(session)
        with pytest.raises(OSError, match="pin failure"):
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective="initial retention failure",
                    artifact_refs=(review.process_reference,),
                    budget_usage=ProjectBudgetUsage(artifact_bytes=100_000),
                ),
                rollout_id="failed-initial",
                created_at=ctx.clock(),
            )
        assert pinned == 2
        assert await _counts(session) == before
    async with ctx.database.transaction() as session:
        assert await session.get(ArtifactRow, unrelated.artifact_id) is not None
        assert await session.get(ProcessRolloutRow, "failed-initial") is None


async def test_outer_rollback_also_rolls_back_successful_process_savepoint(evidence_context):
    ctx = evidence_context
    async with ctx.database.transaction() as session:
        before = await _counts(session)
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with ctx.database.transaction() as session:
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective="outer transaction must own the commit"
                ),
                rollout_id="outer-rollback",
                created_at=ctx.clock(),
            )
            raise RuntimeError("synthetic outer rollback")
    async with ctx.database.transaction() as session:
        assert await _counts(session) == before
        assert await session.get(ProcessRolloutRow, "outer-rollback") is None


async def test_outer_rollback_removes_evidence_admission_and_all_pins(evidence_context):
    ctx = evidence_context
    review = await _review(ctx)
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with ctx.database.transaction() as session:
            await ctx.evidence.admit(session, review=review, now=ctx.clock())
            raise RuntimeError("synthetic outer rollback")
    async with ctx.database.transaction() as session:
        assert (
            await session.get(
                ProcessEvidenceAdmissionRow, review.process_reference.process_artifact_id
            )
            is None
        )
        assert not set(await session.scalars(select(ArtifactReferenceRow.artifact_id)))
        # Previously committed classification and physical candidate are separate from admission.
        classification = await ctx.information.get(
            session, artifact_id=review.candidate_artifact_id
        )
        assert ctx.artifacts.read_bytes(classification.artifact, allow_restricted=True)


async def test_append_pins_both_state_and_event_and_missing_pin_prevents_claim(evidence_context):
    ctx = evidence_context
    review = await _admit(ctx)
    claimed, decision, usage = await _prepare_action(ctx, artifact_bytes=100_000)
    async with ctx.database.transaction() as session:
        event, state = await ctx.process.append_event(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            amber_decision_id=decision.decision_id,
            kind=ProcessEventKind.ARTIFACT_ADMITTED,
            actor_id="test.worker",
            payload={},
            artifact_refs=(review.process_reference,),
            resulting_state=ProjectStatePayload(
                objective=claimed.state.payload.objective,
                artifact_refs=(review.process_reference,),
                budget_usage=usage,
            ),
            occurred_at=ctx.clock(),
        )
        for owner_type, owner_id in (
            ("process_state", state.state_id),
            ("process_event", event.event_id),
        ):
            ids = set(
                await session.scalars(
                    select(ArtifactReferenceRow.artifact_id).where(
                        ArtifactReferenceRow.owner_type == owner_type,
                        ArtifactReferenceRow.owner_id == owner_id,
                    )
                )
            )
            assert ids == {review.candidate_artifact_id}
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_state",
                ArtifactReferenceRow.owner_id == state.state_id,
            )
        )
    async with ctx.database.transaction() as session:
        with pytest.raises(ArtifactIntegrityError, match="retention ownership"):
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=ctx.clock()
            )
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
        assert row is not None and row.sequence == 1 and row.lease_token is None
        # Privileged reconstruction still works even when worker use is denied.
        _initial, history = await ctx.process.replay(session, rollout_id=row.rollout_id)
        assert history == (event,)


async def test_transition_pin_failure_keeps_head_lease_and_ownership(evidence_context, monkeypatch):
    ctx = evidence_context
    review = await _admit(ctx)
    claimed, decision, usage = await _prepare_action(ctx, artifact_bytes=100_000)
    reference_method = ctx.catalog.reference

    async def fail_event_pin(session, reference, **kwargs):
        if kwargs["owner_type"] == "process_event":
            raise OSError("synthetic event ownership failure")
        return await reference_method(session, reference, **kwargs)

    monkeypatch.setattr(ctx.catalog, "reference", fail_event_pin)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(OSError, match="event ownership failure"):
            await ctx.process.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.ARTIFACT_ADMITTED,
                actor_id="test.worker",
                payload={},
                artifact_refs=(review.process_reference,),
                resulting_state=ProjectStatePayload(
                    objective=claimed.state.payload.objective,
                    artifact_refs=(review.process_reference,),
                    budget_usage=usage,
                ),
                occurred_at=ctx.clock(),
            )
        assert await _counts(session) == before
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
        assert row is not None and row.sequence == 0
        assert row.current_state_id == claimed.state.state_id
        assert row.lease_token == claimed.lease_token


async def test_fork_second_child_failure_reverts_first_child_and_parent(evidence_context):
    ctx = evidence_context
    review = await _admit(ctx)
    claimed, decision, usage = await _prepare_action(ctx, artifact_bytes=100_000)
    async with ctx.database.transaction() as session:
        await ctx.process.append_event(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            amber_decision_id=decision.decision_id,
            kind=ProcessEventKind.ARTIFACT_ADMITTED,
            actor_id="test.worker",
            payload={},
            resulting_state=ProjectStatePayload(
                objective=claimed.state.payload.objective,
                artifact_refs=(review.process_reference,),
                budget_usage=usage,
            ),
            occurred_at=ctx.clock(),
        )
    claimed, decision, _usage = await _prepare_action(ctx, kind=ProcessEventKind.ROLLOUT_FORKED)
    other_execution = ctx.execution.model_copy(
        update={"execution_id": "different-child-execution", "seed": ctx.execution.seed + 1}
    )
    children = (
        ProcessForkChildPlan("a-same-execution", "first-child", 1, ctx.execution),
        ProcessForkChildPlan("b-new-execution", "second-child", 0, other_execution),
    )
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.process.fork_rollout(
                session,
                parent_rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                actor_id="test.worker",
                children=children,
                intervention={"description": "synthetic comparison"},
                fork_id="failed-fork",
                occurred_at=ctx.clock(),
            )
        assert await _counts(session) == before
    async with ctx.database.transaction() as session:
        parent = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
        assert parent is not None and parent.sequence == 1
        assert parent.lease_token == claimed.lease_token
        assert await session.get(ProcessRolloutRow, "first-child") is None
        assert await session.get(ProcessExecutionRow, sha256_digest(other_execution)) is None


async def test_legacy_raw_state_roundtrips_for_replay_but_cannot_be_claimed(evidence_context):
    ctx = evidence_context
    legacy_artifact = ctx.artifacts.put_text("SYNTHETIC_LEGACY_RAW", restricted=True, raw_data=True)
    async with ctx.database.transaction() as session:
        rollout = await ctx.process.get_rollout(session, rollout_id="evidence-rollout")
        previous = await ctx.process.get_state(session, state_id=rollout.initial_state_id)
        raw_payload = {
            "objective": "legacy process",
            "plan": [],
            "hypotheses": [],
            "claims": [],
            "artifact_refs": [legacy_artifact.model_dump(mode="json")],
            "dependencies": [],
            "budget_usage": {
                "actions": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "artifact_bytes": legacy_artifact.size_bytes,
                "wall_time_seconds": 0.0,
                "cost": 0.0,
            },
            "worker_assignments": [],
            "unresolved_risks": [],
            "memory_refs": [],
            "extension_state": {},
        }
        legacy_payload = ProjectStatePayload.model_validate(raw_payload, strict=False)
        assert legacy_payload.model_dump(mode="json") == raw_payload
        digest = project_state_digest(
            state_id=previous.state_id,
            rollout_id=rollout.rollout_id,
            sequence=0,
            parent_state_id=None,
            triggering_event_id=None,
            payload=legacy_payload,
            created_at=previous.created_at,
        )
        raw_record = previous.model_dump(mode="json")
        raw_record.update(payload=raw_payload, state_digest=digest)
        assert (
            ProjectStateVersion.model_validate(raw_record, strict=False).model_dump(mode="json")
            == raw_record
        )
        # Privileged legacy fixture insertion, bypassing the new ingress API deliberately.
        await session.execute(
            update(ProcessStateRow)
            .where(ProcessStateRow.state_id == previous.state_id)
            .values(record_json=raw_record, state_digest=digest)
        )
    async with ctx.database.transaction() as session:
        initial, events = await ctx.process.replay(session, rollout_id=rollout.rollout_id)
        assert not events and initial.model_dump(mode="json") == raw_record
        with pytest.raises(ProcessInvariantError, match="legacy refs denied"):
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=ctx.clock()
            )
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, rollout.rollout_id)
        assert row is not None and row.lease_token is None


@pytest.mark.parametrize("changed_field", ["execution_digest", "process_artifact_id"])
async def test_event_reference_forgery_does_not_advance_state(evidence_context, changed_field):
    ctx = evidence_context
    review = await _admit(ctx)
    forged = review.process_reference.model_copy(
        update={
            changed_field: (
                sha256_digest("other-execution")
                if changed_field == "execution_digest"
                else f"process-artifact-{uuid4().hex}"
            )
        }
    )
    claimed, decision, usage = await _prepare_action(ctx, artifact_bytes=100_000)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.process.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.ARTIFACT_ADMITTED,
                actor_id="test.worker",
                payload={},
                artifact_refs=(forged,),
                resulting_state=claimed.state.payload.model_copy(update={"budget_usage": usage}),
                occurred_at=ctx.clock(),
            )
        assert await _counts(session) == before
