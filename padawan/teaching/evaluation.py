from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from statistics import mean


class InterventionCondition(StrEnum):
    FRONTIER_TEACHER_A = "frontier_teacher_a"
    FRONTIER_TEACHER_B = "frontier_teacher_b"
    GENERIC_FEEDBACK = "generic_feedback"
    MINIMAL_REPAIR = "minimal_repair"
    FULL_DEMONSTRATION = "full_demonstration"
    STUDENT_SELF_CRITIQUE = "student_self_critique"
    NO_INTERVENTION = "no_intervention"
    SHUFFLED_WRONG_COMPETENCY = "shuffled_wrong_competency"


@dataclass(frozen=True)
class TeacherOutcomeObservation:
    teacher_id: str
    condition: InterventionCondition
    competency_id: str
    error_class: str
    student_state_id: str
    intervention_mode: str
    revision_gain: float
    transfer_gain: float
    delayed_retention_score: float | None
    token_use: int
    latency_ms: float
    cost_usd: float | None
    harmful: bool


@dataclass(frozen=True)
class TeacherEffectiveness:
    teacher_id: str
    condition: InterventionCondition
    competency_id: str
    error_class: str
    observations: int
    mean_revision_gain: float
    mean_transfer_gain: float
    mean_delayed_retention: float | None
    mean_tokens: float
    mean_latency_ms: float
    total_cost_usd: float | None
    harmful_intervention_rate: float


def balanced_conditions(
    *, seed: int, blocks: int, conditions: tuple[InterventionCondition, ...]
) -> tuple[InterventionCondition, ...]:
    if blocks <= 0:
        raise ValueError("blocks must be positive")
    if not conditions:
        raise ValueError("at least one condition is required")
    rng = random.Random(seed)
    cycle = list(conditions)
    rng.shuffle(cycle)
    return tuple(cycle[index % len(cycle)] for index in range(blocks))


def summarize_teacher_effectiveness(
    observations: Iterable[TeacherOutcomeObservation],
) -> tuple[TeacherEffectiveness, ...]:
    groups: dict[tuple[str, InterventionCondition, str, str], list[TeacherOutcomeObservation]] = {}
    for observation in observations:
        key = (
            observation.teacher_id,
            observation.condition,
            observation.competency_id,
            observation.error_class,
        )
        groups.setdefault(key, []).append(observation)
    reports: list[TeacherEffectiveness] = []
    for (teacher, condition, competency, error_class), rows in sorted(
        groups.items(), key=lambda item: tuple(str(value) for value in item[0])
    ):
        retentions = [
            row.delayed_retention_score for row in rows if row.delayed_retention_score is not None
        ]
        costs = [row.cost_usd for row in rows if row.cost_usd is not None]
        reports.append(
            TeacherEffectiveness(
                teacher_id=teacher,
                condition=condition,
                competency_id=competency,
                error_class=error_class,
                observations=len(rows),
                mean_revision_gain=mean(row.revision_gain for row in rows),
                mean_transfer_gain=mean(row.transfer_gain for row in rows),
                mean_delayed_retention=mean(retentions) if retentions else None,
                mean_tokens=mean(row.token_use for row in rows),
                mean_latency_ms=mean(row.latency_ms for row in rows),
                total_cost_usd=sum(costs) if costs else None,
                harmful_intervention_rate=mean(float(row.harmful) for row in rows),
            )
        )
    return tuple(reports)
