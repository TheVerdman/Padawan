"""Opt-in, CPU-only engineering measurement; never a model or production launcher.

Run with PYTHONPATH=. .venv/bin/python scripts/measure_pprl_history.py --output runs/NEW.
The parent owns all children. Each fixture has a new finite declaration before any action.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNG_VALUES = (0, 8, 32, 64)
LIMITS = {
    "fixtures": 24,
    "committed_actions": 648,
    "children": 48,
    "monitor_processes": 1200,
    "child_seconds": 180,
    "total_seconds": 1200,
    "child_rss_bytes": 768 * 1024**2,
    "retained_bytes": 256 * 1024**2,
    "retained_files": 2000,
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def files(root):
    return {str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*") if p.is_file()}


async def child(root, *, history, shape, phase):
    # Imports belong to child startup; the parent has no DB/model runtime objects.
    from sqlalchemy import event, select

    from padawan.governance.amber import AmberStatus
    from padawan.governance.amber_store import AmberStore
    from padawan.models.database import Database
    from padawan.models.hashing import canonical_json_bytes, sha256_digest
    from padawan.models.tables import ProcessRolloutRow, ProcessWorkerRegistrationRow
    from padawan.pprl.contracts import (
        ProcessEventKind,
        ProjectSplit,
        ProjectStatePayload,
        RolloutStatus,
    )
    from padawan.pprl.distributions import ProcessDistributionRegistry
    from padawan.pprl.store import ProcessStore
    from padawan.pprl.tasks import ProcessTaskStore
    from padawan.pprl.worker_broker import ProcessWorkerBroker
    from padawan.pprl.worker_contracts import ProcessWorkerScope
    from padawan.pprl.worker_protocol_contracts import ProcessWorkerRequest
    from tests.pprl_helpers import (
        DeterministicProjectGenerator,
        component,
        distribution,
        envelope,
        execution,
        fund_resources,
        program,
        worker_model,
    )
    from tests.pprl_task_helpers import task_plan

    database = Database.sqlite(root / "institution.sqlite")
    amber = AmberStore()
    store = ProcessStore(amber)
    broker = ProcessWorkerBroker(database=database, store=store, broker_audience="history-fixture")

    async def issue(manifest):
        now = datetime.now(UTC)
        async with database.transaction() as session:
            _, access = await store.workers.issue(
                session,
                execution_digest=manifest["execution"],
                role_id="researcher",
                worker_model_digest=manifest["model"],
                declared_capabilities=("reasoning",),
                issued_by="reviewer-a",
                evidence="trusted CPU fixture client; no model or process attestation",
                expires_at=now + timedelta(minutes=10),
                now=now,
            )
        return access

    async def request(access, request_id, kind, **kwargs):
        return await broker.request(
            ProcessWorkerRequest(
                worker_id=access.worker_id,
                credential=access.credential,
                broker_audience="history-fixture",
                request_id=request_id,
                kind=kind,
                **kwargs,
            )
        )

    async def action(access, index):
        begin = time.perf_counter()
        observed = await request(access, f"claim-{index}", "claim")
        claim_done = time.perf_counter()
        proposed = await request(
            access,
            f"propose-{index}",
            "propose",
            assignment_id=observed.assignment_id,
            observation_request_id=f"claim-{index}",
            proposal={
                "event_kind": ProcessEventKind.STATE_CHECKPOINTED,
                "target_class": "scientific_math",
                "incremental_usage": {"actions": 1, "wall_time_seconds": 10.0},
            },
        )
        assert proposed.disposition.value == "admitted"
        proposal_done = time.perf_counter()
        async with database.transaction() as session:
            receipt, _ = await broker.inspect_request(
                session, worker_id=access.worker_id, request_id=f"propose-{index}"
            )
            assignment = await store.workers.assignment(session, observed.assignment_id)
            row = await session.get(ProcessRolloutRow, assignment.rollout_id)
            state = await store.get_state(session, state_id=row.current_state_id)
            plan = state.payload.plan
            if shape == "growing":
                plan = (*plan, f"Public synthetic progress {index:04d}. ".ljust(128, "."))
            updated = state.payload.model_copy(
                update={
                    "plan": plan,
                    "budget_usage": state.payload.budget_usage.model_copy(
                        update={"actions": index + 1, "wall_time_seconds": 10.0 * (index + 1)}
                    ),
                }
            )
            await store.append_event(
                session,
                rollout_id=row.rollout_id,
                lease_token=row.lease_token,
                amber_decision_id=receipt.decision_id,
                kind=ProcessEventKind.STATE_CHECKPOINTED,
                actor_id=access.worker_id,
                payload={"summary": "Pure synthetic state update"},
                resulting_state=updated,
                worker_access=access.assigned(observed.assignment_id),
            )
        end = time.perf_counter()
        return {
            "total_seconds": end - begin,
            "claim_observe_seconds": claim_done - begin,
            "proposal_seconds": proposal_done - claim_done,
            "commit_seconds": end - proposal_done,
        }

    async def snapshot(manifest):
        async with database.transaction() as session:
            row = await session.get(ProcessRolloutRow, manifest["rollout"])
            state = await store.get_state(session, state_id=row.current_state_id)
            initial, events = await store.replay(session, rollout_id=row.rollout_id)
            plan = await ProcessTaskStore().check_rollout(session, row)
            account = await amber.resources.inspect(session, manifest["authorization"])
            await amber.resources.replay(session, manifest["authorization"])
            assert plan.digest == manifest["task_plan"]
            assert (
                await amber.resources.grant(session, manifest["authorization"])
            ).digest == manifest["grant"]
            assert state.payload.budget_usage.actions == len(events)
            assert account.open_reservations == 0
            assert account.charged.actions == len(events)
            assert account.charged.action_microseconds == 10_000_000 * len(events)
            assert not any(account.held.model_dump().values())
            assert not account.stopped
            states = [
                initial,
                *[await store.get_state(session, state_id=e.resulting_state_id) for e in events],
            ]
            for index, version in enumerate(states):
                expected_plan = ("Keep the declared synthetic updates",)
                if shape == "growing":
                    expected_plan += tuple(
                        f"Public synthetic progress {i:04d}. ".ljust(128, ".") for i in range(index)
                    )
                assert version.payload.plan == expected_plan
                assert version.payload.budget_usage.actions == index
            return {
                "state_digest": state.state_digest,
                "payload_digest": sha256_digest(state.payload),
                "state_bytes": len(canonical_json_bytes(state.payload)),
                "all_state_bytes": sum(len(canonical_json_bytes(s)) for s in states),
                "all_event_bytes": sum(len(canonical_json_bytes(e)) for e in events),
                "events": len(events),
                "account_digest": account.digest,
                "account": account.model_dump(mode="json"),
                "task_plan": plan.digest,
            }

    try:
        if phase == "build":
            await database.create_schema()
            now = datetime.now(UTC)
            model = worker_model().model_copy(
                update={
                    "model_id": "history-fixture-no-inference",
                    "checkpoint": component("trusted-synthetic-client"),
                    "protocol": "in-process-synthetic-client",
                }
            )
            initial = ProjectStatePayload(
                objective="Measure bounded pure state history",
                plan=("Keep the declared synthetic updates",),
            )
            async with database.transaction() as session:
                registry = ProcessDistributionRegistry()
                dd = await registry.register_distribution(session, distribution())
                pd = await registry.register_program(session, program(dd))
                instance = await registry.sample(
                    session,
                    distribution_digest=dd,
                    split=ProjectSplit.TRAIN,
                    seed=history,
                    generator=DeterministicProjectGenerator(),
                )
                authority = envelope(program_digest=pd, distribution_digest=dd, model=model)
                authority = authority.model_copy(
                    update={
                        "created_at": now - timedelta(minutes=3),
                        "expires_at": now + timedelta(minutes=15),
                        "checkpoint_policy": authority.checkpoint_policy.model_copy(
                            update={"training_permitted": False}
                        ),
                        "budgets": authority.budgets.model_copy(
                            update={
                                "actions": history + 1,
                                "input_tokens": 1,
                                "output_tokens": 1,
                                "cost": 0.0,
                            }
                        ),
                    }
                )
                await amber.prepare(session, envelope=authority, actor_id="preparer")
                for status, minutes in ((AmberStatus.AUTHORIZED, 2), (AmberStatus.ACTIVE, 1)):
                    await amber.transition(
                        session,
                        authorization_digest=authority.digest,
                        to_status=status,
                        actor_id="reviewer-a",
                        reason="bounded CPU measurement",
                        evidence_refs=("review:history-measurement",),
                        occurred_at=now - timedelta(minutes=minutes),
                    )
                grant = await fund_resources(session, amber, authority)
                manifest = execution(
                    program_digest=pd,
                    distribution_digest=dd,
                    instance=instance,
                    authorization_digest=authority.digest,
                    model=model,
                )
                ed = await store.register_execution(session, manifest)
                await store.workers.enroll(
                    session,
                    ProcessWorkerScope(
                        execution_digest=ed,
                        authorization_digest=authority.digest,
                        broker_audience="history-fixture",
                        maximum_credential_seconds=900,
                        maximum_registered_workers=1,
                        maximum_request_records=2 * (history + 1),
                        maximum_request_history_bytes=16 * 1024**2,
                        reviewed_by="reviewer-a",
                        review_evidence="one trusted synthetic client; no inference",
                        created_at=now,
                    ),
                )
                plan = await task_plan(session, amber, manifest, initial, now)
                await ProcessTaskStore().enroll(session, plan, now=now)
                rollout = await store.create_rollout(
                    session,
                    execution_digest=ed,
                    replication_index=0,
                    initial_state=initial,
                    rollout_id=plan.tasks[0].rollout_id,
                    created_at=now,
                )
            manifest = {
                "execution": ed,
                "rollout": rollout.rollout_id,
                "authorization": authority.digest,
                "task_plan": plan.digest,
                "grant": grant.digest,
                "model": sha256_digest(model),
            }
            write(root / "fixture.json", manifest)
            access = await issue(manifest)
            build_start = time.perf_counter()
            build_actions = [await action(access, index) for index in range(history)]
            build_seconds = time.perf_counter() - build_start
            before = await snapshot(manifest)
            samples = []
            for _ in range(6):  # first traversal warms the path, never the end-to-end action
                start = time.perf_counter()
                async with database.transaction() as session:
                    await amber.resources.assert_rollout_recoverable(
                        session, rollout_id=manifest["rollout"]
                    )
                samples.append(time.perf_counter() - start)
            counter = [0]

            def count(*_args):
                counter[0] += 1

            event.listen(database.engine.sync_engine, "before_cursor_execute", count)
            async with database.transaction() as session:
                await amber.resources.assert_rollout_recoverable(
                    session, rollout_id=manifest["rollout"]
                )
            event.remove(database.engine.sync_engine, "before_cursor_execute", count)
            measured = await action(access, history)
            after = await snapshot(manifest)
            write(
                root / "build.json",
                {
                    "history": history,
                    "shape": shape,
                    "before": before,
                    "after": after,
                    "build_seconds": build_seconds,
                    "build_actions": build_actions,
                    "integrity_warmup_seconds": samples[0],
                    "integrity_seconds": samples[1:],
                    "integrity_sql_statements": counter[0],
                    "action": measured,
                },
            )
        else:
            manifest = json.loads((root / "fixture.json").read_text())
            before = json.loads((root / "build.json").read_text())["after"]
            start = time.perf_counter()
            async with database.transaction() as session:
                for row in await session.scalars(select(ProcessWorkerRegistrationRow)):
                    await store.workers.revoke(
                        session,
                        worker_id=row.worker_id,
                        revoked_by="reviewer-a",
                        evidence="owning parent reaped builder before fresh interpreter",
                        now=datetime.now(UTC),
                    )
            reconstructed = await snapshot(manifest)
            assert reconstructed == before
            replay_done = time.perf_counter()
            access = await issue(manifest)
            observed = await request(access, "restart-claim", "claim")
            assert sha256_digest(observed.observation.state) == before["payload_digest"]
            claim_done = time.perf_counter()
            async with database.transaction() as session:
                row = await session.get(ProcessRolloutRow, manifest["rollout"])
                await store.release_claim(
                    session,
                    rollout_id=row.rollout_id,
                    lease_token=row.lease_token,
                    to_status=RolloutStatus.ACTIVE,
                    worker_access=access.assigned(observed.assignment_id),
                )
                await store.workers.revoke(
                    session,
                    worker_id=access.worker_id,
                    revoked_by="reviewer-a",
                    evidence="measurement finished; no effect dispatched",
                    now=datetime.now(UTC),
                )
            assert await snapshot(manifest) == before
            write(
                root / "restart.json",
                {
                    "revoke_replay_seconds": replay_done - start,
                    "issue_claim_observe_seconds": claim_done - replay_done,
                    "observation_bytes": len(canonical_json_bytes(observed.observation)),
                    "state_account_task_unchanged": True,
                },
            )
    finally:
        await database.close()


def supervise(command, root, log_prefix, started, monitor_count):
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
    }
    begin = time.perf_counter()
    peak_rss = 0
    with (
        log_prefix.with_suffix(".stdout").open("wb") as out,
        log_prefix.with_suffix(".stderr").open("wb") as err,
    ):
        process = subprocess.Popen(
            command,
            cwd=REPO,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                now = time.perf_counter()
                sizes = files(root)
                monitor_count[0] += 1
                if monitor_count[0] > LIMITS["monitor_processes"]:
                    raise RuntimeError("measurement monitoring subprocess bound reached")
                rss = subprocess.run(
                    ["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                ).stdout.strip()
                peak_rss = max(peak_rss, int(rss or 0) * 1024)
                if (
                    now - begin > LIMITS["child_seconds"]
                    or now - started > LIMITS["total_seconds"]
                    or peak_rss > LIMITS["child_rss_bytes"]
                    or sum(sizes.values()) > LIMITS["retained_bytes"]
                    or len(sizes) > LIMITS["retained_files"]
                ):
                    raise RuntimeError("measurement resource bound reached")
                time.sleep(1.0)
            if process.returncode:
                raise RuntimeError(
                    f"owned measurement child failed: {process.returncode}; inspect private log"
                )
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            write(
                log_prefix.with_suffix(".process.json"),
                {
                    "pid": process.pid,
                    "exit_code": process.returncode,
                    "reaped": True,
                    "seconds": time.perf_counter() - begin,
                    "observed_peak_rss_bytes": peak_rss,
                },
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--phase", choices=("build", "restart"))
    parser.add_argument("--history", type=int, choices=(*RUNG_VALUES, 2))
    parser.add_argument("--shape", choices=("flat", "growing"))
    args = parser.parse_args()
    os.umask(0o077)
    root = args.output.resolve()
    if not root.is_relative_to(REPO / "runs") or root == REPO / "runs":
        parser.error("measurement output must be a new private repository runs directory")
    if args.phase:
        declaration = json.loads((root.parent / "manifest.json").read_text())
        declared_names = {
            f"{shape}-h{history:03d}-r{rep}"
            for history, shape, rep in declaration["cases"]
            if history == args.history and shape == args.shape
        }
        if root.name not in declared_names:
            parser.error("child fixture is not declared by its owning measurement")
        if args.phase == "build" and (root / "institution.sqlite").exists():
            parser.error("fixture build requires an unused database path")
        if (
            digest(Path(__file__).read_bytes())
            != declaration["source_sha256"]["scripts/measure_pprl_history.py"]
        ):
            parser.error("child source differs from the frozen measurement")
        resource.setrlimit(resource.RLIMIT_CPU, (150, 160))
        resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
        asyncio.run(child(root, history=args.history, shape=args.shape, phase=args.phase))
        return
    root.mkdir(mode=0o700, parents=False)
    rungs, repeats = ((0, 2), 1) if args.smoke else (RUNG_VALUES, 3)
    cases = [
        (h, shape, rep) for h in rungs for rep in range(repeats) for shape in ("flat", "growing")
    ]
    source_paths = (
        subprocess.check_output(["git", "ls-files", "-z", "*.py", "pyproject.toml"], cwd=REPO)
        .decode()
        .split("\0")
    )
    source_paths = sorted(set(filter(None, source_paths)) | {str(Path(__file__).relative_to(REPO))})
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "limits": LIMITS,
        "cases": cases,
        "total_declared_actions": sum(h + 1 for h, _, _ in cases),
        "source_sha256": {p: digest((REPO / p).read_bytes()) for p in source_paths},
        "python": sys.version,
        "python_executable_sha256": digest(Path(sys.executable).read_bytes()),
        "host": {
            "system": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "versions": {
            p: importlib.metadata.version(p)
            for p in ("sqlalchemy", "aiosqlite", "pydantic", "sympy")
        },
    }
    write(root / "manifest.json", manifest)
    (root / "measurement-source.py").write_bytes(Path(__file__).read_bytes())
    started = time.perf_counter()
    completed = []
    monitor_count = [0]
    try:
        for history, shape, rep in cases:
            case = root / f"{shape}-h{history:03d}-r{rep}"
            case.mkdir(mode=0o700)
            for phase in ("build", "restart"):
                command = [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--output",
                    str(case),
                    "--phase",
                    phase,
                    "--history",
                    str(history),
                    "--shape",
                    shape,
                ]
                supervise(command, root, case / phase, started, monitor_count)
            sizes = files(case)
            write(case / "retention.json", sizes)
            completed.append(case.name)
            print(
                json.dumps({"completed": case.name, "retained_bytes": sum(sizes.values())}),
                flush=True,
            )
        assert all(
            digest((REPO / p).read_bytes()) == value
            for p, value in manifest["source_sha256"].items()
        )
        status = "passed"
    except BaseException:
        status = "stopped"
        raise
    finally:
        write(
            root / "result.json",
            {
                "status": status,
                "completed": completed,
                "seconds": time.perf_counter() - started,
                "retained_bytes": sum(files(root).values()),
                "owned_children_reaped": True,
                "monitor_processes": monitor_count[0],
            },
        )


if __name__ == "__main__":
    main()
