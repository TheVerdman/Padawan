from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    ExperimentBlockRow,
    ExperimentRow,
    ResearchExecutionRow,
    StudentStateRow,
)
from padawan.state.store import StateStore


@dataclass(frozen=True)
class MatchedBlock:
    instance_group_id: str
    item_a_id: str
    item_b_id: str

    def __post_init__(self) -> None:
        if self.item_a_id == self.item_b_id:
            raise ValueError("matched block needs two different sibling items")


@dataclass(frozen=True)
class BlockAssignment:
    block_id: str
    block_index: int
    instance_group_id: str
    treatment_item_id: str
    control_item_id: str


@dataclass(frozen=True)
class ExperimentReport:
    experiment_id: str
    total_blocks: int
    analyzed_blocks: int
    excluded_contaminated: int
    excluded_infrastructure: int
    treatment_success_rate: float | None
    control_success_rate: float | None
    paired_gain: float | None
    mcnemar_b: int
    mcnemar_c: int
    mcnemar_exact_p: float | None
    bootstrap_95_ci: tuple[float, float] | None
    floor_detected: bool
    ceiling_detected: bool


class ExperimentEngine:
    def __init__(self, state_store: StateStore) -> None:
        self.state_store = state_store

    async def create(
        self,
        session: AsyncSession,
        *,
        parent_state_id: str,
        seed: int,
        blocks: tuple[MatchedBlock, ...],
        treatment_condition: str,
        control_condition: str,
        experiment_id: str | None = None,
        research_execution_digest: str | None = None,
    ) -> tuple[str, tuple[BlockAssignment, ...]]:
        if not blocks:
            raise ValueError("experiment needs matched blocks")
        assigned_id = experiment_id or f"experiment-{uuid4()}"
        existing = await session.get(ExperimentRow, assigned_id)
        if existing is not None:
            if existing.research_execution_digest != research_execution_digest:
                raise ValueError("experiment replay has different research controls")
            if (
                existing.parent_state_id != parent_state_id
                or existing.seed != seed
                or existing.design.get("treatment_condition") != treatment_condition
                or existing.design.get("control_condition") != control_condition
            ):
                raise ValueError("experiment replay has a different design")
            rows = (
                await session.scalars(
                    select(ExperimentBlockRow)
                    .where(ExperimentBlockRow.experiment_id == assigned_id)
                    .order_by(ExperimentBlockRow.block_index)
                )
            ).all()
            stored_assignments = tuple(_row_assignment(row) for row in rows)
            if stored_assignments != assign_blocks(seed=seed, blocks=blocks):
                raise ValueError("experiment replay has different matched blocks")
            return assigned_id, stored_assignments
        if research_execution_digest is not None:
            await _validate_research_execution(
                session,
                execution_digest=research_execution_digest,
                parent_state_id=parent_state_id,
                seed=seed,
            )
        session.add(
            ExperimentRow(
                experiment_id=assigned_id,
                parent_state_id=parent_state_id,
                research_execution_digest=research_execution_digest,
                seed=seed,
                design={
                    "kind": "state_forked_matched_blocks",
                    "treatment_condition": treatment_condition,
                    "control_condition": control_condition,
                    "counterbalanced": True,
                    "research_execution_digest": research_execution_digest,
                },
                status="active",
                created_at=datetime.now(UTC),
                completed_at=None,
            )
        )
        await session.flush()
        fork = await self.state_store.fork(
            session,
            parent_state_id=parent_state_id,
            experiment_id=assigned_id,
            intervention={
                "treatment_condition": treatment_condition,
                "control_condition": control_condition,
            },
            fork_id=f"fork-{assigned_id}",
        )
        assignments = assign_blocks(seed=seed, blocks=blocks)
        for assignment in assignments:
            session.add(
                ExperimentBlockRow(
                    block_id=assignment.block_id,
                    experiment_id=assigned_id,
                    block_index=assignment.block_index,
                    assignment={
                        "instance_group_id": assignment.instance_group_id,
                        "treatment_item_id": assignment.treatment_item_id,
                        "control_item_id": assignment.control_item_id,
                        "treatment_state_id": fork.treatment_state_id,
                        "control_state_id": fork.control_state_id,
                        "treatment_branch_id": fork.treatment_branch_id,
                        "control_branch_id": fork.control_branch_id,
                    },
                    outcomes=None,
                    contamination_detected=False,
                    infrastructure_failure=False,
                )
            )
        await session.flush()
        return assigned_id, assignments

    async def record_block(
        self,
        session: AsyncSession,
        *,
        block_id: str,
        treatment_success: bool | None,
        control_success: bool | None,
        treatment_score: float | None,
        control_score: float | None,
        contamination_checks: dict[str, bool],
        infrastructure_failures: tuple[str, ...] = (),
    ) -> None:
        row = await session.scalar(
            select(ExperimentBlockRow)
            .where(ExperimentBlockRow.block_id == block_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(block_id)
        contamination = not all(contamination_checks.values())
        infrastructure = bool(infrastructure_failures)
        if row.outcomes is not None:
            expected = {
                "treatment_success": treatment_success,
                "control_success": control_success,
                "treatment_score": treatment_score,
                "control_score": control_score,
                "contamination_checks": contamination_checks,
                "infrastructure_failures": list(infrastructure_failures),
            }
            if row.outcomes != expected:
                raise ValueError("block outcome replay conflicts with persisted outcome")
            return
        row.outcomes = {
            "treatment_success": treatment_success,
            "control_success": control_success,
            "treatment_score": treatment_score,
            "control_score": control_score,
            "contamination_checks": contamination_checks,
            "infrastructure_failures": list(infrastructure_failures),
        }
        row.contamination_detected = contamination
        row.infrastructure_failure = infrastructure
        await session.flush()

    async def analyze(self, session: AsyncSession, *, experiment_id: str) -> ExperimentReport:
        experiment = await session.get(ExperimentRow, experiment_id)
        if experiment is None:
            raise KeyError(experiment_id)
        rows = (
            await session.scalars(
                select(ExperimentBlockRow)
                .where(ExperimentBlockRow.experiment_id == experiment_id)
                .order_by(ExperimentBlockRow.block_index)
            )
        ).all()
        contaminated = sum(row.contamination_detected for row in rows)
        infrastructure = sum(row.infrastructure_failure for row in rows)
        analyzed = [
            row
            for row in rows
            if row.outcomes is not None
            and not row.contamination_detected
            and not row.infrastructure_failure
            and row.outcomes.get("treatment_success") is not None
            and row.outcomes.get("control_success") is not None
        ]
        if not analyzed:
            return ExperimentReport(
                experiment_id=experiment_id,
                total_blocks=len(rows),
                analyzed_blocks=0,
                excluded_contaminated=contaminated,
                excluded_infrastructure=infrastructure,
                treatment_success_rate=None,
                control_success_rate=None,
                paired_gain=None,
                mcnemar_b=0,
                mcnemar_c=0,
                mcnemar_exact_p=None,
                bootstrap_95_ci=None,
                floor_detected=False,
                ceiling_detected=False,
            )
        pairs = [
            (
                bool(cast(dict[str, Any], row.outcomes)["treatment_success"]),
                bool(cast(dict[str, Any], row.outcomes)["control_success"]),
            )
            for row in analyzed
        ]
        treatment_rate = mean(float(treatment) for treatment, _ in pairs)
        control_rate = mean(float(control) for _, control in pairs)
        differences = [float(treatment) - float(control) for treatment, control in pairs]
        b = sum(treatment and not control for treatment, control in pairs)
        c = sum(control and not treatment for treatment, control in pairs)
        p_value = _mcnemar_exact(b, c)
        interval = _bootstrap_interval(differences, seed=experiment.seed)
        return ExperimentReport(
            experiment_id=experiment_id,
            total_blocks=len(rows),
            analyzed_blocks=len(analyzed),
            excluded_contaminated=contaminated,
            excluded_infrastructure=infrastructure,
            treatment_success_rate=treatment_rate,
            control_success_rate=control_rate,
            paired_gain=mean(differences),
            mcnemar_b=b,
            mcnemar_c=c,
            mcnemar_exact_p=p_value,
            bootstrap_95_ci=interval,
            floor_detected=control_rate <= 0.1 and treatment_rate <= 0.1,
            ceiling_detected=control_rate >= 0.9 and treatment_rate >= 0.9,
        )


def assign_blocks(*, seed: int, blocks: tuple[MatchedBlock, ...]) -> tuple[BlockAssignment, ...]:
    rng = random.Random(seed)
    assignments: list[BlockAssignment] = []
    initial_reverse = bool(rng.getrandbits(1))
    for index, block in enumerate(blocks):
        # Alternation supplies counterbalance; seeded random orientation prevents a
        # fixed first-item bias.
        reverse = initial_reverse ^ bool(index % 2)
        treatment, control = (
            (block.item_b_id, block.item_a_id) if reverse else (block.item_a_id, block.item_b_id)
        )
        block_digest = sha256_digest(
            {"seed": seed, "index": index, "group": block.instance_group_id}
        )
        assignments.append(
            BlockAssignment(
                block_id=f"block-{block_digest[7:31]}",
                block_index=index,
                instance_group_id=block.instance_group_id,
                treatment_item_id=treatment,
                control_item_id=control,
            )
        )
    return tuple(assignments)


def _mcnemar_exact(b: int, c: int) -> float | None:
    discordant = b + c
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, k) for k in range(0, min(b, c) + 1)) / (2**discordant)
    return float(min(1.0, 2.0 * tail))


def _bootstrap_interval(
    differences: list[float], *, seed: int, samples: int = 10_000
) -> tuple[float, float]:
    rng = random.Random(seed ^ 0x5041444157414E)
    means = sorted(mean(rng.choice(differences) for _ in differences) for _ in range(samples))
    lower = means[int(0.025 * (samples - 1))]
    upper = means[int(0.975 * (samples - 1))]
    return lower, upper


def _row_assignment(row: ExperimentBlockRow) -> BlockAssignment:
    return BlockAssignment(
        block_id=row.block_id,
        block_index=row.block_index,
        instance_group_id=str(row.assignment["instance_group_id"]),
        treatment_item_id=str(row.assignment["treatment_item_id"]),
        control_item_id=str(row.assignment["control_item_id"]),
    )


async def _validate_research_execution(
    session: AsyncSession,
    *,
    execution_digest: str,
    parent_state_id: str,
    seed: int,
) -> None:
    execution = await session.get(ResearchExecutionRow, execution_digest)
    if execution is None:
        raise ValueError("experiment cites an unregistered research execution")
    state = await session.get(StudentStateRow, parent_state_id)
    if state is None:
        raise ValueError("experiment parent state is missing")
    if execution.seed != seed:
        raise ValueError("experiment seed differs from its research execution")
    if execution.checkpoint_id != state.checkpoint_id:
        raise ValueError("experiment checkpoint differs from its research execution")
    if execution.runtime_id != state.runtime_id:
        raise ValueError("experiment runtime differs from its research execution")
    if execution.research_role != state.research_role:
        raise ValueError("experiment role differs from its research execution")
