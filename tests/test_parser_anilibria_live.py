"""Канарейки AniLibria: ходят на живое API. Запуск: pytest -m network"""

from __future__ import annotations

import pytest
import requests

from atsm.config import Settings
from atsm.parsers import ParserRegistry

pytestmark = pytest.mark.network

CATALOG = "https://anilibria.top/api/v1/anime/catalog/releases"


@pytest.fixture(scope="module")
def alias() -> str:
    """Берём свежий релиз из каталога, а не прибитый гвоздями — он может уйти."""
    response = requests.get(
        CATALOG, params={"limit": 1}, headers={"User-Agent": "ATSM/0.1"}, timeout=25
    )
    response.raise_for_status()
    return response.json()["data"][0]["alias"]


@pytest.fixture(scope="module")
def registry() -> ParserRegistry:
    return ParserRegistry(Settings())


def test_live_release_parses(registry: ParserRegistry, alias: str) -> None:
    url = f"https://anilibria.top/anime/releases/release/{alias}/"
    parser = registry.for_url(url)
    info = parser.fetch(url)

    assert info.title
    assert info.releases
    latest = info.releases[0]
    assert latest.magnet and latest.magnet.startswith("magnet:")
    assert latest.quality  # именно этого нет у astar
    assert latest.size_bytes


def test_old_api_is_gone() -> None:
    """API v3 снято. Если ответ изменится — значит, сервис снова переехал."""
    response = requests.get(
        "https://api.anilibria.tv/v3/title/search",
        params={"search": "test", "limit": 1},
        headers={"User-Agent": "ATSM/0.1"},
        timeout=20,
    )
    assert response.status_code == 410


def test_torrent_file_downloads(registry: ParserRegistry, alias: str) -> None:
    url = f"https://anilibria.top/anime/releases/release/{alias}/"
    parser = registry.for_url(url)
    info = parser.fetch(url)

    data = parser.download_torrent(info.releases[0])
    assert data.startswith(b"d")  # bencode
    assert len(data) > 500
