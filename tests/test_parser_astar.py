"""Парсер astar на сохранённом снимке страницы — без сети."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from atsm.config import Settings
from atsm.parsers import LayoutChanged, ParseError, ParserRegistry, SourceUnreachable
from atsm.parsers.astar import AstarParser

FIXTURE = Path(__file__).parent / "fixtures" / "astar_7788.html"
CHALLENGE = Path(__file__).parent / "fixtures" / "astar_challenge.html"
PAGE_URL = "https://v19.astar.bz/7788-pozhiratel-zvezd-swallowed-star.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_bytes().decode("windows-1251")


@pytest.fixture
def parser() -> AstarParser:
    settings = Settings()
    return AstarParser(session=None, sources=settings.sources)


@pytest.fixture
def info(parser: AstarParser, html: str):
    return parser.parse(html, base_url=PAGE_URL)


def test_all_releases_found(info) -> None:
    assert len(info.releases) == 235


def test_title_is_first_variant(info) -> None:
    assert info.title == "Пожиратель звёзд"


def test_slug_and_poster(info) -> None:
    assert info.slug == "7788-pozhiratel-zvezd-swallowed-star.html"
    assert info.poster_url.endswith("/uploads/posters/7788/original.jpg")


def test_latest_release_fields(info) -> None:
    """Эталон по снимку страницы: «Серия 235 (844.05 Mb)», дата 03-08-2026."""
    latest = info.releases[0]
    assert latest.external_id == "46219"
    assert latest.episode == 235
    assert latest.episode_end is None
    assert latest.episode_raw == "Серия 235 (844.05 Mb)"
    assert latest.size_bytes == int(844.05 * 1024**2)
    assert latest.seeders == 107
    assert latest.leechers == 4
    assert latest.downloads == 1949
    assert latest.published_at == date(2026, 8, 3)
    assert latest.torrent_url == "https://v19.astar.bz/engine/gettorrent.php?id=46219"
    # Источник magnet не отдаёт и качество не публикует (ТЗ §26).
    assert latest.magnet is None
    assert latest.quality is None


def test_releases_sorted_by_episode_desc(info) -> None:
    numbered = [r.episode for r in info.releases if r.episode is not None]
    assert numbered[:3] == [235, 234, 233]
    assert numbered == sorted(numbered, reverse=True)


def test_unnumbered_releases_go_last(info) -> None:
    """Сборники без распознанного номера не должны всплывать над свежими сериями."""
    tail = [r.episode for r in info.releases[-1:]]
    assert tail == [None] or info.releases[-1].episode is not None


def test_episode_ranges_parsed(info) -> None:
    """На странице есть сборники: «Серии 27-28», «Серии 1-2», «Фильмы 1-4»."""
    by_id = {r.external_id: r for r in info.releases}

    pack = by_id["27179"]
    assert pack.episode_raw.startswith("Серии 27-28")
    assert (pack.episode, pack.episode_end) == (27, 28)

    first_pack = by_id["21420"]
    assert (first_pack.episode, first_pack.episode_end) == (1, 2)

    # «Фильмы 1-4» — не серии, номер не присваивается.
    films = by_id["34477"]
    assert films.episode is None and films.episode_end is None
    assert films.size_bytes == int(14.32 * 1024**3)


def test_external_ids_are_unique(info) -> None:
    ids = [r.external_id for r in info.releases]
    assert len(set(ids)) == len(ids)


def test_every_release_has_torrent_url(info) -> None:
    assert all(r.torrent_url and "gettorrent.php" in r.torrent_url for r in info.releases)


def test_empty_page_reports_layout_changed(parser: AstarParser) -> None:
    with pytest.raises(LayoutChanged):
        parser.parse("<html><body><h1>Пожиратель звёзд</h1></body></html>", base_url=PAGE_URL)


def test_browser_check_is_not_a_layout_change(parser: AstarParser) -> None:
    """Заглушка «Проверка безопасности» — сайт закрыт, а не переверстан.

    Иначе подписка помечается «изменена структура сайта», и пользователь идёт
    чинить парсер вместо того, чтобы подождать.
    """
    import re

    import requests
    import responses

    parser.session = requests.Session()
    with responses.RequestsMock() as mock:
        mock.add(
            responses.GET,
            re.compile(r"https://.*astar\.bz/.*"),
            body=CHALLENGE.read_bytes(),
            status=200,
        )
        with pytest.raises(SourceUnreachable, match="проверку браузера"):
            parser.fetch(PAGE_URL)


def test_slug_extraction_survives_mirror_change(parser: AstarParser) -> None:
    assert parser.extract_slug(PAGE_URL) == parser.extract_slug(
        "https://v30.astar.bz/7788-pozhiratel-zvezd-swallowed-star.html"
    )


def test_bad_url_rejected(parser: AstarParser) -> None:
    with pytest.raises(ParseError):
        parser.extract_slug("https://v19.astar.bz/")


def test_mirror_order_prefers_url_then_settings(parser: AstarParser) -> None:
    hosts = parser.hosts_to_try(PAGE_URL)
    assert hosts[0] == "v19.astar.bz"
    assert "v30.astar.bz" in hosts
    assert len(hosts) == len(set(hosts))


class TestRegistry:
    def test_finds_astar_by_any_mirror(self) -> None:
        registry = ParserRegistry(Settings())
        for url in (
            "https://v19.astar.bz/7788-x.html",
            "https://v30.astar.bz/7788-x.html",
            "http://astar.bz/7788-x.html",
        ):
            assert registry.for_url(url).name == "astar"

    def test_rejects_unknown_source(self) -> None:
        registry = ParserRegistry(Settings())
        assert not registry.supports("https://nyaa.si/view/1")
        with pytest.raises(Exception, match="не относится"):
            registry.for_url("https://nyaa.si/view/1")
