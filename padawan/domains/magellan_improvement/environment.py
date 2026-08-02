from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from padawan.domains.magellan_improvement.contracts import (
    MagellanEnvironmentAssessment,
    MagellanEnvironmentHandshake,
    MagellanRepositorySnapshot,
    MagellanSourceFile,
)
from padawan.models.hashing import sha256_digest

_MAX_UNTRACKED_FILE_BYTES = 16 * 1024 * 1024
_MAX_UNTRACKED_TOTAL_BYTES = 128 * 1024 * 1024
_DEPENDENCY_PATHS = (
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
)
_MIGRATION_ROOTS = ("alembic/versions", "backend/alembic/versions")
_VOLATILE_NAMES = {
    ".DS_Store",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "target",
    "venv",
}
_SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".jks"}
_SENSITIVE_EXACT_NAMES = {".netrc", ".npmrc", ".pypirc", "id_rsa", "id_ed25519"}
_IGNORED_SENSITIVE_PATHSPECS = (
    ":(glob)**/.env",
    ":(glob)**/.env.*",
    ":(glob)**/*.key",
    ":(glob)**/*.pem",
    ":(glob)**/*.p12",
    ":(glob)**/*.pfx",
    ":(glob)**/*.jks",
    ":(glob)**/*credential*",
    ":(glob)**/*secret*",
    ":(glob)**/*private_key*",
    ":(glob)**/*service-account*",
    ":(glob)**/*api_key*",
    ":(glob)**/*api-key*",
    ":(glob)**/*password*",
    ":(glob)**/.token",
    ":(glob)**/.netrc",
    ":(glob)**/.npmrc",
    ":(glob)**/.pypirc",
    ":(glob)**/id_rsa",
    ":(glob)**/id_ed25519",
)


class MagellanEnvironmentInspector:
    """Fingerprint an external Magellan tree without importing it or storing its local path."""

    def inspect_repository(
        self,
        repository_root: Path,
        *,
        created_at: datetime | None = None,
    ) -> MagellanRepositorySnapshot:
        try:
            root = repository_root.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("configured Magellan repository is unavailable") from exc
        if not root.is_dir():
            raise ValueError("Magellan repository root must be a directory")
        commit_sha = self._git_text(root, "rev-parse", "HEAD").strip()
        branch_value = self._git_text(
            root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        branch = branch_value.strip() or None
        tracked_patch = self._git_bytes(root, "diff", "--binary", "HEAD", "--")
        tracked_paths = _nul_paths(self._git_bytes(root, "diff", "--name-only", "-z", "HEAD", "--"))
        untracked_paths = _nul_paths(
            self._git_bytes(
                root,
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            )
        )
        ignored_sensitive_paths = _nul_paths(
            self._git_bytes(
                root,
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                "--",
                *_IGNORED_SENSITIVE_PATHSPECS,
            )
        )
        volatile: list[str] = []
        sensitive = [
            _safe_relative(relative)
            for relative in ignored_sensitive_paths
            if _is_sensitive(relative) and not _is_volatile(relative)
        ]
        untracked_manifest: list[MagellanSourceFile] = []
        total_bytes = 0
        for relative in sorted(untracked_paths):
            safe_relative = _safe_relative(relative)
            if _is_volatile(safe_relative):
                volatile.append(safe_relative)
                continue
            if _is_sensitive(safe_relative):
                sensitive.append(safe_relative)
                continue
            path = root / safe_relative
            if path.is_symlink() or not path.is_file():
                sensitive.append(safe_relative)
                continue
            try:
                size = path.stat().st_size
            except OSError:
                sensitive.append(safe_relative)
                continue
            if size > _MAX_UNTRACKED_FILE_BYTES or total_bytes + size > _MAX_UNTRACKED_TOTAL_BYTES:
                sensitive.append(safe_relative)
                continue
            try:
                payload = path.read_bytes()
            except OSError:
                sensitive.append(safe_relative)
                continue
            total_bytes += len(payload)
            untracked_manifest.append(
                MagellanSourceFile(
                    path=safe_relative,
                    size_bytes=len(payload),
                    digest=sha256_digest(payload),
                )
            )
        tracked_changed_paths = tuple(sorted(_safe_relative(path) for path in tracked_paths))
        included_files = tuple(untracked_manifest)
        volatile_paths = tuple(sorted(volatile))
        sensitive_paths = tuple(sorted(sensitive))
        tracked_diff_digest = sha256_digest(tracked_patch)
        untracked_manifest_digest = sha256_digest(
            [entry.model_dump(mode="json") for entry in included_files]
        )
        dependency_digest = sha256_digest(_current_file_manifest(root, _DEPENDENCY_PATHS))
        migration_digest = sha256_digest(
            [
                entry
                for relative_root in _MIGRATION_ROOTS
                for entry in _current_glob_manifest(root, relative_root, "*.py")
            ]
        )
        source_digest = sha256_digest(
            {
                "commit_sha": commit_sha,
                "tracked_diff_digest": tracked_diff_digest,
                "untracked_manifest_digest": untracked_manifest_digest,
                "excluded_volatile_paths": volatile_paths,
                "excluded_sensitive_paths": sensitive_paths,
                "dependency_digest": dependency_digest,
                "migration_digest": migration_digest,
            }
        )
        timestamp = created_at or datetime.now(UTC)
        return MagellanRepositorySnapshot(
            snapshot_id=f"magellan-source-{source_digest[7:31]}",
            commit_sha=commit_sha,
            branch=branch,
            dirty=bool(
                tracked_changed_paths or included_files or volatile_paths or sensitive_paths
            ),
            tracked_changed_paths=tracked_changed_paths,
            included_untracked_files=included_files,
            excluded_volatile_paths=volatile_paths,
            excluded_sensitive_paths=sensitive_paths,
            tracked_diff_digest=tracked_diff_digest,
            untracked_manifest_digest=untracked_manifest_digest,
            dependency_digest=dependency_digest,
            migration_digest=migration_digest,
            source_digest=source_digest,
            created_at=timestamp,
        )

    def assess(
        self,
        repository_root: Path,
        *,
        handshake_path: Path | None = None,
        created_at: datetime | None = None,
    ) -> MagellanEnvironmentAssessment:
        repository = self.inspect_repository(repository_root, created_at=created_at)
        handshake = self.load_handshake(handshake_path) if handshake_path is not None else None
        blockers = (
            ("environment handshake is missing",)
            if handshake is None
            else handshake.evaluation_blockers()
        )
        if handshake is not None and (
            handshake.repository_source_digest != repository.source_digest
        ):
            blockers = (*blockers, "handshake repository digest does not match the worktree")
        return MagellanEnvironmentAssessment(
            repository=repository,
            handshake=handshake,
            environment_fingerprint=(handshake.fingerprint if handshake is not None else None),
            ready=not blockers,
            blockers=blockers,
        )

    @staticmethod
    def load_handshake(path: Path) -> MagellanEnvironmentHandshake:
        try:
            selected = path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError("configured Magellan environment handshake is unavailable") from exc
        if not selected.is_file():
            raise ValueError("Magellan environment handshake must be a regular file")
        try:
            size = selected.stat().st_size
        except OSError as exc:
            raise ValueError("configured Magellan environment handshake is unreadable") from exc
        if size > 1_048_576:
            raise ValueError("Magellan environment handshake is unexpectedly large")
        try:
            payload = json.loads(selected.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("configured Magellan environment handshake is unreadable") from exc
        try:
            return MagellanEnvironmentHandshake.model_validate(payload, strict=False)
        except ValidationError as exc:
            raise ValueError("configured Magellan environment handshake is invalid") from exc

    @staticmethod
    def _git_bytes(root: Path, *arguments: str, check: bool = True) -> bytes:
        try:
            result = subprocess.run(
                ("git", "-C", str(root), *arguments),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Magellan repository inspection timed out") from exc
        if check and result.returncode != 0:
            message = (
                result.stderr.decode("utf-8", errors="replace")
                .strip()
                .replace(str(root), "[MAGELLAN_REPOSITORY]")
            )
            raise ValueError(f"Magellan repository inspection failed: {message}")
        return result.stdout

    @classmethod
    def _git_text(cls, root: Path, *arguments: str, check: bool = True) -> str:
        return cls._git_bytes(root, *arguments, check=check).decode("utf-8", errors="strict")


def _nul_paths(payload: bytes) -> tuple[str, ...]:
    return tuple(entry.decode("utf-8", errors="strict") for entry in payload.split(b"\0") if entry)


def _safe_relative(value: str) -> str:
    normalized = PurePosixPath(value)
    if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts:
        raise ValueError("Git returned an unsafe repository-relative path")
    return normalized.as_posix()


def _is_volatile(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return any(part in _VOLATILE_NAMES for part in parts)


def _is_sensitive(relative: str) -> bool:
    path = PurePosixPath(relative)
    name = path.name.casefold()
    name_parts = set(name.replace("-", "_").replace(".", "_").split("_"))
    if name in {".env.example", ".env.sample", ".env.template"}:
        return False
    if name in _SENSITIVE_EXACT_NAMES:
        return True
    if name == ".env" or name.startswith(".env."):
        return True
    if path.suffix.casefold() in _SENSITIVE_SUFFIXES:
        return True
    if name_parts.intersection({"secret", "secrets", "token", "tokens", "password", "passwd"}):
        return True
    return any(
        token in name
        for token in (
            "credential",
            "client_secret",
            "private_key",
            "service-account",
            "service_account",
            "api_key",
            "api-key",
            "password",
        )
    )


def _current_file_manifest(root: Path, relative_paths: tuple[str, ...]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for relative in relative_paths:
        path = root / relative
        if path.is_file() and not path.is_symlink():
            try:
                payload = path.read_bytes()
            except OSError as exc:
                raise ValueError("Magellan dependency or migration file is unreadable") from exc
            manifest.append(
                {"path": relative, "size_bytes": len(payload), "digest": sha256_digest(payload)}
            )
    return manifest


def _current_glob_manifest(root: Path, relative_root: str, pattern: str) -> list[dict[str, Any]]:
    directory = root / relative_root
    if not directory.is_dir():
        return []
    paths = tuple(
        path for path in directory.glob(pattern) if path.is_file() and not path.is_symlink()
    )
    return _current_file_manifest(
        root,
        tuple(path.relative_to(root).as_posix() for path in sorted(paths)),
    )
