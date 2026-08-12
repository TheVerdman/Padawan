from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from padawan.adapters.base import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamEvent,
    ModelProviderError,
)
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.interaction.contracts import (
    InteractionAxis,
    InteractionFeedbackKind,
    InteractionMode,
    ServingPathIdentity,
    StudentTargetDescriptor,
)
from padawan.interaction.service import (
    GenerationSubmission,
    InteractionService,
    TemporaryGenerationSubmission,
)
from padawan.interaction.store import InteractionStore
from padawan.interaction.targets import ManagedStudentTarget, StudentTargetRegistry
from padawan.models.contracts import (
    Capability,
    CapabilityAvailability,
    ResearchRole,
    RuntimeCapabilities,
    SamplingConfiguration,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    IdentityEvidenceStatus,
    ModelServingIdentity,
    VersionedComponentIdentity,
)
from padawan.models.tables import (
    ArtifactRow,
    AttemptRow,
    EpisodeRow,
    ExternalCallRow,
    InteractionConsentEventRow,
    InteractionFeedbackRow,
    InteractionMessageRow,
    InteractionSessionRow,
    InteractionTraceRow,
    InteractionTurnRow,
    MemorySnapshotRow,
    OperationSpanRow,
    TrainingEligibilityRow,
)


def _component(component_id: str) -> VersionedComponentIdentity:
    return VersionedComponentIdentity(
        component_id=component_id,
        version="1.0.0",
        digest=sha256_digest(component_id),
        evidence_status=IdentityEvidenceStatus.PINNED,
        evidence=f"test identity for {component_id}",
    )


def _capabilities() -> RuntimeCapabilities:
    available = Capability(
        availability=CapabilityAvailability.AVAILABLE,
        reason="available in the deterministic test stream",
    )
    unavailable = Capability(
        availability=CapabilityAvailability.UNAVAILABLE,
        reason="not emitted by the deterministic test stream",
    )
    return RuntimeCapabilities(
        responses_api=available,
        streaming=available,
        cancellation=available,
        logprobs=unavailable,
        token_ids=unavailable,
        private_reasoning=available,
        reasoning_boundaries=available,
        gpu_telemetry=available,
        router_telemetry=unavailable,
    )


def _descriptor() -> StudentTargetDescriptor:
    runtime_parameters = {
        "batch_size": "1",
        "continuation": "false",
        "response_storage": "false",
    }
    runtime = _component("test.runtime")
    serving = _component("test.serving")
    model = ModelServingIdentity(
        purpose="student",
        research_role=ResearchRole.TARGET,
        model_id="student-test-model",
        checkpoint=_component("test.checkpoint"),
        quantization=_component("test.quantization"),
        runtime=runtime,
        serving_artifact=serving,
        protocol="responses",
        runtime_parameters=runtime_parameters,
        runtime_parameters_digest=sha256_digest(runtime_parameters),
    )
    return StudentTargetDescriptor(
        target_id="student.test.primary",
        display_name="Test Student",
        provider="test_student",
        model=model,
        serving_path=ServingPathIdentity(
            target_id="student.test.primary",
            endpoint_origin="https://student.test",
            route="/v1/responses",
            protocol="responses",
            components=tuple(
                sorted((runtime, serving), key=lambda item: (item.component_id, item.version))
            ),
        ),
        configured_context_window_tokens=8_192,
        effective_input_limit_tokens=7_168,
        private_reasoning_capture_enabled=True,
    )


class _StreamingStudent:
    def __init__(self) -> None:
        self.calls: list[GenerationRequest] = []

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        terminal: GenerationResult | None = None
        async for event in self.stream(request):
            if event.result is not None:
                terminal = event.result
        if terminal is None:  # pragma: no cover - contract guard
            raise RuntimeError("test stream omitted its terminal event")
        return terminal

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]:
        self.calls.append(request)
        index = len(self.calls)
        private = f"hidden plan {index}"
        output = f"assistant reply {index}"
        now = datetime.now(UTC)
        yield GenerationStreamEvent(
            sequence=0,
            event_type="response.reasoning_text.delta",
            occurred_at=now,
            raw_event=json.dumps(
                {"type": "response.reasoning_text.delta", "delta": private}
            ).encode(),
            metadata={"type": "response.reasoning_text.delta", "delta": private},
            private_reasoning_delta=private,
        )
        await asyncio.sleep(0)
        yield GenerationStreamEvent(
            sequence=1,
            event_type="response.output_text.delta",
            occurred_at=datetime.now(UTC),
            raw_event=b'{"type":"response.output_text.delta","delta":"assistant "}',
            metadata={"type": "response.output_text.delta", "delta": "assistant "},
            public_text_delta="assistant ",
        )
        await asyncio.sleep(0)
        suffix = f"reply {index}"
        yield GenerationStreamEvent(
            sequence=2,
            event_type="response.output_text.delta",
            occurred_at=datetime.now(UTC),
            raw_event=json.dumps({"type": "response.output_text.delta", "delta": suffix}).encode(),
            metadata={"type": "response.output_text.delta", "delta": suffix},
            public_text_delta=suffix,
        )
        result = GenerationResult(
            request_id=request.request_id,
            response_id=f"response-{index}",
            provider="test_student",
            model_id="student-test-model",
            protocol="responses",
            output_text=output,
            raw_request=request.model_dump_json().encode(),
            raw_response=json.dumps({"output": output, "private_reasoning": private}).encode(),
            usage={"input_tokens": 10 + index, "output_tokens": 3, "total_tokens": 13 + index},
            token_ids=None,
            token_logprobs=None,
            private_reasoning=private,
            reasoning_summary=None,
            finish_reason="stop",
            latency_ms=3.5,
            capabilities=_capabilities(),
            telemetry={"gpu": {"device": "test"}, "serving_revision": "test-1"},
            provider_metadata={"streamed": True},
        )
        yield GenerationStreamEvent(
            sequence=3,
            event_type="response.completed",
            occurred_at=datetime.now(UTC),
            raw_event=result.raw_response,
            metadata={"type": "response.completed", "response_id": result.response_id},
            result=result,
        )


class _FailingStreamingStudent(_StreamingStudent):
    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]:
        self.calls.append(request)
        yield GenerationStreamEvent(
            sequence=0,
            event_type="response.output_text.delta",
            occurred_at=datetime.now(UTC),
            raw_event=b'{"type":"response.output_text.delta","delta":"partial"}',
            metadata={"type": "response.output_text.delta", "delta": "partial"},
            public_text_delta="partial",
        )
        raise ModelProviderError(
            "test provider failed after one event",
            provider="test_student",
            status_code=503,
            retryable=True,
            response_body=b'{"error":"unavailable"}',
        )


async def _lab(
    database: Any, artifact_root: Path
) -> tuple[InteractionService, InteractionStore, LocalArtifactStore, _StreamingStudent]:
    artifacts = LocalArtifactStore(artifact_root)
    store = InteractionStore(ArtifactCatalog(artifacts))
    client = _StreamingStudent()

    async def load_descriptor() -> StudentTargetDescriptor:
        return _descriptor()

    registry = StudentTargetRegistry(
        (
            ManagedStudentTarget(
                target_id="student.test.primary",
                display_name="Test Student",
                provider="test_student",
                client=client,
                descriptor_loader=load_descriptor,
            ),
        )
    )
    service = InteractionService(
        database=database,
        artifacts=artifacts,
        store=store,
        targets=registry,
        system_prompt="You are the selected Padawan student model.",
    )
    return service, store, artifacts, client


async def _create_session(store: InteractionStore, database: Any, *, consent: bool) -> str:
    async with database.transaction() as session:
        record = await store.create_session(
            session,
            title="Evidence-grade conversation",
            research_trace_consent=consent,
        )
    return record.session_id


async def _generate(
    service: InteractionService,
    *,
    session_id: str,
    submission: GenerationSubmission,
) -> list[Any]:
    return [
        update
        async for update in service.stream_generation(
            session_id=session_id,
            submission=submission,
        )
    ]


async def _count(database: Any, row_type: type[Any]) -> int:
    async with database.transaction() as session:
        return int(await session.scalar(select(func.count()).select_from(row_type)) or 0)


async def test_durable_chat_streams_explicit_history_and_separates_reasoning(
    database: Any, tmp_path: Path
) -> None:
    service, store, _artifacts, client = await _lab(database, tmp_path / "artifacts")
    session_id = await _create_session(store, database, consent=True)

    first = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="First question",
        ),
    )
    assert [update.event for update in first] == ["start", "delta", "delta", "completed"]
    assert "".join(update.data["text"] for update in first if update.event == "delta") == (
        "assistant reply 1"
    )
    first_completed = first[-1].data
    first_assistant_id = first_completed["assistant_message"]["message_id"]

    second = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="Follow-up question",
            parent_message_id=first_assistant_id,
        ),
    )
    assert client.calls[0].input == [{"role": "user", "content": "First question"}]
    assert client.calls[1].input == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "assistant reply 1"},
        {"role": "user", "content": "Follow-up question"},
    ]
    assert all(call.previous_response_id is None and not call.store for call in client.calls)

    trace_id = second[-1].data["trace_id"]
    public_trace = await service.trace_inspector(trace_id=trace_id, include_restricted=False)
    restricted_trace = await service.trace_inspector(trace_id=trace_id, include_restricted=True)
    assert "hidden plan 2" not in json.dumps(public_trace)
    assert "hidden plan 2" not in json.dumps(await _conversation(database, store, session_id))
    assert restricted_trace["restricted_research_view"]["private_reasoning"] == ("hidden plan 2")
    assert public_trace["manifest"]["evidence_class"] == ("exploratory_not_controlled_benchmark")
    assert public_trace["controlled_benchmark_eligible"] is False
    assert public_trace["manifest"]["target"]["model"]["checkpoint"]["component_id"] == (
        "test.checkpoint"
    )
    assert public_trace["manifest"]["target"]["model"]["quantization"]["component_id"] == (
        "test.quantization"
    )
    assert public_trace["manifest"]["harness_profile"]["continuation"] == {
        "continuation_mode": "none",
        "response_storage_enabled": False,
        "previous_response_id_enabled": False,
        "reasoning_retention_enabled": False,
        "reasoning_retention_mode": "none",
        "private_reasoning_capture_enabled": True,
        "private_reasoning_used_as_context": False,
    }
    assert public_trace["timing"]["first_event_at"] is not None
    assert public_trace["timing"]["first_public_token_at"] is not None
    assert public_trace["raw_event_summary"][0]["private_reasoning"] is True
    assert public_trace["raw_event_summary"][1]["public_delta"] is True

    async with database.transaction() as session:
        call = await session.scalar(
            select(ExternalCallRow).where(ExternalCallRow.interaction_trace_id == trace_id)
        )
        assert call is not None
        assert call.run_id is None
        assert call.status == "completed"
    for table in (EpisodeRow, AttemptRow, MemorySnapshotRow, TrainingEligibilityRow):
        assert await _count(database, table) == 0


async def test_retry_replays_identical_context_and_requires_declared_axis_changes(
    database: Any, tmp_path: Path
) -> None:
    service, store, _artifacts, _client = await _lab(database, tmp_path / "artifacts")
    session_id = await _create_session(store, database, consent=False)
    first = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(target_id="student.test.primary", content="Repeat me"),
    )
    source_turn_id = first[-1].data["turn_id"]
    source_trace_id = first[-1].data["trace_id"]

    retry = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            mode=InteractionMode.RETRY,
            source_turn_id=source_turn_id,
        ),
    )
    retry_trace_id = retry[-1].data["trace_id"]
    source = await service.trace_inspector(trace_id=source_trace_id, include_restricted=False)
    retried = await service.trace_inspector(trace_id=retry_trace_id, include_restricted=False)
    assert retried["manifest"]["comparison_source_trace_id"] == source_trace_id
    assert (
        retried["manifest"]["rendered_context_digest"]
        == source["manifest"]["rendered_context_digest"]
    )
    assert (
        retried["manifest"]["selected_history_digest"]
        == source["manifest"]["selected_history_digest"]
    )
    assert retried["manifest"]["actual_axis_changes"] == []

    changed_sampling = SamplingConfiguration(
        temperature=0.7,
        top_p=0.95,
        max_output_tokens=2_048,
    )
    replay = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            mode=InteractionMode.REPLAY,
            source_turn_id=source_turn_id,
            sampling=changed_sampling,
            declared_axis_changes=(InteractionAxis.SAMPLING,),
        ),
    )
    replay_trace = await service.trace_inspector(
        trace_id=replay[-1].data["trace_id"], include_restricted=False
    )
    assert replay_trace["manifest"]["declared_axis_changes"] == ["sampling"]
    assert replay_trace["manifest"]["actual_axis_changes"] == ["sampling"]

    with pytest.raises(ValidationError, match="declared replay axes"):
        await _generate(
            service,
            session_id=session_id,
            submission=GenerationSubmission(
                target_id="student.test.primary",
                mode=InteractionMode.REPLAY,
                source_turn_id=source_turn_id,
                sampling=changed_sampling,
            ),
        )
    assert await _count(database, InteractionTurnRow) == 3
    assert await _count(database, InteractionMessageRow) == 6


async def test_temporary_chat_streams_without_any_durable_research_write(
    database: Any, tmp_path: Path
) -> None:
    service, _store, artifacts, client = await _lab(database, tmp_path / "artifacts")
    tables = (
        InteractionSessionRow,
        InteractionConsentEventRow,
        InteractionMessageRow,
        InteractionTurnRow,
        InteractionTraceRow,
        InteractionFeedbackRow,
        ExternalCallRow,
        OperationSpanRow,
        ArtifactRow,
    )
    before = {table.__tablename__: await _count(database, table) for table in tables}
    before_blobs = tuple(artifacts.blob_root.glob("*/*"))

    updates = [
        update
        async for update in service.stream_temporary(
            TemporaryGenerationSubmission(
                target_id="student.test.primary",
                history=(
                    {"role": "user", "content": "Temporary first"},
                    {"role": "assistant", "content": "Temporary reply"},
                    {"role": "user", "content": "Temporary follow-up"},
                ),
            )
        )
    ]
    assert [update.event for update in updates] == ["start", "delta", "delta", "completed"]
    assert updates[0].data["temporary"] is True
    assert updates[0].data["durable_storage"] is False
    assert updates[0].data["target"]["target_id"] == "student.test.primary"
    assert updates[0].data["target"]["model_id"] == "student-test-model"
    assert client.calls[0].input[-1] == {
        "role": "user",
        "content": "Temporary follow-up",
    }
    after = {table.__tablename__: await _count(database, table) for table in tables}
    assert after == before
    assert tuple(artifacts.blob_root.glob("*/*")) == before_blobs


async def test_consent_is_snapshotted_feedback_is_separate_and_deletion_preserves_only_research(
    database: Any, tmp_path: Path
) -> None:
    service, store, _artifacts, _client = await _lab(database, tmp_path / "artifacts")
    session_id = await _create_session(store, database, consent=False)
    personal = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(target_id="student.test.primary", content="Personal"),
    )
    personal_turn = personal[-1].data["turn_id"]
    personal_trace = personal[-1].data["trace_id"]
    parent = personal[-1].data["assistant_message"]["message_id"]

    async with database.transaction() as session:
        personal_feedback = await store.append_feedback(
            session,
            turn_id=personal_turn,
            kind=InteractionFeedbackKind.NOTE,
            body="Delete this personal note with the conversation.",
        )
        consent = await store.append_consent(
            session,
            session_id=session_id,
            research_trace_consent=True,
        )
        feedback = await store.append_feedback(
            session,
            turn_id=personal_turn,
            kind=InteractionFeedbackKind.CORRECTION,
            body="The durable correction is separate from the immutable message.",
        )
    assert consent.sequence == 2
    assert feedback.consent.consent_event_id == consent.consent_event_id

    research = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="Research-consented",
            parent_message_id=parent,
        ),
    )
    research_trace = research[-1].data["trace_id"]
    personal_record = await service.trace_inspector(
        trace_id=personal_trace, include_restricted=False
    )
    research_record = await service.trace_inspector(
        trace_id=research_trace, include_restricted=False
    )
    assert personal_record["retention_classification"] == "personal_deletable"
    assert research_record["retention_classification"] == "consented_research_evidence"
    assert personal_record["manifest"]["consent"]["sequence"] == 1
    assert research_record["manifest"]["consent"]["sequence"] == 2

    async with database.transaction() as session:
        result = await store.delete_session(session, session_id=session_id)
    assert result == {
        "deleted_personal_trace_ids": (personal_trace,),
        "retained_research_trace_ids": (research_trace,),
        "deleted_personal_feedback_ids": (personal_feedback.feedback_id,),
        "retained_research_feedback_ids": (feedback.feedback_id,),
    }
    assert await _count(database, InteractionSessionRow) == 0
    assert await _count(database, InteractionTurnRow) == 0
    assert await _count(database, InteractionMessageRow) == 0
    assert await _count(database, InteractionFeedbackRow) == 1
    async with database.transaction() as session:
        assert await session.get(InteractionTraceRow, personal_trace) is None
        assert await session.get(InteractionTraceRow, research_trace) is not None
        calls = (await session.scalars(select(ExternalCallRow))).all()
        assert [call.interaction_trace_id for call in calls] == [research_trace]
        retained_feedback = await session.get(InteractionFeedbackRow, feedback.feedback_id)
        assert retained_feedback is not None
        assert retained_feedback.retention_classification == "consented_research_evidence"


async def test_failed_and_abandoned_streams_seal_evidence_instead_of_staying_active(
    database: Any, tmp_path: Path
) -> None:
    service, store, _artifacts, _client = await _lab(database, tmp_path / "artifacts")
    session_id = await _create_session(store, database, consent=False)
    failing = _FailingStreamingStudent()
    service.targets.get("student.test.primary").client = failing

    updates = await _generate(
        service,
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="Fail after streaming starts",
        ),
    )
    assert [update.event for update in updates] == ["start", "delta", "error"]
    failed_trace_id = updates[-1].data["trace_id"]
    failed_trace = await service.trace_inspector(trace_id=failed_trace_id, include_restricted=True)
    assert failed_trace["status"] == "failed"
    assert failed_trace["timing"]["first_public_token_at"] is not None
    assert failed_trace["raw_event_summary"][0]["public_delta"] is True
    assert (
        failed_trace["restricted_research_view"]["raw_events"]["terminal_error"]["provider_error"]
        is True
    )
    async with database.transaction() as session:
        call = await session.scalar(
            select(ExternalCallRow).where(ExternalCallRow.interaction_trace_id == failed_trace_id)
        )
        assert call is not None
        assert call.status == "failed_terminal"
        assert call.completed_at is not None
        operation = await session.scalar(
            select(OperationSpanRow).where(OperationSpanRow.source_ref == call.request_id)
        )
        assert operation is not None and operation.status == "failed"

    service.targets.get("student.test.primary").client = _StreamingStudent()
    stream = service.stream_generation(
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="Disconnect before provider invocation",
        ),
    )
    started = await anext(stream)
    assert started.event == "start"
    await stream.aclose()
    cancelled_trace = await service.trace_inspector(
        trace_id=started.data["trace_id"], include_restricted=True
    )
    assert cancelled_trace["status"] == "cancelled"
    assert cancelled_trace["restricted_research_view"]["raw_events"]["events"] == []
    async with database.transaction() as session:
        cancelled_call = await session.scalar(
            select(ExternalCallRow).where(
                ExternalCallRow.interaction_trace_id == started.data["trace_id"]
            )
        )
        assert cancelled_call is None

    stream = service.stream_generation(
        session_id=session_id,
        submission=GenerationSubmission(
            target_id="student.test.primary",
            content="Disconnect after the first public token",
        ),
    )
    started = await anext(stream)
    delta = await anext(stream)
    assert delta.event == "delta"
    await stream.aclose()
    cancelled_trace = await service.trace_inspector(
        trace_id=started.data["trace_id"], include_restricted=True
    )
    assert cancelled_trace["status"] == "cancelled"
    assert len(cancelled_trace["restricted_research_view"]["raw_events"]["events"]) == 2
    async with database.transaction() as session:
        cancelled_call = await session.scalar(
            select(ExternalCallRow).where(
                ExternalCallRow.interaction_trace_id == started.data["trace_id"]
            )
        )
        assert cancelled_call is not None
        assert cancelled_call.status == "cancelled"
        assert cancelled_call.completed_at is not None
        operation = await session.scalar(
            select(OperationSpanRow).where(OperationSpanRow.source_ref == cancelled_call.request_id)
        )
        assert operation is not None and operation.status == "cancelled"


async def _conversation(
    database: Any, store: InteractionStore, session_id: str
) -> dict[str, object]:
    async with database.transaction() as session:
        return await store.conversation(session, session_id=session_id)
