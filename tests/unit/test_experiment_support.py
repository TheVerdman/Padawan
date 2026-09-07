from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from padawan.atlas import vertex_control
from padawan.atlas.local_campaign import native_counts, write_report
from padawan.atlas.preparation import code_identity
from padawan.models.hashing import sha256_digest
from padawan.orchestration.source_identity import source_identity


def test_source_inventories_preserve_both_selections_and_detect_code_drift(tmp_path):
    paths = [
        "padawan/component.py",
        "scripts/run_atlas_coding.py",
        "scripts/prepare_atlas_coding.py",
        "scripts/prepare_atlas_vertex.py",
        "scripts/preflight_atlas_vertex.py",
        "scripts/control_atlas_vertex.py",
        "scripts/launch_atlas_campaign.py",
        "scripts/other_recipe.py",
    ]
    for path in [*paths, "tests/test_example.py", ".env", "runs/response.json"]:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path)
    expected = {path: hashlib.sha256(path.encode()).hexdigest() for path in paths}
    assert source_identity(tmp_path) == expected
    assert code_identity(tmp_path) == {
        path: digest for path, digest in expected.items() if path != "scripts/other_recipe.py"
    }
    (tmp_path / "padawan/component.py").write_text("changed implementation")
    assert source_identity(tmp_path) != expected
    assert code_identity(tmp_path)["padawan/component.py"] != expected["padawan/component.py"]


@pytest.mark.parametrize("case", ["vertex_source", "local_source", "local_configuration"])
async def test_recipe_refuses_stale_inputs_before_constructing_a_client(
    tmp_path, monkeypatch, case
):
    repo = Path(__file__).resolve().parents[2]
    filename = "run_atlas_coding.py" if case == "vertex_source" else "run_atlas_local.py"
    spec = importlib.util.spec_from_file_location(
        "source_drift_recipe", repo / "scripts" / filename
    )
    assert spec is not None and spec.loader is not None
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)

    def forbidden_client(*args, **kwargs):
        raise AssertionError("a stale source/configuration constructed a model client")

    monkeypatch.setattr(
        entry,
        "VertexRawPredictClient" if case == "vertex_source" else "LocalMetalClient",
        forbidden_client,
    )
    inputs = {"launch": {}, "sources": {}}
    if case == "local_configuration":
        inputs.update(
            sources=source_identity(repo), config_digest=sha256_digest({"original": True})
        )
        (tmp_path / "configuration.json").write_text("{}")
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(inputs))
    with pytest.raises(ValueError, match="code differs|source changed|condition changed"):
        if case == "vertex_source":
            await entry.execute(SimpleNamespace(inputs=path))
        else:
            entry.checked_inputs(tmp_path)


def test_local_report_keeps_unresolved_and_unrun_trials_distinct(tmp_path):
    def write(name, value):
        (tmp_path / name).write_text(json.dumps(value))

    write("inputs.json", {"selected_problem_ids": ["one", "two"], "config": {"repetitions": 1}})
    write("state.json", {"phase": "stopped"})
    write(
        "bundle.json",
        {
            "items": [
                {
                    "item_digest": name,
                    "metadata": {"problem_id": name, "publisher_difficulty": "hard"},
                }
                for name in ("one", "two")
            ],
            "requests": [
                {"request_id": name, "item_digest": name, "trial_index": 0, "sampling": {"seed": 1}}
                for name in ("one", "two")
            ],
        },
    )
    with sqlite3.connect(tmp_path / "atlas.sqlite3") as database:
        database.executescript(
            "CREATE TABLE external_calls (request_id TEXT, status TEXT, result_usage TEXT);"
            "CREATE TABLE artifact_references (owner_id TEXT, owner_type TEXT, artifact_id TEXT);"
            "CREATE TABLE artifacts (artifact_id TEXT, uri TEXT, digest TEXT, size_bytes INTEGER, "
            "media_type TEXT, restricted INTEGER, raw_data INTEGER);"
        )
        database.executemany(
            "INSERT INTO external_calls VALUES (?,?,?)",
            [
                ("one", "pending", None),
                ("preflight", "completed", '{"input_tokens": 4, "output_tokens": 7}'),
            ],
        )
    assert native_counts(tmp_path) == {
        "calls": 2,
        "completed_calls": 1,
        "input_tokens": 4,
        "output_tokens": 7,
        "unresolved": 1,
    }
    write_report(tmp_path)
    import csv

    with (tmp_path / "trials.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert [(row["request_id"], row["status"]) for row in rows] == [
        ("one", "unresolved_effect"),
        ("two", "not_run"),
    ]
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["planned"] == 2 and summary["completed"] == 0
    assert summary["verified_successes"] == 0


@pytest.mark.parametrize("current_pin", [False, True])
def test_controller_pins_implementation_and_launches_original_guard_command(
    tmp_path, monkeypatch, current_pin
):
    control = vertex_control.Control.__new__(vertex_control.Control)
    control.root = tmp_path
    control.config_path = tmp_path / "config.json"
    control.config_digest = "sha256:" + "a" * 64
    control.entrypoint = Path(__file__).resolve().parents[2] / "scripts/control_atlas_vertex.py"
    control.gcloud = "unused-credential-command"
    control.config = dict.fromkeys(
        (
            "stop_dispatch_after_hours",
            "capture_after_hours",
            "begin_teardown_after_hours",
            "maximum_deployment_hours",
        ),
        1,
    )
    updates = []
    monkeypatch.setattr(
        control, "state", lambda: {"endpoint_create_result": {}, "model_upload_result": {}}
    )
    monkeypatch.setattr(control, "update", lambda **values: updates.append(values))
    validation = tmp_path / "operational-validation.json"
    validation.write_text("{}")
    vertex_control.atomic_save(
        tmp_path / "ready-to-deploy.json",
        {
            "passed": True,
            "config_sha256": control.config_digest,
            "controller_sha256": vertex_control.digest(
                vertex_control.__file__ if current_pin else control.entrypoint
            ),
            "operational_validation_sha256": vertex_control.digest(validation),
        },
    )

    class LaunchObservedError(Exception):
        pass

    def inspect_launch(argv, **kwargs):
        assert argv[4] == str(control.entrypoint)
        assert argv[-1] == "guard"
        assert kwargs["start_new_session"] is True
        raise LaunchObservedError

    monkeypatch.setattr(vertex_control.subprocess, "Popen", inspect_launch)
    if current_pin:
        with pytest.raises(LaunchObservedError):
            control.deploy()
        assert len(updates) == 1 and "deployment_started_at" in updates[0]
    else:
        with pytest.raises(ValueError, match="passing validation"):
            control.deploy()
        assert updates == []
        assert not (tmp_path / "guard.log").exists()
