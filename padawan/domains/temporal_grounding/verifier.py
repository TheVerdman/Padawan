from __future__ import annotations

import math
from datetime import UTC, datetime

from padawan.domains.contracts import VerifierDisposition, VerifierResult
from padawan.domains.temporal_grounding.contracts import TemporalScenarioManifest
from padawan.models.hashing import sha256_digest
from padawan.temporal.contracts import DurationForecast, TemporalAction, TemporalDecision


class TemporalPolicyVerifier:
    verifier_id = "temporal.policy"
    verifier_version = "padawan-temporal-policy-v1"

    def verify(
        self,
        *,
        scenario: TemporalScenarioManifest,
        decision: TemporalDecision,
        created_at: datetime | None = None,
    ) -> VerifierResult:
        timestamp = created_at or datetime.now(UTC)
        oracle = scenario.oracle
        action_match = {_action_key(action) for action in decision.actions} == {
            _action_key(action) for action in oracle.required_actions
        }
        forecast_score, forecast_acceptable, forecast_errors = _forecast_score(
            now=scenario.frame.now_utc,
            observed=decision.duration_forecasts,
            expected=oracle.required_duration_forecasts,
        )
        next_check_match = _instant_match(decision.next_check_at, oracle.next_check_at)
        response_normalized = decision.response.casefold()
        forbidden_fragments = tuple(
            fragment
            for fragment in oracle.forbidden_response_fragments
            if fragment.casefold() in response_normalized
        )
        components: dict[str, float] = {
            "continuity": float(decision.continuity == oracle.continuity),
            "gap_acknowledgement": float(decision.acknowledge_gap == oracle.acknowledge_gap),
            "activity_honesty": float(not decision.claims_continuous_activity),
            "action_policy": float(action_match),
            "duration_calibration": forecast_score,
            "next_check": float(next_check_match),
            "response_consistency": float(not forbidden_fragments),
        }
        acceptance_components = {
            **components,
            "duration_calibration": float(forecast_acceptable),
        }
        first_failure = next(
            (name for name, value in acceptance_components.items() if value < 1.0 - 1e-12),
            None,
        )
        verified = (
            first_failure is None
            and forecast_acceptable
            and not decision.claims_continuous_activity
        )
        score = sum(components.values()) / len(components)
        disposition = VerifierDisposition.VERIFIED if verified else VerifierDisposition.REJECTED
        return VerifierResult(
            result_id=(
                "temporal-result-"
                + sha256_digest(
                    {
                        "scenario_id": scenario.scenario_id,
                        "decision": decision.model_dump(mode="json"),
                        "verifier_version": self.verifier_version,
                    }
                )[7:31]
            ),
            verifier_id=self.verifier_id,
            verifier_version=self.verifier_version,
            scope=scenario.scenario_id,
            disposition=disposition,
            deterministic=True,
            summary=(
                "temporal policy satisfies the authoritative event-time oracle"
                if verified
                else f"temporal policy failed component: {first_failure or 'unknown'}"
            ),
            evidence={
                "scenario_id": scenario.scenario_id,
                "scenario_family": scenario.family.value,
                "frame_digest": sha256_digest(scenario.frame.model_dump(mode="json")),
                "oracle_digest": sha256_digest(oracle.model_dump(mode="json")),
                "decision_digest": sha256_digest(decision.model_dump(mode="json")),
                "component_scores": components,
                "acceptance_components": acceptance_components,
                "score": score,
                "first_failure": first_failure,
                "forecast_acceptable": forecast_acceptable,
                "forecast_errors_seconds": forecast_errors,
                "forbidden_response_fragments": forbidden_fragments,
                "operational_time_used_as_scenario_time": False,
            },
            created_at=timestamp,
        )


def _action_key(action: TemporalAction) -> tuple[str, str, str | None, str]:
    return (
        action.kind.value,
        action.subject,
        action.execute_at.astimezone(UTC).isoformat() if action.execute_at is not None else None,
        action.rationale_code,
    )


def _forecast_score(
    *,
    now: datetime,
    observed: tuple[DurationForecast, ...],
    expected: tuple[DurationForecast, ...],
) -> tuple[float, bool, dict[str, dict[str, float]]]:
    observed_by_operation = {forecast.operation_id: forecast for forecast in observed}
    expected_by_operation = {forecast.operation_id: forecast for forecast in expected}
    if set(observed_by_operation) != set(expected_by_operation):
        return 0.0, False, {}
    if not expected:
        return 1.0, True, {}
    scores: list[float] = []
    acceptable = True
    errors: dict[str, dict[str, float]] = {}
    for operation_id, target in expected_by_operation.items():
        prediction = observed_by_operation[operation_id]
        p50_error = abs((prediction.p50_completed_at - target.p50_completed_at).total_seconds())
        p90_error = abs((prediction.p90_completed_at - target.p90_completed_at).total_seconds())
        target_p50_remaining = max(abs((target.p50_completed_at - now).total_seconds()), 1.0)
        target_p90_remaining = max(abs((target.p90_completed_at - now).total_seconds()), 1.0)
        p50_log_error = abs(math.log1p(p50_error) / math.log1p(target_p50_remaining))
        p90_log_error = abs(math.log1p(p90_error) / math.log1p(target_p90_remaining))
        profile_match = prediction.source_profile_id == target.source_profile_id
        operation_score = max(0.0, 1.0 - (p50_log_error + p90_log_error) / 2.0)
        if not profile_match:
            operation_score = 0.0
        tolerance_p50 = max(5.0, target_p50_remaining * 0.05)
        tolerance_p90 = max(5.0, target_p90_remaining * 0.05)
        within_tolerance = (
            profile_match and p50_error <= tolerance_p50 and p90_error <= tolerance_p90
        )
        acceptable = acceptable and within_tolerance
        scores.append(operation_score)
        errors[operation_id] = {
            "p50": p50_error,
            "p90": p90_error,
            "p50_tolerance": tolerance_p50,
            "p90_tolerance": tolerance_p90,
        }
    return sum(scores) / len(scores), acceptable, errors


def _instant_match(observed: datetime | None, expected: datetime | None) -> bool:
    if observed is None or expected is None:
        return observed is expected
    return abs((observed - expected).total_seconds()) <= 1.0
