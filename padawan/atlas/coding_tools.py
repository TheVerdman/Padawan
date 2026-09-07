"""Explicit compiler execution and finite, separately retained coding trajectories."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import update

from padawan.adapters.base import GenerationRequest, GenerationResult
from padawan.adapters.prepared import PreparedGenerationClient
from padawan.artifacts.store import ArtifactCatalog, artifact_put_bytes, artifact_read_bytes
from padawan.atlas.activation import AtlasActivation
from padawan.atlas.coding_judge import (
    DockerBatchJudge,
    JudgePackage,
    file_sha256,
)
from padawan.atlas.coding_tool_boundary import (
    RECEIPT_MEDIA,
    owned_reference,
    register_trajectory,
    retain_owned,
)
from padawan.atlas.coding_tool_contracts import (
    TOOL_NAME,
    CodingPolicy,
    CodingToolTrajectory,
    CompilerInput,
    CompilerPolicyV2,
    CompilerReceipt,
    next_tool_request,
    output_usage,
    public_response_items,
)
from padawan.atlas.contracts import AtlasItemManifest, AtlasTrialRequest
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ArtifactRow, ExternalCallRow, RunRow
from padawan.orchestration.external_calls import (
    IdempotentGenerationExecutor,
    _artifact_row_to_reference,
)
from padawan.rewards.engine import RewardEngine

RESULT_MEDIA = "application/vnd.padawan.atlas-coding-tool-result+json"


class CompilerTool:
    """Source and explicit stdin are the entire untrusted surface. No judge data is mounted."""

    def __init__(self, judge: DockerBatchJudge, policy: CodingPolicy) -> None:
        if judge.image != policy.judge_image:
            raise ValueError("compiler image differs from its declared policy")
        self.judge, self.policy = judge, policy

    def run(
        self,
        call: dict[str, Any],
        *,
        trajectory_id: str,
        turn_index: int,
        deadline: float | None = None,
    ) -> CompilerReceipt:
        policy = self.policy
        base = dict(
            trajectory_id=trajectory_id,
            turn_index=turn_index,
            call_id=call["call_id"],
            call_digest=sha256_digest(call),
            policy_digest=policy.digest,
        )
        v2 = isinstance(policy, CompilerPolicyV2)
        if call["name"] != TOOL_NAME and not (v2 and call["name"] == "submit_solution"):
            return CompilerReceipt(
                **base,
                feedback={"status": "invalid_tool", "allowed_tool": TOOL_NAME},
                execution_evidence={"executed": False},
            )
        try:
            if v2:
                from padawan.atlas.coding_v2 import tool_input_v2

                assert isinstance(policy, CompilerPolicyV2)
                request = tool_input_v2(call, policy)
            else:
                request = CompilerInput.model_validate_json(call["arguments"])
                request.validate_limits(policy)
        except (ValueError, TypeError):
            return CompilerReceipt(
                **base,
                feedback={
                    "status": "invalid_arguments",
                    "requirements": (
                        "source: nonempty C++17 string up to 65536 UTF-8 bytes; "
                        + (
                            "compile_and_run additionally requires stdin: one string up to "
                            "16384 UTF-8 bytes. submit_solution accepts source only, no fences."
                            if v2
                            else "stdin: one to four strings totaling at most 16384 UTF-8 bytes"
                        )
                    ),
                },
                execution_evidence={"executed": False},
            )
        if v2 and call["name"] == "submit_solution":
            return CompilerReceipt(
                **base,
                feedback={"status": "submission_received"},
                execution_evidence={
                    "executed": False,
                    "source_digest": sha256_digest(request.source),
                },
            )
        evidence: dict[str, Any] = {
            "executed": True,
            "image": self.judge.image,
            "source_digest": sha256_digest(request.source),
            "network": "none",
            "fresh_mount": True,
            "credential_mounts": False,
            "hidden_judge_mounts": False,
            "records": [],
        }

        def stream(path: Path) -> dict[str, Any]:
            data = path.read_bytes()
            evidence["records"].append(
                {"name": path.name, "sha256": file_sha256(path), "bytes": len(data)}
            )
            return {
                "text": data[: policy.feedback_bytes_per_stream].decode("utf-8", errors="replace"),
                "truncated": len(data) > policy.feedback_bytes_per_stream,
            }

        with tempfile.TemporaryDirectory(prefix="compiler-", dir=self.judge.scratch) as scratch:
            root = Path(scratch)
            work = root / "source"
            work.mkdir(mode=0o700)
            (work / "main.cpp").write_text(request.source)
            output = root / "compile.stdout"
            code, oom = self.judge._run(
                ["g++", "-O2", "-pipe", "-static", "-s", "-std=gnu++17", "-o", "main", "main.cpp"],
                work=work,
                writable=True,
                memory=2 * 1024**3,
                seconds=policy.compile_seconds,
                output=output,
                output_limit=policy.capture_bytes_per_stream,
                stderr_limit=policy.capture_bytes_per_stream,
                deadline=deadline,
            )
            feedback: dict[str, Any] = {
                "status": "compile_error" if code else "compiled",
                "compile": {
                    "exit_code": code,
                    "out_of_memory": oom,
                    "stdout": stream(output),
                    "stderr": stream(output.with_suffix(".stderr")),
                },
                "runs": [],
            }
            if code == 0:
                for index, stdin in enumerate(request.stdin):
                    input_path = root / f"input-{index}.txt"
                    input_path.write_text(stdin)
                    output = root / f"run-{index}.stdout"
                    code, oom = self.judge._run(
                        ["/work/main"],
                        work=work,
                        writable=False,
                        memory=1024**3,
                        seconds=policy.execution_seconds,
                        output=output,
                        input_path=input_path,
                        output_limit=policy.capture_bytes_per_stream,
                        stderr_limit=policy.capture_bytes_per_stream,
                        deadline=deadline,
                    )
                    evidence["records"].append(
                        {
                            "name": input_path.name,
                            "sha256": file_sha256(input_path),
                            "bytes": input_path.stat().st_size,
                        }
                    )
                    feedback["runs"].append(
                        {
                            "input_index": index,
                            "exit_code": code,
                            "out_of_memory": oom,
                            "timed_out": code in {124, 137, 143, 152},
                            "output_limit_exceeded": code == 153,
                            "stdout": stream(output),
                            "stderr": stream(output.with_suffix(".stderr")),
                        }
                    )
        return CompilerReceipt(**base, feedback=feedback, execution_evidence=evidence)


def tokenizer_messages(request: GenerationRequest) -> list[dict[str, Any]]:
    """Convert public Responses items to the checkpoint's chat-template structure."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": request.instructions}]
    if isinstance(request.input, str):
        messages.append({"role": "user", "content": request.input})
        return messages
    for item in request.input:
        if item.get("type") == "function_call":
            call = {
                "id": item["call_id"],
                "type": "function",
                "function": {"name": item["name"], "arguments": json.loads(item["arguments"])},
            }
            if messages[-1].get("role") == "assistant":
                messages[-1].setdefault("tool_calls", []).append(call)
            else:
                messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif item.get("type") == "function_call_output":
            messages.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
            )
        elif item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
            messages.append({"role": item["role"], "content": item["content"]})
        else:
            raise ValueError("non-public item in compiler tokenizer input")
    return messages


def count_tool_input(tokenizer: Any, request: GenerationRequest) -> int:
    from padawan.adapters.openai_compatible.local_metal import local_template_kwargs

    v2 = any(tool.get("name") == "submit_solution" for tool in request.tools)
    encoded = tokenizer.apply_chat_template(
        tokenizer_messages(request),
        tokenize=True,
        add_generation_prompt=True,
        return_dict=True,
        tools=[
            {
                "type": "function",
                "function": {k: v for k, v in tool.items() if k != "type"},
            }
            for tool in request.tools
        ],
        **(local_template_kwargs(request) if v2 else {"force_nonempty_content": True}),
    )
    return len(encoded["input_ids"])


async def _retain_receipt(
    executor: IdempotentGenerationExecutor,
    rid: str,
    index: int,
    receipt: CompilerReceipt,
    run_id: str,
) -> ArtifactRef:
    ref = await artifact_put_bytes(
        executor.artifacts,
        canonical_json_bytes(receipt),
        media_type=RECEIPT_MEDIA,
        restricted=True,
        raw_data=True,
    )
    async with executor.database.transaction() as session:
        await session.execute(
            update(RunRow).where(RunRow.run_id == run_id).values(paused=RunRow.paused)
        )
        await retain_owned(
            session,
            executor.catalog,
            reference=ref,
            owner_type="atlas_compiler_receipt",
            owner_id=f"{rid}:{index}",
        )
    return ref


async def run_tool_coding_trial(
    *,
    executor: IdempotentGenerationExecutor,
    client: PreparedGenerationClient,
    activation_ref: ArtifactRef,
    request: AtlasTrialRequest,
    generation_request: GenerationRequest,
    item: AtlasItemManifest,
    package: JudgePackage,
    judge: DockerBatchJudge,
    policy: CodingPolicy,
    tokenizer: Any,
    judge_semaphore: asyncio.Semaphore,
    judge_timeout_seconds: float,
    admission_check: Callable[[], Awaitable[bool]] | None = None,
) -> str:
    """One comparison cell; separate native model effects, one terminal hidden judgment."""
    from padawan.atlas.coding_runner import extract_cpp, judge_with_deadline

    catalog = ArtifactCatalog(executor.artifacts)
    async with executor.database.transaction() as session:
        completed = await owned_reference(
            session, catalog, owner_type="atlas_coding_tool_result", owner_id=request.request_id
        )
        if completed is not None:
            value = json.loads(
                await artifact_read_bytes(executor.artifacts, completed, allow_restricted=True)
            )
            # Validate all explicitly retained source bytes even for read-only replay.
            for record in value["artifacts"]:
                await artifact_read_bytes(
                    executor.artifacts,
                    ArtifactRef.model_validate_json(json.dumps(record)),
                    allow_restricted=True,
                )
            return str(value["status"])
    initial_tokens = count_tool_input(tokenizer, generation_request)
    activation = AtlasActivation.model_validate_json(
        await artifact_read_bytes(executor.artifacts, activation_ref, allow_restricted=True)
    )
    started_at = datetime.now(UTC)
    async with executor.database.transaction() as session:
        prior_trajectory = await owned_reference(
            session, catalog, owner_type="atlas_coding_tool_trajectory", owner_id=request.request_id
        )
    if prior_trajectory is not None:
        trajectory = CodingToolTrajectory.model_validate_json(
            await artifact_read_bytes(executor.artifacts, prior_trajectory, allow_restricted=True)
        )
        started_at = trajectory.started_at
    else:
        trajectory = CodingToolTrajectory(
            trajectory_id=request.request_id,
            run_id=request.run_id,
            initial_request=generation_request,
            initial_input_tokens=initial_tokens,
            activation=activation_ref,
            policy=policy,
            problem_id=item.metadata["problem_id"],
            statement_digest=sha256_digest(item.prompt),
            started_at=started_at,
            model_deadline=min(
                started_at + timedelta(seconds=policy.trajectory_timeout_seconds),
                activation.expires_at - timedelta(seconds=judge_timeout_seconds),
            ),
        )
    # Native registered items/requests still own the original full statement and hidden package.
    if (
        generation_request.input != item.prompt
        or item.verifier_payload["archive_sha256"] != package.archive_digest
    ):
        raise PermissionError("tool trajectory statement or judge differs from the fixed root")
    if (
        trajectory.initial_request != generation_request
        or trajectory.policy != policy
        or trajectory.activation != activation_ref
        or trajectory.initial_input_tokens != initial_tokens
    ):
        raise PermissionError(
            "compiler trajectory cannot be resumed under changed inputs or policy"
        )
    async with executor.database.transaction() as session:
        trajectory_ref = await register_trajectory(session, catalog, trajectory)
    started = time.monotonic()
    model_deadline = started + (trajectory.model_deadline - datetime.now(UTC)).total_seconds()
    previous: list[tuple[GenerationResult, tuple[CompilerReceipt, ...]]] = []
    refs = [trajectory_ref, activation_ref]
    inputs_total = outputs_total = calls_total = 0
    model_turns = 0
    compiler_executions = 0
    compiler_attempts = 0
    v2_policy = policy if isinstance(policy, CompilerPolicyV2) else None
    v2 = v2_policy is not None
    generation_limit = (
        v2_policy.generation_token_limit
        if v2_policy is not None
        else generation_request.sampling.max_output_tokens
    )
    generation: GenerationResult | None = None
    final_text = ""
    termination = "generation_allowance_exhausted"
    tool = CompilerTool(judge, policy)
    for turn in range(policy.max_model_turns):
        try:
            if turn:
                provisional = next_tool_request(trajectory, previous, input_tokens=1)
                count = count_tool_input(tokenizer, provisional)
            else:
                count = initial_tokens
            current = next_tool_request(trajectory, previous, input_tokens=count)
        except ValueError:
            if generation is None:
                raise
            termination = "public_history_limit_exhausted"
            break
        if admission_check is not None and not await admission_check():
            raise PermissionError("compiler continuation stopped by the live admission guard")
        seconds_left = model_deadline - time.monotonic()
        if seconds_left <= 0:
            if v2 and generation is not None:
                termination = "trajectory_deadline_before_next_effect"
                break
            raise TimeoutError("compiler trajectory deadline exhausted before a new model effect")
        async with asyncio.timeout(seconds_left):
            generation = await executor.execute(
                run_id=request.run_id,
                purpose="capability_atlas",
                provider=activation.provider,
                request=current,
                prepared=client.prepare_generation(current),
                atlas_activation=activation_ref,
                atlas_tool_trajectory=trajectory_ref,
            )
        async with executor.database.transaction() as session:
            row = await session.get(ExternalCallRow, current.request_id)
            assert row is not None and row.response_artifact_id is not None
            artifact = await session.get(ArtifactRow, row.response_artifact_id)
            assert artifact is not None
            refs.append(_artifact_row_to_reference(artifact))
        outputs_total += output_usage(generation, current.sampling.max_output_tokens)
        if outputs_total > generation_limit:
            raise ValueError("cumulative output exceeds the original trajectory allowance")
        model_turns += 1
        inputs_total += generation.usage["input_tokens"]
        if generation.usage["input_tokens"] > policy.max_input_tokens_per_turn or generation.usage[
            "input_tokens"
        ] + outputs_total > policy.max_context_tokens + (
            outputs_total - generation.usage["output_tokens"]
        ):
            raise ValueError("native usage exceeded the declared per-turn context")
        _, calls = public_response_items(generation)
        progress = {
            "event": "compiler_model_turn",
            "trajectory": request.request_id,
            "turn": turn,
            "output_tokens": generation.usage["output_tokens"],
            "total_output_tokens": outputs_total,
            "tool_calls": len(calls),
        }
        print(json.dumps(progress), flush=True)
        if not calls:
            if v2:
                previous.append((generation, ()))
                termination = "no_explicit_submission"
                if outputs_total >= generation_limit:
                    break
                continue
            final_text = generation.output_text
            termination = "final_submission" if final_text.strip() else "no_final_submission"
            break
        if len(calls) + calls_total > policy.max_tool_calls:
            termination = "tool_call_limit_exceeded"
            break
        if outputs_total >= generation_limit and not (
            v2 and len(calls) == 1 and calls[0]["name"] == "submit_solution"
        ):
            termination = "generation_allowance_exhausted_before_tool_feedback"
            break
        receipts = []
        for number, call in enumerate(calls):
            async with executor.database.transaction() as session:
                prior = await owned_reference(
                    session,
                    catalog,
                    owner_type="atlas_compiler_receipt",
                    owner_id=f"{current.request_id}:{number}",
                )
            if prior is not None:
                receipt = CompilerReceipt.model_validate_json(
                    await artifact_read_bytes(executor.artifacts, prior, allow_restricted=True)
                )
                ref = prior
            else:
                intent = await artifact_put_bytes(
                    executor.artifacts,
                    canonical_json_bytes(
                        {
                            "trajectory_id": request.request_id,
                            "model_turn": current.request_id,
                            "call": call,
                            "policy_digest": policy.digest,
                        }
                    ),
                    media_type="application/vnd.padawan.atlas-compiler-intent+json",
                    restricted=True,
                    raw_data=True,
                )
                async with executor.database.transaction() as session:
                    await session.execute(
                        update(RunRow)
                        .where(RunRow.run_id == request.run_id)
                        .values(paused=RunRow.paused)
                    )
                    outstanding = await owned_reference(
                        session,
                        catalog,
                        owner_type="atlas_compiler_intent",
                        owner_id=f"{current.request_id}:{number}",
                    )
                    if outstanding is not None:
                        raise PermissionError(
                            "unresolved compiler action is not automatically repeated"
                        )
                    await retain_owned(
                        session,
                        catalog,
                        reference=intent,
                        owner_type="atlas_compiler_intent",
                        owner_id=f"{current.request_id}:{number}",
                    )
                refs.append(intent)
                # Tool time and waiting for local CPU slots share the trajectory wall budget.
                remaining = model_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        "compiler trajectory deadline exhausted before tool execution"
                    )
                await asyncio.wait_for(judge_semaphore.acquire(), timeout=remaining)
                try:
                    forbidden = v2_policy is not None and (
                        len(calls) != 1
                        or (
                            call["name"] == TOOL_NAME
                            and (
                                current.metadata.get("compiler_phase") == "finalize"
                                or compiler_attempts >= v2_policy.max_compiler_calls
                            )
                        )
                    )
                    if forbidden:
                        receipt = CompilerReceipt(
                            trajectory_id=request.request_id,
                            turn_index=turn,
                            call_id=call["call_id"],
                            call_digest=sha256_digest(call),
                            policy_digest=policy.digest,
                            feedback={
                                "status": "invalid_tool",
                                "requirements": (
                                    "Call one available function; compiler limit is four."
                                ),
                            },
                            execution_evidence={"executed": False},
                        )
                    else:
                        work = asyncio.create_task(
                            asyncio.to_thread(
                                tool.run,
                                call,
                                trajectory_id=request.request_id,
                                turn_index=turn,
                                deadline=model_deadline,
                            )
                        )
                        try:
                            receipt = await asyncio.shield(work)
                        except asyncio.CancelledError:
                            await work  # await the owned container's bounded cleanup
                            raise
                finally:
                    judge_semaphore.release()
                ref = await _retain_receipt(
                    executor, current.request_id, number, receipt, request.run_id
                )
            receipts.append(receipt)
            refs.append(ref)
            calls_total += 1
            compiler_attempts += int(call["name"] == TOOL_NAME)
            compiler_executions += int(receipt.execution_evidence.get("executed") is True)
            if v2 and receipt.feedback["status"] == "submission_received":
                from padawan.atlas.coding_v2 import tool_input_v2

                assert isinstance(policy, CompilerPolicyV2)
                final_text = tool_input_v2(call, policy).source
                termination = "explicit_submission"
            print(
                json.dumps(
                    {
                        "event": "compiler_feedback",
                        "trajectory": request.request_id,
                        "turn": turn,
                        "status": receipt.feedback["status"],
                        "calls_used": calls_total,
                    }
                ),
                flush=True,
            )
        previous.append((generation, tuple(receipts)))
        if v2 and termination == "explicit_submission":
            break
        if v2 and (calls_total >= policy.max_tool_calls or outputs_total >= generation_limit):
            termination = "allowance_exhausted_without_submission"
            break
    assert generation is not None
    source = final_text if v2 else extract_cpp(final_text)
    evidence: dict[str, Any] = {
        "evaluated_output_digest": sha256_digest(final_text),
        "termination": termination,
        "final_generation_request_id": generation.request_id,
        "tool_calls_used": calls_total,
        "compiler_executions": compiler_executions,
        "compiler_access_available": True,
        **(
            {"explicit_submission_required": True, "compiler_attempts": compiler_attempts}
            if v2
            else {}
        ),
    }
    disposition = VerifierDisposition.REJECTED
    summary = "No final C++ program was submitted within the finite compiler trajectory."
    if source:
        result = await judge_with_deadline(
            semaphore=judge_semaphore,
            judge=judge,
            package=package,
            source=source,
            timeout_seconds=judge_timeout_seconds,
        )
        evidence.update(asdict(result))
        disposition = (
            VerifierDisposition.VERIFIED if result.success else VerifierDisposition.REJECTED
        )
        summary = result.verdict
    verifier = VerifierResult(
        result_id=f"coding-tool-verifier-{request.request_id}",
        verifier_id="livecodebench-pro.batch",
        verifier_version="padawan-1",
        scope=request.request_id,
        disposition=disposition,
        deterministic=True,
        summary=summary,
        evidence=evidence,
        created_at=datetime.now(UTC),
    )
    status = (
        "verified_success" if disposition == VerifierDisposition.VERIFIED else "verified_failure"
    )
    final_ref = await artifact_put_bytes(
        executor.artifacts,
        final_text.encode(),
        media_type="text/plain",
        restricted=True,
        raw_data=True,
    )
    refs.append(final_ref)
    payload = {
        "schema_version": "2" if v2 else "1",
        "kind": "atlas_coding_tool_trajectory_result",
        "request": request,
        "trajectory": trajectory_ref,
        "problem_id": item.metadata["problem_id"],
        "status": status,
        "verifier": verifier,
        "policy": policy,
        "model_turns": model_turns,
        "tool_calls": calls_total,
        "compiler_executions": compiler_executions,
        "tokens": {"input_tokens": inputs_total, "output_tokens": outputs_total},
        "final_output": final_ref,
        "final_generation_request_id": generation.request_id,
        "artifacts": refs,
        "elapsed_seconds": time.monotonic() - started,
        "completed_at": datetime.now(UTC),
        "information_boundary": {
            "private_reasoning_in_context": False,
            "hidden_judge_feedback": False,
        },
    }
    result_ref = await artifact_put_bytes(
        executor.artifacts,
        canonical_json_bytes(payload),
        media_type=RESULT_MEDIA,
        restricted=True,
        raw_data=True,
    )
    async with executor.database.transaction() as session:
        await session.execute(
            update(RunRow).where(RunRow.run_id == request.run_id).values(paused=RunRow.paused)
        )
        await RewardEngine().record_verifier_result(session, verifier)
        for reference in refs:
            await catalog.reference(
                session,
                reference,
                owner_type="atlas_coding_tool_result_source",
                owner_id=request.request_id,
            )
        await retain_owned(
            session,
            catalog,
            reference=result_ref,
            owner_type="atlas_coding_tool_result",
            owner_id=request.request_id,
        )
    return status
