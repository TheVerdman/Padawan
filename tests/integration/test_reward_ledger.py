from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.domains.contracts import (
    HardGateResult,
    RewardObservation,
    TrainingEligibilityDecision,
    TrainingLane,
    VerifierDisposition,
    VerifierResult,
)
from padawan.experiments.engine import ExperimentEngine
from padawan.reporting import ReportingService
from padawan.rewards import RewardEngine, default_meta_utility_policy
from padawan.state.store import StateStore

_NOW = datetime(2026, 8, 1, tzinfo=UTC)


def _verifier(result_id: str, disposition: VerifierDisposition) -> VerifierResult:
    return VerifierResult(
        result_id=result_id,
        verifier_id="test.integrity",
        verifier_version="1",
        scope="episode-test",
        disposition=disposition,
        deterministic=True,
        summary=f"integrity was {disposition.value}",
        evidence={"source": "deterministic-test-evidence"},
        created_at=_NOW,
    )


def _observations(*, missing: str | None = None) -> tuple[RewardObservation, ...]:
    values = {
        "held_out_capability_delta": 0.1,
        "unseen_transfer": 0.2,
        "delayed_retention": 0.3,
        "interference_and_regression": 0.1,
        "harmful_interventions": 0.0,
        "contamination": 0.0,
        "normalized_cost": 0.5,
    }
    return tuple(
        RewardObservation(
            component_id=component_id,
            value=None if component_id == missing else value,
            evidence_refs=(f"evidence:{component_id}",),
            missing_reason=(
                "retention window has not elapsed" if component_id == missing else None
            ),
        )
        for component_id, value in values.items()
    )


async def test_reward_ledger_recomputes_from_policy_and_raw_components(database) -> None:
    engine = RewardEngine()
    policy = default_meta_utility_policy(created_at=_NOW)
    verifier = _verifier("verifier-integrity-pass", VerifierDisposition.VERIFIED)
    gate = HardGateResult(
        gate_id="environment-integrity",
        passed=True,
        disposition=VerifierDisposition.VERIFIED,
        evidence_refs=(verifier.result_id,),
        reason="environment fingerprint and authorization checks passed",
    )

    async with database.transaction() as session:
        await engine.register_policy(session, policy)
        await engine.record_verifier_result(session, verifier)
        reward = await engine.compute(
            session,
            reward_id="reward-complete",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            hard_gates=(gate,),
            observations=_observations(),
        )
        replay = await engine.compute(
            session,
            reward_id="reward-complete",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            hard_gates=(gate,),
            observations=_observations(),
        )
        recomputation = await engine.recompute(session, reward_id=reward.reward_id)
    report = await ReportingService(database, ExperimentEngine(StateStore())).reward(
        reward.reward_id
    )

    assert reward == replay
    assert reward.derived_utility == pytest.approx(0.2)
    assert recomputation.valid
    assert recomputation.recomputed_utility == pytest.approx(0.2)
    assert report["format"] == "padawan.reward_report"
    assert report["recomputation"]["valid"] is True


async def test_missing_values_and_failed_gates_never_become_implicit_zero(database) -> None:
    engine = RewardEngine()
    policy = default_meta_utility_policy(created_at=_NOW)
    verified = _verifier("verifier-missing-pass", VerifierDisposition.VERIFIED)
    rejected = _verifier("verifier-integrity-fail", VerifierDisposition.REJECTED)
    async with database.transaction() as session:
        await engine.register_policy(session, policy)
        await engine.record_verifier_result(session, verified)
        await engine.record_verifier_result(session, rejected)
        incomplete = await engine.compute(
            session,
            reward_id="reward-missing",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            hard_gates=(
                HardGateResult(
                    gate_id="integrity",
                    passed=True,
                    disposition=VerifierDisposition.VERIFIED,
                    evidence_refs=(verified.result_id,),
                    reason="integrity passed",
                ),
            ),
            observations=_observations(missing="delayed_retention"),
            created_at=_NOW,
        )
        failed = await engine.compute(
            session,
            reward_id="reward-gate-failed",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            hard_gates=(
                HardGateResult(
                    gate_id="integrity",
                    passed=False,
                    disposition=VerifierDisposition.REJECTED,
                    evidence_refs=(rejected.result_id,),
                    reason="authorization invariant failed",
                ),
            ),
            observations=_observations(),
            created_at=_NOW,
        )
        unsafe_decision = TrainingEligibilityDecision(
            decision_id="eligibility-unsafe",
            policy_id="training-policy",
            policy_version="1",
            allowed_lanes=(TrainingLane.PROCESS,),
            excluded_lanes={},
            evidence_refs=(failed.reward_id,),
            created_at=_NOW,
        )
        with pytest.raises(ValueError, match="hard-gate failures"):
            await engine.record_training_eligibility(
                session, reward_id=failed.reward_id, decision=unsafe_decision
            )
        safe_decision = unsafe_decision.model_copy(
            update={
                "decision_id": "eligibility-evaluation-only",
                "allowed_lanes": (TrainingLane.EVALUATION_ONLY,),
                "excluded_lanes": {
                    TrainingLane.SFT: "integrity hard gate failed",
                    TrainingLane.RLVR: "integrity hard gate failed",
                    TrainingLane.PROCESS: "integrity hard gate failed",
                },
            }
        )
        stored = await engine.record_training_eligibility(
            session, reward_id=failed.reward_id, decision=safe_decision
        )
        eligibility_verification = await engine.verify_training_eligibility(
            session, decision_id=stored.decision_id
        )

    assert incomplete.derived_utility is None
    assert (
        next(
            component
            for component in incomplete.components
            if component.component_id == "delayed_retention"
        ).missing_reason
        == "retention window has not elapsed"
    )
    assert failed.derived_utility is None
    assert stored.reward_id == failed.reward_id
    assert eligibility_verification.valid


async def test_reward_policy_versions_are_append_only(database) -> None:
    engine = RewardEngine()
    policy = default_meta_utility_policy(created_at=_NOW)
    async with database.transaction() as session:
        await engine.register_policy(session, policy)
        with pytest.raises(ValueError, match="conflicts"):
            await engine.register_policy(
                session,
                policy.model_copy(update={"description": "rewritten in place"}),
            )
