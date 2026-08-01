from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from padawan.models.contracts import EvidenceCitation


class TeacherOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    claimed_first_consequential_error: str | None = None
    error_class: str | None = None
    lesson: str = Field(min_length=1)
    repair: str = Field(min_length=1)
    expected_transfer_scope: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
