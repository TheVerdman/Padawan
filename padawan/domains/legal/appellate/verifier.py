from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from padawan.domains.contracts import (
    HardGateResult,
    RewardObservation,
    VerifierDisposition,
    VerifierResult,
)
from padawan.domains.legal.appellate.contracts import (
    STRUCTURAL_SECTIONS,
    AppellateCitation,
    AppellateCitationKind,
    AppellateClaimKind,
    AppellateCourtPack,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSubmission,
    AppellateVerificationBundle,
    AuthorityCurrentnessAssessment,
    BriefSectionKind,
    CurrentnessStatus,
)
from padawan.domains.legal.appellate.corpus import APPELLATE_VERIFIER_VERSION
from padawan.models.hashing import sha256_digest

_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)?")
_QUOTED_SPAN = re.compile(r'["“]([^"”]+)["”]')
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_CONTROLLED_RECORD_KINDS = {
    AppellateClaimKind.JURISDICTION,
    AppellateClaimKind.RECORD_FACT,
    AppellateClaimKind.PROCEDURAL_HISTORY,
    AppellateClaimKind.PRESERVATION,
}
_SEMANTIC_CLAIM_KINDS = {
    AppellateClaimKind.LEGAL_RULE,
    AppellateClaimKind.STANDARD_OF_REVIEW,
    AppellateClaimKind.APPLICATION,
    AppellateClaimKind.COUNTERARGUMENT,
    AppellateClaimKind.REMEDY,
}
_HARD_STAGES = (
    "pack_integrity",
    "task_binding",
    "rule_compliance",
    "claim_map_integrity",
    "record_resolution",
    "authority_resolution",
    "quotation_fidelity",
    "leakage",
)


class AppellateBriefVerifier:
    """Verify a closed-record brief without conflating resolution with legal meaning."""

    verifier_id = "appellate.closed_record"
    verifier_version = APPELLATE_VERIFIER_VERSION

    def verify(
        self,
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
        semantic_assessment: AppellateSemanticAssessment | None = None,
        currentness_assessments: tuple[AuthorityCurrentnessAssessment, ...] = (),
        created_at: datetime | None = None,
    ) -> AppellateVerificationBundle:
        timestamp = created_at or datetime.now(UTC)
        submission_digest = sha256_digest(submission.model_dump(mode="json"))
        input_digest = sha256_digest(
            {
                "pack": pack,
                "scenario": scenario,
                "submission": submission,
                "semantic_assessment": semantic_assessment,
                "currentness_assessments": currentness_assessments,
            }
        )
        stage_data: list[tuple[str, VerifierDisposition, list[str], dict[str, Any], bool]] = []

        pack_errors, pack_evidence = self._pack_integrity(pack=pack, scenario=scenario)
        stage_data.append(
            (
                "pack_integrity",
                _deterministic_disposition(pack_errors),
                pack_errors,
                pack_evidence,
                True,
            )
        )
        binding_errors, binding_evidence = self._task_binding(
            pack=pack,
            scenario=scenario,
            submission=submission,
        )
        stage_data.append(
            (
                "task_binding",
                _deterministic_disposition(binding_errors),
                binding_errors,
                binding_evidence,
                True,
            )
        )
        rule_errors, rule_evidence = self._rule_compliance(
            scenario=scenario,
            submission=submission,
        )
        stage_data.append(
            (
                "rule_compliance",
                _deterministic_disposition(rule_errors),
                rule_errors,
                rule_evidence,
                True,
            )
        )
        map_errors, map_evidence = self._claim_map_integrity(submission=submission)
        stage_data.append(
            (
                "claim_map_integrity",
                _deterministic_disposition(map_errors),
                map_errors,
                map_evidence,
                True,
            )
        )
        record_errors, record_evidence = self._record_resolution(
            scenario=scenario,
            submission=submission,
        )
        stage_data.append(
            (
                "record_resolution",
                _deterministic_disposition(record_errors),
                record_errors,
                record_evidence,
                True,
            )
        )
        authority_errors, authority_evidence = self._authority_resolution(
            pack=pack,
            submission=submission,
        )
        stage_data.append(
            (
                "authority_resolution",
                _deterministic_disposition(authority_errors),
                authority_errors,
                authority_evidence,
                True,
            )
        )
        quote_errors, quote_evidence = self._quotation_fidelity(
            pack=pack,
            scenario=scenario,
            submission=submission,
        )
        stage_data.append(
            (
                "quotation_fidelity",
                _deterministic_disposition(quote_errors),
                quote_errors,
                quote_evidence,
                True,
            )
        )
        leakage_errors, leakage_evidence = self._leakage(
            pack=pack,
            scenario=scenario,
            submission=submission,
        )
        stage_data.append(
            (
                "leakage",
                _deterministic_disposition(leakage_errors),
                leakage_errors,
                leakage_evidence,
                True,
            )
        )

        semantic = self._semantic_results(
            pack=pack,
            scenario=scenario,
            submission=submission,
            submission_digest=submission_digest,
            assessment=semantic_assessment,
        )
        stage_data.extend(semantic)
        currentness_disposition, currentness_errors, currentness_evidence = self._currentness(
            pack=pack,
            submission=submission,
            assessments=currentness_assessments,
        )
        stage_data.append(
            (
                "currentness",
                currentness_disposition,
                currentness_errors,
                currentness_evidence,
                True,
            )
        )

        results = tuple(
            _result(
                stage=stage,
                disposition=disposition,
                errors=errors,
                evidence=evidence,
                deterministic=deterministic,
                scenario=scenario,
                submission=submission,
                input_digest=input_digest,
                created_at=timestamp,
            )
            for stage, disposition, errors, evidence, deterministic in stage_data
        )
        result_by_stage = {
            result.verifier_id.removeprefix("appellate."): result for result in results
        }
        gates = tuple(_gate(result_by_stage[stage]) for stage in _HARD_STAGES)
        task_verified = all(gate.passed for gate in gates) and all(
            result_by_stage[stage].disposition == VerifierDisposition.VERIFIED
            for stage in (
                "proposition_support",
                "applicability",
                "adverse_authority",
                "issue_remedy_coverage",
            )
        )
        observations = self._reward_observations(
            scenario=scenario,
            submission=submission,
            result_by_stage=result_by_stage,
        )
        return AppellateVerificationBundle(
            scenario_id=scenario.scenario_id,
            scenario_digest=scenario.scenario_digest,
            scenario_split=scenario.split,
            court_pack_digest=pack.pack_digest,
            brief_id=submission.brief_id,
            submission_digest=submission_digest,
            verification_input_digest=input_digest,
            verifier_results=results,
            hard_gates=gates,
            reward_observations=observations,
            task_verified=task_verified,
            created_at=timestamp,
        )

    @staticmethod
    def _pack_integrity(
        *, pack: AppellateCourtPack, scenario: AppellateScenarioManifest
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        if scenario.court_pack_id != pack.pack_id:
            errors.append("scenario court-pack identity differs from the verifier pack")
        if scenario.court_pack_digest != pack.pack_digest:
            errors.append("scenario court-pack digest differs from the verifier pack")
        if not pack.coverage.task_complete:
            errors.append("court pack does not declare task-complete authority coverage")
        if pack.coverage.globally_complete:
            errors.append("court pack incorrectly claims globally complete authority coverage")
        if pack.local_rules_edition_on > pack.governing_law_cutoff:
            errors.append("local-rules edition postdates the governing-law cutoff")
        if pack.national_rules_effective_on > pack.governing_law_cutoff:
            errors.append("national rules took effect after the governing-law cutoff")
        if any(authority.decided_on > pack.governing_law_cutoff for authority in pack.authorities):
            errors.append("court pack includes an authority decided after the governing-law cutoff")
        authority_ids = {authority.authority_id for authority in pack.authorities}
        unknown_adverse = set(scenario.required_adverse_authority_ids) - authority_ids
        if unknown_adverse:
            errors.append("scenario requires adverse authority absent from the court pack")
        expected_environment = sha256_digest(
            {
                "court_pack_digest": pack.pack_digest,
                "record_digest": scenario.record.record_digest,
                "brief_type": scenario.brief_type,
                "procedural_posture": scenario.procedural_posture,
                "split": scenario.split,
            }
        )
        if scenario.environment_fingerprint != expected_environment:
            errors.append("scenario environment fingerprint differs from the admitted materials")
        return errors, {
            "court_pack_id": pack.pack_id,
            "court_pack_digest": pack.pack_digest,
            "record_digest": scenario.record.record_digest,
            "environment_fingerprint": scenario.environment_fingerprint,
            "governing_law_cutoff": pack.governing_law_cutoff.isoformat(),
            "globally_complete": pack.coverage.globally_complete,
            "citator_sources": list(pack.coverage.dependable_citator_source_ids),
        }

    @staticmethod
    def _task_binding(
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        if submission.scenario_id != scenario.scenario_id:
            errors.append("brief belongs to another appellate scenario")
        if submission.court_pack_digest != pack.pack_digest:
            errors.append("brief cites a different court-pack digest")
        return errors, {
            "brief_id": submission.brief_id,
            "brief_type": submission.brief_type.value,
            "research_role": submission.research_role.value,
            "scenario_id": scenario.scenario_id,
        }

    @staticmethod
    def _rule_compliance(
        *, scenario: AppellateScenarioManifest, submission: AppellateSubmission
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        observed_sections = tuple(section.kind for section in submission.sections)
        if observed_sections != scenario.required_sections:
            errors.append("brief sections do not match the required order and set")
        observed_claim_kinds = {claim.kind for claim in submission.claims}
        missing_claim_kinds = set(scenario.required_claim_kinds) - observed_claim_kinds
        if missing_claim_kinds:
            errors.append(
                "brief omits required claim kinds: "
                + ", ".join(sorted(kind.value for kind in missing_claim_kinds))
            )
        word_count = counted_words(submission)
        certificate = submission.compliance_certificate
        if certificate.counted_words != word_count:
            errors.append("certificate word count differs from the deterministic count")
        if not certificate.uses_word_count_limit:
            errors.append("certificate does not invoke the word-count route")
        if not certificate.service_certified:
            errors.append("certificate of service is not affirmed")
        if word_count > scenario.exercise_word_limit:
            errors.append("brief exceeds the exercise word limit")
        if word_count > 13_000:
            errors.append("brief exceeds the principal-brief federal word limit")
        sections_by_kind = {section.kind: section for section in submission.sections}
        toc_section = sections_by_kind.get(BriefSectionKind.TABLE_OF_CONTENTS)
        toc_text = (
            " ".join(paragraph.text for paragraph in toc_section.paragraphs).casefold()
            if toc_section is not None
            else ""
        )
        for section in submission.sections:
            if (
                section.kind not in {BriefSectionKind.COVER, BriefSectionKind.TABLE_OF_CONTENTS}
                and section.heading.casefold() not in toc_text
            ):
                errors.append(f"table of contents omits {section.heading}")
        toa_section = sections_by_kind.get(BriefSectionKind.TABLE_OF_AUTHORITIES)
        toa_text = (
            " ".join(paragraph.text for paragraph in toa_section.paragraphs)
            if toa_section is not None
            else ""
        )
        for display in sorted(
            {
                citation.display
                for citation in submission.citations
                if citation.kind in {AppellateCitationKind.AUTHORITY, AppellateCitationKind.RULE}
            }
        ):
            if display not in toa_text:
                errors.append(f"table of authorities omits {display}")
        jurisdiction_claim_ids = {
            claim.claim_id
            for claim in submission.claims
            if claim.kind == AppellateClaimKind.JURISDICTION
        }
        jurisdiction_sources = {
            citation.source_id
            for citation in submission.citations
            if citation.claim_id in jurisdiction_claim_ids
            and citation.kind == AppellateCitationKind.RULE
        }
        required_jurisdiction_sources = {
            "28-usc-1291-final-decisions",
            "28-usc-1332-diversity",
        }
        if not required_jurisdiction_sources.issubset(jurisdiction_sources):
            errors.append("jurisdictional statement omits the declared statutory bases")
        argument_claim_ids = {
            claim_id
            for section in submission.sections
            if section.kind == BriefSectionKind.ARGUMENT
            for paragraph in section.paragraphs
            for claim_id in paragraph.claim_ids
        }
        claim_by_id = {claim.claim_id: claim for claim in submission.claims}
        argument_kinds = {
            claim_by_id[claim_id].kind for claim_id in argument_claim_ids if claim_id in claim_by_id
        }
        for required in (
            AppellateClaimKind.STANDARD_OF_REVIEW,
            AppellateClaimKind.APPLICATION,
            AppellateClaimKind.COUNTERARGUMENT,
        ):
            if required not in argument_kinds:
                errors.append(f"argument section omits {required.value}")
        return errors, {
            "observed_sections": [section.value for section in observed_sections],
            "required_sections": [section.value for section in scenario.required_sections],
            "counted_words": word_count,
            "exercise_word_limit": scenario.exercise_word_limit,
            "federal_word_limit": 13_000,
            "certificate_counted_words": certificate.counted_words,
        }

    @staticmethod
    def _claim_map_integrity(
        *, submission: AppellateSubmission
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        mapped_claim_ids = [
            claim_id
            for section in submission.sections
            for paragraph in section.paragraphs
            for claim_id in paragraph.claim_ids
        ]
        if set(mapped_claim_ids) != {claim.claim_id for claim in submission.claims}:
            errors.append("claim map does not cover every declared claim")
        if len(mapped_claim_ids) != len(set(mapped_claim_ids)):
            errors.append("a claim appears in more than one paragraph")
        for section in submission.sections:
            for paragraph in section.paragraphs:
                if section.kind in STRUCTURAL_SECTIONS and paragraph.claim_ids:
                    errors.append("structural section contains substantive claims")
                if section.kind not in STRUCTURAL_SECTIONS and not paragraph.claim_ids:
                    errors.append("substantive paragraph has no claim mapping")
        return errors, {
            "claim_count": len(submission.claims),
            "citation_count": len(submission.citations),
            "mapped_claim_count": len(mapped_claim_ids),
            "substantive_paragraph_count": sum(
                1
                for section in submission.sections
                if section.kind not in STRUCTURAL_SECTIONS
                for _paragraph in section.paragraphs
            ),
        }

    @staticmethod
    def _record_resolution(
        *, scenario: AppellateScenarioManifest, submission: AppellateSubmission
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        documents = {document.document_id: document for document in scenario.record.documents}
        pages = {
            page.page_id: (document.document_id, page)
            for document in scenario.record.documents
            for page in document.pages
        }
        facts = {fact.fact_id: fact for fact in scenario.record.facts}
        citations_by_claim = _citations_by_claim(submission.citations)
        resolved_citations = 0
        for citation in submission.citations:
            if citation.kind != AppellateCitationKind.RECORD:
                continue
            document = documents.get(citation.source_id)
            page_entry = pages.get(citation.locator_id)
            if document is None or page_entry is None or page_entry[0] != citation.source_id:
                errors.append(
                    f"record citation {citation.citation_id} does not resolve in the closed record"
                )
                continue
            page = page_entry[1]
            expected_display = f"JA {page.appendix_page}"
            if citation.display != expected_display:
                errors.append(
                    f"record citation {citation.citation_id} has a noncanonical appendix locator"
                )
            resolved_citations += 1
        for claim in submission.claims:
            claim_facts = [facts.get(fact_id) for fact_id in claim.record_fact_ids]
            if any(fact is None for fact in claim_facts):
                errors.append(f"claim {claim.claim_id} cites an unknown record fact")
                continue
            record_citations = [
                citation
                for citation in citations_by_claim.get(claim.claim_id, ())
                if citation.kind == AppellateCitationKind.RECORD
            ]
            if claim.record_fact_ids and not record_citations:
                errors.append(f"claim {claim.claim_id} declares record facts without record cites")
            for fact in claim_facts:
                if fact is None:
                    continue
                if not any(citation.locator_id in fact.page_ids for citation in record_citations):
                    errors.append(
                        f"claim {claim.claim_id} lacks a citation to a page supporting "
                        f"{fact.fact_id}"
                    )
            if claim.kind in _CONTROLLED_RECORD_KINDS:
                if len(claim_facts) != 1 or claim_facts[0] is None:
                    errors.append(
                        f"controlled record claim {claim.claim_id} must identify exactly one fact"
                    )
                elif _normalize(claim.text) != _normalize(claim_facts[0].statement):
                    errors.append(
                        f"controlled record claim {claim.claim_id} is not the admitted fact text"
                    )
            for citation in record_citations:
                if not any(
                    fact is not None and citation.locator_id in fact.page_ids
                    for fact in claim_facts
                ):
                    errors.append(
                        f"record citation {citation.citation_id} is not bound to a declared fact"
                    )
        return errors, {
            "record_id": scenario.record.record_id,
            "record_digest": scenario.record.record_digest,
            "record_pages": len(pages),
            "record_facts": len(facts),
            "resolved_record_citations": resolved_citations,
            "controlled_record_claims": sum(
                claim.kind in _CONTROLLED_RECORD_KINDS for claim in submission.claims
            ),
        }

    @staticmethod
    def _authority_resolution(
        *, pack: AppellateCourtPack, submission: AppellateSubmission
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        rules = {rule.rule_id: rule for rule in pack.rules}
        authorities = {authority.authority_id: authority for authority in pack.authorities}
        passages = {
            (authority.authority_id, passage.passage_id): passage
            for authority in pack.authorities
            for passage in authority.passages
        }
        citations_by_claim = _citations_by_claim(submission.citations)
        resolved = 0
        unresolved: list[str] = []
        for citation in submission.citations:
            if citation.kind == AppellateCitationKind.RECORD:
                continue
            if citation.kind == AppellateCitationKind.RULE:
                rule = rules.get(citation.source_id)
                if rule is None or citation.locator_id != citation.source_id:
                    unresolved.append(citation.citation_id)
                    errors.append(
                        f"rule citation {citation.citation_id} is unresolved in the closed corpus"
                    )
                    continue
                if citation.display != rule.citation:
                    errors.append(f"rule citation {citation.citation_id} has the wrong display")
                resolved += 1
                continue
            authority = authorities.get(citation.source_id)
            passage = passages.get((citation.source_id, citation.locator_id))
            if authority is None or passage is None:
                unresolved.append(citation.citation_id)
                errors.append(
                    f"authority citation {citation.citation_id} is unresolved in the declared "
                    "closed corpus; absence alone is not a fabrication finding"
                )
                continue
            if citation.display != passage.pinpoint_citation:
                errors.append(f"authority citation {citation.citation_id} has the wrong pinpoint")
            resolved += 1
        for claim in submission.claims:
            legal_citations = [
                citation
                for citation in citations_by_claim.get(claim.claim_id, ())
                if citation.kind in {AppellateCitationKind.AUTHORITY, AppellateCitationKind.RULE}
            ]
            if claim.kind in _SEMANTIC_CLAIM_KINDS and not legal_citations:
                errors.append(f"legal claim {claim.claim_id} has no authority or rule citation")
            cited_authorities = {
                citation.source_id
                for citation in legal_citations
                if citation.kind == AppellateCitationKind.AUTHORITY
            }
            if not set(claim.treated_authority_ids).issubset(cited_authorities):
                errors.append(
                    f"claim {claim.claim_id} treats an authority it does not actually cite"
                )
        return errors, {
            "resolved_legal_citations": resolved,
            "unresolved_citation_ids": unresolved,
            "coverage_scope": pack.coverage.description,
            "absence_interpretation": pack.coverage.absence_interpretation,
        }

    @staticmethod
    def _quotation_fidelity(
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
    ) -> tuple[list[str], dict[str, Any]]:
        errors: list[str] = []
        source_text: dict[tuple[AppellateCitationKind, str, str], tuple[str, bool]] = {}
        for rule in pack.rules:
            source_text[(AppellateCitationKind.RULE, rule.rule_id, rule.rule_id)] = (
                rule.text,
                rule.verbatim,
            )
        for authority in pack.authorities:
            for passage in authority.passages:
                source_text[
                    (AppellateCitationKind.AUTHORITY, authority.authority_id, passage.passage_id)
                ] = (passage.text, passage.verbatim)
        for document in scenario.record.documents:
            for page in document.pages:
                source_text[(AppellateCitationKind.RECORD, document.document_id, page.page_id)] = (
                    page.text,
                    True,
                )
        claims = {claim.claim_id: claim for claim in submission.claims}
        verified_quotes = 0
        for citation in submission.citations:
            if citation.quoted_text is None:
                continue
            claim = claims[citation.claim_id]
            admitted = source_text.get((citation.kind, citation.source_id, citation.locator_id))
            if admitted is None:
                errors.append(f"quotation on {citation.citation_id} does not match its source")
            elif not admitted[1]:
                errors.append(
                    f"quotation on {citation.citation_id} cites a non-verbatim task rendering"
                )
            elif _normalize(citation.quoted_text) not in _normalize(admitted[0]):
                errors.append(f"quotation on {citation.citation_id} does not match its source")
            if _normalize(citation.quoted_text) not in _normalize(claim.text):
                errors.append(f"quotation on {citation.citation_id} is absent from its claim")
            verified_quotes += 1
        for claim in submission.claims:
            declared_quotes = {
                _normalize(citation.quoted_text)
                for citation in submission.citations
                if citation.claim_id == claim.claim_id and citation.quoted_text is not None
            }
            for span in _QUOTED_SPAN.findall(claim.text):
                if _normalize(span) not in declared_quotes:
                    errors.append(f"claim {claim.claim_id} contains an unmapped quotation")
        return errors, {"declared_quotes": verified_quotes, "quotation_errors": len(errors)}

    @staticmethod
    def _leakage(
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
    ) -> tuple[list[str], dict[str, Any]]:
        restricted = set(pack.sealed_sentence_digests).union(scenario.restricted_sentence_digests)
        observed: list[str] = []
        leaked: list[str] = []
        for section in submission.sections:
            for paragraph in section.paragraphs:
                for sentence in _SENTENCE_BOUNDARY.split(paragraph.text):
                    normalized = _normalize(sentence)
                    if not normalized:
                        continue
                    digest = sha256_digest(normalized)
                    observed.append(digest)
                    if digest in restricted:
                        leaked.append(digest)
        errors = (
            ["brief contains a sentence reserved for sealed or evaluator-only evidence"]
            if leaked
            else []
        )
        return errors, {
            "sentences_scanned": len(observed),
            "restricted_digest_count": len(restricted),
            "matched_digests": sorted(set(leaked)),
        }

    @staticmethod
    def _semantic_results(
        *,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
        submission_digest: str,
        assessment: AppellateSemanticAssessment | None,
    ) -> list[tuple[str, VerifierDisposition, list[str], dict[str, Any], bool]]:
        unknown_evidence = {
            "assessment_id": None,
            "reason": "no evidence-bound semantic adjudication was supplied",
        }
        if assessment is None:
            return [
                (stage, VerifierDisposition.UNKNOWN, [], unknown_evidence, False)
                for stage in (
                    "proposition_support",
                    "applicability",
                    "adverse_authority",
                    "issue_remedy_coverage",
                )
            ]
        errors: list[str] = []
        if assessment.scenario_id != scenario.scenario_id:
            errors.append("semantic assessment belongs to another scenario")
        if assessment.submission_digest != submission_digest:
            errors.append("semantic assessment belongs to different brief content")
        claims = {claim.claim_id: claim for claim in submission.claims}
        legal_claims = {
            claim.claim_id: claim
            for claim in submission.claims
            if claim.kind in _SEMANTIC_CLAIM_KINDS
        }
        assessments = {item.claim_id: item for item in assessment.claim_assessments}
        if set(assessments) != set(legal_claims):
            errors.append("semantic assessment does not cover exactly the legal claims")
        for claim_id, item in assessments.items():
            claim = legal_claims.get(claim_id)
            if claim is None:
                continue
            if item.proposition != claim.text:
                errors.append(f"semantic proposition differs from claim {claim_id}")
            citations = [
                citation for citation in submission.citations if citation.claim_id == claim_id
            ]
            allowed_passages = {
                citation.locator_id
                for citation in citations
                if citation.kind == AppellateCitationKind.AUTHORITY
            }
            allowed_rules = {
                citation.source_id
                for citation in citations
                if citation.kind == AppellateCitationKind.RULE
            }
            if not set(item.authority_passage_ids).issubset(allowed_passages):
                errors.append(f"semantic assessment cites an unbound passage for {claim_id}")
            if not set(item.rule_ids).issubset(allowed_rules):
                errors.append(f"semantic assessment cites an unbound rule for {claim_id}")
            if not set(item.record_fact_ids).issubset(claim.record_fact_ids):
                errors.append(f"semantic assessment cites an unbound record fact for {claim_id}")
            if not item.authority_passage_ids and not item.rule_ids:
                errors.append(f"semantic assessment gives no legal source for {claim_id}")
            if (
                claim.kind in {AppellateClaimKind.APPLICATION, AppellateClaimKind.COUNTERARGUMENT}
                and claim.record_fact_ids
                and not item.record_fact_ids
            ):
                errors.append(f"semantic applicability gives no record facts for {claim_id}")
            if item.support == VerifierDisposition.INFRASTRUCTURE_FAILURE or (
                item.applicability == VerifierDisposition.INFRASTRUCTURE_FAILURE
            ):
                errors.append("semantic assessments cannot encode infrastructure failure as law")
        semantic_evidence = {
            "assessment_id": assessment.assessment_id,
            "adjudicator_id": assessment.adjudicator_id,
            "method": assessment.method.value,
            "assessment_errors": errors,
            "claim_assessment_count": len(assessment.claim_assessments),
        }
        if errors:
            support_disposition = VerifierDisposition.UNKNOWN
            applicability_disposition = VerifierDisposition.UNKNOWN
        else:
            support_disposition = _aggregate(item.support for item in assessment.claim_assessments)
            applicability_disposition = _aggregate(
                item.applicability for item in assessment.claim_assessments
            )

        adverse_errors: list[str] = []
        adverse_by_id = {
            item.authority_id: item for item in assessment.adverse_authority_assessments
        }
        required_adverse = set(scenario.required_adverse_authority_ids)
        structurally_treated = {
            authority_id
            for claim in submission.claims
            if claim.kind == AppellateClaimKind.COUNTERARGUMENT
            for authority_id in claim.treated_authority_ids
        }
        if not required_adverse.issubset(structurally_treated):
            adverse_errors.append("brief omits a required adverse authority treatment")
        if set(adverse_by_id) != required_adverse:
            adverse_errors.append("semantic assessment does not cover required adverse authority")
        authority_passage_ids = {
            authority.authority_id: {passage.passage_id for passage in authority.passages}
            for authority in pack.authorities
        }
        for authority_id, adverse_item in adverse_by_id.items():
            claim = claims.get(adverse_item.treatment_claim_id)
            if (
                claim is None
                or claim.kind != AppellateClaimKind.COUNTERARGUMENT
                or authority_id not in claim.treated_authority_ids
            ):
                adverse_errors.append(f"adverse assessment for {authority_id} is not claim-bound")
                continue
            claim_passages = {
                citation.locator_id
                for citation in submission.citations
                if citation.claim_id == claim.claim_id
                and citation.kind == AppellateCitationKind.AUTHORITY
                and citation.source_id == authority_id
            }
            admitted_passages = authority_passage_ids.get(authority_id, set())
            cited_passages = set(adverse_item.authority_passage_ids)
            if not cited_passages.issubset(admitted_passages):
                adverse_errors.append(
                    f"adverse assessment for {authority_id} cites text from another authority"
                )
            if not cited_passages.issubset(claim_passages):
                adverse_errors.append(
                    f"adverse assessment for {authority_id} cites text not attached to its claim"
                )
            if not set(adverse_item.record_fact_ids).issubset(claim.record_fact_ids):
                adverse_errors.append(f"adverse assessment for {authority_id} cites unbound facts")
        if adverse_errors:
            adverse_disposition = (
                VerifierDisposition.REJECTED
                if not required_adverse.issubset(structurally_treated)
                else VerifierDisposition.UNKNOWN
            )
        else:
            adverse_disposition = _aggregate(
                item.disposition for item in assessment.adverse_authority_assessments
            )
        adverse_evidence = {
            "assessment_id": assessment.assessment_id,
            "required_authority_ids": sorted(required_adverse),
            "structurally_treated_authority_ids": sorted(structurally_treated),
            "assessment_errors": adverse_errors,
        }

        coverage_errors: list[str] = []
        coverage_specs = (
            (assessment.issue_coverage, {AppellateClaimKind.ISSUE}),
            (assessment.preservation_coverage, {AppellateClaimKind.PRESERVATION}),
            (assessment.remedy_coverage, {AppellateClaimKind.REMEDY}),
        )
        for coverage, kinds in coverage_specs:
            if any(
                claim_id not in claims or claims[claim_id].kind not in kinds
                for claim_id in coverage.claim_ids
            ):
                coverage_errors.append("coverage assessment cites a claim of the wrong kind")
        coverage_disposition = (
            VerifierDisposition.UNKNOWN
            if coverage_errors
            else _aggregate(coverage.disposition for coverage, _kinds in coverage_specs)
        )
        coverage_evidence = {
            "assessment_id": assessment.assessment_id,
            "assessment_errors": coverage_errors,
            "issue_claim_ids": list(assessment.issue_coverage.claim_ids),
            "preservation_claim_ids": list(assessment.preservation_coverage.claim_ids),
            "remedy_claim_ids": list(assessment.remedy_coverage.claim_ids),
        }
        return [
            (
                "proposition_support",
                support_disposition,
                errors,
                semantic_evidence,
                False,
            ),
            (
                "applicability",
                applicability_disposition,
                errors,
                semantic_evidence,
                False,
            ),
            (
                "adverse_authority",
                adverse_disposition,
                adverse_errors,
                adverse_evidence,
                False,
            ),
            (
                "issue_remedy_coverage",
                coverage_disposition,
                coverage_errors,
                coverage_evidence,
                False,
            ),
        ]

    @staticmethod
    def _currentness(
        *,
        pack: AppellateCourtPack,
        submission: AppellateSubmission,
        assessments: tuple[AuthorityCurrentnessAssessment, ...],
    ) -> tuple[VerifierDisposition, list[str], dict[str, Any]]:
        cited_authorities = {
            citation.source_id
            for citation in submission.citations
            if citation.kind == AppellateCitationKind.AUTHORITY
        }
        dependable_sources = set(pack.coverage.dependable_citator_source_ids)
        if not dependable_sources:
            return (
                VerifierDisposition.UNKNOWN,
                [],
                {
                    "reason": "court pack declares no dependable citator source",
                    "cited_authority_ids": sorted(cited_authorities),
                    "accepted_assessment_ids": [],
                    "good_law_claimed": False,
                },
            )
        errors: list[str] = []
        by_authority = {assessment.authority_id: assessment for assessment in assessments}
        if len(by_authority) != len(assessments):
            errors.append("currentness assessments contain duplicate authorities")
        if set(by_authority) != cited_authorities:
            errors.append("citator assessments do not cover exactly the cited authorities")
        accepted: list[str] = []
        statuses: list[CurrentnessStatus] = []
        for authority_id in cited_authorities:
            assessment = by_authority.get(authority_id)
            if assessment is None:
                continue
            if assessment.citator_source_id not in dependable_sources:
                errors.append(f"currentness source for {authority_id} is not admitted")
                continue
            if assessment.as_of < pack.governing_law_cutoff:
                errors.append(f"currentness assessment for {authority_id} predates the cutoff")
                continue
            accepted.append(assessment.assessment_id)
            statuses.append(assessment.status)
        if errors or len(statuses) != len(cited_authorities):
            disposition = VerifierDisposition.UNKNOWN
        elif any(status == CurrentnessStatus.NEGATIVE_TREATMENT for status in statuses):
            disposition = VerifierDisposition.REJECTED
        elif statuses and all(status == CurrentnessStatus.GOOD_LAW for status in statuses):
            disposition = VerifierDisposition.VERIFIED
        else:
            disposition = VerifierDisposition.UNKNOWN
        return (
            disposition,
            errors,
            {
                "cited_authority_ids": sorted(cited_authorities),
                "accepted_assessment_ids": accepted,
                "dependable_citator_source_ids": sorted(dependable_sources),
                "good_law_claimed": disposition == VerifierDisposition.VERIFIED,
            },
        )

    @staticmethod
    def _reward_observations(
        *,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
        result_by_stage: Mapping[str, VerifierResult],
    ) -> tuple[RewardObservation, ...]:
        observations = [
            _observation("proposition_support", result_by_stage["proposition_support"]),
            _observation("applicability", result_by_stage["applicability"]),
            _observation("adverse_authority", result_by_stage["adverse_authority"]),
            _observation("issue_remedy_coverage", result_by_stage["issue_remedy_coverage"]),
        ]
        word_count = counted_words(submission)
        observations.append(
            RewardObservation(
                component_id="concision",
                value=max(0.0, 1.0 - (word_count / scenario.exercise_word_limit)),
                evidence_refs=(result_by_stage["rule_compliance"].result_id,),
            )
        )
        observations.append(_observation("currentness", result_by_stage["currentness"]))
        return tuple(observations)


def counted_words(submission: AppellateSubmission) -> int:
    return sum(
        len(_WORD.findall(paragraph.text))
        for section in submission.sections
        if section.kind not in STRUCTURAL_SECTIONS
        for paragraph in section.paragraphs
    )


def restricted_sentence_digest(text: str) -> str:
    """Hash a normalized sentence for sealed-leakage manifests and offline tests."""

    return sha256_digest(_normalize(text))


def _observation(component_id: str, result: VerifierResult) -> RewardObservation:
    if result.disposition == VerifierDisposition.UNKNOWN:
        return RewardObservation(
            component_id=component_id,
            value=None,
            evidence_refs=(result.result_id,),
            missing_reason=result.evidence.get("reason", result.summary),
        )
    if result.disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE:
        return RewardObservation(
            component_id=component_id,
            value=None,
            evidence_refs=(result.result_id,),
            missing_reason="verifier infrastructure did not produce an authoritative outcome",
        )
    return RewardObservation(
        component_id=component_id,
        value=1.0 if result.disposition == VerifierDisposition.VERIFIED else 0.0,
        evidence_refs=(result.result_id,),
    )


def _result(
    *,
    stage: str,
    disposition: VerifierDisposition,
    errors: list[str],
    evidence: dict[str, Any],
    deterministic: bool,
    scenario: AppellateScenarioManifest,
    submission: AppellateSubmission,
    input_digest: str,
    created_at: datetime,
) -> VerifierResult:
    result_digest = sha256_digest(
        {
            "verifier_version": APPELLATE_VERIFIER_VERSION,
            "stage": stage,
            "scenario_id": scenario.scenario_id,
            "brief_id": submission.brief_id,
            "input_digest": input_digest,
            "disposition": disposition,
            "errors": errors,
            "evidence": evidence,
            "created_at": created_at,
        }
    )
    if disposition == VerifierDisposition.VERIFIED:
        summary = f"Appellate {stage.replace('_', ' ')} verified"
    elif disposition == VerifierDisposition.REJECTED:
        summary = f"Appellate {stage.replace('_', ' ')} rejected"
    elif disposition == VerifierDisposition.UNKNOWN:
        summary = f"Appellate {stage.replace('_', ' ')} remains unknown"
    else:
        summary = f"Appellate {stage.replace('_', ' ')} unavailable"
    return VerifierResult(
        result_id=f"appellate-result-{stage}-{result_digest[7:23]}",
        verifier_id=f"appellate.{stage}",
        verifier_version=APPELLATE_VERIFIER_VERSION,
        scope=scenario.scenario_id,
        disposition=disposition,
        deterministic=deterministic,
        summary=summary,
        evidence={"input_digest": input_digest, "errors": errors, **evidence},
        created_at=created_at,
    )


def _gate(result: VerifierResult) -> HardGateResult:
    stage = result.verifier_id.removeprefix("appellate.")
    return HardGateResult(
        gate_id=f"appellate:{stage}",
        passed=result.disposition == VerifierDisposition.VERIFIED,
        disposition=result.disposition,
        evidence_refs=(result.result_id,),
        reason=result.summary,
    )


def _deterministic_disposition(errors: list[str]) -> VerifierDisposition:
    return VerifierDisposition.REJECTED if errors else VerifierDisposition.VERIFIED


def _aggregate(dispositions: Iterable[VerifierDisposition]) -> VerifierDisposition:
    values = tuple(dispositions)
    if not values or any(value == VerifierDisposition.UNKNOWN for value in values):
        return VerifierDisposition.UNKNOWN
    if any(value == VerifierDisposition.INFRASTRUCTURE_FAILURE for value in values):
        return VerifierDisposition.UNKNOWN
    if any(value == VerifierDisposition.REJECTED for value in values):
        return VerifierDisposition.REJECTED
    return VerifierDisposition.VERIFIED


def _normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def _citations_by_claim(
    citations: tuple[AppellateCitation, ...],
) -> dict[str, tuple[AppellateCitation, ...]]:
    grouped: dict[str, list[AppellateCitation]] = {}
    for citation in citations:
        grouped.setdefault(citation.claim_id, []).append(citation)
    return {claim_id: tuple(items) for claim_id, items in grouped.items()}
