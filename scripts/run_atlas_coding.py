"""Prepare, register, or execute the finite official-BF16 coding scout.

Preparation is local and makes no model calls. Registration requires a retained real serving
preflight and explicit activation bounds. Execution sends only the already registered requests.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from padawan.adapters.openai_compatible.vertex import VertexRawPredictClient
from padawan.artifacts.store import LocalArtifactStore, artifact_put_bytes
from padawan.atlas.adapters import AdapterReadinessContext
from padawan.atlas.coding_experiment import register_coding_run
from padawan.atlas.coding_judge import DockerBatchJudge, file_sha256, unpack_package
from padawan.atlas.coding_manifests import (
    DEFAULT_PROBLEM_LOCK,
    coding_item,
    validate_prepared_problem_lock,
)
from padawan.atlas.coding_runner import run_coding_trials
from padawan.atlas.coding_tool_contracts import COMPILER_TOOL, CompilerPolicy
from padawan.atlas.contracts import (
    AtlasItemManifest,
    AtlasTrialRequest,
    DatasetGovernance,
    ModalityValidationEvidence,
)
from padawan.atlas.dependencies import coding_dependency
from padawan.atlas.dispatch import drain_dispatch
from padawan.atlas.orchestration import FixedRunConfiguration
from padawan.models.contracts import ArtifactRef
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    HarnessProfile,
    ModelServingIdentity,
    ResearchExecutionManifest,
    VersionedComponentIdentity,
)
from padawan.orchestration.external_calls import IdempotentGenerationExecutor


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def record(model, value):
    return model.model_validate_json(json.dumps(value))


def save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        stream.write(json.dumps(value, indent=2) + "\n")


def retain_guard_refusal(control, error):
    from control_atlas_vertex import retain_guard_refusal as retain

    return retain(control, error)


async def await_guard_admission(admission, stop, deadline):
    """Wait before any native call intent; never replay a dispatched request."""
    from control_atlas_vertex import GuardHealthError

    while not stop.is_set():
        try:
            if datetime.now(UTC) >= deadline:
                raise GuardHealthError({"reasons": ["dispatch_deadline_reached"]})
            if admission.check():
                return True
        except Exception as error:
            stop.set()
            retain_guard_refusal(admission.control, error)
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=1)
    return False


def code_identity() -> dict:
    # Bind the current code, including reviewed uncommitted implementation, without credentials.
    paths = sorted(Path("padawan").rglob("*.py")) + [
        Path("scripts") / name
        for name in (
            "run_atlas_coding.py",
            "prepare_atlas_coding.py",
            "prepare_atlas_vertex.py",
            "preflight_atlas_vertex.py",
            "control_atlas_vertex.py",
            "launch_atlas_campaign.py",
        )
    ]
    return {str(p): file_sha256(p) for p in paths}


def component(name: str, version: str, value, evidence: str) -> dict:
    return {
        "component_id": name,
        "version": version,
        "digest": sha256_digest(value),
        "evidence_status": "pinned",
        "evidence": evidence,
    }


def limit(unit: str, value: float) -> dict:
    return {"disposition": "capped", "scope": "request", "unit": unit, "value": value}


def prepare(args) -> None:

    launch = load(args.config)
    dataset = load(args.dataset / "prepared-dataset.json")
    if not dataset["ready"] or len(dataset["selected"]) != 64:
        raise ValueError("prepare requires the complete frozen 64-item benchmark")
    if launch["protocol"] != "responses" or "BF16" not in launch["model_id"]:
        raise ValueError("this scout requires the official BF16 model through Responses")
    server_args = launch["server_args"]
    context_tokens = int(server_args[server_args.index("--max-model-len") + 1])
    largest_output = max(w["output_tokens"] for w in launch["waves"])
    compiler_policy = None
    if launch.get("compiler_tool_policy"):
        compiler_policy = CompilerPolicy.model_validate_json(
            json.dumps(launch["compiler_tool_policy"])
        )
        if compiler_policy.judge_image != launch["judge_image"]:
            raise ValueError("compiler tool policy must use the pinned CPU environment")
    if context_tokens < launch["input_token_limit"] + largest_output:
        raise ValueError("serving context must fit the full input and largest output allowance")
    tokenizer = coding_dependency("transformers").AutoTokenizer.from_pretrained(
        args.tokenizer, local_files_only=True, trust_remote_code=False
    )
    now = datetime.now(UTC).isoformat()
    audit = {}
    for row in dataset["selected"]:
        tool_kwargs = (
            {
                "tools": [
                    {
                        "type": "function",
                        "function": {k: v for k, v in COMPILER_TOOL.items() if k != "type"},
                    }
                ],
                "force_nonempty_content": True,
            }
            if compiler_policy
            else {}
        )
        encoding = tokenizer.apply_chat_template(
            [
                {"role": "system", "content": launch["instructions"]},
                {"role": "user", "content": row["problem_statement"]},
            ],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            **tool_kwargs,
        )
        token_ids = encoding["input_ids"]
        if (
            not isinstance(token_ids, list)
            or not token_ids
            or any(type(value) is not int for value in token_ids)
        ):
            raise ValueError("official tokenizer did not return one complete token sequence")
        audit[row["problem_id"]] = len(token_ids)
    if max(audit.values()) > launch["input_token_limit"]:
        raise ValueError("a complete statement exceeds the configured input limit; no truncation")
    evidence = {
        "token_counts": audit,
        "tokenizer_identity": load(args.tokenizer / "identity.json"),
        "dependencies": {name: version(name) for name in ("transformers", "tokenizers", "jinja2")},
    }
    text_gate = {
        "modality": "text",
        "status": "passed",
        "gate_id": "official-tokenizer-complete-inputs",
        "gate_revision": "1",
        "evidence_digest": sha256_digest(evidence),
        "evidence_refs": ["retained complete-statement tokenizer audit"],
        "validated_at": now,
    }
    sources = code_identity()
    environment_parameters = dict(
        sorted(
            {
                "code_digest": sha256_digest(sources),
                "judge_image": launch["judge_image"],
                "judge_testlib_digest": file_sha256(args.testlib),
                "platform": "local-linux-arm64-judge_remote-linux-amd64-bf16",
                "dataset_digest": file_sha256(args.dataset / "prepared-dataset.json"),
            }.items()
        )
    )
    environment = component(
        "atlas-coding-environment",
        "1",
        environment_parameters,
        "Exact local code, official judge archives and container identity",
    )
    profile = {
        "profile_id": "atlas.nemotron-bf16.base",
        "version": "1",
        "tier": "standardized",
        "purpose": "Native reasoning coding frontier, with terminal hidden judging",
        "continuation": {
            "continuation_mode": "none",
            "response_storage_enabled": False,
            "previous_response_id_enabled": False,
            "reasoning_retention_enabled": False,
            "reasoning_retention_mode": "none",
            "private_reasoning_capture_enabled": True,
            "private_reasoning_used_as_context": False,
        },
        "context": {
            "policy_id": "complete-statements",
            "version": "1",
            "configured_context_window_tokens": context_tokens,
            "effective_input_limit_tokens": launch["input_token_limit"],
            "context_limit_evidence": "Official tokenizer audit; actual serving preflight required",
            "token_counting_mode": "pinned official tokenizer and native provider usage",
            "history_selection": "one independent complete statement",
            "truncation_enabled": False,
            "truncation_strategy": "none",
            "compaction_enabled": False,
            "compaction_strategy": "none",
        },
        "prompt_templates": [
            component(
                "coding-native", "1", launch["instructions"], "Exact retained task instructions"
            )
        ],
        "tools": [component("tool.none", "1", {"tools": []}, "No model tool calls in native wave")],
        "budgets": {
            "actions": limit("actions", 1),
            "input_tokens": limit("tokens", launch["input_token_limit"]),
            "output_tokens": limit("tokens", largest_output),
            "latency": limit("seconds", launch["request_timeout_seconds"]),
            "wall_time": limit(
                "seconds", launch["request_timeout_seconds"] + launch["judge_deadline_seconds"]
            ),
            "retries": limit("retries", 0),
            "cost": limit("usd", launch["proposed_spending_limit_usd"]),
        },
        "created_at": now,
    }
    if compiler_policy is not None:
        profile["profile_id"] = "atlas.nemotron-bf16.compiler"
        profile["purpose"] = "Finite public compiler feedback; one terminal hidden judgment"
        profile["continuation"]["continuation_mode"] = "explicit_history"
        profile["context"]["effective_input_limit_tokens"] = (
            compiler_policy.max_input_tokens_per_turn
        )
        profile["context"]["history_selection"] = (
            "Complete statement and explicit public tool transcript; no private reasoning"
        )
        profile["tools"] = [
            component(
                "tool.compile_and_run",
                "1",
                {"policy": compiler_policy, "wire": COMPILER_TOOL},
                "Pinned finite compiler policy, image and exact function schema",
            )
        ]
        profile["budgets"]["actions"] = limit(
            "actions", compiler_policy.max_model_turns + compiler_policy.max_tool_calls
        )
        profile["budgets"]["input_tokens"] = limit(
            "tokens", compiler_policy.max_model_turns * compiler_policy.max_input_tokens_per_turn
        )
        for value in profile["budgets"].values():
            value["scope"] = "trajectory"
    runtime_parameters = dict(
        sorted(
            {
                "provider": "vertex-nemotron-bf16",
                "dtype": "bfloat16",
                "server_configuration_digest": sha256_digest(launch),
            }.items()
        )
    )
    serving_image = component(
        "vllm-openai",
        launch["runtime_version"],
        launch["source_image"],
        "Pinned official linux/amd64 image; no local CUDA validation claim",
    )
    serving_image["digest"] = launch["source_image"].split("@", 1)[1]
    model = {
        "purpose": "student",
        "research_role": "target",
        "model_id": launch["model_id"],
        "checkpoint": component(
            launch["model_id"],
            launch["model_revision"],
            {"revision": launch["model_revision"], "metadata": launch["model_metadata_sha256"]},
            "Official NVIDIA BF16 repository pinned by immutable revision",
        ),
        "quantization": component(
            "unquantized-bf16",
            "1",
            {"dtype": "bfloat16", "quantization": None},
            "Official BF16 checkpoint; no quantization substitution",
        ),
        "runtime": component(
            "vllm",
            launch["runtime_version"],
            launch["source_image"],
            "Pinned official serving image",
        ),
        "serving_artifact": serving_image,
        "protocol": "responses",
        "runtime_parameters": runtime_parameters,
        "runtime_parameters_digest": sha256_digest(runtime_parameters),
    }
    governance = {
        "governance_id": "lcbpro-internal-diagnostic-20260906",
        "benchmark_id": "LiveCodeBench-Pro",
        "benchmark_version": dataset["statement_revision"],
        "dataset_revision": dataset["statement_revision"],
        "source_url": "https://huggingface.co/datasets/QAQAQAQAQ/LiveCodeBench-Pro",
        "rights": {
            "rights_id": "lcbpro-authorized-local-evaluation",
            "version": "1",
            "basis": "open_license",
            "license_id": "Apache-2.0",
            "basis_detail": (
                "Publisher dataset metadata; user approved gated access and internal evaluation"
            ),
            "permitted_uses": ["evaluation", "evidence_retention", "internal_research"],
            "distribution_scope": "internal_only",
            "review_status": "confirmed",
            "reviewed_by": "user-approved dataset access; metadata reviewed by Codex",
            "reviewed_at": now,
        },
        "access": "local",
        "redistribution": "metadata_only",
        "contamination": "suspected",
        "evaluation_class": "development",
        "license_expression": "Apache-2.0",
        "contamination_evidence": [
            "Public 2024-2025 problems; NVIDIA discloses LCB-derived training"
        ],
        "reviewed_at": now,
    }
    for kind, value in (
        (HarnessProfile, profile),
        (ModelServingIdentity, model),
        (VersionedComponentIdentity, environment),
        (DatasetGovernance, governance),
        (ModalityValidationEvidence, text_gate),
    ):
        record(kind, value)
    ladder = []
    for label, count in launch["ladder_population"].items():
        ladder.extend(
            row["problem_id"]
            for row in dataset["selected"]
            if row["difficulty"] == label and row["problem_id"] not in ladder
        )
        candidates = [
            pid
            for pid in ladder
            if next(r for r in dataset["selected"] if r["problem_id"] == pid)["difficulty"] == label
        ]
        ladder = [pid for pid in ladder if pid not in candidates[count:]]
    if launch.get("explicit_paired_population"):
        ladder = launch["paired_problem_order"]
        selected_rows = {r["problem_id"]: r for r in dataset["selected"]}
        if len(ladder) != len(set(ladder)) or not set(ladder).issubset(selected_rows):
            raise ValueError("explicit paired population must contain unique frozen problems")
        if (
            Counter(selected_rows[pid]["difficulty"] for pid in ladder)
            != launch["ladder_population"]
        ):
            raise ValueError("explicit paired difficulty allocation differs from the plan")
    output = {
        "status": "prepared_no_model_calls",
        "launch": launch,
        "config_file": str(args.config.resolve()),
        "tokenizer_directory": str(args.tokenizer.resolve()),
        "dataset": str(args.dataset.resolve()),
        "testlib": str(args.testlib.resolve()),
        "sources": sources,
        "profile": profile,
        "model": model,
        "environment": environment,
        "environment_parameters": environment_parameters,
        "governance": governance,
        "text_gate": text_gate,
        "tokenizer_audit": evidence,
        "ladder_problem_ids": sorted(ladder),
    }
    if launch.get("explicit_paired_population"):
        lock_path = Path(launch.get("problem_lock_file", DEFAULT_PROBLEM_LOCK)).resolve()
        output["problem_lock"] = {"path": str(lock_path), "sha256": file_sha256(lock_path)}
        validate_prepared_problem_lock(output)
    save(args.output, output)
    print(
        json.dumps(
            {
                "prepared": str(args.output),
                "items": len(audit),
                "maximum_input_tokens": max(audit.values()),
                "ladder_items": len(ladder),
            }
        )
    )


async def execute(args) -> None:
    inputs = load(args.inputs)
    launch = inputs["launch"]
    validate_prepared_problem_lock(inputs)
    if code_identity() != inputs["sources"]:
        raise ValueError(
            "current code differs from prepared inputs; prepare again before activation"
        )
    if not args.raw_predict_url:
        raise ValueError("the actual provider-returned dedicated rawPredict URL is required")
    client = VertexRawPredictClient(
        raw_predict_url=args.raw_predict_url,
        model=launch["model_id"],
        timeout_seconds=launch["request_timeout_seconds"],
        gcloud=args.gcloud if args.command == "run" else None,
        compiler_tools=bool(launch.get("compiler_tool_policy")),
    )
    root = args.run_directory.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    artifacts = LocalArtifactStore(root / "artifacts")
    database = Database.sqlite(root / "atlas.sqlite3")
    control = None
    guard_admission = None
    monitor_task = None
    monitor_done = asyncio.Event()
    stop = asyncio.Event()
    dataset_root = Path(inputs["dataset"])
    dataset = load(dataset_root / "prepared-dataset.json")
    if (
        file_sha256(dataset_root / "prepared-dataset.json")
        != inputs["environment_parameters"]["dataset_digest"]
    ):
        raise ValueError("dataset changed after preparation")
    try:
        if args.command == "run" and launch.get("independent_guard_required"):
            from control_atlas_vertex import Control, GuardAdmission, atomic_save

            if not args.control_directory:
                raise ValueError("this paid campaign requires its live independent cloud guard")
            control = Control(inputs["config_file"], args.control_directory, args.gcloud)
            if control.config != launch:
                raise ValueError("cloud controller configuration differs from the frozen inputs")
            guard_admission = GuardAdmission(control)
            # Publish the heartbeat before the PID, so the guard cannot observe a half-enrollment.
            atomic_save(
                control.root / "runner-heartbeat.json", {"at": datetime.now(UTC).isoformat()}
            )
            control.update(
                controller_pid=os.getpid(), controller_started_at=datetime.now(UTC).isoformat()
            )

            async def monitor():
                while not monitor_done.is_set():
                    atomic_save(
                        control.root / "runner-heartbeat.json",
                        {"at": datetime.now(UTC).isoformat()},
                    )
                    try:
                        guard_admission.check()
                    except Exception as error:
                        already_stopped = stop.is_set()
                        stop.set()
                        if not already_stopped:
                            retain_guard_refusal(control, error)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(monitor_done.wait(), timeout=5)

            monitor_task = asyncio.create_task(monitor())
        await database.create_schema()
        with tempfile.TemporaryDirectory(prefix="packages-", dir=root) as tmp:
            judge = DockerBatchJudge(
                image=launch["judge_image"],
                scratch=root / "judge-scratch",
                testlib=Path(inputs["testlib"]),
                testlib_digest=inputs["environment_parameters"]["judge_testlib_digest"],
            )
            rows = {r["problem_id"]: r for r in dataset["selected"]}
            items, packages = {}, {}
            for entry in dataset["packages"]:
                pid = entry["problem_id"]
                package = unpack_package(
                    dataset_root / "archives" / f"{pid}.zip",
                    Path(tmp) / pid,
                    problem_id=pid,
                    expected_digest=entry["archive_sha256"],
                    include_extra_cases=True,
                )
                item = coding_item(rows[pid], package, judge)
                items[item.item_digest], packages[item.item_digest] = item, package
            if args.command == "register":
                if not args.preflight or not args.authorization_ref or not args.expires_at:
                    raise ValueError(
                        "registration needs real preflight, authority reference and deadline"
                    )
                now = datetime.now(UTC)
                expires = datetime.fromisoformat(args.expires_at)
                capture_hours = launch.get(
                    "capture_after_hours", launch["stop_dispatch_after_hours"]
                )
                if expires.tzinfo is None or not 0 < (expires - now).total_seconds() <= (
                    capture_hours * 3600
                ):
                    raise ValueError(
                        "activation expiry must be within the approved result-capture window"
                    )
                source = load(args.preflight)
                deployed_at = datetime.fromisoformat(source["deployment_started_at"])
                if (
                    deployed_at.tzinfo is None
                    or expires > deployed_at + timedelta(hours=capture_hours)
                    or source.get("authorization_ref") != args.authorization_ref
                ):
                    raise ValueError("Atlas activation exceeds the original deployment authority")
                ref = await artifact_put_bytes(
                    artifacts,
                    args.preflight.read_bytes(),
                    media_type="application/json",
                    restricted=True,
                    raw_data=True,
                )
                for wave in launch["waves"]:
                    selected = tuple(
                        item
                        for item in items.values()
                        if wave["items"] == 64
                        or item.metadata["problem_id"] in inputs["ladder_problem_ids"]
                    )
                    config = FixedRunConfiguration(
                        instructions=launch["instructions"],
                        max_output_tokens_per_request=wave["output_tokens"],
                        action_budget_per_request=(
                            2 * launch["compiler_tool_policy"]["max_tool_calls"] + 1
                            if launch.get("compiler_tool_policy")
                            else 1
                        ),
                        max_cost_usd_per_request=(
                            launch["proposed_spending_limit_usd"] / launch["maximum_trial_requests"]
                        ),
                        temperature=launch["temperature"],
                        top_p=launch["top_p"],
                        base_seed=launch["base_seed"],
                        edge_preflight_evidence_digest=ref.digest,
                        **(
                            {
                                "tools": (COMPILER_TOOL,),
                                "tool_ids": ("tool.compile_and_run",),
                                "tool_choice": "auto",
                                "tool_preflight_evidence_digest": ref.digest,
                            }
                            if launch.get("compiler_tool_policy")
                            else {}
                        ),
                    )
                    bundle = await register_coding_run(
                        database=database,
                        artifacts=artifacts,
                        client=client,
                        base_profile=record(HarnessProfile, inputs["profile"]),
                        model=record(ModelServingIdentity, inputs["model"]),
                        environment=record(VersionedComponentIdentity, inputs["environment"]),
                        environment_parameters=inputs["environment_parameters"],
                        governance=record(DatasetGovernance, inputs["governance"]),
                        items=selected,
                        text_gate=record(ModalityValidationEvidence, inputs["text_gate"]),
                        preflight_artifact=ref,
                        configuration=config,
                        repetitions=launch["repetitions"],
                        run_id=f"{root.name}-{wave['id']}",
                        authorization_ref=args.authorization_ref,
                        max_in_flight=wave["max_in_flight"],
                        starts_at=now,
                        expires_at=expires,
                        created_at=now,
                    )
                    save(root / f"{wave['id']}.json", bundle)
                print(
                    json.dumps(
                        {
                            "registered_requests": launch["maximum_trial_requests"],
                            "model_calls": 0,
                        }
                    )
                )
                return
            executor = IdempotentGenerationExecutor(
                database=database, artifacts=artifacts, client=client
            )
            shared_judge_semaphore = asyncio.Semaphore(launch["max_concurrent_judges"])
            tokenizer = None
            compiler_policy = None
            if launch.get("compiler_tool_policy"):
                compiler_policy = CompilerPolicy.model_validate_json(
                    json.dumps(launch["compiler_tool_policy"])
                )
                tokenizer = coding_dependency("transformers").AutoTokenizer.from_pretrained(
                    inputs["tokenizer_directory"], local_files_only=True, trust_remote_code=False
                )

            async def continuation_admission():
                if guard_admission is None:
                    return not stop.is_set()
                deadline = datetime.fromisoformat(control.state()["teardown_at"])
                return await await_guard_admission(guard_admission, stop, deadline)

            async def dispatch(bundle, requests):
                return await run_coding_trials(
                    executor=executor,
                    client=client,
                    activation_ref=record(ArtifactRef, bundle["activation"]),
                    requests=tuple(record(AtlasTrialRequest, r) for r in requests),
                    items={
                        i.item_digest: i
                        for i in (record(AtlasItemManifest, r) for r in bundle["items"])
                    },
                    profile=record(HarnessProfile, bundle["profile"]),
                    execution=record(ResearchExecutionManifest, bundle["execution"]),
                    configuration=record(FixedRunConfiguration, bundle["configuration"]),
                    readiness=record(AdapterReadinessContext, bundle["readiness"]),
                    packages=packages,
                    judge=judge,
                    judge_timeout_seconds=launch["judge_deadline_seconds"],
                    max_judges=launch["max_concurrent_judges"],
                    shared_judge_semaphore=shared_judge_semaphore,
                    compiler_policy=compiler_policy,
                    tokenizer=tokenizer,
                    continuation_admission=continuation_admission,
                )

            if args.wave == "paired":
                # A fixed prospective order; no outcome-based resampling or selection.
                bundles = {w["id"]: load(root / f"{w['id']}.json") for w in launch["waves"]}
                schedule = []
                by_pid = {}
                for wave_id, bundle in bundles.items():
                    manifests = {i["item_digest"]: i for i in bundle["items"]}
                    for request in bundle["requests"]:
                        pid = manifests[request["item_digest"]]["metadata"]["problem_id"]
                        by_pid[pid, request["trial_index"], wave_id] = request
                priority = launch["paired_problem_order"]
                wave_ids = list(bundles)
                for start in range(0, len(priority), 8):
                    for repetition in range(launch["repetitions"]):
                        for offset, pid in enumerate(priority[start : start + 8]):
                            rotation = (start + offset + repetition) % len(wave_ids)
                            order = wave_ids[rotation:] + wave_ids[:rotation]
                            schedule.extend(
                                (wave_id, by_pid[pid, repetition, wave_id]) for wave_id in order
                            )
                for pid in launch["supplemental_problem_order"]:
                    for repetition in range(launch["repetitions"]):
                        schedule.append(("base-8k", by_pid[pid, repetition, "base-8k"]))
                results = {request["request_id"]: "not_run" for _, request in schedule}
                prospective = {"schedule": [{"wave": w, "request": r} for w, r in schedule]}
                order_path = root / "prospective-dispatch-order.json"
                if order_path.exists():
                    if load(order_path) != prospective:
                        raise ValueError("prospective dispatch order changed")
                else:
                    save(order_path, prospective)
                wave_semaphores = {
                    w["id"]: asyncio.Semaphore(w["max_in_flight"]) for w in launch["waves"]
                }
                # Activation references do not expose their payload; derive the immutable
                # capture window from the registered execution, or the separate cloud clock.
                first = next(iter(bundles.values()))
                from padawan.artifacts.store import artifact_read_bytes
                from padawan.atlas.activation import AtlasActivation

                declared = AtlasActivation.model_validate_json(
                    await artifact_read_bytes(
                        artifacts,
                        record(ArtifactRef, first["activation"]),
                        allow_restricted=True,
                    )
                )
                dispatch_deadline = declared.expires_at
                if control is not None:
                    dispatch_deadline = datetime.fromisoformat(control.state()["dispatch_deadline"])

                async def one(job):
                    wave_id, request = job
                    async with wave_semaphores[wave_id]:
                        if stop.is_set() or datetime.now(UTC) >= dispatch_deadline:
                            return {request["request_id"]: "not_run"}
                        if guard_admission is not None and not await await_guard_admission(
                            guard_admission, stop, dispatch_deadline
                        ):
                            return {request["request_id"]: "not_run"}
                        return await dispatch(bundles[wave_id], [request])

                def completed(job, outcome):
                    results.update(outcome)
                    if control is not None:
                        atomic_save(
                            root / "dispatch-progress.json",
                            {
                                "at": datetime.now(UTC).isoformat(),
                                "outcomes": results,
                            },
                        )
                    print(
                        json.dumps({"trial": job[1]["request_id"], "outcome": outcome}), flush=True
                    )

                # Keep the frozen order and per-condition limits while refilling free slots.
                # A slow request need not idle the fleet at an arbitrary block boundary.
                block_size = (
                    launch.get("paired_block_size", len(priority)) * len(wave_ids)
                    if launch.get("paired_block_barrier", False)
                    else len(schedule)
                )
                for offset in range(0, len(schedule), block_size):
                    if (
                        stop.is_set()
                        or (dispatch_deadline - datetime.now(UTC)).total_seconds() < 30
                    ):
                        break
                    block = schedule[offset : offset + block_size]
                    outcome = await drain_dispatch(
                        block,
                        dispatch=one,
                        request_id=lambda job: job[1]["request_id"],
                        concurrency=launch["paired_concurrency"],
                        dispatch_deadline=dispatch_deadline,
                        stop=stop,
                        on_result=completed,
                    )
                    results.update(outcome)
                    print(
                        json.dumps(
                            {
                                "completed_block": offset // block_size,
                                "counts": dict(Counter(outcome.values())),
                            }
                        ),
                        flush=True,
                    )
            else:
                bundle = load(root / f"{args.wave}.json")
                results = await dispatch(bundle, bundle["requests"])
            save(
                root / f"{args.wave}-dispatch-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json",
                results,
            )
            save(
                root
                / f"{args.wave}-dispatch-exit-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json",
                {
                    "at": datetime.now(UTC).isoformat(),
                    "wave": args.wave,
                    "counts": dict(Counter(results.values())),
                    "admission_stopped": stop.is_set(),
                    "admission_stop": control.state().get("admission_stop") if control else None,
                    "admission_pause": control.state().get("admission_pause") if control else None,
                    "dispatch_deadline": control.state().get("dispatch_deadline")
                    if control
                    else None,
                    "retry_authorized": False,
                },
            )
            print(json.dumps({"wave": args.wave, "counts": dict(Counter(results.values()))}))
    finally:
        monitor_done.set()
        if monitor_task is not None:
            await asyncio.gather(monitor_task, return_exceptions=True)
        await client.close()
        await database.close()
        if control is not None:
            control.update(
                controller_finished_at=datetime.now(UTC).isoformat(), cleanup_requested=True
            )
            try:
                await asyncio.to_thread(control.cleanup)
            finally:
                control.api.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    for flag in ("config", "dataset", "tokenizer", "testlib", "output"):
        prep.add_argument(f"--{flag}", type=Path, required=True)
    for name in ("register", "run"):
        command = sub.add_parser(name)
        command.add_argument("--inputs", type=Path, required=True)
        command.add_argument("--run-directory", type=Path, required=True)
        command.add_argument("--raw-predict-url", required=True)
        if name == "register":
            command.add_argument("--preflight", type=Path, required=True)
            command.add_argument("--authorization-ref", required=True)
            command.add_argument("--expires-at", required=True)
        else:
            command.add_argument("--wave", required=True)
            command.add_argument("--gcloud", default="gcloud")
            command.add_argument("--control-directory", type=Path)
    args = parser.parse_args()
    prepare(args) if args.command == "prepare" else asyncio.run(execute(args))


if __name__ == "__main__":
    main()
