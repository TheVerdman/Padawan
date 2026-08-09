from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.developmental.workflow import DomainDevelopmentalWorkflowHandler
from padawan.domains.temporal_grounding import (
    TemporalGroundingCorpusGenerator,
    TemporalScenarioFamily,
)
from padawan.domains.temporal_grounding.developmental import (
    TemporalGroundingDevelopmentalAuthority,
)
from padawan.episodes.store import EpisodeStore
from padawan.experiments.engine import ExperimentEngine
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import CorpusPool, ResearchRole, RunState
from padawan.models.tables import AttemptRow, EpisodeRow, OperationSpanRow, RunRow
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.temporal import TemporalAction, TemporalActionKind, TemporalDecision
from padawan.updates.backends import MemoryConsolidationBackend
from tests.helpers import CallbackGenerationClient


async def test_temporal_domain_runs_matched_duration_episode_and_preserves_messages(
    database, tmp_path
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "temporal-artifacts")
    catalog = ArtifactCatalog(artifacts)
    registry = CorpusRegistry()
    states = StateStore()
    experiments = ExperimentEngine(states)
    memory = LessonMemory()
    runs = RunStore()
    generator = TemporalGroundingCorpusGenerator()
    created_at = datetime(2026, 8, 9, tzinfo=UTC)
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=211,
        groups_per_family=1,
        siblings_per_group=3,
        families=(TemporalScenarioFamily.DURATION_CALIBRATION,),
        created_at=created_at,
    )
    expected_by_item = {
        item.item_id: TemporalDecision.model_validate(
            item.expected_answer["decision"],
            strict=False,  # type: ignore[index]
        )
        for item in items
    }

    def student_response(request) -> str:
        expected = expected_by_item[request.metadata["item_id"]]
        correct = ":revision" in request.request_id or ":transfer:treatment" in request.request_id
        if correct:
            return expected.model_copy(
                update={"response": "The recorded duration profile supports this schedule."}
            ).model_dump_json()
        wrong_action = TemporalAction(
            kind=TemporalActionKind.CONTINUE,
            subject="unverified_operation",
            rationale_code="ignore_duration_evidence",
        )
        return expected.model_copy(
            update={
                "actions": (wrong_action,),
                "duration_forecasts": (),
                "next_check_at": None,
                "response": "Continue immediately without checking the timing evidence.",
            }
        ).model_dump_json()

    def teacher_response(request) -> str:
        payload = json.loads(str(request.input))
        grade = payload["deterministic_grade"]
        evidence_id = next(
            item["evidence_id"] for item in grade["evidence"] if item["kind"] == "deterministic"
        )
        return json.dumps(
            {
                "citations": [{"evidence_id": evidence_id}],
                "claimed_first_consequential_error": grade["first_invalid_step_id"],
                "error_class": grade["error_class"],
                "lesson": "Use the active operation's duration profile and next safe poll time.",
                "repair": "Select a wait or poll action supported by the recorded timing evidence.",
                "expected_transfer_scope": "Unseen temporal duration-calibration scenarios.",
                "confidence": 0.8,
            },
            sort_keys=True,
        )

    student = CallbackGenerationClient(student_response, "student-test")
    teacher = CallbackGenerationClient(teacher_response, "teacher-test")
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=created_at):
            await registry.register_competency(session, competency)
        await registry.register_items(session, items)
        state = await states.create_student(
            session,
            student_id="temporal-student",
            checkpoint_id="checkpoint-test",
            runtime_id="runtime-test",
            research_role=ResearchRole.TARGET,
            initial_working_state={"prior": "persistent temporal cognition"},
        )
        run_id = await runs.create(
            session,
            run_id="run-temporal-developmental",
            payload={
                "student_id": "temporal-student",
                "state_id": state.state_id,
                "domain_id": "temporal.grounding",
                "pool": CorpusPool.CURRICULUM.value,
                "experiment_seed": 43,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )

    handler = DomainDevelopmentalWorkflowHandler(
        authority=TemporalGroundingDevelopmentalAuthority(),
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(catalog),
        experiments=experiments,
        provenance=ProvenanceLedger(code_revision="test", environment="test"),
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
        student_role=ResearchRole.TARGET,
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id="worker-test",
        idle_poll_seconds=0,
    )

    actions = await supervisor.run(budget=64)

    assert actions == 10
    assert len(student.calls) == 4
    assert len(teacher.calls) == 1
    async with database.transaction() as session:
        run = await session.get(RunRow, run_id)
        assert run is not None and run.state == RunState.COMPLETE.value
        episode = await session.get(EpisodeRow, f"episode-{run_id}")
        assert episode is not None
        assert episode.record_json["teaching_outcome"] == "successful_transfer"
        cold_attempt = await session.scalar(
            select(AttemptRow).where(AttemptRow.attempt_id.like("attempt-cold-%"))
        )
        assert cold_attempt is not None
        rendered = cold_attempt.record_json["rendered_messages"]
        assert [message["role"] for message in rendered] == [
            "developer",
            "user",
            "assistant",
            "user",
        ]
        assert "padawan_temporal_context" in rendered[0]["content"]
        assert await session.scalar(select(func.count()).select_from(OperationSpanRow)) == 5
