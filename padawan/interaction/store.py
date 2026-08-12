from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog
from padawan.interaction.contracts import (
    ExploratoryInteractionManifest,
    InteractionConsentEventRecord,
    InteractionConsentSnapshot,
    InteractionFeedbackKind,
    InteractionFeedbackRecord,
    InteractionMessageRecord,
    InteractionMode,
    InteractionRetentionClass,
    InteractionSessionRecord,
    InteractionTraceRecord,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ArtifactReferenceRow,
    ExternalCallRow,
    InteractionConsentEventRow,
    InteractionFeedbackRow,
    InteractionMessageRow,
    InteractionSessionRow,
    InteractionTraceRow,
    InteractionTurnRow,
    OperationSpanEventRow,
    OperationSpanRow,
)


class InteractionStore:
    """Persistence boundary for personal conversations and independent traces."""

    def __init__(self, catalog: ArtifactCatalog) -> None:
        self.catalog = catalog

    async def create_session(
        self,
        session: AsyncSession,
        *,
        title: str,
        research_trace_consent: bool,
        created_at: datetime | None = None,
    ) -> InteractionSessionRecord:
        timestamp = created_at or datetime.now(UTC)
        session_id = f"interaction-session-{uuid4()}"
        row = InteractionSessionRow(
            session_id=session_id,
            title=title.strip(),
            current_consent_sequence=1,
            created_at=timestamp,
            last_activity_at=timestamp,
        )
        if not row.title:
            raise ValueError("interaction session title cannot be empty")
        session.add(row)
        await session.flush()
        consent = await self._append_consent(
            session,
            session_row=row,
            sequence=1,
            research_trace_consent=research_trace_consent,
            recorded_at=timestamp,
        )
        return InteractionSessionRecord(
            session_id=row.session_id,
            title=row.title,
            current_consent=consent,
            created_at=row.created_at,
            last_activity_at=row.last_activity_at,
        )

    async def append_consent(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        research_trace_consent: bool,
        recorded_at: datetime | None = None,
    ) -> InteractionConsentSnapshot:
        row = await session.get(InteractionSessionRow, session_id)
        if row is None:
            raise KeyError(session_id)
        sequence = row.current_consent_sequence + 1
        consent = await self._append_consent(
            session,
            session_row=row,
            sequence=sequence,
            research_trace_consent=research_trace_consent,
            recorded_at=recorded_at or datetime.now(UTC),
        )
        row.current_consent_sequence = sequence
        row.last_activity_at = consent.recorded_at
        return consent

    async def current_consent(
        self, session: AsyncSession, *, session_id: str
    ) -> InteractionConsentSnapshot:
        owner = await session.get(InteractionSessionRow, session_id)
        if owner is None:
            raise KeyError(session_id)
        row = await session.scalar(
            select(InteractionConsentEventRow).where(
                InteractionConsentEventRow.session_id == session_id,
                InteractionConsentEventRow.sequence == owner.current_consent_sequence,
            )
        )
        if row is None:
            raise RuntimeError("interaction session lost its consent event")
        return _consent_from_row(row)

    async def list_sessions(self, session: AsyncSession) -> tuple[InteractionSessionRecord, ...]:
        rows = (
            await session.scalars(
                select(InteractionSessionRow).order_by(
                    InteractionSessionRow.last_activity_at.desc()
                )
            )
        ).all()
        records: list[InteractionSessionRecord] = []
        for row in rows:
            consent = await self.current_consent(session, session_id=row.session_id)
            records.append(
                InteractionSessionRecord(
                    session_id=row.session_id,
                    title=row.title,
                    current_consent=consent,
                    created_at=row.created_at,
                    last_activity_at=row.last_activity_at,
                )
            )
        return tuple(records)

    async def selected_history(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        parent_message_id: str | None,
    ) -> tuple[InteractionMessageRow, ...]:
        if parent_message_id is None:
            return ()
        reverse: list[InteractionMessageRow] = []
        seen: set[str] = set()
        current: str | None = parent_message_id
        while current is not None:
            if current in seen:
                raise RuntimeError("interaction message ancestry contains a cycle")
            seen.add(current)
            row = await session.get(InteractionMessageRow, current)
            if row is None or row.session_id != session_id:
                raise ValueError("parent message is not in the selected session")
            reverse.append(row)
            current = row.parent_message_id
        return tuple(reversed(reverse))

    async def turn_for_message(
        self, session: AsyncSession, *, message_id: str
    ) -> InteractionTurnRow | None:
        return cast(
            InteractionTurnRow | None,
            await session.scalar(
                select(InteractionTurnRow).where(
                    or_(
                        InteractionTurnRow.user_message_id == message_id,
                        InteractionTurnRow.assistant_message_id == message_id,
                    )
                )
            ),
        )

    async def begin_turn(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        turn_id: str,
        trace_id: str,
        request_id: str,
        parent_message_id: str | None,
        parent_turn_id: str | None,
        source_turn_id: str | None,
        mode: InteractionMode,
        user_message_id: str,
        user_content: str,
        manifest: ExploratoryInteractionManifest,
        manifest_digest: str,
        selected_history_artifact: ArtifactRef,
        generation_request_artifact: ArtifactRef,
    ) -> None:
        owner = await session.get(InteractionSessionRow, session_id)
        if owner is None:
            raise KeyError(session_id)
        if manifest.trace_id != trace_id or manifest.turn_id != turn_id:
            raise ValueError("interaction manifest differs from allocated turn identity")
        if sha256_digest(manifest.model_dump(mode="json")) != manifest_digest:
            raise ValueError("interaction manifest digest is invalid")
        message = InteractionMessageRow(
            message_id=user_message_id,
            session_id=session_id,
            parent_message_id=parent_message_id,
            role="user",
            content=user_content,
            content_digest=sha256_digest(user_content),
            created_at=manifest.created_at,
        )
        session.add(message)
        await session.flush()
        session.add(
            InteractionTurnRow(
                turn_id=turn_id,
                session_id=session_id,
                parent_turn_id=parent_turn_id,
                source_turn_id=source_turn_id,
                user_message_id=user_message_id,
                assistant_message_id=None,
                trace_id=trace_id,
                mode=mode.value,
                status="pending",
                error=None,
                created_at=manifest.created_at,
                completed_at=None,
            )
        )
        for reference, owner_type in (
            (selected_history_artifact, "interaction_trace_selected_history"),
            (generation_request_artifact, "interaction_trace_generation_request"),
        ):
            await self.catalog.register(session, reference)
            await self.catalog.reference(
                session,
                reference,
                owner_type=owner_type,
                owner_id=trace_id,
            )
        session.add(
            InteractionTraceRow(
                trace_id=trace_id,
                source_session_id=session_id,
                source_turn_id=turn_id,
                request_id=request_id,
                manifest_digest=manifest_digest,
                manifest_json=manifest.model_dump(mode="json"),
                evidence_class=manifest.evidence_class,
                retention_classification=manifest.retention_classification.value,
                controlled_benchmark_eligible=False,
                memory_synthesis_status="not_implemented",
                training_candidate_status="not_admitted",
                selected_history_artifact_id=selected_history_artifact.artifact_id,
                generation_request_artifact_id=generation_request_artifact.artifact_id,
                wire_request_artifact_id=None,
                raw_events_artifact_id=None,
                public_response_artifact_id=None,
                private_reasoning_artifact_id=None,
                generation_result_artifact_id=None,
                status="pending",
                response_metadata={},
                usage={},
                timing={},
                raw_event_summary=[],
                record_json=None,
                error=None,
                created_at=manifest.created_at,
                sealed_at=None,
            )
        )
        owner.last_activity_at = manifest.created_at
        await session.flush()

    async def mark_streaming(self, session: AsyncSession, *, turn_id: str, trace_id: str) -> None:
        turn = await session.get(InteractionTurnRow, turn_id)
        trace = await session.get(InteractionTraceRow, trace_id)
        if turn is None or trace is None or trace.sealed_at is not None:
            raise KeyError(turn_id)
        turn.status = "streaming"
        trace.status = "streaming"

    async def complete_turn(
        self,
        session: AsyncSession,
        *,
        turn_id: str,
        assistant_message_id: str,
        assistant_content: str,
        trace_record: InteractionTraceRecord,
    ) -> InteractionMessageRecord:
        turn = await session.get(InteractionTurnRow, turn_id)
        trace = await session.get(InteractionTraceRow, trace_record.trace_id)
        if turn is None or trace is None:
            raise KeyError(turn_id)
        if trace.sealed_at is not None or turn.status == "completed":
            raise ValueError("interaction trace is already sealed")
        if trace.manifest_digest != trace_record.manifest_digest:
            raise ValueError("trace record differs from pending manifest")
        completed_at = trace_record.sealed_at
        assistant = InteractionMessageRow(
            message_id=assistant_message_id,
            session_id=turn.session_id,
            parent_message_id=turn.user_message_id,
            role="assistant",
            content=assistant_content,
            content_digest=sha256_digest(assistant_content),
            created_at=completed_at,
        )
        session.add(assistant)
        await session.flush()
        turn.assistant_message_id = assistant_message_id
        turn.status = "completed"
        turn.error = None
        turn.completed_at = completed_at
        artifacts = trace_record.artifacts
        for reference, owner_type in (
            (artifacts.wire_request, "interaction_trace_wire_request"),
            (artifacts.raw_events, "interaction_trace_raw_events"),
            (artifacts.public_response, "interaction_trace_public_response"),
            (artifacts.generation_result, "interaction_trace_generation_result"),
        ):
            await self.catalog.register(session, reference)
            await self.catalog.reference(
                session, reference, owner_type=owner_type, owner_id=trace.trace_id
            )
        if artifacts.private_reasoning is not None:
            await self.catalog.register(session, artifacts.private_reasoning)
            await self.catalog.reference(
                session,
                artifacts.private_reasoning,
                owner_type="interaction_trace_private_reasoning",
                owner_id=trace.trace_id,
            )
        trace.wire_request_artifact_id = artifacts.wire_request.artifact_id
        trace.raw_events_artifact_id = artifacts.raw_events.artifact_id
        trace.public_response_artifact_id = artifacts.public_response.artifact_id
        trace.private_reasoning_artifact_id = (
            artifacts.private_reasoning.artifact_id
            if artifacts.private_reasoning is not None
            else None
        )
        trace.generation_result_artifact_id = artifacts.generation_result.artifact_id
        trace.status = "completed"
        trace.response_metadata = {
            "response_id": trace_record.response_id,
            "provider": trace_record.provider,
            "model_id": trace_record.model_id,
            "protocol": trace_record.protocol,
            "finish_reason": trace_record.finish_reason,
            "capabilities": trace_record.capabilities,
            "serving_telemetry": trace_record.serving_telemetry,
        }
        trace.usage = dict(trace_record.usage)
        trace.timing = trace_record.timing.model_dump(mode="json")
        trace.raw_event_summary = list(trace_record.raw_event_summary)
        trace.record_json = trace_record.model_dump(mode="json")
        trace.error = None
        trace.sealed_at = completed_at
        owner = await session.get(InteractionSessionRow, turn.session_id)
        if owner is not None:
            owner.last_activity_at = completed_at
        return InteractionMessageRecord(
            message_id=assistant.message_id,
            session_id=assistant.session_id,
            parent_message_id=assistant.parent_message_id,
            turn_id=turn.turn_id,
            role="assistant",
            content=assistant.content,
            content_digest=assistant.content_digest,
            created_at=assistant.created_at,
        )

    async def fail_turn(
        self,
        session: AsyncSession,
        *,
        turn_id: str,
        trace_id: str,
        error: dict[str, object],
        status: str = "failed",
        failed_at: datetime | None = None,
    ) -> None:
        turn = await session.get(InteractionTurnRow, turn_id)
        trace = await session.get(InteractionTraceRow, trace_id)
        if turn is None or trace is None:
            raise KeyError(turn_id)
        timestamp = failed_at or datetime.now(UTC)
        if status not in {"failed", "cancelled"}:
            raise ValueError("interaction failure status must be failed or cancelled")
        turn.status = status
        turn.error = error
        turn.completed_at = timestamp
        trace.status = status
        trace.error = error
        trace.sealed_at = timestamp

    async def append_feedback(
        self,
        session: AsyncSession,
        *,
        turn_id: str,
        kind: InteractionFeedbackKind,
        body: str | None,
        created_at: datetime | None = None,
    ) -> InteractionFeedbackRecord:
        turn = await session.get(InteractionTurnRow, turn_id)
        if turn is None:
            raise KeyError(turn_id)
        consent = await self.current_consent(session, session_id=turn.session_id)
        record = InteractionFeedbackRecord(
            feedback_id=f"interaction-feedback-{uuid4()}",
            session_id=turn.session_id,
            turn_id=turn_id,
            kind=kind,
            body=body,
            consent=consent,
            retention_classification=consent.retention_classification,
            created_at=created_at or datetime.now(UTC),
        )
        session.add(
            InteractionFeedbackRow(
                feedback_id=record.feedback_id,
                session_id=record.session_id,
                turn_id=record.turn_id,
                kind=record.kind.value,
                body=record.body,
                consent_event_id=record.consent.consent_event_id,
                retention_classification=record.retention_classification.value,
                record_json=record.model_dump(mode="json"),
                created_at=record.created_at,
            )
        )
        return record

    async def conversation(self, session: AsyncSession, *, session_id: str) -> dict[str, object]:
        owner = await session.get(InteractionSessionRow, session_id)
        if owner is None:
            raise KeyError(session_id)
        consent = await self.current_consent(session, session_id=session_id)
        turns = (
            await session.scalars(
                select(InteractionTurnRow)
                .where(InteractionTurnRow.session_id == session_id)
                .order_by(InteractionTurnRow.created_at)
            )
        ).all()
        messages = (
            await session.scalars(
                select(InteractionMessageRow)
                .where(InteractionMessageRow.session_id == session_id)
                .order_by(InteractionMessageRow.created_at)
            )
        ).all()
        message_turn: dict[str, str] = {}
        for turn in turns:
            message_turn[turn.user_message_id] = turn.turn_id
            if turn.assistant_message_id is not None:
                message_turn[turn.assistant_message_id] = turn.turn_id
        return {
            "session": InteractionSessionRecord(
                session_id=owner.session_id,
                title=owner.title,
                current_consent=consent,
                created_at=owner.created_at,
                last_activity_at=owner.last_activity_at,
            ).model_dump(mode="json"),
            "messages": [
                InteractionMessageRecord(
                    message_id=row.message_id,
                    session_id=row.session_id,
                    parent_message_id=row.parent_message_id,
                    turn_id=message_turn[row.message_id],
                    role=row.role,  # type: ignore[arg-type]
                    content=row.content,
                    content_digest=row.content_digest,
                    created_at=row.created_at,
                ).model_dump(mode="json")
                for row in messages
            ],
            "turns": [
                {
                    "turn_id": row.turn_id,
                    "parent_turn_id": row.parent_turn_id,
                    "source_turn_id": row.source_turn_id,
                    "user_message_id": row.user_message_id,
                    "assistant_message_id": row.assistant_message_id,
                    "trace_id": row.trace_id,
                    "mode": row.mode,
                    "status": row.status,
                    "error": row.error,
                    "created_at": row.created_at.isoformat(),
                    "completed_at": (
                        row.completed_at.isoformat() if row.completed_at is not None else None
                    ),
                }
                for row in turns
            ],
        }

    async def delete_session(
        self, session: AsyncSession, *, session_id: str
    ) -> dict[str, tuple[str, ...]]:
        owner = await session.get(InteractionSessionRow, session_id)
        if owner is None:
            raise KeyError(session_id)
        traces = (
            await session.scalars(
                select(InteractionTraceRow).where(
                    InteractionTraceRow.source_session_id == session_id
                )
            )
        ).all()
        retained = tuple(
            sorted(
                trace.trace_id
                for trace in traces
                if trace.retention_classification
                == InteractionRetentionClass.CONSENTED_RESEARCH_EVIDENCE.value
            )
        )
        deletable = [
            trace
            for trace in traces
            if trace.retention_classification == InteractionRetentionClass.PERSONAL_DELETABLE.value
        ]
        request_ids = [trace.request_id for trace in deletable]
        trace_ids = [trace.trace_id for trace in deletable]
        if request_ids:
            operation_ids = (
                await session.scalars(
                    select(OperationSpanRow.operation_id).where(
                        OperationSpanRow.source_ref.in_(request_ids)
                    )
                )
            ).all()
            if operation_ids:
                await session.execute(
                    delete(OperationSpanEventRow).where(
                        OperationSpanEventRow.operation_id.in_(operation_ids)
                    )
                )
                await session.execute(
                    delete(OperationSpanRow).where(OperationSpanRow.operation_id.in_(operation_ids))
                )
            await session.execute(
                delete(ArtifactReferenceRow).where(ArtifactReferenceRow.owner_id.in_(request_ids))
            )
            await session.execute(
                delete(ExternalCallRow).where(ExternalCallRow.request_id.in_(request_ids))
            )
        if trace_ids:
            await session.execute(
                delete(ArtifactReferenceRow).where(ArtifactReferenceRow.owner_id.in_(trace_ids))
            )
            await session.execute(
                delete(InteractionTraceRow).where(InteractionTraceRow.trace_id.in_(trace_ids))
            )
        feedback = (
            await session.scalars(
                select(InteractionFeedbackRow).where(
                    InteractionFeedbackRow.session_id == session_id
                )
            )
        ).all()
        retained_feedback = tuple(
            sorted(
                row.feedback_id
                for row in feedback
                if row.retention_classification
                == InteractionRetentionClass.CONSENTED_RESEARCH_EVIDENCE.value
            )
        )
        deletable_feedback = tuple(
            sorted(
                row.feedback_id
                for row in feedback
                if row.retention_classification
                == InteractionRetentionClass.PERSONAL_DELETABLE.value
            )
        )
        if deletable_feedback:
            await session.execute(
                delete(InteractionFeedbackRow).where(
                    InteractionFeedbackRow.feedback_id.in_(deletable_feedback)
                )
            )
        await session.execute(
            delete(InteractionTurnRow).where(InteractionTurnRow.session_id == session_id)
        )
        await session.execute(
            delete(InteractionMessageRow).where(InteractionMessageRow.session_id == session_id)
        )
        await session.execute(
            delete(InteractionConsentEventRow).where(
                InteractionConsentEventRow.session_id == session_id
            )
        )
        await session.delete(owner)
        return {
            "deleted_personal_trace_ids": tuple(sorted(trace_ids)),
            "retained_research_trace_ids": retained,
            "deleted_personal_feedback_ids": deletable_feedback,
            "retained_research_feedback_ids": retained_feedback,
        }

    async def _append_consent(
        self,
        session: AsyncSession,
        *,
        session_row: InteractionSessionRow,
        sequence: int,
        research_trace_consent: bool,
        recorded_at: datetime,
    ) -> InteractionConsentSnapshot:
        consent = InteractionConsentSnapshot(
            consent_event_id=f"interaction-consent-{uuid4()}",
            sequence=sequence,
            retention_classification=(
                InteractionRetentionClass.CONSENTED_RESEARCH_EVIDENCE
                if research_trace_consent
                else InteractionRetentionClass.PERSONAL_DELETABLE
            ),
            research_trace_consent=research_trace_consent,
            recorded_at=recorded_at,
        )
        record = InteractionConsentEventRecord(
            session_id=session_row.session_id,
            consent=consent,
        )
        session.add(
            InteractionConsentEventRow(
                consent_event_id=consent.consent_event_id,
                session_id=session_row.session_id,
                sequence=consent.sequence,
                research_trace_consent=consent.research_trace_consent,
                retention_classification=consent.retention_classification.value,
                record_json=record.model_dump(mode="json"),
                created_at=consent.recorded_at,
            )
        )
        await session.flush()
        return consent


def _consent_from_row(row: InteractionConsentEventRow) -> InteractionConsentSnapshot:
    record = InteractionConsentEventRecord.model_validate(row.record_json, strict=False)
    return record.consent
