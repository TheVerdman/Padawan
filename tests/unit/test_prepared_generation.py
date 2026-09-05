from __future__ import annotations

import json

import httpx
import pytest

from padawan.adapters.base import GenerationRequest, ModelProviderError
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.models.contracts import SamplingConfiguration
from padawan.models.hashing import sha256_digest


def _request() -> GenerationRequest:
    return GenerationRequest(
        request_id="prepared-fixture",
        instructions="Return a public answer.",
        input='Check π and a "quoted" string.',
        sampling=SamplingConfiguration(max_output_tokens=12, seed=19),
    )


@pytest.mark.parametrize("protocol", ["responses", "chat_completions", "completions"])
async def test_prepared_transport_sends_exact_body_without_ambient_client_data(protocol):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "id": "prepared-response",
                "model": "test-model",
                "output": [],
                "choices": [{"text": "ok", "message": {"content": "ok"}}],
                "usage": {"input_tokens": 10, "output_tokens": 12},
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        params={"ambient": "forbidden"},
        cookies={"ambient": "forbidden"},
        headers={"x-ambient": "forbidden"},
        auth=("ambient-user", "ambient-password"),
        follow_redirects=True,
    ) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-model",
            protocol=protocol,
            retry_attempts=1,
            client=transport,
        )
        request = _request()
        prepared = client.prepare_generation(request)
        assert seen == []
        result = await client.generate_prepared(request, prepared)
    assert len(seen) == 1
    assert str(seen[0].url) == prepared.destination
    assert seen[0].content == result.raw_request == prepared.body_json.encode()
    assert sha256_digest(result.raw_request) == prepared.body_digest
    assert "cookie" not in seen[0].headers and "authorization" not in seen[0].headers
    assert "x-ambient" not in seen[0].headers
    assert json.loads(result.raw_request)["model"] == "test-model"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("model", "another-model"),
        ("provider", "another-provider"),
        ("base_url", "https://other.invalid"),
        ("protocol", "chat_completions"),
        ("retry_attempts", 2),
        ("allow_legacy_fallback", True),
        ("timeout_seconds", 3.0),
        ("capture_private_reasoning", True),
    ],
)
async def test_prepared_configuration_drift_denies_before_http(setting, value):
    seen = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: seen.append(request))
    ) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-model",
            retry_attempts=1,
            client=transport,
        )
        request = _request()
        prepared = client.prepare_generation(request)
        setattr(client, setting, value)
        with pytest.raises(ValueError):
            await client.generate_prepared(request, prepared)
    assert seen == []


@pytest.mark.parametrize("status", [302, 429, 503])
async def test_prepared_transport_never_redirects_or_retries(status):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(
            status, headers={"Location": "https://other.invalid"}, content=b"fixture"
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-model",
            retry_attempts=1,
            client=transport,
        )
        request = _request()
        with pytest.raises(ModelProviderError) as failure:
            await client.generate_prepared(request, client.prepare_generation(request))
        assert failure.value.retryable is False
        assert failure.value.response_body == b"fixture"
    assert len(seen) == 1


async def test_prepared_network_timeout_is_not_an_automatic_retry():
    seen = []

    def handle(request):
        seen.append(request)
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-model",
            retry_attempts=1,
            client=transport,
        )
        request = _request()
        with pytest.raises(ModelProviderError, match="unresolved") as failure:
            await client.generate_prepared(request, client.prepare_generation(request))
        assert failure.value.retryable is False
    assert len(seen) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"usage": {"input_tokens": []}}',
        b'{"usage": {"input_tokens": null, "output_tokens": 0}}',
        b'{"usage": {"input_tokens": "1", "output_tokens": 0}}',
        b'{"usage": {"input_tokens": true, "output_tokens": 0}}',
        b'{"usage": {"input_tokens": 1.5, "output_tokens": 0}}',
        b'{"usage": {"input_tokens": 1, "prompt_tokens": 2, "output_tokens": 0}}',
        b"{}",
    ],
)
async def test_malformed_prepared_response_keeps_exact_private_bytes(body):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))
    ) as transport:
        client = OpenAICompatibleClient(
            base_url="https://worker.invalid",
            model="test-model",
            retry_attempts=1,
            client=transport,
        )
        request = _request()
        with pytest.raises(ModelProviderError) as failure:
            await client.generate_prepared(request, client.prepare_generation(request))
        assert failure.value.retryable is False
        assert failure.value.response_body == body
