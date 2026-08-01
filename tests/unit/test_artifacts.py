from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from padawan.artifacts.store import (
    ArtifactAccessDeniedError,
    ArtifactCatalog,
    ArtifactIntegrityError,
    LocalArtifactStore,
    redact_secrets,
)


def test_content_addressed_round_trip_and_duplicate_suppression(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    first = store.put_bytes(b"exact\x00bytes", media_type="application/octet-stream")
    second = store.put_bytes(b"exact\x00bytes", media_type="application/octet-stream")
    assert first == second
    assert store.read_bytes(first) == b"exact\x00bytes"
    assert store.verify(first)


def test_restricted_access_and_hash_tampering(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    reference = store.put_text("sensitive trace", restricted=True, raw_data=True)
    with pytest.raises(ArtifactAccessDeniedError):
        store.read_bytes(reference)
    blob = tmp_path / "blobs" / "sha256" / reference.digest[7:9] / reference.digest[9:]
    blob.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError):
        store.read_bytes(reference, allow_restricted=True)


async def test_gc_never_deletes_referenced_artifact(database, tmp_path) -> None:
    store = LocalArtifactStore(tmp_path / "cas")
    catalog = ArtifactCatalog(store)
    kept = store.put_text("kept")
    orphan = store.put_text("orphan")
    async with database.transaction() as session:
        await catalog.reference(session, kept, owner_type="test", owner_id="owner")
        references = await catalog.referenced_digests(session)
    future = datetime.now(UTC) + timedelta(days=30)
    result = store.collect_garbage(
        referenced_digests=references,
        minimum_age=timedelta(days=1),
        dry_run=False,
        now=future,
    )
    assert kept.digest not in result.deleted
    assert orphan.digest in result.deleted
    assert store.read_text(kept) == "kept"


def test_secret_redaction_covers_known_and_patterned_secrets() -> None:
    text = "Authorization: Bearer secret-token api_key=abc123 password=hunter2 exact"
    redacted = redact_secrets(text, known_secrets=("exact",))
    assert "secret-token" not in redacted
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "exact" not in redacted
    assert redacted.count("[REDACTED]") == 4
