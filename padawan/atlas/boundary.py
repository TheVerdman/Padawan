"""Deterministic boundary finding and predeclared sequential-analysis primitives."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from statistics import NormalDist
from typing import Annotated

from pydantic import Field, FiniteFloat, model_validator

from padawan.atlas.contracts import (
    CapabilityCurve,
    CapabilityCurvePoint,
    EvaluationClass,
    MetricEstimate,
    Probability,
    StopRule,
    SuiteStatus,
    TrialAllocation,
    content_id,
)
from padawan.models.contracts import NonEmpty, Sha256, StrictRecord
from padawan.models.hashing import sha256_digest


def wilson_interval(
    successes: int,
    trials: int,
    *,
    confidence_level: float = 0.95,
) -> tuple[float, float]:
    """Return a Wilson score interval without a normal-approximation dependency."""

    if trials <= 0:
        raise ValueError("Wilson intervals require at least one observed trial")
    if successes < 0 or successes > trials:
        raise ValueError("Wilson successes must lie between zero and trials")
    if confidence_level <= 0.0 or confidence_level >= 1.0:
        raise ValueError("confidence level must lie strictly between zero and one")
    z_score = NormalDist().inv_cdf((1.0 + confidence_level) / 2.0)
    proportion = successes / trials
    denominator = 1.0 + z_score**2 / trials
    center = (proportion + z_score**2 / (2.0 * trials)) / denominator
    margin = (
        z_score
        * math.sqrt(proportion * (1.0 - proportion) / trials + z_score**2 / (4.0 * trials**2))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


class BinaryTrialObservation(StrictRecord):
    item_id: NonEmpty
    trial_index: Annotated[int, Field(ge=0)]
    success: bool | None
    result_digest: Sha256 | None = None
    missing_reason: NonEmpty | None = None
    timeout: bool = False
    infrastructure_failure: bool = False
    contaminated: bool = False

    @model_validator(mode="after")
    def outcome_is_explicit(self) -> BinaryTrialObservation:
        missing = self.success is None
        if missing != (self.missing_reason is not None):
            raise ValueError("missing trial outcomes require exactly one reason")
        if not missing and self.result_digest is None:
            raise ValueError("observed trials require an immutable result digest")
        exclusions = (self.timeout, self.infrastructure_failure, self.contaminated)
        if sum(exclusions) > 1:
            raise ValueError("timeout, infrastructure, and contamination categories are exclusive")
        if any(exclusions) and not missing:
            raise ValueError("excluded outcomes cannot receive a binary score")
        return self


class RepeatedTrialPlan(StrictRecord):
    item_ids: Annotated[tuple[NonEmpty, ...], Field(min_length=1)]
    trials_per_item: Annotated[int, Field(gt=0)]
    pass_k: Annotated[tuple[int, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def plan_is_canonical(self) -> RepeatedTrialPlan:
        if tuple(sorted(self.item_ids)) != self.item_ids or len(self.item_ids) != len(
            set(self.item_ids)
        ):
            raise ValueError("repeated-trial item IDs must be unique and canonical")
        if tuple(sorted(self.pass_k)) != self.pass_k or len(self.pass_k) != len(set(self.pass_k)):
            raise ValueError("pass@k values must be unique and increasing")
        if any(value <= 0 or value > self.trials_per_item for value in self.pass_k):
            raise ValueError("pass@k values must lie within the declared replicate count")
        return self


class PassAtKEstimate(StrictRecord):
    k: Annotated[int, Field(gt=0)]
    value: Probability | None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_is_labelled(self) -> PassAtKEstimate:
        if (self.value is None) != (self.missing_reason is not None):
            raise ValueError("missing pass@k estimates require exactly one reason")
        return self


class RepeatedTrialItemSummary(StrictRecord):
    item_id: NonEmpty
    planned_trials: Annotated[int, Field(gt=0)]
    observed_trials: Annotated[int, Field(ge=0)]
    successes: Annotated[int, Field(ge=0)]
    failures: Annotated[int, Field(ge=0)]
    missing_trials: Annotated[int, Field(ge=0)]
    timeout_trials: Annotated[int, Field(ge=0)]
    infrastructure_failures: Annotated[int, Field(ge=0)]
    contaminated_trials: Annotated[int, Field(ge=0)]
    unresolved_trials: Annotated[int, Field(ge=0)]
    pass_at_k: tuple[PassAtKEstimate, ...]

    @model_validator(mode="after")
    def denominator_is_fixed(self) -> RepeatedTrialItemSummary:
        if self.successes + self.failures != self.observed_trials:
            raise ValueError("repeated-trial observed outcomes disagree with their counts")
        if self.observed_trials + self.missing_trials != self.planned_trials:
            raise ValueError("repeated trials must account for every declared replicate")
        if (
            self.timeout_trials
            + self.infrastructure_failures
            + self.contaminated_trials
            + self.unresolved_trials
            != self.missing_trials
        ):
            raise ValueError("repeated-trial missing categories disagree with missing trials")
        return self


class RepeatedTrialAnalysis(StrictRecord):
    items: tuple[RepeatedTrialItemSummary, ...]
    aggregate: MetricEstimate


def _pass_at_k(successes: int, total: int, k: int) -> float:
    if successes == 0:
        return 0.0
    failures = total - successes
    if failures < k:
        return 1.0
    return 1.0 - math.comb(failures, k) / math.comb(total, k)


def repeated_trial_analysis(
    plan: RepeatedTrialPlan,
    observations: tuple[BinaryTrialObservation, ...],
    *,
    metric_id: str = "verified_success_rate",
    confidence_level: float = 0.95,
) -> RepeatedTrialAnalysis:
    """Evaluate a fixed replicate plan and reject post-hoc replicate inflation."""

    keys = [(item.item_id, item.trial_index) for item in observations]
    if len(keys) != len(set(keys)):
        raise ValueError("repeated-trial replicate keys must be unique")
    planned_items = set(plan.item_ids)
    if any(item.item_id not in planned_items for item in observations):
        raise ValueError("repeated-trial observation cites an undeclared item")
    if any(item.trial_index >= plan.trials_per_item for item in observations):
        raise ValueError("repeated-trial observation exceeds the declared replicate count")
    digests = [item.result_digest for item in observations if item.result_digest is not None]
    if len(digests) != len(set(digests)):
        raise ValueError("a result digest cannot inflate more than one replicate")
    observed_digests = [
        item.result_digest
        for item in observations
        if item.success is not None and item.result_digest is not None
    ]

    by_key = {(item.item_id, item.trial_index): item for item in observations}
    summaries: list[RepeatedTrialItemSummary] = []
    total_successes = 0
    total_failures = 0
    infrastructure_failures = 0
    timeout_trials = 0
    contaminated_trials = 0
    for item_id in plan.item_ids:
        item_observations = tuple(
            by_key.get((item_id, trial_index)) for trial_index in range(plan.trials_per_item)
        )
        successes = sum(item is not None and item.success is True for item in item_observations)
        failures = sum(item is not None and item.success is False for item in item_observations)
        observed = successes + failures
        missing = plan.trials_per_item - observed
        total_successes += successes
        total_failures += failures
        item_timeouts = sum(item is not None and item.timeout for item in item_observations)
        item_infrastructure = sum(
            item is not None and item.infrastructure_failure for item in item_observations
        )
        item_contaminated = sum(
            item is not None and item.contaminated for item in item_observations
        )
        item_unresolved = missing - item_timeouts - item_infrastructure - item_contaminated
        timeout_trials += item_timeouts
        infrastructure_failures += item_infrastructure
        contaminated_trials += item_contaminated
        complete = missing == 0
        pass_estimates = tuple(
            PassAtKEstimate(
                k=k,
                value=_pass_at_k(successes, plan.trials_per_item, k) if complete else None,
                missing_reason=None
                if complete
                else "pass@k is undefined until every predeclared replicate is observed",
            )
            for k in plan.pass_k
        )
        summaries.append(
            RepeatedTrialItemSummary(
                item_id=item_id,
                planned_trials=plan.trials_per_item,
                observed_trials=observed,
                successes=successes,
                failures=failures,
                missing_trials=missing,
                timeout_trials=item_timeouts,
                infrastructure_failures=item_infrastructure,
                contaminated_trials=item_contaminated,
                unresolved_trials=item_unresolved,
                pass_at_k=pass_estimates,
            )
        )

    planned = len(plan.item_ids) * plan.trials_per_item
    observed_total = total_successes + total_failures
    if observed_total:
        lower, upper = wilson_interval(
            total_successes,
            observed_total,
            confidence_level=confidence_level,
        )
        value: float | None = total_successes / observed_total
        missing_reason: str | None = None
        interval_confidence: float | None = confidence_level
    else:
        value = None
        lower = None
        upper = None
        interval_confidence = None
        missing_reason = "no scorable binary outcomes were observed"
    return RepeatedTrialAnalysis(
        items=tuple(summaries),
        aggregate=MetricEstimate(
            metric_id=metric_id,
            value=value,
            lower=lower,
            upper=upper,
            confidence_level=interval_confidence,
            planned_trials=planned,
            observed_trials=observed_total,
            missing_trials=planned - observed_total,
            infrastructure_failures=infrastructure_failures + timeout_trials,
            contaminated_trials=contaminated_trials,
            evidence_result_digests=tuple(sorted(observed_digests)),
            missing_reason=missing_reason,
        ),
    )


class DifficultyBin(StrictRecord):
    difficulty: FiniteFloat
    planned_trials: Annotated[int, Field(gt=0)]
    successes: Annotated[int, Field(ge=0)]
    failures: Annotated[int, Field(ge=0)]
    missing_trials: Annotated[int, Field(ge=0)]
    evidence_result_digests: tuple[Sha256, ...] = ()

    @model_validator(mode="after")
    def denominator_is_fixed(self) -> DifficultyBin:
        if self.successes + self.failures + self.missing_trials != self.planned_trials:
            raise ValueError("difficulty-bin outcomes must equal the declared denominator")
        if len(self.evidence_result_digests) != len(set(self.evidence_result_digests)):
            raise ValueError("difficulty-bin evidence digests must be unique")
        return self


class _PavaBlock:
    def __init__(self, indices: list[int], successes: int, trials: int) -> None:
        self.indices = indices
        self.successes = successes
        self.trials = trials

    @property
    def mean(self) -> float:
        return self.successes / self.trials


def build_capability_curve(
    *,
    family_id: str,
    condition_id: str,
    bins: tuple[DifficultyBin, ...],
    boundary_probability: float = 0.5,
    confidence_level: float = 0.95,
) -> CapabilityCurve:
    """Fit a non-increasing success curve with weighted PAVA."""

    if not bins:
        raise ValueError("capability curves require at least one difficulty bin")
    if boundary_probability < 0.0 or boundary_probability > 1.0:
        raise ValueError("boundary probability must lie in [0, 1]")
    ordered = tuple(sorted(bins, key=lambda item: item.difficulty))
    if len({item.difficulty for item in ordered}) != len(ordered):
        raise ValueError("capability-curve difficulty values must be unique")
    raw_rates: list[tuple[int, float]] = []
    blocks: list[_PavaBlock] = []
    for index, item in enumerate(ordered):
        trials = item.successes + item.failures
        if not trials:
            continue
        rate = item.successes / trials
        raw_rates.append((index, rate))
        blocks.append(_PavaBlock([index], item.successes, trials))
        while len(blocks) >= 2 and blocks[-2].mean < blocks[-1].mean:
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                _PavaBlock(
                    [*left.indices, *right.indices],
                    left.successes + right.successes,
                    left.trials + right.trials,
                )
            )
    monotonic_violations = sum(
        left_rate < right_rate
        for (_, left_rate), (_, right_rate) in zip(raw_rates, raw_rates[1:], strict=False)
    )
    fitted: dict[int, tuple[float, float, float]] = {}
    for block in blocks:
        lower, upper = wilson_interval(
            block.successes,
            block.trials,
            confidence_level=confidence_level,
        )
        for index in block.indices:
            fitted[index] = (block.mean, lower, upper)
    points = tuple(
        CapabilityCurvePoint(
            difficulty=item.difficulty,
            success_probability=fitted[index][0] if index in fitted else None,
            lower=fitted[index][1] if index in fitted else None,
            upper=fitted[index][2] if index in fitted else None,
            trials=item.successes + item.failures,
            missing=item.missing_trials,
        )
        for index, item in enumerate(ordered)
    )
    boundary = _interpolated_boundary(points, boundary_probability)
    evidence = tuple(
        sorted({digest for item in ordered for digest in item.evidence_result_digests})
    )
    identity = {
        "family_id": family_id,
        "condition_id": condition_id,
        "boundary_probability": boundary_probability,
        "points": points,
        "monotonic_violations": monotonic_violations,
        "evidence_result_digests": evidence,
    }
    return CapabilityCurve(
        curve_id=content_id("capability-curve", identity),
        family_id=family_id,
        condition_id=condition_id,
        boundary_probability=boundary_probability,
        estimated_boundary=boundary,
        points=points,
        monotonic_violations=monotonic_violations,
        evidence_result_digests=evidence,
    )


def _interpolated_boundary(
    points: tuple[CapabilityCurvePoint, ...], boundary_probability: float
) -> float | None:
    observed = tuple(point for point in points if point.success_probability is not None)
    for point in observed:
        if point.success_probability == boundary_probability:
            return float(point.difficulty)
    for left, right in zip(observed, observed[1:], strict=False):
        assert left.success_probability is not None
        assert right.success_probability is not None
        if left.success_probability > boundary_probability > right.success_probability:
            probability_span = left.success_probability - right.success_probability
            fraction = (left.success_probability - boundary_probability) / probability_span
            return float(left.difficulty) + fraction * (
                float(right.difficulty) - float(left.difficulty)
            )
    return None


class RaschItemDifficulty(StrictRecord):
    item_id: NonEmpty
    item_digest: Sha256
    difficulty: FiniteFloat


class RaschDesign(StrictRecord):
    design_id: NonEmpty
    items: Annotated[tuple[RaschItemDifficulty, ...], Field(min_length=2)]
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)] = 0.95
    prior_standard_deviation: Annotated[FiniteFloat, Field(gt=0.0)] = 4.0
    minimum_observed_items: Annotated[int, Field(gt=0)] = 3
    solver_tolerance: Annotated[FiniteFloat, Field(gt=0.0)] = 1e-10
    maximum_iterations: Annotated[int, Field(gt=0)] = 100
    design_digest: Sha256

    @model_validator(mode="after")
    def design_is_predeclared(self) -> RaschDesign:
        item_digests = tuple(item.item_digest for item in self.items)
        item_ids = tuple(item.item_id for item in self.items)
        if item_digests != tuple(sorted(item_digests)):
            raise ValueError("Rasch items must use canonical digest order")
        if len(item_digests) != len(set(item_digests)):
            raise ValueError("Rasch item digests must be unique")
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Rasch item IDs must be unique")
        if self.minimum_observed_items > len(self.items):
            raise ValueError("Rasch minimum observations exceed the declared item count")
        identity = self.model_dump(mode="json", exclude={"design_id", "design_digest"})
        if self.design_digest != sha256_digest(identity):
            raise ValueError("Rasch design digest disagrees with item difficulties")
        if self.design_id != content_id("rasch-design", identity):
            raise ValueError("Rasch design ID disagrees with item difficulties")
        return self


def build_rasch_design(
    *,
    items: tuple[RaschItemDifficulty, ...],
    confidence_level: float = 0.95,
    prior_standard_deviation: float = 4.0,
    minimum_observed_items: int = 3,
    solver_tolerance: float = 1e-10,
    maximum_iterations: int = 100,
) -> RaschDesign:
    """Freeze Rasch item difficulties and solver policy before observing responses."""

    ordered = tuple(sorted(items, key=lambda item: item.item_digest))
    identity = {
        "items": ordered,
        "confidence_level": confidence_level,
        "prior_standard_deviation": prior_standard_deviation,
        "minimum_observed_items": minimum_observed_items,
        "solver_tolerance": solver_tolerance,
        "maximum_iterations": maximum_iterations,
    }
    return RaschDesign(
        design_id=content_id("rasch-design", identity),
        items=ordered,
        confidence_level=confidence_level,
        prior_standard_deviation=prior_standard_deviation,
        minimum_observed_items=minimum_observed_items,
        solver_tolerance=solver_tolerance,
        maximum_iterations=maximum_iterations,
        design_digest=sha256_digest(identity),
    )


class RaschResponse(StrictRecord):
    item_digest: Sha256
    success: bool | None
    result_digest: Sha256 | None = None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def missing_is_explicit(self) -> RaschResponse:
        if (self.success is None) != (self.missing_reason is not None):
            raise ValueError("missing Rasch responses require exactly one reason")
        if self.success is not None and self.result_digest is None:
            raise ValueError("observed Rasch responses require immutable result evidence")
        return self


class RaschAbilityEstimate(StrictRecord):
    estimate_id: NonEmpty
    design_digest: Sha256
    method: NonEmpty
    ability: FiniteFloat | None
    standard_error: Annotated[FiniteFloat, Field(gt=0.0)] | None
    lower: FiniteFloat | None
    upper: FiniteFloat | None
    confidence_level: Annotated[FiniteFloat, Field(gt=0.0, lt=1.0)]
    planned_items: Annotated[int, Field(ge=0)]
    observed_items: Annotated[int, Field(ge=0)]
    missing_items: Annotated[int, Field(ge=0)]
    boundary_limited: bool
    converged: bool
    iterations: Annotated[int, Field(ge=0)]
    evidence_result_digests: tuple[Sha256, ...]
    analysis_evidence_digest: Sha256
    estimate_digest: Sha256
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def estimate_is_complete_and_bound(self) -> RaschAbilityEstimate:
        if self.observed_items + self.missing_items != self.planned_items:
            raise ValueError("Rasch estimate must account for every declared item")
        if len(self.evidence_result_digests) != self.observed_items:
            raise ValueError("Rasch evidence must account for every observed item")
        if len(self.evidence_result_digests) != len(set(self.evidence_result_digests)):
            raise ValueError("Rasch evidence result digests must be unique")
        missing = self.ability is None
        interval_values = (self.standard_error, self.lower, self.upper)
        if missing != all(value is None for value in interval_values):
            raise ValueError("Rasch ability and uncertainty must be present together")
        if not missing and any(value is None for value in interval_values):
            raise ValueError("Rasch uncertainty interval is incomplete")
        if missing != (self.missing_reason is not None):
            raise ValueError("missing Rasch estimates require exactly one reason")
        if self.converged == missing:
            raise ValueError("Rasch estimates converge exactly when an ability is reported")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("Rasch confidence bounds are reversed")
        identity = self.model_dump(mode="json", exclude={"estimate_id", "estimate_digest"})
        if self.estimate_digest != sha256_digest(identity):
            raise ValueError("Rasch estimate digest disagrees with its evidence")
        if self.estimate_id != content_id("rasch-estimate", identity):
            raise ValueError("Rasch estimate ID disagrees with its evidence")
        return self


def _rasch_estimate_record(
    *,
    design: RaschDesign,
    ability: float | None,
    standard_error: float | None,
    lower: float | None,
    upper: float | None,
    observed_items: int,
    boundary_limited: bool,
    converged: bool,
    iterations: int,
    evidence_result_digests: tuple[str, ...],
    analysis_evidence_digest: str,
    missing_reason: str | None,
) -> RaschAbilityEstimate:
    identity = {
        "design_digest": design.design_digest,
        "method": "rasch_logistic_normal_map_v1",
        "ability": ability,
        "standard_error": standard_error,
        "lower": lower,
        "upper": upper,
        "confidence_level": design.confidence_level,
        "planned_items": len(design.items),
        "observed_items": observed_items,
        "missing_items": len(design.items) - observed_items,
        "boundary_limited": boundary_limited,
        "converged": converged,
        "iterations": iterations,
        "evidence_result_digests": evidence_result_digests,
        "analysis_evidence_digest": analysis_evidence_digest,
        "missing_reason": missing_reason,
    }
    return RaschAbilityEstimate(
        estimate_id=content_id("rasch-estimate", identity),
        design_digest=design.design_digest,
        method="rasch_logistic_normal_map_v1",
        ability=ability,
        standard_error=standard_error,
        lower=lower,
        upper=upper,
        confidence_level=design.confidence_level,
        planned_items=len(design.items),
        observed_items=observed_items,
        missing_items=len(design.items) - observed_items,
        boundary_limited=boundary_limited,
        converged=converged,
        iterations=iterations,
        evidence_result_digests=evidence_result_digests,
        analysis_evidence_digest=analysis_evidence_digest,
        estimate_digest=sha256_digest(identity),
        missing_reason=missing_reason,
    )


def estimate_rasch_ability(
    design: RaschDesign,
    responses: tuple[RaschResponse, ...],
) -> RaschAbilityEstimate:
    """Estimate one-parameter logistic ability with a predeclared weak Normal prior.

    The fixed prior keeps all-success and all-failure campaigns finite while
    ``boundary_limited`` makes that separation explicit. Missing items never become failures.
    """

    response_digests = [response.item_digest for response in responses]
    if len(response_digests) != len(set(response_digests)):
        raise ValueError("Rasch responses must contain at most one row per declared item")
    declared = {item.item_digest: item for item in design.items}
    if any(digest not in declared for digest in response_digests):
        raise ValueError("Rasch response cites an item outside the declared design")
    all_result_digests = [
        response.result_digest for response in responses if response.result_digest is not None
    ]
    if len(all_result_digests) != len(set(all_result_digests)):
        raise ValueError("one result digest cannot inflate multiple Rasch responses")
    by_digest = {response.item_digest: response for response in responses}
    response_manifest = tuple(
        by_digest[item.item_digest].model_dump(mode="json")
        if item.item_digest in by_digest
        else {
            "item_digest": item.item_digest,
            "success": None,
            "result_digest": None,
            "missing_reason": "no response row was recorded",
        }
        for item in design.items
    )
    analysis_evidence_digest = sha256_digest(
        {"design_digest": design.design_digest, "responses": response_manifest}
    )
    observed = tuple(
        (declared[response.item_digest], response)
        for response in responses
        if response.success is not None
    )
    evidence = tuple(
        sorted(
            response.result_digest for _, response in observed if response.result_digest is not None
        )
    )
    boundary_limited = bool(observed) and len({response.success for _, response in observed}) == 1
    if len(observed) < design.minimum_observed_items:
        return _rasch_estimate_record(
            design=design,
            ability=None,
            standard_error=None,
            lower=None,
            upper=None,
            observed_items=len(observed),
            boundary_limited=boundary_limited,
            converged=False,
            iterations=0,
            evidence_result_digests=evidence,
            analysis_evidence_digest=analysis_evidence_digest,
            missing_reason="fewer than the predeclared minimum observed items",
        )

    theta = 0.0
    prior_variance = design.prior_standard_deviation**2
    converged = False
    iterations = 0
    for iteration in range(1, design.maximum_iterations + 1):
        iterations = iteration
        probabilities = tuple(
            _stable_logistic(theta - float(item.difficulty)) for item, _ in observed
        )
        score = (
            sum(
                (1.0 if response.success else 0.0) - probability
                for (_, response), probability in zip(observed, probabilities, strict=True)
            )
            - theta / prior_variance
        )
        information = sum(probability * (1.0 - probability) for probability in probabilities)
        information += 1.0 / prior_variance
        step = score / information
        theta += max(-5.0, min(5.0, step))
        if abs(step) <= design.solver_tolerance:
            converged = True
            break
    if not converged:
        return _rasch_estimate_record(
            design=design,
            ability=None,
            standard_error=None,
            lower=None,
            upper=None,
            observed_items=len(observed),
            boundary_limited=boundary_limited,
            converged=False,
            iterations=iterations,
            evidence_result_digests=evidence,
            analysis_evidence_digest=analysis_evidence_digest,
            missing_reason="predeclared Rasch solver iteration limit reached",
        )

    probabilities = tuple(_stable_logistic(theta - float(item.difficulty)) for item, _ in observed)
    information = sum(probability * (1.0 - probability) for probability in probabilities)
    information += 1.0 / prior_variance
    standard_error = math.sqrt(1.0 / information)
    z_score = NormalDist().inv_cdf((1.0 + design.confidence_level) / 2.0)
    return _rasch_estimate_record(
        design=design,
        ability=theta,
        standard_error=standard_error,
        lower=theta - z_score * standard_error,
        upper=theta + z_score * standard_error,
        observed_items=len(observed),
        boundary_limited=boundary_limited,
        converged=True,
        iterations=iterations,
        evidence_result_digests=evidence,
        analysis_evidence_digest=analysis_evidence_digest,
        missing_reason=None,
    )


def _stable_logistic(value: float) -> float:
    if value >= 0.0:
        return 1.0 / (1.0 + math.exp(-value))
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


class BoundaryCandidate(StrictRecord):
    item_digest: Sha256
    difficulty: FiniteFloat
    planned_trials: Annotated[int, Field(gt=0)]
    successes: Annotated[int, Field(ge=0)]
    failures: Annotated[int, Field(ge=0)]
    missing_trials: Annotated[int, Field(ge=0)]
    infrastructure_failures: Annotated[int, Field(ge=0)] = 0
    contaminated_trials: Annotated[int, Field(ge=0)] = 0
    prior_result_digests: tuple[Sha256, ...] = ()

    @model_validator(mode="after")
    def allocation_is_within_plan(self) -> BoundaryCandidate:
        allocated = self.successes + self.failures + self.missing_trials
        if allocated > self.planned_trials:
            raise ValueError("boundary candidate exceeds its predeclared trial budget")
        if len(self.prior_result_digests) != len(
            set(self.prior_result_digests)
        ) or self.prior_result_digests != tuple(sorted(self.prior_result_digests)):
            raise ValueError("boundary candidate evidence digests must be unique and canonical")
        if len(self.prior_result_digests) != allocated:
            raise ValueError("boundary candidate evidence must account for every prior allocation")
        if self.infrastructure_failures + self.contaminated_trials > self.missing_trials:
            raise ValueError("boundary candidate exclusion counts exceed missing trials")
        return self

    @property
    def allocated_trials(self) -> int:
        return self.successes + self.failures + self.missing_trials


def allocate_near_boundary(
    *,
    campaign_digest: str,
    condition_id: str,
    suite_digest: str,
    suite_status: SuiteStatus,
    evaluation_class: EvaluationClass,
    candidates: tuple[BoundaryCandidate, ...],
    boundary_probability: float,
    created_at: datetime,
) -> TrialAllocation:
    """Choose the most uncertain near-boundary item with a reproducible decision digest."""

    if suite_status == SuiteStatus.SEALED or evaluation_class == EvaluationClass.SEALED_PROMOTION:
        raise ValueError("adaptive allocation cannot consume a sealed promotion suite")
    if suite_status != SuiteStatus.READY:
        raise ValueError("adaptive allocation requires a ready suite")
    if evaluation_class != EvaluationClass.ADAPTIVE_SEARCH:
        raise ValueError("adaptive allocation is restricted to adaptive-search suites")
    if boundary_probability <= 0.0 or boundary_probability >= 1.0:
        raise ValueError("adaptive boundary probability must lie strictly between zero and one")
    if not candidates:
        raise ValueError("adaptive allocation requires boundary candidates")
    item_digests = [item.item_digest for item in candidates]
    if len(item_digests) != len(set(item_digests)):
        raise ValueError("adaptive candidates must have unique item digests")
    prior_digests = [digest for item in candidates for digest in item.prior_result_digests]
    if len(prior_digests) != len(set(prior_digests)):
        raise ValueError("prior results cannot be reused across adaptive candidates")
    eligible = tuple(item for item in candidates if item.allocated_trials < item.planned_trials)
    if not eligible:
        raise ValueError("all adaptive candidates have exhausted their trial budgets")

    def priority(item: BoundaryCandidate) -> tuple[float, float, str]:
        observed = item.successes + item.failures
        if not observed:
            return (1.0, 1.0, item.item_digest)
        lower, upper = wilson_interval(item.successes, observed)
        rate = item.successes / observed
        proximity = 1.0 - abs(rate - boundary_probability)
        return ((upper - lower) * proximity, upper - lower, item.item_digest)

    selected = max(eligible, key=priority)
    decision_sequence = sum(item.allocated_trials for item in candidates)
    canonical_candidates = tuple(
        item.model_dump(mode="json")
        for item in sorted(candidates, key=lambda item: item.item_digest)
    )
    decision_identity = {
        "campaign_digest": campaign_digest,
        "condition_id": condition_id,
        "suite_digest": suite_digest,
        "suite_status": suite_status,
        "evaluation_class": evaluation_class,
        "boundary_probability": boundary_probability,
        "candidates": canonical_candidates,
        "selected_item_digest": selected.item_digest,
        "trial_index": selected.allocated_trials,
        "decision_sequence": decision_sequence,
        "allocation_policy_id": "atlas.boundary.uncertainty_proximity",
        "allocation_policy_version": "1.0.0",
    }
    evidence_digest = sha256_digest(decision_identity)
    return TrialAllocation(
        allocation_id=content_id("atlas-allocation", decision_identity),
        campaign_digest=campaign_digest,
        condition_id=condition_id,
        suite_digest=suite_digest,
        item_digest=selected.item_digest,
        trial_index=selected.allocated_trials,
        decision_sequence=decision_sequence,
        selection_probability=1.0,
        allocation_policy_id="atlas.boundary.uncertainty_proximity",
        allocation_policy_version="1.0.0",
        decision_evidence_digest=evidence_digest,
        prior_result_digests=tuple(sorted(prior_digests)),
        created_at=created_at,
    )


class MetamorphicRelationKind(StrEnum):
    SAME_BINARY_OUTCOME = "same_binary_outcome"
    FLIPPED_BINARY_OUTCOME = "flipped_binary_outcome"
    NONDECREASING_SCORE = "nondecreasing_score"
    NONINCREASING_SCORE = "nonincreasing_score"


class MetamorphicProbePair(StrictRecord):
    pair_id: NonEmpty
    base_item_digest: Sha256
    variant_item_digest: Sha256
    relation: MetamorphicRelationKind
    relation_verifier_id: NonEmpty
    relation_verifier_version: NonEmpty
    independent_verification_digest: Sha256 | None
    base_success: bool | None = None
    variant_success: bool | None = None
    base_score: Probability | None = None
    variant_score: Probability | None = None
    base_result_digest: Sha256 | None = None
    variant_result_digest: Sha256 | None = None
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def pair_is_distinct_and_complete(self) -> MetamorphicProbePair:
        if self.base_item_digest == self.variant_item_digest:
            raise ValueError("metamorphic pairs require distinct items")
        binary = self.relation in {
            MetamorphicRelationKind.SAME_BINARY_OUTCOME,
            MetamorphicRelationKind.FLIPPED_BINARY_OUTCOME,
        }
        outcome_missing = (
            self.base_success is None or self.variant_success is None
            if binary
            else self.base_score is None or self.variant_score is None
        )
        evidence_missing = self.base_result_digest is None or self.variant_result_digest is None
        missing = outcome_missing or evidence_missing
        if missing != (self.missing_reason is not None):
            raise ValueError("incomplete metamorphic pairs require exactly one reason")
        return self


class MetamorphicEvaluation(StrictRecord):
    pair_id: NonEmpty
    relation: MetamorphicRelationKind
    satisfied: bool | None
    evidence_digest: Sha256
    missing_reason: NonEmpty | None = None

    @model_validator(mode="after")
    def indeterminate_is_labelled(self) -> MetamorphicEvaluation:
        if (self.satisfied is None) != (self.missing_reason is not None):
            raise ValueError("indeterminate metamorphic relations require exactly one reason")
        return self


def evaluate_metamorphic_pair(pair: MetamorphicProbePair) -> MetamorphicEvaluation:
    identity = pair.model_dump(mode="json")
    evidence_digest = sha256_digest(identity)
    if pair.independent_verification_digest is None:
        return MetamorphicEvaluation(
            pair_id=pair.pair_id,
            relation=pair.relation,
            satisfied=None,
            evidence_digest=evidence_digest,
            missing_reason="metamorphic relation lacks independent oracle verification",
        )
    if pair.missing_reason is not None:
        return MetamorphicEvaluation(
            pair_id=pair.pair_id,
            relation=pair.relation,
            satisfied=None,
            evidence_digest=evidence_digest,
            missing_reason=pair.missing_reason,
        )
    if pair.relation == MetamorphicRelationKind.SAME_BINARY_OUTCOME:
        satisfied = pair.base_success == pair.variant_success
    elif pair.relation == MetamorphicRelationKind.FLIPPED_BINARY_OUTCOME:
        satisfied = pair.base_success != pair.variant_success
    elif pair.relation == MetamorphicRelationKind.NONDECREASING_SCORE:
        assert pair.base_score is not None and pair.variant_score is not None
        satisfied = pair.variant_score >= pair.base_score
    else:
        assert pair.base_score is not None and pair.variant_score is not None
        satisfied = pair.variant_score <= pair.base_score
    return MetamorphicEvaluation(
        pair_id=pair.pair_id,
        relation=pair.relation,
        satisfied=satisfied,
        evidence_digest=evidence_digest,
    )


class StopDecision(StrEnum):
    CONTINUE = "continue"
    STOP_PRECISION = "stop_precision"
    STOP_MAXIMUM = "stop_maximum"
    INVALID_INFRASTRUCTURE = "invalid_infrastructure"


class StopAssessment(StrictRecord):
    rule_id: NonEmpty
    decision: StopDecision
    allocated_trials: Annotated[int, Field(ge=0)]
    observed_trials: Annotated[int, Field(ge=0)]
    missing_trials: Annotated[int, Field(ge=0)]
    interval_lower: Probability | None
    interval_upper: Probability | None
    interval_width: Probability | None
    reason: NonEmpty

    @model_validator(mode="after")
    def interval_is_complete(self) -> StopAssessment:
        values = (self.interval_lower, self.interval_upper, self.interval_width)
        if any(value is None for value in values) != all(value is None for value in values):
            raise ValueError("stop-rule interval must be wholly present or absent")
        if self.observed_trials + self.missing_trials != self.allocated_trials:
            raise ValueError("stop assessment must account for every allocated trial")
        return self


def assess_stop_rule(
    rule: StopRule,
    *,
    successes: int,
    failures: int,
    infrastructure_failures: int = 0,
    contaminated_trials: int = 0,
    other_missing_trials: int = 0,
) -> StopAssessment:
    counts = (
        successes,
        failures,
        infrastructure_failures,
        contaminated_trials,
        other_missing_trials,
    )
    if any(value < 0 for value in counts):
        raise ValueError("stop-rule counts cannot be negative")
    observed = successes + failures
    missing = infrastructure_failures + contaminated_trials + other_missing_trials
    allocated = observed + missing
    if allocated > rule.maximum_trials:
        raise ValueError("allocated trials exceed the predeclared stop-rule maximum")
    if observed:
        lower, upper = wilson_interval(
            successes,
            observed,
            confidence_level=rule.confidence_level,
        )
        width: float | None = upper - lower
    else:
        lower = None
        upper = None
        width = None
    if allocated < rule.minimum_trials:
        decision = StopDecision.CONTINUE
        reason = "minimum predeclared trial count has not been reached"
    elif (
        allocated and infrastructure_failures / allocated > rule.maximum_infrastructure_failure_rate
    ):
        decision = StopDecision.INVALID_INFRASTRUCTURE
        reason = "infrastructure-failure rate exceeds the predeclared maximum"
    elif allocated == rule.maximum_trials:
        decision = StopDecision.STOP_MAXIMUM
        reason = "predeclared maximum trial count reached"
    elif missing:
        decision = StopDecision.CONTINUE
        reason = "precision stopping is blocked by unresolved or excluded outcomes"
    elif width is not None and width <= rule.target_interval_width:
        decision = StopDecision.STOP_PRECISION
        reason = "predeclared confidence-interval precision target reached"
    else:
        decision = StopDecision.CONTINUE
        reason = "predeclared precision target has not been reached"
    return StopAssessment(
        rule_id=rule.rule_id,
        decision=decision,
        allocated_trials=allocated,
        observed_trials=observed,
        missing_trials=missing,
        interval_lower=lower,
        interval_upper=upper,
        interval_width=width,
        reason=reason,
    )
