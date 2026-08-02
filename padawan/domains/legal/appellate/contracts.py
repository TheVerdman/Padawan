from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from padawan.domains.contracts import (
    HardGateResult,
    RewardObservation,
    VerifierDisposition,
    VerifierResult,
)
from padawan.models.contracts import NonEmpty, ResearchRole, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


class AppellateBriefType(StrEnum):
    APPELLANT_PRINCIPAL = "appellant_principal"


class AppellateScenarioFamily(StrEnum):
    AMBIGUOUS_VIDEO = "ambiguous_video"
    CONCLUSIVE_VIDEO = "conclusive_video"
    PRESERVATION_TRANSFER = "preservation_transfer"


class BriefSectionKind(StrEnum):
    COVER = "cover"
    TABLE_OF_CONTENTS = "table_of_contents"
    TABLE_OF_AUTHORITIES = "table_of_authorities"
    JURISDICTIONAL_STATEMENT = "jurisdictional_statement"
    ISSUES_PRESENTED = "issues_presented"
    STATEMENT_OF_CASE = "statement_of_case"
    SUMMARY_OF_ARGUMENT = "summary_of_argument"
    ARGUMENT = "argument"
    CONCLUSION = "conclusion"
    CERTIFICATE_OF_COMPLIANCE = "certificate_of_compliance"
    CERTIFICATE_OF_SERVICE = "certificate_of_service"


STRUCTURAL_SECTIONS = frozenset(
    {
        BriefSectionKind.COVER,
        BriefSectionKind.TABLE_OF_CONTENTS,
        BriefSectionKind.TABLE_OF_AUTHORITIES,
        BriefSectionKind.CERTIFICATE_OF_COMPLIANCE,
        BriefSectionKind.CERTIFICATE_OF_SERVICE,
    }
)


class AppellateClaimKind(StrEnum):
    JURISDICTION = "jurisdiction"
    ISSUE = "issue"
    RECORD_FACT = "record_fact"
    PROCEDURAL_HISTORY = "procedural_history"
    PRESERVATION = "preservation"
    LEGAL_RULE = "legal_rule"
    STANDARD_OF_REVIEW = "standard_of_review"
    APPLICATION = "application"
    COUNTERARGUMENT = "counterargument"
    REMEDY = "remedy"


class AppellateCitationKind(StrEnum):
    RECORD = "record"
    AUTHORITY = "authority"
    RULE = "rule"


class AuthorityWeight(StrEnum):
    SUPREME_COURT = "supreme_court"
    CONTROLLING_CIRCUIT = "controlling_circuit"
    PERSUASIVE = "persuasive"


class CurrentnessStatus(StrEnum):
    GOOD_LAW = "good_law"
    NEGATIVE_TREATMENT = "negative_treatment"
    UNKNOWN = "unknown"


class SemanticAssessmentMethod(StrEnum):
    HUMAN = "human"
    GOVERNED_MODEL = "governed_model"
    HYBRID = "hybrid"


class AppellateSourceSnapshot(StrictRecord):
    source_id: NonEmpty
    title: NonEmpty
    source_uri: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    observed_on: date
    effective_on: date | None = None
    content_digest: Sha256


class AppellateRule(StrictRecord):
    rule_id: NonEmpty
    source_id: NonEmpty
    citation: NonEmpty
    pinpoint: NonEmpty
    text: NonEmpty
    verbatim: bool
    content_digest: Sha256

    @model_validator(mode="after")
    def digest_matches_text(self) -> AppellateRule:
        if self.content_digest != sha256_digest(self.text):
            raise ValueError("appellate rule digest differs from its text")
        return self


class AuthorityPassage(StrictRecord):
    passage_id: NonEmpty
    official_page: NonEmpty
    pinpoint_citation: NonEmpty
    text: NonEmpty
    verbatim: Literal[True] = True
    content_digest: Sha256

    @model_validator(mode="after")
    def digest_matches_text(self) -> AuthorityPassage:
        if self.content_digest != sha256_digest(self.text):
            raise ValueError("authority passage digest differs from its text")
        return self


class AuthorityDocument(StrictRecord):
    authority_id: NonEmpty
    source_id: NonEmpty
    case_name: NonEmpty
    canonical_citation: NonEmpty
    docket_number: NonEmpty
    court: NonEmpty
    decided_on: date
    published: bool
    weight: AuthorityWeight
    passages: Annotated[tuple[AuthorityPassage, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def passages_are_unique(self) -> AuthorityDocument:
        passage_ids = [passage.passage_id for passage in self.passages]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError("authority passage IDs must be unique")
        return self


class AuthorityCoverageDeclaration(StrictRecord):
    description: NonEmpty
    task_complete: bool
    globally_complete: bool
    dependable_citator_source_ids: tuple[NonEmpty, ...] = ()
    absence_interpretation: NonEmpty


class AppellateCourtPack(StrictRecord):
    pack_id: NonEmpty
    version: NonEmpty
    court: NonEmpty
    jurisdiction: NonEmpty
    governing_law_cutoff: date
    national_rules_effective_on: date
    local_rules_edition_on: date
    sources: Annotated[tuple[AppellateSourceSnapshot, ...], Field(min_length=1)]
    rules: Annotated[tuple[AppellateRule, ...], Field(min_length=1)]
    authorities: Annotated[tuple[AuthorityDocument, ...], Field(min_length=1)]
    coverage: AuthorityCoverageDeclaration
    sealed_sentence_digests: tuple[Sha256, ...] = ()
    pack_digest: Sha256

    @model_validator(mode="after")
    def manifest_is_content_addressed(self) -> AppellateCourtPack:
        source_ids = [source.source_id for source in self.sources]
        rule_ids = [rule.rule_id for rule in self.rules]
        authority_ids = [authority.authority_id for authority in self.authorities]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("appellate source IDs must be unique")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("appellate rule IDs must be unique")
        if len(authority_ids) != len(set(authority_ids)):
            raise ValueError("appellate authority IDs must be unique")
        passage_ids = [
            passage.passage_id for authority in self.authorities for passage in authority.passages
        ]
        if len(passage_ids) != len(set(passage_ids)):
            raise ValueError("authority passage IDs must be globally unique within a court pack")
        if len(self.sealed_sentence_digests) != len(set(self.sealed_sentence_digests)):
            raise ValueError("sealed appellate sentence digests must be unique")
        known_sources = set(source_ids)
        if any(rule.source_id not in known_sources for rule in self.rules):
            raise ValueError("appellate rule cites an unknown source snapshot")
        if any(authority.source_id not in known_sources for authority in self.authorities):
            raise ValueError("appellate authority cites an unknown source snapshot")
        if not set(self.coverage.dependable_citator_source_ids).issubset(known_sources):
            raise ValueError("currentness coverage cites an unknown source snapshot")
        if len(self.coverage.dependable_citator_source_ids) != len(
            set(self.coverage.dependable_citator_source_ids)
        ):
            raise ValueError("dependable citator source IDs must be unique")
        if any(
            source.effective_on is not None and source.effective_on > source.observed_on
            for source in self.sources
        ):
            raise ValueError("appellate source effective date follows its observation date")
        source_by_id = {source.source_id: source for source in self.sources}
        for source_id, source in source_by_id.items():
            expected_content_digest = sha256_digest(
                {
                    "rules": [
                        rule.model_dump(mode="json")
                        for rule in self.rules
                        if rule.source_id == source_id
                    ],
                    "authorities": [
                        authority.model_dump(mode="json")
                        for authority in self.authorities
                        if authority.source_id == source_id
                    ],
                }
            )
            if source.content_digest != expected_content_digest:
                raise ValueError("appellate source digest differs from its admitted content")
        if self.pack_digest != sha256_digest(self.model_dump(mode="json", exclude={"pack_digest"})):
            raise ValueError("appellate court-pack digest is not reproducible")
        return self


class AppellateRecordPage(StrictRecord):
    page_id: NonEmpty
    appendix_page: Annotated[int, Field(ge=1)]
    text: NonEmpty
    content_digest: Sha256

    @model_validator(mode="after")
    def digest_matches_text(self) -> AppellateRecordPage:
        if self.content_digest != sha256_digest(self.text):
            raise ValueError("record-page digest differs from its text")
        return self


class AppellateRecordDocument(StrictRecord):
    document_id: NonEmpty
    title: NonEmpty
    pages: Annotated[tuple[AppellateRecordPage, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def pages_are_unique(self) -> AppellateRecordDocument:
        page_ids = [page.page_id for page in self.pages]
        appendix_pages = [page.appendix_page for page in self.pages]
        if len(page_ids) != len(set(page_ids)) or len(appendix_pages) != len(set(appendix_pages)):
            raise ValueError("record document pages must have unique IDs and appendix numbers")
        return self


class AppellateRecordFact(StrictRecord):
    fact_id: NonEmpty
    statement: NonEmpty
    page_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def pages_are_unique(self) -> AppellateRecordFact:
        if len(self.page_ids) != len(set(self.page_ids)):
            raise ValueError("record fact page IDs must be unique")
        return self


class AppellateClosedRecord(StrictRecord):
    record_id: NonEmpty
    source_kind: Literal["synthetic"] = "synthetic"
    documents: Annotated[tuple[AppellateRecordDocument, ...], Field(min_length=1)]
    facts: Annotated[tuple[AppellateRecordFact, ...], Field(min_length=1)]
    record_digest: Sha256

    @model_validator(mode="after")
    def record_is_content_addressed(self) -> AppellateClosedRecord:
        document_ids = [document.document_id for document in self.documents]
        page_ids = [page.page_id for document in self.documents for page in document.pages]
        appendix_pages = [
            page.appendix_page for document in self.documents for page in document.pages
        ]
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("record document IDs must be unique")
        if len(page_ids) != len(set(page_ids)) or len(appendix_pages) != len(set(appendix_pages)):
            raise ValueError("closed-record pages must be globally unique")
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("record fact IDs must be unique")
        known_pages = set(page_ids)
        if any(not set(fact.page_ids).issubset(known_pages) for fact in self.facts):
            raise ValueError("record fact cites an unknown page")
        if self.record_digest != sha256_digest(
            self.model_dump(mode="json", exclude={"record_digest"})
        ):
            raise ValueError("closed-record digest is not reproducible")
        return self


class AppellateScenarioManifest(StrictRecord):
    scenario_id: NonEmpty
    version: NonEmpty
    family: AppellateScenarioFamily
    competency_id: NonEmpty
    split: NonEmpty
    seed: int
    court_pack_id: NonEmpty
    court_pack_digest: Sha256
    brief_type: AppellateBriefType
    procedural_posture: NonEmpty
    issue: NonEmpty
    requested_relief: NonEmpty
    exercise_word_limit: Annotated[int, Field(ge=100, le=13_000)]
    required_sections: Annotated[tuple[BriefSectionKind, ...], Field(min_length=1)]
    required_claim_kinds: Annotated[tuple[AppellateClaimKind, ...], Field(min_length=1)]
    required_adverse_authority_ids: tuple[NonEmpty, ...]
    record: AppellateClosedRecord
    restricted_sentence_digests: tuple[Sha256, ...] = ()
    environment_fingerprint: Sha256
    scenario_digest: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def scenario_is_content_addressed(self) -> AppellateScenarioManifest:
        if len(self.required_sections) != len(set(self.required_sections)):
            raise ValueError("required appellate sections must be unique")
        if len(self.required_claim_kinds) != len(set(self.required_claim_kinds)):
            raise ValueError("required appellate claim kinds must be unique")
        expected_environment = sha256_digest(
            {
                "court_pack_digest": self.court_pack_digest,
                "record_digest": self.record.record_digest,
                "brief_type": self.brief_type,
                "procedural_posture": self.procedural_posture,
                "split": self.split,
            }
        )
        if self.environment_fingerprint != expected_environment:
            raise ValueError("appellate environment fingerprint is not reproducible")
        if self.scenario_digest != sha256_digest(
            self.model_dump(mode="json", exclude={"scenario_id", "scenario_digest"})
        ):
            raise ValueError("appellate scenario digest is not reproducible")
        expected_id = f"appellate-scenario-{self.scenario_digest[7:23]}"
        if self.scenario_id != expected_id:
            raise ValueError("appellate scenario ID differs from its digest")
        return self


class AppellateCitation(StrictRecord):
    citation_id: NonEmpty
    claim_id: NonEmpty
    kind: AppellateCitationKind
    source_id: NonEmpty
    locator_id: NonEmpty
    display: NonEmpty
    quoted_text: NonEmpty | None = None


class AppellateClaim(StrictRecord):
    claim_id: NonEmpty
    kind: AppellateClaimKind
    text: NonEmpty
    citation_ids: tuple[NonEmpty, ...]
    record_fact_ids: tuple[NonEmpty, ...] = ()
    treated_authority_ids: tuple[NonEmpty, ...] = ()

    @model_validator(mode="after")
    def references_are_unique(self) -> AppellateClaim:
        for values, label in (
            (self.citation_ids, "citation"),
            (self.record_fact_ids, "record fact"),
            (self.treated_authority_ids, "treated authority"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"appellate claim {label} IDs must be unique")
        return self


class AppellateParagraph(StrictRecord):
    paragraph_id: NonEmpty
    text: NonEmpty
    claim_ids: tuple[NonEmpty, ...] = ()


class AppellateBriefSection(StrictRecord):
    kind: BriefSectionKind
    heading: NonEmpty
    paragraphs: Annotated[tuple[AppellateParagraph, ...], Field(min_length=1)]


class AppellateComplianceCertificate(StrictRecord):
    brief_id: NonEmpty
    counted_words: Annotated[int, Field(ge=0)]
    typeface_points: Annotated[int, Field(ge=14)]
    uses_word_count_limit: bool
    service_certified: bool


class AppellateSubmission(StrictRecord):
    brief_id: NonEmpty
    scenario_id: NonEmpty
    court_pack_digest: Sha256
    brief_type: AppellateBriefType
    research_role: ResearchRole
    sections: Annotated[tuple[AppellateBriefSection, ...], Field(min_length=1)]
    claims: Annotated[tuple[AppellateClaim, ...], Field(min_length=1)]
    citations: tuple[AppellateCitation, ...]
    compliance_certificate: AppellateComplianceCertificate
    created_at: datetime

    @model_validator(mode="after")
    def claim_map_is_structurally_complete(self) -> AppellateSubmission:
        section_kinds = [section.kind for section in self.sections]
        claim_ids = [claim.claim_id for claim in self.claims]
        citation_ids = [citation.citation_id for citation in self.citations]
        paragraph_ids = [
            paragraph.paragraph_id for section in self.sections for paragraph in section.paragraphs
        ]
        for values, label in (
            (section_kinds, "section kinds"),
            (claim_ids, "claim IDs"),
            (citation_ids, "citation IDs"),
            (paragraph_ids, "paragraph IDs"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"appellate submission {label} must be unique")
        claim_by_id = {claim.claim_id: claim for claim in self.claims}
        citation_by_id = {citation.citation_id: citation for citation in self.citations}
        unknown_claims = {
            claim_id
            for section in self.sections
            for paragraph in section.paragraphs
            for claim_id in paragraph.claim_ids
            if claim_id not in claim_by_id
        }
        if unknown_claims:
            raise ValueError("appellate paragraph cites an unknown claim")
        unknown_citations = {
            citation_id
            for claim in self.claims
            for citation_id in claim.citation_ids
            if citation_id not in citation_by_id
        }
        if unknown_citations:
            raise ValueError("appellate claim cites an unknown citation")
        if any(citation.claim_id not in claim_by_id for citation in self.citations):
            raise ValueError("appellate citation cites an unknown claim")
        for claim in self.claims:
            attached = tuple(
                citation.citation_id
                for citation in self.citations
                if citation.claim_id == claim.claim_id
            )
            if set(attached) != set(claim.citation_ids):
                raise ValueError("appellate claim and citation map disagree")
        referenced_claims: list[str] = []
        for section in self.sections:
            for paragraph in section.paragraphs:
                if section.kind in STRUCTURAL_SECTIONS and paragraph.claim_ids:
                    raise ValueError("structural appellate sections cannot contain mapped claims")
                if section.kind not in STRUCTURAL_SECTIONS and not paragraph.claim_ids:
                    raise ValueError("substantive appellate paragraphs require mapped claims")
                if paragraph.claim_ids:
                    canonical_text = " ".join(
                        claim_by_id[claim_id].text for claim_id in paragraph.claim_ids
                    )
                    if paragraph.text != canonical_text:
                        raise ValueError("appellate paragraph text differs from its claim map")
                    referenced_claims.extend(paragraph.claim_ids)
        if sorted(referenced_claims) != sorted(claim_ids) or len(referenced_claims) != len(
            claim_ids
        ):
            raise ValueError("every appellate claim must appear in exactly one paragraph")
        if self.compliance_certificate.brief_id != self.brief_id:
            raise ValueError("compliance certificate belongs to another brief")
        return self


class AppellateClaimAssessment(StrictRecord):
    claim_id: NonEmpty
    proposition: NonEmpty
    support: VerifierDisposition
    applicability: VerifierDisposition
    authority_passage_ids: tuple[NonEmpty, ...] = ()
    rule_ids: tuple[NonEmpty, ...] = ()
    record_fact_ids: tuple[NonEmpty, ...] = ()
    rationale: NonEmpty

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> AppellateClaimAssessment:
        for values in (
            self.authority_passage_ids,
            self.rule_ids,
            self.record_fact_ids,
        ):
            if len(values) != len(set(values)):
                raise ValueError("semantic claim evidence IDs must be unique")
        if VerifierDisposition.INFRASTRUCTURE_FAILURE in {self.support, self.applicability}:
            raise ValueError("semantic legal dispositions cannot encode infrastructure failure")
        return self


class AppellateAdverseAuthorityAssessment(StrictRecord):
    authority_id: NonEmpty
    treatment_claim_id: NonEmpty
    disposition: VerifierDisposition
    authority_passage_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    record_fact_ids: tuple[NonEmpty, ...] = ()
    rationale: NonEmpty

    @model_validator(mode="after")
    def evidence_is_canonical(self) -> AppellateAdverseAuthorityAssessment:
        if len(self.authority_passage_ids) != len(set(self.authority_passage_ids)) or len(
            self.record_fact_ids
        ) != len(set(self.record_fact_ids)):
            raise ValueError("adverse-authority evidence IDs must be unique")
        if self.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE:
            raise ValueError("adverse-authority disposition cannot encode infrastructure failure")
        return self


class AppellateCoverageAssessment(StrictRecord):
    disposition: VerifierDisposition
    claim_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    rationale: NonEmpty

    @model_validator(mode="after")
    def claims_are_unique(self) -> AppellateCoverageAssessment:
        if len(self.claim_ids) != len(set(self.claim_ids)):
            raise ValueError("coverage-assessment claim IDs must be unique")
        if self.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE:
            raise ValueError("coverage disposition cannot encode infrastructure failure")
        return self


class AppellateSemanticAssessment(StrictRecord):
    assessment_id: NonEmpty
    scenario_id: NonEmpty
    submission_digest: Sha256
    adjudicator_id: NonEmpty
    research_role: Literal[ResearchRole.ADJUDICATOR] = ResearchRole.ADJUDICATOR
    method: SemanticAssessmentMethod
    model_id: str | None = None
    claim_assessments: Annotated[tuple[AppellateClaimAssessment, ...], Field(min_length=1)]
    adverse_authority_assessments: tuple[AppellateAdverseAuthorityAssessment, ...]
    issue_coverage: AppellateCoverageAssessment
    preservation_coverage: AppellateCoverageAssessment
    remedy_coverage: AppellateCoverageAssessment
    created_at: datetime

    @model_validator(mode="after")
    def method_has_identity_and_unique_subjects(self) -> AppellateSemanticAssessment:
        if self.method != SemanticAssessmentMethod.HUMAN and not self.model_id:
            raise ValueError("model-assisted appellate assessment requires a model identity")
        claim_ids = [assessment.claim_id for assessment in self.claim_assessments]
        authority_ids = [
            assessment.authority_id for assessment in self.adverse_authority_assessments
        ]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("semantic claim assessments must be unique")
        if len(authority_ids) != len(set(authority_ids)):
            raise ValueError("adverse-authority assessments must be unique")
        return self


class AuthorityCurrentnessAssessment(StrictRecord):
    assessment_id: NonEmpty
    authority_id: NonEmpty
    citator_source_id: NonEmpty
    status: CurrentnessStatus
    as_of: date
    retrieval_digest: Sha256
    source_uri: Annotated[str, Field(pattern=r"^https://[^\s]+$")]
    created_at: datetime


class AppellateVerificationBundle(StrictRecord):
    scenario_id: NonEmpty
    scenario_digest: Sha256
    scenario_split: NonEmpty
    court_pack_digest: Sha256
    brief_id: NonEmpty
    submission_digest: Sha256
    verification_input_digest: Sha256
    verifier_results: Annotated[tuple[VerifierResult, ...], Field(min_length=1)]
    hard_gates: Annotated[tuple[HardGateResult, ...], Field(min_length=1)]
    reward_observations: Annotated[tuple[RewardObservation, ...], Field(min_length=1)]
    task_verified: bool
    created_at: datetime

    @model_validator(mode="after")
    def evidence_graph_is_coherent(self) -> AppellateVerificationBundle:
        result_ids = [result.result_id for result in self.verifier_results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("appellate verifier-result IDs must be unique")
        expected_stages = {
            "pack_integrity",
            "task_binding",
            "rule_compliance",
            "claim_map_integrity",
            "record_resolution",
            "authority_resolution",
            "quotation_fidelity",
            "leakage",
            "proposition_support",
            "applicability",
            "adverse_authority",
            "issue_remedy_coverage",
            "currentness",
        }
        if {result.verifier_id.removeprefix("appellate.") for result in self.verifier_results} != (
            expected_stages
        ):
            raise ValueError("appellate verification bundle has an incomplete verifier set")
        if any(result.scope != self.scenario_id for result in self.verifier_results):
            raise ValueError("appellate verifier result belongs to another scenario")
        if any(
            result.evidence.get("input_digest") != self.verification_input_digest
            for result in self.verifier_results
        ):
            raise ValueError("appellate verifier results do not share the input digest")
        expected_gate_ids = {
            f"appellate:{stage}"
            for stage in (
                "pack_integrity",
                "task_binding",
                "rule_compliance",
                "claim_map_integrity",
                "record_resolution",
                "authority_resolution",
                "quotation_fidelity",
                "leakage",
            )
        }
        if {gate.gate_id for gate in self.hard_gates} != expected_gate_ids:
            raise ValueError("appellate verification bundle has an incomplete hard-gate set")
        if any(not set(gate.evidence_refs).issubset(result_ids) for gate in self.hard_gates):
            raise ValueError("appellate hard gate cites an unknown verifier result")
        result_by_id = {result.result_id: result for result in self.verifier_results}
        for gate in self.hard_gates:
            if len(gate.evidence_refs) != 1:
                raise ValueError("appellate hard gates must cite exactly one verifier result")
            result = result_by_id[gate.evidence_refs[0]]
            expected_verifier_id = gate.gate_id.replace("appellate:", "appellate.", 1)
            if result.verifier_id != expected_verifier_id:
                raise ValueError("appellate hard gate cites the wrong verifier stage")
            if gate.disposition != result.disposition or gate.passed != (
                result.disposition == VerifierDisposition.VERIFIED
            ):
                raise ValueError("appellate hard gate differs from its verifier result")
        component_ids = [observation.component_id for observation in self.reward_observations]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("appellate reward observations must be unique")
        expected_components = {
            "proposition_support",
            "applicability",
            "adverse_authority",
            "issue_remedy_coverage",
            "concision",
            "currentness",
        }
        if set(component_ids) != expected_components:
            raise ValueError("appellate verification bundle has an incomplete reward vector")
        result_by_stage = {
            result.verifier_id.removeprefix("appellate."): result
            for result in self.verifier_results
        }
        expected_task_verified = all(gate.passed for gate in self.hard_gates) and all(
            result_by_stage[stage].disposition == VerifierDisposition.VERIFIED
            for stage in (
                "proposition_support",
                "applicability",
                "adverse_authority",
                "issue_remedy_coverage",
            )
        )
        if self.task_verified != expected_task_verified:
            raise ValueError("appellate task-verification flag differs from its evidence")
        return self
