from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from typer.testing import CliRunner

from padawan.artifacts.store import LocalArtifactStore
from padawan.cli.app import app
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.models.contracts import ArtifactRef


def test_cli_database_corpus_and_operations_json(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "cli.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    runner = CliRunner()

    migrated = runner.invoke(app, ["--json", "db", "migrate"])
    assert migrated.exit_code == 0, migrated.output
    assert json.loads(migrated.stdout)["revision"] == "head"

    generated = runner.invoke(
        app,
        [
            "--json",
            "corpus",
            "generate",
            "algebra",
            "--groups-per-family",
            "1",
            "--siblings-per-group",
            "3",
            "--seed",
            "41",
        ],
    )
    assert generated.exit_code == 0, generated.output
    assert json.loads(generated.stdout)["registered"] == 24

    lean_generated = runner.invoke(
        app,
        [
            "--json",
            "corpus",
            "generate",
            "lean-math",
            "--groups-per-family",
            "1",
            "--siblings-per-group",
            "2",
            "--seed",
            "41",
        ],
    )
    assert lean_generated.exit_code == 0, lean_generated.output
    assert json.loads(lean_generated.stdout)["registered"] == 4

    validated = runner.invoke(app, ["--json", "corpus", "validate"])
    inspected = runner.invoke(app, ["--json", "corpus", "inspect", "--limit", "2"])
    operations = runner.invoke(app, ["--json", "report", "operations"])
    assert validated.exit_code == inspected.exit_code == operations.exit_code == 0
    assert json.loads(validated.stdout)["valid"] is True
    assert len(json.loads(inspected.stdout)["items"]) == 2
    assert json.loads(operations.stdout)["format"] == "padawan.operations_report"
    assert len(list((artifact_root / "blobs" / "sha256").glob("*/*"))) == 6


def test_cli_failure_has_nonzero_exit_and_manifest(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "missing.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))

    result = CliRunner().invoke(app, ["--json", "episode", "inspect", "missing"])

    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["error"]["type"] in {"OperationalError", "ProgrammingError"}
    assert payload["manifest"]["digest"].startswith("sha256:")
    manifest_ref = ArtifactRef.model_validate(payload["manifest"], strict=False)
    manifest = json.loads(LocalArtifactStore(artifact_root).read_text(manifest_ref))
    assert manifest["configuration"]["invocation"]["episode_id"] == "missing"


def test_cli_lean_verification_emits_hard_gate(tmp_path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    proof_file = tmp_path / "proof.lean"
    proof_file.write_text("by\n  omega\n", encoding="utf-8")
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))

    class FakeVerifier:
        def verify(self, task) -> VerifierResult:
            assert task.proof == "by\n  omega\n"
            return VerifierResult(
                result_id="lean-result-fake",
                verifier_id="lean4.kernel",
                verifier_version="test",
                scope=task.task_id,
                disposition=VerifierDisposition.VERIFIED,
                deterministic=True,
                summary="Lean kernel accepted the candidate proof",
                evidence={"kernel_executed": True},
                created_at=datetime.now(UTC),
            )

    monkeypatch.setattr("padawan.cli.app._lean_verifier", lambda _settings: FakeVerifier())
    result = CliRunner().invoke(
        app,
        [
            "--json",
            "verify",
            "lean",
            "--statement",
            "True",
            "--proof-file",
            str(proof_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verification"]["disposition"] == "verified"
    assert payload["hard_gate"]["passed"] is True


def test_cli_magellan_assessment_is_read_only_and_redacts_repository_path(
    tmp_path, monkeypatch
) -> None:
    repository = tmp_path / "private-magellan-worktree"
    artifact_root = tmp_path / "artifacts"
    repository.mkdir()
    subprocess.run(("git", "-C", str(repository), "init", "-b", "main"), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "config", "user.name", "Padawan Tests"),
        check=True,
    )
    subprocess.run(
        (
            "git",
            "-C",
            str(repository),
            "config",
            "user.email",
            "padawan@example.invalid",
        ),
        check=True,
    )
    (repository / "README.md").write_text("# Magellan test tree\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(repository), "add", "README.md"), check=True)
    subprocess.run(
        ("git", "-C", str(repository), "commit", "-m", "initial"),
        check=True,
    )
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    monkeypatch.setenv("PADAWAN_MAGELLAN_REPOSITORY_ROOT", str(repository))

    result = CliRunner().invoke(app, ["--json", "verify", "magellan-environment"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["ready"] is False
    assert payload["blockers"] == ["environment handshake is missing"]
    assert str(repository) not in result.stdout
    manifest_ref = ArtifactRef.model_validate(payload["manifest"], strict=False)
    manifest = json.loads(LocalArtifactStore(artifact_root).read_text(manifest_ref))
    serialized_manifest = json.dumps(manifest, sort_keys=True)
    assert str(repository) not in serialized_manifest
    assert manifest["configuration"]["magellan_repository_configured"] is True
    assert manifest["configuration"]["magellan_handshake_configured"] is False
