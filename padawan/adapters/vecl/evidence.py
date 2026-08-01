from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from padawan.models.hashing import sha256_digest


@dataclass(frozen=True)
class VeclEvidenceImport:
    source_event_id: str
    source_digest: str
    event_type: str
    payload: dict[str, Any]
    trusted: bool
    trust_reason: str


def import_legacy_event(value: dict[str, Any]) -> VeclEvidenceImport:
    """Translate a VECL-style event without depending on VECL's mutable store.

    Imported records are evidence only. They never become authoritative Padawan
    provenance and are untrusted unless their declared payload hash verifies.
    """

    event_id = str(value.get("event_id") or "")
    event_type = str(value.get("event_type") or value.get("kind") or "")
    payload = value.get("payload")
    if not event_id or not event_type or not isinstance(payload, dict):
        raise ValueError("legacy event is missing event_id, event_type, or payload")
    actual = sha256_digest(payload)
    declared = value.get("payload_hash") or value.get("digest")
    trusted = declared == actual
    return VeclEvidenceImport(
        source_event_id=event_id,
        source_digest=actual,
        event_type=event_type,
        payload=payload,
        trusted=trusted,
        trust_reason=(
            "declared payload hash verified"
            if trusted
            else "missing or mismatched legacy payload hash"
        ),
    )
