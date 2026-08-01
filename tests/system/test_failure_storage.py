from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import func, select

from padawan.adapters.base import ModelProviderError
from padawan.models.contracts import CorpusPool, DevelopmentalEpisode, ItemStatus, RunState
from padawan.models.tables import (
    ArtifactReferenceRow,
    CorpusItemRow,
    EpisodeRow,
    ExposureRow,
    ExternalCallRow,
    ProvenanceEventRow,
    RunRow,
)
from tests.helpers import build_test_workflow, public_derivation


class FailingProvider:
    async def generate(self, _request):
        raise ModelProviderError(
            "real provider rejected the request",
            provider="student-test",
            status_code=400,
            retryable=False,
            response_body=b'{"error":"bad request"}',
        )


async def _create_run(database, runs, state_id: str, run_id: str) -> None:
    async with database.transaction() as session:
        await runs.create(
            session,
            run_id=run_id,
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


async def test_provider_failure_after_episode_start_is_a_valid_failed_episode(
    database, tmp_path
) -> None:
    handler, supervisor, runs, _, _, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    handler.student_calls.client = FailingProvider()
    await _create_run(database, runs, state_id, "run-provider-failure")
    actions = await supervisor.run(budget=256)
    assert actions == 4
    async with database.transaction() as session:
        run = await session.get(RunRow, "run-provider-failure")
        episode = await session.get(EpisodeRow, "episode-run-provider-failure")
        call = await session.scalar(
            select(ExternalCallRow).where(ExternalCallRow.run_id == "run-provider-failure")
        )
        assert run is not None and run.state == RunState.FAILED_TERMINAL.value
        assert episode is not None and episode.status == "failed"
        record = DevelopmentalEpisode.model_validate(episode.record_json, strict=False)
        assert [outcome.value for outcome in record.system_outcomes] == ["provider_failure"]
        assert record.exposure_ids == ("exposure-run-provider-failure-cold-prompt",)
        assert call is not None and call.status == "failed_terminal"
        assert await session.scalar(select(func.count()).select_from(ExposureRow)) == 1
        assert all(
            row.status == ItemStatus.ACTIVE.value and row.lease_token is None
            for row in (await session.scalars(select(CorpusItemRow))).all()
        )
        same_student = await handler.registry.lease_matched_group(
            session,
            owner="new-run-same-student",
            pool=CorpusPool.CURRICULUM,
            count=3,
            lease_for=handler.lease_for,
            student_id="student-test",
        )
        other_student = await handler.registry.lease_matched_group(
            session,
            owner="new-run-other-student",
            pool=CorpusPool.CURRICULUM,
            count=3,
            lease_for=handler.lease_for,
            student_id="other-student",
        )
        assert same_student == ()
        assert len(other_student) == 3


async def test_exhausted_teacher_retries_close_review_episode_and_keep_failures(
    database, tmp_path
) -> None:
    _, supervisor, runs, _, teacher, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    teacher.callback = lambda _request: "malformed teacher output"
    await _create_run(database, runs, state_id, "run-teacher-rejected")
    await supervisor.run(budget=256)
    assert len(teacher.calls) == 3
    async with database.transaction() as session:
        run = await session.get(RunRow, "run-teacher-rejected")
        episode = await session.get(EpisodeRow, "episode-run-teacher-rejected")
        assert run is not None and run.state == RunState.REVIEW_REQUIRED.value
        assert episode is not None and episode.status == "review_required"
        record = DevelopmentalEpisode.model_validate(episode.record_json, strict=False)
        assert record.teaching_outcome is not None
        assert record.teaching_outcome.value == "unsupported_critique"
        assert record.exposure_ids == ("exposure-run-teacher-rejected-cold-prompt",)
        failure_refs = await session.scalar(
            select(func.count())
            .select_from(ArtifactReferenceRow)
            .where(ArtifactReferenceRow.owner_type == "teacher_failure")
        )
        assert failure_refs == 3
        rejection_events = await session.scalar(
            select(func.count())
            .select_from(ProvenanceEventRow)
            .where(ProvenanceEventRow.event_type == "TeacherResponsesRejected")
        )
        assert rejection_events == 1


async def test_harmful_intervention_outcome_remains_in_completed_episode(
    database, tmp_path
) -> None:
    _, supervisor, runs, student, _, state_id = await build_test_workflow(
        database, tmp_path / "artifacts"
    )
    expected_by_prompt = cast(dict[str, dict[str, Any]], student.data["expected_by_prompt"])

    def harmful_callback(request):
        context = json.loads(str(request.input))
        task = context["task"]
        expected = expected_by_prompt[task]
        correct = ":revision" in request.request_id or ":transfer:control" in request.request_id
        return public_derivation(expected, correct=correct)

    student.callback = harmful_callback
    await _create_run(database, runs, state_id, "run-harmful")
    await supervisor.run(budget=256)
    async with database.transaction() as session:
        episode = await session.get(EpisodeRow, "episode-run-harmful")
        assert episode is not None and episode.status == "complete"
        assert episode.record_json["teaching_outcome"] == "harmful_intervention"
        assert episode.record_json["memory_writes"][0]["action"] == "none"
