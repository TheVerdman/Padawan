"""Run one explicitly authorized, frozen Vertex campaign and always request owned cleanup."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from control_atlas_vertex import (
    Control,
    GuardAdmission,
    atomic_save,
    digest,
    now,
    transient_cloud_error,
)
from run_atlas_coding import code_identity

from padawan.atlas.coding_manifests import validate_prepared_problem_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--gcloud", required=True)
    args = parser.parse_args()
    inputs = json.loads(args.inputs.read_text())
    validate_prepared_problem_lock(inputs)
    root = args.root.resolve()
    if code_identity() != inputs["sources"]:
        raise ValueError("prepared source identity differs before any cloud action")
    control = Control(inputs["config_file"], root, args.gcloud)
    if control.config != inputs["launch"]:
        raise ValueError("prepared launch configuration differs before any cloud action")
    readiness = json.loads((root / "ready-to-deploy.json").read_text())
    if readiness.get("inputs_sha256") != digest(args.inputs):
        raise ValueError("deployment validation refers to a different input freeze")
    finished = threading.Event()
    heartbeat_thread = None
    owns_attempt = False

    def heartbeat():
        while not finished.is_set():
            atomic_save(root / "runner-heartbeat.json", {"at": now().isoformat()})
            finished.wait(5)

    def run(command, *, timeout):
        process = subprocess.Popen(command)
        try:
            code = process.wait(timeout=timeout)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        if code:
            raise RuntimeError(
                f"campaign stage exited with status {code}; retained evidence remains"
            )

    try:
        if control.state():
            raise ValueError(
                "campaign launch already attempted; inspect original state without relaunch"
            )
        owns_attempt = True
        control.create()
        control.deploy()
        admission = GuardAdmission(control)
        dispatch_deadline = datetime.fromisoformat(control.state()["dispatch_deadline"])
        while True:
            admission.wait(dispatch_deadline)
            try:
                control.status()
            except Exception as error:
                if not transient_cloud_error(error):
                    raise
                control.log("deployment_status_read_retry", error_type=type(error).__name__)
                time.sleep(min(20, max(0, (dispatch_deadline - now()).total_seconds())))
                continue
            latest = json.loads((root / "latest-status.json").read_text())
            operation = latest.get("operation") or {}
            if operation.get("error"):
                raise RuntimeError("deployment operation failed; GPU campaign will not be admitted")
            if operation.get("done"):
                break
            time.sleep(20)
        current = control.state()
        if not current.get("deployment_ready_at"):
            control.update(
                deployment_ready_at=now().isoformat(), deployment_terminal_operation=operation
            )
        endpoint = latest["endpoint"]
        dns = endpoint["dedicatedEndpointDns"].rstrip("/")
        if not dns.startswith("https://"):
            dns = "https://" + dns
        destination = f"{dns}/v1/{control.plan['endpoint_resource']}:rawPredict"
        atomic_save(root / "runner-heartbeat.json", {"at": now().isoformat()})
        control.update(
            controller_pid=os.getpid(),
            controller_started_at=now().isoformat(),
            controller_stage="serving_preflight",
        )
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        admission.wait(dispatch_deadline)
        control.log("real_preflight_started")
        run(
            [
                sys.executable,
                "scripts/preflight_atlas_vertex.py",
                "--inputs",
                str(args.inputs.resolve()),
                "--launch-plan",
                str(root / "vertex-launch/launch-plan.json"),
                "--authorization-ref",
                control.config["authorization_ref"],
                "--deployment-started-at",
                current["deployment_started_at"],
                "--output",
                str(root / "preflight"),
                "--gcloud",
                args.gcloud,
            ],
            timeout=control.config["request_timeout_seconds"]
            + control.config["judge_deadline_seconds"]
            + 120,
        )
        admission.wait(dispatch_deadline)
        if now() >= datetime.fromisoformat(current["dispatch_deadline"]):
            raise RuntimeError(
                "preflight consumed the fixed dispatch window; no benchmark is admitted"
            )
        control.log("real_preflight_passed")
        run(
            [
                sys.executable,
                "scripts/run_atlas_coding.py",
                "register",
                "--inputs",
                str(args.inputs.resolve()),
                "--run-directory",
                str(root / "campaign"),
                "--raw-predict-url",
                destination,
                "--preflight",
                str(root / "preflight/preflight.json"),
                "--authorization-ref",
                control.config["authorization_ref"],
                "--expires-at",
                current["capture_deadline"],
            ],
            timeout=180,
        )
        control.log(
            "benchmark_dispatch_started", planned_trials=control.config["maximum_trial_requests"]
        )
        run(
            [
                sys.executable,
                "scripts/run_atlas_coding.py",
                "run",
                "--inputs",
                str(args.inputs.resolve()),
                "--run-directory",
                str(root / "campaign"),
                "--raw-predict-url",
                destination,
                "--wave",
                "paired",
                "--gcloud",
                args.gcloud,
                "--control-directory",
                str(root),
            ],
            timeout=max(
                1, (datetime.fromisoformat(current["capture_deadline"]) - now()).total_seconds()
            )
            + 60,
        )
        control.log("benchmark_controller_completed")
    except BaseException as error:
        atomic_save(
            root / "launcher-exit.json",
            {
                "at": now().isoformat(),
                "error_type": type(error).__name__,
                "relaunch_authorized_by_this_receipt": False,
            },
        )
        raise
    finally:
        finished.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=10)
        if owns_attempt and control.state():
            control.update(cleanup_requested=True, controller_finished_at=now().isoformat())
            try:
                control.cleanup()
            except Exception as error:
                control.log(
                    "foreground_cleanup_pending_independent_guard_active",
                    error_type=type(error).__name__,
                )
        control.api.close()


if __name__ == "__main__":
    main()
