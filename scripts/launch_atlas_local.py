"""Detached six-hour local supervisor and independent cleanup watchdog.

No scheduling service or cloud resource is created. The watchdog survives the supervisor,
and every process group/container is scoped to this run's retained ownership record.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from padawan.models.hashing import sha256_digest
from padawan.orchestration.local_host import (
    GIB,
    append_event,
    atomic_save,
    clean_environment,
    cleanup_owned,
    command,
    health_reasons,
    host_snapshot,
    load,
    now,
    process_identity,
    process_matches,
    stop_owned,
)
from padawan.orchestration.source_identity import source_identity

REPO = Path(__file__).resolve().parents[1]


def storage_usage(root: Path) -> dict[str, int]:
    retained = scratch = files = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                size = path.stat().st_size
                files += 1
                if path.relative_to(root).parts[0] == "scratch":
                    scratch += size
                else:
                    retained += size
        except FileNotFoundError:
            continue
    return {"retained_bytes": retained, "scratch_bytes": scratch, "files": files}


def stop_request(root: Path, reason: str, **details) -> None:
    if not (root / "stop.json").exists():
        atomic_save(root / "stop.json", {"at": now(), "reason": reason, **details})


def watchdog(root: Path) -> None:
    inputs = load(root / "inputs.json")
    config = inputs["config"]
    baseline = load(root / "host-preflight.json")
    warning_since: float | None = None
    snapshot_failure_since: float | None = None
    while True:
        state = load(root / "state.json")
        if state["phase"] in {"finished", "failed", "cleanup_failed"}:
            return
        reasons = []
        parent = state["processes"]["supervisor"]
        if not process_matches(parent):
            reasons.append("supervisor_disappeared")
        heartbeat = root / "supervisor-heartbeat.json"
        if (
            not heartbeat.exists()
            or time.time() - datetime.fromisoformat(load(heartbeat)["at"]).timestamp()
            > config["heartbeat_timeout_seconds"]
        ):
            reasons.append("supervisor_heartbeat_expired")
        if state["phase"] != "cleaning" and time.time() >= state["hard_deadline_epoch"]:
            reasons.append("six_hour_deadline")
        if state["phase"] == "cleaning" and time.time() >= state["hard_deadline_epoch"] + 90:
            reasons.append("cleanup_grace_expired")
        if state["phase"] == "running":
            worker = state["processes"].get("worker")
            hb = root / "worker-heartbeat.json"
            if (
                time.time() - state.get("worker_enrolled_epoch", 0) > 30
                and worker
                and process_matches(worker)
                and (
                    not hb.exists()
                    or time.time() - datetime.fromisoformat(load(hb)["at"]).timestamp()
                    > config["heartbeat_timeout_seconds"]
                    or load(hb)["pid"] != worker["pid"]
                )
            ):
                reasons.append("controller_heartbeat_expired")
            event_file = root / "events.jsonl"
            if (
                event_file.exists()
                and time.time() - event_file.stat().st_mtime > config["progress_timeout_seconds"]
            ):
                reasons.append("controller_progress_stalled")
        try:
            snapshot = host_snapshot(root, state)
            snapshot.update(storage_usage(root))
            append_event(root / "host.jsonl", snapshot)
            hard, warnings = health_reasons(snapshot, baseline)
            reasons.extend(hard)
            if warnings:
                warning_since = warning_since or time.monotonic()
                if time.monotonic() - warning_since >= config["health_warning_grace_seconds"]:
                    reasons.extend(warnings)
            else:
                warning_since = None
            if snapshot["retained_bytes"] > config["maximum_retained_bytes"]:
                reasons.append("retained_storage_limit")
            if snapshot["scratch_bytes"] > config["maximum_scratch_bytes"]:
                reasons.append("scratch_storage_limit")
            if snapshot["files"] > config["maximum_files"]:
                reasons.append("file_count_limit")
            snapshot_failure_since = None
        except Exception as error:
            warnings = ["host_observation_unavailable"]
            snapshot_failure_since = snapshot_failure_since or time.monotonic()
            if time.monotonic() - snapshot_failure_since >= 30:
                reasons.append("host_observation_expired")
            append_event(
                root / "events.jsonl",
                {"event": "host_observation_error", "type": type(error).__name__},
            )
        atomic_save(
            root / "guard.json",
            {"at": now(), "healthy": not reasons, "reasons": reasons, "warnings": warnings},
        )
        if reasons:
            stop_request(root, "watchdog_stop", reasons=reasons)
            append_event(root / "events.jsonl", {"event": "watchdog_stop", "reasons": reasons})
            cleanup = cleanup_owned(root, state)
            atomic_save(root / "watchdog-cleanup.json", cleanup)
            return
        time.sleep(5)


def http_ready(port: int) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def start_child(argv: list[str], log: Path, environment: dict[str, str]) -> subprocess.Popen:
    with log.open("ab") as stream:
        return subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            cwd=REPO,
            env=environment,
            start_new_session=True,
        )


def supervisor(root: Path) -> int:
    os.umask(0o077)
    inputs = load(root / "inputs.json")
    config = inputs["config"]
    if (
        source_identity(REPO) != inputs["sources"]
        or sha256_digest(load(root / "configuration.json")) != inputs["config_digest"]
    ):
        raise ValueError("local launch differs from frozen source or configuration")
    validation = load(root / "local-validation.json")
    if not validation.get("passed") or validation.get("inputs_digest") != sha256_digest(inputs):
        raise ValueError("local launch requires completed offline and CPU control validation")
    if (root / "state.json").exists():
        raise FileExistsError("this run already has a supervisor record")
    environment = clean_environment(REPO, root)
    started = time.time()
    state: dict[str, Any] = {
        "phase": "preflight",
        "launched_at": now(),
        "launched_epoch": started,
        "hard_deadline_epoch": started + config["duration_seconds"],
        "dispatch_deadline_epoch": started
        + config["duration_seconds"]
        - config["stop_new_episodes_seconds_before_end"],
        "config_digest": inputs["config_digest"],
        "controller_restarts": 0,
        "processes": {"supervisor": process_identity(os.getpid())},
    }
    baseline = host_snapshot(root, state)
    physical = int(command(["/usr/sbin/sysctl", "-n", "hw.memsize"]))
    if (
        physical < 64 * GIB
        or baseline["swap_bytes"] > config["maximum_initial_swap_bytes"]
        or baseline["memory_free_percent"] < 50
        or not baseline["ac_power"]
        or not baseline["thermal_normal"]
        or baseline["competing_model_pids"]
        or baseline["new_panic_reports"]
        or baseline["disk_free_bytes"] < 50 * GIB
    ):
        atomic_save(root / "host-preflight-failure.json", baseline)
        state.update(phase="failed", stop_reason="host_preflight_failed", finished_at=now())
        atomic_save(root / "state.json", state)
        cleanup_owned(root, state)
        from padawan.atlas.local_campaign import write_report

        write_report(root)
        raise RuntimeError("fresh local host preflight did not pass")
    atomic_save(root / "host-preflight.json", baseline)
    atomic_save(root / "state.json", state)
    atomic_save(root / "supervisor-heartbeat.json", {"at": now(), "pid": os.getpid()})
    append_event(
        root / "events.jsonl",
        {"event": "supervisor_started", "hard_deadline_epoch": state["hard_deadline_epoch"]},
    )
    server = worker = guard = awake = None

    def interrupted(signum, frame):
        stop_request(root, "supervisor_signal", signal=signum)

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)

    def publish() -> None:
        atomic_save(root / "state.json", state)
        atomic_save(root / "supervisor-heartbeat.json", {"at": now(), "pid": os.getpid()})

    def healthy() -> bool:
        if (root / "stop.json").exists():
            return False
        path = root / "guard.json"
        return (
            path.exists()
            and load(path)["healthy"]
            and time.time() - datetime.fromisoformat(load(path)["at"]).timestamp() < 30
        )

    try:
        guard = start_child(
            [
                sys.executable,
                str(REPO / "scripts/launch_atlas_local.py"),
                "watchdog",
                "--root",
                str(root),
            ],
            root / "watchdog.log",
            environment,
        )
        state["processes"]["watchdog"] = process_identity(guard.pid)
        publish()
        for _ in range(30):
            publish()
            if healthy():
                break
            if guard.poll() is not None or (root / "stop.json").exists():
                raise RuntimeError("independent watchdog did not become healthy")
            time.sleep(1)
        if not healthy():
            raise RuntimeError("independent watchdog startup deadline")
        awake = start_child(
            ["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
            root / "caffeinate.log",
            environment,
        )
        state["processes"]["caffeinate"] = process_identity(awake.pid)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", config["port"]))
        server_environment = {
            **environment,
            "PYTHONPATH": inputs["runtime"]["upstream"]
            + os.pathsep
            + str(Path(config["lab_path"]) / "src"),
        }
        server_command = [
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
        ]
        atomic_save(
            root / "server-launch.json",
            {"command": server_command, "environment": server_environment, "at": now()},
        )
        server = start_child(server_command, root / "server.log", server_environment)
        state["processes"]["server"] = process_identity(server.pid)
        state["phase"] = "starting_server"
        publish()
        until = time.monotonic() + config["startup_timeout_seconds"]
        while not http_ready(config["port"]):
            publish()
            if not healthy() or server.poll() is not None or time.monotonic() >= until:
                raise RuntimeError("owned local server did not pass startup")
            time.sleep(2)
        listeners = command(
            ["/usr/sbin/lsof", "-n", "-P", f"-iTCP:{config['port']}", "-sTCP:LISTEN", "-Fp"]
        )
        pids = {int(line[1:]) for line in listeners.splitlines() if line.startswith("p")}
        if not pids or any(os.getpgid(pid) != state["processes"]["server"]["pgid"] for pid in pids):
            raise PermissionError("loopback listener ownership differs from the launched server")
        log = (root / "server.log").read_text(errors="replace")
        checks = {
            "metal_platform": "Platform plugin metal is activated" in log,
            "mlx_gpu": "MLX device set to: Device(gpu, 0)" in log,
            "no_cpu_fallback": "CpuPlatform" not in log,
            "wired_override_disabled": "Metal wired-limit override disabled" in log,
            "listener_owned": True,
        }
        atomic_save(
            root / "server-readiness.json",
            {
                "passed": all(checks.values()),
                "at": now(),
                "checks": checks,
                "listener_pids": sorted(pids),
            },
        )
        if not all(checks.values()):
            raise RuntimeError("local server identity or Metal configuration was not observed")
        append_event(root / "events.jsonl", {"event": "server_ready", "pid": server.pid})
        worker_argv = [
            sys.executable,
            str(REPO / "scripts/run_atlas_local.py"),
            "--root",
            str(root),
        ]
        if not healthy():
            raise RuntimeError("watchdog stopped before controller startup")
        worker = start_child(worker_argv, root / "controller.log", environment)
        state["processes"]["worker"] = process_identity(worker.pid)
        state.update(phase="running", worker_enrolled_epoch=time.time())
        publish()
        while True:
            publish()
            if not healthy():
                state["stop_reason"] = "watchdog_or_operator_stop"
                break
            if server.poll() is not None:
                state["stop_reason"] = "server_exited"
                stop_request(root, "server_exited", exit_code=server.returncode)
                break
            if time.time() >= state["hard_deadline_epoch"]:
                state["stop_reason"] = "six_hour_deadline"
                stop_request(root, "six_hour_deadline")
                break
            code = worker.poll()
            if code is not None:
                if (
                    code == 75
                    and state["controller_restarts"] < config["maximum_controller_restarts"]
                ):
                    if (
                        not (root / "progress.json").exists()
                        or not load(root / "progress.json")["completed"]
                    ):
                        raise RuntimeError(
                            "controller requested restart without a committed checkpoint"
                        )
                    state["controller_restarts"] += 1
                    append_event(
                        root / "events.jsonl",
                        {"event": "controller_restarting", "server_pid_unchanged": server.pid},
                    )
                    if not healthy():
                        raise RuntimeError("watchdog stopped before controller restart")
                    worker = start_child(
                        [*worker_argv, "--resume"], root / "controller.log", environment
                    )
                    state["processes"]["worker"] = process_identity(worker.pid)
                    state["worker_enrolled_epoch"] = time.time()
                    publish()
                    continue
                state["stop_reason"] = "controller_finished" if code == 0 else "controller_failed"
                state["controller_exit_code"] = code
                if code:
                    stop_request(root, "controller_failed", exit_code=code)
                break
            time.sleep(2)
    except Exception as error:
        state["stop_reason"] = type(error).__name__
        state["error"] = str(error)
        stop_request(root, "supervisor_failure", error_type=type(error).__name__)
    finally:
        state["phase"] = "cleaning"
        publish()
        cleanup = cleanup_owned(root, state)
        for process in (worker, server, awake):
            if process is not None:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
        cleanup = cleanup_owned(root, state)
        with contextlib.suppress(Exception):
            atomic_save(root / "host-postflight.json", host_snapshot(root, state))
        state["finished_at"] = now()
        state["phase"] = "finished" if state.get("controller_exit_code") == 0 else "failed"
        if not cleanup["passed"]:
            state["phase"] = "cleanup_failed"
        publish()
        if guard is not None:
            with contextlib.suppress(subprocess.TimeoutExpired):
                guard.wait(timeout=10)
            if guard.poll() is None:
                stop_owned(state["processes"].get("watchdog"))
                guard.wait(timeout=10)
        subprocess.run(
            [
                sys.executable,
                str(REPO / "scripts/run_atlas_local.py"),
                "--root",
                str(root),
                "--report-only",
            ],
            cwd=REPO,
            env=environment,
            check=False,
            timeout=45,
        )
        append_event(
            root / "events.jsonl",
            {
                "event": "local_run_closed",
                "phase": state["phase"],
                "cleanup_passed": cleanup["passed"],
            },
        )
    return 0 if state["phase"] == "finished" else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("start", "supervisor", "watchdog"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    os.umask(0o077)
    if args.mode == "watchdog":
        watchdog(root)
    elif args.mode == "supervisor":
        raise SystemExit(supervisor(root))
    else:
        if (root / "state.json").exists():
            raise FileExistsError("this run already has an execution record")
        if source_identity(REPO) != load(root / "inputs.json")["sources"]:
            raise ValueError("source changed after preparation")
        process = start_child(
            [sys.executable, str(Path(__file__).resolve()), "supervisor", "--root", str(root)],
            root / "supervisor.log",
            clean_environment(REPO, root),
        )
        atomic_save(
            root / "dispatch.json", {"at": now(), "supervisor": process_identity(process.pid)}
        )
        print(json.dumps({"launched": True, "supervisor_pid": process.pid, "root": str(root)}))


if __name__ == "__main__":
    main()
