from __future__ import annotations

import json
from datetime import UTC, datetime

from padawan.artifacts.store import LocalArtifactStore
from padawan.grading.algebra import AlgebraGrader
from padawan.models.contracts import (
    AttemptRecord,
    CommentValidationStatus,
    SamplingConfiguration,
    StudentStateRecord,
    TeacherMode,
)
from padawan.models.hashing import sha256_digest
from padawan.teaching.contracts import TeacherOutput
from padawan.teaching.service import TeacherContext, TeacherService
from padawan.teaching.validator import CommentValidator
from tests.helpers import (
    CallbackGenerationClient,
    public_derivation,
    runtime_capabilities,
    teacher_response,
)


def _grade(*, invalid_step: bool = False):
    expected = {
        "kind": "solution_set",
        "variable": "x",
        "values": ["5"],
        "exclusions": [],
    }
    response: object = json.loads(public_derivation(expected, correct=False))
    if invalid_step:
        response = {
            "steps": [
                {
                    "step_id": "s1",
                    "before": "x = 1",
                    "operation": "invalid",
                    "after": "x = 2",
                    "assumptions": [],
                }
            ],
            "final_answer": expected,
        }
    return AlgebraGrader().grade(
        attempt_id="attempt-teacher", response=response, expected_answer=expected
    )


def _output(grade, **updates: object) -> TeacherOutput:
    deterministic = next(
        evidence for evidence in grade.evidence if evidence.kind == "deterministic"
    )
    payload: dict[str, object] = {
        "citations": [{"evidence_id": deterministic.evidence_id}],
        "claimed_first_consequential_error": grade.first_invalid_step_id,
        "error_class": grade.error_class,
        "lesson": "Check the final result against the original relation.",
        "repair": "Recompute carefully without copying a transfer answer.",
        "expected_transfer_scope": "similar unseen algebra items",
        "confidence": 0.8,
    }
    payload.update(updates)
    return TeacherOutput.model_validate(payload)


def _validate(output: TeacherOutput, grade, **updates: object):
    arguments = {
        "grade": grade,
        "mode": TeacherMode.DIAGNOSTIC_CRITIQUE,
        "public_step_ids": {"s1"},
        "private_trace_token_count": None,
        "prior_lesson_ids": set(),
        "expected_answer": {
            "kind": "solution_set",
            "variable": "x",
            "values": ["5"],
            "exclusions": [],
        },
        "forbidden_transfer_answers": (),
    }
    arguments.update(updates)
    return CommentValidator().validate(output, **arguments)


def test_nonexistent_evidence_and_public_step_fail() -> None:
    grade = _grade()
    output = _output(
        grade,
        citations=[{"evidence_id": "missing", "public_step_id": "not-a-step"}],
    )
    result = _validate(output, grade)
    assert result.status == CommentValidationStatus.REJECTED
    assert any("nonexistent evidence" in error for error in result.errors)
    assert any("nonexistent public step" in error for error in result.errors)


def test_comment_contradicting_sympy_first_error_fails() -> None:
    grade = _grade(invalid_step=True)
    output = _output(grade, claimed_first_consequential_error="not-s1")
    result = _validate(output, grade)
    assert any("contradicts deterministic first error" in error for error in result.errors)


def test_missing_first_error_localization_fails() -> None:
    grade = _grade(invalid_step=True)
    output = _output(grade, claimed_first_consequential_error=None)
    result = _validate(output, grade)
    assert result.status == CommentValidationStatus.REJECTED


def test_socratic_answer_and_transfer_leakage_fail() -> None:
    grade = _grade()
    answer_output = _output(grade, lesson="The answer is 5.")
    answer_result = _validate(answer_output, grade, mode=TeacherMode.SOCRATIC_HINT)
    assert any("answer leakage" in error for error in answer_result.errors)
    transfer_output = _output(grade, repair="Use secret-transfer-answer now.")
    transfer_result = _validate(
        transfer_output,
        grade,
        forbidden_transfer_answers=("secret-transfer-answer",),
    )
    assert "transfer-item answer leakage" in transfer_result.errors


def test_structured_transfer_answer_fragment_leakage_fails() -> None:
    grade = _grade()
    output = _output(grade, repair="The answer to the unseen transfer item is 37.")
    result = _validate(
        output,
        grade,
        forbidden_transfer_answers=(
            json.dumps(
                {
                    "kind": "solution_set",
                    "variable": "x",
                    "values": ["37"],
                    "exclusions": [],
                }
            ),
        ),
    )
    assert "transfer-item answer leakage" in result.errors


def test_high_confidence_without_deterministic_citation_fails() -> None:
    grade = _grade()
    public_evidence = next(
        evidence for evidence in grade.evidence if evidence.kind == "public_step"
    )
    output = _output(
        grade,
        citations=[{"evidence_id": public_evidence.evidence_id, "public_step_id": "s1"}],
        confidence=0.99,
    )
    result = _validate(output, grade)
    assert "unsupported certainty: high confidence lacks deterministic evidence" in result.errors


async def test_teacher_retry_preserves_failed_raw_output(tmp_path) -> None:
    artifacts = LocalArtifactStore(tmp_path)
    expected = {
        "kind": "solution_set",
        "variable": "x",
        "values": ["5"],
        "exclusions": [],
    }
    grade = _grade()
    raw_generation = artifacts.put_text("raw", restricted=True, raw_data=True)
    attempt = AttemptRecord(
        attempt_id="attempt-teacher",
        episode_id="episode-teacher",
        item_id="item-teacher",
        state_before_id="state-teacher",
        rendered_messages=(),
        rendered_prompt="prompt",
        raw_generation_ref=raw_generation,
        public_derivation=json.loads(public_derivation(expected, correct=False)),
        final_answer={"kind": "solution_set", "values": ["999"], "exclusions": []},
        timing_ms={"total": 1.0},
        stop_reason="completed",
        request_id="student-request",
        model_id="student",
        checkpoint_id="checkpoint",
        runtime_id="runtime",
        runtime_version="1",
        sampling=SamplingConfiguration(max_output_tokens=100),
        capabilities=runtime_capabilities(),
        created_at=datetime.now(UTC),
    )
    semantic = {
        "state_id": "state-teacher",
        "student_id": "student",
        "checkpoint_id": "checkpoint",
        "runtime_id": "runtime",
        "parent_state_id": None,
        "branch_id": "branch",
        "compacted_working_state": {},
        "lesson_memory_refs": [],
        "unresolved_hypotheses": [],
        "competency_estimates": [],
        "active_experiment_id": None,
        "creation_reason": "test",
        "lifecycle_status": "canonical",
        "created_at": datetime.now(UTC).isoformat(),
    }
    state = StudentStateRecord.model_validate(
        {**semantic, "state_hash": sha256_digest(semantic)}, strict=False
    )
    calls = 0

    def callback(request):
        nonlocal calls
        calls += 1
        return "not-json" if calls == 1 else teacher_response(request)

    client = CallbackGenerationClient(callback, "teacher-test")
    result = await TeacherService(client=client, artifacts=artifacts).request_with_validation(
        TeacherContext(
            episode_id="episode-teacher",
            task={"prompt": "solve"},
            attempt=attempt,
            grade=grade,
            student_state=state,
            mode=TeacherMode.DIAGNOSTIC_CRITIQUE,
            guidance_budget_tokens=200,
            expected_answer=expected,
            forbidden_transfer_answers=(
                json.dumps(
                    {
                        "kind": "solution_set",
                        "variable": "x",
                        "values": ["never-disclose-transfer-answer"],
                        "exclusions": [],
                    }
                ),
            ),
        ),
        max_attempts=2,
    )
    assert result.intervention.validation_status == CommentValidationStatus.ACCEPTED
    assert len(result.failed_attempts) == 1
    first_prompt = json.loads(str(client.calls[0].input))
    assert "never-disclose-transfer-answer" not in str(client.calls[0].input)
    assert first_prompt["forbidden"]["transfer_item_answers_withheld"] is True
    assert first_prompt["forbidden"]["transfer_item_count"] == 1
    failed = result.failed_attempts[0]
    assert artifacts.read_bytes(failed.raw_response_ref, allow_restricted=True).startswith(b"{")
