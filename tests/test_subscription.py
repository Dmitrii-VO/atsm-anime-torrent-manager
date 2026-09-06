from __future__ import annotations

import pytest

from atsm.core.subscription_service import SubscriptionExists, SubscriptionService
from atsm.db.repositories import Repositories
from atsm.parsers.base import ParserError, SourceUnreachable

from .fakes import URL, FakeParser, FakeRegistry, release


@pytest.fixture
def repos(db) -> Repositories:
    return Repositories(db)


@pytest.fixture
def parser() -> FakeParser:
    return FakeParser([release("1", 1), release("2", 2), release("3", 3)])


@pytest.fixture
def service(repos: Repositories, parser: FakeParser) -> SubscriptionService:
    return SubscriptionService(repos, FakeRegistry(parser))


def test_add_imports_archive_silently(service, repos) -> None:
    anime = service.add(URL)

    assert anime.title == "Пожиратель звёзд"
    assert len(repos.releases.list_for_anime(anime.id)) == 3
    # Архив не должен попасть в ленту (ТЗ §5).
    assert repos.releases.feed_count() == 0
    assert anime.last_check_ok is True


def test_add_writes_history(service, repos) -> None:
    anime = service.add(URL)
    entries = repos.history.recent()
    assert entries[0].anime_id == anime.id
    assert "импортировано раздач: 3" in entries[0].message


def test_duplicate_subscription_rejected(service) -> None:
    first = service.add(URL)
    with pytest.raises(SubscriptionExists) as exc:
        service.add(URL)
    assert exc.value.anime.id == first.id


def test_duplicate_detected_across_mirrors(service) -> None:
    """Тот же slug на другом зеркале — это та же подписка (ТЗ §3)."""
    service.add("https://example.test/7788-pozhiratel.html")
    with pytest.raises(SubscriptionExists):
        service.add("https://example.test/7788-pozhiratel.html/")


def test_unsupported_url_rejected(service) -> None:
    with pytest.raises(ParserError):
        service.add("https://nyaa.si/view/1")


def test_source_failure_propagates_and_saves_nothing(service, parser, repos) -> None:
    parser.fail_with(SourceUnreachable("сайт лежит"))
    with pytest.raises(SourceUnreachable):
        service.add(URL)
    assert repos.anime.list() == []


def test_archive_failure_rolls_back_subscription(service, repos, monkeypatch) -> None:
    def fail_import(*args, **kwargs):
        raise RuntimeError("сбой импорта")

    monkeypatch.setattr(repos.releases, "_add_many", fail_import)

    with pytest.raises(RuntimeError, match="сбой импорта"):
        service.add(URL)

    assert repos.anime.list() == []
    assert repos.history.recent() == []


def test_auto_download_flag_persisted(service, repos) -> None:
    anime = service.add(URL, auto_download=True)
    assert repos.anime.get(anime.id).auto_download is True


def test_remove(service, repos) -> None:
    anime = service.add(URL)
    service.remove(anime.id)
    assert repos.anime.list() == []
    # Раздачи уходят каскадом.
    assert repos.releases.feed_count() == 0


def test_preview_does_not_save(service, repos) -> None:
    info = service.preview(URL)
    assert len(info.releases) == 3
    assert repos.anime.list() == []
