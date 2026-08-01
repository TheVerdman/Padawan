from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.grading.algebra import AlgebraGrader
from padawan.models.contracts import CorpusPool, GradeOutcome
from tests.helpers import public_derivation


@pytest.mark.parametrize("family", list(AlgebraFamily))
def test_generator_produces_unique_matched_siblings(family: AlgebraFamily) -> None:
    items = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=99,
        groups_per_family=2,
        siblings_per_group=3,
        families=(family,),
    )
    assert len(items) == 6
    for group in {item.instance_group_id for item in items}:
        siblings = [item for item in items if item.instance_group_id == group]
        assert len(siblings) == 3
        assert len({json.dumps(item.expected_answer, sort_keys=True) for item in siblings}) == 3
        assert all(0 <= item.difficulty <= 1 for item in siblings)
        assert all(item.verifier_spec.verifier_type == "symbolic_algebra" for item in siblings)


def test_sympy_grader_accepts_verified_answer_and_rejects_bad_process() -> None:
    expected = {
        "kind": "solution_set",
        "variable": "x",
        "values": ["2"],
        "exclusions": [],
    }
    grader = AlgebraGrader()
    correct = grader.grade(
        attempt_id="a1",
        response=public_derivation(expected, correct=True),
        expected_answer=expected,
    )
    assert correct.outcome == GradeOutcome.CORRECT
    bad_step = {
        "steps": [
            {
                "step_id": "s1",
                "before": "x = 2",
                "operation": "unsupported change",
                "after": "x = 3",
                "assumptions": [],
            }
        ],
        "final_answer": expected,
    }
    invalid = grader.grade(attempt_id="a2", response=bad_step, expected_answer=expected)
    assert invalid.outcome == GradeOutcome.INVALID_PROCESS
    assert invalid.first_invalid_step_id == "s1"


def test_malformed_output_is_student_failure_not_infrastructure_failure() -> None:
    grade = AlgebraGrader().grade(
        attempt_id="a",
        response="not JSON",
        expected_answer={"kind": "solution_set", "values": ["1"], "exclusions": []},
    )
    assert grade.outcome == GradeOutcome.MALFORMED
    assert grade.student_failure
    assert not grade.infrastructure_failure


def test_incompatible_schema_version_is_rejected() -> None:
    item = AlgebraCorpusGenerator().generate(
        pool=CorpusPool.CURRICULUM,
        seed=1,
        groups_per_family=1,
        siblings_per_group=2,
        families=(AlgebraFamily.DISTRIBUTION_SIGN,),
    )[0]
    payload = item.model_dump(mode="json")
    payload["schema_version"] = "2.0.0"
    with pytest.raises(ValidationError):
        type(item).model_validate(payload)
