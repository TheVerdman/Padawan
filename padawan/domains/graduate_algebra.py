"""Graduate proof tasks with explicit researcher adjudication, not a symbolic proof oracle.

This authority plugs into Padawan's existing shared developmental workflow. Its
grading requests contain the public proof and a fixed rubric, never private traces.
The same Codex session authors teaching and reviews proofs in this exploratory pilot;
these reviews are neither independent nor kernel verification.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from padawan.adapters.base import GenerationRequest
from padawan.domains.contracts import DomainSpec
from padawan.domains.developmental.contracts import DomainAttemptContent
from padawan.models.contracts import (
    AttemptRecord,
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    EvidenceRecord,
    GradeOutcome,
    GradeRecord,
    ResearchRole,
    SamplingConfiguration,
    StudentStateRecord,
    TeacherMode,
    VerifierSpec,
    project_authored_internal_rights,
)
from padawan.models.hashing import canonical_json_bytes, sha256_digest

DOMAIN_ID = "math.graduate_algebra"
COMPETENCY_ID = "graduate_algebra.nonnormal_galois_base_change"
RUBRIC_VERSION = "graduate-proof-review-v1"
STUDENT_OUTPUT_TOKENS = 65536
STUDENT_CONTEXT_TOKENS = 131072
STUDENT_TIMEOUT_SECONDS = 3600


def domain_spec() -> DomainSpec:
    return DomainSpec(
        domain_id=DOMAIN_ID,
        version="1.0.0",
        title="Graduate abstract algebra proofs",
        description="Nonnormal base change, finite etale algebras, and permutation Galois groups.",
        evidence_hierarchy=(
            "reviewed_mathematical_proof",
            "finite_group_checks",
            "student_proof",
            "teacher_feedback",
        ),
        deterministic_verifiers=(),
        permissible_teacher_modes=(TeacherMode.DIAGNOSTIC_CRITIQUE,),
        supports_tools=False,
    )


def competency() -> CompetencyRecord:
    return CompetencyRecord(
        competency_id=COMPETENCY_ID,
        title="Nonnormal Galois base change and etale decomposition",
        description="Prove subgroup, field, tensor-product and solvability conclusions together.",
        grader_requirements=(RUBRIC_VERSION,),
        permissible_teacher_modes=(TeacherMode.DIAGNOSTIC_CRITIQUE,),
        difficulty_calibration={
            "level": "graduate abstract algebra",
            "empirically_calibrated": False,
            "basis": "Galois theory, subgroup cores, tensor products, permutation characters.",
        },
        version=1,
        created_at=datetime(2026, 9, 7, tzinfo=UTC),
    )


def corpus_items(suite: dict[str, Any]) -> list[CorpusItemRecord]:
    timestamp = datetime(2026, 9, 7, tzinfo=UTC)
    items = []
    for group_index, group in enumerate(suite["groups"]):
        for index, task in enumerate(group["tasks"]):
            items.append(
                CorpusItemRecord(
                    competency_id=COMPETENCY_ID,
                    template_family_id="graduate-galois-base-change-v1",
                    instance_group_id=f"graduate-group-{group_index + 1:02d}",
                    item_id=f"graduate-{group_index + 1:02d}-{index:02d}-{task['id']}",
                    generation_seed=7092026 + group_index * 1000 + index,
                    generator_version="authored-graduate-galois-v1",
                    difficulty=0.95,
                    prompt=task["prompt"],
                    expected_answer={
                        "kind": "reviewed_graduate_proof",
                        "rubric": task["rubric"],
                        "reference": task["reference"],
                    },
                    verifier_spec=VerifierSpec(
                        verifier_type="reviewed_graduate_proof",
                        verifier_version=RUBRIC_VERSION,
                        parameters={
                            "task_id": task["id"],
                            "rubric_digest": sha256_digest(task["rubric"]),
                            "kernel_verified": False,
                        },
                    ),
                    pool=CorpusPool.CURRICULUM,
                    source="project-authored:graduate-galois-pilot",
                    rights=project_authored_internal_rights(reviewed_at=timestamp),
                    contamination_scope="instance_group",
                    created_at=timestamp,
                )
            )
    return items


class GraduateAlgebraAuthority:
    domain_id = DOMAIN_ID
    competency_ids = (COMPETENCY_ID,)

    def __init__(self, review_root: Path, *, timeout_seconds: float = 300) -> None:
        self.review_root = review_root
        self.timeout_seconds = timeout_seconds
        review_root.mkdir(parents=True, exist_ok=True, mode=0o700)

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
                "Write a complete mathematical solution to this graduate abstract algebra problem. "
                "Prove the requested claims, state the hypotheses of theorems you "
                "use, and distinguish "
                "field equality, isomorphism, linear disjointness, and equality "
                "of permutation characters. "
                "Your answer is a public mathematical proof. Give justified "
                "intermediate results and a "
                "clear conclusion for every part. Do not invent computations or tool results. "
                "If a part remains unresolved, identify the gap precisely."
            ),
            input=json.dumps(context, sort_keys=True),
            sampling=SamplingConfiguration(
                temperature=0.7,
                top_p=0.95,
                max_output_tokens=STUDENT_OUTPUT_TOKENS,
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
        if not output_text.strip():
            raise ValueError("empty public answer; proof grading cannot begin")
        return DomainAttemptContent(public_derivation=None, final_answer=output_text.strip())

    async def grade_attempt(
        self,
        *,
        run_id: str,
        phase: str,
        item: CorpusItemRecord,
        attempt: AttemptRecord,
    ) -> GradeRecord:
        # Phase and condition are omitted from the review view. This does not make
        # the current session's adjudication independent or fully blinded.
        del run_id, phase
        assert item.expected_answer is not None
        review_id = sha256_digest(attempt.attempt_id)[7:]
        payload = {
            "review_id": review_id,
            "task": item.prompt,
            "public_proof": attempt.final_answer,
            "rubric": item.expected_answer["rubric"],
            "reference": item.expected_answer["reference"],
            "scoring": "Five criteria: 0 wrong; 1 partial; 2 proved. Success: 10/10, no errors.",
        }
        digest = sha256_digest(payload)
        request = {"request_digest": digest, **payload}
        request_path = self.review_root / f"{review_id}.request.json"
        response_path = self.review_root / f"{review_id}.response.json"
        encoded = canonical_json_bytes(request)
        if request_path.exists() and request_path.read_bytes() != encoded:
            raise ValueError("proof review input changed")
        if not request_path.exists():
            request_path.write_bytes(encoded)
        started = time.monotonic()
        while not response_path.exists():
            if time.monotonic() - started >= self.timeout_seconds:
                raise TimeoutError("proof review was not supplied within its wait budget")
            await asyncio.sleep(0.2)
        review = json.loads(response_path.read_bytes())
        if review.get("request_digest") != digest:
            raise ValueError("proof review is not bound to its exact public proof and rubric")
        ratings = review.get("criteria", [])
        if len(ratings) != 5 or any(
            type(r.get("points")) is not int or r["points"] not in {0, 1, 2} or not r.get("reason")
            for r in ratings
        ):
            raise ValueError("proof review requires five explained integer ratings")
        if not review.get("reviewer") or not review.get("summary"):
            raise ValueError("proof review requires reviewer and summary")
        errors = review.get("significant_errors", [])
        if not isinstance(errors, list) or any(not isinstance(e, str) for e in errors):
            raise ValueError("proof review errors must be explicit text")
        points = sum(r["points"] for r in ratings)
        if points == 10 and errors:
            raise ValueError("full proof credit contradicts a significant error")
        outcome = (
            GradeOutcome.CORRECT
            if points == 10
            else GradeOutcome.PARTIAL
            if points
            else GradeOutcome.INCORRECT
        )
        evidence = EvidenceRecord(
            evidence_id=f"graduate-review-{review_id}",
            kind="teacher",
            statement=review["summary"],
            payload={
                **review,
                "review_digest": sha256_digest(review),
                "adjudicated_proof": True,
                "kernel_verified": False,
                "independent_of_teacher": False,
            },
        )
        return GradeRecord(
            grade_id=f"grade-graduate-{review_id}",
            attempt_id=attempt.attempt_id,
            grader_type="authored_graduate_proof_review",
            grader_version=RUBRIC_VERSION,
            outcome=outcome,
            score=points / 10,
            deterministic=False,
            evidence=(evidence,),
            parsed_answer={"kind": "public_graduate_proof", "proof": attempt.final_answer},
            error_class=None if points == 10 else "incomplete_or_invalid_proof",
            confidence=None,
            unsupported_claims=tuple(errors),
            infrastructure_failure=False,
            student_failure=points != 10,
            created_at=datetime.now(UTC),
        )

    def teacher_task(self, item: CorpusItemRecord) -> dict[str, Any]:
        return {"domain_id": self.domain_id, "item_id": item.item_id, "prompt": item.prompt}

    def expected_answer(self, item: CorpusItemRecord) -> dict[str, Any] | None:
        return item.expected_answer

    def forbidden_transfer_answer(self, item: CorpusItemRecord) -> str | None:
        # General proof text has no sound substring-based answer oracle. The
        # teacher sees only the cold item; solution leakage is separately reviewed.
        return None
