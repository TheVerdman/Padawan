from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.models.contracts import Hypothesis, LifecycleStatus, StudentStateRecord
from padawan.models.hashing import sha256_digest
from padawan.state.compaction import CompactionValidationError, StateCompactor


def _state() -> StudentStateRecord:
    semantic = {
        "state_id": "state-compact",
        "student_id": "student",
        "checkpoint_id": "checkpoint",
        "runtime_id": "runtime",
        "parent_state_id": None,
        "branch_id": "branch",
        "compacted_working_state": {"working": "state"},
        "lesson_memory_refs": ["lesson-prior"],
        "unresolved_hypotheses": [
            Hypothesis(
                hypothesis_id="hypothesis",
                statement="This remains uncertain.",
                confidence=0.4,
            ).model_dump(mode="json")
        ],
        "competency_estimates": [],
        "active_experiment_id": None,
        "creation_reason": "test",
        "lifecycle_status": LifecycleStatus.CANONICAL.value,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
    }
    return StudentStateRecord.model_validate(
        {**semantic, "state_hash": sha256_digest(semantic)}, strict=False
    )


def test_compaction_is_replayable_and_preserves_failures_uncertainty_and_provenance() -> None:
    summaries = (
        {
            "episode_id": "episode-1",
            "completed_at": "2026-01-01T00:00:00Z",
            "validated_lesson_ids": ["lesson-1"],
            "failed_strategies": ["cancelled terms without preserving exclusion"],
            "uncertainty": ["whether the rule generalizes"],
            "provenance_event_ids": ["event-1"],
        },
        {
            "episode_id": "episode-2",
            "completed_at": "2026-01-02T00:00:00Z",
            "validated_lesson_ids": [],
            "failed_strategies": ["guessed the second branch"],
            "uncertainty": ["branch count"],
            "provenance_event_ids": ["event-2"],
        },
    )
    compactor = StateCompactor()
    first = compactor.compact(
        state=_state(),
        episode_summaries=summaries,
        trigger="episode_count",
        maximum_recent_episodes=1,
    )
    second = compactor.compact(
        state=_state(),
        episode_summaries=summaries,
        trigger="episode_count",
        maximum_recent_episodes=1,
    )
    assert first == second
    assert first.source_episode_ids == ("episode-2",)
    assert {item["episode_id"] for item in first.failed_strategies} == {
        "episode-1",
        "episode-2",
    }
    assert first.validated_lessons == ("lesson-1", "lesson-prior")
    assert first.confidence_notes == ("whether the rule generalizes", "branch count")
    assert first.provenance_refs == ("event-1", "event-2")
    assert first.dropped[0]["preserved_failure_count"] == 1


def test_compaction_replay_digest_rejects_changed_source() -> None:
    compactor = StateCompactor()
    summaries = ({"episode_id": "episode", "failed_strategies": []},)
    record = compactor.compact(state=_state(), episode_summaries=summaries, trigger="manual")
    replay = {
        "state": _state().model_dump(mode="json"),
        "episode_summaries": [{"episode_id": "different"}],
        "trigger": "manual",
        "maximum_recent_episodes": 32,
    }
    with pytest.raises(CompactionValidationError, match="digest"):
        compactor.validate(record, replay_input=replay)
