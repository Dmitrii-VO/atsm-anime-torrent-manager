"""Доступ к данным. Репозитории возвращают доменные модели, а не sqlite3.Row."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from ..core.models import (
    Anime,
    HistoryAction,
    HistoryEntry,
    Release,
    ReleaseState,
    SourceState,
)
from ..parsers.base import ReleaseInfo
from .database import Database


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


class AnimeRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _to_model(self, row: sqlite3.Row) -> Anime:
        keys = row.keys()
        return Anime(
            id=row["id"],
            title=row["title"],
            source=row["source"],
            url=row["url"],
            slug=row["slug"],
            poster_path=row["poster_path"],
            status=row["status"],
            auto_download=bool(row["auto_download"]),
            is_favorite=bool(row["is_favorite"]),
            last_check_at=_parse_dt(row["last_check_at"]),
            last_check_ok=None if row["last_check_ok"] is None else bool(row["last_check_ok"]),
            last_error=row["last_error"],
            created_at=_parse_dt(row["created_at"]),
            new_count=row["new_count"] if "new_count" in keys else 0,
            last_episode=row["last_episode"] if "last_episode" in keys else None,
        )

    _SELECT = """
        SELECT a.*,
               (SELECT COUNT(*) FROM release r
                 WHERE r.anime_id = a.id AND r.is_seen = 0 AND r.state = 'new') AS new_count,
               (SELECT MAX(r.episode) FROM release r WHERE r.anime_id = a.id) AS last_episode
          FROM anime a
    """

    def list(self) -> list[Anime]:
        rows = self.db.query(self._SELECT + " ORDER BY a.is_favorite DESC, a.title COLLATE NOCASE")
        return [self._to_model(row) for row in rows]

    def get(self, anime_id: int) -> Anime | None:
        row = self.db.query_one(self._SELECT + " WHERE a.id = ?", (anime_id,))
        return self._to_model(row) if row else None

    def get_by_slug(self, source: str, slug: str) -> Anime | None:
        row = self.db.query_one(self._SELECT + " WHERE a.source = ? AND a.slug = ?", (source, slug))
        return self._to_model(row) if row else None

    def add(
        self,
        *,
        title: str,
        source: str,
        url: str,
        slug: str,
        poster_path: str | None = None,
        auto_download: bool = False,
    ) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO anime (title, source, url, slug, poster_path, auto_download,
                                      created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (title, source, url, slug, poster_path, int(auto_download), _now()),
            )
            return int(cursor.lastrowid)

    def delete(self, anime_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM anime WHERE id = ?", (anime_id,))

    def set_flag(self, anime_id: int, field: str, value: bool) -> None:
        if field not in {"auto_download", "is_favorite"}:
            raise ValueError(f"Недопустимое поле: {field}")
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE anime SET {field} = ? WHERE id = ?", (int(value), anime_id))

    def update_url(self, anime_id: int, url: str) -> None:
        """Источник переехал на другое зеркало — сохраняем актуальный адрес."""
        with self.db.transaction() as conn:
            conn.execute("UPDATE anime SET url = ? WHERE id = ?", (url, anime_id))

    def update_poster(self, anime_id: int, poster_path: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("UPDATE anime SET poster_path = ? WHERE id = ?", (poster_path, anime_id))

    def mark_checked(self, anime_id: int, ok: bool, error: str | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE anime SET last_check_at = ?, last_check_ok = ?, last_error = ?"
                " WHERE id = ?",
                (_now(), int(ok), error, anime_id),
            )


class ReleaseRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def _to_model(self, row: sqlite3.Row) -> Release:
        keys = row.keys()
        return Release(
            id=row["id"],
            anime_id=row["anime_id"],
            external_id=row["external_id"],
            episode=row["episode"],
            episode_end=row["episode_end"],
            episode_raw=row["episode_raw"],
            title=row["title"],
            quality=row["quality"],
            size_bytes=row["size_bytes"],
            seeders=row["seeders"],
            leechers=row["leechers"],
            downloads=row["downloads"],
            published_at=_parse_date(row["published_at"]),
            torrent_url=row["torrent_url"],
            magnet=row["magnet"],
            info_hash=row["info_hash"],
            state=row["state"],
            state_message=row["state_message"],
            is_seen=bool(row["is_seen"]),
            first_seen_at=_parse_dt(row["first_seen_at"]),
            anime_title=row["anime_title"] if "anime_title" in keys else None,
        )

    def known_external_ids(self, anime_id: int) -> set[str]:
        rows = self.db.query("SELECT external_id FROM release WHERE anime_id = ?", (anime_id,))
        return {row["external_id"] for row in rows}

    def add_many(self, anime_id: int, releases: list[ReleaseInfo], *, seen: bool) -> int:
        """Вставляет раздачи. seen=True — первичный импорт архива (ТЗ §5)."""
        if not releases:
            return 0
        now = _now()
        rows = [
            (
                anime_id,
                r.external_id,
                r.episode,
                r.episode_end,
                r.episode_raw,
                r.title,
                r.quality,
                r.size_bytes,
                r.seeders,
                r.leechers,
                r.downloads,
                r.published_at.isoformat() if r.published_at else None,
                r.torrent_url,
                r.magnet,
                int(seen),
                now,
            )
            for r in releases
        ]
        with self.db.transaction() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO release
                   (anime_id, external_id, episode, episode_end, episode_raw, title, quality,
                    size_bytes, seeders, leechers, downloads, published_at, torrent_url, magnet,
                    is_seen, first_seen_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        return len(rows)

    def update_stats(self, anime_id: int, releases: list[ReleaseInfo]) -> None:
        """Обновляет сиды/личи у уже известных раздач — цифры живут своей жизнью."""
        if not releases:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """UPDATE release SET seeders = ?, leechers = ?, downloads = ?
                    WHERE anime_id = ? AND external_id = ?""",
                [
                    (r.seeders, r.leechers, r.downloads, anime_id, r.external_id)
                    for r in releases
                ],
            )

    _SELECT = """
        SELECT r.*, a.title AS anime_title
          FROM release r JOIN anime a ON a.id = r.anime_id
    """

    def list_for_anime(self, anime_id: int) -> list[Release]:
        rows = self.db.query(
            self._SELECT
            + " WHERE r.anime_id = ?"
            + " ORDER BY r.episode IS NULL, r.episode DESC, r.first_seen_at DESC",
            (anime_id,),
        )
        return [self._to_model(row) for row in rows]

    def feed(self) -> list[Release]:
        """Лента новых серий по всем подпискам (ТЗ §5)."""
        rows = self.db.query(
            self._SELECT
            + " WHERE r.is_seen = 0 AND r.state IN ('new', 'error')"
            + " ORDER BY r.first_seen_at DESC, a.title COLLATE NOCASE, r.episode DESC"
        )
        return [self._to_model(row) for row in rows]

    def feed_count(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM release WHERE is_seen = 0 AND state IN ('new','error')"
        )
        return int(row["n"]) if row else 0

    def get(self, release_id: int) -> Release | None:
        row = self.db.query_one(self._SELECT + " WHERE r.id = ?", (release_id,))
        return self._to_model(row) if row else None

    def set_state(
        self,
        release_id: int,
        state: ReleaseState | str,
        message: str | None = None,
        *,
        seen: bool | None = None,
    ) -> None:
        fields = ["state = ?", "state_message = ?", "updated_at = ?"]
        params: list = [str(state), message, _now()]
        if seen is not None:
            fields.append("is_seen = ?")
            params.append(int(seen))
        params.append(release_id)
        with self.db.transaction() as conn:
            conn.execute(f"UPDATE release SET {', '.join(fields)} WHERE id = ?", tuple(params))

    def set_info_hash(self, release_id: int, info_hash: str) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE release SET info_hash = ? WHERE id = ?", (info_hash, release_id)
            )

    def mark_seen(self, release_ids: list[int]) -> None:
        if not release_ids:
            return
        placeholders = ",".join("?" * len(release_ids))
        with self.db.transaction() as conn:
            conn.execute(
                f"UPDATE release SET is_seen = 1 WHERE id IN ({placeholders})", tuple(release_ids)
            )

    def mark_all_seen(self, anime_id: int | None = None) -> None:
        sql = "UPDATE release SET is_seen = 1 WHERE is_seen = 0"
        params: tuple = ()
        if anime_id is not None:
            sql += " AND anime_id = ?"
            params = (anime_id,)
        with self.db.transaction() as conn:
            conn.execute(sql, params)


class HistoryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def log(
        self,
        action: HistoryAction | str,
        message: str | None = None,
        *,
        anime_id: int | None = None,
        release_id: int | None = None,
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO history (anime_id, release_id, action, message, created_at)"
                " VALUES (?,?,?,?,?)",
                (anime_id, release_id, str(action), message, _now()),
            )

    def recent(self, limit: int = 200) -> list[HistoryEntry]:
        rows = self.db.query(
            """SELECT h.*, a.title AS anime_title
                 FROM history h LEFT JOIN anime a ON a.id = h.anime_id
                ORDER BY h.created_at DESC, h.id DESC LIMIT ?""",
            (limit,),
        )
        return [
            HistoryEntry(
                id=row["id"],
                action=row["action"],
                message=row["message"],
                created_at=_parse_dt(row["created_at"]),
                anime_id=row["anime_id"],
                release_id=row["release_id"],
                anime_title=row["anime_title"],
            )
            for row in rows
        ]


class SourceStatusRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def set(self, source: str, state: SourceState | str, message: str | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO source_status (source, state, message, checked_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(source) DO UPDATE SET
                       state = excluded.state,
                       message = excluded.message,
                       checked_at = excluded.checked_at""",
                (source, str(state), message, _now()),
            )

    def all(self) -> dict[str, dict]:
        rows = self.db.query("SELECT * FROM source_status")
        return {
            row["source"]: {
                "state": row["state"],
                "message": row["message"],
                "checked_at": _parse_dt(row["checked_at"]),
            }
            for row in rows
        }


class Repositories:
    """Комплект репозиториев — удобно передавать одним объектом."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.anime = AnimeRepository(db)
        self.releases = ReleaseRepository(db)
        self.history = HistoryRepository(db)
        self.sources = SourceStatusRepository(db)
