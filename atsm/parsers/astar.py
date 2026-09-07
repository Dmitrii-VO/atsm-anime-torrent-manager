"""Парсер astar.bz (AniStar).

Особенности источника, установленные разведкой (ТЗ §26):
  * список раздач в статическом HTML, JS не нужен;
  * кодировка windows-1251, при этом в <head> есть ложный charset=utf-8 —
    поэтому в BeautifulSoup передаётся уже декодированная строка;
  * magnet-ссылок и поля качества нет, есть сиды/личи/скачивания;
  * домен ротируется (v19 → v30 → …), постоянен только суффикс astar.bz.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
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

ENCODING = "windows-1251"

# Заглушка «Проверка безопасности»: сайт отдаёт её вместо страницы под нагрузкой
# и при подозрении на бота. HTTP 200, вёрстка тут ни при чём — раздач просто нет.
CHALLENGE_MARKERS = ("Проверка безопасности", "включите JavaScript")

SLUG_RE = re.compile(r"/(?P<slug>\d+-[^/]+?\.html)$", re.IGNORECASE)
TORRENT_ID_RE = re.compile(r"torrent_(?P<id>\d+)_info")
# Кроме обычных «Серия 235» встречаются сборники: «Серии 27-28», «Фильмы 1-4».
EPISODE_RE = re.compile(r"Сери[яи]\s+(\d+)(?:\s*-\s*(\d+))?")
SIZE_RE = re.compile(r"([\d.,]+)\s*(Kb|Mb|Gb|Tb)", re.IGNORECASE)
DATE_RE = re.compile(r"Дата:\s*([\d]{2}-[\d]{2}-[\d]{4})")
UNITS = {"kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}


class AstarParser(BaseParser):
    name = "astar"
    display_name = "AniStar"
    domains = ("astar.bz",)

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
        self.limiter = limiter

    # --- адреса и зеркала ------------------------------------------------

    @staticmethod
    def extract_slug(url: str) -> str:
        match = SLUG_RE.search(urlparse(url).path)
        if not match:
            raise ParseError(f"Не удалось определить страницу аниме в ссылке: {url}")
        return match.group("slug")

    def hosts_to_try(self, url: str) -> list[str]:
        """Хост из ссылки, затем настроенный, затем известные зеркала."""
        ordered: list[str] = []
        for host in (urlparse(url).hostname, self.sources.astar_host, *self.sources.astar_mirrors):
            if host and host not in ordered:
                ordered.append(host)
        return ordered

    @staticmethod
    def build_url(host: str, slug: str) -> str:
        return urlunparse(("https", host, "/" + slug, "", "", ""))

    # --- загрузка --------------------------------------------------------

    def _get(self, url: str) -> requests.Response:
        if self.limiter:
            self.limiter.wait(urlparse(url).hostname or "")
        return self.session.get(url, timeout=self.timeout, allow_redirects=True)

    @staticmethod
    def _is_challenge(response: requests.Response) -> bool:
        # Заглушка отдаётся в UTF-8, боевые страницы — в windows-1251,
        # поэтому декодируем отдельно от основного разбора.
        if len(response.content) > 20_000:
            return False
        text = response.content.decode("utf-8", errors="ignore")
        return any(marker in text for marker in CHALLENGE_MARKERS)

    def fetch(self, url: str) -> AnimeInfo:
        slug = self.extract_slug(url)
        errors: list[str] = []
        blocked = False

        for host in self.hosts_to_try(url):
            candidate = self.build_url(host, slug)
            try:
                response = self._get(candidate)
            except requests.RequestException as exc:
                errors.append(f"{host}: {type(exc).__name__}")
                logger.debug("Зеркало {} недоступно: {}", host, exc)
                continue

            if response.status_code != 200:
                errors.append(f"{host}: HTTP {response.status_code}")
                logger.debug("Зеркало {} вернуло HTTP {}", host, response.status_code)
                continue

            if self._is_challenge(response):
                errors.append(f"{host}: проверка браузера")
                logger.debug("Зеркало {} отдало заглушку проверки браузера", host)
                blocked = True
                continue

            html = response.content.decode(ENCODING, errors="replace")
            info = self.parse(html, base_url=candidate)

            # Рабочее зеркало запоминаем, чтобы следующие проверки шли сразу в него.
            if self.sources.astar_host != host:
                logger.info("Актуальное зеркало astar: {}", host)
                self.sources.astar_host = host
            return info

        if blocked:
            # Не «изменилась вёрстка»: страница цела, её просто не показывают.
            raise SourceUnreachable(
                "astar включил проверку браузера (защита от ботов и нагрузки). "
                "Раздачи временно недоступны — проверьте позже."
            )
        raise SourceUnreachable("Ни одно зеркало astar не ответило: " + "; ".join(errors))

    def download_torrent(self, release: ReleaseInfo) -> bytes:
        if not release.torrent_url:
            raise ParseError(f"У раздачи {release.external_id} нет ссылки на torrent")
        try:
            response = self._get(release.torrent_url)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SourceUnreachable(f"Не удалось скачать torrent: {exc}") from exc

        content_type = response.headers.get("content-type", "")
        # Вместо файла может прийти HTML-страница с ошибкой — важно поймать сразу.
        if "bittorrent" not in content_type and not response.content.startswith(b"d"):
            raise ParseError(f"Вместо .torrent получен {content_type or 'неизвестный тип'}")
        return response.content

    # --- разбор ----------------------------------------------------------

    def parse(self, html: str, base_url: str) -> AnimeInfo:
        soup = BeautifulSoup(html, "lxml")
        blocks = soup.select("div.torrent[id^=torrent_]")
        if not blocks:
            raise LayoutChanged("На странице не найдено ни одной раздачи (div.torrent)")

        releases = []
        for block in blocks:
            release = self._parse_release(block, base_url)
            if release is not None:
                releases.append(release)
        if not releases:
            raise LayoutChanged("Блоки раздач найдены, но ни один не удалось разобрать")

        releases.sort(key=lambda r: (r.episode is not None, r.episode or 0), reverse=True)

        return AnimeInfo(
            title=self._parse_title(soup),
            url=base_url,
            slug=self.extract_slug(base_url),
            source=self.name,
            poster_url=self._parse_poster(soup, base_url),
            status="ongoing",
            releases=releases,
        )

    @staticmethod
    def _parse_title(soup: BeautifulSoup) -> str:
        node = soup.select_one("h1")
        raw = node.get_text(" ", strip=True) if node else ""
        if not raw:
            meta = soup.find("meta", property="og:title")
            raw = (meta.get("content") if meta else "") or ""
        # На сайте заголовок — все варианты названия через «/».
        # В списке подписок нужен один короткий, берём первый.
        title = raw.split("/")[0].split(",")[0].strip()
        if not title:
            raise ParseError("Не удалось определить название аниме")
        return title

    @staticmethod
    def _parse_poster(soup: BeautifulSoup, base_url: str) -> str | None:
        meta = soup.find("meta", property="og:image")
        src = meta.get("content") if meta else None
        return urljoin(base_url, src) if src else None

    def _parse_release(self, block, base_url: str) -> ReleaseInfo | None:
        match = TORRENT_ID_RE.search(block.get("id", ""))
        link = block.select_one("div.title a[href]")
        if not match or not link:
            return None

        external_id = match.group("id")
        episode_raw = link.get_text(" ", strip=True)
        text = block.get_text(" ", strip=True)

        episode, episode_end = self._parse_episode(episode_raw)
        date_match = DATE_RE.search(text)

        return ReleaseInfo(
            external_id=external_id,
            episode_raw=episode_raw,
            episode=episode,
            episode_end=episode_end,
            title=episode_raw,
            size_bytes=self._parse_size(episode_raw) or self._parse_size(text),
            seeders=self._parse_int(block, "div.li_distribute"),
            leechers=self._parse_int(block, "div.li_swing"),
            downloads=self._parse_int(block, "div.li_download"),
            published_at=self._parse_date(date_match.group(1)) if date_match else None,
            torrent_url=urljoin(base_url, link["href"]),
            magnet=None,  # источник magnet не отдаёт
        )

    @staticmethod
    def _parse_episode(raw: str) -> tuple[int | None, int | None]:
        """«Серия 235» → (235, None); «Серии 27-28» → (27, 28); «Фильмы 1-4» → (None, None).

        Для сборника номером серии считается начало диапазона: так раздача
        встаёт в ленту на своё место, а не над свежими сериями.
        """
        match = EPISODE_RE.search(raw)
        if not match:
            return None, None
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        return start, end

    @staticmethod
    def _parse_size(text: str) -> int | None:
        match = SIZE_RE.search(text)
        if not match:
            return None
        value = float(match.group(1).replace(",", "."))
        return int(value * UNITS[match.group(2).lower()])

    @staticmethod
    def _parse_int(block, selector: str) -> int | None:
        node = block.select_one(selector)
        if not node:
            return None
        digits = re.sub(r"\D", "", node.get_text(strip=True))
        return int(digits) if digits else None

    @staticmethod
    def _parse_date(value: str):
        try:
            return datetime.strptime(value, "%d-%m-%Y").date()
        except ValueError:
            return None
