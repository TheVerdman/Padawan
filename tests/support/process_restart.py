"""Shared process restart fixture setup."""

import asyncio
import json
import sys
from pathlib import Path


async def run_broker(payload):
    root = Path(__file__).resolve().parents[2]
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
        "runpy.run_module('tests.recovery_restart_fixture', run_name='__main__')",
        str(root),
        env={"PATH": "/usr/bin:/bin"},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(json.dumps(payload).encode()), 15)
        assert child.returncode == 0, stderr.decode()
        assert not stderr
        return json.loads(stdout)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
