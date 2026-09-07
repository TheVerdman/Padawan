"""Real local integration, native registration, checkpointed execution, and reports.

The separately owned supervisor launches this controller. Its model transport is loopback-only;
only public code/tool feedback enters a continuation. No cloud client is constructed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from padawan.adapters.base import GenerationRequest
from padawan.adapters.openai_compatible.local_metal import LocalMetalClient
from padawan.artifacts.store import LocalArtifactStore
from padawan.atlas.coding_judge import file_sha256, unpack_package
from padawan.atlas.coding_tool_contracts import (
    CodingToolTrajectory,
    CompilerPolicy,
    CompilerPolicyV2,
    compiler_tools,
    next_tool_request,
    output_usage,
    public_response_items,
)
from padawan.atlas.coding_tools import CompilerTool, count_tool_input, run_tool_coding_trial
from padawan.atlas.coding_v2 import tool_input_v2
from padawan.atlas.contracts import (
    AtlasItemManifest,
    AtlasTrialRequest,
)
from padawan.atlas.dependencies import coding_dependency
from padawan.atlas.local_campaign import (
    make_judge as make_judge,
)
from padawan.atlas.local_campaign import (
    native_counts as native_counts,
)
from padawan.atlas.local_campaign import (
    register as register,
)
from padawan.atlas.local_campaign import (
    write_report as write_report,
)
from padawan.atlas.orchestration import FixedRunConfiguration, generation_request_for
from padawan.atlas.preparation import record as record
from padawan.models.contracts import ArtifactRef, SamplingConfiguration
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    HarnessProfile,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor, _serialize_result
from padawan.orchestration.local_host import (
    append_event,
    atomic_save,
    container_inventory,
    load,
    now,
)
from padawan.orchestration.source_identity import source_identity


def checked_inputs(root: Path) -> dict:
    inputs = load(root / "inputs.json")
    if source_identity(Path(__file__).resolve().parents[1]) != inputs["sources"]:
        raise ValueError("runtime source changed after preparation")
    if sha256_digest(load(root / "configuration.json")) != inputs["config_digest"]:
        raise ValueError("local condition changed after preparation")
    if (
        file_sha256(Path(inputs["dataset"]) / "prepared-dataset.json")
        != inputs["environment_parameters"]["dataset_digest"]
    ):
        raise ValueError("benchmark inventory changed after preparation")
    return inputs


async def real_preflight(root: Path, inputs: dict, client: LocalMetalClient, tokenizer) -> dict:
    config = inputs["config"]
    policy = record(CompilerPolicyV2, config["compiler_policy"])
    judge = make_judge(root, inputs)
    tool = CompilerTool(judge, policy)
    directory = root / "preflight"
    directory.mkdir(mode=0o700)
    checks, generations = [], []
    dummy = ArtifactRef(
        artifact_id="local-preflight-authority",
        uri="artifact://sha256/" + inputs["config_digest"][7:],
        digest=inputs["config_digest"],
        media_type="application/json",
        size_bytes=(root / "configuration.json").stat().st_size,
        restricted=True,
        raw_data=True,
    )

    async def generate(request: GenerationRequest, path: Path):
        if (root / "stop.json").exists():
            raise PermissionError("local supervisor stopped integration")
        count = count_tool_input(tokenizer, request)
        prepared = client.prepare_generation(request)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.with_suffix(".request.json").write_text(prepared.body_json)
        result = await client.generate_prepared(request, prepared)
        path.with_suffix(".generation.json").write_bytes(_serialize_result(result))
        path.with_suffix(".response.json").write_bytes(result.raw_response)
        if result.usage["input_tokens"] != count:
            raise ValueError("local tokenizer and actual server input counts differ")
        output_usage(result, request.sampling.max_output_tokens)
        generations.append(
            {
                "request_id": request.request_id,
                "input_tokens": count,
                "output_tokens": result.usage["output_tokens"],
                "raw_request_digest": sha256_digest(result.raw_request),
                "raw_response_digest": sha256_digest(result.raw_response),
            }
        )
        append_event(root / "events.jsonl", {"event": "preflight_model_turn", **generations[-1]})
        return result

    for index, factor in enumerate((2, 3)):
        broken = (
            "#include <iostream>\nint main(){long long n;std::cin>>n;std::cout<<n*"
            + str(factor)
            + "<<'\\n'}\n"
        )
        prompt = (
            "This is a compiler-integration control. Read one integer and output it multiplied by "
            + str(factor)
            + ". First call compile_and_run using EXACTLY the following intentionally "
            "broken source and stdin string 21 followed by a newline. "
            "This first call must demonstrate "
            "compiler-error feedback; do not fix the source before the first call. After receiving "
            "the error, repair it, compile and run it again, then call submit_solution with the "
            "correct program. Here is the intentionally broken source:\n" + broken
        )
        initial = GenerationRequest(
            request_id=f"local-preflight-{index}",
            instructions=config["instructions"],
            input=prompt,
            sampling=SamplingConfiguration(
                max_output_tokens=8192, temperature=0.7, top_p=0.95, seed=2026090704 + index
            ),
            tools=compiler_tools(policy),
            tool_choice="auto",
        )
        started = datetime.now(UTC)
        trajectory = CodingToolTrajectory(
            trajectory_id=initial.request_id,
            run_id="local-operational-preflight",
            initial_request=initial,
            initial_input_tokens=count_tool_input(tokenizer, initial),
            activation=dummy,
            policy=policy,
            problem_id=f"control-{index}",
            statement_digest=sha256_digest(prompt),
            started_at=started,
            model_deadline=started + timedelta(seconds=1200),
        )
        previous, statuses, final = [], [], ""
        for turn in range(policy.max_model_turns):
            provisional = next_tool_request(trajectory, previous, input_tokens=1)
            current = next_tool_request(
                trajectory, previous, input_tokens=count_tool_input(tokenizer, provisional)
            )
            result = await generate(current, directory / f"case-{index}/turn-{turn}")
            _, calls = public_response_items(result)
            if len(calls) != 1:
                raise ValueError(
                    "automatic integration must produce exactly one addressed tool call"
                )
            receipt = await asyncio.to_thread(
                tool.run,
                calls[0],
                trajectory_id=initial.request_id,
                turn_index=turn,
                deadline=time.monotonic() + 150,
            )
            atomic_save(
                directory / f"case-{index}/receipt-{turn}.json", receipt.model_dump(mode="json")
            )
            statuses.append(receipt.feedback["status"])
            previous.append((result, (receipt,)))
            if receipt.feedback["status"] == "submission_received":
                final = tool_input_v2(calls[0], policy).source
                break
        if not final or "compile_error" not in statuses or "compiled" not in statuses:
            raise ValueError("real automatic tool-error-repair-submission cycle did not pass")
        final_policy = CompilerPolicy(judge_image=judge.image)
        final_call = {
            "type": "function_call",
            "name": "compile_and_run",
            "call_id": f"validation-{index}",
            "arguments": json.dumps(
                {"source": final, "stdin": ["0\n", "21\n", "-7\n", "100000\n"]}
            ),
        }
        verified = await asyncio.to_thread(
            CompilerTool(judge, final_policy).run,
            final_call,
            trajectory_id=f"validation-{index}",
            turn_index=0,
            deadline=time.monotonic() + 150,
        )
        actual = [item["stdout"]["text"].strip() for item in verified.feedback.get("runs", [])]
        expected = [str(value * factor) for value in (0, 21, -7, 100000)]
        if actual != expected or any(item["exit_code"] for item in verified.feedback["runs"]):
            raise ValueError("explicit final program failed the independent CPU integration check")
        atomic_save(
            directory / f"case-{index}/final-validation.json", verified.model_dump(mode="json")
        )
        checks.append(
            {"case": index, "passed": True, "statuses": statuses, "cpu_cases": len(expected)}
        )

    # A real request near the maximum allowed input checks the new 32K server context.
    filler = "Neutral context-capacity observation. " * 16000
    suffix = (
        "\nSubmit a C++17 program that reads one integer and prints twice that integer. "
        "Call submit_solution only."
    )
    probe = GenerationRequest(
        request_id="local-preflight-input-capacity",
        instructions="Return the requested function call.",
        input="",
        sampling=SamplingConfiguration(max_output_tokens=512, temperature=0, seed=2026090706),
        tools=(compiler_tools(policy)[1],),
        tool_choice="auto",
        metadata={"compiler_phase": "finalize"},
    )
    low, high = 0, len(filler)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = probe.model_copy(update={"input": filler[:middle] + suffix})
        if count_tool_input(tokenizer, candidate) <= policy.max_input_tokens_per_turn:
            low = middle
        else:
            high = middle - 1
    probe = probe.model_copy(update={"input": filler[:low] + suffix})
    result = await generate(probe, directory / "input-capacity")
    _, calls = public_response_items(result)
    if len(calls) != 1 or calls[0]["name"] != "submit_solution":
        raise ValueError("long-input real tool probe did not submit an explicit program")
    tool_input_v2(calls[0], policy)
    checks.append(
        {"case": "input-capacity", "passed": True, "input_tokens": result.usage["input_tokens"]}
    )
    if container_inventory(root.name):
        raise RuntimeError("integration left an owned CPU container")
    prepared = client.prepare_generation(probe)
    report = {
        "passed": True,
        "at": now(),
        "provider": client.provider,
        "model_id": config["model_id"],
        "protocol": "responses",
        "checkpoint_revision": config["model_revision"],
        "serving_artifact_digest": inputs["model"]["serving_artifact"]["digest"],
        "server_configuration_digest": inputs["config_digest"],
        "destination": prepared.destination,
        "configuration_digest": prepared.configuration_digest,
        "compiler_tool_preflight": {
            "passed": True,
            "tool_manifest_digest": sha256_digest(
                {"tools": compiler_tools(policy), "tool_choice": "auto"}
            ),
            "selection": "auto",
            "checks": checks,
        },
        "generations": generations,
        "real_model_calls": len(generations),
        "output_tokens": sum(item["output_tokens"] for item in generations),
        "input_tokens": sum(item["input_tokens"] for item in generations),
    }
    atomic_save(root / "preflight.json", report)
    return report


async def worker(root: Path, resume: bool) -> int:
    inputs = checked_inputs(root)
    config = inputs["config"]
    policy = record(CompilerPolicyV2, config["compiler_policy"])
    tokenizer = coding_dependency("transformers").AutoTokenizer.from_pretrained(
        config["model_path"], local_files_only=True, trust_remote_code=False
    )
    client = LocalMetalClient(
        base_url=f"http://127.0.0.1:{config['port']}",
        model=config["model_id"],
        timeout_seconds=config["model_timeout_seconds"],
    )
    db = None
    try:
        if not resume:
            await real_preflight(root, inputs, client, tokenizer)
            append_event(root / "events.jsonl", {"event": "real_preflight_passed"})
            bundle = await register(root, inputs, client)
        else:
            bundle = load(root / "bundle.json")
            if sha256_digest(bundle) != load(root / "bundle-identity.json")["digest"]:
                raise ValueError("registered bundle changed before controller recovery")
        artifacts = LocalArtifactStore(root / "artifacts")
        db = Database.sqlite(root / "atlas.sqlite3")
        executor = IdempotentGenerationExecutor(database=db, artifacts=artifacts, client=client)
        judge = make_judge(root, inputs)
        requests = {r["request_id"]: record(AtlasTrialRequest, r) for r in bundle["requests"]}
        items = {i["item_digest"]: record(AtlasItemManifest, i) for i in bundle["items"]}
        profile = record(HarnessProfile, bundle["profile"])
        configuration = record(FixedRunConfiguration, bundle["configuration"])
        activation = record(ArtifactRef, bundle["activation"])
        progress = (
            load(root / "progress.json")
            if (root / "progress.json").exists()
            else {"completed": [], "next_index": 0, "phase": "main"}
        )
        initial_count = len(progress["completed"])
        preflight_tokens = load(root / "preflight.json")["output_tokens"]
        state = load(root / "state.json")

        async def admitted() -> bool:
            if (root / "stop.json").exists():
                return False
            guard = load(root / "guard.json")
            return (
                guard["healthy"]
                and time.time() - datetime.fromisoformat(guard["at"]).timestamp() < 30
            )

        async def run_one(rid: str) -> str:
            request = requests[rid]
            item = items[request.item_digest]
            pid = item.metadata["problem_id"]
            with tempfile.TemporaryDirectory(prefix="episode-", dir=root / "scratch") as tmp:
                package = unpack_package(
                    Path(inputs["dataset"]) / "archives" / f"{pid}.zip",
                    Path(tmp) / pid,
                    problem_id=pid,
                    expected_digest=item.verifier_payload["archive_sha256"],
                    include_extra_cases=True,
                )
                generation = generation_request_for(
                    request=request, item=item, configuration=configuration, profile=profile
                )
                return await run_tool_coding_trial(
                    executor=executor,
                    client=client,
                    activation_ref=activation,
                    request=request,
                    generation_request=generation,
                    item=item,
                    package=package,
                    judge=judge,
                    policy=policy,
                    tokenizer=tokenizer,
                    judge_semaphore=asyncio.Semaphore(1),
                    judge_timeout_seconds=config["judge_timeout_seconds"],
                    admission_check=admitted,
                )

        if resume and progress["completed"]:
            before = native_counts(root)
            last = progress["completed"][-1]
            observed = await run_one(last["request_id"])
            after = native_counts(root)
            if before != after or observed != last["status"]:
                raise RuntimeError("controller recovery duplicated or changed completed evidence")
            atomic_save(
                root / "resume-proof.json",
                {
                    "passed": True,
                    "at": now(),
                    "before": before,
                    "after": after,
                    "replayed_request": last["request_id"],
                    "model_calls_added": 0,
                },
            )
            append_event(
                root / "events.jsonl",
                {"event": "controller_recovery_verified", "model_calls_added": 0},
            )
        for index in range(progress["next_index"], len(bundle["schedule"])):
            totals = native_counts(root)
            if totals["unresolved"]:
                raise RuntimeError("unresolved native effects cannot be automatically replayed")
            if (root / "stop.json").exists() or time.time() >= state["dispatch_deadline_epoch"]:
                progress["stop_reason"] = "dispatch_deadline_or_supervisor_stop"
                break
            if (
                totals["output_tokens"] + preflight_tokens + policy.generation_token_limit
                > config["maximum_generated_tokens"]
            ):
                progress["stop_reason"] = "global_generation_budget"
                break
            rid = bundle["schedule"][index]
            pid = items[requests[rid].item_digest].metadata["problem_id"]
            progress.update(
                phase="main", active_request=rid, active_problem=pid, active_started_at=now()
            )
            atomic_save(root / "progress.json", progress)
            append_event(
                root / "events.jsonl",
                {"event": "episode_started", "request_id": rid, "problem_id": pid, "index": index},
            )
            outcome = await run_one(rid)
            progress["completed"].append(
                {"request_id": rid, "problem_id": pid, "status": outcome, "at": now()}
            )
            progress.update(
                next_index=index + 1,
                active_request=None,
                active_problem=None,
                counts=native_counts(root),
            )
            atomic_save(root / "progress.json", progress)
            append_event(
                root / "events.jsonl",
                {
                    "event": "episode_completed",
                    "request_id": rid,
                    "problem_id": pid,
                    "status": outcome,
                    "index": index,
                },
            )
            write_report(root)
            if (
                not resume
                and len(progress["completed"]) - initial_count
                == config["controller_restart_after_completed_episodes"]
            ):
                append_event(root / "events.jsonl", {"event": "controller_checkpoint_ready"})
                return 75
        progress["phase"] = "finished"
        progress.setdefault("stop_reason", "finite_queue_completed")
        atomic_save(root / "progress.json", progress)
        return 0
    finally:
        if db is not None:
            await db.close()
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    os.umask(0o077)
    if args.report_only:
        write_report(root)
        return
    stopped = threading.Event()

    def heartbeat():
        while not stopped.is_set():
            atomic_save(root / "worker-heartbeat.json", {"at": now(), "pid": os.getpid()})
            stopped.wait(5)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    code = 1
    try:
        code = asyncio.run(worker(root, args.resume))
    except BaseException as error:
        atomic_save(
            root / "controller-error.json",
            {
                "at": now(),
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            },
        )
        append_event(
            root / "events.jsonl", {"event": "controller_failed", "type": type(error).__name__}
        )
    finally:
        stopped.set()
        thread.join(timeout=10)
        write_report(root)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
