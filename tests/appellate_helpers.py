from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import VerifierDisposition
from padawan.domains.legal.appellate import (
    AppellateAdverseAuthorityAssessment,
    AppellateBriefSection,
    AppellateBriefType,
    AppellateCitation,
    AppellateCitationKind,
    AppellateClaim,
    AppellateClaimAssessment,
    AppellateClaimKind,
    AppellateComplianceCertificate,
    AppellateCoverageAssessment,
    AppellateParagraph,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSubmission,
    BriefSectionKind,
    SemanticAssessmentMethod,
    counted_words,
)
from padawan.models.contracts import ResearchRole
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 2, tzinfo=UTC)


def build_submission(
    scenario: AppellateScenarioManifest,
    *,
    research_role: ResearchRole = ResearchRole.TARGET,
) -> AppellateSubmission:
    facts = {fact.fact_id: fact for fact in scenario.record.facts}
    claims = (
        AppellateClaim(
            claim_id="claim-jurisdiction",
            kind=AppellateClaimKind.JURISDICTION,
            text=facts["jurisdiction"].statement,
            citation_ids=(
                "cite-jurisdiction",
                "cite-appellate-jurisdiction",
                "cite-diversity-jurisdiction",
            ),
            record_fact_ids=("jurisdiction",),
        ),
        AppellateClaim(
            claim_id="claim-issue",
            kind=AppellateClaimKind.ISSUE,
            text=scenario.issue,
            citation_ids=(),
        ),
        AppellateClaim(
            claim_id="claim-incident",
            kind=AppellateClaimKind.RECORD_FACT,
            text=facts["incident"].statement,
            citation_ids=("cite-incident",),
            record_fact_ids=("incident",),
        ),
        AppellateClaim(
            claim_id="claim-witness",
            kind=AppellateClaimKind.RECORD_FACT,
            text=facts["witness"].statement,
            citation_ids=("cite-witness",),
            record_fact_ids=("witness",),
        ),
        AppellateClaim(
            claim_id="claim-video",
            kind=AppellateClaimKind.RECORD_FACT,
            text=facts["video"].statement,
            citation_ids=("cite-video",),
            record_fact_ids=("video",),
        ),
        AppellateClaim(
            claim_id="claim-procedure",
            kind=AppellateClaimKind.PROCEDURAL_HISTORY,
            text=facts["district-ruling"].statement,
            citation_ids=("cite-procedure",),
            record_fact_ids=("district-ruling",),
        ),
        AppellateClaim(
            claim_id="claim-preservation",
            kind=AppellateClaimKind.PRESERVATION,
            text=facts["preservation"].statement,
            citation_ids=("cite-preservation",),
            record_fact_ids=("preservation",),
        ),
        AppellateClaim(
            claim_id="claim-legal-rule",
            kind=AppellateClaimKind.LEGAL_RULE,
            text=(
                "Tolan explains that a judge's function is not “to weigh the evidence and "
                "determine the truth of the matter” and requires a genuine issue to be left for "
                "trial."
            ),
            citation_ids=("cite-tolan",),
        ),
        AppellateClaim(
            claim_id="claim-standard",
            kind=AppellateClaimKind.STANDARD_OF_REVIEW,
            text=(
                "The Court reviews summary judgment de novo, views reasonable inferences for the "
                "nonmovant, and does not decide credibility."
            ),
            citation_ids=("cite-brown-standard",),
        ),
        AppellateClaim(
            claim_id="claim-application",
            kind=AppellateClaimKind.APPLICATION,
            text=(
                "The conflicting witness accounts, inconclusive video, and district court's "
                "choice between them present the kind of jury question identified in Brown."
            ),
            citation_ids=(
                "cite-application-witness",
                "cite-application-video",
                "cite-application-ruling",
                "cite-brown-grainy",
            ),
            record_fact_ids=("witness", "video", "district-ruling"),
            treated_authority_ids=("brown-v-walmart",),
        ),
        AppellateClaim(
            claim_id="claim-counterargument",
            kind=AppellateClaimKind.COUNTERARGUMENT,
            text=(
                "Scott permits rejection of an account only when the record blatantly contradicts "
                "it; this record's video characteristics therefore require explicit treatment of "
                "Scott without assuming its exception applies."
            ),
            citation_ids=("cite-scott", "cite-counter-video"),
            record_fact_ids=("video",),
            treated_authority_ids=("scott-v-harris",),
        ),
        AppellateClaim(
            claim_id="claim-remedy",
            kind=AppellateClaimKind.REMEDY,
            text=scenario.requested_relief,
            citation_ids=("cite-remedy-rule",),
        ),
    )
    citations = (
        _record_citation("cite-jurisdiction", "claim-jurisdiction", 1),
        AppellateCitation(
            citation_id="cite-appellate-jurisdiction",
            claim_id="claim-jurisdiction",
            kind=AppellateCitationKind.RULE,
            source_id="28-usc-1291-final-decisions",
            locator_id="28-usc-1291-final-decisions",
            display="28 U.S.C. § 1291",
        ),
        AppellateCitation(
            citation_id="cite-diversity-jurisdiction",
            claim_id="claim-jurisdiction",
            kind=AppellateCitationKind.RULE,
            source_id="28-usc-1332-diversity",
            locator_id="28-usc-1332-diversity",
            display="28 U.S.C. § 1332(a)",
        ),
        _record_citation("cite-incident", "claim-incident", 2),
        _record_citation("cite-witness", "claim-witness", 3),
        _record_citation("cite-video", "claim-video", 4),
        _record_citation("cite-preservation", "claim-preservation", 5),
        _record_citation("cite-procedure", "claim-procedure", 6),
        AppellateCitation(
            citation_id="cite-tolan",
            claim_id="claim-legal-rule",
            kind=AppellateCitationKind.AUTHORITY,
            source_id="tolan-v-cotton",
            locator_id="tolan-summary-judgment",
            display="Tolan v. Cotton, 572 U.S. 650, 657 (2014)",
            quoted_text="to weigh the evidence and determine the truth of the matter",
        ),
        AppellateCitation(
            citation_id="cite-brown-standard",
            claim_id="claim-standard",
            kind=AppellateCitationKind.AUTHORITY,
            source_id="brown-v-walmart",
            locator_id="brown-standard-of-review",
            display=(
                "Brown v. Wal-Mart Stores East, LP, No. 24-1102, slip op. at 7 "
                "(4th Cir. June 4, 2025)"
            ),
        ),
        _record_citation("cite-application-witness", "claim-application", 3),
        _record_citation("cite-application-video", "claim-application", 4),
        _record_citation("cite-application-ruling", "claim-application", 6),
        AppellateCitation(
            citation_id="cite-brown-grainy",
            claim_id="claim-application",
            kind=AppellateCitationKind.AUTHORITY,
            source_id="brown-v-walmart",
            locator_id="brown-grainy-video",
            display=(
                "Brown v. Wal-Mart Stores East, LP, No. 24-1102, slip op. at 17 "
                "(4th Cir. June 4, 2025)"
            ),
        ),
        AppellateCitation(
            citation_id="cite-scott",
            claim_id="claim-counterargument",
            kind=AppellateCitationKind.AUTHORITY,
            source_id="scott-v-harris",
            locator_id="scott-blatant-contradiction",
            display="Scott v. Harris, 550 U.S. 372, 380 (2007)",
        ),
        _record_citation("cite-counter-video", "claim-counterargument", 4),
        AppellateCitation(
            citation_id="cite-remedy-rule",
            claim_id="claim-remedy",
            kind=AppellateCitationKind.RULE,
            source_id="frap-28-ordered-sections",
            locator_id="frap-28-ordered-sections",
            display="Fed. R. App. P. 28(a)",
        ),
    )
    claim_by_id = {claim.claim_id: claim for claim in claims}
    headings = {
        BriefSectionKind.COVER: "Cover",
        BriefSectionKind.TABLE_OF_CONTENTS: "Table of Contents",
        BriefSectionKind.TABLE_OF_AUTHORITIES: "Table of Authorities",
        BriefSectionKind.JURISDICTIONAL_STATEMENT: "Jurisdictional Statement",
        BriefSectionKind.ISSUES_PRESENTED: "Issues Presented",
        BriefSectionKind.STATEMENT_OF_CASE: "Statement of Case",
        BriefSectionKind.SUMMARY_OF_ARGUMENT: "Summary of Argument",
        BriefSectionKind.ARGUMENT: "Argument",
        BriefSectionKind.CONCLUSION: "Conclusion",
        BriefSectionKind.CERTIFICATE_OF_COMPLIANCE: "Certificate of Compliance",
        BriefSectionKind.CERTIFICATE_OF_SERVICE: "Certificate of Service",
    }
    toc_text = "; ".join(
        headings[kind]
        for kind in scenario.required_sections
        if kind not in {BriefSectionKind.COVER, BriefSectionKind.TABLE_OF_CONTENTS}
    )
    toa_text = "; ".join(
        sorted(
            {
                citation.display
                for citation in citations
                if citation.kind in {AppellateCitationKind.AUTHORITY, AppellateCitationKind.RULE}
            }
        )
    )
    section_claims = {
        BriefSectionKind.JURISDICTIONAL_STATEMENT: ("claim-jurisdiction",),
        BriefSectionKind.ISSUES_PRESENTED: ("claim-issue",),
        BriefSectionKind.STATEMENT_OF_CASE: (
            "claim-incident",
            "claim-witness",
            "claim-video",
            "claim-procedure",
            "claim-preservation",
        ),
        BriefSectionKind.SUMMARY_OF_ARGUMENT: ("claim-legal-rule",),
        BriefSectionKind.ARGUMENT: (
            "claim-standard",
            "claim-application",
            "claim-counterargument",
        ),
        BriefSectionKind.CONCLUSION: ("claim-remedy",),
    }
    structural_text = {
        BriefSectionKind.COVER: "No. 26-1000 — Brief of Appellant",
        BriefSectionKind.TABLE_OF_CONTENTS: toc_text,
        BriefSectionKind.TABLE_OF_AUTHORITIES: toa_text,
        BriefSectionKind.CERTIFICATE_OF_COMPLIANCE: "Certificate reports the counted words.",
        BriefSectionKind.CERTIFICATE_OF_SERVICE: "Service is certified.",
    }
    sections: list[AppellateBriefSection] = []
    for section_kind in scenario.required_sections:
        claim_ids = section_claims.get(section_kind, ())
        if claim_ids:
            paragraphs = tuple(
                AppellateParagraph(
                    paragraph_id=f"paragraph-{claim_id}",
                    text=claim_by_id[claim_id].text,
                    claim_ids=(claim_id,),
                )
                for claim_id in claim_ids
            )
        else:
            paragraphs = (
                AppellateParagraph(
                    paragraph_id=f"paragraph-{section_kind.value}",
                    text=structural_text[section_kind],
                ),
            )
        sections.append(
            AppellateBriefSection(
                kind=section_kind,
                heading=headings[section_kind],
                paragraphs=paragraphs,
            )
        )
    certificate = AppellateComplianceCertificate(
        brief_id="brief-reference",
        counted_words=0,
        typeface_points=14,
        uses_word_count_limit=True,
        service_certified=True,
    )
    initial = AppellateSubmission(
        brief_id="brief-reference",
        scenario_id=scenario.scenario_id,
        court_pack_digest=scenario.court_pack_digest,
        brief_type=AppellateBriefType.APPELLANT_PRINCIPAL,
        research_role=research_role,
        sections=tuple(sections),
        claims=claims,
        citations=citations,
        compliance_certificate=certificate,
        created_at=NOW,
    )
    return initial.model_copy(
        update={
            "compliance_certificate": certificate.model_copy(
                update={"counted_words": counted_words(initial)}
            )
        }
    )


def build_semantic_assessment(
    scenario: AppellateScenarioManifest,
    submission: AppellateSubmission,
) -> AppellateSemanticAssessment:
    legal_claims = {
        claim.claim_id: claim
        for claim in submission.claims
        if claim.kind
        in {
            AppellateClaimKind.LEGAL_RULE,
            AppellateClaimKind.STANDARD_OF_REVIEW,
            AppellateClaimKind.APPLICATION,
            AppellateClaimKind.COUNTERARGUMENT,
            AppellateClaimKind.REMEDY,
        }
    }
    evidence = {
        "claim-legal-rule": (("tolan-summary-judgment",), (), ()),
        "claim-standard": (("brown-standard-of-review",), (), ()),
        "claim-application": (
            ("brown-grainy-video",),
            (),
            ("witness", "video", "district-ruling"),
        ),
        "claim-counterargument": (
            ("scott-blatant-contradiction",),
            (),
            ("video",),
        ),
        "claim-remedy": ((), ("frap-28-ordered-sections",), ()),
    }
    claim_assessments = tuple(
        AppellateClaimAssessment(
            claim_id=claim_id,
            proposition=claim.text,
            support=VerifierDisposition.VERIFIED,
            applicability=VerifierDisposition.VERIFIED,
            authority_passage_ids=evidence[claim_id][0],
            rule_ids=evidence[claim_id][1],
            record_fact_ids=evidence[claim_id][2],
            rationale="The cited passage and declared record facts directly address this claim.",
        )
        for claim_id, claim in legal_claims.items()
    )
    return AppellateSemanticAssessment(
        assessment_id="appellate-assessment-reference",
        scenario_id=scenario.scenario_id,
        submission_digest=sha256_digest(submission.model_dump(mode="json")),
        adjudicator_id="human-test-adjudicator",
        method=SemanticAssessmentMethod.HUMAN,
        claim_assessments=claim_assessments,
        adverse_authority_assessments=(
            AppellateAdverseAuthorityAssessment(
                authority_id="scott-v-harris",
                treatment_claim_id="claim-counterargument",
                disposition=VerifierDisposition.VERIFIED,
                authority_passage_ids=("scott-blatant-contradiction",),
                record_fact_ids=("video",),
                rationale="The treatment states Scott's exception and applies the video record.",
            ),
        ),
        issue_coverage=AppellateCoverageAssessment(
            disposition=VerifierDisposition.VERIFIED,
            claim_ids=("claim-issue",),
            rationale="The issue matches the challenged summary-judgment ruling.",
        ),
        preservation_coverage=AppellateCoverageAssessment(
            disposition=VerifierDisposition.VERIFIED,
            claim_ids=("claim-preservation",),
            rationale="The brief identifies where the issue was preserved below.",
        ),
        remedy_coverage=AppellateCoverageAssessment(
            disposition=VerifierDisposition.VERIFIED,
            claim_ids=("claim-remedy",),
            rationale="The conclusion requests precise appellate relief.",
        ),
        created_at=NOW,
    )


def _record_citation(citation_id: str, claim_id: str, page: int) -> AppellateCitation:
    return AppellateCitation(
        citation_id=citation_id,
        claim_id=claim_id,
        kind=AppellateCitationKind.RECORD,
        source_id="joint-appendix",
        locator_id=f"record-page-{page}",
        display=f"JA {page}",
    )
