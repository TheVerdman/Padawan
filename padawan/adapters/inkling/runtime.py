from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any, Literal

import httpx

from padawan.adapters.base import (
    GenerationRequest,
    GenerationResult,
    GenerationStreamEvent,
    ModelProviderError,
)
from padawan.adapters.inkling.contract import (
    InklingServingContract,
    validate_responses_edge_url,
)
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient


@dataclass(frozen=True)
class InklingEdgeIdentity:
    """Identity reported by the Responses edge that answered capability preflight."""

    image_digest: str | None
    deployment_revision: str | None

    def __post_init__(self) -> None:
        if self.image_digest is None and self.deployment_revision is None:
            raise ValueError(
                "Inkling edge identity requires an image digest or deployment revision"
            )
        if (
            self.image_digest is not None
            and re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_digest) is None
        ):
            raise ValueError("Inkling edge image digest is not a sha256 identity")
        if self.deployment_revision is not None and not self.deployment_revision:
            raise ValueError("Inkling edge deployment revision cannot be empty")


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
        expected_edge_image_digest: str | None = None,
        expected_edge_deployment_revision: str | None = None,
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
        self.expected_edge_image_digest = expected_edge_image_digest
        self.expected_edge_deployment_revision = expected_edge_deployment_revision
        self._negotiation_lock = asyncio.Lock()
        self._negotiated: dict[str, Any] | None = None
        self._verified_edge_identity: InklingEdgeIdentity | None = None
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

    async def negotiate(self, *, refresh: bool = False) -> dict[str, Any]:
        async with self._negotiation_lock:
            if self._negotiated is not None and not refresh:
                return self._negotiated
            server = await self.client.negotiate()
            if self.contract is not None:
                errors = _contract_errors(
                    server,
                    self.contract,
                    expected_edge_image_digest=self.expected_edge_image_digest,
                    expected_edge_deployment_revision=self.expected_edge_deployment_revision,
                )
                if errors:
                    raise ModelProviderError(
                        "Inkling serving contract negotiation failed: " + "; ".join(errors),
                        provider="inkling",
                    )
                self._verified_edge_identity = _edge_identity(server)
            self._negotiated = {
                **server,
                "runtime_id": self.runtime_id,
                "runtime_revision": self.runtime_version,
                "checkpoint_id": self.checkpoint_id,
                "quantization_manifest": self.quantization_manifest,
                "tensor_parallel_size": self.tensor_parallel_size,
                "edge_identity": (
                    {
                        "image_digest": self._verified_edge_identity.image_digest,
                        "deployment_revision": self._verified_edge_identity.deployment_revision,
                    }
                    if self._verified_edge_identity is not None
                    else None
                ),
            }
            return self._negotiated

    @property
    def verified_edge_identity(self) -> InklingEdgeIdentity:
        if self._verified_edge_identity is None:
            raise RuntimeError("Inkling edge identity has not passed capability preflight")
        return self._verified_edge_identity

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        async for event in self.stream(request):
            if event.result is not None:
                return event.result
        raise ModelProviderError(
            "Inkling stream ended without a terminal result", provider="inkling"
        )

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]:
        if self.contract is not None and (
            request.store or request.previous_response_id is not None
        ):
            raise ModelProviderError(
                "validated Inkling serving disables response storage and continuation",
                provider="inkling",
            )
        # The mutable edge may be redeployed while a worker remains alive. Recheck
        # its authenticated capability identity immediately before every model call.
        negotiated = await self.negotiate(refresh=True)
        async for event in self.client.stream(request):
            if event.result is None:
                yield event
                continue
            result = event.result
            telemetry = {
                "checkpoint_id": self.checkpoint_id,
                "quantization_manifest": self.quantization_manifest,
                "tensor_parallel_size": self.tensor_parallel_size,
                "runtime_revision": self.runtime_version,
                "negotiated_server": negotiated,
                "server_telemetry": result.telemetry,
            }
            yield replace(
                event,
                result=GenerationResult(**{**result.__dict__, "telemetry": telemetry}),
            )

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


def _contract_errors(
    server: dict[str, Any],
    contract: InklingServingContract,
    *,
    expected_edge_image_digest: str | None,
    expected_edge_deployment_revision: str | None,
) -> list[str]:
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
    transport = extension.get("transport")

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
        "transport.edge_image_digest": (
            transport.get("edge_image_digest") if isinstance(transport, dict) else None
        ),
        "transport.edge_deployment_revision": (
            transport.get("edge_deployment_revision") if isinstance(transport, dict) else None
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
    observed_image = observed["transport.edge_image_digest"]
    observed_revision = observed["transport.edge_deployment_revision"]
    if observed_image is None and observed_revision is None:
        errors.append(
            "capability transport must report an edge image digest or deployment revision"
        )
    if observed_image is not None and (
        not isinstance(observed_image, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", observed_image) is None
    ):
        errors.append("capability transport edge image digest is invalid")
    if observed_revision is not None and (
        not isinstance(observed_revision, str) or not observed_revision
    ):
        errors.append("capability transport edge deployment revision is invalid")
    if expected_edge_image_digest is not None and observed_image != expected_edge_image_digest:
        errors.append(
            "capability transport edge image digest differs from configured deployment identity"
        )
    if (
        expected_edge_deployment_revision is not None
        and observed_revision != expected_edge_deployment_revision
    ):
        errors.append(
            "capability transport edge deployment revision differs from configured deployment "
            "identity"
        )
    return errors


def _edge_identity(server: dict[str, Any]) -> InklingEdgeIdentity:
    extension = server.get("padawan_extension")
    transport = extension.get("transport") if isinstance(extension, dict) else None
    if not isinstance(transport, dict):
        raise ValueError("Inkling capability response has no transport identity")
    image_digest = transport.get("edge_image_digest")
    deployment_revision = transport.get("edge_deployment_revision")
    return InklingEdgeIdentity(
        image_digest=image_digest if isinstance(image_digest, str) else None,
        deployment_revision=(deployment_revision if isinstance(deployment_revision, str) else None),
    )
