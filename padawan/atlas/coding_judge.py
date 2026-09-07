"""Local batch C++ judging with separate candidate and official-checker containers.

The host and Docker daemon are trusted. No service is launched. Only a candidate's binary is
mounted in its execution container; hidden inputs arrive on stdin and answers go exclusively to
the checker. This module does not admit compiler output or hidden-test feedback to model context.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4
from zipfile import ZipFile


class JudgeInfrastructureError(RuntimeError):
    """A judge defect or incomplete package must not count as a model failure."""


@dataclass(frozen=True)
class JudgeCase:
    input_path: Path
    answer_path: Path
    seconds: float
    memory_bytes: int


@dataclass(frozen=True)
class JudgePackage:
    problem_id: str
    archive_digest: str
    root: Path
    checker: Path
    cases: tuple[JudgeCase, ...]
    declared_case_count: int


@dataclass(frozen=True)
class JudgeResult:
    verdict: str
    cases_passed: int
    cases_total: int
    elapsed_seconds: float
    package_digest: str
    candidate_digest: str
    image: str
    failed_case: int | None = None

    @property
    def success(self) -> bool:
        return self.verdict == "Accepted"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("package path must be a relative POSIX filename")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in value.split("/")):
        raise ValueError("package path escapes its root")
    return str(path)


def _quantity(value: object, *, memory: bool) -> float:
    if not isinstance(value, str):
        raise ValueError("judge limits require explicit units")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(ms|s|k|m|g)", value)
    if match is None:
        raise ValueError("unsupported judge limit")
    factors = {"k": 1024, "m": 1024**2, "g": 1024**3} if memory else {"ms": 0.001, "s": 1}
    if match[2] not in factors or float(match[1]) <= 0:
        raise ValueError("invalid judge limit")
    return float(match[1]) * factors[match[2]]


def unpack_package(
    archive: Path,
    destination: Path,
    *,
    problem_id: str,
    expected_digest: str,
    max_unpacked_bytes: int = 2 * 1024**3,
    include_extra_cases: bool = False,
) -> JudgePackage:
    """Check the complete archive before extracting; never execute an upstream launcher."""
    import yaml

    if not re.fullmatch(r"[A-Za-z0-9_-]+", problem_id):
        raise ValueError("invalid problem ID")
    if file_sha256(archive) != expected_digest:
        raise ValueError("judge archive digest mismatch")
    if destination.exists():
        raise ValueError("judge package destination must be new")
    with ZipFile(archive) as zipped:
        entries = zipped.infolist()
        names = [entry.filename.rstrip("/") for entry in entries]
        if len(entries) > 20_000 or sum(e.file_size for e in entries) > max_unpacked_bytes:
            raise ValueError("judge archive exceeds preparation limits")
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive members")
        for entry in entries:
            _relative(entry.filename.rstrip("/"))
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}):
                raise ValueError("non-regular judge archive member")
        raw_config = zipped.read("config.yaml")
        if len(raw_config) > 65_536:
            raise ValueError("oversized judge configuration")
        config = yaml.safe_load(raw_config)
        if not isinstance(config, dict):
            raise ValueError("judge configuration must be a mapping")
        allowed = {
            "type",
            "time_limit",
            "memory_limit",
            "checker",
            "input_prefix",
            "output_prefix",
            "input_suffix",
            "output_suffix",
            "subtasks",
        }
        if set(config) - allowed or config.get("type") != "default":
            raise ValueError("this runner supports declared batch/default packages only")
        checker = _relative(config["checker"])
        subtasks = config.get("subtasks")
        if not isinstance(subtasks, list) or not subtasks:
            raise ValueError("judge package has no declared subtasks")
        cases: list[JudgeCase] = []
        for subtask in subtasks:
            if not isinstance(subtask, dict) or set(subtask) - {
                "score",
                "n_cases",
                "time_limit",
                "memory_limit",
            }:
                raise ValueError("unsupported subtask configuration")
            count = subtask.get("n_cases")
            if type(count) is not int or not 1 <= count <= 10_000:
                raise ValueError("invalid declared case count")
            seconds = _quantity(subtask.get("time_limit", config.get("time_limit")), memory=False)
            memory = int(
                _quantity(subtask.get("memory_limit", config.get("memory_limit")), memory=True)
            )
            if seconds > 120 or memory > 4 * 1024**3:
                raise ValueError("package exceeds supported per-case resources")
            for _ in range(count):
                index = len(cases) + 1
                inp = _relative(
                    f"testdata/{config.get('input_prefix', '')}{index}"
                    f"{config.get('input_suffix', '.in')}"
                )
                ans = _relative(
                    f"testdata/{config.get('output_prefix', '')}{index}"
                    f"{config.get('output_suffix', '.ans')}"
                )
                if inp not in names or ans not in names:
                    raise ValueError("declared judge case is missing")
                cases.append(JudgeCase(destination / inp, destination / ans, seconds, memory))
        if checker not in names or not checker.endswith(".cpp"):
            raise ValueError("official C++ checker is missing")
        declared = {str(case.input_path.relative_to(destination)) for case in cases}
        declared |= {str(case.answer_path.relative_to(destination)) for case in cases}
        test_files = {
            name for name in names if name.startswith("testdata/") and not name.endswith("/")
        }
        test_files -= {e.filename.rstrip("/") for e in entries if e.is_dir()}
        declared_case_count = len(cases)
        if declared != test_files:
            if not include_extra_cases:
                raise ValueError("archive test coverage differs from declared cases")
            # An explicit preparation choice can strengthen coverage, never drop tests.
            while declared != test_files:
                index = len(cases) + 1
                inp = _relative(
                    f"testdata/{config.get('input_prefix', '')}{index}"
                    f"{config.get('input_suffix', '.in')}"
                )
                ans = _relative(
                    f"testdata/{config.get('output_prefix', '')}{index}"
                    f"{config.get('output_suffix', '.ans')}"
                )
                if inp not in test_files or ans not in test_files or index > 10_000:
                    raise ValueError("undeclared tests must be contiguous complete pairs")
                cases.append(
                    JudgeCase(
                        destination / inp,
                        destination / ans,
                        _quantity(config.get("time_limit"), memory=False),
                        int(_quantity(config.get("memory_limit"), memory=True)),
                    )
                )
                declared.update((inp, ans))
        destination.mkdir(parents=True, mode=0o700)
        try:
            for entry in entries:
                target = destination / entry.filename
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(entry) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return JudgePackage(
        problem_id,
        expected_digest,
        destination,
        destination / checker,
        tuple(cases),
        declared_case_count,
    )


class DockerBatchJudge:
    """An explicit local CPU runner. Construction itself performs no Docker operations."""

    def __init__(
        self,
        *,
        image: str,
        scratch: Path,
        testlib: Path,
        testlib_digest: str,
        owner_label: str | None = None,
    ) -> None:
        if not re.fullmatch(r"(?:[A-Za-z0-9./:_-]+@)?sha256:[0-9a-f]{64}", image):
            raise ValueError("judge image must be pinned by digest")
        if file_sha256(testlib) != testlib_digest:
            raise ValueError("testlib header digest mismatch")
        self.image = image
        self.scratch = scratch.resolve()
        self.testlib = testlib.resolve()
        if owner_label is not None and not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", owner_label):
            raise ValueError("judge owner label must be a bounded literal identifier")
        self.owner_label = owner_label
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _run(
        self,
        command: list[str],
        *,
        work: Path,
        writable: bool,
        memory: int,
        seconds: float,
        output: Path,
        input_path: Path | None = None,
        output_limit: int = 128 * 1024**2,
        stderr_limit: int = 64 * 1024**2,
        deadline: float | None = None,
    ) -> tuple[int, bool]:
        name = f"padawan-judge-{uuid4().hex}"
        command_line = [
            "docker",
            "run",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            "1",
            "--memory",
            str(memory),
            "--memory-swap",
            str(memory),
            "--pids-limit",
            "128",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--ulimit",
            f"cpu={math.ceil(seconds)}:{math.ceil(seconds) + 1}",
            "--ulimit",
            f"stack={memory}:{memory}",
            "--ulimit",
            "core=0:0",
            "--ulimit",
            "fsize=268435456:268435456",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--log-driver",
            "none",
            "--label",
            "padawan.validation=atlas-coding-judge",
            "--mount",
            f"type=bind,src={work.resolve()},dst=/work" + ("" if writable else ",readonly"),
            "--workdir",
            "/work",
            "--entrypoint",
            "/usr/bin/timeout",
            self.image,
            "--signal=TERM",
            "--kill-after=1s",
            f"{seconds * 2}s",
            *command,
        ]
        if input_path is not None:
            command_line.insert(2, "--interactive")
        if self.owner_label is not None:
            command_line[2:2] = ["--label", f"padawan.atlas.owner={self.owner_label}"]
        stderr_path = output.with_suffix(".stderr")
        started = time.monotonic()
        process: subprocess.Popen[bytes] | None = None
        try:
            with (
                (input_path or Path(os.devnull)).open("rb") as stdin,
                output.open("wb") as stdout,
                stderr_path.open("wb") as stderr,
            ):
                # Do not send a create/start effect after its deadline. In particular,
                # an immediately cancelled `docker run` can outlive an earlier
                # `docker rm` that observed no container yet.
                if deadline is not None and time.monotonic() >= deadline:
                    return 124, False
                process = subprocess.Popen(
                    command_line, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                assert process.stdout is not None and process.stderr is not None
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, (stdout, output_limit))
                    selector.register(process.stderr, selectors.EVENT_READ, (stderr, stderr_limit))
                    while selector.get_map():
                        if deadline is not None and time.monotonic() >= deadline:
                            return 124, False
                        if time.monotonic() - started > seconds * 2 + 30:
                            raise JudgeInfrastructureError(
                                "Docker command exceeded its startup/execution deadline"
                            )
                        for key, _ in selector.select(timeout=0.1):
                            chunk = os.read(key.fd, 65_536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            destination, limit = key.data
                            remaining = limit - destination.tell()
                            destination.write(chunk[:remaining])
                            if len(chunk) > remaining:
                                return 153, False
                process.wait(timeout=5)
                inspected = subprocess.run(
                    ["docker", "inspect", name, "--format", "{{json .State}}"],
                    capture_output=True,
                    check=False,
                    timeout=10,
                )
                if inspected.returncode != 0:
                    raise JudgeInfrastructureError("candidate container could not be inspected")
                state = json.loads(inspected.stdout)
                if state.get("Error") or not state.get("StartedAt"):
                    raise JudgeInfrastructureError(
                        "Docker could not execute the declared judge command"
                    )
                return process.returncode, bool(state.get("OOMKilled"))
        finally:
            if process is not None:
                # Stop and reap the client before removing its owned container,
                # so an in-progress client cannot start it after removal.
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                cleanup = subprocess.run(
                    ["docker", "rm", "--force", name],
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
                if cleanup.returncode != 0 and b"No such container" not in cleanup.stderr:
                    raise JudgeInfrastructureError(
                        "owned judge container cleanup was not confirmed"
                    )

    def _compile(
        self,
        source: bytes,
        directory: Path,
        *,
        checker: bool = False,
        deadline: float | None = None,
    ) -> int:
        directory.mkdir(mode=0o700)
        (directory / "main.cpp").write_bytes(source)
        if checker:
            shutil.copyfile(self.testlib, directory / "testlib.h")
        code, _ = self._run(
            ["g++", "-O2", "-pipe", "-static", "-s", "-std=gnu++17", "-o", "main", "main.cpp"],
            work=directory,
            writable=True,
            memory=2 * 1024**3,
            seconds=60,
            output=directory.parent / (directory.name + ".compile"),
            output_limit=8 * 1024**2,
            deadline=deadline,
        )
        return code

    def judge(
        self,
        *,
        package: JudgePackage,
        source: str,
        deadline: float,
        failure_evidence: Path | None = None,
    ) -> JudgeResult:
        if not source or len(source.encode()) > 1024**2:
            raise ValueError("candidate code is empty or exceeds 1 MiB")
        started = time.monotonic()
        verdict = "Accepted"
        passed = 0
        with tempfile.TemporaryDirectory(prefix="trial-", dir=self.scratch) as tmp:
            work = Path(tmp)
            if time.monotonic() >= deadline:
                raise JudgeInfrastructureError("trial judge deadline exhausted before compilation")
            if self._compile(
                package.checker.read_bytes(), work / "checker", checker=True, deadline=deadline
            ):
                raise JudgeInfrastructureError("official checker failed compilation")
            if time.monotonic() >= deadline:
                raise JudgeInfrastructureError("trial judge deadline exhausted before compilation")
            compile_code = self._compile(source.encode(), work / "candidate", deadline=deadline)
            if time.monotonic() >= deadline:
                raise JudgeInfrastructureError("trial judge deadline exhausted during compilation")
            if compile_code:
                verdict = "Compile Error"
            else:
                for index, case in enumerate(package.cases, start=1):
                    if time.monotonic() >= deadline:
                        raise JudgeInfrastructureError("trial judge deadline exhausted")
                    out = work / "candidate.stdout"
                    code, oom = self._run(
                        ["/work/main"],
                        work=work / "candidate",
                        writable=False,
                        memory=case.memory_bytes,
                        seconds=case.seconds,
                        output=out,
                        input_path=case.input_path,
                        deadline=deadline,
                    )
                    if time.monotonic() >= deadline:
                        raise JudgeInfrastructureError(
                            "trial judge deadline exhausted during execution"
                        )
                    if oom:
                        verdict = "Memory Limit Exceeded"
                    elif code in {124, 137, 143, 152}:
                        verdict = "Time Limit Exceeded"
                    elif code == 153:
                        verdict = "Output Limit Exceeded"
                    elif code:
                        verdict = "Runtime Error"
                    else:
                        # Give each checker immutable input files in its own mount. Besides
                        # separating cases, this avoids rewriting files under Docker Desktop's
                        # cached bind mounts between rapidly replaced containers.
                        check = work / f"check-{index}"
                        check.mkdir(mode=0o700)
                        shutil.copyfile(work / "checker" / "main", check / "main")
                        (check / "main").chmod(0o700)
                        shutil.copyfile(case.input_path, check / "in.txt")
                        shutil.copyfile(case.answer_path, check / "ans.txt")
                        shutil.copyfile(out, check / "out.txt")
                        checker_code, checker_oom = self._run(
                            ["/work/main", "in.txt", "out.txt", "ans.txt"],
                            work=check,
                            writable=False,
                            memory=256 * 1024**2,
                            seconds=10,
                            output=work / "checker.stdout",
                            output_limit=1024**2,
                            deadline=deadline,
                        )
                        if checker_oom or checker_code not in {0, 1, 2}:
                            if failure_evidence is not None:
                                failure_evidence.mkdir(parents=True, mode=0o700)
                                for name in (
                                    "candidate.stdout",
                                    "checker.stdout",
                                    "checker.stderr",
                                ):
                                    shutil.copyfile(work / name, failure_evidence / name)
                            raise JudgeInfrastructureError(
                                f"official checker failed or rejected its own data: {checker_code}"
                            )
                        if checker_code:
                            verdict = "Wrong Answer"
                        shutil.rmtree(check)
                    if verdict != "Accepted":
                        if failure_evidence is not None:
                            # Researcher-only diagnostics; never a model input.
                            failure_evidence.mkdir(parents=True, mode=0o700)
                            for name in (
                                "candidate.stdout",
                                "candidate.stderr",
                                "checker.stdout",
                                "checker.stderr",
                            ):
                                if (work / name).is_file():
                                    shutil.copyfile(work / name, failure_evidence / name)
                        break
                    passed = index
        return JudgeResult(
            verdict,
            passed,
            len(package.cases),
            time.monotonic() - started,
            package.archive_digest,
            hashlib.sha256(source.encode()).hexdigest(),
            self.image,
            passed + 1 if verdict != "Accepted" else None,
        )


def read_statement_shards(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """Load already authorized Parquet files; never silently replace conflicting duplicates."""
    import pyarrow.parquet as parquet

    rows: dict[str, dict[str, Any]] = {}
    required = {"problem_id", "problem_title", "problem_statement", "difficulty", "platform"}
    for path in sorted(paths):
        for row in parquet.read_table(path).to_pylist():
            if not required <= row.keys() or any(
                not isinstance(row[key], str) or not row[key] for key in required
            ):
                raise ValueError("statement shard has missing problem fields")
            if row["difficulty"] not in {"easy", "medium", "hard"}:
                raise ValueError("unexpected benchmark difficulty category")
            prior = rows.get(row["problem_id"])
            if prior is not None and prior != row:
                raise ValueError("conflicting copies of a benchmark problem")
            rows[row["problem_id"]] = row
    return rows
