"""Prepare an inert local pilot sample/schedule and independent integer verifier.

No adapter, worker, listener, model call or task authorization is constructed.
Generated answers and schedule are researcher-only; only public project questions are worker data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "configs/pprl/nemotron-local-pilot-v1.json"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def oracle(prompt):
    """Independently solve only the three trusted generator grammars, without SymPy/eval."""
    equation = prompt.split("\n", 1)[0].removeprefix("Solve over the real numbers: ")
    match = re.fullmatch(r"(-?\d+)\*x \+ \((-?\d+)\) = (-?\d+)\*x \+ \((-?\d+)\)", equation)
    if match:
        a, b, c, d = map(int, match.groups())
        if a == c or (d - b) % (a - c):
            raise ValueError("unsupported nonintegral linear item")
        return ((d - b) // (a - c),)
    match = re.fullmatch(r"\(x - \((-?\d+)\)\)\*\*2 = (\d+)", equation)
    if match:
        center, squared = map(int, match.groups())
        distance = math.isqrt(squared)
        if distance**2 != squared or distance == 0:
            raise ValueError("unsupported branch item")
        return (center - distance, center + distance)
    match = re.fullmatch(r"sqrt\(x \+ \((-?\d+)\)\) = x - \((-?\d+)\)", equation)
    if match:
        constant, shift = map(int, match.groups())
        discriminant = (2 * shift + 1) ** 2 - 4 * (shift**2 - constant)
        square = math.isqrt(discriminant)
        if square**2 != discriminant:
            raise ValueError("unsupported radical item")
        roots = []
        for numerator in (2 * shift + 1 - square, 2 * shift + 1 + square):
            if numerator % 2 == 0:
                value = numerator // 2
                if value >= shift and value + constant == (value - shift) ** 2:
                    roots.append(value)
        return tuple(sorted(set(roots)))
    raise ValueError("item is outside the frozen generator grammar")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def verify_candidate(raw, questions):
    """Untrusted bytes cross a fixed tiny grammar. Never evaluate model-authored expressions."""
    if not isinstance(raw, bytes) or len(raw) > 4096:
        return {"disposition": "malformed"}
    try:
        item = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(item, dict) or set(item) != {"slot", "values"}:
            raise ValueError("shape")
        slot, values = item["slot"], item["values"]
        if type(slot) is not int or not 0 <= slot < len(questions):
            raise ValueError("slot")
        if not isinstance(values, list) or not 1 <= len(values) <= 2:
            raise ValueError("values")
        if any(
            type(value) is not str or not re.fullmatch(r"-?(?:0|[1-9]\d{0,2})", value)
            for value in values
        ):
            raise ValueError("integer grammar")
        numbers = tuple(sorted(int(value) for value in values))
        if len(set(numbers)) != len(numbers) or any(abs(value) > 100 for value in numbers):
            raise ValueError("integer bound")
    except (ValueError, UnicodeError, RecursionError):
        return {"disposition": "malformed"}
    # Oracle failures are infrastructure errors, never a model's incorrect answer.
    correct = numbers == oracle(questions[slot])
    return {
        "disposition": "correct" if correct else "incorrect",
        "slot": slot,
        "values": list(numbers),
    }


def prepare(plan):
    from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
    from padawan.models.contracts import CorpusPool

    domain = plan["domain"]
    rng = random.Random(domain["sampling_seed"])
    timestamp = datetime(2026, 9, 6, tzinfo=UTC)
    generator = AlgebraCorpusGenerator()
    public, private, seen, groups = [], [], set(), set()
    attempts = 0
    for partition, count in (
        ("calibration", domain["calibration_projects"]),
        ("main", domain["main_projects"]),
    ):
        for index in range(count):
            records = []
            for family in domain["families"]:
                for _ in range(domain["items_per_family_per_project"]):
                    while True:
                        attempts += 1
                        if attempts > domain["maximum_candidate_draws"]:
                            raise ValueError("frozen sampling draw ceiling reached")
                        seed = rng.randrange(1, 2**31)
                        record = generator.generate(
                            pool=CorpusPool.CURRICULUM,
                            seed=seed,
                            groups_per_family=1,
                            siblings_per_group=2,
                            families=(AlgebraFamily(family),),
                            created_at=timestamp,
                        )[0]
                        question = record.prompt.split("\n", 1)[0]
                        if question not in seen and record.instance_group_id not in groups:
                            break
                    seen.add(question)
                    groups.add(record.instance_group_id)
                    expected = tuple(sorted(int(v) for v in record.expected_answer["values"]))
                    assert oracle(question) == expected
                    records.append(record)
            rng.shuffle(records)
            questions = [record.prompt.split("\n", 1)[0] for record in records]
            identifier = f"{partition}-{index:02d}"
            public.append({"project": identifier, "questions": questions})
            private.append(
                {
                    "project": identifier,
                    "partition": partition,
                    "items": [record.model_dump(mode="json") for record in records],
                    "public_digest": sha(public[-1]),
                }
            )
    scheduler = random.Random(domain["schedule_seed"])
    conditions = list(plan["conditions"])
    scheduler.shuffle(conditions)
    blocks = [
        (project["project"], rep)
        for project in public
        for rep in range(domain["replicates"] if project["project"].startswith("main-") else 1)
    ]
    calibration = [block for block in blocks if block[0].startswith("calibration-")]
    main_blocks = [block for block in blocks if block[0].startswith("main-")]
    scheduler.shuffle(main_blocks)
    schedule = []
    for block_index, (project, rep) in enumerate([*calibration, *main_blocks]):
        rotation = block_index % len(conditions)
        for condition in conditions[rotation:] + conditions[:rotation]:
            calls = plan["workload"][
                "main_model_calls_per_rollout"
                if project.startswith("main-")
                else "calibration_model_calls_per_rollout"
            ]
            schedule.append(
                {
                    "project": project,
                    "replicate": rep,
                    "condition": condition,
                    "call_seeds": [
                        int(sha([domain["schedule_seed"], project, rep, action])[:8], 16) % (2**31)
                        for action in range(calls)
                    ],
                    "maximum_model_calls": calls,
                }
            )
    summary = {
        "proposal_sha256": sha(plan),
        "public_sha256": sha(public),
        "private_sha256": sha(private),
        "schedule_sha256": sha(schedule),
        "candidate_draws": attempts,
        "unique_questions": len(seen),
        "unique_groups": len(groups),
        "planned_rollouts": len(schedule),
        "main_rollouts": sum(s["project"].startswith("main-") for s in schedule),
        "model_call_ceiling": sum(s["maximum_model_calls"] for s in schedule),
        "independent_oracle_matches": len(seen),
        "execution_authorized": False,
        "parameter_training_ready": False,
    }
    assert len(schedule) <= 128
    assert len(schedule) == plan["workload"]["maximum_rollouts"]
    assert summary["model_call_ceiling"] == plan["workload"]["maximum_model_calls"]
    return public, private, schedule, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.resolve()
    if not root.is_relative_to(REPO / "runs") or root == REPO / "runs":
        parser.error("prepare only into an exclusive repository runs directory")
    public, private, schedule, summary = prepare(json.loads(PLAN.read_text()))
    root.mkdir(mode=0o700)
    for name, value in (
        ("public-projects", public),
        ("private-samples", private),
        ("schedule", schedule),
        ("summary", summary),
    ):
        (root / f"{name}.json").write_bytes(canonical(value) + b"\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
