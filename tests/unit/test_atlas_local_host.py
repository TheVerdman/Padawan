"""Real disposable process controls; no model runtime or Docker dependency."""

from __future__ import annotations

import subprocess
import sys

import pytest

from padawan.atlas.local_host import owned_group_members, process_identity, stop_owned


def test_cleanup_terminates_descendants_after_group_leader_exits():
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(0.5)",
        ],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    identity = process_identity(child.pid)
    try:
        assert child.stdout is not None
        descendant = int(child.stdout.readline())
        child.wait(timeout=5)
        assert descendant in owned_group_members(identity)
        assert stop_owned(identity)
        assert owned_group_members(identity) == []
    finally:
        stop_owned(identity)
        child.wait(timeout=5)


def test_cleanup_refuses_a_changed_leader_identity():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    identity = process_identity(child.pid)
    try:
        with pytest.raises(PermissionError, match="identity changed"):
            stop_owned({**identity, "started": "different-start"})
        assert child.poll() is None
    finally:
        assert stop_owned(identity)
        child.wait(timeout=5)


def test_cleanup_refuses_its_own_process_group():
    import os

    with pytest.raises(PermissionError, match="independent ownership"):
        stop_owned({"pid": os.getpgrp(), "pgid": os.getpgrp(), "started": "irrelevant"})
