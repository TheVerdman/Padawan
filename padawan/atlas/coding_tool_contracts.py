"""Finite compiler trajectories and the explicit public tool projection.

These are private experimental control/evidence contracts. They are not PPRL process
memory, worker credentials, or single-generation Atlas result envelopes.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.models.contracts import ArtifactRef, NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest

TOOL_ID = "tool.compile_and_run"
TOOL_NAME = "compile_and_run"
V2_PER_TURN_TOKENS = 8192
COMPILER_TOOL = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Compile a complete C++17 program and run it on up to four explicit standard inputs. "
        "Use public examples or your own tests, reference solvers, and stress tests. "
        "Returns compiler diagnostics, exit status, standard output and standard error. "
        "There is no hidden judge access, network, shell tool, or persistent filesystem. "
        "Each call starts fresh. Runtime is limited to 5 CPU seconds per input."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Complete self-contained C++17 source"},
            "stdin": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 4,
                "description": (
                    "One to four explicit standard-input strings; "
                    "use an empty string for a self-contained stress test"
                ),
            },
        },
        "required": ["source", "stdin"],
        "additionalProperties": False,
    },
}


class _CompilerLimits(StrictRecord):
    max_tool_calls: int = Field(default=4, ge=1, le=8)
    max_source_bytes: int = Field(default=65_536, ge=1024, le=262_144)
    max_stdin_bytes: int = Field(default=16_384, ge=1, le=65_536)
    max_inputs: int = Field(default=4, ge=1, le=4)
    compile_seconds: int = Field(default=60, ge=1, le=60)
    execution_seconds: int = Field(default=5, ge=1, le=10)
    feedback_bytes_per_stream: int = Field(default=4096, ge=128, le=8192)
    capture_bytes_per_stream: int = Field(default=65_536, ge=8192, le=262_144)
    max_input_tokens_per_turn: int = Field(default=32_768, ge=8192, le=65_536)
    max_context_tokens: int = Field(default=139_264, ge=8192, le=262_144)
    max_serialized_history_bytes: int = Field(default=524_288, ge=8192, le=1_048_576)
    trajectory_timeout_seconds: int = Field(default=2400, ge=60, le=3600)
    judge_image: NonEmpty

    @property
    def max_model_turns(self) -> int:
        return self.max_tool_calls + 1

    @property
    def digest(self) -> str:
        return sha256_digest(self)


class CompilerPolicy(_CompilerLimits):
    version: Literal["1"] = "1"


class CompilerPolicyV2(_CompilerLimits):
    """Explicit submission and a cumulative budget separate from each model turn.

    Kept separate from v1 so parsing historical v1 evidence does not add new fields
    or change its policy digest.
    """

    version: Literal["2"] = "2"
    generation_token_limit: int = Field(default=32_768, ge=8192, le=131_072)
    per_turn_token_limit: Literal[8192] = 8192
    finalization_token_reserve: int = Field(default=4096, ge=1024, le=8192)
    model_turn_limit: int = Field(default=8, ge=3, le=9)
    max_compiler_calls: int = Field(default=4, ge=1, le=6)
    no_call_corrections: int = Field(default=2, ge=1, le=3)

    @property
    def max_model_turns(self) -> int:
        return self.model_turn_limit

    @model_validator(mode="after")
    def budgets_fit(self) -> CompilerPolicyV2:
        if not (
            self.finalization_token_reserve < self.generation_token_limit
            and self.per_turn_token_limit <= self.generation_token_limit
            and self.max_compiler_calls < self.max_tool_calls
        ):
            raise ValueError("v2 requires a bounded turn budget and reserved final submission")
        return self


CodingPolicy = CompilerPolicy | CompilerPolicyV2


COMPILER_TOOL_V2 = {
    "type": "function",
    "name": TOOL_NAME,
    "description": (
        "Compile a complete C++17 program and run it on one standard-input string. "
        "Use public examples or your own tests. Returns bounded compiler diagnostics "
        "and program output. No hidden tests, network, shell, or persistent filesystem."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Complete C++17 source code"},
            "stdin": {"type": "string", "description": "One input string; empty is allowed"},
        },
        "required": ["source", "stdin"],
        "additionalProperties": False,
    },
}
SUBMIT_TOOL = {
    "type": "function",
    "name": "submit_solution",
    "description": (
        "Submit your final complete C++17 program and end this problem. "
        "This action is terminal. Supply source code directly, without Markdown fences."
    ),
    "parameters": {
        "type": "object",
        "properties": {"source": {"type": "string", "description": "Complete C++17 program"}},
        "required": ["source"],
        "additionalProperties": False,
    },
}


def compiler_tools(policy: CodingPolicy) -> tuple[dict[str, Any], ...]:
    return (COMPILER_TOOL,) if policy.version == "1" else (COMPILER_TOOL_V2, SUBMIT_TOOL)


def compiler_wire_identity(policy: CodingPolicy) -> dict[str, Any]:
    return {
        "policy": policy,
        "wire": COMPILER_TOOL if policy.version == "1" else compiler_tools(policy),
    }


class CompilerInput(StrictRecord):
    source: str
    stdin: tuple[str, ...]

    def validate_limits(self, policy: CodingPolicy) -> None:
        if not 0 < len(self.source.encode()) <= policy.max_source_bytes:
            raise ValueError("source exceeds the compiler tool's byte limit or is empty")
        if not 1 <= len(self.stdin) <= policy.max_inputs:
            raise ValueError("compiler tool requires one to four standard inputs")
        if sum(len(value.encode()) for value in self.stdin) > policy.max_stdin_bytes:
            raise ValueError("standard inputs exceed the compiler tool's byte limit")


class CompilerReceipt(StrictRecord):
    trajectory_id: NonEmpty
    turn_index: int = Field(ge=0, le=8)
    call_id: NonEmpty
    call_digest: Sha256
    policy_digest: Sha256
    feedback: dict[str, Any]
    execution_evidence: dict[str, Any]


class CodingToolTrajectory(StrictRecord):
    schema_version: Literal["1"] = "1"
    trajectory_id: NonEmpty
    run_id: NonEmpty
    initial_request: GenerationRequest
    activation: ArtifactRef
    policy: CodingPolicy = Field(discriminator="version")
    initial_input_tokens: int = Field(gt=0)
    problem_id: NonEmpty
    statement_digest: Sha256
    started_at: datetime
    model_deadline: datetime

    @model_validator(mode="after")
    def initial_is_exact(self) -> CodingToolTrajectory:
        request = self.initial_request
        if (
            request.request_id != self.trajectory_id
            or request.tools != compiler_tools(self.policy)
            or request.tool_choice != "auto"
            or request.previous_response_id is not None
            or request.store
            or not isinstance(request.input, str)
            or sha256_digest(request.input) != self.statement_digest
        ):
            raise ValueError("compiler trajectory requires its exact public initial request")
        if not self.activation.restricted or not self.activation.raw_data:
            raise ValueError("compiler activation must remain private")
        if (
            self.started_at.tzinfo is None
            or self.model_deadline.tzinfo is None
            or not 0
            < (self.model_deadline - self.started_at).total_seconds()
            <= self.policy.trajectory_timeout_seconds
        ):
            raise ValueError("compiler trajectory requires an immutable bounded clock")
        return self


def public_response_items(
    result: GenerationResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project only declared public text and addressed function calls; ignore all other keys.

    In particular, reasoning items, summaries, metadata and arbitrary provider extensions
    never become continuation input. Unknown function names are returned for a bounded
    tool-error reply, not dispatched to another capability.
    """
    wire = json.loads(result.raw_response)
    if wire.get("error") is not None or wire.get("status") not in {"completed", "incomplete"}:
        raise ValueError("provider did not complete a usable response")
    messages: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for item in wire.get("output", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message" and item.get("role") == "assistant":
            content = "".join(
                part["text"]
                for part in item.get("content", [])
                if isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            )
            if content:
                messages.append({"role": "assistant", "content": content})
        elif item.get("type") == "function_call":
            if any(
                not isinstance(item.get(k), str) or not item[k]
                for k in ("call_id", "name", "arguments")
            ):
                raise ValueError("malformed addressed function call")
            call = {k: item[k] for k in ("type", "call_id", "name", "arguments")}
            if any(prior["call_id"] == call["call_id"] for prior in calls):
                raise ValueError("duplicate function call identity")
            calls.append(call)
            messages.append(call)
    return messages, calls


def output_usage(result: GenerationResult, maximum: int) -> int:
    value = result.usage.get("output_tokens")
    if type(value) is not int or not 0 < value <= maximum:
        raise ValueError("missing, zero or excessive generated-token accounting")
    return value


def next_tool_request(
    trajectory: CodingToolTrajectory,
    previous: list[tuple[GenerationResult, tuple[CompilerReceipt, ...]]],
    *,
    input_tokens: int,
) -> GenerationRequest:
    """Reconstruct the exact next request from public messages and scoped tool results."""
    policy = trajectory.policy
    if isinstance(policy, CompilerPolicyV2):
        from padawan.atlas.coding_v2 import next_request_v2

        return next_request_v2(trajectory, previous, input_tokens=input_tokens)
    initial = trajectory.initial_request
    if not 0 <= len(previous) < policy.max_model_turns:
        raise PermissionError("compiler trajectory model-turn allowance exhausted")
    if not 0 < input_tokens <= policy.max_input_tokens_per_turn:
        raise ValueError("complete public history exceeds its input-token allowance")
    history: list[dict[str, Any]] = [{"role": "user", "content": initial.input}]
    spent = 0
    calls_used = 0
    known_call_ids: set[str] = set()
    for index, (result, receipts) in enumerate(previous):
        expected_id = initial.request_id if index == 0 else f"{initial.request_id}.turn-{index}"
        if result.request_id != expected_id:
            raise PermissionError("tool trajectory generation owner or order differs")
        spent += output_usage(result, initial.sampling.max_output_tokens - spent)
        messages, calls = public_response_items(result)
        if not calls or len(calls) != len(receipts):
            raise PermissionError("continuation requires complete addressed compiler feedback")
        if calls_used + len(calls) > policy.max_tool_calls:
            raise PermissionError("compiler call allowance exhausted")
        history.extend(messages)
        for call, receipt in zip(calls, receipts, strict=True):
            if (
                call["call_id"] in known_call_ids
                or receipt.trajectory_id != trajectory.trajectory_id
                or receipt.turn_index != index
                or receipt.call_id != call["call_id"]
                or receipt.call_digest != sha256_digest(call)
                or receipt.policy_digest != policy.digest
            ):
                raise PermissionError("compiler feedback differs from its exact call and owner")
            known_call_ids.add(call["call_id"])
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(receipt.feedback, sort_keys=True, ensure_ascii=False),
                }
            )
        calls_used += len(calls)
    remaining = initial.sampling.max_output_tokens - spent
    if remaining <= 0:
        raise PermissionError("cumulative generation allowance exhausted")
    if len(json.dumps(history, ensure_ascii=False).encode()) > policy.max_serialized_history_bytes:
        raise ValueError("complete public history exceeds its byte allowance")
    # Actual tokenizer counting occurs before admission; no history is truncated or summarized.
    allowance = min(remaining, policy.max_context_tokens - input_tokens)
    if allowance <= 0:
        raise ValueError("serving context has no remaining output capacity")
    if not previous:
        if allowance != initial.sampling.max_output_tokens:
            raise ValueError("initial tool request no longer fits the declared context")
        return initial
    return initial.model_copy(
        update={
            "request_id": f"{initial.request_id}.turn-{len(previous)}",
            "input": history,
            "sampling": initial.sampling.model_copy(update={"max_output_tokens": allowance}),
            "tool_choice": "none" if calls_used == policy.max_tool_calls else "auto",
            "metadata": {**initial.metadata, "tool_input_tokens": str(input_tokens)},
        }
    )
