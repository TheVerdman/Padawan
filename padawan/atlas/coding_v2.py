"""Public-only continuation and strict terminal submission for local coding episodes."""

from __future__ import annotations

import json
from typing import Any

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.atlas.coding_tool_contracts import (
    SUBMIT_TOOL,
    CodingToolTrajectory,
    CompilerInput,
    CompilerPolicyV2,
    CompilerReceipt,
    compiler_tools,
    output_usage,
    public_response_items,
)
from padawan.models.hashing import sha256_digest

NO_ACTION_FEEDBACK = (
    "No function call was received. This has not submitted a solution. "
    "Call compile_and_run with source and stdin as strings to test code, or call "
    "submit_solution with your complete C++17 source to finish. Prose and Markdown "
    "are not terminal submissions. Your remaining budget has not been reset."
)
FINALIZE_INSTRUCTIONS = (
    "\n\nThe work phase is over. Your remaining tokens are reserved for final submission. "
    "Call submit_solution now with your best complete C++17 source. "
    "Do not call the compiler or provide prose instead of the function call."
)


def strict_arguments(raw: str, required: set[str]) -> dict[str, str]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate argument key")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if (
        not isinstance(value, dict)
        or set(value) != required
        or any(type(item) is not str for item in value.values())
    ):
        raise ValueError("arguments must contain exactly the declared string fields")
    return value


def tool_input_v2(call: dict[str, Any], policy: CompilerPolicyV2) -> CompilerInput:
    required = {"source", "stdin"} if call["name"] == "compile_and_run" else {"source"}
    if call["name"] not in {"compile_and_run", "submit_solution"}:
        raise ValueError("unknown tool")
    args = strict_arguments(call["arguments"], required)
    request = CompilerInput(source=args["source"], stdin=(args.get("stdin", ""),))
    request.validate_limits(policy)
    if call["name"] == "submit_solution" and "```" in request.source:
        raise ValueError("submission must be source code without Markdown fences")
    return request


def next_request_v2(
    trajectory: CodingToolTrajectory,
    previous: list[tuple[GenerationResult, tuple[CompilerReceipt, ...]]],
    *,
    input_tokens: int,
) -> GenerationRequest:
    policy = trajectory.policy
    assert isinstance(policy, CompilerPolicyV2)
    initial = trajectory.initial_request
    if not 0 <= len(previous) < policy.max_model_turns:
        raise PermissionError("model-turn allowance exhausted")
    if not 0 < input_tokens <= policy.max_input_tokens_per_turn:
        raise ValueError("complete public history exceeds its input allowance")
    history: list[dict[str, Any]] = [{"role": "user", "content": initial.input}]
    spent = calls_used = compiler_calls = no_calls = 0
    known_ids: set[str] = set()
    for index, (result, receipts) in enumerate(previous):
        rid = initial.request_id if index == 0 else f"{initial.request_id}.turn-{index}"
        if result.request_id != rid:
            raise PermissionError("continuation model owner or order differs")
        spent += output_usage(result, policy.per_turn_token_limit)
        messages, calls = public_response_items(result)
        if len(calls) != len(receipts):
            raise PermissionError("every tool call requires its addressed receipt")
        if len(calls) + calls_used > policy.max_tool_calls:
            raise PermissionError("tool allowance exhausted")
        history.extend(messages)
        if not calls:
            no_calls += 1
            history.append({"role": "user", "content": NO_ACTION_FEEDBACK})
        for call, receipt in zip(calls, receipts, strict=True):
            if (
                call["call_id"] in known_ids
                or receipt.trajectory_id != trajectory.trajectory_id
                or receipt.turn_index != index
                or receipt.call_id != call["call_id"]
                or receipt.call_digest != sha256_digest(call)
                or receipt.policy_digest != policy.digest
            ):
                raise PermissionError("tool receipt differs from its exact owner, call, or policy")
            if receipt.feedback.get("status") == "submission_received":
                raise PermissionError("a terminal submission cannot authorize another model turn")
            known_ids.add(call["call_id"])
            compiler_calls += int(call["name"] == "compile_and_run")
            history.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": json.dumps(receipt.feedback, sort_keys=True, ensure_ascii=False),
                }
            )
        calls_used += len(calls)
    remaining = policy.generation_token_limit - spent
    if remaining <= 0 or calls_used >= policy.max_tool_calls:
        raise PermissionError("cumulative generation or tool allowance exhausted")
    finalize = bool(previous) and (
        remaining <= policy.finalization_token_reserve
        or compiler_calls >= policy.max_compiler_calls
        or no_calls >= policy.no_call_corrections
        or len(previous) == policy.max_model_turns - 1
        or calls_used == policy.max_tool_calls - 1
    )
    allowance = min(
        policy.per_turn_token_limit,
        remaining if finalize else remaining - policy.finalization_token_reserve,
        policy.max_context_tokens - input_tokens,
    )
    if allowance <= 0:
        raise ValueError("context has no room for its next bounded generation")
    if len(json.dumps(history, ensure_ascii=False).encode()) > policy.max_serialized_history_bytes:
        raise ValueError("complete public history exceeds its byte allowance")
    if not previous:
        if initial.sampling.max_output_tokens != allowance:
            raise ValueError("initial request differs from its declared per-turn allowance")
        return initial
    return initial.model_copy(
        update={
            "request_id": f"{initial.request_id}.turn-{len(previous)}",
            "instructions": initial.instructions + (FINALIZE_INSTRUCTIONS if finalize else ""),
            "input": history,
            "sampling": initial.sampling.model_copy(update={"max_output_tokens": allowance}),
            "tools": (SUBMIT_TOOL,) if finalize else compiler_tools(policy),
            "tool_choice": "auto",
            "metadata": {
                **initial.metadata,
                "tool_input_tokens": str(input_tokens),
                "compiler_phase": "finalize" if finalize else "work",
            },
        }
    )
