from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from padawan.adapters.base import GenerationRequest
from padawan.domains.contracts import VerifierDisposition
from padawan.domains.developmental.contracts import DomainAttemptContent
from padawan.domains.lean_math.verifier import LeanProofTask, LeanVerifier
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


class LeanDevelopmentalAuthority:
    domain_id = "math.lean"
    competency_ids: tuple[str, ...] = (
        "lean_math.int_linear_equation",
        "lean_math.nat_linear_bound",
    )

    def __init__(self, verifier: LeanVerifier) -> None:
        self.verifier = verifier

    def build_student_request(
        self,
        *,
        request_id: str,
        item: CorpusItemRecord,
        state: StudentStateRecord,
        intervention: dict[str, Any] | None,
        retrieved_lessons: dict[str, Any],
    ) -> GenerationRequest:
        context = {
            "task": item.prompt,
            "statement": item.verifier_spec.parameters["statement"],
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
                "Act as the persistent student agent. Complete the pinned Lean theorem using "
                "legitimate accumulated state. Return only a tactic proof beginning with `by`. "
                "Do not return imports, declarations, markdown fences, prose, `sorry`, or `admit`."
            ),
            input=json.dumps(context, sort_keys=True),
            sampling=SamplingConfiguration(
                temperature=0.0,
                top_p=None,
                max_output_tokens=2_000,
                seed=item.generation_seed,
                top_logprobs=None,
            ),
            metadata={
                "domain_id": self.domain_id,
                "item_id": item.item_id,
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
        del item, research_role, created_at
        proof = output_text.strip()
        if not proof:
            raise ValueError("Lean student returned an empty proof")
        return DomainAttemptContent(public_derivation=None, final_answer=proof)

    async def grade_attempt(
        self,
        *,
        run_id: str,
        phase: str,
        item: CorpusItemRecord,
        attempt: AttemptRecord,
    ) -> GradeRecord:
        del run_id, phase
        proof = attempt.final_answer if isinstance(attempt.final_answer, str) else ""
        statement = str(item.verifier_spec.parameters["statement"])
        if not proof.strip():
            return _malformed_grade(attempt.attempt_id, statement)
        result = await asyncio.to_thread(
            self.verifier.verify,
            LeanProofTask(task_id=item.item_id, statement=statement, proof=proof),
        )
        outcome, score, error_class, infrastructure, student_failure = {
            VerifierDisposition.VERIFIED: (GradeOutcome.CORRECT, 1.0, None, False, False),
            VerifierDisposition.REJECTED: (
                GradeOutcome.INCORRECT,
                0.0,
                "lean_kernel_rejection",
                False,
                True,
            ),
            VerifierDisposition.INFRASTRUCTURE_FAILURE: (
                GradeOutcome.INFRASTRUCTURE_FAILURE,
                0.0,
                "lean_verifier_infrastructure",
                True,
                False,
            ),
            VerifierDisposition.UNKNOWN: (
                GradeOutcome.GRADER_FAILURE,
                0.0,
                "lean_verifier_unknown",
                True,
                False,
            ),
        }[result.disposition]
        evidence = EvidenceRecord(
            evidence_id=result.result_id,
            kind="deterministic",
            statement=result.summary,
            payload=result.model_dump(mode="json"),
            artifact_refs=result.artifact_refs,
        )
        return GradeRecord(
            grade_id=f"grade-lean-{sha256_digest(attempt.attempt_id)[7:31]}",
            attempt_id=attempt.attempt_id,
            grader_type=self.verifier.verifier_id,
            grader_version=self.verifier.verifier_version,
            outcome=outcome,
            score=score,
            deterministic=True,
            evidence=(evidence,),
            parsed_answer={"kind": "lean4_proof", "statement": statement, "proof": proof},
            public_step_validation=(),
            first_invalid_step_id=None,
            error_class=error_class,
            confidence=1.0 if result.disposition != VerifierDisposition.UNKNOWN else None,
            unsupported_claims=(),
            infrastructure_failure=infrastructure,
            student_failure=student_failure,
            artifacts=result.artifact_refs,
            created_at=datetime.now(UTC),
        )

    def teacher_task(self, item: CorpusItemRecord) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "item_id": item.item_id,
            "prompt": item.prompt,
            "statement": item.verifier_spec.parameters["statement"],
            "verifier": item.verifier_spec.model_dump(mode="json"),
        }

    def expected_answer(self, item: CorpusItemRecord) -> dict[str, Any] | None:
        return dict(item.expected_answer) if item.expected_answer is not None else None

    def forbidden_transfer_answer(self, item: CorpusItemRecord) -> str | None:
        return (
            json.dumps(item.expected_answer, sort_keys=True)
            if item.expected_answer is not None
            else None
        )


def _malformed_grade(attempt_id: str, statement: str) -> GradeRecord:
    evidence = EvidenceRecord(
        evidence_id=f"lean-malformed-{sha256_digest(attempt_id)[7:31]}",
        kind="deterministic",
        statement="The student did not provide a non-empty Lean tactic proof.",
        payload={"statement_digest": sha256_digest(statement), "kernel_executed": False},
    )
    return GradeRecord(
        grade_id=f"grade-lean-{sha256_digest(attempt_id)[7:31]}",
        attempt_id=attempt_id,
        grader_type="lean4.kernel",
        grader_version="input-contract-v1",
        outcome=GradeOutcome.MALFORMED,
        score=0.0,
        deterministic=True,
        evidence=(evidence,),
        parsed_answer=None,
        public_step_validation=(),
        first_invalid_step_id=None,
        error_class="malformed_lean_proof",
        confidence=1.0,
        unsupported_claims=(),
        infrastructure_failure=False,
        student_failure=True,
        created_at=datetime.now(UTC),
    )
