from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.information import (
    ArtifactInformationStore,
    ForensicArtifactRef,
    InformationClass,
    ProcessArtifactRef,
)
from padawan.artifacts.store import ArtifactCatalog, ArtifactIntegrityError, LocalArtifactStore
from padawan.governance.amber import AmberActionRequest, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.contracts import (
    RightsUse,
    SamplingConfiguration,
    project_authored_internal_rights,
)
from padawan.models.database import Database
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactReferenceRow,
    ProcessEvidenceAdmissionRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProcessExecutionManifest,
    ProcessProgram,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError, ProcessEvidenceStore
from padawan.pprl.evidence_contracts import (
    EvidenceAdmissionPolicy,
    ProcessEvidenceAdmission,
    ProcessEvidenceUse,
)
from padawan.pprl.generation import ProcessGenerationExecutor
from padawan.pprl.store import ProcessStore
from tests.helpers import CallbackGenerationClient
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


@dataclass
class EvidenceContext:
    database: Database
    artifacts: LocalArtifactStore
    catalog: ArtifactCatalog
    information: ArtifactInformationStore
    evidence: ProcessEvidenceStore
    amber: AmberStore
    process: ProcessStore
    execution: ProcessExecutionManifest
    program: ProcessProgram
    split: ProjectSplit
    clock: Callable[[], datetime]

    @property
    def execution_digest(self) -> str:
        return sha256_digest(self.execution)


@pytest_asyncio.fixture
async def evidence_context(database, tmp_path, pprl_now, request) -> EvidenceContext:
    split = getattr(request, "param", ProjectSplit.TRAIN)
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    process = ProcessStore(amber)
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=split,
            seed=93,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(
            program_digest=program_digest, distribution_digest=distribution_digest
        )
        await amber.prepare(session, envelope=authorization, actor_id="preparer")
        for status, minute in ((AmberStatus.AUTHORIZED, 1), (AmberStatus.ACTIVE, 2)):
            await amber.transition(
                session,
                authorization_digest=authorization.digest,
                to_status=status,
                actor_id="reviewer-a",
                reason="synthetic offline fixture",
                evidence_refs=("review:test",),
                occurred_at=NOW + timedelta(minutes=minute),
            )
        manifest = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization.digest,
        )
        execution_digest = await process.register_execution(session, manifest)
        await process.create_rollout(
            session,
            execution_digest=execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(objective="exercise reviewed evidence admission"),
            rollout_id="evidence-rollout",
            created_at=NOW + timedelta(minutes=3),
        )
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    catalog = ArtifactCatalog(artifacts)
    policy = EvidenceAdmissionPolicy(
        policy_id="test.evidence-admission",
        version="1.0.0",
        reviewer_ids=("reviewer-a", "reviewer-not-in-amber"),
        maximum_bytes=100_000,
        maximum_forensic_sources=4,
        maximum_forensic_source_bytes=1_000_000,
    )
    return EvidenceContext(
        database,
        artifacts,
        catalog,
        ArtifactInformationStore(catalog),
        ProcessEvidenceStore(catalog=catalog, amber=amber, policy=policy),
        amber,
        process,
        manifest,
        process_program,
        split,
        pprl_now,
    )


async def _review(
    ctx: EvidenceContext,
    *,
    content: str = "The checked calculation gives 42.",
    sources: tuple[ForensicArtifactRef, ...] = (),
    training: bool = False,
) -> ProcessEvidenceAdmission:
    artifact = ctx.artifacts.put_text(content, restricted=True)
    async with ctx.database.transaction() as session:
        classified = await ctx.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.PROCESS_CANDIDATE,
            classified_by="test.broker",
            reason="synthetic candidate awaiting an explicit review",
            classified_at=ctx.clock(),
        )
    return ProcessEvidenceAdmission(
        process_reference=ProcessArtifactRef(
            process_artifact_id=f"process-artifact-{uuid4().hex}",
            execution_digest=ctx.execution_digest,
            content_digest=artifact.digest,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
        ),
        candidate_artifact_id=artifact.artifact_id,
        candidate_classification_digest=classified.digest,
        forensic_sources=sources,
        admission_policy=ctx.evidence.policy,
        policy_digest=ctx.evidence.policy.digest,
        reviewer_id="reviewer-a",
        rationale="Synthetic review of exact candidate bytes and the declared source set.",
        contamination_scope=f"test-{ctx.split.value}",
        rights=project_authored_internal_rights(reviewed_at=NOW),
        allowed_uses=(ProcessEvidenceUse.PROCESS, ProcessEvidenceUse.TRAINING_PROJECTION)
        if training
        else (ProcessEvidenceUse.PROCESS,),
        reviewed_at=ctx.clock(),
    )


async def _model_sources(ctx: EvidenceContext) -> tuple[ForensicArtifactRef, ...]:
    instant = ctx.clock()
    async with ctx.database.transaction() as session:
        claimed = await ctx.process.claim_next(
            session, worker_id="synthetic-worker", lease_for=timedelta(minutes=5), now=instant
        )
        assert claimed is not None
        admission = await ctx.amber.admit(
            session,
            request=AmberActionRequest(
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
                worker_model_digest=sha256_digest(worker_model()),
                target_class="scientific_math",
                environment_fingerprint=ctx.execution.environment_fingerprint,
                projected_usage=ProjectBudgetUsage(
                    actions=1,
                    input_tokens=10,
                    output_tokens=12,
                    artifact_bytes=1_000_000,
                    wall_time_seconds=30.0,
                ),
                projected_artifact_bytes=1_000_000,
                requested_at=instant,
            ),
            active_workers=0,
        )
    client = CallbackGenerationClient(
        lambda _request: "synthetic raw model output", "test-open-weight"
    )
    generation = ProcessGenerationExecutor(
        database=ctx.database,
        executor=IdempotentGenerationExecutor(
            database=ctx.database, artifacts=ctx.artifacts, client=client
        ),
    )
    result = await generation.execute(
        rollout_id=claimed.rollout.rollout_id,
        lease_token=claimed.lease_token,
        amber_decision_id=admission.decision_id,
        invocation_id="evidence-source-invocation",
        role_id="researcher",
        worker_model_digest=sha256_digest(worker_model()),
        purpose="process_worker",
        provider="test-open-weight",
        request=GenerationRequest(
            request_id="evidence-source-request",
            instructions="synthetic fixture",
            input="calculate",
            sampling=SamplingConfiguration(max_output_tokens=12),
            store=False,
        ),
    )
    assert len(client.calls) == 1
    async with ctx.database.transaction() as session:
        return tuple(
            [
                await ctx.information.forensic_reference(session, artifact_id=artifact.artifact_id)
                for artifact in result.artifact_refs
            ]
        )


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
