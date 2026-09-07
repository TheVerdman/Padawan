from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from padawan.models.contracts import CorpusPool, RunState
from padawan.orchestration.state_machine import RunStore
from padawan.state.store import StateStore
from tests.support.postgres import (
    _postgres_inventory,
)
from tests.support.postgres import (
    postgres_database as postgres_database,
)

pytestmark = pytest.mark.postgres


async def test_postgres_matched_lease_skip_locked(postgres_database) -> None:
    registry = await _postgres_inventory(postgres_database)
    start = asyncio.Event()

    async def worker(owner: str):
        async with postgres_database.transaction() as session:
            await start.wait()
            leases = await registry.lease_matched_group(
                session,
                owner=owner,
                pool=CorpusPool.CURRICULUM,
                count=3,
                lease_for=timedelta(minutes=5),
            )
            await asyncio.sleep(0.05)
            return leases

    tasks = [asyncio.create_task(worker("one")), asyncio.create_task(worker("two"))]
    start.set()
    outcomes = await asyncio.gather(*tasks)
    assert sorted(len(value) for value in outcomes) == [0, 3]


async def test_postgres_concurrent_fork_is_symmetric(postgres_database) -> None:
    states = StateStore()
    async with postgres_database.transaction() as session:
        parent = await states.create_student(
            session,
            student_id="student",
            checkpoint_id="checkpoint",
            runtime_id="runtime",
        )
    start = asyncio.Event()

    async def create_fork():
        async with postgres_database.transaction() as session:
            await start.wait()
            return await states.fork(
                session,
                parent_state_id=parent.state_id,
                experiment_id="experiment",
                intervention={"treatment": "teacher", "control": "none"},
                fork_id="fork-shared",
            )

    tasks = [asyncio.create_task(create_fork()), asyncio.create_task(create_fork())]
    start.set()
    left, right = await asyncio.gather(*tasks)
    assert left == right
    assert left.treatment_state_id != left.control_state_id


async def test_postgres_only_one_worker_claims_run(postgres_database) -> None:
    runs = RunStore()
    async with postgres_database.transaction() as session:
        run_id = await runs.create(session, payload={})
    start = asyncio.Event()

    async def claim(worker: str):
        async with postgres_database.transaction() as session:
            await start.wait()
            result = await runs.claim_next(
                session, worker_id=worker, lease_for=timedelta(minutes=5)
            )
            await asyncio.sleep(0.05)
            return result

    tasks = [asyncio.create_task(claim("one")), asyncio.create_task(claim("two"))]
    start.set()
    outcomes = await asyncio.gather(*tasks)
    claimed = [value for value in outcomes if value is not None]
    assert len(claimed) == 1
    assert claimed[0].run_id == run_id
    assert claimed[0].state == RunState.CREATED


async def test_postgres_alembic_upgrade_has_no_schema_drift(postgres_database, monkeypatch) -> None:
    await postgres_database.drop_schema()
    monkeypatch.setenv("PADAWAN_DATABASE_URL", postgres_database.url)
    configuration = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    configuration.set_main_option("sqlalchemy.url", postgres_database.url)

    await asyncio.to_thread(command.upgrade, configuration, "head")
    await asyncio.to_thread(command.check, configuration)
