from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
    project_authored_internal_rights,
)
from padawan.models.hashing import sha256_digest

LEAN_TOOLCHAIN = "leanprover/lean4:v4.32.2"
MATHLIB_VERSION = "v4.32.2"
MATHLIB_REVISION = "905b95818eb32af7874a58b427f50c1711a5e96c"
LEAN_VERIFIER_TYPE = "lean4_kernel"
LEAN_VERIFIER_VERSION = "lean4.32.2-mathlib4.32.2-padawan1"


class LeanMathFamily(StrEnum):
    INT_LINEAR_EQUATION = "int_linear_equation"
    NAT_LINEAR_BOUND = "nat_linear_bound"


@dataclass(frozen=True)
class GeneratedLeanProblem:
    statement: str
    proof: str
    difficulty: float
    metadata: dict[str, Any]


class LeanMathCorpusGenerator:
    """Deterministic, matched Lean tasks whose answers are checked by the kernel."""

    generator_version = "padawan-lean-math-v1"

    def competencies(self, *, created_at: datetime | None = None) -> list[CompetencyRecord]:
        timestamp = created_at or datetime.now(UTC)
        modes = (
            TeacherMode.SOCRATIC_HINT,
            TeacherMode.DIAGNOSTIC_CRITIQUE,
            TeacherMode.GENERAL_PRINCIPLE,
            TeacherMode.MINIMAL_REPAIR,
            TeacherMode.FULL_DEMONSTRATION,
            TeacherMode.CONTRASTIVE_EXPLANATION,
        )
        return [
            CompetencyRecord(
                competency_id=f"lean_math.{family.value}",
                title=family.value.replace("_", " ").title(),
                description=(
                    "Lean 4 theorem-proving competency with a pinned Mathlib environment "
                    f"for {family.value}."
                ),
                parent_competency_id=None,
                prerequisite_competency_ids=(),
                grader_requirements=(LEAN_VERIFIER_TYPE, LEAN_VERIFIER_VERSION),
                permissible_teacher_modes=modes,
                difficulty_calibration={
                    "scale": "0_to_1",
                    "generator": self.generator_version,
                    "dimensions": ["numeric_magnitude", "hypothesis_count", "number_domain"],
                },
                version=1,
                created_at=timestamp,
            )
            for family in LeanMathFamily
        ]

    def generate(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int = 2,
        siblings_per_group: int = 2,
        families: tuple[LeanMathFamily, ...] | None = None,
        created_at: datetime | None = None,
    ) -> list[CorpusItemRecord]:
        if pool == CorpusPool.QUARANTINE:
            raise ValueError("new items cannot be generated directly into quarantine")
        if groups_per_family <= 0 or siblings_per_group < 2:
            raise ValueError(
                "matched generation requires positive groups and at least two siblings"
            )
        timestamp = created_at or datetime.now(UTC)
        selected = families or tuple(LeanMathFamily)
        suffix = {
            CorpusPool.CURRICULUM: "train",
            CorpusPool.ROTATING_SHADOW: "shadow",
            CorpusPool.SEALED_ANCHOR: "sealed",
        }[pool]
        records: list[CorpusItemRecord] = []
        for family_index, family in enumerate(selected):
            template_family_id = f"lean-{family.value}-v1-{suffix}"
            for group_index in range(groups_per_family):
                group_seed = seed + family_index * 1_000_003 + group_index * 10_007
                group_digest = sha256_digest(str(group_seed))[7:19]
                instance_group_id = f"{template_family_id}-g{group_digest}"
                statements: set[str] = set()
                for sibling_index in range(siblings_per_group):
                    problem_seed = group_seed + sibling_index * 101
                    salt = 0
                    while True:
                        problem = _generate_problem(family, problem_seed + salt)
                        if problem.statement not in statements:
                            statements.add(problem.statement)
                            break
                        salt += 1
                    item_digest = sha256_digest(
                        {
                            "family": family.value,
                            "group": instance_group_id,
                            "seed": problem_seed + salt,
                            "statement": problem.statement,
                        }
                    )[7:23]
                    records.append(
                        CorpusItemRecord(
                            competency_id=f"lean_math.{family.value}",
                            template_family_id=template_family_id,
                            instance_group_id=instance_group_id,
                            item_id=f"item-lean-{family.value}-{item_digest}",
                            generation_seed=problem_seed + salt,
                            generator_version=self.generator_version,
                            difficulty=problem.difficulty,
                            prompt=_render_prompt(problem.statement),
                            expected_answer={
                                "kind": "lean4_proof",
                                "statement": problem.statement,
                                "proof": problem.proof,
                            },
                            verifier_spec=VerifierSpec(
                                verifier_type=LEAN_VERIFIER_TYPE,
                                verifier_version=LEAN_VERIFIER_VERSION,
                                parameters={
                                    "statement": problem.statement,
                                    "imports": ["Mathlib"],
                                    "lean_toolchain": LEAN_TOOLCHAIN,
                                    "mathlib_version": MATHLIB_VERSION,
                                    "mathlib_revision": MATHLIB_REVISION,
                                    **problem.metadata,
                                },
                            ),
                            pool=pool,
                            source="deterministic:padawan.domains.lean_math.corpus",
                            rights=project_authored_internal_rights(reviewed_at=timestamp),
                            contamination_scope="instance_group",
                            created_at=timestamp,
                        )
                    )
        return records


def _generate_problem(family: LeanMathFamily, seed: int) -> GeneratedLeanProblem:
    rng = random.Random(seed)
    if family == LeanMathFamily.INT_LINEAR_EQUATION:
        coefficient = rng.randint(2, 9)
        solution = rng.choice([value for value in range(-15, 16) if value != 0])
        offset = rng.randint(-20, 20)
        right = coefficient * solution + offset
        return GeneratedLeanProblem(
            statement=(f"∀ x : Int, {coefficient} * x + ({offset}) = {right} → x = ({solution})"),
            proof="by\n  omega",
            difficulty=0.42,
            metadata={"family": family.value, "number_domain": "Int"},
        )
    if family == LeanMathFamily.NAT_LINEAR_BOUND:
        coefficient = rng.randint(2, 8)
        bound = rng.randint(4, 30)
        offset = rng.randint(0, 12)
        upper = coefficient * bound + offset
        return GeneratedLeanProblem(
            statement=(f"∀ n : Nat, n ≤ {bound} → {coefficient} * n + {offset} ≤ {upper}"),
            proof="by\n  omega",
            difficulty=0.36,
            metadata={"family": family.value, "number_domain": "Nat"},
        )
    raise AssertionError(family)


def _render_prompt(statement: str) -> str:
    return (
        "Complete the following theorem in Lean 4 with Mathlib available. Return only the "
        "proof term beginning with `by`; do not include imports, declarations, markdown fences, "
        "or prose.\n\n"
        f"theorem padawan_candidate : ({statement}) := ?_"
    )
