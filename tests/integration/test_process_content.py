from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import field_serializer
from sqlalchemy import delete, update

from padawan.artifacts.information import ForensicArtifactRef, InformationClass, ProcessArtifactRef
from padawan.models.contracts import NonEmpty, StrictRecord
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ProcessContentAdmissionRow, ProcessRolloutRow
from padawan.pprl.content import (
    ProcessContentBoundary,
    ProcessContentDeniedError,
    RegisteredProcessSchema,
)
from padawan.pprl.content_contracts import ProcessContentSurface
from padawan.pprl.contracts import (
    HypothesisStatus,
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectClaim,
    ProjectHypothesis,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.store import ProcessStore, _build_event
from tests.pprl_evidence_helpers import _admit, _counts, _prepare_action, _review
from tests.pprl_evidence_helpers import evidence_context as evidence_context


class Observation(StrictRecord):
    condition: NonEmpty
    values: tuple[float, ...]
    digest_under_study: NonEmpty


def _registry(
    *,
    model=Observation,
    namespace="science.observation",
    surface=ProcessContentSurface.STATE_EXTENSION,
):
    return ProcessContentBoundary(
        schemas=(RegisteredProcessSchema(namespace, "1.0.0", surface, model),)
    )


@pytest.mark.parametrize(
    "field", ["extension", "memory", "claim", "hypothesis", "objective", "plan", "risk"]
)
async def test_new_state_blocks_nested_or_unresolved_forensic_content(evidence_context, field):
    ctx = evidence_context
    artifact = ctx.artifacts.put_text("SYNTHETIC_FORENSIC_CONTENT", restricted=True, raw_data=True)
    async with ctx.database.transaction() as session:
        classification = await ctx.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.FORENSIC,
            classified_by="test.broker",
            reason="synthetic forensic record",
            classified_at=ctx.clock(),
        )
        changes = {
            "extension": {
                "extension_state": {"unknown": {"nested": artifact.model_dump(mode="json")}}
            },
            "memory": {"memory_refs": (artifact.artifact_id,)},
            "claim": {
                "claims": (
                    ProjectClaim(
                        claim_id="c1",
                        statement="claim",
                        confidence=0.5,
                        evidence_refs=(artifact.uri,),
                    ),
                )
            },
            "hypothesis": {
                "hypotheses": (
                    ProjectHypothesis(
                        hypothesis_id="h1",
                        statement="hypothesis",
                        status=HypothesisStatus.OPEN,
                        evidence_refs=(classification.digest,),
                    ),
                )
            },
            "objective": {"objective": f"inspect {artifact.artifact_id}"},
            "plan": {"plan": (f"read {artifact.uri}",)},
            "risk": {"unresolved_risks": (artifact.digest.removeprefix("sha256:").upper(),)},
        }[field]
        payload = ProjectStatePayload(objective="public objective").model_copy(update=changes)
        before = await _counts(session)
        with pytest.raises(ProcessContentDeniedError) as failure:
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=payload,
                created_at=ctx.clock(),
            )
        assert str(failure.value) == "process content admission denied"
        assert failure.value.__context__ is None and failure.value.__cause__ is None
        assert await _counts(session) == before


async def test_candidate_digest_without_explicit_admission_is_not_a_memory_channel(
    evidence_context,
):
    ctx = evidence_context
    review = await _review(ctx)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessContentDeniedError):
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective=review.process_reference.content_digest
                ),
                created_at=ctx.clock(),
            )


async def test_declared_scientific_extension_preserves_bytes_and_pins_policy(evidence_context):
    ctx = evidence_context
    boundary = _registry()
    process = ProcessStore(ctx.amber, evidence=ctx.evidence, content=boundary)
    content = {
        "condition": "control",
        "values": [0.25, 0.75],
        "digest_under_study": sha256_digest("unrelated scientific hash; not an artifact lookup"),
    }
    payload = ProjectStatePayload(
        objective="measure a declared observation", extension_state={"science.observation": content}
    )
    encoded = canonical_json_bytes(payload)
    async with ctx.database.transaction() as session:
        rollout = await process.create_rollout(
            session,
            execution_digest=ctx.execution_digest,
            replication_index=1,
            initial_state=payload,
            created_at=ctx.clock(),
        )
        state = await process.get_state(session, state_id=rollout.initial_state_id)
        assert canonical_json_bytes(state.payload) == encoded
        receipt = await session.get(ProcessContentAdmissionRow, ("state", state.state_id))
        assert receipt is not None and receipt.source_digest == state.state_digest
        assert receipt.policy_digest == boundary.policy.digest
        assert (
            receipt.record_json["policy"]["schemas"][0]["json_schema"]
            == Observation.model_json_schema()
        )
        await boundary.verify_state(
            session, state, execution_digest=ctx.execution_digest, now=ctx.clock()
        )
    returned_policy = boundary.policy
    returned_policy.schemas[0].json_schema["additionalProperties"] = True
    assert boundary.policy.schemas[0].json_schema["additionalProperties"] is False


@pytest.mark.parametrize("schema_kind", ["untyped", "mapping", "forensic", "process_ref"])
def test_registry_refuses_open_schemas_and_nested_storage_records(schema_kind):
    class Untyped(StrictRecord):
        value: Any

    class Mapping(StrictRecord):
        values: dict[str, str]

    class Forensic(StrictRecord):
        source: ForensicArtifactRef

    class ProcessReference(StrictRecord):
        source: ProcessArtifactRef

    models = {
        "untyped": Untyped,
        "mapping": Mapping,
        "forensic": Forensic,
        "process_ref": ProcessReference,
    }
    with pytest.raises(ValueError):
        _registry(model=models[schema_kind])


async def test_extension_namespace_is_bound_to_its_declared_surface(evidence_context):
    ctx = evidence_context
    payload = {
        "schema_id": "science.observation",
        "content": {"condition": "control", "values": [0.5], "digest_under_study": "public"},
    }
    event = _build_event(
        event_id="synthetic-schema-event",
        rollout_id="synthetic-schema-rollout",
        sequence=1,
        kind=ProcessEventKind.EVIDENCE_UPDATED,
        actor_id="test.worker",
        lease_token_digest=sha256_digest("lease"),
        parent_state_id="parent",
        resulting_state_id="successor",
        worker_invocation_id=None,
        research_execution_digest=None,
        amber_authorization_digest=ctx.execution.amber_authorization_digest,
        amber_decision_id="synthetic-decision",
        rollout_status=RolloutStatus.ACTIVE,
        payload=payload,
        artifact_refs=(),
        created_at=ctx.clock(),
    )
    async with ctx.database.transaction() as session:
        await _registry(surface=ProcessContentSurface.EVENT).check_event(session, event)
        with pytest.raises(ProcessContentDeniedError):
            await _registry(surface=ProcessContentSurface.STATE_EXTENSION).check_event(
                session, event
            )
        with pytest.raises(ProcessContentDeniedError):
            await _registry(surface=ProcessContentSurface.EVENT).check_intervention(
                session, payload
            )
        await _registry(surface=ProcessContentSurface.FORK_INTERVENTION).check_intervention(
            session, payload
        )
        changed = event.model_copy(
            update={
                "payload": {
                    "schema_id": payload["schema_id"],
                    "content": {**payload["content"], "telemetry": "SYNTHETIC_PRIVATE"},
                }
            }
        )
        with pytest.raises(ProcessContentDeniedError):
            await _registry(surface=ProcessContentSurface.EVENT).check_event(session, changed)


async def test_extension_serializer_cannot_add_a_hidden_channel(evidence_context):
    ctx = evidence_context

    class SurprisingSerialization(StrictRecord):
        value: str

        @field_serializer("value")
        def add_unrequested_text(self, value: str) -> str:
            return value + " SYNTHETIC_PRIVATE_SERIALIZER"

    boundary = _registry(model=SurprisingSerialization)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessContentDeniedError) as failure:
            await boundary.check_state(
                session,
                ProjectStatePayload(
                    objective="preserve supplied bytes",
                    extension_state={"science.observation": {"value": "public"}},
                ),
            )
        assert "SYNTHETIC_" not in str(failure.value)
        assert failure.value.__context__ is None and failure.value.__cause__ is None


async def test_forensic_digest_in_a_registered_extension_key_is_denied(evidence_context):
    ctx = evidence_context
    artifact = ctx.artifacts.put_text("SYNTHETIC_KEY_SOURCE", restricted=True, raw_data=True)
    async with ctx.database.transaction() as session:
        classification = await ctx.information.classify(
            session,
            artifact=artifact,
            information_class=InformationClass.FORENSIC,
            classified_by="test.broker",
            reason="synthetic namespace probe",
            classified_at=ctx.clock(),
        )
    boundary = _registry(namespace=classification.digest)
    process = ProcessStore(ctx.amber, evidence=ctx.evidence, content=boundary)
    async with ctx.database.transaction() as session:
        with pytest.raises(ProcessContentDeniedError):
            await process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=ProjectStatePayload(
                    objective="no private namespace",
                    extension_state={
                        classification.digest: {
                            "condition": "control",
                            "values": [0.5],
                            "digest_under_study": "public",
                        }
                    },
                ),
                created_at=ctx.clock(),
            )


async def test_memory_and_evidence_links_resolve_only_through_top_level_admitted_refs(
    evidence_context,
):
    ctx = evidence_context
    review = await _admit(ctx)
    reference = review.process_reference
    async with ctx.database.transaction() as session:
        rollout = await ctx.process.create_rollout(
            session,
            execution_digest=ctx.execution_digest,
            replication_index=1,
            initial_state=ProjectStatePayload(
                objective="use reviewed evidence",
                artifact_refs=(reference,),
                memory_refs=(reference.process_artifact_id,),
                claims=(
                    ProjectClaim(
                        claim_id="c1",
                        statement="an admitted finding",
                        confidence=0.5,
                        evidence_refs=(reference.process_artifact_id,),
                    ),
                ),
                budget_usage=ProjectBudgetUsage(artifact_bytes=reference.size_bytes),
            ),
            created_at=ctx.clock(),
        )
        state = await ctx.process.get_state(session, state_id=rollout.initial_state_id)
        assert state.payload.memory_refs == (reference.process_artifact_id,)


@pytest.mark.parametrize(
    "fault", ["missing", "corrupted", "wrong_scope", "policy_drift", "changed_source"]
)
async def test_bad_receipt_denies_claim_without_changing_lease_or_privileged_history(
    evidence_context, fault
):
    ctx = evidence_context
    async with ctx.database.transaction() as session:
        rollout = await ctx.process.get_rollout(session, rollout_id="evidence-rollout")
        state = await ctx.process.get_state(session, state_id=rollout.initial_state_id)
        identity = ("state", state.state_id)
        receipt = await session.get(ProcessContentAdmissionRow, identity)
        assert receipt is not None
        if fault == "missing":
            await session.execute(
                delete(ProcessContentAdmissionRow).where(
                    ProcessContentAdmissionRow.record_id == state.state_id
                )
            )
        elif fault == "corrupted":
            corrupted = {**receipt.record_json, "SYNTHETIC_PRIVATE_RECEIPT": "private value"}
            await session.execute(
                update(ProcessContentAdmissionRow)
                .where(ProcessContentAdmissionRow.record_id == state.state_id)
                .values(record_json=corrupted)
            )
        elif fault == "wrong_scope":
            await session.execute(
                update(ProcessContentAdmissionRow)
                .where(ProcessContentAdmissionRow.record_id == state.state_id)
                .values(source_digest=sha256_digest("other source"))
            )
    if fault == "policy_drift":
        policy = ctx.process.content.policy.model_copy(update={"version": "different-version"})
        ctx.process = ProcessStore(
            ctx.amber, evidence=ctx.evidence, content=ProcessContentBoundary(policy=policy)
        )
    async with ctx.database.transaction() as session:
        if fault == "changed_source":
            changed = state.model_copy(
                update={"payload": ProjectStatePayload(objective="uncommitted different content")}
            )
            with pytest.raises(ProcessContentDeniedError):
                await ctx.process.content.verify_state(
                    session, changed, execution_digest=ctx.execution_digest, now=ctx.clock()
                )
            return
        before = await _counts(session)
        with pytest.raises(ProcessContentDeniedError) as failure:
            await ctx.process.claim_next(
                session, worker_id="replacement", lease_for=timedelta(minutes=5), now=ctx.clock()
            )
        assert "SYNTHETIC_" not in str(failure.value)
        assert failure.value.__context__ is None and failure.value.__cause__ is None
        assert await _counts(session) == before
        initial, events = await ctx.process.replay(session, rollout_id=rollout.rollout_id)
        assert initial == state and not events
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, rollout.rollout_id)
        assert row is not None and row.lease_token is None


@pytest.mark.parametrize("fault", ["large_string", "deep", "cycle", "unknown_field"])
async def test_content_limits_fail_before_mutation(evidence_context, fault):
    ctx = evidence_context
    payload = ProjectStatePayload(objective="bounded content")
    if fault == "large_string":
        payload = payload.model_copy(
            update={"objective": "x" * (ctx.process.content.policy.maximum_string_bytes + 1)}
        )
    elif fault == "unknown_field":
        payload = payload.model_copy(update={"SYNTHETIC_PRIVATE_FIELD": "not a declared field"})
    else:
        nested = {}
        if fault == "cycle":
            nested["cycle"] = nested
        else:
            for _ in range(ctx.process.content.policy.maximum_depth + 1):
                nested = {"nested": nested}
        payload.extension_state["unknown"] = nested
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(ProcessContentDeniedError):
            await ctx.process.create_rollout(
                session,
                execution_digest=ctx.execution_digest,
                replication_index=1,
                initial_state=payload,
                created_at=ctx.clock(),
            )
        assert await _counts(session) == before


async def test_unknown_event_fields_and_partial_receipt_failure_roll_back(
    evidence_context, monkeypatch
):
    ctx = evidence_context
    claimed, decision, usage = await _prepare_action(ctx)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(ProcessContentDeniedError):
            await ctx.process.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.ARTIFACT_ADMITTED,
                actor_id="test.worker",
                payload={"raw_response": {"private": "SYNTHETIC_RAW"}},
                resulting_state=claimed.state.payload.model_copy(update={"budget_usage": usage}),
                occurred_at=ctx.clock(),
            )
        assert await _counts(session) == before

    async def fail_event_receipt(*args, **kwargs):
        raise OSError("synthetic receipt persistence failure")

    monkeypatch.setattr(ctx.process.content, "admit_event", fail_event_receipt)
    async with ctx.database.transaction() as session:
        before = await _counts(session)
        with pytest.raises(OSError, match="receipt persistence failure"):
            await ctx.process.append_event(
                session,
                rollout_id=claimed.rollout.rollout_id,
                lease_token=claimed.lease_token,
                amber_decision_id=decision.decision_id,
                kind=ProcessEventKind.ARTIFACT_ADMITTED,
                actor_id="test.worker",
                payload={"summary": "public action"},
                resulting_state=claimed.state.payload.model_copy(update={"budget_usage": usage}),
                occurred_at=ctx.clock(),
            )
        assert await _counts(session) == before
    async with ctx.database.transaction() as session:
        row = await session.get(ProcessRolloutRow, claimed.rollout.rollout_id)
        assert row is not None and row.sequence == 0 and row.lease_token == claimed.lease_token
