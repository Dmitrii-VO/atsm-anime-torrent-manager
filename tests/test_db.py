from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atsm.db import SCHEMA_VERSION, Database
from atsm.db import database as database_module

EXPECTED_TABLES = {
    "anime",
    "release",
    "history",
    "source_status",
    "rules",
    "settings",
    "schema_version",
}


def test_migrate_creates_all_tables(db: Database) -> None:
    assert EXPECTED_TABLES.issubset(set(db.table_names()))
    assert db.current_version() == SCHEMA_VERSION


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "atsm.db"
    Database(path).migrate()

    database = Database(path)
    assert database.migrate() == SCHEMA_VERSION
    assert database.query_one("SELECT COUNT(*) AS n FROM schema_version")["n"] == 1
    database.close()


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "atsm.db"
    database = Database(path)
    database.migrate()
    with database.transaction() as conn:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 5,))
    database.close()

    with pytest.raises(RuntimeError, match="более новой версией"):
        Database(path).migrate()


def test_foreign_keys_cascade(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO anime (title, source, url, slug, created_at) VALUES (?,?,?,?,?)",
            ("Тест", "astar", "https://v30.astar.bz/1-test.html", "1-test", "2026-08-04"),
        )
        anime_id = conn.execute("SELECT id FROM anime").fetchone()["id"]
        conn.execute(
            "INSERT INTO release (anime_id, external_id, episode_raw, first_seen_at)"
            " VALUES (?,?,?,?)",
            (anime_id, "46219", "Серия 235 (844.05 Mb)", "2026-08-04"),
        )

    with db.transaction() as conn:
        conn.execute("DELETE FROM anime WHERE id = ?", (anime_id,))

    assert db.query_one("SELECT COUNT(*) AS n FROM release")["n"] == 0


def test_release_dedup_constraint(db: Database) -> None:
    """Ключ дедупликации — (anime_id, external_id), ТЗ §18."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO anime (title, source, url, slug, created_at) VALUES (?,?,?,?,?)",
            ("Тест", "astar", "https://v30.astar.bz/1-test.html", "1-test", "2026-08-04"),
        )
        anime_id = conn.execute("SELECT id FROM anime").fetchone()["id"]
        conn.execute(
            "INSERT INTO release (anime_id, external_id, episode_raw, first_seen_at)"
            " VALUES (?,?,?,?)",
            (anime_id, "46219", "Серия 235", "2026-08-04"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO release (anime_id, external_id, episode_raw, first_seen_at)"
                " VALUES (?,?,?,?)",
                (anime_id, "46219", "Серия 235 (повтор)", "2026-08-05"),
            )


def test_transaction_rolls_back(db: Database) -> None:
    with pytest.raises(ValueError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO anime (title, source, url, slug, created_at) VALUES (?,?,?,?,?)",
                ("Откат", "astar", "https://v30.astar.bz/2-x.html", "2-x", "2026-08-04"),
            )
            raise ValueError("сбой посреди транзакции")

    assert db.query_one("SELECT COUNT(*) AS n FROM anime")["n"] == 0


def test_failed_migration_rolls_back_and_can_be_retried(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "broken.db"
    migrations = {
        1: lambda: "CREATE TABLE first_step (id INTEGER);",
        2: lambda: "CREATE TABLE partial_step (id INTEGER); SELECT * FROM missing_table;",
    }
    monkeypatch.setattr(database_module, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(database_module, "MIGRATIONS", migrations)

    database = Database(path)
    with pytest.raises(sqlite3.OperationalError):
        database.migrate()

    assert "first_step" not in database.table_names()
    assert "partial_step" not in database.table_names()
    assert database.current_version() == 0

    migrations[2] = lambda: "CREATE TABLE second_step (id INTEGER);"
    assert database.migrate() == 2
    assert {"first_step", "second_step"}.issubset(database.table_names())
    database.close()
