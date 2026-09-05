"""Opt-in, bounded Linux CPU fixtures. These never load a model or pull an image."""

import asyncio
import base64
import json
import os
import signal
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from padawan.governance.amber import AmberStatus
from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.models.tables import ProcessContainerWorkloadRow
from padawan.pprl.container_contracts import ProcessContainerProfile
from padawan.pprl.container_driver import DockerContainerDriver, supervisor_bytes
from padawan.pprl.containers import ProcessContainerUnavailableError
from tests.container_helpers import commit_container_action, container_context, container_profile

pytestmark = pytest.mark.docker


@pytest.fixture
def fixture_profile():
    selected = os.environ.get("PADAWAN_TEST_CONTAINER_PROFILE")
    if not selected:
        pytest.skip("set PADAWAN_TEST_CONTAINER_PROFILE to an explicitly reviewed CPU fixture")
    original = ProcessContainerProfile.model_validate_json(Path(selected).read_bytes())
    assert original.supervisor_digest == sha256_digest(supervisor_bytes())
    assert original.cpu_millicores <= 500 and original.memory_bytes <= 268_435_456
    assert original.maximum_wall_ms <= 10_000 and original.maximum_output_bytes <= 65_536

    def make(script, **changes):
        # Each fixture has a fresh synthetic grant for its exact, bounded probe command.
        return container_profile(
            datetime.now(UTC),
            **{
                **original.model_dump(),
                "argv": (original.python_executable, "-I", "-c", script),
                **changes,
            },
        )

    return make


@pytest.fixture
def retained_directory(tmp_path, request):
    root = os.environ.get("PADAWAN_TEST_CONTAINER_EVIDENCE")
    directory = (Path(root) if root else tmp_path) / request.node.name
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def retain(directory, name, value):
    (directory / name).write_bytes(canonical_json_bytes(value))


async def raw_command(profile, *args):
    with tempfile.TemporaryDirectory(prefix="padawan-cpu-probe-client-") as config:
        return await DockerContainerDriver(profile)._command(config, *args)


async def inspect_owned(profile, name):
    data = json.loads(await raw_command(profile, "inspect", name))[0]
    assert data["Name"] == "/" + name
    assert data["Config"]["Labels"]["padawan.pprl.container"] == name
    assert data["Config"]["Labels"]["padawan.pprl.profile"] == profile.digest
    return data


async def assert_removed(profile, container_id):
    result = await raw_command(
        profile, "ps", "-a", "--no-trunc", "--filter", "id=" + container_id, "--format", "{{.ID}}"
    )
    assert not result.strip()


async def retain_process(ctx, directory, invocation_id):
    async with ctx.database.transaction() as session:
        workload, receipt, data = await ctx.records.inspect(session, invocation_id)
        balance = await ctx.amber.resources.inspect(session, ctx.authorization.digest)
    retain(directory, "workload.json", workload)
    retain(directory, "receipt.json", receipt)
    retain(directory, "balance.json", balance)
    retain(directory, "execution.json", data)
    with (
        sqlite3.connect(ctx.database.engine.url.database) as source,
        sqlite3.connect(directory / "broker-state.sqlite3") as destination,
    ):
        source.backup(destination)
    return workload, receipt, data, balance


async def test_real_container_execution_controls_and_private_evidence(
    database, tmp_path, fixture_profile, retained_directory
):
    host_canary = tmp_path / "owned-host-canary"
    host_canary.write_text("fixture-only-not-a-secret")
    script = """import errno, hashlib, json, os, pathlib, signal, socket, subprocess, sys
def denied(operation):
    try:
        operation()
    except OSError as exc:
        return exc.errno
    return None
data = sys.stdin.buffer.read()
lines = pathlib.Path('/proc/self/status').read_text().splitlines()
status = dict(line.split(':', 1) for line in lines)
work = pathlib.Path('/work/probe')
work.write_text('#!/bin/sh\\nexit 0\\n')
work.chmod(0o755)
s = socket.socket()
s.settimeout(0.25)
result = {
    'input_digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
    'resolver': pathlib.Path('/etc/resolv.conf').read_text(),
    'uid': os.getuid(), 'gid': os.getgid(), 'groups': os.getgroups(),
    'cap_eff': status['CapEff'].strip(), 'no_new_privs': status['NoNewPrivs'].strip(),
    'setuid_denied': denied(lambda: os.setuid(0)),
    'stop_supervisor_denied': denied(lambda: os.kill(1, signal.SIGSTOP)),
    'kill_supervisor_denied': denied(lambda: os.kill(1, signal.SIGTERM)),
    'root_write_denied': denied(lambda: pathlib.Path('/root-probe').write_text('x')),
    'host_read_denied': denied(lambda: pathlib.Path(HOST_CANARY).read_bytes()),
    'socket_read_denied': denied(lambda: pathlib.Path('/var/run/docker.sock').stat()),
    'network_denied': denied(lambda: s.connect(('192.0.2.1', 9))),
    'tmpfs_noexec': denied(lambda: subprocess.run(['/work/probe'], check=True)),
    'private_work_contents': sorted(item.name for item in pathlib.Path('/work').iterdir()),
    'namespace_processes': sorted(int(item.name) for item in pathlib.Path('/proc').iterdir()
                                 if item.name.isdigit()),
}
s.close()
print(json.dumps(result, sort_keys=True))
""".replace("HOST_CANARY", repr(str(host_canary)))
    profile = fixture_profile(script)
    retain(retained_directory, "profile.json", profile)
    ctx = await container_context(
        database,
        retained_directory,
        lambda: datetime.now(UTC),
        profile=profile,
        driver=DockerContainerDriver(profile),
    )
    receipt = await ctx.service.execute(**ctx.kwargs)
    _, _, data, balance = await retain_process(ctx, retained_directory, receipt.invocation_id)
    result = json.loads(base64.b64decode(data["stdout"]))
    assert data["exit_code"] == 0 and data["stderr"] == "" and not data["failures"]
    assert result["input_digest"] == sha256_digest(canonical_json_bytes(ctx.observed.observation))
    assert (result["uid"], result["gid"], result["groups"]) == (65534, 65534, [])
    assert int(result["cap_eff"], 16) == 0 and result["no_new_privs"] == "1"
    resolver_lines = [
        line.strip()
        for line in result["resolver"].splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert resolver_lines == ["nameserver 127.0.0.1", "options ndots:0"]
    for key in (
        "setuid_denied",
        "stop_supervisor_denied",
        "kill_supervisor_denied",
        "root_write_denied",
        "host_read_denied",
        "socket_read_denied",
        "network_denied",
        "tmpfs_noexec",
    ):
        assert result[key] is not None, (key, result)
    assert result["private_work_contents"] == ["probe"]
    assert 1 in result["namespace_processes"] and len(result["namespace_processes"]) == 2
    assert receipt.complete_capture and receipt.removed and balance.open_reservations == 0
    event, state = await commit_container_action(ctx)
    assert "cap_eff" not in event.model_dump_json() + state.model_dump_json()
    retain(retained_directory, "public-event.json", event)
    await assert_removed(profile, data["container_id"])
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)


@pytest.mark.parametrize("interruption", ["output_limit", "cancel", "pause"])
async def test_real_container_interruption_conserves_evidence_and_resources(
    database, fixture_profile, retained_directory, interruption
):
    script = "import time; print('fixture-ready', flush=True); time.sleep(60)"
    if interruption == "output_limit":
        script = "import sys,time; sys.stdout.write('x'*100000); sys.stdout.flush(); time.sleep(60)"
    profile = fixture_profile(script, maximum_output_bytes=8192)
    retain(retained_directory, "profile.json", profile)
    ctx = await container_context(
        database,
        retained_directory,
        lambda: datetime.now(UTC),
        profile=profile,
        driver=DockerContainerDriver(profile),
    )
    started = asyncio.Event()
    original = ctx.driver._spawn

    async def spawn(config, *args):
        result = await original(config, *args)
        if args[0] == "start":
            started.set()
        return result

    ctx.driver._spawn = spawn
    task = asyncio.create_task(ctx.service.execute(**ctx.kwargs))
    try:
        await asyncio.wait_for(started.wait(), timeout=20)
        await asyncio.sleep(0.2)
        if interruption == "cancel":
            task.cancel()
        elif interruption == "pause":
            async with database.transaction() as session:
                await ctx.amber.transition(
                    session,
                    authorization_digest=ctx.authorization.digest,
                    to_status=AmberStatus.PAUSED,
                    actor_id="reviewer-a",
                    reason="fixture pause",
                    evidence_refs=("fixture:pause",),
                    occurred_at=datetime.now(UTC),
                )
        if interruption == "output_limit":
            with pytest.raises(ProcessContainerUnavailableError):
                await asyncio.wait_for(task, timeout=20)
        elif interruption == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=20)
        else:
            await asyncio.wait_for(task, timeout=20)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    async with database.transaction() as session:
        row = await session.scalar(select(ProcessContainerWorkloadRow))
        assert row is not None
        invocation_id = row.invocation_id
    _, receipt, data, balance = await retain_process(ctx, retained_directory, invocation_id)
    assert data["terminated"] and data["terminal_verified"] and receipt.removed
    await assert_removed(profile, data["container_id"])
    if interruption == "output_limit":
        assert data["truncated"] and not receipt.complete_capture
        assert len(base64.b64decode(data["stdout"])) <= profile.maximum_output_bytes
        assert balance.open_reservations == 1 and balance.held.actions == 1
        with pytest.raises(PermissionError):
            await commit_container_action(ctx)
    else:
        assert receipt.complete_capture and balance.open_reservations == 0
        assert balance.charged.actions == 1
        assert data["cancelled"] == (interruption == "cancel")
    with pytest.raises(ProcessContainerUnavailableError):
        await ctx.service.execute(**ctx.kwargs)


async def test_container_watchdog_survives_broker_process_group_death(
    fixture_profile, retained_directory
):
    # SIGKILL the owned broker and CLI, leaving only the container-local supervisor.
    profile = fixture_profile(
        "import pathlib,time; pathlib.Path('/work/ready').write_text('yes'); time.sleep(60)",
        maximum_wall_ms=6000,
    )
    path = retained_directory / "profile.json"
    retain(retained_directory, "profile.json", profile)
    name = "padawan-cpu-" + uuid4().hex
    source = """import asyncio, sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from padawan.models.hashing import canonical_json_bytes
from padawan.pprl.container_contracts import ProcessContainerProfile
from padawan.pprl.container_driver import DockerContainerDriver
async def main():
    profile = ProcessContainerProfile.model_validate_json(Path(sys.argv[1]).read_bytes())
    async def authorize(): pass
    async def audit(kind, data):
        Path(sys.argv[3], kind + '.json').write_bytes(canonical_json_bytes(data))
    result = await DockerContainerDriver(profile).run(name=sys.argv[2], public_input=b'fixture',
        deadline=datetime.now(UTC)+timedelta(seconds=30), authorize=authorize,
        before_start=authorize, audit=audit)
    Path(sys.argv[3], 'unexpected-broker-result.json').write_bytes(result.bytes())
asyncio.run(main())
"""
    (retained_directory / "broker-fixture.py").write_text(source)
    broker = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        source,
        str(path),
        name,
        str(retained_directory),
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path.cwd())},
    )
    container_id = None
    try:
        async with asyncio.timeout(20):
            while not (retained_directory / "created.json").exists():
                assert broker.returncode is None
                await asyncio.sleep(0.05)
            container_id = json.loads((retained_directory / "created.json").read_bytes())["Id"]
            while True:
                running = await inspect_owned(profile, name)
                if running["State"]["Running"]:
                    # Read only our own marker to establish that the unprivileged child is active.
                    ready = await raw_command(
                        profile,
                        "exec",
                        container_id,
                        profile.python_executable,
                        "-I",
                        "-c",
                        "from pathlib import Path; print(Path('/work/ready').exists())",
                    )
                    if ready.strip() == b"True":
                        break
                await asyncio.sleep(0.05)
        retain(retained_directory, "before-broker-death.json", running)
        assert broker.returncode is None
        os.killpg(broker.pid, signal.SIGKILL)
        await broker.wait()
        assert broker.returncode == -signal.SIGKILL
        async with asyncio.timeout(15):
            while True:
                terminal = await inspect_owned(profile, name)
                if not terminal["State"]["Running"]:
                    break
                await asyncio.sleep(0.2)
        retain(retained_directory, "after-broker-death.json", terminal)
        assert terminal["State"]["Status"] == "exited" and terminal["State"]["ExitCode"] == 124
        assert not (retained_directory / "unexpected-broker-result.json").exists()
        retain(
            retained_directory,
            "broker-death.json",
            {
                "pid": broker.pid,
                "returncode": broker.returncode,
                "container_id": container_id,
                "signal": "SIGKILL",
            },
        )
    finally:
        if broker.returncode is None:
            os.killpg(broker.pid, signal.SIGKILL)
            await broker.wait()
        if container_id is not None:
            owned = await inspect_owned(profile, name)
            if owned["State"]["Running"]:
                await raw_command(profile, "kill", "--signal", "KILL", container_id)
            removed = await raw_command(profile, "rm", container_id)
            await assert_removed(profile, container_id)
            retain(
                retained_directory,
                "cleanup.json",
                {"removed_id": removed.decode().strip(), "remaining": ""},
            )


async def test_fresh_containers_cannot_observe_peer_tmpfs(fixture_profile, retained_directory):
    first_ready = asyncio.Event()
    second_profile = fixture_profile("import pathlib; print(list(pathlib.Path('/work').iterdir()))")
    first_profile = fixture_profile(
        "import pathlib,time; pathlib.Path('/work/peer-canary').write_text('x'); time.sleep(60)"
    )

    async def run(profile, suffix, ready=None):
        name = "padawan-cpu-" + uuid4().hex
        retain(retained_directory, suffix + "-profile.json", profile)

        async def authorize():
            pass

        async def audit(kind, data):
            retain(retained_directory, suffix + "-" + kind + ".json", data)

        driver = DockerContainerDriver(profile)
        original = driver._spawn

        async def spawn(config, *args):
            result = await original(config, *args)
            if args[0] == "start" and ready is not None:
                ready.set()
            return result

        driver._spawn = spawn
        evidence = await driver.run(
            name=name,
            public_input=b"fixture",
            deadline=datetime.now(UTC) + timedelta(seconds=20),
            authorize=authorize,
            before_start=authorize,
            audit=audit,
        )
        (retained_directory / (suffix + "-result.json")).write_bytes(evidence.bytes())
        assert evidence.terminated and evidence.removed and evidence.capture_complete
        return evidence

    first = asyncio.create_task(run(first_profile, "first", first_ready))
    try:
        await asyncio.wait_for(first_ready.wait(), timeout=20)
        created = json.loads((retained_directory / "first-created.json").read_bytes())
        async with asyncio.timeout(3):
            while True:
                ready = await raw_command(
                    first_profile,
                    "exec",
                    created["Id"],
                    first_profile.python_executable,
                    "-I",
                    "-c",
                    "from pathlib import Path; print(Path('/work/peer-canary').exists())",
                )
                if ready.strip() == b"True":
                    break
                await asyncio.sleep(0.05)
        assert not first.done()
        second = await run(second_profile, "second")
        assert second.stdout.strip() == b"[]" and second.exit_code == 0
        await asyncio.wait_for(first, timeout=20)
    finally:
        if not first.done():
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
