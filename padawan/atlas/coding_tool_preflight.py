"""Two actual model calls exercising compiler dispatch and public-feedback continuation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from padawan.adapters.base import GenerationRequest
from padawan.artifacts.store import artifact_put_bytes
from padawan.atlas.coding_judge import DockerBatchJudge
from padawan.atlas.coding_tool_contracts import COMPILER_TOOL, CompilerPolicy, public_response_items
from padawan.atlas.coding_tools import CompilerTool
from padawan.models.contracts import SamplingConfiguration
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.orchestration.external_calls import IdempotentGenerationExecutor


async def compiler_preflight(
    *,
    executor: IdempotentGenerationExecutor,
    run_id: str,
    judge: DockerBatchJudge,
    policy: CompilerPolicy,
    output: Path,
) -> dict[str, Any]:
    import asyncio

    from padawan.atlas.coding_runner import extract_cpp

    tool = CompilerTool(judge, policy)
    request = GenerationRequest(
        request_id=f"{run_id}-compiler-0",
        instructions=(
            "Use compile_and_run exactly once to compile a complete C++17 program that reads "
            "two integers and prints their sum. Supply standard input 7 11 followed by a newline."
        ),
        input="This is a compiler protocol check. Make the function call now.",
        sampling=SamplingConfiguration(max_output_tokens=8192, temperature=0.0, seed=20260907),
        tools=(COMPILER_TOOL,),
        tool_choice={"type": "function", "name": "compile_and_run"},
    )
    first = await executor.execute(
        run_id=run_id,
        purpose="atlas_compiler_protocol_preflight",
        provider="vertex-nemotron-bf16",
        request=request,
    )
    messages, calls = public_response_items(first)
    if len(calls) != 1 or calls[0]["name"] != "compile_and_run":
        raise ValueError("real compiler preflight did not return its single function call")
    receipt = await asyncio.to_thread(
        tool.run, calls[0], trajectory_id=request.request_id, turn_index=0
    )
    runs = receipt.feedback.get("runs", [])
    if (
        receipt.feedback.get("status") != "compiled"
        or len(runs) != 1
        or runs[0]["exit_code"] != 0
        or runs[0]["stdout"]["text"].strip() != "18"
    ):
        raise ValueError("real compiler preflight did not compile and execute correctly")
    feedback = {
        "type": "function_call_output",
        "call_id": calls[0]["call_id"],
        "output": json.dumps(receipt.feedback, sort_keys=True),
    }
    second_request = request.model_copy(
        update={
            "request_id": f"{run_id}-compiler-1",
            "instructions": (
                "Use the successful compiler feedback and return that correct C++17 program "
                "inside one cpp code block. Do not call tools again."
            ),
            "input": [{"role": "user", "content": request.input}, *messages, feedback],
            "tool_choice": "none",
        }
    )
    second = await executor.execute(
        run_id=run_id,
        purpose="atlas_compiler_protocol_preflight",
        provider="vertex-nemotron-bf16",
        request=second_request,
    )
    _, repeated_calls = public_response_items(second)
    if repeated_calls or not second.output_text:
        raise ValueError("real public-feedback continuation did not produce a final program")
    final_call = {
        "type": "function_call",
        "call_id": "preflight-final-audit",
        "name": "compile_and_run",
        "arguments": json.dumps(
            {
                "source": extract_cpp(second.output_text),
                "stdin": ["7 11\n", "-8 3\n", "0 0\n", "1000000000 1000000000\n"],
            }
        ),
    }
    final_check = await asyncio.to_thread(
        tool.run, final_call, trajectory_id=request.request_id, turn_index=1
    )
    results = final_check.feedback.get("runs", [])
    if (
        len(results) != 4
        or any(result["exit_code"] != 0 for result in results)
        or [result["stdout"]["text"].strip() for result in results]
        != ["18", "-5", "0", "2000000000"]
    ):
        raise ValueError("real compiler-feedback final program failed independent CPU checks")
    evidence = {
        "passed": True,
        "model_requests": 2,
        "compiler_executions": 2,
        "final_cpu_checks": 4,
        "policy_digest": policy.digest,
        "first_request_id": first.request_id,
        "second_request_id": second.request_id,
        "first_wire_digest": sha256_digest(first.raw_response),
        "second_wire_digest": sha256_digest(second.raw_response),
        "compiler_receipt": receipt,
        "final_receipt": final_check,
        "private_reasoning_in_continuation": False,
        "output_tokens": first.usage["output_tokens"] + second.usage["output_tokens"],
    }
    ref = await artifact_put_bytes(
        executor.artifacts,
        canonical_json_bytes(evidence),
        media_type="application/vnd.padawan.compiler-preflight+json",
        restricted=True,
        raw_data=True,
    )
    async with executor.database.transaction() as session:
        await executor.catalog.reference(
            session, ref, owner_type="atlas_compiler_preflight", owner_id=run_id
        )
    path = output / "compiler-tool-preflight.json"
    path.write_bytes(canonical_json_bytes({**evidence, "artifact": ref}))
    path.chmod(0o600)
    return {
        "passed": True,
        "model_requests": 2,
        "final_cpu_checks": 4,
        "policy_digest": policy.digest,
        "artifact": ref.model_dump(mode="json"),
        "output_tokens": evidence["output_tokens"],
    }
