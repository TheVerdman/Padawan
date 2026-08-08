from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, Literal

import httpx

from padawan.adapters.base import (
    GenerationRequest,
    GenerationResult,
    ModelProviderError,
)
from padawan.models.contracts import (
    Capability,
    CapabilityAvailability,
    RuntimeCapabilities,
)

ProtocolName = Literal["responses", "chat_completions", "completions", "auto"]


class OpenAICompatibleClient:
    """Real OpenAI-shaped HTTP client with Responses-first, explicit compatibility fallback."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        provider: str = "openai_compatible",
        protocol: ProtocolName = "responses",
        allow_legacy_fallback: bool = False,
        timeout_seconds: float = 120.0,
        retry_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
        capabilities_override: RuntimeCapabilities | None = None,
    ) -> None:
        if not model:
            raise ValueError("model is required")
        if retry_attempts <= 0:
            raise ValueError("retry_attempts must be positive")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider = provider
        self.protocol = protocol
        self.allow_legacy_fallback = allow_legacy_fallback
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self._owned_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
        self.capabilities_override = capabilities_override

    async def close(self) -> None:
        if self._owned_client:
            await self.client.aclose()

    async def negotiate(self) -> dict[str, Any]:
        """Read server identity without triggering a generation."""

        headers = self._headers(request_id="capability-negotiation")
        models_response = await self.client.get(f"{self.base_url}/v1/models", headers=headers)
        if models_response.status_code >= 400:
            raise ModelProviderError(
                "model capability negotiation failed",
                provider=self.provider,
                status_code=models_response.status_code,
                retryable=models_response.status_code in {408, 409, 429}
                or models_response.status_code >= 500,
                response_body=models_response.content,
            )
        try:
            model_payload = models_response.json()
        except ValueError as exc:
            raise ModelProviderError(
                f"models endpoint returned invalid JSON: {exc}", provider=self.provider
            ) from exc
        extension: dict[str, Any] | None = None
        try:
            extension_response = await self.client.get(
                f"{self.base_url}/v1/padawan/capabilities", headers=headers
            )
            if extension_response.status_code == 200:
                parsed = extension_response.json()
                extension = parsed if isinstance(parsed, dict) else None
        except (httpx.HTTPError, ValueError):
            extension = None
        return {
            "models": model_payload,
            "padawan_extension": extension,
            "configured_protocol": self.protocol,
            "legacy_fallback_enabled": self.allow_legacy_fallback,
        }

    async def generate(
        self,
        request: GenerationRequest,
        *,
        stream: bool = False,
    ) -> GenerationResult:
        protocol = "responses" if self.protocol == "auto" else self.protocol
        try:
            return await self._generate_with_protocol(request, protocol=protocol, stream=stream)
        except ModelProviderError as exc:
            fallback_allowed = (
                protocol == "responses"
                and self.allow_legacy_fallback
                and exc.status_code in {404, 405, 422, 501}
            )
            if not fallback_allowed:
                raise
            result = await self._generate_with_protocol(
                request, protocol="chat_completions", stream=stream
            )
            return GenerationResult(
                **{
                    **result.__dict__,
                    "provider_metadata": {
                        **result.provider_metadata,
                        "explicit_legacy_fallback": True,
                        "responses_failure_status": exc.status_code,
                    },
                }
            )

    async def _generate_with_protocol(
        self,
        request: GenerationRequest,
        *,
        protocol: str,
        stream: bool,
    ) -> GenerationResult:
        if protocol == "responses":
            path = "/v1/responses"
            payload = _responses_payload(request, self.model, stream=stream)
        elif protocol == "chat_completions":
            path = "/v1/chat/completions"
            payload = _chat_payload(request, self.model, stream=stream)
        elif protocol == "completions":
            path = "/v1/completions"
            payload = _completion_payload(request, self.model, stream=stream)
        else:
            raise ValueError(f"unsupported protocol: {protocol}")
        raw_request = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        started = time.monotonic()
        response_json: dict[str, Any]
        raw_response: bytes
        last_error: ModelProviderError | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                if stream:
                    response_json, raw_response = await self._stream_request(
                        path, payload, request_id=request.request_id, protocol=protocol
                    )
                else:
                    response_json, raw_response = await self._request(
                        path, payload, request_id=request.request_id
                    )
                break
            except ModelProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.retry_attempts:
                    raise
                await asyncio.sleep(min(8.0, 0.25 * (2 ** (attempt - 1))))
        else:
            if last_error is None:
                raise AssertionError("retry loop exited without response or error")
            raise last_error
        latency_ms = (time.monotonic() - started) * 1000.0
        if protocol == "responses":
            parsed = _parse_responses(response_json)
        elif protocol == "chat_completions":
            parsed = _parse_chat(response_json)
        else:
            parsed = _parse_completion(response_json)
        capabilities = self.capabilities_override or _default_capabilities(
            protocol=protocol, streaming=stream, response=response_json
        )
        return GenerationResult(
            request_id=request.request_id,
            response_id=parsed["response_id"],
            provider=self.provider,
            model_id=str(response_json.get("model") or self.model),
            protocol=protocol,
            output_text=parsed["output_text"],
            raw_request=raw_request,
            raw_response=raw_response,
            usage=parsed["usage"],
            token_ids=parsed["token_ids"],
            token_logprobs=parsed["token_logprobs"],
            private_reasoning=None,
            reasoning_summary=parsed["reasoning_summary"],
            finish_reason=parsed["finish_reason"],
            latency_ms=latency_ms,
            capabilities=capabilities,
            telemetry=parsed["telemetry"],
            provider_metadata={"streamed": stream},
        )

    async def _request(
        self, path: str, payload: dict[str, Any], *, request_id: str
    ) -> tuple[dict[str, Any], bytes]:
        try:
            response = await self.client.post(
                f"{self.base_url}{path}",
                headers=self._headers(request_id=request_id),
                json=payload,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ModelProviderError(str(exc), provider=self.provider, retryable=True) from exc
        if response.status_code >= 400:
            raise ModelProviderError(
                f"provider returned HTTP {response.status_code}",
                provider=self.provider,
                status_code=response.status_code,
                retryable=response.status_code in {408, 409, 429} or response.status_code >= 500,
                response_body=response.content,
            )
        try:
            payload_json = response.json()
        except ValueError as exc:
            raise ModelProviderError(
                f"provider returned invalid JSON: {exc}",
                provider=self.provider,
                status_code=response.status_code,
                response_body=response.content,
            ) from exc
        if not isinstance(payload_json, dict):
            raise ModelProviderError("provider response must be an object", provider=self.provider)
        return payload_json, response.content

    async def _stream_request(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        request_id: str,
        protocol: str,
    ) -> tuple[dict[str, Any], bytes]:
        events: list[dict[str, Any]] = []
        raw_parts: list[bytes] = []
        try:
            async with self.client.stream(
                "POST",
                f"{self.base_url}{path}",
                headers=self._headers(request_id=request_id),
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise ModelProviderError(
                        f"provider returned HTTP {response.status_code}",
                        provider=self.provider,
                        status_code=response.status_code,
                        retryable=response.status_code in {408, 409, 429}
                        or response.status_code >= 500,
                        response_body=body,
                    )
                async for event in _iter_sse(response):
                    raw_parts.append(event[0])
                    if event[1] is not None:
                        events.append(event[1])
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ModelProviderError(str(exc), provider=self.provider, retryable=True) from exc
        if protocol == "responses":
            completed = next(
                (
                    item.get("response")
                    for item in reversed(events)
                    if item.get("type") == "response.completed"
                ),
                None,
            )
            if not isinstance(completed, dict):
                raise ModelProviderError(
                    "Responses stream ended without response.completed", provider=self.provider
                )
            return completed, b"".join(raw_parts)
        return _collapse_legacy_stream(events, protocol=protocol), b"".join(raw_parts)

    def _headers(self, *, request_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Idempotency-Key": request_id,
            "X-Request-ID": request_id,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _responses_payload(request: GenerationRequest, model: str, *, stream: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "instructions": request.instructions,
        "input": request.input,
        "max_output_tokens": request.sampling.max_output_tokens,
        "store": request.store,
        "stream": stream,
        "metadata": request.metadata,
    }
    if request.previous_response_id is not None:
        payload["previous_response_id"] = request.previous_response_id
    if request.sampling.temperature is not None:
        payload["temperature"] = request.sampling.temperature
    if request.sampling.top_p is not None:
        payload["top_p"] = request.sampling.top_p
    if request.sampling.top_logprobs is not None:
        payload["top_logprobs"] = request.sampling.top_logprobs
        payload["include"] = ["message.output_text.logprobs"]
    if request.json_schema is not None:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": request.schema_name or "padawan_response",
                "strict": True,
                "schema": _strict_json_schema(request.json_schema),
            }
        }
    return payload


def _chat_payload(request: GenerationRequest, model: str, *, stream: bool) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    if request.instructions:
        messages.append({"role": "system", "content": request.instructions})
    if isinstance(request.input, str):
        messages.append({"role": "user", "content": request.input})
    else:
        messages.extend(request.input)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": request.sampling.max_output_tokens,
        "stream": stream,
    }
    if request.sampling.temperature is not None:
        payload["temperature"] = request.sampling.temperature
    if request.sampling.top_p is not None:
        payload["top_p"] = request.sampling.top_p
    if request.sampling.seed is not None:
        payload["seed"] = request.sampling.seed
    if request.sampling.top_logprobs is not None:
        payload["logprobs"] = True
        payload["top_logprobs"] = request.sampling.top_logprobs
    if request.json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name or "padawan_response",
                "strict": True,
                "schema": _strict_json_schema(request.json_schema),
            },
        }
    return payload


def _completion_payload(request: GenerationRequest, model: str, *, stream: bool) -> dict[str, Any]:
    if not isinstance(request.input, str):
        raise ValueError("Completions compatibility requires a string prompt")
    return {
        "model": model,
        "prompt": f"{request.instructions}\n\n{request.input}".strip(),
        "max_tokens": request.sampling.max_output_tokens,
        "temperature": request.sampling.temperature or 0,
        "top_p": request.sampling.top_p or 1,
        "seed": request.sampling.seed,
        "logprobs": request.sampling.top_logprobs,
        "stream": stream,
    }


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a Pydantic schema to the strict Structured Outputs subset.

    Strict Responses schemas require every object property in ``required`` and
    reject default-valued schema keywords. Nullable fields retain their null
    union; collection defaults become fields the model must emit. The input
    schema remains unchanged.
    """

    normalized = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties)
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(normalized)
    return normalized


def _parse_responses(payload: dict[str, Any]) -> dict[str, Any]:
    output_texts: list[str] = []
    summaries: list[str] = []
    logprobs: list[float] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    output_texts.append(str(content.get("text") or ""))
                    for token in content.get("logprobs") or []:
                        if isinstance(token, dict) and isinstance(
                            token.get("logprob"), (int, float)
                        ):
                            logprobs.append(float(token["logprob"]))
        elif item.get("type") == "reasoning":
            for summary in item.get("summary") or []:
                if isinstance(summary, dict) and summary.get("text"):
                    summaries.append(str(summary["text"]))
    usage_value = payload.get("usage")
    usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
    finish_reason = str(payload.get("status")) if payload.get("status") is not None else None
    return {
        "response_id": str(payload["id"]) if payload.get("id") else None,
        "output_text": "".join(output_texts),
        "usage": _normalize_usage(usage),
        "token_ids": _optional_int_tuple(payload.get("output_token_ids")),
        "token_logprobs": tuple(logprobs) if logprobs else None,
        "reasoning_summary": "\n".join(summaries) if summaries else None,
        "finish_reason": finish_reason,
        "telemetry": _optional_dict(payload.get("padawan_telemetry")),
    }


def _parse_chat(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message_value = choice.get("message")
    message: dict[str, Any] = message_value if isinstance(message_value, dict) else {}
    content = message.get("content")
    text = content if isinstance(content, str) else ""
    token_logprobs: list[float] = []
    logprobs_payload = choice.get("logprobs")
    if isinstance(logprobs_payload, dict):
        for token in logprobs_payload.get("content") or []:
            if isinstance(token, dict) and isinstance(token.get("logprob"), (int, float)):
                token_logprobs.append(float(token["logprob"]))
    return {
        "response_id": str(payload["id"]) if payload.get("id") else None,
        "output_text": text,
        "usage": _normalize_usage(_optional_dict(payload.get("usage")) or {}),
        "token_ids": _optional_int_tuple(payload.get("output_token_ids")),
        "token_logprobs": tuple(token_logprobs) if token_logprobs else None,
        "reasoning_summary": None,
        "finish_reason": str(choice.get("finish_reason")) if choice.get("finish_reason") else None,
        "telemetry": _optional_dict(payload.get("padawan_telemetry")),
    }


def _parse_completion(payload: dict[str, Any]) -> dict[str, Any]:
    choices = payload.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    logs = choice.get("logprobs") if isinstance(choice.get("logprobs"), dict) else {}
    raw_logs = logs.get("token_logprobs") if isinstance(logs, dict) else None
    token_logs = (
        tuple(float(value) for value in raw_logs if value is not None)
        if isinstance(raw_logs, list)
        else None
    )
    return {
        "response_id": str(payload["id"]) if payload.get("id") else None,
        "output_text": str(choice.get("text") or ""),
        "usage": _normalize_usage(_optional_dict(payload.get("usage")) or {}),
        "token_ids": _optional_int_tuple(payload.get("output_token_ids")),
        "token_logprobs": token_logs,
        "reasoning_summary": None,
        "finish_reason": str(choice.get("finish_reason")) if choice.get("finish_reason") else None,
        "telemetry": _optional_dict(payload.get("padawan_telemetry")),
    }


def _normalize_usage(usage: dict[str, Any]) -> dict[str, int]:
    mapping = {
        "input_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
        "output_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
        "total_tokens": usage.get("total_tokens", 0),
    }
    return {key: int(value or 0) for key, value in mapping.items()}


def _default_capabilities(
    *, protocol: str, streaming: bool, response: dict[str, Any]
) -> RuntimeCapabilities:
    logprob_present = bool(
        _parse_responses(response)["token_logprobs"]
        if protocol == "responses"
        else _parse_chat(response)["token_logprobs"]
        if protocol == "chat_completions"
        else _parse_completion(response)["token_logprobs"]
    )
    token_ids_present = isinstance(response.get("output_token_ids"), list)
    telemetry_present = isinstance(response.get("padawan_telemetry"), dict)
    return RuntimeCapabilities(
        responses_api=_cap(
            protocol == "responses",
            "request completed through /v1/responses"
            if protocol == "responses"
            else "request used an explicitly configured legacy protocol",
        ),
        streaming=_cap(streaming, "stream enabled" if streaming else "not requested"),
        cancellation=_cap(True, "HTTP stream/request cancellation propagates through asyncio"),
        logprobs=_cap(logprob_present, "returned by server" if logprob_present else "not returned"),
        token_ids=_cap(
            token_ids_present,
            "returned by server extension" if token_ids_present else "not returned",
        ),
        private_reasoning=Capability(
            availability=CapabilityAvailability.UNAVAILABLE,
            reason="provider response did not expose raw private reasoning",
        ),
        reasoning_boundaries=Capability(
            availability=CapabilityAvailability.UNAVAILABLE,
            reason="no raw private channel was exposed",
        ),
        gpu_telemetry=_cap(
            telemetry_present,
            "returned by Padawan server extension" if telemetry_present else "not returned",
        ),
        router_telemetry=Capability(
            availability=CapabilityAvailability.AVAILABLE
            if telemetry_present and "router" in (response.get("padawan_telemetry") or {})
            else CapabilityAvailability.UNAVAILABLE,
            reason="server extension" if telemetry_present else "not returned",
        ),
    )


def _cap(value: bool, reason: str) -> Capability:
    return Capability(
        availability=(
            CapabilityAvailability.AVAILABLE if value else CapabilityAvailability.UNAVAILABLE
        ),
        reason=reason,
    )


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[bytes, dict[str, Any] | None]]:
    async for line in response.aiter_lines():
        raw = (line + "\n").encode("utf-8")
        if not line.startswith("data:"):
            yield raw, None
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            yield raw, None
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ModelProviderError(
                f"invalid SSE JSON: {exc}", provider="openai_compatible"
            ) from exc
        yield raw, parsed if isinstance(parsed, dict) else None


def _collapse_legacy_stream(events: list[dict[str, Any]], *, protocol: str) -> dict[str, Any]:
    text_parts: list[str] = []
    response_id: str | None = None
    model: str | None = None
    finish_reason: str | None = None
    for event in events:
        response_id = str(event.get("id") or response_id or "") or None
        model = str(event.get("model") or model or "") or None
        choices = event.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        if protocol == "chat_completions":
            delta_value = choice.get("delta")
            delta: dict[str, Any] = delta_value if isinstance(delta_value, dict) else {}
            if delta.get("content"):
                text_parts.append(str(delta["content"]))
        elif choice.get("text"):
            text_parts.append(str(choice["text"]))
        if choice.get("finish_reason"):
            finish_reason = str(choice["finish_reason"])
    choice_payload: dict[str, Any]
    if protocol == "chat_completions":
        choice_payload = {
            "message": {"content": "".join(text_parts)},
            "finish_reason": finish_reason,
        }
    else:
        choice_payload = {"text": "".join(text_parts), "finish_reason": finish_reason}
    return {"id": response_id, "model": model, "choices": [choice_payload], "usage": {}}


def _optional_int_tuple(value: Any) -> tuple[int, ...] | None:
    if not isinstance(value, list) or not all(isinstance(item, int) for item in value):
        return None
    return tuple(value)


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None
