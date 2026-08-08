from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from padawan.adapters.base import GenerationRequest
from padawan.domains.legal.appellate.contracts import (
    AppellateCitationKind,
    AppellateCourtPack,
    AppellateScenarioManifest,
    AppellateSemanticAssessment,
    AppellateSemanticAssessmentDraft,
    AppellateSubmission,
    SemanticAssessmentMethod,
)
from padawan.domains.legal.appellate.verifier import AppellateBriefVerifier
from padawan.models.contracts import SamplingConfiguration
from padawan.models.hashing import sha256_digest
from padawan.orchestration.external_calls import IdempotentGenerationExecutor

_SEMANTIC_STAGES = {
    "appellate.proposition_support",
    "appellate.applicability",
    "appellate.adverse_authority",
    "appellate.issue_remedy_coverage",
}


class AppellateAdjudicationError(RuntimeError):
    """A governed adjudicator could not produce evidence-bound semantic findings."""


class AppellateAdjudicationService:
    """Run a model adjudicator over only the evidence admitted for each claim."""

    def __init__(
        self,
        *,
        executor: IdempotentGenerationExecutor,
        provider: str,
        max_attempts: int = 3,
        max_output_tokens: int = 8_000,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("adjudicator attempts must be positive")
        if max_output_tokens < 1_000:
            raise ValueError("adjudicator output budget is too small")
        self.executor = executor
        self.provider = provider
        self.max_attempts = max_attempts
        self.max_output_tokens = max_output_tokens

    async def adjudicate(
        self,
        *,
        run_id: str,
        phase: str,
        pack: AppellateCourtPack,
        scenario: AppellateScenarioManifest,
        submission: AppellateSubmission,
    ) -> AppellateSemanticAssessment:
        correction_errors: tuple[str, ...] = ()
        failures: list[str] = []
        for attempt_number in range(1, self.max_attempts + 1):
            request_id = f"{run_id}:appellate:adjudicator:{phase}:attempt:{attempt_number}"
            request = GenerationRequest(
                request_id=request_id,
                instructions=(
                    "You are a governed appellate semantic adjudicator, not a citator and not "
                    "counsel. Decide proposition support, applicability, adverse-authority "
                    "treatment, and issue/preservation/remedy coverage only from the admitted "
                    "closed-record evidence attached to each claim. Use `unknown` when that "
                    "evidence is insufficient. Never use outside knowledge, infer authority "
                    "currentness, or make a good-law assertion. Return only the required schema."
                ),
                input=json.dumps(
                    _adjudication_payload(
                        pack=pack,
                        scenario=scenario,
                        submission=submission,
                        correction_errors=correction_errors,
                    ),
                    sort_keys=True,
                ),
                sampling=SamplingConfiguration(
                    temperature=0.0,
                    top_p=None,
                    max_output_tokens=self.max_output_tokens,
                    seed=scenario.seed,
                    top_logprobs=None,
                ),
                schema_name="padawan_appellate_semantic_assessment",
                json_schema=AppellateSemanticAssessmentDraft.model_json_schema(),
                metadata={
                    "domain_id": "legal.appellate.fourth_circuit",
                    "scenario_id": scenario.scenario_id,
                    "brief_id": submission.brief_id,
                    "research_role": "adjudicator",
                },
                store=False,
            )
            response = await self.executor.execute(
                run_id=run_id,
                purpose="appellate_semantic_adjudication",
                provider=self.provider,
                request=request,
            )
            try:
                draft = AppellateSemanticAssessmentDraft.model_validate_json(response.output_text)
            except ValidationError as exc:
                correction_errors = ("adjudicator output did not satisfy the schema", str(exc))
                failures.append(correction_errors[0])
                continue
            identity = sha256_digest(
                {
                    "scenario_id": scenario.scenario_id,
                    "submission_digest": sha256_digest(submission.model_dump(mode="json")),
                    "provider": response.provider,
                    "model_id": response.model_id,
                    "draft": draft,
                }
            )
            assessment = AppellateSemanticAssessment(
                assessment_id=f"appellate-assessment-{identity[7:31]}",
                scenario_id=scenario.scenario_id,
                submission_digest=sha256_digest(submission.model_dump(mode="json")),
                adjudicator_id=f"{response.provider}:{response.model_id}",
                method=SemanticAssessmentMethod.GOVERNED_MODEL,
                model_id=response.model_id,
                claim_assessments=draft.claim_assessments,
                adverse_authority_assessments=draft.adverse_authority_assessments,
                issue_coverage=draft.issue_coverage,
                preservation_coverage=draft.preservation_coverage,
                remedy_coverage=draft.remedy_coverage,
                created_at=datetime.now(UTC),
            )
            validation = AppellateBriefVerifier().verify(
                pack=pack,
                scenario=scenario,
                submission=submission,
                semantic_assessment=assessment,
            )
            binding_errors = _semantic_binding_errors(validation.verifier_results)
            if not binding_errors:
                return assessment
            correction_errors = binding_errors
            failures.extend(binding_errors)
        detail = "; ".join(dict.fromkeys(failures)) or "no valid adjudicator output"
        raise AppellateAdjudicationError(
            f"appellate semantic adjudication failed its evidence boundary: {detail}"
        )


def _adjudication_payload(
    *,
    pack: AppellateCourtPack,
    scenario: AppellateScenarioManifest,
    submission: AppellateSubmission,
    correction_errors: tuple[str, ...],
) -> dict[str, Any]:
    passage_by_id = {
        passage.passage_id: {
            "authority_id": authority.authority_id,
            "case_name": authority.case_name,
            "canonical_citation": authority.canonical_citation,
            "court": authority.court,
            "decided_on": authority.decided_on.isoformat(),
            "published": authority.published,
            "weight": authority.weight.value,
            "passage_id": passage.passage_id,
            "official_page": passage.official_page,
            "pinpoint_citation": passage.pinpoint_citation,
            "text": passage.text,
        }
        for authority in pack.authorities
        for passage in authority.passages
    }
    rule_by_id = {
        rule.rule_id: {
            "rule_id": rule.rule_id,
            "citation": rule.citation,
            "pinpoint": rule.pinpoint,
            "text": rule.text,
        }
        for rule in pack.rules
    }
    fact_by_id = {
        fact.fact_id: {
            "fact_id": fact.fact_id,
            "statement": fact.statement,
            "page_ids": list(fact.page_ids),
        }
        for fact in scenario.record.facts
    }
    citations_by_claim: dict[str, list[Any]] = {}
    for citation in submission.citations:
        citations_by_claim.setdefault(citation.claim_id, []).append(citation)
    claim_packets: list[dict[str, Any]] = []
    for claim in submission.claims:
        citations = citations_by_claim.get(claim.claim_id, [])
        passage_ids = {
            citation.locator_id
            for citation in citations
            if citation.kind == AppellateCitationKind.AUTHORITY
        }
        rule_ids = {
            citation.source_id
            for citation in citations
            if citation.kind == AppellateCitationKind.RULE
        }
        claim_packets.append(
            {
                "claim": claim.model_dump(mode="json"),
                "citations": [citation.model_dump(mode="json") for citation in citations],
                "admitted_authority_passages": [
                    passage_by_id[passage_id]
                    for passage_id in sorted(passage_ids)
                    if passage_id in passage_by_id
                ],
                "admitted_rules": [
                    rule_by_id[rule_id] for rule_id in sorted(rule_ids) if rule_id in rule_by_id
                ],
                "admitted_record_facts": [
                    fact_by_id[fact_id]
                    for fact_id in claim.record_fact_ids
                    if fact_id in fact_by_id
                ],
            }
        )
    return {
        "scope": {
            "scenario_id": scenario.scenario_id,
            "brief_id": submission.brief_id,
            "submission_digest": sha256_digest(submission.model_dump(mode="json")),
            "court": pack.court,
            "jurisdiction": pack.jurisdiction,
            "brief_type": scenario.brief_type.value,
            "procedural_posture": scenario.procedural_posture,
            "issue": scenario.issue,
            "requested_relief": scenario.requested_relief,
            "required_adverse_authority_ids": list(scenario.required_adverse_authority_ids),
            "currentness_available": False,
        },
        "claim_packets": claim_packets,
        "correction_errors_from_prior_attempt": list(correction_errors),
    }


def _semantic_binding_errors(results: tuple[Any, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    for result in results:
        if result.verifier_id not in _SEMANTIC_STAGES:
            continue
        for error in result.evidence.get("assessment_errors", []):
            errors.append(str(error))
    return tuple(dict.fromkeys(errors))
