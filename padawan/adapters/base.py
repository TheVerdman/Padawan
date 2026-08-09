from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from padawan.models.contracts import RuntimeCapabilities, SamplingConfiguration


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    instructions: str
    input: str | list[dict[str, Any]]
    sampling: SamplingConfiguration
    schema_name: str | None = None
    json_schema: dict[str, Any] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    previous_response_id: str | None = None
    store: bool = False


@dataclass(frozen=True)
class GenerationResult:
    request_id: str
    response_id: str | None
    provider: str
    model_id: str
    protocol: str
    output_text: str
    raw_request: bytes
    raw_response: bytes
    usage: dict[str, int]
    token_ids: tuple[int, ...] | None
    token_logprobs: tuple[float, ...] | None
    private_reasoning: str | None
    reasoning_summary: str | None
    finish_reason: str | None
    latency_ms: float
    capabilities: RuntimeCapabilities
    telemetry: dict[str, Any] | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)


class StudentRuntime(Protocol):
    runtime_id: str
    runtime_version: str
    checkpoint_id: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
        retryable: bool = False,
        response_body: bytes = b"",
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retryable = retryable
        self.response_body = response_body


def rendered_messages(request: GenerationRequest) -> tuple[dict[str, Any], ...]:
    """Return the exact role/message structure supplied to message-oriented transports."""

    messages: list[dict[str, Any]] = []
    if request.instructions:
        messages.append({"role": "developer", "content": request.instructions})
    if isinstance(request.input, str):
        messages.append({"role": "user", "content": request.input})
    else:
        messages.extend(deepcopy(request.input))
    return tuple(messages)


def rendered_prompt(request: GenerationRequest) -> str:
    if isinstance(request.input, str):
        return request.input
    return json.dumps(
        request.input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
