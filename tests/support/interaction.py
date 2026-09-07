"""Shared interaction fixture setup."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from padawan.adapters.base import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamEvent,
    ModelProviderError,
)
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.interaction.contracts import (
    ServingPathIdentity,
    StudentTargetDescriptor,
)
from padawan.interaction.service import (
    GenerationSubmission,
    InteractionService,
)
from padawan.interaction.store import InteractionStore
from padawan.interaction.targets import ManagedStudentTarget, StudentTargetRegistry
from padawan.models.contracts import (
    Capability,
    CapabilityAvailability,
    ResearchRole,
    RuntimeCapabilities,
)
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    IdentityEvidenceStatus,
    ModelServingIdentity,
    VersionedComponentIdentity,
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


async def _conversation(
    database: Any, store: InteractionStore, session_id: str
) -> dict[str, object]:
    async with database.transaction() as session:
        return await store.conversation(session, session_id=session_id)
