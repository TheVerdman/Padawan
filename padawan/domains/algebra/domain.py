from __future__ import annotations

from datetime import UTC, datetime

from padawan.corpus.algebra import AlgebraCorpusGenerator
from padawan.domains.algebra.workflow import AlgebraWorkflowHandler
from padawan.domains.contracts import DomainSpec
from padawan.domains.runtime import DomainRuntimeContext
from padawan.grading.algebra import AlgebraGrader
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
)


class AlgebraDomain:
    """Installed symbolic-algebra domain with deterministic SymPy authority."""

    def __init__(self) -> None:
        self.generator = AlgebraCorpusGenerator()
        self.grader = AlgebraGrader()
        self._spec = DomainSpec(
            domain_id="math.algebra",
            version="1.0.0",
            title="Symbolic algebra",
            description="Governed symbolic-algebra tasks with public-step validation.",
            evidence_hierarchy=(
                "sympy_verification",
                "public_derivation_steps",
                "student_output",
                "teacher_interpretation",
            ),
            deterministic_verifiers=("symbolic_algebra",),
            permissible_teacher_modes=(
                TeacherMode.SOCRATIC_HINT,
                TeacherMode.DIAGNOSTIC_CRITIQUE,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.MINIMAL_REPAIR,
                TeacherMode.FULL_DEMONSTRATION,
                TeacherMode.CONTRASTIVE_EXPLANATION,
                TeacherMode.MICRO_CURRICULUM,
                TeacherMode.METACOGNITIVE_FEEDBACK,
            ),
            supports_tools=False,
        )

    @property
    def spec(self) -> DomainSpec:
        return self._spec

    def competencies(self) -> tuple[CompetencyRecord, ...]:
        return tuple(self.generator.competencies(created_at=datetime.now(UTC)))

    def validate_verifier_spec(self, verifier: VerifierSpec) -> None:
        if verifier.verifier_type != self.grader.grader_type:
            raise ValueError("algebra items require the symbolic_algebra verifier")
        if verifier.verifier_version != self.grader.grader_version:
            raise ValueError("algebra verifier version is not installed")

    def validate_item(self, item: CorpusItemRecord) -> None:
        if not item.competency_id.startswith("algebra."):
            raise ValueError("item does not belong to the algebra competency namespace")
        self.validate_verifier_spec(item.verifier_spec)
        if item.expected_answer is None:
            raise ValueError("algebra items require a deterministic expected answer")

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

    def build_workflow(self, context: DomainRuntimeContext) -> AlgebraWorkflowHandler:
        return AlgebraWorkflowHandler(
            database=context.database,
            artifacts=context.artifacts,
            registry=context.corpus_registry,
            states=context.states,
            episodes=context.episodes,
            experiments=context.experiments,
            grader=self.grader,
            provenance=context.provenance,
            student_calls=context.student_calls,
            teacher_calls=context.teacher_calls,
            teacher_provider=context.teacher_provider,
            memory=context.memory,
            memory_backend=context.memory_backend,
            student_runtime_id=context.student_runtime_id,
            student_runtime_version=context.student_runtime_version,
            student_checkpoint_id=context.student_checkpoint_id,
            student_role=context.student_role,
            lease_for=context.lease_for,
        )
