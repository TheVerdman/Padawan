"""Deterministic behavioral measurements used by Capability Atlas campaigns."""

from __future__ import annotations

from itertools import combinations
from typing import Annotated

from pydantic import Field, FiniteFloat, model_validator

from padawan.atlas.contracts import Probability
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord


class EfficiencyObservation(StrictRecord):
    observation_id: NonEmpty
    result_digest: Sha256 | None
    verified_success: bool | None
    outcome_missing_reason: NonEmpty | None = None
    cost_usd: Annotated[FiniteFloat, Field(ge=0.0)] | None
    cost_missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_values_are_explicit(self) -> EfficiencyObservation:
        if (self.verified_success is None) != (self.outcome_missing_reason is not None):
            raise ValueError("missing efficiency outcomes require exactly one reason")
        if self.verified_success is not None and self.result_digest is None:
            raise ValueError("observed efficiency outcomes require an immutable result digest")
        if (self.cost_usd is None) != (self.cost_missing_reason is not None):
            raise ValueError("unknown request cost requires exactly one reason")
        return self


class EfficiencyMetrics(StrictRecord):
    planned_requests: Annotated[int, Field(ge=0)]
    observed_outcomes: Annotated[int, Field(ge=0)]
    missing_outcomes: Annotated[int, Field(ge=0)]
    verified_successes: Annotated[int, Field(ge=0)]
    unknown_cost_requests: Annotated[int, Field(ge=0)]
    known_cost_subtotal_usd: Annotated[FiniteFloat, Field(ge=0.0)]
    total_cost_usd: Annotated[FiniteFloat, Field(ge=0.0)] | None
    total_cost_missing_reason: NonEmpty | None = None
    cost_per_verified_success_usd: Annotated[FiniteFloat, Field(ge=0.0)] | None
    cost_per_success_missing_reason: NonEmpty | None = None
    verified_successes_per_request: Probability
    requests_per_verified_success: Annotated[FiniteFloat, Field(ge=0.0)] | None
    evidence_result_digests: tuple[Sha256, ...]

    @model_validator(mode="after")
    def denominators_and_unknowns_are_honest(self) -> EfficiencyMetrics:
        if self.observed_outcomes + self.missing_outcomes != self.planned_requests:
            raise ValueError("efficiency metrics must account for every planned request")
        if self.verified_successes > self.observed_outcomes:
            raise ValueError("verified successes exceed observed outcomes")
        if self.unknown_cost_requests > self.planned_requests:
            raise ValueError("unknown cost count exceeds planned requests")
        if (self.total_cost_usd is None) != (self.total_cost_missing_reason is not None):
            raise ValueError("unknown total cost requires exactly one reason")
        if (self.cost_per_verified_success_usd is None) != (
            self.cost_per_success_missing_reason is not None
        ):
            raise ValueError("unknown cost per success requires exactly one reason")
        expected_rate = (
            self.verified_successes / self.planned_requests if self.planned_requests else 0.0
        )
        if abs(self.verified_successes_per_request - expected_rate) > 1e-12:
            raise ValueError("success efficiency disagrees with the fixed request denominator")
        if (self.requests_per_verified_success is None) != (self.verified_successes == 0):
            raise ValueError("requests per success is defined exactly when a success exists")
        if len(self.evidence_result_digests) != self.observed_outcomes:
            raise ValueError("efficiency evidence must account for every observed outcome")
        if len(self.evidence_result_digests) != len(set(self.evidence_result_digests)):
            raise ValueError("efficiency result digests must be unique")
        return self


def efficiency_metrics(
    observations: tuple[EfficiencyObservation, ...],
) -> EfficiencyMetrics:
    """Compute request and cost efficiency without imputing unknown request costs."""

    identifiers = [item.observation_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("efficiency observation IDs must be unique")
    result_digests = [
        item.result_digest for item in observations if item.verified_success is not None
    ]
    if len(result_digests) != len(set(result_digests)):
        raise ValueError("an immutable result cannot count as multiple efficiency outcomes")
    observed = tuple(item for item in observations if item.verified_success is not None)
    successes = sum(item.verified_success is True for item in observed)
    known_cost_subtotal = sum(item.cost_usd or 0.0 for item in observations)
    unknown_costs = sum(item.cost_usd is None for item in observations)
    if unknown_costs:
        total_cost: float | None = None
        total_cost_reason: str | None = "one or more request costs are unknown"
        cost_per_success: float | None = None
        cost_per_success_reason: str | None = "one or more request costs are unknown"
    else:
        total_cost = known_cost_subtotal
        total_cost_reason = None
        if successes:
            cost_per_success = known_cost_subtotal / successes
            cost_per_success_reason = None
        else:
            cost_per_success = None
            cost_per_success_reason = "no verified successes were observed"
    planned = len(observations)
    return EfficiencyMetrics(
        planned_requests=planned,
        observed_outcomes=len(observed),
        missing_outcomes=planned - len(observed),
        verified_successes=successes,
        unknown_cost_requests=unknown_costs,
        known_cost_subtotal_usd=known_cost_subtotal,
        total_cost_usd=total_cost,
        total_cost_missing_reason=total_cost_reason,
        cost_per_verified_success_usd=cost_per_success,
        cost_per_success_missing_reason=cost_per_success_reason,
        verified_successes_per_request=successes / planned if planned else 0.0,
        requests_per_verified_success=planned / successes if successes else None,
        evidence_result_digests=tuple(
            sorted(digest for digest in result_digests if digest is not None)
        ),
    )


class CalibrationObservation(StrictRecord):
    observation_id: NonEmpty
    answered: bool
    correct: bool | None
    confidence: Probability | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def outcome_is_explicit(self) -> CalibrationObservation:
        if self.missing_reason is not None:
            if self.answered or self.correct is not None or self.confidence is not None:
                raise ValueError("missing calibration observations cannot carry an outcome")
        elif self.answered:
            if self.correct is None or self.confidence is None:
                raise ValueError(
                    "answered calibration observations require correctness and confidence"
                )
        elif self.correct is not None:
            raise ValueError("abstained calibration observations cannot be marked correct")
        return self


class CalibrationBin(StrictRecord):
    lower: Probability
    upper: Probability
    count: Annotated[int, Field(ge=0)]
    mean_confidence: Probability | None
    accuracy: Probability | None

    @model_validator(mode="after")
    def empty_bins_have_no_estimate(self) -> CalibrationBin:
        if self.lower > self.upper:
            raise ValueError("calibration-bin bounds are reversed")
        missing = self.mean_confidence is None or self.accuracy is None
        if missing != (self.count == 0):
            raise ValueError("calibration bins have estimates exactly when populated")
        return self


class SelectiveAccuracyPoint(StrictRecord):
    threshold: Probability
    retained: Annotated[int, Field(ge=0)]
    planned: Annotated[int, Field(ge=0)]
    coverage: Probability
    accuracy: Probability | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def denominator_is_fixed(self) -> SelectiveAccuracyPoint:
        expected = self.retained / self.planned if self.planned else 0.0
        if abs(self.coverage - expected) > 1e-12:
            raise ValueError("selective coverage disagrees with the fixed denominator")
        if (self.accuracy is None) != (self.missing_reason is not None):
            raise ValueError("missing selective accuracy requires exactly one reason")
        if self.retained == 0 and self.accuracy is not None:
            raise ValueError("empty selective sets cannot receive an accuracy")
        return self


class CalibrationMetrics(StrictRecord):
    planned: Annotated[int, Field(ge=0)]
    answered: Annotated[int, Field(ge=0)]
    abstained: Annotated[int, Field(ge=0)]
    missing: Annotated[int, Field(ge=0)]
    accuracy: Probability | None
    mean_confidence: Probability | None
    brier_score: Annotated[FiniteFloat, Field(ge=0.0, le=1.0)] | None
    expected_calibration_error: Probability | None
    bins: tuple[CalibrationBin, ...]
    selective_accuracy: tuple[SelectiveAccuracyPoint, ...]

    @model_validator(mode="after")
    def observations_are_accounted_for(self) -> CalibrationMetrics:
        if self.answered + self.abstained + self.missing != self.planned:
            raise ValueError("calibration metrics must account for every planned observation")
        estimates = (
            self.accuracy,
            self.mean_confidence,
            self.brier_score,
            self.expected_calibration_error,
        )
        if self.answered == 0 and any(value is not None for value in estimates):
            raise ValueError("calibration estimates require at least one answered item")
        if self.answered > 0 and any(value is None for value in estimates):
            raise ValueError("answered calibration sets require complete estimates")
        return self


def calibration_metrics(
    observations: tuple[CalibrationObservation, ...],
    *,
    bin_count: int = 10,
    selective_thresholds: tuple[float, ...] = (0.0, 0.5, 0.7, 0.9),
) -> CalibrationMetrics:
    """Measure calibration without dropping abstentions or missing observations."""

    if bin_count <= 0:
        raise ValueError("calibration bin count must be positive")
    identifiers = [item.observation_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("calibration observation IDs must be unique")
    if any(threshold < 0.0 or threshold > 1.0 for threshold in selective_thresholds):
        raise ValueError("selective thresholds must lie in [0, 1]")
    if tuple(sorted(set(selective_thresholds))) != selective_thresholds:
        raise ValueError("selective thresholds must be unique and increasing")

    answered = tuple(item for item in observations if item.answered)
    missing = sum(item.missing_reason is not None for item in observations)
    abstained = len(observations) - len(answered) - missing
    accuracy: float | None = None
    mean_confidence: float | None = None
    brier: float | None = None
    expected_calibration_error: float | None = None
    if answered:
        correctness = [1.0 if item.correct else 0.0 for item in answered]
        confidences = [float(item.confidence) for item in answered if item.confidence is not None]
        accuracy = sum(correctness) / len(answered)
        mean_confidence = sum(confidences) / len(answered)
        brier = sum(
            (confidence - correct) ** 2
            for confidence, correct in zip(confidences, correctness, strict=True)
        ) / len(answered)

    bins: list[CalibrationBin] = []
    calibration_error = 0.0
    for index in range(bin_count):
        lower = index / bin_count
        upper = (index + 1) / bin_count
        members = tuple(
            item
            for item in answered
            if item.confidence is not None
            and lower <= item.confidence
            and (item.confidence < upper or (index == bin_count - 1 and item.confidence <= upper))
        )
        if not members:
            bins.append(
                CalibrationBin(
                    lower=lower,
                    upper=upper,
                    count=0,
                    mean_confidence=None,
                    accuracy=None,
                )
            )
            continue
        bin_confidence = sum(
            float(item.confidence) for item in members if item.confidence is not None
        ) / len(members)
        bin_accuracy = sum(bool(item.correct) for item in members) / len(members)
        calibration_error += len(members) / len(answered) * abs(bin_accuracy - bin_confidence)
        bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(members),
                mean_confidence=bin_confidence,
                accuracy=bin_accuracy,
            )
        )
    if answered:
        expected_calibration_error = calibration_error

    selective: list[SelectiveAccuracyPoint] = []
    for threshold in selective_thresholds:
        retained = tuple(
            item
            for item in answered
            if item.confidence is not None and item.confidence >= threshold
        )
        selective.append(
            SelectiveAccuracyPoint(
                threshold=threshold,
                retained=len(retained),
                planned=len(observations),
                coverage=len(retained) / len(observations) if observations else 0.0,
                accuracy=(sum(bool(item.correct) for item in retained) / len(retained))
                if retained
                else None,
                missing_reason=None if retained else "no answered items met the threshold",
            )
        )

    return CalibrationMetrics(
        planned=len(observations),
        answered=len(answered),
        abstained=abstained,
        missing=missing,
        accuracy=accuracy,
        mean_confidence=mean_confidence,
        brier_score=brier,
        expected_calibration_error=expected_calibration_error,
        bins=tuple(bins),
        selective_accuracy=tuple(selective),
    )


class ConsistencyObservation(StrictRecord):
    group_id: NonEmpty
    trial_index: Annotated[int, Field(ge=0)]
    response_key: NonEmpty | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_is_explicit(self) -> ConsistencyObservation:
        if (self.response_key is None) != (self.missing_reason is not None):
            raise ValueError("missing consistency responses require exactly one reason")
        return self


class ConsistencyMetrics(StrictRecord):
    planned_pairs: Annotated[int, Field(ge=0)]
    observed_pairs: Annotated[int, Field(ge=0)]
    missing_pairs: Annotated[int, Field(ge=0)]
    agreeing_pairs: Annotated[int, Field(ge=0)]
    pairwise_agreement: Probability | None

    @model_validator(mode="after")
    def pairs_are_accounted_for(self) -> ConsistencyMetrics:
        if self.observed_pairs + self.missing_pairs != self.planned_pairs:
            raise ValueError("consistency metrics must account for every planned pair")
        if self.agreeing_pairs > self.observed_pairs:
            raise ValueError("agreeing pairs exceed observed pairs")
        if (self.pairwise_agreement is None) != (self.observed_pairs == 0):
            raise ValueError("pairwise consistency is defined exactly when pairs are observed")
        return self


def consistency_metrics(
    observations: tuple[ConsistencyObservation, ...], *, trials_per_group: int
) -> ConsistencyMetrics:
    if trials_per_group < 2:
        raise ValueError("consistency requires at least two declared trials per group")
    keys = [(item.group_id, item.trial_index) for item in observations]
    if len(keys) != len(set(keys)):
        raise ValueError("consistency replicate keys must be unique")
    if any(item.trial_index >= trials_per_group for item in observations):
        raise ValueError("consistency observation exceeds the declared replicate plan")
    groups = sorted({item.group_id for item in observations})
    planned_pairs = len(groups) * (trials_per_group * (trials_per_group - 1) // 2)
    observed_pairs = 0
    agreeing_pairs = 0
    by_group = {(item.group_id, item.trial_index): item for item in observations}
    for group_id in groups:
        for left, right in combinations(range(trials_per_group), 2):
            left_item = by_group.get((group_id, left))
            right_item = by_group.get((group_id, right))
            if (
                left_item is None
                or right_item is None
                or left_item.response_key is None
                or right_item.response_key is None
            ):
                continue
            observed_pairs += 1
            agreeing_pairs += left_item.response_key == right_item.response_key
    return ConsistencyMetrics(
        planned_pairs=planned_pairs,
        observed_pairs=observed_pairs,
        missing_pairs=planned_pairs - observed_pairs,
        agreeing_pairs=agreeing_pairs,
        pairwise_agreement=agreeing_pairs / observed_pairs if observed_pairs else None,
    )


class SelfCorrectionObservation(StrictRecord):
    observation_id: NonEmpty
    initial_correct: bool | None
    final_correct: bool | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def pair_is_complete(self) -> SelfCorrectionObservation:
        missing = self.initial_correct is None or self.final_correct is None
        if missing != (self.missing_reason is not None):
            raise ValueError("missing correction pairs require exactly one reason")
        if missing and (self.initial_correct is not None or self.final_correct is not None):
            raise ValueError("partially observed correction pairs are not comparable")
        return self


class SelfCorrectionMetrics(StrictRecord):
    planned: Annotated[int, Field(ge=0)]
    observed: Annotated[int, Field(ge=0)]
    missing: Annotated[int, Field(ge=0)]
    improved: Annotated[int, Field(ge=0)]
    regressed: Annotated[int, Field(ge=0)]
    stable_correct: Annotated[int, Field(ge=0)]
    stable_incorrect: Annotated[int, Field(ge=0)]
    correction_rate: Probability | None
    regression_rate: Probability | None

    @model_validator(mode="after")
    def pairs_are_accounted_for(self) -> SelfCorrectionMetrics:
        if self.observed + self.missing != self.planned:
            raise ValueError("self-correction metrics must account for every planned pair")
        if (
            self.improved + self.regressed + self.stable_correct + self.stable_incorrect
            != self.observed
        ):
            raise ValueError("self-correction transition counts disagree with observed pairs")
        return self


def self_correction_metrics(
    observations: tuple[SelfCorrectionObservation, ...],
) -> SelfCorrectionMetrics:
    identifiers = [item.observation_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("self-correction observation IDs must be unique")
    observed = tuple(item for item in observations if item.missing_reason is None)
    improved = sum(
        item.initial_correct is False and item.final_correct is True for item in observed
    )
    regressed = sum(
        item.initial_correct is True and item.final_correct is False for item in observed
    )
    stable_correct = sum(
        item.initial_correct is True and item.final_correct is True for item in observed
    )
    stable_incorrect = sum(
        item.initial_correct is False and item.final_correct is False for item in observed
    )
    initially_incorrect = improved + stable_incorrect
    initially_correct = regressed + stable_correct
    return SelfCorrectionMetrics(
        planned=len(observations),
        observed=len(observed),
        missing=len(observations) - len(observed),
        improved=improved,
        regressed=regressed,
        stable_correct=stable_correct,
        stable_incorrect=stable_incorrect,
        correction_rate=improved / initially_incorrect if initially_incorrect else None,
        regression_rate=regressed / initially_correct if initially_correct else None,
    )


class ActionTraceObservation(StrictRecord):
    trace_id: NonEmpty
    action_keys: tuple[NonEmpty, ...] | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_is_explicit(self) -> ActionTraceObservation:
        if (self.action_keys is None) != (self.missing_reason is not None):
            raise ValueError("missing action traces require exactly one reason")
        return self


class RepeatedActionMetrics(StrictRecord):
    planned_traces: Annotated[int, Field(ge=0)]
    observed_traces: Annotated[int, Field(ge=0)]
    missing_traces: Annotated[int, Field(ge=0)]
    adjacent_opportunities: Annotated[int, Field(ge=0)]
    adjacent_repeats: Annotated[int, Field(ge=0)]
    two_cycle_opportunities: Annotated[int, Field(ge=0)]
    two_cycle_reentries: Annotated[int, Field(ge=0)]
    adjacent_repeat_rate: Probability | None
    two_cycle_rate: Probability | None

    @model_validator(mode="after")
    def traces_are_accounted_for(self) -> RepeatedActionMetrics:
        if self.observed_traces + self.missing_traces != self.planned_traces:
            raise ValueError("action metrics must account for every planned trace")
        if self.adjacent_repeats > self.adjacent_opportunities:
            raise ValueError("adjacent repeats exceed available opportunities")
        if self.two_cycle_reentries > self.two_cycle_opportunities:
            raise ValueError("two-cycle reentries exceed available opportunities")
        return self


def repeated_action_metrics(
    observations: tuple[ActionTraceObservation, ...],
) -> RepeatedActionMetrics:
    identifiers = [item.trace_id for item in observations]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("action-trace IDs must be unique")
    observed = tuple(item for item in observations if item.action_keys is not None)
    adjacent_opportunities = sum(max(len(item.action_keys or ()) - 1, 0) for item in observed)
    adjacent_repeats = sum(
        sum(left == right for left, right in zip(actions, actions[1:], strict=False))
        for item in observed
        for actions in (item.action_keys or (),)
    )
    two_cycle_opportunities = sum(max(len(item.action_keys or ()) - 2, 0) for item in observed)
    two_cycle_reentries = sum(
        sum(
            actions[index] == actions[index - 2] and actions[index] != actions[index - 1]
            for index in range(2, len(actions))
        )
        for item in observed
        for actions in (item.action_keys or (),)
    )
    return RepeatedActionMetrics(
        planned_traces=len(observations),
        observed_traces=len(observed),
        missing_traces=len(observations) - len(observed),
        adjacent_opportunities=adjacent_opportunities,
        adjacent_repeats=adjacent_repeats,
        two_cycle_opportunities=two_cycle_opportunities,
        two_cycle_reentries=two_cycle_reentries,
        adjacent_repeat_rate=(adjacent_repeats / adjacent_opportunities)
        if adjacent_opportunities
        else None,
        two_cycle_rate=(two_cycle_reentries / two_cycle_opportunities)
        if two_cycle_opportunities
        else None,
    )
