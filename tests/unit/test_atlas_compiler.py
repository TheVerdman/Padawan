"""Control/projection fixtures only: no model transport or generated capability evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.atlas.coding_tool_contracts import (
    COMPILER_TOOL,
    CodingToolTrajectory,
    CompilerInput,
    CompilerPolicy,
    CompilerPolicyV2,
    CompilerReceipt,
    compiler_tools,
    next_tool_request,
    output_usage,
    public_response_items,
)
from padawan.models.contracts import ArtifactRef, RuntimeCapabilities, SamplingConfiguration
from padawan.models.hashing import sha256_digest


def trajectory_v2():
    value = trajectory()
    rules = CompilerPolicyV2(judge_image=value.policy.judge_image, max_tool_calls=6)
    initial = value.initial_request.model_copy(
        update={
            "tools": compiler_tools(rules),
            "sampling": value.initial_request.sampling.model_copy(
                update={"max_output_tokens": 8192}
            ),
        }
    )
    return value.model_copy(update={"policy": rules, "initial_request": initial})


def v2_record(t, *, turn=0, tokens=8192, name="compile_and_run", no_call=False):
    result, receipt = record(
        request_id="root" if turn == 0 else f"root.turn-{turn}",
        output_tokens=tokens,
        call_id=f"call-{turn}",
    )
    wire = json.loads(result.raw_response)
    call = next(item for item in wire["output"] if item["type"] == "function_call")
    call["name"] = name
    call["arguments"] = json.dumps(
        {"source": "int main() {}", **({"stdin": ""} if name == "compile_and_run" else {})}
    )
    if no_call:
        wire["output"] = [item for item in wire["output"] if item["type"] != "function_call"]
    result = replace(result, raw_response=json.dumps(wire).encode())
    public_calls = public_response_items(result)[1]
    receipt = receipt.model_copy(
        update={
            "turn_index": turn,
            "policy_digest": t.policy.digest,
            "call_digest": sha256_digest(public_calls[0]) if public_calls else receipt.call_digest,
        }
    )
    return result, (() if no_call else (receipt,))


def test_v2_reserves_tokens_and_switches_to_explicit_submission():
    t = trajectory_v2()
    previous = [v2_record(t, turn=i) for i in range(3)]
    request = next_tool_request(t, previous, input_tokens=200)
    assert request.sampling.max_output_tokens == 4096
    previous.append(v2_record(t, turn=3, tokens=4096))
    request = next_tool_request(t, previous, input_tokens=250)
    assert request.sampling.max_output_tokens == 4096
    assert [tool["name"] for tool in request.tools] == ["submit_solution"]
    assert request.metadata["compiler_phase"] == "finalize"
    assert "CANARY" not in request.model_dump_json()


def test_v2_prose_receives_bounded_correction_and_then_finalization():
    t = trajectory_v2()
    previous = [v2_record(t, no_call=True)]
    request = next_tool_request(t, previous, input_tokens=200)
    assert "has not submitted" in request.model_dump_json()
    previous.append(v2_record(t, turn=1, no_call=True))
    request = next_tool_request(t, previous, input_tokens=300)
    assert request.metadata["compiler_phase"] == "finalize"
    assert request.tool_choice == "auto"


def test_v2_terminal_receipt_forbids_continuation():
    t = trajectory_v2()
    result, receipts = v2_record(t, tokens=100, name="submit_solution")
    receipt = receipts[0].model_copy(update={"feedback": {"status": "submission_received"}})
    with pytest.raises(PermissionError, match="terminal"):
        next_tool_request(t, [(result, (receipt,))], input_tokens=200)


@pytest.mark.parametrize("stdin", [[], [""], 0, None, False])
def test_v2_does_not_coerce_nonstring_input(stdin):
    from padawan.atlas.coding_v2 import tool_input_v2

    with pytest.raises(ValueError):
        tool_input_v2(
            {
                "name": "compile_and_run",
                "arguments": json.dumps({"source": "int main(){}", "stdin": stdin}),
            },
            trajectory_v2().policy,
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"source":"one","source":"two"}',
        '{"source":"code","extra":"ignored"}',
        '{"source":42}',
    ],
)
def test_v2_submission_rejects_ambiguous_or_extra_arguments(raw):
    from padawan.atlas.coding_v2 import tool_input_v2

    with pytest.raises(ValueError):
        tool_input_v2({"name": "submit_solution", "arguments": raw}, trajectory_v2().policy)


@pytest.mark.asyncio
async def test_local_transport_is_exact_and_finalization_disables_thinking():
    from padawan.adapters.openai_compatible.local_metal import LocalMetalClient

    t = trajectory_v2()
    request = next_tool_request(
        t, [v2_record(t, no_call=True), v2_record(t, turn=1, no_call=True)], input_tokens=300
    )
    client = LocalMetalClient(base_url="http://127.0.0.1:18767", model="local-fixture")
    try:
        prepared = client.prepare_generation(request)
        assert prepared.destination == "http://127.0.0.1:18767/v1/responses"
        body = json.loads(prepared.body_json)
        assert body["chat_template_kwargs"]["enable_thinking"] is False
        assert body["store"] is False and body["parallel_tool_calls"] is False
        assert "CANARY" not in prepared.body_json
    finally:
        await client.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000/proxy",
        "http://user:pass@127.0.0.1:8000",
    ],
)
def test_local_transport_refuses_remote_or_ambiguous_destinations(url):
    from padawan.adapters.openai_compatible.local_metal import LocalMetalClient

    with pytest.raises(ValueError):
        LocalMetalClient(base_url=url, model="local-fixture")


def policy(**updates):
    return CompilerPolicy(judge_image="sha256:" + "a" * 64, **updates)


def trajectory():
    timestamp = datetime.now(UTC)
    initial = GenerationRequest(
        request_id="root",
        instructions="Control fixture",
        input="statement",
        sampling=SamplingConfiguration(
            max_output_tokens=100, temperature=1.0, top_p=0.95, seed=20260916
        ),
        tools=(COMPILER_TOOL,),
        tool_choice="auto",
    )
    return CodingToolTrajectory(
        trajectory_id="root",
        run_id="run",
        initial_request=initial,
        initial_input_tokens=10,
        policy=policy(),
        problem_id="control",
        statement_digest=sha256_digest("statement"),
        activation=ArtifactRef(
            artifact_id="control",
            uri="artifact://sha256/" + sha256_digest("activation")[7:],
            digest=sha256_digest("activation"),
            media_type="application/json",
            size_bytes=10,
            restricted=True,
            raw_data=True,
        ),
        started_at=timestamp,
        model_deadline=timestamp + timedelta(seconds=60),
    )


def record(*, request_id="root", output_tokens=20, call_id="call-0"):
    call = {
        "type": "function_call",
        "name": "compile_and_run",
        "call_id": call_id,
        "arguments": json.dumps({"source": "int main() {}", "stdin": [""]}),
    }
    wire = {
        "status": "completed",
        "output": [
            {
                "type": "reasoning",
                "content": [{"type": "reasoning_text", "text": "PRIVATE-CANARY"}],
                "summary": [{"text": "SUMMARY-CANARY"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "PUBLIC-CODE"},
                    {"type": "reasoning_text", "text": "NESTED-CANARY"},
                ],
            },
            {**call, "provider_secret": "METADATA-CANARY"},
        ],
        "provider_extension": "EXTENSION-CANARY",
    }
    result = GenerationResult(
        request_id=request_id,
        response_id="structural-fixture",
        provider="fixture",
        model_id="fixture",
        protocol="responses",
        output_text="PUBLIC-CODE",
        raw_request=b"{}",
        raw_response=json.dumps(wire).encode(),
        usage={"output_tokens": output_tokens, "input_tokens": 10},
        token_ids=None,
        token_logprobs=None,
        private_reasoning="PRIVATE-CANARY",
        reasoning_summary="SUMMARY-CANARY",
        finish_reason="completed",
        latency_ms=0,
        capabilities=RuntimeCapabilities.model_validate_json(
            json.dumps(
                {
                    name: {"availability": "unknown", "reason": "structural control fixture"}
                    for name in RuntimeCapabilities.model_fields
                }
            )
        ),
    )
    receipt = CompilerReceipt(
        trajectory_id="root",
        turn_index=0,
        call_id=call_id,
        call_digest=sha256_digest(call),
        policy_digest=policy().digest,
        feedback={"status": "compiled", "stdout": "42"},
        execution_evidence={"private_path": "PATH-CANARY"},
    )
    return result, receipt


def test_public_projection_excludes_every_private_extension():
    result, receipt = record()
    projected, calls = public_response_items(result)
    assert calls[0].keys() == {"type", "name", "call_id", "arguments"}
    assert "CANARY" not in json.dumps(projected)
    request = next_tool_request(trajectory(), [(result, (receipt,))], input_tokens=30)
    body = request.model_dump_json()
    assert "CANARY" not in body
    assert "PUBLIC-CODE" in body and "42" in body
    assert request.sampling.max_output_tokens == 80
    assert request.sampling.seed == 20260916
    assert request.previous_response_id is None and request.store is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("trajectory_id", "other"),
        ("turn_index", 1),
        ("call_id", "other"),
        ("policy_digest", sha256_digest("other")),
        ("call_digest", sha256_digest("other")),
    ],
)
def test_feedback_is_bound_to_call_owner_order_and_policy(field, value):
    result, receipt = record()
    with pytest.raises(PermissionError):
        next_tool_request(
            trajectory(), [(result, (receipt.model_copy(update={field: value}),))], input_tokens=30
        )


@pytest.mark.parametrize("tokens", [0, -1, 101, True, None])
def test_missing_or_excessive_usage_is_refused(tokens):
    result, _ = record()
    with pytest.raises(ValueError):
        output_usage(replace(result, usage={"output_tokens": tokens}), 100)


def test_cumulative_budget_cannot_reset():
    result, receipt = record(output_tokens=100)
    with pytest.raises(PermissionError, match="cumulative"):
        next_tool_request(trajectory(), [(result, (receipt,))], input_tokens=30)


def test_fifth_call_is_not_available_after_four_calls():
    previous = []
    for index in range(4):
        result, receipt = record(
            request_id="root" if index == 0 else f"root.turn-{index}",
            output_tokens=10,
            call_id=f"call-{index}",
        )
        previous.append((result, (receipt.model_copy(update={"turn_index": index}),)))
    request = next_tool_request(trajectory(), previous, input_tokens=40)
    assert request.tool_choice == "none" and request.sampling.max_output_tokens == 60
    with pytest.raises(PermissionError):
        next_tool_request(trajectory(), previous + [previous[-1]], input_tokens=40)


@pytest.mark.parametrize("count", [0, 32769])
def test_complete_history_never_truncates_to_fit(count):
    with pytest.raises(ValueError):
        next_tool_request(trajectory(), [], input_tokens=count)


def test_final_submission_does_not_authorize_another_model_turn():
    result, _ = record()
    wire = json.loads(result.raw_response)
    wire["output"] = [item for item in wire["output"] if item["type"] != "function_call"]
    result = replace(result, raw_response=json.dumps(wire).encode())
    with pytest.raises(PermissionError):
        next_tool_request(trajectory(), [(result, ())], input_tokens=30)


@pytest.mark.parametrize(
    "source,stdin",
    [
        ("", ("",)),
        ("x" * 65537, ("",)),
        ("int main(){}", ()),
        ("int main(){}", ("",) * 5),
        ("int main(){}", ("x" * 16385,)),
    ],
)
def test_compiler_input_resource_limits(source, stdin):
    with pytest.raises(ValueError):
        CompilerInput(source=source, stdin=stdin).validate_limits(policy())


def test_duplicate_call_ids_are_rejected():
    result, _ = record()
    wire = json.loads(result.raw_response)
    wire["output"].append(wire["output"][-1])
    with pytest.raises(ValueError):
        public_response_items(replace(result, raw_response=json.dumps(wire).encode()))


@pytest.mark.parametrize(
    "change",
    [
        {"store": True},
        {"previous_response_id": "private-response"},
        {"tools": ()},
        {"tool_choice": "required"},
        {"input": "different statement"},
    ],
)
def test_trajectory_rejects_changed_initial_authority(change):
    value = trajectory()
    with pytest.raises(ValueError):
        CodingToolTrajectory.model_validate_json(
            value.model_copy(
                update={"initial_request": value.initial_request.model_copy(update=change)}
            ).model_dump_json()
        )
