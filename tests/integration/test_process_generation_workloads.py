from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select, update

from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.artifacts.information import InformationClass
from padawan.artifacts.store import artifact_read_bytes
from padawan.governance.amber import AmberStatus
from padawan.models.contracts import SamplingConfiguration, unreviewed_provider_output_rights
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ArtifactRow,
    ExternalCallRow,
    ProcessGenerationWorkloadRow,
    ProcessObservationDecisionRow,
    ProcessWorkerInvocationRow,
)
from padawan.orchestration import external_calls
from padawan.pprl import generation as generation_module
from padawan.pprl.contracts import ProjectSplit
from padawan.pprl.generation import ProcessGenerationExecutor, ProcessGenerationUnavailableError
from padawan.pprl.generation_boundary import ProcessGenerationBoundary
from padawan.pprl.generation_contracts import ProcessGenerationWorkload
from padawan.pprl.store import ProcessInvariantError, _invocation_forensic_bytes
from tests.helpers import CallbackGenerationClient
from tests.pprl_evidence_helpers import evidence_context as evidence_context
from tests.pprl_generation_helpers import generation_boundary
from tests.support.process_generation import (
    _no_intent,
    _ready,
    _receipt,
)


async def test_generation_receipt_binds_exact_inputs_before_io_and_survives_new_broker(
    evidence_context,
):
    ready = await _ready(evidence_context)
    output = await ready.service.execute(**ready.kwargs)
    receipt = await _receipt(ready)
    async with ready.ctx.database.transaction() as session:
        original_completed_at = (
            await session.get(ProcessWorkerInvocationRow, receipt.invocation_id)
        ).completed_at
    assert receipt.request_json == canonical_json_bytes(ready.request).decode()
    assert ready.client.calls[0].input == ready.observed.observation_bytes.decode()
    assert receipt.prepared.body_digest == sha256_digest(
        ready.client.prepare_generation(ready.request).body_json.encode()
    )
    assert await artifact_read_bytes(
        ready.ctx.artifacts, receipt.prepared_artifact.artifact, allow_restricted=True
    ) == canonical_json_bytes(receipt.prepared)
    public = canonical_json_bytes(output.output)
    assert receipt.request_id.encode() not in public
    assert receipt.policy.policy_id.encode() not in public
    assert receipt.prepared_artifact.artifact.artifact_id.encode() not in public
    fresh = ProcessGenerationExecutor(
        database=ready.ctx.database,
        executor=ready.external,
        boundary=generation_boundary(ready.ctx.process, ready.external),
    )
    assert (await fresh.execute(**ready.kwargs)).output == output.output
    assert len(ready.client.calls) == 1
    async with ready.ctx.database.transaction() as session:
        assert (
            await session.get(ProcessWorkerInvocationRow, receipt.invocation_id)
        ).completed_at == original_completed_at
        owners = set(
            await session.scalars(
                select(ArtifactReferenceRow.owner_type).where(
                    ArtifactReferenceRow.artifact_id
                    == receipt.prepared_artifact.artifact.artifact_id
                )
            )
        )
        assert {"process_generation_workload", "process_worker_invocation"} <= owners


@pytest.mark.parametrize(
    "field,value",
    [
        ("instructions", "substituted instructions"),
        ("input", "unobserved input"),
        ("metadata", {"ambient": "forensic"}),
        ("json_schema", {"private": "schema"}),
        ("schema_name", "unadmitted-schema"),
        ("tool_choice", "auto"),
        ("sampling", SamplingConfiguration(max_output_tokens=12, temperature=0.5)),
        ("previous_response_id", "private-response"),
        ("store", True),
        ("tools", ({"type": "function", "name": "unadmitted"},)),
    ],
)
async def test_substituted_model_inputs_cannot_dispatch_or_publish_intent(
    evidence_context, field, value
):
    ready = await _ready(evidence_context)
    with pytest.raises(ProcessGenerationUnavailableError) as failure:
        await ready.service.execute(
            **{**ready.kwargs, "request": ready.request.model_copy(update={field: value})}
        )
    assert failure.value.__context__ is None
    await _no_intent(ready)


@pytest.mark.parametrize("missing", ["boundary", "binding"])
async def test_missing_workload_composition_or_observation_denies(evidence_context, missing):
    ready = await _ready(evidence_context, bind=missing != "binding")
    if missing == "boundary":
        ready.service = ProcessGenerationExecutor(
            database=ready.ctx.database, executor=ready.external
        )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    await _no_intent(ready)


async def test_caller_mutation_after_admission_starts_cannot_change_dispatch(
    evidence_context, monkeypatch
):
    ready = await _ready(evidence_context)
    entered, release = asyncio.Event(), asyncio.Event()
    original = ready.boundary.admit

    async def hold(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(ready.boundary, "admit", hold)
    task = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        ready.request.metadata["ambient"] = "must not reach the provider"
    finally:
        release.set()
    await asyncio.wait_for(task, 5)
    assert ready.client.calls[0].metadata == {}


@pytest.mark.parametrize("changed", ["provider", "model", "destination", "policy", "reviewer"])
async def test_configured_authority_substitution_denies_before_io(
    evidence_context, monkeypatch, changed
):
    ready = await _ready(evidence_context)
    if changed == "provider":
        ready.client.provider = "other-provider"
    elif changed in {"model", "destination"}:
        original = ready.client.prepare_generation

        def altered(request):
            updates = (
                {"model_id": "other-model"}
                if changed == "model"
                else {"destination": "https://other.invalid/v1/responses", "transport": "http"}
            )
            return original(request).model_copy(update=updates)

        monkeypatch.setattr(ready.client, "prepare_generation", altered)
    else:
        updates = (
            {"worker_model_digest": sha256_digest("other-model")}
            if changed == "policy"
            else {"reviewed_by": "unlisted-reviewer"}
        )
        ready.service.boundary = ProcessGenerationBoundary(
            observations=ready.boundary.observations,
            catalog=ready.boundary.catalog,
            client=ready.client,
            policy=ready.boundary.policy.model_copy(update=updates),
        )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    await _no_intent(ready)


@pytest.mark.parametrize("violation", ["forensic_id", "rights", "request_bytes", "prepared_bytes"])
async def test_fixed_policy_inputs_require_rights_information_boundary_and_bounds(
    evidence_context, violation
):
    ready = await _ready(evidence_context)
    if violation == "forensic_id":
        artifact = ready.ctx.artifacts.put_text(
            "private synthetic input", restricted=True, raw_data=True
        )
        async with ready.ctx.database.transaction() as session:
            await ready.ctx.information.classify(
                session,
                artifact=artifact,
                information_class=InformationClass.FORENSIC,
                classified_by="synthetic-reviewer",
                reason="fixture",
                classified_at=ready.ctx.clock(),
            )
        updates = {"instructions": artifact.artifact_id}
    elif violation == "rights":
        updates = {"instructions_rights": unreviewed_provider_output_rights(provider="fixture")}
    else:
        updates = {
            "maximum_request_bytes" if violation == "request_bytes" else "maximum_prepared_bytes": 1
        }
    boundary = ProcessGenerationBoundary(
        observations=ready.boundary.observations,
        catalog=ready.boundary.catalog,
        client=ready.client,
        policy=ready.boundary.policy.model_copy(update=updates),
    )
    ready.service.boundary = boundary
    ready.kwargs["request"] = boundary.request(
        request_id=ready.request.request_id, observation=ready.observed.observation
    )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    await _no_intent(ready)


async def test_second_delivery_cannot_resend_while_first_effect_is_pending(evidence_context):
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldClient(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            entered.set()
            await release.wait()
            return await super().generate_prepared(request, prepared)

    client = HeldClient(lambda request: "candidate", "test-open-weight")
    ready = await _ready(evidence_context, client=client)
    first = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(ProcessGenerationUnavailableError):
            await ready.service.execute(**ready.kwargs)
    finally:
        release.set()
    await asyncio.wait_for(first, 5)
    assert len(client.calls) == 1


async def test_pause_after_intent_prevents_provider_call(evidence_context, monkeypatch):
    ready = await _ready(evidence_context)
    original = ready.external.execute

    async def pause_then_execute(**kwargs):
        async with ready.ctx.database.transaction() as session:
            await ready.ctx.amber.transition(
                session,
                authorization_digest=ready.ctx.execution.amber_authorization_digest,
                to_status=AmberStatus.PAUSED,
                actor_id="reviewer-a",
                reason="synthetic pause",
                evidence_refs=("review:test",),
                occurred_at=ready.ctx.clock(),
            )
        return await original(**kwargs)

    monkeypatch.setattr(ready.external, "execute", pause_then_execute)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert ready.client.calls == []
    assert await _receipt(ready)


async def test_pause_during_provider_call_retains_response_but_withholds_output(evidence_context):
    entered, release = asyncio.Event(), asyncio.Event()

    class HeldClient(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            entered.set()
            await release.wait()
            return await super().generate_prepared(request, prepared)

    ready = await _ready(
        evidence_context, client=HeldClient(lambda request: "candidate", "test-open-weight")
    )
    task = asyncio.create_task(ready.service.execute(**ready.kwargs))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with ready.ctx.database.transaction() as session:
            await ready.ctx.amber.transition(
                session,
                authorization_digest=ready.ctx.execution.amber_authorization_digest,
                to_status=AmberStatus.PAUSED,
                actor_id="reviewer-a",
                reason="synthetic pause while provider is in flight",
                evidence_refs=("review:test",),
                occurred_at=ready.ctx.clock(),
            )
    finally:
        release.set()
    with pytest.raises(ProcessGenerationUnavailableError):
        await asyncio.wait_for(task, 5)
    async with ready.ctx.database.transaction() as session:
        call = await session.get(ExternalCallRow, ready.request.request_id)
        invocation = await session.get(ProcessWorkerInvocationRow, ready.kwargs["invocation_id"])
        assert call.status == "completed" and call.response_artifact_id is not None
        assert invocation.status == "failed"
        assert invocation.error["external_effect"] == "completed"
    assert len(ready.client.calls) == 1


@pytest.mark.parametrize("expire_at", ["after_intent", "before_dispatch", "after_provider"])
async def test_action_deadline_is_not_restarted_by_admission_work(
    evidence_context, monkeypatch, expire_at
):
    ready = await _ready(evidence_context)

    def expire():
        monkeypatch.setattr(
            generation_module,
            "datetime",
            SimpleNamespace(now=lambda _tz: ready.action.requested_at + timedelta(seconds=31)),
        )

    target, method = (
        (ready.client, "generate_prepared")
        if expire_at == "after_provider"
        else (ready.boundary, "validate_current" if expire_at == "before_dispatch" else "admit")
    )
    original = getattr(target, method)

    async def expire_after(*args, **kwargs):
        result = await original(*args, **kwargs)
        expire()
        return result

    monkeypatch.setattr(target, method, expire_after)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert len(ready.client.calls) == (1 if expire_at == "after_provider" else 0)
    assert await _receipt(ready)


async def test_event_byte_accounting_requires_the_prepared_workload_source(evidence_context):
    ready = await _ready(evidence_context)
    await ready.service.execute(**ready.kwargs)
    receipt = await _receipt(ready)
    async with ready.ctx.database.transaction() as session:
        invocation = await session.get(ProcessWorkerInvocationRow, receipt.invocation_id)
        original_bytes = sum(
            [
                (await session.get(ArtifactRow, artifact_id)).size_bytes
                for artifact_id in (invocation.request_artifact_id, invocation.response_artifact_id)
            ]
        )
        assert await _invocation_forensic_bytes(session, invocation, ready.ctx.clock()) == (
            original_bytes + receipt.prepared_artifact.artifact.size_bytes
        )
        await session.execute(delete(ProcessGenerationWorkloadRow))
        with pytest.raises(ProcessInvariantError, match="legacy fallback is forbidden"):
            await _invocation_forensic_bytes(session, invocation, ready.ctx.clock())


async def test_unknown_effect_cannot_be_redispatched_after_response_storage_failure(
    evidence_context, monkeypatch
):
    ready = await _ready(evidence_context)

    async def fail(*args, **kwargs):
        raise OSError("synthetic response persistence failure")

    monkeypatch.setattr(external_calls, "artifact_put_bytes", fail)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert len(ready.client.calls) == 1
    async with ready.ctx.database.transaction() as session:
        row = await session.get(ExternalCallRow, ready.request.request_id)
        assert row.status == "pending" and row.response_artifact_id is None


@pytest.mark.parametrize(
    "damage", ["pin", "invocation_pin", "receipt", "binding", "missing", "blob"]
)
async def test_cached_generation_does_not_repair_damaged_workload_lineage(evidence_context, damage):
    ready = await _ready(evidence_context)
    await ready.service.execute(**ready.kwargs)
    async with ready.ctx.database.transaction() as session:
        if damage == "pin":
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_generation_workload"
                )
            )
        elif damage == "invocation_pin":
            await session.execute(
                delete(ArtifactReferenceRow).where(
                    ArtifactReferenceRow.owner_type == "process_worker_invocation"
                )
            )
        elif damage == "missing":
            await session.execute(delete(ProcessGenerationWorkloadRow))
        elif damage == "blob":
            row = await session.get(ProcessGenerationWorkloadRow, ready.kwargs["invocation_id"])
            record = ProcessGenerationWorkload.model_validate(row.record_json, strict=False)
            ready.ctx.artifacts._path_for_hex(record.prepared_artifact.artifact.digest[7:]).unlink()
        elif damage == "binding":
            await session.execute(delete(ProcessObservationDecisionRow))
        else:
            row = await session.get(ProcessGenerationWorkloadRow, ready.kwargs["invocation_id"])
            payload = {**row.record_json, "worker_id": "substituted-worker"}
            await session.execute(
                update(ProcessGenerationWorkloadRow).values(
                    record_json=payload, record_digest=sha256_digest(payload)
                )
            )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert len(ready.client.calls) == 1


async def test_workload_policy_change_does_not_reinterpret_a_completed_invocation(evidence_context):
    ready = await _ready(evidence_context)
    await ready.service.execute(**ready.kwargs)
    ready.service.boundary = ProcessGenerationBoundary(
        observations=ready.boundary.observations,
        catalog=ready.boundary.catalog,
        client=ready.client,
        policy=ready.boundary.policy.model_copy(update={"version": "2.0.0"}),
    )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert len(ready.client.calls) == 1


async def test_workload_receipt_is_immutable_and_outer_rollback_does_not_publish(evidence_context):
    ready = await _ready(evidence_context)
    with pytest.raises(RuntimeError, match="outer rollback"):
        async with ready.ctx.database.transaction() as session:
            session.add(
                ProcessWorkerInvocationRow(
                    invocation_id=ready.kwargs["invocation_id"],
                    request_id=ready.request.request_id,
                    rollout_id=ready.kwargs["rollout_id"],
                    role_id="researcher",
                    worker_model_digest=ready.kwargs["worker_model_digest"],
                    amber_decision_id=ready.decision.decision_id,
                    research_execution_digest=None,
                    status="planned",
                    usage={},
                    error=None,
                    request_artifact_id=None,
                    response_artifact_id=None,
                    created_at=ready.ctx.clock(),
                )
            )
            await session.flush()
            await ready.boundary.admit(
                session,
                invocation_id=ready.kwargs["invocation_id"],
                rollout_id=ready.kwargs["rollout_id"],
                lease_token=ready.kwargs["lease_token"],
                worker_id="prepared-worker",
                decision_id=ready.decision.decision_id,
                request=ready.request,
                purpose="process_worker",
                is_new=True,
                maximum_artifact_bytes=1_000_000,
                now=ready.ctx.clock(),
            )
            raise RuntimeError("outer rollback")
    await _no_intent(ready)
    await ready.service.execute(**ready.kwargs)
    with pytest.raises(ValueError, match="immutable"):
        async with ready.ctx.database.transaction() as session:
            row = await session.get(ProcessGenerationWorkloadRow, ready.kwargs["invocation_id"])
            row.record_digest = sha256_digest("changed")
            await session.flush()


async def test_cancellation_retains_prepared_intent_and_never_resends(evidence_context):
    entered = asyncio.Event()

    class CancelledClient(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            self.calls.append(request)
            entered.set()
            await asyncio.Event().wait()

    ready = await _ready(
        evidence_context, client=CancelledClient(lambda request: "unused", "test-open-weight")
    )
    task = asyncio.create_task(ready.service.execute(**ready.kwargs))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as failure:
        await asyncio.wait_for(task, 5)
    assert failure.value.__context__ is None
    assert await _receipt(ready)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    assert len(ready.client.calls) == 1
    async with ready.ctx.database.transaction() as session:
        call = await session.get(ExternalCallRow, ready.request.request_id)
        assert call.error["external_effect"] == "unknown"


async def test_workload_publication_failure_rolls_back_invocation_and_new_pins(
    evidence_context, monkeypatch
):
    ready = await _ready(evidence_context)

    async def fail(*args, **kwargs):
        raise RuntimeError("synthetic publication failure after pins")

    monkeypatch.setattr(ready.boundary, "_retained", fail)
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    await _no_intent(ready)


async def test_result_with_different_wire_bytes_is_retained_but_not_admitted(evidence_context):
    class ChangedWire(CallbackGenerationClient):
        async def generate_prepared(self, request, prepared):
            result = await super().generate_prepared(request, prepared)
            return replace(result, raw_request=b"different transmitted request")

    ready = await _ready(
        evidence_context, client=ChangedWire(lambda request: "candidate", "test-open-weight")
    )
    with pytest.raises(ProcessGenerationUnavailableError):
        await ready.service.execute(**ready.kwargs)
    async with ready.ctx.database.transaction() as session:
        call = await session.get(ExternalCallRow, ready.request.request_id)
        invocation = await session.get(ProcessWorkerInvocationRow, ready.kwargs["invocation_id"])
        assert call.status == "completed" and call.response_artifact_id is not None
        assert invocation.status == "failed"


@pytest.mark.parametrize(
    "evidence_context",
    [{"split": ProjectSplit.TRAIN, "destination": "https://worker.invalid/v1/responses"}],
    indirect=True,
)
async def test_process_dispatch_uses_admitted_http_destination_and_exact_retained_body(
    evidence_context,
):
    sent = []

    def handle(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "synthetic-response",
                "model": "test-open-weight-model",
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "candidate"}]}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 12},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-open-weight-model",
            provider="test-open-weight",
            retry_attempts=1,
            client=transport,
        )
        ready = await _ready(
            evidence_context, client=client, destination="https://worker.invalid/v1/responses"
        )
        assert (await ready.service.execute(**ready.kwargs)).output.output_text == "candidate"
    receipt = await _receipt(ready)
    assert len(sent) == 1
    assert str(sent[0].url) == receipt.prepared.destination == ready.action.requested_destination
    assert sent[0].content == receipt.prepared.body_json.encode()
