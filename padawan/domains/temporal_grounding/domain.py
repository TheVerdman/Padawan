from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import DomainSpec
from padawan.domains.developmental.workflow import DomainDevelopmentalWorkflowHandler
from padawan.domains.runtime import DomainRuntimeContext
from padawan.domains.temporal_grounding.contracts import (
    TemporalScenarioFamily,
    TemporalScenarioManifest,
)
from padawan.domains.temporal_grounding.corpus import (
    TEMPORAL_VERIFIER_TYPE,
    TEMPORAL_VERIFIER_VERSION,
    TemporalGroundingCorpusGenerator,
)
from padawan.domains.temporal_grounding.developmental import (
    TemporalGroundingDevelopmentalAuthority,
)
from padawan.domains.temporal_grounding.verifier import TemporalPolicyVerifier
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
)


class TemporalGroundingDomain:
    def __init__(self) -> None:
        self.generator = TemporalGroundingCorpusGenerator()
        self.verifier = TemporalPolicyVerifier()
        self._spec = DomainSpec(
            domain_id="temporal.grounding",
            version="1.0.0",
            title="Temporal grounding and duration calibration",
            description=(
                "Authoritative event-time reasoning, activity honesty, evidence freshness, "
                "duration forecasting, and online ETA revision."
            ),
            evidence_hierarchy=(
                "authoritative_temporal_frame",
                "durable_operation_span",
                "versioned_duration_profile",
                "structured_temporal_decision",
                "user_visible_response",
                "teacher_interpretation",
            ),
            deterministic_verifiers=(TEMPORAL_VERIFIER_TYPE,),
            permissible_teacher_modes=(
                TeacherMode.SOCRATIC_HINT,
                TeacherMode.DIAGNOSTIC_CRITIQUE,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.MINIMAL_REPAIR,
                TeacherMode.CONTRASTIVE_EXPLANATION,
                TeacherMode.METACOGNITIVE_FEEDBACK,
            ),
            supports_tools=True,
        )

    @property
    def spec(self) -> DomainSpec:
        return self._spec

    def competencies(self) -> tuple[CompetencyRecord, ...]:
        return tuple(self.generator.competencies(created_at=datetime.now(UTC)))

    def validate_verifier_spec(self, verifier: VerifierSpec) -> None:
        if verifier.verifier_type != TEMPORAL_VERIFIER_TYPE:
            raise ValueError("temporal items require the temporal policy verifier")
        if verifier.verifier_version != TEMPORAL_VERIFIER_VERSION:
            raise ValueError("temporal verifier version is not installed")
        if "scenario" not in verifier.parameters:
            raise ValueError("temporal verifier requires a scenario manifest")
        TemporalScenarioManifest.model_validate(verifier.parameters["scenario"], strict=False)

    def validate_item(self, item: CorpusItemRecord) -> None:
        if not item.competency_id.startswith("temporal."):
            raise ValueError("item does not belong to the temporal competency namespace")
        self.validate_verifier_spec(item.verifier_spec)
        scenario = TemporalScenarioManifest.model_validate(
            item.verifier_spec.parameters["scenario"], strict=False
        )
        expected_competency = f"temporal.{scenario.family.value}"
        if item.competency_id != expected_competency:
            raise ValueError("temporal scenario family differs from item competency")
        expected = item.expected_answer
        if expected is None or expected.get("kind") != "temporal_decision":
            raise ValueError("temporal items require a typed decision answer")

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
                families=tuple(TemporalScenarioFamily),
            )
        )

    def build_workflow(self, context: DomainRuntimeContext) -> DomainDevelopmentalWorkflowHandler:
        return DomainDevelopmentalWorkflowHandler.from_context(
            authority=TemporalGroundingDevelopmentalAuthority(self.verifier),
            context=context,
        )
