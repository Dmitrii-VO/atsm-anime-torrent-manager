"""Проверка подписок и поиск новых серий (ТЗ §11).

Диффинг идёт по внешним идентификаторам раздач: множество известных id
против того, что сейчас на странице. Номер серии для этого не используется —
он может отсутствовать или повторяться у сборников.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from ..db.repositories import Repositories
from ..parsers import ParserRegistry
from ..parsers.base import ParserError
from ..torrent.base import TorrentClientError
from .models import Anime, HistoryAction, Release, ReleaseState, SourceState


@dataclass(slots=True)
class CheckResult:
    anime: Anime
    new_releases: list[Release] = field(default_factory=list)
    sent: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(slots=True)
class CheckSummary:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return sum(len(r.new_releases) for r in self.results)

    @property
    def failed(self) -> list[CheckResult]:
        return [r for r in self.results if not r.ok]

    @property
    def new_releases(self) -> list[Release]:
        return [release for result in self.results for release in result.new_releases]


class UpdateService:
    def __init__(
        self,
        repos: Repositories,
        registry: ParserRegistry,
        torrent_service=None,
    ) -> None:
        self.repos = repos
        self.registry = registry
        self.torrent_service = torrent_service

    # --- проверка --------------------------------------------------------

    def check_anime(self, anime: Anime) -> CheckResult:
        logger.info("Проверка подписки «{}»", anime.title)
        try:
            parser = self.registry.get(anime.source)
            info = parser.fetch(anime.url)
        except ParserError as exc:
            return self._handle_failure(anime, exc)

        known = self.repos.releases.known_external_ids(anime.id)
        fresh = [r for r in info.releases if r.external_id not in known]

        # Сиды/личи меняются постоянно — обновляем и у известных раздач.
        self.repos.releases.update_stats(
            anime.id, [r for r in info.releases if r.external_id in known]
        )

        if fresh:
            self.repos.releases.add_many(anime.id, fresh, seen=False)
            for release_info in fresh:
                self.repos.history.log(
                    HistoryAction.FOUND,
                    f"Найдена новая раздача: {release_info.episode_raw}",
                    anime_id=anime.id,
                )
            logger.info("«{}»: новых раздач {}", anime.title, len(fresh))

        # Страница могла открыться через другое зеркало — запоминаем адрес.
        if info.url != anime.url:
            self.repos.anime.update_url(anime.id, info.url)

        self.repos.anime.mark_checked(anime.id, ok=True)
        self.repos.sources.set(anime.source, SourceState.OK)

        new_ids = {r.external_id for r in fresh}
        stored = [
            release
            for release in self.repos.releases.list_for_anime(anime.id)
            if release.external_id in new_ids
        ]
        result = CheckResult(anime=anime, new_releases=stored)

        if anime.auto_download and stored:
            result.sent = self._auto_download(anime, stored)
        return result

    def check_all(self, animes: list[Anime] | None = None) -> CheckSummary:
        targets = animes if animes is not None else self.repos.anime.list()
        summary = CheckSummary()
        for anime in targets:
            try:
                summary.results.append(self.check_anime(anime))
            except Exception as exc:  # noqa: BLE001 — одна битая подписка не рушит цикл
                logger.exception("Непредвиденная ошибка при проверке «{}»", anime.title)
                summary.results.append(CheckResult(anime=anime, error=str(exc)))
        logger.info(
            "Проверено подписок: {}, новых раздач: {}, ошибок: {}",
            len(summary.results),
            summary.new_count,
            len(summary.failed),
        )
        return summary

    # --- автозагрузка ----------------------------------------------------

    def _auto_download(self, anime: Anime, releases: list[Release]) -> int:
        if self.torrent_service is None:
            return 0
        sent = 0
        for release in releases:
            try:
                if self.torrent_service.send(release):
                    sent += 1
            except (TorrentClientError, ParserError) as exc:
                logger.warning("Автоотправка «{}» не удалась: {}", release.episode_label, exc)
        if sent:
            logger.info("«{}»: автоматически отправлено раздач {}", anime.title, sent)
        return sent

    # --- ошибки ----------------------------------------------------------

    def _handle_failure(self, anime: Anime, exc: ParserError) -> CheckResult:
        message = str(exc)
        # Каждый класс ошибки парсера соответствует состоянию источника (ТЗ §21).
        try:
            state = SourceState(exc.state)
        except ValueError:
            state = SourceState.PARSE_ERROR
        self.repos.anime.mark_checked(anime.id, ok=False, error=message)
        self.repos.sources.set(anime.source, state, message)
        self.repos.history.log(
            HistoryAction.PARSE_ERROR, message, anime_id=anime.id
        )
        logger.error("Проверка «{}» не удалась: {}", anime.title, message)
        return CheckResult(anime=anime, error=message)

    # --- работа с лентой -------------------------------------------------

    def feed(self) -> list[Release]:
        return self.repos.releases.feed()

    def feed_count(self) -> int:
        return self.repos.releases.feed_count()

    def mark_seen(self, release_ids: list[int]) -> None:
        self.repos.releases.mark_seen(release_ids)

    def retry(self, release: Release) -> bool:
        """Повторная отправка раздачи, ранее упавшей с ошибкой."""
        if self.torrent_service is None:
            return False
        self.repos.releases.set_state(release.id, ReleaseState.NEW)
        return self.torrent_service.send(release)
