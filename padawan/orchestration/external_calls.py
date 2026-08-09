from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.artifacts.store import (
    ArtifactBackend,
    ArtifactCatalog,
    artifact_put_bytes,
    artifact_put_text,
    artifact_read_bytes,
)
from padawan.models.contracts import ArtifactRef, RuntimeCapabilities
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.tables import ArtifactRow, ExternalCallRow
from padawan.temporal.contracts import OperationStatus
from padawan.temporal.telemetry import OperationTelemetryStore


class GenerationClient(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class IdempotentGenerationExecutor:
    """Persists intent before I/O and returns persisted responses after a crash."""

    def __init__(
        self,
        *,
        database: Database,
        artifacts: ArtifactBackend,
        client: GenerationClient,
        telemetry: OperationTelemetryStore | None = None,
    ) -> None:
        self.database = database
        self.artifacts = artifacts
        self.catalog = ArtifactCatalog(artifacts)
        self.client = client
        self.telemetry = telemetry or OperationTelemetryStore()

    async def execute(
        self,
        *,
        run_id: str,
        purpose: str,
        provider: str,
        request: GenerationRequest,
    ) -> GenerationResult:
        request_hash = sha256_digest(request.model_dump(mode="json"))
        operation_id = _operation_id(request.request_id)
        environment_fingerprint = sha256_digest(
            {
                "provider": provider,
                "operation_type": "model_generation",
            }
        )
        async with self.database.transaction() as session:
            existing = await session.get(ExternalCallRow, request.request_id)
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ValueError("request ID reused with different content")
                if existing.status == "completed" and existing.response_artifact_id:
                    artifact = await session.get(ArtifactRow, existing.response_artifact_id)
                    if artifact is None:
                        raise RuntimeError("persisted response artifact is missing")
                    reference = _artifact_row_to_reference(artifact)
                    payload = await artifact_read_bytes(
                        self.artifacts, reference, allow_restricted=True
                    )
                    return _deserialize_result(payload)
                try:
                    operation = await self.telemetry.get(session, operation_id=operation_id)
                except KeyError:
                    await self.telemetry.create(
                        session,
                        operation_id=operation_id,
                        operation_type="model_generation",
                        environment_fingerprint=environment_fingerprint,
                        workload_class=purpose,
                        workload=_generation_workload(request, provider),
                        run_id=run_id,
                        source_ref=request.request_id,
                        status=OperationStatus.RUNNING,
                        occurred_at=existing.created_at,
                    )
                else:
                    if operation.status == OperationStatus.WAITING:
                        await self.telemetry.transition(
                            session,
                            operation_id=operation_id,
                            status=OperationStatus.RUNNING,
                            detail={"reason": "idempotent external call resumed"},
                        )
            else:
                timestamp = datetime.now(UTC)
                request_ref = await artifact_put_text(
                    self.artifacts,
                    request.model_dump_json(),
                    media_type="application/json",
                    restricted=True,
                    raw_data=True,
                )
                await self.catalog.register(session, request_ref)
                await self.catalog.reference(
                    session,
                    request_ref,
                    owner_type="external_call_request",
                    owner_id=request.request_id,
                )
                session.add(
                    ExternalCallRow(
                        request_id=request.request_id,
                        run_id=run_id,
                        purpose=purpose,
                        provider=provider,
                        request_hash=request_hash,
                        request_artifact_id=request_ref.artifact_id,
                        response_artifact_id=None,
                        provider_response_id=None,
                        status="pending",
                        error=None,
                        created_at=timestamp,
                        completed_at=None,
                    )
                )
                await self.telemetry.create(
                    session,
                    operation_id=operation_id,
                    operation_type="model_generation",
                    environment_fingerprint=environment_fingerprint,
                    workload_class=purpose,
                    workload=_generation_workload(request, provider),
                    run_id=run_id,
                    source_ref=request.request_id,
                    status=OperationStatus.RUNNING,
                    occurred_at=timestamp,
                )

        try:
            result = await self.client.generate(request)
        except ModelProviderError as exc:
            async with self.database.transaction() as session:
                row = await session.get(ExternalCallRow, request.request_id)
                if row is not None:
                    row.status = "failed_retryable" if exc.retryable else "failed_terminal"
                    row.error = {
                        "message": str(exc),
                        "status_code": exc.status_code,
                        "retryable": exc.retryable,
                        "response_digest": sha256_digest(exc.response_body),
                    }
                    await self.telemetry.transition(
                        session,
                        operation_id=operation_id,
                        status=(
                            OperationStatus.WAITING if exc.retryable else OperationStatus.FAILED
                        ),
                        detail={
                            "provider_status_code": exc.status_code,
                            "retryable": exc.retryable,
                            "response_digest": sha256_digest(exc.response_body),
                        },
                    )
            raise

        envelope = _serialize_result(result)
        response_ref = await artifact_put_bytes(
            self.artifacts,
            envelope,
            media_type="application/vnd.padawan.generation-result+json",
            restricted=True,
            raw_data=True,
        )
        async with self.database.transaction() as session:
            row = await session.scalar(
                select(ExternalCallRow)
                .where(ExternalCallRow.request_id == request.request_id)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("external call intent disappeared")
            if row.status == "completed" and row.response_artifact_id:
                persisted = await session.get(ArtifactRow, row.response_artifact_id)
                if persisted is None:
                    raise RuntimeError("completed call lost its response artifact")
                return _deserialize_result(
                    await artifact_read_bytes(
                        self.artifacts,
                        _artifact_row_to_reference(persisted),
                        allow_restricted=True,
                    )
                )
            await self.catalog.register(session, response_ref)
            await self.catalog.reference(
                session,
                response_ref,
                owner_type="external_call_response",
                owner_id=request.request_id,
            )
            row.response_artifact_id = response_ref.artifact_id
            row.provider_response_id = result.response_id
            row.status = "completed"
            row.error = None
            row.completed_at = datetime.now(UTC)
            await self.telemetry.transition(
                session,
                operation_id=operation_id,
                status=OperationStatus.SUCCEEDED,
                occurred_at=row.completed_at,
                detail={
                    "provider_latency_ms": result.latency_ms,
                    "response_id_present": result.response_id is not None,
                },
            )
        return result


def _serialize_result(result: GenerationResult) -> bytes:
    payload = {
        "request_id": result.request_id,
        "response_id": result.response_id,
        "provider": result.provider,
        "model_id": result.model_id,
        "protocol": result.protocol,
        "output_text": result.output_text,
        "raw_request_base64": base64.b64encode(result.raw_request).decode("ascii"),
        "raw_response_base64": base64.b64encode(result.raw_response).decode("ascii"),
        "usage": result.usage,
        "token_ids": result.token_ids,
        "token_logprobs": result.token_logprobs,
        "private_reasoning": result.private_reasoning,
        "reasoning_summary": result.reasoning_summary,
        "finish_reason": result.finish_reason,
        "latency_ms": result.latency_ms,
        "capabilities": result.capabilities.model_dump(mode="json"),
        "telemetry": result.telemetry,
        "provider_metadata": result.provider_metadata,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _deserialize_result(payload: bytes) -> GenerationResult:
    data = json.loads(payload)
    return GenerationResult(
        request_id=str(data["request_id"]),
        response_id=str(data["response_id"]) if data.get("response_id") else None,
        provider=str(data["provider"]),
        model_id=str(data["model_id"]),
        protocol=str(data["protocol"]),
        output_text=str(data["output_text"]),
        raw_request=base64.b64decode(data["raw_request_base64"]),
        raw_response=base64.b64decode(data["raw_response_base64"]),
        usage={str(key): int(value) for key, value in data["usage"].items()},
        token_ids=tuple(data["token_ids"]) if data.get("token_ids") is not None else None,
        token_logprobs=(
            tuple(float(value) for value in data["token_logprobs"])
            if data.get("token_logprobs") is not None
            else None
        ),
        private_reasoning=data.get("private_reasoning"),
        reasoning_summary=data.get("reasoning_summary"),
        finish_reason=data.get("finish_reason"),
        latency_ms=float(data["latency_ms"]),
        capabilities=RuntimeCapabilities.model_validate(data["capabilities"], strict=False),
        telemetry=data.get("telemetry"),
        provider_metadata=data.get("provider_metadata") or {},
    )


def _artifact_row_to_reference(row: ArtifactRow) -> ArtifactRef:
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


def _operation_id(request_id: str) -> str:
    return f"operation-generation-{sha256_digest(request_id)[7:39]}"


def _generation_workload(request: GenerationRequest, provider: str) -> dict[str, object]:
    if isinstance(request.input, str):
        input_kind = "text"
        input_size = len(request.input)
        message_count = 1
    else:
        input_kind = "messages"
        input_size = len(json.dumps(request.input, sort_keys=True))
        message_count = len(request.input)
    return {
        "provider": provider,
        "input_kind": input_kind,
        "input_size_chars": input_size,
        "message_count": message_count,
        "max_output_tokens": request.sampling.max_output_tokens,
        "schema_constrained": request.json_schema is not None,
    }
