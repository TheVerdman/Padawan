from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import sympy as sp
from pydantic import ValidationError

from padawan.grading.derivation import FinalAnswer, PublicDerivation, PublicStep
from padawan.models.contracts import EvidenceRecord, GradeOutcome, GradeRecord
from padawan.models.hashing import sha256_digest


class AlgebraGrader:
    """SymPy-backed final-answer and public-step grader."""

    grader_type = "symbolic_algebra"
    grader_version = "padawan-sympy-v1"

    def grade(
        self,
        *,
        attempt_id: str,
        response: str | bytes | dict[str, Any],
        expected_answer: dict[str, Any],
    ) -> GradeRecord:
        created_at = datetime.now(UTC)
        try:
            payload = _decode_response(response)
            derivation = PublicDerivation.model_validate(payload)
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            malformed_evidence = EvidenceRecord(
                evidence_id=f"ev-{sha256_digest(str(exc))[7:23]}",
                kind="deterministic",
                statement="The public derivation did not satisfy the required schema.",
                payload={"error": str(exc)},
            )
            return GradeRecord(
                grade_id=f"grade-{uuid4()}",
                attempt_id=attempt_id,
                grader_type=self.grader_type,
                grader_version=self.grader_version,
                outcome=GradeOutcome.MALFORMED,
                score=0.0,
                deterministic=True,
                evidence=(malformed_evidence,),
                parsed_answer=None,
                public_step_validation=(),
                first_invalid_step_id=None,
                error_class="malformed_output",
                confidence=None,
                unsupported_claims=(),
                infrastructure_failure=False,
                student_failure=True,
                created_at=created_at,
            )

        validations: list[dict[str, Any]] = []
        evidence: list[EvidenceRecord] = []
        first_invalid: str | None = None
        previous_after: str | None = None
        for step in derivation.steps:
            result = self._validate_step(step, previous_after=previous_after)
            validations.append(result)
            evidence.append(
                EvidenceRecord(
                    evidence_id=str(result["evidence_id"]),
                    kind="public_step",
                    statement=(
                        f"Public step {step.step_id} is symbolically valid."
                        if result["valid"]
                        else f"Public step {step.step_id} is not symbolically justified."
                    ),
                    payload=result,
                )
            )
            if not result["valid"] and first_invalid is None:
                first_invalid = step.step_id
            previous_after = step.after

        final_valid, final_detail = _validate_final(derivation.final_answer, expected_answer)
        final_evidence_id = f"ev-final-{sha256_digest(final_detail)[7:23]}"
        evidence.append(
            EvidenceRecord(
                evidence_id=final_evidence_id,
                kind="deterministic",
                statement=(
                    "The final answer matches the verified symbolic result."
                    if final_valid
                    else "The final answer does not match the verified symbolic result."
                ),
                payload=final_detail,
            )
        )
        all_steps_valid = first_invalid is None
        if final_valid and all_steps_valid:
            outcome = GradeOutcome.CORRECT
            score = 1.0
            error_class = None
        elif final_valid:
            outcome = GradeOutcome.INVALID_PROCESS
            score = 0.5
            error_class = "invalid_public_step"
        else:
            valid_step_fraction = sum(bool(item["valid"]) for item in validations) / len(
                validations
            )
            score = min(0.49, 0.35 * valid_step_fraction)
            outcome = GradeOutcome.PARTIAL if score > 0 else GradeOutcome.INCORRECT
            error_class = "incorrect_final_answer"
        return GradeRecord(
            grade_id=f"grade-{uuid4()}",
            attempt_id=attempt_id,
            grader_type=self.grader_type,
            grader_version=self.grader_version,
            outcome=outcome,
            score=score,
            deterministic=True,
            evidence=tuple(evidence),
            parsed_answer=derivation.final_answer.model_dump(mode="json"),
            public_step_validation=tuple(validations),
            first_invalid_step_id=first_invalid,
            error_class=error_class,
            confidence=None,
            unsupported_claims=(),
            infrastructure_failure=False,
            student_failure=not (final_valid and all_steps_valid),
            created_at=created_at,
        )

    def _validate_step(self, step: PublicStep, *, previous_after: str | None) -> dict[str, Any]:
        errors: list[str] = []
        try:
            before = _parse_math(step.before)
            after = _parse_math(step.after)
            transformation_valid = _equivalent(before, after, variable="x")
        except Exception as exc:
            transformation_valid = False
            errors.append(f"parse_or_solver_error: {exc}")
        chain_valid = True
        if previous_after is not None:
            try:
                chain_valid = _equivalent(
                    _parse_math(previous_after), _parse_math(step.before), variable="x"
                )
            except Exception as exc:
                chain_valid = False
                errors.append(f"chain_parse_error: {exc}")
        if not transformation_valid:
            errors.append("before and after are not symbolically equivalent")
        if not chain_valid:
            errors.append("step does not continue from the preceding result")
        valid = transformation_valid and chain_valid
        evidence_id = f"ev-step-{sha256_digest({'step': step.model_dump(), 'valid': valid})[7:23]}"
        return {
            "step_id": step.step_id,
            "valid": valid,
            "transformation_valid": transformation_valid,
            "chain_valid": chain_valid,
            "operation": step.operation,
            "assumptions": list(step.assumptions),
            "errors": errors,
            "evidence_id": evidence_id,
        }


def _decode_response(response: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    text = response.decode("utf-8") if isinstance(response, bytes) else response
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("derivation response must be a JSON object")
    return value


def _parse_math(text: str) -> sp.Expr | sp.Equality:
    normalized = text.replace("^", "**").strip()
    symbol = sp.Symbol("x", real=True)
    locals_map = {"x": symbol, "sqrt": sp.sqrt}
    if "=" in normalized:
        left, right = normalized.split("=", 1)
        return sp.Eq(
            sp.sympify(left.strip(), locals=locals_map),
            sp.sympify(right.strip(), locals=locals_map),
            evaluate=False,
        )
    return sp.sympify(normalized, locals=locals_map)


def _equivalent(
    before: sp.Expr | sp.Equality,
    after: sp.Expr | sp.Equality,
    *,
    variable: str,
) -> bool:
    symbol = sp.Symbol(variable, real=True)
    if isinstance(before, sp.Equality) and isinstance(after, sp.Equality):
        left_solutions = sp.solveset(before, symbol, domain=sp.S.Reals)
        right_solutions = sp.solveset(after, symbol, domain=sp.S.Reals)
        return bool(left_solutions == right_solutions)
    if isinstance(before, sp.Equality) or isinstance(after, sp.Equality):
        return False
    return bool(sp.simplify(before - after) == 0)


def _validate_final(answer: FinalAnswer, expected: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    expected_kind = str(expected.get("kind"))
    detail: dict[str, Any] = {
        "observed": answer.model_dump(mode="json"),
        "expected": expected,
    }
    if answer.kind != expected_kind:
        detail["error"] = "answer kind differs"
        return False, detail
    try:
        observed_exclusions = {_canonical_number(value) for value in answer.exclusions}
        expected_exclusions = {
            _canonical_number(str(value)) for value in expected.get("exclusions", [])
        }
        if observed_exclusions != expected_exclusions:
            detail["error"] = "excluded values differ"
            return False, detail
        if answer.kind == "solution_set":
            observed = {_canonical_number(value) for value in answer.values}
            wanted = {_canonical_number(str(value)) for value in expected.get("values", [])}
            detail["observed_canonical_values"] = sorted(observed)
            detail["expected_canonical_values"] = sorted(wanted)
            return observed == wanted, detail
        symbol = sp.Symbol(str(answer.variable), real=True)
        locals_map = {str(answer.variable): symbol, "sqrt": sp.sqrt}
        observed_expression = sp.sympify(
            str(answer.expression).replace("^", "**"), locals=locals_map
        )
        expected_expression = sp.sympify(
            str(expected["expression"]).replace("^", "**"), locals=locals_map
        )
        equivalent = bool(sp.simplify(observed_expression - expected_expression) == 0)
        detail["symbolically_equivalent"] = equivalent
        return equivalent, detail
    except Exception as exc:
        detail["error"] = f"final answer parse error: {exc}"
        return False, detail


def _canonical_number(value: str) -> str:
    return str(sp.sstr(sp.simplify(sp.sympify(value.replace("^", "**")))))
