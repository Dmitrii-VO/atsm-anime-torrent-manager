"""Сопоставление подписки со справочниками и хранение результата."""

from __future__ import annotations

import re
import threading

from loguru import logger

from ..db.repositories import Repositories
from .anilist import AniListProvider
from .base import AnimeMetadata, MetadataCandidate, MetadataError
from .shikimori import ShikimoriProvider


def normalize(title: str) -> str:
    """Для сравнения названий: регистр, ё/е и пунктуация значения не имеют."""
    text = title.lower().replace("ё", "е")
    return re.sub(r"[^\w\s]", " ", text).strip()


class MetadataService:
    def __init__(
        self,
        repos: Repositories,
        shikimori: ShikimoriProvider | None = None,
        anilist: AniListProvider | None = None,
    ) -> None:
        self.repos = repos
        self.shikimori = shikimori or ShikimoriProvider()
        self.anilist = anilist or AniListProvider()
        self._enrich_lock = threading.Lock()

    # --- поиск -----------------------------------------------------------

    def search(self, title: str, limit: int = 5) -> list[MetadataCandidate]:
        """Кандидаты для ручной привязки: у одного тайтла бывают сезоны и фильмы."""
        return self.shikimori.search(title, limit=limit)

    def pick_best(self, title: str, candidates: list[MetadataCandidate]) -> MetadataCandidate | None:
        """Точное совпадение русского названия важнее порядка выдачи.

        Поиск по «Пожиратель звёзд» возвращает ещё «Пожиратель звёзд 3» и
        полнометражку — брать вслепую первый результат нельзя.
        """
        if not candidates:
            return None

        target = normalize(title)
        for candidate in candidates:
            for variant in (candidate.title_ru, candidate.title_romaji):
                if variant and normalize(variant) == target:
                    return candidate
        return candidates[0]

    # --- обогащение ------------------------------------------------------

    def enrich(self, anime_id: int, shikimori_id: str | None = None) -> AnimeMetadata:
        """Собирает данные по подписке и сохраняет их.

        Если shikimori_id не задан — берётся сохранённый ранее, иначе тайтл
        подбирается по названию подписки.
        """
        with self._enrich_lock:
            return self._enrich(anime_id, shikimori_id)

    def _enrich(self, anime_id: int, shikimori_id: str | None = None) -> AnimeMetadata:
        anime = self.repos.anime.get(anime_id)
        if anime is None:
            raise MetadataError("Подписка не найдена")

        stored = self.repos.metadata.get(anime_id)
        external_id = shikimori_id or (stored.get("shikimori_id") if stored else None)

        if not external_id:
            candidates = self.search(anime.title)
            best = self.pick_best(anime.title, candidates)
            if best is None:
                raise MetadataError(f"На Shikimori ничего не найдено по запросу «{anime.title}»")
            external_id = best.external_id
            logger.info("«{}» сопоставлено с Shikimori id {}", anime.title, external_id)

        metadata = self.shikimori.fetch(external_id)
        anilist_id = self._add_anilist(metadata)
        same_title = stored is not None and stored.get("shikimori_id") == external_id
        preserve_existing = anilist_id is None and same_title

        self.repos.metadata.save(
            anime_id,
            metadata,
            anilist_id=anilist_id,
            preserve_existing=preserve_existing,
        )
        return metadata

    def backfill_franchise(self) -> int:
        """Догружает франшизу тем подпискам, где её ещё нет.

        Идентификатор тайтла уже сохранён, поэтому поиск не нужен — один
        запрос на подписку. AniList здесь не трогаем: франшиза берётся
        только из Shikimori.
        """
        anime_ids = self.repos.metadata.missing_franchise()
        updated = 0

        for anime_id in anime_ids:
            stored = self.repos.metadata.get(anime_id)
            external_id = (stored or {}).get("shikimori_id")
            if not external_id:
                continue
            try:
                metadata = self.shikimori.fetch(external_id)
            except MetadataError as exc:
                logger.debug("Франшиза для подписки {} не получена: {}", anime_id, exc)
                continue

            self.repos.metadata.update_franchise(anime_id, metadata.franchise)
            updated += 1

        if updated:
            logger.info("Догружена франшиза для подписок: {}", updated)
        return updated

    def _add_anilist(self, metadata: AnimeMetadata) -> str | None:
        """AniList дополняет точным временем выхода серии. Его отказ не критичен."""
        if not metadata.title_romaji:
            return None
        try:
            extra = self.anilist.fetch_by_title(metadata.title_romaji)
        except MetadataError as exc:
            logger.debug("AniList не ответил: {}", exc)
            return None

        if extra is None:
            return None

        # Точное время выхода — то, ради чего AniList и нужен.
        if extra.next_episode_at:
            metadata.next_episode_at = extra.next_episode_at
            metadata.next_episode_number = extra.next_episode_number
        metadata.merge(extra)
        return extra.external_id
