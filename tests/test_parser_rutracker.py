"""Парсер RuTracker на сохранённых страницах — без сети.

Снимки сняты с живого сайта 07.09.2026 и вычищены от кук и имени пользователя.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import requests
import responses

from atsm.config import Settings
from atsm.parsers import ParseError, SourceUnreachable
from atsm.parsers.rutracker import RuTrackerParser

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_HTML = FIXTURES / "rutracker_search.html"
TOPIC_HTML = FIXTURES / "rutracker_topic.html"
TOPIC_URL = "https://rutracker.org/forum/viewtopic.php?t=6879526"


@pytest.fixture
def settings() -> Settings:
    settings = Settings()
    settings.sources.rutracker_cookies = "bb_session=тест; cf_clearance=тест"
    settings.sources.rutracker_user_agent = "Mozilla/5.0 (проверка)"
    return settings


@pytest.fixture
def parser(settings: Settings) -> RuTrackerParser:
    return RuTrackerParser(session=requests.Session(), sources=settings.sources)


class TestSearch:
    @responses.activate
    def test_finds_all_rows_once(self, parser: RuTrackerParser) -> None:
        """48 результатов, а не 96: data-topic_id встречается в строке дважды."""
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body=SEARCH_HTML.read_bytes(),
        )
        hits = parser.search("Дьявол носит Prada")

        assert len(hits) == 48
        assert len({hit.topic_id for hit in hits}) == 48

    @responses.activate
    def test_hit_fields(self, parser: RuTrackerParser) -> None:
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body=SEARCH_HTML.read_bytes(),
        )
        hit = parser.search("Дьявол носит Prada")[0]

        assert hit.topic_id == "6879526"
        assert "Дьявол носит Prada" in hit.title
        assert hit.category == "Зарубежное кино (UHD Video)"
        assert hit.size_bytes == 20_186_346_291
        assert hit.seeders and hit.leechers is not None
        assert hit.added == date(2026, 7, 26)
        assert hit.url.endswith("viewtopic.php?t=6879526")

    @responses.activate
    def test_query_goes_into_request(self, parser: RuTrackerParser) -> None:
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body=SEARCH_HTML.read_bytes(),
        )
        parser.search("Дьявол")
        assert "nm=%D0%94" in responses.calls[0].request.url

    def test_empty_query_does_not_touch_network(self, parser: RuTrackerParser) -> None:
        assert parser.search("   ") == []


class TestTopic:
    @responses.activate
    def test_topic_becomes_single_release(self, parser: RuTrackerParser) -> None:
        responses.get(
            "https://rutracker.org/forum/viewtopic.php",
            body=TOPIC_HTML.read_bytes(),
        )
        info = parser.fetch(TOPIC_URL)

        assert info.source == "rutracker"
        assert info.slug == "6879526"
        assert "Дьявол носит Prada" in info.title
        assert len(info.releases) == 1

        release = info.releases[0]
        # Ключ дедупликации — тема плюс хеш: при перезаливе номер темы тот же.
        assert release.external_id.startswith("6879526:")
        assert len(release.external_id.split(":")[1]) == 40
        assert release.magnet and release.magnet.startswith("magnet:?xt=urn:btih:")
        assert release.quality
        assert release.torrent_url.endswith("dl.php?t=6879526")

    def test_bad_url_rejected(self, parser: RuTrackerParser) -> None:
        with pytest.raises(ParseError):
            parser.extract_slug("https://rutracker.org/forum/index.php")

    def test_topic_id_survives_extra_params(self, parser: RuTrackerParser) -> None:
        assert parser.extract_slug("https://rutracker.org/forum/viewtopic.php?t=42&start=50") == "42"


class TestAccessDiagnostics:
    def test_without_cookies_says_what_to_do(self, settings: Settings) -> None:
        settings.sources.rutracker_cookies = ""
        parser = RuTrackerParser(session=requests.Session(), sources=settings.sources)

        with pytest.raises(SourceUnreachable, match="Copy as cURL"):
            parser.search("что угодно")

    @responses.activate
    def test_cloudflare_asks_for_fresh_cookies(self, parser: RuTrackerParser) -> None:
        """403 с заглушкой — это устаревшие куки, а не поломка парсера."""
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body="<html><body>Just a moment...</body></html>",
            status=403,
        )
        with pytest.raises(SourceUnreachable, match="Cloudflare"):
            parser.search("Дьявол")

    @responses.activate
    def test_login_page_means_session_expired(self, parser: RuTrackerParser) -> None:
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body="<html><form><input name='login_username'></form></html>",
        )
        with pytest.raises(SourceUnreachable, match="истекла"):
            parser.search("Дьявол")

    @responses.activate
    def test_html_instead_of_torrent_is_caught(self, parser: RuTrackerParser) -> None:
        from atsm.parsers.base import ReleaseInfo

        responses.get(
            "https://rutracker.org/forum/dl.php",
            body="<html>ошибка</html>",
            content_type="text/html",
        )
        release = ReleaseInfo(
            external_id="1",
            episode_raw="фильм",
            torrent_url="https://rutracker.org/forum/dl.php?t=1",
        )
        with pytest.raises(ParseError, match="Вместо .torrent"):
            parser.download_torrent(release)

    @responses.activate
    def test_cookies_and_user_agent_are_sent(self, parser: RuTrackerParser) -> None:
        """cf_clearance привязан к UA, поэтому оба обязаны уходить в запрос."""
        responses.get(
            "https://rutracker.org/forum/tracker.php",
            body=SEARCH_HTML.read_bytes(),
        )
        parser.search("Дьявол")

        request = responses.calls[0].request
        assert request.headers["User-Agent"] == "Mozilla/5.0 (проверка)"
        assert "cf_clearance" in request.headers["Cookie"]


def test_russian_month_dates() -> None:
    """«26-Июл-26» — strptime такое не разбирает, поэтому свой разбор."""
    assert RuTrackerParser._parse_date("26-Июл-26") == date(2026, 7, 26)
    assert RuTrackerParser._parse_date("1-Янв-25") == date(2025, 1, 1)
    assert RuTrackerParser._parse_date("31-Дек-2024") == date(2024, 12, 31)
    assert RuTrackerParser._parse_date("вчера") is None


def test_size_parsing() -> None:
    assert RuTrackerParser._parse_size("18.8 GB ↓") == int(18.8 * 1024**3)
    assert RuTrackerParser._parse_size("700 MB") == 700 * 1024**2
    assert RuTrackerParser._parse_size("нет") is None
