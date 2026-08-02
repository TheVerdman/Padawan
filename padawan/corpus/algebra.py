from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import sympy as sp

from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
    project_authored_internal_rights,
)
from padawan.models.hashing import sha256_digest


class AlgebraFamily(StrEnum):
    DISTRIBUTION_SIGN = "distribution_sign"
    LINEAR_BOTH_SIDES = "linear_both_sides"
    POLYNOMIAL_FACTORING = "polynomial_factoring"
    RATIONAL_SIMPLIFICATION = "rational_simplification"
    INVALID_CANCELLATION = "invalid_cancellation"
    EXCLUDED_VALUES = "excluded_values"
    EXTRANEOUS_ROOTS = "extraneous_roots"
    OMITTED_BRANCHES = "omitted_branches"


@dataclass(frozen=True)
class GeneratedProblem:
    prompt_math: str
    expected_answer: dict[str, Any]
    difficulty: float
    generation_metadata: dict[str, Any]


class AlgebraCorpusGenerator:
    generator_version = "padawan-algebra-v1"

    def competencies(self, *, created_at: datetime | None = None) -> list[CompetencyRecord]:
        timestamp = created_at or datetime.now(UTC)
        modes = tuple(TeacherMode)
        records: list[CompetencyRecord] = []
        for family in AlgebraFamily:
            title = family.value.replace("_", " ").title()
            records.append(
                CompetencyRecord(
                    competency_id=f"algebra.{family.value}",
                    title=title,
                    description=f"Deterministic symbolic-algebra competency for {family.value}.",
                    parent_competency_id=None,
                    prerequisite_competency_ids=(),
                    grader_requirements=("sympy", "public_derivation_v1"),
                    permissible_teacher_modes=modes,
                    difficulty_calibration={
                        "scale": "0_to_1",
                        "generator": self.generator_version,
                        "dimensions": [
                            "coefficient_magnitude",
                            "branch_count",
                            "domain_constraints",
                        ],
                    },
                    version=1,
                    created_at=timestamp,
                )
            )
        return records

    def generate(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int = 2,
        siblings_per_group: int = 2,
        families: tuple[AlgebraFamily, ...] | None = None,
        created_at: datetime | None = None,
    ) -> list[CorpusItemRecord]:
        if pool == CorpusPool.QUARANTINE:
            raise ValueError("new items cannot be generated directly into quarantine")
        if groups_per_family <= 0 or siblings_per_group < 2:
            raise ValueError(
                "matched generation requires positive groups and at least two siblings"
            )
        timestamp = created_at or datetime.now(UTC)
        selected = families or tuple(AlgebraFamily)
        records: list[CorpusItemRecord] = []
        visibility_suffix = {
            CorpusPool.CURRICULUM: "train",
            CorpusPool.ROTATING_SHADOW: "shadow",
            CorpusPool.SEALED_ANCHOR: "sealed",
        }[pool]
        for family_index, family in enumerate(selected):
            template_family_id = f"alg-{family.value}-v1-{visibility_suffix}"
            for group_index in range(groups_per_family):
                group_seed = seed + family_index * 1_000_003 + group_index * 10_007
                instance_group_id = f"{template_family_id}-g{sha256_digest(str(group_seed))[7:19]}"
                answers: set[str] = set()
                for sibling_index in range(siblings_per_group):
                    problem_seed = group_seed + sibling_index * 101
                    salt = 0
                    while True:
                        problem = _generate_problem(family, problem_seed + salt)
                        answer_key = json.dumps(problem.expected_answer, sort_keys=True)
                        if answer_key not in answers:
                            answers.add(answer_key)
                            break
                        salt += 1
                    item_digest = sha256_digest(
                        {
                            "family": family.value,
                            "instance_group_id": instance_group_id,
                            "seed": problem_seed + salt,
                            "prompt": problem.prompt_math,
                        }
                    )[7:23]
                    prompt = _render_prompt(problem.prompt_math)
                    records.append(
                        CorpusItemRecord(
                            competency_id=f"algebra.{family.value}",
                            template_family_id=template_family_id,
                            instance_group_id=instance_group_id,
                            item_id=f"item-{family.value}-{item_digest}",
                            generation_seed=problem_seed + salt,
                            generator_version=self.generator_version,
                            difficulty=problem.difficulty,
                            prompt=prompt,
                            expected_answer=problem.expected_answer,
                            verifier_spec=VerifierSpec(
                                verifier_type="symbolic_algebra",
                                verifier_version="padawan-sympy-v1",
                                parameters={
                                    "family": family.value,
                                    "variable": "x",
                                    **problem.generation_metadata,
                                },
                            ),
                            pool=pool,
                            source="deterministic:padawan.corpus.algebra",
                            rights=project_authored_internal_rights(reviewed_at=timestamp),
                            contamination_scope="instance_group",
                            created_at=timestamp,
                        )
                    )
        return records


def _generate_problem(family: AlgebraFamily, seed: int) -> GeneratedProblem:
    rng = random.Random(seed)
    x = sp.Symbol("x", real=True)
    if family == AlgebraFamily.DISTRIBUTION_SIGN:
        coefficient = rng.choice([2, 3, 4, 5, 6])
        shift = rng.choice([value for value in range(-7, 8) if value != 0])
        solution = rng.choice([value for value in range(-12, 13) if value not in {0, shift}])
        right = -coefficient * (solution + shift)
        equation = f"-{coefficient}*(x + ({shift})) = {right}"
        return _solution_problem(equation, [solution], 0.25, {"sign_sensitive": True})

    if family == AlgebraFamily.LINEAR_BOTH_SIDES:
        left_coefficient = rng.randint(2, 8)
        right_coefficient = rng.choice(
            [value for value in range(-6, 9) if value != left_coefficient]
        )
        solution = rng.choice([value for value in range(-10, 11) if value != 0])
        left_constant = rng.randint(-12, 12)
        right_constant = (left_coefficient - right_coefficient) * solution + left_constant
        equation = (
            f"{left_coefficient}*x + ({left_constant}) = {right_coefficient}*x + ({right_constant})"
        )
        return _solution_problem(equation, [solution], 0.35, {"variables_both_sides": True})

    if family == AlgebraFamily.POLYNOMIAL_FACTORING:
        root_a = rng.choice([value for value in range(-9, 10) if value != 0])
        root_b = rng.choice([value for value in range(-9, 10) if value not in {0, root_a}])
        expanded = sp.expand((x - root_a) * (x - root_b))
        factored = sp.factor(expanded)
        return GeneratedProblem(
            prompt_math=f"Factor completely over the integers: {sp.sstr(expanded)}",
            expected_answer={
                "kind": "expression",
                "variable": "x",
                "expression": sp.sstr(factored),
                "exclusions": [],
            },
            difficulty=0.4,
            generation_metadata={"roots": [root_a, root_b]},
        )

    if family == AlgebraFamily.RATIONAL_SIMPLIFICATION:
        cancelled = rng.choice([value for value in range(-8, 9) if value != 0])
        pole = rng.choice([value for value in range(-8, 9) if value not in {0, cancelled}])
        offset = rng.choice([value for value in range(-8, 9) if value not in {cancelled, pole}])
        original_text = (
            f"((x - ({cancelled}))*(x + ({offset})))/((x - ({cancelled}))*(x - ({pole})))"
        )
        simplified = (x + offset) / (x - pole)
        return GeneratedProblem(
            prompt_math=(
                f"Simplify {original_text} and preserve every exclusion from the original domain."
            ),
            expected_answer={
                "kind": "expression",
                "variable": "x",
                "expression": sp.sstr(simplified),
                "exclusions": [str(cancelled), str(pole)],
            },
            difficulty=0.58,
            generation_metadata={"original_denominator_roots": [cancelled, pole]},
        )

    if family == AlgebraFamily.INVALID_CANCELLATION:
        offset = rng.choice([value for value in range(-12, 13) if value != 0])
        original_text = f"(x**2 + ({offset})*x)/x"
        return GeneratedProblem(
            prompt_math=(
                f"Simplify {original_text}. A cancellation is valid only if its "
                "domain restriction is retained."
            ),
            expected_answer={
                "kind": "expression",
                "variable": "x",
                "expression": f"x + ({offset})",
                "exclusions": ["0"],
            },
            difficulty=0.62,
            generation_metadata={"invalid_if_exclusion_omitted": True},
        )

    if family == AlgebraFamily.EXCLUDED_VALUES:
        pole_a = rng.choice([value for value in range(-8, 9) if value != 0])
        pole_b = rng.choice([value for value in range(-8, 9) if value not in {0, pole_a}])
        original = 1 / (x - pole_a) + 1 / (x - pole_b)
        simplified = sp.factor(sp.together(original))
        return GeneratedProblem(
            prompt_math=(f"Combine and simplify {sp.sstr(original)}; state all excluded values."),
            expected_answer={
                "kind": "expression",
                "variable": "x",
                "expression": sp.sstr(simplified),
                "exclusions": [str(pole_a), str(pole_b)],
            },
            difficulty=0.66,
            generation_metadata={"original_denominator_roots": [pole_a, pole_b]},
        )

    if family == AlgebraFamily.EXTRANEOUS_ROOTS:
        shift = rng.randint(-3, 7)
        gap = rng.randint(2, 5)
        valid_root = shift + gap
        extraneous_root = shift + 1 - gap
        radicand_constant = shift * shift - valid_root * extraneous_root
        equation = f"sqrt(x + ({radicand_constant})) = x - ({shift})"
        return _solution_problem(
            equation,
            [valid_root],
            0.78,
            {
                "squared_candidates": [extraneous_root, valid_root],
                "extraneous_root": extraneous_root,
            },
        )

    if family == AlgebraFamily.OMITTED_BRANCHES:
        center = rng.randint(-8, 8)
        distance = rng.randint(2, 9)
        equation = f"(x - ({center}))**2 = {distance * distance}"
        return _solution_problem(
            equation,
            [center - distance, center + distance],
            0.7,
            {"required_branch_count": 2},
        )
    raise AssertionError(family)


def _solution_problem(
    equation: str,
    solutions: list[int],
    difficulty: float,
    metadata: dict[str, Any],
) -> GeneratedProblem:
    _validate_solution_problem(equation, solutions)
    return GeneratedProblem(
        prompt_math=f"Solve over the real numbers: {equation}",
        expected_answer={
            "kind": "solution_set",
            "variable": "x",
            "values": [str(value) for value in sorted(solutions)],
            "exclusions": [],
        },
        difficulty=difficulty,
        generation_metadata=metadata,
    )


def _validate_solution_problem(equation: str, expected: list[int]) -> None:
    x = sp.Symbol("x", real=True)
    left, right = equation.replace("^", "**").split("=", 1)
    locals_map = {"x": x, "sqrt": sp.sqrt}
    observed = sp.solveset(
        sp.Eq(
            sp.sympify(left, locals=locals_map),
            sp.sympify(right, locals=locals_map),
        ),
        x,
        domain=sp.S.Reals,
    )
    wanted = sp.FiniteSet(*(sp.Integer(value) for value in expected))
    if observed != wanted:
        raise ValueError(f"generator produced invalid item: {equation}; {observed} != {wanted}")


def _render_prompt(problem: str) -> str:
    schema = {
        "steps": [
            {
                "step_id": "s1",
                "before": "the expression or equation before this operation",
                "operation": "a precise description of the operation",
                "after": "the expression or equation after this operation",
                "assumptions": ["domain restrictions introduced or preserved"],
            }
        ],
        "final_answer": {
            "kind": "solution_set or expression",
            "variable": "x",
            "values": ["use for solution_set"],
            "expression": "use for expression, otherwise null",
            "exclusions": ["all values excluded by the original problem"],
        },
    }
    return (
        f"{problem}\n\n"
        "Return only a JSON object matching this public-derivation shape. Include every algebra "
        "step; do not call an external symbolic solver.\n"
        f"{json.dumps(schema, sort_keys=True)}"
    )
