from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from padawan.domains.contracts import RewardMissingAction, TrainingLane, VerifierDisposition
from padawan.domains.magellan_improvement import (
    MagellanAgentTrace,
    MagellanScenarioFamily,
    MagellanScenarioGenerator,
    MagellanScenarioVerifier,
    MagellanVerificationBundle,
    MagellanWorldSnapshot,
    decide_magellan_training_eligibility,
    default_magellan_reward_policy,
)
from padawan.domains.magellan_improvement.contracts import (
    MagellanAgentProtocol,
    MagellanApprovalDecision,
    MagellanApprovalStatus,
    MagellanEnvironmentAssessment,
    MagellanEnvironmentHandshake,
    MagellanExternalEffects,
    MagellanFailure,
    MagellanFailureClass,
    MagellanIdempotencyMode,
    MagellanNetworkPolicy,
    MagellanPlanRecord,
    MagellanPlanStatus,
    MagellanPlanStep,
    MagellanRepositorySnapshot,
    MagellanRuntimeSecretPolicy,
    MagellanSourceIsolation,
    MagellanTenantIsolation,
    MagellanToolCall,
    MagellanToolCapability,
    MagellanToolObservation,
    MagellanToolStatus,
    MagellanToolTier,
    MagellanTraceStatus,
    MagellanValidatorResult,
    MagellanWorldIsolation,
)
from padawan.models.contracts import CorpusPool, ResearchRole
from padawan.models.hashing import sha256_digest

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _digest(value: object) -> str:
    return sha256_digest(value)


def _tool(
    name: str,
    tier: MagellanToolTier,
    *,
    externally_effectful: bool = False,
) -> MagellanToolCapability:
    return MagellanToolCapability(
        tool_name=name,
        tier=tier,
        validators=(f"{name}.validator",) if tier >= MagellanToolTier.COMMIT_RECOVERABLE else (),
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        externally_effectful=externally_effectful,
    )


def _environment(
    *,
    protocol: MagellanAgentProtocol = MagellanAgentProtocol.RESPONSES,
) -> MagellanEnvironmentAssessment:
    tracked_diff_digest = _digest(b"")
    untracked_manifest_digest = _digest([])
    dependency_digest = _digest("dependencies")
    migration_digest = _digest("migrations")
    source_digest = _digest(
        {
            "commit_sha": "a" * 40,
            "tracked_diff_digest": tracked_diff_digest,
            "untracked_manifest_digest": untracked_manifest_digest,
            "excluded_volatile_paths": (),
            "excluded_sensitive_paths": (),
            "dependency_digest": dependency_digest,
            "migration_digest": migration_digest,
        }
    )
    repository = MagellanRepositorySnapshot(
        snapshot_id=f"magellan-source-{source_digest[7:31]}",
        commit_sha="a" * 40,
        branch="my-new-branch",
        dirty=False,
        tracked_changed_paths=(),
        included_untracked_files=(),
        excluded_volatile_paths=(),
        excluded_sensitive_paths=(),
        tracked_diff_digest=tracked_diff_digest,
        untracked_manifest_digest=untracked_manifest_digest,
        dependency_digest=dependency_digest,
        migration_digest=migration_digest,
        source_digest=source_digest,
        created_at=NOW,
    )
    handshake = MagellanEnvironmentHandshake(
        environment_id="magellan-sandbox-test",
        repository_source_digest=source_digest,
        driver_digest=_digest("driver"),
        database_backend="postgresql",
        database_schema_digest=_digest("schema"),
        authorization_policy_digest=_digest("authorization"),
        world_schema_version="1.0.0",
        source_isolation=MagellanSourceIsolation.CONTENT_ADDRESSED_COPY,
        runtime_secret_policy=MagellanRuntimeSecretPolicy.ENV_ALLOWLIST_ONLY,
        world_isolation=MagellanWorldIsolation.DATABASE_CLONE,
        reset_protocol_version="1.0.0",
        reset_verified=True,
        matched_worlds_independent=True,
        network_policy=MagellanNetworkPolicy.DENIED,
        external_effects=MagellanExternalEffects.RECORDED_MOCKS,
        agent_protocol=protocol,
        tenant_isolation=MagellanTenantIsolation.ENFORCED,
        idempotency_mode=MagellanIdempotencyMode.DURABLE,
        tool_surface=(
            _tool("query_shipment", MagellanToolTier.READ),
            _tool("update_shipment_intake", MagellanToolTier.COMMIT_RECOVERABLE),
            _tool(
                "send_message_to_shipper",
                MagellanToolTier.COMMIT_RECOVERABLE,
                externally_effectful=True,
            ),
            _tool("lookup_rate", MagellanToolTier.READ),
            _tool("draft_quote_for_shipper", MagellanToolTier.PROPOSE),
            _tool("find_qualified_carriers", MagellanToolTier.READ),
            _tool("draft_carrier_outreach", MagellanToolTier.PROPOSE),
            _tool(
                "send_carrier_outreach",
                MagellanToolTier.COMMIT_RECOVERABLE,
                externally_effectful=True,
            ),
            _tool(
                "book_carrier",
                MagellanToolTier.COMMIT_REGULATED,
                externally_effectful=True,
            ),
        ),
        created_at=NOW,
    )
    blockers = handshake.evaluation_blockers()
    return MagellanEnvironmentAssessment(
        repository=repository,
        handshake=handshake,
        environment_fingerprint=handshake.fingerprint,
        ready=not blockers,
        blockers=blockers,
    )


def _scenario(environment, family: MagellanScenarioFamily, *, seed: int = 17):
    assert environment.handshake is not None
    return MagellanScenarioGenerator(
        environment_fingerprint=environment.handshake.fingerprint
    ).scenario(family=family, seed=seed, split=CorpusPool.CURRICULUM.value, created_at=NOW)


def _world(scenario, state) -> MagellanWorldSnapshot:
    state_digest = _digest(state)
    snapshot_digest = _digest(
        {
            "allocation_id": "allocation-1",
            "world_id": "world-1",
            "isolation_token_digest": _digest("isolation-1"),
            "database_snapshot_id": "database-snapshot-1",
            "tenant_id": scenario.tenant_id,
            "user_id": scenario.user_id,
            "authorization_snapshot_digest": scenario.authorization_snapshot_digest,
            "environment_fingerprint": scenario.environment_fingerprint,
            "state_digest": state_digest,
        }
    )
    return MagellanWorldSnapshot(
        snapshot_id=f"magellan-world-{snapshot_digest[7:31]}",
        allocation_id="allocation-1",
        world_id="world-1",
        isolation_token_digest=_digest("isolation-1"),
        database_snapshot_id="database-snapshot-1",
        tenant_id=scenario.tenant_id,
        user_id=scenario.user_id,
        authorization_snapshot_digest=scenario.authorization_snapshot_digest,
        environment_fingerprint=scenario.environment_fingerprint,
        state_digest=state_digest,
        observable_state=state,
        created_at=NOW,
    )


def _trace(
    scenario,
    *,
    status: MagellanTraceStatus,
    final_state_digest: str,
    calls: tuple[MagellanToolCall, ...] = (),
    observations: tuple[MagellanToolObservation, ...] = (),
    plans: tuple[MagellanPlanRecord, ...] = (),
    approvals: tuple[MagellanApprovalDecision, ...] = (),
    failures: tuple[MagellanFailure, ...] = (),
    role: ResearchRole = ResearchRole.TARGET,
    protocol: MagellanAgentProtocol = MagellanAgentProtocol.RESPONSES,
) -> MagellanAgentTrace:
    return MagellanAgentTrace(
        trace_id=f"trace-{scenario.family.value}",
        scenario_id=scenario.scenario_id,
        student_id="inkling-small",
        checkpoint_id="checkpoint-inkling-small",
        state_id="state-1",
        research_role=role,
        tenant_id=scenario.tenant_id,
        user_id=scenario.user_id,
        authorization_snapshot_digest=scenario.authorization_snapshot_digest,
        environment_fingerprint=scenario.environment_fingerprint,
        agent_protocol=protocol,
        status=status,
        agent_latency_ms=1_000,
        agent_cost_usd=0.1,
        agent_input_tokens=100,
        agent_output_tokens=50,
        plans=plans,
        calls=calls,
        observations=observations,
        approvals=approvals,
        failures=failures,
        final_answer="done",
        final_state_digest=final_state_digest,
        created_at=NOW,
        completed_at=NOW + timedelta(seconds=5),
    )


def _call(
    scenario,
    *,
    call_id: str,
    sequence: int,
    tool_name: str,
    tier: MagellanToolTier,
    inputs: dict[str, object],
    idempotency_key: str | None = None,
    approval_bypass_used: bool = False,
) -> MagellanToolCall:
    return MagellanToolCall(
        call_id=call_id,
        sequence=sequence,
        tool_name=tool_name,
        tenant_id=scenario.tenant_id,
        user_id=scenario.user_id,
        tier=tier,
        inputs=inputs,
        input_digest=_digest(inputs),
        idempotency_key=idempotency_key,
        approval_bypass_used=approval_bypass_used,
        requested_at=NOW + timedelta(seconds=sequence),
    )


def _observation(
    *,
    call: MagellanToolCall,
    status: MagellanToolStatus,
    output: dict[str, object],
    before_digest: str,
    after_digest: str,
    mutation: bool,
    cached: bool = False,
    reason: str | None = None,
) -> MagellanToolObservation:
    validator_results = (
        (
            MagellanValidatorResult(
                validator_id=f"{call.tool_name}.validator",
                passed=True,
                summary="validator passed",
                evidence_refs=(f"validator-evidence-{call.call_id}",),
            ),
        )
        if call.tier is not None
        and call.tier >= MagellanToolTier.COMMIT_RECOVERABLE
        and status in {MagellanToolStatus.SUCCEEDED, MagellanToolStatus.WAITING}
        else ()
    )
    return MagellanToolObservation(
        observation_id=f"observation-{call.call_id}",
        call_id=call.call_id,
        status=status,
        output=output,
        output_digest=_digest(output),
        state_before_digest=before_digest,
        state_after_digest=after_digest,
        mutation_occurred=mutation,
        cached=cached,
        reason=reason,
        validator_results=validator_results,
        provenance_refs=(
            (f"provenance-{call.call_id}",)
            if status in {MagellanToolStatus.SUCCEEDED, MagellanToolStatus.WAITING}
            else ()
        ),
        latency_ms=25,
        cost_usd=0.02,
        observed_at=NOW + timedelta(seconds=call.sequence, milliseconds=500),
    )


def _idempotent_case(*, approval_bypass_used: bool = False):
    environment = _environment()
    scenario = _scenario(environment, MagellanScenarioFamily.IDEMPOTENT_REPLAY)
    before_state = MagellanScenarioGenerator.initial_world(seed=scenario.seed)
    after_state = deepcopy(before_state)
    after_state["effects"]["shipper_messages"] = 1
    before = _world(scenario, before_state)
    after = _world(scenario, after_state)
    key = f"{scenario.idempotency_namespace}:shipper-message"
    inputs = {
        "shipment_id": scenario.task_inputs["shipment_id"],
        "message": scenario.task_inputs["message"],
    }
    first = _call(
        scenario,
        call_id="call-1",
        sequence=1,
        tool_name="send_message_to_shipper",
        tier=MagellanToolTier.COMMIT_RECOVERABLE,
        inputs=inputs,
        idempotency_key=key,
        approval_bypass_used=approval_bypass_used,
    )
    second = _call(
        scenario,
        call_id="call-2",
        sequence=2,
        tool_name="send_message_to_shipper",
        tier=MagellanToolTier.COMMIT_RECOVERABLE,
        inputs=inputs,
        idempotency_key=key,
        approval_bypass_used=approval_bypass_used,
    )
    output = {"message_id": "MSG-1"}
    first_observation = _observation(
        call=first,
        status=MagellanToolStatus.SUCCEEDED,
        output=output,
        before_digest=before.state_digest,
        after_digest=after.state_digest,
        mutation=True,
    )
    second_observation = _observation(
        call=second,
        status=MagellanToolStatus.SUCCEEDED,
        output=output,
        before_digest=after.state_digest,
        after_digest=after.state_digest,
        mutation=False,
        cached=True,
    )
    trace = _trace(
        scenario,
        status=MagellanTraceStatus.COMPLETE,
        final_state_digest=after.state_digest,
        calls=(first, second),
        observations=(first_observation, second_observation),
    )
    return environment, scenario, before, after, trace


def test_idempotent_trace_verifies_and_is_training_eligible() -> None:
    environment, scenario, before, after, trace = _idempotent_case()
    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=trace,
        created_at=NOW,
    )

    assert bundle.task_verified is True
    assert all(gate.passed for gate in bundle.hard_gates)
    assert [result.disposition for result in bundle.verifier_results] == [
        VerifierDisposition.VERIFIED,
        VerifierDisposition.VERIFIED,
        VerifierDisposition.VERIFIED,
        VerifierDisposition.VERIFIED,
        VerifierDisposition.VERIFIED,
    ]
    assert {item.component_id: item.value for item in bundle.reward_observations} == {
        "task_completion": 1.0,
        "constraint_satisfaction": 1.0,
        "recovery_behavior": None,
        "tool_efficiency": 0.5,
        "normalized_cost": 0.0056,
    }
    policy = default_magellan_reward_policy()
    assert [component.component_id for component in policy.components] == [
        "task_completion",
        "constraint_satisfaction",
        "recovery_behavior",
        "tool_efficiency",
        "normalized_cost",
    ]
    assert policy.components[2].missing_action == RewardMissingAction.OMIT
    eligibility = decide_magellan_training_eligibility(
        reward_id="reward-magellan-1",
        bundle=bundle,
        trace=trace,
        scenario=scenario,
        created_at=NOW,
    )
    assert set(eligibility.allowed_lanes) == {
        TrainingLane.EVALUATION_ONLY,
        TrainingLane.PREFERENCE,
        TrainingLane.PROCESS,
        TrainingLane.RLVR,
        TrainingLane.SFT,
    }


def test_successful_looking_trace_cannot_compensate_for_approval_bypass() -> None:
    environment, scenario, before, after, trace = _idempotent_case(approval_bypass_used=True)
    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=trace,
        created_at=NOW,
    )

    assert bundle.task_verified is True
    assert bundle.verifier_results[1].disposition == VerifierDisposition.REJECTED
    assert bundle.hard_gates[1].passed is False
    eligibility = decide_magellan_training_eligibility(
        reward_id="reward-magellan-bypass",
        bundle=bundle,
        trace=trace,
        scenario=scenario,
        created_at=NOW,
    )
    assert eligibility.allowed_lanes == (TrainingLane.EVALUATION_ONLY,)


def test_idempotency_namespace_requires_a_delimited_exact_prefix() -> None:
    environment, scenario, before, after, trace = _idempotent_case()
    escaped_key = f"{scenario.idempotency_namespace}-other:shipper-message"
    escaped_calls = tuple(
        call.model_copy(update={"idempotency_key": escaped_key}) for call in trace.calls
    )
    escaped_trace = trace.model_copy(update={"calls": escaped_calls})

    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=escaped_trace,
        created_at=NOW,
    )

    assert bundle.task_verified is True
    assert bundle.hard_gates[1].passed is False


def test_verification_bundle_cannot_detach_a_gate_from_its_result() -> None:
    environment, scenario, before, after, trace = _idempotent_case()
    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=trace,
        created_at=NOW,
    )
    payload = bundle.model_dump(mode="json")
    payload["hard_gates"][0]["passed"] = False
    payload["hard_gates"][0]["disposition"] = VerifierDisposition.REJECTED.value

    with pytest.raises(ValueError, match="hard gate differs"):
        MagellanVerificationBundle.model_validate(payload, strict=False)


def test_safe_failure_is_process_preference_evidence_but_baseline_and_sealed_are_not() -> None:
    environment = _environment()
    scenario = _scenario(environment, MagellanScenarioFamily.IDEMPOTENT_REPLAY)
    state = MagellanScenarioGenerator.initial_world(seed=scenario.seed)
    before = _world(scenario, state)
    after = _world(scenario, state)
    trace = _trace(
        scenario,
        status=MagellanTraceStatus.COMPLETE,
        final_state_digest=after.state_digest,
    )
    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=trace,
        created_at=NOW,
    )

    assert bundle.task_verified is False
    assert all(gate.passed for gate in bundle.hard_gates)
    decision = decide_magellan_training_eligibility(
        reward_id="reward-safe-failure",
        bundle=bundle,
        trace=trace,
        scenario=scenario,
        created_at=NOW,
    )
    assert set(decision.allowed_lanes) == {
        TrainingLane.EVALUATION_ONLY,
        TrainingLane.PREFERENCE,
        TrainingLane.PROCESS,
    }

    baseline_trace = trace.model_copy(update={"research_role": ResearchRole.BASELINE})
    baseline_bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=baseline_trace,
        created_at=NOW,
    )
    baseline_decision = decide_magellan_training_eligibility(
        reward_id="reward-restricted-baseline",
        bundle=baseline_bundle,
        trace=baseline_trace,
        scenario=scenario,
        created_at=NOW,
    )
    assert baseline_decision.allowed_lanes == (TrainingLane.EVALUATION_ONLY,)

    for pool in (CorpusPool.SEALED_ANCHOR, CorpusPool.QUARANTINE):
        restricted_scenario = MagellanScenarioGenerator(
            environment_fingerprint=scenario.environment_fingerprint
        ).scenario(
            family=MagellanScenarioFamily.IDEMPOTENT_REPLAY,
            seed=23,
            split=pool.value,
            created_at=NOW,
        )
        restricted_state = MagellanScenarioGenerator.initial_world(seed=restricted_scenario.seed)
        restricted_before = _world(restricted_scenario, restricted_state)
        restricted_after = _world(restricted_scenario, restricted_state)
        restricted_trace = _trace(
            restricted_scenario,
            status=MagellanTraceStatus.COMPLETE,
            final_state_digest=restricted_after.state_digest,
        )
        restricted_bundle = MagellanScenarioVerifier().verify(
            scenario=restricted_scenario,
            environment=environment,
            before=restricted_before,
            after=restricted_after,
            trace=restricted_trace,
            created_at=NOW,
        )
        restricted_decision = decide_magellan_training_eligibility(
            reward_id=f"reward-restricted-{pool.value}",
            bundle=restricted_bundle,
            trace=restricted_trace,
            scenario=restricted_scenario,
            created_at=NOW,
        )
        assert restricted_decision.allowed_lanes == (TrainingLane.EVALUATION_ONLY,)


def test_tenant_isolation_and_invalid_dependency_are_verified_refusals() -> None:
    environment = _environment()
    verifier = MagellanScenarioVerifier()

    tenant_scenario = _scenario(environment, MagellanScenarioFamily.TENANT_ISOLATION)
    tenant_state = MagellanScenarioGenerator.initial_world(seed=tenant_scenario.seed)
    tenant_before = _world(tenant_scenario, tenant_state)
    tenant_after = _world(tenant_scenario, tenant_state)
    tenant_call = _call(
        tenant_scenario,
        call_id="tenant-call",
        sequence=1,
        tool_name="query_shipment",
        tier=MagellanToolTier.READ,
        inputs={
            "shipment_id": tenant_scenario.task_inputs["shipment_id"],
            "target_tenant_id": tenant_scenario.task_inputs["target_tenant_id"],
        },
    )
    tenant_observation = _observation(
        call=tenant_call,
        status=MagellanToolStatus.REFUSED,
        output={},
        before_digest=tenant_before.state_digest,
        after_digest=tenant_after.state_digest,
        mutation=False,
        reason="tenant_not_authorized",
    )
    tenant_trace = _trace(
        tenant_scenario,
        status=MagellanTraceStatus.REFUSED,
        final_state_digest=tenant_after.state_digest,
        calls=(tenant_call,),
        observations=(tenant_observation,),
    )
    tenant_bundle = verifier.verify(
        scenario=tenant_scenario,
        environment=environment,
        before=tenant_before,
        after=tenant_after,
        trace=tenant_trace,
        created_at=NOW,
    )
    assert tenant_bundle.task_verified is True
    assert all(gate.passed for gate in tenant_bundle.hard_gates)

    plan_scenario = _scenario(
        environment,
        MagellanScenarioFamily.INVALID_DEPENDENCY,
        seed=19,
    )
    plan_state = MagellanScenarioGenerator.initial_world(seed=plan_scenario.seed)
    plan_before = _world(plan_scenario, plan_state)
    plan_after = _world(plan_scenario, plan_state)
    steps = (
        MagellanPlanStep(
            step_id="step-1",
            sequence=1,
            tool_name="query_shipment",
            inputs={},
            input_digest=_digest({}),
            depends_on=("step-2",),
        ),
        MagellanPlanStep(
            step_id="step-2",
            sequence=2,
            tool_name="lookup_rate",
            inputs={},
            input_digest=_digest({}),
        ),
    )
    rejected_plan = MagellanPlanRecord(
        plan_id="plan-invalid",
        revision=1,
        status=MagellanPlanStatus.REJECTED,
        steps=steps,
        rejection_reason="forward_dependency",
        created_at=NOW,
    )
    plan_trace = _trace(
        plan_scenario,
        status=MagellanTraceStatus.REFUSED,
        final_state_digest=plan_after.state_digest,
        plans=(rejected_plan,),
    )
    plan_bundle = verifier.verify(
        scenario=plan_scenario,
        environment=environment,
        before=plan_before,
        after=plan_after,
        trace=plan_trace,
        created_at=NOW,
    )
    assert plan_bundle.task_verified is True
    assert all(gate.passed for gate in plan_bundle.hard_gates)


def test_regulated_mutation_requires_recorded_approval() -> None:
    environment = _environment()
    scenario = _scenario(environment, MagellanScenarioFamily.REGULATED_APPROVAL)
    before_state = MagellanScenarioGenerator.initial_world(seed=scenario.seed)
    after_state = deepcopy(before_state)
    after_state["shipment"]["status"] = "tender_booked"
    after_state["effects"]["bookings"] = 1
    before = _world(scenario, before_state)
    after = _world(scenario, after_state)
    call = _call(
        scenario,
        call_id="book-call",
        sequence=1,
        tool_name="book_carrier",
        tier=MagellanToolTier.COMMIT_REGULATED,
        inputs={
            "carrier_id": scenario.task_inputs["approved_carrier_id"],
            "shipment_id": scenario.task_inputs["shipment_id"],
        },
        idempotency_key=f"{scenario.idempotency_namespace}:booking",
    )
    observation = _observation(
        call=call,
        status=MagellanToolStatus.SUCCEEDED,
        output={"booking_id": "BOOK-1"},
        before_digest=before.state_digest,
        after_digest=after.state_digest,
        mutation=True,
    )
    approval = MagellanApprovalDecision(
        approval_id="approval-1",
        call_id=call.call_id,
        tenant_id=scenario.tenant_id,
        user_id=scenario.user_id,
        status=MagellanApprovalStatus.APPROVED,
        decided_by="human-reviewer",
        decided_at=NOW + timedelta(seconds=1, milliseconds=250),
    )
    approved_trace = _trace(
        scenario,
        status=MagellanTraceStatus.COMPLETE,
        final_state_digest=after.state_digest,
        calls=(call,),
        observations=(observation,),
        approvals=(approval,),
    )
    verifier = MagellanScenarioVerifier()
    approved = verifier.verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=approved_trace,
        created_at=NOW,
    )
    missing = verifier.verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=approved_trace.model_copy(update={"approvals": ()}),
        created_at=NOW,
    )
    missing_validators = verifier.verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=approved_trace.model_copy(
            update={"observations": (observation.model_copy(update={"validator_results": ()}),)}
        ),
        created_at=NOW,
    )
    wrong_inputs = {**call.inputs, "carrier_id": "CAR-WRONG"}
    wrong_call = call.model_copy(
        update={"inputs": wrong_inputs, "input_digest": _digest(wrong_inputs)}
    )
    wrong_task = verifier.verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=approved_trace.model_copy(update={"calls": (wrong_call,)}),
        created_at=NOW,
    )

    assert approved.task_verified is True
    assert all(gate.passed for gate in approved.hard_gates)
    assert missing.task_verified is True
    assert missing.hard_gates[1].passed is False
    assert missing_validators.task_verified is True
    assert missing_validators.hard_gates[1].passed is False
    assert all(gate.passed for gate in wrong_task.hard_gates)
    assert wrong_task.task_verified is False


def test_infrastructure_failure_stays_missing_and_never_becomes_zero_reward() -> None:
    environment = _environment(protocol=MagellanAgentProtocol.CHAT_COMPLETIONS)
    scenario = _scenario(environment, MagellanScenarioFamily.IDEMPOTENT_REPLAY)
    state = MagellanScenarioGenerator.initial_world(seed=scenario.seed)
    before = _world(scenario, state)
    after = _world(scenario, state)
    failure = MagellanFailure(
        failure_id="failure-protocol",
        failure_class=MagellanFailureClass.AGENT_PROTOCOL,
        stage="agent_start",
        retryable=False,
        message="configured planner exposes Chat Completions only",
    )
    trace = _trace(
        scenario,
        status=MagellanTraceStatus.INFRASTRUCTURE_FAILURE,
        final_state_digest=after.state_digest,
        failures=(failure,),
        protocol=MagellanAgentProtocol.CHAT_COMPLETIONS,
    )

    bundle = MagellanScenarioVerifier().verify(
        scenario=scenario,
        environment=environment,
        before=before,
        after=after,
        trace=trace,
        created_at=NOW,
    )
    observations = {item.component_id: item for item in bundle.reward_observations}

    assert bundle.task_verified is False
    assert bundle.verifier_results[0].disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE
    assert bundle.verifier_results[2].disposition == VerifierDisposition.INFRASTRUCTURE_FAILURE
    assert observations["task_completion"].value is None
    assert observations["task_completion"].missing_reason is not None
    assert observations["normalized_cost"].value == 0.004
