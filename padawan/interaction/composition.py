from __future__ import annotations

import inspect
from dataclasses import dataclass
from urllib.parse import urlsplit

from padawan.adapters.inkling.contract import INKLING_SMALL_AMPERE
from padawan.adapters.inkling.runtime import InklingRuntime
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.artifacts.factory import build_artifact_backend
from padawan.artifacts.store import ArtifactBackend, ArtifactCatalog
from padawan.config.composition import validate_inkling_student_configuration
from padawan.config.settings import Settings
from padawan.experiments.defaults import build_student_serving_identity
from padawan.interaction.contracts import ServingPathIdentity, StudentTargetDescriptor
from padawan.interaction.service import InteractionService
from padawan.interaction.store import InteractionStore
from padawan.interaction.targets import ManagedStudentTarget, StudentTargetRegistry
from padawan.models.contracts import ResearchRole
from padawan.models.database import Database


@dataclass
class InteractionApplication:
    database: Database
    artifacts: ArtifactBackend
    targets: StudentTargetRegistry
    store: InteractionStore
    service: InteractionService

    async def close(self) -> None:
        await self.targets.close()
        close_artifacts = getattr(self.artifacts, "close", None)
        if close_artifacts is not None:
            result = close_artifacts()
            if inspect.isawaitable(result):
                await result
        await self.database.close()


def build_interaction_application(settings: Settings) -> InteractionApplication:
    """Compose configured targets without probing or starting infrastructure."""

    database = Database(settings.database_url)
    artifacts = build_artifact_backend(settings)
    targets = StudentTargetRegistry()
    if settings.inkling_model:
        targets.register(_inkling_target(settings))
    if settings.compatible_model and settings.compatible_base_url:
        targets.register(_compatible_target(settings))
    store = InteractionStore(ArtifactCatalog(artifacts))
    service = InteractionService(
        database=database,
        artifacts=artifacts,
        store=store,
        targets=targets,
        system_prompt=settings.interaction_system_prompt,
    )
    return InteractionApplication(
        database=database,
        artifacts=artifacts,
        targets=targets,
        store=store,
        service=service,
    )


def _inkling_target(settings: Settings) -> ManagedStudentTarget:
    target_id = "student.inkling-small-ampere.w8a16"
    model_id = settings.inkling_model
    if model_id is None:
        raise ValueError("student target requires a configured model")
    validate_inkling_student_configuration(settings, selected_model=model_id)
    runtime = InklingRuntime(
        base_url=settings.inkling_base_url,
        model=model_id,
        checkpoint_id=INKLING_SMALL_AMPERE.checkpoint_id,
        runtime_revision=settings.inkling_runtime_revision,
        quantization_manifest=INKLING_SMALL_AMPERE.quantization_manifest(),
        tensor_parallel_size=settings.inkling_tensor_parallel_size,
        protocol="responses",
        allow_legacy_fallback=False,
        expected_edge_image_digest=settings.inkling_edge_image_digest,
        expected_edge_deployment_revision=settings.inkling_edge_deployment_revision,
        api_key=(
            settings.inkling_api_key.get_secret_value()
            if settings.inkling_api_key is not None
            else None
        ),
        timeout_seconds=settings.inkling_timeout_seconds,
        contract=INKLING_SMALL_AMPERE,
    )

    async def descriptor() -> StudentTargetDescriptor:
        await runtime.negotiate(refresh=True)
        edge = runtime.verified_edge_identity
        model = build_student_serving_identity(
            provider="inkling",
            model_id=model_id,
            runtime_id=runtime.runtime_id,
            runtime_version=runtime.runtime_version,
            checkpoint_id=runtime.checkpoint_id,
            role=ResearchRole.TARGET,
            base_url=settings.inkling_base_url,
            allow_legacy_fallback=False,
            timeout_seconds=settings.inkling_timeout_seconds,
            retry_attempts=1,
            edge_image_digest=edge.image_digest,
            edge_deployment_revision=edge.deployment_revision,
            edge_identity_verified=True,
        )
        components = tuple(
            sorted(
                (
                    model.runtime,
                    model.serving_artifact,
                    *((model.transport_artifact,) if model.transport_artifact is not None else ()),
                ),
                key=lambda item: (item.component_id, item.version),
            )
        )
        return StudentTargetDescriptor(
            target_id=target_id,
            display_name="Inkling-Small-Ampere",
            provider="inkling",
            model=model,
            serving_path=ServingPathIdentity(
                target_id=target_id,
                endpoint_origin=_endpoint_origin(settings.inkling_base_url),
                route="/v1/responses",
                protocol="responses",
                components=components,
            ),
            configured_context_window_tokens=INKLING_SMALL_AMPERE.configured_max_model_len,
            effective_input_limit_tokens=(
                INKLING_SMALL_AMPERE.maximum_verified_actual_input_tokens
            ),
            private_reasoning_capture_enabled=True,
        )

    return ManagedStudentTarget(
        target_id=target_id,
        display_name="Inkling-Small-Ampere",
        provider="inkling",
        client=runtime,
        descriptor_loader=descriptor,
    )


def _compatible_target(settings: Settings) -> ManagedStudentTarget:
    target_id = "student.openai-compatible.default"
    model_id = settings.compatible_model
    base_url = settings.compatible_base_url
    if model_id is None or base_url is None:
        raise ValueError("compatible target requires a model and base URL")
    endpoint_origin = _endpoint_origin(base_url)
    endpoint_route = _endpoint_route(base_url, "/v1/responses")
    client = OpenAICompatibleClient(
        base_url=base_url,
        model=model_id,
        api_key=(
            settings.compatible_api_key.get_secret_value()
            if settings.compatible_api_key is not None
            else None
        ),
        provider="openai_compatible_student",
        protocol="responses",
        allow_legacy_fallback=False,
        timeout_seconds=settings.external_timeout_seconds,
        retry_attempts=settings.external_retry_attempts,
        capture_private_reasoning=False,
    )

    async def descriptor() -> StudentTargetDescriptor:
        negotiated = await client.negotiate()
        models = negotiated.get("models")
        records = models.get("data") if isinstance(models, dict) else None
        advertised = {
            str(item["id"]) for item in records or [] if isinstance(item, dict) and item.get("id")
        }
        if advertised and model_id not in advertised:
            raise ValueError("configured student model is not advertised by the endpoint")
        model = build_student_serving_identity(
            provider="compatible",
            model_id=model_id,
            runtime_id="openai-compatible-responses",
            runtime_version="v1",
            checkpoint_id=model_id,
            role=ResearchRole.TARGET,
            base_url=base_url,
            allow_legacy_fallback=False,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
            edge_image_digest=None,
            edge_deployment_revision=None,
            edge_identity_verified=False,
        )
        components = tuple(
            sorted(
                (model.runtime, model.serving_artifact),
                key=lambda item: (item.component_id, item.version),
            )
        )
        return StudentTargetDescriptor(
            target_id=target_id,
            display_name=model_id,
            provider="openai_compatible_student",
            model=model,
            serving_path=ServingPathIdentity(
                target_id=target_id,
                endpoint_origin=endpoint_origin,
                route=endpoint_route,
                protocol="responses",
                components=components,
            ),
            private_reasoning_capture_enabled=False,
        )

    return ManagedStudentTarget(
        target_id=target_id,
        display_name=model_id,
        provider="openai_compatible_student",
        client=client,
        descriptor_loader=descriptor,
    )


def _endpoint_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("student endpoint must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("student endpoint must not contain credentials, query, or fragment")
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def _endpoint_route(value: str, suffix: str) -> str:
    _endpoint_origin(value)
    prefix = urlsplit(value).path.rstrip("/")
    return f"{prefix}{suffix}"
