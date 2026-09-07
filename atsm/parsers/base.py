"""Контракт плагина-источника (ТЗ §20) и типизированные ошибки (ТЗ §21)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


class ParserError(Exception):
    """Базовая ошибка источника. Каждый подкласс = состояние в диагностике."""

    state = "parse_error"


class SourceUnreachable(ParserError):
    """Сайт не ответил: сеть, таймаут, 5xx, все зеркала мертвы."""

    state = "unreachable"


class ParseError(ParserError):
    """Ответ получен, но разобрать его не удалось."""

    state = "parse_error"


class LayoutChanged(ParserError):
    """HTTP 200, но ни одной раздачи не найдено — вёрстка сайта изменилась."""

    state = "layout_changed"


@dataclass(slots=True)
class ReleaseInfo:
    """Одна раздача. external_id — ключ дедупликации (ТЗ §18)."""

    external_id: str
    episode_raw: str
    episode: int | None = None
    # Конец диапазона для сборников («Серии 27-28» → episode=27, episode_end=28).
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


@dataclass(slots=True)
class SearchHit:
    """Строка результата поиска у источников, которые умеют искать (ТЗ §3).

    Отдельно от ReleaseInfo: найденное ещё не подписка и не раздача в базе —
    это просто то, что показывается в таблице до выбора пользователя.
    """

    topic_id: str
    title: str
    url: str
    category: str | None = None
    size_bytes: int | None = None
    seeders: int | None = None
    leechers: int | None = None
    added: date | None = None


@dataclass(slots=True)
class AnimeInfo:
    title: str
    url: str
    slug: str
    source: str
    poster_url: str | None = None
    status: str = "ongoing"
    releases: list[ReleaseInfo] = field(default_factory=list)


class BaseParser(ABC):
    """Источник. Добавление нового сайта = новый подкласс, ядро не меняется."""

    name: str = ""
    display_name: str = ""
    domains: tuple[str, ...] = ()

    def match(self, url: str) -> bool:
        """Сопоставление по суффиксу хоста: переживает смену номера зеркала."""
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains)

    @abstractmethod
    def fetch(self, url: str) -> AnimeInfo:
        """Один запрос → метаданные аниме и полный список раздач."""

    @abstractmethod
    def download_torrent(self, release: ReleaseInfo) -> bytes:
        """Содержимое .torrent-файла для передачи в клиент."""
