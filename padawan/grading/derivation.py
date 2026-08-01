from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PublicStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$")
    before: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    after: str = Field(min_length=1)
    assumptions: tuple[str, ...] = ()


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["solution_set", "expression"]
    variable: str = "x"
    values: tuple[str, ...] = ()
    expression: str | None = None
    exclusions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def shape_matches_kind(self) -> FinalAnswer:
        if self.kind == "solution_set" and not self.values:
            raise ValueError("solution_set requires at least one value")
        if self.kind == "expression" and self.expression is None:
            raise ValueError("expression answer requires expression")
        return self


class PublicDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: tuple[PublicStep, ...] = Field(min_length=1)
    final_answer: FinalAnswer

    @model_validator(mode="after")
    def unique_step_ids(self) -> PublicDerivation:
        identifiers = [step.step_id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("step IDs must be unique")
        return self
