from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from padawan.models.contracts import ResearchRole, RunState, TeacherMode
from padawan.models.database import Database
from padawan.models.tables import RunRow, StudentRow
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor

_TERMINAL = {
    RunState.COMPLETE.value,
    RunState.FAILED_TERMINAL.value,
    RunState.REVIEW_REQUIRED.value,
}


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    state: str
    episode_id: str | None
    last_error: dict[str, Any] | None


@dataclass(frozen=True)
class ResearchLoopResult:
    requested_episode_budget: int
    closed_episodes: int
    runs: tuple[RunOutcome, ...]
    stop_reason: str


class AutonomousResearchLoop:
    """Selects/resumes work and lets the durable worker execute each action."""

    def __init__(
        self,
        *,
        database: Database,
        runs: RunStore,
        supervisor: AutonomousSupervisor,
        student_id: str,
        research_role: ResearchRole = ResearchRole.TARGET,
        domain_id: str = "math.algebra",
        retry_budget: int = 3,
    ) -> None:
        if retry_budget < 0:
            raise ValueError("retry budget cannot be negative")
        self.database = database
        self.runs = runs
        self.supervisor = supervisor
        self.student_id = student_id
        self.research_role = research_role
        self.domain_id = domain_id
        self.retry_budget = retry_budget

    async def run(
        self,
        *,
        episode_budget: int,
        experiment_seed: int,
        teacher_mode: TeacherMode = TeacherMode.DIAGNOSTIC_CRITIQUE,
    ) -> ResearchLoopResult:
        if episode_budget <= 0:
            raise ValueError("episode budget must be positive")
        outcomes: list[RunOutcome] = []
        stop_reason = "episode_budget_exhausted"
        for index in range(episode_budget):
            run_id = await self._resume_or_create(
                seed=experiment_seed + index,
                teacher_mode=teacher_mode,
            )
            # A domain episode uses a bounded durable transition path. The larger action budget
            # allows bounded retries without turning this into a monolithic script.
            await self.supervisor.run(budget=256)
            row = await self._run_row(run_id)
            outcomes.append(
                RunOutcome(
                    run_id=row.run_id,
                    state=row.state,
                    episode_id=row.episode_id or row.payload.get("episode_id"),
                    last_error=dict(row.last_error) if row.last_error else None,
                )
            )
            if row.state == RunState.COMPLETE.value:
                continue
            if row.state == RunState.REVIEW_REQUIRED.value:
                stop_reason = "governance_review_required"
            elif row.last_error and row.last_error.get("infrastructure"):
                stop_reason = "terminal_infrastructure_problem"
            elif row.last_error and row.last_error.get("error_class") == "InventoryExhaustedError":
                stop_reason = "no_valid_inventory"
            else:
                stop_reason = "terminal_run_failure"
            break
        return ResearchLoopResult(
            requested_episode_budget=episode_budget,
            closed_episodes=len(outcomes),
            runs=tuple(outcomes),
            stop_reason=stop_reason,
        )

    async def _resume_or_create(self, *, seed: int, teacher_mode: TeacherMode) -> str:
        async with self.database.transaction() as session:
            existing = await session.scalar(
                select(RunRow)
                .where(
                    RunRow.state.not_in(_TERMINAL),
                    RunRow.active_student_id == self.student_id,
                )
                .order_by(RunRow.created_at)
                .limit(1)
            )
            if existing is not None:
                if existing.research_role != self.research_role.value:
                    raise ValueError("active run has a different research role")
                return existing.run_id
            student = await session.get(StudentRow, self.student_id)
            if student is None or student.canonical_state_id is None:
                raise ValueError(f"student has no canonical state: {self.student_id}")
            if student.research_role != self.research_role.value:
                raise ValueError("student identity has a different research role")
            return await self.runs.create(
                session,
                payload={
                    "student_id": self.student_id,
                    "domain_id": self.domain_id,
                    "research_role": self.research_role.value,
                    "state_id": student.canonical_state_id,
                    "pool": "curriculum",
                    "experiment_seed": seed,
                    "teacher_mode": teacher_mode.value,
                    "treatment_condition": "frontier_teacher_critique",
                    "control_condition": "no_intervention",
                },
                retry_budget=self.retry_budget,
            )

    async def _run_row(self, run_id: str) -> RunRow:
        async with self.database.transaction() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            return row
