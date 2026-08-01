from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from padawan.models.tables import Base


def _config(database_path: Path, monkeypatch) -> Config:
    url = f"sqlite+aiosqlite:///{database_path}"
    monkeypatch.setenv("PADAWAN_DATABASE_URL", url)
    configuration = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    configuration.set_main_option("sqlalchemy.url", url)
    return configuration


def _tables(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        }


def test_empty_database_upgrade_and_schema_match(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "migration.sqlite3"
    configuration = _config(database_path, monkeypatch)
    command.upgrade(configuration, "head")
    expected = set(Base.metadata.tables)
    assert expected <= _tables(database_path)
    command.check(configuration)


def test_upgrade_from_every_revision(tmp_path, monkeypatch) -> None:
    database_path = tmp_path / "all-revisions.sqlite3"
    configuration = _config(database_path, monkeypatch)
    scripts = ScriptDirectory.from_config(configuration)
    revisions = list(scripts.walk_revisions(base="base", head="heads"))
    assert revisions
    command.upgrade(configuration, "head")
    for revision in reversed(revisions):
        base = revision.down_revision or "base"
        if isinstance(base, tuple):
            raise AssertionError("branch merges require an explicit migration test")
        command.downgrade(configuration, base)
        command.upgrade(configuration, revision.revision)
    command.upgrade(configuration, "head")
    assert set(Base.metadata.tables) <= _tables(database_path)
