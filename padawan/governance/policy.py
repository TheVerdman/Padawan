from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import PurePosixPath

from padawan.models.contracts import ArtifactRef


class ArtifactClass(StrEnum):
    EXPORTABLE = "exportable"
    RESTRICTED = "restricted"
    PRIVATE_REASONING = "private_reasoning"


@dataclass(frozen=True)
class AccessContext:
    principal: str
    roles: frozenset[str]
    purpose: str


@dataclass(frozen=True)
class ExportDecision:
    allowed: bool
    reason: str
    classification: ArtifactClass


@dataclass(frozen=True)
class RetentionPolicy:
    raw_data_age: timedelta | None
    normalized_records_age: timedelta | None = None
    delete_only_when_unreferenced: bool = True

    def __post_init__(self) -> None:
        if self.raw_data_age is not None and self.raw_data_age <= timedelta(0):
            raise ValueError("raw-data retention must be positive")
        if self.normalized_records_age is not None:
            raise ValueError("normalized research records are immutable and not auto-deleted")
        if not self.delete_only_when_unreferenced:
            raise ValueError("referenced artifacts may never be removed by retention policy")


class ExportPolicy:
    """Single authority for restricted-artifact and private-reasoning disclosure."""

    def __init__(
        self,
        *,
        allow_restricted: bool = False,
        allow_private_reasoning: bool = False,
        maximum_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        if maximum_bytes <= 0:
            raise ValueError("maximum export size must be positive")
        self.allow_restricted = allow_restricted
        self.allow_private_reasoning = allow_private_reasoning
        self.maximum_bytes = maximum_bytes

    def decide(
        self,
        reference: ArtifactRef,
        *,
        context: AccessContext,
        contains_private_reasoning: bool = False,
    ) -> ExportDecision:
        classification = _classification(reference, contains_private_reasoning)
        if reference.size_bytes > self.maximum_bytes:
            return ExportDecision(False, "artifact exceeds export size limit", classification)
        if contains_private_reasoning and not (
            self.allow_private_reasoning and "private-reasoning-export" in context.roles
        ):
            return ExportDecision(
                False, "private reasoning export is not authorized", classification
            )
        if reference.restricted and not (
            self.allow_restricted and "restricted-artifact-export" in context.roles
        ):
            return ExportDecision(
                False, "restricted artifact export is not authorized", classification
            )
        if context.purpose not in {"audit", "research", "incident-response"}:
            return ExportDecision(False, "export purpose is not permitted", classification)
        return ExportDecision(True, "policy permits export", classification)

    @staticmethod
    def safe_relative_name(value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("unsafe export path")
        if any(re.fullmatch(r"[A-Za-z0-9._-]+", part) is None for part in path.parts):
            raise ValueError("export path contains unsupported characters")
        return path.as_posix()


def _classification(reference: ArtifactRef, contains_private_reasoning: bool) -> ArtifactClass:
    if contains_private_reasoning:
        return ArtifactClass.PRIVATE_REASONING
    if reference.restricted or reference.raw_data:
        return ArtifactClass.RESTRICTED
    return ArtifactClass.EXPORTABLE
