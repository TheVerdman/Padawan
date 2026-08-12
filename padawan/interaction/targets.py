from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from padawan.adapters.base import GenerationRequest, GenerationResult, GenerationStreamEvent
from padawan.interaction.contracts import StudentTargetDescriptor, TargetReadinessRecord


class StreamingStudentClient(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]: ...


DescriptorLoader = Callable[[], Awaitable[StudentTargetDescriptor]]


@dataclass
class ManagedStudentTarget:
    """One model-neutral selectable student deployment with a batch-safe lease."""

    target_id: str
    display_name: str
    provider: str
    client: StreamingStudentClient
    descriptor_loader: DescriptorLoader

    def __post_init__(self) -> None:
        self.lock = asyncio.Lock()
        self._last_descriptor: StudentTargetDescriptor | None = None
        self._last_error: str | None = None
        self._checked_at: datetime | None = None

    async def prepare(self) -> StudentTargetDescriptor:
        descriptor = await self.descriptor_loader()
        if descriptor.target_id != self.target_id:
            raise ValueError("target descriptor ID differs from registry key")
        self._last_descriptor = descriptor
        self._last_error = None
        self._checked_at = datetime.now(UTC)
        return descriptor

    async def readiness(self, *, refresh: bool) -> TargetReadinessRecord:
        if self.lock.locked():
            return TargetReadinessRecord(
                target_id=self.target_id,
                status="busy",
                checked_at=self._checked_at,
                detail="A generation currently owns this target's batch-one lease.",
                serving_path=(
                    self._last_descriptor.serving_path
                    if self._last_descriptor is not None
                    else None
                ),
            )
        if not refresh:
            return TargetReadinessRecord(
                target_id=self.target_id,
                status="ready" if self._last_descriptor is not None else "configured",
                checked_at=self._checked_at,
                detail=(
                    "Last capability preflight succeeded."
                    if self._last_descriptor is not None
                    else "Configured but not probed; no infrastructure action has been taken."
                ),
                serving_path=(
                    self._last_descriptor.serving_path
                    if self._last_descriptor is not None
                    else None
                ),
            )
        try:
            descriptor = await self.prepare()
        except Exception as exc:
            self._last_error = str(exc)
            self._checked_at = datetime.now(UTC)
            return TargetReadinessRecord(
                target_id=self.target_id,
                status="unavailable",
                checked_at=self._checked_at,
                detail=f"Capability preflight failed: {exc}",
            )
        return TargetReadinessRecord(
            target_id=self.target_id,
            status="ready",
            checked_at=self._checked_at,
            detail="Authenticated capability preflight succeeded; no deployment was started.",
            serving_path=descriptor.serving_path,
        )

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationStreamEvent]:
        return self.client.stream(request)

    async def close(self) -> None:
        close = getattr(self.client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result


class StudentTargetRegistry:
    def __init__(self, targets: Iterable[ManagedStudentTarget] = ()) -> None:
        self._targets: dict[str, ManagedStudentTarget] = {}
        for target in targets:
            self.register(target)

    def register(self, target: ManagedStudentTarget) -> None:
        if target.target_id in self._targets:
            raise ValueError(f"duplicate student target: {target.target_id}")
        self._targets[target.target_id] = target

    def get(self, target_id: str) -> ManagedStudentTarget:
        try:
            return self._targets[target_id]
        except KeyError as exc:
            raise KeyError(f"unknown student target: {target_id}") from exc

    def list(self) -> tuple[ManagedStudentTarget, ...]:
        return tuple(self._targets[key] for key in sorted(self._targets))

    async def close(self) -> None:
        for target in self.list():
            await target.close()
