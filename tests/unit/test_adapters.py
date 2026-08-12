from __future__ import annotations

import json

import httpx
import pytest

from padawan.adapters.base import GenerationRequest, ModelProviderError
from padawan.adapters.frontier_anthropic.client import AnthropicMessagesClient
from padawan.adapters.frontier_openai.client import OpenAIResponsesClient
from padawan.adapters.openai_compatible.client import (
    OpenAICompatibleClient,
    _strict_json_schema,
)
from padawan.models.contracts import CapabilityAvailability, SamplingConfiguration
from padawan.teaching.contracts import TeacherOutput


def _request() -> GenerationRequest:
    return GenerationRequest(
        request_id="request-1",
        instructions="Return the contract.",
        input="solve",
        sampling=SamplingConfiguration(
            temperature=0.1,
            top_p=0.9,
            max_output_tokens=200,
            top_logprobs=2,
        ),
        schema_name="answer",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        metadata={"purpose": "test"},
        store=False,
    )


def test_pydantic_schema_is_normalized_for_strict_responses_without_mutation() -> None:
    source = TeacherOutput.model_json_schema()
    normalized = _strict_json_schema(source)

    assert source != normalized
    assert "default" in source["properties"]["error_class"]

    def assert_strict(node: object) -> None:
        if isinstance(node, dict):
            assert "default" not in node
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert node["additionalProperties"] is False
                assert node["required"] == list(properties)
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    assert_strict(normalized)


async def test_responses_api_payload_and_raw_round_trip() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "model": "frontier-model",
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "content": [{"type": "reasoning_text", "text": "provider-private-trace"}],
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"answer":"ok"}',
                                "logprobs": [{"logprob": -0.1}],
                            }
                        ],
                    },
                ],
                "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = OpenAIResponsesClient(model="frontier-model", api_key="test-key", client=transport)
        result = await client.generate(_request())
    sent = json.loads(cast_bytes(captured["body"]))
    assert captured["path"] == "/v1/responses"
    assert sent["store"] is False
    assert sent["text"]["format"]["type"] == "json_schema"
    assert sent["text"]["format"]["strict"] is True
    assert result.protocol == "responses"
    assert result.output_text == '{"answer":"ok"}'
    assert json.loads(result.raw_request) == sent
    assert json.loads(result.raw_response)["id"] == "resp_1"
    assert result.capabilities.private_reasoning.availability == (
        CapabilityAvailability.UNAVAILABLE
    )
    assert result.private_reasoning is None


async def test_responses_payload_transmits_declared_effort_and_tool_surface() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "id": "resp-effort",
                "model": "inkling-test",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    request = GenerationRequest(
        request_id="atlas-effort-tool",
        instructions="Use the declared harness.",
        input="probe",
        sampling=SamplingConfiguration(
            max_output_tokens=64,
            reasoning_effort="max",
        ),
        tools=(
            {
                "type": "function",
                "name": "lookup",
                "description": "Lookup deterministic evidence.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        ),
        tool_choice="auto",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = OpenAICompatibleClient(
            base_url="http://local",
            model="inkling-test",
            client=transport,
            retry_attempts=1,
        )
        await client.generate(request)
    sent = json.loads(cast_bytes(captured["body"]))
    assert sent["reasoning"] == {"effort": "max"}
    assert sent["tool_choice"] == "auto"
    assert sent["tools"] == list(request.tools)


async def test_legacy_fallback_requires_explicit_opt_in() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/responses":
            return httpx.Response(404, json={"error": "not supported"})
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "model": "local",
                "choices": [
                    {"message": {"content": '{"answer":"legacy"}'}, "finish_reason": "stop"}
                ],
                "usage": {},
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        strict_client = OpenAICompatibleClient(
            base_url="http://local",
            model="local",
            client=http_client,
            protocol="responses",
            allow_legacy_fallback=False,
            retry_attempts=1,
        )
        with pytest.raises(ModelProviderError):
            await strict_client.generate(_request())
        fallback_client = OpenAICompatibleClient(
            base_url="http://local",
            model="local",
            client=http_client,
            protocol="responses",
            allow_legacy_fallback=True,
            retry_attempts=1,
        )
        result = await fallback_client.generate(_request())
    assert paths == ["/v1/responses", "/v1/responses", "/v1/chat/completions"]
    assert result.protocol == "chat_completions"
    assert result.provider_metadata["explicit_legacy_fallback"] is True


async def test_responses_stream_requires_and_preserves_completed_event() -> None:
    captured_headers: dict[str, str] = {}
    completed = {
        "type": "response.completed",
        "response": {
            "id": "resp-stream",
            "model": "local",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"answer":"stream"}'}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        },
    }
    sse = f"data: {json.dumps(completed)}\n\ndata: [DONE]\n\n".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.update(request.headers)
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = OpenAICompatibleClient(
            base_url="http://local", model="local", client=transport, retry_attempts=1
        )
        result = await client.generate(_request(), stream=True)
    assert result.output_text == '{"answer":"stream"}'
    assert captured_headers["accept"] == "text/event-stream"
    assert b"response.completed" in result.raw_response
    assert result.capabilities.streaming.availability == CapabilityAvailability.AVAILABLE


async def test_anthropic_is_a_real_second_provider_transport() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"answer":"ok"}'}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
                "stop_reason": "end_turn",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        client = AnthropicMessagesClient(
            model="claude-test", api_key="anthropic-key", client=transport
        )
        result = await client.generate(_request())
    assert captured["path"] == "/v1/messages"
    assert result.protocol == "anthropic_messages"
    assert result.output_text == '{"answer":"ok"}'
    assert result.usage["total_tokens"] == 5
    assert result.capabilities.responses_api.availability == CapabilityAvailability.UNAVAILABLE


def cast_bytes(value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError("expected bytes")
    return value
