from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.episodes.runner import AlgebraWorkflowHandler
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine
from padawan.grading.algebra import AlgebraGrader
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import (
    Capability,
    CapabilityAvailability,
    CorpusPool,
    RuntimeCapabilities,
)
from padawan.models.database import Database
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.updates.backends import MemoryConsolidationBackend


def runtime_capabilities() -> RuntimeCapabilities:
    available = Capability(
        availability=CapabilityAvailability.AVAILABLE, reason="test transport supports it"
    )
    unavailable = Capability(
        availability=CapabilityAvailability.UNAVAILABLE, reason="test transport omits it"
    )
    return RuntimeCapabilities(
        responses_api=available,
        streaming=available,
        cancellation=available,
        logprobs=unavailable,
        token_ids=unavailable,
        private_reasoning=unavailable,
        reasoning_boundaries=unavailable,
        gpu_telemetry=unavailable,
        router_telemetry=unavailable,
    )


class CallbackGenerationClient:
    def __init__(
        self,
        callback: Callable[[GenerationRequest], str],
        provider: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.callback = callback
        self.provider = provider
        self.calls: list[GenerationRequest] = []
        self.data = data or {}

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)
        output = self.callback(request)
        raw_request = json.dumps(
            {"request": request.model_dump(mode="json")}, sort_keys=True
        ).encode()
        raw_response = json.dumps(
            {"id": f"response-{request.request_id}", "output_text": output}, sort_keys=True
        ).encode()
        return GenerationResult(
            request_id=request.request_id,
            response_id=f"response-{request.request_id}",
            provider=self.provider,
            model_id=f"{self.provider}-model",
            protocol="responses",
            output_text=output,
            raw_request=raw_request,
            raw_response=raw_response,
            usage={"input_tokens": 10, "output_tokens": 12, "total_tokens": 22},
            token_ids=None,
            token_logprobs=None,
            private_reasoning=None,
            reasoning_summary=None,
            finish_reason="completed",
            latency_ms=2.0,
            capabilities=runtime_capabilities(),
        )


def public_derivation(expected: dict[str, Any], *, correct: bool) -> str:
    final = dict(expected)
    if not correct:
        if expected["kind"] == "solution_set":
            final["values"] = ["999"]
        else:
            final["expression"] = "x + 999"
    return json.dumps(
        {
            "steps": [
                {
                    "step_id": "s1",
                    "before": "x",
                    "operation": "identity check",
                    "after": "x",
                    "assumptions": [],
                }
            ],
            "final_answer": final,
        },
        sort_keys=True,
    )


def teacher_response(request: GenerationRequest) -> str:
    payload = json.loads(str(request.input))
    grade = payload["deterministic_grade"]
    evidence_id = next(
        evidence["evidence_id"]
        for evidence in grade["evidence"]
        if evidence["kind"] == "deterministic"
    )
    return json.dumps(
        {
            "citations": [{"evidence_id": evidence_id}],
            "claimed_first_consequential_error": grade["first_invalid_step_id"],
            "error_class": grade["error_class"],
            "lesson": "Verify the final result against the original algebraic relation.",
            "repair": "Recompute the final symbolic result while preserving every restriction.",
            "expected_transfer_scope": "Unseen siblings in the same algebra competency.",
            "confidence": 0.8,
        },
        sort_keys=True,
    )


async def build_test_workflow(
    database: Database, artifact_root: Path
) -> tuple[
    AlgebraWorkflowHandler,
    AutonomousSupervisor,
    RunStore,
    CallbackGenerationClient,
    CallbackGenerationClient,
    str,
]:
    artifacts = LocalArtifactStore(artifact_root)
    catalog = ArtifactCatalog(artifacts)
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    memory = LessonMemory()
    runs = RunStore()
    generator = AlgebraCorpusGenerator()
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=41,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AlgebraFamily.DISTRIBUTION_SIGN,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    expected_by_prompt = {item.prompt: item.expected_answer for item in items}

    def student_callback(request: GenerationRequest) -> str:
        context = json.loads(str(request.input))
        expected = expected_by_prompt[context["task"]]
        correct = ":revision" in request.request_id or ":transfer:treatment" in request.request_id
        return public_derivation(expected, correct=correct)

    student = CallbackGenerationClient(
        student_callback,
        "student-test",
        data={"expected_by_prompt": expected_by_prompt},
    )
    teacher = CallbackGenerationClient(teacher_response, "teacher-test")
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=datetime(2026, 1, 1, tzinfo=UTC)):
            await registry.register_competency(session, competency)
        await registry.register_items(session, items)
        state = await states.create_student(
            session,
            student_id="student-test",
            checkpoint_id="checkpoint-test",
            runtime_id="runtime-test",
            initial_working_state={"prior": "legitimate persistent cognition"},
        )
    handler = AlgebraWorkflowHandler(
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(catalog),
        experiments=experiments,
        grader=AlgebraGrader(),
        provenance=ProvenanceLedger(code_revision="test-revision", environment="test"),
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher
        ),
        teacher_provider="teacher-test",
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id="runtime-test",
        student_runtime_version="1",
        student_checkpoint_id="checkpoint-test",
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id="worker-test",
        idle_poll_seconds=0,
    )
    return handler, supervisor, runs, student, teacher, state.state_id
