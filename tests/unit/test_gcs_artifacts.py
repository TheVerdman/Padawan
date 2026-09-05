from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from google.api_core.exceptions import PreconditionFailed, ServiceUnavailable

from padawan.artifacts.gcs import GCSArtifactStore
from padawan.artifacts.store import (
    ArtifactAccessDeniedError,
    ArtifactCatalog,
    ArtifactIntegrityError,
    artifact_put_bytes,
    artifact_read_bytes,
)
from padawan.models.database import Database
from padawan.models.tables import ArtifactRow


@dataclass
class _Object:
    content: bytes
    metadata: dict[str, str]
    content_type: str
    generation: int
    metageneration: int
    etag: str
    crc32c: str
    md5_hash: str | None
    updated: datetime


class _Blob:
    def __init__(self, bucket: _Bucket, name: str) -> None:
        self.bucket = bucket
        self.name = name
        self.metadata: dict[str, str] | None = None
        self.content_type: str | None = None
        self.size: int | None = None
        self.generation: int | None = None
        self.metageneration: int | None = None
        self.etag: str | None = None
        self.crc32c: str | None = None
        self.md5_hash: str | None = None
        self.updated: datetime | None = None
        if name in bucket.objects:
            self._reload_from_object()

    def upload_from_string(self, content: bytes, **kwargs: Any) -> None:
        self.bucket.upload_attempts += 1
        assert kwargs["if_generation_match"] == 0
        assert kwargs["checksum"] == "crc32c"
        assert kwargs["retry"] is not None
        if self.bucket.fail_next_upload:
            self.bucket.fail_next_upload = False
            raise ServiceUnavailable("interrupted upload")
        if self.name in self.bucket.objects:
            raise PreconditionFailed("object exists")  # type: ignore[no-untyped-call]
        self.bucket.objects[self.name] = _Object(
            content=content,
            metadata=dict(self.metadata or {}),
            content_type=str(kwargs["content_type"]),
            generation=1,
            metageneration=1,
            etag="fake-etag-1",
            crc32c=str((self.metadata or {})["padawan-crc32c"]),
            md5_hash=None,
            updated=datetime.now(UTC),
        )
        self._reload_from_object()

    def reload(self, **kwargs: Any) -> None:
        assert kwargs["timeout"] > 0
        self._reload_from_object()

    def download_as_bytes(self, **kwargs: Any) -> bytes:
        record = self.bucket.objects[self.name]
        assert kwargs["if_generation_match"] == record.generation
        assert kwargs["checksum"] == "crc32c"
        assert kwargs["retry"] is not None
        return record.content

    def delete(self, **kwargs: Any) -> None:
        record = self.bucket.objects[self.name]
        assert kwargs["if_generation_match"] == record.generation
        assert kwargs["retry"] is not None
        del self.bucket.objects[self.name]

    def _reload_from_object(self) -> None:
        record = self.bucket.objects[self.name]
        self.metadata = dict(record.metadata)
        self.content_type = record.content_type
        self.size = len(record.content)
        self.generation = record.generation
        self.metageneration = record.metageneration
        self.etag = record.etag
        self.crc32c = record.crc32c
        self.md5_hash = record.md5_hash
        self.updated = record.updated


class _Bucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects: dict[str, _Object] = {}
        self.upload_attempts = 0
        self.fail_next_upload = False

    def blob(self, name: str) -> _Blob:
        return _Blob(self, name)

    def get_blob(self, name: str, **kwargs: Any) -> _Blob | None:
        del kwargs
        return _Blob(self, name) if name in self.objects else None


class _Client:
    def __init__(self, bucket_name: str = "test-bucket") -> None:
        self.bucket_instance = _Bucket(bucket_name)
        self.closed = False

    def bucket(self, name: str) -> _Bucket:
        assert name == self.bucket_instance.name
        return self.bucket_instance

    def list_blobs(self, bucket_name: str, *, prefix: str, **kwargs: Any) -> list[_Blob]:
        assert bucket_name == self.bucket_instance.name
        assert kwargs["retry"] is not None
        return [
            _Blob(self.bucket_instance, name)
            for name in sorted(self.bucket_instance.objects)
            if name.startswith(prefix)
        ]

    def close(self) -> None:
        self.closed = True


def _store() -> tuple[GCSArtifactStore, _Client]:
    client = _Client()
    store = GCSArtifactStore(
        bucket_name="test-bucket",
        prefix="padawan/tests",
        project="test-project",
        client=client,
    )
    return store, client


def test_gcs_content_addressing_deduplicates_with_generation_precondition() -> None:
    store, client = _store()

    first = store.put_bytes(b"same-content", media_type="application/octet-stream")
    second = store.put_bytes(b"same-content", media_type="application/octet-stream")

    assert first == second
    assert client.bucket_instance.upload_attempts == 2
    assert len(client.bucket_instance.objects) == 1
    assert store.read_bytes(first) == b"same-content"
    metadata = store.storage_metadata(first)
    assert metadata["bucket"] == "test-bucket"
    assert metadata["generation"] == "1"
    assert metadata["crc32c"]


def test_gcs_restricted_access_and_remote_corruption_are_enforced() -> None:
    store, client = _store()
    reference = store.put_bytes(
        b"private-data",
        media_type="application/octet-stream",
        restricted=True,
        raw_data=True,
    )

    with pytest.raises(ArtifactAccessDeniedError):
        store.read_bytes(reference)

    assert store.read_bytes(reference, allow_restricted=True) == b"private-data"
    record = next(iter(client.bucket_instance.objects.values()))
    record.content = b"changed-data"
    with pytest.raises(ArtifactIntegrityError, match="integrity check"):
        store.read_bytes(reference, allow_restricted=True)


def test_gcs_deduplication_rejects_classification_conflicts() -> None:
    store, _ = _store()
    store.put_bytes(b"same", media_type="text/plain", restricted=False)

    with pytest.raises(ArtifactIntegrityError, match="metadata conflicts"):
        store.put_bytes(b"same", media_type="text/plain", restricted=True)


def test_gcs_interrupted_upload_never_returns_or_caches_a_false_success() -> None:
    store, client = _store()
    client.bucket_instance.fail_next_upload = True

    with pytest.raises(ServiceUnavailable, match="interrupted upload"):
        store.put_bytes(b"retry-safe", media_type="application/octet-stream")

    assert not client.bucket_instance.objects
    reference = store.put_bytes(b"retry-safe", media_type="application/octet-stream")
    assert store.read_bytes(reference) == b"retry-safe"


def test_gcs_raw_only_content_requires_explicit_restricted_access() -> None:
    store, _ = _store()
    reference = store.put_text("raw capture", raw_data=True)
    with pytest.raises(ArtifactAccessDeniedError):
        store.read_bytes(reference)
    assert store.read_text(reference, allow_restricted=True) == "raw capture"


def test_gcs_cached_metadata_cannot_authorize_a_relabelled_reference() -> None:
    store, _ = _store()
    reference = store.put_text("protected capture", restricted=True, raw_data=True)
    assert store.storage_metadata(reference)
    forged = reference.model_copy(update={"restricted": False, "raw_data": False})
    with pytest.raises(ArtifactIntegrityError):
        store.storage_metadata(forged)


def test_gcs_storage_metadata_rechecks_the_stored_classification() -> None:
    store, client = _store()
    reference = store.put_text("protected capture", restricted=True, raw_data=True)
    record = next(iter(client.bucket_instance.objects.values()))
    record.metadata["padawan-restricted"] = "false"
    with pytest.raises(ArtifactIntegrityError):
        store.storage_metadata(reference)


def test_gcs_garbage_collection_is_dry_run_by_default() -> None:
    store, client = _store()
    reference = store.put_bytes(b"orphan", media_type="text/plain")
    record = next(iter(client.bucket_instance.objects.values()))
    record.updated = datetime.now(UTC) - timedelta(days=8)

    dry = store.collect_garbage(referenced_digests=set())
    assert dry.eligible == (reference.digest,)
    assert dry.deleted == ()
    assert client.bucket_instance.objects

    deleted = store.collect_garbage(referenced_digests=set(), dry_run=False)
    assert deleted.deleted == (reference.digest,)
    assert not client.bucket_instance.objects


async def test_gcs_async_facade_and_catalog_preserve_backend_metadata(
    database: Database,
) -> None:
    store, _ = _store()
    reference = await artifact_put_bytes(
        store,
        b"catalogued",
        media_type="application/octet-stream",
        restricted=True,
        raw_data=True,
    )
    assert await artifact_read_bytes(store, reference, allow_restricted=True) == b"catalogued"

    async with database.transaction() as session:
        row = await ArtifactCatalog(store).register(session, reference)
        assert row.storage_backend == "gcs"
        assert row.metadata_json["bucket"] == "test-bucket"

    async with database.transaction() as session:
        stored = await session.get(ArtifactRow, reference.artifact_id)
        assert stored is not None
        assert stored.restricted
        assert stored.raw_data
