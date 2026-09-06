"""Read-only audit and grouped summaries of retained history measurements."""

import argparse
import hashlib
import json
import sqlite3
import statistics
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def interval(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def audit_database(path, history, shape):
    """Check actual retained rows independently of the builder's summary assertions."""
    assert not Path(str(path) + "-wal").exists()
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        expected = {
            "process_rollouts": 1,
            "process_task_plans": 1,
            "process_resource_grants": 1,
            "process_states": history + 2,
            "process_events": history + 1,
            "process_worker_registrations": 2,
            "process_worker_requests": 2 * (history + 1) + 1,
            "process_resource_reservations": history + 1,
        }
        for table, count in expected.items():  # table names are fixed above, never input SQL
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == count
        assert (
            connection.execute(
                "SELECT count(*) FROM process_worker_heads WHERE status != 'revoked'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM process_rollouts WHERE lease_token IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        states = connection.execute(
            "SELECT record_json FROM process_states ORDER BY sequence"
        ).fetchall()
        for index, (raw,) in enumerate(states):
            state = json.loads(raw)
            assert state["sequence"] == index
            assert state["payload"]["budget_usage"]["actions"] == index
            expected_plan = ["Keep the declared synthetic updates"]
            if shape == "growing":
                expected_plan += [
                    f"Public synthetic progress {i:04d}. ".ljust(128, ".") for i in range(index)
                ]
            assert state["payload"]["plan"] == expected_plan
            assert not state["payload"]["artifact_refs"] and not state["payload"]["memory_refs"]
    finally:
        connection.close()


def summarize(root):
    manifest, result = read(root / "manifest.json"), read(root / "result.json")
    source = hashlib.sha256((root / "measurement-source.py").read_bytes()).hexdigest()
    assert source == manifest["source_sha256"]["scripts/measure_pprl_history.py"]
    names = [f"{shape}-h{h:03d}-r{rep}" for h, shape, rep in manifest["cases"]]
    assert result["status"] == "passed" and result["completed"] == names
    assert result["owned_children_reaped"]
    records, processes, actions, evidence = [], [], 0, {}
    for name in names:
        case = root / name
        build, restart = read(case / "build.json"), read(case / "restart.json")
        h = build["history"]
        audit_database(case / "institution.sqlite", h, build["shape"])
        assert len(build["build_actions"]) == h
        assert len(build["integrity_seconds"]) == 5
        for label, expected in (("before", h), ("after", h + 1)):
            snapshot = build[label]
            account = snapshot["account"]
            assert snapshot["events"] == account["charged"]["actions"] == expected
            assert account["charged"]["action_microseconds"] == expected * 10_000_000
            assert account["open_reservations"] == 0 and not account["stopped"]
            assert not any(account["held"].values())
            assert account["charged"]["input_tokens"] == account["charged"]["output_tokens"] == 0
            assert account["charged"]["micro_usd"] == 0
        assert build["before"]["task_plan"] == build["after"]["task_plan"]
        assert restart["state_account_task_unchanged"]
        parent = read(case / "restart.process.json")
        for phase in ("build", "restart"):
            process = read(case / f"{phase}.process.json")
            assert process["exit_code"] == 0 and process["reaped"]
            assert process["seconds"] < manifest["limits"]["child_seconds"]
            assert process["observed_peak_rss_bytes"] <= manifest["limits"]["child_rss_bytes"]
            processes.append(process)
        records.append(
            {
                "history": h,
                "shape": build["shape"],
                "integrity_seconds": statistics.median(build["integrity_seconds"]),
                "sql_statements": build["integrity_sql_statements"],
                "action_seconds": build["action"]["total_seconds"],
                "claim_observe_seconds": build["action"]["claim_observe_seconds"],
                "proposal_seconds": build["action"]["proposal_seconds"],
                "commit_seconds": build["action"]["commit_seconds"],
                "restart_parent_seconds": parent["seconds"],
                "restart_replay_seconds": restart["revoke_replay_seconds"],
                "restart_claim_seconds": restart["issue_claim_observe_seconds"],
                "build_seconds": build["build_seconds"],
                "sqlite_bytes_after_probe": (case / "institution.sqlite").stat().st_size,
                "snapshot_bytes_at_history": build["before"]["all_state_bytes"],
                "public_state_bytes_at_history": build["before"]["state_bytes"],
                "observation_bytes_after_probe": restart["observation_bytes"],
            }
        )
        actions += h + 1
    for path in sorted(root.rglob("*")):
        if path.is_file():
            evidence[str(path.relative_to(root))] = {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    assert len(processes) <= manifest["limits"]["children"]
    assert actions == manifest["total_declared_actions"] <= manifest["limits"]["committed_actions"]
    assert result["seconds"] < manifest["limits"]["total_seconds"]
    assert len(evidence) <= manifest["limits"]["retained_files"]
    assert sum(item["bytes"] for item in evidence.values()) <= manifest["limits"]["retained_bytes"]
    groups = []
    for h, shape in sorted({(r["history"], r["shape"]) for r in records}):
        rows = [r for r in records if (r["history"], r["shape"]) == (h, shape)]
        groups.append(
            {
                "history": h,
                "shape": shape,
                "fixtures": len(rows),
                **{
                    key: interval([row[key] for row in rows])
                    for key in rows[0]
                    if key not in {"history", "shape"}
                },
            }
        )
    return {
        "passed": True,
        "independent_sqlite_audit": True,
        "source_sha256": source,
        "fixture_count": len(names),
        "committed_actions": actions,
        "owned_process_count": len(processes),
        "owned_pids": [p["pid"] for p in processes],
        "max_observed_rss_bytes": max(p["observed_peak_rss_bytes"] for p in processes),
        "seconds": result["seconds"],
        "groups": groups,
        "evidence": evidence,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    print(json.dumps(summarize(arguments.root), indent=2, sort_keys=True))
