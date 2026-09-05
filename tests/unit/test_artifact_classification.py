from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from padawan.artifacts.store import (
    ArtifactAccessDeniedError,
    ArtifactCatalog,
    ArtifactIntegrityError,
    LocalArtifactStore,
)
from padawan.governance.policy import AccessContext, ExportPolicy
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import canonical_json_bytes, sha256_digest


def _blob(store: LocalArtifactStore, reference: ArtifactRef) -> Path:
    return store.blob_root / reference.digest[7:9] / reference.digest[9:]


@pytest.mark.parametrize("restricted,raw_data", [(True, False), (False, True), (True, True)])
def test_restart_and_relabel_cannot_disclose_protected_content(
    tmp_path, restricted: bool, raw_data: bool
) -> None:
    original = LocalArtifactStore(tmp_path).put_text(
        "synthetic forensic capture", restricted=restricted, raw_data=raw_data
    )
    reopened = LocalArtifactStore(tmp_path)
    forged = original.model_copy(update={"restricted": False, "raw_data": False})
    for supplied in (original, forged):
        with pytest.raises((ArtifactAccessDeniedError, ArtifactIntegrityError)):
            reopened.read_bytes(supplied)
    with pytest.raises(ArtifactIntegrityError):
        reopened.read_bytes(forged, allow_restricted=True)
    assert reopened.read_text(original, allow_restricted=True) == "synthetic forensic capture"


@pytest.mark.parametrize(
    "update",
    [
        {"artifact_id": "forged-id"},
        {"media_type": "application/json"},
        {"restricted": True},
        {"raw_data": True},
    ],
)
def test_reads_bind_all_reference_metadata(tmp_path, update) -> None:
    store = LocalArtifactStore(tmp_path)
    reference = store.put_text("ordinary process artifact")
    with pytest.raises(ArtifactIntegrityError):
        store.read_bytes(reference.model_copy(update=update), allow_restricted=True)


@pytest.mark.parametrize(
    "replacement",
    [
        {"restricted": False, "raw_data": False},
        {"restricted": True, "raw_data": False},
        {"restricted": True, "raw_data": True, "media_type": "application/json"},
    ],
)
def test_duplicate_write_cannot_reclassify_existing_content(tmp_path, replacement) -> None:
    store = LocalArtifactStore(tmp_path)
    reference = store.put_text("fixed classification", restricted=True, raw_data=True)
    with pytest.raises(ArtifactIntegrityError):
        store.put_text("fixed classification", **replacement)
    assert store.read_text(reference, allow_restricted=True) == "fixed classification"


def test_unclassified_legacy_blob_cannot_be_read_or_laundered_by_put(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    content = b"unclassified legacy bytes"
    digest = sha256_digest(content)
    reference = ArtifactRef(
        artifact_id=f"art-{digest[7:]}",
        uri=f"artifact://sha256/{digest[7:]}",
        digest=digest,
        media_type="text/plain; charset=utf-8",
        size_bytes=len(content),
    )
    path = _blob(store, reference)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    for privileged in (False, True):
        with pytest.raises((ArtifactAccessDeniedError, ArtifactIntegrityError)):
            store.read_bytes(reference, allow_restricted=privileged)
    with pytest.raises((ArtifactAccessDeniedError, ArtifactIntegrityError)):
        store.put_bytes(content, media_type=reference.media_type)
    assert path.read_bytes() == content


def test_copying_only_blob_does_not_copy_classification_authority(tmp_path) -> None:
    original = LocalArtifactStore(tmp_path / "source")
    reference = original.put_text("classified source", restricted=True, raw_data=True)
    destination = LocalArtifactStore(tmp_path / "destination")
    copied = _blob(destination, reference)
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(_blob(original, reference).read_bytes())
    with pytest.raises((ArtifactAccessDeniedError, ArtifactIntegrityError)):
        destination.read_bytes(reference, allow_restricted=True)


@pytest.mark.parametrize("missing_field", ["restricted", "raw_data"])
def test_missing_classification_fields_cannot_default_to_public(tmp_path, missing_field) -> None:
    store = LocalArtifactStore(tmp_path)
    reference = store.put_text("public content with complete classification")
    metadata_path = store.metadata_root / reference.digest[7:9] / reference.digest[9:]
    incomplete = json.loads(metadata_path.read_bytes())
    del incomplete[missing_field]
    metadata_path.write_bytes(canonical_json_bytes(incomplete))
    with pytest.raises(ArtifactIntegrityError):
        store.read_bytes(reference)


def test_failed_metadata_publication_never_publishes_content(tmp_path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path)
    replace = os.replace

    def fail_metadata_publication(source, destination) -> None:
        if Path(destination).is_relative_to(store.metadata_root):
            raise OSError("injected classification-publication failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("padawan.artifacts.store.os.replace", fail_metadata_publication)
        with pytest.raises(OSError, match="injected"):
            store.put_text("not published", restricted=True)
    assert not any(path.is_file() for path in store.blob_root.rglob("*"))
    assert not list(tmp_path.rglob(".write-*"))
    reference = store.put_text("not published", restricted=True)
    assert store.read_text(reference, allow_restricted=True) == "not published"


def test_interrupted_publication_preserves_classification_on_retry(tmp_path, monkeypatch) -> None:
    store = LocalArtifactStore(tmp_path)
    replace = os.replace

    def fail_blob_publication(source, destination) -> None:
        if Path(destination).is_relative_to(store.blob_root):
            raise OSError("injected blob-publication failure")
        replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr("padawan.artifacts.store.os.replace", fail_blob_publication)
        with pytest.raises(OSError, match="injected"):
            store.put_text("interrupted evidence", restricted=True, raw_data=True)
    reopened = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactIntegrityError):
        reopened.put_text("interrupted evidence")
    reference = reopened.put_text("interrupted evidence", restricted=True, raw_data=True)
    assert reopened.read_text(reference, allow_restricted=True) == "interrupted evidence"


def test_conflicting_concurrent_publishers_cannot_both_succeed(tmp_path) -> None:
    barrier = Barrier(2)

    def write(restricted: bool) -> ArtifactRef | Exception:
        store = LocalArtifactStore(tmp_path)
        barrier.wait(timeout=5)
        try:
            return store.put_text("same concurrent content", restricted=restricted)
        except ArtifactIntegrityError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(write, (False, True)))
    successes = [result for result in results if isinstance(result, ArtifactRef)]
    assert len(successes) == 1
    assert sum(isinstance(result, ArtifactIntegrityError) for result in results) == 1
    assert (
        LocalArtifactStore(tmp_path).read_text(successes[0], allow_restricted=True)
        == "same concurrent content"
    )


def test_gc_does_not_allow_classification_downgrade_on_recreation(tmp_path) -> None:
    store = LocalArtifactStore(tmp_path)
    reference = store.put_text("retired evidence", restricted=True, raw_data=True)
    result = store.collect_garbage(
        referenced_digests=set(),
        minimum_age=timedelta(days=1),
        dry_run=False,
        now=datetime.now(UTC) + timedelta(days=2),
    )
    assert result.deleted == (reference.digest,)
    with pytest.raises(ArtifactIntegrityError):
        store.put_text("retired evidence")
    recreated = store.put_text("retired evidence", restricted=True, raw_data=True)
    assert recreated == reference


async def test_existing_catalog_registration_revalidates_uri_and_storage(
    database, tmp_path
) -> None:
    store = LocalArtifactStore(tmp_path)
    catalog = ArtifactCatalog(store)
    reference = store.put_text("catalogued artifact")
    async with database.transaction() as session:
        await catalog.register(session, reference)
    forged = reference.model_copy(update={"uri": f"artifact://sha256/{'0' * 64}"})
    async with database.transaction() as session:
        with pytest.raises(ArtifactIntegrityError):
            await catalog.register(session, forged)
    _blob(store, reference).unlink()
    async with database.transaction() as session:
        with pytest.raises(FileNotFoundError):
            await catalog.register(session, reference)


def test_raw_only_export_requires_restricted_authority(tmp_path) -> None:
    reference = LocalArtifactStore(tmp_path).put_text("raw capture", raw_data=True)
    context = AccessContext(principal="researcher", roles=frozenset(), purpose="research")
    assert not ExportPolicy().decide(reference, context=context).allowed
    assert not ExportPolicy(allow_restricted=True).decide(reference, context=context).allowed
    authorized = AccessContext(
        principal="researcher", roles=frozenset({"restricted-artifact-export"}), purpose="research"
    )
    assert ExportPolicy(allow_restricted=True).decide(reference, context=authorized).allowed
