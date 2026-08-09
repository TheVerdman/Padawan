from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import sha256_digest
from padawan.models.tables import DurationProfileRow, OperationSpanEventRow, OperationSpanRow
from padawan.temporal.clock import Clock, SystemClock
from padawan.temporal.contracts import (
    DurationProfile,
    OperationSpanEventRecord,
    OperationSpanRecord,
    OperationStatus,
)

_TERMINAL_STATUSES = tuple(status.value for status in OperationStatus if status.terminal)
_ACTIVE_STATUSES = tuple(status.value for status in OperationStatus if not status.terminal)

_ALLOWED_TRANSITIONS: dict[OperationStatus, frozenset[OperationStatus]] = {
    OperationStatus.QUEUED: frozenset(
        {
            OperationStatus.RUNNING,
            OperationStatus.WAITING,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.TIMED_OUT,
        }
    ),
    OperationStatus.RUNNING: frozenset(
        {
            OperationStatus.RUNNING,
            OperationStatus.WAITING,
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.TIMED_OUT,
        }
    ),
    OperationStatus.WAITING: frozenset(
        {
            OperationStatus.RUNNING,
            OperationStatus.WAITING,
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.CANCELLED,
            OperationStatus.TIMED_OUT,
        }
    ),
    OperationStatus.SUCCEEDED: frozenset(),
    OperationStatus.FAILED: frozenset(),
    OperationStatus.CANCELLED: frozenset(),
    OperationStatus.TIMED_OUT: frozenset(),
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OperationTelemetryStore:
    """Durable operation lifecycle with append-only transition evidence."""

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()

    async def create(
        self,
        session: AsyncSession,
        *,
        operation_id: str,
        operation_type: str,
        environment_fingerprint: str,
        workload_class: str,
        workload: dict[str, Any],
        parent_operation_id: str | None = None,
        run_id: str | None = None,
        source_ref: str | None = None,
        status: OperationStatus = OperationStatus.QUEUED,
        occurred_at: datetime | None = None,
    ) -> OperationSpanRecord:
        if status.terminal or status == OperationStatus.WAITING:
            raise ValueError("new operation spans must be queued or running")
        timestamp = _utc(occurred_at or self.clock.now())
        existing = await session.get(OperationSpanRow, operation_id)
        if existing is not None:
            record = await self._record(session, existing)
            identity = (
                operation_type,
                environment_fingerprint,
                workload_class,
                workload,
                parent_operation_id,
                run_id,
                source_ref,
            )
            observed = (
                record.operation_type,
                record.environment_fingerprint,
                record.workload_class,
                record.workload,
                record.parent_operation_id,
                record.run_id,
                record.source_ref,
            )
            if identity != observed:
                raise ValueError("operation ID reused with a different identity")
            return record
        row = OperationSpanRow(
            operation_id=operation_id,
            parent_operation_id=parent_operation_id,
            run_id=run_id,
            source_ref=source_ref,
            operation_type=operation_type,
            environment_fingerprint=environment_fingerprint,
            workload_class=workload_class,
            workload=workload,
            status=status.value,
            enqueued_at=timestamp,
            started_at=timestamp if status == OperationStatus.RUNNING else None,
            first_progress_at=None,
            completed_at=None,
            event_sequence=1,
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(row)
        await session.flush()
        session.add(
            OperationSpanEventRow(
                event_id=_event_id(operation_id, 1, status, timestamp, None, {}),
                operation_id=operation_id,
                sequence=1,
                status=status.value,
                occurred_at=timestamp,
                progress=None,
                detail={},
            )
        )
        await session.flush()
        return await self._record(session, row)

    async def transition(
        self,
        session: AsyncSession,
        *,
        operation_id: str,
        status: OperationStatus,
        occurred_at: datetime | None = None,
        progress: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> OperationSpanRecord:
        timestamp = _utc(occurred_at or self.clock.now())
        row = await session.scalar(
            select(OperationSpanRow)
            .where(OperationSpanRow.operation_id == operation_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(operation_id)
        prior = OperationStatus(row.status)
        if status not in _ALLOWED_TRANSITIONS[prior]:
            raise ValueError(f"invalid operation transition: {prior.value} -> {status.value}")
        if timestamp < _utc(row.updated_at):
            raise ValueError("operation transition time precedes prior evidence")
        if progress is not None and (progress < 0 or progress > 1):
            raise ValueError("operation progress must be between zero and one")
        if progress is not None and status not in {
            OperationStatus.RUNNING,
            OperationStatus.WAITING,
        }:
            raise ValueError("progress is valid only for running or waiting operations")
        if status == OperationStatus.RUNNING and row.started_at is None:
            row.started_at = timestamp
        if progress is not None and row.first_progress_at is None:
            if row.started_at is None:
                raise ValueError("operation progress requires a start")
            row.first_progress_at = timestamp
        if status.terminal:
            row.completed_at = timestamp
        row.status = status.value
        row.event_sequence += 1
        row.updated_at = timestamp
        event_detail = detail or {}
        session.add(
            OperationSpanEventRow(
                event_id=_event_id(
                    operation_id,
                    row.event_sequence,
                    status,
                    timestamp,
                    progress,
                    event_detail,
                ),
                operation_id=operation_id,
                sequence=row.event_sequence,
                status=status.value,
                occurred_at=timestamp,
                progress=progress,
                detail=event_detail,
            )
        )
        await session.flush()
        return await self._record(session, row)

    async def get(self, session: AsyncSession, *, operation_id: str) -> OperationSpanRecord:
        row = await session.get(OperationSpanRow, operation_id)
        if row is None:
            raise KeyError(operation_id)
        return await self._record(session, row)

    async def active(self, session: AsyncSession) -> tuple[OperationSpanRecord, ...]:
        rows = (
            await session.scalars(
                select(OperationSpanRow)
                .where(OperationSpanRow.status.in_(_ACTIVE_STATUSES))
                .order_by(OperationSpanRow.enqueued_at, OperationSpanRow.operation_id)
            )
        ).all()
        return tuple([await self._record(session, row) for row in rows])

    async def build_duration_profile(
        self,
        session: AsyncSession,
        *,
        operation_type: str,
        environment_fingerprint: str,
        workload_class: str,
        as_of: datetime | None = None,
    ) -> DurationProfile:
        cutoff = _utc(as_of or self.clock.now())
        observed_now = _utc(self.clock.now())
        if cutoff > observed_now:
            raise ValueError("duration profile cutoff cannot be in the future")
        created_at = cutoff
        rows = list(
            (
                await session.scalars(
                    select(OperationSpanRow)
                    .where(
                        OperationSpanRow.operation_type == operation_type,
                        OperationSpanRow.environment_fingerprint == environment_fingerprint,
                        OperationSpanRow.workload_class == workload_class,
                        OperationSpanRow.status.in_(_TERMINAL_STATUSES),
                        OperationSpanRow.completed_at <= cutoff,
                    )
                    .order_by(OperationSpanRow.operation_id)
                )
            ).all()
        )
        if not rows:
            raise ValueError("duration profile requires at least one completed operation")
        source_span_ids = tuple(row.operation_id for row in rows)
        successful_durations = sorted(
            max((_utc(row.completed_at) - _utc(row.enqueued_at)).total_seconds(), 1e-6)
            for row in rows
            if row.status == OperationStatus.SUCCEEDED.value and row.completed_at is not None
        )
        timeout_count = sum(row.status == OperationStatus.TIMED_OUT.value for row in rows)
        p50_seconds = _nearest_rank(successful_durations, 0.5)
        p90_seconds = _nearest_rank(successful_durations, 0.9)
        p95_seconds = _nearest_rank(successful_durations, 0.95)
        identity_digest = sha256_digest(
            {
                "operation_type": operation_type,
                "environment_fingerprint": environment_fingerprint,
                "workload_class": workload_class,
                "sample_count": len(rows),
                "success_count": len(successful_durations),
                "timeout_count": timeout_count,
                "p50_seconds": p50_seconds,
                "p90_seconds": p90_seconds,
                "p95_seconds": p95_seconds,
                "timeout_probability": timeout_count / len(rows),
                "source_span_ids": source_span_ids,
                "as_of": cutoff.isoformat(),
                "created_at": created_at.isoformat(),
            }
        )
        profile = DurationProfile(
            profile_id=f"duration-profile-{identity_digest[7:39]}",
            operation_type=operation_type,
            environment_fingerprint=environment_fingerprint,
            workload_class=workload_class,
            sample_count=len(rows),
            success_count=len(successful_durations),
            timeout_count=timeout_count,
            p50_seconds=p50_seconds,
            p90_seconds=p90_seconds,
            p95_seconds=p95_seconds,
            timeout_probability=timeout_count / len(rows),
            source_span_ids=source_span_ids,
            as_of=cutoff,
            created_at=created_at,
        )
        payload = profile.model_dump(mode="json")
        record_digest = sha256_digest(payload)
        existing = await session.get(DurationProfileRow, profile.profile_id)
        if existing is not None:
            if existing.record_digest != record_digest or existing.record_json != payload:
                raise ValueError("duration profile identity conflicts with persisted evidence")
            return DurationProfile.model_validate(existing.record_json, strict=False)
        session.add(
            DurationProfileRow(
                profile_id=profile.profile_id,
                operation_type=operation_type,
                environment_fingerprint=environment_fingerprint,
                workload_class=workload_class,
                as_of=cutoff,
                record_digest=record_digest,
                record_json=payload,
                created_at=created_at,
            )
        )
        await session.flush()
        return profile

    async def latest_duration_profile(
        self,
        session: AsyncSession,
        *,
        operation_type: str,
        environment_fingerprint: str,
        workload_class: str,
    ) -> DurationProfile | None:
        row = await session.scalar(
            select(DurationProfileRow)
            .where(
                DurationProfileRow.operation_type == operation_type,
                DurationProfileRow.environment_fingerprint == environment_fingerprint,
                DurationProfileRow.workload_class == workload_class,
            )
            .order_by(DurationProfileRow.as_of.desc(), DurationProfileRow.profile_id.desc())
            .limit(1)
        )
        return (
            None if row is None else DurationProfile.model_validate(row.record_json, strict=False)
        )

    async def _record(self, session: AsyncSession, row: OperationSpanRow) -> OperationSpanRecord:
        event_rows = (
            await session.scalars(
                select(OperationSpanEventRow)
                .where(OperationSpanEventRow.operation_id == row.operation_id)
                .order_by(OperationSpanEventRow.sequence)
            )
        ).all()
        events = tuple(
            OperationSpanEventRecord(
                event_id=event.event_id,
                operation_id=event.operation_id,
                sequence=event.sequence,
                status=OperationStatus(event.status),
                occurred_at=_utc(event.occurred_at),
                progress=event.progress,
                detail=event.detail,
            )
            for event in event_rows
        )
        return OperationSpanRecord(
            operation_id=row.operation_id,
            parent_operation_id=row.parent_operation_id,
            run_id=row.run_id,
            source_ref=row.source_ref,
            operation_type=row.operation_type,
            environment_fingerprint=row.environment_fingerprint,
            workload_class=row.workload_class,
            workload=row.workload,
            status=OperationStatus(row.status),
            enqueued_at=_utc(row.enqueued_at),
            started_at=_utc(row.started_at) if row.started_at is not None else None,
            first_progress_at=(
                _utc(row.first_progress_at) if row.first_progress_at is not None else None
            ),
            completed_at=_utc(row.completed_at) if row.completed_at is not None else None,
            events=events,
            created_at=_utc(row.created_at),
            updated_at=_utc(row.updated_at),
        )


def _nearest_rank(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    index = max(0, math.ceil(probability * len(values)) - 1)
    return values[index]


def _event_id(
    operation_id: str,
    sequence: int,
    status: OperationStatus,
    occurred_at: datetime,
    progress: float | None,
    detail: dict[str, Any],
) -> str:
    digest = sha256_digest(
        {
            "operation_id": operation_id,
            "sequence": sequence,
            "status": status.value,
            "occurred_at": occurred_at.isoformat(),
            "progress": progress,
            "detail": detail,
        }
    )
    return f"operation-event-{digest[7:39]}"
