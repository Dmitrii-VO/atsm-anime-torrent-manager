"""Действия над раздачами: скачать, отправить в клиент, открыть magnet (ТЗ §7)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from loguru import logger

from ..db.repositories import Repositories
from ..parsers import ParserRegistry
from ..parsers.base import ParserError
from ..torrent.base import BaseTorrentClient, TorrentClientError
from ..torrent.bencode import BencodeError, info_hash
from .models import HistoryAction, Release, ReleaseState

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAGNET_HASH = re.compile(r"urn:btih:([0-9a-fA-F]{40})")


class TorrentService:
    def __init__(
        self,
        repos: Repositories,
        registry: ParserRegistry,
        client: BaseTorrentClient | None,
        cache_dir: Path,
    ) -> None:
        self.repos = repos
        self.registry = registry
        self.client = client
        self.cache_dir = cache_dir

    # --- получение файла -------------------------------------------------

    def fetch_torrent(self, release: Release) -> bytes:
        parser = self.registry.get(self._source_of(release))
        return parser.download_torrent(_as_release_info(release))

    def save_to(self, release: Release, directory: Path) -> Path:
        """Сохраняет .torrent в выбранную папку (действие «Скачать torrent»)."""
        data = self.fetch_torrent(release)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / self.filename_for(release)
        path.write_bytes(data)
        logger.info("Файл сохранён: {}", path)
        return path

    def filename_for(self, release: Release) -> str:
        title = release.anime_title or "anime"
        name = f"{title} - {release.episode_label}"
        return _UNSAFE_CHARS.sub("_", name).strip() + ".torrent"

    # --- отправка в клиент -----------------------------------------------

    def send(self, release: Release) -> bool:
        """Отправляет раздачу в торрент-клиент и фиксирует результат."""
        if self.client is None:
            raise TorrentClientError("Торрент-клиент не настроен")

        try:
            if release.magnet:
                # Хеш нужен, чтобы позже спросить у клиента, скачана ли раздача.
                # У magnet он лежит прямо в ссылке — качать файл ради него не надо.
                self._remember_magnet_hash(release)
                result = self.client.add_magnet(release.magnet)
            else:
                data = self.fetch_torrent(release)
                self._remember_hash(release, data)
                result = self.client.add_torrent_file(data, self.filename_for(release))
        except (TorrentClientError, ParserError) as exc:
            self._mark_error(release, str(exc))
            raise

        if not result.ok:
            self._mark_error(release, result.message)
            return False

        self.repos.releases.set_state(release.id, ReleaseState.SENT, result.message, seen=True)
        self.repos.history.log(
            HistoryAction.SENT,
            f"Передано в торрент-клиент: {release.episode_label}",
            anime_id=release.anime_id,
            release_id=release.id,
        )
        logger.info("«{}» {} → торрент-клиент", release.anime_title, release.episode_label)
        self._drop_replaced(release)
        return True

    def send_many(self, releases: list[Release]) -> tuple[int, list[str]]:
        """Массовая отправка (ТЗ §14). Возвращает число успешных и список ошибок."""
        sent, errors = 0, []
        for release in releases:
            try:
                if self.send(release):
                    sent += 1
                else:
                    errors.append(f"{release.anime_title}: {release.episode_label}")
            except (TorrentClientError, ParserError) as exc:
                errors.append(f"{release.anime_title} — {exc}")
        return sent, errors

    # --- прочие действия -------------------------------------------------

    def open_magnet(self, release: Release) -> bool:
        if not release.magnet:
            return False
        webbrowser.open(release.magnet)
        return True

    def open_in_default_client(self, release: Release) -> Path:
        """Фоллбэк без Web API: отдать файл ассоциированной программе (ТЗ §10)."""
        path = self.save_to(release, self.cache_dir)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603,S607
        return path

    def refresh_download_states(self) -> int:
        """Отмечает скачанные раздачи по данным клиента (ТЗ §13)."""
        if self.client is None or not hasattr(self.client, "torrent_states"):
            return 0

        pending = [
            release
            for anime in self.repos.anime.list()
            for release in self.repos.releases.list_for_anime(anime.id)
            if release.state == ReleaseState.SENT and release.info_hash
        ]
        if not pending:
            return 0

        try:
            states = self.client.torrent_states([r.info_hash for r in pending])
        except TorrentClientError as exc:
            logger.debug("Статусы раздач не получены: {}", exc)
            return 0

        updated = 0
        for release in pending:
            state = states.get((release.info_hash or "").lower())
            if state in {
                "uploading",
                "stalledUP",
                "pausedUP",
                "stoppedUP",
                "queuedUP",
                "forcedUP",
            }:
                self.repos.releases.set_state(release.id, ReleaseState.DOWNLOADED, "Скачана")
                updated += 1
        return updated

    def _drop_replaced(self, release: Release) -> None:
        """Убирает из клиента пачки, которые новая раздача перекрыла целиком.

        AniLiberty обновляет раздачу на месте: «1-4» становится «1-5» с новым
        хешем. Без этого в клиенте копятся дубликаты уже скачанных серий.
        """
        settings = getattr(self.client, "settings", None)
        if not getattr(settings, "delete_replaced", False) or not hasattr(self.client, "delete"):
            return

        replaced = [
            old
            for old in self.repos.releases.list_for_anime(release.anime_id)
            if old.id != release.id and old.info_hash and _covers(release, old)
        ]
        if not replaced:
            return

        try:
            self.client.delete([old.info_hash for old in replaced])
        except TorrentClientError as exc:
            # Не повод рушить успешную отправку: дубликат переживём.
            logger.debug("Старые раздачи не удалены: {}", exc)
            return

        for old in replaced:
            # Хеш больше ни о чём не спросишь — раздачи в клиенте нет.
            self.repos.releases.set_info_hash(old.id, "")
            self.repos.history.log(
                HistoryAction.SENT,
                f"{old.episode_label} заменена на {release.episode_label}, "
                "старая раздача убрана из клиента",
                anime_id=old.anime_id,
                release_id=old.id,
            )
        logger.info("Заменено пачек: {} → {}", len(replaced), release.episode_label)

    # --- служебное -------------------------------------------------------

    def _remember_magnet_hash(self, release: Release) -> None:
        match = _MAGNET_HASH.search(release.magnet or "")
        if match:
            self.repos.releases.set_info_hash(release.id, match.group(1).lower())

    def _remember_hash(self, release: Release, data: bytes) -> None:
        try:
            self.repos.releases.set_info_hash(release.id, info_hash(data))
        except BencodeError as exc:
            logger.debug("info_hash не вычислен: {}", exc)

    def _mark_error(self, release: Release, message: str) -> None:
        self.repos.releases.set_state(release.id, ReleaseState.ERROR, message)
        self.repos.history.log(
            HistoryAction.SEND_ERROR,
            f"{release.episode_label}: {message}",
            anime_id=release.anime_id,
            release_id=release.id,
        )
        logger.error("Отправка «{}» не удалась: {}", release.episode_label, message)

    def _source_of(self, release: Release) -> str:
        anime = self.repos.anime.get(release.anime_id)
        if anime is None:
            raise ParserError("Подписка не найдена")
        return anime.source


def _covers(new: Release, old: Release) -> bool:
    """Диапазон серий старой раздачи целиком лежит внутри новой."""
    if new.episode is None or old.episode is None:
        return False
    new_end = new.episode_end or new.episode
    old_end = old.episode_end or old.episode
    if (new.episode, new_end) == (old.episode, old_end):
        return False  # та же серия, перезалитая заново — не замена пачки
    return new.episode <= old.episode and old_end <= new_end


def _as_release_info(release: Release):
    """Torrent-загрузчику парсера нужен только адрес файла."""
    from ..parsers.base import ReleaseInfo

    return ReleaseInfo(
        external_id=release.external_id,
        episode_raw=release.episode_raw,
        torrent_url=release.torrent_url,
        magnet=release.magnet,
    )
