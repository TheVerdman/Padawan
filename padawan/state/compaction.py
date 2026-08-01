from __future__ import annotations

from typing import Any, Literal

from padawan.models.contracts import CompactedStateRecord, Hypothesis, StudentStateRecord
from padawan.models.hashing import sha256_digest


class CompactionValidationError(RuntimeError):
    pass


class StateCompactor:
    """Deterministic compaction that preserves failures, uncertainty, and source references."""

    def compact(
        self,
        *,
        state: StudentStateRecord,
        episode_summaries: tuple[dict[str, Any], ...],
        trigger: Literal[
            "token_budget", "episode_count", "memory_pressure", "manual", "experiment"
        ],
        maximum_recent_episodes: int = 32,
    ) -> CompactedStateRecord:
        if maximum_recent_episodes <= 0:
            raise ValueError("maximum_recent_episodes must be positive")
        replay_input = {
            "state": state.model_dump(mode="json"),
            "episode_summaries": list(episode_summaries),
            "trigger": trigger,
            "maximum_recent_episodes": maximum_recent_episodes,
        }
        replay_digest = sha256_digest(replay_input)
        ordered = sorted(
            episode_summaries,
            key=lambda item: (str(item.get("completed_at", "")), str(item.get("episode_id", ""))),
        )
        kept = ordered[-maximum_recent_episodes:]
        dropped_input = ordered[:-maximum_recent_episodes]
        validated_lessons = sorted(
            {
                str(lesson_id)
                for summary in ordered
                for lesson_id in summary.get("validated_lesson_ids", [])
                if lesson_id
            }
            | set(state.lesson_memory_refs)
        )
        failed_strategies = tuple(
            {
                "episode_id": summary.get("episode_id"),
                "strategy": failure,
                "provenance_refs": summary.get("provenance_event_ids", []),
            }
            for summary in ordered
            for failure in summary.get("failed_strategies", [])
        )
        confidence_notes = tuple(
            str(note)
            for summary in ordered
            for note in summary.get("uncertainty", [])
            if str(note).strip()
        )
        provenance_refs = tuple(
            sorted(
                {
                    str(reference)
                    for summary in ordered
                    for reference in summary.get("provenance_event_ids", [])
                }
            )
        )
        dropped = tuple(
            {
                "episode_id": summary.get("episode_id"),
                "reason": "outside recent episode window; raw episode remains immutable",
                "preserved_failure_count": len(summary.get("failed_strategies", [])),
                "preserved_lesson_count": len(summary.get("validated_lesson_ids", [])),
            }
            for summary in dropped_input
        )
        hypotheses = tuple(state.unresolved_hypotheses)
        record = CompactedStateRecord(
            compacted_state_id=f"compact-{replay_digest[7:31]}",
            state_id=state.state_id,
            source_episode_ids=tuple(str(item.get("episode_id")) for item in kept),
            validated_lessons=tuple(validated_lessons),
            unresolved_hypotheses=hypotheses,
            failed_strategies=failed_strategies,
            confidence_notes=confidence_notes,
            provenance_refs=provenance_refs,
            dropped=dropped,
            trigger=trigger,
            replay_input_digest=replay_digest,
            created_at=state.created_at,
        )
        self.validate(record, replay_input=replay_input)
        return record

    @staticmethod
    def validate(record: CompactedStateRecord, *, replay_input: dict[str, Any]) -> None:
        if record.replay_input_digest != sha256_digest(replay_input):
            raise CompactionValidationError("compaction replay digest mismatch")
        input_episodes = {str(item.get("episode_id")) for item in replay_input["episode_summaries"]}
        if not set(record.source_episode_ids).issubset(input_episodes):
            raise CompactionValidationError("compaction cites an unknown episode")
        for failure in record.failed_strategies:
            if failure.get("episode_id") not in input_episodes:
                raise CompactionValidationError("failed strategy lost its source episode")


def hypothesis_from_payload(payload: dict[str, Any]) -> Hypothesis:
    return Hypothesis.model_validate(payload, strict=False)
