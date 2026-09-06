"""Native private fixture controller endpoint; inherited pipes only, no listener.

The test supervisor owns and reaps every broker and worker process directly.
Only this broker imports institutional stores. Workers receive no fixture path.
"""

import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from padawan.models.database import Database
from padawan.pprl.worker_broker import serve_worker_stream
from tests.continuity_fixture import Institution


def emit(value):
    print(json.dumps(value), flush=True)


async def main():
    os.umask(0o077)
    reader = asyncio.StreamReader(limit=131_073)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), sys.stdin.buffer
    )

    async def read():
        data = await reader.readline()
        if not data:
            return None
        if len(data) > 131_072 or not data.endswith(b"\n"):
            raise ValueError("fixture control frame exceeds its bound")
        return json.loads(data)

    async def checkpoint(name):
        emit({"crash_checkpoint": name})
        # Actual SIGKILL from the owning test supervisor skips finally/SQL rollback.
        # A missing kill expires and fails instead of proceeding past the crash point.
        async with asyncio.timeout(15):
            await reader.readline()
        raise RuntimeError("crash checkpoint must terminate the broker")

    header = await read()
    root = Path(header["root"])
    if header["initialize"]:
        root.mkdir(mode=0o700)  # exclusive: no schema creation in an existing directory
    elif not (root / "fixture.json").is_file() or not (root / "institution.sqlite").is_file():
        raise ValueError("restart requires retained fixture stores")
    database = Database.sqlite(root / "institution.sqlite")
    institution = Institution(database, root)
    sockets = {index: socket.socket(fileno=int(fd)) for index, fd in enumerate(sys.argv[1:])}
    try:
        if header["initialize"]:
            await database.create_schema()
            await institution.initialize()
            recovery = None
        else:
            institution.reopen()
            recovery = await institution.recover_roster()
        snapshot = await institution.snapshot()
        roster = (
            await institution.issue_roster() if snapshot["rollout"]["status"] == "active" else []
        )
        emit({"roster": roster, "recovery": recovery, "snapshot": snapshot})
        for _ in range(128):
            command = await read()
            if command is None or command["op"] == "stop":
                break
            match command["op"]:
                case "serve":
                    channel = sockets.pop(command["channel"])
                    worker_reader, writer = await asyncio.open_connection(sock=channel)
                    await serve_worker_stream(worker_reader, writer, institution.broker)
                    result = {"served": command["channel"]}
                case "snapshot":
                    result = await institution.snapshot()
                case "commit":
                    result = await institution.commit(command, checkpoint)
                case "pause":
                    result = await institution.pause(command["paused"])
                case "effect":
                    result = await institution.effect(command)
                case "abandon":
                    result = await institution.abandon(command["recovery_id"])
                case "compile":
                    result = await institution.compile()
                case "audit":
                    result = await institution.audit()
                case _:
                    raise ValueError("unknown private fixture operation")
            emit(result)
        else:
            raise ValueError("fixture command count exceeded")
    finally:
        for channel in sockets.values():
            channel.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
