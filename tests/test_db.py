from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from atsm.db import SCHEMA_VERSION, Database

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
