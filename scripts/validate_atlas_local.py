"""Prelaunch CPU, orphan-cleanup and native-admission controls; no model transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from run_atlas_local import make_judge, native_counts, record, register, write_report
from transformers import AutoTokenizer

from padawan.adapters.base import GenerationRequest
from padawan.adapters.openai_compatible.local_metal import LocalMetalClient
from padawan.artifacts.store import LocalArtifactStore
from padawan.atlas.coding_tool_boundary import register_trajectory
from padawan.atlas.coding_tool_contracts import (
    CodingToolTrajectory,
    CompilerPolicyV2,
    compiler_tools,
)
from padawan.atlas.coding_tools import CompilerTool, count_tool_input
from padawan.atlas.contracts import AtlasItemManifest, AtlasTrialRequest
from padawan.atlas.local_host import (
    atomic_save,
    cleanup_containers,
    container_inventory,
    host_snapshot,
    load,
    now,
    owned_group_members,
    process_identity,
    stop_owned,
)
from padawan.atlas.orchestration import FixedRunConfiguration, generation_request_for
from padawan.models.contracts import ArtifactRef, SamplingConfiguration
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import HarnessProfile
from padawan.orchestration.external_calls import IdempotentGenerationExecutor


async def native_control(root: Path, inputs: dict) -> dict:
    root.mkdir(mode=0o700)
    (root / "scratch").mkdir()
    inputs = json.loads(json.dumps(inputs))
    chosen = []
    for label in ("easy", "medium", "hard"):
        chosen.append(
            next(
                p
                for p in inputs["selected_problem_ids"]
                if inputs["difficulty_by_problem"][p] == label
            )
        )
    inputs["selected_problem_ids"] = chosen
    inputs["config"].update(items_per_difficulty=1, repetitions=2)
    atomic_save(root / "inputs.json", inputs)
    atomic_save(root / "state.json", {"hard_deadline_epoch": time.time() + 3600})
    config = inputs["config"]
    client = LocalMetalClient(
        base_url=f"http://127.0.0.1:{config['port']}", model=config["model_id"]
    )
    db = None
    stopped = []

    class NoTransportError(RuntimeError):
        pass

    async def no_transport(request, prepared):
        stopped.append(request.request_id)
        raise NoTransportError("offline admission control; no HTTP permitted")

    client.generate_prepared = no_transport
    try:
        policy = record(CompilerPolicyV2, config["compiler_policy"])
        prepared = client.prepare_generation(
            GenerationRequest(
                request_id="control",
                instructions="control",
                input="control",
                sampling=SamplingConfiguration(max_output_tokens=1),
            )
        )
        atomic_save(
            root / "preflight.json",
            {
                "passed": True,
                "offline_control_fixture_only": True,
                "model_calls": 0,
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
                    "offline_control_fixture_only": True,
                    "tool_manifest_digest": sha256_digest(
                        {"tools": compiler_tools(policy), "tool_choice": "auto"}
                    ),
                },
            },
        )
        bundle = await register(root, inputs, client)
        assert len(bundle["requests"]) == len(set(bundle["schedule"])) == 6
        db = Database.sqlite(root / "atlas.sqlite3")
        executor = IdempotentGenerationExecutor(
            database=db, artifacts=LocalArtifactStore(root / "artifacts"), client=client
        )
        tokenizer = AutoTokenizer.from_pretrained(
            config["model_path"], local_files_only=True, trust_remote_code=False
        )
        request = record(AtlasTrialRequest, bundle["requests"][0])
        item = record(
            AtlasItemManifest,
            next(i for i in bundle["items"] if i["item_digest"] == request.item_digest),
        )
        generation = generation_request_for(
            request=request,
            item=item,
            configuration=record(FixedRunConfiguration, bundle["configuration"]),
            profile=record(HarnessProfile, bundle["profile"]),
        )
        assert generation.sampling.max_output_tokens == policy.per_turn_token_limit
        activation = record(ArtifactRef, bundle["activation"])
        started = datetime.now(UTC)
        trajectory = CodingToolTrajectory(
            trajectory_id=request.request_id,
            run_id=request.run_id,
            initial_request=generation,
            activation=activation,
            policy=policy,
            problem_id=item.metadata["problem_id"],
            statement_digest=sha256_digest(item.prompt),
            initial_input_tokens=count_tool_input(tokenizer, generation),
            started_at=started,
            model_deadline=started + timedelta(minutes=20),
        )
        async with db.transaction() as session:
            ref = await register_trajectory(session, executor.catalog, trajectory)
        for expected in (NoTransportError, PermissionError):
            try:
                await executor.execute(
                    run_id=request.run_id,
                    purpose="capability_atlas",
                    provider=client.provider,
                    request=generation,
                    prepared=client.prepare_generation(generation),
                    atlas_activation=activation,
                    atlas_tool_trajectory=ref,
                )
            except expected:
                pass
            else:
                raise AssertionError("native control did not stop at the expected boundary")
        assert stopped == [request.request_id]
        counts = native_counts(root)
        assert counts["calls"] == counts["unresolved"] == 1
        write_report(root)
        assert load(root / "summary.json")["planned"] == 6
        return {
            "passed": True,
            "planned": 6,
            "admitted_intents": 1,
            "model_calls": 0,
            "unresolved_replay_refused": True,
            "report_written": True,
        }
    finally:
        if db is not None:
            await db.close()
        await client.close()


def cpu_controls(root: Path, inputs: dict) -> dict:
    judge = make_judge(root, inputs)
    policy = record(CompilerPolicyV2, inputs["config"]["compiler_policy"])
    tool = CompilerTool(judge, policy)
    cases = [
        (
            "valid-string-input",
            "#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<n*2;}",
            "21\n",
            "compiled",
            "42",
        ),
        ("compile-error", "int main(){ this is invalid C++; }", "", "compile_error", None),
        ("execution-timeout", 'int main(){for(;;){asm volatile("");}}', "", "compiled", None),
    ]
    reports = []
    for index, (name, source, stdin, status, output) in enumerate(cases):
        receipt = tool.run(
            {
                "type": "function_call",
                "name": "compile_and_run",
                "call_id": name,
                "arguments": json.dumps({"source": source, "stdin": stdin}),
            },
            trajectory_id="offline-cpu-control",
            turn_index=index,
            deadline=time.monotonic() + 90,
        )
        assert receipt.feedback["status"] == status
        if output is not None:
            assert receipt.feedback["runs"][0]["stdout"]["text"].strip() == output
        if name == "execution-timeout":
            assert receipt.feedback["runs"][0]["timed_out"]
        reports.append({"name": name, "passed": True})
        print(json.dumps({"control": name, "passed": True}), flush=True)
    assert not container_inventory(root.name)
    return {"passed": True, "checks": reports, "model_calls": 0}


def watchdog_control(root: Path, inputs: dict) -> dict:
    root.mkdir(mode=0o700)
    atomic_save(root / "inputs.json", inputs)
    parent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    server = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    parent_id, server_id = process_identity(parent.pid), process_identity(server.pid)
    cid = None
    try:
        cid = subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "--pull=never",
                "--network=none",
                "--memory=64m",
                "--cpus=1",
                "--pids-limit=16",
                "--label",
                f"padawan.atlas.owner={root.name}",
                inputs["config"]["compiler_policy"]["judge_image"],
                "sleep",
                "60",
            ],
            text=True,
        ).strip()
        state = {
            "phase": "starting_server",
            "launched_epoch": time.time(),
            "hard_deadline_epoch": time.time() + 120,
            "processes": {"supervisor": parent_id, "server": server_id},
        }
        atomic_save(root / "host-preflight.json", host_snapshot(root, state))
        atomic_save(root / "state.json", state)
        atomic_save(root / "supervisor-heartbeat.json", {"at": now()})
        stop_owned(parent_id)
        parent.wait(timeout=5)
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("launch_atlas_local.py")),
                "watchdog",
                "--root",
                str(root),
            ],
            check=True,
            timeout=40,
        )
        server.wait(timeout=5)
        assert "supervisor_disappeared" in load(root / "stop.json")["reasons"]
        assert load(root / "watchdog-cleanup.json")["passed"]
        assert not owned_group_members(server_id) and not container_inventory(root.name)
        return {
            "passed": True,
            "supervisor_loss_detected": True,
            "owned_process_removed": True,
            "owned_cpu_container_removed": bool(cid),
        }
    finally:
        stop_owned(parent_id)
        stop_owned(server_id)
        parent.wait(timeout=5)
        server.wait(timeout=5)
        cleanup_containers(root.name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    os.umask(0o077)
    inputs = load(root / "inputs.json")
    controls = root / "validation"
    controls.mkdir(mode=0o700)
    cpu = cpu_controls(controls / f"{root.name}-cpu", inputs)
    atomic_save(controls / "cpu.json", cpu)
    watchdog = watchdog_control(controls / f"{root.name}-watchdog", inputs)
    atomic_save(controls / "watchdog.json", watchdog)
    native = asyncio.run(native_control(controls / f"{root.name}-native", inputs))
    atomic_save(controls / "native.json", native)
    report = {
        "passed": True,
        "at": now(),
        "inputs_digest": sha256_digest(inputs),
        "cpu": cpu,
        "watchdog": watchdog,
        "native": native,
        "model_calls": 0,
    }
    atomic_save(root / "local-validation.json", report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
