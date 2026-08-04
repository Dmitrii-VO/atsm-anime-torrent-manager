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
from typing import Iterator

from loguru import logger

SCHEMA_VERSION = 2


def _load_initial_schema() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


# Сборники вида «Серии 27-28»: начало диапазона в episode, конец здесь.
_MIGRATION_002 = "ALTER TABLE release ADD COLUMN episode_end INTEGER;"

# Миграции применяются по порядку; версия N приводит схему к состоянию N.
# Новая версия — новая запись здесь, ничего существующего не меняем.
MIGRATIONS: dict[int, callable] = {
    1: _load_initial_schema,
    2: lambda: _MIGRATION_002,
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

        with self.transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            for target in range(version + 1, SCHEMA_VERSION + 1):
                logger.info("Применяется миграция БД до версии {}", target)
                conn.executescript(MIGRATIONS[target]())
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

        logger.info("Схема БД: версия {} → {}", version, SCHEMA_VERSION)
        return SCHEMA_VERSION

    def table_names(self) -> list[str]:
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        return [row["name"] for row in rows]
