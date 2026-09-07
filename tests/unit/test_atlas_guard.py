"""Control-only recovery checks with real files and PIDs; no model or cloud requests."""

import asyncio
import importlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def guard(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    module = importlib.import_module("control_atlas_vertex")
    control = module.Control.__new__(module.Control)
    control.root = tmp_path
    control.config_digest = "sha256:" + "a" * 64
    control.config = {}
    module.atomic_save(
        tmp_path / "cloud-state.json",
        {"config_sha256": control.config_digest, "guard_pid": os.getpid()},
    )

    def heartbeat(*, age=0, cloud_age=0, **changes):
        instant = datetime.now(UTC)
        module.atomic_save(
            tmp_path / "guard-heartbeat.json",
            {
                "at": (instant - timedelta(seconds=age)).isoformat(),
                "pid": os.getpid(),
                "config_sha256": control.config_digest,
                "last_cloud_probe_at": (instant - timedelta(seconds=cloud_age)).isoformat(),
                "healthy": True,
                **changes,
            },
        )

    heartbeat()
    return module, control, heartbeat


def test_short_delays_remain_healthy_and_explicit_pause_recovers(guard):
    module, control, heartbeat = guard
    gate = module.GuardAdmission(control)
    # Both ages would have permanently stopped the previous 20s/75s controller.
    heartbeat(age=30, cloud_age=110)
    assert gate.check()
    heartbeat(healthy=False)
    assert gate.check() is False
    assert control.state()["admission_pause"]
    assert not control.state().get("stop_requested_at")
    heartbeat()
    assert gate.check()
    assert control.state()["admission_pause"] is None
    assert not control.state().get("admission_stop")
    events = [
        json.loads(line)["event"]
        for line in (control.root / "cloud-events.jsonl").read_text().splitlines()
    ]
    assert events == ["dispatch_guard_paused", "dispatch_guard_resumed"]


@pytest.mark.parametrize("field", ["heartbeat", "cloud", "unhealthy", "first_probe"])
def test_degraded_evidence_blocks_then_resumes_without_terminal_stop(guard, field):
    module, control, heartbeat = guard
    values = {
        "heartbeat": {"age": 65},
        "cloud": {"cloud_age": 185},
        "unhealthy": {"healthy": False},
        "first_probe": {"healthy": False, "last_cloud_probe_at": None},
    }
    heartbeat(**values[field])
    gate = module.GuardAdmission(control)
    assert gate.check() is False
    heartbeat()
    assert gate.check()
    assert not (control.root / "admission-stop.json").exists()


@pytest.mark.parametrize(
    "kind", ["state", "heartbeat", "pid", "dead", "stop", "future", "malformed"]
)
def test_authority_loss_or_invalid_evidence_is_terminal_even_after_recovery(guard, kind):
    module, control, heartbeat = guard
    gate = module.GuardAdmission(control)
    if kind == "state":
        module.atomic_save(
            control.root / "cloud-state.json", {"config_sha256": "wrong", "guard_pid": os.getpid()}
        )
    elif kind == "heartbeat":
        heartbeat(config_sha256="wrong")
    elif kind == "pid":
        heartbeat(pid=os.getpid() + 1)
    elif kind == "dead":
        # A reaped owned process provides a real absent PID.
        import subprocess

        child = subprocess.Popen(["/usr/bin/true"])
        child.wait()
        control.update(guard_pid=child.pid)
        heartbeat(pid=child.pid)
    elif kind == "stop":
        control.update(stop_requested_at=datetime.now(UTC).isoformat())
    elif kind == "future":
        heartbeat(age=-30)
    else:
        (control.root / "guard-heartbeat.json").write_text("invalid json")
    # A foreign state cannot be written through the owned control object.
    with pytest.raises(ValueError):
        gate.check()
    if kind != "state":
        original = json.loads((control.root / "admission-stop.json").read_text())
        heartbeat()
        control.update(guard_pid=os.getpid())
        with pytest.raises(module.GuardHealthError):
            gate.check()
        assert json.loads((control.root / "admission-stop.json").read_text()) == original


def test_recovery_window_is_bounded_and_first_stop_receipt_is_recoverable(guard):
    module, control, heartbeat = guard
    gate = module.GuardAdmission(control)
    heartbeat(healthy=False)
    assert gate.check() is False
    gate.paused_since = time.monotonic() - module.GUARD_RECOVERY_SECONDS - 1
    with pytest.raises(module.GuardHealthError) as caught:
        gate.check()
    assert "guard_recovery_exhausted" in caught.value.evidence["reasons"]
    receipt = control.state()["admission_stop"]
    (control.root / "admission-stop.json").unlink()
    module.retain_guard_refusal(control, ValueError("later error"))
    assert json.loads((control.root / "admission-stop.json").read_text()) == receipt


@pytest.mark.asyncio
async def test_waiting_file_operations_resume_once_each_and_never_write_during_pause(guard):
    module, control, heartbeat = guard
    runner = importlib.import_module("run_atlas_coding")
    gate = module.GuardAdmission(control)
    stop = asyncio.Event()
    heartbeat(healthy=False)
    deadline = datetime.now(UTC) + timedelta(seconds=10)

    async def file_operation(index):
        if await runner.await_guard_admission(gate, stop, deadline):
            with (control.root / f"operation-{index}").open("x") as stream:
                stream.write(str(index))
            return index
        return None

    tasks = [asyncio.create_task(file_operation(i)) for i in range(8)]
    await asyncio.sleep(0.05)
    assert not list(control.root.glob("operation-*"))
    assert not any(task.done() for task in tasks)
    heartbeat()
    assert await asyncio.gather(*tasks) == list(range(8))
    assert len(list(control.root.glob("operation-*"))) == 8
    assert not stop.is_set()


@pytest.mark.asyncio
async def test_dispatch_deadline_ends_pause_without_admission(guard):
    module, control, heartbeat = guard
    runner = importlib.import_module("run_atlas_coding")
    heartbeat(healthy=False)
    stop = asyncio.Event()
    assert not await runner.await_guard_admission(
        module.GuardAdmission(control), stop, datetime.now(UTC) - timedelta(seconds=1)
    )
    assert stop.is_set()
    assert control.state()["admission_stop"]["guard_evidence"]["reasons"] == [
        "dispatch_deadline_reached"
    ]


def test_only_transient_cloud_errors_are_eligible_for_bounded_recovery(guard):
    module, _, _ = guard
    assert module.transient_cloud_error(httpx.ReadTimeout("timeout"))
    assert module.transient_cloud_error(module.CredentialRefreshError("refresh unavailable"))
    for status in (429, 500, 502, 503, 504):
        assert module.transient_cloud_error(module.ControlRequestError("GET", status))
    for error in (
        module.ControlRequestError("GET", 401),
        module.ControlRequestError("GET", 403),
        ValueError("ownership mismatch"),
        RuntimeError("unknown error"),
    ):
        assert not module.transient_cloud_error(error)


@pytest.mark.parametrize("recovers", [True, False])
def test_guard_loop_retries_transient_reads_and_enforces_the_recovery_limit(
    guard, monkeypatch, recovers
):
    """Inject only a control-read fault; this fixture does not represent cloud validation."""
    import itertools

    module, control, _ = guard
    start = datetime.now(UTC)
    control.config = {
        "stop_dispatch_after_hours": 1,
        "begin_teardown_after_hours": 2,
        "capture_after_hours": 2.5,
        "maximum_deployment_hours": 3,
        "maximum_startup_minutes": 45,
    }
    control.update(
        deployment_started_at=start.isoformat(),
        deployment_ready_at=start.isoformat(),
        dispatch_deadline=(start + timedelta(hours=1)).isoformat(),
        teardown_at=(start + timedelta(hours=2)).isoformat(),
        capture_deadline=(start + timedelta(hours=2.5)).isoformat(),
        cleanup_deadline=(start + timedelta(hours=3)).isoformat(),
        controller_pid=os.getpid(),
    )
    module.atomic_save(control.root / "runner-heartbeat.json", {"at": start.isoformat()})
    calls = []
    health = []
    cleanups = []

    def read_control_resource(resource):
        calls.append(resource)
        if len(calls) == 1 or not recovers:
            raise httpx.ReadTimeout("controlled transport interruption")
        if len(calls) == 3:
            control.update(cleanup_verified_at=datetime.now(UTC).isoformat())
        return {}

    def cleanup():
        cleanups.append(control.state())
        return True

    ticks = itertools.count(40, 40)
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    monkeypatch.setattr(control, "owned", read_control_resource)
    monkeypatch.setattr(control, "cleanup", cleanup)
    control._guard_loop(lambda last, healthy: health.append((last, healthy)))
    assert health[0][1] is False
    if recovers:
        assert not control.state().get("stop_requested_at")
        assert health[-1][1] is True
        assert not cleanups
    else:
        assert len(calls) >= 2
        assert control.state()["stop_requested_at"]
        assert control.state()["guard_terminal_error"]["recoverable"] is True
        assert len(cleanups) == 1
