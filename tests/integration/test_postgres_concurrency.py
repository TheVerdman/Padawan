from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.models.contracts import CorpusPool, RunState
from padawan.models.database import Database
from padawan.orchestration.state_machine import RunStore
from padawan.state.store import StateStore

pytestmark = pytest.mark.postgres


@pytest_asyncio.fixture
async def postgres_database() -> AsyncIterator[Database]:
    url = os.environ.get("PADAWAN_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("PADAWAN_TEST_POSTGRES_URL is not configured")
    parsed = make_url(url)
    if "test" not in (parsed.database or "").lower():
        pytest.skip("PADAWAN_TEST_POSTGRES_URL must name a dedicated test database")
    database = Database(url)
    await database.drop_schema()
    await database.create_schema()
    try:
        yield database
    finally:
        await database.drop_schema()
        await database.close()


async def _postgres_inventory(database: Database) -> CorpusRegistry:
    generator = AlgebraCorpusGenerator()
    registry = CorpusRegistry()
    items = generator.generate(
        pool=CorpusPool.CURRICULUM,
        seed=900,
        groups_per_family=1,
        siblings_per_group=3,
        families=(AlgebraFamily.DISTRIBUTION_SIGN,),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with database.transaction() as session:
        for competency in generator.competencies(created_at=datetime(2026, 1, 1, tzinfo=UTC)):
            await registry.register_competency(session, competency)
        await registry.register_items(session, items)
    return registry


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
