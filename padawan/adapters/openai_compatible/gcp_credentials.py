"""Expiry-aware use of the operator's existing gcloud login; never persist credentials."""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime


class CredentialRefreshError(RuntimeError):
    """An authentication precondition failed before the protected request."""


@dataclass(frozen=True)
class AccessCredential:
    access_token: str = field(repr=False)
    expires_at: datetime

    def usable(self, minimum_seconds: float, *, now: datetime | None = None) -> bool:
        return (self.expires_at - (now or datetime.now(UTC))).total_seconds() >= minimum_seconds


def parse_credential(raw: str, minimum_seconds: float) -> AccessCredential:
    """Reject absent/unknown expiry instead of assigning an assumed token lifetime."""
    try:
        value = json.loads(raw)["credential"]
        token = value["access_token"]
        expires = datetime.fromisoformat(value["token_expiry"].replace("Z", "+00:00"))
        if (
            not isinstance(token, str)
            or not token
            or any(c.isspace() for c in token)
            or expires.tzinfo is None
        ):
            raise ValueError
        credential = AccessCredential(token, expires)
        if not credential.usable(minimum_seconds):
            raise ValueError
        return credential
    except (KeyError, TypeError, ValueError, AttributeError):
        # Raw helper output can contain a credential; never attach it to an exception.
        raise CredentialRefreshError(
            "GCP credential or its remaining lifetime is invalid"
        ) from None


def credential_command(gcloud: str, minimum_seconds: float, *, force: bool = False) -> list[str]:
    if not 0 < minimum_seconds < 3600:
        raise ValueError("GCP credential validity must be positive and below one hour")
    return [
        gcloud,
        "config",
        "config-helper",
        "--format=json",
        "--force-auth-refresh" if force else f"--min-expiry={math.ceil(minimum_seconds + 15)}s",
    ]


class GcloudCredentialSource:
    """Cache only until the expiry actually reported by gcloud, with a request-sized margin."""

    def __init__(self, gcloud: str) -> None:
        self.gcloud = gcloud
        self._cached: AccessCredential | None = None
        self._lock = asyncio.Lock()

    def get(self, minimum_seconds: float = 120, *, force: bool = False) -> AccessCredential:
        if not force and self._cached and self._cached.usable(minimum_seconds + 15):
            return self._cached
        try:
            result = subprocess.run(
                credential_command(self.gcloud, minimum_seconds, force=force),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=45,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise CredentialRefreshError("GCP credential helper failed") from None
        if result.returncode:
            raise CredentialRefreshError("GCP credential helper failed")
        self._cached = parse_credential(result.stdout, minimum_seconds)
        return self._cached

    async def get_async(self, minimum_seconds: float = 120) -> AccessCredential:
        async with self._lock:
            if self._cached and self._cached.usable(minimum_seconds + 15):
                return self._cached
            try:
                child = await asyncio.create_subprocess_exec(
                    *credential_command(self.gcloud, minimum_seconds),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError:
                raise CredentialRefreshError("GCP credential helper failed") from None
            try:
                raw, _ = await asyncio.wait_for(child.communicate(), timeout=45)
            except BaseException:
                if child.returncode is None:
                    child.kill()
                await child.wait()
                raise
            if child.returncode:
                raise CredentialRefreshError("GCP credential helper failed")
            self._cached = parse_credential(raw.decode(), minimum_seconds)
            return self._cached
