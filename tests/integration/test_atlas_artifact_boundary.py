from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

from padawan.artifacts.information import InformationClass
from padawan.artifacts.store import ArtifactAccessDeniedError
from padawan.atlas.artifacts import AtlasArtifactBoundary
from padawan.atlas.contracts import (
    ExploratoryFailureProposal,
    FailureOrigin,
    TokenAccounting,
    TrialStatus,
)
from padawan.atlas.registry import AtlasRegistry, AtlasRegistryError
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactReferenceRow,
    ArtifactRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    ExternalCallRow,
    VerifierResultRow,
)
from padawan.pprl.content import ProcessContentBoundary, ProcessContentDeniedError
from padawan.pprl.contracts import ProjectStatePayload
from tests.support.atlas_artifacts import (
    _counts,
    _record,
    _rehash,
    _validate,
)
from tests.support.atlas_artifacts import (
    atlas_evidence as atlas_evidence,
)
from tests.support.atlas_registry import (
    NOW,
)


async def test_registry_retains_real_forensic_bytes_under_independent_request_and_result_owners(
    atlas_evidence,
):
    ctx = atlas_evidence
    await _record(ctx)
    await _validate(ctx)
    async with ctx.database.transaction() as session:
        request_pins = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "atlas_trial_request"
                )
            )
        )
        result_pins = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "atlas_trial_result"
                )
            )
        )
        assert len(request_pins) == 2
        assert result_pins == request_pins | {ctx.response.artifact_id, ctx.envelope.artifact_id}
        for artifact_id in result_pins:
            info = await ctx.registry.artifacts.information.get(session, artifact_id=artifact_id)
            assert info.information_class == InformationClass.FORENSIC
            with pytest.raises(ArtifactAccessDeniedError):
                ctx.store.read_bytes(info.artifact)
        before = await _counts(session)
        await ctx.registry.record_trial_request(session, ctx.request)
        await ctx.registry.record_trial_result(session, ctx.result)
        assert await _counts(session) == before


@pytest.mark.parametrize(
    "missing",
    [
        "blob",
        "classification",
        "request_owner",
        "result_owner",
        "extra_owner",
        "call_scope",
        "call_output",
        "request_time",
        "result_time",
        "verifier_identity",
        "catalog_flags",
    ],
)
async def test_prior_result_cannot_be_reused_when_source_integrity_or_ownership_is_lost(
    atlas_evidence, missing
):
    ctx = atlas_evidence
    await _record(ctx)
    async with ctx.database.transaction() as session:
        if missing == "blob":
            ctx.store._path_for_hex(ctx.response.digest[7:]).unlink()
        elif missing == "classification":
            await session.execute(
                delete(ArtifactInformationRow).where(
                    ArtifactInformationRow.artifact_id == ctx.response.artifact_id
                )
            )
        elif missing in {"request_owner", "result_owner"}:
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type
                    == (
                        "atlas_trial_request"
                        if missing == "request_owner"
                        else "atlas_trial_result"
                    )
                )
            )
        elif missing == "extra_owner":
            extra = ctx.store.put_text(
                "unrelated privileged evidence", restricted=True, raw_data=True
            )
            await ctx.catalog.reference(
                session, extra, owner_type="atlas_trial_result", owner_id=ctx.result.result_id
            )
        elif missing == "call_scope":
            await session.execute(
                update(ExternalCallRow)
                .where(ExternalCallRow.request_id == ctx.request.request_id)
                .values(purpose="different-workload")
            )
        elif missing == "call_output":
            await session.execute(
                update(ExternalCallRow)
                .where(ExternalCallRow.request_id == ctx.request.request_id)
                .values(result_output_text_digest=sha256_digest("different evaluated output"))
            )
        elif missing == "request_time":
            await session.execute(
                update(AtlasTrialRequestRow)
                .where(AtlasTrialRequestRow.request_id == ctx.request.request_id)
                .values(created_at=ctx.request.created_at + timedelta(seconds=1))
            )
        elif missing == "result_time":
            await session.execute(
                update(AtlasTrialResultRow)
                .where(AtlasTrialResultRow.result_id == ctx.result.result_id)
                .values(completed_at=ctx.result.completed_at + timedelta(seconds=1))
            )
        elif missing == "verifier_identity":
            await session.execute(
                update(VerifierResultRow)
                .where(VerifierResultRow.result_id == ctx.result.verifier_evidence[0].evidence_id)
                .values(verifier_version="substituted-version")
            )
        else:
            await session.execute(
                update(ArtifactRow)
                .where(ArtifactRow.artifact_id == ctx.response.artifact_id)
                .values(restricted=False)
            )
    with pytest.raises((OSError, RuntimeError, ValueError)):
        await _validate(ctx)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises((OSError, RuntimeError, ValueError)):
            await ctx.registry.record_trial_result(session, ctx.result)
        assert await _counts(session) == before  # no implicit repair/backfill on retry


@pytest.mark.parametrize("status", [TrialStatus.TIMEOUT, TrialStatus.INFRASTRUCTURE_FAILURE])
async def test_failed_trials_retain_requests_and_error_responses_without_inventing_outputs(
    atlas_evidence, status
):
    ctx = atlas_evidence
    failed_request = ctx.store.put_text(
        "captured request before failure", restricted=True, raw_data=True
    )
    error_response = ctx.store.put_text(
        "private provider error response", restricted=True, raw_data=True
    )
    result = _rehash(
        ctx.result,
        status=status,
        success=None,
        score=None,
        generation_provider=None,
        generation_model_id=None,
        generation_protocol=None,
        raw_request_digest=None,
        raw_response_digest=None,
        capabilities_digest=None,
        external_call_artifact=None,
        response_artifact=None,
        response_digest=None,
        verifier_evidence=(),
        primary_authority=None,
        failure_origin=FailureOrigin.INFRASTRUCTURE,
        tokens=TokenAccounting(
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            counting_mode="unavailable",
            missing_reason="provider failed before reporting usage",
        ),
    )
    async with ctx.database.transaction() as session:
        await ctx.catalog.register(session, failed_request)
        await ctx.catalog.register(session, error_response)
        await session.execute(
            update(ExternalCallRow)
            .where(ExternalCallRow.request_id == ctx.request.request_id)
            .values(
                request_artifact_id=failed_request.artifact_id,
                response_artifact_id=None,
                status="failed_retryable",
                error={
                    "classification": (
                        "timeout" if status == TrialStatus.TIMEOUT else "provider_error"
                    ),
                    "response_artifact_id": error_response.artifact_id,
                    "response_digest": error_response.digest,
                },
            )
        )
    await _record(ctx, result=result)
    async with ctx.database.transaction() as session:
        await ctx.registry.validate_trial_artifacts(session, result_id=result.result_id)
        pins = set(
            await session.scalars(
                select(ArtifactReferenceRow.artifact_id).where(
                    ArtifactReferenceRow.owner_type == "atlas_trial_result",
                    ArtifactReferenceRow.owner_id == result.result_id,
                )
            )
        )
        assert failed_request.artifact_id in pins and error_response.artifact_id in pins
        assert ctx.response.artifact_id not in pins and ctx.envelope.artifact_id not in pins
    ctx.store._path_for_hex(error_response.digest[7:]).unlink()
    async with ctx.database.transaction() as session:
        with pytest.raises(OSError):
            await ctx.registry.record_trial_result(session, result)


@pytest.mark.parametrize("surface", ["grader", "verifier"])
async def test_nested_explicit_artifacts_require_protected_forensic_storage(
    atlas_evidence, surface
):
    ctx = atlas_evidence
    exposed = ctx.store.put_text("private grader finding in incorrectly public storage")
    async with ctx.database.transaction() as session:
        await ctx.catalog.register(session, exposed)
    if surface == "grader":
        result = _rehash(ctx.result, grader_artifacts=(exposed,))
    else:
        result = _rehash(
            ctx.result,
            verifier_evidence=(
                ctx.result.verifier_evidence[0].model_copy(update={"artifact_refs": (exposed,)}),
            ),
        )
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(PermissionError, match="protected raw"):
            await ctx.registry.record_trial_result(session, result)
        assert await _counts(session) == before


@pytest.mark.parametrize("operation", ["request_retry", "result"])
async def test_metadata_only_registry_cannot_assert_artifact_retention(atlas_evidence, operation):
    ctx = atlas_evidence
    registry = AtlasRegistry()
    async with ctx.database.transaction() as session:
        with pytest.raises(AtlasRegistryError, match="configured forensic boundary"):
            if operation == "request_retry":
                await registry.record_trial_request(session, ctx.request)
            else:
                await registry.record_trial_result(session, ctx.result)


@pytest.mark.parametrize("failure_point", ["second_pin", "after_record", "cancelled"])
async def test_caught_failures_do_not_commit_partial_forensic_ownership_or_record(
    atlas_evidence, monkeypatch, failure_point
):
    ctx = atlas_evidence
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        reference = ctx.registry.artifacts.catalog.reference
        flush = session.flush
        calls = 0

        async def fail_pin(*args, **kwargs):
            nonlocal calls
            result = await reference(*args, **kwargs)
            calls += 1
            if calls == 2:
                if failure_point == "cancelled":
                    raise asyncio.CancelledError("synthetic cancellation")
                raise RuntimeError("synthetic retention failure")
            return result

        async def fail_flush(*args, **kwargs):
            publishing = any(isinstance(value, AtlasTrialResultRow) for value in session.new)
            await flush(*args, **kwargs)
            if publishing:
                raise RuntimeError("synthetic retention failure")

        with monkeypatch.context() as patch:
            if failure_point == "after_record":
                patch.setattr(session, "flush", fail_flush)
            else:
                patch.setattr(ctx.registry.artifacts.catalog, "reference", fail_pin)
            with pytest.raises(
                asyncio.CancelledError if failure_point == "cancelled" else RuntimeError
            ):
                await ctx.registry.record_trial_result(session, ctx.result)
        assert await _counts(session) == before
    await _record(ctx)


async def test_outer_rollback_and_catalog_gc_preserve_the_declared_retention_contract(
    atlas_evidence,
):
    ctx = atlas_evidence
    with pytest.raises(RuntimeError, match="outer failed"):
        async with ctx.database.transaction() as session:
            await ctx.registry.record_trial_result(session, ctx.result)
            raise RuntimeError("outer failed")
    async with ctx.database.transaction() as session:
        assert await session.get(AtlasTrialResultRow, ctx.result.result_id) is None
    await _record(ctx)
    orphan = ctx.store.put_text("unreferenced orphan")
    async with ctx.database.transaction() as session:
        digests = await ctx.catalog.referenced_digests(session)
    collected = ctx.store.collect_garbage(
        referenced_digests=digests,
        minimum_age=timedelta(days=1),
        now=datetime.now(UTC) + timedelta(days=2),
        dry_run=False,
    )
    assert collected.deleted == (orphan.digest,)
    await _validate(ctx)


async def test_retained_atlas_sources_still_cannot_enter_process_state(atlas_evidence):
    ctx = atlas_evidence
    await _record(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessContentDeniedError):
            await ProcessContentBoundary().check_state(
                session,
                ProjectStatePayload(
                    objective="use the Atlas finding", artifact_refs=(ctx.response,)
                ),
            )
        with pytest.raises(ProcessContentDeniedError):
            await ProcessContentBoundary().check_state(
                session, ProjectStatePayload(objective=f"use {ctx.response.uri}")
            )


async def test_exploratory_trace_retention_is_explicit_atomic_and_not_repaired_on_retry(
    atlas_evidence,
):
    ctx = atlas_evidence
    trace = ctx.store.put_text("consented raw exploratory trace", restricted=True, raw_data=True)
    async with ctx.database.transaction() as session:
        await ctx.catalog.register(session, trace)
    proposal = ExploratoryFailureProposal(
        proposal_id="private-proposal",
        consent_evidence_digest=sha256_digest("declared consent"),
        consent_lane="local-offline-fixture",
        source_trace_digest=trace.digest,
        source_trace_artifact=trace,
        redacted_excerpt_digest=sha256_digest("separately reviewed excerpt"),
        proposed_phenomenon="arithmetic failure",
        proposed_failure_node_ids=("arithmetic",),
        deduplication_key=sha256_digest("private proposal"),
        created_at=NOW + timedelta(seconds=5),
    )
    async with ctx.database.transaction() as session:
        with pytest.raises(AtlasRegistryError, match="configured forensic boundary"):
            await AtlasRegistry().register_exploratory_proposal(session, proposal)
        await ctx.registry.register_exploratory_proposal(session, proposal)
        before = await _counts(session)
        await ctx.registry.register_exploratory_proposal(session, proposal)
        assert await _counts(session) == before
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "atlas_exploratory_proposal"
            )
        )
    async with ctx.database.transaction() as session:
        with pytest.raises(PermissionError, match="retention ownership"):
            await ctx.registry.register_exploratory_proposal(session, proposal)


@pytest.mark.parametrize("bound", ["count", "bytes"])
async def test_reference_bounds_reject_before_backend_reads(atlas_evidence, monkeypatch, bound):
    ctx = atlas_evidence
    boundary = AtlasArtifactBoundary(ctx.catalog)
    refs = (
        (ctx.response,) * 65
        if bound == "count"
        else (ctx.response.model_copy(update={"size_bytes": boundary.maximum_bytes + 1}),)
    )

    async def forbidden_backend(*_args, **_kwargs):
        raise AssertionError("source should have been bounded before any backend read")

    monkeypatch.setattr(boundary.information, "get", forbidden_backend)
    async with ctx.database.transaction() as session:
        with pytest.raises(ValueError, match="bound"):
            await boundary.validate(
                session,
                owner_type="atlas_trial_result",
                owner_id="bounded",
                references=refs,
                recorded_at=NOW,
            )
