"""Disposable process groups created by these tests, independent of host inspection."""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import Any

from padawan.orchestration import local_host


@contextmanager
def owned_process(argv: Sequence[str]) -> Iterator[tuple[subprocess.Popen[str], dict[str, Any]]]:
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, start_new_session=True)
    try:
        identity = local_host.process_identity(child.pid)
        yield child, identity
    finally:
        # This fallback owns the new session directly through Popen. It must work
        # even when ps/identity inspection fails or the group leader has exited.
        # It accepts no externally supplied identity and changes no runtime policy.
        try:
            assert child.pid != os.getpgrp()
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
        finally:
            child.wait(timeout=5)
            if child.stdout is not None:
                child.stdout.close()
