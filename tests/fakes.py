"""Заглушки источника для тестов ядра — без сети и без реального сайта."""

from __future__ import annotations

from datetime import date

from atsm.parsers.base import AnimeInfo, BaseParser, ParserError, ReleaseInfo


class FakeParser(BaseParser):
    name = "fake"
    display_name = "Fake"
    domains = ("example.test",)

    def __init__(self, releases: list[ReleaseInfo] | None = None) -> None:
        self.releases = releases if releases is not None else []
        self.title = "Пожиратель звёзд"
        self.error: Exception | None = None
        self.fetch_calls = 0
        self.downloaded: list[str] = []
        self.session = None
        self.timeout = 5

    # --- управление поведением в тесте ---
    def set_releases(self, releases: list[ReleaseInfo]) -> None:
        self.releases = releases

    def fail_with(self, error: Exception) -> None:
        self.error = error

    # --- контракт BaseParser ---
    @staticmethod
    def extract_slug(url: str) -> str:
        return url.rstrip("/").rsplit("/", 1)[-1]

    def fetch(self, url: str) -> AnimeInfo:
        self.fetch_calls += 1
        if self.error:
            raise self.error
        return AnimeInfo(
            title=self.title,
            url=url,
            slug=self.extract_slug(url),
            source=self.name,
            poster_url=None,
            releases=list(self.releases),
        )

    def download_torrent(self, release: ReleaseInfo) -> bytes:
        if self.error:
            raise self.error
        self.downloaded.append(release.external_id)
        return b"d8:announce4:test4:infod4:name4:testee"


class FakeRegistry:
    def __init__(self, parser: FakeParser) -> None:
        self.parser = parser

    def for_url(self, url: str) -> FakeParser:
        if not self.parser.match(url):
            raise ParserError("Ссылка не относится ни к одному поддерживаемому источнику")
        return self.parser

    def get(self, name: str) -> FakeParser:
        if name != self.parser.name:
            raise ParserError(f"Источник «{name}» не поддерживается")
        return self.parser

    def supports(self, url: str) -> bool:
        return self.parser.match(url)

    def all(self) -> list[FakeParser]:
        return [self.parser]


def release(external_id: str, episode: int | None, **kwargs) -> ReleaseInfo:
    return ReleaseInfo(
        external_id=external_id,
        episode=episode,
        episode_raw=f"Серия {episode}",
        size_bytes=kwargs.pop("size_bytes", 884_998_144),
        seeders=kwargs.pop("seeders", 10),
        published_at=kwargs.pop("published_at", date(2026, 8, 3)),
        torrent_url=f"https://example.test/engine/gettorrent.php?id={external_id}",
        **kwargs,
    )


URL = "https://example.test/7788-pozhiratel.html"
