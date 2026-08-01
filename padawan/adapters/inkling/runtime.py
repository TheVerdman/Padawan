from __future__ import annotations

from typing import Any, Literal

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient


class InklingRuntime:
    """Padawan boundary for the independently served Inkling checkpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        checkpoint_id: str,
        runtime_revision: str,
        quantization_manifest: dict[str, Any],
        tensor_parallel_size: int = 4,
        protocol: Literal["responses", "chat_completions"] = "responses",
        allow_legacy_fallback: bool = False,
        api_key: str | None = None,
        timeout_seconds: float = 600.0,
    ) -> None:
        if tensor_parallel_size <= 0:
            raise ValueError("tensor_parallel_size must be positive")
        self.runtime_id = "inkling-vllm"
        self.runtime_version = runtime_revision
        self.checkpoint_id = checkpoint_id
        self.quantization_manifest = quantization_manifest
        self.tensor_parallel_size = tensor_parallel_size
        self.client = OpenAICompatibleClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider="inkling",
            protocol=protocol,
            allow_legacy_fallback=allow_legacy_fallback,
            timeout_seconds=timeout_seconds,
            retry_attempts=2,
        )

    async def negotiate(self) -> dict[str, Any]:
        server = await self.client.negotiate()
        return {
            **server,
            "runtime_id": self.runtime_id,
            "runtime_revision": self.runtime_version,
            "checkpoint_id": self.checkpoint_id,
            "quantization_manifest": self.quantization_manifest,
            "tensor_parallel_size": self.tensor_parallel_size,
        }

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        result = await self.client.generate(request, stream=True)
        telemetry = {
            "checkpoint_id": self.checkpoint_id,
            "quantization_manifest": self.quantization_manifest,
            "tensor_parallel_size": self.tensor_parallel_size,
            "runtime_revision": self.runtime_version,
            "server_telemetry": result.telemetry,
        }
        return GenerationResult(**{**result.__dict__, "telemetry": telemetry})

    async def close(self) -> None:
        await self.client.close()
