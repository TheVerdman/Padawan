from __future__ import annotations

from datetime import timedelta

import pytest

from padawan.governance.amber import AmberAdmissionDecision, AmberStatus
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
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
from padawan.pprl.store import ClaimedProcessRollout, ProcessStore
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    component,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


class OneActionHandler:
    def __init__(self, *, target_class: str = "scientific_math") -> None:
        self.target_class = target_class
        self.executed = 0

    def propose(self, claimed: ClaimedProcessRollout) -> ProcessActionProposal:
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
    ) -> ProcessActionResult:
        assert admission.rollout_id == claimed.rollout.rollout_id
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
            event_payload={"expression": "21 * 2", "result": 42},
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
        handler=handler,
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


@pytest.mark.asyncio
async def test_coordinator_never_executes_denied_boundary_action(database, pprl_now) -> None:
    store, amber = await _bootstrap_rollout(database, rollout_id="denied-rollout")
    handler = OneActionHandler(target_class="undeclared-target")
    coordinator = ProcessCoordinator(
        database=database,
        store=store,
        amber=amber,
        handler=handler,
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
