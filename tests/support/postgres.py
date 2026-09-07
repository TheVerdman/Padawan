"""Shared postgres fixture setup."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.engine import make_url

from padawan.corpus.algebra import AlgebraCorpusGenerator, AlgebraFamily
from padawan.corpus.registry import CorpusRegistry
from padawan.models.contracts import CorpusPool
from padawan.models.database import Database


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
