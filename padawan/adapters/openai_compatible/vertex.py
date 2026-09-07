"""One prepared Responses API effect through a dedicated Vertex rawPredict route."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.adapters.openai_compatible.gcp_credentials import (
    CredentialRefreshError,
    GcloudCredentialSource,
)
from padawan.adapters.prepared import PreparedGeneration
from padawan.models.hashing import canonical_json_bytes, sha256_digest


class VertexRawPredictClient(OpenAICompatibleClient):
    """Bearer credentials remain in request headers, outside the retained configuration.

    The caller obtains the exact dedicated DNS from Vertex's endpoint resource. This adapter
    changes only the destination; parsing, private-channel capture and one-effect transport use
    the existing OpenAI-compatible implementation. It never negotiates or falls back.
    """

    def __init__(
        self,
        *,
        raw_predict_url: str,
        model: str,
        gcloud: str | None = None,
        compiler_tools: bool = False,
        **kwargs: Any,
    ) -> None:
        url = urlsplit(raw_predict_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or not url.hostname.endswith(".prediction.vertexai.goog")
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.port not in {None, 443}
            or re.fullmatch(
                r"/v1/projects/[a-z0-9-]+/locations/[a-z0-9-]+/endpoints/[0-9]+:rawPredict",
                url.path,
            )
            is None
        ):
            raise ValueError("Vertex requires the exact credential-free dedicated rawPredict URL")
        self.raw_predict_url = raw_predict_url
        self.gcloud = gcloud
        self.compiler_tools = compiler_tools
        self._credentials = GcloudCredentialSource(gcloud) if gcloud is not None else None
        super().__init__(
            base_url=f"https://{url.netloc}",
            model=model,
            protocol="responses",
            provider="vertex-nemotron-bf16",
            retry_attempts=1,
            allow_legacy_fallback=False,
            capture_private_reasoning=True,
            **kwargs,
        )

    def prepare_generation(self, request: GenerationRequest) -> PreparedGeneration:
        prepared = super().prepare_generation(request)
        if self.compiler_tools:
            import json

            body = json.loads(prepared.body_json)
            body["chat_template_kwargs"] = {"force_nonempty_content": True}
            body["parallel_tool_calls"] = False
            body_bytes = canonical_json_bytes(body)
            prepared = prepared.model_copy(
                update={
                    "body_json": body_bytes.decode(),
                    "body_digest": sha256_digest(body_bytes),
                    "configuration_digest": sha256_digest(
                        {
                            "native": prepared.configuration_digest,
                            "compiler_tools": "qwen3_coder-public-only-v1",
                            "chat_template_kwargs": body["chat_template_kwargs"],
                            "parallel_tool_calls": False,
                        }
                    ),
                }
            )
        return prepared.model_copy(
            update={
                "destination": self.raw_predict_url,
                "configuration_digest": sha256_digest(
                    {
                        "adapter": "padawan.vertex-raw-predict.v2-expiry-aware",
                        "native_configuration_digest": prepared.configuration_digest,
                        "raw_predict_url": self.raw_predict_url,
                    }
                ),
            }
        )

    async def generate(
        self, request: GenerationRequest, *, stream: bool = False
    ) -> GenerationResult:
        if stream:
            raise ValueError(
                "the finite Vertex scout uses explicit non-streaming Responses effects"
            )
        return await self.generate_prepared(request, self.prepare_generation(request))

    async def generate_prepared(
        self, request: GenerationRequest, prepared: PreparedGeneration
    ) -> GenerationResult:
        if self._credentials is not None:
            try:
                credential = await self._credentials.get_async(self.timeout_seconds + 60)
            except CredentialRefreshError:
                raise ModelProviderError(
                    "GCP credential refresh failed before model dispatch", provider=self.provider
                ) from None
            self.api_key = credential.access_token
        return await super().generate_prepared(request, prepared)
