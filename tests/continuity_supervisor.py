"""Bounded test supervisor owning all native children, sockets and scratch directories."""

import asyncio
import json
import socket
import sys
import tempfile
import time
from pathlib import Path

from padawan.models.hashing import canonical_json_bytes, sha256_digest
from tests.continuity_fixture import CANARY, WORKER_SOURCE

REPOSITORY = Path(__file__).resolve().parents[1]
CHANNELS_PER_WORKER = 16
MAXIMUM_CONTROL = 1_300_000


async def send(process, message):
    data = canonical_json_bytes(message) + b"\n"
    if len(data) > 131_072:
        raise ValueError("fixture command exceeds limit")
    process.stdin.write(data)
    await process.stdin.drain()


async def receive(process):
    async with asyncio.timeout(15):
        raw = await process.stdout.readline()
    if not raw or len(raw) > MAXIMUM_CONTROL or not raw.endswith(b"\n"):
        raise AssertionError("native fixture failed to return a bounded control message")
    return json.loads(raw)


class NativePhase:
    def __init__(self, root, index):
        if not 0 <= index < 5:
            raise ValueError("at most five broker incarnations per fixture")
        self.root, self.index = root, index
        self.broker = None
        self.workers, self.identities, self.channels, self.scratch = [], [], [], []
        self.indices = [0, 0]
        self.journal = {"phase": index, "steps": [], "children": [], "cleanup": []}
        self.started = time.monotonic()
        self.closed = False

    async def __aenter__(self):
        try:
            self.channels = [socket.socketpair() for _ in range(2 * CHANNELS_PER_WORKER)]
            broker_fds = [pair[0].fileno() for pair in self.channels]
            script = (
                "import runpy,sys; "
                f"sys.path.insert(0, {str(REPOSITORY)!r}); "
                f"runpy.run_path({str(REPOSITORY / 'tests/continuity_broker.py')!r}, "
                "run_name='__main__')"
            )
            self.broker = await self.spawn(
                "broker", ["-I", "-c", script, *map(str, broker_fds)], broker_fds, REPOSITORY
            )
            for parent, _ in self.channels:
                parent.close()
            await send(self.broker, {"root": str(self.root), "initialize": self.index == 0})
            boot = await receive(self.broker)
            self.identities = boot.pop("roster")  # ephemeral: never journal the bootstrap
            self.recovery, self.snapshot = boot["recovery"], boot["snapshot"]
            self.journal["boot"] = boot
            for index, identity in enumerate(self.identities):
                scratch = tempfile.TemporaryDirectory(prefix="pprl-scripted-worker-")
                self.scratch.append(scratch)
                fds = [
                    child.fileno()
                    for _, child in self.channels[
                        index * CHANNELS_PER_WORKER : (index + 1) * CHANNELS_PER_WORKER
                    ]
                ]
                worker = await self.spawn(
                    identity["worker_id"],
                    ["-I", "-S", "-c", WORKER_SOURCE.read_text(), *map(str, fds)],
                    fds,
                    Path(scratch.name),
                )
                self.workers.append(worker)
                await send(worker, identity)
            for _, child in self.channels:
                child.close()
            return self
        except BaseException:
            await self.close(crash=True)
            raise

    async def spawn(self, name, arguments, fds, cwd):
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            *arguments,
            pass_fds=tuple(fds),
            env={"PATH": "/usr/bin:/bin"},
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=MAXIMUM_CONTROL,
        )
        self.journal["children"].append({"identity": name, "pid": process.pid})
        return process

    async def command(self, op, **fields):
        await send(self.broker, {"op": op, **fields})
        result = await receive(self.broker)
        self.journal["steps"].append({"command": {"op": op, **fields}, "reply": result})
        return result

    async def rpc(self, worker, kind, **fields):
        index = self.indices[worker]
        if index >= CHANNELS_PER_WORKER:
            raise ValueError("worker channel count exceeded")
        self.indices[worker] += 1
        channel = worker * CHANNELS_PER_WORKER + index
        await send(self.broker, {"op": "serve", "channel": channel})
        await send(self.workers[worker], {"kind": kind, **fields})
        served, response = await asyncio.gather(receive(self.broker), receive(self.workers[worker]))
        assert served == {"served": channel}
        assert response["local_index"] == index
        reply = json.loads(response["reply_bytes"])
        self.journal["steps"].append(
            {
                "worker_id": self.identities[worker]["worker_id"],
                "kind": kind,
                **response,
                "reply_digest": sha256_digest(response["reply_bytes"]),
            }
        )
        return reply

    async def propose(self, worker, **fields):
        reply = await self.rpc(worker, "propose", **fields)
        assert reply["disposition"] == "admitted"
        return {
            "worker_id": self.identities[worker]["worker_id"],
            "request_id": reply["request_id"],
        }

    async def claim(self, worker):
        reply = await self.rpc(worker, "claim")
        assert reply["observation"]["state"] == self.snapshot["state"]["payload"]
        observation = canonical_json_bytes(reply["observation"])
        manifest = json.loads((self.root / "fixture.json").read_bytes())
        # Prove actual interface non-disclosure, including discoverable forensic locators.
        forbidden = [
            CANARY,
            str(self.root),
            *[manifest["canary"][key] for key in ("uri", "digest", "artifact_id")],
        ]
        assert all(value.encode() not in observation for value in forbidden)
        assert set(reply["observation"]) == {"schema_version", "state"}
        return reply

    async def action(self, worker, crash=None):
        await self.claim(worker)
        proposal = await self.propose(worker)
        result = await self.command("commit", **proposal, **({"crash": crash} if crash else {}))
        if crash:
            assert result == {"crash_checkpoint": crash}
        else:
            self.snapshot = result
        return result

    async def finish(self):
        for index in range(8):
            if self.snapshot["rollout"]["status"] == "complete":
                return self.snapshot
            await self.action(index % 2)
        raise AssertionError("finite workflow did not finish within seven transitions")

    async def audit(self, phases):
        result = await self.command("audit")
        assert set(result["counts"].values()) == {1}
        delivered = {
            (item["worker_id"], json.loads(item["reply_bytes"])["request_id"]): item["reply_bytes"]
            for phase in phases
            for item in phase.journal["steps"]
            if "reply_bytes" in item and "error" not in json.loads(item["reply_bytes"])
        }
        retained = {
            (item["worker_id"], item["request_id"]): item["reply_bytes"]
            for item in result["requests"]
        }
        assert delivered == retained  # actual wire bytes, independently reconstructed in broker
        current = {identity["worker_id"] for identity in self.identities}
        assert result["workers"] == {
            identity["worker_id"]: "active" if identity["worker_id"] in current else "revoked"
            for phase in phases
            for identity in phase.identities
        }

    async def close(self, *, crash=False):
        if self.closed:
            return
        self.closed = True
        processes = [*self.workers, *([self.broker] if self.broker else [])]
        for process in processes:
            if process.returncode is None:
                if crash:
                    process.kill()
                else:
                    process.stdin.close()  # EOF: no semantic state or credential shutdown payload
        errors = []
        for process in processes:
            try:
                async with asyncio.timeout(5):
                    await process.wait()
            except TimeoutError:
                process.kill()
                await process.wait()
                errors.append("graceful shutdown timed out")
            stderr = await process.stderr.read(MAXIMUM_CONTROL + 1)
            # Script exceptions cannot contain bearer tokens: no bootstrap object is printed.
            if stderr:
                errors.append(stderr.decode(errors="replace")[:20_000])
            self.journal["cleanup"].append({"pid": process.pid, "returncode": process.returncode})
            if stderr:
                self.journal["cleanup"][-1]["stderr_digest"] = sha256_digest(stderr)
            if not crash and process.returncode != 0:
                errors.append("unexpected native child exit")
        for pair in self.channels:
            for channel in pair:
                channel.close()
        for scratch in self.scratch:
            scratch.cleanup()
        self.journal["scratch_removed"] = all(not Path(item.name).exists() for item in self.scratch)
        self.journal["elapsed_seconds"] = time.monotonic() - self.started
        if self.root.is_dir():
            (self.root / f"phase-{self.index:02}.json").write_bytes(
                canonical_json_bytes(self.journal)
            )
        if errors:
            # Avoid pytest locals / assertion rewrites exposing ephemeral identity dictionaries.
            raise AssertionError("native fixture cleanup failed: " + "\n".join(errors))

    async def __aexit__(self, exc_type, exc, traceback):
        await self.close(crash=exc_type is not None)


def no_retained_credentials(root, phases):
    secrets = [identity["credential"].encode() for phase in phases for identity in phase.identities]
    paths = [path for path in root.rglob("*") if path.is_file()]
    assert sum(path.stat().st_size for path in paths) < 64 * 1024 * 1024
    for path in paths:
        content = path.read_bytes()
        if any(secret in content for secret in secrets):
            raise AssertionError("plaintext scoped credential was retained")
