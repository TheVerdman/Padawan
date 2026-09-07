from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update

from padawan.artifacts.information import InformationClass
from padawan.atlas.contracts import (
    AtlasCampaignManifest,
    AtlasItemManifest,
    AtlasSuiteManifest,
    AtlasTrialRequest,
    DatasetGovernance,
    EvaluationClass,
    content_id,
)
from padawan.atlas.evidence import AtlasEvidenceSourceBoundary
from padawan.atlas.evidence_contracts import (
    AtlasEvidenceDisclosurePolicy,
    AtlasEvidenceOriginReview,
    AtlasEvidenceSourceScope,
    AtlasProcessEvidenceAdmissionRecord,
)
from padawan.models.contracts import RightsUse, project_authored_internal_rights
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    AtlasDatasetGovernanceRow,
    ExternalCallRow,
    ProcessEvidenceAdmissionRow,
)
from padawan.pprl.contracts import ProjectBudgetUsage, ProjectSplit, ProjectStatePayload
from padawan.pprl.evidence import ProcessEvidenceReadDeniedError, ProcessEvidenceStore
from padawan.pprl.evidence_contracts import ProcessEvidenceAdmissionRecord, ProcessEvidenceUse
from padawan.pprl.store import ProcessStore
from tests.pprl_evidence_helpers import _review
from tests.pprl_evidence_helpers import evidence_context as evidence_context
from tests.support.atlas_artifacts import _atlas_evidence
from tests.support.atlas_registry import NOW as ATLAS_NOW
from tests.support.atlas_registry import (
    _allocation,
    _binding,
    _governance,
    _profile,
    _rehash_run_manifest,
    _request,
    _run_manifest,
    _run_row,
    _trial_result,
)


@pytest_asyncio.fixture
async def atlas_process(evidence_context):
    ctx = evidence_context
    rights = project_authored_internal_rights(reviewed_at=ATLAS_NOW)
    source = await _atlas_evidence(
        ctx.database,
        store=ctx.artifacts,
        governance=_governance().model_copy(update={"rights": rights}),
    )
    async with ctx.database.transaction() as session:
        await source.registry.record_trial_result(session, source.result)
    policy = AtlasEvidenceDisclosurePolicy(
        policy_id="fixture.atlas-disclosure",
        version="1",
        target_execution_digest=ctx.execution_digest,
        target_contamination_scope=f"test-{ctx.split.value}",
        intervention_description="Offline fixture with explicitly reviewed external evidence.",
        reviewer_ids=("reviewer-a",),
        source_scopes=(
            AtlasEvidenceSourceScope(
                campaign_digest=source.request.campaign_digest,
                condition_id=source.request.condition_id,
                suite_digest=source.request.suite_digest,
                research_execution_digest=source.request.research_execution_digest,
                output_rights=rights,
            ),
        ),
        created_at=ctx.clock(),
        expires_at=ctx.clock() + timedelta(hours=1),
    )
    boundary = AtlasEvidenceSourceBoundary(catalog=ctx.catalog, policy=policy)
    ctx.evidence = ProcessEvidenceStore(
        catalog=ctx.catalog,
        amber=ctx.amber,
        policy=ctx.evidence.policy.model_copy(update={"maximum_forensic_sources": 64}),
        atlas=boundary,
    )
    ctx.process = ProcessStore(ctx.amber, evidence=ctx.evidence)
    return SimpleNamespace(process=ctx, source=source, boundary=boundary)


async def _prepare(ctx, *, content="The inspected arithmetic trial succeeded.", result_ids=None):
    async with ctx.process.database.transaction() as session:
        described = await ctx.boundary.describe(
            session, result_ids=result_ids or (ctx.source.result.result_id,)
        )
    review = await _review(ctx.process, sources=described.forensic_sources, content=content)
    origin = AtlasEvidenceOriginReview(
        candidate_review_digest=review.digest,
        disclosure_policy=ctx.boundary.policy,
        policy_digest=ctx.boundary.policy.digest,
        trials=described.trials,
    )
    return review, origin


async def _admit(ctx, review, origin):
    async with ctx.process.database.transaction() as session:
        return await ctx.process.evidence.admit_atlas(
            session, review=review, origin=origin, now=ctx.process.clock()
        )


async def _read(ctx, reference, *, use=ProcessEvidenceUse.PROCESS, now=None, broker=None):
    async with ctx.process.database.transaction() as session:
        return await (broker or ctx.process.evidence).read(
            session,
            reference=reference,
            execution_digest=ctx.process.execution_digest,
            use=use,
            now=now or ctx.process.clock(),
        )


async def _overlap(ctx, evaluation_class, *, same_prompt=False):
    """Register real alternate suite membership through the ordinary Atlas registry."""
    async with ctx.process.database.transaction() as session:
        row = await session.get(AtlasDatasetGovernanceRow, ctx.source.suite.governance_id)
        governance = DatasetGovernance.model_validate(row.record_json, strict=False).model_copy(
            update={
                "governance_id": f"overlap-{evaluation_class.value}",
                "dataset_revision": f"overlap-{evaluation_class.value}",
                "evaluation_class": evaluation_class,
            }
        )
        await ctx.source.registry.register_dataset_governance(session, governance)
        item = ctx.source.item
        if same_prompt:
            item = item.model_copy(
                update={"metadata": {"alias_test": "same prompt, different item"}}
            )
            identity = item.model_dump(mode="json", exclude={"item_id", "item_digest", "prompt"})
            item = AtlasItemManifest.model_validate(
                {
                    **item.model_dump(mode="python"),
                    "item_id": content_id("atlas-item", identity),
                    "item_digest": sha256_digest(identity),
                }
            )
        suite = ctx.source.suite.model_copy(
            update={
                "suite_id": f"overlap-{evaluation_class.value}",
                "governance_id": governance.governance_id,
                "evaluation_class": evaluation_class,
                "items": (item,),
                "item_digests": (item.item_digest,),
            }
        )
        identity = {
            field: getattr(suite, field)
            for field in (
                "suite_id",
                "version",
                "benchmark_id",
                "benchmark_version",
                "split",
                "governance_id",
                "adapter_kind",
                "evaluation_class",
                "item_digests",
                "modality_gates",
                "task_manifest_digests",
                "corpus_digests",
                "environment_fingerprints",
                "evaluation_suite_manifest_digest",
            )
        }
        suite = AtlasSuiteManifest.model_validate(
            {**suite.model_dump(mode="python"), "content_digest": sha256_digest(identity)}
        )
        await ctx.source.registry.register_suite(session, suite)


async def test_reviewed_atlas_derivative_survives_broker_replacement_and_process_retention(
    atlas_process,
):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    reference = await _admit(ctx, review, origin)
    assert await _admit(ctx, review, origin) == reference
    assert await _read(ctx, reference) == b"The inspected arithmetic trial succeeded."
    reopened = ProcessEvidenceStore(
        catalog=ctx.process.catalog,
        amber=ctx.process.amber,
        policy=ctx.process.evidence.policy,
        atlas=AtlasEvidenceSourceBoundary(catalog=ctx.process.catalog, policy=ctx.boundary.policy),
    )
    assert await _read(ctx, reference, broker=reopened) == await _read(ctx, reference)
    async with ctx.process.database.transaction() as session:
        receipt = await reopened.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert isinstance(receipt, AtlasProcessEvidenceAdmissionRecord)
        assert receipt.review == review and receipt.atlas_origin == origin
        created = await ctx.process.process.create_rollout(
            session,
            execution_digest=ctx.process.execution_digest,
            replication_index=1,
            initial_state=ProjectStatePayload(
                objective="Apply separately reviewed evidence",
                artifact_refs=(reference,),
                budget_usage=ProjectBudgetUsage(artifact_bytes=reference.size_bytes),
            ),
            rollout_id="atlas-informed-process",
            created_at=ctx.process.clock(),
        )
        state = await ctx.process.process.get_state(session, state_id=created.initial_state_id)
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
            *(source.artifact.artifact_id for source in review.forensic_sources),
        }
        public = canonical_json_bytes(state.payload)
        for identifier in (
            origin.digest,
            ctx.source.result.result_id,
            ctx.source.request.request_id,
        ):
            assert identifier.encode() not in public
        assert b"atlas_origin" not in public and b"raw_data" not in public


async def test_atlas_derivative_does_not_gain_training_use_or_implicit_legacy_admission(
    atlas_process,
):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    async with ctx.process.database.transaction() as session:
        with pytest.raises(PermissionError, match="completed source"):
            await ctx.process.evidence.admit(session, review=review, now=ctx.process.clock())
    reference = await _admit(ctx, review, origin)
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference, use=ProcessEvidenceUse.TRAINING_PROJECTION)
    training = review.model_copy(
        update={
            "allowed_uses": (ProcessEvidenceUse.PROCESS, ProcessEvidenceUse.TRAINING_PROJECTION)
        }
    )
    with pytest.raises(PermissionError):
        await _admit(
            ctx, training, origin.model_copy(update={"candidate_review_digest": training.digest})
        )


async def test_missing_atlas_composition_cannot_admit_or_read_a_reviewed_origin(atlas_process):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    unconfigured = ProcessEvidenceStore(
        catalog=ctx.process.catalog, amber=ctx.process.amber, policy=ctx.process.evidence.policy
    )
    async with ctx.process.database.transaction() as session:
        with pytest.raises(PermissionError, match="configured boundary"):
            await unconfigured.admit_atlas(
                session, review=review, origin=origin, now=ctx.process.clock()
            )
    reference = await _admit(ctx, review, origin)
    with pytest.raises(ProcessEvidenceReadDeniedError) as denied:
        await _read(ctx, reference, broker=unconfigured)
    assert denied.value.__cause__ is None and denied.value.__context__ is None
    assert str(denied.value) == "process evidence read denied"


@pytest.mark.parametrize("field", ["candidate_review_digest", "result_digest", "context_digest"])
async def test_forged_origin_cannot_substitute_review_or_source(atlas_process, field):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    if field == "candidate_review_digest":
        forged = origin.model_copy(update={field: sha256_digest("wrong review")})
    else:
        forged = origin.model_copy(
            update={
                "trials": (
                    origin.trials[0].model_copy(update={field: sha256_digest("wrong source")}),
                )
            }
        )
    with pytest.raises(PermissionError):
        await _admit(ctx, review, forged)


@pytest.mark.parametrize("field", ["result_id", "run_id", "uri", "digest", "policy_id"])
async def test_candidate_cannot_carry_discoverable_atlas_identifiers(atlas_process, field):
    ctx = atlas_process
    identifier = {
        "result_id": ctx.source.result.result_id,
        "run_id": ctx.source.request.run_id,
        "uri": ctx.source.response.uri,
        "digest": ctx.source.result.result_digest,
        "policy_id": ctx.boundary.policy.policy_id,
    }[field]
    review, origin = await _prepare(ctx, content=f"Follow this source: {identifier}")
    with pytest.raises(PermissionError, match="identifier"):
        await _admit(ctx, review, origin)


@pytest.mark.parametrize("loss", ["source_owner", "source_blob", "origin", "version"])
async def test_retained_origin_never_falls_back_when_evidence_is_lost(atlas_process, loss):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    reference = await _admit(ctx, review, origin)
    async with ctx.process.database.transaction() as session:
        if loss == "source_owner":
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "atlas_trial_request"
                )
            )
        elif loss == "source_blob":
            ctx.process.artifacts._path_for_hex(ctx.source.response.digest[7:]).unlink()
        else:
            row = await session.get(ProcessEvidenceAdmissionRow, reference.process_artifact_id)
            damaged = {**row.record_json}
            damaged.pop("atlas_origin")
            if loss == "version":
                damaged["schema_version"] = "1.0.0"
            await session.execute(
                update(ProcessEvidenceAdmissionRow)
                .where(
                    ProcessEvidenceAdmissionRow.process_artifact_id == reference.process_artifact_id
                )
                .values(record_json=damaged, record_digest=sha256_digest(damaged))
            )
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference)


async def test_policy_expiry_and_replacement_withhold_previously_admitted_bytes(atlas_process):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    reference = await _admit(ctx, review, origin)
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference, now=ctx.boundary.policy.expires_at)
    ctx.process.evidence.atlas = AtlasEvidenceSourceBoundary(
        catalog=ctx.process.catalog, policy=ctx.boundary.policy.model_copy(update={"version": "2"})
    )
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference)


@pytest.mark.parametrize("change", ["missing", "extra"])
async def test_review_must_cover_exactly_the_retained_atlas_artifact_set(atlas_process, change):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    if change == "missing":
        refs = review.forensic_sources[:-1]
    else:
        artifact = ctx.process.artifacts.put_text(
            "unrelated forensic source", restricted=True, raw_data=True
        )
        async with ctx.process.database.transaction() as session:
            await ctx.process.information.classify(
                session,
                artifact=artifact,
                information_class=InformationClass.FORENSIC,
                classified_by="fixture.broker",
                reason="explicit extra-source rejection fixture",
                classified_at=ctx.process.clock(),
            )
            extra = await ctx.process.information.forensic_reference(
                session, artifact_id=artifact.artifact_id
            )
        refs = tuple(
            sorted((*review.forensic_sources, extra), key=lambda ref: ref.artifact.artifact_id)
        )
    review = review.model_copy(
        update={"forensic_sources": refs, "reviewed_at": ctx.process.clock()}
    )
    origin = origin.model_copy(update={"candidate_review_digest": review.digest})
    with pytest.raises(PermissionError, match="exact source set"):
        await _admit(ctx, review, origin)


@pytest.mark.parametrize(
    "field", ["target_execution_digest", "target_contamination_scope", "reviewer_ids"]
)
async def test_disclosure_policy_must_authorize_exact_destination_and_reviewer(
    atlas_process, field
):
    ctx = atlas_process
    values = {
        "target_execution_digest": sha256_digest("another execution"),
        "target_contamination_scope": "another partition",
        "reviewer_ids": ("another-reviewer",),
    }
    ctx.boundary = AtlasEvidenceSourceBoundary(
        catalog=ctx.process.catalog,
        policy=ctx.boundary.policy.model_copy(update={field: values[field]}),
    )
    ctx.process.evidence.atlas = ctx.boundary
    review, origin = await _prepare(ctx)
    with pytest.raises(PermissionError, match="use authority"):
        await _admit(ctx, review, origin)


async def test_unconfigured_source_is_rejected_before_physical_source_reads(
    atlas_process, monkeypatch
):
    ctx = atlas_process
    scope = ctx.boundary.policy.source_scopes[0].model_copy(
        update={"suite_digest": sha256_digest("unapproved suite")}
    )
    boundary = AtlasEvidenceSourceBoundary(
        catalog=ctx.process.catalog,
        policy=ctx.boundary.policy.model_copy(update={"source_scopes": (scope,)}),
    )

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unapproved sources must not reach backend inspection")

    monkeypatch.setattr(boundary.registry, "validate_trial_artifacts", forbidden)
    async with ctx.process.database.transaction() as session:
        with pytest.raises(PermissionError, match="configured disclosure sources"):
            await boundary.describe(session, result_ids=(ctx.source.result.result_id,))


@pytest.mark.parametrize("surface", ["dataset", "output"])
async def test_source_evaluation_rights_do_not_imply_disclosure_rights(atlas_process, surface):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    source_rights = ctx.boundary.policy.source_scopes[0].output_rights
    rights = source_rights.model_copy(
        update={
            "permitted_uses": tuple(
                use for use in source_rights.permitted_uses if use != RightsUse.EVIDENCE_RETENTION
            )
        }
    )
    if surface == "dataset":
        async with ctx.process.database.transaction() as session:
            row = await session.get(AtlasDatasetGovernanceRow, ctx.source.suite.governance_id)
            governance = DatasetGovernance.model_validate(row.record_json, strict=False).model_copy(
                update={"rights": rights}
            )
            await session.execute(
                update(AtlasDatasetGovernanceRow)
                .where(AtlasDatasetGovernanceRow.governance_id == governance.governance_id)
                .values(
                    record_json=governance.model_dump(mode="json"),
                    record_digest=sha256_digest(governance),
                    rights_digest=sha256_digest(rights),
                )
            )
    else:
        scope = ctx.boundary.policy.source_scopes[0].model_copy(update={"output_rights": rights})
        ctx.boundary = AtlasEvidenceSourceBoundary(
            catalog=ctx.process.catalog,
            policy=ctx.boundary.policy.model_copy(update={"source_scopes": (scope,)}),
        )
        ctx.process.evidence.atlas = ctx.boundary
        origin = origin.model_copy(
            update={
                "disclosure_policy": ctx.boundary.policy,
                "policy_digest": ctx.boundary.policy.digest,
            }
        )
    with pytest.raises(PermissionError, match="rights"):
        await _admit(ctx, review, origin)


@pytest.mark.parametrize(
    "evaluation_class", [EvaluationClass.CHALLENGE, EvaluationClass.SEALED_PROMOTION]
)
@pytest.mark.parametrize("same_prompt", [False, True])
async def test_protected_item_or_prompt_membership_blocks_disclosure(
    atlas_process, evaluation_class, same_prompt
):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    reference = await _admit(ctx, review, origin)
    await _overlap(ctx, evaluation_class, same_prompt=same_prompt)
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference)
    with pytest.raises(PermissionError, match="challenge or sealed"):
        await _prepare(ctx)


@pytest.mark.parametrize(
    "evidence_context", [ProjectSplit.TRAIN, ProjectSplit.ADAPTIVE_DEVELOPMENT], indirect=True
)
async def test_adaptive_overlap_requires_adaptive_destination(atlas_process):
    ctx = atlas_process
    await _overlap(ctx, EvaluationClass.ADAPTIVE_SEARCH)
    review, origin = await _prepare(ctx)
    if ctx.process.split == ProjectSplit.TRAIN:
        with pytest.raises(PermissionError, match="adaptive destination"):
            await _admit(ctx, review, origin)
    else:
        reference = await _admit(ctx, review, origin)
        assert await _read(ctx, reference) == b"The inspected arithmetic trial succeeded."


@pytest.mark.parametrize(
    "evidence_context", [ProjectSplit.VALIDATION, ProjectSplit.SEALED], indirect=True
)
async def test_evaluation_destinations_cannot_receive_atlas_findings(atlas_process):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    with pytest.raises(PermissionError):
        await _admit(ctx, review, origin)


@pytest.mark.parametrize("point", ["second_pin", "after_record", "cancelled"])
async def test_caught_failure_cannot_publish_partial_atlas_admission(
    atlas_process, monkeypatch, point
):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    async with ctx.process.database.transaction() as session:
        before = await session.scalar(select(func.count()).select_from(ArtifactReferenceRow))
        original = ctx.process.catalog.reference
        original_flush = session.flush
        calls = 0

        async def fail(*args, **kwargs):
            nonlocal calls
            await original(*args, **kwargs)
            calls += 1
            if calls == 2:
                if point == "cancelled":
                    raise asyncio.CancelledError("synthetic cancellation")
                raise RuntimeError("synthetic Atlas admission failure")

        async def fail_flush(*args, **kwargs):
            publishing = any(
                isinstance(record, ProcessEvidenceAdmissionRow) for record in session.new
            )
            await original_flush(*args, **kwargs)
            if publishing:
                raise RuntimeError("synthetic Atlas publication failure")

        with monkeypatch.context() as patch:
            if point == "after_record":
                patch.setattr(session, "flush", fail_flush)
            else:
                patch.setattr(ctx.process.catalog, "reference", fail)
            with pytest.raises(asyncio.CancelledError if point == "cancelled" else RuntimeError):
                await ctx.process.evidence.admit_atlas(
                    session, review=review, origin=origin, now=ctx.process.clock()
                )
        assert (
            await session.scalar(select(func.count()).select_from(ArtifactReferenceRow)) == before
        )
        assert (
            await session.get(
                ProcessEvidenceAdmissionRow, review.process_reference.process_artifact_id
            )
            is None
        )
    await _admit(ctx, review, origin)


async def test_outer_rollback_preserves_sources_without_publishing_an_origin(atlas_process):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    with pytest.raises(RuntimeError, match="outer"):
        async with ctx.process.database.transaction() as session:
            await ctx.process.evidence.admit_atlas(
                session, review=review, origin=origin, now=ctx.process.clock()
            )
            raise RuntimeError("outer rollback fixture")
    async with ctx.process.database.transaction() as session:
        assert (
            await session.get(
                ProcessEvidenceAdmissionRow, review.process_reference.process_artifact_id
            )
            is None
        )
        assert await ctx.source.registry.validate_trial_artifacts(
            session, result_id=ctx.source.result.result_id
        )
    await _admit(ctx, review, origin)


async def test_multiple_trial_origins_keep_distinct_lineage_with_shared_artifact_bytes(
    atlas_process,
):
    ctx = atlas_process
    campaign = ctx.source.campaign.model_copy(update={"campaign_id": "second-evidence-campaign"})
    identity = campaign.model_dump(mode="json", exclude={"manifest_digest", "status", "created_at"})
    campaign = AtlasCampaignManifest.model_validate(
        {**campaign.model_dump(mode="python"), "manifest_digest": sha256_digest(identity)}
    )
    binding = _binding(campaign, ctx.source.suite, _profile(), ctx.source.execution)
    allocation = _allocation(campaign, ctx.source.suite).model_copy(
        update={"allocation_id": "allocation-2"}
    )
    request = _request(
        campaign, ctx.source.suite, ctx.source.item, ctx.source.execution
    ).model_copy(
        update={
            "request_id": "request-2",
            "run_id": "atlas-run-2",
            "allocation_id": allocation.allocation_id,
        }
    )
    identity = request.model_dump(mode="json", exclude={"request_digest", "created_at"})
    request = AtlasTrialRequest.model_validate(
        {**request.model_dump(mode="python"), "request_digest": sha256_digest(identity)}
    )
    run = _rehash_run_manifest(
        _run_manifest(
            campaign, ctx.source.suite, binding, _profile(), ctx.source.execution, request
        ),
        run_id=request.run_id,
    )
    result = _trial_result(ctx.source.execution, request)
    async with ctx.process.database.transaction() as session:
        registry = ctx.source.registry
        await registry.register_campaign(session, campaign)
        await registry.register_execution_binding(session, binding)
        await registry.record_allocation(session, allocation)
        session.add(_run_row(request.run_id, ctx.source.execution))
        await session.flush()
        await registry.register_run_manifest(session, run)
        await registry.record_trial_request(session, request)
        prior = await session.get(ExternalCallRow, ctx.source.request.request_id)
        values = {column.name: getattr(prior, column.name) for column in prior.__table__.columns}
        session.add(
            ExternalCallRow(
                **{**values, "request_id": request.request_id, "run_id": request.run_id}
            )
        )
        await session.flush()
        await registry.record_trial_result(session, result)
    scope = ctx.boundary.policy.source_scopes[0].model_copy(
        update={"campaign_digest": campaign.manifest_digest}
    )
    policy = ctx.boundary.policy.model_copy(
        update={
            "source_scopes": tuple(
                sorted(
                    (*ctx.boundary.policy.source_scopes, scope), key=lambda scope: scope.coordinates
                )
            )
        }
    )
    ctx.boundary = AtlasEvidenceSourceBoundary(catalog=ctx.process.catalog, policy=policy)
    ctx.process.evidence.atlas = ctx.boundary
    review, origin = await _prepare(
        ctx, result_ids=tuple(sorted((ctx.source.result.result_id, result.result_id)))
    )
    assert len(origin.trials) == 2 and len(review.forensic_sources) == 4
    reference = await _admit(ctx, review, origin)
    assert await _read(ctx, reference) == b"The inspected arithmetic trial succeeded."
    async with ctx.process.database.transaction() as session:
        receipt = await ctx.process.evidence.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert isinstance(receipt, AtlasProcessEvidenceAdmissionRecord)
        assert receipt.atlas_origin.trials == origin.trials


async def test_source_context_changes_cannot_reuse_an_earlier_origin(atlas_process):
    ctx = atlas_process
    review, origin = await _prepare(ctx)
    reference = await _admit(ctx, review, origin)
    async with ctx.process.database.transaction() as session:
        await session.execute(
            update(ExternalCallRow)
            .where(ExternalCallRow.request_id == ctx.source.request.request_id)
            .values(provider_response_id="changed-capture-identity")
        )
        # Retention alone still passes: the original source-description digest is
        # what prevents this changed capture context from reusing the old review.
        assert await ctx.source.registry.validate_trial_artifacts(
            session, result_id=ctx.source.result.result_id
        )
    with pytest.raises(ProcessEvidenceReadDeniedError):
        await _read(ctx, reference)


async def test_version_one_admission_keeps_its_original_receipt_format(atlas_process):
    ctx = atlas_process.process
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        reference = await ctx.evidence.admit(session, review=review, now=ctx.clock())
        stored = await session.get(ProcessEvidenceAdmissionRow, reference.process_artifact_id)
        original = ProcessEvidenceAdmissionRecord.model_validate(stored.record_json, strict=False)
        reread = await ctx.evidence.inspect_admission(
            session, process_artifact_id=reference.process_artifact_id
        )
        assert type(reread) is ProcessEvidenceAdmissionRecord
        assert (
            canonical_json_bytes(reread)
            == canonical_json_bytes(original)
            == canonical_json_bytes(stored.record_json)
        )
        assert "atlas_origin" not in stored.record_json
        assert stored.record_digest == original.digest


@pytest.mark.parametrize("count", [0, 17])
async def test_trial_selection_bounds_precede_any_source_inspection(
    atlas_process, monkeypatch, count
):
    ctx = atlas_process

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("unbounded selection must not inspect sources")

    monkeypatch.setattr(ctx.boundary, "_source", forbidden)
    async with ctx.process.database.transaction() as session:
        with pytest.raises(PermissionError, match="bounded canonical"):
            await ctx.boundary.describe(
                session, result_ids=tuple(f"trial-{index:02}" for index in range(count))
            )
