from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from padawan.adapters.base import GenerationRequest
from padawan.models.contracts import (
    AttemptRecord,
    CorpusItemRecord,
    GradeRecord,
    ResearchRole,
    StudentStateRecord,
)


@dataclass(frozen=True)
class DomainAttemptContent:
    """Domain-owned interpretation of one student transport response."""

    public_derivation: dict[str, Any] | None
    final_answer: dict[str, Any] | str | None


class DevelopmentalAuthority(Protocol):
    """Domain semantics required by the shared developmental control flow."""

    domain_id: str
    competency_ids: tuple[str, ...]

    def build_student_request(
        self,
        *,
        request_id: str,
        item: CorpusItemRecord,
        state: StudentStateRecord,
        intervention: dict[str, Any] | None,
        retrieved_lessons: dict[str, Any],
    ) -> GenerationRequest: ...

    def decode_student_output(
        self,
        *,
        output_text: str,
        item: CorpusItemRecord,
        research_role: ResearchRole,
        created_at: datetime,
    ) -> DomainAttemptContent: ...

    async def grade_attempt(
        self,
        *,
        run_id: str,
        phase: str,
        item: CorpusItemRecord,
        attempt: AttemptRecord,
    ) -> GradeRecord: ...

    def teacher_task(self, item: CorpusItemRecord) -> dict[str, Any]: ...

    def expected_answer(self, item: CorpusItemRecord) -> dict[str, Any] | None: ...

    def forbidden_transfer_answer(self, item: CorpusItemRecord) -> str | None: ...
