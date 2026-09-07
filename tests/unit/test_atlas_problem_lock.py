"""Fixed statement and judge identity checks; no models, Docker or cloud resources."""

import copy
import hashlib
import json

import pytest

from padawan.atlas.coding_manifests import validate_prepared_problem_lock, validate_problem_lock


@pytest.fixture
def locked_inputs(tmp_path):
    archive = b"local archive identity fixture"
    dataset = {
        "statement_revision": "statements-v1",
        "test_revision": "judges-v1",
        "selected": [{"problem_id": "fixed", "problem_statement": "The exact full problem."}],
        "packages": [
            {
                "problem_id": "fixed",
                "archive_sha256": hashlib.sha256(archive).hexdigest(),
                "declared_cases": 1,
                "retained_cases": 1,
            }
        ],
    }
    lock = {
        "schema_version": "1",
        "problem_order": ["fixed"],
        "statement_revision": "statements-v1",
        "test_revision": "judges-v1",
        "problems": [
            {
                **dataset["packages"][0],
                "statement_sha256": hashlib.sha256(
                    dataset["selected"][0]["problem_statement"].encode()
                ).hexdigest(),
            }
        ],
    }
    (tmp_path / "archives").mkdir()
    (tmp_path / "archives/fixed.zip").write_bytes(archive)
    dataset_path = tmp_path / "prepared-dataset.json"
    dataset_path.write_text(json.dumps(dataset))
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(json.dumps(lock))
    inputs = {
        "launch": {"explicit_paired_population": True, "paired_problem_order": ["fixed"]},
        "dataset": str(tmp_path),
        "ladder_problem_ids": ["fixed"],
        "environment_parameters": {
            "dataset_digest": hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        },
        "problem_lock": {
            "path": str(lock_path),
            "sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        },
    }
    return dataset, lock, inputs


def test_exact_problem_and_physical_judge_pass(locked_inputs):
    _, _, inputs = locked_inputs
    validate_prepared_problem_lock(inputs)


@pytest.mark.parametrize("change", ["statement", "archive", "count", "revision", "order"])
def test_same_problem_name_cannot_hide_an_experimental_change(locked_inputs, change):
    dataset, lock, _ = copy.deepcopy(locked_inputs)
    order = ["fixed"]
    if change == "statement":
        dataset["selected"][0]["problem_statement"] += " Added hint."
    elif change == "archive":
        dataset["packages"][0]["archive_sha256"] = "another judge"
    elif change == "count":
        dataset["packages"][0]["retained_cases"] = 0
    elif change == "revision":
        dataset["statement_revision"] = "new dataset"
    else:
        order = ["another"]
    with pytest.raises(ValueError):
        validate_problem_lock(order, dataset, lock)


@pytest.mark.parametrize("change", ["archive_bytes", "lock_bytes", "missing_lock", "ladder"])
def test_paid_launch_refuses_missing_or_changed_local_inputs(locked_inputs, tmp_path, change):
    _, _, inputs = locked_inputs
    if change == "archive_bytes":
        (tmp_path / "archives/fixed.zip").write_bytes(b"substituted")
    elif change == "lock_bytes":
        (tmp_path / "lock.json").write_text("{}")
    elif change == "missing_lock":
        del inputs["problem_lock"]
    else:
        inputs["ladder_problem_ids"] = ["another"]
    with pytest.raises(ValueError):
        validate_prepared_problem_lock(inputs)
