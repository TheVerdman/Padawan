from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from padawan.interaction.contracts import (
    InteractionAxis,
    InteractionFeedbackKind,
    InteractionMode,
)
from padawan.interaction.service import (
    GenerationSubmission,
    TemporaryGenerationSubmission,
)
from padawan.models.contracts import (
    SamplingConfiguration,
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
from tests.support.interaction import (
    _conversation,
    _count,
    _create_session,
    _FailingStreamingStudent,
    _generate,
    _lab,
    _StreamingStudent,
)


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
