from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from padawan.adapters.frontier_anthropic.client import AnthropicMessagesClient
from padawan.adapters.frontier_openai.client import OpenAIResponsesClient
from padawan.adapters.inkling.contract import (
    INKLING_SMALL_AMPERE,
    validate_responses_edge_url,
)
from padawan.adapters.inkling.runtime import InklingRuntime
from padawan.adapters.openai_compatible.client import OpenAICompatibleClient
from padawan.artifacts.factory import build_artifact_backend
from padawan.artifacts.store import ArtifactBackend, ArtifactCatalog
from padawan.config.settings import Settings
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.builtin import build_builtin_domain_registry
from padawan.domains.registry import DomainRegistry, WorkflowDomainPackage
from padawan.domains.runtime import DomainRuntimeContext
from padawan.episodes.store import EpisodeStore
from padawan.experiments.controls import ResearchWorkerConfiguration
from padawan.experiments.defaults import build_live_worker_configuration
from padawan.experiments.engine import ExperimentEngine
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import ResearchRole
from padawan.models.database import Database
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor, WorkHandler
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.updates.backends import MemoryConsolidationBackend

StudentProvider = Literal["inkling", "openai", "compatible"]
TeacherProvider = Literal["openai", "anthropic"]


@dataclass
class LiveApplication:
    database: Database
    artifacts: ArtifactBackend
    registry: CorpusRegistry
    states: StateStore
    runs: RunStore
    experiments: ExperimentEngine
    memory: LessonMemory
    domains: DomainRegistry
    domain: WorkflowDomainPackage
    handler: WorkHandler
    supervisor: AutonomousSupervisor
    clients: tuple[Any, ...]
    student_role: ResearchRole
    student_runtime_id: str
    student_runtime_version: str
    student_checkpoint_id: str
    research_worker: ResearchWorkerConfiguration

    async def close(self) -> None:
        for client in self.clients:
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        close_artifacts = getattr(self.artifacts, "close", None)
        if close_artifacts is not None:
            result = close_artifacts()
            if inspect.isawaitable(result):
                await result
        await self.database.close()


async def build_live_application(
    settings: Settings,
    *,
    student_provider: StudentProvider,
    teacher_provider: TeacherProvider,
    student_model: str | None = None,
    teacher_model: str | None = None,
    compatible_base_url: str | None = None,
    compatible_api_key: str | None = None,
    allow_legacy_student_fallback: bool = False,
) -> LiveApplication:
    _validate_live_configuration(
        settings,
        student_provider=student_provider,
        teacher_provider=teacher_provider,
        student_model=student_model,
        teacher_model=teacher_model,
        compatible_base_url=compatible_base_url,
        allow_legacy_student_fallback=allow_legacy_student_fallback,
    )
    domains = build_builtin_domain_registry()
    domain = domains.get_workflow(settings.domain_id)
    database = Database(settings.database_url)
    artifacts = build_artifact_backend(settings)
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    memory = LessonMemory()
    runs = RunStore()
    clients: list[Any] = []
    verified_edge_image_digest: str | None = None
    verified_edge_deployment_revision: str | None = None

    if student_provider == "inkling":
        student_role = ResearchRole.TARGET
        selected_student_model = student_model or settings.inkling_model
        if not selected_student_model:
            raise ValueError("Inkling requires --student-model or PADAWAN_INKLING_MODEL")
        student = InklingRuntime(
            base_url=settings.inkling_base_url,
            model=selected_student_model,
            checkpoint_id=INKLING_SMALL_AMPERE.checkpoint_id,
            runtime_revision=settings.inkling_runtime_revision,
            quantization_manifest=INKLING_SMALL_AMPERE.quantization_manifest(),
            tensor_parallel_size=settings.inkling_tensor_parallel_size,
            protocol="responses",
            allow_legacy_fallback=allow_legacy_student_fallback,
            expected_edge_image_digest=settings.inkling_edge_image_digest,
            expected_edge_deployment_revision=settings.inkling_edge_deployment_revision,
            api_key=_secret(settings.inkling_api_key),
            timeout_seconds=settings.inkling_timeout_seconds,
            contract=INKLING_SMALL_AMPERE,
        )
        try:
            await student.negotiate()
        except BaseException:
            await student.close()
            raise
        verified_edge_image_digest = student.verified_edge_identity.image_digest
        verified_edge_deployment_revision = student.verified_edge_identity.deployment_revision
        student_client: Any = student
        student_runtime_id = student.runtime_id
        student_runtime_version = student.runtime_version
        checkpoint_id = student.checkpoint_id
        clients.append(student)
    elif student_provider == "openai":
        student_role = ResearchRole.BASELINE
        selected_student_model = student_model or settings.openai_model
        key = _secret(settings.openai_api_key)
        if not selected_student_model or not key:
            raise ValueError("OpenAI baseline requires a model and API key")
        student_client = OpenAIResponsesClient(
            model=selected_student_model,
            api_key=key,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
        )
        student_runtime_id = "openai-responses"
        student_runtime_version = "v1"
        checkpoint_id = selected_student_model
        clients.append(student_client)
    else:
        student_role = ResearchRole.TARGET
        selected_student_model = student_model or settings.compatible_model
        base_url = compatible_base_url or settings.compatible_base_url
        if not selected_student_model or not base_url:
            raise ValueError("compatible student requires a model and base URL")
        student_client = OpenAICompatibleClient(
            base_url=base_url,
            model=selected_student_model,
            api_key=compatible_api_key or _secret(settings.compatible_api_key),
            provider="openai_compatible_student",
            protocol="responses",
            allow_legacy_fallback=allow_legacy_student_fallback,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
        )
        student_runtime_id = "openai-compatible-responses"
        student_runtime_version = "v1"
        checkpoint_id = selected_student_model
        clients.append(student_client)

    if teacher_provider == "openai":
        selected_teacher_model = teacher_model or settings.openai_model
        key = _secret(settings.openai_api_key)
        if not selected_teacher_model or not key:
            raise ValueError("OpenAI teacher requires a model and API key")
        teacher_client: Any = OpenAIResponsesClient(
            model=selected_teacher_model,
            api_key=key,
            base_url=settings.openai_base_url,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
        )
        clients.append(teacher_client)
    else:
        selected_teacher_model = teacher_model or settings.anthropic_model
        key = _secret(settings.anthropic_api_key)
        if not selected_teacher_model or not key:
            raise ValueError("Anthropic teacher requires a model and API key")
        teacher_client = AnthropicMessagesClient(
            model=selected_teacher_model,
            api_key=key,
            base_url=settings.anthropic_base_url,
            timeout_seconds=settings.external_timeout_seconds,
            retry_attempts=settings.external_retry_attempts,
        )
        clients.append(teacher_client)

    catalog = ArtifactCatalog(artifacts)
    student_executor = IdempotentGenerationExecutor(
        database=database, artifacts=artifacts, client=student_client
    )
    teacher_executor = IdempotentGenerationExecutor(
        database=database, artifacts=artifacts, client=teacher_client
    )
    runtime_context = DomainRuntimeContext(
        database=database,
        artifacts=artifacts,
        corpus_registry=registry,
        states=states,
        episodes=EpisodeStore(catalog),
        experiments=experiments,
        provenance=ProvenanceLedger(
            code_revision=settings.code_revision,
            environment=settings.environment,
        ),
        student_calls=student_executor,
        teacher_calls=teacher_executor,
        teacher_provider=teacher_provider,
        adjudicator_calls=teacher_executor,
        adjudicator_provider=teacher_provider,
        domain_options={
            "lean_project_root": settings.lean_project_root,
            "lean_lake_executable": settings.lean_lake_executable,
            "lean_elan_home": settings.lean_elan_home,
            "lean_sandbox_mode": settings.lean_sandbox_mode,
            "lean_timeout_seconds": settings.lean_timeout_seconds,
            "lean_output_limit_bytes": settings.lean_output_limit_bytes,
            "lean_memory_limit_mb": settings.lean_memory_limit_mb,
        },
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id=student_runtime_id,
        student_runtime_version=student_runtime_version,
        student_checkpoint_id=checkpoint_id,
        student_role=student_role,
        lease_for=timedelta(seconds=settings.lease_seconds),
    )
    handler = domain.build_workflow(runtime_context)
    research_worker = build_live_worker_configuration(
        settings=settings,
        domain=domain.spec,
        student_provider=student_provider,
        student_model_id=selected_student_model,
        student_runtime_id=student_runtime_id,
        student_runtime_version=student_runtime_version,
        student_checkpoint_id=checkpoint_id,
        student_role=student_role,
        student_base_url=(
            settings.inkling_base_url
            if student_provider == "inkling"
            else settings.openai_base_url
            if student_provider == "openai"
            else compatible_base_url or settings.compatible_base_url
        ),
        teacher_provider=teacher_provider,
        teacher_model_id=selected_teacher_model,
        corpus_competency_ids=tuple(
            sorted(competency.competency_id for competency in domain.competencies())
        ),
        allow_legacy_student_fallback=allow_legacy_student_fallback,
        inkling_edge_image_digest=verified_edge_image_digest,
        inkling_edge_deployment_revision=verified_edge_deployment_revision,
        inkling_edge_identity_verified=student_provider == "inkling",
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id=settings.worker_id,
        lease_for=timedelta(seconds=settings.lease_seconds),
        research_worker=research_worker,
    )
    return LiveApplication(
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        runs=runs,
        experiments=experiments,
        memory=memory,
        domains=domains,
        domain=domain,
        handler=handler,
        supervisor=supervisor,
        clients=tuple(clients),
        student_role=student_role,
        student_runtime_id=student_runtime_id,
        student_runtime_version=student_runtime_version,
        student_checkpoint_id=checkpoint_id,
        research_worker=research_worker,
    )


def _secret(value: Any) -> str | None:
    if value is None:
        return None
    return str(value.get_secret_value())


def _validate_live_configuration(
    settings: Settings,
    *,
    student_provider: StudentProvider,
    teacher_provider: TeacherProvider,
    student_model: str | None,
    teacher_model: str | None,
    compatible_base_url: str | None,
    allow_legacy_student_fallback: bool,
) -> None:
    if allow_legacy_student_fallback and student_provider != "compatible":
        raise ValueError("legacy student fallback is available only for compatible providers")
    if student_provider == "inkling":
        selected_model = student_model or settings.inkling_model
        validate_inkling_student_configuration(settings, selected_model=selected_model)
    if student_provider == "openai" and not (
        (student_model or settings.openai_model) and _secret(settings.openai_api_key)
    ):
        raise ValueError("OpenAI baseline requires a model and API key")
    if student_provider == "compatible" and not (
        (student_model or settings.compatible_model)
        and (compatible_base_url or settings.compatible_base_url)
    ):
        raise ValueError("compatible student requires a model and base URL")
    if teacher_provider == "openai" and not (
        (teacher_model or settings.openai_model) and _secret(settings.openai_api_key)
    ):
        raise ValueError("OpenAI teacher requires a model and API key")
    if teacher_provider == "anthropic" and not (
        (teacher_model or settings.anthropic_model) and _secret(settings.anthropic_api_key)
    ):
        raise ValueError("Anthropic teacher requires a model and API key")


def validate_inkling_student_configuration(
    settings: Settings,
    *,
    selected_model: str | None,
) -> None:
    """Apply the validated Inkling admission gate at every composition root."""

    if not selected_model:
        raise ValueError("Inkling requires --student-model or PADAWAN_INKLING_MODEL")
    if selected_model != INKLING_SMALL_AMPERE.served_model_name:
        raise ValueError(
            f"validated Inkling serving requires model {INKLING_SMALL_AMPERE.served_model_name!r}"
        )
    validate_responses_edge_url(settings.inkling_base_url)
    hostname = urlsplit(settings.inkling_base_url).hostname
    if hostname not in {"127.0.0.1", "::1", "localhost"} and not _secret(settings.inkling_api_key):
        raise ValueError(
            "authenticated Inkling Responses edge requires INKLING_API_KEY or "
            "PADAWAN_INKLING_API_KEY"
        )
    if settings.inkling_runtime_revision != INKLING_SMALL_AMPERE.runtime_revision:
        raise ValueError("Inkling runtime revision differs from the validated serving image")
    if not (settings.inkling_edge_image_digest or settings.inkling_edge_deployment_revision):
        raise ValueError(
            "Inkling requires PADAWAN_INKLING_EDGE_IMAGE_DIGEST or "
            "PADAWAN_INKLING_EDGE_DEPLOYMENT_REVISION"
        )
    if settings.inkling_tensor_parallel_size != INKLING_SMALL_AMPERE.tensor_parallel_size:
        raise ValueError("Inkling tensor parallel size differs from the validated topology")
