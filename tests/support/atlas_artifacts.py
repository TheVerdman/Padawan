"""Shared atlas artifacts fixture setup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import func, select

from padawan.artifacts.store import ArtifactCatalog
from padawan.atlas.contracts import (
    AtlasTrialResult,
    content_id,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactInformationRow,
    ArtifactReferenceRow,
    AtlasExploratoryProposalRow,
    AtlasTrialRequestRow,
    AtlasTrialResultRow,
    ExternalCallRow,
    VerifierResultRow,
)
from tests.support.atlas_registry import (
    _artifact_store,
    _register_executable_chain,
    _response_artifact,
    _trial_result,
    _verifier_record,
)


@pytest_asyncio.fixture
async def atlas_evidence(database):
    return await _atlas_evidence(database)


async def _atlas_evidence(database, *, store=None, governance=None):
    async with database.transaction() as session:
        store = store or _artifact_store(session)
        registry, execution, campaign, suite, item, request, run = await _register_executable_chain(
            session, artifacts=store, governance=governance
        )
        await registry.record_trial_request(session, request)
        catalog = ArtifactCatalog(store)
        response = store.put_text("4", media_type="text/plain", restricted=True, raw_data=True)
        assert response == _response_artifact()
        envelope = store.put_text(
            "registry envelope",
            media_type="application/vnd.padawan.generation-result+json",
            restricted=True,
            raw_data=True,
        )
        await catalog.register(session, response)
        await catalog.register(session, envelope)
        verifier = _verifier_record()
        session.add(
            VerifierResultRow(
                result_id=verifier.result_id,
                verifier_id=verifier.verifier_id,
                verifier_version=verifier.verifier_version,
                scope=verifier.scope,
                disposition=verifier.disposition.value,
                deterministic=verifier.deterministic,
                record_digest=sha256_digest(verifier),
                record_json=verifier.model_dump(mode="json"),
                created_at=verifier.created_at,
            )
        )
        result = _trial_result(execution, request)
        session.add(
            ExternalCallRow(
                request_id=request.request_id,
                run_id=request.run_id,
                purpose="capability_atlas",
                provider="test-provider",
                request_hash=request.wire_request_digest,
                request_artifact_id=response.artifact_id,
                response_artifact_id=envelope.artifact_id,
                provider_response_id="response-test",
                status="completed",
                error=None,
                result_envelope_digest=envelope.digest,
                result_model_id=execution.student_model.model_id,
                result_protocol=execution.student_model.protocol,
                result_raw_request_digest=result.raw_request_digest,
                result_raw_response_digest=response.digest,
                result_output_text_digest=sha256_digest("registry output"),
                result_usage={"input_tokens": 4, "output_tokens": 1, "total_tokens": 5},
                result_capabilities_digest=result.capabilities_digest,
                result_latency_ms=1,
                created_at=request.created_at,
                completed_at=result.completed_at,
            )
        )
    return SimpleNamespace(
        database=database,
        registry=registry,
        execution=execution,
        campaign=campaign,
        suite=suite,
        item=item,
        request=request,
        run=run,
        result=result,
        store=store,
        catalog=catalog,
        response=response,
        envelope=envelope,
    )


async def _record(ctx, *, result=None, registry=None):
    async with ctx.database.transaction() as session:
        return await (registry or ctx.registry).record_trial_result(session, result or ctx.result)


async def _validate(ctx):
    async with ctx.database.transaction() as session:
        await ctx.registry.validate_trial_artifacts(session, result_id=ctx.result.result_id)


async def _counts(session):
    return tuple(
        [
            await session.scalar(select(func.count()).select_from(table))
            for table in (
                ArtifactInformationRow,
                ArtifactReferenceRow,
                AtlasTrialRequestRow,
                AtlasTrialResultRow,
                AtlasExploratoryProposalRow,
            )
        ]
    )


def _rehash(result, **changes):
    draft = result.model_copy(update=changes)
    identity = draft.model_dump(mode="json", exclude={"result_id", "result_digest", "completed_at"})
    return AtlasTrialResult.model_validate(
        {
            **draft.model_dump(mode="python"),
            "result_id": content_id("atlas-result", identity),
            "result_digest": sha256_digest(identity),
        }
    )
