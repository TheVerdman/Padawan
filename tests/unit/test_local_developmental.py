from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from padawan.adapters.base import GenerationRequest
from padawan.adapters.openai_compatible.local_developmental import LocalDevelopmentalClient
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.developmental.workflow import DomainDevelopmentalWorkflowHandler
from padawan.domains.graduate_algebra import (
    COMPETENCY_ID,
    DOMAIN_ID,
    GraduateAlgebraAuthority,
    competency,
    corpus_items,
)
from padawan.domains.graduate_algebra_suite import SUITE
from padawan.episodes.store import EpisodeStore
from padawan.experiments.controls import (
    ResearchControlRegistry,
    parent_state_identity,
    research_corpus_digest,
)
from padawan.experiments.engine import ExperimentEngine
from padawan.experiments.local_developmental import local_developmental_control
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import ResearchRole, RunState, SamplingConfiguration
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    CorpusItemRow,
    EpisodeRow,
    LessonVersionRow,
    RunRow,
    StudentStateRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.teaching.authored import AuthoredTeacherClient


def generation_request(**updates) -> GenerationRequest:
    return GenerationRequest(
        request_id="local-test",
        instructions="Provide a public proof.",
        input="public task",
        sampling=SamplingConfiguration(
            temperature=0.7,
            top_p=0.95,
            max_output_tokens=65536,
            seed=17,
            top_logprobs=None,
        ),
        **updates,
    )


async def test_local_transport_preserves_proof_budget_and_avoids_unsupported_grammar() -> None:
    client = LocalDevelopmentalClient(base_url="http://127.0.0.1:18768", model="fixture")
    try:
        plain = generation_request()
        body = json.loads(client.prepare_generation(plain).body_json)
        assert body["max_output_tokens"] == 65536
        assert body["temperature"] == 0.7
        assert body["instructions"] == plain.instructions
        assert body["store"] is False
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        schema = {"type": "object", "properties": {"public": {"type": "string"}}}
        structured = generation_request(schema_name="public", json_schema=schema)
        prepared = client.prepare_generation(structured)
        body = json.loads(prepared.body_json)
        assert "text" not in body
        assert json.dumps(schema, sort_keys=True) in body["instructions"]
        assert prepared.request_digest == sha256_digest(structured)
        with pytest.raises(ValueError, match="stateless"):
            client.prepare_generation(plain.model_copy(update={"store": True}))
    finally:
        await client.close()
    with pytest.raises(ValueError, match="loopback"):
        LocalDevelopmentalClient(base_url="https://api.openai.com", model="fixture")


async def test_authored_teacher_rejects_private_trace_before_creating_a_request(tmp_path) -> None:
    client = AuthoredTeacherClient(tmp_path)
    request = generation_request(schema_name="padawan_teacher_intervention").model_copy(
        update={"input": json.dumps({"private_reasoning": {"content": "restricted fixture"}})}
    )
    with pytest.raises(ValueError, match="public evidence only"):
        await client.generate(request)
    assert not list(tmp_path.iterdir())


async def test_authored_teacher_rejects_feedback_for_a_different_request(tmp_path) -> None:
    client = AuthoredTeacherClient(tmp_path, timeout_seconds=2)
    request = generation_request(schema_name="padawan_teacher_intervention").model_copy(
        update={"input": json.dumps({"private_reasoning": {"availability": "unavailable"}})}
    )
    pending = asyncio.create_task(client.generate(request))
    await asyncio.sleep(0)
    prompt = next(tmp_path.glob("*.request.json"))
    response = prompt.with_name(prompt.name.replace(".request.", ".response."))
    response.write_text(json.dumps({"request_digest": "wrong-input"}))
    with pytest.raises(ValueError, match="exact teacher request"):
        await pending


def test_empty_public_proof_stops_before_mathematical_grading(tmp_path) -> None:
    authority = GraduateAlgebraAuthority(tmp_path)
    with pytest.raises(ValueError, match="proof grading cannot begin"):
        authority.decode_student_output(
            output_text=" \n",
            item=corpus_items(SUITE)[0],
            research_role=ResearchRole.TARGET,
            created_at=datetime.now(UTC),
        )


async def test_reviewed_graduate_episode_uses_native_controls_and_transfer_gate(database, tmp_path):
    # All outputs and reviews in this test are explicitly synthetic control fixtures.
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    registry, states, runs, memory = CorpusRegistry(), StateStore(), RunStore(), LessonMemory()
    review_root, teaching_root = tmp_path / "reviews", tmp_path / "teaching"
    authority = GraduateAlgebraAuthority(review_root, timeout_seconds=5)
    teacher = AuthoredTeacherClient(teaching_root, timeout_seconds=5)
    fixture_lesson = "FIXTURE_LESSON: distinguish subgroup generation from subgroup intersection."

    sent_requests = []

    def transport(request):
        body = json.loads(request.content)
        request_id = request.headers["X-Request-ID"]
        sent_requests.append((request_id, body))
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert "text" not in body
        text = (
            "FIXTURE_CORRECT"
            if ":revision" in request_id or ":transfer:treatment" in request_id
            else "FIXTURE_INCOMPLETE"
        )
        return httpx.Response(
            200,
            json={
                "id": f"response-{request_id}",
                "model": "student-fixture-model",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            },
        )

    student = LocalDevelopmentalClient(
        base_url="http://127.0.0.1:18768", model="student-fixture-model"
    )
    await student.close()
    student.client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    inputs = {
        "config": {
            "model_id": "student-fixture-model",
            "port": 18768,
            "runtime_args": [],
            "duration_seconds": 16200,
            "suite_digest": sha256_digest(SUITE),
        },
        "sources": {"fixture": "synthetic offline composition"},
        "runtime": {"head": "fixture-runtime"},
        "model_inventory": [],
    }
    controls = ResearchControlRegistry()
    async with database.transaction() as session:
        await registry.register_competency(session, competency())
        await registry.register_items(session, corpus_items(SUITE))
        state = await states.create_student(
            session,
            student_id="graduate-fixture",
            checkpoint_id="student-fixture-model",
            runtime_id="vllm-metal-local",
            research_role=ResearchRole.TARGET,
            initial_working_state={},
        )
        rows = list((await session.scalars(select(CorpusItemRow))).all())
        control, worker = local_developmental_control(
            inputs, corpus_digest=research_corpus_digest(rows)
        )
        await controls.register_profile(session, control.profile)
        row = await session.get(StudentStateRow, state.state_id)
        manifest = control.execution_manifest(
            execution_id="fixture-execution",
            seed=17,
            created_at=datetime.now(UTC),
            parent_state=parent_state_identity(row),
        )
        execution = await controls.register_execution(
            session, manifest, parent_state_id=state.state_id
        )
        await runs.create(
            session,
            run_id="graduate-fixture-run",
            retry_budget=0,
            research_execution_digest=execution.execution_digest,
            payload={
                "student_id": "graduate-fixture",
                "state_id": state.state_id,
                "domain_id": DOMAIN_ID,
                "pool": "curriculum",
                "competency_id": COMPETENCY_ID,
                "experiment_seed": 17,
                "teacher_mode": "diagnostic_critique",
                "teacher_retry_attempts": 2,
                "treatment_condition": "authored_evidence_critique",
                "control_condition": "no_intervention",
            },
        )

    from padawan.updates.backends import MemoryConsolidationBackend

    handler = DomainDevelopmentalWorkflowHandler(
        authority=authority,
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(ArtifactCatalog(artifacts)),
        experiments=ExperimentEngine(states),
        provenance=ProvenanceLedger(code_revision="fixture", environment="test"),
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher
        ),
        teacher_provider=teacher.provider,
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id="vllm-metal-local",
        student_runtime_version="fixture-runtime",
        student_checkpoint_id="student-fixture-model",
        student_role=ResearchRole.TARGET,
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id="fixture-worker",
        idle_poll_seconds=0,
        research_worker=worker,
    )

    async def fixture_inputs():
        while True:
            for root in (review_root, teaching_root):
                for path in root.glob("*.request.json"):
                    response_path = path.with_name(path.name.replace(".request.", ".response."))
                    if response_path.exists():
                        continue
                    data = json.loads(path.read_text())
                    if root == review_root:
                        points = 2 if data["public_proof"] == "FIXTURE_CORRECT" else 0
                        response = {
                            "request_digest": data["request_digest"],
                            "reviewer": "offline-fixture",
                            "summary": "Synthetic review for workflow validation only.",
                            "criteria": [{"points": points, "reason": "synthetic fixture"}] * 5,
                            "significant_errors": [] if points else ["synthetic incomplete proof"],
                        }
                    else:
                        grade = data["context"]["deterministic_grade"]
                        response = {
                            "request_digest": data["request_digest"],
                            "author": "offline-fixture",
                            "evidence_review_note": "Synthetic teaching fixture only.",
                            "output": {
                                "citations": [{"evidence_id": grade["evidence"][0]["evidence_id"]}],
                                "claimed_first_consequential_error": None,
                                "error_class": grade["error_class"],
                                "lesson": fixture_lesson,
                                "repair": "FIXTURE_REPAIR",
                                "expected_transfer_scope": "Same competency.",
                                "confidence": 0.8,
                            },
                        }
                    response_path.write_text(json.dumps(response))
            await asyncio.sleep(0.01)

    feeder = asyncio.create_task(fixture_inputs())
    try:
        await asyncio.wait_for(supervisor.run(budget=64), timeout=15)
    finally:
        feeder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await feeder
        await student.close()
    async with database.transaction() as session:
        run = await session.get(RunRow, "graduate-fixture-run")
        assert run.state == RunState.COMPLETE.value, run.last_error
        episode = await session.get(EpisodeRow, "episode-graduate-fixture-run")
        assert episode.record_json["teaching_outcome"] == "successful_transfer"
        assert len(list((await session.scalars(select(LessonVersionRow))).all())) == 1
    assert len(sent_requests) == 4
    for request_id, body in sent_requests:
        context = json.loads(body["input"])
        assert "reference" not in context
        assert "rubric" not in context
        assert body["max_output_tokens"] == 65536
        if request_id.endswith(":transfer:control"):
            assert fixture_lesson not in body["input"]
        if request_id.endswith(":transfer:treatment"):
            assert fixture_lesson in body["input"]
