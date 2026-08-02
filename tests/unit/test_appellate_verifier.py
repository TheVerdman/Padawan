from __future__ import annotations

import pytest

from padawan.domains.contracts import TrainingLane, VerifierDisposition
from padawan.domains.legal.appellate import (
    AppellateBriefVerifier,
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
    AppellateSubmission,
    AppellateVerificationBundle,
    AuthorityCurrentnessAssessment,
    BriefSectionKind,
    CurrentnessStatus,
    build_fourth_circuit_pack,
    decide_appellate_training_eligibility,
    default_appellate_reward_policy,
)
from padawan.models.contracts import CorpusPool, ResearchRole
from padawan.models.hashing import sha256_digest
from tests.appellate_helpers import NOW, build_semantic_assessment, build_submission


def _scenario():
    return AppellateCorpusGenerator().scenario(
        family=AppellateScenarioFamily.AMBIGUOUS_VIDEO,
        seed=31,
        split=CorpusPool.CURRICULUM.value,
        created_at=NOW,
    )


def test_valid_closed_record_brief_passes_hard_gates_but_currentness_is_unknown() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    assessment = build_semantic_assessment(scenario, submission)

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        semantic_assessment=assessment,
        created_at=NOW,
    )

    assert all(gate.passed for gate in bundle.hard_gates)
    assert bundle.task_verified is True
    results = {result.verifier_id: result for result in bundle.verifier_results}
    assert results["appellate.currentness"].disposition == VerifierDisposition.UNKNOWN
    assert results["appellate.currentness"].evidence["good_law_claimed"] is False
    observations = {item.component_id: item for item in bundle.reward_observations}
    assert observations["currentness"].value is None
    assert observations["currentness"].missing_reason is not None
    assert default_appellate_reward_policy(created_at=NOW).components[-1].missing_action.value == (
        "omit"
    )

    eligibility = decide_appellate_training_eligibility(
        reward_id="reward-appellate-reference",
        bundle=bundle,
        submission=submission,
        scenario=scenario,
        created_at=NOW,
    )
    assert set(eligibility.allowed_lanes) == {
        TrainingLane.EVALUATION_ONLY,
        TrainingLane.PREFERENCE,
        TrainingLane.PROCESS,
        TrainingLane.RLVR,
        TrainingLane.SFT,
    }


def test_without_semantic_adjudication_support_and_applicability_remain_unknown() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        created_at=NOW,
    )

    assert all(gate.passed for gate in bundle.hard_gates)
    assert bundle.task_verified is False
    results = {result.verifier_id: result for result in bundle.verifier_results}
    assert results["appellate.proposition_support"].disposition == VerifierDisposition.UNKNOWN
    assert results["appellate.applicability"].disposition == VerifierDisposition.UNKNOWN
    decision = decide_appellate_training_eligibility(
        reward_id="reward-unadjudicated",
        bundle=bundle,
        submission=submission,
        scenario=scenario,
        created_at=NOW,
    )
    assert decision.allowed_lanes == (TrainingLane.EVALUATION_ONLY,)


def test_semantic_adjudicator_cannot_cite_evidence_not_attached_to_the_claim() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    assessment = build_semantic_assessment(scenario, submission)
    claim_assessments = tuple(
        item.model_copy(update={"authority_passage_ids": ("brown-grainy-video",)})
        if item.claim_id == "claim-counterargument"
        else item
        for item in assessment.claim_assessments
    )
    assessment = assessment.model_copy(update={"claim_assessments": claim_assessments})

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        semantic_assessment=assessment,
        created_at=NOW,
    )
    results = {result.verifier_id: result for result in bundle.verifier_results}

    assert results["appellate.proposition_support"].disposition == VerifierDisposition.UNKNOWN
    assert "unbound passage" in " ".join(
        results["appellate.proposition_support"].evidence["errors"]
    )


def test_unadmitted_good_law_assertion_cannot_override_missing_citator() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    assessment = AuthorityCurrentnessAssessment(
        assessment_id="unadmitted-currentness",
        authority_id="tolan-v-cotton",
        citator_source_id="self-attested-citator",
        status=CurrentnessStatus.GOOD_LAW,
        as_of=build_fourth_circuit_pack().governing_law_cutoff,
        retrieval_digest=sha256_digest("unadmitted-currentness-response"),
        source_uri="https://example.invalid/currentness",
        created_at=NOW,
    )

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        currentness_assessments=(assessment,),
        created_at=NOW,
    )
    result = next(
        item for item in bundle.verifier_results if item.verifier_id == "appellate.currentness"
    )

    assert result.disposition == VerifierDisposition.UNKNOWN
    assert result.evidence["accepted_assessment_ids"] == []
    assert result.evidence["good_law_claimed"] is False


def test_unresolved_authority_is_rejected_without_being_labeled_fabricated() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    citations = tuple(
        citation.model_copy(update={"source_id": "unlisted-opinion"})
        if citation.citation_id == "cite-scott"
        else citation
        for citation in submission.citations
    )
    bad = submission.model_copy(update={"citations": citations})

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(), scenario=scenario, submission=bad, created_at=NOW
    )
    result = next(
        result
        for result in bundle.verifier_results
        if result.verifier_id == "appellate.authority_resolution"
    )

    assert result.disposition == VerifierDisposition.REJECTED
    assert "absence alone is not a fabrication finding" in " ".join(result.evidence["errors"])


@pytest.mark.parametrize(
    ("mutation", "failed_gate"),
    [
        ("quote", "appellate:quotation_fidelity"),
        ("rule_quote", "appellate:quotation_fidelity"),
        ("record", "appellate:record_resolution"),
        ("outside_record", "appellate:record_resolution"),
        ("order", "appellate:rule_compliance"),
        ("certificate", "appellate:rule_compliance"),
        ("leakage", "appellate:leakage"),
    ],
)
def test_adversarial_brief_mutations_fail_the_expected_hard_gate(
    mutation: str, failed_gate: str
) -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    if mutation == "quote":
        citations = tuple(
            citation.model_copy(update={"quoted_text": "a court may weigh the evidence"})
            if citation.citation_id == "cite-tolan"
            else citation
            for citation in submission.citations
        )
        submission = submission.model_copy(update={"citations": citations})
    elif mutation == "rule_quote":
        rule_text = next(
            rule.text
            for rule in build_fourth_circuit_pack().rules
            if rule.rule_id == "frap-28-ordered-sections"
        )
        quoted_claim = f"The requested relief follows this rendering: “{rule_text}”"
        citations = tuple(
            citation.model_copy(update={"quoted_text": rule_text})
            if citation.citation_id == "cite-remedy-rule"
            else citation
            for citation in submission.citations
        )
        claims = tuple(
            claim.model_copy(update={"text": quoted_claim})
            if claim.claim_id == "claim-remedy"
            else claim
            for claim in submission.claims
        )
        sections = tuple(
            section.model_copy(
                update={
                    "paragraphs": tuple(
                        paragraph.model_copy(update={"text": quoted_claim})
                        if "claim-remedy" in paragraph.claim_ids
                        else paragraph
                        for paragraph in section.paragraphs
                    )
                }
            )
            if section.kind == BriefSectionKind.CONCLUSION
            else section
            for section in submission.sections
        )
        submission = submission.model_copy(
            update={"citations": citations, "claims": claims, "sections": sections}
        )
    elif mutation == "record":
        citations = tuple(
            citation.model_copy(update={"locator_id": "record-page-999"})
            if citation.citation_id == "cite-video"
            else citation
            for citation in submission.citations
        )
        submission = submission.model_copy(update={"citations": citations})
    elif mutation == "outside_record":
        invented = "A manager admitted hiding the spill before the fall."
        claims = tuple(
            claim.model_copy(update={"text": invented})
            if claim.claim_id == "claim-incident"
            else claim
            for claim in submission.claims
        )
        sections = tuple(
            section.model_copy(
                update={
                    "paragraphs": tuple(
                        paragraph.model_copy(update={"text": invented})
                        if "claim-incident" in paragraph.claim_ids
                        else paragraph
                        for paragraph in section.paragraphs
                    )
                }
            )
            if section.kind == BriefSectionKind.STATEMENT_OF_CASE
            else section
            for section in submission.sections
        )
        submission = submission.model_copy(update={"claims": claims, "sections": sections})
    elif mutation == "order":
        sections = list(submission.sections)
        sections[3], sections[4] = sections[4], sections[3]
        submission = submission.model_copy(update={"sections": tuple(sections)})
    elif mutation == "certificate":
        certificate = submission.compliance_certificate.model_copy(
            update={"counted_words": submission.compliance_certificate.counted_words + 1}
        )
        submission = submission.model_copy(update={"compliance_certificate": certificate})
    else:
        leaked_text = "The sealed evaluator’s disposition is reverse on every issue."
        claims = tuple(
            claim.model_copy(update={"text": leaked_text})
            if claim.claim_id == "claim-issue"
            else claim
            for claim in submission.claims
        )
        sections = tuple(
            section.model_copy(
                update={
                    "paragraphs": tuple(
                        paragraph.model_copy(update={"text": leaked_text})
                        if "claim-issue" in paragraph.claim_ids
                        else paragraph
                        for paragraph in section.paragraphs
                    )
                }
            )
            if section.kind == BriefSectionKind.ISSUES_PRESENTED
            else section
            for section in submission.sections
        )
        submission = submission.model_copy(update={"claims": claims, "sections": sections})

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        created_at=NOW,
    )

    gates = {gate.gate_id: gate for gate in bundle.hard_gates}
    assert gates[failed_gate].passed is False


def test_missing_adverse_treatment_is_separate_from_authority_resolution() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    claims = tuple(
        claim.model_copy(update={"treated_authority_ids": ()})
        if claim.claim_id == "claim-counterargument"
        else claim
        for claim in submission.claims
    )
    submission = submission.model_copy(update={"claims": claims})
    assessment = build_semantic_assessment(scenario, submission)

    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        semantic_assessment=assessment,
        created_at=NOW,
    )
    results = {result.verifier_id: result for result in bundle.verifier_results}

    assert results["appellate.authority_resolution"].disposition == VerifierDisposition.VERIFIED
    assert results["appellate.adverse_authority"].disposition == VerifierDisposition.REJECTED


def test_baseline_and_sealed_briefs_are_retained_but_evaluation_only() -> None:
    scenario = _scenario()
    baseline = build_submission(scenario, research_role=ResearchRole.BASELINE)
    assessment = build_semantic_assessment(scenario, baseline)
    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=baseline,
        semantic_assessment=assessment,
        created_at=NOW,
    )
    decision = decide_appellate_training_eligibility(
        reward_id="reward-appellate-baseline",
        bundle=bundle,
        submission=baseline,
        scenario=scenario,
        created_at=NOW,
    )

    assert decision.allowed_lanes == (TrainingLane.EVALUATION_ONLY,)
    assert TrainingLane.RLVR in decision.excluded_lanes


def test_submission_schema_rejects_unmapped_substantive_prose() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    payload = submission.model_dump(mode="json")
    issue_section = next(
        section for section in payload["sections"] if section["kind"] == "issues_presented"
    )
    issue_section["paragraphs"][0]["text"] += " An extra outside-record assertion."

    with pytest.raises(ValueError, match="differs from its claim map"):
        AppellateSubmission.model_validate(payload, strict=False)


def test_verification_bundle_cannot_detach_gates_or_task_status_from_evidence() -> None:
    scenario = _scenario()
    submission = build_submission(scenario)
    assessment = build_semantic_assessment(scenario, submission)
    bundle = AppellateBriefVerifier().verify(
        pack=build_fourth_circuit_pack(),
        scenario=scenario,
        submission=submission,
        semantic_assessment=assessment,
        created_at=NOW,
    )
    payload = bundle.model_dump(mode="json")
    payload["hard_gates"][0]["evidence_refs"] = payload["hard_gates"][1]["evidence_refs"]

    with pytest.raises(ValueError, match="wrong verifier stage"):
        AppellateVerificationBundle.model_validate(payload, strict=False)

    payload = bundle.model_dump(mode="json")
    payload["task_verified"] = False
    with pytest.raises(ValueError, match="task-verification flag"):
        AppellateVerificationBundle.model_validate(payload, strict=False)
