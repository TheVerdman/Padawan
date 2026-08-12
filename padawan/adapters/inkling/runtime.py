from __future__ import annotations

import asyncio
from typing import Any, Literal

import httpx

from padawan.adapters.base import GenerationRequest, GenerationResult, ModelProviderError
from padawan.adapters.inkling.contract import (
    InklingServingContract,
    validate_responses_edge_url,
)
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
        contract: InklingServingContract | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if tensor_parallel_size <= 0:
            raise ValueError("tensor_parallel_size must be positive")
        validate_responses_edge_url(base_url)
        if contract is not None:
            _validate_runtime_configuration(
                contract=contract,
                model=model,
                checkpoint_id=checkpoint_id,
                runtime_revision=runtime_revision,
                quantization_manifest=quantization_manifest,
                tensor_parallel_size=tensor_parallel_size,
                protocol=protocol,
                allow_legacy_fallback=allow_legacy_fallback,
                timeout_seconds=timeout_seconds,
            )
        self.runtime_id = "inkling-vllm"
        self.runtime_version = runtime_revision
        self.checkpoint_id = checkpoint_id
        self.quantization_manifest = quantization_manifest
        self.tensor_parallel_size = tensor_parallel_size
        self.contract = contract
        self._negotiation_lock = asyncio.Lock()
        self._negotiated: dict[str, Any] | None = None
        self.client = OpenAICompatibleClient(
            base_url=base_url,
            model=model,
            api_key=api_key,
            provider="inkling",
            protocol=protocol,
            allow_legacy_fallback=allow_legacy_fallback,
            timeout_seconds=timeout_seconds,
            retry_attempts=1 if contract is not None else 2,
            client=http_client,
            capture_private_reasoning=True,
        )

    async def negotiate(self) -> dict[str, Any]:
        async with self._negotiation_lock:
            if self._negotiated is not None:
                return self._negotiated
            server = await self.client.negotiate()
            if self.contract is not None:
                errors = _contract_errors(server, self.contract)
                if errors:
                    raise ModelProviderError(
                        "Inkling serving contract negotiation failed: " + "; ".join(errors),
                        provider="inkling",
                    )
            self._negotiated = {
                **server,
                "runtime_id": self.runtime_id,
                "runtime_revision": self.runtime_version,
                "checkpoint_id": self.checkpoint_id,
                "quantization_manifest": self.quantization_manifest,
                "tensor_parallel_size": self.tensor_parallel_size,
            }
            return self._negotiated

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.contract is not None and (
            request.store or request.previous_response_id is not None
        ):
            raise ModelProviderError(
                "validated Inkling serving disables response storage and continuation",
                provider="inkling",
            )
        negotiated = await self.negotiate()
        result = await self.client.generate(request, stream=True)
        telemetry = {
            "checkpoint_id": self.checkpoint_id,
            "quantization_manifest": self.quantization_manifest,
            "tensor_parallel_size": self.tensor_parallel_size,
            "runtime_revision": self.runtime_version,
            "negotiated_server": negotiated,
            "server_telemetry": result.telemetry,
        }
        return GenerationResult(**{**result.__dict__, "telemetry": telemetry})

    async def close(self) -> None:
        await self.client.close()


def _validate_runtime_configuration(
    *,
    contract: InklingServingContract,
    model: str,
    checkpoint_id: str,
    runtime_revision: str,
    quantization_manifest: dict[str, Any],
    tensor_parallel_size: int,
    protocol: str,
    allow_legacy_fallback: bool,
    timeout_seconds: float,
) -> None:
    expected = {
        "model": (model, contract.served_model_name),
        "checkpoint_id": (checkpoint_id, contract.checkpoint_id),
        "runtime_revision": (runtime_revision, contract.runtime_revision),
        "tensor_parallel_size": (tensor_parallel_size, contract.tensor_parallel_size),
        "protocol": (protocol, "responses"),
        "quantization profile": (
            quantization_manifest.get("profile_id"),
            contract.profile_id,
        ),
        "conversion manifest": (
            quantization_manifest.get("conversion_manifest_sha256"),
            contract.conversion_manifest_sha256,
        ),
        "serving image": (
            quantization_manifest.get("serving_image_digest"),
            contract.serving_image_digest,
        ),
    }
    mismatches = [
        f"{name} must be {wanted!r}, observed {observed!r}"
        for name, (observed, wanted) in expected.items()
        if observed != wanted
    ]
    if allow_legacy_fallback:
        mismatches.append("legacy Chat Completions fallback is outside the Responses-only contract")
    if timeout_seconds < contract.request_timeout_seconds:
        mismatches.append(
            f"timeout_seconds must be at least {contract.request_timeout_seconds} for the "
            "validated context ladder"
        )
    if mismatches:
        raise ValueError("; ".join(mismatches))


def _contract_errors(server: dict[str, Any], contract: InklingServingContract) -> list[str]:
    errors: list[str] = []
    models = server.get("models")
    records = models.get("data") if isinstance(models, dict) else None
    model_ids = (
        [
            record.get("id")
            for record in records
            if isinstance(record, dict) and isinstance(record.get("id"), str)
        ]
        if isinstance(records, list)
        else []
    )
    if contract.served_model_name not in model_ids:
        errors.append(f"/v1/models does not advertise {contract.served_model_name!r}")

    extension = server.get("padawan_extension")
    if not isinstance(extension, dict):
        errors.append("/v1/padawan/capabilities is missing")
        return errors
    model = extension.get("model")
    protocol = extension.get("protocol")
    routes = protocol.get("routes") if isinstance(protocol, dict) else None
    features = extension.get("features")
    streaming = features.get("streaming") if isinstance(features, dict) else None
    structured = features.get("structured_outputs") if isinstance(features, dict) else None
    continuation = features.get("previous_response_id") if isinstance(features, dict) else None
    runtime = extension.get("runtime")

    observed = {
        "service": extension.get("service"),
        "profile_id": extension.get("profile_id"),
        "model.id": model.get("id") if isinstance(model, dict) else None,
        "model.checkpoint_id": (model.get("checkpoint_id") if isinstance(model, dict) else None),
        "model.quantization": model.get("quantization") if isinstance(model, dict) else None,
        "protocol.primary": protocol.get("primary") if isinstance(protocol, dict) else None,
        "protocol.routes.responses": (
            routes.get("responses") if isinstance(routes, dict) else None
        ),
        "protocol.chat_completions_contract": (
            protocol.get("chat_completions_contract") if isinstance(protocol, dict) else None
        ),
        "features.streaming.supported": (
            streaming.get("supported") if isinstance(streaming, dict) else None
        ),
        "features.streaming.terminal_event": (
            streaming.get("terminal_event") if isinstance(streaming, dict) else None
        ),
        "features.structured_outputs.json_schema": (
            structured.get("json_schema") if isinstance(structured, dict) else None
        ),
        "features.previous_response_id.supported": (
            continuation.get("supported") if isinstance(continuation, dict) else None
        ),
        "runtime.tensor_parallel_size": (
            runtime.get("tensor_parallel_size") if isinstance(runtime, dict) else None
        ),
        "runtime.max_model_len": (
            runtime.get("max_model_len") if isinstance(runtime, dict) else None
        ),
        "runtime.max_num_seqs": (
            runtime.get("max_num_seqs") if isinstance(runtime, dict) else None
        ),
        "runtime.chunked_prefill": (
            runtime.get("chunked_prefill") if isinstance(runtime, dict) else None
        ),
    }
    expected = {
        "service": contract.service,
        "profile_id": contract.profile_id,
        "model.id": contract.served_model_name,
        "model.checkpoint_id": contract.checkpoint_id,
        "model.quantization": contract.quantization,
        "protocol.primary": "responses",
        "protocol.routes.responses": "/v1/responses",
        "protocol.chat_completions_contract": False,
        "features.streaming.supported": True,
        "features.streaming.terminal_event": "response.completed",
        "features.structured_outputs.json_schema": True,
        "features.previous_response_id.supported": False,
        "runtime.tensor_parallel_size": contract.tensor_parallel_size,
        "runtime.max_model_len": contract.configured_max_model_len,
        "runtime.max_num_seqs": 1,
        "runtime.chunked_prefill": True,
    }
    errors.extend(
        f"capability {name} must be {wanted!r}, observed {observed[name]!r}"
        for name, wanted in expected.items()
        if observed[name] != wanted
    )
    return errors
