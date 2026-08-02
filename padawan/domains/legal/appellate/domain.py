from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import DomainSpec
from padawan.domains.legal.appellate.contracts import AppellateScenarioManifest
from padawan.domains.legal.appellate.corpus import (
    APPELLATE_DOMAIN_ID,
    APPELLATE_DOMAIN_VERSION,
    APPELLATE_VERIFIER_TYPE,
    APPELLATE_VERIFIER_VERSION,
    AppellateCorpusGenerator,
)
from padawan.domains.legal.appellate.court_pack import build_fourth_circuit_pack
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
)


class AppellateBriefingDomain:
    """Corpus and verifier authority for the first closed federal appellate pack.

    The package intentionally has no live student or autonomous developmental workflow. It emits
    governed tasks and validates evidence contracts for an external runner.
    """

    def __init__(self) -> None:
        self.generator = AppellateCorpusGenerator()
        self._spec = DomainSpec(
            domain_id=APPELLATE_DOMAIN_ID,
            version=APPELLATE_DOMAIN_VERSION,
            title="Closed-Record Fourth Circuit Appellate Briefing",
            description=(
                "Synthetic summary-judgment appeals verified against one pinned Fourth Circuit "
                "rule and authority pack, a closed record, and a typed claim/citation map."
            ),
            evidence_hierarchy=(
                "court_pack_and_closed_record",
                "deterministic_rule_record_citation_quote_and_leakage_checks",
                "evidence_bound_semantic_adjudication",
                "currentness_assessment_from_dependable_citator",
                "brief_prose",
                "teacher_interpretation",
            ),
            deterministic_verifiers=(APPELLATE_VERIFIER_TYPE,),
            permissible_teacher_modes=(
                TeacherMode.DIAGNOSTIC_CRITIQUE,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.MINIMAL_REPAIR,
                TeacherMode.CONTRASTIVE_EXPLANATION,
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
        if verifier.verifier_type != APPELLATE_VERIFIER_TYPE:
            raise ValueError("appellate items require the closed-record verifier")
        if verifier.verifier_version != APPELLATE_VERIFIER_VERSION:
            raise ValueError("appellate verifier version is not installed")
        pack = build_fourth_circuit_pack()
        if verifier.parameters.get("court_pack_id") != pack.pack_id:
            raise ValueError("appellate verifier cites an uninstalled court pack")
        if verifier.parameters.get("court_pack_digest") != pack.pack_digest:
            raise ValueError("appellate verifier court-pack digest differs from the installed pack")
        if verifier.parameters.get("currentness_capability") != "unknown_without_citator":
            raise ValueError("appellate verifier overstates currentness capability")
        scenario_payload = verifier.parameters.get("scenario")
        if not isinstance(scenario_payload, dict):
            raise ValueError("appellate verifier requires a scenario manifest")
        scenario = AppellateScenarioManifest.model_validate(scenario_payload, strict=False)
        if scenario.version != APPELLATE_DOMAIN_VERSION:
            raise ValueError("appellate scenario version differs from the installed domain")
        if scenario.court_pack_id != pack.pack_id or scenario.court_pack_digest != pack.pack_digest:
            raise ValueError("appellate scenario differs from the installed court pack")

    def validate_item(self, item: CorpusItemRecord) -> None:
        if not item.competency_id.startswith("appellate."):
            raise ValueError("item does not belong to the appellate competency namespace")
        if item.expected_answer is not None:
            raise ValueError("appellate agentic items cannot carry a fixed expected answer")
        self.validate_verifier_spec(item.verifier_spec)
        scenario = AppellateScenarioManifest.model_validate(
            item.verifier_spec.parameters["scenario"], strict=False
        )
        if scenario.competency_id != item.competency_id:
            raise ValueError("appellate scenario competency differs from its corpus item")
        if scenario.seed != item.generation_seed:
            raise ValueError("appellate scenario seed differs from its corpus lineage")
        if scenario.split != item.pool.value:
            raise ValueError("appellate scenario split differs from its governed corpus pool")
        if item.generator_version != self.generator.generator_version:
            raise ValueError("appellate corpus generator version is not installed")
        expected = self.generator.scenario(
            family=scenario.family,
            seed=scenario.seed,
            split=scenario.split,
            created_at=scenario.created_at,
        )
        if scenario != expected:
            raise ValueError("appellate scenario differs from deterministic generator output")

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
