from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import (
    RewardComponentPolicy,
    RewardMissingAction,
    RewardNormalization,
    RewardPolicy,
    TrainingEligibilityDecision,
    TrainingLane,
)
from padawan.domains.magellan_improvement.contracts import (
    MagellanAgentTrace,
    MagellanScenarioManifest,
    MagellanVerificationBundle,
)
from padawan.models.contracts import CorpusPool, ResearchRole
from padawan.models.hashing import sha256_digest

MAGELLAN_REWARD_POLICY_ID = "padawan.magellan.task_utility"
MAGELLAN_REWARD_POLICY_VERSION = "1.0.0"
MAGELLAN_ELIGIBILITY_POLICY_ID = "padawan.magellan.training_eligibility"
MAGELLAN_ELIGIBILITY_POLICY_VERSION = "1.0.0"


def default_magellan_reward_policy(*, created_at: datetime | None = None) -> RewardPolicy:
    """Return the recomputable utility applied only after lexicographic hard gates pass."""

    weights = {
        "task_completion": 1.0,
        "constraint_satisfaction": 0.75,
        "recovery_behavior": 0.25,
        "tool_efficiency": 0.15,
        "normalized_cost": -0.1,
    }
    return RewardPolicy(
        policy_id=MAGELLAN_REWARD_POLICY_ID,
        version=MAGELLAN_REWARD_POLICY_VERSION,
        description=(
            "Magellan task utility after environment, authorization, trace, and safety gates; "
            "the scalar never compensates for a failed gate."
        ),
        components=tuple(
            RewardComponentPolicy(
                component_id=component_id,
                weight=weight,
                normalization=RewardNormalization(clamp_min=0.0, clamp_max=1.0),
                missing_action=(
                    RewardMissingAction.OMIT
                    if component_id == "recovery_behavior"
                    else RewardMissingAction.INVALIDATE_UTILITY
                ),
            )
            for component_id, weight in weights.items()
        ),
        created_at=created_at or datetime.now(UTC),
    )


def decide_magellan_training_eligibility(
    *,
    reward_id: str,
    bundle: MagellanVerificationBundle,
    trace: MagellanAgentTrace,
    scenario: MagellanScenarioManifest,
    created_at: datetime | None = None,
) -> TrainingEligibilityDecision:
    """Classify one immutable trace; pair construction remains a compiler responsibility."""

    if bundle.trace_id != trace.trace_id:
        raise ValueError("Magellan eligibility trace differs from its verification bundle")
    if bundle.trace_digest != sha256_digest(trace.model_dump(mode="json")):
        raise ValueError("Magellan eligibility trace content differs from its verification bundle")
    if bundle.environment_fingerprint != trace.environment_fingerprint:
        raise ValueError("Magellan eligibility environment differs from its verification bundle")
    if bundle.scenario_id != scenario.scenario_id or bundle.scenario_digest != sha256_digest(
        scenario.model_dump(mode="json")
    ):
        raise ValueError("Magellan eligibility scenario differs from its verification bundle")
    if bundle.scenario_split != scenario.split:
        raise ValueError("Magellan eligibility split differs from its verification bundle")
    try:
        pool = CorpusPool(scenario.split)
    except ValueError as exc:
        raise ValueError("Magellan scenario split is not a governed corpus pool") from exc

    allowed: list[TrainingLane] = [TrainingLane.EVALUATION_ONLY]
    excluded: dict[TrainingLane, str] = {
        TrainingLane.CONTINUED_PRETRAINING: (
            "agent episodes are not licensed source corpora for continued pretraining"
        )
    }
    candidate_lanes = {
        TrainingLane.SFT,
        TrainingLane.PREFERENCE,
        TrainingLane.RLVR,
        TrainingLane.PROCESS,
    }
    hard_gates_pass = all(gate.passed for gate in bundle.hard_gates)
    if trace.research_role != ResearchRole.TARGET:
        reason = "non-target traces are diagnostic and excluded from target training by default"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif pool in {CorpusPool.SEALED_ANCHOR, CorpusPool.QUARANTINE}:
        reason = "sealed or quarantined evidence is structurally evaluation-only"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif not hard_gates_pass:
        reason = "a Magellan environment, authorization, trace, or safety hard gate failed"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif bundle.task_verified:
        allowed.extend(
            (
                TrainingLane.SFT,
                TrainingLane.PREFERENCE,
                TrainingLane.RLVR,
                TrainingLane.PROCESS,
            )
        )
    else:
        allowed.extend((TrainingLane.PREFERENCE, TrainingLane.PROCESS))
        excluded[TrainingLane.SFT] = "unsuccessful trajectories are not SFT exemplars"
        excluded[TrainingLane.RLVR] = "RLVR candidates require verified task completion"

    ordered_allowed = tuple(sorted(allowed, key=lambda lane: lane.value))
    ordered_excluded = dict(sorted(excluded.items(), key=lambda entry: entry[0].value))
    timestamp = created_at or bundle.created_at
    decision_digest = sha256_digest(
        {
            "policy_id": MAGELLAN_ELIGIBILITY_POLICY_ID,
            "policy_version": MAGELLAN_ELIGIBILITY_POLICY_VERSION,
            "reward_id": reward_id,
            "trace_id": trace.trace_id,
            "pool": pool.value,
            "allowed": ordered_allowed,
            "excluded": ordered_excluded,
            "created_at": timestamp,
        }
    )
    return TrainingEligibilityDecision(
        decision_id=f"magellan-eligibility-{decision_digest[7:31]}",
        policy_id=MAGELLAN_ELIGIBILITY_POLICY_ID,
        policy_version=MAGELLAN_ELIGIBILITY_POLICY_VERSION,
        allowed_lanes=ordered_allowed,
        excluded_lanes=ordered_excluded,
        evidence_refs=(reward_id, *[result.result_id for result in bundle.verifier_results]),
        created_at=timestamp,
    )
