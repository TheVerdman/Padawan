"""Exercise dispatch faults with retained real outcomes and native SQLite; never call a model."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import run_atlas_coding as entry
from sqlalchemy import update

from padawan.artifacts.store import ArtifactCatalog
from padawan.atlas.dispatch import drain_dispatch
from padawan.models.tables import RunRow


async def validate(prior, output):
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = prior / "continuation"
    original_sha = entry.file_sha256(source / "atlas.sqlite3")
    connection = sqlite3.connect(f"file:{source / 'atlas.sqlite3'}?mode=ro", uri=True)
    target = sqlite3.connect(output / "atlas.sqlite3")
    connection.backup(target)
    target.close()
    requests = {
        row[0]: json.loads(row[1])
        for row in connection.execute("SELECT request_id,record_json FROM atlas_trial_requests")
    }
    ids = [
        row[0]
        for row in connection.execute(
            "SELECT request_id FROM atlas_trial_results ORDER BY request_id LIMIT 8"
        )
    ]
    counts = [
        connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        for table in (
            "external_calls",
            "atlas_trial_results",
            "verifier_results",
        )
    ]
    pending = connection.execute(
        "SELECT count(*) FROM external_calls WHERE status='pending'"
    ).fetchone()[0]
    connection.close()
    shutil.copytree(source / "artifacts", output / "artifacts")
    inputs = entry.load(prior / "inputs-authorized.json")
    launch = inputs["launch"]
    dataset = entry.load(Path(inputs["dataset"]) / "prepared-dataset.json")
    package_index = {row["problem_id"]: row for row in dataset["packages"]}
    database = entry.Database.sqlite(output / "atlas.sqlite3")
    artifacts = entry.LocalArtifactStore(output / "artifacts")
    client = entry.VertexRawPredictClient(
        raw_predict_url=entry.load(prior / "preflight/preflight.json")["destination"],
        model=launch["model_id"],
        timeout_seconds=launch["request_timeout_seconds"],
    )
    executor = entry.IdempotentGenerationExecutor(
        database=database, artifacts=artifacts, client=client
    )
    judge = entry.DockerBatchJudge(
        image=launch["judge_image"],
        scratch=output / "scratch",
        testlib=Path(inputs["testlib"]),
        testlib_digest=inputs["environment_parameters"]["judge_testlib_digest"],
    )
    forbidden_calls = []

    async def forbid_inference(*args, **kwargs):
        forbidden_calls.append("inference")
        raise AssertionError("no inference is permitted in native replay validation")

    def forbid_judging(*args, **kwargs):
        forbidden_calls.append("judging")
        raise AssertionError("no new judging is permitted in native replay validation")

    client.generate_prepared = forbid_inference
    judge.judge = forbid_judging
    report = {"real_source_results": len(ids), "original_pending_effects": pending}
    try:
        with tempfile.TemporaryDirectory(prefix="packages-", dir=output) as tmp:
            cases, packages = {}, {}
            for rid in ids:
                request = requests[rid]
                group = request["run_id"].removeprefix("continuation-")
                bundle = entry.load(source / f"{group}.json")
                items = {
                    v["item_digest"]: entry.record(entry.AtlasItemManifest, v)
                    for v in bundle["items"]
                }
                item_digest = request["item_digest"]
                pid = items[item_digest].metadata["problem_id"]
                if item_digest not in packages:
                    packages[item_digest] = entry.unpack_package(
                        Path(inputs["dataset"]) / "archives" / f"{pid}.zip",
                        Path(tmp) / pid,
                        problem_id=pid,
                        expected_digest=package_index[pid]["archive_sha256"],
                        include_extra_cases=True,
                    )
                cases[rid] = (request, bundle, items)

            async def replay(rid):
                request, bundle, items = cases[rid]
                return await entry.run_coding_trials(
                    executor=executor,
                    client=client,
                    activation_ref=entry.record(entry.ArtifactRef, bundle["activation"]),
                    requests=(entry.record(entry.AtlasTrialRequest, request),),
                    items=items,
                    profile=entry.record(entry.HarnessProfile, bundle["profile"]),
                    execution=entry.record(entry.ResearchExecutionManifest, bundle["execution"]),
                    configuration=entry.record(
                        entry.FixedRunConfiguration, bundle["configuration"]
                    ),
                    readiness=entry.record(entry.AdapterReadinessContext, bundle["readiness"]),
                    packages=packages,
                    judge=judge,
                )

            async def contend():
                for _ in range(30):
                    async with database.transaction() as session:
                        await session.execute(update(RunRow).values(paused=RunRow.paused))

            replayed = await asyncio.gather(*(replay(rid) for rid in ids * 4), contend())
            assert all(
                next(iter(result.values())) in {"verified_success", "verified_failure"}
                for result in replayed[:-1]
            )
            report["concurrent_real_result_replays"] = 32
            report["competing_native_write_transactions"] = 30

            original_register = ArtifactCatalog.register
            injected = False
            started, finished = [], []
            barrier = asyncio.Event()

            async def fail_one_receipt(catalog, session, ref):
                nonlocal injected
                if (
                    ref.media_type == "application/vnd.padawan.atlas-dispatch-status+json"
                    and not injected
                ):
                    injected = True
                    raise sqlite3.OperationalError("controlled dispatch receipt failure")
                return await original_register(catalog, session, ref)

            async def fault_job(rid):
                started.append(rid)
                if len(started) == 4:
                    barrier.set()
                await barrier.wait()
                try:
                    return await replay(rid)
                finally:
                    finished.append(rid)

            with patch.object(ArtifactCatalog, "register", fail_one_receipt):
                outcome = await drain_dispatch(
                    ids,
                    dispatch=fault_job,
                    request_id=lambda rid: rid,
                    concurrency=4,
                    dispatch_deadline=datetime.now(UTC) + timedelta(minutes=5),
                )
            assert injected and len(started) == len(finished) == 4
            assert sum(v == "not_run" for v in outcome.values()) == 4
            assert (
                sum(v.startswith("dispatch_infrastructure_failure:") for v in outcome.values()) == 1
            )
            report["receipt_failure_stopped_admission_and_drained_all_four"] = True

            started, finished = [], []
            barrier, release = asyncio.Event(), asyncio.Event()

            async def cancellable_job(rid):
                started.append(rid)
                if len(started) == 4:
                    barrier.set()
                await release.wait()
                try:
                    return await replay(rid)
                finally:
                    finished.append(rid)

            parent = asyncio.create_task(
                drain_dispatch(
                    ids,
                    dispatch=cancellable_job,
                    request_id=lambda rid: rid,
                    concurrency=4,
                    dispatch_deadline=datetime.now(UTC) + timedelta(minutes=5),
                )
            )
            await barrier.wait()
            parent.cancel()
            await asyncio.sleep(0)
            release.set()
            with contextlib.suppress(asyncio.CancelledError):
                await parent
            assert len(started) == len(finished) == 4
            report["controller_cancellation_drained_all_four_before_close"] = True

            observed = await drain_dispatch(
                ids,
                dispatch=replay,
                request_id=lambda rid: rid,
                concurrency=4,
                dispatch_deadline=datetime.now(UTC) - timedelta(seconds=1),
            )
            assert set(observed.values()) == {"not_run"}
            report["expired_dispatch_window_started_no_work"] = True
        await database.close()
        connection = sqlite3.connect(output / "atlas.sqlite3")
        assert [
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "external_calls",
                "atlas_trial_results",
                "verifier_results",
            )
        ] == counts
        assert (
            connection.execute(
                "SELECT count(*) FROM external_calls WHERE status='pending'"
            ).fetchone()[0]
            == pending
        )
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        connection.close()
        assert entry.file_sha256(source / "atlas.sqlite3") == original_sha
        assert not forbidden_calls
        report.update(
            passed=True,
            new_model_calls=0,
            new_judge_runs=0,
            original_database_unchanged=True,
            unknown_effects_unchanged=True,
        )
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        await client.close()
        await database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(validate(arguments.prior_run.resolve(), arguments.output.resolve()))
