from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from padawan.artifacts.gcs import GCSArtifactStore
from padawan.artifacts.store import artifact_put_bytes, artifact_read_bytes
from padawan.models.hashing import sha256_digest

pytestmark = [pytest.mark.live, pytest.mark.gcs]


async def test_real_gcs_create_only_concurrency_checksum_and_cleanup() -> None:
    bucket = os.environ.get("PADAWAN_TEST_GCS_BUCKET")
    if not bucket:
        pytest.skip("PADAWAN_TEST_GCS_BUCKET is not configured")
    project = os.environ.get("PADAWAN_TEST_GCS_PROJECT") or os.environ.get("PADAWAN_GCS_PROJECT")
    prefix = f"padawan/round2-contract-tests/{uuid4()}"
    store = GCSArtifactStore(
        bucket_name=bucket,
        prefix=prefix,
        project=project,
        timeout_seconds=60,
    )
    content = b"padawan-gcs-contract-v1"
    hex_digest = sha256_digest(content).removeprefix("sha256:")
    object_name = f"{prefix}/blobs/sha256/{hex_digest[:2]}/{hex_digest[2:]}"
    try:
        first, second = await asyncio.gather(
            artifact_put_bytes(
                store,
                content,
                media_type="application/octet-stream",
                restricted=True,
                raw_data=True,
            ),
            artifact_put_bytes(
                store,
                content,
                media_type="application/octet-stream",
                restricted=True,
                raw_data=True,
            ),
        )
        assert first == second
        reference = first
        assert await artifact_read_bytes(store, reference, allow_restricted=True) == content
        metadata = store.storage_metadata(reference)
        assert metadata["bucket"] == bucket
        assert metadata["generation"]
        assert metadata["crc32c"]
        assert metadata["etag"]
    finally:
        blob = store.bucket.get_blob(object_name, timeout=60)
        if blob is not None and blob.generation is not None:
            blob.delete(
                if_generation_match=int(blob.generation),
                timeout=60,
            )
        store.close()
