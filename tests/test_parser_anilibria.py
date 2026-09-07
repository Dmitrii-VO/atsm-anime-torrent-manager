"""Парсер AniLibria на сохранённом ответе API — без сети."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from atsm.config import Settings
from atsm.parsers import LayoutChanged, ParseError, ParserRegistry
from atsm.parsers.anilibria import AniLibriaParser

FIXTURE = Path(__file__).parent / "fixtures" / "anilibria_release.json"
ALIAS = "gaikotsu-kishi-sama-tadaima-isekai-e-odekakechuu-ii"
PAGE_URL = f"https://aniliberty.top/anime/releases/release/{ALIAS}/"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def parser() -> AniLibriaParser:
    return AniLibriaParser(session=None, sources=Settings().sources)


@pytest.fixture
def info(parser: AniLibriaParser, payload: dict):
    return parser.parse(payload, ALIAS)


def test_release_parsed(info) -> None:
    assert info.title == "Рыцарь-скелет вступает в параллельный мир 2"
    assert info.source == "anilibria"
    assert info.slug == ALIAS
    assert info.status == "ongoing"
    assert info.url == PAGE_URL
    assert info.poster_url.startswith("https://aniliberty.top/storage/")


def test_two_quality_tracks(info) -> None:
    """Главная причина брать этот источник: несколько раздач на одни серии."""
    assert len(info.releases) == 2
    qualities = {r.quality for r in info.releases}
    assert any("AVC" in q for q in qualities)
    assert any("HEVC" in q for q in qualities)
    # На astar поле качества всегда пустое — здесь оно наконец заполнено.
    assert all(r.quality for r in info.releases)


def test_magnet_and_torrent_file(info) -> None:
    for release in info.releases:
        assert release.magnet.startswith("magnet:?xt=urn:btih:")
        assert release.torrent_url.endswith("/file")


def test_episode_ranges(info) -> None:
    ranges = {(r.episode, r.episode_end) for r in info.releases}
    assert ranges == {(1, 5), (1, 4)}


def test_sorted_by_latest_episode(info) -> None:
    """Свежая пачка «1-5» должна идти выше отставшей «1-4»."""
    assert info.releases[0].episode_end == 5


def test_external_id_includes_hash(info) -> None:
    """Раздача обновляется на месте: id прежний, hash новый.

    Без hash в ключе рост пачки «1-4» → «1-5» не был бы замечен как новинка.
    """
    for release in info.releases:
        torrent_id, _, info_hash = release.external_id.partition(":")
        assert torrent_id.isdigit()
        assert len(info_hash) == 40


def test_growing_pack_is_detected_as_new(parser: AniLibriaParser, payload: dict) -> None:
    before = parser.parse(payload, ALIAS)
    known = {r.external_id for r in before.releases}

    # Имитируем выход новой серии: тот же id, новый hash и диапазон.
    grown = json.loads(json.dumps(payload))
    grown["torrents"][0]["description"] = "1-6"
    grown["torrents"][0]["hash"] = "a" * 40

    after = parser.parse(grown, ALIAS)
    fresh = [r for r in after.releases if r.external_id not in known]

    assert len(fresh) == 1
    assert fresh[0].episode_end == 6


def test_size_and_stats(info) -> None:
    latest = info.releases[0]
    assert latest.size_bytes == 7484211905
    assert latest.seeders is not None
    assert latest.published_at is None or isinstance(latest.published_at, date)


def test_release_without_torrents(parser: AniLibriaParser, payload: dict) -> None:
    empty = {**payload, "torrents": []}
    with pytest.raises(LayoutChanged):
        parser.parse(empty, ALIAS)


def test_copyright_block_is_explained(parser: AniLibriaParser, payload: dict) -> None:
    blocked = {**payload, "torrents": [], "is_blocked_by_copyrights": True}
    with pytest.raises(LayoutChanged, match="правообладателем"):
        parser.parse(blocked, ALIAS)


def test_broken_payload(parser: AniLibriaParser) -> None:
    with pytest.raises(ParseError):
        parser.parse({"что-то": "не то"}, ALIAS)


class TestUrls:
    @pytest.mark.parametrize(
        "url",
        [
            "https://aniliberty.top/anime/releases/release/some-alias/",
            "https://aniliberty.top/anime/releases/release/some-alias",
            "https://aniliberty.top/anime/releases/release/some-alias/episodes",
            "https://aniliberty.top/anime/releases/release/some-alias?x=1",
            # Старый домен: страницы редиректят на новый, но ссылка должна работать.
            "https://anilibria.top/anime/releases/release/some-alias/",
        ],
    )
    def test_alias_extracted(self, parser: AniLibriaParser, url: str) -> None:
        assert parser.extract_slug(url) == "some-alias"

    def test_bad_url(self, parser: AniLibriaParser) -> None:
        with pytest.raises(ParseError):
            parser.extract_slug("https://aniliberty.top/anime/releases/")

    def test_both_domains_routed(self) -> None:
        """Проект переехал с anilibria.top на aniliberty.top — работать
        должны обе ссылки, иначе старые подписки отвалятся."""
        registry = ParserRegistry(Settings())
        for host in ("aniliberty.top", "anilibria.top"):
            url = f"https://{host}/anime/releases/release/bleach/episodes"
            assert registry.for_url(url).name == "anilibria"

    def test_host_from_link_is_tried_first(self, parser: AniLibriaParser) -> None:
        hosts = parser.hosts_to_try("https://anilibria.top/anime/releases/release/x/")
        assert hosts[0] == "anilibria.top"
        assert "aniliberty.top" in hosts
        assert len(hosts) == len(set(hosts))

    def test_registry_routes_by_domain(self) -> None:
        registry = ParserRegistry(Settings())
        assert registry.for_url(PAGE_URL).name == "anilibria"
        assert registry.for_url("https://v19.astar.bz/7788-x.html").name == "astar"

    def test_all_sources_registered(self) -> None:
        names = {p.name for p in ParserRegistry(Settings()).all()}
        assert names == {"astar", "anilibria", "rutracker"}


def test_range_parsing(parser: AniLibriaParser) -> None:
    assert parser._parse_range("1-5") == (1, 5)
    assert parser._parse_range("7") == (7, None)
    assert parser._parse_range("Серии 10-12") == (10, 12)
    assert parser._parse_range(None) == (None, None)
    assert parser._parse_range("фильм") == (None, None)
