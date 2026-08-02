from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.artifacts.store import ArtifactCatalog
from padawan.models.contracts import (
    AttemptRecord,
    DevelopmentalEpisode,
    GradeRecord,
    RevisionRecord,
    TeacherInterventionRecord,
    TransferTrialRecord,
)
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AttemptRow,
    EpisodeRow,
    GradeRow,
    RevisionRow,
    TeacherInterventionRow,
    TransferTrialRow,
)


class EpisodeConflictError(RuntimeError):
    pass


class EpisodeStore:
    """Canonical normalized storage for every successful or failed developmental episode."""

    def __init__(self, artifacts: ArtifactCatalog) -> None:
        self.artifacts = artifacts

    async def create(
        self,
        session: AsyncSession,
        *,
        episode_id: str,
        student_id: str,
        state_before_id: str,
        item_id: str,
    ) -> EpisodeRow:
        existing = await session.get(EpisodeRow, episode_id)
        if existing is not None:
            if (
                existing.student_id != student_id
                or existing.state_before_id != state_before_id
                or existing.item_id != item_id
            ):
                raise EpisodeConflictError("episode ID conflicts with existing episode")
            return existing
        row = EpisodeRow(
            episode_id=episode_id,
            student_id=student_id,
            state_before_id=state_before_id,
            state_after_id=None,
            item_id=item_id,
            status="active",
            record_json={},
            created_at=datetime.now(UTC),
            completed_at=None,
        )
        session.add(row)
        await session.flush()
        return row

    async def store_attempt(self, session: AsyncSession, attempt: AttemptRecord) -> AttemptRecord:
        existing = await session.scalar(
            select(AttemptRow).where(AttemptRow.request_id == attempt.request_id)
        )
        if existing is not None:
            stored = AttemptRecord.model_validate(existing.record_json, strict=False)
            if sha256_digest(stored.model_dump(mode="json")) != sha256_digest(
                attempt.model_dump(mode="json")
            ):
                raise EpisodeConflictError("request replay produced a different attempt")
            return stored
        for influence_id in attempt.influence_refs:
            influence = await session.get(TeacherInterventionRow, influence_id)
            if influence is None:
                raise EpisodeConflictError(
                    f"attempt cites unknown teacher influence: {influence_id}"
                )
            if influence.episode_id != attempt.episode_id:
                raise EpisodeConflictError("attempt teacher influence belongs to another episode")
        row = AttemptRow(
            attempt_id=attempt.attempt_id,
            episode_id=attempt.episode_id,
            item_id=attempt.item_id,
            state_before_id=attempt.state_before_id,
            state_after_id=attempt.state_after_id,
            request_id=attempt.request_id,
            response_id=attempt.response_id,
            model_id=attempt.model_id,
            runtime_id=attempt.runtime_id,
            research_role=attempt.research_role.value,
            record_json=attempt.model_dump(mode="json"),
            created_at=attempt.created_at,
        )
        session.add(row)
        await self.artifacts.reference(
            session,
            attempt.raw_generation_ref,
            owner_type="attempt",
            owner_id=attempt.attempt_id,
        )
        if attempt.private_reasoning_ref is not None:
            await self.artifacts.reference(
                session,
                attempt.private_reasoning_ref,
                owner_type="attempt_private_reasoning",
                owner_id=attempt.attempt_id,
            )
        for artifact in attempt.artifacts:
            await self.artifacts.reference(
                session, artifact, owner_type="attempt", owner_id=attempt.attempt_id
            )
        await session.flush()
        return attempt

    async def store_grade(self, session: AsyncSession, grade: GradeRecord) -> GradeRecord:
        existing = await session.scalar(
            select(GradeRow).where(GradeRow.attempt_id == grade.attempt_id)
        )
        if existing is not None:
            return GradeRecord.model_validate(existing.record_json, strict=False)
        session.add(
            GradeRow(
                grade_id=grade.grade_id,
                attempt_id=grade.attempt_id,
                deterministic=grade.deterministic,
                outcome=grade.outcome.value,
                score=grade.score,
                record_json=grade.model_dump(mode="json"),
                created_at=grade.created_at,
            )
        )
        for artifact in grade.artifacts:
            await self.artifacts.reference(
                session, artifact, owner_type="grade", owner_id=grade.grade_id
            )
        await session.flush()
        return grade

    async def store_intervention(
        self, session: AsyncSession, intervention: TeacherInterventionRecord
    ) -> TeacherInterventionRecord:
        existing = await session.scalar(
            select(TeacherInterventionRow).where(
                TeacherInterventionRow.request_id == intervention.request_id
            )
        )
        if existing is not None:
            return TeacherInterventionRecord.model_validate(existing.record_json, strict=False)
        session.add(
            TeacherInterventionRow(
                intervention_id=intervention.intervention_id,
                episode_id=intervention.episode_id,
                attempt_id=intervention.targeted_attempt_id,
                request_id=intervention.request_id,
                provider=intervention.provider,
                model_id=intervention.model_id,
                mode=intervention.mode.value,
                validation_status=intervention.validation_status.value,
                record_json=intervention.model_dump(mode="json"),
                created_at=intervention.created_at,
            )
        )
        await self.artifacts.reference(
            session,
            intervention.exact_prompt_ref,
            owner_type="teacher_prompt",
            owner_id=intervention.intervention_id,
        )
        await self.artifacts.reference(
            session,
            intervention.raw_response_ref,
            owner_type="teacher_response",
            owner_id=intervention.intervention_id,
        )
        await session.flush()
        return intervention

    async def store_revision(
        self, session: AsyncSession, revision: RevisionRecord
    ) -> RevisionRecord:
        existing = await session.get(RevisionRow, revision.revision_id)
        if existing is not None:
            return revision
        session.add(
            RevisionRow(
                revision_id=revision.revision_id,
                original_attempt_id=revision.original_attempt_id,
                intervention_id=revision.intervention_id,
                revised_attempt_id=revision.revised_attempt_id,
                revised_grade_id=revision.revised_grade_id,
                created_at=revision.created_at,
            )
        )
        await session.flush()
        return revision

    async def store_transfer(
        self,
        session: AsyncSession,
        *,
        episode_id: str,
        transfer: TransferTrialRecord,
        experiment_id: str | None,
    ) -> TransferTrialRecord:
        existing = await session.get(TransferTrialRow, transfer.transfer_trial_id)
        if existing is not None:
            return TransferTrialRecord.model_validate(existing.record_json, strict=False)
        session.add(
            TransferTrialRow(
                transfer_trial_id=transfer.transfer_trial_id,
                episode_id=episode_id,
                experiment_id=experiment_id,
                record_json=transfer.model_dump(mode="json"),
                created_at=transfer.created_at,
            )
        )
        await session.flush()
        return transfer

    async def commit(
        self,
        session: AsyncSession,
        episode: DevelopmentalEpisode,
    ) -> DevelopmentalEpisode:
        row = await session.scalar(
            select(EpisodeRow).where(EpisodeRow.episode_id == episode.episode_id).with_for_update()
        )
        if row is None:
            raise KeyError(episode.episode_id)
        if row.status == "complete":
            stored = DevelopmentalEpisode.model_validate(row.record_json, strict=False)
            if sha256_digest(stored.model_dump(mode="json")) != sha256_digest(
                episode.model_dump(mode="json")
            ):
                raise EpisodeConflictError("completed episode cannot be replaced")
            return stored
        row.state_after_id = episode.student_state_after_id
        row.status = episode.status
        row.record_json = episode.model_dump(mode="json")
        row.completed_at = episode.completed_at
        await session.flush()
        return episode

    async def inspect(self, session: AsyncSession, *, episode_id: str) -> dict[str, object]:
        episode = await session.get(EpisodeRow, episode_id)
        if episode is None:
            raise KeyError(episode_id)
        attempts = (
            await session.scalars(select(AttemptRow).where(AttemptRow.episode_id == episode_id))
        ).all()
        interventions = (
            await session.scalars(
                select(TeacherInterventionRow).where(
                    TeacherInterventionRow.episode_id == episode_id
                )
            )
        ).all()
        return {
            "episode": episode.record_json
            or {
                "episode_id": episode.episode_id,
                "status": episode.status,
            },
            "attempts": [row.record_json for row in attempts],
            "teacher_interventions": [row.record_json for row in interventions],
        }
