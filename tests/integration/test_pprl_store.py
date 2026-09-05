from __future__ import annotations

from datetime import timedelta

import pytest

from padawan.governance.amber import (
    AmberActionRequest,
    AmberAdmissionDisposition,
    AmberStatus,
)
from padawan.governance.amber_store import AmberStore
from padawan.models.hashing import sha256_digest
from padawan.pprl.contracts import (
    ProcessEventKind,
    ProjectBudgetUsage,
    ProjectSplit,
    ProjectStatePayload,
)
from padawan.pprl.distributions import ProcessDistributionRegistry
from padawan.pprl.store import ProcessForkChildPlan, ProcessInvariantError, ProcessStore
from tests.pprl_helpers import (
    NOW,
    DeterministicProjectGenerator,
    distribution,
    envelope,
    execution,
    program,
    worker_model,
)


@pytest.mark.asyncio
async def test_repeated_distribution_plan_requires_instances_and_replicates(database) -> None:
    registry = ProcessDistributionRegistry()
    async with database.transaction() as session:
        manifest = distribution()
        distribution_digest = await registry.register_distribution(session, manifest)
        first = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=11,
            generator=DeterministicProjectGenerator(),
        )
        second = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=12,
            generator=DeterministicProjectGenerator(),
        )
        plan = await registry.plan_replications(
            session,
            distribution_digest=distribution_digest,
            instance_ids=(first.instance_id, second.instance_id),
        )
        assert len(plan) == 4
        assert {item.replication_index for item in plan} == {0, 1}
        with pytest.raises(ValueError, match="too few unique"):
            await registry.plan_replications(
                session,
                distribution_digest=distribution_digest,
                instance_ids=(first.instance_id,),
            )


@pytest.mark.asyncio
async def test_process_event_requires_amber_decision_and_replays(database) -> None:
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    store = ProcessStore(amber)
    model = worker_model()
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=21,
            generator=DeterministicProjectGenerator(),
        )
        authorization = envelope(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            model=model,
        )
        authorization_digest = await amber.prepare(
            session,
            envelope=authorization,
            actor_id="preparer",
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
            reason="test run activated",
            occurred_at=NOW + timedelta(minutes=2),
        )
        process_execution = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization_digest,
            model=model,
        )
        execution_digest = await store.register_execution(session, process_execution)
        rollout = await store.create_rollout(
            session,
            execution_digest=execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(objective="solve the project"),
            rollout_id="rollout-main",
            created_at=NOW + timedelta(minutes=3),
        )
        assert rollout.sequence == 0

    async with database.transaction() as session:
        claimed = await store.claim_next(
            session,
            worker_id="worker-a",
            lease_for=timedelta(minutes=5),
            now=NOW + timedelta(minutes=4),
        )
        assert claimed is not None
        request = AmberActionRequest(
            authorization_digest=authorization_digest,
            rollout_id=claimed.rollout.rollout_id,
            rollout_sequence=claimed.rollout.sequence,
            state_digest=claimed.state.state_digest,
            lease_token_digest=sha256_digest(claimed.lease_token),
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            persistence_mode=process_program.persistence_mode,
            event_kind=ProcessEventKind.WORK_PLANNED,
            role_id="researcher",
            worker_model_digest=sha256_digest(model),
            target_class="scientific_math",
            environment_fingerprint=instance.environment_fingerprint,
            projected_usage=ProjectBudgetUsage(actions=1),
            requested_at=NOW + timedelta(minutes=4),
        )
        decision = await amber.admit(
            session,
            request=request,
            active_workers=0,
            decision_id="decision-work-plan",
        )
        assert decision.disposition == AmberAdmissionDisposition.ADMITTED
        event, state = await store.append_event(
            session,
            rollout_id=claimed.rollout.rollout_id,
            lease_token=claimed.lease_token,
            amber_decision_id=decision.decision_id,
            kind=ProcessEventKind.WORK_PLANNED,
            actor_id="worker-a",
            payload={"plan": ["calculate", "verify"]},
            resulting_state=ProjectStatePayload(
                objective="solve the project",
                plan=("calculate", "verify"),
                budget_usage=ProjectBudgetUsage(actions=1),
            ),
            event_id="event-work-plan",
            resulting_state_id="state-after-plan",
            occurred_at=NOW + timedelta(minutes=5),
        )
        assert event.amber_decision_id == decision.decision_id
        assert state.parent_state_id == claimed.state.state_id

    async with database.transaction() as session:
        initial, events = await store.replay(session, rollout_id="rollout-main")
        assert initial.sequence == 0
        assert [event.event_id for event in events] == ["event-work-plan"]
        with pytest.raises(PermissionError, match="no Amber admission"):
            claimed = await store.claim_next(
                session,
                worker_id="worker-b",
                lease_for=timedelta(minutes=5),
                now=NOW + timedelta(minutes=6),
            )
            assert claimed is not None
            await store.append_event(
                session,
                rollout_id="rollout-main",
                lease_token=claimed.lease_token,
                amber_decision_id="missing-decision",
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="worker-b",
                payload={},
                resulting_state=claimed.state.payload,
                occurred_at=NOW + timedelta(minutes=6),
            )
        with pytest.raises(PermissionError, match="different rollout lease"):
            await store.append_event(
                session,
                rollout_id="rollout-main",
                lease_token=claimed.lease_token,
                amber_decision_id="decision-work-plan",
                kind=ProcessEventKind.WORK_PLANNED,
                actor_id="worker-b",
                payload={},
                resulting_state=claimed.state.payload,
                occurred_at=NOW + timedelta(minutes=6),
            )
        expiry_decision = await amber.admit(
            session,
            request=AmberActionRequest(
                authorization_digest=authorization_digest,
                rollout_id=claimed.rollout.rollout_id,
                rollout_sequence=claimed.rollout.sequence,
                state_digest=claimed.state.state_digest,
                lease_token_digest=sha256_digest(claimed.lease_token),
                program_digest=program_digest,
                distribution_digest=distribution_digest,
                split=ProjectSplit.TRAIN,
                persistence_mode=process_program.persistence_mode,
                event_kind=ProcessEventKind.CLAIM_UPDATED,
                role_id="researcher",
                worker_model_digest=sha256_digest(model),
                target_class="scientific_math",
                environment_fingerprint=instance.environment_fingerprint,
                projected_usage=ProjectBudgetUsage(actions=2),
                requested_at=NOW + timedelta(minutes=6),
            ),
            active_workers=0,
            decision_id="decision-expiring-lease",
        )
        with pytest.raises(ProcessInvariantError, match="lease expired"):
            await store.append_event(
                session,
                rollout_id="rollout-main",
                lease_token=claimed.lease_token,
                amber_decision_id=expiry_decision.decision_id,
                kind=ProcessEventKind.CLAIM_UPDATED,
                actor_id="worker-b",
                payload={},
                resulting_state=claimed.state.payload.model_copy(
                    update={"budget_usage": ProjectBudgetUsage(actions=2)}
                ),
                occurred_at=NOW + timedelta(minutes=11),
            )

    async with database.transaction() as session:
        assert (
            await store.claim_next(
                session,
                worker_id="worker-after-expiry",
                lease_for=timedelta(minutes=5),
                now=NOW + timedelta(days=2),
            )
            is None
        )


@pytest.mark.asyncio
async def test_process_fork_inherits_identical_project_payload(database) -> None:
    registry = ProcessDistributionRegistry()
    amber = AmberStore()
    store = ProcessStore(amber)
    model = worker_model()
    async with database.transaction() as session:
        distribution_digest = await registry.register_distribution(session, distribution())
        process_program = program(distribution_digest)
        program_digest = await registry.register_program(session, process_program)
        instance = await registry.sample(
            session,
            distribution_digest=distribution_digest,
            split=ProjectSplit.TRAIN,
            seed=31,
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
            reason="test run activated",
            occurred_at=NOW + timedelta(minutes=2),
        )
        parent_execution = execution(
            program_digest=program_digest,
            distribution_digest=distribution_digest,
            instance=instance,
            authorization_digest=authorization_digest,
            model=model,
            execution_id="fork-parent-execution",
        )
        parent_execution_digest = await store.register_execution(session, parent_execution)
        await store.create_rollout(
            session,
            execution_digest=parent_execution_digest,
            replication_index=0,
            initial_state=ProjectStatePayload(
                objective="compare continuations",
                plan=("shared prior plan",),
            ),
            rollout_id="fork-parent",
            created_at=NOW + timedelta(minutes=3),
        )

    async with database.transaction() as session:
        claimed = await store.claim_next(
            session,
            worker_id="fork-worker",
            lease_for=timedelta(minutes=5),
            now=NOW + timedelta(minutes=4),
        )
        assert claimed is not None
        decision = await amber.admit(
            session,
            request=AmberActionRequest(
                authorization_digest=authorization_digest,
                rollout_id="fork-parent",
                rollout_sequence=claimed.rollout.sequence,
                state_digest=claimed.state.state_digest,
                lease_token_digest=sha256_digest(claimed.lease_token),
                program_digest=program_digest,
                distribution_digest=distribution_digest,
                split=ProjectSplit.TRAIN,
                persistence_mode=process_program.persistence_mode,
                event_kind=ProcessEventKind.ROLLOUT_FORKED,
                role_id="researcher",
                worker_model_digest=sha256_digest(model),
                target_class="scientific_math",
                environment_fingerprint=instance.environment_fingerprint,
                projected_usage=ProjectBudgetUsage(actions=1),
                requested_at=NOW + timedelta(minutes=4),
            ),
            active_workers=0,
            decision_id="decision-fork",
        )
        fork = await store.fork_rollout(
            session,
            parent_rollout_id="fork-parent",
            lease_token=claimed.lease_token,
            amber_decision_id=decision.decision_id,
            actor_id="fork-worker",
            children=(
                ProcessForkChildPlan(
                    condition_id="control",
                    rollout_id="fork-control",
                    replication_index=0,
                    execution=execution(
                        program_digest=program_digest,
                        distribution_digest=distribution_digest,
                        instance=instance,
                        authorization_digest=authorization_digest,
                        model=model,
                        execution_id="fork-control-execution",
                        seed=101,
                    ),
                ),
                ProcessForkChildPlan(
                    condition_id="treatment",
                    rollout_id="fork-treatment",
                    replication_index=0,
                    execution=execution(
                        program_digest=program_digest,
                        distribution_digest=distribution_digest,
                        instance=instance,
                        authorization_digest=authorization_digest,
                        model=model,
                        execution_id="fork-treatment-execution",
                        seed=102,
                    ),
                ),
            ),
            intervention={"description": "planner=alternative"},
            fork_id="fork-paired-continuation",
            event_id="event-fork",
            occurred_at=NOW + timedelta(minutes=5),
        )
        assert tuple(child.condition_id for child in fork.children) == (
            "control",
            "treatment",
        )
        control = await store.get_rollout(session, rollout_id="fork-control")
        treatment = await store.get_rollout(session, rollout_id="fork-treatment")
        control_state = await store.get_state(session, state_id=control.current_state_id)
        treatment_state = await store.get_state(session, state_id=treatment.current_state_id)
        assert control_state.payload == treatment_state.payload
        assert control_state.payload.budget_usage == ProjectBudgetUsage(actions=1)
        assert control.parent_rollout_id == treatment.parent_rollout_id == "fork-parent"
