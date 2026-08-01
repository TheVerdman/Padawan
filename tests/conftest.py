from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from padawan.models.database import Database


@pytest_asyncio.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    instance = Database.sqlite(tmp_path / "padawan-test.sqlite3")
    await instance.create_schema()
    try:
        yield instance
    finally:
        await instance.close()
