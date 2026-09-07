"""Freeze the already-authorized local endurance condition without starting inference."""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from run_atlas_coding import component, limit
from transformers import AutoTokenizer

from padawan.adapters.base import GenerationRequest
from padawan.atlas.coding_judge import file_sha256
from padawan.atlas.coding_tool_contracts import (
    CompilerPolicyV2,
    compiler_tools,
    compiler_wire_identity,
)
from padawan.atlas.coding_tools import count_tool_input
from padawan.atlas.local_host import atomic_save, load
from padawan.models.contracts import SamplingConfiguration
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import HarnessProfile, ModelServingIdentity

REPO = Path(__file__).resolve().parents[1]


def source_identity() -> dict[str, str]:
    paths = sorted((REPO / "padawan").rglob("*.py")) + sorted((REPO / "scripts").glob("*.py"))
    return {str(path.relative_to(REPO)): file_sha256(path) for path in paths}


def runtime_inventory(config: dict) -> dict:
    lab = Path(config["lab_path"])
    upstream = lab / ".upstreams/vllm-metal"
    python = upstream / ".venv-vllm-metal/bin/python"
    head = subprocess.check_output(
        ["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "-C", str(upstream), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip()
    if head != config["vllm_metal_revision"] or dirty:
        raise ValueError("local serving source is not the pinned clean runtime")
    expression = (
        "import importlib.metadata as m,json; "
        "names=" + repr(list(config["runtime_packages"])) + "; "
        "print(json.dumps({'packages':{n:m.version(n) for n in names},"
        "'mlx_lm_origin':json.loads(m.distribution('mlx-lm').read_text('direct_url.json'))}))"
    )
    inventory = json.loads(subprocess.check_output([str(python), "-c", expression], text=True))
    if inventory["packages"] != config["runtime_packages"]:
        raise ValueError("installed runtime packages differ from the frozen local condition")
    if inventory["mlx_lm_origin"].get("vcs_info", {}).get("commit_id") != config["mlx_lm_revision"]:
        raise ValueError("MLX-LM source revision differs")
    return {"python": str(python), "upstream": str(upstream), "head": head, **inventory}


def prepare(config_path: Path, root: Path) -> dict:
    if (root / "inputs.json").exists():
        raise FileExistsError("prepare a fresh condition rather than overwrite frozen inputs")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    config = load(config_path)
    if config["external_spend_usd"] != 0 or config["duration_seconds"] > 21600:
        raise ValueError("local campaign exceeds its authorized cost or clock")
    policy = CompilerPolicyV2.model_validate_json(json.dumps(config["compiler_policy"]))
    runtime = runtime_inventory(config)
    lab = Path(config["lab_path"])
    capsule_path = lab / "artifacts/reviewed/phase1a-mlx-reference/capsule.json"
    if (
        file_sha256(capsule_path)
        != "107d37975ab8941ca89f5f129c8d4c79560c1c970dab9c3f43768e566c00738e"
    ):
        raise ValueError("reference capsule identity changed")
    capsule = load(capsule_path)
    model_path = Path(config["model_path"])
    inventory = []
    for entry in capsule["model"]["files"]:
        path = model_path / entry["path"]
        if path.stat().st_size != entry["bytes"] or file_sha256(path) != entry["sha256"]:
            raise ValueError("cached model file differs from its pinned capsule")
        inventory.append({k: entry[k] for k in ("path", "bytes", "sha256")})
    atomic_save(root / "model-inventory.json", {"passed": True, "files": inventory})
    parent = load(REPO / "runs/atlas-compiler-20260907/inputs.json")
    dataset_path = Path(parent["dataset"])
    dataset = load(dataset_path / "prepared-dataset.json")
    if (
        file_sha256(dataset_path / "prepared-dataset.json")
        != parent["environment_parameters"]["dataset_digest"]
    ):
        raise ValueError("frozen benchmark inventory changed")
    anchors = set(parent["ladder_problem_ids"])
    selected = []
    rng = random.Random(config["sampling_seed"])
    for difficulty in ("easy", "medium", "hard"):
        available = sorted(
            (row for row in dataset["selected"] if row["difficulty"] == difficulty),
            key=lambda row: row["problem_id"],
        )
        fixed = [row for row in available if row["problem_id"] in anchors]
        rest = [row for row in available if row["problem_id"] not in anchors]
        rng.shuffle(rest)
        selected.extend(fixed + rest[: config["items_per_difficulty"] - len(fixed)])
    if Counter(row["difficulty"] for row in selected) != {
        key: config["items_per_difficulty"] for key in ("easy", "medium", "hard")
    }:
        raise ValueError("mixed-difficulty population is incomplete")
    packages = {entry["problem_id"]: entry for entry in dataset["packages"]}
    for row in selected:
        pid = row["problem_id"]
        if file_sha256(dataset_path / "archives" / f"{pid}.zip") != packages[pid]["archive_sha256"]:
            raise ValueError("frozen judge archive changed")
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, local_files_only=True, trust_remote_code=False
    )
    counts = {}
    for row in selected:
        request = GenerationRequest(
            request_id="tokenizer-audit",
            instructions=config["instructions"],
            input=row["problem_statement"],
            sampling=SamplingConfiguration(max_output_tokens=policy.per_turn_token_limit),
            tools=compiler_tools(policy),
            tool_choice="auto",
        )
        counts[row["problem_id"]] = count_tool_input(tokenizer, request)
    if max(counts.values()) > policy.max_input_tokens_per_turn:
        raise ValueError("a complete statement does not fit; no truncation is authorized")
    at = datetime.now(UTC).isoformat()
    sources = source_identity()
    environment_parameters = {
        "code_digest": sha256_digest(sources),
        "configuration_digest": sha256_digest(config),
        "dataset_digest": file_sha256(dataset_path / "prepared-dataset.json"),
        "judge_image": policy.judge_image,
        "judge_testlib_digest": file_sha256(Path(parent["testlib"])),
        "model_inventory_digest": sha256_digest(inventory),
        "platform": "local-apple-metal-affine4_linux-arm64-cpu-judge",
    }
    profile = json.loads(json.dumps(parent["profile"]))
    profile.update(
        profile_id="atlas.nemotron-metal.compiler-v2",
        version="2",
        purpose="Local coding episode endurance with explicit submission",
        created_at=at,
    )
    profile["context"].update(
        configured_context_window_tokens=policy.max_context_tokens,
        effective_input_limit_tokens=policy.max_input_tokens_per_turn,
        context_limit_evidence="Pinned local tokenizer; real local HTTP integration required",
        history_selection="Complete per-problem public tool history; no private reasoning",
    )
    profile["prompt_templates"] = [
        component("local-coding-v2", "2", config["instructions"], "Exact local instructions")
    ]
    profile["tools"] = [
        component(
            "tool.compile_and_run",
            "2",
            compiler_wire_identity(policy),
            "Compiler and explicit submission schemas",
        )
    ]
    profile["budgets"] = {
        "actions": limit("actions", policy.max_model_turns + policy.max_tool_calls),
        "input_tokens": limit("tokens", policy.max_model_turns * policy.max_input_tokens_per_turn),
        "output_tokens": limit("tokens", policy.generation_token_limit),
        "latency": limit("seconds", config["model_timeout_seconds"]),
        "wall_time": limit(
            "seconds", policy.trajectory_timeout_seconds + config["judge_timeout_seconds"]
        ),
        "retries": limit("retries", 0),
        "cost": limit("usd", 0),
    }
    for value in profile["budgets"].values():
        value["scope"] = "trajectory"
    runtime_parameters = {
        "provider": "local-nemotron-metal-4bit",
        "server_configuration_digest": sha256_digest(config),
        "initial_thinking": "true",
        "finalization_thinking": "false",
    }
    runtime_parameters = dict(sorted(runtime_parameters.items()))
    serving = component(
        "vllm-metal-local", config["vllm_metal_revision"], runtime, "Pinned existing local runtime"
    )
    model = {
        "purpose": "student",
        "research_role": "target",
        "model_id": config["model_id"],
        "checkpoint": component(
            config["model_id"],
            config["model_revision"],
            inventory,
            "Locally rehashed cached model capsule",
        ),
        "quantization": component(
            "mlx-affine-4bit-g64",
            "1",
            {"bits": 4, "group_size": 64, "mode": "affine"},
            "Separate local quantized condition",
        ),
        "runtime": serving,
        "serving_artifact": serving,
        "protocol": "responses",
        "runtime_parameters": runtime_parameters,
        "runtime_parameters_digest": sha256_digest(runtime_parameters),
    }
    HarnessProfile.model_validate_json(json.dumps(profile))
    ModelServingIdentity.model_validate_json(json.dumps(model))
    inputs = {
        "config": config,
        "config_path": str(config_path.resolve()),
        "config_digest": sha256_digest(config),
        "sources": sources,
        "runtime": runtime,
        "profile": profile,
        "model": model,
        "dataset": str(dataset_path),
        "testlib": parent["testlib"],
        "selected_problem_ids": [row["problem_id"] for row in selected],
        "difficulty_by_problem": {row["problem_id"]: row["difficulty"] for row in selected},
        "regression_anchors": sorted(anchors),
        "environment_parameters": environment_parameters,
        "environment": component(
            "local-coding-endurance",
            "1",
            environment_parameters,
            "Frozen source, model and judge inventory",
        ),
        "governance": parent["governance"],
        "text_gate": {
            "modality": "text",
            "status": "passed",
            "gate_id": "local-complete-statements",
            "gate_revision": "1",
            "evidence_digest": sha256_digest(counts),
            "evidence_refs": ["retained local tokenizer audit"],
            "validated_at": at,
        },
        "token_counts": counts,
        "prepared_at": at,
    }
    snapshot = root / "source-snapshot"
    for relative in sources:
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / relative, target)
        if file_sha256(target) != sources[relative]:
            raise ValueError("source snapshot copy differs")
    shutil.copyfile(config_path, root / "configuration.json")
    atomic_save(root / "inputs.json", inputs)
    print(
        json.dumps(
            {
                "prepared": str(root),
                "items": len(selected),
                "planned_episodes": len(selected) * config["repetitions"],
                "maximum_initial_tokens": max(counts.values()),
                "model_files_verified": len(inventory),
            }
        )
    )
    return inputs


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.config.resolve(), args.root.resolve())
