from __future__ import annotations

from padawan.experiments.engine import MatchedBlock, assign_blocks
from padawan.teaching.evaluation import (
    InterventionCondition,
    TeacherOutcomeObservation,
    balanced_conditions,
    summarize_teacher_effectiveness,
)


def test_sibling_assignment_is_seeded_and_counterbalanced() -> None:
    blocks = tuple(MatchedBlock(f"group-{index}", f"a-{index}", f"b-{index}") for index in range(4))
    first = assign_blocks(seed=91, blocks=blocks)
    second = assign_blocks(seed=91, blocks=blocks)
    assert first == second
    orientations = [item.treatment_item_id.startswith("b-") for item in first]
    assert orientations[0] != orientations[1]
    assert orientations[1] != orientations[2]
    assert orientations[2] != orientations[3]


def test_teacher_conditions_are_balanced_and_outcomes_not_preference_scores() -> None:
    conditions = (
        InterventionCondition.FRONTIER_TEACHER_A,
        InterventionCondition.NO_INTERVENTION,
        InterventionCondition.STUDENT_SELF_CRITIQUE,
    )
    assigned = balanced_conditions(seed=4, blocks=6, conditions=conditions)
    assert {condition: assigned.count(condition) for condition in conditions} == {
        condition: 2 for condition in conditions
    }
    rows = [
        TeacherOutcomeObservation(
            teacher_id="teacher-a",
            condition=InterventionCondition.FRONTIER_TEACHER_A,
            competency_id="algebra.linear",
            error_class="sign",
            student_state_id=f"state-{index}",
            intervention_mode="diagnostic_critique",
            revision_gain=gain,
            transfer_gain=transfer,
            delayed_retention_score=retention,
            token_use=tokens,
            latency_ms=latency,
            cost_usd=cost,
            harmful=harmful,
        )
        for index, (gain, transfer, retention, tokens, latency, cost, harmful) in enumerate(
            [
                (0.5, 1.0, 0.8, 100, 10.0, 0.01, False),
                (0.0, -1.0, 0.2, 200, 20.0, 0.02, True),
            ]
        )
    ]
    report = summarize_teacher_effectiveness(rows)[0]
    assert report.observations == 2
    assert report.mean_revision_gain == 0.25
    assert report.mean_transfer_gain == 0.0
    assert report.mean_delayed_retention == 0.5
    assert report.mean_tokens == 150
    assert report.total_cost_usd == 0.03
    assert report.harmful_intervention_rate == 0.5
