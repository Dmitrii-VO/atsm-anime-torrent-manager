"""Канарейки справочников: ходят на живые API. Запуск: pytest -m network"""

from __future__ import annotations

import pytest

from atsm.metadata import AniListProvider, ShikimoriProvider

pytestmark = pytest.mark.network


def test_shikimori_finds_by_russian_title() -> None:
    """Ключевое допущение: подписки заводятся с русским названием с astar."""
    found = ShikimoriProvider().search("Пожиратель звёзд", limit=5)

    assert found, "Shikimori ничего не вернул"
    assert any(c.title_ru == "Пожиратель звёзд" for c in found)
    assert all(c.external_id.isdigit() for c in found)


def test_shikimori_detail_fields_alive() -> None:
    meta = ShikimoriProvider().fetch("44218")

    assert meta.title_ru == "Пожиратель звёзд"
    assert meta.title_romaji
    assert meta.poster_url and meta.poster_url.startswith("https://")
    assert meta.genres


def test_anilist_gives_airing_time_for_ongoing() -> None:
    """Ради этого AniList и добавлен: точное время выхода серии."""
    meta = AniListProvider().fetch_by_title("One Piece")

    assert meta is not None
    assert meta.status == "Онгоинг"
    assert meta.next_episode_number and meta.next_episode_at


def test_episode_numbering_differs_from_tracker() -> None:
    """Напоминание в виде теста: у справочника своя нумерация.

    На astar у «Пожирателя звёзд» 235 раздач, потому что дунхуа нумеруется
    сквозняком, а справочники разбивают его по сезонам. Считать эти числа
    прогрессом подписки нельзя.
    """
    meta = ShikimoriProvider().fetch("44218")
    assert meta.episodes_total and meta.episodes_total < 100
