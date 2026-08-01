from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.models.contracts import (
    Capability,
    CapabilityAvailability,
    RuntimeCapabilities,
)


class AnthropicMessagesClient:
    """Concrete second-provider client; downstream Pydantic remains the schema authority."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 120.0,
        retry_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model or not api_key:
            raise ValueError("Anthropic model and API key are required")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.retry_attempts = retry_attempts
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        messages = (
            [{"role": "user", "content": request.input}]
            if isinstance(request.input, str)
            else [item for item in request.input if item.get("role") in {"user", "assistant"}]
        )
        system = request.instructions
        if request.json_schema is not None:
            system += (
                "\nReturn only JSON conforming to this schema. "
                "Application validation is authoritative:\n"
                + json.dumps(request.json_schema, sort_keys=True)
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "messages": messages,
            "max_tokens": request.sampling.max_output_tokens,
        }
        if request.sampling.temperature is not None:
            payload["temperature"] = request.sampling.temperature
        if request.sampling.top_p is not None:
            payload["top_p"] = request.sampling.top_p
        raw_request = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        started = time.monotonic()
        response: httpx.Response | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                response = await self.client.post(
                    f"{self.base_url}/v1/messages",
                    headers={
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                        "Idempotency-Key": request.request_id,
                    },
                    json=payload,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.retry_attempts:
                    raise ModelProviderError(
                        str(exc), provider="anthropic", retryable=True
                    ) from exc
                await asyncio.sleep(min(8.0, 0.25 * (2 ** (attempt - 1))))
                continue
            if response.status_code < 400:
                break
            retryable = response.status_code in {408, 409, 429} or response.status_code >= 500
            if not retryable or attempt >= self.retry_attempts:
                raise ModelProviderError(
                    f"Anthropic returned HTTP {response.status_code}",
                    provider="anthropic",
                    status_code=response.status_code,
                    retryable=retryable,
                    response_body=response.content,
                )
            await asyncio.sleep(min(8.0, 0.25 * (2 ** (attempt - 1))))
        if response is None:
            raise AssertionError("Anthropic retry loop produced no response")
        try:
            data = response.json()
        except ValueError as exc:
            raise ModelProviderError(
                f"Anthropic returned invalid JSON: {exc}",
                provider="anthropic",
                response_body=response.content,
            ) from exc
        content = data.get("content") if isinstance(data, dict) else []
        output = "".join(
            str(block.get("text") or "")
            for block in content or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = data.get("usage") if isinstance(data, dict) else {}
        return GenerationResult(
            request_id=request.request_id,
            response_id=str(data.get("id")) if data.get("id") else None,
            provider="anthropic",
            model_id=str(data.get("model") or self.model),
            protocol="anthropic_messages",
            output_text=output,
            raw_request=raw_request,
            raw_response=response.content,
            usage={
                "input_tokens": int((usage or {}).get("input_tokens") or 0),
                "output_tokens": int((usage or {}).get("output_tokens") or 0),
                "total_tokens": int((usage or {}).get("input_tokens") or 0)
                + int((usage or {}).get("output_tokens") or 0),
            },
            token_ids=None,
            token_logprobs=None,
            private_reasoning=None,
            reasoning_summary=None,
            finish_reason=str(data.get("stop_reason")) if data.get("stop_reason") else None,
            latency_ms=(time.monotonic() - started) * 1000.0,
            capabilities=_capabilities(),
            telemetry=None,
            provider_metadata={"structured_output_transport": "prompt_plus_application_validation"},
        )


def _capabilities() -> RuntimeCapabilities:
    unavailable = CapabilityAvailability.UNAVAILABLE
    return RuntimeCapabilities(
        responses_api=Capability(availability=unavailable, reason="Anthropic uses /v1/messages"),
        streaming=Capability(availability=unavailable, reason="not implemented in this adapter"),
        cancellation=Capability(
            availability=CapabilityAvailability.AVAILABLE,
            reason="async HTTP cancellation propagates",
        ),
        logprobs=Capability(availability=unavailable, reason="not returned"),
        token_ids=Capability(availability=unavailable, reason="not returned"),
        private_reasoning=Capability(availability=unavailable, reason="not exposed"),
        reasoning_boundaries=Capability(availability=unavailable, reason="not exposed"),
        gpu_telemetry=Capability(availability=unavailable, reason="remote provider"),
        router_telemetry=Capability(availability=unavailable, reason="not exposed"),
    )
