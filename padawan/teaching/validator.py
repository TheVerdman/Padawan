from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from padawan.models.contracts import (
    CommentValidationStatus,
    GradeRecord,
    TeacherMode,
)
from padawan.teaching.contracts import TeacherOutput


@dataclass(frozen=True)
class CommentValidationResult:
    status: CommentValidationStatus
    errors: tuple[str, ...]


class CommentValidator:
    """Rejects teacher authority claims that are unsupported or leak forbidden answers."""

    def validate(
        self,
        output: TeacherOutput,
        *,
        grade: GradeRecord,
        mode: TeacherMode,
        public_step_ids: set[str],
        private_trace_token_count: int | None,
        prior_lesson_ids: set[str],
        expected_answer: dict[str, Any] | None,
        forbidden_transfer_answers: tuple[str, ...] = (),
    ) -> CommentValidationResult:
        errors: list[str] = []
        evidence_by_id = {evidence.evidence_id: evidence for evidence in grade.evidence}
        deterministic_citation = False
        for citation in output.citations:
            evidence = evidence_by_id.get(citation.evidence_id)
            if evidence is None:
                errors.append(f"nonexistent evidence reference: {citation.evidence_id}")
            elif evidence.kind == "deterministic":
                deterministic_citation = True
            if (
                citation.public_step_id is not None
                and citation.public_step_id not in public_step_ids
            ):
                errors.append(f"nonexistent public step: {citation.public_step_id}")
            if (
                citation.prior_lesson_id is not None
                and citation.prior_lesson_id not in prior_lesson_ids
            ):
                errors.append(f"nonexistent prior lesson: {citation.prior_lesson_id}")
            if citation.private_span_start is not None and citation.private_span_end is not None:
                if private_trace_token_count is None:
                    errors.append("private trace cited when capability is unavailable")
                elif citation.private_span_end > private_trace_token_count:
                    errors.append("private trace citation is out of bounds")

        claimed = output.claimed_first_consequential_error
        objective = grade.first_invalid_step_id
        if objective is not None and claimed != objective:
            errors.append(
                f"claimed first error {claimed!r} contradicts deterministic "
                f"first error {objective!r}"
            )
        if objective is None and claimed is not None and grade.deterministic:
            errors.append("teacher claims an invalid public step where the grader found none")
        if output.error_class and grade.error_class and output.error_class != grade.error_class:
            errors.append(
                f"teacher error class {output.error_class!r} contradicts {grade.error_class!r}"
            )
        if output.confidence >= 0.9 and not deterministic_citation:
            errors.append("unsupported certainty: high confidence lacks deterministic evidence")

        combined = f"{output.lesson}\n{output.repair}"
        if (
            mode
            in {
                TeacherMode.SOCRATIC_HINT,
                TeacherMode.GENERAL_PRINCIPLE,
                TeacherMode.METACOGNITIVE_FEEDBACK,
            }
            and expected_answer is not None
        ):
            combined_normal = _normal_form(combined)
            leaked = {
                fragment
                for fragment in _answer_fragments(expected_answer)
                if fragment in combined_normal
            }
            if leaked:
                errors.append(f"answer leakage forbidden in {mode.value}: {sorted(leaked)}")
        combined_normal = _normal_form(combined)
        for transfer_answer in forbidden_transfer_answers:
            fragments = _serialized_answer_fragments(transfer_answer)
            if any(fragment in combined_normal for fragment in fragments):
                errors.append("transfer-item answer leakage")
                break
        status = CommentValidationStatus.REJECTED if errors else CommentValidationStatus.ACCEPTED
        return CommentValidationResult(status=status, errors=tuple(errors))


def _answer_fragments(answer: dict[str, Any]) -> set[str]:
    fragments: set[str] = set()
    for value in answer.get("values", []):
        normalized = _normal_form(str(value))
        if normalized:
            fragments.add(normalized)
    expression = answer.get("expression")
    if expression:
        normalized = _normal_form(str(expression))
        if normalized:
            fragments.add(normalized)
    return fragments


def _serialized_answer_fragments(serialized_answer: str) -> set[str]:
    try:
        answer = json.loads(serialized_answer)
    except json.JSONDecodeError:
        answer = None
    if isinstance(answer, dict):
        fragments = _answer_fragments(answer)
        if fragments:
            return fragments
    normalized = _normal_form(serialized_answer)
    return {normalized} if normalized else set()


def _normal_form(text: str) -> str:
    return re.sub(r"\s+", "", text.lower().replace("^", "**"))
