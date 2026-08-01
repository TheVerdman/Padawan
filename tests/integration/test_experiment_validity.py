from __future__ import annotations

from padawan.experiments.engine import ExperimentEngine, MatchedBlock
from padawan.state.store import StateStore


async def test_contamination_and_infrastructure_attrition_are_excluded(database) -> None:
    states = StateStore()
    engine = ExperimentEngine(states)
    async with database.transaction() as session:
        parent = await states.create_student(
            session,
            student_id="student-experiment",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
        experiment_id, assignments = await engine.create(
            session,
            parent_state_id=parent.state_id,
            seed=7,
            blocks=(
                MatchedBlock("g0", "a0", "b0"),
                MatchedBlock("g1", "a1", "b1"),
                MatchedBlock("g2", "a2", "b2"),
                MatchedBlock("g3", "a3", "b3"),
                MatchedBlock("g4", "a4", "b4"),
            ),
            treatment_condition="teacher",
            control_condition="none",
        )
        await engine.record_block(
            session,
            block_id=assignments[0].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"matched": True, "separate_branches": True},
        )
        await engine.record_block(
            session,
            block_id=assignments[1].block_id,
            treatment_success=True,
            control_success=True,
            treatment_score=1.0,
            control_score=1.0,
            contamination_checks={"matched": True, "separate_branches": True},
        )
        await engine.record_block(
            session,
            block_id=assignments[2].block_id,
            treatment_success=False,
            control_success=True,
            treatment_score=0.0,
            control_score=1.0,
            contamination_checks={"matched": True, "separate_branches": True},
        )
        await engine.record_block(
            session,
            block_id=assignments[3].block_id,
            treatment_success=True,
            control_success=False,
            treatment_score=1.0,
            control_score=0.0,
            contamination_checks={"matched": False, "separate_branches": True},
        )
        await engine.record_block(
            session,
            block_id=assignments[4].block_id,
            treatment_success=None,
            control_success=None,
            treatment_score=None,
            control_score=None,
            contamination_checks={"matched": True, "separate_branches": True},
            infrastructure_failures=("teacher_timeout",),
        )
        report = await engine.analyze(session, experiment_id=experiment_id)
    assert report.total_blocks == 5
    assert report.analyzed_blocks == 3
    assert report.excluded_contaminated == 1
    assert report.excluded_infrastructure == 1
    assert report.treatment_success_rate == 2 / 3
    assert report.control_success_rate == 2 / 3
    assert report.paired_gain == 0
    assert report.mcnemar_b == 1
    assert report.mcnemar_c == 1
    assert report.mcnemar_exact_p == 1.0
    assert report.bootstrap_95_ci is not None
