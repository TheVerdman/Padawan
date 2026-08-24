from __future__ import annotations

import json
import shlex
import subprocess
from datetime import UTC, datetime

from typer.testing import CliRunner

from padawan.artifacts.store import LocalArtifactStore
from padawan.atlas.campaigns import build_first_inkling_campaign_bundle
from padawan.cli.app import app
from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.legal.appellate import (
    AppellateCorpusGenerator,
    AppellateScenarioFamily,
)
from padawan.models.contracts import ArtifactRef
from padawan.models.hashing import sha256_digest
from tests.appellate_helpers import NOW, build_submission
from tests.pprl_helpers import distribution, envelope, program


def test_cli_atlas_offline_plan_verification_and_report(tmp_path, monkeypatch) -> None:
    artifact_root = tmp_path / "artifacts"
    report_path = tmp_path / "capability-atlas-v0.md"
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    runner = CliRunner()

    planned = runner.invoke(app, ["--json", "atlas", "plan"])
    verified = runner.invoke(app, ["--json", "atlas", "verify-offline"])
    rendered = runner.invoke(
        app,
        ["--json", "atlas", "report", "--output", str(report_path)],
    )

    assert planned.exit_code == 0, planned.output
    assert verified.exit_code == 0, verified.output
    assert rendered.exit_code == 0, rendered.output
    plan = json.loads(planned.stdout)
    verification = json.loads(verified.stdout)
    report = json.loads(rendered.stdout)
    assert plan["evidence_lanes"]["locally_reproduced_observations"] == []
    assert plan["live_campaign_plan"]["authorization_required"] is True
    assert verification["external_requests_made"] == 0
    assert verification["promotion_eligible"] is False
    assert report["output"] == str(report_path)
    text = report_path.read_text(encoding="utf-8")
    assert "upstream prior only" in text
    assert "no locally reproduced Inkling capability scores" in text


def test_cli_atlas_campaign_prepare_is_pure_exact_and_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(tmp_path / "artifacts"))

    def forbidden_live_application(*_args, **_kwargs):
        raise AssertionError("campaign preparation must not compose a live application")

    monkeypatch.setattr("padawan.cli.app.build_live_application", forbidden_live_application)
    bundle = build_first_inkling_campaign_bundle()
    runner = CliRunner()
    prepared_arguments: list[str] = []
    for stage in bundle.live_plan.stages:
        command = stage.command.replace(
            "REPLACE_WITH_AUTHORIZATION_REFERENCE", f"approval://atlas-{stage.stage_id}"
        )
        arguments = shlex.split(command)
        assert arguments[0] == "padawan"
        # Invoke the generated command verbatim apart from the installed executable name.
        # Pretty output is still JSON and therefore remains machine-readable.
        prepared = runner.invoke(app, arguments[1:])
        assert prepared.exit_code == 0, prepared.output
        envelope = json.loads(prepared.stdout)
        assert envelope["contract"] == "padawan.atlas.campaign-preparation.v1"
        assert envelope["mode"] == "preparation_only"
        assert envelope["allocation_set"] == stage.stage_id
        assert envelope["execution_gateway_status"] == "unavailable_fail_closed"
        assert envelope["execution_permitted"] is False
        assert envelope["authorization_verified"] is False
        assert envelope["network_calls_made"] == 0
        assert envelope["database_writes"] == 0
        assert envelope["artifact_writes"] == 0
        assert envelope["external_requests_made"] == 0
        assert envelope["external_cost_usd"] == 0.0
        assert envelope["gpu_actions"] == 0
        assert envelope["evidence_artifact_plan"]
        prepared_arguments = arguments[1:]
    assert not (tmp_path / "artifacts").exists()

    stage = bundle.live_plan.stages[-1]
    placeholder = runner.invoke(app, ["--json", *shlex.split(stage.command)[1:]])
    assert placeholder.exit_code == 1
    assert "placeholder" in placeholder.output

    drifted_arguments = list(prepared_arguments)
    request_index = drifted_arguments.index("--max-requests") + 1
    drifted_arguments[request_index] = str(stage.max_requests - 1)
    drifted = runner.invoke(app, ["--json", *drifted_arguments])
    assert drifted.exit_code == 1
    assert "exact predeclared" in drifted.output

    run = runner.invoke(app, ["--json", "atlas", "campaign", "run"])
    assert run.exit_code == 1
    assert "execution is unavailable and fails closed" in run.output


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

    appellate_generated = runner.invoke(
        app,
        [
            "--json",
            "corpus",
            "generate",
            "appellate",
            "--groups-per-family",
            "1",
            "--siblings-per-group",
            "2",
            "--seed",
            "41",
        ],
    )
    assert appellate_generated.exit_code == 0, appellate_generated.output
    appellate_payload = json.loads(appellate_generated.stdout)
    assert appellate_payload["registered"] == 6
    assert appellate_payload["currentness_capability"] == "unknown_without_citator"

    validated = runner.invoke(app, ["--json", "corpus", "validate"])
    inspected = runner.invoke(app, ["--json", "corpus", "inspect", "--limit", "2"])
    operations = runner.invoke(app, ["--json", "report", "operations"])
    assert validated.exit_code == inspected.exit_code == operations.exit_code == 0
    assert json.loads(validated.stdout)["valid"] is True
    assert len(json.loads(inspected.stdout)["items"]) == 2
    assert json.loads(operations.stdout)["format"] == "padawan.operations_report"
    assert len(list((artifact_root / "blobs" / "sha256").glob("*/*"))) == 7


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


def test_cli_terminal_research_result_has_nonzero_exit_and_failed_manifest(
    tmp_path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))

    async def failed_research_run(**_kwargs):
        return {
            "closed_episodes": 1,
            "runs": [
                {
                    "run_id": "run-provider-failure",
                    "state": "FAILED_TERMINAL",
                    "last_error": {"error_class": "ModelProviderError"},
                }
            ],
            "stop_reason": "terminal_infrastructure_problem",
        }

    monkeypatch.setattr("padawan.cli.app._live_research_run", failed_research_run)
    result = CliRunner().invoke(app, ["--json", "experiment", "run"])

    assert result.exit_code == 1
    payload = json.loads(result.stderr)
    assert payload["error"]["type"] == "TerminalRunFailure"
    assert payload["runs"][0]["state"] == "FAILED_TERMINAL"
    manifest_ref = ArtifactRef.model_validate(payload["manifest"], strict=False)
    manifest = json.loads(LocalArtifactStore(artifact_root).read_text(manifest_ref))
    assert manifest["status"] == "failed"
    assert manifest["error"]["type"] == "TerminalRunFailure"
    assert manifest["result"]["runs"][0]["run_id"] == "run-provider-failure"


def test_cli_compiles_and_verifies_internal_training_bundle(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "training.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    runner = CliRunner()

    assert runner.invoke(app, ["--json", "db", "migrate"]).exit_code == 0
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
            "71",
        ],
    )
    assert generated.exit_code == 0, generated.output
    compiled = runner.invoke(app, ["--json", "training", "compile"])
    assert compiled.exit_code == 0, compiled.output
    payload = json.loads(compiled.stdout)
    assert payload["internal_only"] is True
    assert payload["included_counts"]["evidence_ledger"] == 24
    bundle_id = payload["bundle_id"]

    verified = runner.invoke(app, ["--json", "training", "verify", bundle_id])
    inspected = runner.invoke(app, ["--json", "training", "inspect", bundle_id])
    assert verified.exit_code == 0, verified.output
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(verified.stdout)["valid"] is True
    assert json.loads(inspected.stdout)["bundle_id"] == bundle_id


def test_cli_registers_and_audits_pprl_amber_control_plane(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "pprl.sqlite3"
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_DATABASE_URL", f"sqlite+aiosqlite:///{database_path}")
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    process_distribution = distribution()
    distribution_digest = sha256_digest(process_distribution)
    process_program = program(distribution_digest)
    program_digest = sha256_digest(process_program)
    authorization = envelope(
        program_digest=program_digest,
        distribution_digest=distribution_digest,
    )
    manifest_file = tmp_path / "distribution.json"
    program_file = tmp_path / "program.json"
    envelope_file = tmp_path / "envelope.json"
    manifest_file.write_text(process_distribution.model_dump_json(), encoding="utf-8")
    program_file.write_text(process_program.model_dump_json(), encoding="utf-8")
    envelope_file.write_text(authorization.model_dump_json(), encoding="utf-8")
    runner = CliRunner()

    assert runner.invoke(app, ["--json", "db", "migrate"]).exit_code == 0
    registered_distribution = runner.invoke(
        app,
        [
            "--json",
            "pprl",
            "distribution",
            "register",
            "--manifest-file",
            str(manifest_file),
        ],
    )
    registered_program = runner.invoke(
        app,
        ["--json", "pprl", "program", "register", "--program-file", str(program_file)],
    )
    prepared = runner.invoke(
        app,
        [
            "--json",
            "pprl",
            "amber",
            "prepare",
            "--envelope-file",
            str(envelope_file),
            "--actor-id",
            "preparer",
        ],
    )
    assert registered_distribution.exit_code == 0, registered_distribution.output
    assert registered_program.exit_code == 0, registered_program.output
    assert prepared.exit_code == 0, prepared.output
    authorization_digest = json.loads(prepared.stdout)["authorization_digest"]
    assert authorization_digest == authorization.digest

    authorized = runner.invoke(
        app,
        [
            "--json",
            "pprl",
            "amber",
            "transition",
            authorization_digest,
            "--to-status",
            "authorized",
            "--actor-id",
            "reviewer-a",
            "--reason",
            "independent test review",
            "--evidence-ref",
            "review:test",
        ],
    )
    active = runner.invoke(
        app,
        [
            "--json",
            "pprl",
            "amber",
            "transition",
            authorization_digest,
            "--to-status",
            "active",
            "--actor-id",
            "operator",
            "--reason",
            "activate reviewed boundary",
        ],
    )
    inspected = runner.invoke(
        app,
        ["--json", "pprl", "amber", "inspect", authorization_digest],
    )
    assert authorized.exit_code == 0, authorized.output
    assert active.exit_code == 0, active.output
    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.stdout)
    assert payload["status"] == "active"
    assert [event["to_status"] for event in payload["history"]] == [
        "prepared",
        "authorized",
        "active",
    ]


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


def test_cli_appellate_verification_preserves_semantic_and_currentness_unknown(
    tmp_path, monkeypatch
) -> None:
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setenv("PADAWAN_ARTIFACT_ROOT", str(artifact_root))
    scenario = AppellateCorpusGenerator().scenario(
        family=AppellateScenarioFamily.AMBIGUOUS_VIDEO,
        seed=79,
        split="curriculum",
        created_at=NOW,
    )
    submission = build_submission(scenario)
    scenario_file = tmp_path / "scenario.json"
    submission_file = tmp_path / "submission.json"
    scenario_file.write_text(scenario.model_dump_json(), encoding="utf-8")
    submission_file.write_text(submission.model_dump_json(), encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "--json",
            "verify",
            "appellate",
            "--scenario-file",
            str(scenario_file),
            "--submission-file",
            str(submission_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert all(gate["passed"] for gate in payload["hard_gates"])
    dispositions = {
        item["verifier_id"]: item["disposition"] for item in payload["verifier_results"]
    }
    assert dispositions["appellate.proposition_support"] == "unknown"
    assert dispositions["appellate.currentness"] == "unknown"


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
