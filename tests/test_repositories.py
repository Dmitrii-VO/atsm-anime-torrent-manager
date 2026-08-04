from __future__ import annotations

from datetime import date

import pytest

from atsm.core.models import HistoryAction, ReleaseState, SourceState
from atsm.db.repositories import Repositories
from atsm.parsers.base import ReleaseInfo


@pytest.fixture
def repos(db) -> Repositories:
    return Repositories(db)


@pytest.fixture
def anime_id(repos: Repositories) -> int:
    return repos.anime.add(
        title="Пожиратель звёзд",
        source="astar",
        url="https://v30.astar.bz/7788-x.html",
        slug="7788-x.html",
    )


def make_release(external_id: str, episode: int | None = None, **kwargs) -> ReleaseInfo:
    return ReleaseInfo(
        external_id=external_id,
        episode=episode,
        episode_raw=f"Серия {episode}",
        size_bytes=kwargs.pop("size_bytes", 884_998_144),
        seeders=kwargs.pop("seeders", 10),
        published_at=kwargs.pop("published_at", date(2026, 8, 3)),
        torrent_url=f"https://v30.astar.bz/engine/gettorrent.php?id={external_id}",
        **kwargs,
    )


class TestAnimeRepo:
    def test_add_and_get(self, repos: Repositories, anime_id: int) -> None:
        anime = repos.anime.get(anime_id)
        assert anime.title == "Пожиратель звёзд"
        assert anime.auto_download is False
        assert anime.new_count == 0

    def test_lookup_by_slug_ignores_mirror(self, repos: Repositories, anime_id: int) -> None:
        assert repos.anime.get_by_slug("astar", "7788-x.html").id == anime_id

    def test_flags_and_check_status(self, repos: Repositories, anime_id: int) -> None:
        repos.anime.set_flag(anime_id, "auto_download", True)
        repos.anime.set_flag(anime_id, "is_favorite", True)
        repos.anime.mark_checked(anime_id, ok=False, error="Сайт недоступен")

        anime = repos.anime.get(anime_id)
        assert anime.auto_download and anime.is_favorite
        assert anime.last_check_ok is False
        assert anime.last_error == "Сайт недоступен"
        assert anime.last_check_at is not None

    def test_rejects_unknown_flag(self, repos: Repositories, anime_id: int) -> None:
        with pytest.raises(ValueError):
            repos.anime.set_flag(anime_id, "title", True)

    def test_favorites_first(self, repos: Repositories, anime_id: int) -> None:
        other = repos.anime.add(title="AAA", source="astar", url="u2", slug="s2")
        repos.anime.set_flag(anime_id, "is_favorite", True)
        assert [a.id for a in repos.anime.list()] == [anime_id, other]


class TestReleaseRepo:
    def test_archive_import_is_silent(self, repos: Repositories, anime_id: int) -> None:
        """Первичный импорт не должен наполнять ленту (ТЗ §5)."""
        repos.releases.add_many(anime_id, [make_release("1", 1), make_release("2", 2)], seen=True)
        assert repos.releases.feed() == []
        assert repos.anime.get(anime_id).new_count == 0

    def test_new_releases_reach_feed(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("1", 1)], seen=True)
        repos.releases.add_many(anime_id, [make_release("2", 2)], seen=False)

        feed = repos.releases.feed()
        assert [r.external_id for r in feed] == ["2"]
        assert feed[0].anime_title == "Пожиратель звёзд"
        assert repos.releases.feed_count() == 1
        assert repos.anime.get(anime_id).new_count == 1

    def test_duplicate_insert_ignored(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("1", 1)], seen=False)
        repos.releases.add_many(anime_id, [make_release("1", 1)], seen=False)
        assert len(repos.releases.list_for_anime(anime_id)) == 1

    def test_known_ids(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("7", 7), make_release("8", 8)], seen=True)
        assert repos.releases.known_external_ids(anime_id) == {"7", "8"}

    def test_stats_refresh(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("1", 1, seeders=5)], seen=True)
        repos.releases.update_stats(anime_id, [make_release("1", 1, seeders=99)])
        assert repos.releases.list_for_anime(anime_id)[0].seeders == 99

    def test_sorting_puts_unnumbered_last(self, repos: Repositories, anime_id: int) -> None:
        packs = ReleaseInfo(external_id="99", episode=None, episode_raw="Фильмы 1-4")
        repos.releases.add_many(
            anime_id, [make_release("1", 1), packs, make_release("5", 5)], seen=True
        )
        assert [r.episode for r in repos.releases.list_for_anime(anime_id)] == [5, 1, None]

    def test_state_transitions(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("1", 1)], seen=False)
        release = repos.releases.feed()[0]

        repos.releases.set_state(release.id, ReleaseState.SENT, "в qBittorrent", seen=True)
        updated = repos.releases.get(release.id)
        assert updated.state == ReleaseState.SENT
        assert updated.is_seen is True
        assert repos.releases.feed() == []

    def test_error_state_stays_in_feed(self, repos: Repositories, anime_id: int) -> None:
        """Неудачную отправку пользователь должен видеть и мочь повторить."""
        repos.releases.add_many(anime_id, [make_release("1", 1)], seen=False)
        release = repos.releases.feed()[0]
        repos.releases.set_state(release.id, ReleaseState.ERROR, "нет связи")
        assert [r.id for r in repos.releases.feed()] == [release.id]

    def test_mark_all_seen(self, repos: Repositories, anime_id: int) -> None:
        repos.releases.add_many(anime_id, [make_release("1", 1), make_release("2", 2)], seen=False)
        repos.releases.mark_all_seen(anime_id)
        assert repos.releases.feed_count() == 0

    def test_size_and_episode_labels(self, repos: Repositories, anime_id: int) -> None:
        pack = ReleaseInfo(
            external_id="3", episode=27, episode_end=28, episode_raw="Серии 27-28", size_bytes=None
        )
        repos.releases.add_many(anime_id, [make_release("1", 1), pack], seen=True)
        releases = {r.external_id: r for r in repos.releases.list_for_anime(anime_id)}
        assert releases["1"].size_label == "844.00 МБ"
        assert releases["3"].episode_label == "Серии 27-28"
        assert releases["3"].size_label == "—"


class TestHistoryAndSources:
    def test_history_is_newest_first(self, repos: Repositories, anime_id: int) -> None:
        repos.history.log(HistoryAction.CHECK, "проверка", anime_id=anime_id)
        repos.history.log(HistoryAction.FOUND, "новая серия", anime_id=anime_id)

        entries = repos.history.recent()
        assert entries[0].action == HistoryAction.FOUND
        assert entries[0].anime_title == "Пожиратель звёзд"

    def test_source_status_upsert(self, repos: Repositories) -> None:
        repos.sources.set("astar", SourceState.OK)
        repos.sources.set("astar", SourceState.LAYOUT_CHANGED, "нет блоков раздач")

        status = repos.sources.all()["astar"]
        assert status["state"] == SourceState.LAYOUT_CHANGED
        assert status["message"] == "нет блоков раздач"
        assert status["checked_at"] is not None
