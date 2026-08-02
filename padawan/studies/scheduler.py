from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Select, exists, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import CorpusPool, ItemStatus
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    EvaluationOutcome,
    EvaluationSchedule,
    EvaluationTrialStatus,
    EvaluationTrialType,
    StudyStatus,
)
from padawan.models.tables import (
    CorpusItemRow,
    EpisodeRow,
    EvaluationTrialRow,
    ExposureRow,
    StudentStateRow,
    StudyExperimentRow,
    StudyRow,
    VerifierResultRow,
)


@dataclass(frozen=True)
class ClaimedEvaluationTrial:
    schedule: EvaluationSchedule
    lease_token: str
    lease_owner: str
    lease_expires_at: datetime


class EvaluationScheduler:
    """Durable due-time scheduler for fresh retention and interference probes."""

    async def schedule(
        self, session: AsyncSession, definition: EvaluationSchedule
    ) -> EvaluationTrialRow:
        payload = definition.model_dump(mode="json")
        digest = sha256_digest(payload)
        existing = await session.get(EvaluationTrialRow, definition.trial_id)
        if existing is not None:
            if existing.definition_digest != digest or existing.record_json != payload:
                raise ValueError("evaluation trial ID conflicts with persisted schedule")
            return existing
        study = await session.get(StudyRow, definition.study_id)
        if study is None:
            raise ValueError(f"evaluation trial cites unknown study: {definition.study_id}")
        if StudyStatus(study.status) != StudyStatus.ACTIVE:
            raise ValueError("evaluation trials may only be scheduled for an active study")
        binding_exists = await session.scalar(
            select(
                exists().where(
                    StudyExperimentRow.study_id == definition.study_id,
                    StudyExperimentRow.checkpoint_id == definition.checkpoint_id,
                    StudyExperimentRow.environment_fingerprint
                    == definition.environment_fingerprint,
                )
            )
        )
        if not binding_exists:
            raise ValueError("evaluation trial checkpoint/environment is outside the study")
        state = await session.get(StudentStateRow, definition.state_snapshot_id)
        if state is None:
            raise ValueError("evaluation trial state snapshot is unknown")
        if (
            state.student_id != definition.student_id
            or state.checkpoint_id != definition.checkpoint_id
        ):
            raise ValueError(
                "evaluation trial state identity does not match its student/checkpoint"
            )
        source = await session.get(EpisodeRow, definition.source_episode_id)
        if source is None or source.status != "complete" or source.state_after_id is None:
            raise ValueError("evaluation trial source episode is not complete")
        if source.student_id != definition.student_id:
            raise ValueError("evaluation trial source episode belongs to another student")
        source_item = await session.get(CorpusItemRow, source.item_id)
        selected_item = await session.get(CorpusItemRow, definition.item_id)
        if source_item is None or selected_item is None:
            raise ValueError("evaluation trial corpus lineage is missing")
        if selected_item.competency_id != definition.competency_id:
            raise ValueError("evaluation trial competency differs from selected item")
        if source_item.competency_id != definition.competency_id:
            raise ValueError("evaluation trial must probe the source competency")
        if selected_item.pool not in {
            CorpusPool.ROTATING_SHADOW.value,
            CorpusPool.SEALED_ANCHOR.value,
        }:
            raise ValueError("retention and interference probes require evaluation-only inventory")
        if selected_item.status != ItemStatus.ACTIVE.value:
            raise ValueError("evaluation trial item is not active")
        exposed = await session.scalar(
            select(
                exists().where(
                    ExposureRow.student_id == definition.student_id,
                    ExposureRow.instance_group_id == selected_item.instance_group_id,
                )
            )
        )
        if exposed:
            raise ValueError("evaluation trial item group is not fresh for the student")
        if definition.trial_type == EvaluationTrialType.RETENTION:
            if definition.state_snapshot_id != source.state_after_id:
                raise ValueError("retention trial must inherit the source episode's final snapshot")
        else:
            interfering = await session.get(EpisodeRow, definition.interfering_episode_id)
            if (
                interfering is None
                or interfering.status != "complete"
                or interfering.state_after_id is None
            ):
                raise ValueError("interference trial requires a complete interfering episode")
            if interfering.student_id != definition.student_id:
                raise ValueError("interfering episode belongs to another student")
            if definition.state_snapshot_id != interfering.state_after_id:
                raise ValueError("interference trial must inherit the post-interference snapshot")
            interfering_item = await session.get(CorpusItemRow, interfering.item_id)
            if interfering_item is None:
                raise ValueError("interfering episode corpus lineage is missing")
            if interfering_item.competency_id == source_item.competency_id:
                raise ValueError("interference trial requires learning in another competency")
        row = EvaluationTrialRow(
            trial_id=definition.trial_id,
            study_id=definition.study_id,
            trial_type=definition.trial_type.value,
            source_episode_id=definition.source_episode_id,
            interfering_episode_id=definition.interfering_episode_id,
            student_id=definition.student_id,
            checkpoint_id=definition.checkpoint_id,
            state_snapshot_id=definition.state_snapshot_id,
            competency_id=definition.competency_id,
            item_id=definition.item_id,
            instance_group_id=selected_item.instance_group_id,
            environment_fingerprint=definition.environment_fingerprint,
            assignment_seed=definition.assignment_seed,
            assignment_propensity=definition.assignment_propensity,
            due_at=definition.due_at,
            status=EvaluationTrialStatus.SCHEDULED.value,
            definition_digest=digest,
            record_json=payload,
            outcome_json=None,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            created_at=definition.created_at,
            completed_at=None,
        )
        session.add(row)
        await session.flush()
        return row

    async def claim_due(
        self,
        session: AsyncSession,
        *,
        worker_id: str,
        lease_for: timedelta,
        now: datetime | None = None,
    ) -> ClaimedEvaluationTrial | None:
        if not worker_id:
            raise ValueError("evaluation worker ID is required")
        if lease_for.total_seconds() <= 0:
            raise ValueError("evaluation lease duration must be positive")
        timestamp = now or datetime.now(UTC)
        await self.recover_expired(session, now=timestamp)
        query: Select[tuple[EvaluationTrialRow]] = (
            select(EvaluationTrialRow)
            .join(CorpusItemRow, CorpusItemRow.item_id == EvaluationTrialRow.item_id)
            .where(
                EvaluationTrialRow.status == EvaluationTrialStatus.SCHEDULED.value,
                EvaluationTrialRow.due_at <= timestamp,
                EvaluationTrialRow.lease_owner.is_(None),
                CorpusItemRow.status == ItemStatus.ACTIVE.value,
                CorpusItemRow.lease_owner.is_(None),
            )
            .order_by(EvaluationTrialRow.due_at, EvaluationTrialRow.trial_id)
        )
        token = f"evaluation-lease-{uuid4()}"
        expires_at = timestamp + lease_for
        dialect = session.bind.dialect.name if session.bind is not None else "unknown"
        if dialect == "postgresql":
            row = await session.scalar(query.with_for_update(skip_locked=True).limit(1))
            if row is None:
                return None
            row.status = EvaluationTrialStatus.LEASED.value
            row.lease_owner = worker_id
            row.lease_token = token
            row.lease_expires_at = expires_at
        else:
            candidate = (
                query.with_only_columns(EvaluationTrialRow.trial_id).limit(1).scalar_subquery()
            )
            row = await session.scalar(
                update(EvaluationTrialRow)
                .where(
                    EvaluationTrialRow.trial_id == candidate,
                    EvaluationTrialRow.status == EvaluationTrialStatus.SCHEDULED.value,
                    EvaluationTrialRow.lease_owner.is_(None),
                )
                .values(
                    status=EvaluationTrialStatus.LEASED.value,
                    lease_owner=worker_id,
                    lease_token=token,
                    lease_expires_at=expires_at,
                )
                .returning(EvaluationTrialRow)
            )
            if row is None:
                return None
        item_result = await session.execute(
            update(CorpusItemRow)
            .where(
                CorpusItemRow.item_id == row.item_id,
                CorpusItemRow.status == ItemStatus.ACTIVE.value,
                CorpusItemRow.lease_owner.is_(None),
            )
            .values(
                status=ItemStatus.LEASED.value,
                lease_owner=worker_id,
                lease_token=token,
                lease_expires_at=expires_at,
                lease_attempt=CorpusItemRow.lease_attempt + 1,
            )
        )
        if not cast(CursorResult[Any], item_result).rowcount:
            row.status = EvaluationTrialStatus.SCHEDULED.value
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
            await session.flush()
            return None
        await session.flush()
        return ClaimedEvaluationTrial(
            schedule=EvaluationSchedule.model_validate(row.record_json, strict=False),
            lease_token=token,
            lease_owner=worker_id,
            lease_expires_at=expires_at,
        )

    async def complete(
        self,
        session: AsyncSession,
        *,
        outcome: EvaluationOutcome,
        lease_token: str,
        lease_owner: str,
    ) -> None:
        row = await session.scalar(
            select(EvaluationTrialRow)
            .where(EvaluationTrialRow.trial_id == outcome.trial_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(outcome.trial_id)
        payload = outcome.model_dump(mode="json")
        if row.status == EvaluationTrialStatus.COMPLETE.value:
            if row.outcome_json != payload:
                raise ValueError("evaluation outcome replay conflicts with persisted outcome")
            return
        if (
            row.status != EvaluationTrialStatus.LEASED.value
            or row.lease_token != lease_token
            or row.lease_owner != lease_owner
        ):
            raise ValueError("evaluation outcome requires the active trial lease")
        if outcome.exposure_id is not None:
            exposure = await session.get(ExposureRow, outcome.exposure_id)
            if exposure is None:
                raise ValueError("evaluation outcome cites an unknown exposure")
            if (
                exposure.student_id != row.student_id
                or exposure.checkpoint_id != row.checkpoint_id
                or exposure.state_id != row.state_snapshot_id
                or exposure.item_id != row.item_id
                or exposure.instance_group_id != row.instance_group_id
                or exposure.episode_id != row.trial_id
                or not exposure.prompt_exposed
            ):
                raise ValueError("evaluation exposure does not match the scheduled trial")
        if _as_utc(outcome.completed_at) < _as_utc(row.created_at):
            raise ValueError("evaluation outcome predates its scheduled trial")
        for result_id in outcome.verifier_result_ids:
            if await session.get(VerifierResultRow, result_id) is None:
                raise ValueError(f"evaluation outcome cites unknown verifier evidence: {result_id}")
        item = await session.scalar(
            select(CorpusItemRow)
            .where(
                CorpusItemRow.item_id == row.item_id,
                CorpusItemRow.lease_token == lease_token,
                CorpusItemRow.lease_owner == lease_owner,
            )
            .with_for_update()
        )
        if item is None:
            raise ValueError("evaluation item lease is missing or stale")
        row.outcome_json = payload
        row.status = EvaluationTrialStatus.COMPLETE.value
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
        row.completed_at = outcome.completed_at
        item.status = ItemStatus.ACTIVE.value
        item.lease_owner = None
        item.lease_token = None
        item.lease_expires_at = None
        await session.flush()

    async def recover_expired(self, session: AsyncSession, *, now: datetime | None = None) -> int:
        timestamp = now or datetime.now(UTC)
        query = select(EvaluationTrialRow).where(
            EvaluationTrialRow.status == EvaluationTrialStatus.LEASED.value,
            EvaluationTrialRow.lease_expires_at.is_not(None),
            EvaluationTrialRow.lease_expires_at <= timestamp,
        )
        if session.bind is not None and session.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rows = (await session.scalars(query)).all()
        for row in rows:
            if row.lease_token is not None:
                await session.execute(
                    update(CorpusItemRow)
                    .where(
                        CorpusItemRow.item_id == row.item_id,
                        CorpusItemRow.lease_token == row.lease_token,
                        CorpusItemRow.lease_owner == row.lease_owner,
                    )
                    .values(
                        status=ItemStatus.ACTIVE.value,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                    )
                )
            row.status = EvaluationTrialStatus.SCHEDULED.value
            row.lease_owner = None
            row.lease_token = None
            row.lease_expires_at = None
        await session.flush()
        return len(rows)


def _as_utc(value: datetime) -> datetime:
    # SQLite returns timezone-aware columns as naive values. Padawan writes UTC.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
