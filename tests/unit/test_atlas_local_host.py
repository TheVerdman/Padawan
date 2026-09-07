"""Real disposable process controls; no model runtime or Docker dependency."""

from __future__ import annotations

import os
import sys
import time

import pytest

from padawan.orchestration import local_host
from padawan.orchestration.local_host import owned_group_members, stop_owned
from tests.support.local_processes import owned_process


def test_cleanup_terminates_descendants_after_group_leader_exits():
    with owned_process(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(0.5)",
        ],
    ) as (child, identity):
        assert child.stdout is not None
        descendant = int(child.stdout.readline())
        child.wait(timeout=5)
        assert descendant in owned_group_members(identity)
        assert stop_owned(identity)
        assert owned_group_members(identity) == []


def test_cleanup_refuses_a_changed_leader_identity():
    with owned_process([sys.executable, "-c", "import time; time.sleep(60)"]) as (child, identity):
        with pytest.raises(PermissionError, match="identity changed"):
            stop_owned({**identity, "started": "different-start"})
        assert child.poll() is None
        assert stop_owned(identity)
        child.wait(timeout=5)


def test_cleanup_refuses_its_own_process_group():
    with pytest.raises(PermissionError, match="independent ownership"):
        stop_owned({"pid": os.getpgrp(), "pgid": os.getpgrp(), "started": "irrelevant"})


def test_fixture_reaps_process_when_identity_inspection_fails(monkeypatch):
    inspected = []

    def fail_inspection(pid):
        inspected.append(pid)
        raise RuntimeError("injected identity inspection failure")

    monkeypatch.setattr(local_host, "process_identity", fail_inspection)
    with (
        pytest.raises(RuntimeError, match="injected identity inspection failure"),
        owned_process([sys.executable, "-c", "import time; time.sleep(60)"]),
    ):
        pytest.fail("setup should fail before the fixture yields")
    assert len(inspected) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(inspected[0], 0)
    with pytest.raises(ProcessLookupError):
        os.killpg(inspected[0], 0)


def test_fixture_cleans_descendants_when_test_fails_after_leader_exit():
    with (
        pytest.raises(RuntimeError, match="injected test failure"),
        owned_process(
            [
                sys.executable,
                "-c",
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "print(p.pid,flush=True); time.sleep(0.5)",
            ]
        ) as (child, identity),
    ):
        assert child.stdout is not None
        descendant = int(child.stdout.readline())
        child.wait(timeout=5)
        assert descendant in owned_group_members(identity)
        raise RuntimeError("injected test failure")
    deadline = time.monotonic() + 5
    while owned_group_members(identity) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert owned_group_members(identity) == []
