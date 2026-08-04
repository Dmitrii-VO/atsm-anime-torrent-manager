"""Доменные модели (ТЗ §18)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ReleaseState(StrEnum):
    NEW = "new"
    SENT = "sent"
    DOWNLOADED = "downloaded"
    ERROR = "error"
    IGNORED = "ignored"


class AnimeStatus(StrEnum):
    ONGOING = "ongoing"
    COMPLETED = "completed"
    PAUSED = "paused"


class SourceState(StrEnum):
    OK = "ok"
    UNREACHABLE = "unreachable"
    PARSE_ERROR = "parse_error"
    LAYOUT_CHANGED = "layout_changed"


SOURCE_STATE_LABELS = {
    SourceState.OK: "Работает",
    SourceState.UNREACHABLE: "Недоступен",
    SourceState.PARSE_ERROR: "Ошибка парсинга",
    SourceState.LAYOUT_CHANGED: "Изменена структура сайта",
}


class HistoryAction(StrEnum):
    FOUND = "found"
    SENT = "sent"
    DOWNLOADED = "downloaded"
    SEND_ERROR = "send_error"
    PARSE_ERROR = "parse_error"
    CHECK = "check"


@dataclass(slots=True)
class Anime:
    id: int
    title: str
    source: str
    url: str
    slug: str
    poster_path: str | None = None
    status: str = AnimeStatus.ONGOING
    auto_download: bool = False
    is_favorite: bool = False
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    # Считаются запросом со стороны репозитория, в таблице не хранятся.
    new_count: int = 0
    last_episode: int | None = None


@dataclass(slots=True)
class Release:
    id: int
    anime_id: int
    external_id: str
    episode_raw: str
    episode: int | None = None
    episode_end: int | None = None
    title: str | None = None
    quality: str | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    downloads: int | None = None
    published_at: date | None = None
    torrent_url: str | None = None
    magnet: str | None = None
    info_hash: str | None = None
    state: str = ReleaseState.NEW
    state_message: str | None = None
    is_seen: bool = False
    first_seen_at: datetime | None = None
    anime_title: str | None = None  # для ленты, из JOIN

    @property
    def episode_label(self) -> str:
        if self.episode is None:
            return self.episode_raw
        if self.episode_end:
            return f"Серии {self.episode}-{self.episode_end}"
        return f"Серия {self.episode}"

    @property
    def size_label(self) -> str:
        if not self.size_bytes:
            return "—"
        size = float(self.size_bytes)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if size < 1024 or unit == "ГБ":
                return f"{size:.0f} {unit}" if unit in ("Б", "КБ") else f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} ГБ"


@dataclass(slots=True)
class HistoryEntry:
    id: int
    action: str
    message: str | None
    created_at: datetime | None
    anime_id: int | None = None
    release_id: int | None = None
    anime_title: str | None = None
