"""Парсер RuTracker (ТЗ §20).

Источник устроен иначе, чем astar и AniLiberty, и это определяет реализацию:

  * сайт закрыт **Cloudflare**, JS-проверку `requests` не проходит. Обход не
    делается: приложение ходит сессией, которую пользователь открыл в браузере
    сам, — куки и User-Agent он переносит в настройки («Copy as cURL»);
  * подписок на RuTracker нет. Нужны поиск, скачивание и потоковый просмотр,
    поэтому главный метод здесь — `search()`, а не `fetch()`;
  * `fetch()` всё же реализован: тема форума = одна раздача. Это даёт добавление
    по ссылке и позволяет переиспользовать всё, что написано для подписок,
    включая отправку в клиент и потоковый просмотр.

Разметка описана в vault/Заметки/RuTracker — устройство источника.md.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, urlparse

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
    SearchHit,
    SourceUnreachable,
)

HOSTS = ("rutracker.org", "rutracker.net")
ENCODING = "windows-1251"

TOPIC_RE = re.compile(r"[?&]t=(\d+)")
SIZE_RE = re.compile(r"([\d.,]+)\s*(KB|MB|GB|TB)", re.IGNORECASE)
UNITS = {"kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[0-9A-Fa-f]{40}[^\"'\s]*")
# Качество прячется в заголовке темы: [2026, США, боевик, WEB-DLRip-AVC].
QUALITY_RE = re.compile(
    r"\b(\d{3,4}p|4K|UHD|BDRemux|BDRip|WEB-?DLRip|WEB-?DL|WEBRip|HDRip|DVDRip|HDTVRip)"
    r"(?:-(AVC|HEVC|x264|x265))?\b",
    re.IGNORECASE,
)
# «26-Июл-26» — месяц русским сокращением, strptime такое не берёт.
MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
DATE_RE = re.compile(r"(\d{1,2})-([А-Яа-я]{3})-(\d{2,4})")


class RuTrackerParser(BaseParser):
    name = "rutracker"
    display_name = "RuTracker"
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
        self.limiter = limiter or RateLimiter(1.0)

    # --- доступ ----------------------------------------------------------

    @property
    def host(self) -> str:
        return self.sources.rutracker_host or HOSTS[0]

    @property
    def configured(self) -> bool:
        """Без кук из браузера источник недоступен в принципе."""
        return bool(self.sources.rutracker_cookies.strip())

    def _cookies(self) -> dict[str, str]:
        jar: dict[str, str] = {}
        for part in self.sources.rutracker_cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name and value:
                jar[name] = value
        return jar

    def _get(self, url: str) -> requests.Response:
        if not self.configured:
            raise SourceUnreachable(
                "RuTracker не настроен: вставьте в настройках cURL из браузера "
                "(DevTools → Network → Copy as cURL)."
            )
        self.limiter.wait(self.host)
        headers = {}
        if self.sources.rutracker_user_agent:
            headers["User-Agent"] = self.sources.rutracker_user_agent
        proxy = self.sources.rutracker_proxy
        try:
            response = self.session.get(
                url,
                headers=headers,
                cookies=self._cookies(),
                timeout=self.timeout,
                allow_redirects=True,
                proxies={"http": proxy, "https": proxy} if proxy else None,
            )
        except requests.RequestException as exc:
            logger.debug("Запрос к RuTracker не прошёл: {}", exc)
            raise SourceUnreachable(
                f"RuTracker недоступен ({self.host}). Проверьте связь и обход блокировок."
            ) from exc
        self._check(response)
        return response

    @staticmethod
    def _check(response: requests.Response) -> None:
        """Ловит заглушку Cloudflare и чужой ответ до всякого разбора."""
        head = response.content[:4000].decode("utf-8", "ignore")
        if response.status_code == 403 or "Just a moment" in head:
            raise SourceUnreachable(
                "RuTracker закрыт проверкой Cloudflare: куки устарели. "
                "Откройте сайт в браузере и вставьте свежий cURL в настройках."
            )
        if response.status_code != 200:
            raise SourceUnreachable(f"RuTracker вернул HTTP {response.status_code}")

    @staticmethod
    def _require_session(html: str) -> None:
        if "login_username" in html or "Необходимо авторизоваться" in html:
            raise SourceUnreachable(
                "Сессия RuTracker истекла: войдите в браузере и обновите куки в настройках."
            )

    # --- адреса ----------------------------------------------------------

    @staticmethod
    def extract_slug(url: str) -> str:
        match = TOPIC_RE.search(url)
        if not match:
            raise ParseError(f"Не удалось определить тему RuTracker в ссылке: {url}")
        return match.group(1)

    def topic_url(self, topic_id: str) -> str:
        return f"https://{self.host}/forum/viewtopic.php?t={topic_id}"

    def torrent_url(self, topic_id: str) -> str:
        return f"https://{self.host}/forum/dl.php?t={topic_id}"

    def match(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in HOSTS)

    # --- поиск -----------------------------------------------------------

    def search(self, query: str, forums: str = "") -> list[SearchHit]:
        """Поиск по трекеру: `tracker.php?nm=…`, до 500 результатов."""
        query = query.strip()
        if not query:
            return []
        url = f"https://{self.host}/forum/tracker.php?nm={quote(query)}"
        if forums:
            url += f"&f={quote(forums)}"

        html = self._get(url).content.decode(ENCODING, errors="replace")
        self._require_session(html)

        soup = BeautifulSoup(html, "lxml")
        hits = []
        # Идём строками таблицы, а не по data-topic_id: атрибут встречается
        # в разметке дважды на строку и даёт вдвое больше «результатов».
        for row in soup.select("tr.hl-tr[data-topic_id]"):
            hit = self._parse_hit(row)
            if hit is not None:
                hits.append(hit)
        logger.info("RuTracker: по запросу «{}» найдено {}", query, len(hits))
        return hits

    def _parse_hit(self, row) -> SearchHit | None:
        topic_id = row.get("data-topic_id")
        title_node = row.select_one("td.t-title-col a") or row.select_one("a.tLink")
        if not topic_id or title_node is None:
            return None

        category = row.select_one("td.f-name-col a")
        size_node = row.select_one("td.tor-size")
        cells = row.find_all("td")
        return SearchHit(
            topic_id=topic_id,
            title=title_node.get_text(strip=True),
            url=self.topic_url(topic_id),
            category=category.get_text(strip=True) if category else None,
            size_bytes=self._parse_size(size_node.get_text()) if size_node else None,
            # Класс есть не во всех таблицах трекера, поэтому запасной путь —
            # позиция: последние четыре ячейки это S, L, C и дата.
            seeders=self._parse_int(row.select_one(".seedmed") or self._cell(cells, -4)),
            leechers=self._parse_int(row.select_one(".leechmed") or self._cell(cells, -3)),
            added=self._parse_date(cells[-1].get_text()) if cells else None,
        )

    @staticmethod
    def _cell(cells: list, index: int):
        return cells[index] if len(cells) >= abs(index) else None

    # --- одна тема как раздача -------------------------------------------

    def fetch(self, url: str) -> AnimeInfo:
        topic_id = self.extract_slug(url)
        html = self._get(self.topic_url(topic_id)).content.decode(ENCODING, "replace")
        self._require_session(html)

        soup = BeautifulSoup(html, "lxml")
        title_node = soup.select_one("h1.maintitle")
        if title_node is None:
            raise LayoutChanged("На странице темы RuTracker нет заголовка (h1.maintitle)")
        title = title_node.get_text(strip=True)

        magnet_match = MAGNET_RE.search(html)
        magnet = magnet_match.group(0) if magnet_match else None
        # Ключ дедупликации включает info_hash: при перезаливе темы номер тот же,
        # а содержимое другое — как у AniLiberty с пачками.
        info_hash = magnet.split("btih:")[1][:40].lower() if magnet else topic_id

        quality_match = QUALITY_RE.search(title)
        size_node = soup.select_one("#tor-size-humn") or soup.select_one(".attach_link")

        return AnimeInfo(
            title=title,
            url=self.topic_url(topic_id),
            slug=topic_id,
            source=self.name,
            poster_url=None,
            releases=[
                ReleaseInfo(
                    external_id=f"{topic_id}:{info_hash}",
                    episode_raw=title,
                    title=title,
                    quality=quality_match.group(0) if quality_match else None,
                    size_bytes=self._parse_size(size_node.get_text()) if size_node else None,
                    seeders=self._parse_int(soup.select_one(".seed")),
                    leechers=self._parse_int(soup.select_one(".leech")),
                    torrent_url=self.torrent_url(topic_id),
                    magnet=magnet,
                )
            ],
        )

    def download_torrent(self, release: ReleaseInfo) -> bytes:
        url = release.torrent_url
        if not url:
            raise ParseError(f"У раздачи {release.external_id} нет ссылки на torrent")
        response = self._get(url)
        content_type = response.headers.get("content-type", "")
        # Вместо файла может прийти HTML с ошибкой — ловим сразу, как у astar.
        if "bittorrent" not in content_type and not response.content.startswith(b"d"):
            raise ParseError(f"Вместо .torrent получен {content_type or 'неизвестный тип'}")
        return response.content

    # --- разбор ----------------------------------------------------------

    @staticmethod
    def _parse_size(text: str) -> int | None:
        match = SIZE_RE.search(text or "")
        if not match:
            return None
        return int(float(match.group(1).replace(",", ".")) * UNITS[match.group(2).lower()])

    @staticmethod
    def _parse_int(node) -> int | None:
        if node is None:
            return None
        digits = re.sub(r"\D", "", node.get_text(strip=True))
        return int(digits) if digits else None

    @staticmethod
    def _parse_date(text: str) -> date | None:
        match = DATE_RE.search(text or "")
        if not match:
            return None
        month = MONTHS.get(match.group(2).lower()[:3])
        if not month:
            return None
        year = int(match.group(3))
        return date(2000 + year if year < 100 else year, month, int(match.group(1)))
