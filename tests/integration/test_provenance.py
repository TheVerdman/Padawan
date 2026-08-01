from __future__ import annotations

import json

import pytest
from sqlalchemy import update

from padawan.models.tables import ProvenanceEventRow
from padawan.provenance.ledger import ProvenanceLedger


async def test_provenance_chain_parents_export_and_tamper_detection(database) -> None:
    ledger = ProvenanceLedger(code_revision="abc123", environment="test")
    async with database.transaction() as session:
        first = await ledger.append(
            session,
            event_type="First",
            actor="test",
            payload={"value": 1},
            event_id="event-first",
        )
        await ledger.append(
            session,
            event_type="Second",
            actor="test",
            payload={"value": 2},
            parent_event_ids=(first.event_id,),
            event_id="event-second",
        )
    async with database.transaction() as session:
        valid = await ledger.verify(session)
        exported = await ledger.export_jsonl(session)
        assert valid.valid
        lines = [json.loads(line) for line in exported.splitlines()]
        assert [line["event_id"] for line in lines] == ["event-first", "event-second"]
        with pytest.raises(ValueError, match="different payload"):
            await ledger.append(
                session,
                event_type="First",
                actor="test",
                payload={"value": "changed"},
                event_id="event-first",
            )
    async with database.transaction() as session:
        await session.execute(
            update(ProvenanceEventRow)
            .where(ProvenanceEventRow.event_id == "event-first")
            .values(payload={"value": "tampered"})
        )
    async with database.transaction() as session:
        invalid = await ledger.verify(session)
        assert not invalid.valid
        assert any("payload hash mismatch" in error for error in invalid.errors)
