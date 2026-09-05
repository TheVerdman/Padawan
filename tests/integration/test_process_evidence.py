from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.artifacts.information import (
    InformationClass,
)
from padawan.artifacts.store import ArtifactIntegrityError
from padawan.governance.amber import AmberStatus
from padawan.models.contracts import (
    RightsUse,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactReferenceRow,
    ProcessEvidenceAdmissionRow,
    ProcessWorkerInvocationRow,
)
from padawan.pprl.contracts import (
    ProjectSplit,
)
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError, ProcessEvidenceStore
from padawan.pprl.evidence_contracts import (
    ProcessEvidenceUse,
)
from padawan.pprl.generation import ProcessGenerationUnavailableError
from tests.helpers import CallbackGenerationClient
from tests.pprl_evidence_helpers import _model_sources, _review
from tests.pprl_evidence_helpers import evidence_context as evidence_context
from tests.pprl_helpers import (
    NOW,
)


async def test_generation_failure_keeps_provider_payloads_out_of_worker_errors(
    evidence_context,
) -> None:
    ctx = evidence_context

    class FailingClient(CallbackGenerationClient):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls.append(request)
            raise ModelProviderError(
                "SYNTHETIC_PRIVATE_PROVIDER_ERROR",
                provider=self.provider,
                status_code=500,
                response_body=b"SYNTHETIC_PRIVATE_PROVIDER_BODY",
            )

    client = FailingClient(lambda _request: "unused", "test-open-weight")
    with pytest.raises(ProcessGenerationUnavailableError) as failure:
        await _model_sources(ctx, client)
    assert len(client.calls) == 1
    assert str(failure.value) == "process generation produced no admitted output"
    assert failure.value.__cause__ is None and failure.value.__context__ is None
    async with ctx.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, "evidence-source-invocation")
        assert invocation is not None and invocation.status == "failed"
        assert invocation.error["message"] == "SYNTHETIC_PRIVATE_PROVIDER_ERROR"
        assert invocation.request_artifact_id is not None
        assert invocation.response_artifact_id is None


@pytest.mark.parametrize("invalid_usage", [True, None, "SYNTHETIC_PRIVATE_USAGE"])
async def test_generation_rejects_unknown_or_noninteger_usage(
    evidence_context, invalid_usage
) -> None:
    ctx = evidence_context

    class InvalidUsageClient(CallbackGenerationClient):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            result = await super().generate(request)
            return replace(result, usage={**result.usage, "input_tokens": invalid_usage})

    client = InvalidUsageClient(lambda _request: "public output", "test-open-weight")
    with pytest.raises(ProcessGenerationUnavailableError) as failure:
        await _model_sources(ctx, client)
    assert len(client.calls) == 1
    assert "SYNTHETIC_" not in str(failure.value)
    async with ctx.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, "evidence-source-invocation")
        assert invocation is not None and invocation.status == "failed"


async def test_generation_cancellation_preserves_semantics_without_private_payload(
    evidence_context,
) -> None:
    ctx = evidence_context

    class CancelledClient(CallbackGenerationClient):
        async def generate(self, request: GenerationRequest) -> GenerationResult:
            self.calls.append(request)
            raise asyncio.CancelledError("SYNTHETIC_PRIVATE_CANCELLATION")

    client = CancelledClient(lambda _request: "unused", "test-open-weight")
    with pytest.raises(asyncio.CancelledError) as cancellation:
        await _model_sources(ctx, client)
    assert len(client.calls) == 1
    assert str(cancellation.value) == "process generation cancelled"
    assert cancellation.value.__cause__ is None and cancellation.value.__context__ is None
    async with ctx.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, "evidence-source-invocation")
        assert invocation is not None and invocation.status == "cancelled"


async def test_evidence_read_cancellation_has_no_private_payload(
    evidence_context, monkeypatch
) -> None:
    ctx = evidence_context
    review = await _review(ctx)

    async def cancelled_read(*args, **kwargs):
        raise asyncio.CancelledError("SYNTHETIC_PRIVATE_READ_CANCELLATION")

    monkeypatch.setattr(ctx.evidence, "_read", cancelled_read)
    async with ctx.database.transaction() as session:
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
    assert str(cancellation.value) == "process evidence read cancelled"
    assert cancellation.value.__cause__ is None and cancellation.value.__context__ is None


async def test_candidate_classification_is_not_admission(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
        assert await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == 0


async def test_reviewed_derivative_has_separate_worker_view_and_retained_sources(
    evidence_context,
) -> None:
    ctx = evidence_context
    sources = await _model_sources(ctx)
    review = await _review(ctx, sources=sources, training=True)
    async with ctx.database.transaction() as session:
        reference = await ctx.evidence.admit(session, review=review, now=ctx.clock())
        assert await ctx.evidence.admit(session, review=review, now=ctx.clock()) == reference
        assert (
            await ctx.evidence.read(
                session,
                reference=reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.TRAINING_PROJECTION,
                now=ctx.clock(),
            )
            == b"The checked calculation gives 42."
        )
        retained = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "process_evidence_admission",
                    ArtifactReferenceRow.owner_id == reference.process_artifact_id,
                )
            )
        )
        assert retained == {
            review.candidate_artifact_id,
            *(source.artifact.artifact_id for source in sources),
        }
        privileged = await ctx.evidence.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert privileged.review == review
        assert privileged.admitted_at >= review.reviewed_at
        assert privileged.authorization_sequence == 2
    worker_bytes = canonical_json_bytes(reference)
    for source in sources:
        assert source.artifact.artifact_id.encode() not in worker_bytes
        assert source.artifact.digest.encode() not in worker_bytes
        assert source.classification_digest.encode() not in worker_bytes
    assert b"reviewer" not in worker_bytes and b"forensic" not in worker_bytes
    # A new broker instance uses stored authority, with no worker-local context.
    reopened = ProcessEvidenceStore(
        catalog=ctx.catalog, amber=ctx.amber, policy=ctx.evidence.policy
    )
    async with ctx.database.transaction() as session:
        assert (
            await reopened.read(
                session,
                reference=reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
            == b"The checked calculation gives 42."
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer_id", "worker-self-approval"),
        ("reviewer_id", "reviewer-not-in-amber"),
        ("policy_digest", sha256_digest("different policy")),
        ("contamination_scope", "test-sealed"),
        ("candidate_classification_digest", sha256_digest("forged classification")),
    ],
)
async def test_review_cannot_change_authority_or_scope(evidence_context, field, value) -> None:
    ctx = evidence_context
    review = (await _review(ctx)).model_copy(update={field: value})
    async with ctx.database.transaction() as session:
        with pytest.raises((PermissionError, ValueError)):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 0
        )
        assert await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == 0


async def test_read_revalidates_scope_metadata_use_and_amber(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        reference = await ctx.evidence.admit(session, review=review, now=ctx.clock())
    for ref, execution_digest, use in (
        (reference, sha256_digest("other execution"), ProcessEvidenceUse.PROCESS),
        (
            reference.model_copy(update={"size_bytes": 1}),
            ctx.execution_digest,
            ProcessEvidenceUse.PROCESS,
        ),
        (reference, ctx.execution_digest, ProcessEvidenceUse.TRAINING_PROJECTION),
    ):
        async with ctx.database.transaction() as session:
            with pytest.raises(PermissionError):
                await ctx.evidence.read(
                    session,
                    reference=ref,
                    execution_digest=execution_digest,
                    use=use,
                    now=ctx.clock(),
                )
    async with ctx.database.transaction() as session:
        await ctx.amber.transition(
            session,
            authorization_digest=ctx.execution.amber_authorization_digest,
            to_status=AmberStatus.PAUSED,
            actor_id="operator",
            reason="synthetic pause",
            occurred_at=ctx.clock(),
        )
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
        # Pause withholds worker use while retaining privileged review history.
        assert (
            await ctx.evidence.inspect_admission(
                session, process_artifact_id=reference.process_artifact_id
            )
        ).review == review


@pytest.mark.parametrize(
    "evidence_context",
    [ProjectSplit.SEALED, ProjectSplit.VALIDATION, ProjectSplit.ADAPTIVE_DEVELOPMENT],
    indirect=True,
)
async def test_nontraining_partition_cannot_admit_training_projection(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx, training=True)
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="process-training"):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())


async def test_forged_forensic_provenance_and_identifier_leak_are_denied(evidence_context) -> None:
    ctx = evidence_context
    sources = await _model_sources(ctx)
    for review in (
        await _review(ctx, sources=sources, content="Source: " + sources[0].artifact.digest),
        await _review(
            ctx,
            sources=(
                sources[0].model_copy(update={"classification_digest": sha256_digest("forgery")}),
            ),
        ),
    ):
        async with ctx.database.transaction() as session:
            with pytest.raises(PermissionError):
                await ctx.evidence.admit(session, review=review, now=ctx.clock())
    async with ctx.database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 0
        )


async def test_unknown_forensic_origin_cannot_enter_institution(evidence_context) -> None:
    ctx = evidence_context
    raw = ctx.artifacts.put_text("unbound forensic capture", restricted=True, raw_data=True)
    async with ctx.database.transaction() as session:
        await ctx.information.classify(
            session,
            artifact=raw,
            information_class=InformationClass.FORENSIC,
            classified_by="test.broker",
            reason="origin not yet joined",
            classified_at=ctx.clock(),
        )
        source = await ctx.information.forensic_reference(session, artifact_id=raw.artifact_id)
    review = await _review(ctx, sources=(source,))
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="completed source"):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())


async def test_caught_admission_failure_rolls_back_ownership_pins(
    evidence_context, monkeypatch
) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    original = ctx.catalog.reference

    async def fail_after_pin(*args, **kwargs):
        await original(*args, **kwargs)
        raise OSError("injected admission failure after retention pin")

    with monkeypatch.context() as patch:
        patch.setattr(ctx.catalog, "reference", fail_after_pin)
        async with ctx.database.transaction() as session:
            with pytest.raises(OSError, match="injected"):
                await ctx.evidence.admit(session, review=review, now=ctx.clock())
    async with ctx.database.transaction() as session:
        assert (
            await session.scalar(select(func.count()).select_from(ProcessEvidenceAdmissionRow)) == 0
        )
        assert await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == 0
        assert (
            await ctx.evidence.admit(session, review=review, now=ctx.clock())
            == review.process_reference
        )


async def test_information_class_is_immutable_and_legacy_is_unclassified(evidence_context) -> None:
    ctx = evidence_context
    artifact = ctx.artifacts.put_text("legacy catalogue object", restricted=True)
    async with ctx.database.transaction() as session:
        await ctx.catalog.register(session, artifact)
        with pytest.raises(PermissionError, match="no admitted information"):
            await ctx.information.get(session, artifact_id=artifact.artifact_id)
        await ctx.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.PROCESS_CANDIDATE,
            classified_by="test.broker",
            reason="explicit classification",
            classified_at=ctx.clock(),
        )
    async with ctx.database.transaction() as session:
        row = await session.get(ArtifactInformationRow, artifact.artifact_id)
        assert row is not None
        row.information_class = InformationClass.FORENSIC.value
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()


async def test_persisted_admission_corruption_is_rejected(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
        receipt = await ctx.evidence.inspect_admission(
            session, process_artifact_id=review.process_reference.process_artifact_id
        )
    damaged = receipt.model_dump(mode="json")
    del damaged["review"]["process_reference"]["domain"]
    async with ctx.database.transaction() as session:
        # Simulate damaged persistence independently of the immutable ORM guard.
        await session.execute(update(ProcessEvidenceAdmissionRow).values(record_json=damaged))
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )


async def test_worker_read_failure_never_echoes_privileged_validation_input(
    evidence_context,
) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
        receipt = await ctx.evidence.inspect_admission(
            session, process_artifact_id=review.process_reference.process_artifact_id
        )
    damaged = receipt.model_dump(mode="json")
    damaged["review"]["admission_policy"]["maximum_bytes"] = "SYNTHETIC_FORENSIC_MARKER"
    async with ctx.database.transaction() as session:
        await session.execute(update(ProcessEvidenceAdmissionRow).values(record_json=damaged))
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="process evidence read denied") as failure:
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
        assert "SYNTHETIC_FORENSIC_MARKER" not in str(failure.value)
        assert failure.value.__cause__ is None
        assert failure.value.__context__ is None


async def test_missing_ownership_blocks_reads_and_duplicate_admission(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
    async with ctx.database.transaction() as session:
        await session.execute(delete(ArtifactReferenceRow))
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
        with pytest.raises(ArtifactIntegrityError, match="retention ownership"):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())


async def test_forensic_source_cannot_be_admitted_as_its_own_process_artifact(
    evidence_context,
) -> None:
    ctx = evidence_context
    source = (await _model_sources(ctx))[0]
    review = await _review(ctx)
    forged = review.model_copy(
        update={
            "candidate_artifact_id": source.artifact.artifact_id,
            "candidate_classification_digest": source.classification_digest,
            "process_reference": review.process_reference.model_copy(
                update={
                    "content_digest": source.artifact.digest,
                    "media_type": source.artifact.media_type,
                    "size_bytes": source.artifact.size_bytes,
                }
            ),
        }
    )
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="classified process candidate"):
            await ctx.evidence.admit(session, review=forged, now=ctx.clock())
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=source,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )


async def test_forensic_sources_cannot_cross_execution_scope(evidence_context) -> None:
    ctx = evidence_context
    sources = await _model_sources(ctx)
    ctx.execution = ctx.execution.model_copy(update={"execution_id": "other-execution", "seed": 99})
    async with ctx.database.transaction() as session:
        await ctx.process.register_execution(session, ctx.execution)
    review = await _review(ctx, sources=sources)
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="completed source"):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())


async def test_review_cannot_override_rights_expiry_or_existing_receipt(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx, training=True)
    insufficient = review.model_copy(
        update={
            "rights": review.rights.model_copy(
                update={
                    "permitted_uses": tuple(
                        use for use in review.rights.permitted_uses if use != RightsUse.PROCESS
                    ),
                }
            )
        }
    )
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="process-training"):
            await ctx.evidence.admit(session, review=insufficient, now=ctx.clock())
        with pytest.raises(PermissionError, match="Amber"):
            await ctx.evidence.admit(session, review=review, now=NOW + timedelta(days=2))
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
        with pytest.raises(ArtifactIntegrityError, match="reused"):
            await ctx.evidence.admit(
                session,
                review=review.model_copy(update={"rationale": "rewritten history"}),
                now=ctx.clock(),
            )


async def test_admission_time_cannot_be_backdated_on_read_or_retry(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
        with pytest.raises(ProcessEvidenceReadDeniedError):
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=review.reviewed_at,
            )
        with pytest.raises(PermissionError, match="predates"):
            await ctx.evidence.admit(session, review=review, now=review.reviewed_at)


@pytest.mark.parametrize("bound", ["maximum_forensic_sources", "maximum_forensic_source_bytes"])
async def test_provenance_is_bounded_before_admission(evidence_context, bound) -> None:
    ctx = evidence_context
    sources = await _model_sources(ctx)
    ctx.evidence = ProcessEvidenceStore(
        catalog=ctx.catalog,
        amber=ctx.amber,
        policy=ctx.evidence.policy.model_copy(update={bound: 0}),
    )
    review = await _review(ctx, sources=sources)
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="provenance bounds"):
            await ctx.evidence.admit(session, review=review, now=ctx.clock())


async def test_pinned_admission_retains_content_through_catalog_gc(evidence_context) -> None:
    ctx = evidence_context
    review = await _review(ctx)
    orphan = ctx.artifacts.put_text("unadmitted orphan")
    async with ctx.database.transaction() as session:
        await ctx.evidence.admit(session, review=review, now=ctx.clock())
    async with ctx.database.transaction() as session:
        retained = await ctx.catalog.referenced_digests(session)
    result = ctx.artifacts.collect_garbage(
        referenced_digests=retained,
        minimum_age=timedelta(days=1),
        dry_run=False,
        now=datetime.now().astimezone() + timedelta(days=2),
    )
    assert result.deleted == (orphan.digest,)
    async with ctx.database.transaction() as session:
        assert (
            await ctx.evidence.read(
                session,
                reference=review.process_reference,
                execution_digest=ctx.execution_digest,
                use=ProcessEvidenceUse.PROCESS,
                now=ctx.clock(),
            )
            == b"The checked calculation gives 42."
        )
