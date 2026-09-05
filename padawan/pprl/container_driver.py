"""Local Docker actuation with independently captured, privileged execution evidence.

This driver is trusted infrastructure, never a worker interface. Its callbacks
must bind and recheck PPRL authority; it creates no authority of its own.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from padawan.models.hashing import canonical_json_bytes, sha256_digest
from padawan.pprl.container_contracts import ContainerRuntimeIdentity, ProcessContainerProfile

SUPERVISOR_PATH = Path(__file__).resolve().parent.parent / "container_supervisor.py"
_NAME = re.compile(r"padawan-cpu-[a-f0-9]{32}\Z")
_CONTAINER_ID = re.compile(r"[a-f0-9]{64}\Z")
_OWNER_LABEL = "padawan.pprl.container"


def supervisor_bytes() -> bytes:
    return SUPERVISOR_PATH.read_bytes()


def container_wire(
    profile: ProcessContainerProfile, public_input: bytes, deadline: datetime
) -> bytes:
    if deadline.tzinfo is None or len(public_input) > profile.maximum_input_bytes:
        raise ValueError("container input or deadline is invalid")
    wire = canonical_json_bytes(
        {
            "argv": list(profile.argv),
            "stdin_base64": base64.b64encode(public_input).decode("ascii"),
            "deadline_unix_ms": int(deadline.timestamp() * 1000),
        }
    )
    if len(wire) > profile.maximum_wire_bytes:
        raise ValueError("container wire input exceeds its bound")
    return wire


def runtime_identity(info: dict[str, Any]) -> ContainerRuntimeIdentity:
    return ContainerRuntimeIdentity(
        engine_id=info["ID"],
        server_version=info["ServerVersion"],
        kernel_version=info["KernelVersion"],
        operating_system=info["OSType"],
        architecture=info["Architecture"],
        security_options=tuple(sorted(info["SecurityOptions"])),
    )


@dataclass
class _Capture:
    maximum: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    truncated: bool = False

    async def drain(self, source: asyncio.StreamReader, target: bytearray) -> None:
        while data := await source.read(8192):
            remaining = max(0, self.maximum - len(self.stdout) - len(self.stderr))
            target.extend(data[:remaining])
            self.truncated = self.truncated or len(data) > remaining


@dataclass
class ContainerRunEvidence:
    """Raw broker-only evidence. Never serialize this into a worker result."""

    container_name: str
    profile_digest: str
    wire_digest: str
    started_at: datetime
    container_id: str | None = None
    start_attempted: bool = False
    terminated: bool = False
    terminal_verified: bool = False
    removed: bool = False
    capture_complete: bool = True
    truncated: bool = False
    cancelled: bool = False
    exit_code: int | None = None
    reason: str = "not_started"
    stdout: bytes = b""
    stderr: bytes = b""
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    finished_at: datetime | None = None

    def bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                **self.__dict__,
                "stdout": base64.b64encode(self.stdout).decode("ascii"),
                "stderr": base64.b64encode(self.stderr).decode("ascii"),
                "started_at": self.started_at.isoformat(),
                "finished_at": None if self.finished_at is None else self.finished_at.isoformat(),
            }
        )


class ContainerRunCancelled(asyncio.CancelledError):
    def __init__(self, evidence: ContainerRunEvidence) -> None:
        super().__init__("container execution interrupted")
        self.evidence = evidence


class DockerContainerDriver:
    """One owned container per call; no discovery-based selection or automatic retry."""

    def __init__(self, profile: ProcessContainerProfile) -> None:
        self._profile = ProcessContainerProfile.model_validate_json(profile.model_dump_json())
        self._source = supervisor_bytes()
        if sha256_digest(self._source) != self._profile.supervisor_digest:
            raise ValueError("container supervisor differs from reviewed source")

    @property
    def profile(self) -> ProcessContainerProfile:
        return self._profile.model_copy(deep=True)

    def _client(self, config: str, *arguments: str) -> tuple[str, ...]:
        binary = Path(self._profile.docker_binary)
        if not binary.is_absolute() or sha256_digest(binary.read_bytes()) != (
            self._profile.docker_binary_digest
        ):
            raise PermissionError("Docker client differs from reviewed binary")
        return (str(binary), "--config", config, "--host", self._profile.socket_uri, *arguments)

    async def _spawn(self, config: str, *arguments: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            *self._client(config, *arguments),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin"},
            cwd=config,
        )

    async def _command(self, config: str, *arguments: str) -> bytes:
        child = await self._spawn(config, *arguments)
        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        child.stdin.close()
        capture = _Capture(self._profile.maximum_control_bytes)
        readers = [
            asyncio.create_task(capture.drain(child.stdout, capture.stdout)),
            asyncio.create_task(capture.drain(child.stderr, capture.stderr)),
        ]
        try:
            async with asyncio.timeout(10):
                while child.returncode is None:
                    if capture.truncated:
                        raise ValueError("Docker control output exceeds its bound")
                    await asyncio.sleep(0.025)
                await asyncio.gather(*readers)
            if child.returncode or capture.truncated:
                raise RuntimeError(
                    "Docker control failed: " + bytes(capture.stderr).decode("utf-8", "replace")
                )
            return bytes(capture.stdout)
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()
            await asyncio.gather(*readers, return_exceptions=True)

    def create_arguments(self, name: str) -> tuple[str, ...]:
        if _NAME.fullmatch(name) is None:
            raise ValueError("container name is not an exact broker-owned identity")
        p = self._profile
        return (
            "create",
            "--pull=never",
            "--name",
            name,
            "--label",
            f"{_OWNER_LABEL}={name}",
            "--label",
            f"padawan.pprl.profile={p.digest}",
            "--hostname",
            "padawan-action",
            "--network=none",
            "--dns=127.0.0.1",
            "--dns-search=.",
            "--dns-option=ndots:0",
            "--ipc=none",
            "--cgroupns=private",
            "--read-only",
            "--restart=no",
            "--init=false",
            "--user",
            "0:0",
            "--workdir",
            "/work",
            "--cap-drop=ALL",
            "--cap-add=SETUID",
            "--cap-add=SETGID",
            "--security-opt",
            "no-new-privileges=true",
            "--cpus",
            f"{p.cpu_millicores // 1000}.{p.cpu_millicores % 1000:03d}",
            "--memory",
            str(p.memory_bytes),
            "--memory-swap",
            str(p.memory_bytes),
            "--pids-limit",
            str(p.pids_limit),
            "--tmpfs",
            f"/work:rw,noexec,nosuid,nodev,mode=1777,size={p.tmpfs_bytes}",
            "--ulimit",
            "core=0:0",
            "--ulimit",
            "nofile=64:64",
            "--log-driver=none",
            "--env",
            "TMPDIR=/work",
            "--entrypoint",
            p.python_executable,
            "--interactive",
            p.image_id,
            "-I",
            "-S",
            "-B",
            "-c",
            self._source.decode("utf-8"),
            str(p.maximum_wire_bytes),
            str(p.maximum_wall_ms),
        )

    @staticmethod
    def validate_inspection(
        data: dict[str, Any],
        *,
        profile: ProcessContainerProfile,
        source: bytes,
        name: str,
        image: dict[str, Any],
        container_id: str,
    ) -> None:
        p = profile
        if sha256_digest(source) != profile.supervisor_digest:
            raise PermissionError("inspection has a substituted supervisor")
        host, config = data["HostConfig"], data["Config"]
        expected_env = {value.split("=", 1)[0]: value for value in image["Config"].get("Env") or []}
        expected_env["TMPDIR"] = "TMPDIR=/work"
        flags = {
            "NetworkMode": "none",
            "IpcMode": "none",
            "CgroupnsMode": "private",
            "PidMode": "",
            "UTSMode": "",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NanoCpus": p.cpu_millicores * 1_000_000,
            "Memory": p.memory_bytes,
            "MemorySwap": p.memory_bytes,
            "PidsLimit": p.pids_limit,
            "AutoRemove": False,
            "PublishAllPorts": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "Init": False,
            "UsernsMode": "",
            "Dns": ["127.0.0.1"],
            "DnsSearch": ["."],
            "DnsOptions": ["ndots:0"],
            "Tmpfs": {"/work": f"rw,noexec,nosuid,nodev,mode=1777,size={p.tmpfs_bytes}"},
        }
        if (
            image.get("Id") != p.image_id
            or image.get("Os") != "linux"
            or image.get("Architecture") != p.image_architecture
            or image["Config"].get("Volumes")
            or image["Config"].get("Healthcheck")
            or data["Id"] != container_id
            or data["Name"] != "/" + name
            or data["Image"] != p.image_id
            or any(host.get(key) != value for key, value in flags.items())
            or config.get("Image") != p.image_id
            or config.get("User") != "0:0"
            or config.get("WorkingDir") != "/work"
            or config.get("Hostname") != "padawan-action"
            or config.get("Tty") is not False
            or config.get("OpenStdin") is not True
            or config.get("StdinOnce") is not True
            or config.get("Entrypoint") != [p.python_executable]
            or config.get("Cmd")
            != [
                "-I",
                "-S",
                "-B",
                "-c",
                source.decode("utf-8"),
                str(p.maximum_wire_bytes),
                str(p.maximum_wall_ms),
            ]
            or sorted(config.get("Env") or []) != sorted(expected_env.values())
            or config.get("Labels", {}).get(_OWNER_LABEL) != name
            or config.get("Labels", {}).get("padawan.pprl.profile") != p.digest
            or data.get("Mounts")
            or config.get("Volumes")
            or config.get("Healthcheck")
            or sorted(host.get("CapAdd") or []) != ["CAP_SETGID", "CAP_SETUID"]
            or host.get("CapDrop") != ["ALL"]
            or host.get("SecurityOpt") != ["no-new-privileges=true"]
            or host.get("LogConfig") != {"Type": "none", "Config": {}}
            or sorted(host.get("Ulimits") or [], key=lambda item: item["Name"])
            != [
                {"Name": "core", "Hard": 0, "Soft": 0},
                {"Name": "nofile", "Hard": 64, "Soft": 64},
            ]
            or any(
                host.get(key)
                for key in (
                    "Binds",
                    "VolumesFrom",
                    "Devices",
                    "DeviceRequests",
                    "PortBindings",
                    "ExtraHosts",
                    "Links",
                    "GroupAdd",
                    "Sysctls",
                    "StorageOpt",
                    "DeviceCgroupRules",
                    "CgroupParent",
                )
            )
            or host.get("Runtime") not in {"runc", ""}
        ):
            raise PermissionError("created container differs from the reviewed execution boundary")

    async def run(
        self,
        *,
        name: str,
        public_input: bytes,
        deadline: datetime,
        authorize: Callable[[], Awaitable[None]],
        before_start: Callable[[], Awaitable[None]],
        audit: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> ContainerRunEvidence:
        wire = container_wire(self._profile, public_input, deadline)
        evidence = ContainerRunEvidence(
            container_name=name,
            profile_digest=self._profile.digest,
            wire_digest=sha256_digest(wire),
            started_at=datetime.now(UTC),
        )
        attached: asyncio.subprocess.Process | None = None
        readers: list[asyncio.Task[None]] = []
        capture = _Capture(self._profile.maximum_output_bytes)
        create_attempted = False
        image: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="padawan-container-client-") as config_dir:
            try:
                await authorize()
                info = json.loads(await self._command(config_dir, "info", "--format", "{{json .}}"))
                if runtime_identity(info) != self._profile.runtime:
                    raise PermissionError("Docker daemon differs from reviewed runtime")
                image = json.loads(
                    await self._command(config_dir, "image", "inspect", self._profile.image_id)
                )[0]
                if (
                    image["Id"] != self._profile.image_id
                    or image["Os"] != "linux"
                    or image["Architecture"] != self._profile.image_architecture
                    or image["Config"].get("Volumes")
                    or image["Config"].get("Healthcheck")
                ):
                    raise PermissionError(
                        "container image has drifted or installs implicit effects"
                    )
                await audit(
                    "runtime",
                    {"runtime": runtime_identity(info).model_dump(mode="json"), "image": image},
                )
                await authorize()
                create_attempted = True
                result = await self._command(config_dir, *self.create_arguments(name))
                container_id = result.decode("ascii").strip()
                if _CONTAINER_ID.fullmatch(container_id) is None:
                    raise ValueError("Docker create did not return an exact container identity")
                evidence.container_id = container_id
                created = json.loads(await self._command(config_dir, "inspect", container_id))[0]
                self.validate_inspection(
                    created,
                    profile=self._profile,
                    source=self._source,
                    name=name,
                    image=image,
                    container_id=container_id,
                )
                if created["State"]["Status"] != "created" or created["State"]["Running"]:
                    raise PermissionError("container executed before inspected start admission")
                evidence.snapshots.append(created)
                await audit("created", created)
                await authorize()
                await before_start()
                if datetime.now(UTC) >= deadline:
                    raise PermissionError("container action deadline expired before start")
                # The callback must durably mark dispatch before allowing this command.
                evidence.start_attempted = True
                attached = await self._spawn(
                    config_dir, "start", "--attach", "--interactive", container_id
                )
                assert (
                    attached.stdin is not None
                    and attached.stdout is not None
                    and attached.stderr is not None
                )
                readers = [
                    asyncio.create_task(capture.drain(attached.stdout, capture.stdout)),
                    asyncio.create_task(capture.drain(attached.stderr, capture.stderr)),
                ]
                attached.stdin.write(wire)
                async with asyncio.timeout(5):
                    await attached.stdin.drain()
                attached.stdin.close()
                while attached.returncode is None:
                    if capture.truncated:
                        evidence.reason = "output_limit"
                        break
                    if datetime.now(UTC) >= deadline:
                        evidence.reason = "deadline"
                        break
                    await authorize()
                    await asyncio.sleep(0.1)
                else:
                    evidence.reason = "exited"
            except asyncio.CancelledError:
                evidence.cancelled, evidence.reason = True, "cancelled"
            except Exception as exc:
                evidence.reason = "boundary_failure"
                evidence.failures.append({"class": type(exc).__name__, "message": str(exc)})
            finally:
                if create_attempted:
                    try:
                        found = json.loads(await self._command(config_dir, "inspect", name))[0]
                        found_id = found["Id"]
                        if (
                            _CONTAINER_ID.fullmatch(found_id) is None
                            or found["Name"] != "/" + name
                            or found["Config"].get("Labels", {}).get(_OWNER_LABEL) != name
                            or found["Config"].get("Labels", {}).get("padawan.pprl.profile")
                            != self._profile.digest
                            or evidence.container_id not in {None, found_id}
                        ):
                            raise PermissionError(
                                "cleanup cannot establish exact container ownership"
                            )
                        evidence.container_id = found_id
                        if found["State"]["Running"]:
                            await self._command(config_dir, "kill", "--signal", "KILL", found_id)
                        terminal = json.loads(await self._command(config_dir, "inspect", found_id))[
                            0
                        ]
                        if terminal["State"]["Running"] or terminal["State"]["Status"] not in {
                            "exited",
                            "created",
                        }:
                            raise RuntimeError("container terminal state is unknown")
                        evidence.terminated = terminal["State"]["Status"] == "exited"
                        evidence.exit_code = (
                            terminal["State"]["ExitCode"] if evidence.terminated else None
                        )
                        evidence.snapshots.append(terminal)
                        try:
                            self.validate_inspection(
                                terminal,
                                profile=self._profile,
                                source=self._source,
                                name=name,
                                image=image,
                                container_id=found_id,
                            )
                            await audit("terminal", terminal)
                            evidence.terminal_verified = True
                        except Exception as exc:
                            evidence.capture_complete = False
                            evidence.failures.append(
                                {"class": type(exc).__name__, "message": str(exc)}
                            )
                            evidence.reason = "boundary_failure"
                        removed = await self._command(config_dir, "rm", found_id)
                        remaining = await self._command(
                            config_dir,
                            "ps",
                            "-a",
                            "--no-trunc",
                            "--filter",
                            f"id={found_id}",
                            "--format",
                            "{{.ID}}",
                        )
                        if remaining.strip():
                            raise RuntimeError("container remains after cleanup")
                        evidence.removed = True
                        await audit(
                            "cleanup",
                            {
                                "container_id": found_id,
                                "removed_id": removed.decode("ascii").strip(),
                                "remaining": remaining.decode("ascii"),
                            },
                        )
                    except Exception as exc:
                        evidence.capture_complete = False
                        evidence.failures.append({"class": type(exc).__name__, "message": str(exc)})
                if attached is not None:
                    if attached.returncode is None:
                        attached.kill()
                    await attached.wait()
                reader_results = await asyncio.gather(*readers, return_exceptions=True)
                if any(isinstance(result, BaseException) for result in reader_results):
                    evidence.capture_complete = False
                evidence.stdout, evidence.stderr = bytes(capture.stdout), bytes(capture.stderr)
                evidence.truncated = capture.truncated
                evidence.capture_complete = evidence.capture_complete and not capture.truncated
                evidence.finished_at = datetime.now(UTC)
        if evidence.cancelled:
            raise ContainerRunCancelled(evidence)
        return evidence
