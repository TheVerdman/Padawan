from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

from padawan.domains.temporal_grounding.contracts import (
    TemporalConversationMessage,
    TemporalScenarioFamily,
    TemporalScenarioManifest,
    TemporalScenarioOracle,
)
from padawan.models.contracts import (
    CompetencyRecord,
    CorpusItemRecord,
    CorpusPool,
    TeacherMode,
    VerifierSpec,
    project_authored_internal_rights,
)
from padawan.models.hashing import sha256_digest
from padawan.temporal.contracts import (
    ActiveOperation,
    DurationForecast,
    DurationProfile,
    FreshnessDisposition,
    OperationStatus,
    TemporalAction,
    TemporalActionKind,
    TemporalContinuity,
    TemporalEvent,
    TemporalEventKind,
    TemporalFrame,
    TemporalObservation,
)

TEMPORAL_VERIFIER_TYPE = "temporal_policy"
TEMPORAL_VERIFIER_VERSION = "padawan-temporal-policy-v1"

_TEACHER_MODES = (
    TeacherMode.SOCRATIC_HINT,
    TeacherMode.DIAGNOSTIC_CRITIQUE,
    TeacherMode.GENERAL_PRINCIPLE,
    TeacherMode.MINIMAL_REPAIR,
    TeacherMode.CONTRASTIVE_EXPLANATION,
    TeacherMode.METACOGNITIVE_FEEDBACK,
)

_COMPETENCY_TITLES = {
    TemporalScenarioFamily.GAP_CONTINUITY: "Conversational gap continuity",
    TemporalScenarioFamily.ACTIVITY_HONESTY: "Activity-grounded status reporting",
    TemporalScenarioFamily.OBSERVATION_FRESHNESS: "Time-aware evidence freshness",
    TemporalScenarioFamily.DURATION_CALIBRATION: "Prospective duration calibration",
    TemporalScenarioFamily.ETA_REVISION: "Online ETA revision and polling",
}


class TemporalGroundingCorpusGenerator:
    generator_version = "padawan-temporal-grounding-v1"

    def competencies(self, *, created_at: datetime | None = None) -> list[CompetencyRecord]:
        timestamp = created_at or datetime.now(UTC)
        return [
            CompetencyRecord(
                competency_id=_competency_id(family),
                title=_COMPETENCY_TITLES[family],
                description=(
                    "Temporal grounding under an authoritative event frame, including elapsed "
                    "time, activity evidence, duration distributions, and calibrated actions."
                ),
                parent_competency_id=None,
                prerequisite_competency_ids=(),
                grader_requirements=(TEMPORAL_VERIFIER_TYPE, TEMPORAL_VERIFIER_VERSION),
                permissible_teacher_modes=_TEACHER_MODES,
                difficulty_calibration={
                    "scale": "0_to_1",
                    "generator": self.generator_version,
                    "dimensions": [
                        "elapsed_timescale",
                        "freshness_uncertainty",
                        "duration_quantiles",
                        "online_revision",
                    ],
                },
                version=1,
                created_at=timestamp,
            )
            for family in TemporalScenarioFamily
        ]

    def generate(
        self,
        *,
        pool: CorpusPool,
        seed: int,
        groups_per_family: int = 2,
        siblings_per_group: int = 3,
        families: tuple[TemporalScenarioFamily, ...] | None = None,
        created_at: datetime | None = None,
    ) -> list[CorpusItemRecord]:
        if pool == CorpusPool.QUARANTINE:
            raise ValueError("new temporal items cannot be generated into quarantine")
        if groups_per_family <= 0 or siblings_per_group < 2:
            raise ValueError("temporal matched generation requires at least two siblings")
        timestamp = created_at or datetime.now(UTC)
        selected = families or tuple(TemporalScenarioFamily)
        suffix = {
            CorpusPool.CURRICULUM: "train",
            CorpusPool.ROTATING_SHADOW: "shadow",
            CorpusPool.SEALED_ANCHOR: "sealed",
        }[pool]
        records: list[CorpusItemRecord] = []
        for family_index, family in enumerate(selected):
            template_family_id = f"temporal-{family.value}-v1-{suffix}"
            for group_index in range(groups_per_family):
                group_seed = seed + family_index * 1_000_003 + group_index * 10_007
                group_digest = sha256_digest(str(group_seed))[7:19]
                instance_group_id = f"{template_family_id}-g{group_digest}"
                for sibling_index in range(siblings_per_group):
                    problem_seed = group_seed + sibling_index * 101
                    scenario = self.scenario(
                        family=family,
                        seed=group_seed,
                        variant=sibling_index,
                        split=pool.value,
                        created_at=timestamp,
                    )
                    item_digest = sha256_digest(
                        {
                            "scenario_id": scenario.scenario_id,
                            "group": instance_group_id,
                            "sibling": sibling_index,
                        }
                    )[7:23]
                    records.append(
                        CorpusItemRecord(
                            competency_id=_competency_id(family),
                            template_family_id=template_family_id,
                            instance_group_id=instance_group_id,
                            item_id=f"item-temporal-{family.value}-{item_digest}",
                            generation_seed=problem_seed,
                            generator_version=self.generator_version,
                            difficulty=_difficulty(family, sibling_index),
                            prompt=scenario.current_user_message.content,
                            expected_answer={
                                "kind": "temporal_decision",
                                "decision": _expected_decision(scenario.oracle),
                            },
                            verifier_spec=VerifierSpec(
                                verifier_type=TEMPORAL_VERIFIER_TYPE,
                                verifier_version=TEMPORAL_VERIFIER_VERSION,
                                parameters={"scenario": scenario.model_dump(mode="json")},
                            ),
                            pool=pool,
                            source="deterministic:padawan.temporal_grounding_v1",
                            rights=project_authored_internal_rights(reviewed_at=timestamp),
                            contamination_scope="instance_group",
                            created_at=timestamp,
                        )
                    )
        return records

    def scenario(
        self,
        *,
        family: TemporalScenarioFamily,
        seed: int,
        variant: int,
        split: str,
        created_at: datetime,
    ) -> TemporalScenarioManifest:
        rng = random.Random(seed)
        anchor = datetime(2026, 1, 15, 14, 0, tzinfo=UTC) + timedelta(
            days=rng.randint(0, 240), minutes=rng.randint(0, 600)
        )
        builder = {
            TemporalScenarioFamily.GAP_CONTINUITY: _gap_scenario,
            TemporalScenarioFamily.ACTIVITY_HONESTY: _activity_scenario,
            TemporalScenarioFamily.OBSERVATION_FRESHNESS: _freshness_scenario,
            TemporalScenarioFamily.DURATION_CALIBRATION: _duration_scenario,
            TemporalScenarioFamily.ETA_REVISION: _eta_revision_scenario,
        }[family]
        frame, prior, current, oracle = builder(
            seed=seed,
            variant=variant,
            anchor=anchor,
        )
        scenario_digest = sha256_digest(
            {
                "family": family.value,
                "seed": seed,
                "variant": variant,
                "split": split,
                "frame": frame.model_dump(mode="json"),
            }
        )
        return TemporalScenarioManifest(
            scenario_id=f"temporal-scenario-{scenario_digest[7:31]}",
            family=family,
            frame=frame,
            prior_conversation=prior,
            current_user_message=current,
            oracle=oracle,
            split=split,
            created_at=created_at,
        )


def _gap_scenario(
    *, seed: int, variant: int, anchor: datetime
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
    TemporalScenarioOracle,
]:
    gaps = (timedelta(seconds=45), timedelta(hours=8), timedelta(days=45))
    gap = gaps[variant % len(gaps)]
    continuity = _continuity(gap)
    frame, prior, current = _base_frame(
        seed=seed,
        variant=variant,
        current_at=anchor,
        gap=gap,
        current_text="I tried the same command again and it is still failing. What next?",
    )
    oracle = TemporalScenarioOracle(
        continuity=continuity,
        acknowledge_gap=continuity == TemporalContinuity.REESTABLISH_CONTEXT,
        required_actions=(
            _action(TemporalActionKind.CONTINUE, "diagnosis", "time_irrelevant_to_next_step"),
        ),
        forbidden_response_fragments=_false_activity_fragments(),
    )
    return frame, prior, current, oracle


def _activity_scenario(
    *, seed: int, variant: int, anchor: datetime
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
    TemporalScenarioOracle,
]:
    gap = (timedelta(hours=4), timedelta(days=12), timedelta(days=90))[variant % 3]
    frame, prior, current = _base_frame(
        seed=seed,
        variant=variant,
        current_at=anchor,
        gap=gap,
        current_text="Where are we on this task?",
    )
    oracle = TemporalScenarioOracle(
        continuity=_continuity(gap),
        acknowledge_gap=gap >= timedelta(days=30),
        required_actions=(
            _action(
                TemporalActionKind.CONTINUE,
                "task_status",
                "no_background_activity_recorded",
            ),
        ),
        forbidden_response_fragments=_false_activity_fragments(),
    )
    return frame, prior, current, oracle


def _freshness_scenario(
    *, seed: int, variant: int, anchor: datetime
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
    TemporalScenarioOracle,
]:
    gap = timedelta(days=14)
    frame, prior, current = _base_frame(
        seed=seed,
        variant=variant,
        current_at=anchor,
        gap=gap,
        current_text="Please continue from the repository state we inspected earlier.",
    )
    freshness = (
        FreshnessDisposition.FRESH,
        FreshnessDisposition.UNKNOWN,
        FreshnessDisposition.STALE,
    )[variant % 3]
    observation = TemporalObservation(
        observation_id=f"workspace-observation-{abs(seed)}",
        subject="workspace",
        observed_at=anchor - gap,
        version=f"sha256:{sha256_digest(str(seed))[7:]}",
        freshness=freshness,
        freshness_policy="revalidate_before_state_dependent_action",
    )
    frame = frame.model_copy(update={"observations": (observation,)})
    action = (
        _action(TemporalActionKind.CONTINUE, "workspace", "version_confirmed_fresh")
        if freshness == FreshnessDisposition.FRESH
        else _action(
            TemporalActionKind.REVALIDATE,
            "workspace",
            "state_freshness_not_established",
        )
    )
    oracle = TemporalScenarioOracle(
        continuity=_continuity(gap),
        acknowledge_gap=False,
        required_actions=(action,),
        forbidden_response_fragments=_forbidden_fragments_for(action),
    )
    return frame, prior, current, oracle


def _duration_scenario(
    *, seed: int, variant: int, anchor: datetime
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
    TemporalScenarioOracle,
]:
    elapsed = 35.0
    p50 = (120.0, 180.0, 240.0)[variant % 3]
    p90 = (300.0, 420.0, 600.0)[variant % 3]
    profile = _synthetic_profile(
        seed,
        anchor,
        operation_type="deployment",
        p50=p50,
        p90=p90,
        p95=p90 * 1.35,
    )
    started_at = anchor - timedelta(seconds=elapsed)
    p50_at = started_at + timedelta(seconds=p50)
    p90_at = started_at + timedelta(seconds=p90)
    next_poll = anchor + timedelta(seconds=30)
    operation_id = f"operation-deploy-{abs(seed)}"
    operation = ActiveOperation(
        operation_id=operation_id,
        operation_type="deployment",
        status=OperationStatus.RUNNING,
        enqueued_at=started_at,
        started_at=started_at,
        last_progress_at=anchor - timedelta(seconds=5),
        progress=0.35,
        duration_profile=profile,
        expected_completion_p50=p50_at,
        expected_completion_p90=p90_at,
        next_safe_poll_at=next_poll,
        timeout_at=started_at + timedelta(seconds=p90 * 2),
        cancellable=True,
    )
    frame, prior, current = _base_frame(
        seed=seed,
        variant=variant,
        current_at=anchor,
        gap=timedelta(seconds=elapsed),
        current_text="How long should the deployment take, and should we check it now?",
        active_operations=(operation,),
    )
    forecast = DurationForecast(
        operation_id=operation_id,
        p50_completed_at=p50_at,
        p90_completed_at=p90_at,
        source_profile_id=profile.profile_id,
    )
    action = _action(
        TemporalActionKind.WAIT,
        operation_id,
        "poll_not_due",
        execute_at=next_poll,
    )
    oracle = TemporalScenarioOracle(
        continuity=TemporalContinuity.IMMEDIATE_CONTINUATION,
        acknowledge_gap=False,
        required_actions=(action,),
        required_duration_forecasts=(forecast,),
        next_check_at=next_poll,
        forbidden_response_fragments=_forbidden_fragments_for(action),
    )
    return frame, prior, current, oracle


def _eta_revision_scenario(
    *, seed: int, variant: int, anchor: datetime
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
    TemporalScenarioOracle,
]:
    mode = variant % 3
    elapsed = (75.0, 210.0, 720.0)[mode]
    p50, p90 = 180.0, 480.0
    profile = _synthetic_profile(
        seed,
        anchor,
        operation_type="repository_index",
        p50=p50,
        p90=p90,
        p95=720.0,
    )
    started_at = anchor - timedelta(seconds=elapsed)
    operation_id = f"operation-index-{abs(seed)}"
    next_poll = anchor + timedelta(seconds=30) if mode == 0 else anchor - timedelta(seconds=15)
    timeout_at = anchor + timedelta(seconds=300) if mode < 2 else anchor - timedelta(seconds=1)
    operation = ActiveOperation(
        operation_id=operation_id,
        operation_type="repository_index",
        status=OperationStatus.RUNNING,
        enqueued_at=started_at,
        started_at=started_at,
        last_progress_at=(anchor - timedelta(seconds=(10, 100, 600)[mode])),
        progress=(0.45, 0.45, 0.45)[mode],
        duration_profile=profile,
        expected_completion_p50=started_at + timedelta(seconds=p50),
        expected_completion_p90=started_at + timedelta(seconds=p90),
        next_safe_poll_at=next_poll,
        timeout_at=timeout_at,
        cancellable=True,
    )
    frame, prior, current = _base_frame(
        seed=seed,
        variant=variant,
        current_at=anchor,
        gap=timedelta(seconds=elapsed),
        current_text="The indexing job has not completed yet. What should happen next?",
        active_operations=(operation,),
    )
    if mode == 0:
        action = _action(
            TemporalActionKind.WAIT,
            operation_id,
            "healthy_operation_poll_not_due",
            execute_at=next_poll,
        )
        next_check = next_poll
    elif mode == 1:
        action = _action(
            TemporalActionKind.POLL,
            operation_id,
            "scheduled_poll_due",
            execute_at=anchor,
        )
        next_check = anchor
    else:
        action = _action(
            TemporalActionKind.INSPECT,
            operation_id,
            "operation_exceeded_timeout_without_progress",
            execute_at=anchor,
        )
        next_check = anchor
    oracle = TemporalScenarioOracle(
        continuity=_continuity(timedelta(seconds=elapsed)),
        acknowledge_gap=False,
        required_actions=(action,),
        next_check_at=next_check,
        forbidden_response_fragments=_forbidden_fragments_for(action),
    )
    return frame, prior, current, oracle


def _base_frame(
    *,
    seed: int,
    variant: int,
    current_at: datetime,
    gap: timedelta,
    current_text: str,
    active_operations: tuple[ActiveOperation, ...] = (),
) -> tuple[
    TemporalFrame,
    tuple[TemporalConversationMessage, ...],
    TemporalConversationMessage,
]:
    previous_assistant_at = current_at - gap
    previous_user_at = previous_assistant_at - timedelta(seconds=20)
    prior = (
        TemporalConversationMessage(
            message_id=f"message-{abs(seed)}-prior-user",
            role="user",
            content="Please investigate the task and tell me what you find.",
            sent_at=previous_user_at,
        ),
        TemporalConversationMessage(
            message_id=f"message-{abs(seed)}-prior-assistant",
            role="assistant",
            content="I recorded the current evidence and the next safe step.",
            sent_at=previous_assistant_at,
        ),
    )
    current = TemporalConversationMessage(
        message_id=f"message-{abs(seed)}-current-user",
        role="user",
        content=current_text,
        sent_at=current_at,
    )
    events = (
        TemporalEvent(
            event_id=f"event-{abs(seed)}-prior-user",
            kind=TemporalEventKind.USER_MESSAGE,
            occurred_at=previous_user_at,
            subject="conversation",
        ),
        TemporalEvent(
            event_id=f"event-{abs(seed)}-prior-assistant",
            kind=TemporalEventKind.ASSISTANT_MESSAGE,
            occurred_at=previous_assistant_at,
            subject="conversation",
        ),
        TemporalEvent(
            event_id=f"event-{abs(seed)}-current-user",
            kind=TemporalEventKind.USER_MESSAGE,
            occurred_at=current_at,
            subject="conversation",
        ),
    )
    frame = TemporalFrame(
        frame_id=("temporal-frame-" + sha256_digest((seed, variant, current_at.isoformat()))[7:31]),
        sequence=3,
        now_utc=current_at + timedelta(seconds=1),
        user_timezone="America/New_York",
        conversation_started_at=previous_user_at,
        current_event_at=current_at,
        previous_user_message_at=previous_user_at,
        previous_assistant_message_at=previous_assistant_at,
        elapsed_since_previous_exchange_seconds=gap.total_seconds(),
        events=events,
        agent_activity_since_last_exchange=(),
        observations=(),
        active_operations=active_operations,
    )
    return frame, prior, current


def _synthetic_profile(
    seed: int,
    as_of: datetime,
    *,
    operation_type: str,
    p50: float,
    p90: float,
    p95: float,
) -> DurationProfile:
    fingerprint = sha256_digest({"environment": "temporal-synthetic-v1", "seed": seed % 7})
    profile_evidence_id = sha256_digest(
        {
            "seed": seed,
            "operation_type": operation_type,
            "p50": p50,
            "p90": p90,
            "p95": p95,
        }
    )[7:19]
    source_span_ids = tuple(
        f"synthetic-span-{profile_evidence_id}-{index:02d}" for index in range(10)
    )
    identity = sha256_digest(
        {
            "seed": seed,
            "operation_type": operation_type,
            "p50": p50,
            "p90": p90,
            "p95": p95,
            "source_span_ids": source_span_ids,
        }
    )
    return DurationProfile(
        profile_id=f"duration-profile-synthetic-{identity[7:31]}",
        operation_type=operation_type,
        environment_fingerprint=fingerprint,
        workload_class="matched_temporal_scenario",
        sample_count=len(source_span_ids),
        success_count=9,
        timeout_count=1,
        p50_seconds=p50,
        p90_seconds=p90,
        p95_seconds=p95,
        timeout_probability=0.1,
        source_span_ids=source_span_ids,
        as_of=as_of,
        created_at=as_of,
    )


def _continuity(gap: timedelta) -> TemporalContinuity:
    if gap <= timedelta(minutes=5):
        return TemporalContinuity.IMMEDIATE_CONTINUATION
    if gap <= timedelta(days=7):
        return TemporalContinuity.RESUME_AFTER_GAP
    return TemporalContinuity.REESTABLISH_CONTEXT


def _action(
    kind: TemporalActionKind,
    subject: str,
    rationale_code: str,
    *,
    execute_at: datetime | None = None,
) -> TemporalAction:
    return TemporalAction(
        kind=kind,
        subject=subject,
        execute_at=execute_at,
        rationale_code=rationale_code,
    )


def _false_activity_fragments() -> tuple[str, ...]:
    return (
        "i have been working",
        "i've been working",
        "i have kept working",
        "i've kept working",
        "i kept working",
        "i continued working",
        "i was working in the background",
        "i spent the last",
    )


def _forbidden_fragments_for(action: TemporalAction) -> tuple[str, ...]:
    action_specific = {
        TemporalActionKind.WAIT: (
            "check it now",
            "poll now",
            "continue immediately",
        ),
        TemporalActionKind.POLL: ("keep waiting", "wait until later"),
        TemporalActionKind.REVALIDATE: (
            "no need to revalidate",
            "continue without revalidating",
        ),
        TemporalActionKind.INSPECT: ("keep waiting", "nothing needs inspection"),
    }.get(action.kind, ())
    return tuple(sorted(set(_false_activity_fragments() + action_specific)))


def _competency_id(family: TemporalScenarioFamily) -> str:
    return f"temporal.{family.value}"


def _difficulty(family: TemporalScenarioFamily, sibling_index: int) -> float:
    base = {
        TemporalScenarioFamily.GAP_CONTINUITY: 0.3,
        TemporalScenarioFamily.ACTIVITY_HONESTY: 0.35,
        TemporalScenarioFamily.OBSERVATION_FRESHNESS: 0.5,
        TemporalScenarioFamily.DURATION_CALIBRATION: 0.65,
        TemporalScenarioFamily.ETA_REVISION: 0.75,
    }[family]
    return min(0.95, base + (sibling_index % 3) * 0.05)


def _expected_decision(oracle: TemporalScenarioOracle) -> dict[str, Any]:
    return {
        "continuity": oracle.continuity.value,
        "acknowledge_gap": oracle.acknowledge_gap,
        "claims_continuous_activity": False,
        "actions": [action.model_dump(mode="json") for action in oracle.required_actions],
        "duration_forecasts": [
            forecast.model_dump(mode="json") for forecast in oracle.required_duration_forecasts
        ],
        "next_check_at": oracle.next_check_at.isoformat() if oracle.next_check_at else None,
        "response": _authored_response(oracle),
    }


def _authored_response(oracle: TemporalScenarioOracle) -> str:
    action = oracle.required_actions[0]
    if action.subject == "diagnosis":
        if oracle.acknowledge_gap:
            return (
                "Enough time has passed that I should reestablish the current context, then "
                "continue the diagnosis from the recorded failure."
            )
        return "The elapsed time does not change the next diagnostic step, so we can continue."
    if action.subject == "task_status":
        return (
            "No background activity is recorded since the last exchange. I can continue from "
            "the stored task state now."
        )
    if action.subject == "workspace":
        if action.kind == TemporalActionKind.REVALIDATE:
            return (
                "The stored workspace observation is not established as fresh, so I should "
                "revalidate it before taking a state-dependent action."
            )
        return "The workspace observation is still fresh, so I can continue from that state."
    if oracle.required_duration_forecasts:
        forecast = oracle.required_duration_forecasts[0]
        check_at = oracle.next_check_at or action.execute_at
        return (
            f"The recorded profile puts median completion at "
            f"{forecast.p50_completed_at.isoformat()} and p90 completion at "
            f"{forecast.p90_completed_at.isoformat()}. The next safe check is "
            f"{check_at.isoformat() if check_at is not None else 'not yet scheduled'}, so I "
            "should wait rather than poll early."
        )
    if action.kind == TemporalActionKind.WAIT:
        return (
            f"The operation is still within its expected window; the next safe poll is "
            f"{action.execute_at.isoformat() if action.execute_at is not None else 'pending'}."
        )
    if action.kind == TemporalActionKind.POLL:
        return "The scheduled check is due, so I should poll the operation now."
    if action.kind == TemporalActionKind.INSPECT:
        return (
            "The operation exceeded its timeout without recent progress, so I should inspect "
            "the failure state now."
        )
    return "I will take the action supported by the authoritative temporal evidence."
