"""Pinned Linux-container PID 1; no Padawan imports, storage or control credentials.

The broker passes this source to the image's isolated Python interpreter. The
untrusted command runs as a different UID and cannot disable the parent timer.
"""

import base64
import json
import os
import signal
import sys
import time
from typing import NoReturn


def _expired(_signum: int, _frame: object) -> NoReturn:
    # Exiting the namespace's PID 1 terminates every remaining worker descendant.
    os._exit(124)


def _stopped(_signum: int, _frame: object) -> NoReturn:
    os._exit(143)


def main() -> None:
    if os.getpid() != 1 or os.getuid() != 0:
        raise RuntimeError("container supervisor requires its own PID namespace")
    maximum_wire_bytes, maximum_wall_ms = map(int, sys.argv[1:])
    if not 1 <= maximum_wire_bytes <= 16_000_000 or not 1 <= maximum_wall_ms <= 86_400_000:
        raise ValueError("invalid supervisor limits")
    signal.signal(signal.SIGALRM, _expired)
    signal.signal(signal.SIGTERM, _stopped)
    signal.signal(signal.SIGHUP, _stopped)
    signal.setitimer(signal.ITIMER_REAL, maximum_wall_ms / 1000)
    wire = sys.stdin.buffer.read(maximum_wire_bytes + 1)
    if len(wire) > maximum_wire_bytes:
        raise ValueError("supervisor input is too large")
    request = json.loads(wire)
    if set(request) != {"argv", "stdin_base64", "deadline_unix_ms"}:
        raise ValueError("unknown supervisor input fields")
    argv = request["argv"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(arg, str) and "\0" not in arg for arg in argv)
        or not argv[0].startswith("/")
        or type(request["deadline_unix_ms"]) is not int
    ):
        raise ValueError("invalid supervisor command")
    remaining = min(maximum_wall_ms / 1000, request["deadline_unix_ms"] / 1000 - time.time())
    if remaining <= 0:
        os._exit(124)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    data = base64.b64decode(request["stdin_base64"], validate=True)
    read_fd, write_fd = os.pipe()
    child = os.fork()
    if child == 0:
        os.close(write_fd)
        os.dup2(read_fd, 0)
        os.close(read_fd)
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
        os.execv(argv[0], argv)
        os._exit(125)
    os.close(read_fd)
    try:
        while data:
            written = os.write(write_fd, data)
            data = data[written:]
    except BrokenPipeError:
        pass
    finally:
        os.close(write_fd)
    _, status = os.waitpid(child, 0)
    code = os.waitstatus_to_exitcode(status)
    os._exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        os.write(2, b"container supervisor failed\n")
        os._exit(125)
