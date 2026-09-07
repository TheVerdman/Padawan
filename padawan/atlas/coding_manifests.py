"""Content-addressed coding items and suites; hidden judge identities stay out of prompts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from padawan.atlas.coding_judge import DockerBatchJudge, JudgePackage
from padawan.atlas.contracts import (
    AdapterKind,
    AtlasItemManifest,
    AtlasSuiteManifest,
    DatasetGovernance,
    EvaluationClass,
    Modality,
    ModalityValidationEvidence,
    SuiteStatus,
    content_id,
)
from padawan.models.hashing import sha256_digest

DEFAULT_PROBLEM_LOCK = Path("configs/atlas/nemotron-64k128k-problems-v1.json")


def validate_problem_lock(
    problem_order: list[str], dataset: dict[str, Any], lock: dict[str, Any]
) -> None:
    """Pin the actual statements and judges, rather than only matching problem names."""
    if lock.get("schema_version") != "1" or problem_order != lock.get("problem_order"):
        raise ValueError("problem selection or order differs from the fixed comparison")
    if len(problem_order) != len(set(problem_order)):
        raise ValueError("fixed comparison contains a duplicate problem")
    if any(dataset.get(key) != lock.get(key) for key in ("statement_revision", "test_revision")):
        raise ValueError("benchmark revision differs from the fixed comparison")
    rows = {row["problem_id"]: row for row in dataset["selected"]}
    packages = {row["problem_id"]: row for row in dataset["packages"]}
    pins = lock["problems"]
    if len(pins) != len(problem_order) or [p["problem_id"] for p in pins] != problem_order:
        raise ValueError("fixed comparison manifest has inconsistent membership")
    for pin in pins:
        pid = pin["problem_id"]
        row, package = rows.get(pid), packages.get(pid)
        if row is None or package is None:
            raise ValueError("fixed comparison statement or judge package is missing")
        if hashlib.sha256(row["problem_statement"].encode()).hexdigest() != pin["statement_sha256"]:
            raise ValueError("problem statement changed within the fixed comparison")
        if any(
            package.get(key) != pin.get(key)
            for key in ("archive_sha256", "declared_cases", "retained_cases")
        ):
            raise ValueError("judge package changed within the fixed comparison")


def validate_prepared_problem_lock(inputs: dict[str, Any]) -> None:
    """Recheck the frozen selection and physical archives before paid work or dispatch."""
    if not inputs["launch"].get("explicit_paired_population"):
        return
    receipt = inputs.get("problem_lock")
    if not receipt:
        raise ValueError("explicit paired comparison requires a retained problem lock")
    path = Path(receipt["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
        raise ValueError("comparison problem lock changed after preparation")
    root = Path(inputs["dataset"])
    raw_dataset = (root / "prepared-dataset.json").read_bytes()
    if (
        hashlib.sha256(raw_dataset).hexdigest()
        != inputs["environment_parameters"]["dataset_digest"]
    ):
        raise ValueError("dataset changed after the comparison was prepared")
    lock = json.loads(raw)
    validate_problem_lock(inputs["launch"]["paired_problem_order"], json.loads(raw_dataset), lock)
    if sorted(inputs["ladder_problem_ids"]) != sorted(lock["problem_order"]):
        raise ValueError("prepared ladder differs from the fixed comparison")
    for pin in lock["problems"]:
        archive = root / "archives" / f"{pin['problem_id']}.zip"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["archive_sha256"]:
            raise ValueError("physical judge archive changed after preparation")


def coding_item(
    row: dict[str, Any], package: JudgePackage, judge: DockerBatchJudge
) -> AtlasItemManifest:
    prompt = row["problem_statement"]
    label = row["difficulty"]
    if not isinstance(prompt, str) or not prompt.strip() or label not in {"easy", "medium", "hard"}:
        raise ValueError("coding item requires a complete statement and original difficulty label")
    if row["problem_id"] != package.problem_id:
        raise ValueError("coding statement and judge package identify different problems")
    identity: dict[str, Any] = {
        "family_id": f"livecodebench-pro.{row['platform']}",
        # This is a publisher-label axis, never an invented contest rating or measured ability.
        "difficulty": float({"easy": 0, "medium": 1, "hard": 2}[label]),
        "adapter_kind": AdapterKind.CODING_AGENTIC,
        "modalities": (Modality.TEXT,),
        "prompt_digest": sha256_digest(prompt),
        "verifier_id": "livecodebench-pro.batch",
        "verifier_version": "padawan-1",
        "verifier_payload": {
            "archive_sha256": package.archive_digest,
            "case_count": len(package.cases),
            "declared_case_count": package.declared_case_count,
            "judge_image": judge.image,
        },
        "metadata": {
            "problem_id": row["problem_id"],
            "publisher_difficulty": label,
            "difficulty_axis": "publisher_ordinal_not_calibrated",
            "contamination": "public_pre_model_release_diagnostic",
        },
        "pair_id": None,
        "variant_id": None,
    }
    return AtlasItemManifest(
        **identity,
        prompt=prompt,
        item_id=content_id("atlas-item", identity),
        item_digest=sha256_digest(identity),
    )


def coding_suite(
    *,
    items: tuple[AtlasItemManifest, ...],
    governance: DatasetGovernance,
    task_digest: str,
    corpus_digest: str,
    environment_fingerprint: str,
    text_gate: ModalityValidationEvidence,
    created_at: datetime,
) -> AtlasSuiteManifest:
    if not items or any(item.adapter_kind != AdapterKind.CODING_AGENTIC for item in items):
        raise ValueError("coding suite requires coding items")
    identity: dict[str, Any] = {
        "suite_id": "livecodebench-pro.scout",
        "benchmark_id": governance.benchmark_id,
        "benchmark_version": governance.benchmark_version,
        "split": "development",
        "governance_id": governance.governance_id,
        "adapter_kind": AdapterKind.CODING_AGENTIC,
        "evaluation_class": EvaluationClass.DEVELOPMENT,
        "item_digests": tuple(sorted(item.item_digest for item in items)),
        "modality_gates": (text_gate,),
        "task_manifest_digests": (task_digest,),
        "corpus_digests": (corpus_digest,),
        "environment_fingerprints": (environment_fingerprint,),
        "evaluation_suite_manifest_digest": None,
    }
    # Different frozen populations or environments are distinct immutable suite versions.
    identity["version"] = "padawan-1." + sha256_digest(identity).removeprefix("sha256:")
    return AtlasSuiteManifest(
        **identity,
        title="LiveCodeBench Pro diagnostic coding scout",
        items=items,
        status=SuiteStatus.READY,
        content_digest=sha256_digest(identity),
        created_at=created_at,
    )
