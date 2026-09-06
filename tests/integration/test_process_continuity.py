"""Actual native loss/restart, fixed public task and independently checked engineering evidence."""

import json

import pytest

from padawan.models.hashing import canonical_json_bytes
from padawan.pprl.container_contracts import ProcessContainerProfile
from tests.continuity_supervisor import NativePhase, no_retained_credentials


def assert_complete(snapshot):
    assert snapshot["rollout"]["status"] == "complete"
    assert snapshot["rollout"]["sequence"] == 7
    hypotheses = snapshot["state"]["payload"]["hypotheses"]
    rejected = [int(h["statement"]) for h in hypotheses if h["status"] == "rejected"]
    supported = [int(h["statement"]) for h in hypotheses if h["status"] == "supported"]
    assert rejected == [2, 3, 5, 7, 11]
    assert supported == [13]
    assert all(221 % value != 0 for value in rejected)
    assert all(1 < value < 221 and 221 % value == 0 for value in supported)
    tested = []
    previous = snapshot["initial"]["payload"]["hypotheses"]
    for state in snapshot["states"][:-1]:
        current = state["payload"]["hypotheses"]
        changed = [
            after for before, after in zip(previous, current, strict=True) if before != after
        ]
        assert len(changed) == 1
        value = int(changed[0]["statement"])
        assert changed[0]["status"] == ("supported" if 221 % value == 0 else "rejected")
        tested.append(value)
        previous = current
    assert tested == [2, 3, 5, 7, 11, 13]
    assert snapshot["events"][-1]["kind"] == "project_completed"
    assert not any(snapshot["account"]["held"].values())
    assert snapshot["account"]["charged"]["actions"] == 7
    assert snapshot["account"]["charged"]["input_tokens"] == 0
    assert snapshot["account"]["charged"]["output_tokens"] == 0
    assert snapshot["account"]["charged"]["micro_usd"] == 0
    assert snapshot["account"]["open_reservations"] == 0


def assert_identity(before, after):
    for key in ("rollout_id", "execution_digest", "replication_index", "initial_state_id"):
        assert before["rollout"][key] == after["rollout"][key]
    assert before["task_plan_digest"] == after["task_plan_digest"]
    assert before["account"]["grant_digest"] == after["account"]["grant_digest"]


def assert_replaced(phases):
    ids = [identity["worker_id"] for phase in phases for identity in phase.identities]
    assert len(ids) == len(set(ids))
    for phase in phases:
        assert len(phase.identities) == 2
        for identity in phase.identities:
            messages = [
                item
                for item in phase.journal["steps"]
                if item.get("worker_id") == identity["worker_id"]
            ]
            assert messages and messages[0]["local_index"] == 0
        assert phase.journal["scratch_removed"]
        assert len(phase.journal["cleanup"]) == 3


@pytest.fixture(scope="module")
async def scripted_reference(tmp_path_factory):
    root = tmp_path_factory.mktemp("scripted-reference") / "institution"
    async with NativePhase(root, 0) as phase:
        final = await phase.finish()
        assert_complete(final)
        compiled = await phase.command("compile")
        assert not any(compiled["products"].values())
        await phase.audit([phase])
    assert_replaced([phase])
    no_retained_credentials(root, [phase])
    return final


@pytest.mark.parametrize(
    "fault", ["after_claim", "after_admission", "before_commit", "after_commit"]
)
async def test_native_crash_preserves_task_state_negative_results_and_costs(
    tmp_path, scripted_reference, fault
):
    root = tmp_path / "institution"
    async with NativePhase(root, 0) as first:
        await first.action(0)
        await first.action(1)
        before = first.snapshot
        if fault in {"before_commit", "after_commit"}:
            await first.action(0, crash=fault)
        else:
            await first.claim(0)
            if fault == "after_admission":
                await first.propose(0)
        await first.close(crash=True)
    assert {item["returncode"] for item in first.journal["cleanup"]} == {-9}
    async with NativePhase(root, 1) as second:
        after = second.snapshot
        assert_identity(before, after)
        assert second.recovery["disposition"] == "ready"
        expected_worker = None if fault == "after_commit" else first.identities[0]["worker_id"]
        assert second.recovery["previous_worker_id"] == expected_worker
        assert after["rollout"]["sequence"] == (3 if fault == "after_commit" else 2)
        assert after["account"]["charged"]["actions"] == after["rollout"]["sequence"]
        assert not any(after["account"]["held"].values())
        if fault != "after_commit":
            assert after["state"] == before["state"]
            assert after["account"]["charged"] == before["account"]["charged"]
        assert [effect["disposition"] for effect in second.recovery["effects"]] == (
            ["released_unstarted"] if fault in {"after_admission", "before_commit"} else []
        )
        final = await second.finish()
        assert_complete(final)
        assert canonical_json_bytes(final["state"]["payload"]) == canonical_json_bytes(
            scripted_reference["state"]["payload"]
        )
        assert final["account"]["charged"] == scripted_reference["account"]["charged"]
        await second.audit([first, second])
    assert_replaced([first, second])
    no_retained_credentials(root, [first, second])


async def test_repeated_complete_roster_churn_and_explicit_pause_resume(
    tmp_path, scripted_reference
):
    root = tmp_path / "institution"
    phases = []
    for index in range(3):
        async with NativePhase(root, index) as phase:
            phases.append(phase)
            if index:
                assert phase.snapshot["state"] == phases[index - 1].snapshot["state"]
                assert (
                    phase.snapshot["account_digest"] == phases[index - 1].snapshot["account_digest"]
                )
                assert_identity(phases[0].snapshot, phase.snapshot)
            await phase.action(0)
            await phase.action(1)
            if index == 1:
                before = phase.snapshot
                await phase.command("pause", paused=True)
                denied = await phase.rpc(0, "claim")
                assert denied == {"error": "worker_request_denied"}
                paused = await phase.command("snapshot")
                assert paused["state"] == before["state"]
                assert paused["account_digest"] == before["account_digest"]
                await phase.command("pause", paused=False)
            if index == 2:
                final = await phase.finish()
                assert_complete(final)
                assert final["state"]["payload"] == scripted_reference["state"]["payload"]
                assert final["account"]["charged"] == scripted_reference["account"]["charged"]
                await phase.audit(phases)
    assert_replaced(phases)
    no_retained_credentials(root, phases)


@pytest.mark.parametrize("kind", ["unknown", "synthetic_completed"])
async def test_lost_effect_stops_same_task_and_keeps_original_hold_or_charge(tmp_path, kind):
    root = tmp_path / "institution"
    async with NativePhase(root, 0) as first:
        await first.action(0)
        await first.action(1)
        before = first.snapshot
        await first.claim(0)
        profile = ProcessContainerProfile.model_validate_json(
            json.dumps(json.loads((root / "fixture.json").read_text())["profile"])
        )
        extra = {
            "synthetic_tool": {
                "digest": profile.tool_digest,
                "maximum_retention_bytes": profile.maximum_retention_bytes,
            }
        }
        proposal = await first.propose(0, **extra)
        await first.command("effect", kind=kind, **proposal)
        stopped = await first.command("snapshot")
        await first.close(crash=True)
    async with NativePhase(root, 1) as second:
        assert second.identities == []
        assert second.recovery["disposition"] == "review_required"
        assert second.snapshot["state"] == before["state"]
        assert_identity(before, second.snapshot)
        assert second.snapshot["account_digest"] == stopped["account_digest"]
        effects = second.recovery["effects"]
        assert len(effects) == 1
        assert effects[0]["disposition"] == (
            "unknown" if kind == "unknown" else "completed_unadmitted"
        )
        account = second.snapshot["account"]
        assert account["charged"]["actions"] == (2 if kind == "unknown" else 3)
        assert account["held"]["actions"] == (1 if kind == "unknown" else 0)
        receipt = await second.command(
            "abandon", recovery_id=second.recovery["request"]["recovery_id"]
        )
        assert receipt["parameter_training_eligible"] is False
        final = await second.command("snapshot")
        assert final["rollout"]["status"] == "cancelled"
        assert final["state"] == before["state"]
        assert final["account_digest"] == stopped["account_digest"]
        compiled = await second.command("compile")
        assert compiled["rollout_ids"] == [before["rollout"]["rollout_id"]]
        assert not any(compiled["products"].values())
        assert any(
            "process_rollout_abandoned" in item["reason_codes"] for item in compiled["exclusions"]
        )
    # Reopen again: terminality and original accounting survive another native broker.
    async with NativePhase(root, 2) as third:
        assert third.identities == []
        assert third.snapshot["state"] == before["state"]
        assert third.snapshot["account_digest"] == final["account_digest"]
        assert third.snapshot["rollout"]["status"] == "cancelled"
        await third.audit([first, second, third])
    no_retained_credentials(root, [first, second, third])


async def test_scripted_executor_refuses_premature_completion_and_recovers_unstarted_work(
    tmp_path, scripted_reference
):
    root = tmp_path / "institution"
    with pytest.raises(AssertionError, match="scripted action mask violation"):
        async with NativePhase(root, 0) as first:
            await first.action(0)
            await first.action(1)
            before = first.snapshot
            await first.claim(0)
            proposal = await first.propose(0, premature_completion=True)
            await first.command("commit", **proposal)
    async with NativePhase(root, 1) as second:
        assert second.snapshot["state"] == before["state"]
        assert second.snapshot["account"]["charged"] == before["account"]["charged"]
        assert [item["disposition"] for item in second.recovery["effects"]] == [
            "released_unstarted"
        ]
        final = await second.finish()
        assert_complete(final)
        assert final["state"]["payload"] == scripted_reference["state"]["payload"]
        await second.audit([first, second])
    assert_replaced([first, second])
    no_retained_credentials(root, [first, second])
