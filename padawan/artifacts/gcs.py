from __future__ import annotations

import base64
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, cast

import google_crc32c
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage  # type: ignore[import-untyped]
from google.cloud.storage.retry import (  # type: ignore[import-untyped]
    DEFAULT_RETRY,
    DEFAULT_RETRY_IF_GENERATION_SPECIFIED,
)

from padawan.artifacts.store import (
    ArtifactAccessDeniedError,
    ArtifactIntegrityError,
    GarbageCollectionResult,
    redact_secrets,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest

_URI_RE = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_OBJECT_RE = re.compile(
    r"^(?P<prefix>.+)/blobs/sha256/(?P<head>[0-9a-f]{2})/(?P<tail>[0-9a-f]{62})$"
)


class GCSArtifactStore:
    """Immutable SHA-256 object store backed by Google Cloud Storage."""

    backend_name = "gcs"
    networked = True

    def __init__(
        self,
        *,
        bucket_name: str,
        prefix: str = "padawan/artifacts",
        project: str | None = None,
        timeout_seconds: float = 60.0,
        client: Any | None = None,
    ) -> None:
        if not bucket_name or bucket_name.startswith("gs://") or "/" in bucket_name:
            raise ValueError("bucket_name must be a bare GCS bucket name")
        normalized_prefix = prefix.strip("/")
        if not normalized_prefix or any(
            part in {"", ".", ".."} for part in normalized_prefix.split("/")
        ):
            raise ValueError("GCS artifact prefix is invalid")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.bucket_name = bucket_name
        self.prefix = normalized_prefix
        self.project = project
        self.timeout_seconds = timeout_seconds
        self.client = client or storage.Client(project=project)
        self.bucket = self.client.bucket(bucket_name)
        self._metadata: dict[str, dict[str, object]] = {}
        self._metadata_lock = Lock()

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        restricted: bool = False,
        raw_data: bool = False,
    ) -> ArtifactRef:
        if not media_type.strip():
            raise ValueError("media_type must be non-empty")
        digest = sha256_digest(content)
        hex_digest = digest.removeprefix("sha256:")
        expected_crc32c = _crc32c_base64(content)
        name = self._object_name(hex_digest)
        blob = self.bucket.blob(name)
        expected_metadata = {
            "padawan-sha256": hex_digest,
            "padawan-size": str(len(content)),
            "padawan-media-type": media_type,
            "padawan-restricted": str(restricted).lower(),
            "padawan-raw-data": str(raw_data).lower(),
            "padawan-crc32c": expected_crc32c,
        }
        blob.metadata = expected_metadata
        try:
            blob.upload_from_string(
                content,
                content_type=media_type,
                if_generation_match=0,
                checksum="crc32c",
                timeout=self.timeout_seconds,
                retry=DEFAULT_RETRY_IF_GENERATION_SPECIFIED,
            )
        except PreconditionFailed:
            blob = self.bucket.get_blob(
                name,
                timeout=self.timeout_seconds,
                retry=DEFAULT_RETRY,
            )
            if blob is None:
                raise ArtifactIntegrityError(
                    "GCS precondition failed but object is absent"
                ) from None
        else:
            blob.reload(timeout=self.timeout_seconds, retry=DEFAULT_RETRY)
        reference = ArtifactRef(
            artifact_id=f"art-{hex_digest}",
            uri=f"artifact://sha256/{hex_digest}",
            digest=digest,
            media_type=media_type,
            size_bytes=len(content),
            restricted=restricted,
            raw_data=raw_data,
        )
        self._validate_blob(
            blob,
            reference,
            expected_metadata=expected_metadata,
            expected_crc32c=expected_crc32c,
        )
        self._cache_metadata(reference, blob)
        return reference

    def put_text(
        self,
        text: str,
        *,
        media_type: str = "text/plain; charset=utf-8",
        restricted: bool = False,
        raw_data: bool = False,
        redact: bool = False,
        known_secrets: Iterable[str] = (),
    ) -> ArtifactRef:
        safe_text = redact_secrets(text, known_secrets=known_secrets) if redact else text
        return self.put_bytes(
            safe_text.encode("utf-8"),
            media_type=media_type,
            restricted=restricted,
            raw_data=raw_data,
        )

    def read_bytes(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> bytes:
        if (reference.restricted or reference.raw_data) and not allow_restricted:
            raise ArtifactAccessDeniedError(reference.artifact_id)
        hex_digest = self._validate_reference(reference)
        name = self._object_name(hex_digest)
        blob = self.bucket.get_blob(name, timeout=self.timeout_seconds, retry=DEFAULT_RETRY)
        if blob is None:
            raise FileNotFoundError(reference.artifact_id)
        self._validate_blob(blob, reference)
        generation = int(blob.generation) if blob.generation is not None else None
        content = cast(
            bytes,
            blob.download_as_bytes(
                if_generation_match=generation,
                checksum="crc32c",
                timeout=self.timeout_seconds,
                retry=DEFAULT_RETRY_IF_GENERATION_SPECIFIED,
            ),
        )
        if len(content) != reference.size_bytes or sha256_digest(content) != reference.digest:
            raise ArtifactIntegrityError(
                f"GCS artifact failed integrity check: {reference.artifact_id}"
            )
        self._cache_metadata(reference, blob)
        return content

    def read_text(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> str:
        return self.read_bytes(reference, allow_restricted=allow_restricted).decode("utf-8")

    def verify(self, reference: ArtifactRef) -> bool:
        self.read_bytes(reference, allow_restricted=True)
        return True

    def storage_metadata(self, reference: ArtifactRef) -> dict[str, object]:
        # A cached digest is not authority for a new caller's classification.
        # Revalidate against the stored object before catalog admission.
        hex_digest = self._validate_reference(reference)
        blob = self.bucket.get_blob(
            self._object_name(hex_digest),
            timeout=self.timeout_seconds,
            retry=DEFAULT_RETRY,
        )
        if blob is None:
            raise FileNotFoundError(reference.artifact_id)
        self._validate_blob(blob, reference)
        self._cache_metadata(reference, blob)
        with self._metadata_lock:
            return dict(self._metadata[reference.digest])

    def collect_garbage(
        self,
        *,
        referenced_digests: set[str],
        minimum_age: timedelta = timedelta(days=7),
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> GarbageCollectionResult:
        reference_hex = {digest.removeprefix("sha256:") for digest in referenced_digests}
        cutoff = now or datetime.now(UTC)
        cutoff = cutoff - minimum_age
        eligible: list[str] = []
        deleted: list[str] = []
        scanned = 0
        object_prefix = f"{self.prefix}/blobs/sha256/"
        for blob in self.client.list_blobs(
            self.bucket_name,
            prefix=object_prefix,
            timeout=self.timeout_seconds,
            retry=DEFAULT_RETRY,
        ):
            match = _OBJECT_RE.fullmatch(str(blob.name))
            if match is None or match.group("prefix") != self.prefix:
                continue
            scanned += 1
            hex_digest = match.group("head") + match.group("tail")
            updated = blob.updated
            if hex_digest in reference_hex or updated is None or updated > cutoff:
                continue
            digest = f"sha256:{hex_digest}"
            eligible.append(digest)
            if not dry_run:
                try:
                    blob.delete(
                        if_generation_match=int(blob.generation),
                        timeout=self.timeout_seconds,
                        retry=DEFAULT_RETRY_IF_GENERATION_SPECIFIED,
                    )
                except NotFound:
                    continue
                deleted.append(digest)
        return GarbageCollectionResult(scanned, tuple(eligible), tuple(deleted), dry_run)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def _validate_reference(self, reference: ArtifactRef) -> str:
        match = _URI_RE.fullmatch(reference.uri)
        if match is None:
            raise ArtifactIntegrityError("invalid artifact URI")
        if (
            reference.digest != f"sha256:{match.group(1)}"
            or reference.artifact_id != f"art-{match.group(1)}"
        ):
            raise ArtifactIntegrityError("artifact identity, URI, and digest disagree")
        return match.group(1)

    def _object_name(self, hex_digest: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", hex_digest) is None:
            raise ValueError("invalid SHA-256 digest")
        return f"{self.prefix}/blobs/sha256/{hex_digest[:2]}/{hex_digest[2:]}"

    def _validate_blob(
        self,
        blob: Any,
        reference: ArtifactRef,
        *,
        expected_metadata: dict[str, str] | None = None,
        expected_crc32c: str | None = None,
    ) -> None:
        metadata = dict(blob.metadata or {})
        required = expected_metadata or {
            "padawan-sha256": reference.digest.removeprefix("sha256:"),
            "padawan-size": str(reference.size_bytes),
            "padawan-media-type": reference.media_type,
            "padawan-restricted": str(reference.restricted).lower(),
            "padawan-raw-data": str(reference.raw_data).lower(),
        }
        if any(metadata.get(key) != value for key, value in required.items()):
            raise ArtifactIntegrityError("GCS object metadata conflicts with artifact reference")
        try:
            observed_size = int(blob.size)
        except (TypeError, ValueError) as exc:
            raise ArtifactIntegrityError("GCS object has invalid size evidence") from exc
        if observed_size != reference.size_bytes:
            raise ArtifactIntegrityError("GCS object size conflicts with artifact reference")
        if blob.content_type != reference.media_type:
            raise ArtifactIntegrityError("GCS object media type conflicts with artifact reference")
        if blob.generation is None or blob.crc32c is None:
            raise ArtifactIntegrityError("GCS object lacks generation or CRC32C evidence")
        observed_crc32c = str(blob.crc32c)
        if metadata.get("padawan-crc32c") != observed_crc32c:
            raise ArtifactIntegrityError("GCS object CRC32C conflicts with immutable metadata")
        if expected_crc32c is not None and observed_crc32c != expected_crc32c:
            raise ArtifactIntegrityError("GCS object CRC32C conflicts with uploaded content")

    def _cache_metadata(self, reference: ArtifactRef, blob: Any) -> None:
        metadata: dict[str, object] = {
            "bucket": self.bucket_name,
            "object": str(blob.name),
            "generation": str(blob.generation),
            "metageneration": str(blob.metageneration),
            "etag": str(blob.etag),
            "crc32c": str(blob.crc32c),
            "md5_hash": str(blob.md5_hash) if blob.md5_hash is not None else None,
            "project": self.project,
        }
        with self._metadata_lock:
            self._metadata[reference.digest] = metadata


def _crc32c_base64(content: bytes) -> str:
    checksum = google_crc32c.value(content)
    return base64.b64encode(checksum.to_bytes(4, byteorder="big")).decode("ascii")
