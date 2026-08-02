from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from padawan.artifacts.store import ArtifactBackend, redact_secrets
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest


@dataclass(frozen=True)
class CommandManifest:
    command: str
    status: str
    started_at: datetime
    completed_at: datetime
    configuration: dict[str, object]
    result: dict[str, Any]
    error: dict[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "padawan.command_manifest",
            "version": 1,
            "manifest_id": f"manifest-{sha256_digest(self._semantic())[7:31]}",
            **self._semantic(),
        }

    def _semantic(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
            "configuration": self.configuration,
            "result": self.result,
            "error": self.error,
        }


class ManifestWriter:
    def __init__(self, artifacts: ArtifactBackend) -> None:
        self.artifacts = artifacts

    def write(
        self, manifest: CommandManifest, *, known_secrets: tuple[str, ...] = ()
    ) -> ArtifactRef:
        serialized = json.dumps(manifest.as_dict(), sort_keys=True, indent=2)
        safe = redact_secrets(serialized, known_secrets=known_secrets)
        return self.artifacts.put_text(
            safe,
            media_type="application/vnd.padawan.command-manifest+json",
            restricted=False,
            raw_data=False,
        )


def now() -> datetime:
    return datetime.now(UTC)
