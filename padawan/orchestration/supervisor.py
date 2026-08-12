from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from padawan.models.contracts import RunState
from padawan.models.database import Database
from padawan.orchestration.state_machine import ClaimedRun, RunStore

if TYPE_CHECKING:
    from padawan.experiments.controls import (
        ResearchControlRegistry,
        ResearchWorkerConfiguration,
    )

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WorkResult:
    to_state: RunState
    payload_updates: dict[str, object]
    details: dict[str, object]


class WorkHandler(Protocol):
    async def handle(self, run: ClaimedRun) -> WorkResult: ...


@runtime_checkable
class FailureRecorder(Protocol):
    async def record_failure(
        self, run: ClaimedRun, error: Exception, terminal_state: RunState
    ) -> None: ...


class AutonomousSupervisor:
    """Budgeted worker loop with heartbeats, stale recovery, pause support, and clean shutdown."""

    def __init__(
        self,
        *,
        database: Database,
        runs: RunStore,
        handler: WorkHandler,
        worker_id: str,
        lease_for: timedelta = timedelta(minutes=5),
        idle_poll_seconds: float = 1.0,
        research_worker: ResearchWorkerConfiguration | None = None,
        research_controls: ResearchControlRegistry | None = None,
    ) -> None:
        self.database = database
        self.runs = runs
        self.handler = handler
        self.worker_id = worker_id
        self.lease_for = lease_for
        self.idle_poll_seconds = idle_poll_seconds
        self.research_worker = research_worker
        if research_controls is None:
            # Import lazily: research contracts also depend on orchestration domain types.
            from padawan.experiments.controls import ResearchControlRegistry

            research_controls = ResearchControlRegistry()
        self.research_controls = research_controls
        self._stop = asyncio.Event()

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self, *, budget: int | None = None) -> int:
        self._install_signal_handlers()
        completed_actions = 0
        while not self._stop.is_set() and (budget is None or completed_actions < budget):
            async with self.database.transaction() as session:
                await self.runs.recover_stale_workers(
                    session, stale_before=datetime.now(UTC) - self.lease_for * 2
                )
                eligible_controls = (
                    await self.research_controls.compatible_execution_digests(
                        session,
                        worker=self.research_worker,
                    )
                    if self.research_worker is not None
                    else ()
                )
                continuation_controls = (
                    await self.research_controls.compatible_execution_digests(
                        session,
                        worker=self.research_worker,
                        require_current_corpus=False,
                    )
                    if self.research_worker is not None
                    else ()
                )
                claimed = await self.runs.claim_next(
                    session,
                    worker_id=self.worker_id,
                    lease_for=self.lease_for,
                    eligible_research_execution_digests=eligible_controls,
                    continuation_research_execution_digests=continuation_controls,
                )
                await self.runs.heartbeat_worker(
                    session,
                    worker_id=self.worker_id,
                    current_run_id=claimed.run_id if claimed else None,
                    capabilities={
                        "runner": "padawan-v1",
                        "controlled_execution_admissions": len(eligible_controls),
                        "controlled_continuation_admissions": len(continuation_controls),
                    },
                )
            if claimed is None:
                await asyncio.sleep(self.idle_poll_seconds)
                if budget is not None:
                    break
                continue
            if claimed.state == RunState.FAILED_RETRYABLE:
                async with self.database.transaction() as session:
                    await self.runs.resume_retry(
                        session,
                        run_id=claimed.run_id,
                        lease_token=claimed.lease_token,
                        actor=self.worker_id,
                    )
                continue
            try:
                result = await self.handler.handle(claimed)
                async with self.database.transaction() as session:
                    await self.runs.transition(
                        session,
                        run_id=claimed.run_id,
                        lease_token=claimed.lease_token,
                        actor=self.worker_id,
                        to_state=result.to_state,
                        payload_updates=result.payload_updates,
                        details=result.details,
                    )
                completed_actions += 1
            except Exception as exc:
                retryable = bool(getattr(exc, "retryable", False))
                infrastructure = exc.__class__.__name__.endswith(("Error", "Timeout"))
                async with self.database.transaction() as session:
                    target = await self.runs.fail(
                        session,
                        run_id=claimed.run_id,
                        lease_token=claimed.lease_token,
                        actor=self.worker_id,
                        error_class=exc.__class__.__name__,
                        message=str(exc),
                        retryable=retryable,
                        infrastructure=infrastructure,
                    )
                if target == RunState.FAILED_TERMINAL and isinstance(self.handler, FailureRecorder):
                    await self.handler.record_failure(claimed, exc, target)
                await log.aerror(
                    "run_action_failed",
                    run_id=claimed.run_id,
                    from_state=claimed.state.value,
                    to_state=target.value,
                    error_class=exc.__class__.__name__,
                )
        return completed_actions

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, self.request_stop)
