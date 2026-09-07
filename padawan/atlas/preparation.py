"""Shared manifest construction; preserve the original serialized campaign values."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from padawan.models.hashing import sha256_digest
from padawan.orchestration.source_identity import source_identity


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def record[T: BaseModel](model: type[T], value: Any) -> T:
    return model.model_validate_json(json.dumps(value))


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        stream.write(json.dumps(value, indent=2) + "\n")


def component(name: str, version: str, value: Any, evidence: str) -> dict[str, Any]:
    return {
        "component_id": name,
        "version": version,
        "digest": sha256_digest(value),
        "evidence_status": "pinned",
        "evidence": evidence,
    }


def limit(unit: str, value: float) -> dict[str, Any]:
    return {"disposition": "capped", "scope": "request", "unit": unit, "value": value}


def code_identity(repo: Path = Path(".")) -> dict[str, str]:
    return source_identity(
        repo,
        script_names=(
            "run_atlas_coding.py",
            "prepare_atlas_coding.py",
            "prepare_atlas_vertex.py",
            "preflight_atlas_vertex.py",
            "control_atlas_vertex.py",
            "launch_atlas_campaign.py",
        ),
    )
