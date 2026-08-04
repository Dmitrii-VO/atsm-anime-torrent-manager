"""Канарейка: ходит на живой сайт и ловит смену вёрстки (ТЗ §21).

По умолчанию отключена. Запуск: pytest -m network
"""

from __future__ import annotations

import pytest

from atsm.config import Settings
from atsm.parsers import ParserRegistry

PAGE_URL = "https://v19.astar.bz/7788-pozhiratel-zvezd-swallowed-star.html"

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def registry() -> ParserRegistry:
    return ParserRegistry(Settings())


def test_live_page_still_parses(registry: ParserRegistry) -> None:
    parser = registry.for_url(PAGE_URL)
    info = parser.fetch(PAGE_URL)

    assert info.title
    assert len(info.releases) > 100
    latest = info.releases[0]
    assert latest.episode and latest.episode > 200
    assert latest.torrent_url and latest.size_bytes


def test_mirror_is_resolved(registry: ParserRegistry) -> None:
    """Ссылка на старое зеркало должна отработать через актуальный домен."""
    parser = registry.for_url(PAGE_URL)
    info = parser.fetch(PAGE_URL)
    assert info.slug == "7788-pozhiratel-zvezd-swallowed-star.html"


def test_torrent_downloads_without_auth(registry: ParserRegistry) -> None:
    parser = registry.for_url(PAGE_URL)
    info = parser.fetch(PAGE_URL)
    data = parser.download_torrent(info.releases[0])
    assert data.startswith(b"d")  # bencode
    assert len(data) > 1000
