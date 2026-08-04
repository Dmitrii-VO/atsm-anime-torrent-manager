"""Парсер AniLiberty (aniliberty.top, бывш. anilibria.top).

Работает через JSON API, а не по разметке — переверстка сайта источнику не
страшна. Модель раздач принципиально иная, чем у astar:

  * astar    — один торрент на серию, их сотни;
  * AniLiberty — две-три раздачи на весь релиз (AVC/HEVC), каждая содержит
    все вышедшие серии и **обновляется на месте** по мере выхода новых.

Отсюда главное решение: идентификатор раздачи включает hash торрента, иначе
рост пачки «1-4» → «1-5» не был бы виден как новая серия — id-то прежний.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlparse

import requests
from loguru import logger

from ..config import SourceSettings
from ..services.http import RateLimiter
from .base import (
    AnimeInfo,
    BaseParser,
    LayoutChanged,
    ParseError,
    ReleaseInfo,
    SourceUnreachable,
)

# Проект переехал: anilibria.top отдаёт 302 на aniliberty.top, API работает
# на обоих. Первый в списке — основной, остальные держим как запасные,
# чтобы подписки пережили следующее переименование.
HOSTS = ("aniliberty.top", "anilibria.top")

ALIAS_RE = re.compile(r"/release/(?P<alias>[^/?#]+)")
RANGE_RE = re.compile(r"(\d+)(?:\s*-\s*(\d+))?")


class AniLibriaParser(BaseParser):
    name = "anilibria"
    display_name = "AniLiberty"
    domains = HOSTS

    def __init__(
        self,
        session: requests.Session,
        sources: SourceSettings,
        timeout: float = 15.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.session = session
        self.sources = sources
        self.timeout = timeout
        # Заголовков о лимитах сервис не отдаёт — держим паузу на всякий случай.
        self.limiter = limiter or RateLimiter(1.0)

    # --- адреса ----------------------------------------------------------

    @staticmethod
    def extract_slug(url: str) -> str:
        """Из ссылки вида /anime/releases/release/<alias>/ достаёт alias."""
        match = ALIAS_RE.search(urlparse(url).path)
        if not match:
            raise ParseError(f"Не удалось определить релиз AniLiberty в ссылке: {url}")
        return match.group("alias")

    @staticmethod
    def page_url(alias: str, host: str = HOSTS[0]) -> str:
        return f"https://{host}/anime/releases/release/{alias}/"

    def hosts_to_try(self, url: str) -> list[str]:
        """Хост из ссылки первым, затем остальные известные."""
        ordered: list[str] = []
        for host in (urlparse(url).hostname, *HOSTS):
            if host and host not in ordered:
                ordered.append(host)
        return ordered

    def _get(self, url: str) -> requests.Response:
        self.limiter.wait("aniliberty.top")
        return self.session.get(url, timeout=self.timeout, allow_redirects=True)

    # --- загрузка --------------------------------------------------------

    def fetch(self, url: str) -> AnimeInfo:
        alias = self.extract_slug(url)
        errors: list[str] = []

        for host in self.hosts_to_try(url):
            try:
                response = self._get(f"https://{host}/api/v1/anime/releases/{alias}")
            except requests.RequestException as exc:
                errors.append(f"{host}: {type(exc).__name__}")
                logger.debug("Домен {} недоступен: {}", host, exc)
                continue

            if response.status_code == 404:
                raise ParseError(f"Релиз «{alias}» не найден на {host}")
            if response.status_code != 200:
                errors.append(f"{host}: HTTP {response.status_code}")
                continue

            try:
                data = response.json()
            except ValueError as exc:
                raise ParseError(f"{host} вернул не JSON: {exc}") from exc

            return self.parse(data, alias, host=host)

        raise SourceUnreachable("AniLiberty не отвечает: " + "; ".join(errors))

    def download_torrent(self, release: ReleaseInfo) -> bytes:
        if not release.torrent_url:
            raise ParseError(f"У раздачи {release.external_id} нет ссылки на torrent")
        try:
            response = self._get(release.torrent_url)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceUnreachable(f"Не удалось скачать torrent: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        if "bittorrent" not in content_type and not response.content.startswith(b"d"):
            raise ParseError(f"Вместо .torrent получен {content_type or 'неизвестный тип'}")
        return response.content

    # --- разбор ----------------------------------------------------------

    def parse(self, data: dict, alias: str, host: str = HOSTS[0]) -> AnimeInfo:
        if not isinstance(data, dict) or "name" not in data:
            raise ParseError("Неожиданная структура ответа AniLiberty")

        torrents = data.get("torrents") or []
        if not torrents:
            # Релиз без раздач — обычное дело для анонса или блокировки,
            # но для подписки это тупик, о котором нужно сказать вслух.
            raise LayoutChanged(
                "У релиза нет ни одной раздачи"
                + (" (заблокирован правообладателем)" if data.get("is_blocked_by_copyrights") else "")
            )

        releases = [self._parse_torrent(t, host) for t in torrents]
        releases = [r for r in releases if r is not None]
        if not releases:
            raise LayoutChanged("Раздачи есть, но ни одну не удалось разобрать")

        releases.sort(key=lambda r: (r.episode_end or r.episode or 0, r.external_id), reverse=True)

        poster = (data.get("poster") or {}).get("src")
        return AnimeInfo(
            title=self._title(data),
            url=self.page_url(alias, host),
            slug=alias,
            source=self.name,
            poster_url=f"https://{host}{poster}" if poster else None,
            status="ongoing" if data.get("is_ongoing") else "completed",
            releases=releases,
        )

    @staticmethod
    def _title(data: dict) -> str:
        names = data.get("name") or {}
        title = names.get("main") or names.get("english") or ""
        if not title:
            raise ParseError("В ответе AniLiberty нет названия релиза")
        return title.strip()

    def _parse_torrent(self, torrent: dict, host: str = HOSTS[0]) -> ReleaseInfo | None:
        torrent_id = torrent.get("id")
        info_hash = torrent.get("hash")
        if not torrent_id or not info_hash:
            return None

        episode, episode_end = self._parse_range(torrent.get("description"))
        quality = self._label(torrent.get("quality"))
        codec = self._label(torrent.get("codec"))
        kind = self._label(torrent.get("type"))

        return ReleaseInfo(
            # Раздача обновляется на месте: id остаётся, hash меняется вместе
            # с диапазоном серий. Без hash рост пачки не был бы виден.
            external_id=f"{torrent_id}:{info_hash}",
            episode_raw=torrent.get("label") or f"Раздача {torrent_id}",
            episode=episode,
            episode_end=episode_end,
            title=torrent.get("label"),
            quality=" ".join(x for x in (quality, codec, kind) if x) or None,
            size_bytes=torrent.get("size"),
            seeders=torrent.get("seeders"),
            leechers=torrent.get("leechers"),
            downloads=torrent.get("completed_times"),
            published_at=self._parse_date(torrent.get("updated_at") or torrent.get("created_at")),
            torrent_url=f"https://{host}/api/v1/anime/torrents/{torrent_id}/file",
            magnet=torrent.get("magnet"),
        )

    @staticmethod
    def _label(value) -> str | None:
        """Поля-справочники приходят объектом {value, description}."""
        if isinstance(value, dict):
            return value.get("description") or value.get("value")
        return value or None

    @staticmethod
    def _parse_range(description) -> tuple[int | None, int | None]:
        """«1-5» → (1, 5); «7» → (7, None); мусор → (None, None)."""
        if not description:
            return None, None
        match = RANGE_RE.search(str(description))
        if not match:
            return None, None
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        return start, end

    @staticmethod
    def _parse_date(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            logger.debug("AniLiberty вернул неразбираемую дату: {}", value)
            return None
