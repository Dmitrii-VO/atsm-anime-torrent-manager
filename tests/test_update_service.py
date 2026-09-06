from __future__ import annotations

import pytest

from atsm.core.models import ReleaseState, SourceState
from atsm.core.subscription_service import SubscriptionService
from atsm.core.update_service import UpdateService
from atsm.db.repositories import Repositories
from atsm.parsers.base import LayoutChanged, SourceUnreachable
from atsm.torrent.base import TorrentClientError

from .fakes import URL, FakeParser, FakeRegistry, release


class FakeTorrentService:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.error: Exception | None = None

    def send(self, rel) -> bool:
        if self.error:
            raise self.error
        self.sent.append(rel.external_id)
        return True


@pytest.fixture
def repos(db) -> Repositories:
    return Repositories(db)


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser([release("1", 1), release("2", 2)])


@pytest.fixture
def registry(parser: FakeParser) -> FakeRegistry:
    return FakeRegistry(parser)


@pytest.fixture
def torrent() -> FakeTorrentService:
    return FakeTorrentService()


@pytest.fixture
def service(repos, registry, torrent) -> UpdateService:
    return UpdateService(repos, registry, torrent_service=torrent)


@pytest.fixture
def anime(repos, registry):
    return SubscriptionService(repos, registry).add(URL)


def test_no_changes_means_no_new(service, anime, parser) -> None:
    result = service.check_anime(anime)
    assert result.new_releases == []
    assert result.ok


def test_single_new_episode_detected(service, anime, parser, repos) -> None:
    parser.set_releases([release("3", 3), release("2", 2), release("1", 1)])

    result = service.check_anime(anime)

    assert [r.external_id for r in result.new_releases] == ["3"]
    assert repos.releases.feed_count() == 1
    assert repos.releases.feed()[0].episode == 3


def test_repeated_check_does_not_duplicate(service, anime, parser, repos) -> None:
    parser.set_releases([release("3", 3), release("2", 2), release("1", 1)])
    service.check_anime(anime)
    second = service.check_anime(repos.anime.get(anime.id))

    assert second.new_releases == []
    assert len(repos.releases.list_for_anime(anime.id)) == 3
    assert repos.releases.feed_count() == 1


def test_new_release_logged_to_history(service, anime, parser, repos) -> None:
    parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])
    service.check_anime(anime)
    assert any("Найдена новая раздача" in (e.message or "") for e in repos.history.recent())


def test_seeders_refreshed_for_known_releases(service, anime, parser, repos) -> None:
    parser.set_releases([release("1", 1, seeders=777), release("2", 2)])
    service.check_anime(anime)
    stored = {r.external_id: r for r in repos.releases.list_for_anime(anime.id)}
    assert stored["1"].seeders == 777


def test_external_fields_refreshed_for_known_release(service, anime, parser, repos) -> None:
    changed = release(
        "1",
        1,
        quality="1080p HEVC",
        torrent_url="https://new-mirror.example/1.torrent",
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
    )
    parser.set_releases([changed, release("2", 2)])

    service.check_anime(anime)

    stored = {r.external_id: r for r in repos.releases.list_for_anime(anime.id)}["1"]
    assert stored.quality == "1080p HEVC"
    assert stored.torrent_url == "https://new-mirror.example/1.torrent"
    assert stored.magnet == changed.magnet
    assert stored.is_seen is True
    assert stored.state == ReleaseState.NEW


def test_pack_release_is_detected_as_new(service, anime, parser, repos) -> None:
    """Сборник без номера серии тоже новинка — диффинг идёт по external_id."""
    from atsm.parsers.base import ReleaseInfo

    parser.set_releases(
        [release("1", 1), release("2", 2), ReleaseInfo("99", "Фильмы 1-4", episode=None)]
    )
    result = service.check_anime(anime)
    assert [r.external_id for r in result.new_releases] == ["99"]


class TestAutoDownload:
    def test_disabled_by_default(self, service, anime, parser, torrent) -> None:
        parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])
        result = service.check_anime(anime)
        assert torrent.sent == []
        assert result.sent == 0

    def test_enabled_sends_new_releases(self, service, anime, parser, torrent, repos) -> None:
        repos.anime.set_flag(anime.id, "auto_download", True)
        parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])

        result = service.check_anime(repos.anime.get(anime.id))

        assert torrent.sent == ["3"]
        assert result.sent == 1

    def test_client_failure_does_not_break_check(
        self, service, anime, parser, torrent, repos
    ) -> None:
        repos.anime.set_flag(anime.id, "auto_download", True)
        torrent.error = TorrentClientError("qBittorrent недоступен")
        parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])

        result = service.check_anime(repos.anime.get(anime.id))

        assert result.ok
        assert result.sent == 0
        # Раздача найдена и осталась в ленте — пользователь отправит вручную.
        assert repos.releases.feed_count() == 1


class TestDiagnostics:
    def test_unreachable_source(self, service, anime, parser, repos) -> None:
        parser.fail_with(SourceUnreachable("все зеркала молчат"))

        result = service.check_anime(anime)

        assert not result.ok
        assert repos.anime.get(anime.id).last_check_ok is False
        assert repos.sources.all()["fake"]["state"] == SourceState.UNREACHABLE

    def test_layout_changed_is_distinct_state(self, service, anime, parser, repos) -> None:
        parser.fail_with(LayoutChanged("нет блоков раздач"))
        service.check_anime(anime)
        assert repos.sources.all()["fake"]["state"] == SourceState.LAYOUT_CHANGED

    def test_recovery_clears_error(self, service, anime, parser, repos) -> None:
        parser.fail_with(SourceUnreachable("сбой"))
        service.check_anime(anime)

        parser.error = None
        service.check_anime(repos.anime.get(anime.id))

        anime_after = repos.anime.get(anime.id)
        assert anime_after.last_check_ok is True
        assert anime_after.last_error is None
        assert repos.sources.all()["fake"]["state"] == SourceState.OK


class TestCheckAll:
    def test_one_broken_subscription_does_not_stop_others(
        self, repos, registry, parser, torrent
    ) -> None:
        subs = SubscriptionService(repos, registry)
        first = subs.add(URL)
        second = subs.add("https://example.test/999-second.html")

        parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])
        service = UpdateService(repos, registry, torrent)
        summary = service.check_all()

        assert len(summary.results) == 2
        assert summary.new_count >= 1
        assert first.id != second.id

    def test_summary_counts_failures(self, service, anime, parser) -> None:
        parser.fail_with(SourceUnreachable("сбой"))
        summary = service.check_all()
        assert len(summary.failed) == 1
        assert summary.new_count == 0


def test_retry_resets_state_and_resends(service, anime, parser, torrent, repos) -> None:
    parser.set_releases([release("3", 3), release("1", 1), release("2", 2)])
    service.check_anime(anime)
    target = repos.releases.feed()[0]
    repos.releases.set_state(target.id, ReleaseState.ERROR, "нет связи")

    assert service.retry(repos.releases.get(target.id)) is True
    assert torrent.sent == ["3"]
