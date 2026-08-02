from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from pydantic import ValidationError

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.artifacts.store import ArtifactBackend, artifact_put_bytes, artifact_put_text
from padawan.models.contracts import (
    ArtifactRef,
    AttemptRecord,
    CommentValidationStatus,
    GradeRecord,
    SamplingConfiguration,
    StudentStateRecord,
    TeacherInterventionRecord,
    TeacherMode,
    unreviewed_provider_output_rights,
)
from padawan.teaching.contracts import TeacherOutput
from padawan.teaching.validator import CommentValidator


class TeacherClient(Protocol):
    async def generate(self, request: GenerationRequest) -> GenerationResult: ...


@dataclass(frozen=True)
class TeacherContext:
    episode_id: str
    task: dict[str, Any]
    attempt: AttemptRecord
    grade: GradeRecord
    student_state: StudentStateRecord
    mode: TeacherMode
    guidance_budget_tokens: int
    expected_answer: dict[str, Any] | None
    prior_lessons: tuple[dict[str, Any], ...] = ()
    private_reasoning: str | None = None
    private_reasoning_policy_allows: bool = False
    forbidden_transfer_answers: tuple[str, ...] = ()


@dataclass(frozen=True)
class TeacherFailure:
    request_id: str
    provider: str
    raw_response_ref: ArtifactRef
    error: str
    validation_errors: tuple[str, ...]


@dataclass(frozen=True)
class TeacherRunResult:
    intervention: TeacherInterventionRecord
    failed_attempts: tuple[TeacherFailure, ...]


class TeacherExhaustedError(RuntimeError):
    def __init__(self, failures: tuple[TeacherFailure, ...]) -> None:
        super().__init__("teacher response failed validation within retry budget")
        self.failures = failures


class TeacherService:
    def __init__(
        self,
        *,
        client: TeacherClient,
        artifacts: ArtifactBackend,
        validator: CommentValidator | None = None,
    ) -> None:
        self.client = client
        self.artifacts = artifacts
        self.validator = validator or CommentValidator()

    async def request_with_validation(
        self,
        context: TeacherContext,
        *,
        max_attempts: int = 3,
        request_id_prefix: str | None = None,
    ) -> TeacherRunResult:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        failures: list[TeacherFailure] = []
        correction_errors: tuple[str, ...] = ()
        for attempt_number in range(1, max_attempts + 1):
            prompt = _build_prompt(context, correction_errors=correction_errors)
            prompt_ref = await artifact_put_text(
                self.artifacts,
                prompt,
                media_type="application/json; charset=utf-8",
                restricted=context.private_reasoning is not None,
                raw_data=True,
            )
            prefix = request_id_prefix or f"teacher-{context.episode_id}"
            request_id = f"{prefix}:attempt:{attempt_number}"
            request = GenerationRequest(
                request_id=request_id,
                instructions=(
                    "You are one teacher inside an evidence-governed institution. "
                    "Deterministic grader evidence outranks your interpretation. "
                    "Return only the schema."
                ),
                input=prompt,
                sampling=SamplingConfiguration(
                    temperature=0.2,
                    top_p=None,
                    max_output_tokens=context.guidance_budget_tokens,
                    seed=None,
                    top_logprobs=None,
                ),
                schema_name="padawan_teacher_intervention",
                json_schema=TeacherOutput.model_json_schema(),
                metadata={"episode_id": context.episode_id, "mode": context.mode.value},
                store=False,
            )
            response = await self.client.generate(request)
            raw_ref = await artifact_put_bytes(
                self.artifacts,
                response.raw_response,
                media_type="application/json",
                restricted=True,
                raw_data=True,
            )
            try:
                output = TeacherOutput.model_validate_json(response.output_text)
            except ValidationError as exc:
                failure = TeacherFailure(
                    request_id=request_id,
                    provider=response.provider,
                    raw_response_ref=raw_ref,
                    error="malformed teacher contract",
                    validation_errors=(str(exc),),
                )
                failures.append(failure)
                correction_errors = failure.validation_errors
                continue
            public_step_ids = {
                str(step.get("step_id"))
                for step in (context.attempt.public_derivation or {}).get("steps", [])
                if isinstance(step, dict) and step.get("step_id")
            }
            validation = self.validator.validate(
                output,
                grade=context.grade,
                mode=context.mode,
                public_step_ids=public_step_ids,
                private_trace_token_count=(
                    len(context.attempt.output_token_ids)
                    if context.private_reasoning is not None
                    and context.attempt.output_token_ids is not None
                    else None
                ),
                prior_lesson_ids={
                    str(lesson["lesson_id"])
                    for lesson in context.prior_lessons
                    if lesson.get("lesson_id")
                },
                expected_answer=context.expected_answer,
                forbidden_transfer_answers=context.forbidden_transfer_answers,
            )
            intervention = TeacherInterventionRecord(
                intervention_id=f"intervention-{uuid4()}",
                episode_id=context.episode_id,
                targeted_attempt_id=context.attempt.attempt_id,
                provider=response.provider,
                model_id=response.model_id,
                mode=context.mode,
                exact_prompt_ref=prompt_ref,
                raw_response_ref=raw_ref,
                citations=output.citations,
                claimed_first_consequential_error=output.claimed_first_consequential_error,
                error_class=output.error_class,
                lesson=output.lesson,
                repair=output.repair,
                expected_transfer_scope=output.expected_transfer_scope,
                confidence=output.confidence,
                validation_status=validation.status,
                validation_errors=validation.errors,
                request_id=request_id,
                response_id=response.response_id,
                token_usage=response.usage,
                latency_ms=response.latency_ms,
                estimated_cost_usd=None,
                output_rights=unreviewed_provider_output_rights(provider=response.provider),
                created_at=datetime.now(UTC),
            )
            if validation.status == CommentValidationStatus.ACCEPTED:
                return TeacherRunResult(intervention, tuple(failures))
            failure = TeacherFailure(
                request_id=request_id,
                provider=response.provider,
                raw_response_ref=raw_ref,
                error="comment validator rejected teacher response",
                validation_errors=validation.errors,
            )
            failures.append(failure)
            correction_errors = validation.errors
        raise TeacherExhaustedError(tuple(failures))


def _build_prompt(context: TeacherContext, *, correction_errors: tuple[str, ...]) -> str:
    private_reasoning: dict[str, Any]
    if context.private_reasoning_policy_allows and context.private_reasoning is not None:
        private_reasoning = {"availability": "available", "content": context.private_reasoning}
    else:
        private_reasoning = {
            "availability": "unavailable",
            "reason": (
                "policy forbids disclosure"
                if context.private_reasoning is not None
                else "runtime did not expose private reasoning"
            ),
        }
    payload = {
        "task": context.task,
        "student_attempt": {
            "attempt_id": context.attempt.attempt_id,
            "public_derivation": context.attempt.public_derivation,
            "final_answer": context.attempt.final_answer,
        },
        "private_reasoning": private_reasoning,
        "deterministic_grade": context.grade.model_dump(mode="json"),
        "student_developmental_state": {
            "state_id": context.student_state.state_id,
            "compacted_working_state": context.student_state.compacted_working_state,
            "competency_estimates": [
                item.model_dump(mode="json") for item in context.student_state.competency_estimates
            ],
            "unresolved_hypotheses": [
                item.model_dump(mode="json") for item in context.student_state.unresolved_hypotheses
            ],
        },
        "prior_lessons": list(context.prior_lessons),
        "intervention_mode": context.mode.value,
        "guidance_budget_tokens": context.guidance_budget_tokens,
        "forbidden": {
            "answer_leakage": context.mode
            in {
                TeacherMode.SOCRATIC_HINT,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.METACOGNITIVE_FEEDBACK,
            },
            "transfer_item_answers_withheld": True,
            "transfer_item_count": len(context.forbidden_transfer_answers),
        },
        "correction_errors_from_prior_teacher_attempt": list(correction_errors),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)
