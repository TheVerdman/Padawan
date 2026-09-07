"""Shared registration and evidence reporting for the frozen local coding condition."""

from __future__ import annotations

import csv
import json
import random
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from padawan.adapters.openai_compatible.local_metal import LocalMetalClient
from padawan.artifacts.store import LocalArtifactStore, artifact_put_bytes
from padawan.atlas.coding_experiment import register_coding_run
from padawan.atlas.coding_judge import DockerBatchJudge, unpack_package
from padawan.atlas.coding_manifests import coding_item
from padawan.atlas.coding_tool_contracts import (
    CompilerPolicyV2,
    compiler_tools,
)
from padawan.atlas.contracts import (
    DatasetGovernance,
    ModalityValidationEvidence,
)
from padawan.atlas.orchestration import FixedRunConfiguration
from padawan.atlas.preparation import record
from padawan.models.contracts import ArtifactRef
from padawan.models.database import Database
from padawan.models.hashing import sha256_digest
from padawan.models.research_contracts import (
    HarnessProfile,
    ModelServingIdentity,
    VersionedComponentIdentity,
)
from padawan.orchestration.local_host import (
    atomic_save,
    load,
    now,
)


def make_judge(root: Path, inputs: dict[str, Any]) -> DockerBatchJudge:
    return DockerBatchJudge(
        image=inputs["config"]["compiler_policy"]["judge_image"],
        scratch=root / "scratch/judge",
        testlib=Path(inputs["testlib"]),
        testlib_digest=inputs["environment_parameters"]["judge_testlib_digest"],
        owner_label=root.name,
    )


def native_counts(root: Path) -> dict[str, int]:
    path = root / "atlas.sqlite3"
    if not path.exists():
        return {
            "calls": 0,
            "completed_calls": 0,
            "output_tokens": 0,
            "input_tokens": 0,
            "unresolved": 0,
        }
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT status, result_usage FROM external_calls").fetchall()
    totals = {
        "calls": len(rows),
        "completed_calls": 0,
        "output_tokens": 0,
        "input_tokens": 0,
        "unresolved": 0,
    }
    for status, raw in rows:
        if status != "completed":
            totals["unresolved"] += 1
            continue
        totals["completed_calls"] += 1
        usage = json.loads(raw)
        for key in ("input_tokens", "output_tokens"):
            totals[key] += usage[key]
    return totals


async def register(root: Path, inputs: dict[str, Any], client: LocalMetalClient) -> dict[str, Any]:
    config = inputs["config"]
    policy = record(CompilerPolicyV2, config["compiler_policy"])
    judge = make_judge(root, inputs)
    artifacts = LocalArtifactStore(root / "artifacts")
    db = Database.sqlite(root / "atlas.sqlite3")
    await db.create_schema()
    data = load(Path(inputs["dataset"]) / "prepared-dataset.json")
    rows = {row["problem_id"]: row for row in data["selected"]}
    entries = {row["problem_id"]: row for row in data["packages"]}
    items = []
    try:
        for pid in inputs["selected_problem_ids"]:
            with tempfile.TemporaryDirectory(prefix="registration-", dir=root / "scratch") as tmp:
                package = unpack_package(
                    Path(inputs["dataset"]) / "archives" / f"{pid}.zip",
                    Path(tmp) / pid,
                    problem_id=pid,
                    expected_digest=entries[pid]["archive_sha256"],
                    include_extra_cases=True,
                )
                items.append(coding_item(rows[pid], package, judge))
        preflight = await artifact_put_bytes(
            artifacts,
            (root / "preflight.json").read_bytes(),
            media_type="application/json",
            restricted=True,
            raw_data=True,
        )
        configuration = FixedRunConfiguration(
            instructions=config["instructions"],
            max_output_tokens_per_request=policy.generation_token_limit,
            action_budget_per_request=policy.max_model_turns + policy.max_tool_calls,
            max_cost_usd_per_request=0,
            temperature=config["temperature"],
            top_p=config["top_p"],
            base_seed=config["base_seed"],
            edge_preflight_evidence_digest=preflight.digest,
            tool_preflight_evidence_digest=preflight.digest,
            tools=compiler_tools(policy),
            tool_ids=("tool.compile_and_run",),
            tool_choice="auto",
        )
        started = datetime.now(UTC)
        bundle = await register_coding_run(
            database=db,
            artifacts=artifacts,
            client=client,
            base_profile=record(HarnessProfile, inputs["profile"]),
            model=record(ModelServingIdentity, inputs["model"]),
            environment=record(VersionedComponentIdentity, inputs["environment"]),
            environment_parameters=inputs["environment_parameters"],
            governance=record(DatasetGovernance, inputs["governance"]),
            items=tuple(items),
            text_gate=record(ModalityValidationEvidence, inputs["text_gate"]),
            preflight_artifact=preflight,
            configuration=configuration,
            repetitions=config["repetitions"],
            run_id=root.name,
            authorization_ref=config["authorization_ref"],
            max_in_flight=1,
            starts_at=started,
            expires_at=datetime.fromtimestamp(
                load(root / "state.json")["hard_deadline_epoch"], UTC
            ),
            created_at=started,
        )
        # Difficulty-balanced order inside each replicate, with a predeclared rotation.
        schedule: list[str] = []
        rng = random.Random(config["schedule_seed"])
        item_map = {item.item_digest: item for item in items}
        for repeat in range(config["repetitions"]):
            queues = {}
            for label in ("easy", "medium", "hard"):
                queue = [
                    r
                    for r in bundle["requests"]
                    if r["trial_index"] == repeat
                    and item_map[r["item_digest"]].metadata["publisher_difficulty"] == label
                ]
                rng.shuffle(queue)
                queues[label] = queue
            labels = ["easy", "medium", "hard"]
            labels = labels[repeat % 3 :] + labels[: repeat % 3]
            for position in range(config["items_per_difficulty"]):
                schedule.extend(queues[label][position]["request_id"] for label in labels)
        bundle["schedule"] = schedule
        atomic_save(root / "bundle.json", bundle)
        atomic_save(root / "bundle-identity.json", {"digest": sha256_digest(bundle)})
        return bundle
    finally:
        await db.close()


def write_report(root: Path) -> None:
    inputs = load(root / "inputs.json")
    state = load(root / "state.json") if (root / "state.json").exists() else {}
    progress = (
        load(root / "progress.json") if (root / "progress.json").exists() else {"completed": []}
    )
    completed = {r["request_id"]: r for r in progress["completed"]}
    counts = native_counts(root)
    rows: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    if (root / "bundle.json").exists():
        bundle = load(root / "bundle.json")
        items = {item["item_digest"]: item for item in bundle["items"]}
        with sqlite3.connect(f"file:{root / 'atlas.sqlite3'}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            calls = db.execute("SELECT request_id, status FROM external_calls").fetchall()
            refs = db.execute(
                "SELECT r.owner_id, a.artifact_id, a.uri, a.digest, a.size_bytes, "
                "a.media_type, a.restricted, a.raw_data FROM artifact_references r "
                "JOIN artifacts a ON a.artifact_id=r.artifact_id "
                "WHERE r.owner_type='atlas_coding_tool_result'"
            ).fetchall()
            store = LocalArtifactStore(root / "artifacts")
            for ref in refs:
                fields = dict(ref)
                owner = fields.pop("owner_id")
                fields["restricted"] = bool(fields["restricted"])
                fields["raw_data"] = bool(fields["raw_data"])
                reference = record(ArtifactRef, fields)
                value = json.loads(store.read_bytes(reference, allow_restricted=True))
                if owner != value["request"]["request_id"]:
                    raise ValueError("terminal result owner differs from the native artifact")
                for source in value["artifacts"]:
                    store.read_bytes(record(ArtifactRef, source), allow_restricted=True)
                outcomes.append(value)
                completed[owner] = {"status": value["status"]}
        indexed = {r["request"]["request_id"]: r for r in outcomes}
        for request in bundle["requests"]:
            rid = request["request_id"]
            item = items[request["item_digest"]]
            result = indexed.get(rid, {})
            evidence = result.get("verifier", {}).get("evidence", {})
            episode_calls = [
                call
                for call in calls
                if call["request_id"] == rid or call["request_id"].startswith(rid + ".turn-")
            ]
            unfinished = "not_run"
            if episode_calls:
                unfinished = "in_progress" if state.get("phase") == "running" else "interrupted"
                if state.get("phase") != "running" and any(
                    call["status"] != "completed" for call in episode_calls
                ):
                    unfinished = "unresolved_effect"
            rows.append(
                {
                    "request_id": rid,
                    "problem_id": item["metadata"]["problem_id"],
                    "difficulty": item["metadata"]["publisher_difficulty"],
                    "replicate": request["trial_index"],
                    "seed": request["sampling"]["seed"],
                    "status": completed.get(rid, {}).get("status", unfinished),
                    "verdict": result.get("verifier", {}).get("summary", ""),
                    "termination": evidence.get("termination", ""),
                    "model_turns": result.get("model_turns", ""),
                    "tool_calls": result.get("tool_calls", ""),
                    "compiler_executions": result.get("compiler_executions", ""),
                    "output_tokens": result.get("tokens", {}).get("output_tokens", ""),
                }
            )
    if rows:
        with (root / "trials.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    summary: dict[str, Any] = {
        "at": now(),
        "state": state.get("phase", "preparing"),
        "planned": len(rows)
        or len(inputs["selected_problem_ids"]) * inputs["config"]["repetitions"],
        "completed": len(completed),
        "verified_successes": sum(r["status"] == "verified_success" for r in completed.values()),
        "native_counts": counts,
        "verdicts": dict(Counter(r["verdict"] for r in rows if r["verdict"])),
        "stop_reason": progress.get("stop_reason", state.get("stop_reason")),
        "cleanup": load(root / "cleanup.json") if (root / "cleanup.json").exists() else None,
        "controller_recovery": load(root / "resume-proof.json")
        if (root / "resume-proof.json").exists()
        else None,
    }
    atomic_save(root / "summary.json", summary)
    cleanup = "Verified" if summary["cleanup"] and summary["cleanup"]["passed"] else "Pending"
    stop = (
        datetime.fromtimestamp(state["hard_deadline_epoch"], UTC).astimezone().isoformat()
        if state.get("hard_deadline_epoch")
        else "Not started"
    )
    lines = [
        "# Local coding endurance run",
        "",
        f"Status: **{summary['state']}**. Updated {summary['at']}.",
        "",
        f"Completed **{summary['completed']}/{summary['planned']}** planned episodes; "
        f"**{summary['verified_successes']} verified successes**. "
        "Unrun episodes remain separately listed.",
        "",
        f"Native calls: {counts['completed_calls']} completed, {counts['unresolved']} unresolved. "
        f"Generated tokens: {counts['output_tokens']:,}; "
        f"processed input tokens: {counts['input_tokens']:,}. "
        "Operational preflight is reported separately.",
        "",
        f"Hard stop: {stop}. External model/cloud spending: $0. Cleanup: {cleanup}.",
        "",
        "The condition uses cached affine 4-bit Nemotron through the pinned local Metal runtime, "
        "one active trajectory, explicit submission, and a protected finalization budget. "
        "The source and full benchmark statements/judge archives are frozen. These outcomes are "
        "separate from BF16 cloud results and do not measure a parameter update or a "
        "persistence benefit.",
        "",
        "The queue contains eight publisher-easy, eight medium, and eight hard real programming "
        "problems, with eight predetermined seeds per problem. The eight earlier regression "
        "anchors are retained. The six-hour clock and global token cap may leave planned "
        "episodes unrun.",
        "",
        "Only public assistant messages, addressed calls and bounded compiler feedback enter "
        "later prompts. Provider reasoning, raw traffic, hidden judge data and researcher "
        "evidence remain private. Automatic function selection is used throughout; "
        "finalization disables thinking and offers only submit_solution.",
        "",
        "A separate watchdog checks power, thermals, memory pressure, swap growth, disk space, "
        "owned processes and controller/supervisor heartbeats. One planned controller restart "
        "occurs after a committed episode, with no model-server restart. Unknown effects "
        "stop execution rather than retrying.",
        "",
        "[Per-episode table](trials.csv) · [Summary](summary.json) · "
        "[Real integration evidence](preflight.json) · [Configuration](configuration.json) · "
        "[Host observations](host.jsonl) · [Events](events.jsonl)",
    ]
    if summary["stop_reason"]:
        lines += ["", "Stop reason: " + str(summary["stop_reason"])]
    if summary["controller_recovery"]:
        lines += [
            "",
            "Controller recovery verified; completed evidence was replayed "
            "with zero added model calls.",
        ]
    (root / "report.md").write_text("\n".join(lines) + "\n")
