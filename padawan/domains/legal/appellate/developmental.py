from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from padawan.adapters.base import GenerationRequest
from padawan.domains.contracts import VerifierDisposition
from padawan.domains.developmental.contracts import DomainAttemptContent
from padawan.domains.legal.appellate.adjudication import AppellateAdjudicationService
from padawan.domains.legal.appellate.contracts import (
    AppellateComplianceCertificate,
    AppellateScenarioManifest,
    AppellateSubmission,
    AppellateSubmissionDraft,
    AuthorityCurrentnessAssessment,
)
from padawan.domains.legal.appellate.court_pack import build_fourth_circuit_pack
from padawan.domains.legal.appellate.verifier import AppellateBriefVerifier
from padawan.models.contracts import (
    AttemptRecord,
    CorpusItemRecord,
    EvidenceRecord,
    GradeOutcome,
    GradeRecord,
    ResearchRole,
    SamplingConfiguration,
    StudentStateRecord,
)
from padawan.models.hashing import sha256_digest


class AppellateDevelopmentalAuthority:
    domain_id = "legal.appellate.fourth_circuit"
    competency_ids: tuple[str, ...] = (
        "appellate.ambiguous_video",
        "appellate.conclusive_video",
        "appellate.preservation_transfer",
    )

    def __init__(
        self,
        *,
        adjudicator: AppellateAdjudicationService,
        currentness_assessments: tuple[AuthorityCurrentnessAssessment, ...] = (),
    ) -> None:
        self.adjudicator = adjudicator
        self.currentness_assessments = currentness_assessments
        self.pack = build_fourth_circuit_pack()
        self.verifier = AppellateBriefVerifier()

    def build_student_request(
        self,
        *,
        request_id: str,
        item: CorpusItemRecord,
        state: StudentStateRecord,
        intervention: dict[str, Any] | None,
        retrieved_lessons: dict[str, Any],
    ) -> GenerationRequest:
        scenario = _scenario(item)
        context = {
            "closed_task": {
                "court_pack": self.pack.model_dump(mode="json"),
                "scenario": scenario.model_dump(mode="json"),
            },
            "persistent_student_state": {
                "compacted_working_state": state.compacted_working_state,
                "lesson_memory_refs": list(state.lesson_memory_refs),
            },
            "retrieved_lesson_memory": retrieved_lessons,
            "current_intervention": intervention,
        }
        return GenerationRequest(
            request_id=request_id,
            instructions=(
                "Act as the persistent student agent. Draft the appellant's principal brief "
                "only from the supplied closed court pack and synthetic record. Return only the "
                "AppellateSubmissionDraft schema. Every substantive paragraph must be exactly "
                "the joined text of its mapped claims; every claim must carry exact admitted "
                "record, rule, and authority locators. Treat outside authorities as unresolved. "
                "Do not claim that any authority is good law or use outside research."
            ),
            input=json.dumps(context, sort_keys=True),
            sampling=SamplingConfiguration(
                temperature=0.0,
                top_p=None,
                max_output_tokens=12_000,
                seed=item.generation_seed,
                top_logprobs=None,
            ),
            schema_name="padawan_appellate_submission_draft",
            json_schema=AppellateSubmissionDraft.model_json_schema(),
            metadata={
                "domain_id": self.domain_id,
                "item_id": item.item_id,
                "scenario_id": scenario.scenario_id,
                "state_id": state.state_id,
            },
            store=False,
        )

    def decode_student_output(
        self,
        *,
        output_text: str,
        item: CorpusItemRecord,
        research_role: ResearchRole,
        created_at: datetime,
    ) -> DomainAttemptContent:
        draft = AppellateSubmissionDraft.model_validate_json(output_text)
        scenario = _scenario(item)
        identity = sha256_digest(
            {
                "item_id": item.item_id,
                "scenario_id": scenario.scenario_id,
                "research_role": research_role,
                "draft": draft,
            }
        )
        brief_id = f"brief-{identity[7:31]}"
        submission = AppellateSubmission(
            brief_id=brief_id,
            scenario_id=scenario.scenario_id,
            court_pack_digest=scenario.court_pack_digest,
            brief_type=scenario.brief_type,
            research_role=research_role,
            sections=draft.sections,
            claims=draft.claims,
            citations=draft.citations,
            compliance_certificate=AppellateComplianceCertificate(
                brief_id=brief_id,
                counted_words=draft.compliance.counted_words,
                typeface_points=draft.compliance.typeface_points,
                uses_word_count_limit=draft.compliance.uses_word_count_limit,
                service_certified=draft.compliance.service_certified,
            ),
            created_at=created_at,
        )
        return DomainAttemptContent(
            public_derivation=None,
            final_answer=submission.model_dump(mode="json"),
        )

    async def grade_attempt(
        self,
        *,
        run_id: str,
        phase: str,
        item: CorpusItemRecord,
        attempt: AttemptRecord,
    ) -> GradeRecord:
        if not isinstance(attempt.final_answer, dict):
            return _malformed_grade(attempt.attempt_id, "submission is not a JSON object")
        try:
            submission = AppellateSubmission.model_validate(attempt.final_answer, strict=False)
        except ValueError as exc:
            return _malformed_grade(attempt.attempt_id, str(exc))
        scenario = _scenario(item)
        deterministic_bundle = self.verifier.verify(
            pack=self.pack,
            scenario=scenario,
            submission=submission,
            currentness_assessments=self.currentness_assessments,
        )
        assessment = None
        if all(gate.passed for gate in deterministic_bundle.hard_gates):
            assessment = await self.adjudicator.adjudicate(
                run_id=run_id,
                phase=phase,
                pack=self.pack,
                scenario=scenario,
                submission=submission,
            )
        bundle = self.verifier.verify(
            pack=self.pack,
            scenario=scenario,
            submission=submission,
            semantic_assessment=assessment,
            currentness_assessments=self.currentness_assessments,
        )
        hard_fail = any(not gate.passed for gate in bundle.hard_gates)
        semantic = [
            result
            for result in bundle.verifier_results
            if result.verifier_id
            in {
                "appellate.proposition_support",
                "appellate.applicability",
                "appellate.adverse_authority",
                "appellate.issue_remedy_coverage",
            }
        ]
        semantic_decided = all(
            result.disposition in {VerifierDisposition.VERIFIED, VerifierDisposition.REJECTED}
            for result in semantic
        )
        utility = _observed_utility(bundle)
        if hard_fail:
            outcome = GradeOutcome.INVALID_PROCESS
            score = 0.0
            error_class = "appellate_integrity_gate_failure"
        elif bundle.task_verified:
            outcome = GradeOutcome.CORRECT
            score = utility
            error_class = None
        elif semantic_decided:
            outcome = GradeOutcome.PARTIAL
            score = min(0.49, utility)
            error_class = "appellate_semantic_rejection"
        else:
            outcome = GradeOutcome.PARTIAL
            score = min(0.24, utility)
            error_class = "appellate_semantic_unknown"
        evidence = tuple(
            EvidenceRecord(
                evidence_id=result.result_id,
                kind="deterministic" if result.deterministic else "system",
                statement=result.summary,
                payload=result.model_dump(mode="json"),
                artifact_refs=result.artifact_refs,
            )
            for result in bundle.verifier_results
        )
        unsupported = tuple(
            str(error)
            for result in bundle.verifier_results
            for error in result.evidence.get("errors", [])
        )
        parsed_answer: dict[str, Any] = {
            "submission": submission.model_dump(mode="json"),
            "verification_bundle": bundle.model_dump(mode="json"),
            "semantic_assessment": (
                assessment.model_dump(mode="json") if assessment is not None else None
            ),
        }
        artifacts = tuple(
            dict.fromkeys(
                artifact for result in bundle.verifier_results for artifact in result.artifact_refs
            )
        )
        return GradeRecord(
            grade_id=f"grade-appellate-{sha256_digest(attempt.attempt_id)[7:31]}",
            attempt_id=attempt.attempt_id,
            grader_type=self.verifier.verifier_id,
            grader_version=self.verifier.verifier_version,
            outcome=outcome,
            score=score,
            deterministic=assessment is None,
            evidence=evidence,
            parsed_answer=parsed_answer,
            public_step_validation=(),
            first_invalid_step_id=None,
            error_class=error_class,
            confidence=1.0 if hard_fail else 0.8,
            unsupported_claims=unsupported,
            infrastructure_failure=False,
            student_failure=hard_fail or not bundle.task_verified,
            artifacts=artifacts,
            created_at=datetime.now(UTC),
        )

    def teacher_task(self, item: CorpusItemRecord) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "item_id": item.item_id,
            "court_pack": self.pack.model_dump(mode="json"),
            "scenario": _scenario(item).model_dump(mode="json"),
            "currentness_capability": "unknown_without_citator",
        }

    def expected_answer(self, item: CorpusItemRecord) -> dict[str, Any] | None:
        del item
        return None

    def forbidden_transfer_answer(self, item: CorpusItemRecord) -> str | None:
        del item
        return None


def _scenario(item: CorpusItemRecord) -> AppellateScenarioManifest:
    return AppellateScenarioManifest.model_validate(
        item.verifier_spec.parameters["scenario"], strict=False
    )


def _observed_utility(bundle: Any) -> float:
    values = [
        float(observation.value)
        for observation in bundle.reward_observations
        if observation.value is not None
    ]
    return sum(values) / len(values) if values else 0.0


def _malformed_grade(attempt_id: str, reason: str) -> GradeRecord:
    evidence = EvidenceRecord(
        evidence_id=f"appellate-malformed-{sha256_digest(attempt_id)[7:31]}",
        kind="deterministic",
        statement="The student output did not satisfy the appellate submission contract.",
        payload={"error": reason},
    )
    return GradeRecord(
        grade_id=f"grade-appellate-{sha256_digest(attempt_id)[7:31]}",
        attempt_id=attempt_id,
        grader_type="appellate.closed_record",
        grader_version="input-contract-v1",
        outcome=GradeOutcome.MALFORMED,
        score=0.0,
        deterministic=True,
        evidence=(evidence,),
        parsed_answer=None,
        public_step_validation=(),
        first_invalid_step_id=None,
        error_class="malformed_appellate_submission",
        confidence=1.0,
        unsupported_claims=(),
        infrastructure_failure=False,
        student_failure=True,
        created_at=datetime.now(UTC),
    )
