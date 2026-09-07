"""Bounded local host observations and cleanup of explicitly owned process groups."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GIB = 1024**3


def now() -> str:
    return datetime.now(UTC).isoformat()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("local control document must be an object")
    return value


def atomic_save(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w") as stream:
        os.chmod(tmp, 0o600)
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def append_event(path: Path, value: dict[str, Any]) -> None:
    with path.open("a") as stream:
        stream.write(json.dumps({"at": now(), **value}, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def clean_environment(repo: Path, root: Path) -> dict[str, str]:
    """Only host/runtime necessities are inherited; provider credentials and proxies are absent."""
    env = {
        key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TMPDIR") if key in os.environ
    }
    env.update(
        PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        LANG="en_US.UTF-8",
        LC_ALL="en_US.UTF-8",
        PYTHONPATH=str(repo) + os.pathsep + str(repo / "scripts"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        VLLM_NO_USAGE_STATS="1",
        DO_NOT_TRACK="1",
        VLLM_ENABLE_RESPONSES_API_STORE="0",
        VLLM_ENABLE_V1_MULTIPROCESSING="0",
        VLLM_METAL_DISABLE_WIRED_LIMIT="1",
        VLLM_METAL_MEMORY_FRACTION="0.50",
        VLLM_METAL_USE_PAGED_ATTENTION="1",
        VLLM_PLUGINS="metal",
        VLLM_CACHE_ROOT=str(root / "server-cache"),
    )
    return env


def command(argv: list[str], *, timeout: float = 10) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"local command failed: {Path(argv[0]).name}, code {result.returncode}")
    return result.stdout.strip()


def process_identity(pid: int) -> dict[str, Any]:
    started = command(["/bin/ps", "-p", str(pid), "-o", "lstart="])
    if not started:
        raise ProcessLookupError(pid)
    return {"pid": pid, "started": started, "pgid": os.getpgid(pid)}


def process_matches(identity: dict[str, Any]) -> bool:
    try:
        if "Z" in command(["/bin/ps", "-p", str(identity["pid"]), "-o", "stat="]):
            return False
        return process_identity(identity["pid"]) == identity
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return False


def owned_group_members(identity: dict[str, Any] | None) -> list[int]:
    if not identity:
        return []
    if identity["pid"] != identity["pgid"] or identity["pgid"] == os.getpgrp():
        raise PermissionError("refusing to terminate a group without exact independent ownership")
    try:
        current = process_identity(identity["pid"])
    except (RuntimeError, ProcessLookupError):
        current = None
    if current is not None and current != identity:
        raise PermissionError("process group leader identity changed")
    # Descendants can outlive their leader. The original group remains owned even
    # when that leader has exited; checking only its PID would leak those children.
    listing = command(["/bin/ps", "-axo", "pid=,pgid=,stat="])
    members = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 3 and int(parts[1]) == identity["pgid"] and "Z" not in parts[2]:
            members.append(int(parts[0]))
    return members


def stop_owned(identity: dict[str, Any] | None) -> bool:
    if not identity or not owned_group_members(identity):
        return True
    for termination in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(identity["pgid"], termination)
        for _ in range(20):
            if not owned_group_members(identity):
                return True
            time.sleep(0.25)
    return not owned_group_members(identity)


def container_inventory(owner: str) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner):
        raise ValueError("invalid container owner label")
    raw = command(
        ["docker", "ps", "-aq", "--filter", f"label=padawan.atlas.owner={owner}"], timeout=10
    )
    ids = raw.splitlines() if raw else []
    if any(not re.fullmatch(r"[a-f0-9]{12,64}", cid) for cid in ids):
        raise ValueError("invalid Docker inventory identity")
    return ids


def cleanup_containers(owner: str) -> dict[str, Any]:
    removed: set[str] = set()
    for _ in range(3):
        ids = container_inventory(owner)
        if ids:
            command(["docker", "rm", "--force", *ids], timeout=20)
            removed.update(ids)
        time.sleep(0.5)
    remaining = container_inventory(owner)
    return {
        "owner": owner,
        "removed": sorted(removed),
        "remaining": remaining,
        "passed": not remaining,
    }


def host_snapshot(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    boot = command(["/usr/sbin/sysctl", "-n", "kern.boottime"])
    swap = command(["/usr/sbin/sysctl", "-n", "vm.swapusage"])
    matched = re.search(r"used\s*=\s*([0-9.]+)([MGT])", swap)
    if matched is None:
        raise ValueError("unknown swap observation")
    swap_bytes = int(float(matched[1]) * {"M": 1024**2, "G": GIB, "T": 1024**4}[matched[2]])
    pressure = command(["/usr/bin/memory_pressure", "-Q"])
    matched = re.search(r"System-wide memory free percentage:\s*(\d+)%", pressure)
    if matched is None:
        raise ValueError("unknown memory-pressure observation")
    free_percent = int(matched[1])
    vm = command(["/usr/bin/vm_stat"])
    pages = re.search(r"page size of (\d+) bytes", vm)
    compressed = re.search(r"Pages occupied by compressor:\s*(\d+)", vm)
    wired = re.search(r"Pages wired down:\s*(\d+)", vm)
    if not all((pages, compressed, wired)):
        raise ValueError("unknown VM observation")
    assert pages and compressed and wired
    thermal = command(["/usr/bin/pmset", "-g", "therm"])
    power = command(["/usr/bin/pmset", "-g", "batt"])
    known_groups = {
        identity["pgid"]
        for name, identity in state.get("processes", {}).items()
        if identity and name in {"server", "worker", "caffeinate", "supervisor", "watchdog"}
    }
    server_group = (state.get("processes", {}).get("server") or {}).get("pgid")
    rss = 0
    competing: list[int] = []
    # Arguments are used for process classification only, never retained or printed.
    listing = command(["/bin/ps", "-axo", "pid=,pgid=,rss=,args="])
    for line in listing.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid, group, memory = map(int, parts[:3])
        if group == server_group:
            rss += memory * 1024
        try:
            argv = shlex.split(parts[3])
        except ValueError:
            continue
        executable = Path(argv[0]).name.lower() if argv else ""
        model_process = (
            (
                executable.startswith("python")
                and len(argv) >= 3
                and argv[1] == "-m"
                and argv[2].startswith(("vllm.entrypoints", "mlx_lm.server"))
            )
            or (
                executable in {"vllm", "ollama"}
                and len(argv) >= 2
                and argv[1] in {"serve", "runner"}
            )
            or executable == "llama-server"
        )
        if group not in known_groups and pid != os.getpid() and model_process:
            competing.append(pid)
    panics: list[str] = []
    since = state.get("launched_epoch", time.time())
    for base in (
        Path("/Library/Logs/DiagnosticReports"),
        Path.home() / "Library/Logs/DiagnosticReports",
    ):
        if base.is_dir():
            panics.extend(p.name for p in base.glob("*.panic") if p.stat().st_mtime >= since)
    return {
        "at": now(),
        "boot": boot,
        "swap_bytes": swap_bytes,
        "memory_free_percent": free_percent,
        "compressor_bytes": int(compressed[1]) * int(pages[1]),
        "wired_bytes": int(wired[1]) * int(pages[1]),
        "server_group_rss_bytes": rss,
        "thermal_normal": "No thermal warning level has been recorded" in thermal
        and "No performance warning level has been recorded" in thermal,
        "ac_power": "Now drawing from 'AC Power'" in power,
        "disk_free_bytes": shutil.disk_usage(root).free,
        "competing_model_pids": competing,
        "new_panic_reports": sorted(panics),
    }


def health_reasons(
    snapshot: dict[str, Any], baseline: dict[str, Any]
) -> tuple[list[str], list[str]]:
    hard, sustained = [], []
    if snapshot["boot"] != baseline["boot"]:
        hard.append("boot_changed")
    if snapshot["new_panic_reports"]:
        hard.append("new_panic_report")
    if snapshot["competing_model_pids"]:
        hard.append("competing_model_process")
    if snapshot["disk_free_bytes"] < 30 * GIB:
        hard.append("disk_reserve_exhausted")
    if snapshot["server_group_rss_bytes"] > 44 * GIB:
        hard.append("server_rss_hard_limit")
    if snapshot["swap_bytes"] > baseline["swap_bytes"]:
        sustained.append("swap_growth")
    if snapshot["memory_free_percent"] < 20:
        sustained.append("memory_pressure")
    if snapshot["compressor_bytes"] > max(baseline["compressor_bytes"] + 2 * GIB, 4 * GIB):
        sustained.append("compressor_growth")
    if snapshot["server_group_rss_bytes"] > 40 * GIB:
        sustained.append("server_rss_growth")
    if not snapshot["thermal_normal"]:
        sustained.append("thermal_pressure")
    if not snapshot["ac_power"]:
        sustained.append("ac_power_lost")
    return hard, sustained


def cleanup_owned(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    process_results = {}
    errors = {}
    for role in ("worker", "server", "caffeinate"):
        identity = state.get("processes", {}).get(role)
        try:
            process_results[role] = stop_owned(identity)
        except Exception as error:
            process_results[role] = False
            errors[role] = type(error).__name__
    try:
        containers = cleanup_containers(root.name)
    except Exception as error:
        containers = {"owner": root.name, "passed": False, "error_type": type(error).__name__}
    report: dict[str, Any] = {
        "at": now(),
        "processes": process_results,
        "containers": containers,
        "errors": errors,
    }
    report["passed"] = all(process_results.values()) and containers["passed"]
    atomic_save(root / "cleanup.json", report)
    return report
