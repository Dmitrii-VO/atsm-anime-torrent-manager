"""Добавление подписки по ссылке (ТЗ §3, способ 1)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ..db.repositories import Repositories
from ..parsers import AnimeInfo, ParserRegistry
from ..parsers.base import ParserError
from .models import Anime, HistoryAction


class SubscriptionExists(Exception):
    def __init__(self, anime: Anime) -> None:
        super().__init__(f"Подписка на «{anime.title}» уже есть")
        self.anime = anime


class SubscriptionService:
    def __init__(
        self,
        repos: Repositories,
        registry: ParserRegistry,
        poster_dir: Path | None = None,
    ) -> None:
        self.repos = repos
        self.registry = registry
        self.poster_dir = poster_dir

    def preview(self, url: str) -> AnimeInfo:
        """Загружает страницу без сохранения — для диалога добавления."""
        parser = self.registry.for_url(url.strip())
        return parser.fetch(url.strip())

    def add(self, url: str, *, auto_download: bool = False) -> Anime:
        """Добавляет подписку и импортирует архив раздач.

        Весь существующий архив помечается просмотренным: иначе лента и
        уведомления сразу получат сотни записей (ТЗ §5).
        """
        url = url.strip()
        parser = self.registry.for_url(url)
        slug = parser.extract_slug(url) if hasattr(parser, "extract_slug") else url

        existing = self.repos.anime.get_by_slug(parser.name, slug)
        if existing:
            raise SubscriptionExists(existing)

        info = parser.fetch(url)
        anime_id = self.repos.anime.add(
            title=info.title,
            source=info.source,
            url=info.url,
            slug=info.slug,
            auto_download=auto_download,
        )
        imported = self.repos.releases.add_many(anime_id, info.releases, seen=True)
        self.repos.anime.mark_checked(anime_id, ok=True)
        self.repos.history.log(
            HistoryAction.CHECK,
            f"Добавлена подписка, импортировано раздач: {imported}",
            anime_id=anime_id,
        )
        logger.info("Добавлена подписка «{}» ({} раздач)", info.title, imported)

        if info.poster_url:
            self._save_poster(anime_id, info.poster_url, parser)

        anime = self.repos.anime.get(anime_id)
        assert anime is not None
        return anime

    def remove(self, anime_id: int) -> None:
        anime = self.repos.anime.get(anime_id)
        self.repos.anime.delete(anime_id)
        if anime:
            logger.info("Подписка удалена: «{}»", anime.title)

    def _save_poster(self, anime_id: int, poster_url: str, parser) -> None:
        """Постер — приятный бонус: любая ошибка не должна ломать добавление."""
        if not self.poster_dir:
            return
        try:
            response = parser.session.get(poster_url, timeout=parser.timeout)
            response.raise_for_status()
            suffix = Path(poster_url).suffix or ".jpg"
            path = self.poster_dir / f"{anime_id}{suffix}"
            path.write_bytes(response.content)
            self.repos.anime.update_poster(anime_id, str(path))
        except (OSError, ParserError, Exception) as exc:  # noqa: BLE001
            logger.debug("Постер не загружен ({}): {}", poster_url, exc)
