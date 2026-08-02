from __future__ import annotations

from datetime import UTC, datetime

from padawan.domains.contracts import (
    RewardComponentPolicy,
    RewardMissingAction,
    RewardNormalization,
    RewardPolicy,
    TrainingEligibilityDecision,
    TrainingLane,
    VerifierDisposition,
)
from padawan.domains.legal.appellate.contracts import (
    AppellateScenarioManifest,
    AppellateSubmission,
    AppellateVerificationBundle,
)
from padawan.models.contracts import CorpusPool, ResearchRole
from padawan.models.hashing import sha256_digest

APPELLATE_REWARD_POLICY_ID = "padawan.appellate.brief_utility"
APPELLATE_REWARD_POLICY_VERSION = "1.0.0"
APPELLATE_ELIGIBILITY_POLICY_ID = "padawan.appellate.training_eligibility"
APPELLATE_ELIGIBILITY_POLICY_VERSION = "1.0.0"


def default_appellate_reward_policy(*, created_at: datetime | None = None) -> RewardPolicy:
    """Return the post-gate utility policy; unknown currentness is omitted, never zeroed."""

    weights = {
        "proposition_support": 1.0,
        "applicability": 0.9,
        "adverse_authority": 0.55,
        "issue_remedy_coverage": 0.45,
        "concision": 0.15,
        "currentness": 0.2,
    }
    return RewardPolicy(
        policy_id=APPELLATE_REWARD_POLICY_ID,
        version=APPELLATE_REWARD_POLICY_VERSION,
        description=(
            "Appellate semantic and coverage utility after noncompensable pack, task, rule, "
            "claim-map, record, authority, quotation, and leakage gates. Currentness is omitted "
            "when no dependable citator exists."
        ),
        components=tuple(
            RewardComponentPolicy(
                component_id=component_id,
                weight=weight,
                normalization=RewardNormalization(clamp_min=0.0, clamp_max=1.0),
                missing_action=(
                    RewardMissingAction.OMIT
                    if component_id == "currentness"
                    else RewardMissingAction.INVALIDATE_UTILITY
                ),
            )
            for component_id, weight in weights.items()
        ),
        created_at=created_at or datetime.now(UTC),
    )


def decide_appellate_training_eligibility(
    *,
    reward_id: str,
    bundle: AppellateVerificationBundle,
    submission: AppellateSubmission,
    scenario: AppellateScenarioManifest,
    created_at: datetime | None = None,
) -> TrainingEligibilityDecision:
    """Classify one immutable brief while retaining baseline and teacher evidence."""

    if bundle.brief_id != submission.brief_id:
        raise ValueError("appellate eligibility brief differs from its verification bundle")
    if bundle.submission_digest != sha256_digest(submission.model_dump(mode="json")):
        raise ValueError("appellate eligibility brief content differs from its bundle")
    if bundle.scenario_id != scenario.scenario_id:
        raise ValueError("appellate eligibility scenario differs from its bundle")
    if bundle.scenario_digest != scenario.scenario_digest:
        raise ValueError("appellate eligibility scenario content differs from its bundle")
    if bundle.scenario_split != scenario.split:
        raise ValueError("appellate eligibility split differs from its bundle")
    try:
        pool = CorpusPool(scenario.split)
    except ValueError as exc:
        raise ValueError("appellate scenario split is not a governed corpus pool") from exc

    allowed: list[TrainingLane] = [TrainingLane.EVALUATION_ONLY]
    excluded: dict[TrainingLane, str] = {
        TrainingLane.CONTINUED_PRETRAINING: (
            "brief episodes are not admitted source documents for continued pretraining"
        )
    }
    candidate_lanes = {
        TrainingLane.SFT,
        TrainingLane.PREFERENCE,
        TrainingLane.RLVR,
        TrainingLane.PROCESS,
    }
    hard_gates_pass = all(gate.passed for gate in bundle.hard_gates)
    semantic_stages = {
        "appellate.proposition_support",
        "appellate.applicability",
        "appellate.adverse_authority",
        "appellate.issue_remedy_coverage",
    }
    semantic_results = [
        result for result in bundle.verifier_results if result.verifier_id in semantic_stages
    ]
    semantic_decided = len(semantic_results) == len(semantic_stages) and all(
        result.disposition in {VerifierDisposition.VERIFIED, VerifierDisposition.REJECTED}
        for result in semantic_results
    )
    if submission.research_role != ResearchRole.TARGET:
        reason = "baseline, teacher, verifier, and adjudicator briefs are retained but excluded"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif pool != CorpusPool.CURRICULUM:
        reason = "shadow, sealed, and quarantined appellate evidence is evaluation-only"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif not hard_gates_pass:
        reason = "an appellate deterministic integrity hard gate failed"
        excluded.update({lane: reason for lane in candidate_lanes})
    elif not semantic_decided:
        reason = "semantic support or applicability remains unknown or unadjudicated"
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
        excluded[TrainingLane.SFT] = "semantically rejected briefs are not SFT exemplars"
        excluded[TrainingLane.RLVR] = "RLVR candidates require verified briefing performance"

    ordered_allowed = tuple(sorted(allowed, key=lambda lane: lane.value))
    ordered_excluded = dict(sorted(excluded.items(), key=lambda entry: entry[0].value))
    timestamp = created_at or bundle.created_at
    decision_digest = sha256_digest(
        {
            "policy_id": APPELLATE_ELIGIBILITY_POLICY_ID,
            "policy_version": APPELLATE_ELIGIBILITY_POLICY_VERSION,
            "reward_id": reward_id,
            "brief_id": submission.brief_id,
            "scenario_id": scenario.scenario_id,
            "pool": pool,
            "allowed": ordered_allowed,
            "excluded": ordered_excluded,
            "created_at": timestamp,
        }
    )
    return TrainingEligibilityDecision(
        decision_id=f"appellate-eligibility-{decision_digest[7:31]}",
        policy_id=APPELLATE_ELIGIBILITY_POLICY_ID,
        policy_version=APPELLATE_ELIGIBILITY_POLICY_VERSION,
        allowed_lanes=ordered_allowed,
        excluded_lanes=ordered_excluded,
        evidence_refs=(reward_id, *[result.result_id for result in bundle.verifier_results]),
        subject_refs=(submission.brief_id,),
        created_at=timestamp,
    )
