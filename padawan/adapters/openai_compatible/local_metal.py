"""One exact loopback-only Responses effect, with no cloud or protocol fallback."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import httpx

from padawan.adapters.base import GenerationRequest
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.adapters.prepared import PreparedGeneration
from padawan.models.hashing import canonical_json_bytes, sha256_digest


def local_template_kwargs(request: GenerationRequest) -> dict[str, bool]:
    return {
        "enable_thinking": request.metadata.get("compiler_phase") != "finalize",
        "truncate_history_thinking": True,
    }


class LocalMetalClient(OpenAICompatibleClient):
    def __init__(self, *, base_url: str, model: str, timeout_seconds: float = 360) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port is None
            or not 1024 <= parsed.port <= 65535
            or parsed.path not in {"", "/"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("local Metal requires an explicit IPv4 loopback port")
        super().__init__(
            base_url=base_url.rstrip("/"),
            model=model,
            provider="local-nemotron-metal-4bit",
            protocol="responses",
            retry_attempts=1,
            capture_private_reasoning=True,
            timeout_seconds=timeout_seconds,
            client=httpx.AsyncClient(timeout=timeout_seconds, trust_env=False),
        )
        self._owned_client = True

    def prepare_generation(self, request: GenerationRequest) -> PreparedGeneration:
        if request.store or request.previous_response_id is not None or request.json_schema:
            raise ValueError("local coding requires stateless history without grammar decoding")
        prepared = super().prepare_generation(request)
        body = json.loads(prepared.body_json)
        body["chat_template_kwargs"] = local_template_kwargs(request)
        body["parallel_tool_calls"] = False
        encoded = canonical_json_bytes(body)
        return prepared.model_copy(
            update={
                "body_json": encoded.decode(),
                "body_digest": sha256_digest(encoded),
                "configuration_digest": sha256_digest(
                    {"adapter": "padawan.local-metal.v1", "native": prepared.configuration_digest}
                ),
            }
        )
