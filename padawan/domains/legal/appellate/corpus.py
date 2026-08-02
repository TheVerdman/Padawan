from __future__ import annotations

import json
import random
from datetime import UTC, datetime

from padawan.domains.legal.appellate.contracts import (
    AppellateBriefType,
    AppellateClaimKind,
    AppellateClosedRecord,
    AppellateCourtPack,
    AppellateRecordDocument,
    AppellateRecordFact,
    AppellateRecordPage,
    AppellateScenarioFamily,
    AppellateScenarioManifest,
    BriefSectionKind,
)
from padawan.domains.legal.appellate.court_pack import build_fourth_circuit_pack
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    DistributionScope,
    RightsBasis,
    RightsReviewStatus,
    RightsUse,
    SourceRights,
    TeacherMode,
    VerifierSpec,
)
from padawan.models.hashing import sha256_digest

APPELLATE_DOMAIN_ID = "legal.appellate.fourth_circuit"
APPELLATE_DOMAIN_VERSION = "1.0.0"
APPELLATE_VERIFIER_TYPE = "appellate_closed_record"
APPELLATE_VERIFIER_VERSION = "padawan-appellate-v1"

REQUIRED_SECTIONS = (
    BriefSectionKind.COVER,
    BriefSectionKind.TABLE_OF_CONTENTS,
    BriefSectionKind.TABLE_OF_AUTHORITIES,
    BriefSectionKind.JURISDICTIONAL_STATEMENT,
    BriefSectionKind.ISSUES_PRESENTED,
    BriefSectionKind.STATEMENT_OF_CASE,
    BriefSectionKind.SUMMARY_OF_ARGUMENT,
    BriefSectionKind.ARGUMENT,
    BriefSectionKind.CONCLUSION,
    BriefSectionKind.CERTIFICATE_OF_COMPLIANCE,
    BriefSectionKind.CERTIFICATE_OF_SERVICE,
)

REQUIRED_CLAIM_KINDS = (
    AppellateClaimKind.JURISDICTION,
    AppellateClaimKind.ISSUE,
    AppellateClaimKind.RECORD_FACT,
    AppellateClaimKind.PROCEDURAL_HISTORY,
    AppellateClaimKind.PRESERVATION,
    AppellateClaimKind.LEGAL_RULE,
    AppellateClaimKind.STANDARD_OF_REVIEW,
    AppellateClaimKind.APPLICATION,
    AppellateClaimKind.COUNTERARGUMENT,
    AppellateClaimKind.REMEDY,
)

_DIFFICULTY = {
    AppellateScenarioFamily.AMBIGUOUS_VIDEO: 0.64,
    AppellateScenarioFamily.CONCLUSIVE_VIDEO: 0.76,
    AppellateScenarioFamily.PRESERVATION_TRANSFER: 0.71,
}


class AppellateCorpusGenerator:
    generator_version = "padawan-appellate-synthetic-v1"

    def competencies(self, *, created_at: datetime | None = None) -> list[CompetencyRecord]:
        timestamp = created_at or datetime.now(UTC)
        modes = (
            TeacherMode.DIAGNOSTIC_CRITIQUE,
            TeacherMode.GENERAL_PRINCIPLE,
            TeacherMode.MINIMAL_REPAIR,
            TeacherMode.CONTRASTIVE_EXPLANATION,
            TeacherMode.METACOGNITIVE_FEEDBACK,
        )
        return [
            CompetencyRecord(
                competency_id=_competency_id(family),
                title=f"Appellate Briefing: {family.value.replace('_', ' ').title()}",
                description=(
                    "Closed-record Fourth Circuit briefing with deterministic rule, record, "
                    f"citation, quotation, and leakage checks for {family.value}."
                ),
                prerequisite_competency_ids=(),
                grader_requirements=(APPELLATE_VERIFIER_TYPE,),
                permissible_teacher_modes=modes,
                difficulty_calibration={
                    "scale": "0_to_1",
                    "generator": self.generator_version,
                    "dimensions": [
                        "record_ambiguity",
                        "adverse_authority",
                        "procedural_posture",
                        "preservation",
                    ],
                },
                version=1,
                created_at=timestamp,
            )
            for family in AppellateScenarioFamily
        ]

    def generate(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int = 1,
        siblings_per_group: int = 2,
        families: tuple[AppellateScenarioFamily, ...] | None = None,
        created_at: datetime | None = None,
    ) -> list[CorpusItemRecord]:
        if pool == CorpusPool.QUARANTINE:
            raise ValueError("new appellate tasks cannot be generated into quarantine")
        if groups_per_family <= 0 or siblings_per_group < 2:
            raise ValueError("appellate generation requires matched sibling groups")
        timestamp = created_at or datetime.now(UTC)
        selected = families or tuple(AppellateScenarioFamily)
        visibility = {
            CorpusPool.CURRICULUM: "train",
            CorpusPool.ROTATING_SHADOW: "shadow",
            CorpusPool.SEALED_ANCHOR: "sealed",
        }[pool]
        pack = build_fourth_circuit_pack()
        rights = _appellate_internal_rights(reviewed_at=timestamp)
        records: list[CorpusItemRecord] = []
        for family_index, family in enumerate(selected):
            template_family_id = f"appellate-{family.value}-v1-{visibility}"
            for group_index in range(groups_per_family):
                group_seed = seed + family_index * 1_000_003 + group_index * 10_007
                group_id = (
                    f"{template_family_id}-g"
                    f"{sha256_digest({'family': family, 'seed': group_seed, 'pool': pool})[7:19]}"
                )
                for sibling_index in range(siblings_per_group):
                    scenario_seed = group_seed + sibling_index * 101
                    scenario = self.scenario(
                        family=family,
                        seed=scenario_seed,
                        split=pool.value,
                        created_at=timestamp,
                    )
                    records.append(
                        CorpusItemRecord(
                            competency_id=scenario.competency_id,
                            template_family_id=template_family_id,
                            instance_group_id=group_id,
                            item_id=f"item-appellate-{scenario.scenario_digest[7:23]}",
                            generation_seed=scenario_seed,
                            generator_version=self.generator_version,
                            difficulty=_DIFFICULTY[family],
                            prompt=_render_prompt(pack=pack, scenario=scenario),
                            expected_answer=None,
                            verifier_spec=VerifierSpec(
                                verifier_type=APPELLATE_VERIFIER_TYPE,
                                verifier_version=APPELLATE_VERIFIER_VERSION,
                                parameters={
                                    "court_pack_id": pack.pack_id,
                                    "court_pack_digest": pack.pack_digest,
                                    "scenario": scenario.model_dump(mode="json"),
                                    "currentness_capability": "unknown_without_citator",
                                },
                            ),
                            pool=pool,
                            source=(
                                "deterministic:padawan.domains.legal.appellate.corpus; "
                                "official federal excerpts and task renderings"
                            ),
                            rights=rights,
                            contamination_scope="instance_group",
                            created_at=timestamp,
                        )
                    )
        return records

    def scenario(
        self,
        *,
        family: AppellateScenarioFamily,
        seed: int,
        split: str,
        created_at: datetime | None = None,
    ) -> AppellateScenarioManifest:
        timestamp = created_at or datetime.now(UTC)
        pack = build_fourth_circuit_pack()
        record = _build_record(family=family, seed=seed)
        environment_fingerprint = sha256_digest(
            {
                "court_pack_digest": pack.pack_digest,
                "record_digest": record.record_digest,
                "brief_type": AppellateBriefType.APPELLANT_PRINCIPAL,
                "procedural_posture": "appeal from final summary judgment",
                "split": split,
            }
        )
        candidate = AppellateScenarioManifest.model_construct(
            scenario_id="pending",
            version=APPELLATE_DOMAIN_VERSION,
            family=family,
            competency_id=_competency_id(family),
            split=split,
            seed=seed,
            court_pack_id=pack.pack_id,
            court_pack_digest=pack.pack_digest,
            brief_type=AppellateBriefType.APPELLANT_PRINCIPAL,
            procedural_posture="appeal from final summary judgment",
            issue=(
                "Whether the district court improperly resolved a material factual dispute and "
                "weighed the summary-judgment record against the nonmoving plaintiff."
            ),
            requested_relief="Vacate the judgment and remand for trial.",
            exercise_word_limit=1_500,
            required_sections=REQUIRED_SECTIONS,
            required_claim_kinds=REQUIRED_CLAIM_KINDS,
            required_adverse_authority_ids=("scott-v-harris",),
            record=record,
            restricted_sentence_digests=(),
            environment_fingerprint=environment_fingerprint,
            scenario_digest=f"sha256:{'0' * 64}",
            created_at=timestamp,
        )
        normalized = candidate.model_dump(mode="json", exclude={"scenario_id", "scenario_digest"})
        scenario_digest = sha256_digest(normalized)
        payload = candidate.model_dump(mode="json")
        payload["scenario_id"] = f"appellate-scenario-{scenario_digest[7:23]}"
        payload["scenario_digest"] = scenario_digest
        return AppellateScenarioManifest.model_validate(
            payload,
            strict=False,
        )


def _build_record(*, family: AppellateScenarioFamily, seed: int) -> AppellateClosedRecord:
    rng = random.Random(seed)
    plaintiff = ("Morgan", "Rivera", "Chen", "Okafor")[rng.randrange(4)]
    store = f"Store {100 + rng.randrange(900)}"
    common = (
        (
            "jurisdiction",
            f"{plaintiff}, a Virginia citizen, sought $150,000 from {store}, a Delaware citizen; "
            "the district court exercised diversity jurisdiction, entered final judgment on "
            f"January 12, 2026, and {plaintiff} filed a notice of appeal on February 2, 2026.",
        ),
        (
            "incident",
            f"{plaintiff} slipped on a clear liquid in an aisle at {store} and sustained "
            "an injury.",
        ),
        (
            "witness",
            "A customer testified that an employee stood beside the liquid twelve minutes before "
            "the fall, while the shift manager denied that any employee had prior notice.",
        ),
    )
    if family == AppellateScenarioFamily.CONCLUSIVE_VIDEO:
        variant = (
            "video",
            "The unobstructed, time-stamped video shows that no employee entered the aisle during "
            "the twenty minutes before the fall.",
        )
        preservation = (
            "preservation",
            "At summary judgment, the plaintiff cited the customer's deposition and challenged "
            "whether the video eliminated every reasonable inference of notice.",
        )
    elif family == AppellateScenarioFamily.PRESERVATION_TRANSFER:
        variant = (
            "video",
            "The grainy video is partially obstructed and does not conclusively show whether an "
            "employee entered the aisle before the fall.",
        )
        preservation = (
            "preservation",
            "The plaintiff expressly argued in the summary-judgment opposition that weighing the "
            "customer's testimony against the video would invade the jury's role.",
        )
    else:
        variant = (
            "video",
            "The grainy video is partially obstructed and does not conclusively show whether an "
            "employee entered the aisle before the fall.",
        )
        preservation = (
            "preservation",
            "At summary judgment, the plaintiff cited the customer's deposition and argued that "
            "the conflicting testimony created a jury question.",
        )
    district = (
        "district-ruling",
        "The district court granted summary judgment after discounting the customer's testimony "
        "and treating the video as resolving the notice dispute.",
    )
    fact_specs = (*common, variant, preservation, district)
    pages = tuple(
        AppellateRecordPage(
            page_id=f"record-page-{index}",
            appendix_page=index,
            text=statement,
            content_digest=sha256_digest(statement),
        )
        for index, (_fact_id, statement) in enumerate(fact_specs, start=1)
    )
    document = AppellateRecordDocument(
        document_id="joint-appendix",
        title="Synthetic Joint Appendix",
        pages=pages,
    )
    facts = tuple(
        AppellateRecordFact(
            fact_id=fact_id,
            statement=statement,
            page_ids=(f"record-page-{index}",),
        )
        for index, (fact_id, statement) in enumerate(fact_specs, start=1)
    )
    payload = {
        "record_id": f"synthetic-record-{family.value}-{seed}",
        "source_kind": "synthetic",
        "documents": (document,),
        "facts": facts,
    }
    return AppellateClosedRecord.model_validate(
        {**payload, "record_digest": sha256_digest(payload)}, strict=True
    )


def _render_prompt(*, pack: AppellateCourtPack, scenario: AppellateScenarioManifest) -> str:
    pack_payload = pack.model_dump(mode="json")
    return (
        "Draft the appellant's principal brief only from the closed materials below. Return an "
        "AppellateSubmission JSON object: the ordered brief plus a complete claim/citation map. "
        "Treat an authority outside the declared corpus as unresolved, never as automatically "
        "fabricated. Do not claim that any authority is good law; this pack has no citator. "
        "Substantive paragraph text must be exactly the joined text of its mapped claims. Record "
        "assertions must use the supplied canonical record facts.\n\n"
        + json.dumps(
            {
                "court_pack": pack_payload,
                "scenario": scenario.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    )


def _competency_id(family: AppellateScenarioFamily) -> str:
    return f"appellate.{family.value}"


def _appellate_internal_rights(*, reviewed_at: datetime) -> SourceRights:
    uses = tuple(
        sorted(
            (
                RightsUse.CONTINUED_PRETRAINING,
                RightsUse.EVALUATION,
                RightsUse.EVIDENCE_RETENTION,
                RightsUse.INTERNAL_RESEARCH,
                RightsUse.PREFERENCE,
                RightsUse.PROCESS,
                RightsUse.RLVR,
                RightsUse.SFT,
            ),
            key=lambda use: use.value,
        )
    )
    return SourceRights(
        rights_id="padawan.appellate.synthetic-federal.internal",
        version="1.0.0",
        basis=RightsBasis.PROJECT_AUTHORED,
        basis_detail=(
            "Project-authored synthetic records and task compilations with attributed official "
            "federal excerpts and task-scoped rule renderings for internal research."
        ),
        permitted_uses=uses,
        distribution_scope=DistributionScope.INTERNAL_ONLY,
        review_status=RightsReviewStatus.CONFIRMED,
        restrictions=(
            "internal research only",
            "official-source attribution and source digests must be retained",
        ),
        reviewed_by="padawan.appellate-source-policy",
        reviewed_at=reviewed_at,
    )
