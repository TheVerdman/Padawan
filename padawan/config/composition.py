from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

from padawan.adapters.frontier_anthropic.client import AnthropicMessagesClient
from padawan.adapters.frontier_openai.client import OpenAIResponsesClient
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

    if student_provider == "inkling":
        student_role = ResearchRole.TARGET
        selected_student_model = student_model or settings.inkling_model
        if not selected_student_model:
            raise ValueError("Inkling requires --student-model or PADAWAN_INKLING_MODEL")
        student = InklingRuntime(
            base_url=settings.inkling_base_url,
            model=selected_student_model,
            checkpoint_id=selected_student_model,
            runtime_revision=settings.inkling_runtime_revision,
            quantization_manifest={
                "availability": "unknown",
                "reason": "server identity must supply a checkpoint quantization manifest",
            },
            tensor_parallel_size=settings.inkling_tensor_parallel_size,
            protocol="responses",
            allow_legacy_fallback=allow_legacy_student_fallback,
            timeout_seconds=settings.external_timeout_seconds,
        )
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
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student_client
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher_client
        ),
        teacher_provider=teacher_provider,
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id=student_runtime_id,
        student_runtime_version=student_runtime_version,
        student_checkpoint_id=checkpoint_id,
        student_role=student_role,
        lease_for=timedelta(seconds=settings.lease_seconds),
    )
    handler = domain.build_workflow(runtime_context)
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id=settings.worker_id,
        lease_for=timedelta(seconds=settings.lease_seconds),
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
) -> None:
    if student_provider == "inkling" and not (student_model or settings.inkling_model):
        raise ValueError("Inkling requires --student-model or PADAWAN_INKLING_MODEL")
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
