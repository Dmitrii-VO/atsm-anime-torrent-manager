"""Shikimori — основной справочник.

Выбран основным, потому что ищет по русскому названию: подписки заводятся
с astar, где заголовок русский, и другого ключа для сопоставления нет.
"""

from __future__ import annotations

from datetime import datetime

import requests
from loguru import logger

from ..services.http import RateLimiter
from .base import AnimeMetadata, MetadataCandidate, MetadataError, MetadataProvider

BASE = "https://shikimori.one"
API = f"{BASE}/api"

KIND_LABELS = {
    "tv": "ТВ",
    "movie": "Фильм",
    "ova": "OVA",
    "ona": "ONA",
    "special": "Спешл",
    "music": "Клип",
}
STATUS_LABELS = {
    "anons": "Анонс",
    "ongoing": "Онгоинг",
    "released": "Вышел",
}


class ShikimoriProvider(MetadataProvider):
    name = "shikimori"
    display_name = "Shikimori"

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        # Shikimori просит представляться и ограничивает частоту.
        self.limiter = limiter or RateLimiter(0.7)

    def _get(self, path: str, params: dict | None = None):
        self.limiter.wait("shikimori.one")
        try:
            response = self.session.get(
                f"{API}{path}",
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "ATSM"},
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            raise MetadataError(f"Shikimori недоступен: {exc}") from exc
        except ValueError as exc:
            raise MetadataError(f"Shikimori вернул не JSON: {exc}") from exc

    def search(self, title: str, limit: int = 5) -> list[MetadataCandidate]:
        data = self._get("/animes", {"search": title, "limit": limit})
        if not isinstance(data, list):
            raise MetadataError("Неожиданный ответ поиска Shikimori")

        return [
            MetadataCandidate(
                provider=self.name,
                external_id=str(item["id"]),
                title_ru=item.get("russian") or None,
                title_romaji=item.get("name") or None,
                kind=KIND_LABELS.get(item.get("kind"), item.get("kind")),
                year=_year(item.get("aired_on")),
                episodes=item.get("episodes") or None,
            )
            for item in data
            if item.get("id")
        ]

    def fetch(self, external_id: str) -> AnimeMetadata:
        data = self._get(f"/animes/{external_id}")
        poster = (data.get("image") or {}).get("original")

        return AnimeMetadata(
            provider=self.name,
            external_id=str(data["id"]),
            title_ru=data.get("russian") or None,
            title_romaji=data.get("name") or None,
            title_native=_first(data.get("japanese")),
            kind=KIND_LABELS.get(data.get("kind"), data.get("kind")),
            status=STATUS_LABELS.get(data.get("status"), data.get("status")),
            score=_float(data.get("score")),
            episodes_total=data.get("episodes") or None,
            episodes_aired=data.get("episodes_aired") or None,
            next_episode_at=_parse_dt(data.get("next_episode_at")),
            poster_url=f"{BASE}{poster}" if poster else None,
            genres=[g.get("russian") or g.get("name") for g in (data.get("genres") or [])],
            description=data.get("description") or None,
            site_url=f"{BASE}{data.get('url')}" if data.get("url") else None,
        )


def _first(value) -> str | None:
    """Некоторые названия Shikimori отдаёт списком, а не строкой."""
    if isinstance(value, (list, tuple)):
        return next((str(x) for x in value if x), None)
    return str(value) if value else None


def _year(aired_on: str | None) -> int | None:
    try:
        return int(aired_on[:4]) if aired_on else None
    except (ValueError, TypeError):
        return None


def _float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # Свежие тайтлы приходят с оценкой 0.0 — это «нет оценки», а не ноль.
    return result or None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        logger.debug("Shikimori вернул неразбираемую дату: {}", value)
        return None
