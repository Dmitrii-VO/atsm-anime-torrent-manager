"""AniList — дополняющий справочник.

Нужен ради точного времени выхода следующей серии: Shikimori даёт дату,
AniList — конкретную минуту и номер эпизода. Ищет по латинскому названию,
поэтому вызывается после Shikimori, по его romaji.

Лимит сервиса — 30 запросов в минуту, отсюда пауза в 2 секунды.
"""

from __future__ import annotations

from datetime import datetime

import requests

from ..services.http import RateLimiter
from .base import AnimeMetadata, MetadataCandidate, MetadataError, MetadataProvider

ENDPOINT = "https://graphql.anilist.co"

STATUS_LABELS = {
    "FINISHED": "Вышел",
    "RELEASING": "Онгоинг",
    "NOT_YET_RELEASED": "Анонс",
    "CANCELLED": "Отменён",
    "HIATUS": "Приостановлен",
}

_FIELDS = """
    id
    title { romaji english native }
    format
    status
    averageScore
    episodes
    genres
    description(asHtml: false)
    siteUrl
    coverImage { large }
    startDate { year }
    nextAiringEpisode { episode airingAt }
"""

SEARCH_QUERY = """
query ($search: String, $limit: Int) {
  Page(perPage: $limit) {
    media(search: $search, type: ANIME) {
      id
      title { romaji english native }
      format
      episodes
      startDate { year }
    }
  }
}
"""

FETCH_QUERY = "query ($id: Int) { Media(id: $id, type: ANIME) { %s } }" % _FIELDS
FETCH_BY_TITLE = "query ($search: String) { Media(search: $search, type: ANIME) { %s } }" % _FIELDS


class AniListProvider(MetadataProvider):
    name = "anilist"
    display_name = "AniList"

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.limiter = limiter or RateLimiter(2.0)

    def _query(self, query: str, variables: dict) -> dict:
        self.limiter.wait("graphql.anilist.co")
        try:
            response = self.session.post(
                ENDPOINT,
                json={"query": query, "variables": variables},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MetadataError(f"AniList недоступен: {exc}") from exc

        if response.status_code == 429:
            raise MetadataError("AniList: превышен лимит запросов, попробуйте позже")
        if response.status_code >= 400:
            raise MetadataError(f"AniList вернул HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise MetadataError(f"AniList вернул не JSON: {exc}") from exc

        if payload.get("errors"):
            raise MetadataError(f"AniList: {payload['errors'][0].get('message', 'ошибка')}")
        return payload.get("data") or {}

    def search(self, title: str, limit: int = 5) -> list[MetadataCandidate]:
        data = self._query(SEARCH_QUERY, {"search": title, "limit": limit})
        media = ((data.get("Page") or {}).get("media")) or []
        return [self._candidate(item) for item in media if item.get("id")]

    def fetch(self, external_id: str) -> AnimeMetadata:
        data = self._query(FETCH_QUERY, {"id": int(external_id)})
        return self._metadata(data.get("Media") or {})

    def fetch_by_title(self, title: str) -> AnimeMetadata | None:
        """Прямой путь: у нас есть romaji от Shikimori, лишний поиск не нужен."""
        data = self._query(FETCH_BY_TITLE, {"search": title})
        media = data.get("Media")
        return self._metadata(media) if media else None

    def _candidate(self, item: dict) -> MetadataCandidate:
        titles = item.get("title") or {}
        return MetadataCandidate(
            provider=self.name,
            external_id=str(item["id"]),
            title_ru=None,  # русских названий у AniList нет
            title_romaji=titles.get("romaji") or titles.get("english"),
            kind=item.get("format"),
            year=(item.get("startDate") or {}).get("year"),
            episodes=item.get("episodes"),
        )

    def _metadata(self, media: dict) -> AnimeMetadata:
        if not media:
            raise MetadataError("AniList не вернул тайтл")

        titles = media.get("title") or {}
        next_ep = media.get("nextAiringEpisode") or {}
        score = media.get("averageScore")

        return AnimeMetadata(
            provider=self.name,
            external_id=str(media.get("id")),
            title_romaji=titles.get("romaji") or titles.get("english"),
            title_native=titles.get("native"),
            kind=media.get("format"),
            status=STATUS_LABELS.get(media.get("status"), media.get("status")),
            # У AniList оценка в процентах, приводим к десятибалльной шкале.
            score=round(score / 10, 2) if score else None,
            episodes_total=media.get("episodes"),
            next_episode_number=next_ep.get("episode"),
            next_episode_at=(
                datetime.fromtimestamp(next_ep["airingAt"]) if next_ep.get("airingAt") else None
            ),
            poster_url=(media.get("coverImage") or {}).get("large"),
            genres=list(media.get("genres") or []),
            description=media.get("description"),
            site_url=media.get("siteUrl"),
        )
