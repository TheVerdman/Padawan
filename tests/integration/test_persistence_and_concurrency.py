from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.models.contracts import (
    CorpusPool,
    ExposureRecord,
    ExposureType,
    ItemStatus,
)
from padawan.models.tables import (
    CorpusItemRow,
    ExposureRow,
    StateForkRow,
    StudentStateRow,
)
from padawan.state.store import StateInvariantError, StateStore


async def _register_items(database, *, count: int = 3):
    generator = AlgebraCorpusGenerator()
    registry = CorpusRegistry()
    records = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=120,
        groups_per_family=1,
        siblings_per_group=max(2, count),
        families=(AlgebraFamily.LINEAR_BOTH_SIDES,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )[:count]
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=datetime(2026, 1, 1, tzinfo=UTC)):
            await registry.register_competency(session, competency)
        await registry.register_items(session, records)
    return registry, records


async def test_two_workers_cannot_lease_same_item(database) -> None:
    registry, records = await _register_items(database, count=1)
    start = asyncio.Event()

    async def worker(owner: str):
        async with database.transaction() as session:
            await start.wait()
            lease = await registry.lease_item(
                session,
                owner=owner,
                pool=CorpusPool.CURRICULUM,
                lease_for=timedelta(minutes=1),
            )
            await asyncio.sleep(0.02)
            return lease

    tasks = [asyncio.create_task(worker("one")), asyncio.create_task(worker("two"))]
    start.set()
    leases = await asyncio.gather(*tasks)
    assert sum(lease is not None for lease in leases) == 1
    assert next(lease.item.item_id for lease in leases if lease) == records[0].item_id


async def test_expired_lease_recovers_and_retired_item_cannot_be_leased(database) -> None:
    registry, _ = await _register_items(database, count=1)
    now = datetime.now(UTC)
    async with database.transaction() as session:
        first = await registry.lease_item(
            session,
            owner="one",
            pool=CorpusPool.CURRICULUM,
            lease_for=timedelta(seconds=1),
            now=now,
        )
        assert first is not None
    async with database.transaction() as session:
        recovered = await registry.recover_expired_leases(session, now=now + timedelta(seconds=2))
        second = await registry.lease_item(
            session,
            owner="two",
            pool=CorpusPool.CURRICULUM,
            lease_for=timedelta(minutes=1),
            now=now + timedelta(seconds=2),
        )
        assert recovered == 1
        assert second is not None
        row = await session.get(CorpusItemRow, second.item.item_id)
        assert row is not None
        row.status = ItemStatus.RETIRED.value
        row.lease_owner = None
        row.lease_token = None
        row.lease_expires_at = None
    async with database.transaction() as session:
        assert (
            await registry.lease_item(
                session,
                owner="three",
                pool=CorpusPool.CURRICULUM,
                lease_for=timedelta(minutes=1),
            )
            is None
        )


async def test_exposure_and_retirement_rollback_together(database) -> None:
    registry, records = await _register_items(database, count=1)
    async with database.transaction() as session:
        lease = await registry.lease_item(
            session,
            owner="worker",
            pool=CorpusPool.CURRICULUM,
            lease_for=timedelta(minutes=5),
        )
        assert lease is not None
    exposure = ExposureRecord(
        exposure_id="exposure-atomic",
        student_id="student",
        checkpoint_id="checkpoint",
        state_id="state",
        item_id=records[0].item_id,
        template_family_id=records[0].template_family_id,
        instance_group_id=records[0].instance_group_id,
        exposure_type=ExposureType.CRITIQUE,
        prompt_exposed=True,
        answer_exposed=False,
        critique_exposed=True,
        repair_exposed=False,
        metadata_exposed=False,
        episode_id="episode",
        created_at=datetime.now(UTC),
    )
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await registry.record_exposure_and_retire(
                session,
                exposure=exposure,
                lease_token=lease.token,
                lease_owner="worker",
                retirement_reason="answer feedback",
            )
            raise RuntimeError("simulate crash before commit")
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ExposureRow)) == 0
        row = await session.get(CorpusItemRow, records[0].item_id)
        assert row is not None and row.status == ItemStatus.LEASED.value
        await registry.record_exposure_and_retire(
            session,
            exposure=exposure,
            lease_token=lease.token,
            lease_owner="worker",
            retirement_reason="answer feedback",
        )
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ExposureRow)) == 1
        row = await session.get(CorpusItemRow, records[0].item_id)
        assert row is not None and row.status == ItemStatus.RETIRED.value


async def test_fork_is_atomic_immutable_and_branch_isolated(database) -> None:
    states = StateStore()
    async with database.transaction() as session:
        parent = await states.create_student(
            session,
            student_id="student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
            initial_working_state={"knowledge": ["prior"]},
        )
    with pytest.raises(RuntimeError):
        async with database.transaction() as session:
            await states.fork(
                session,
                parent_state_id=parent.state_id,
                experiment_id="experiment-rollback",
                intervention={"treatment": "lesson", "control": "none"},
            )
            raise RuntimeError("crash during fork transaction")
    async with database.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(StateForkRow)) == 0
        assert await session.scalar(select(func.count()).select_from(StudentStateRow)) == 1
        fork = await states.fork(
            session,
            parent_state_id=parent.state_id,
            experiment_id="experiment",
            intervention={"treatment": "lesson", "control": "none"},
        )
        treatment = await states.get(session, state_id=fork.treatment_state_id)
        control = await states.get(session, state_id=fork.control_state_id)
        assert treatment.compacted_working_state == control.compacted_working_state
        assert treatment.lesson_memory_refs == control.lesson_memory_refs
        with pytest.raises(StateInvariantError):
            states.assert_branch(treatment, control.branch_id)
    with pytest.raises(ValueError, match="immutable"):
        async with database.transaction() as session:
            row = await session.get(StudentStateRow, parent.state_id)
            assert row is not None
            row.compacted_working_state = {"changed": True}
            await session.flush()
