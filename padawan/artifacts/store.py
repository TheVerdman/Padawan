from __future__ import annotations

import asyncio
import fcntl
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ArtifactReferenceRow, ArtifactRow

_URI_RE = re.compile(r"^artifact://sha256/([0-9a-f]{64})$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;\"']+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


class ArtifactIntegrityError(RuntimeError):
    pass


class ArtifactAccessDeniedError(PermissionError):
    pass


class ArtifactBackend(Protocol):
    backend_name: str
    networked: bool

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        restricted: bool = False,
        raw_data: bool = False,
    ) -> ArtifactRef: ...

    def read_bytes(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> bytes: ...

    def put_text(
        self,
        text: str,
        *,
        media_type: str = "text/plain; charset=utf-8",
        restricted: bool = False,
        raw_data: bool = False,
        redact: bool = False,
        known_secrets: Iterable[str] = (),
    ) -> ArtifactRef: ...

    def read_text(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> str: ...

    def storage_metadata(self, reference: ArtifactRef) -> dict[str, object]: ...


@dataclass(frozen=True)
class GarbageCollectionResult:
    scanned: int
    eligible: tuple[str, ...]
    deleted: tuple[str, ...]
    dry_run: bool


class LocalArtifactStore:
    """Broker-owned CAS with immutable metadata and serialized publication/GC.

    Metadata is authoritative within the trusted broker filesystem. This interface
    does not isolate a worker that can access that filesystem directly.
    """

    backend_name = "local"
    networked = False

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.blob_root = self.root / "blobs" / "sha256"
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root = self.root / "metadata" / "sha256"
        self.metadata_root.mkdir(parents=True, exist_ok=True)

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
        reference = ArtifactRef(
            artifact_id=f"art-{hex_digest}",
            uri=f"artifact://sha256/{hex_digest}",
            digest=digest,
            media_type=media_type,
            size_bytes=len(content),
            restricted=restricted,
            raw_data=raw_data,
        )
        with self._lock(exclusive=True):
            target = self._path_for_hex(hex_digest)
            metadata_path = self._metadata_path(hex_digest)
            if target.exists() or metadata_path.exists():
                # Never infer classification from a legacy blob or a new caller.
                stored = self._read_metadata(hex_digest)
                self._match_reference(stored, reference)
            else:
                # Publish classification first. A crash leaves a non-readable
                # reservation whose retry must retain the same classification.
                self._atomic_write(metadata_path, canonical_json_bytes(reference), mode=0o600)
            if target.exists():
                self._verify_path(target, digest, len(content))
            else:
                self._atomic_write(target, content, mode=0o600 if restricted or raw_data else 0o644)
                self._verify_path(target, digest, len(content))
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
        hex_digest = self._validate_reference(reference)
        with self._lock(exclusive=False):
            stored = self._read_metadata(hex_digest)
            if (stored.restricted or stored.raw_data) and not allow_restricted:
                raise ArtifactAccessDeniedError("restricted artifact read is not authorized")
            self._match_reference(stored, reference)
            content = self._path_for_hex(hex_digest).read_bytes()
            if len(content) != stored.size_bytes or sha256_digest(content) != stored.digest:
                raise ArtifactIntegrityError("artifact failed integrity check")
            return content

    def read_text(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> str:
        return self.read_bytes(reference, allow_restricted=allow_restricted).decode("utf-8")

    def verify(self, reference: ArtifactRef) -> bool:
        self.read_bytes(reference, allow_restricted=True)
        return True

    def storage_metadata(self, reference: ArtifactRef) -> dict[str, object]:
        self.verify(reference)
        return {
            "layout": "blobs/sha256/{prefix}/{digest}",
            "classification_schema": "1.0.0",
            "classification_digest": sha256_digest(reference),
        }

    def collect_garbage(
        self,
        *,
        referenced_digests: set[str],
        minimum_age: timedelta = timedelta(days=7),
        dry_run: bool = True,
        now: datetime | None = None,
    ) -> GarbageCollectionResult:
        """Delete only unreferenced, aged blobs; dry-run is deliberately the default."""

        reference_hex = {digest.removeprefix("sha256:") for digest in referenced_digests}
        cutoff = (now or datetime.now(UTC)).timestamp() - minimum_age.total_seconds()
        eligible: list[str] = []
        deleted: list[str] = []
        scanned = 0
        with self._lock(exclusive=True):
            for path in sorted(self.blob_root.glob("*/*")):
                if not path.is_file():
                    continue
                scanned += 1
                hex_digest = path.parent.name + path.name
                if hex_digest in reference_hex or path.stat().st_mtime > cutoff:
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}", hex_digest):
                    continue
                eligible.append(f"sha256:{hex_digest}")
                if not dry_run:
                    path.unlink()
                    self._fsync_directory(path.parent)
                    deleted.append(f"sha256:{hex_digest}")
                    # Keep immutable metadata as a classification tombstone.
                    # Re-creating identical bytes must not downgrade authority.
        return GarbageCollectionResult(scanned, tuple(eligible), tuple(deleted), dry_run)

    @contextmanager
    def _lock(self, *, exclusive: bool) -> Iterator[None]:
        with (self.root / ".store.lock").open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _metadata_path(self, hex_digest: str) -> Path:
        path = (self.metadata_root / hex_digest[:2] / hex_digest[2:]).resolve()
        if self.metadata_root not in path.parents:
            raise ArtifactIntegrityError("unsafe artifact metadata path")
        return path

    def _read_metadata(self, hex_digest: str) -> ArtifactRef:
        try:
            content = self._metadata_path(hex_digest).read_bytes()
        except FileNotFoundError:
            raise ArtifactAccessDeniedError("artifact classification is unavailable") from None
        try:
            stored = ArtifactRef.model_validate_json(content)
        except ValueError:
            raise ArtifactIntegrityError("invalid stored artifact metadata") from None
        if (
            self._validate_reference(stored) != hex_digest
            or canonical_json_bytes(stored) != content
        ):
            # This also rejects omitted classification fields filled by schema defaults.
            raise ArtifactIntegrityError("stored artifact metadata is not complete and canonical")
        return stored

    @staticmethod
    def _validate_reference(reference: ArtifactRef) -> str:
        match = _URI_RE.fullmatch(reference.uri)
        if match is None:
            raise ArtifactIntegrityError("invalid artifact URI")
        hex_digest = match.group(1)
        if (
            reference.digest != f"sha256:{hex_digest}"
            or reference.artifact_id != f"art-{hex_digest}"
        ):
            raise ArtifactIntegrityError("artifact identity, URI, and digest disagree")
        return hex_digest

    @staticmethod
    def _match_reference(stored: ArtifactRef, supplied: ArtifactRef) -> None:
        if stored != supplied:
            raise ArtifactIntegrityError("stored metadata conflicts with artifact reference")

    @classmethod
    def _atomic_write(cls, target: Path, content: bytes, *, mode: int) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        cls._fsync_directory(target.parent.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".write-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(mode)
            os.replace(temporary, target)
            cls._fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)

    def _path_for_hex(self, hex_digest: str) -> Path:
        if re.fullmatch(r"[0-9a-f]{64}", hex_digest) is None:
            raise ValueError("invalid SHA-256 digest")
        path = (self.blob_root / hex_digest[:2] / hex_digest[2:]).resolve()
        if self.blob_root not in path.parents:
            raise ValueError("unsafe artifact path")
        return path

    @staticmethod
    def _verify_path(path: Path, digest: str, size: int) -> None:
        content = path.read_bytes()
        if len(content) != size or sha256_digest(content) != digest:
            raise ArtifactIntegrityError(f"existing artifact is corrupt: {path}")

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class ArtifactCatalog:
    """Transactional metadata and ownership references for an ArtifactBackend."""

    def __init__(self, backend: ArtifactBackend) -> None:
        self.backend = backend

    async def register(
        self,
        session: AsyncSession,
        reference: ArtifactRef,
        *,
        metadata: dict[str, object] | None = None,
    ) -> ArtifactRow:
        storage_metadata = await artifact_storage_metadata(self.backend, reference)
        existing = await session.scalar(
            select(ArtifactRow).where(ArtifactRow.artifact_id == reference.artifact_id)
        )
        if existing is not None:
            if (
                existing.digest != reference.digest
                or existing.uri != reference.uri
                or existing.size_bytes != reference.size_bytes
                or existing.media_type != reference.media_type
                or existing.restricted != reference.restricted
                or existing.raw_data != reference.raw_data
                or existing.storage_backend != self.backend.backend_name
            ):
                raise ArtifactIntegrityError("catalog record conflicts with artifact reference")
            return existing
        row = ArtifactRow(
            artifact_id=reference.artifact_id,
            digest=reference.digest,
            uri=reference.uri,
            media_type=reference.media_type,
            size_bytes=reference.size_bytes,
            restricted=reference.restricted,
            raw_data=reference.raw_data,
            storage_backend=self.backend.backend_name,
            metadata_json={**(metadata or {}), **storage_metadata},
            created_at=datetime.now(UTC),
        )
        session.add(row)
        await session.flush()
        return row

    async def reference(
        self,
        session: AsyncSession,
        reference: ArtifactRef,
        *,
        owner_type: str,
        owner_id: str,
    ) -> None:
        await self.register(session, reference)
        existing = await session.scalar(
            select(ArtifactReferenceRow).where(
                ArtifactReferenceRow.owner_type == owner_type,
                ArtifactReferenceRow.owner_id == owner_id,
                ArtifactReferenceRow.artifact_id == reference.artifact_id,
            )
        )
        if existing is None:
            session.add(
                ArtifactReferenceRow(
                    reference_id=f"aref-{uuid4()}",
                    owner_type=owner_type,
                    owner_id=owner_id,
                    artifact_id=reference.artifact_id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.flush()

    async def referenced_digests(self, session: AsyncSession) -> set[str]:
        result = await session.execute(
            select(ArtifactRow.digest)
            .join(
                ArtifactReferenceRow,
                ArtifactReferenceRow.artifact_id == ArtifactRow.artifact_id,
            )
            .distinct()
        )
        return set(result.scalars())


async def artifact_put_bytes(
    backend: ArtifactBackend,
    content: bytes,
    *,
    media_type: str,
    restricted: bool = False,
    raw_data: bool = False,
) -> ArtifactRef:
    if backend.networked:
        return await asyncio.to_thread(
            backend.put_bytes,
            content,
            media_type=media_type,
            restricted=restricted,
            raw_data=raw_data,
        )
    return backend.put_bytes(
        content, media_type=media_type, restricted=restricted, raw_data=raw_data
    )


async def artifact_put_text(
    backend: ArtifactBackend,
    text: str,
    *,
    media_type: str = "text/plain; charset=utf-8",
    restricted: bool = False,
    raw_data: bool = False,
    redact: bool = False,
    known_secrets: Iterable[str] = (),
) -> ArtifactRef:
    if backend.networked:
        return await asyncio.to_thread(
            backend.put_text,
            text,
            media_type=media_type,
            restricted=restricted,
            raw_data=raw_data,
            redact=redact,
            known_secrets=known_secrets,
        )
    return backend.put_text(
        text,
        media_type=media_type,
        restricted=restricted,
        raw_data=raw_data,
        redact=redact,
        known_secrets=known_secrets,
    )


async def artifact_read_bytes(
    backend: ArtifactBackend,
    reference: ArtifactRef,
    *,
    allow_restricted: bool = False,
) -> bytes:
    if backend.networked:
        return await asyncio.to_thread(
            backend.read_bytes, reference, allow_restricted=allow_restricted
        )
    return backend.read_bytes(reference, allow_restricted=allow_restricted)


async def artifact_storage_metadata(
    backend: ArtifactBackend, reference: ArtifactRef
) -> dict[str, object]:
    if backend.networked:
        return await asyncio.to_thread(backend.storage_metadata, reference)
    return backend.storage_metadata(reference)


def redact_secrets(text: str, *, known_secrets: Iterable[str] = ()) -> str:
    result = text
    for secret in known_secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result
