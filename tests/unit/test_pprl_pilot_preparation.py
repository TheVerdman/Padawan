"""Offline scientific-design and verifier checks; no model or worker execution."""

import json
from collections import Counter, defaultdict

import pytest

from scripts.prepare_pprl_pilot import PLAN, oracle, prepare, verify_candidate


@pytest.fixture(scope="module")
def prepared():
    return prepare(json.loads(PLAN.read_text()))


def test_samples_have_disjoint_groups_and_independent_verified_answers(prepared):
    public, private, _, summary = prepared
    assert len(public) == len(private) == 14
    assert summary["unique_questions"] == summary["unique_groups"] == 84
    assert summary["independent_oracle_matches"] == 84
    groups = [item["instance_group_id"] for project in private for item in project["items"]]
    assert len(set(groups)) == 84
    for project, retained in zip(public, private, strict=True):
        assert set(project) == {"project", "questions"}
        assert len(project["questions"]) == 6
        counts = Counter(item["competency_id"] for item in retained["items"])
        assert sorted(counts.values()) == [2, 2, 2]
        for slot, item in enumerate(retained["items"]):
            expected = item["expected_answer"]["values"]
            response = json.dumps({"slot": slot, "values": expected}).encode()
            assert verify_candidate(response, project["questions"])["disposition"] == "correct"
            wrong = json.dumps({"slot": slot, "values": ["99"]}).encode()
            assert verify_candidate(wrong, project["questions"])["disposition"] == "incorrect"


def test_preparation_is_byte_reproducible_and_authorizes_nothing(prepared):
    assert prepare(json.loads(PLAN.read_text())) == prepared
    assert prepared[-1]["execution_authorized"] is False
    assert prepared[-1]["parameter_training_ready"] is False


def test_plan_counts_matched_seeds_and_condition_order(prepared):
    _, _, schedule, summary = prepared
    plan = json.loads(PLAN.read_text())
    assert len(schedule) == 104 <= 128
    assert summary["main_rollouts"] == 96
    assert summary["model_call_ceiling"] == 1168
    blocks = defaultdict(list)
    for entry in schedule:
        blocks[(entry["project"], entry["replicate"])].append(entry)
    assert len(blocks) == 26
    positions = defaultdict(Counter)
    seeds_by_project = defaultdict(set)
    for (project, _), block in blocks.items():
        assert {entry["condition"] for entry in block} == set(plan["conditions"])
        assert len({tuple(entry["call_seeds"]) for entry in block}) == 1
        seeds_by_project[project].add(tuple(block[0]["call_seeds"]))
        if project.startswith("main-"):
            for position, entry in enumerate(block):
                positions[entry["condition"]][position] += 1
    assert all(set(counts.values()) == {6} and len(counts) == 4 for counts in positions.values())
    assert all(
        len(seeds) == 2 for name, seeds in seeds_by_project.items() if name.startswith("main-")
    )
    calls = summary["model_call_ceiling"]
    limits = plan["workload"]
    assert calls * limits["max_input_tokens"] == limits["maximum_input_tokens"]
    assert calls * limits["max_output_tokens"] == limits["maximum_output_tokens"]
    assert calls + len(schedule) == limits["maximum_actions"]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"slot":true,"values":["2"]}',
        b'{"slot":0,"slot":1,"values":["2"]}',
        b'{"slot":0,"values":[2]}',
        b'{"slot":0,"values":["2.0"]}',
        b'{"slot":0,"values":["2/1"]}',
        b'{"slot":0,"values":["2","2"]}',
        b'{"slot":0,"values":["101"]}',
        b'{"slot":0,"values":["__import__(x)"]}',
        b'{"slot":0,"values":["2"],"analysis":"private marker"}',
        b'{"slot":0,"values":[]}',
        b'{"slot":6,"values":["2"]}',
        b'{"slot":0,"values":[NaN]}',
        b'```json\n{"slot":0,"values":["2"]}\n```',
        b"\xff",
        b" " * 4097,
        b"[" * 2000,
    ],
)
def test_model_input_has_no_expression_evaluation_or_error_echo(raw):
    assert verify_candidate(raw, ["Solve over the real numbers: 2*x + (0) = 0*x + (4)"]) == {
        "disposition": "malformed"
    }


def test_missing_branch_and_extraneous_root_fail():
    questions = [
        "Solve over the real numbers: (x - (1))**2 = 9",
        "Solve over the real numbers: sqrt(x + (6)) = x - (0)",
    ]
    assert oracle(questions[0]) == (-2, 4)
    assert oracle(questions[1]) == (3,)
    assert verify_candidate(b'{"slot":0,"values":["4"]}', questions)["disposition"] == "incorrect"
    assert (
        verify_candidate(b'{"slot":1,"values":["-2","3"]}', questions)["disposition"] == "incorrect"
    )
    assert (
        verify_candidate(b'{"slot":0,"values":["4","-2"]}', questions)["disposition"] == "correct"
    )


def test_unrecognized_trusted_item_is_infrastructure_error():
    with pytest.raises(ValueError, match="outside the frozen"):
        verify_candidate(b'{"slot":0,"values":["2"]}', ["unknown generator syntax"])
