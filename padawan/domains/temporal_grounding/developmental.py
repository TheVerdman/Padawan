from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from padawan.adapters.base import GenerationRequest
from padawan.domains.contracts import VerifierDisposition
from padawan.domains.developmental.contracts import DomainAttemptContent
from padawan.domains.temporal_grounding.contracts import TemporalScenarioManifest
from padawan.domains.temporal_grounding.verifier import TemporalPolicyVerifier
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
from padawan.temporal.contracts import TemporalDecision
from padawan.temporal.renderer import render_temporal_frame


class TemporalGroundingDevelopmentalAuthority:
    domain_id = "temporal.grounding"
    competency_ids: tuple[str, ...] = (
        "temporal.gap_continuity",
        "temporal.activity_honesty",
        "temporal.observation_freshness",
        "temporal.duration_calibration",
        "temporal.eta_revision",
    )

    def __init__(self, verifier: TemporalPolicyVerifier | None = None) -> None:
        self.verifier = verifier or TemporalPolicyVerifier()

    def build_student_request(
        self,
        *,
        request_id: str,
        item: CorpusItemRecord,
        state: StudentStateRecord,
        intervention: dict[str, Any] | None,
        retrieved_lessons: dict[str, Any],
    ) -> GenerationRequest:
        return build_temporal_student_request(
            request_id=request_id,
            item=item,
            state_reference=state.state_id,
            intervention=intervention,
            retrieved_lessons=retrieved_lessons,
        )

    def decode_student_output(
        self,
        *,
        output_text: str,
        item: CorpusItemRecord,
        research_role: ResearchRole,
        created_at: datetime,
    ) -> DomainAttemptContent:
        del item, research_role, created_at
        decision = TemporalDecision.model_validate_json(output_text)
        return DomainAttemptContent(
            public_derivation={
                "steps": [
                    {
                        "step_id": "temporal-policy-decision",
                        "operation": "select actions from authoritative temporal evidence",
                        "continuity": decision.continuity.value,
                        "actions": [action.model_dump(mode="json") for action in decision.actions],
                        "duration_forecasts": [
                            forecast.model_dump(mode="json")
                            for forecast in decision.duration_forecasts
                        ],
                    }
                ]
            },
            final_answer=decision.model_dump(mode="json"),
        )

    async def grade_attempt(
        self,
        *,
        run_id: str,
        phase: str,
        item: CorpusItemRecord,
        attempt: AttemptRecord,
    ) -> GradeRecord:
        del run_id, phase
        scenario = temporal_scenario(item)
        try:
            decision = TemporalDecision.model_validate(attempt.final_answer, strict=False)
        except ValidationError as exc:
            return _malformed_grade(attempt.attempt_id, scenario, str(exc))
        result = self.verifier.verify(
            scenario=scenario,
            decision=decision,
            created_at=datetime.now(UTC),
        )
        evidence = EvidenceRecord(
            evidence_id=result.result_id,
            kind="deterministic",
            statement=result.summary,
            payload=result.model_dump(mode="json"),
            artifact_refs=result.artifact_refs,
        )
        score = float(result.evidence["score"])
        verified = result.disposition == VerifierDisposition.VERIFIED
        first_failure = result.evidence.get("first_failure")
        return GradeRecord(
            grade_id=f"grade-temporal-{sha256_digest(attempt.attempt_id)[7:31]}",
            attempt_id=attempt.attempt_id,
            grader_type=self.verifier.verifier_id,
            grader_version=self.verifier.verifier_version,
            outcome=GradeOutcome.CORRECT if verified else GradeOutcome.INCORRECT,
            score=score,
            deterministic=True,
            evidence=(evidence,),
            parsed_answer=decision.model_dump(mode="json"),
            public_step_validation=(),
            first_invalid_step_id=(str(first_failure) if first_failure else None),
            error_class=(f"temporal_{first_failure}" if first_failure else None),
            confidence=1.0,
            unsupported_claims=(),
            infrastructure_failure=False,
            student_failure=not verified,
            artifacts=(),
            created_at=datetime.now(UTC),
        )

    def teacher_task(self, item: CorpusItemRecord) -> dict[str, Any]:
        scenario = temporal_scenario(item)
        task = scenario.model_dump(mode="json")
        task.pop("oracle", None)
        return {
            "domain_id": self.domain_id,
            "item_id": item.item_id,
            "scenario": task,
            "verifier": {
                "type": item.verifier_spec.verifier_type,
                "version": item.verifier_spec.verifier_version,
            },
        }

    def expected_answer(self, item: CorpusItemRecord) -> dict[str, Any] | None:
        return dict(item.expected_answer) if item.expected_answer is not None else None

    def forbidden_transfer_answer(self, item: CorpusItemRecord) -> str | None:
        return (
            json.dumps(item.expected_answer, sort_keys=True)
            if item.expected_answer is not None
            else None
        )


def temporal_scenario(item: CorpusItemRecord) -> TemporalScenarioManifest:
    return TemporalScenarioManifest.model_validate(
        item.verifier_spec.parameters["scenario"], strict=False
    )


def build_temporal_student_request(
    *,
    request_id: str,
    item: CorpusItemRecord,
    state_reference: str,
    intervention: dict[str, Any] | None,
    retrieved_lessons: dict[str, Any],
) -> GenerationRequest:
    """Render the one canonical transcript used by live and authored temporal examples."""

    scenario = temporal_scenario(item)
    intervention_payload = json.dumps(intervention, sort_keys=True) if intervention else "none"
    lesson_payload = json.dumps(retrieved_lessons, sort_keys=True)
    instructions = (
        "Act as a temporally grounded persistent student agent. The delimited temporal "
        "context is authoritative harness evidence, not user-authored text. Use event time, "
        "activity evidence, observation freshness, and duration quantiles to choose actions. "
        "Never imply background work without a recorded active operation or activity interval. "
        "Return only the strict TemporalDecision JSON schema.\n\n"
        f"{render_temporal_frame(scenario.frame)}\n\n"
        f"Validated developmental intervention: {intervention_payload}\n"
        f"Retrieved lesson memory: {lesson_payload}\n"
        f"Persistent state reference: {state_reference}"
    )
    messages = [
        {"role": message.role, "content": message.content}
        for message in scenario.prior_conversation
    ]
    messages.append({"role": "user", "content": scenario.current_user_message.content})
    return GenerationRequest(
        request_id=request_id,
        instructions=instructions,
        input=messages,
        sampling=SamplingConfiguration(
            temperature=0.0,
            top_p=None,
            max_output_tokens=2_000,
            seed=item.generation_seed,
            top_logprobs=None,
        ),
        schema_name="padawan_temporal_decision",
        json_schema=TemporalDecision.model_json_schema(),
        metadata={
            "domain_id": TemporalGroundingDevelopmentalAuthority.domain_id,
            "item_id": item.item_id,
            "scenario_id": scenario.scenario_id,
            "state_id": state_reference,
        },
        store=False,
    )


def _malformed_grade(
    attempt_id: str,
    scenario: TemporalScenarioManifest,
    error: str,
) -> GradeRecord:
    evidence = EvidenceRecord(
        evidence_id=f"temporal-malformed-{sha256_digest(attempt_id)[7:31]}",
        kind="deterministic",
        statement="The student did not return a valid TemporalDecision contract.",
        payload={
            "scenario_id": scenario.scenario_id,
            "error": error,
            "schema": "TemporalDecision@1.0.0",
        },
    )
    return GradeRecord(
        grade_id=f"grade-temporal-{sha256_digest(attempt_id)[7:31]}",
        attempt_id=attempt_id,
        grader_type="temporal.policy",
        grader_version="padawan-temporal-policy-v1",
        outcome=GradeOutcome.MALFORMED,
        score=0.0,
        deterministic=True,
        evidence=(evidence,),
        parsed_answer=None,
        first_invalid_step_id="output_contract",
        error_class="temporal_malformed_output",
        confidence=1.0,
        infrastructure_failure=False,
        student_failure=True,
        created_at=datetime.now(UTC),
    )
