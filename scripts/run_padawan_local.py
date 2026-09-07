"""Prepare and run a short native Padawan loop with local Nemotron and authored feedback.

The runtime inventory is inherited from an explicit local manifest and verified again.
No previous campaign tasks, results, permissions, or pending calls are resumed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from padawan.adapters.openai_compatible.local_developmental import LocalDevelopmentalClient
from padawan.artifacts.store import ArtifactCatalog, LocalArtifactStore
from padawan.corpus.registry import CorpusRegistry
from padawan.domains.developmental.workflow import DomainDevelopmentalWorkflowHandler
from padawan.domains.graduate_algebra import (
    COMPETENCY_ID,
    DOMAIN_ID,
    STUDENT_CONTEXT_TOKENS,
    STUDENT_OUTPUT_TOKENS,
    STUDENT_TIMEOUT_SECONDS,
    GraduateAlgebraAuthority,
    competency,
    corpus_items,
)
from padawan.domains.graduate_algebra_suite import SUITE
from padawan.episodes.store import EpisodeStore
from padawan.experiments.controls import (
    ResearchControlRegistry,
    parent_state_identity,
    research_corpus_digest,
)
from padawan.experiments.engine import ExperimentEngine
from padawan.experiments.local_developmental import local_developmental_control
from padawan.memory.lessons import LessonMemory
from padawan.models.contracts import ResearchRole, RunState
from padawan.models.database import Database
from padawan.models.hashing import file_sha256, sha256_digest
from padawan.models.tables import (
    CorpusItemRow,
    EpisodeRow,
    LessonVersionRow,
    RunRow,
    StudentRow,
    StudentStateRow,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor
from padawan.orchestration.local_host import (
    append_event,
    atomic_save,
    clean_environment,
    command,
    health_reasons,
    host_snapshot,
    load,
    now,
    process_identity,
    process_matches,
    stop_owned,
)
from padawan.orchestration.source_identity import source_identity as repository_source_identity
from padawan.orchestration.state_machine import RunStore
from padawan.orchestration.supervisor import AutonomousSupervisor
from padawan.provenance.ledger import ProvenanceLedger
from padawan.state.store import StateStore
from padawan.teaching.authored import AuthoredTeacherClient
from padawan.updates.backends import MemoryConsolidationBackend

REPO = Path(__file__).resolve().parents[1]
FAMILIES = ("nonnormal_galois_base_change",)


def source_identity() -> dict[str, str]:
    return repository_source_identity(REPO)


async def prepare(
    root: Path, runtime_manifest: Path, *, duration_seconds: int = 16200, episode_limit: int = 1
) -> None:
    from padawan.adapters.openai_compatible.local_runtime import runtime_inventory

    if root.exists():
        raise FileExistsError("prepare a fresh pilot directory")
    if not 1 <= duration_seconds <= 16200 or episode_limit not in {1, 2}:
        raise ValueError("pilot bounds are one or two episodes and at most 16200 seconds")
    parent = load(runtime_manifest)
    old = parent["config"]
    config = {
        key: old[key]
        for key in (
            "model_id",
            "model_path",
            "lab_path",
            "runtime_args",
            "vllm_metal_revision",
            "mlx_lm_revision",
            "runtime_packages",
        )
    }
    runtime_args = list(config["runtime_args"])
    # Same think-tag syntax, without Nemotron's fallback that can promote a
    # reasoning-only response to public content when thinking is disabled.
    runtime_args[runtime_args.index("--reasoning-parser") + 1] = "qwen3"
    runtime_args[runtime_args.index("--max-model-len") + 1] = str(STUDENT_CONTEXT_TOKENS)
    config.update(
        runtime_args=runtime_args,
        port=18768,
        duration_seconds=duration_seconds,
        episode_limit=episode_limit,
        rounds=episode_limit,
        families=list(FAMILIES),
        corpus_seed=7092026,
        assignment_seed=17092026,
        domain_id=DOMAIN_ID,
        suite_digest=sha256_digest(SUITE),
        external_spend_usd=0,
        student_output_tokens_per_call=STUDENT_OUTPUT_TOKENS,
        student_context_window_tokens=STUDENT_CONTEXT_TOKENS,
        student_timeout_seconds=STUDENT_TIMEOUT_SECONDS,
        student_calls_maximum=4 * episode_limit,
        automatic_call_retries=0,
        teacher="authored_in_current_codex_session",
    )
    runtime = runtime_inventory(config)
    capsule = load(
        Path(config["lab_path"]) / "artifacts/reviewed/phase1a-mlx-reference/capsule.json"
    )
    inventory = []
    for entry in capsule["model"]["files"]:
        path = Path(config["model_path"]) / entry["path"]
        if path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
            raise ValueError("cached model differs from the reviewed capsule")
        inventory.append({k: entry[k] for k in ("path", "bytes", "sha256")})
    root.mkdir(parents=True, mode=0o700)
    inputs = {
        "prepared_at": now(),
        "config": config,
        "runtime": runtime,
        "model_inventory": inventory,
        "sources": source_identity(),
        "runtime_manifest_source": str(runtime_manifest.resolve()),
        "teacher_api_calls_authorized": False,
    }
    atomic_save(root / "inputs.json", inputs)
    atomic_save(root / "suite.json", SUITE)
    database = Database.sqlite(root / "pilot.sqlite3")
    await database.create_schema()
    registry, states = CorpusRegistry(), StateStore()
    items = corpus_items(SUITE)
    async with database.transaction() as session:
        await registry.register_competency(session, competency())
        await registry.register_items(session, items)
        await states.create_student(
            session,
            student_id=root.name,
            checkpoint_id=config["model_id"],
            runtime_id="vllm-metal-local",
            research_role=ResearchRole.TARGET,
            initial_working_state={},
        )
        rows = list((await session.scalars(select(CorpusItemRow))).all())
        control, _ = local_developmental_control(inputs, corpus_digest=research_corpus_digest(rows))
        await ResearchControlRegistry().register_profile(session, control.profile)
    await database.close()
    atomic_save(
        root / "preparation.json",
        {
            "inputs_digest": sha256_digest(inputs),
            "model_files_rehashed": len(inventory),
            "corpus_items": len(items),
            "matched_groups": len(items) // 3,
            "native_research_controls_registered": True,
            "model_launched": False,
        },
    )
    print(json.dumps(load(root / "preparation.json")))


async def worker(root: Path) -> int:
    inputs = load(root / "inputs.json")
    if source_identity() != inputs["sources"]:
        raise ValueError("pilot source changed after preparation")
    config = inputs["config"]
    database = Database.sqlite(root / "pilot.sqlite3")
    artifacts = LocalArtifactStore(root / "artifacts")
    registry, states, runs, memory = CorpusRegistry(), StateStore(), RunStore(), LessonMemory()
    student = LocalDevelopmentalClient(
        base_url=f"http://127.0.0.1:{config['port']}",
        model=config["model_id"],
        timeout_seconds=config["student_timeout_seconds"],
    )
    teacher = AuthoredTeacherClient(root / "teaching", timeout_seconds=300)
    controls = ResearchControlRegistry()
    async with database.transaction() as session:
        if await session.scalar(select(RunRow.run_id).limit(1)) is not None:
            raise ValueError("pilot worker cannot automatically resume prior model effects")
        rows = list((await session.scalars(select(CorpusItemRow))).all())
        control, research_worker = local_developmental_control(
            inputs, corpus_digest=research_corpus_digest(rows)
        )
    handler = DomainDevelopmentalWorkflowHandler(
        authority=GraduateAlgebraAuthority(root / "proof-reviews"),
        database=database,
        artifacts=artifacts,
        registry=registry,
        states=states,
        episodes=EpisodeStore(ArtifactCatalog(artifacts)),
        experiments=ExperimentEngine(states),
        provenance=ProvenanceLedger(
            code_revision=sha256_digest(inputs["sources"]), environment="local-pilot"
        ),
        student_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=student
        ),
        teacher_calls=IdempotentGenerationExecutor(
            database=database, artifacts=artifacts, client=teacher
        ),
        teacher_provider=teacher.provider,
        memory=memory,
        memory_backend=MemoryConsolidationBackend(memory),
        student_runtime_id="vllm-metal-local",
        student_runtime_version=inputs["runtime"]["head"],
        student_checkpoint_id=config["model_id"],
        student_role=ResearchRole.TARGET,
    )
    supervisor = AutonomousSupervisor(
        database=database,
        runs=runs,
        handler=handler,
        worker_id=f"{root.name}-worker",
        idle_poll_seconds=0,
        research_worker=research_worker,
    )
    summaries = []
    try:
        for index, family in enumerate(config["families"] * config["rounds"]):
            if index >= config["episode_limit"] or (root / "stop.json").exists():
                break
            run_id = f"{root.name}-{index + 1:02d}"
            seed = config["assignment_seed"] + index
            async with database.transaction() as session:
                learner = await session.get(StudentRow, root.name)
                assert learner is not None
                state = await session.get(StudentStateRow, learner.canonical_state_id)
                assert state is not None
                manifest = control.execution_manifest(
                    execution_id=f"execution-{run_id}",
                    seed=seed,
                    created_at=datetime.now(UTC),
                    parent_state=parent_state_identity(state),
                )
                execution = await controls.register_execution(
                    session, manifest, parent_state_id=state.state_id
                )
                await runs.create(
                    session,
                    run_id=run_id,
                    retry_budget=0,
                    research_execution_digest=execution.execution_digest,
                    payload={
                        "student_id": root.name,
                        "state_id": state.state_id,
                        "domain_id": DOMAIN_ID,
                        "pool": "curriculum",
                        "competency_id": COMPETENCY_ID,
                        "experiment_seed": seed,
                        "teacher_mode": "diagnostic_critique",
                        "teacher_retry_attempts": 2,
                        "treatment_condition": "authored_evidence_critique",
                        "control_condition": "no_intervention",
                    },
                )
            await supervisor.run(budget=256)
            async with database.transaction() as session:
                run = await session.get(RunRow, run_id)
                assert run is not None
                episode = await session.get(EpisodeRow, f"episode-{run_id}")
                summary = {
                    "run_id": run_id,
                    "family": family,
                    "round": index // len(FAMILIES) + 1,
                    "state": run.state,
                    "last_error": run.last_error,
                    "episode": episode.record_json if episode else None,
                }
                summaries.append(summary)
                atomic_save(root / f"episode-{index + 1:02d}.json", summary)
                lessons = list((await session.scalars(select(LessonVersionRow))).all())
                atomic_save(
                    root / "results.json",
                    {
                        "at": now(),
                        "episodes": summaries,
                        "lesson_versions": len(lessons),
                        "parameter_updates": False,
                        "statistical_claim_permitted": False,
                        "limitations": "At most two blocks; same teacher/grader; proofs reviewed.",
                    },
                )
                print(
                    json.dumps(
                        {
                            "episode": index + 1,
                            "family": family,
                            "state": run.state,
                            "metrics": (episode.record_json if episode else {}).get(
                                "pedagogical_metrics"
                            ),
                        }
                    )
                )
                if run.state != RunState.COMPLETE.value:
                    return 1
        return 0 if len(summaries) == config["episode_limit"] else 1
    finally:
        await student.close()
        await database.close()


def start_child(root: Path, name: str, argv: list[str], env: dict[str, str]):
    with (root / f"{name}.log").open("ab") as stream:
        return subprocess.Popen(
            argv,
            cwd=REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )


def guard(root: Path) -> None:
    state = load(root / "host-state.json")
    baseline = load(root / "host-preflight.json")
    while True:
        state = load(root / "host-state.json")
        if state["phase"] == "finished":
            return
        reasons = []
        if not process_matches(state["processes"]["supervisor"]):
            reasons.append("supervisor_disappeared")
        if time.time() >= state["deadline"]:
            reasons.append("pilot_runtime_deadline")
        if (root / "stop.json").exists():
            reasons.append("operator_stop")
        try:
            snapshot = host_snapshot(root, state)
            append_event(root / "host.jsonl", snapshot)
            hard, warnings = health_reasons(snapshot, baseline)
            reasons.extend(hard + warnings)
        except Exception:
            reasons.append("host_observation_unavailable")
        if reasons:
            atomic_save(root / "stop.json", {"at": now(), "reasons": reasons})
            for name in ("worker", "server", "caffeinate"):
                stop_owned(state["processes"].get(name))
            return
        time.sleep(5)


def run(root: Path) -> int:
    from launch_atlas_local import http_ready

    inputs = load(root / "inputs.json")
    if source_identity() != inputs["sources"]:
        raise ValueError("source changed after preparation")
    validation = load(root / "offline-validation.json")
    if not validation.get("passed") or validation["inputs_digest"] != sha256_digest(inputs):
        raise ValueError("pilot needs passing validation for its exact inputs")
    if (root / "host-state.json").exists():
        raise FileExistsError("runtime was already launched for this pilot")
    config, env = inputs["config"], clean_environment(REPO, root)
    state = {
        "phase": "starting",
        "launched_epoch": time.time(),
        "deadline": time.time() + config["duration_seconds"],
        "processes": {"supervisor": process_identity(os.getpid())},
    }
    baseline = host_snapshot(root, state)
    hard, warnings = health_reasons(baseline, baseline)
    if (
        hard
        or warnings
        or baseline["memory_free_percent"] < 70
        or baseline["swap_bytes"] > 4 * 1024**2
    ):
        raise RuntimeError("local host is not ready for the bounded pilot")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", config["port"]))
    atomic_save(root / "host-preflight.json", baseline)
    atomic_save(root / "host-state.json", state)
    processes = {}
    try:
        for name, argv, child_env in (
            ("watchdog", [sys.executable, __file__, "guard", "--root", str(root)], env),
            ("caffeinate", ["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())], env),
            (
                "server",
                [
                    inputs["runtime"]["python"],
                    "-m",
                    "vllm.entrypoints.cli.main",
                    "serve",
                    config["model_path"],
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(config["port"]),
                    "--served-model-name",
                    config["model_id"],
                    *config["runtime_args"],
                ],
                {
                    **env,
                    "PYTHONPATH": inputs["runtime"]["upstream"]
                    + os.pathsep
                    + config["lab_path"]
                    + "/src",
                },
            ),
        ):
            if (root / "stop.json").exists():
                raise RuntimeError("host guard stopped before launch")
            process = start_child(root, name, argv, child_env)
            processes[name] = process
            state["processes"][name] = process_identity(process.pid)
            atomic_save(root / "host-state.json", state)
        started = time.monotonic()
        while not http_ready(config["port"]):
            if (
                processes["server"].poll() is not None
                or processes["watchdog"].poll() is not None
                or (root / "stop.json").exists()
                or time.time() >= state["deadline"]
            ):
                raise RuntimeError("local server startup failed or was stopped")
            if time.monotonic() - started > 180:
                raise TimeoutError("local server startup deadline")
            time.sleep(1)
        listeners = command(
            ["/usr/sbin/lsof", "-n", "-P", f"-iTCP:{config['port']}", "-sTCP:LISTEN", "-Fp"]
        )
        pids = {int(line[1:]) for line in listeners.splitlines() if line.startswith("p")}
        listener_owned = bool(pids) and all(
            os.getpgid(pid) == state["processes"]["server"]["pgid"] for pid in pids
        )
        if not listener_owned:
            raise PermissionError("loopback listener is not owned by the pilot server")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{config['port']}/v1/models", timeout=5) as response:
            models = json.load(response)
        log = (root / "server.log").read_text(errors="replace")
        checks = {
            "listener_owned": listener_owned,
            "served_model_matches": {m["id"] for m in models["data"]} == {config["model_id"]},
            "context_window_matches": all(
                m.get("max_model_len") == config["student_context_window_tokens"]
                for m in models["data"]
            ),
            "metal_platform": "Platform plugin metal is activated" in log,
            "mlx_gpu": "MLX device set to: Device(gpu, 0)" in log,
            "wired_override_disabled": "Metal wired-limit override disabled" in log,
            "watchdog_alive": processes["watchdog"].poll() is None,
        }
        atomic_save(
            root / "server-readiness.json",
            {"at": now(), "passed": all(checks.values()), "checks": checks},
        )
        if not all(checks.values()):
            raise RuntimeError("local server identity or watchdog readiness failed")
        process = start_child(
            root, "worker", [sys.executable, __file__, "worker", "--root", str(root)], env
        )
        processes["worker"] = process
        state["processes"]["worker"] = process_identity(process.pid)
        state["phase"] = "running"
        atomic_save(root / "host-state.json", state)
        while process.poll() is None:
            if (
                processes["watchdog"].poll() is not None or processes["server"].poll() is not None
            ) and not (root / "stop.json").exists():
                atomic_save(
                    root / "stop.json",
                    {"at": now(), "reason": "watchdog_or_server_exited"},
                )
            if (root / "stop.json").exists() or time.time() >= state["deadline"]:
                stop_owned(state["processes"]["worker"])
                return 1
            time.sleep(1)
        return int(process.returncode)
    finally:
        cleanup = {}
        for name in ("worker", "server", "caffeinate", "watchdog"):
            cleanup[name] = stop_owned(state["processes"].get(name))
            if name in processes:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    processes[name].wait(timeout=2)
        state["phase"] = "finished"
        state["finished_at"] = now()
        atomic_save(root / "host-state.json", state)
        atomic_save(
            root / "cleanup.json",
            {"at": now(), "passed": all(cleanup.values()), "processes": cleanup},
        )
        atomic_save(root / "host-postflight.json", host_snapshot(root, state))


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "guard", "worker"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--duration-seconds", type=int, default=16200)
    parser.add_argument("--episode-limit", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "prepare":
        if not args.runtime_manifest:
            parser.error("prepare requires --runtime-manifest")
        asyncio.run(
            prepare(
                root,
                args.runtime_manifest,
                duration_seconds=args.duration_seconds,
                episode_limit=args.episode_limit,
            )
        )
    elif args.mode == "worker":
        raise SystemExit(asyncio.run(worker(root)))
    elif args.mode == "guard":
        guard(root)
    else:
        signal.signal(
            signal.SIGTERM,
            lambda *_: atomic_save(root / "stop.json", {"at": now(), "reason": "signal"}),
        )
        signal.signal(
            signal.SIGINT,
            lambda *_: atomic_save(root / "stop.json", {"at": now(), "reason": "signal"}),
        )
        raise SystemExit(run(root))


if __name__ == "__main__":
    main()
