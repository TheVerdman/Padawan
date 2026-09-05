from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from padawan.governance.amber import AmberAdmissionDecision, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
from padawan.models.tables import (
    AmberAdmissionDecisionRow,
    ProcessObservationDecisionRow,
    ProcessObservationRow,
    ProcessRolloutRow,
)
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
    RolloutStatus,
)
from padawan.pprl.coordinator import (
    ProcessActionProposal,
    ProcessActionResult,
    ProcessCoordinator,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.observation_contracts import ProcessWorkerObservation
from padawan.pprl.observations import ProcessObservationDeniedError, ProcessObservationStore
from padawan.pprl.store import ClaimedProcessRollout, ProcessStore
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    component,
    distribution,
    envelope,
    execution,
    fund_resources,
    program,
    worker_model,
)


class OneActionHandler:
    def __init__(self, *, target_class: str = "scientific_math") -> None:
        self.target_class = target_class
        self.executed = 0
        self.proposed = 0

    def propose(self, observation: ProcessWorkerObservation) -> ProcessActionProposal:
        assert isinstance(observation, ProcessWorkerObservation)
        self.proposed += 1
        return ProcessActionProposal(
            event_kind=ProcessEventKind.TOOL_INVOKED,
            role_id="researcher",
            worker_model_digest=sha256_digest(worker_model()),
            target_class=self.target_class,
            incremental_usage=ProjectBudgetUsage(
                actions=1,
                input_tokens=10,
                output_tokens=5,
                wall_time_seconds=0.1,
                cost=0.01,
            ),
            tool_id="calculator",
            tool_digest=component("calculator").digest,
            tool_operation="evaluate",
        )

    async def execute(
        self,
        claimed: ClaimedProcessRollout,
        proposal: ProcessActionProposal,
        admission: AmberAdmissionDecision,
        observation: ProcessWorkerObservation,
    ) -> ProcessActionResult:
        assert admission.rollout_id == claimed.rollout.rollout_id
        assert observation.state.objective == claimed.state.payload.objective
        self.executed += 1
        previous = claimed.state.payload.budget_usage
        increment = proposal.incremental_usage
        usage = ProjectBudgetUsage(
            actions=previous.actions + increment.actions,
            input_tokens=previous.input_tokens + increment.input_tokens,
            output_tokens=previous.output_tokens + increment.output_tokens,
            wall_time_seconds=float(previous.wall_time_seconds)
            + float(increment.wall_time_seconds),
            cost=float(previous.cost) + float(increment.cost),
        )
        return ProcessActionResult(
            event_payload={"summary": "21 * 2 = 42"},
            resulting_state=claimed.state.payload.model_copy(
                update={
                    "plan": ("calculation completed",),
                    "budget_usage": usage,
                }
            ),
        )


@pytest.mark.asyncio
async def test_coordinator_executes_only_after_amber_admission(database, pprl_now) -> None:
    store, amber = await _bootstrap_rollout(database, rollout_id="coordinator-rollout")
    handler = OneActionHandler()
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=handler,
        executor=handler,
        worker_id="process-worker",
        lease_for=timedelta(minutes=5),
    )
    assert await coordinator.run(budget=1) == 1
    assert handler.executed == 1
    async with database.transaction() as session:
        rollout = await store.get_rollout(session, rollout_id="coordinator-rollout")
        assert rollout.sequence == 1
        assert rollout.status == RolloutStatus.ACTIVE
        _initial, events = await store.replay(session, rollout_id="coordinator-rollout")
        assert events[0].kind == ProcessEventKind.TOOL_INVOKED
        assert events[0].amber_decision_id.startswith("amber-decision-")
        binding = await coordinator.observations.inspect_decision_binding(
            session, decision_id=events[0].amber_decision_id
        )
        receipt = await coordinator.observations.inspect_receipt(
            session, observation_id=binding.observation_id
        )
        assert receipt.state_digest == _initial.state_digest


@pytest.mark.asyncio
async def test_coordinator_never_executes_denied_boundary_action(database, pprl_now) -> None:
    store, amber = await _bootstrap_rollout(database, rollout_id="denied-rollout")
    handler = OneActionHandler(target_class="undeclared-target")
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=handler,
        executor=handler,
        worker_id="process-worker",
        lease_for=timedelta(minutes=5),
    )
    assert await coordinator.run(budget=1) == 0
    assert handler.executed == 0
    async with database.transaction() as session:
        rollout = await store.get_rollout(session, rollout_id="denied-rollout")
        assert rollout.status == RolloutStatus.REVIEW_REQUIRED
        _initial, events = await store.replay(session, rollout_id="denied-rollout")
        assert events == ()
        decision = await session.scalar(select(AmberAdmissionDecisionRow))
        binding = await coordinator.observations.inspect_decision_binding(
            session, decision_id=decision.decision_id
        )
        assert binding.disposition.value == decision.disposition


async def test_planner_mutation_does_not_change_executor_input_or_broker_state(database, pprl_now):
    store, amber = await _bootstrap_rollout(database, rollout_id="mutation-rollout")

    class MutatingPlanner(OneActionHandler):
        def propose(self, observation):
            assert set(observation.model_dump()) == {"schema_version", "state"}
            proposal = super().propose(observation)
            observation.state.extension_state["unreviewed"] = {"marker": "SYNTHETIC_MUTATION"}
            return proposal

    class InspectingExecutor(OneActionHandler):
        async def execute(self, claimed, proposal, admission, observation):
            assert claimed.state.payload.extension_state == {}
            assert observation.state.extension_state == {}
            return await super().execute(claimed, proposal, admission, observation)

    planner, executor = MutatingPlanner(), InspectingExecutor()
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=planner,
        executor=executor,
        worker_id="process-worker",
    )
    assert await coordinator.run() == 1
    assert planner.proposed == 1 and planner.executed == 0
    assert executor.proposed == 0 and executor.executed == 1
    async with database.transaction() as session:
        rollout = await store.get_rollout(session, rollout_id="mutation-rollout")
        source = await store.get_state(session, state_id=rollout.current_state_id)
        assert source.payload.extension_state == {}
        row = await session.scalar(select(ProcessObservationRow))
        receipt = await coordinator.observations.inspect_receipt(
            session, observation_id=row.observation_id
        )
        assert "SYNTHETIC_MUTATION" not in receipt.observation_json


async def test_failed_planning_releases_lease_and_retains_observation(database, pprl_now):
    store, amber = await _bootstrap_rollout(database, rollout_id="planner-failure")

    class FailingPlanner(OneActionHandler):
        def propose(self, observation):
            raise ValueError("synthetic planning failure")

    executor = OneActionHandler()
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=FailingPlanner(),
        executor=executor,
        worker_id="process-worker",
    )
    with pytest.raises(ValueError, match="synthetic planning failure"):
        await coordinator.run()
    assert executor.executed == 0
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, "planner-failure")
        assert row.lease_token is None and row.status == RolloutStatus.FAILED.value
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 0
        )


async def test_observation_failure_rolls_back_claim_before_planning(database, pprl_now):
    store, amber = await _bootstrap_rollout(database, rollout_id="observation-failure")
    policy = ProcessObservationStore(store).policy.model_copy(update={"maximum_bytes": 10})
    handler = OneActionHandler()
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=handler,
        executor=handler,
        worker_id="process-worker",
        observations=ProcessObservationStore(store, policy=policy),
    )
    with pytest.raises(ProcessObservationDeniedError):
        await coordinator.run()
    assert handler.proposed == 0 and handler.executed == 0
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, "observation-failure")
        assert row.lease_token is None and row.status == RolloutStatus.ACTIVE.value
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 0


@pytest.mark.parametrize("target_class", ["scientific_math", "undeclared-target"])
async def test_binding_failure_preserves_decision_without_executing(
    database, pprl_now, monkeypatch, target_class
):
    store, amber = await _bootstrap_rollout(database, rollout_id="binding-failure")
    handler = OneActionHandler(target_class=target_class)
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        planner=handler,
        executor=handler,
        worker_id="process-worker",
    )

    async def fail_binding(*_args, **_kwargs):
        raise ProcessObservationDeniedError("synthetic binding failure")

    monkeypatch.setattr(coordinator.observations, "bind_decision", fail_binding)
    with pytest.raises(ProcessObservationDeniedError, match="synthetic binding failure"):
        await coordinator.run()
    assert handler.proposed == 1 and handler.executed == 0
    async with database.transaction() as session:
        row = await session.get(ProcessRolloutRow, "binding-failure")
        assert row.lease_token is None and row.sequence == 0
        assert (
            await session.scalar(select(func.count()).select_from(AmberAdmissionDecisionRow)) == 1
        )
        assert await session.scalar(select(func.count()).select_from(ProcessObservationRow)) == 1
        assert (
            await session.scalar(select(func.count()).select_from(ProcessObservationDecisionRow))
            == 0
        )


async def _bootstrap_rollout(database, *, rollout_id: str) -> tuple[ProcessStore, AmberStore]:
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    store = ProcessStore(amber)
    model = worker_model()
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        program_digest = await registry.register_program(session, program(distribution_digest))
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=51 if rollout_id == "coordinator-rollout" else 52,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            model=model,
        )
        authorization_digest = await amber.prepare(
            session, envelope=authorization, actor_id="preparer"
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.AUTHORIZED,
            actor_id="reviewer-a",
            reason="test authorization approved",
            evidence_refs=("review:test",),
            occurred_at=NOW + timedelta(minutes=1),
        )
        await amber.transition(
            session,
            authorization_digest=authorization_digest,
            to_status=AmberStatus.ACTIVE,
            actor_id="operator",
            reason="test authorization activated",
            occurred_at=NOW + timedelta(minutes=2),
        )
        await fund_resources(session, amber, authorization)
        process_execution = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization_digest,
            model=model,
            execution_id=f"execution-{rollout_id}",
        )
        execution_digest = await store.register_execution(session, process_execution)
        await store.create_rollout(
            session,
            execution_digest=execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(objective="complete one governed action"),
            rollout_id=rollout_id,
            created_at=NOW + timedelta(minutes=3),
        )
    return store, amber
