from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta, tzinfo

import pytest
import pytest_asyncio

from padawan.models.database import Database


@pytest.fixture
def pprl_now(monkeypatch: pytest.MonkeyPatch) -> Callable[[], datetime]:
    """Run clock-dependent fixtures inside their fixed, one-day Amber authority."""
    from padawan.governance import amber_store
    from padawan.orchestration import external_calls
    from padawan.pprl import (
        abandonment,
        containers,
        coordinator,
        generation,
        recovered_evidence,
        recovery,
        resources,
        store,
        worker_broker,
        worker_identities,
    )
    from tests.pprl_helpers import NOW

    instant = NOW + timedelta(minutes=10)

    def clock() -> datetime:
        nonlocal instant
        instant += timedelta(microseconds=1)
        return instant

    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            value = clock()
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    for module in (
        abandonment,
        amber_store,
        external_calls,
        containers,
        coordinator,
        generation,
        recovered_evidence,
        recovery,
        resources,
        store,
        worker_broker,
        worker_identities,
    ):
        monkeypatch.setattr(module, "datetime", FixtureDatetime)
    return clock


@pytest_asyncio.fixture
async def database(tmp_path) -> AsyncIterator[Database]:
    instance = Database.sqlite(tmp_path / "padawan-test.sqlite3")
    await instance.create_schema()
    try:
        yield instance
    finally:
        await instance.close()
