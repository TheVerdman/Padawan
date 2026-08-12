from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from padawan.experiments.controls import (
    ResearchControlConfiguration,
    ResearchControlRegistry,
    parent_state_identity,
)
from padawan.models.contracts import ResearchRole, RunState, TeacherMode
from padawan.models.database import Database
from padawan.models.research_contracts import BudgetDisposition
from padawan.models.tables import RunRow, StudentRow, StudentStateRow
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor

_TERMINAL = {
    RunState.COMPLETE.value,
    RunState.FAILED_TERMINAL.value,
    RunState.REVIEW_REQUIRED.value,
}
_TREATMENT_CONDITION = "frontier_teacher_critique"
_CONTROL_CONDITION = "no_intervention"
_POOL = "curriculum"


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
        research_controls: ResearchControlRegistry | None = None,
        control_configuration: ResearchControlConfiguration | None = None,
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
        self.research_controls = research_controls
        self.control_configuration = control_configuration
        if (research_controls is None) != (control_configuration is None):
            raise ValueError(
                "research control registry and configuration must be supplied together"
            )

    async def run(
        self,
        *,
        episode_budget: int,
        experiment_seed: int,
        teacher_mode: TeacherMode = TeacherMode.DIAGNOSTIC_CRITIQUE,
    ) -> ResearchLoopResult:
        if episode_budget <= 0:
            raise ValueError("episode budget must be positive")
        self._validate_control_invocation(teacher_mode=teacher_mode)
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
                if self.research_controls is not None and self.control_configuration is not None:
                    self._validate_active_run_payload(
                        existing.payload,
                        seed=seed,
                        teacher_mode=teacher_mode,
                    )
                    if existing.research_execution_digest is not None:
                        await self.research_controls.validate_execution_configuration(
                            session,
                            execution_digest=existing.research_execution_digest,
                            configuration=self.control_configuration,
                            seed=seed,
                        )
                return existing.run_id
            student = await session.get(StudentRow, self.student_id)
            if student is None or student.canonical_state_id is None:
                raise ValueError(f"student has no canonical state: {self.student_id}")
            if student.research_role != self.research_role.value:
                raise ValueError("student identity has a different research role")
            parent_state = await session.get(StudentStateRow, student.canonical_state_id)
            if parent_state is None:
                raise ValueError("student canonical state is missing")
            run_id = f"run-{uuid4()}"
            execution_digest = None
            if self.research_controls is not None and self.control_configuration is not None:
                await self.research_controls.register_profile(
                    session, self.control_configuration.profile
                )
                execution = self.control_configuration.execution_manifest(
                    execution_id=f"execution-{run_id}",
                    seed=seed,
                    created_at=datetime.now(UTC),
                    parent_state=parent_state_identity(parent_state),
                )
                execution_row = await self.research_controls.register_execution(
                    session,
                    execution,
                    parent_state_id=student.canonical_state_id,
                )
                execution_digest = execution_row.execution_digest
            return await self.runs.create(
                session,
                run_id=run_id,
                payload={
                    "student_id": self.student_id,
                    "domain_id": self.domain_id,
                    "research_role": self.research_role.value,
                    "state_id": student.canonical_state_id,
                    "pool": _POOL,
                    "experiment_seed": seed,
                    "teacher_mode": teacher_mode.value,
                    "treatment_condition": _TREATMENT_CONDITION,
                    "control_condition": _CONTROL_CONDITION,
                },
                retry_budget=self.retry_budget,
                research_execution_digest=execution_digest,
            )

    async def _run_row(self, run_id: str) -> RunRow:
        async with self.database.transaction() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                raise KeyError(run_id)
            return row

    def _validate_control_invocation(self, *, teacher_mode: TeacherMode) -> None:
        if self.control_configuration is None:
            return
        expected = {
            "control_condition": _CONTROL_CONDITION,
            "domain_id": self.domain_id,
            "pool": _POOL,
            "teacher_mode": teacher_mode.value,
            "treatment_condition": _TREATMENT_CONDITION,
            "workflow": "padawan.developmental_episode",
        }
        parameters = self.control_configuration.harness_parameters
        mismatches = [key for key, value in expected.items() if parameters.get(key) != value]
        budgets = self.control_configuration.profile.budgets
        if (
            budgets.actions.disposition != BudgetDisposition.CAPPED
            or budgets.actions.scope != "run"
            or budgets.actions.unit != "actions"
            or budgets.actions.value != 256
        ):
            mismatches.append("action_budget")
        if (
            budgets.retries.disposition != BudgetDisposition.CAPPED
            or budgets.retries.scope != "run"
            or budgets.retries.unit != "retries"
            or budgets.retries.value != self.retry_budget
        ):
            mismatches.append("run_retry_budget")
        if mismatches:
            raise ValueError(
                "research controls differ from the live workflow: " + ", ".join(mismatches)
            )

    def _validate_active_run_payload(
        self,
        payload: dict[str, Any],
        *,
        seed: int,
        teacher_mode: TeacherMode,
    ) -> None:
        expected: dict[str, Any] = {
            "control_condition": _CONTROL_CONDITION,
            "domain_id": self.domain_id,
            "experiment_seed": seed,
            "pool": _POOL,
            "teacher_mode": teacher_mode.value,
            "treatment_condition": _TREATMENT_CONDITION,
        }
        mismatches = [key for key, value in expected.items() if payload.get(key) != value]
        if mismatches:
            raise ValueError(
                "active run parameters differ from this invocation: " + ", ".join(mismatches)
            )
