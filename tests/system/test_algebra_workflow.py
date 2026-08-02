from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import func, select

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.models.contracts import CorpusPool, ResearchRole, RunState
from padawan.models.tables import (
    AttemptRow,
    EpisodeRow,
    ExposureRow,
    ExternalCallRow,
    LessonVersionRow,
    ProvenanceEventRow,
    RetrievalDecisionRow,
    RunRow,
    StudentRow,
    StudentStateRow,
)
from padawan.provenance.ledger import ProvenanceLedger
from tests.helpers import build_test_workflow


async def test_complete_crash_resumable_matched_episode(database, tmp_path) -> None:
    _, supervisor, runs, student, teacher, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    async with database.transaction() as session:
        run_id = await runs.create(
            session,
            run_id="run-system",
            payload={
                "student_id": "student-test",
                "state_id": state_id,
                "pool": "curriculum",
                "experiment_seed": 17,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )

    actions = await supervisor.run(budget=256)
    assert actions == 20
    assert len(student.calls) == 4
    assert len(teacher.calls) == 1
    assert all(call.store is False for call in (*student.calls, *teacher.calls))

    async with database.transaction() as session:
        run = await session.get(RunRow, run_id)
        assert run is not None
        assert run.state == RunState.COMPLETE.value
        assert run.sequence == 20
        episode = await session.get(EpisodeRow, f"episode-{run_id}")
        assert episode is not None
        assert episode.status == "complete"
        assert episode.record_json["teaching_outcome"] == "successful_transfer"
        assert episode.record_json["memory_writes"][0]["action"] == "write"
        assert await session.scalar(select(func.count()).select_from(ExposureRow)) == 4
        assert await session.scalar(select(func.count()).select_from(LessonVersionRow)) == 1
        assert await session.scalar(select(func.count()).select_from(ExternalCallRow)) == 5
        assert await session.scalar(select(func.count()).select_from(RetrievalDecisionRow)) == 5
        assert await session.scalar(select(func.count()).select_from(ProvenanceEventRow)) >= 10
        student_row = await session.get(StudentRow, "student-test")
        assert student_row is not None
        assert student_row.canonical_state_id != state_id
        final_state = await session.get(StudentStateRow, student_row.canonical_state_id)
        assert final_state is not None
        assert final_state.parent_state_id == run.payload["treatment_state_id"]
        verification = await ProvenanceLedger(
            code_revision="ignored", environment="ignored"
        ).verify(session)
        assert verification.valid, verification.errors


async def test_validated_lesson_content_reaches_the_next_episode(database, tmp_path) -> None:
    handler, supervisor, runs, student, _, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    async with database.transaction() as session:
        await runs.create(
            session,
            run_id="run-learning-one",
            payload={
                "student_id": "student-test",
                "state_id": state_id,
                "pool": "curriculum",
                "experiment_seed": 17,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )
    await supervisor.run(budget=256)

    generator = AlgebraCorpusGenerator()
    next_items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=141,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AlgebraFamily.DISTRIBUTION_SIGN,),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    expected_by_prompt = student.data["expected_by_prompt"]
    expected_by_prompt.update({item.prompt: item.expected_answer for item in next_items})
    async with database.transaction() as session:
        await handler.registry.register_items(session, next_items)
        student_row = await session.get(StudentRow, "student-test")
        assert student_row is not None and student_row.canonical_state_id is not None
        await runs.create(
            session,
            run_id="run-learning-two",
            payload={
                "student_id": "student-test",
                "state_id": student_row.canonical_state_id,
                "pool": "curriculum",
                "experiment_seed": 18,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )
    await supervisor.run(budget=256)

    second_cold = next(
        call for call in student.calls if call.request_id == "run-learning-two:student:cold"
    )
    context = json.loads(str(second_cold.input))
    selected = context["retrieved_lesson_memory"]["selected"]
    assert len(selected) == 1
    assert selected[0]["general_rule"] == (
        "Verify the final result against the original algebraic relation."
    )
    assert selected[0]["evidence_ids"]


async def test_baseline_role_is_persisted_and_cannot_consolidate_memory(database, tmp_path) -> None:
    _, supervisor, runs, _, _, state_id = await build_test_workflow(
        database,
        tmp_path / "artifacts",
        research_role=ResearchRole.BASELINE,
    )
    async with database.transaction() as session:
        run_id = await runs.create(
            session,
            run_id="run-baseline",
            payload={
                "student_id": "student-test",
                "state_id": state_id,
                "research_role": ResearchRole.BASELINE.value,
                "pool": "curriculum",
                "experiment_seed": 19,
                "teacher_mode": "diagnostic_critique",
                "treatment_condition": "frontier_teacher_critique",
                "control_condition": "no_intervention",
            },
        )

    await supervisor.run(budget=256)

    async with database.transaction() as session:
        run = await session.get(RunRow, run_id)
        assert run is not None
        assert run.research_role == ResearchRole.BASELINE.value
        episode = await session.get(EpisodeRow, f"episode-{run_id}")
        assert episode is not None
        assert episode.record_json["research_role"] == ResearchRole.BASELINE.value
        assert episode.record_json["memory_writes"][0]["action"] == "none"
        assert episode.record_json["memory_writes"][0]["reason"] == (
            "baseline roles cannot consolidate target memory"
        )
        assert await session.scalar(select(func.count()).select_from(LessonVersionRow)) == 0
        stored_attempts = (
            await session.scalars(
                select(AttemptRow).where(AttemptRow.episode_id == f"episode-{run_id}")
            )
        ).all()
        assert stored_attempts
        assert all(row.research_role == ResearchRole.BASELINE.value for row in stored_attempts)
