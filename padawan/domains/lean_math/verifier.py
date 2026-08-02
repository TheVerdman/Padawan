from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import resource
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from padawan.domains.contracts import (
    EnvironmentSnapshot,
    VerifierDisposition,
    VerifierResult,
)
from padawan.domains.lean_math.corpus import (
    LEAN_TOOLCHAIN,
    LEAN_VERIFIER_VERSION,
    MATHLIB_REVISION,
    MATHLIB_VERSION,
)
from padawan.models.contracts import NonEmpty, StrictRecord
from padawan.models.hashing import sha256_digest

LeanSandboxMode = Literal["required", "best_effort", "off"]

_FORBIDDEN_PROOF_TOKEN = re.compile(
    r"(?im)(?<![\w'])(?:"
    r"#\w+|import\b|set_option\b|run_tac\b|run_term_elab\b|elab\b|macro\b|"
    r"syntax\b|unsafe\b|axiom\b|opaque\b|theorem\b|lemma\b|def\b|abbrev\b|"
    r"namespace\b|section\b|end\b|open\b|include\b|omit\b|attribute\b|"
    r"initialize\b|builtin_initialize\b|IO\b|System\b|dbg_trace\b|trace\b|"
    r"include_str\b|include_bytes\b|native_decide\b|sorryAx\b|sorry\b|admit\b|"
    r"Lean\.(?:Elab|Meta|Environment|CoreM|MetaM|ElabM|TermElabM|CommandElabM)\b"
    r")(?![\w'])"
)
_INFRASTRUCTURE_MARKERS = (
    "unknown module prefix",
    "object file",
    "olean",
    "no such file or directory",
    "unknown package",
    "failed to load",
    "invalid lake configuration",
    "sandbox_init",
    "sandbox_apply",
    "failed to resolve symbolic links when locating application",
)


class LeanProofTask(StrictRecord):
    task_id: NonEmpty
    statement: NonEmpty
    proof: NonEmpty


class LeanVerifierConfigurationError(RuntimeError):
    """The pinned verifier environment cannot provide an authoritative result."""


class LeanInputPolicyError(ValueError):
    """A candidate escaped the deliberately narrow proof-term surface."""


@dataclass(frozen=True)
class _ExecutionResult:
    return_code: int
    stdout: bytes
    stderr: bytes
    duration_ms: float
    timed_out: bool
    output_limited: bool
    sandboxed: bool
    memory_limit_enforced: bool


class LeanVerifier:
    """Run untrusted tactic proofs against a pinned Lean kernel and Mathlib closure.

    The generated source fixes imports and theorem declaration. Candidate text is
    limited to a tactic proof, receives an allowlisted environment, and is placed
    behind an OS sandbox by default. Kernel success is the hard authority; parser
    policy, timeouts, and environment failures remain distinct evidence.
    """

    verifier_id = "lean4.kernel"
    verifier_version = LEAN_VERIFIER_VERSION

    def __init__(
        self,
        *,
        project_root: Path,
        lake_executable: Path,
        elan_home: Path,
        sandbox_mode: LeanSandboxMode = "required",
        timeout_seconds: float = 20.0,
        output_limit_bytes: int = 262_144,
        memory_limit_mb: int = 4_096,
        sandbox_executable: Path = Path("/usr/bin/sandbox-exec"),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Lean timeout must be positive")
        if output_limit_bytes < 4_096:
            raise ValueError("Lean output limit must be at least 4096 bytes")
        if memory_limit_mb < 512:
            raise ValueError("Lean memory limit must be at least 512 MiB")
        self.project_root = project_root.expanduser().resolve()
        self.lake_executable = lake_executable.expanduser().resolve()
        self.elan_home = elan_home.expanduser().resolve()
        self.sandbox_mode = sandbox_mode
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.memory_limit_mb = memory_limit_mb
        self.sandbox_executable = sandbox_executable
        self._snapshot: EnvironmentSnapshot | None = None
        self._lean_executable: Path | None = None
        self._lean_path: str | None = None
        self._lean_path_entries: tuple[Path, ...] = ()

    def environment_snapshot(self) -> EnvironmentSnapshot:
        if self._snapshot is not None:
            return self._snapshot
        self._validate_paths()
        toolchain_path = self.project_root / "lean-toolchain"
        lakefile_path = self.project_root / "lakefile.toml"
        manifest_path = self.project_root / "lake-manifest.json"
        toolchain = toolchain_path.read_text(encoding="utf-8").strip()
        if toolchain != LEAN_TOOLCHAIN:
            raise LeanVerifierConfigurationError(
                f"Lean toolchain must be pinned to {LEAN_TOOLCHAIN}"
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LeanVerifierConfigurationError("Lean lake manifest is unreadable") from exc
        if not isinstance(manifest, dict):
            raise LeanVerifierConfigurationError("Lean lake manifest must be an object")
        mathlib = next(
            (
                package
                for package in manifest.get("packages", [])
                if isinstance(package, dict) and package.get("name") == "mathlib"
            ),
            None,
        )
        if not isinstance(mathlib, dict):
            raise LeanVerifierConfigurationError("Lean lake manifest has no Mathlib package")
        if mathlib.get("inputRev") != MATHLIB_VERSION or mathlib.get("rev") != MATHLIB_REVISION:
            raise LeanVerifierConfigurationError("Mathlib revision differs from the verifier pin")
        self._resolve_runtime_environment()
        lean_version = self._read_lean_version()
        if "version 4.32.2" not in lean_version:
            raise LeanVerifierConfigurationError("Lean executable differs from toolchain pin")
        sandbox_available = self._sandbox_available()
        if self._lean_executable is None:
            raise LeanVerifierConfigurationError("Lean executable has not been resolved")
        lean_executable_digest = _file_sha256(self._lean_executable)
        olean_digest, olean_count, olean_bytes = _olean_closure_digest(self._lean_path_entries)
        fingerprint = sha256_digest(
            {
                "toolchain": toolchain,
                "lakefile": lakefile_path.read_text(encoding="utf-8"),
                "manifest": manifest,
                "lean_version": lean_version,
                "platform": platform.system(),
                "machine": platform.machine(),
                "sandbox_mode": self.sandbox_mode,
                "sandbox_available": sandbox_available,
                "lean_executable_digest": lean_executable_digest,
                "olean_closure_digest": olean_digest,
            }
        )
        self._snapshot = EnvironmentSnapshot(
            environment_id=f"lean-env-{fingerprint[7:23]}",
            domain_id="math.lean",
            domain_version="1.0.0",
            fingerprint=fingerprint,
            dependencies={
                "lean": "4.32.2",
                "lean_toolchain": LEAN_TOOLCHAIN,
                "mathlib": MATHLIB_VERSION,
                "mathlib_revision": MATHLIB_REVISION,
                "lean_executable_digest": lean_executable_digest,
                "olean_closure_digest": olean_digest,
            },
            tool_surface=(
                {
                    "name": self.verifier_id,
                    "command": "lean --json <candidate>",
                    "imports": ["Mathlib"],
                },
            ),
            network_enabled=not sandbox_available,
            metadata={
                "platform": platform.system(),
                "machine": platform.machine(),
                "lean_version_output": lean_version,
                "sandbox_mode": self.sandbox_mode,
                "network_isolation_enforced": sandbox_available,
                "olean_file_count": olean_count,
                "olean_total_bytes": olean_bytes,
            },
            created_at=datetime.now(UTC),
        )
        return self._snapshot

    def verify(self, task: LeanProofTask) -> VerifierResult:
        try:
            snapshot = self.environment_snapshot()
        except (OSError, subprocess.SubprocessError, LeanVerifierConfigurationError) as exc:
            return self._configuration_failure(task, exc)
        try:
            source = self.render_source(task)
        except LeanInputPolicyError as exc:
            return VerifierResult(
                verifier_id=self.verifier_id,
                verifier_version=self.verifier_version,
                scope=task.task_id,
                disposition=VerifierDisposition.REJECTED,
                deterministic=True,
                summary="candidate rejected by the Lean proof-input policy",
                evidence={
                    "stage": "input_policy",
                    "reason": str(exc),
                    "environment_fingerprint": snapshot.fingerprint,
                    "statement_digest": sha256_digest(task.statement),
                    "proof_digest": sha256_digest(task.proof),
                    "kernel_executed": False,
                },
                created_at=datetime.now(UTC),
            )
        source_digest = sha256_digest(source)
        try:
            execution, diagnostics, unparsed_stdout, normalized_stderr = self._execute(source)
        except (OSError, subprocess.SubprocessError, LeanVerifierConfigurationError) as exc:
            return self._configuration_failure(
                task,
                exc,
                environment_fingerprint=snapshot.fingerprint,
                source_digest=source_digest,
            )
        disposition = self._disposition(
            execution=execution,
            diagnostics=diagnostics,
            stderr=normalized_stderr,
        )
        summary = {
            VerifierDisposition.VERIFIED: "Lean kernel accepted the candidate proof",
            VerifierDisposition.REJECTED: "Lean kernel rejected the candidate proof",
            VerifierDisposition.INFRASTRUCTURE_FAILURE: (
                "Lean verification did not reach an authoritative kernel result"
            ),
            VerifierDisposition.UNKNOWN: "Lean verification outcome is unknown",
        }[disposition]
        sandbox_failed = "sandbox-exec: sandbox_" in normalized_stderr.casefold()
        sandbox_applied = execution.sandboxed and not sandbox_failed
        return VerifierResult(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            scope=task.task_id,
            disposition=disposition,
            deterministic=True,
            summary=summary,
            evidence={
                "stage": "kernel",
                "environment_fingerprint": snapshot.fingerprint,
                "environment_id": snapshot.environment_id,
                "statement_digest": sha256_digest(task.statement),
                "proof_digest": sha256_digest(task.proof),
                "source_digest": source_digest,
                "kernel_executed": not sandbox_failed,
                "exit_code": execution.return_code,
                "timed_out": execution.timed_out,
                "output_limited": execution.output_limited,
                "duration_ms": round(execution.duration_ms, 3),
                "sandbox_mode": self.sandbox_mode,
                "sandbox_requested": execution.sandboxed,
                "sandboxed": sandbox_applied,
                "network_isolation_enforced": sandbox_applied,
                "filesystem_writes_confined": sandbox_applied,
                "sensitive_home_paths_denied": sandbox_applied,
                "memory_limit_mb": self.memory_limit_mb,
                "memory_limit_enforced": execution.memory_limit_enforced,
                "diagnostics": diagnostics,
                "unparsed_stdout": unparsed_stdout,
                "stderr": normalized_stderr,
                "command": ["lean", "--json", "<candidate.lean>"],
            },
            created_at=datetime.now(UTC),
        )

    def render_source(self, task: LeanProofTask) -> str:
        _validate_statement(task.statement)
        _validate_proof(task.proof)
        theorem_suffix = sha256_digest(
            {"task_id": task.task_id, "statement": task.statement, "proof": task.proof}
        )[7:23]
        indented_proof = "\n".join(f"  {line}" for line in task.proof.strip().splitlines())
        return (
            "import Mathlib\n\n"
            "set_option autoImplicit false\n\n"
            "namespace Padawan.Generated\n\n"
            f"theorem candidate_{theorem_suffix} : ({task.statement}) :=\n"
            f"{indented_proof}\n\n"
            "end Padawan.Generated\n"
        )

    def _validate_paths(self) -> None:
        required = (
            self.project_root / "lean-toolchain",
            self.project_root / "lakefile.toml",
            self.project_root / "lake-manifest.json",
        )
        if not self.project_root.is_dir() or not all(path.is_file() for path in required):
            raise LeanVerifierConfigurationError("Lean project is incomplete")
        if not self.lake_executable.is_file() or not os.access(self.lake_executable, os.X_OK):
            raise LeanVerifierConfigurationError("configured Lake executable is unavailable")
        if not self.elan_home.is_dir():
            raise LeanVerifierConfigurationError("configured ELAN_HOME is unavailable")

    def _read_lean_version(self) -> str:
        if self._lean_executable is None:
            raise LeanVerifierConfigurationError("Lean executable has not been resolved")
        with tempfile.TemporaryDirectory(prefix="padawan-lean-version-") as temporary:
            root = Path(temporary)
            (root / "home").mkdir()
            try:
                completed = subprocess.run(
                    [str(self._lean_executable), "--version"],
                    cwd=self.project_root,
                    env=self._environment(root),
                    capture_output=True,
                    check=False,
                    timeout=min(self.timeout_seconds, 30.0),
                )
            except subprocess.TimeoutExpired as exc:
                raise LeanVerifierConfigurationError("Lean version probe timed out") from exc
        if completed.returncode != 0:
            raise LeanVerifierConfigurationError("Lean version probe failed")
        value = completed.stdout.decode("utf-8", errors="replace").strip()
        if not value:
            raise LeanVerifierConfigurationError("Lean version probe returned no identity")
        return value

    def _resolve_runtime_environment(self) -> None:
        lean_path = self._lake_printenv("LEAN_PATH")
        entries = [Path(value).expanduser().resolve() for value in lean_path.split(os.pathsep)]
        if not entries or not any(entry.is_dir() for entry in entries):
            raise LeanVerifierConfigurationError("Lake returned an empty LEAN_PATH")
        if any(
            not (
                _is_relative_to(entry, self.project_root) or _is_relative_to(entry, self.elan_home)
            )
            for entry in entries
        ):
            raise LeanVerifierConfigurationError("Lake returned a LEAN_PATH outside pinned roots")
        sysroot = Path(self._lake_printenv("LEAN_SYSROOT")).expanduser().resolve()
        executable = sysroot / "bin" / "lean"
        if not _is_relative_to(executable, self.elan_home):
            raise LeanVerifierConfigurationError("Lean sysroot is outside the pinned ELAN_HOME")
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise LeanVerifierConfigurationError("pinned Lean executable is unavailable")
        self._lean_path = os.pathsep.join(str(entry) for entry in entries)
        self._lean_path_entries = tuple(entries)
        self._lean_executable = executable

    def _lake_printenv(self, variable: str) -> str:
        with tempfile.TemporaryDirectory(prefix="padawan-lake-env-") as temporary:
            root = Path(temporary)
            (root / "home").mkdir()
            try:
                completed = subprocess.run(
                    [str(self.lake_executable), "env", "printenv", variable],
                    cwd=self.project_root,
                    env=self._environment(root),
                    capture_output=True,
                    check=False,
                    timeout=min(self.timeout_seconds, 30.0),
                )
            except subprocess.TimeoutExpired as exc:
                raise LeanVerifierConfigurationError(
                    f"Lake environment probe timed out for {variable}"
                ) from exc
        value = completed.stdout.decode("utf-8", errors="replace").strip()
        if completed.returncode != 0 or not value:
            raise LeanVerifierConfigurationError(f"Lake did not provide {variable}")
        return value

    def _execute(self, source: str) -> tuple[_ExecutionResult, list[dict[str, object]], str, str]:
        with tempfile.TemporaryDirectory(prefix="padawan-lean-proof-") as temporary:
            root = Path(temporary)
            home = root / "home"
            home.mkdir()
            source_path = root / "candidate.lean"
            stdout_path = root / "stdout.jsonl"
            stderr_path = root / "stderr.txt"
            source_path.write_text(source, encoding="utf-8")
            if self._lean_executable is None or self._lean_path is None:
                raise LeanVerifierConfigurationError("Lean runtime environment is unresolved")
            command = [
                str(self._lean_executable),
                "--json",
                str(source_path),
            ]
            sandboxed = self._sandbox_available()
            if sandboxed:
                profile_path = root / "sandbox.sb"
                profile_path.write_text(self._sandbox_profile(root), encoding="utf-8")
                command = [
                    str(self.sandbox_executable),
                    "-f",
                    str(profile_path),
                    *command,
                ]
            execution = self._run_process(
                command,
                environment=self._environment(root),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                sandboxed=sandboxed,
            )
            stdout = _normalize_output(
                execution.stdout,
                source_path=source_path,
                temporary_root=root,
                project_root=self.project_root,
                elan_home=self.elan_home,
            )
            stderr = _normalize_output(
                execution.stderr,
                source_path=source_path,
                temporary_root=root,
                project_root=self.project_root,
                elan_home=self.elan_home,
            )
            diagnostics, unparsed = _parse_diagnostics(stdout)
            return execution, diagnostics, unparsed, stderr

    def _run_process(
        self,
        command: list[str],
        *,
        environment: dict[str, str],
        stdout_path: Path,
        stderr_path: Path,
        sandboxed: bool,
    ) -> _ExecutionResult:
        started = time.monotonic()
        timed_out = False
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                start_new_session=True,
                preexec_fn=self._resource_limits,
            )
            try:
                return_code = process.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                return_code = process.wait()
        duration_ms = (time.monotonic() - started) * 1000.0
        stdout = stdout_path.read_bytes()[: self.output_limit_bytes + 1]
        stderr = stderr_path.read_bytes()[: self.output_limit_bytes + 1]
        output_limited = (
            len(stdout) >= self.output_limit_bytes
            or len(stderr) >= self.output_limit_bytes
            or return_code == -signal.SIGXFSZ
        )
        return _ExecutionResult(
            return_code=return_code,
            stdout=stdout[: self.output_limit_bytes],
            stderr=stderr[: self.output_limit_bytes],
            duration_ms=duration_ms,
            timed_out=timed_out,
            output_limited=output_limited,
            sandboxed=sandboxed,
            memory_limit_enforced=platform.system() != "Darwin",
        )

    def _resource_limits(self) -> None:
        cpu_seconds = max(1, math.ceil(self.timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        # Darwin reports an unlimited RLIMIT_AS but rejects attempts to lower it.
        # Linux provides a real address-space ceiling; the evidence record states
        # whether this platform enforced the configured memory bound.
        if platform.system() != "Darwin":
            memory_bytes = self.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self.output_limit_bytes, self.output_limit_bytes),
        )
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))

    def _environment(self, temporary_root: Path) -> dict[str, str]:
        path = os.pathsep.join(
            (
                str(self.lake_executable.parent),
                str(self.elan_home / "bin"),
                "/usr/bin",
                "/bin",
                "/usr/sbin",
                "/sbin",
            )
        )
        return {
            "PATH": path,
            "ELAN_HOME": str(self.elan_home),
            "HOME": str(temporary_root / "home"),
            "TMPDIR": str(temporary_root),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            **({"LEAN_PATH": self._lean_path} if self._lean_path is not None else {}),
        }

    def _sandbox_available(self) -> bool:
        available = (
            platform.system() == "Darwin"
            and self.sandbox_executable.is_file()
            and os.access(self.sandbox_executable, os.X_OK)
        )
        if self.sandbox_mode == "required" and not available:
            raise LeanVerifierConfigurationError(
                "required macOS sandbox-exec boundary is unavailable"
            )
        return available and self.sandbox_mode != "off"

    def _sandbox_profile(self, temporary_root: Path) -> str:
        home = Path.home()
        sensitive: tuple[Path, ...] = (
            home / ".aws",
            home / ".codex",
            home / ".config" / "gcloud",
            home / ".config" / "gh",
            home / ".docker",
            home / ".gnupg",
            home / ".kube",
            home / ".ssh",
            home / "Library" / "Keychains",
        )
        selected_env_file = os.environ.get("PADAWAN_ENV_FILE")
        selected_rule = ""
        if selected_env_file:
            selected_path = str(Path(selected_env_file).expanduser().resolve())
            selected_rule = f"(deny file-read* (literal {json.dumps(selected_path)}))\n"
        sensitive_rules = "\n".join(
            f"(deny file-read* (subpath {json.dumps(str(path))}))" for path in sensitive
        )
        return (
            "(version 1)\n"
            "(allow default)\n"
            "(deny network*)\n"
            "(deny file-write*)\n"
            f"(allow file-write* (subpath {json.dumps(str(temporary_root))}))\n"
            f"{sensitive_rules}\n"
            f"{selected_rule}"
            '(deny file-read* (regex #"/\\.env($|\\.)"))\n'
            '(deny file-read* (regex #"/\\.(netrc|npmrc|pypirc)$"))\n'
            '(deny file-read* (regex #"/(application_default_credentials|credentials)\\.json$"))\n'
        )

    def _disposition(
        self,
        *,
        execution: _ExecutionResult,
        diagnostics: list[dict[str, object]],
        stderr: str,
    ) -> VerifierDisposition:
        if execution.timed_out or execution.output_limited or execution.return_code < 0:
            return VerifierDisposition.INFRASTRUCTURE_FAILURE
        combined = "\n".join(
            [str(diagnostic.get("message", "")) for diagnostic in diagnostics] + [stderr]
        ).casefold()
        if any(marker in combined for marker in _INFRASTRUCTURE_MARKERS):
            return VerifierDisposition.INFRASTRUCTURE_FAILURE
        if execution.return_code == 0 and not any(
            diagnostic.get("severity") == "error" for diagnostic in diagnostics
        ):
            return VerifierDisposition.VERIFIED
        return VerifierDisposition.REJECTED

    def _configuration_failure(
        self,
        task: LeanProofTask,
        error: BaseException,
        *,
        environment_fingerprint: str | None = None,
        source_digest: str | None = None,
    ) -> VerifierResult:
        evidence: dict[str, object] = {
            "stage": "environment",
            "reason": str(error),
            "error_type": type(error).__name__,
            "statement_digest": sha256_digest(task.statement),
            "proof_digest": sha256_digest(task.proof),
            "kernel_executed": False,
        }
        if environment_fingerprint is not None:
            evidence["environment_fingerprint"] = environment_fingerprint
        if source_digest is not None:
            evidence["source_digest"] = source_digest
        return VerifierResult(
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            scope=task.task_id,
            disposition=VerifierDisposition.INFRASTRUCTURE_FAILURE,
            deterministic=True,
            summary="Lean verifier environment is unavailable or invalid",
            evidence=evidence,
            created_at=datetime.now(UTC),
        )


def _validate_statement(statement: str) -> None:
    if len(statement.encode("utf-8")) > 16_384:
        raise LeanInputPolicyError("statement exceeds 16384 bytes")
    if any(token in statement for token in ("\n", "\r", "\x00", ":=", "--", "/-", "-/", "#")):
        raise LeanInputPolicyError("statement contains a forbidden command boundary")
    if not _balanced_delimiters(statement):
        raise LeanInputPolicyError("statement delimiters are unbalanced")
    if _FORBIDDEN_PROOF_TOKEN.search(statement):
        raise LeanInputPolicyError("statement contains a command or effectful token")


def _validate_proof(proof: str) -> None:
    if len(proof.encode("utf-8")) > 32_768:
        raise LeanInputPolicyError("proof exceeds 32768 bytes")
    stripped = proof.strip()
    if not re.match(r"^by(?:\s|$)", stripped):
        raise LeanInputPolicyError("proof must be a tactic term beginning with `by`")
    if any(token in proof for token in ("\x00", "--", "/-", "-/", "```")):
        raise LeanInputPolicyError("proof contains comments, NUL, or a markdown fence")
    if _FORBIDDEN_PROOF_TOKEN.search(proof):
        raise LeanInputPolicyError("proof contains a command or effectful token")


def _balanced_delimiters(value: str) -> bool:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for character in value:
        if character in "([{":
            stack.append(character)
        elif character in pairs and (not stack or stack.pop() != pairs[character]):
            return False
    return not stack


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _normalize_output(
    value: bytes,
    *,
    source_path: Path,
    temporary_root: Path,
    project_root: Path,
    elan_home: Path,
) -> str:
    normalized = value.decode("utf-8", errors="replace")
    replacements = (
        (str(source_path), "<candidate.lean>"),
        (str(temporary_root), "<lean-temp>"),
        (str(project_root), "<lean-project>"),
        (str(elan_home), "<elan-home>"),
    )
    for original, replacement in replacements:
        normalized = normalized.replace(original, replacement)
    return normalized.strip()


def _parse_diagnostics(stdout: str) -> tuple[list[dict[str, object]], str]:
    diagnostics: list[dict[str, object]] = []
    unparsed: list[str] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except ValueError:
            unparsed.append(line)
            continue
        if not isinstance(payload, dict):
            unparsed.append(line)
            continue
        position = _object_dict(payload.get("pos"))
        end_position = _object_dict(payload.get("endPos"))
        message = payload.get("message") or payload.get("data") or ""
        diagnostics.append(
            {
                "severity": str(payload.get("severity", "unknown")),
                "message": message if isinstance(message, str) else json.dumps(message),
                "line": position.get("line"),
                "column": position.get("column"),
                "end_line": end_position.get("line"),
                "end_column": end_position.get("column"),
            }
        )
    return diagnostics, "\n".join(unparsed)


def _object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _file_sha256(path: Path) -> str:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise LeanVerifierConfigurationError("Lean dependency changed while being fingerprinted")
    return f"sha256:{digest.hexdigest()}"


def _olean_closure_digest(entries: tuple[Path, ...]) -> tuple[str, int, int]:
    closure = hashlib.sha256()
    count = 0
    total_bytes = 0
    for root_index, root in enumerate(entries):
        if not root.is_dir():
            continue
        files = sorted(root.rglob("*.olean"), key=lambda path: path.as_posix())
        for path in files:
            if not path.is_file():
                continue
            size = path.stat().st_size
            relative = path.relative_to(root).as_posix()
            digest = _file_sha256(path)
            closure.update(f"{root_index}\0{relative}\0{size}\0{digest}\n".encode())
            count += 1
            total_bytes += size
    if count == 0:
        raise LeanVerifierConfigurationError("Lean environment has no compiled OLean closure")
    return f"sha256:{closure.hexdigest()}", count, total_bytes
