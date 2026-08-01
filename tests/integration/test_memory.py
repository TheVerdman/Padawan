from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from padawan.memory.lessons import LessonMemory, LessonPolicyError
from padawan.models.contracts import LessonRecord, LessonStatus
from padawan.models.tables import RetrievalDecisionRow, ReviewQueueRow


def _lesson(
    lesson_id: str,
    *,
    rule: str = "Preserve every original denominator exclusion.",
    competency: str = "algebra.rational",
    error_class: str = "missing_exclusion",
    branch: str = "branch-a",
    status: LessonStatus = LessonStatus.VALIDATED,
) -> LessonRecord:
    return LessonRecord(
        lesson_id=lesson_id,
        version=1,
        competency_id=competency,
        error_class=error_class,
        general_rule=rule,
        applicability="rational expressions with cancelled factors",
        exclusions=("not polynomial-only tasks",),
        evidence_ids=("evidence-1",),
        source_episode_ids=("episode-source",),
        teacher_id="teacher",
        confidence=0.8,
        successful_transfer_count=1,
        failed_transfer_count=0,
        harmful_retrieval_count=0,
        state_lineage_id="lineage-a",
        branch_id=branch,
        status=status,
        created_at=datetime.now(UTC),
    )


async def test_only_validated_lessons_are_retrieved_and_branch_isolation_holds(database) -> None:
    memory = LessonMemory()
    async with database.transaction() as session:
        await memory.write(session, _lesson("validated"))
        await memory.write(
            session,
            _lesson("candidate", status=LessonStatus.CANDIDATE),
            require_validated_evidence=False,
        )
        result = await memory.retrieve(
            session,
            state_id="state-a",
            state_lineage_id="lineage-a",
            branch_id="branch-a",
            query_text="preserve denominator exclusions",
            competency_id="algebra.rational",
        )
        assert [candidate.lesson.lesson_id for candidate in result.selected] == ["validated"]
        candidate = next(
            item for item in result.all_candidates if item.lesson.lesson_id == "candidate"
        )
        assert candidate.rejection_reason == "status=candidate"
        isolated = await memory.retrieve(
            session,
            state_id="state-b",
            state_lineage_id="lineage-a",
            branch_id="branch-b",
            query_text="preserve denominator exclusions",
            competency_id="algebra.rational",
        )
        assert isolated.selected == ()
        assert any(
            item.rejection_reason == "post-fork branch isolation"
            for item in isolated.all_candidates
        )


async def test_invalidation_and_harmful_history_change_retrieval_policy(database) -> None:
    memory = LessonMemory()
    async with database.transaction() as session:
        await memory.write(session, _lesson("invalidated"))
        invalidated = await memory.invalidate(
            session,
            lesson_id="invalidated",
            reason="counterexample",
            source_episode_id="episode-counterexample",
        )
        assert invalidated.status == LessonStatus.INVALIDATED
        await memory.write(session, _lesson("harmful"))
        await memory.record_retrieval_outcome(
            session,
            lesson_id="harmful",
            successful_transfer=False,
            harmful=True,
            source_episode_id="episode-harm-1",
        )
        harmful = await memory.record_retrieval_outcome(
            session,
            lesson_id="harmful",
            successful_transfer=False,
            harmful=True,
            source_episode_id="episode-harm-2",
        )
        assert harmful.status == LessonStatus.REVIEW_REQUIRED
        result = await memory.retrieve(
            session,
            state_id="state",
            state_lineage_id="lineage-a",
            branch_id="branch-a",
            query_text="denominator exclusion",
        )
        assert result.selected == ()
        reasons = {item.lesson.lesson_id: item.rejection_reason for item in result.all_candidates}
        assert reasons["invalidated"] == "status=invalidated"
        assert reasons["harmful"] == "status=review_required"


async def test_conflicting_validated_lessons_trigger_review(database) -> None:
    memory = LessonMemory()
    async with database.transaction() as session:
        await memory.write(session, _lesson("first", rule="Always preserve exclusions."))
        conflicting = await memory.write(
            session, _lesson("second", rule="Never retain denominator restrictions.")
        )
        assert conflicting.status == LessonStatus.REVIEW_REQUIRED
        review = await session.get(ReviewQueueRow, f"review-{conflicting.lesson_id}")
        # Review identifiers are UUID-backed, so query via relationship fields instead.
        if review is None:
            from sqlalchemy import select

            review = await session.scalar(
                select(ReviewQueueRow).where(ReviewQueueRow.subject_id == "second")
            )
        assert review is not None and review.status == "open"


async def test_rollback_restores_snapshot_and_keeps_source_provenance(database) -> None:
    memory = LessonMemory()
    async with database.transaction() as session:
        original = await memory.write(session, _lesson("stable"))
        snapshot = await memory.snapshot(
            session,
            student_id="student",
            state_lineage_id="lineage-a",
            branch_id="branch-a",
            reason="before update",
        )
        await memory.record_retrieval_outcome(
            session,
            lesson_id="stable",
            successful_transfer=False,
            harmful=True,
            source_episode_id="episode-bad-update",
        )
        await memory.write(
            session,
            _lesson(
                "new-after-snapshot",
                competency="algebra.linear",
                error_class="sign_error",
                rule="Track negative signs.",
            ),
        )
        rolled_back = await memory.rollback(
            session, snapshot_id=snapshot, source_episode_id="episode-rollback"
        )
        assert rolled_back == ("new-after-snapshot", "stable")
        restored = await memory.latest(session, lesson_id="stable")
        removed = await memory.latest(session, lesson_id="new-after-snapshot")
        assert restored is not None and restored.status == LessonStatus.VALIDATED
        assert restored.general_rule == original.general_rule
        assert restored.successful_transfer_count == original.successful_transfer_count
        assert "episode-source" in restored.source_episode_ids
        assert "episode-rollback" in restored.source_episode_ids
        assert removed is not None and removed.status == LessonStatus.INVALIDATED


async def test_retrieval_decision_replay_is_idempotent(database) -> None:
    memory = LessonMemory()
    async with database.transaction() as session:
        await memory.write(session, _lesson("replayable"))
        arguments = {
            "state_id": "state-a",
            "state_lineage_id": "lineage-a",
            "branch_id": "branch-a",
            "query_text": "preserve denominator exclusions",
            "competency_id": "algebra.rational",
            "retrieval_id": "retrieval-stable",
        }
        first = await memory.retrieve(session, **arguments)
        second = await memory.retrieve(session, **arguments)
        assert second == first
        assert await session.scalar(select(func.count()).select_from(RetrievalDecisionRow)) == 1
        with pytest.raises(LessonPolicyError, match="replay conflicts"):
            await memory.retrieve(session, **{**arguments, "query_text": "different query"})
