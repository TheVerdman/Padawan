"""Reviewed derivatives from synthetic effects; no model or physical container is launched."""

import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from padawan.artifacts.information import ArtifactInformationStore
from padawan.artifacts.store import ArtifactCatalog, ArtifactIntegrityError, LocalArtifactStore
from padawan.governance.amber import AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import RightsUse
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ProcessEvidenceAdmissionRow
from padawan.pprl.contracts import ProjectBudgetUsage, ProjectSplit, ProjectStatePayload
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError, ProcessEvidenceStore
from padawan.pprl.evidence_contracts import EvidenceAdmissionPolicy, ProcessEvidenceUse
from padawan.pprl.generation import ProcessGenerationUnavailableError
from padawan.pprl.recovered_evidence import RecoveryEvidenceSourceBoundary
from padawan.pprl.recovered_evidence_contracts import (
    RecoveredEvidenceOriginReview,
    RecoveredProcessEvidenceAdmissionRecord,
    RecoveryEvidenceDisclosurePolicy,
)
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.store import ProcessStore
from tests.container_helpers import container_context
from tests.helpers import CallbackGenerationClient
from tests.integration.test_process_generation_workloads import _ready
from tests.integration.test_process_recovery import expire, recover, reviewed, snapshot
from tests.integration.test_process_recovery_effects import model_context
from tests.pprl_evidence_helpers import _review
from tests.pprl_evidence_helpers import evidence_context as evidence_context


async def configured(base, *, kind="model", execute=True, ready=None, receipt=None):
    if kind == "model":
        ready = ready or await _ready(base)
        native = await model_context(ready)
        calls = ready.client.calls
        manifest = base.execution
    else:
        ready = await container_context(
            base.database, base.artifacts.root, base.clock, enrolled=True
        )
        native, calls, manifest = ready, ready.driver.calls, ready.execution
    if execute:
        await ready.service.execute(**ready.kwargs)
    if receipt is None:
        await expire(native)
        receipt = await recover(native)
    catalog = native.records.catalog
    admission_policy = base.evidence.policy.model_copy(update={"maximum_forensic_sources": 6})
    policy = RecoveryEvidenceDisclosurePolicy(
        policy_id="test.recovered-disclosure",
        version="1.0.0",
        target_execution_digest=receipt.execution_digest,
        target_contamination_scope="test-train",
        content_policy_digest=native.process.content.policy.digest,
        reviewer_ids=("reviewer-a",),
        created_at=base.clock(),
        expires_at=base.clock() + timedelta(hours=1),
    )
    boundary = RecoveryEvidenceSourceBoundary(
        recovery=ProcessRecoveryStore(native.process, catalog), policy=policy
    )
    broker = ProcessEvidenceStore(
        catalog=catalog, amber=native.amber, policy=admission_policy, recovered=boundary
    )
    ctx = SimpleNamespace(
        database=base.database,
        artifacts=catalog.backend,
        catalog=catalog,
        information=ArtifactInformationStore(catalog),
        evidence=broker,
        execution=manifest,
        execution_digest=receipt.execution_digest,
        clock=base.clock,
        split=ProjectSplit.TRAIN,
    )
    return SimpleNamespace(
        ctx=ctx,
        native=native,
        broker=broker,
        boundary=boundary,
        receipt=receipt,
        ready=ready,
        calls=calls,
    )


async def candidate(
    case,
    *,
    content="Reviewed fixture finding: 42.",
    training=False,
    media_type="text/plain; charset=utf-8",
):
    async with case.ctx.database.transaction() as session:
        described = await case.boundary.describe(
            session,
            recovery_id=case.receipt.request.recovery_id,
            decision_id=case.receipt.effects[0].decision_id,
        )
    review = await _review(
        case.ctx,
        content=content,
        sources=described.forensic_sources,
        training=training,
        media_type=media_type,
    )
    origin = RecoveredEvidenceOriginReview(
        candidate_review_digest=review.digest,
        disclosure_policy=case.boundary.policy,
        policy_digest=case.boundary.policy.digest,
        source=described.source,
    )
    return review, origin


async def admit(case, review, origin):
    async with case.ctx.database.transaction() as session:
        return await case.broker.admit_recovered(
            session, review=review, origin=origin, now=case.ctx.clock()
        )


async def test_reviewed_recovery_derivative_is_separate_retained_and_does_not_resolve_effect(
    evidence_context,
):
    await assert_separate_derivative(await configured(evidence_context))


async def test_container_derivative_uses_the_complete_native_source_set(
    database, tmp_path, pprl_now
):
    base = SimpleNamespace(
        database=database,
        artifacts=LocalArtifactStore(tmp_path),
        clock=pprl_now,
        evidence=SimpleNamespace(
            policy=EvidenceAdmissionPolicy(
                policy_id="test.evidence-admission",
                version="1.0.0",
                reviewer_ids=("reviewer-a",),
                maximum_bytes=100_000,
                maximum_forensic_sources=6,
                maximum_forensic_source_bytes=1_000_000,
            )
        ),
    )
    await assert_separate_derivative(await configured(base, kind="container"))


async def assert_separate_derivative(case):
    review, origin = await candidate(case)
    before = await snapshot(case.native)
    reference = await admit(case, review, origin)
    assert await admit(case, review, origin) == reference
    assert await snapshot(case.native) == before
    assert len(case.calls) == 1
    async with case.ctx.database.transaction() as session:
        record = await case.broker.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert isinstance(record, RecoveredProcessEvidenceAdmissionRecord)
        assert record.recovery_origin == origin and record.review == review
        assert (
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )
            == b"Reviewed fixture finding: 42."
        )
        owners = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_evidence_admission",
                    ArtifactReferenceRow.owner_id == reference.process_artifact_id,
                )
            )
        )
        assert owners == {
            review.candidate_artifact_id,
            *(source.artifact.artifact_id for source in review.forensic_sources),
        }
        with pytest.raises(PermissionError, match="uncommitted effect"):
            await case.native.amber.resources.assert_rollout_recoverable(
                session, rollout_id=case.receipt.request.rollout_id
            )
    public = canonical_json_bytes(reference)
    assert b"recovery" not in public and origin.source.recovery_id.encode() not in public
    # New stores/caches read only durable records and the explicitly pinned configuration.
    catalog = ArtifactCatalog(LocalArtifactStore(case.ctx.artifacts.root))
    amber = AmberStore()
    reopened = ProcessEvidenceStore(
        catalog=catalog,
        amber=amber,
        policy=case.broker.policy,
        recovered=RecoveryEvidenceSourceBoundary(
            recovery=ProcessRecoveryStore(ProcessStore(amber), catalog), policy=case.boundary.policy
        ),
    )
    async with case.ctx.database.transaction() as session:
        assert (
            await reopened.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )
            == b"Reviewed fixture finding: 42."
        )
    assert await snapshot(case.native) == before


async def test_recovered_origin_needs_explicit_composition_and_never_admits_training(
    evidence_context,
):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    legacy = ProcessEvidenceStore(
        catalog=case.ctx.catalog, amber=case.native.amber, policy=case.broker.policy
    )
    async with case.ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="explicitly configured"):
            await legacy.admit_recovered(
                session, review=review, origin=origin, now=case.ctx.clock()
            )
        with pytest.raises(PermissionError, match="explicit recovery origin"):
            await case.broker.admit(session, review=review, now=case.ctx.clock())
    training_review, training_origin = await candidate(case, training=True)
    with pytest.raises(PermissionError, match="process-use authority"):
        await admit(case, training_review, training_origin)
    reference = await admit(case, review, origin)
    async with case.ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.TRAINING_PROJECTION,
                now=case.ctx.clock(),
            )
        with pytest.raises(PermissionError):
            await case.broker.retain_for_process(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                owner_type="process_training_projection",
                owner_id="not-a-training-admission",
                use=ProcessEvidenceUse.TRAINING_PROJECTION,
                now=case.ctx.clock(),
            )
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 1
        )


async def test_legacy_source_subset_cannot_erase_recovery_origin_or_allow_training(
    evidence_context,
):
    case = await configured(evidence_context)
    review, _ = await candidate(case, training=True)
    # The old request/response adapter accepts a completed invocation. Dropping the
    # prepared workload must not turn a recovered intervention into ordinary training evidence.
    request_and_response = tuple(
        source.reference
        for source in case.receipt.effects[0].sources
        if source.owner_type in {"external_call_request", "external_call_response"}
    )
    assert len(request_and_response) == 2
    review = review.model_copy(
        update={
            "forensic_sources": tuple(
                sorted(request_and_response, key=lambda ref: ref.artifact.artifact_id)
            )
        }
    )
    async with case.ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="explicit recovery origin"):
            await case.broker.admit(session, review=review, now=case.ctx.clock())


async def test_duplicate_json_keys_cannot_hide_an_escaped_private_reference(evidence_context):
    case = await configured(evidence_context)
    escaped = "".join(f"\\u{ord(char):04x}" for char in case.receipt.request.recovery_id)
    review, origin = await candidate(
        case, content='{"finding":"' + escaped + '","finding":"42"}', media_type="application/json"
    )
    with pytest.raises(ValueError, match="duplicate JSON"):
        await admit(case, review, origin)


@pytest.mark.parametrize("started", [False, True])
async def test_unstarted_and_unknown_effects_cannot_be_disclosed(evidence_context, started):
    ready = await _ready(evidence_context)
    if started:
        async with evidence_context.database.transaction() as session:
            await evidence_context.amber.resources.start(
                session, decision_id=ready.decision.decision_id, now=evidence_context.clock()
            )
    case = await configured(evidence_context, ready=ready, execute=False)
    assert case.receipt.effects[0].disposition == ("unknown" if started else "released_unstarted")
    before = await snapshot(case.native)
    with pytest.raises(PermissionError, match="independently completed effect"):
        await candidate(case)
    assert await snapshot(case.native) == before and not case.calls


@pytest.mark.parametrize(
    "field", ["recovery_id", "recovery_digest", "decision_id", "effect_digest"]
)
async def test_origin_must_name_the_exact_completed_effect(evidence_context, field):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    incorrect = (
        "process-recovery-" + "0" * 32
        if field == "recovery_id"
        else sha256_digest("different source")
    )
    origin = origin.model_copy(
        update={"source": origin.source.model_copy(update={field: incorrect})}
    )
    with pytest.raises(PermissionError):
        await admit(case, review, origin)


@pytest.mark.parametrize(
    "field",
    ["source_set", "reviewer", "scope", "review_time", "rights", "execution", "review_digest"],
)
async def test_review_must_bind_complete_source_scope_and_current_rights(evidence_context, field):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    if field == "source_set":
        review = review.model_copy(update={"forensic_sources": review.forensic_sources[:-1]})
    elif field == "reviewer":
        review = review.model_copy(update={"reviewer_id": "reviewer-not-in-amber"})
    elif field == "scope":
        review = review.model_copy(update={"contamination_scope": "test-test"})
    elif field == "review_time":
        review = review.model_copy(
            update={"reviewed_at": case.receipt.created_at - timedelta(seconds=1)}
        )
    elif field == "rights":
        review = review.model_copy(
            update={
                "rights": review.rights.model_copy(
                    update={"permitted_uses": (RightsUse.EVIDENCE_RETENTION,)}
                )
            }
        )
    elif field == "execution":
        review = review.model_copy(
            update={
                "process_reference": review.process_reference.model_copy(
                    update={"execution_digest": sha256_digest("another execution")}
                )
            }
        )
    origin = origin.model_copy(
        update={
            "candidate_review_digest": sha256_digest("different review")
            if field == "review_digest"
            else review.digest
        }
    )
    before = await snapshot(case.native)
    with pytest.raises(PermissionError):
        await admit(case, review, origin)
    assert await snapshot(case.native) == before


@pytest.mark.parametrize("representation", ["text", "bare_digest", "escaped_json"])
async def test_private_recovery_identifiers_never_become_candidate_content(
    evidence_context, representation
):
    case = await configured(evidence_context)
    if representation == "escaped_json":
        identifier = case.receipt.effects[0].result_digest
        encoded = "".join(f"\\u{ord(char):04x}" for char in identifier)
        content, media_type = '{"finding":{"source":"' + encoded + '"}}', "application/json"
    else:
        content = (
            case.receipt.request.recovery_id
            if representation == "text"
            else case.receipt.digest[7:]
        )
        media_type = "text/markdown"
    review, origin = await candidate(case, content=content, media_type=media_type)
    with pytest.raises(PermissionError, match="private source identifier"):
        await admit(case, review, origin)


async def test_json_derivative_preserves_exact_bytes_and_rejects_unsupported_media(
    evidence_context,
):
    case = await configured(evidence_context)
    content = json.dumps({"finding": {"value": 42, "reviewed": True}}, indent=2)
    review, origin = await candidate(case, content=content, media_type="application/json")
    reference = await admit(case, review, origin)
    async with case.ctx.database.transaction() as session:
        assert (
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )
            == content.encode()
        )
    review, origin = await candidate(
        case, content="Uninterpreted fixture bytes", media_type="application/octet-stream"
    )
    with pytest.raises(PermissionError, match="UTF-8 text or JSON"):
        await admit(case, review, origin)


@pytest.mark.parametrize(
    "owner_type",
    [
        "process_generation_workload",
        "process_recovery_receipt",
        "process_resource_event",
        "process_evidence_admission",
    ],
)
async def test_reads_require_each_independent_owner_and_do_not_repair_missing_pins(
    evidence_context, owner_type
):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    reference = await admit(case, review, origin)
    source = next(
        s for s in case.receipt.effects[0].sources if s.owner_type == "process_generation_workload"
    )
    async with case.ctx.database.transaction() as session:
        pin = await session.scalar(
            select(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.artifact_id == source.reference.artifact.artifact_id,
            )
        )
        assert pin is not None
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == pin.owner_id,
                ArtifactReferenceRow.artifact_id == pin.artifact_id,
            )
        )
    before = await snapshot(case.native)
    async with case.ctx.database.transaction() as session:
        count = await session.scalar(select(func.count()).select_from(ArtifactReferenceRow))
        with pytest.raises(ProcessEvidenceReadDeniedError) as denied:
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )
        assert denied.value.args == ("process evidence read denied",)
        assert denied.value.__context__ is None
        assert await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == count
        with pytest.raises((PermissionError, ValueError, ArtifactIntegrityError)):
            await case.broker.admit_recovered(
                session, review=review, origin=origin, now=case.ctx.clock()
            )
    assert await snapshot(case.native) == before and len(case.calls) == 1


async def test_failed_retention_rolls_back_even_when_caller_catches_error(
    evidence_context, monkeypatch
):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    original = case.ctx.catalog.reference
    calls = 0

    async def fail_second_pin(*args, **kwargs):
        nonlocal calls
        await original(*args, **kwargs)
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic pin failure")

    monkeypatch.setattr(case.ctx.catalog, "reference", fail_second_pin)
    async with case.ctx.database.transaction() as session:
        before = await session.scalar(select(func.count()).select_from(ArtifactReferenceRow))
        with pytest.raises(RuntimeError, match="synthetic pin failure"):
            await case.broker.admit_recovered(
                session, review=review, origin=origin, now=case.ctx.clock()
            )
    async with case.ctx.database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == before
        )
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 0
        )


async def test_declared_read_bound_is_checked_before_the_native_reader(
    evidence_context, monkeypatch
):
    case = await configured(evidence_context)
    case.boundary.policy = case.boundary.policy.model_copy(
        update={"maximum_recovery_source_bytes": 1}
    )

    async def unexpected(*args, **kwargs):
        pytest.fail("native reader must not run outside its declared source bound")

    monkeypatch.setattr(case.boundary.recovery, "read", unexpected)
    with pytest.raises(PermissionError, match="read bounds"):
        await candidate(case)


@pytest.mark.parametrize("change", ["policy", "content_policy", "amber", "expiry"])
async def test_new_use_rechecks_configuration_authority_and_wall_clock(
    evidence_context, monkeypatch, change
):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    reference = await admit(case, review, origin)
    if change == "policy":
        case.boundary.policy = case.boundary.policy.model_copy(update={"version": "2.0.0"})
    elif change == "content_policy":
        case.boundary.recovery.processes = ProcessStore(case.native.amber)
        # Same source/Amber, deliberately different current content contract.
        case.boundary.policy = case.boundary.policy.model_copy(
            update={"content_policy_digest": sha256_digest("another content policy")}
        )
        origin = origin.model_copy(
            update={
                "disclosure_policy": case.boundary.policy,
                "policy_digest": case.boundary.policy.digest,
            }
        )
    elif change == "amber":
        async with case.ctx.database.transaction() as session:
            await case.native.amber.transition(
                session,
                authorization_digest=case.native.authorization.digest,
                to_status=AmberStatus.PAUSED,
                actor_id="reviewer-a",
                reason="stop disclosure fixture",
                occurred_at=case.ctx.clock(),
            )
    else:
        from padawan.pprl import recovered_evidence

        class Expired(datetime):
            @classmethod
            def now(cls, tz=None):
                return case.boundary.policy.expires_at

        monkeypatch.setattr(recovered_evidence, "datetime", Expired)
    async with case.ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )
    with pytest.raises(PermissionError):
        await admit(case, review, origin)


async def test_expiry_during_source_inspection_leaves_no_admission(evidence_context, monkeypatch):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    original = case.boundary.describe
    from padawan.pprl import recovered_evidence

    class Expired(datetime):
        @classmethod
        def now(cls, tz=None):
            return case.boundary.policy.expires_at

    async def expire_after_source(*args, **kwargs):
        result = await original(*args, **kwargs)
        monkeypatch.setattr(recovered_evidence, "datetime", Expired)
        return result

    monkeypatch.setattr(case.boundary, "describe", expire_after_source)
    with pytest.raises(PermissionError, match="expired during source validation"):
        await admit(case, review, origin)
    async with case.ctx.database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 0
        )


async def test_late_result_can_be_reviewed_without_reviving_the_fenced_invocation(evidence_context):
    entered, finish = asyncio.Event(), asyncio.Event()

    class Delayed(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            entered.set()
            await finish.wait()
            return await super().generate_prepared(request, prepared)

    client = Delayed(lambda request: "late private fixture output", "test-open-weight")
    ready = await _ready(evidence_context, client=client)
    native = await model_context(ready)
    pending = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await expire(native)
        unknown = await recover(native)
        assert unknown.effects[0].disposition == "unknown"
    finally:
        finish.set()
    with pytest.raises(ProcessGenerationUnavailableError):
        await asyncio.wait_for(pending, 5)
    receipt = await recover(
        native, reviewed(native).model_copy(update={"expected_lease_token_digest": None})
    )
    case = await configured(evidence_context, ready=ready, execute=False, receipt=receipt)
    await assert_separate_derivative(case)


@pytest.mark.parametrize("corrupt", ["source", "candidate"])
async def test_corrupt_physical_bytes_deny_admission_and_public_read(evidence_context, corrupt):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    reference = await admit(case, review, origin)
    digest = (
        review.forensic_sources[0].artifact.digest
        if corrupt == "source"
        else reference.content_digest
    )
    case.ctx.artifacts._path_for_hex(digest[7:]).write_bytes(b"damaged fixture")
    with pytest.raises(ArtifactIntegrityError):
        await admit(case, review, origin)
    async with case.ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await case.broker.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=case.ctx.clock(),
            )


async def test_process_consumer_retains_dependencies_without_recovery_origin_in_state(
    evidence_context,
):
    case = await configured(evidence_context)
    review, origin = await candidate(case)
    reference = await admit(case, review, origin)
    original = await snapshot(case.native)
    consumer = ProcessStore(case.native.amber, evidence=case.broker)
    async with case.ctx.database.transaction() as session:
        rollout = await consumer.create_rollout(
            session,
            execution_digest=reference.execution_digest,
            replication_index=1,
            initial_state=ProjectStatePayload(
                objective="Consider an explicitly reviewed finding in a separate replica",
                artifact_refs=(reference,),
                budget_usage=ProjectBudgetUsage(artifact_bytes=reference.size_bytes),
            ),
            created_at=case.ctx.clock(),
        )
        state = await consumer.get_state(session, state_id=rollout.initial_state_id)
        owners = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_state",
                    ArtifactReferenceRow.owner_id == state.state_id,
                )
            )
        )
        assert owners == {
            review.candidate_artifact_id,
            *(ref.artifact.artifact_id for ref in review.forensic_sources),
        }
        public = canonical_json_bytes(state.payload)
        assert origin.source.recovery_id.encode() not in public
        assert all(
            ref.artifact.artifact_id.encode() not in public for ref in review.forensic_sources
        )
        journal = await case.native.amber.resources.replay(
            session, case.native.authorization.digest
        )
        assert journal[-2].digest == original[7]
        assert journal[-1].kind == "initial" and journal[-1].subject_id == rollout.rollout_id
        assert journal[-1].amount.artifact_bytes == reference.size_bytes
    current = await snapshot(case.native)
    # The explicit new replica consumes shared funding; the source rollout/effect stays stopped.
    assert current[:7] == original[:7] and current[8:] == original[8:] and len(case.calls) == 1


async def test_legacy_admission_becomes_unusable_after_its_source_is_recovered(evidence_context):
    ready = await _ready(evidence_context)
    await ready.service.execute(**ready.kwargs)
    native = await model_context(ready)
    # Classifications are already authoritative; pick only the original invocation I/O.
    from padawan.models.tables import ProcessWorkerInvocationRow

    async with evidence_context.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, ready.kwargs["invocation_id"])
        refs = [
            await evidence_context.information.forensic_reference(session, artifact_id=artifact_id)
            for artifact_id in (invocation.request_artifact_id, invocation.response_artifact_id)
        ]
    review = await _review(
        evidence_context,
        sources=tuple(sorted(refs, key=lambda ref: ref.artifact.artifact_id)),
        training=True,
    )
    async with evidence_context.database.transaction() as session:
        reference = await evidence_context.evidence.admit(
            session, review=review, now=evidence_context.clock()
        )
        original = await evidence_context.evidence.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
    await expire(native)
    await recover(native)
    async with evidence_context.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await evidence_context.evidence.read(
                session,
                reference=reference,
                execution_digest=reference.execution_digest,
                use=ProcessEvidenceUse.TRAINING_PROJECTION,
                now=evidence_context.clock(),
            )
        retained = await evidence_context.evidence.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert canonical_json_bytes(retained) == canonical_json_bytes(original)
