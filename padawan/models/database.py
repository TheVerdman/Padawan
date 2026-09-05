from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from padawan.models.tables import Base


class Database:
    """Owns one async engine and creates an independent session per task."""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        kwargs: dict[str, object] = {"echo": echo, "pool_pre_ping": True}
        if url == "sqlite+aiosqlite:///:memory:":
            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"check_same_thread": False}
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, **kwargs)
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        if url.startswith("sqlite"):
            event.listen(self.engine.sync_engine, "connect", _configure_sqlite)
            event.listen(self.engine.sync_engine, "begin", _begin_sqlite)

    @classmethod
    def sqlite(cls, path: Path | str) -> Database:
        return cls(f"sqlite+aiosqlite:///{Path(path).resolve()}")

    @property
    def dialect_name(self) -> str:
        return self.engine.dialect.name

    async def create_schema(self) -> None:
        """Create tables for tests and ephemeral local runs; production uses Alembic."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_schema(self) -> None:
        """Drop application and migration metadata tables for an actually empty database."""

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session, session.begin():
            yield session

    async def close(self) -> None:
        await self.engine.dispose()


def _configure_sqlite(dbapi_connection: Any, _connection_record: object) -> None:
    # SQLAlchemy must own BEGIN, including before a read or SAVEPOINT. The
    # sqlite3 legacy mode otherwise releases a first savepoint as its own commit,
    # allowing nested writes to survive an outer transaction rollback.
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=FULL")
    cursor.close()


def _begin_sqlite(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")
