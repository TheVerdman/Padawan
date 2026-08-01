from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ProvenanceEventRow, ProvenanceHeadRow

GENESIS_HASH = sha256_digest("PADAWAN_PROVENANCE_GENESIS_V1")


@dataclass(frozen=True)
class ProvenanceVerification:
    valid: bool
    stream_id: str
    events_checked: int
    head_hash: str
    errors: tuple[str, ...]


class ProvenanceLedger:
    """Append-only, hash-chained provenance over a transaction-owned stream head."""

    def __init__(self, *, code_revision: str, environment: str) -> None:
        if not code_revision or not environment:
            raise ValueError("code_revision and environment are required")
        self.code_revision = code_revision
        self.environment = environment

    async def append(
        self,
        session: AsyncSession,
        *,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        parent_event_ids: tuple[str, ...] = (),
        state_lineage_id: str | None = None,
        episode_id: str | None = None,
        stream_id: str = "global",
        event_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ProvenanceEventRow:
        if not event_type or not actor or not stream_id:
            raise ValueError("event_type, actor, and stream_id are required")
        if event_id is not None:
            duplicate = await session.get(ProvenanceEventRow, event_id)
            if duplicate is not None:
                expected_payload_hash = sha256_digest(payload)
                if duplicate.payload_hash != expected_payload_hash:
                    raise ValueError(f"event id reused with different payload: {event_id}")
                return duplicate
        for parent_id in parent_event_ids:
            if await session.get(ProvenanceEventRow, parent_id) is None:
                raise ValueError(f"unknown parent provenance event: {parent_id}")

        head = await session.scalar(
            select(ProvenanceHeadRow)
            .where(ProvenanceHeadRow.stream_id == stream_id)
            .with_for_update()
        )
        if head is None:
            head = ProvenanceHeadRow(stream_id=stream_id, last_position=0, chain_hash=GENESIS_HASH)
            session.add(head)
            await session.flush()

        timestamp = created_at or datetime.now(UTC)
        assigned_event_id = event_id or f"evt-{uuid4()}"
        position = head.last_position + 1
        payload_hash = sha256_digest(payload)
        chain_hash = _chain_hash(
            event_id=assigned_event_id,
            stream_id=stream_id,
            position=position,
            event_type=event_type,
            actor=actor,
            state_lineage_id=state_lineage_id,
            episode_id=episode_id,
            parent_event_ids=parent_event_ids,
            payload_hash=payload_hash,
            previous_chain_hash=head.chain_hash,
            code_revision=self.code_revision,
            environment=self.environment,
            created_at=timestamp,
        )
        row = ProvenanceEventRow(
            event_id=assigned_event_id,
            stream_id=stream_id,
            position=position,
            parent_event_ids=list(parent_event_ids),
            event_type=event_type,
            actor=actor,
            state_lineage_id=state_lineage_id,
            episode_id=episode_id,
            payload=payload,
            payload_hash=payload_hash,
            previous_chain_hash=head.chain_hash,
            chain_hash=chain_hash,
            code_revision=self.code_revision,
            environment=self.environment,
            created_at=timestamp,
        )
        session.add(row)
        head.last_position = position
        head.chain_hash = chain_hash
        await session.flush()
        return row

    async def verify(
        self, session: AsyncSession, *, stream_id: str = "global"
    ) -> ProvenanceVerification:
        rows = list(
            (
                await session.scalars(
                    select(ProvenanceEventRow)
                    .where(ProvenanceEventRow.stream_id == stream_id)
                    .order_by(ProvenanceEventRow.position)
                )
            ).all()
        )
        errors: list[str] = []
        previous = GENESIS_HASH
        seen: set[str] = set()
        for expected_position, row in enumerate(rows, start=1):
            if row.position != expected_position:
                errors.append(
                    f"position gap: expected {expected_position}, observed {row.position}"
                )
            if row.previous_chain_hash != previous:
                errors.append(f"previous hash mismatch at {row.event_id}")
            computed_payload_hash = sha256_digest(row.payload)
            if row.payload_hash != computed_payload_hash:
                errors.append(f"payload hash mismatch at {row.event_id}")
            missing_parents = [parent for parent in row.parent_event_ids if parent not in seen]
            if missing_parents:
                errors.append(f"unresolved or forward parents at {row.event_id}: {missing_parents}")
            expected_hash = _chain_hash(
                event_id=row.event_id,
                stream_id=row.stream_id,
                position=row.position,
                event_type=row.event_type,
                actor=row.actor,
                state_lineage_id=row.state_lineage_id,
                episode_id=row.episode_id,
                parent_event_ids=tuple(row.parent_event_ids),
                payload_hash=row.payload_hash,
                previous_chain_hash=row.previous_chain_hash,
                code_revision=row.code_revision,
                environment=row.environment,
                created_at=row.created_at,
            )
            if row.chain_hash != expected_hash:
                errors.append(f"chain hash mismatch at {row.event_id}")
            previous = row.chain_hash
            seen.add(row.event_id)

        head = await session.get(ProvenanceHeadRow, stream_id)
        if head is None:
            if rows:
                errors.append("stream head missing")
            head_hash = GENESIS_HASH
        else:
            head_hash = head.chain_hash
            if head.last_position != len(rows):
                errors.append("stream head position mismatch")
            if head.chain_hash != previous:
                errors.append("stream head hash mismatch")
        return ProvenanceVerification(
            valid=not errors,
            stream_id=stream_id,
            events_checked=len(rows),
            head_hash=head_hash,
            errors=tuple(errors),
        )

    async def export_jsonl(self, session: AsyncSession, *, stream_id: str = "global") -> str:
        rows = (
            await session.scalars(
                select(ProvenanceEventRow)
                .where(ProvenanceEventRow.stream_id == stream_id)
                .order_by(ProvenanceEventRow.position)
            )
        ).all()
        return "\n".join(
            json.dumps(_row_payload(row), sort_keys=True, separators=(",", ":")) for row in rows
        )


def _chain_hash(
    *,
    event_id: str,
    stream_id: str,
    position: int,
    event_type: str,
    actor: str,
    state_lineage_id: str | None,
    episode_id: str | None,
    parent_event_ids: tuple[str, ...],
    payload_hash: str,
    previous_chain_hash: str,
    code_revision: str,
    environment: str,
    created_at: datetime,
) -> str:
    return sha256_digest(
        canonical_json_bytes(
            {
                "event_id": event_id,
                "stream_id": stream_id,
                "position": position,
                "event_type": event_type,
                "actor": actor,
                "state_lineage_id": state_lineage_id,
                "episode_id": episode_id,
                "parent_event_ids": list(parent_event_ids),
                "payload_hash": payload_hash,
                "previous_chain_hash": previous_chain_hash,
                "code_revision": code_revision,
                "environment": environment,
                "created_at": _canonical_timestamp(created_at),
            }
        )
    )


def _row_payload(row: ProvenanceEventRow) -> dict[str, object]:
    return {
        "event_id": row.event_id,
        "stream_id": row.stream_id,
        "position": row.position,
        "event_type": row.event_type,
        "actor": row.actor,
        "state_lineage_id": row.state_lineage_id,
        "episode_id": row.episode_id,
        "parent_event_ids": row.parent_event_ids,
        "payload": row.payload,
        "payload_hash": row.payload_hash,
        "previous_chain_hash": row.previous_chain_hash,
        "chain_hash": row.chain_hash,
        "code_revision": row.code_revision,
        "environment": row.environment,
        "created_at": _canonical_timestamp(row.created_at),
    }


def _canonical_timestamp(value: datetime) -> str:
    # SQLite returns timezone-aware columns as naive values. Padawan writes UTC,
    # so normalize both SQLite and PostgreSQL representations before hashing.
    utc_value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")
