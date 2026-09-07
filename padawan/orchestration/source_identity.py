"""Exact source-file inventories for frozen experimental executions."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from padawan.models.hashing import file_sha256


def source_identity(repo: Path, *, script_names: Sequence[str] | None = None) -> dict[str, str]:
    paths = sorted((repo / "padawan").rglob("*.py"))
    paths += (
        sorted((repo / "scripts").glob("*.py"))
        if script_names is None
        else [repo / "scripts" / name for name in script_names]
    )
    return {str(path.relative_to(repo)): file_sha256(path) for path in paths}
