from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.artifacts.store import (
    ArtifactBackend,
    artifact_put_bytes,
    artifact_put_text,
    artifact_read_bytes,
)
from padawan.interaction.contracts import (
    ExploratoryInteractionManifest,
    InteractionAxis,
    InteractionConsentSnapshot,
    InteractionMode,
    InteractionTraceArtifacts,
    InteractionTraceRecord,
    InteractionTraceTiming,
    SelectedHistoryMessage,
    StudentTargetDescriptor,
)
from padawan.interaction.store import InteractionStore
from padawan.interaction.targets import StudentTargetRegistry
from padawan.models.contracts import ArtifactRef, SamplingConfiguration
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    BudgetDisposition,
    BudgetLimit,
    ContextPolicy,
    ContinuationPolicy,
    HarnessBudgets,
    HarnessProfile,
    IdentityEvidenceStatus,
    ResearchInstrumentationSeams,
    VersionedComponentIdentity,
)
from padawan.models.tables import (
    ArtifactRow,
    ExternalCallRow,
    InteractionMessageRow,
    InteractionTraceRow,
    InteractionTurnRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor

_HARNESS_RELEASED_AT = datetime(2026, 8, 12, tzinfo=UTC)


def _json_sampling(value: object) -> object:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if isinstance(normalized.get("stop"), list):
        normalized["stop"] = tuple(normalized["stop"])
    return normalized


class GenerationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str = Field(min_length=1)
    content: str | None = None
    parent_message_id: str | None = None
    mode: InteractionMode = InteractionMode.MESSAGE
    source_turn_id: str | None = None
    sampling: SamplingConfiguration = Field(
        default_factory=lambda: SamplingConfiguration(
            temperature=0.2,
            top_p=0.95,
            max_output_tokens=2_048,
        )
    )
    declared_axis_changes: tuple[InteractionAxis, ...] = ()

    @field_validator("sampling", mode="before")
    @classmethod
    def parse_json_sampling(cls, value: object) -> object:
        return _json_sampling(value)

    @field_validator("declared_axis_changes", mode="before")
    @classmethod
    def parse_json_axes(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value


class TemporaryGenerationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target_id: str = Field(min_length=1)
    history: tuple[dict[str, str], ...]
    sampling: SamplingConfiguration = Field(
        default_factory=lambda: SamplingConfiguration(
            temperature=0.2,
            top_p=0.95,
            max_output_tokens=2_048,
        )
    )

    @field_validator("history", mode="before")
    @classmethod
    def parse_json_history(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @field_validator("sampling", mode="before")
    @classmethod
    def parse_json_sampling(cls, value: object) -> object:
        return _json_sampling(value)


@dataclass(frozen=True)
class InteractionStreamUpdate:
    event: Literal["start", "delta", "completed", "error"]
    data: dict[str, Any]


@dataclass(frozen=True)
class _PreparedTurn:
    session_id: str
    turn_id: str
    trace_id: str
    request_id: str
    user_message_id: str
    user_content: str
    parent_message_id: str | None
    parent_turn_id: str | None
    source_turn_id: str | None
    history_rows: tuple[InteractionMessageRow, ...]
    parent_manifest_digest: str | None
    source_manifest: ExploratoryInteractionManifest | None
    consent: InteractionConsentSnapshot


class InteractionService:
    def __init__(
        self,
        *,
        database: Database,
        artifacts: ArtifactBackend,
        store: InteractionStore,
        targets: StudentTargetRegistry,
        system_prompt: str,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("Interaction Lab system prompt cannot be empty")
        self.database = database
        self.artifacts = artifacts
        self.store = store
        self.targets = targets
        self.system_prompt = system_prompt

    async def stream_generation(
        self,
        *,
        session_id: str,
        submission: GenerationSubmission,
    ) -> AsyncIterator[InteractionStreamUpdate]:
        target = self.targets.get(submission.target_id)
        async with target.lock:
            prepared = await self._prepare_turn(session_id=session_id, submission=submission)
            descriptor = await target.prepare()
            request, manifest, history_ref, request_ref = await self._build_manifest_and_request(
                prepared=prepared,
                submission=submission,
                descriptor=descriptor,
            )
            manifest_digest = sha256_digest(manifest.model_dump(mode="json"))
            async with self.database.transaction() as db_session:
                await self.store.begin_turn(
                    db_session,
                    session_id=prepared.session_id,
                    turn_id=prepared.turn_id,
                    trace_id=prepared.trace_id,
                    request_id=prepared.request_id,
                    parent_message_id=prepared.parent_message_id,
                    parent_turn_id=prepared.parent_turn_id,
                    source_turn_id=prepared.source_turn_id,
                    mode=submission.mode,
                    user_message_id=prepared.user_message_id,
                    user_content=prepared.user_content,
                    manifest=manifest,
                    manifest_digest=manifest_digest,
                    selected_history_artifact=history_ref,
                    generation_request_artifact=request_ref,
                )
                await self.store.mark_streaming(
                    db_session, turn_id=prepared.turn_id, trace_id=prepared.trace_id
                )
            requested_at = datetime.now(UTC)
            first_event_at: datetime | None = None
            first_public_at: datetime | None = None
            public_parts: list[str] = []
            event_records: list[dict[str, Any]] = []
            event_summary: list[dict[str, Any]] = []
            result: GenerationResult | None = None
            executor = IdempotentGenerationExecutor(
                database=self.database,
                artifacts=self.artifacts,
                client=target.client,
            )
            invocation = executor.stream_execute(
                interaction_trace_id=prepared.trace_id,
                purpose="exploratory_interaction",
                provider=descriptor.provider,
                request=request,
            )
            try:
                yield InteractionStreamUpdate(
                    event="start",
                    data={
                        "session_id": session_id,
                        "turn_id": prepared.turn_id,
                        "trace_id": prepared.trace_id,
                        "user_message_id": prepared.user_message_id,
                        "target": _public_target(descriptor),
                        "manifest_digest": manifest_digest,
                        "retention_classification": manifest.retention_classification.value,
                        "evidence_class": manifest.evidence_class,
                    },
                )
                async for event in invocation:
                    if first_event_at is None:
                        first_event_at = event.occurred_at
                    raw_digest = sha256_digest(event.raw_event)
                    event_records.append(
                        {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "occurred_at": event.occurred_at.isoformat(),
                            "raw_event_base64": base64.b64encode(event.raw_event).decode("ascii"),
                            "metadata": event.metadata,
                        }
                    )
                    event_summary.append(
                        {
                            "sequence": event.sequence,
                            "event_type": event.event_type,
                            "occurred_at": event.occurred_at.isoformat(),
                            "raw_digest": raw_digest,
                            "public_delta": event.public_text_delta is not None,
                            "private_reasoning": event.private_reasoning_delta is not None,
                            "terminal": event.terminal,
                        }
                    )
                    if event.public_text_delta:
                        if first_public_at is None:
                            first_public_at = event.occurred_at
                        public_parts.append(event.public_text_delta)
                        yield InteractionStreamUpdate(
                            event="delta", data={"text": event.public_text_delta}
                        )
                    if event.result is not None:
                        result = event.result
                if result is None:
                    raise RuntimeError("generation stream ended without a result")
                streamed_text = "".join(public_parts)
                if result.output_text != streamed_text:
                    remainder = (
                        result.output_text[len(streamed_text) :]
                        if result.output_text.startswith(streamed_text)
                        else result.output_text
                    )
                    if remainder:
                        if first_public_at is None:
                            first_public_at = datetime.now(UTC)
                        public_parts.append(remainder)
                        yield InteractionStreamUpdate(event="delta", data={"text": remainder})
                assistant = await self._seal_completed_trace(
                    prepared=prepared,
                    manifest=manifest,
                    manifest_digest=manifest_digest,
                    history_ref=history_ref,
                    request_ref=request_ref,
                    result=result,
                    requested_at=requested_at,
                    first_event_at=first_event_at,
                    first_public_at=first_public_at,
                    event_records=event_records,
                    event_summary=event_summary,
                )
                yield InteractionStreamUpdate(
                    event="completed",
                    data={
                        "turn_id": prepared.turn_id,
                        "trace_id": prepared.trace_id,
                        "assistant_message": assistant,
                        "usage": result.usage,
                        "latency_ms": result.latency_ms,
                        "response_id": result.response_id,
                    },
                )
            except (asyncio.CancelledError, GeneratorExit):
                await self._seal_failure(
                    prepared=prepared,
                    error={"type": "cancelled", "message": "client disconnected"},
                    status="cancelled",
                    requested_at=requested_at,
                    first_event_at=first_event_at,
                    first_public_at=first_public_at,
                    event_records=event_records,
                    event_summary=event_summary,
                )
                raise
            except Exception as exc:
                await self._seal_failure(
                    prepared=prepared,
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "provider_error": isinstance(exc, ModelProviderError),
                    },
                    status="failed",
                    requested_at=requested_at,
                    first_event_at=first_event_at,
                    first_public_at=first_public_at,
                    event_records=event_records,
                    event_summary=event_summary,
                )
                yield InteractionStreamUpdate(
                    event="error",
                    data={
                        "turn_id": prepared.turn_id,
                        "trace_id": prepared.trace_id,
                        "message": str(exc),
                    },
                )
            finally:
                await _close_async_iterator(invocation)

    async def stream_temporary(
        self, submission: TemporaryGenerationSubmission
    ) -> AsyncIterator[InteractionStreamUpdate]:
        """Stream without writing a session, trace, artifact, call, or telemetry row."""

        history = [dict(item) for item in submission.history]
        if not history or history[-1].get("role") != "user":
            raise ValueError("temporary history must end with a user message")
        if any(
            item.get("role") not in {"user", "assistant"} or not item.get("content")
            for item in history
        ):
            raise ValueError("temporary history contains an invalid message")
        target = self.targets.get(submission.target_id)
        async with target.lock:
            descriptor = await target.prepare()
            request = GenerationRequest(
                request_id=f"temporary-request-{uuid4()}",
                instructions=self.system_prompt,
                input=history,
                sampling=submission.sampling,
                metadata={"purpose": "temporary_interaction"},
                previous_response_id=None,
                store=False,
            )
            yield InteractionStreamUpdate(
                event="start",
                data={
                    "temporary": True,
                    "target": _public_target(descriptor),
                    "durable_storage": False,
                },
            )
            public_parts: list[str] = []
            stream = target.stream(request)
            try:
                terminal: GenerationResult | None = None
                async for event in stream:
                    if event.public_text_delta:
                        public_parts.append(event.public_text_delta)
                        yield InteractionStreamUpdate(
                            event="delta", data={"text": event.public_text_delta}
                        )
                    if event.result is not None:
                        terminal = event.result
                if terminal is None:
                    raise RuntimeError("temporary generation ended without a result")
                streamed = "".join(public_parts)
                if terminal.output_text != streamed:
                    remainder = (
                        terminal.output_text[len(streamed) :]
                        if terminal.output_text.startswith(streamed)
                        else terminal.output_text
                    )
                    if remainder:
                        yield InteractionStreamUpdate(event="delta", data={"text": remainder})
                yield InteractionStreamUpdate(
                    event="completed",
                    data={
                        "temporary": True,
                        "usage": terminal.usage,
                        "latency_ms": terminal.latency_ms,
                    },
                )
            except Exception as exc:
                yield InteractionStreamUpdate(event="error", data={"message": str(exc)})
            finally:
                await _close_async_iterator(stream)

    async def trace_inspector(self, *, trace_id: str, include_restricted: bool) -> dict[str, Any]:
        async with self.database.transaction() as session:
            row = await session.get(InteractionTraceRow, trace_id)
            if row is None:
                raise KeyError(trace_id)
            artifact_ids = {
                "selected_history": row.selected_history_artifact_id,
                "generation_request": row.generation_request_artifact_id,
                "wire_request": row.wire_request_artifact_id,
                "raw_events": row.raw_events_artifact_id,
                "public_response": row.public_response_artifact_id,
                "private_reasoning": row.private_reasoning_artifact_id,
                "generation_result": row.generation_result_artifact_id,
            }
            artifacts: dict[str, dict[str, Any] | None] = {}
            references: dict[str, ArtifactRef] = {}
            for name, artifact_id in artifact_ids.items():
                artifact = await session.get(ArtifactRow, artifact_id) if artifact_id else None
                if artifact is None:
                    artifacts[name] = None
                    continue
                reference = _artifact_reference(artifact)
                references[name] = reference
                artifacts[name] = reference.model_dump(mode="json")
            response: dict[str, Any] = {
                "trace_id": row.trace_id,
                "status": row.status,
                "evidence_class": row.evidence_class,
                "controlled_benchmark_eligible": row.controlled_benchmark_eligible,
                "retention_classification": row.retention_classification,
                "memory_synthesis_status": row.memory_synthesis_status,
                "training_candidate_status": row.training_candidate_status,
                "manifest_digest": row.manifest_digest,
                "manifest": row.manifest_json,
                "usage": row.usage,
                "timing": row.timing,
                "response_metadata": row.response_metadata,
                "raw_event_summary": row.raw_event_summary,
                "artifacts": artifacts,
                "error": row.error,
                "sealed_at": row.sealed_at.isoformat() if row.sealed_at else None,
            }
        if include_restricted:
            restricted: dict[str, Any] = {}
            for name, reference in references.items():
                payload = await artifact_read_bytes(
                    self.artifacts, reference, allow_restricted=True
                )
                if name in {"private_reasoning", "public_response"}:
                    restricted[name] = payload.decode("utf-8")
                else:
                    try:
                        restricted[name] = json.loads(payload)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        restricted[name] = {"base64": base64.b64encode(payload).decode("ascii")}
            response["restricted_research_view"] = restricted
        return response

    async def private_reasoning_view(self, *, trace_id: str) -> dict[str, str | None]:
        async with self.database.transaction() as session:
            trace = await session.get(InteractionTraceRow, trace_id)
            if trace is None:
                raise KeyError(trace_id)
            artifact = (
                await session.get(ArtifactRow, trace.private_reasoning_artifact_id)
                if trace.private_reasoning_artifact_id is not None
                else None
            )
            reference = _artifact_reference(artifact) if artifact is not None else None
        reasoning = (
            (
                await artifact_read_bytes(
                    self.artifacts,
                    reference,
                    allow_restricted=True,
                )
            ).decode("utf-8")
            if reference is not None
            else None
        )
        return {"trace_id": trace_id, "private_reasoning": reasoning}

    async def _prepare_turn(
        self, *, session_id: str, submission: GenerationSubmission
    ) -> _PreparedTurn:
        async with self.database.transaction() as session:
            consent = await self.store.current_consent(session, session_id=session_id)
            source_turn: InteractionTurnRow | None = None
            source_manifest: ExploratoryInteractionManifest | None = None
            parent_message_id = submission.parent_message_id
            content = submission.content
            if submission.mode in {InteractionMode.RETRY, InteractionMode.REPLAY}:
                if submission.source_turn_id is None:
                    raise ValueError("retry and replay require a source turn")
                source_turn = await session.get(InteractionTurnRow, submission.source_turn_id)
                if source_turn is None or source_turn.session_id != session_id:
                    raise ValueError("source turn is not in the selected session")
                source_user = await session.get(InteractionMessageRow, source_turn.user_message_id)
                source_trace = await session.get(InteractionTraceRow, source_turn.trace_id)
                if source_user is None or source_trace is None:
                    raise RuntimeError("source turn lost its message or manifest")
                parent_message_id = source_user.parent_message_id
                content = source_user.content if content is None else content
                source_manifest = ExploratoryInteractionManifest.model_validate(
                    source_trace.manifest_json, strict=False
                )
            elif submission.source_turn_id is not None:
                raise ValueError("only retry or replay accepts a source turn")
            if content is None or not content.strip():
                raise ValueError("generation content cannot be empty")
            history = await self.store.selected_history(
                session, session_id=session_id, parent_message_id=parent_message_id
            )
            parent_turn = (
                await self.store.turn_for_message(session, message_id=parent_message_id)
                if parent_message_id is not None
                else None
            )
            if parent_message_id is not None:
                parent = history[-1]
                if parent.role != "assistant":
                    raise ValueError("new interaction branches must descend from an assistant")
            parent_manifest_digest: str | None = None
            if parent_turn is not None:
                parent_trace = await session.get(InteractionTraceRow, parent_turn.trace_id)
                parent_manifest_digest = (
                    parent_trace.manifest_digest if parent_trace is not None else None
                )
            return _PreparedTurn(
                session_id=session_id,
                turn_id=f"interaction-turn-{uuid4()}",
                trace_id=f"interaction-trace-{uuid4()}",
                request_id=f"interaction-request-{uuid4()}",
                user_message_id=f"interaction-message-{uuid4()}",
                user_content=content.strip(),
                parent_message_id=parent_message_id,
                parent_turn_id=parent_turn.turn_id if parent_turn is not None else None,
                source_turn_id=source_turn.turn_id if source_turn is not None else None,
                history_rows=history,
                parent_manifest_digest=parent_manifest_digest,
                source_manifest=source_manifest,
                consent=consent,
            )

    async def _build_manifest_and_request(
        self,
        *,
        prepared: _PreparedTurn,
        submission: GenerationSubmission,
        descriptor: StudentTargetDescriptor,
    ) -> tuple[GenerationRequest, ExploratoryInteractionManifest, ArtifactRef, ArtifactRef]:
        selected = tuple(
            SelectedHistoryMessage(
                message_id=row.message_id,
                parent_message_id=row.parent_message_id,
                role=row.role,  # type: ignore[arg-type]
                content_digest=row.content_digest,
            )
            for row in prepared.history_rows
        )
        selected_history_digest = sha256_digest([item.model_dump(mode="json") for item in selected])
        rendered_input = [
            {"role": row.role, "content": row.content} for row in prepared.history_rows
        ] + [{"role": "user", "content": prepared.user_content}]
        rendered_context_digest = sha256_digest(
            {"instructions": self.system_prompt, "input": rendered_input}
        )
        prompt_identity = VersionedComponentIdentity(
            component_id="padawan.interaction.system_prompt",
            version="1.0.0",
            digest=sha256_digest(self.system_prompt),
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="Exact server-side Interaction Lab system prompt digest.",
        )
        no_tools = VersionedComponentIdentity(
            component_id="padawan.interaction.no_tools",
            version="1.0.0",
            digest=sha256_digest([]),
            evidence_status=IdentityEvidenceStatus.PINNED,
            evidence="No tools are exposed in the first Interaction Lab slice.",
        )
        tools = (no_tools,)
        harness = _interaction_harness(descriptor, prompt_identity, tools)
        actual_axes = _changed_axes(
            source=prepared.source_manifest,
            target=descriptor,
            sampling=submission.sampling,
            system_prompt=prompt_identity,
            tools=tools,
            rendered_context_digest=rendered_context_digest,
            memory_snapshot_digest=None,
        )
        declared_axes = tuple(
            sorted(set(submission.declared_axis_changes), key=lambda axis: axis.value)
        )
        manifest = ExploratoryInteractionManifest(
            trace_id=prepared.trace_id,
            session_id=prepared.session_id,
            turn_id=prepared.turn_id,
            mode=submission.mode,
            parent_turn_id=prepared.parent_turn_id,
            parent_manifest_digest=prepared.parent_manifest_digest,
            comparison_source_trace_id=(
                prepared.source_manifest.trace_id if prepared.source_manifest is not None else None
            ),
            comparison_source_manifest_digest=(
                sha256_digest(prepared.source_manifest.model_dump(mode="json"))
                if prepared.source_manifest is not None
                else None
            ),
            target=descriptor,
            harness_profile=harness,
            selected_history=selected,
            selected_history_digest=selected_history_digest,
            input_message_digest=sha256_digest(prepared.user_content),
            rendered_context_digest=rendered_context_digest,
            memory_snapshot_digest=None,
            system_prompt=prompt_identity,
            tools=tools,
            sampling=submission.sampling,
            declared_axis_changes=declared_axes,
            actual_axis_changes=actual_axes,
            consent=prepared.consent,
            retention_classification=prepared.consent.retention_classification,
            created_at=datetime.now(UTC),
        )
        request = GenerationRequest(
            request_id=prepared.request_id,
            instructions=self.system_prompt,
            input=rendered_input,
            sampling=submission.sampling,
            metadata={
                "evidence_class": manifest.evidence_class,
                "purpose": "exploratory_interaction",
                "target_id": descriptor.target_id,
                "trace_id": prepared.trace_id,
            },
            previous_response_id=None,
            store=False,
        )
        history_payload = {
            "selected_history": [
                {
                    **item.model_dump(mode="json"),
                    "content": row.content,
                }
                for item, row in zip(selected, prepared.history_rows, strict=True)
            ],
            "current_user": {
                "message_id": prepared.user_message_id,
                "parent_message_id": prepared.parent_message_id,
                "role": "user",
                "content": prepared.user_content,
                "content_digest": sha256_digest(prepared.user_content),
            },
        }
        history_ref = await artifact_put_text(
            self.artifacts,
            json.dumps(history_payload, sort_keys=True, separators=(",", ":")),
            media_type="application/vnd.padawan.interaction-history+json",
            restricted=True,
            raw_data=True,
        )
        request_ref = await artifact_put_text(
            self.artifacts,
            request.model_dump_json(),
            media_type="application/json",
            restricted=True,
            raw_data=True,
        )
        return request, manifest, history_ref, request_ref

    async def _seal_completed_trace(
        self,
        *,
        prepared: _PreparedTurn,
        manifest: ExploratoryInteractionManifest,
        manifest_digest: str,
        history_ref: ArtifactRef,
        request_ref: ArtifactRef,
        result: GenerationResult,
        requested_at: datetime,
        first_event_at: datetime | None,
        first_public_at: datetime | None,
        event_records: list[dict[str, Any]],
        event_summary: list[dict[str, Any]],
    ) -> dict[str, Any]:
        completed_at = datetime.now(UTC)
        wire_request_ref = await artifact_put_bytes(
            self.artifacts,
            result.raw_request,
            media_type="application/json",
            restricted=True,
            raw_data=True,
        )
        raw_events_ref = await artifact_put_text(
            self.artifacts,
            json.dumps(
                {
                    "events": event_records,
                    "raw_response_base64": base64.b64encode(result.raw_response).decode("ascii"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            media_type="application/vnd.padawan.provider-events+json",
            restricted=True,
            raw_data=True,
        )
        public_ref = await artifact_put_text(
            self.artifacts,
            result.output_text,
            media_type="text/plain; charset=utf-8",
            restricted=True,
            raw_data=False,
        )
        private_ref = (
            await artifact_put_text(
                self.artifacts,
                result.private_reasoning,
                media_type="application/vnd.padawan.private-reasoning",
                restricted=True,
                raw_data=True,
            )
            if result.private_reasoning
            else None
        )
        async with self.database.transaction() as session:
            call = await session.get(ExternalCallRow, prepared.request_id)
            if call is None or call.response_artifact_id is None:
                raise RuntimeError("completed interaction invocation lost its result artifact")
            artifact = await session.get(ArtifactRow, call.response_artifact_id)
            if artifact is None:
                raise RuntimeError("generation result artifact metadata is missing")
            result_ref = _artifact_reference(artifact)
        timing = InteractionTraceTiming(
            requested_at=requested_at,
            first_event_at=first_event_at,
            first_public_token_at=first_public_at,
            completed_at=completed_at,
            total_latency_ms=max(0.0, (completed_at - requested_at).total_seconds() * 1000),
            time_to_first_event_ms=(
                max(0.0, (first_event_at - requested_at).total_seconds() * 1000)
                if first_event_at is not None
                else None
            ),
            time_to_first_public_token_ms=(
                max(0.0, (first_public_at - requested_at).total_seconds() * 1000)
                if first_public_at is not None
                else None
            ),
        )
        trace = InteractionTraceRecord(
            trace_id=prepared.trace_id,
            manifest_digest=manifest_digest,
            manifest=manifest,
            request_id=prepared.request_id,
            response_id=result.response_id,
            provider=result.provider,
            model_id=result.model_id,
            protocol=result.protocol,
            finish_reason=result.finish_reason,
            usage=result.usage,
            timing=timing,
            artifacts=InteractionTraceArtifacts(
                selected_history=history_ref,
                generation_request=request_ref,
                wire_request=wire_request_ref,
                raw_events=raw_events_ref,
                public_response=public_ref,
                generation_result=result_ref,
                private_reasoning=private_ref,
            ),
            capabilities=result.capabilities.model_dump(mode="json"),
            serving_telemetry=result.telemetry,
            raw_event_summary=tuple(event_summary),
            retention_classification=manifest.retention_classification,
            sealed_at=completed_at,
        )
        assistant_message_id = f"interaction-message-{uuid4()}"
        async with self.database.transaction() as session:
            assistant = await self.store.complete_turn(
                session,
                turn_id=prepared.turn_id,
                assistant_message_id=assistant_message_id,
                assistant_content=result.output_text,
                trace_record=trace,
            )
        return assistant.model_dump(mode="json")

    async def _seal_failure(
        self,
        *,
        prepared: _PreparedTurn,
        error: dict[str, object],
        status: Literal["failed", "cancelled"],
        requested_at: datetime,
        first_event_at: datetime | None,
        first_public_at: datetime | None,
        event_records: list[dict[str, Any]],
        event_summary: list[dict[str, Any]],
    ) -> None:
        sealed_at = datetime.now(UTC)
        raw_events_ref = await artifact_put_text(
            self.artifacts,
            json.dumps(
                {"events": event_records, "terminal_error": error},
                sort_keys=True,
                separators=(",", ":"),
            ),
            media_type="application/vnd.padawan.provider-events+json",
            restricted=True,
            raw_data=True,
        )
        async with self.database.transaction() as session:
            await self.store.catalog.register(session, raw_events_ref)
            await self.store.catalog.reference(
                session,
                raw_events_ref,
                owner_type="interaction_trace_raw_events",
                owner_id=prepared.trace_id,
            )
            trace = await session.get(InteractionTraceRow, prepared.trace_id)
            if trace is None:
                raise KeyError(prepared.trace_id)
            trace.raw_events_artifact_id = raw_events_ref.artifact_id
            trace.raw_event_summary = event_summary
            trace.timing = InteractionTraceTiming(
                requested_at=requested_at,
                first_event_at=first_event_at,
                first_public_token_at=first_public_at,
                completed_at=sealed_at,
                total_latency_ms=max(0.0, (sealed_at - requested_at).total_seconds() * 1000),
                time_to_first_event_ms=(
                    max(0.0, (first_event_at - requested_at).total_seconds() * 1000)
                    if first_event_at is not None
                    else None
                ),
                time_to_first_public_token_ms=(
                    max(0.0, (first_public_at - requested_at).total_seconds() * 1000)
                    if first_public_at is not None
                    else None
                ),
            ).model_dump(mode="json")
            await self.store.fail_turn(
                session,
                turn_id=prepared.turn_id,
                trace_id=prepared.trace_id,
                error=error,
                status=status,
                failed_at=sealed_at,
            )


def _changed_axes(
    *,
    source: ExploratoryInteractionManifest | None,
    target: StudentTargetDescriptor,
    sampling: SamplingConfiguration,
    system_prompt: VersionedComponentIdentity,
    tools: tuple[VersionedComponentIdentity, ...],
    rendered_context_digest: str,
    memory_snapshot_digest: str | None,
) -> tuple[InteractionAxis, ...]:
    if source is None:
        return ()
    axes: list[InteractionAxis] = []
    if source.target != target:
        axes.append(InteractionAxis.STUDENT_TARGET)
    if source.sampling != sampling:
        axes.append(InteractionAxis.SAMPLING)
    if source.system_prompt != system_prompt:
        axes.append(InteractionAxis.SYSTEM_PROMPT)
    if source.tools != tools:
        axes.append(InteractionAxis.TOOLS)
    if source.rendered_context_digest != rendered_context_digest:
        axes.append(InteractionAxis.RENDERED_CONTEXT)
    if source.memory_snapshot_digest != memory_snapshot_digest:
        axes.append(InteractionAxis.MEMORY_SNAPSHOT)
    return tuple(sorted(axes, key=lambda axis: axis.value))


def _interaction_harness(
    target: StudentTargetDescriptor,
    prompt: VersionedComponentIdentity,
    tools: tuple[VersionedComponentIdentity, ...],
) -> HarnessProfile:
    profile_seed = {
        "target_context": target.configured_context_window_tokens,
        "target_input_limit": target.effective_input_limit_tokens,
        "prompt": prompt.model_dump(mode="json"),
        "tools": [item.model_dump(mode="json") for item in tools],
    }
    return HarnessProfile(
        profile_id="padawan.interaction.explicit-history",
        version=f"1.0.0+{sha256_digest(profile_seed)[7:19]}",
        tier="exploratory_interaction",
        purpose="human_student_interaction_and_trace_capture",
        continuation=ContinuationPolicy(
            continuation_mode="none",
            response_storage_enabled=False,
            previous_response_id_enabled=False,
            reasoning_retention_enabled=False,
            reasoning_retention_mode="none",
            private_reasoning_capture_enabled=target.private_reasoning_capture_enabled,
            private_reasoning_used_as_context=False,
        ),
        context=ContextPolicy(
            policy_id="padawan.interaction.explicit-ancestry",
            version="1.0.0",
            configured_context_window_tokens=target.configured_context_window_tokens,
            effective_input_limit_tokens=target.effective_input_limit_tokens,
            context_limit_evidence="Bound to the selected target descriptor for this turn.",
            token_counting_mode="provider_reported_after_generation",
            history_selection="exact_parent_message_ancestry",
            truncation_enabled=False,
            truncation_strategy="none",
            compaction_enabled=False,
            compaction_strategy="none",
            compactor=None,
        ),
        prompt_templates=(prompt,),
        tools=tools,
        budgets=HarnessBudgets(
            actions=_limit(BudgetDisposition.CAPPED, "turn", "actions", 1),
            input_tokens=_limit(BudgetDisposition.UNBOUNDED, "request", "tokens"),
            output_tokens=_limit(BudgetDisposition.CAPPED, "request", "tokens", 65_536),
            latency=_limit(BudgetDisposition.UNBOUNDED, "request", "seconds"),
            wall_time=_limit(BudgetDisposition.UNBOUNDED, "turn", "seconds"),
            retries=_limit(BudgetDisposition.CAPPED, "request", "retries", 1),
            cost=_limit(BudgetDisposition.NOT_APPLICABLE, "turn", "usd"),
        ),
        instrumentation=ResearchInstrumentationSeams(),
        created_at=_HARNESS_RELEASED_AT,
    )


def _limit(
    disposition: BudgetDisposition,
    scope: str,
    unit: str,
    value: float | None = None,
) -> BudgetLimit:
    return BudgetLimit(disposition=disposition, scope=scope, unit=unit, value=value)


def _artifact_reference(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef.model_validate(
        {
            "artifact_id": row.artifact_id,
            "uri": row.uri,
            "digest": row.digest,
            "media_type": row.media_type,
            "size_bytes": row.size_bytes,
            "restricted": row.restricted,
            "raw_data": row.raw_data,
        },
        strict=False,
    )


def _public_target(descriptor: StudentTargetDescriptor) -> dict[str, Any]:
    return {
        "target_id": descriptor.target_id,
        "display_name": descriptor.display_name,
        "provider": descriptor.provider,
        "model_id": descriptor.model.model_id,
        "checkpoint": descriptor.model.checkpoint.model_dump(mode="json"),
        "quantization": descriptor.model.quantization.model_dump(mode="json"),
        "runtime": descriptor.model.runtime.model_dump(mode="json"),
        "serving_artifact": descriptor.model.serving_artifact.model_dump(mode="json"),
        "transport_artifact": (
            descriptor.model.transport_artifact.model_dump(mode="json")
            if descriptor.model.transport_artifact is not None
            else None
        ),
        "serving_path": descriptor.serving_path.model_dump(mode="json"),
    }


async def _close_async_iterator(iterator: AsyncIterator[Any]) -> None:
    close = getattr(iterator, "aclose", None)
    if close is not None:
        await close()
