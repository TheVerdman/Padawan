from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import DomainSpec
from padawan.domains.lean_math.corpus import (
    LEAN_VERIFIER_TYPE,
    LEAN_VERIFIER_VERSION,
    LeanMathCorpusGenerator,
)
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
)


class LeanMathDomain:
    """Kernel-verifiable Lean mathematics domain.

    Round 2 installs its corpus and verifier authority. It deliberately does not
    claim the algebra domain's autonomous teaching workflow.
    """

    def __init__(self) -> None:
        self.generator = LeanMathCorpusGenerator()
        self._spec = DomainSpec(
            domain_id="math.lean",
            version="1.0.0",
            title="Lean-verified mathematics",
            description="Pinned Lean 4 and Mathlib tasks with kernel-authoritative proof checks.",
            evidence_hierarchy=(
                "lean4_kernel",
                "sandboxed_execution_record",
                "public_proof_term",
                "teacher_interpretation",
            ),
            deterministic_verifiers=(LEAN_VERIFIER_TYPE,),
            permissible_teacher_modes=(
                TeacherMode.SOCRATIC_HINT,
                TeacherMode.DIAGNOSTIC_CRITIQUE,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.MINIMAL_REPAIR,
                TeacherMode.FULL_DEMONSTRATION,
                TeacherMode.CONTRASTIVE_EXPLANATION,
            ),
            supports_tools=True,
        )

    @property
    def spec(self) -> DomainSpec:
        return self._spec

    def competencies(self) -> tuple[CompetencyRecord, ...]:
        return tuple(self.generator.competencies(created_at=datetime.now(UTC)))

    def validate_verifier_spec(self, verifier: VerifierSpec) -> None:
        if verifier.verifier_type != LEAN_VERIFIER_TYPE:
            raise ValueError("Lean math items require the lean4_kernel verifier")
        if verifier.verifier_version != LEAN_VERIFIER_VERSION:
            raise ValueError("Lean verifier version is not installed")
        imports = verifier.parameters.get("imports")
        if imports != ["Mathlib"]:
            raise ValueError("Lean math items must use the pinned Mathlib import surface")

    def validate_item(self, item: CorpusItemRecord) -> None:
        if not item.competency_id.startswith("lean_math."):
            raise ValueError("item does not belong to the Lean math competency namespace")
        self.validate_verifier_spec(item.verifier_spec)
        expected = item.expected_answer
        if expected is None or expected.get("kind") != "lean4_proof":
            raise ValueError("Lean math items require a typed proof answer")
        if expected.get("statement") != item.verifier_spec.parameters.get("statement"):
            raise ValueError("Lean statement differs between answer and verifier specification")
        if not isinstance(expected.get("proof"), str) or not expected["proof"].strip():
            raise ValueError("Lean math items require a non-empty reference proof")

    def generate_curriculum(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int,
        siblings_per_group: int,
    ) -> tuple[CorpusItemRecord, ...]:
        return tuple(
            self.generator.generate(
                pool=pool,
                seed=seed,
                groups_per_family=groups_per_family,
                siblings_per_group=siblings_per_group,
            )
        )
