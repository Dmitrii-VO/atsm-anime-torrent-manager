"""Подключение к SQLite и миграции схемы.

Соединение одно на приложение и разделяется между GUI-потоком и воркерами
проверок, поэтому check_same_thread=False, а все записи сериализуются
внутренним замком. Объём данных небольшой, конкуренция за запись низкая —
пул соединений тут был бы преждевременным усложнением.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Callable, Iterator

from loguru import logger

SCHEMA_VERSION = 4


def _load_initial_schema() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


# Сборники вида «Серии 27-28»: начало диапазона в episode, конец здесь.
_MIGRATION_002 = "ALTER TABLE release ADD COLUMN episode_end INTEGER;"

# Справочные данные из Shikimori/AniList. Отдельная таблица, а не колонки в
# anime: это внешние данные, они обновляются независимо и могут отсутствовать.
_MIGRATION_003 = """
CREATE TABLE IF NOT EXISTS anime_metadata (
    anime_id            INTEGER PRIMARY KEY REFERENCES anime(id) ON DELETE CASCADE,
    shikimori_id        TEXT,
    anilist_id          TEXT,
    title_ru            TEXT,
    title_romaji        TEXT,
    title_native        TEXT,
    kind                TEXT,
    status              TEXT,
    score               REAL,
    episodes_total      INTEGER,   -- нумерация справочника, НЕ совпадает с трекером
    episodes_aired      INTEGER,
    next_episode_number INTEGER,
    next_episode_at     TEXT,
    poster_url          TEXT,
    genres              TEXT,      -- через запятую
    description         TEXT,
    site_url            TEXT,
    updated_at          TEXT NOT NULL
);
"""

# Франшиза из Shikimori: связывает «Блич» и «Блич: Тысячелетняя кровавая
# война» в одну группу. Ключ надёжнее сравнения названий.
_MIGRATION_004 = "ALTER TABLE anime_metadata ADD COLUMN franchise TEXT;"

# Миграции применяются по порядку; версия N приводит схему к состоянию N.
# Новая версия — новая запись здесь, ничего существующего не меняем.
MIGRATIONS: dict[int, Callable[[], str]] = {
    1: _load_initial_schema,
    2: lambda: _MIGRATION_002,
    3: lambda: _MIGRATION_003,
    4: lambda: _MIGRATION_004,
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    # --- жизненный цикл -------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        self._conn = conn
        logger.debug("База данных открыта: {}", self.path)
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                logger.debug("База данных закрыта")

    def __enter__(self) -> "Database":
        self.connect()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # --- доступ ---------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            return self.connect()
        return self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self.conn
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    # --- миграции -------------------------------------------------------

    def current_version(self) -> int:
        row = self.query_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        )
        if row is None:
            return 0
        version_row = self.query_one("SELECT version FROM schema_version")
        return int(version_row["version"]) if version_row else 0

    def migrate(self) -> int:
        """Приводит схему к SCHEMA_VERSION. Возвращает итоговую версию."""
        self.connect()
        version = self.current_version()

        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"База создана более новой версией приложения "
                f"(схема {version}, поддерживается {SCHEMA_VERSION})"
            )
        if version == SCHEMA_VERSION:
            logger.debug("Схема БД актуальна (версия {})", version)
            return version

        statements = [
            "BEGIN IMMEDIATE;",
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);",
        ]
        for target in range(version + 1, SCHEMA_VERSION + 1):
            logger.info("Применяется миграция БД до версии {}", target)
            statements.append(MIGRATIONS[target]())
        statements.extend(
            (
                "DELETE FROM schema_version;",
                f"INSERT INTO schema_version (version) VALUES ({SCHEMA_VERSION});",
                "COMMIT;",
            )
        )

        # executescript сам завершает внешнюю транзакцию. Поэтому BEGIN/COMMIT
        # входят в тот же скрипт, что DDL и запись версии схемы.
        with self._lock:
            conn = self.conn
            try:
                conn.executescript("\n".join(statements))
            except Exception:
                conn.rollback()
                raise

        logger.info("Схема БД: версия {} → {}", version, SCHEMA_VERSION)
        return SCHEMA_VERSION

    def table_names(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in rows]
