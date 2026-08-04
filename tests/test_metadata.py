from __future__ import annotations

from datetime import datetime

import pytest
import requests
import responses

from atsm.db.repositories import Repositories
from atsm.metadata import AniListProvider, MetadataError, MetadataService, ShikimoriProvider
from atsm.metadata.base import AnimeMetadata, MetadataCandidate
from atsm.metadata.service import normalize
from atsm.services.http import RateLimiter

SHIKI = "https://shikimori.one/api"
ANILIST = "https://graphql.anilist.co"

SEARCH_RESPONSE = [
    {"id": 44218, "name": "Tunshi Xingkong", "russian": "Пожиратель звёзд",
     "kind": "ona", "aired_on": "2020-11-29", "episodes": 26},
    {"id": 56523, "name": "Tunshi Xingkong 3rd Season", "russian": "Пожиратель звёзд 3",
     "kind": "ona", "aired_on": "2022-09-14", "episodes": 26},
]

DETAIL_RESPONSE = {
    "id": 44218,
    "name": "Tunshi Xingkong",
    "russian": "Пожиратель звёзд",
    # Shikimori отдаёт японское название списком — на этом уже спотыкались.
    "japanese": ["吞噬星空"],
    "kind": "ona",
    "status": "released",
    "score": "8.07",
    "episodes": 26,
    "episodes_aired": 25,
    "next_episode_at": None,
    "image": {"original": "/system/animes/original/44218.jpg"},
    "genres": [{"russian": "Экшен", "name": "Action"}, {"russian": "Фэнтези", "name": "Fantasy"}],
    "description": "Описание",
    "url": "/animes/44218",
}

ANILIST_MEDIA = {
    "data": {
        "Media": {
            "id": 117012,
            "title": {"romaji": "Tunshi Xingkong", "english": None, "native": "吞噬星空"},
            "format": "ONA",
            "status": "RELEASING",
            "averageScore": 74,
            "episodes": 26,
            "genres": ["Action", "Fantasy"],
            "description": "desc",
            "siteUrl": "https://anilist.co/anime/117012",
            "coverImage": {"large": "https://img/cover.jpg"},
            "startDate": {"year": 2020},
            "nextAiringEpisode": {"episode": 26, "airingAt": 1786284960},
        }
    }
}


@pytest.fixture
def repos(db) -> Repositories:
    return Repositories(db)


@pytest.fixture
def anime_id(repos: Repositories) -> int:
    return repos.anime.add(
        title="Пожиратель звёзд", source="astar",
        url="https://v19.astar.bz/7788-x.html", slug="7788-x.html",
    )


def no_wait() -> RateLimiter:
    return RateLimiter(0)


class TestShikimori:
    @responses.activate
    def test_search_returns_candidates(self) -> None:
        responses.get(f"{SHIKI}/animes", json=SEARCH_RESPONSE)
        found = ShikimoriProvider(limiter=no_wait()).search("Пожиратель звёзд")

        assert [c.external_id for c in found] == ["44218", "56523"]
        assert found[0].title_ru == "Пожиратель звёзд"
        assert found[0].kind == "ONA"
        assert found[0].year == 2020
        assert "2020" in found[0].label

    @responses.activate
    def test_fetch_maps_fields(self) -> None:
        responses.get(f"{SHIKI}/animes/44218", json=DETAIL_RESPONSE)
        meta = ShikimoriProvider(limiter=no_wait()).fetch("44218")

        assert meta.title_ru == "Пожиратель звёзд"
        assert meta.title_native == "吞噬星空"  # список свёрнут в строку
        assert meta.kind == "ONA"
        assert meta.status == "Вышел"
        assert meta.score == 8.07
        assert meta.genres == ["Экшен", "Фэнтези"]
        assert meta.poster_url.startswith("https://shikimori.one/system/")

    @responses.activate
    def test_zero_score_means_absent(self) -> None:
        responses.get(f"{SHIKI}/animes/1", json={**DETAIL_RESPONSE, "id": 1, "score": "0.0"})
        assert ShikimoriProvider(limiter=no_wait()).fetch("1").score is None

    @responses.activate
    def test_network_error_is_typed(self) -> None:
        responses.get(f"{SHIKI}/animes", body=requests.ConnectionError("нет сети"))
        with pytest.raises(MetadataError, match="недоступен"):
            ShikimoriProvider(limiter=no_wait()).search("x")


class TestAniList:
    @responses.activate
    def test_fetch_by_title(self) -> None:
        responses.post(ANILIST, json=ANILIST_MEDIA)
        meta = AniListProvider(limiter=no_wait()).fetch_by_title("Tunshi Xingkong")

        assert meta.external_id == "117012"
        assert meta.status == "Онгоинг"
        # У AniList оценка в процентах — приводим к десятибалльной.
        assert meta.score == 7.4
        assert meta.next_episode_number == 26
        assert isinstance(meta.next_episode_at, datetime)

    @responses.activate
    def test_rate_limit_is_reported_clearly(self) -> None:
        responses.post(ANILIST, status=429, json={})
        with pytest.raises(MetadataError, match="лимит"):
            AniListProvider(limiter=no_wait()).fetch_by_title("x")

    @responses.activate
    def test_graphql_errors_are_surfaced(self) -> None:
        responses.post(ANILIST, json={"errors": [{"message": "Not Found"}]})
        with pytest.raises(MetadataError, match="Not Found"):
            AniListProvider(limiter=no_wait()).fetch_by_title("x")


class TestPickBest:
    def setup_method(self) -> None:
        self.service = MetadataService.__new__(MetadataService)

    def candidates(self) -> list[MetadataCandidate]:
        return [
            MetadataCandidate("shikimori", "56523", "Пожиратель звёзд 3", "Tunshi Xingkong 3rd"),
            MetadataCandidate("shikimori", "44218", "Пожиратель звёзд", "Tunshi Xingkong"),
        ]

    def test_exact_title_wins_over_order(self) -> None:
        """Поиск возвращает сезоны вперемешку — брать первый нельзя."""
        best = self.service.pick_best("Пожиратель звёзд", self.candidates())
        assert best.external_id == "44218"

    def test_falls_back_to_first(self) -> None:
        best = self.service.pick_best("Совсем другое", self.candidates())
        assert best.external_id == "56523"

    def test_empty(self) -> None:
        assert self.service.pick_best("x", []) is None

    def test_normalize_ignores_case_yo_and_punctuation(self) -> None:
        assert normalize("Пожиратель звёзд") == normalize("ПОЖИРАТЕЛЬ ЗВЕЗД!")


class TestService:
    @responses.activate
    def test_enrich_stores_both_providers(self, repos, anime_id) -> None:
        responses.get(f"{SHIKI}/animes", json=SEARCH_RESPONSE)
        responses.get(f"{SHIKI}/animes/44218", json=DETAIL_RESPONSE)
        responses.post(ANILIST, json=ANILIST_MEDIA)

        service = MetadataService(
            repos,
            ShikimoriProvider(limiter=no_wait()),
            AniListProvider(limiter=no_wait()),
        )
        meta = service.enrich(anime_id)

        assert meta.title_ru == "Пожиратель звёзд"
        # Время выхода берётся у AniList, он точнее.
        assert meta.next_episode_number == 26

        stored = repos.metadata.get(anime_id)
        assert stored["shikimori_id"] == "44218"
        assert stored["anilist_id"] == "117012"
        assert stored["genres"] == ["Экшен", "Фэнтези"]
        assert stored["title_native"] == "吞噬星空"

    @responses.activate
    def test_anilist_failure_does_not_break_enrichment(self, repos, anime_id) -> None:
        """AniList — дополнение. Его отказ не должен лишать нас данных Shikimori."""
        responses.get(f"{SHIKI}/animes", json=SEARCH_RESPONSE)
        responses.get(f"{SHIKI}/animes/44218", json=DETAIL_RESPONSE)
        responses.post(ANILIST, status=500, json={})

        service = MetadataService(
            repos, ShikimoriProvider(limiter=no_wait()), AniListProvider(limiter=no_wait())
        )
        meta = service.enrich(anime_id)

        assert meta.title_ru == "Пожиратель звёзд"
        assert repos.metadata.get(anime_id)["anilist_id"] is None

    @responses.activate
    def test_explicit_id_skips_search(self, repos, anime_id) -> None:
        responses.get(f"{SHIKI}/animes/44218", json=DETAIL_RESPONSE)
        responses.post(ANILIST, json=ANILIST_MEDIA)

        service = MetadataService(
            repos, ShikimoriProvider(limiter=no_wait()), AniListProvider(limiter=no_wait())
        )
        service.enrich(anime_id, shikimori_id="44218")

        assert not any("/animes?" in call.request.url for call in responses.calls)

    @responses.activate
    def test_stored_id_is_reused(self, repos, anime_id) -> None:
        responses.get(f"{SHIKI}/animes/44218", json=DETAIL_RESPONSE)
        responses.post(ANILIST, json=ANILIST_MEDIA)
        service = MetadataService(
            repos, ShikimoriProvider(limiter=no_wait()), AniListProvider(limiter=no_wait())
        )
        service.enrich(anime_id, shikimori_id="44218")
        service.enrich(anime_id)  # второй раз — без поиска

        searches = [c for c in responses.calls if c.request.url.startswith(f"{SHIKI}/animes?")]
        assert searches == []

    @responses.activate
    def test_nothing_found(self, repos, anime_id) -> None:
        responses.get(f"{SHIKI}/animes", json=[])
        service = MetadataService(repos, ShikimoriProvider(limiter=no_wait()))
        with pytest.raises(MetadataError, match="ничего не найдено"):
            service.enrich(anime_id)

    def test_metadata_removed_with_anime(self, repos, anime_id) -> None:
        repos.metadata.save(anime_id, AnimeMetadata("shikimori", "44218", title_ru="Тест"))
        assert repos.metadata.get(anime_id) is not None

        repos.anime.delete(anime_id)
        assert repos.metadata.get(anime_id) is None


class TestMerge:
    def test_merge_fills_only_gaps(self) -> None:
        primary = AnimeMetadata("shikimori", "1", title_ru="Русское", score=8.0)
        secondary = AnimeMetadata("anilist", "2", title_ru="Другое", score=7.0, description="d")

        primary.merge(secondary)

        assert primary.title_ru == "Русское"  # своё не затирается
        assert primary.score == 8.0
        assert primary.description == "d"  # пустое заполняется
