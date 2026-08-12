from __future__ import annotations

from datetime import UTC, datetime

import pytest

from padawan.atlas.analysis import (
    ActionTraceObservation,
    CalibrationObservation,
    ConsistencyObservation,
    EfficiencyObservation,
    SelfCorrectionObservation,
    calibration_metrics,
    consistency_metrics,
    efficiency_metrics,
    repeated_action_metrics,
    self_correction_metrics,
)
from padawan.atlas.boundary import (
    BinaryTrialObservation,
    BoundaryCandidate,
    DifficultyBin,
    MetamorphicProbePair,
    MetamorphicRelationKind,
    RaschDesign,
    RaschItemDifficulty,
    RaschResponse,
    RepeatedTrialPlan,
    StopDecision,
    allocate_near_boundary,
    assess_stop_rule,
    build_capability_curve,
    build_rasch_design,
    estimate_rasch_ability,
    evaluate_metamorphic_pair,
    repeated_trial_analysis,
    wilson_interval,
)
from padawan.atlas.contracts import EvaluationClass, StopRule, SuiteStatus
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _digest(value: object) -> str:
    return sha256_digest(value)


def test_wilson_interval_and_fixed_denominator_preserve_missingness() -> None:
    plan = RepeatedTrialPlan(item_ids=("item-a",), trials_per_item=3, pass_k=(1, 2))
    observations = (
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=0,
            success=True,
            result_digest=_digest("result-a-0"),
        ),
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=1,
            success=None,
            missing_reason="timeout",
            infrastructure_failure=True,
            result_digest=_digest("result-a-1"),
        ),
    )

    analysis = repeated_trial_analysis(plan, observations)

    assert analysis.aggregate.value == 1.0
    assert analysis.aggregate.planned_trials == 3
    assert analysis.aggregate.observed_trials == 1
    assert analysis.aggregate.missing_trials == 2
    assert analysis.aggregate.infrastructure_failures == 1
    assert all(estimate.value is None for estimate in analysis.items[0].pass_at_k)
    lower, upper = wilson_interval(1, 1)
    assert analysis.aggregate.lower == pytest.approx(lower)
    assert analysis.aggregate.upper == pytest.approx(upper)


def test_timeouts_and_unresolved_replicates_remain_missing_and_never_inflate_pass_at_k() -> None:
    plan = RepeatedTrialPlan(item_ids=("item-a",), trials_per_item=4, pass_k=(1, 2))
    observations = (
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=0,
            success=True,
            result_digest=_digest("success"),
        ),
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=1,
            success=None,
            result_digest=_digest("timeout"),
            missing_reason="request timeout",
            timeout=True,
        ),
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=2,
            success=None,
            missing_reason="result not yet available",
        ),
    )

    analysis = repeated_trial_analysis(plan, observations)
    summary = analysis.items[0]

    assert summary.missing_trials == 3
    assert summary.timeout_trials == 1
    assert summary.infrastructure_failures == 0
    assert summary.unresolved_trials == 2
    assert analysis.aggregate.infrastructure_failures == 1
    assert all(item.value is None for item in summary.pass_at_k)


def test_repeated_trial_plan_rejects_replicate_and_result_inflation() -> None:
    plan = RepeatedTrialPlan(item_ids=("item-a",), trials_per_item=2, pass_k=(1,))
    outside_plan = BinaryTrialObservation(
        item_id="item-a",
        trial_index=2,
        success=True,
        result_digest=_digest("outside"),
    )
    with pytest.raises(ValueError, match="exceeds"):
        repeated_trial_analysis(plan, (outside_plan,))

    reused = _digest("same-result")
    observations = (
        BinaryTrialObservation(item_id="item-a", trial_index=0, success=True, result_digest=reused),
        BinaryTrialObservation(item_id="item-a", trial_index=1, success=True, result_digest=reused),
    )
    with pytest.raises(ValueError, match="inflate"):
        repeated_trial_analysis(plan, observations)


def test_pass_at_k_is_only_computed_for_complete_predeclared_replicates() -> None:
    plan = RepeatedTrialPlan(item_ids=("item-a",), trials_per_item=3, pass_k=(1, 2, 3))
    observations = tuple(
        BinaryTrialObservation(
            item_id="item-a",
            trial_index=index,
            success=index == 0,
            result_digest=_digest(f"complete-{index}"),
        )
        for index in range(3)
    )

    estimates = repeated_trial_analysis(plan, observations).items[0].pass_at_k

    assert tuple(item.value for item in estimates) == pytest.approx((1 / 3, 2 / 3, 1.0))


def test_weighted_pava_produces_nonincreasing_boundary_curve() -> None:
    bins = (
        DifficultyBin(
            difficulty=0.0,
            planned_trials=10,
            successes=8,
            failures=2,
            missing_trials=0,
        ),
        DifficultyBin(
            difficulty=1.0,
            planned_trials=10,
            successes=2,
            failures=8,
            missing_trials=0,
        ),
        DifficultyBin(
            difficulty=2.0,
            planned_trials=10,
            successes=6,
            failures=4,
            missing_trials=0,
        ),
    )

    curve = build_capability_curve(
        family_id="math.boundary",
        condition_id="standardized",
        bins=bins,
        boundary_probability=0.5,
    )

    assert tuple(point.success_probability for point in curve.points) == pytest.approx(
        (0.8, 0.4, 0.4)
    )
    assert curve.monotonic_violations == 1
    assert curve.estimated_boundary == pytest.approx(0.75)


def _rasch_design(*, minimum_observed_items: int = 3) -> RaschDesign:
    return build_rasch_design(
        items=tuple(
            RaschItemDifficulty(
                item_id=f"rasch-{index}",
                item_digest=_digest(f"rasch-item-{index}"),
                difficulty=difficulty,
            )
            for index, difficulty in enumerate((-1.5, -0.5, 0.5, 1.5))
        ),
        minimum_observed_items=minimum_observed_items,
    )


def test_rasch_ability_binds_declared_difficulties_and_preserves_missing_items() -> None:
    design = _rasch_design()
    by_id = {item.item_id: item for item in design.items}
    responses = (
        RaschResponse(
            item_digest=by_id["rasch-0"].item_digest,
            success=True,
            result_digest=_digest("rasch-result-0"),
        ),
        RaschResponse(
            item_digest=by_id["rasch-1"].item_digest,
            success=True,
            result_digest=_digest("rasch-result-1"),
        ),
        RaschResponse(
            item_digest=by_id["rasch-2"].item_digest,
            success=False,
            result_digest=_digest("rasch-result-2"),
        ),
        RaschResponse(
            item_digest=by_id["rasch-3"].item_digest,
            success=None,
            missing_reason="timeout",
            result_digest=_digest("rasch-timeout"),
        ),
    )

    estimate = estimate_rasch_ability(design, tuple(reversed(responses)))

    assert estimate.converged
    assert estimate.ability is not None
    assert estimate.ability > 0.0
    assert estimate.standard_error is not None and estimate.standard_error > 0.0
    assert estimate.lower is not None and estimate.lower < estimate.ability
    assert estimate.upper is not None and estimate.upper > estimate.ability
    assert estimate.planned_items == 4
    assert estimate.observed_items == 3
    assert estimate.missing_items == 1
    assert len(estimate.evidence_result_digests) == 3


def test_rasch_boundary_cases_are_finite_but_explicitly_regularized() -> None:
    design = _rasch_design()
    successes = tuple(
        RaschResponse(
            item_digest=item.item_digest,
            success=True,
            result_digest=_digest(f"all-success-{item.item_id}"),
        )
        for item in design.items
    )
    failures = tuple(
        RaschResponse(
            item_digest=item.item_digest,
            success=False,
            result_digest=_digest(f"all-failure-{item.item_id}"),
        )
        for item in design.items
    )

    high = estimate_rasch_ability(design, successes)
    low = estimate_rasch_ability(design, failures)

    assert high.boundary_limited and low.boundary_limited
    assert high.ability is not None and low.ability is not None
    assert high.ability > low.ability
    assert high.method == "rasch_logistic_normal_map_v1"


def test_rasch_missingness_and_evidence_substitution_fail_closed() -> None:
    design = _rasch_design()
    responses = tuple(
        RaschResponse(
            item_digest=item.item_digest,
            success=index == 0,
            result_digest=_digest(f"sparse-{index}"),
        )
        for index, item in enumerate(design.items[:2])
    )

    sparse = estimate_rasch_ability(design, responses)

    assert sparse.ability is None
    assert sparse.observed_items == 2
    assert sparse.missing_items == 2
    assert "minimum" in str(sparse.missing_reason)

    payload = design.model_dump(mode="python")
    payload["items"] = (
        design.items[0].model_copy(update={"difficulty": 99.0}),
        *design.items[1:],
    )
    with pytest.raises(ValueError, match="design digest"):
        RaschDesign.model_validate(payload)

    with pytest.raises(ValueError, match="outside"):
        estimate_rasch_ability(
            design,
            (
                RaschResponse(
                    item_digest=_digest("undeclared-item"),
                    success=True,
                    result_digest=_digest("undeclared-result"),
                ),
            ),
        )
    reused = _digest("reused-rasch-result")
    with pytest.raises(ValueError, match="inflate"):
        estimate_rasch_ability(
            design,
            (
                RaschResponse(
                    item_digest=design.items[0].item_digest,
                    success=True,
                    result_digest=reused,
                ),
                RaschResponse(
                    item_digest=design.items[1].item_digest,
                    success=False,
                    result_digest=reused,
                ),
            ),
        )


def test_adaptive_allocation_is_content_bound_and_cannot_touch_sealed_suite() -> None:
    candidates = (
        BoundaryCandidate(
            item_digest=_digest("untested"),
            difficulty=0.8,
            planned_trials=3,
            successes=0,
            failures=0,
            missing_trials=0,
        ),
        BoundaryCandidate(
            item_digest=_digest("known-easy"),
            difficulty=0.2,
            planned_trials=3,
            successes=2,
            failures=0,
            missing_trials=0,
            prior_result_digests=(_digest("known-easy-result-0"), _digest("known-easy-result-1")),
        ),
    )
    allocation = allocate_near_boundary(
        campaign_digest=_digest("campaign"),
        condition_id="optimized",
        suite_digest=_digest("adaptive-suite"),
        suite_status=SuiteStatus.READY,
        evaluation_class=EvaluationClass.ADAPTIVE_SEARCH,
        candidates=candidates,
        boundary_probability=0.5,
        created_at=NOW,
    )
    assert allocation.item_digest == _digest("untested")
    assert allocation.trial_index == 0
    assert allocation.decision_sequence == 2
    assert allocation.selection_probability == 1.0

    with pytest.raises(ValueError, match="sealed"):
        allocate_near_boundary(
            campaign_digest=_digest("campaign"),
            condition_id="optimized",
            suite_digest=_digest("promotion-suite"),
            suite_status=SuiteStatus.SEALED,
            evaluation_class=EvaluationClass.SEALED_PROMOTION,
            candidates=candidates,
            boundary_probability=0.5,
            created_at=NOW,
        )
    with pytest.raises(ValueError, match="adaptive-search"):
        allocate_near_boundary(
            campaign_digest=_digest("campaign"),
            condition_id="optimized",
            suite_digest=_digest("challenge-suite"),
            suite_status=SuiteStatus.READY,
            evaluation_class=EvaluationClass.CHALLENGE,
            candidates=candidates,
            boundary_probability=0.5,
            created_at=NOW,
        )


def test_calibration_selective_accuracy_and_missingness_use_fixed_denominator() -> None:
    metrics = calibration_metrics(
        (
            CalibrationObservation(observation_id="a", answered=True, correct=True, confidence=0.8),
            CalibrationObservation(
                observation_id="b", answered=True, correct=False, confidence=0.6
            ),
            CalibrationObservation(
                observation_id="c", answered=False, correct=None, confidence=0.2
            ),
            CalibrationObservation(
                observation_id="d",
                answered=False,
                correct=None,
                confidence=None,
                missing_reason="infrastructure failure",
            ),
        ),
        bin_count=2,
        selective_thresholds=(0.0, 0.7),
    )

    assert metrics.planned == 4
    assert metrics.answered == 2
    assert metrics.abstained == 1
    assert metrics.missing == 1
    assert metrics.brier_score == pytest.approx(((0.8 - 1.0) ** 2 + 0.6**2) / 2)
    assert metrics.selective_accuracy[1].coverage == 0.25
    assert metrics.selective_accuracy[1].accuracy == 1.0


def test_cost_per_verified_success_preserves_unknown_costs_as_unknown() -> None:
    observations = (
        EfficiencyObservation(
            observation_id="success",
            result_digest=_digest("efficiency-success"),
            verified_success=True,
            cost_usd=2.0,
        ),
        EfficiencyObservation(
            observation_id="unknown-cost",
            result_digest=_digest("efficiency-failure"),
            verified_success=False,
            cost_usd=None,
            cost_missing_reason="local runtime did not expose cost accounting",
        ),
        EfficiencyObservation(
            observation_id="timeout",
            result_digest=None,
            verified_success=None,
            outcome_missing_reason="timeout",
            cost_usd=0.5,
        ),
    )

    unknown = efficiency_metrics(observations)
    known = efficiency_metrics(
        (
            observations[0],
            observations[1].model_copy(update={"cost_usd": 1.0, "cost_missing_reason": None}),
        )
    )

    assert unknown.planned_requests == 3
    assert unknown.verified_successes_per_request == pytest.approx(1 / 3)
    assert unknown.known_cost_subtotal_usd == 2.5
    assert unknown.total_cost_usd is None
    assert unknown.cost_per_verified_success_usd is None
    assert "unknown" in str(unknown.cost_per_success_missing_reason)
    assert known.total_cost_usd == 3.0
    assert known.cost_per_verified_success_usd == 3.0


def test_consistency_self_correction_and_action_pathologies_are_paired() -> None:
    consistency = consistency_metrics(
        (
            ConsistencyObservation(group_id="g", trial_index=0, response_key="yes"),
            ConsistencyObservation(group_id="g", trial_index=1, response_key="yes"),
            ConsistencyObservation(
                group_id="g", trial_index=2, response_key=None, missing_reason="timeout"
            ),
        ),
        trials_per_group=3,
    )
    assert consistency.planned_pairs == 3
    assert consistency.observed_pairs == 1
    assert consistency.missing_pairs == 2
    assert consistency.pairwise_agreement == 1.0

    correction = self_correction_metrics(
        (
            SelfCorrectionObservation(
                observation_id="improved", initial_correct=False, final_correct=True
            ),
            SelfCorrectionObservation(
                observation_id="regressed", initial_correct=True, final_correct=False
            ),
        )
    )
    assert correction.correction_rate == 1.0
    assert correction.regression_rate == 1.0

    actions = repeated_action_metrics(
        (
            ActionTraceObservation(
                trace_id="trace", action_keys=("lookup", "lookup", "update", "lookup")
            ),
        )
    )
    assert actions.adjacent_repeats == 1
    assert actions.two_cycle_reentries == 1


def test_metamorphic_relations_require_independent_oracle_and_complete_pair() -> None:
    pair = MetamorphicProbePair(
        pair_id="pair-a",
        base_item_digest=_digest("base"),
        variant_item_digest=_digest("variant"),
        relation=MetamorphicRelationKind.FLIPPED_BINARY_OUTCOME,
        relation_verifier_id="counterfactual.oracle",
        relation_verifier_version="1.0.0",
        independent_verification_digest=None,
        base_success=True,
        variant_success=False,
        base_result_digest=_digest("base-result"),
        variant_result_digest=_digest("variant-result"),
    )

    unverified = evaluate_metamorphic_pair(pair)
    verified = evaluate_metamorphic_pair(
        pair.model_copy(update={"independent_verification_digest": _digest("oracle")})
    )

    assert unverified.satisfied is None
    assert "independent" in str(unverified.missing_reason)
    assert verified.satisfied is True


def test_stop_rule_cannot_stop_before_minimum_or_with_missing_outcomes() -> None:
    rule = StopRule(
        rule_id="boundary-precision",
        minimum_trials=10,
        maximum_trials=20,
        target_interval_width=1.0,
        confidence_level=0.95,
        boundary_probability=0.5,
        maximum_infrastructure_failure_rate=0.1,
    )

    early = assess_stop_rule(rule, successes=1, failures=1)
    precise = assess_stop_rule(rule, successes=5, failures=5)
    incomplete = assess_stop_rule(rule, successes=5, failures=4, other_missing_trials=1)
    invalid = assess_stop_rule(rule, successes=5, failures=4, infrastructure_failures=2)
    maximum = assess_stop_rule(rule, successes=10, failures=10)

    assert early.decision == StopDecision.CONTINUE
    assert "minimum" in early.reason
    assert precise.decision == StopDecision.STOP_PRECISION
    assert incomplete.decision == StopDecision.CONTINUE
    assert "unresolved" in incomplete.reason
    assert invalid.decision == StopDecision.INVALID_INFRASTRUCTURE
    assert maximum.decision == StopDecision.STOP_MAXIMUM
