from __future__ import annotations

from datetime import date

from padawan.domains.legal.appellate.contracts import (
    AppellateCourtPack,
    AppellateRule,
    AppellateSourceSnapshot,
    AuthorityCoverageDeclaration,
    AuthorityDocument,
    AuthorityPassage,
    AuthorityWeight,
)
from padawan.models.hashing import sha256_digest

FOURTH_CIRCUIT_PACK_ID = "fourth-circuit-summary-judgment-2026-03-23"
FOURTH_CIRCUIT_PACK_VERSION = "1.0.0"

# This is a one-way canary. Its preimage is intentionally absent from the runtime package.
_SEALED_SENTENCE_DIGEST = "sha256:98c07ae6c62fbe520d9df1008252e37a773998b86a7ec9aa54c91b6ade38a122"


def build_fourth_circuit_pack() -> AppellateCourtPack:
    """Return the immutable first court pack from official federal sources.

    The pack is task-complete, not globally complete. It deliberately provides no citator
    authority, so downstream currentness must remain unknown.
    """

    rules = (
        _rule(
            "frap-28-ordered-sections",
            "fourth-rules-28",
            "Fed. R. App. P. 28(a)",
            "Rule 28(a)",
            "The appellant's brief must contain, under appropriate headings and in the order "
            "indicated, a table of contents, table of authorities, jurisdictional statement, "
            "issues presented, statement of the case, summary of the argument, argument, a short "
            "conclusion stating the precise relief sought, and a certificate of compliance when "
            "Rule 32(g)(1) requires one.",
        ),
        _rule(
            "frap-28-argument",
            "fourth-rules-28",
            "Fed. R. App. P. 28(a)(8)",
            "Rule 28(a)(8)",
            "The argument must give the appellant's contentions and reasons with citations to "
            "the authorities and parts of the record relied on, and must give a concise statement "
            "of the applicable standard of review for each issue.",
        ),
        _rule(
            "frap-28-record-references",
            "fourth-rules-28",
            "Fed. R. App. P. 28(e)",
            "Rule 28(e)",
            "References to parts of the record contained in the appendix filed with the "
            "appellant's brief must be to the pages of the appendix.",
        ),
        _rule(
            "fourth-local-28-f-record-support",
            "fourth-rules-28",
            "4th Cir. R. 28(f)",
            "Local Rule 28(f)",
            "The statement of the case must include a narrative statement of all facts necessary "
            "for the Court to reach the requested conclusion, with references to the specific "
            "appendix pages supporting each fact.",
        ),
        _rule(
            "frap-32-word-limit",
            "fourth-rules-32",
            "Fed. R. App. P. 32(a)(7)(B)(i)",
            "Rule 32(a)(7)(B)(i)",
            "A principal brief using the type-volume limitation may contain no more than 13,000 "
            "words.",
        ),
        _rule(
            "frap-32-exclusions",
            "fourth-rules-32",
            "Fed. R. App. P. 32(f)",
            "Rule 32(f)",
            "The cover, disclosure statement, tables, statement regarding oral argument, "
            "addendum, certificates, signature block, and proof of service are excluded from the "
            "length computation; headings, footnotes, and quotations count.",
        ),
        _rule(
            "frap-32-certificate",
            "fourth-rules-32",
            "Fed. R. App. P. 32(g)",
            "Rule 32(g)",
            "A brief submitted under the word-count route must include a certificate stating the "
            "number of words in the document.",
        ),
        _rule(
            "fourth-local-32-b-concision",
            "fourth-rules-32",
            "4th Cir. R. 32(b)",
            "Local Rule 32(b)",
            "The Fourth Circuit encourages short, concise briefs and requires advance permission "
            "before a brief exceeds the federal length limitations.",
        ),
        _rule(
            "28-usc-1291-final-decisions",
            "us-code-title-28",
            "28 U.S.C. § 1291",
            "Section 1291",
            "The regional courts of appeals have jurisdiction over appeals from final decisions "
            "of the district courts unless direct Supreme Court review is available.",
        ),
        _rule(
            "28-usc-1332-diversity",
            "us-code-title-28",
            "28 U.S.C. § 1332(a)",
            "Section 1332(a)",
            "District courts have original jurisdiction over qualifying civil actions between "
            "citizens of different states when the amount in controversy exceeds $75,000.",
        ),
    )
    authorities = (
        AuthorityDocument(
            authority_id="tolan-v-cotton",
            source_id="us-reports-572",
            case_name="Tolan v. Cotton",
            canonical_citation="572 U.S. 650 (2014)",
            docket_number="13-551",
            court="Supreme Court of the United States",
            decided_on=date(2014, 5, 5),
            published=True,
            weight=AuthorityWeight.SUPREME_COURT,
            passages=(
                _passage(
                    "tolan-summary-judgment",
                    "657",
                    "Tolan v. Cotton, 572 U.S. 650, 657 (2014)",
                    "“judge's function” at summary judgment is not “to weigh the evidence and "
                    "determine the truth of the matter but to determine whether there is a genuine "
                    "issue for trial.”",
                ),
            ),
        ),
        AuthorityDocument(
            authority_id="scott-v-harris",
            source_id="us-reports-550",
            case_name="Scott v. Harris",
            canonical_citation="550 U.S. 372 (2007)",
            docket_number="05-1631",
            court="Supreme Court of the United States",
            decided_on=date(2007, 4, 30),
            published=True,
            weight=AuthorityWeight.SUPREME_COURT,
            passages=(
                _passage(
                    "scott-blatant-contradiction",
                    "380",
                    "Scott v. Harris, 550 U.S. 372, 380 (2007)",
                    "When opposing parties tell two different stories, one of which is blatantly "
                    "contradicted by the record, so that no reasonable jury could believe it, a "
                    "court should not adopt that version of the facts for purposes of ruling on a "
                    "motion for summary judgment.",
                ),
            ),
        ),
        AuthorityDocument(
            authority_id="brown-v-walmart",
            source_id="brown-fourth-circuit-opinion",
            case_name="Brown v. Wal-Mart Stores East, LP",
            canonical_citation="No. 24-1102 (4th Cir. June 4, 2025)",
            docket_number="24-1102",
            court="United States Court of Appeals for the Fourth Circuit",
            decided_on=date(2025, 6, 4),
            published=True,
            weight=AuthorityWeight.CONTROLLING_CIRCUIT,
            passages=(
                _passage(
                    "brown-standard-of-review",
                    "7",
                    "Brown v. Wal-Mart Stores East, LP, No. 24-1102, slip op. at 7 "
                    "(4th Cir. June 4, 2025)",
                    "“[W]e view the evidence in the light most favorable to the plaintiff; we draw "
                    "all reasonable inferences in [her] favor; and we do not weigh the evidence or "
                    "make credibility calls, even if we do not believe [s]he will win at trial.”",
                ),
                _passage(
                    "brown-grainy-video",
                    "17",
                    "Brown v. Wal-Mart Stores East, LP, No. 24-1102, slip op. at 17 "
                    "(4th Cir. June 4, 2025)",
                    "The record before us is scant, the video grainy, and the testimony "
                    "conflicting. A reasonable jury could find for either party if given the "
                    "chance. So it must be given the chance.",
                ),
            ),
        ),
    )
    source_specs = (
        (
            "fourth-rules-28",
            "Fourth Circuit Federal and Local Rule 28",
            "https://www.ca4.uscourts.gov/rules/rule28.html",
            date(2026, 8, 2),
            None,
        ),
        (
            "fourth-rules-32",
            "Fourth Circuit Federal and Local Rule 32",
            "https://www.ca4.uscourts.gov/rules/Rule32.html",
            date(2026, 8, 2),
            None,
        ),
        (
            "us-reports-572",
            "Official U.S. Reports, Volume 572",
            "https://www.supremecourt.gov/opinions/boundvolumes/572bv.pdf",
            date(2026, 8, 2),
            None,
        ),
        (
            "us-reports-550",
            "Official U.S. Reports, Volume 550",
            "https://www.supremecourt.gov/opinions/boundvolumes/550bv.pdf",
            date(2026, 8, 2),
            None,
        ),
        (
            "brown-fourth-circuit-opinion",
            "Published Fourth Circuit opinion in Brown v. Wal-Mart Stores East, LP",
            "https://www.ca4.uscourts.gov/opinions/241102.P.pdf",
            date(2026, 8, 2),
            None,
        ),
        (
            "us-code-title-28",
            "GovInfo 2024 United States Code, Title 28",
            "https://www.govinfo.gov/content/pkg/USCODE-2024-title28/pdf/USCODE-2024-title28.pdf",
            date(2026, 8, 2),
            None,
        ),
    )
    sources = tuple(
        AppellateSourceSnapshot(
            source_id=source_id,
            title=title,
            source_uri=uri,
            observed_on=observed_on,
            effective_on=effective_on,
            content_digest=_source_content_digest(
                source_id=source_id,
                rules=rules,
                authorities=authorities,
            ),
        )
        for source_id, title, uri, observed_on, effective_on in source_specs
    )
    payload = {
        "pack_id": FOURTH_CIRCUIT_PACK_ID,
        "version": FOURTH_CIRCUIT_PACK_VERSION,
        "court": "United States Court of Appeals for the Fourth Circuit",
        "jurisdiction": "Federal appellate jurisdiction; synthetic civil appeal",
        "governing_law_cutoff": date(2026, 3, 23),
        "national_rules_effective_on": date(2025, 12, 1),
        "local_rules_edition_on": date(2026, 3, 23),
        "sources": sources,
        "rules": rules,
        "authorities": authorities,
        "coverage": AuthorityCoverageDeclaration(
            description=(
                "Closed task corpus containing the listed rules and three official opinions; it "
                "is sufficient for this exercise but is not a comprehensive authority database."
            ),
            task_complete=True,
            globally_complete=False,
            dependable_citator_source_ids=(),
            absence_interpretation=(
                "An authority missing from this closed corpus is unresolved and out of scope, not "
                "proven fabricated. Good-law status is unknown without a dependable citator."
            ),
        ),
        "sealed_sentence_digests": (_SEALED_SENTENCE_DIGEST,),
    }
    return AppellateCourtPack.model_validate(
        {**payload, "pack_digest": sha256_digest(payload)}, strict=True
    )


def _rule(
    rule_id: str,
    source_id: str,
    citation: str,
    pinpoint: str,
    text: str,
    *,
    verbatim: bool = False,
) -> AppellateRule:
    return AppellateRule(
        rule_id=rule_id,
        source_id=source_id,
        citation=citation,
        pinpoint=pinpoint,
        text=text,
        verbatim=verbatim,
        content_digest=sha256_digest(text),
    )


def _passage(
    passage_id: str,
    official_page: str,
    pinpoint_citation: str,
    text: str,
) -> AuthorityPassage:
    return AuthorityPassage(
        passage_id=passage_id,
        official_page=official_page,
        pinpoint_citation=pinpoint_citation,
        text=text,
        content_digest=sha256_digest(text),
    )


def _source_content_digest(
    *,
    source_id: str,
    rules: tuple[AppellateRule, ...],
    authorities: tuple[AuthorityDocument, ...],
) -> str:
    return sha256_digest(
        {
            "rules": [
                rule.model_dump(mode="json") for rule in rules if rule.source_id == source_id
            ],
            "authorities": [
                authority.model_dump(mode="json")
                for authority in authorities
                if authority.source_id == source_id
            ],
        }
    )
