from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import DomainSpec
from padawan.domains.magellan_improvement.contracts import MagellanScenarioManifest
from padawan.domains.magellan_improvement.corpus import (
    MAGELLAN_DOMAIN_ID,
    MAGELLAN_DOMAIN_VERSION,
    MAGELLAN_VERIFIER_TYPE,
    MAGELLAN_VERIFIER_VERSION,
    UNBOUND_ENVIRONMENT_FINGERPRINT,
    MagellanScenarioGenerator,
)
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
)


class MagellanImprovementDomain:
    """Corpus and verifier authority for an external, explicitly handshaken Magellan world.

    This package intentionally has no autonomous workflow until a real Magellan driver satisfies
    the environment contract. Registering the domain therefore cannot be mistaken for a live
    target-student integration.
    """

    def __init__(self, *, environment_fingerprint: str | None = None) -> None:
        self.generator = MagellanScenarioGenerator(environment_fingerprint=environment_fingerprint)
        self._spec = DomainSpec(
            domain_id=MAGELLAN_DOMAIN_ID,
            version=MAGELLAN_DOMAIN_VERSION,
            title="Magellan Improvement",
            description=(
                "Agentic logistics scenarios verified from isolated world state, authorization, "
                "tool observations, approvals, and durable replay evidence."
            ),
            evidence_hierarchy=(
                "isolated_database_state",
                "authorization_and_tool_observations",
                "approval_and_provenance_records",
                "agent_trace",
                "agent_final_answer",
                "teacher_interpretation",
            ),
            deterministic_verifiers=(MAGELLAN_VERIFIER_TYPE,),
            permissible_teacher_modes=(
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
        if verifier.verifier_type != MAGELLAN_VERIFIER_TYPE:
            raise ValueError("Magellan items require the magellan_scenario verifier")
        if verifier.verifier_version != MAGELLAN_VERIFIER_VERSION:
            raise ValueError("Magellan verifier version is not installed")
        scenario_payload = verifier.parameters.get("scenario")
        if not isinstance(scenario_payload, dict):
            raise ValueError("Magellan verifier specification requires a scenario manifest")
        scenario = MagellanScenarioManifest.model_validate(scenario_payload, strict=False)
        if scenario.version != MAGELLAN_DOMAIN_VERSION:
            raise ValueError("Magellan scenario version differs from the installed domain")
        environment_bound = verifier.parameters.get("environment_bound")
        if not isinstance(environment_bound, bool):
            raise ValueError("Magellan verifier must declare whether its environment is bound")
        expected_bound = scenario.environment_fingerprint != UNBOUND_ENVIRONMENT_FINGERPRINT
        if environment_bound != expected_bound:
            raise ValueError("Magellan environment-bound declaration is inconsistent")

    def validate_item(self, item: CorpusItemRecord) -> None:
        if not item.competency_id.startswith("magellan."):
            raise ValueError("item does not belong to the Magellan competency namespace")
        if item.expected_answer is not None:
            raise ValueError("Magellan agentic items cannot carry a fixed expected answer")
        self.validate_verifier_spec(item.verifier_spec)
        scenario = MagellanScenarioManifest.model_validate(
            item.verifier_spec.parameters["scenario"], strict=False
        )
        if scenario.competency_id != item.competency_id:
            raise ValueError("Magellan scenario competency differs from its corpus item")
        if scenario.seed != item.generation_seed:
            raise ValueError("Magellan scenario seed differs from its corpus lineage")
        if scenario.split != item.pool.value:
            raise ValueError("Magellan scenario split differs from its governed corpus pool")
        if item.generator_version != self.generator.generator_version:
            raise ValueError("Magellan corpus generator version is not installed")
        expected = MagellanScenarioGenerator(
            environment_fingerprint=scenario.environment_fingerprint
        ).scenario(
            family=scenario.family,
            seed=scenario.seed,
            split=scenario.split,
            created_at=scenario.created_at,
        )
        if scenario != expected:
            raise ValueError("Magellan scenario differs from deterministic generator output")

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
