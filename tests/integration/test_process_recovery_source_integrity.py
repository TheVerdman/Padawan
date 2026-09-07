"""Self-consistent outer digests cannot substitute for native source provenance."""

import base64
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, update

from padawan.artifacts.information import ArtifactInformationStore, InformationClass
from padawan.artifacts.store import artifact_read_bytes
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ExternalCallRow, ProcessRecoveryRow
from padawan.pprl.recovery import ProcessRecoveryStore
from padawan.pprl.recovery_evidence import RecoveryEvidenceReader
from tests.container_helpers import container_context
from tests.pprl_evidence_helpers import evidence_context as evidence_context
from tests.support.process_effects import model_context
from tests.support.process_generation import _ready
from tests.support.process_recovery import expire, recover, recovery, snapshot


async def replace_effect(ctx, receipt, effect, *, match_pins=False):
    changed = receipt.model_copy(update={"effects": (effect,)})
    async with ctx.database.transaction() as session:
        # Deliberately bypass immutable ORM listeners to emulate a malformed retained
        # record/import. This does not give workers SQL or forensic write authority.
        await session.execute(
            update(ProcessRecoveryRow)
            .where(ProcessRecoveryRow.recovery_id == receipt.request.recovery_id)
            .values(record_json=changed.model_dump(mode="json"), record_digest=changed.digest)
        )
        if match_pins:
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_recovery_receipt",
                    ArtifactReferenceRow.owner_id == receipt.request.recovery_id,
                    ArtifactReferenceRow.artifact_id.not_in(
                        [s.reference.artifact.artifact_id for s in effect.sources]
                    ),
                )
            )
    return changed


@pytest.mark.parametrize(
    "damage",
    ["prepared", "request", "response", "result_digest", "completed_status", "phase_event"],
)
@pytest.mark.parametrize("match_pins", [False, True])
async def test_model_recovery_read_requires_exact_retained_source_joins(
    evidence_context, damage, match_pins
):
    ready = await _ready(evidence_context)
    ctx = await model_context(ready)
    await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    if damage == "phase_event":
        async with ctx.database.transaction() as session:
            prior = next(
                event.digest
                for event in await ctx.amber.resources.replay(session, ctx.authorization.digest)
                if event.subject_id == effect.decision_id and event.kind == "start"
            )
        assert prior is not None
        effect = effect.model_copy(update={"after_resource_event_digest": prior})
    elif damage in {"result_digest", "completed_status"}:
        effect = effect.model_copy(
            update={
                "result_digest" if damage == "result_digest" else "observed_status": (
                    sha256_digest("substituted result") if damage == "result_digest" else "failed"
                )
            }
        )
    else:
        omitted = {
            "prepared": "process_generation_workload",
            "request": "external_call_request",
            "response": "external_call_response",
        }[damage]
        effect = effect.model_copy(
            update={"sources": tuple(s for s in effect.sources if s.owner_type != omitted)}
        )
    await replace_effect(ctx, receipt, effect, match_pins=match_pins)
    before = await snapshot(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises((PermissionError, ValueError)):
            await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id)
    assert await snapshot(ctx) == before
    assert len(ready.client.calls) == 1


@pytest.mark.parametrize("omitted", ["input", "capture", "result", "completed_status"])
@pytest.mark.parametrize("match_pins", [False, True])
async def test_container_recovery_read_requires_exact_retained_source_joins(
    database, tmp_path, pprl_now, omitted, match_pins
):
    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    await ctx.service.execute(**ctx.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    if omitted == "completed_status":
        effect = effect.model_copy(update={"observed_status": "unknown"})
    else:
        owner_type = {
            "input": "process_container_workload",
            "capture": "process_container_capture",
            "result": "process_container_result",
        }[omitted]
        index = next(
            i for i, source in enumerate(effect.sources) if source.owner_type == owner_type
        )
        effect = effect.model_copy(
            update={"sources": effect.sources[:index] + effect.sources[index + 1 :]}
        )
    await replace_effect(ctx, receipt, effect, match_pins=match_pins)
    before = await snapshot(ctx)
    async with database.transaction() as session:
        with pytest.raises((PermissionError, ValueError)):
            await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id)
    assert await snapshot(ctx) == before
    assert len(ctx.driver.calls) == 1


@pytest.mark.parametrize(
    "field",
    ["request_id", "provider", "model_id", "protocol", "raw_request_base64", "output_text"],
)
async def test_retained_model_result_cannot_substitute_native_identity_or_settled_bytes(
    evidence_context, field
):
    ready = await _ready(evidence_context)
    ctx = await model_context(ready)
    await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    original = next(s for s in effect.sources if s.owner_type == "external_call_response")
    catalog = ctx.records.catalog
    data = json.loads(
        await artifact_read_bytes(
            catalog.backend, original.reference.artifact, allow_restricted=True
        )
    )
    data[field] = (
        base64.b64encode(b"substituted prepared body").decode()
        if field == "raw_request_base64"
        else "substituted result value"
    )
    artifact = catalog.backend.put_bytes(
        canonical_json_bytes(data), media_type="application/json", raw_data=True, restricted=True
    )
    async with ctx.database.transaction() as session:
        information = ArtifactInformationStore(catalog)
        await information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.FORENSIC,
            classified_by="test.source-corruption",
            reason="synthetic probe",
            classified_at=ctx.clock(),
        )
        reference = await information.forensic_reference(session, artifact_id=artifact.artifact_id)
        source = original.model_copy(update={"reference": reference})
        for owner_type, owner_id in (
            (source.owner_type, source.owner_id),
            ("process_recovery_receipt", receipt.request.recovery_id),
        ):
            await catalog.reference(session, artifact, owner_type=owner_type, owner_id=owner_id)
    effect = effect.model_copy(
        update={
            "sources": tuple(source if s == original else s for s in effect.sources),
            "result_digest": artifact.digest,
        }
    )
    await replace_effect(ctx, receipt, effect, match_pins=True)
    before = await snapshot(ctx)
    async with ctx.database.transaction() as session:
        expected = "settlement sources" if field == "output_text" else "exact admitted request"
        with pytest.raises(ValueError, match=expected):
            await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id)
    assert await snapshot(ctx) == before
    assert len(ready.client.calls) == 1


async def test_completed_snapshot_remains_historical_and_requires_independent_settlement_pins(
    evidence_context,
):
    ready = await _ready(evidence_context)
    ctx = await model_context(ready)
    await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    receipt = await recover(ctx)
    effect = receipt.effects[0]
    async with ctx.database.transaction() as session:
        # Native mutable call fields do not rewrite the original completed snapshot.
        await session.execute(
            update(ExternalCallRow)
            .where(ExternalCallRow.request_id == ready.request.request_id)
            .values(status="failed_terminal")
        )
    before = await snapshot(ctx)
    async with ctx.database.transaction() as session:
        assert await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id) == receipt
    assert await snapshot(ctx) == before
    async with ctx.database.transaction() as session:
        await session.execute(
            delete(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == "process_resource_event",
                ArtifactReferenceRow.owner_id == effect.after_resource_event_digest,
            )
        )
    async with ctx.database.transaction() as session:
        with pytest.raises((PermissionError, ValueError)):
            await recovery(ctx).read(session, recovery_id=receipt.request.recovery_id)
    assert await snapshot(ctx) == before
    assert len(ready.client.calls) == 1


async def test_initial_recovery_cannot_publish_a_snapshot_its_reader_would_reject(
    evidence_context, monkeypatch
):
    from dataclasses import replace

    ready = await _ready(evidence_context)
    ctx = await model_context(ready)
    await ready.service.execute(**ready.kwargs)
    await expire(ctx)
    original = RecoveryEvidenceReader.inspect

    async def omit_source(self, *args, **kwargs):
        source = await original(self, *args, **kwargs)
        return replace(source, sources=source.sources[1:])

    monkeypatch.setattr(RecoveryEvidenceReader, "inspect", omit_source)
    before = await snapshot(ctx)
    with pytest.raises(ValueError, match="inconsistent native sources"):
        await recover(ctx)
    assert await snapshot(ctx) == before
    assert len(ready.client.calls) == 1


async def test_recovery_rechecks_expiry_after_final_historical_validation(
    database, tmp_path, pprl_now, monkeypatch
):
    from padawan.pprl import recovery as recovery_module

    ctx = await container_context(database, tmp_path, pprl_now, enrolled=True)
    await expire(ctx)
    original = ProcessRecoveryStore.read

    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return ctx.authorization.expires_at + timedelta(microseconds=1)

    async def expire_during_read(self, *args, **kwargs):
        receipt = await original(self, *args, **kwargs)
        monkeypatch.setattr(recovery_module, "datetime", ExpiredClock)
        return receipt

    monkeypatch.setattr(ProcessRecoveryStore, "read", expire_during_read)
    before = await snapshot(ctx)
    with pytest.raises(PermissionError, match="expired during final source validation"):
        await recover(ctx)
    assert await snapshot(ctx) == before
    assert ctx.driver.calls == []
