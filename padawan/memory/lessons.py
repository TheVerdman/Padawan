from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import LessonRecord, LessonStatus
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    LessonVersionRow,
    MemorySnapshotRow,
    RetrievalDecisionRow,
    ReviewQueueRow,
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


class LessonPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalCandidate:
    lesson: LessonRecord
    relevance: float
    trust: float
    score: float
    selected: bool
    rejection_reason: str | None


@dataclass(frozen=True)
class RetrievalResult:
    retrieval_id: str
    selected: tuple[RetrievalCandidate, ...]
    all_candidates: tuple[RetrievalCandidate, ...]


class LessonMemory:
    """Versioned lesson memory with branch isolation and negative retrieval evidence."""

    async def write(
        self,
        session: AsyncSession,
        lesson: LessonRecord,
        *,
        require_validated_evidence: bool = True,
    ) -> LessonRecord:
        if lesson.status == LessonStatus.VALIDATED and require_validated_evidence:
            if not lesson.evidence_ids or not lesson.source_episode_ids:
                raise LessonPolicyError("validated lessons require evidence and source episodes")
            if lesson.successful_transfer_count < 1:
                raise LessonPolicyError(
                    "validated lessons require at least one successful transfer"
                )
        latest = await self.latest(session, lesson_id=lesson.lesson_id)
        if latest is not None:
            if lesson.version != latest.version + 1:
                raise LessonPolicyError("lesson versions must advance exactly once")
            if lesson.supersedes_version != latest.version:
                raise LessonPolicyError("lesson update must identify the superseded version")
        elif lesson.version != 1:
            raise LessonPolicyError("a new lesson must begin at version 1")

        conflict = await session.scalar(
            select(LessonVersionRow).where(
                LessonVersionRow.competency_id == lesson.competency_id,
                LessonVersionRow.error_class == lesson.error_class,
                LessonVersionRow.status == LessonStatus.VALIDATED.value,
                LessonVersionRow.lesson_id != lesson.lesson_id,
            )
        )
        stored = lesson
        if conflict is not None and not _rules_compatible(
            conflict.general_rule, lesson.general_rule
        ):
            stored = lesson.model_copy(update={"status": LessonStatus.REVIEW_REQUIRED})
            session.add(
                ReviewQueueRow(
                    review_id=f"review-{uuid4()}",
                    review_type="lesson_conflict",
                    subject_id=lesson.lesson_id,
                    reason=f"conflicts with {conflict.lesson_id} v{conflict.version}",
                    evidence={
                        "existing_rule": conflict.general_rule,
                        "proposed_rule": lesson.general_rule,
                    },
                    status="open",
                    created_at=datetime.now(UTC),
                    resolved_at=None,
                )
            )
        session.add(_record_to_row(stored))
        await session.flush()
        return stored

    async def latest(self, session: AsyncSession, *, lesson_id: str) -> LessonRecord | None:
        row = await session.scalar(
            select(LessonVersionRow)
            .where(LessonVersionRow.lesson_id == lesson_id)
            .order_by(LessonVersionRow.version.desc())
            .limit(1)
        )
        return _row_to_record(row) if row is not None else None

    async def invalidate(
        self,
        session: AsyncSession,
        *,
        lesson_id: str,
        reason: str,
        source_episode_id: str,
    ) -> LessonRecord:
        latest = await self.latest(session, lesson_id=lesson_id)
        if latest is None:
            raise KeyError(lesson_id)
        invalidated = latest.model_copy(
            update={
                "version": latest.version + 1,
                "status": LessonStatus.INVALIDATED,
                "supersedes_version": latest.version,
                "source_episode_ids": tuple(
                    dict.fromkeys((*latest.source_episode_ids, source_episode_id))
                ),
                "exclusions": (*latest.exclusions, f"invalidated: {reason}"),
                "created_at": datetime.now(UTC),
            }
        )
        return await self.write(session, invalidated, require_validated_evidence=False)

    async def retrieve(
        self,
        session: AsyncSession,
        *,
        state_id: str,
        state_lineage_id: str,
        branch_id: str,
        query_text: str,
        competency_id: str | None = None,
        error_class: str | None = None,
        inherited_lesson_ids: tuple[str, ...] = (),
        limit: int = 5,
        minimum_trust: float = 0.25,
        retrieval_id: str | None = None,
    ) -> RetrievalResult:
        if limit <= 0:
            raise ValueError("limit must be positive")
        latest_versions = (
            select(
                LessonVersionRow.lesson_id,
                func.max(LessonVersionRow.version).label("max_version"),
            )
            .group_by(LessonVersionRow.lesson_id)
            .subquery()
        )
        query = select(LessonVersionRow).join(
            latest_versions,
            (LessonVersionRow.lesson_id == latest_versions.c.lesson_id)
            & (LessonVersionRow.version == latest_versions.c.max_version),
        )
        if competency_id is not None:
            query = query.where(LessonVersionRow.competency_id == competency_id)
        if error_class is not None:
            query = query.where(LessonVersionRow.error_class == error_class)
        rows = (await session.scalars(query)).all()
        inherited = set(inherited_lesson_ids)
        candidates: list[RetrievalCandidate] = []
        for row in rows:
            lesson = _row_to_record(row)
            rejection: str | None = None
            if lesson.status != LessonStatus.VALIDATED:
                rejection = f"status={lesson.status.value}"
            elif lesson.state_lineage_id != state_lineage_id:
                rejection = "different student state lineage"
            elif lesson.branch_id != branch_id and lesson.lesson_id not in inherited:
                rejection = "post-fork branch isolation"
            relevance = _cosine(_embedding(query_text), _embedding(row.search_text))
            transfer_total = lesson.successful_transfer_count + lesson.failed_transfer_count
            transfer_rate = (
                lesson.successful_transfer_count / transfer_total if transfer_total else 0.0
            )
            harmful_penalty = min(0.8, 0.2 * lesson.harmful_retrieval_count)
            trust = max(
                0.0,
                min(1.0, lesson.confidence * 0.65 + transfer_rate * 0.35 - harmful_penalty),
            )
            if rejection is None and trust < minimum_trust:
                rejection = "trust below threshold"
            score = relevance * 0.55 + trust * 0.45
            candidates.append(
                RetrievalCandidate(
                    lesson=lesson,
                    relevance=relevance,
                    trust=trust,
                    score=score,
                    selected=False,
                    rejection_reason=rejection,
                )
            )
        ranked = sorted(candidates, key=lambda item: (-item.score, item.lesson.lesson_id))
        selected_ids = {
            item.lesson.lesson_id
            for item in [candidate for candidate in ranked if candidate.rejection_reason is None][
                :limit
            ]
        }
        finalized = tuple(
            RetrievalCandidate(
                lesson=item.lesson,
                relevance=item.relevance,
                trust=item.trust,
                score=item.score,
                selected=item.lesson.lesson_id in selected_ids,
                rejection_reason=item.rejection_reason,
            )
            for item in ranked
        )
        assigned_retrieval_id = retrieval_id or f"retrieval-{uuid4()}"
        candidate_ids = [item.lesson.lesson_id for item in finalized]
        retrieved_ids = [item.lesson.lesson_id for item in finalized if item.selected]
        rejected = {
            item.lesson.lesson_id: item.rejection_reason
            for item in finalized
            if item.rejection_reason is not None
        }
        existing = await session.get(RetrievalDecisionRow, assigned_retrieval_id)
        if existing is not None:
            if (
                existing.state_id != state_id
                or existing.branch_id != branch_id
                or existing.query_text != query_text
                or existing.candidate_lesson_ids != candidate_ids
                or existing.retrieved_lesson_ids != retrieved_ids
                or existing.rejected != rejected
            ):
                raise LessonPolicyError("retrieval replay conflicts with persisted decision")
        else:
            session.add(
                RetrievalDecisionRow(
                    retrieval_id=assigned_retrieval_id,
                    state_id=state_id,
                    branch_id=branch_id,
                    query_text=query_text,
                    candidate_lesson_ids=candidate_ids,
                    retrieved_lesson_ids=retrieved_ids,
                    rejected=rejected,
                    created_at=datetime.now(UTC),
                )
            )
        await session.flush()
        return RetrievalResult(
            retrieval_id=assigned_retrieval_id,
            selected=tuple(item for item in finalized if item.selected),
            all_candidates=finalized,
        )

    async def record_retrieval_outcome(
        self,
        session: AsyncSession,
        *,
        lesson_id: str,
        successful_transfer: bool,
        harmful: bool,
        source_episode_id: str,
    ) -> LessonRecord:
        latest = await self.latest(session, lesson_id=lesson_id)
        if latest is None:
            raise KeyError(lesson_id)
        updated = latest.model_copy(
            update={
                "version": latest.version + 1,
                "successful_transfer_count": latest.successful_transfer_count
                + int(successful_transfer),
                "failed_transfer_count": latest.failed_transfer_count
                + int(not successful_transfer),
                "harmful_retrieval_count": latest.harmful_retrieval_count + int(harmful),
                "source_episode_ids": tuple(
                    dict.fromkeys((*latest.source_episode_ids, source_episode_id))
                ),
                "supersedes_version": latest.version,
                "created_at": datetime.now(UTC),
            }
        )
        if harmful and updated.harmful_retrieval_count >= 2:
            updated = updated.model_copy(update={"status": LessonStatus.REVIEW_REQUIRED})
        return await self.write(session, updated, require_validated_evidence=False)

    async def snapshot(
        self,
        session: AsyncSession,
        *,
        student_id: str,
        state_lineage_id: str,
        branch_id: str,
        reason: str,
    ) -> str:
        latest_versions = (
            select(
                LessonVersionRow.lesson_id,
                func.max(LessonVersionRow.version).label("max_version"),
            )
            .where(
                LessonVersionRow.state_lineage_id == state_lineage_id,
                LessonVersionRow.branch_id == branch_id,
            )
            .group_by(LessonVersionRow.lesson_id)
            .subquery()
        )
        rows = (
            await session.scalars(
                select(LessonVersionRow)
                .join(
                    latest_versions,
                    (LessonVersionRow.lesson_id == latest_versions.c.lesson_id)
                    & (LessonVersionRow.version == latest_versions.c.max_version),
                )
                .where(LessonVersionRow.status == LessonStatus.VALIDATED.value)
                .order_by(LessonVersionRow.lesson_id)
            )
        ).all()
        snapshot_id = f"memsnap-{uuid4()}"
        session.add(
            MemorySnapshotRow(
                snapshot_id=snapshot_id,
                student_id=student_id,
                state_lineage_id=state_lineage_id,
                branch_id=branch_id,
                active_lesson_versions=[row.lesson_version_id for row in rows],
                reason=reason,
                created_at=datetime.now(UTC),
            )
        )
        await session.flush()
        return snapshot_id

    async def rollback(
        self,
        session: AsyncSession,
        *,
        snapshot_id: str,
        source_episode_id: str,
    ) -> tuple[str, ...]:
        snapshot = await session.get(MemorySnapshotRow, snapshot_id)
        if snapshot is None:
            raise KeyError(snapshot_id)
        target_rows = (
            await session.scalars(
                select(LessonVersionRow).where(
                    LessonVersionRow.lesson_version_id.in_(snapshot.active_lesson_versions)
                )
            )
        ).all()
        targets = {row.lesson_id: row for row in target_rows}
        latest_versions = (
            select(
                LessonVersionRow.lesson_id,
                func.max(LessonVersionRow.version).label("max_version"),
            )
            .where(
                LessonVersionRow.state_lineage_id == snapshot.state_lineage_id,
                LessonVersionRow.branch_id == snapshot.branch_id,
            )
            .group_by(LessonVersionRow.lesson_id)
            .subquery()
        )
        current_rows = (
            await session.scalars(
                select(LessonVersionRow)
                .join(
                    latest_versions,
                    (LessonVersionRow.lesson_id == latest_versions.c.lesson_id)
                    & (LessonVersionRow.version == latest_versions.c.max_version),
                )
                .where(
                    LessonVersionRow.state_lineage_id == snapshot.state_lineage_id,
                    LessonVersionRow.branch_id == snapshot.branch_id,
                )
            )
        ).all()
        rolled_back: list[str] = []
        for current in current_rows:
            target = targets.get(current.lesson_id)
            if target is None:
                if current.status in {
                    LessonStatus.INVALIDATED.value,
                    LessonStatus.RETIRED.value,
                }:
                    continue
                await self.invalidate(
                    session,
                    lesson_id=current.lesson_id,
                    reason=f"rollback to {snapshot_id}",
                    source_episode_id=source_episode_id,
                )
                rolled_back.append(current.lesson_id)
            elif current.lesson_version_id != target.lesson_version_id:
                target_record = _row_to_record(target)
                restored = target_record.model_copy(
                    update={
                        "version": current.version + 1,
                        "supersedes_version": current.version,
                        "source_episode_ids": tuple(
                            dict.fromkeys((*target_record.source_episode_ids, source_episode_id))
                        ),
                        "created_at": datetime.now(UTC),
                    }
                )
                await self.write(session, restored, require_validated_evidence=False)
                rolled_back.append(current.lesson_id)
        return tuple(sorted(set(rolled_back)))


def _record_to_row(record: LessonRecord) -> LessonVersionRow:
    return LessonVersionRow(
        lesson_version_id=f"{record.lesson_id}:v{record.version}",
        lesson_id=record.lesson_id,
        version=record.version,
        competency_id=record.competency_id,
        error_class=record.error_class,
        state_lineage_id=record.state_lineage_id,
        branch_id=record.branch_id,
        status=record.status.value,
        general_rule=record.general_rule,
        search_text=" ".join(
            [record.general_rule, record.applicability, *record.exclusions, record.error_class]
        ),
        confidence=record.confidence,
        successful_transfer_count=record.successful_transfer_count,
        failed_transfer_count=record.failed_transfer_count,
        harmful_retrieval_count=record.harmful_retrieval_count,
        record_json=record.model_dump(mode="json"),
        created_at=record.created_at,
    )


def _row_to_record(row: LessonVersionRow) -> LessonRecord:
    return LessonRecord.model_validate(row.record_json, strict=False)


def _rules_compatible(left: str, right: str) -> bool:
    left_tokens = set(_TOKEN_RE.findall(left.lower()))
    right_tokens = set(_TOKEN_RE.findall(right.lower()))
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens) >= 0.7


def _embedding(text: str, dimensions: int = 256) -> tuple[float, ...]:
    vector = [0.0] * dimensions
    tokens = _TOKEN_RE.findall(text.lower())
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:], strict=False)]
    for feature in features:
        digest = sha256_digest(feature).removeprefix("sha256:")
        index = int(digest[:8], 16) % dimensions
        sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    raw = sum(a * b for a, b in zip(left, right, strict=True))
    return (raw + 1.0) / 2.0
