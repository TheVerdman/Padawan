from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
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
    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        restricted: bool = False,
        raw_data: bool = False,
    ) -> ArtifactRef: ...

    def read_bytes(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> bytes: ...


@dataclass(frozen=True)
class GarbageCollectionResult:
    scanned: int
    eligible: tuple[str, ...]
    deleted: tuple[str, ...]
    dry_run: bool


class LocalArtifactStore:
    """Immutable local SHA-256 store with atomic writes and verified reads."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.blob_root = self.root / "blobs" / "sha256"
        self.blob_root.mkdir(parents=True, exist_ok=True)

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
        target = self._path_for_hex(hex_digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_path(target, digest, len(content))
        else:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".write-", dir=target.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.chmod(0o600 if restricted else 0o644)
                try:
                    os.replace(temporary, target)
                except OSError:
                    if not target.exists():
                        raise
                self._fsync_directory(target.parent)
                self._verify_path(target, digest, len(content))
            finally:
                temporary.unlink(missing_ok=True)
        return ArtifactRef(
            artifact_id=f"art-{hex_digest}",
            uri=f"artifact://sha256/{hex_digest}",
            digest=digest,
            media_type=media_type,
            size_bytes=len(content),
            restricted=restricted,
            raw_data=raw_data,
        )

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
        if reference.restricted and not allow_restricted:
            raise ArtifactAccessDeniedError(reference.artifact_id)
        match = _URI_RE.fullmatch(reference.uri)
        if match is None:
            raise ArtifactIntegrityError("invalid artifact URI")
        if reference.digest != f"sha256:{match.group(1)}":
            raise ArtifactIntegrityError("URI and digest disagree")
        path = self._path_for_hex(match.group(1))
        content = path.read_bytes()
        if len(content) != reference.size_bytes or sha256_digest(content) != reference.digest:
            raise ArtifactIntegrityError(
                f"artifact failed integrity check: {reference.artifact_id}"
            )
        return content

    def read_text(self, reference: ArtifactRef, *, allow_restricted: bool = False) -> str:
        return self.read_bytes(reference, allow_restricted=allow_restricted).decode("utf-8")

    def verify(self, reference: ArtifactRef) -> bool:
        self.read_bytes(reference, allow_restricted=True)
        return True

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
                deleted.append(f"sha256:{hex_digest}")
        return GarbageCollectionResult(scanned, tuple(eligible), tuple(deleted), dry_run)

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
        existing = await session.scalar(
            select(ArtifactRow).where(ArtifactRow.artifact_id == reference.artifact_id)
        )
        if existing is not None:
            if (
                existing.digest != reference.digest
                or existing.size_bytes != reference.size_bytes
                or existing.media_type != reference.media_type
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
            storage_backend="local",
            metadata_json=metadata or {},
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
