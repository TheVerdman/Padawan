from __future__ import annotations

import json

import httpx
import pytest

from padawan.adapters.base import GenerationRequest, ModelProviderError
from padawan.adapters.inkling import INKLING_SMALL_AMPERE, InklingRuntime
from padawan.models.contracts import SamplingConfiguration

_EDGE_IMAGE_DIGEST = f"sha256:{'d' * 64}"
_EDGE_DEPLOYMENT_REVISION = "inkling-edge-00004-test"


def _request(**updates: object) -> GenerationRequest:
    request = GenerationRequest(
        request_id="inkling-request-1",
        instructions="Return the contract.",
        input="Solve this bounded task.",
        sampling=SamplingConfiguration(max_output_tokens=64, temperature=0.1),
        schema_name="inkling_answer",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
        store=False,
    )
    return request.model_copy(update=updates)


def _capabilities(
    *,
    profile_id: str | None = None,
    edge_image_digest: str = _EDGE_IMAGE_DIGEST,
    edge_deployment_revision: str = _EDGE_DEPLOYMENT_REVISION,
) -> dict[str, object]:
    contract = INKLING_SMALL_AMPERE
    return {
        "schema_version": "1.0.0",
        "service": contract.service,
        "profile_id": profile_id or contract.profile_id,
        "model": {
            "id": contract.served_model_name,
            "checkpoint_id": contract.checkpoint_id,
            "quantization": contract.quantization,
        },
        "protocol": {
            "primary": "responses",
            "routes": {
                "models": "/v1/models",
                "responses": "/v1/responses",
                "capabilities": "/v1/padawan/capabilities",
            },
            "chat_completions_contract": False,
        },
        "features": {
            "streaming": {"supported": True, "terminal_event": "response.completed"},
            "structured_outputs": {"json_schema": True},
            "previous_response_id": {"supported": False},
        },
        "runtime": {
            "tensor_parallel_size": contract.tensor_parallel_size,
            "max_model_len": contract.configured_max_model_len,
            "max_num_seqs": 1,
            "chunked_prefill": True,
        },
        "transport": {
            "edge_image_digest": edge_image_digest,
            "edge_deployment_revision": edge_deployment_revision,
        },
    }


def _runtime(http_client: httpx.AsyncClient, **updates: object) -> InklingRuntime:
    contract = INKLING_SMALL_AMPERE
    arguments: dict[str, object] = {
        "base_url": "https://inkling.example",
        "model": contract.served_model_name,
        "checkpoint_id": contract.checkpoint_id,
        "runtime_revision": contract.runtime_revision,
        "quantization_manifest": contract.quantization_manifest(),
        "tensor_parallel_size": contract.tensor_parallel_size,
        "protocol": "responses",
        "allow_legacy_fallback": False,
        "expected_edge_image_digest": _EDGE_IMAGE_DIGEST,
        "expected_edge_deployment_revision": _EDGE_DEPLOYMENT_REVISION,
        "api_key": "edge-secret",
        "timeout_seconds": contract.request_timeout_seconds,
        "contract": contract,
        "http_client": http_client,
    }
    arguments.update(updates)
    return InklingRuntime(**arguments)  # type: ignore[arg-type]


async def test_validated_inkling_runtime_negotiates_then_streams_once() -> None:
    paths: list[str] = []
    response = {
        "id": "resp-inkling",
        "model": INKLING_SMALL_AMPERE.served_model_name,
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "content": [],
                "summary": [{"type": "summary_text", "text": "brief summary"}],
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"answer":"ok"}'}],
            },
        ],
        "usage": {"input_tokens": 12, "output_tokens": 5, "total_tokens": 17},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers["authorization"] == "Bearer edge-secret"
        if request.url.path == "/v1/models":
            assert request.headers["accept"] == "application/json"
            return httpx.Response(
                200,
                json={"object": "list", "data": [{"id": INKLING_SMALL_AMPERE.served_model_name}]},
            )
        if request.url.path == "/v1/padawan/capabilities":
            return httpx.Response(200, json=_capabilities())
        assert request.url.path == "/v1/responses"
        assert request.headers["accept"] == "text/event-stream"
        payload = json.loads(request.content)
        assert payload["model"] == INKLING_SMALL_AMPERE.served_model_name
        assert payload["stream"] is True
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        reasoning_event = {
            "type": "response.reasoning_text.done",
            "text": "self-hosted-private-trace",
        }
        event = {"type": "response.completed", "response": response}
        return httpx.Response(
            200,
            content=(
                f"event: response.reasoning_text.done\ndata: {json.dumps(reasoning_event)}\n\n"
                f"event: response.completed\ndata: {json.dumps(event)}\n\n"
            ).encode(),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        runtime = _runtime(http_client)
        result = await runtime.generate(_request())
        second_negotiation = await runtime.negotiate()

    assert paths == ["/v1/models", "/v1/padawan/capabilities", "/v1/responses"]
    assert result.output_text == '{"answer":"ok"}'
    assert result.private_reasoning == "self-hosted-private-trace"
    assert result.reasoning_summary == "brief summary"
    assert result.capabilities.private_reasoning.availability.value == "available"
    assert result.capabilities.reasoning_boundaries.availability.value == "available"
    assert result.telemetry is not None
    assert result.telemetry["checkpoint_id"] == INKLING_SMALL_AMPERE.checkpoint_id
    assert result.telemetry["quantization_manifest"]["profile_id"] == (
        INKLING_SMALL_AMPERE.profile_id
    )
    assert second_negotiation["padawan_extension"]["service"] == (INKLING_SMALL_AMPERE.service)
    assert second_negotiation["edge_identity"] == {
        "image_digest": _EDGE_IMAGE_DIGEST,
        "deployment_revision": _EDGE_DEPLOYMENT_REVISION,
    }
    assert runtime.verified_edge_identity.image_digest == _EDGE_IMAGE_DIGEST
    assert runtime.client.retry_attempts == 1


async def test_inkling_contract_mismatch_fails_before_generation() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": INKLING_SMALL_AMPERE.served_model_name}]},
            )
        return httpx.Response(200, json=_capabilities(profile_id="wrong-profile"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        runtime = _runtime(http_client)
        with pytest.raises(ModelProviderError, match="profile_id"):
            await runtime.generate(_request())

    assert paths == ["/v1/models", "/v1/padawan/capabilities"]


async def test_inkling_rejects_stale_declared_edge_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": INKLING_SMALL_AMPERE.served_model_name}]},
            )
        return httpx.Response(
            200,
            json=_capabilities(edge_deployment_revision="inkling-edge-00005-redeployed"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        runtime = _runtime(http_client)
        with pytest.raises(ModelProviderError, match="deployment revision differs"):
            await runtime.negotiate()


async def test_inkling_rechecks_edge_identity_before_each_generation() -> None:
    capability_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal capability_calls
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={"data": [{"id": INKLING_SMALL_AMPERE.served_model_name}]},
            )
        if request.url.path == "/v1/padawan/capabilities":
            capability_calls += 1
            revision = (
                _EDGE_DEPLOYMENT_REVISION
                if capability_calls == 1
                else "inkling-edge-00005-redeployed"
            )
            return httpx.Response(
                200,
                json=_capabilities(edge_deployment_revision=revision),
            )
        raise AssertionError("generation must not reach a redeployed unverified edge")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        runtime = _runtime(http_client)
        await runtime.negotiate()
        with pytest.raises(ModelProviderError, match="deployment revision differs"):
            await runtime.generate(_request())

    assert capability_calls == 2


@pytest.mark.parametrize("updates", [{"store": True}, {"previous_response_id": ""}])
async def test_inkling_rejects_response_storage_without_network_io(
    updates: dict[str, object],
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("request should not be sent")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        runtime = _runtime(http_client)
        with pytest.raises(ModelProviderError, match="disables response storage"):
            await runtime.generate(_request(**updates))


def test_inkling_rejects_direct_vertex_dns_and_legacy_fallback() -> None:
    contract = INKLING_SMALL_AMPERE
    common = {
        "model": contract.served_model_name,
        "checkpoint_id": contract.checkpoint_id,
        "runtime_revision": contract.runtime_revision,
        "quantization_manifest": contract.quantization_manifest(),
        "timeout_seconds": contract.request_timeout_seconds,
        "contract": contract,
    }
    with pytest.raises(ValueError, match="Invoke DNS"):
        InklingRuntime(
            base_url=(
                "https://inkling-small-responses-gate-e.us-central1-123.prediction.vertexai.goog"
            ),
            **common,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="Responses-only"):
        InklingRuntime(
            base_url="https://inkling.example",
            allow_legacy_fallback=True,
            **common,  # type: ignore[arg-type]
        )
