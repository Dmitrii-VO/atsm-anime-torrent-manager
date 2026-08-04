"""Группировка подписок: слияние дубликатов и франшизы."""

from __future__ import annotations

from atsm.core.grouping import build_groups, merge_duplicates
from atsm.core.models import Anime


def anime(
    anime_id: int,
    title: str,
    source: str = "astar",
    shikimori_id: str | None = None,
    franchise: str | None = None,
    **kwargs,
) -> Anime:
    return Anime(
        id=anime_id,
        title=title,
        source=source,
        url=f"https://{source}/{anime_id}",
        slug=str(anime_id),
        shikimori_id=shikimori_id,
        franchise=franchise,
        **kwargs,
    )


class TestMergeDuplicates:
    def test_same_title_from_two_sources_merges(self) -> None:
        entries = merge_duplicates([
            anime(1, "Пожиратель звёзд", "astar", shikimori_id="44218"),
            anime(2, "Пожиратель звёзд", "anilibria", shikimori_id="44218"),
        ])

        assert len(entries) == 1
        assert entries[0].is_merged
        assert entries[0].sources == ["anilibria", "astar"]
        assert entries[0].ids == [1, 2]

    def test_different_titles_stay_apart(self) -> None:
        """«Блич» и «Блич: ТКВ» — разные тайтлы, сливать их нельзя."""
        entries = merge_duplicates([
            anime(1, "Блич", "anilibria", shikimori_id="269", franchise="bleach"),
            anime(2, "Блич: ТКВ", "astar", shikimori_id="60636", franchise="bleach"),
        ])

        assert len(entries) == 2
        assert all(not e.is_merged for e in entries)

    def test_without_metadata_never_merges(self) -> None:
        """Без справки одинаковые названия могут оказаться разными сезонами."""
        entries = merge_duplicates([
            anime(1, "Блич", "astar"),
            anime(2, "Блич", "anilibria"),
        ])
        assert len(entries) == 2

    def test_counters_are_summed(self) -> None:
        entries = merge_duplicates([
            anime(1, "X", "astar", shikimori_id="1", new_count=2, last_episode=10),
            anime(2, "X", "anilibria", shikimori_id="1", new_count=3, last_episode=12),
        ])

        assert entries[0].new_count == 5
        assert entries[0].last_episode == 12

    def test_primary_is_the_autodownload_source(self) -> None:
        entries = merge_duplicates([
            anime(1, "X", "astar", shikimori_id="1"),
            anime(2, "X", "anilibria", shikimori_id="1", auto_download=True),
        ])

        assert entries[0].primary.source == "anilibria"
        assert entries[0].auto_download is True

    def test_error_and_favorite_propagate(self) -> None:
        entries = merge_duplicates([
            anime(1, "X", "astar", shikimori_id="1", last_check_ok=False),
            anime(2, "X", "anilibria", shikimori_id="1", is_favorite=True),
        ])

        assert entries[0].has_error is True
        assert entries[0].is_favorite is True

    def test_lookup_by_source(self) -> None:
        entries = merge_duplicates([
            anime(1, "X", "astar", shikimori_id="1"),
            anime(2, "X", "anilibria", shikimori_id="1"),
        ])
        assert entries[0].anime_by_source("astar").id == 1
        assert entries[0].anime_by_source("nyaa") is None


class TestFranchiseGroups:
    def test_bleach_case(self) -> None:
        """Реальный случай: два тайтла одной франшизы с разных источников."""
        groups = build_groups([
            anime(9, "Блич", "anilibria", shikimori_id="269", franchise="bleach"),
            anime(8, "Блич: Тысячелетняя кровавая война", "astar",
                  shikimori_id="60636", franchise="bleach"),
            anime(6, "Вечная воля 4 сезон", "astar", shikimori_id="62248",
                  franchise="yi_nian_yong_heng"),
        ])

        franchise = next(g for g in groups if g.is_franchise)
        assert franchise.key == "bleach"
        assert franchise.title == "Блич"  # заголовком берётся короткое название
        assert len(franchise.entries) == 2

        single = next(g for g in groups if not g.is_franchise)
        assert single.entries[0].title == "Вечная воля 4 сезон"

    def test_single_title_is_not_a_franchise(self) -> None:
        """Один тайтл во франшизе не должен создавать лишний уровень."""
        groups = build_groups([
            anime(1, "Пожиратель звёзд", shikimori_id="44218", franchise="swallowed_star"),
        ])
        assert len(groups) == 1
        assert groups[0].is_franchise is False

    def test_without_franchise_stays_flat(self) -> None:
        groups = build_groups([anime(1, "Без справки"), anime(2, "Тоже без")])
        assert len(groups) == 2
        assert all(not g.is_franchise for g in groups)

    def test_duplicates_merge_inside_franchise(self) -> None:
        groups = build_groups([
            anime(1, "Блич", "astar", shikimori_id="269", franchise="bleach"),
            anime(2, "Блич", "anilibria", shikimori_id="269", franchise="bleach"),
            anime(3, "Блич: ТКВ", "astar", shikimori_id="60636", franchise="bleach"),
        ])

        assert len(groups) == 1
        group = groups[0]
        assert len(group.entries) == 2
        merged = next(e for e in group.entries if e.is_merged)
        assert merged.sources == ["anilibria", "astar"]

    def test_new_counts_roll_up(self) -> None:
        groups = build_groups([
            anime(1, "Блич", shikimori_id="269", franchise="bleach", new_count=2),
            anime(2, "Блич: ТКВ", shikimori_id="60636", franchise="bleach", new_count=1),
        ])
        assert groups[0].new_count == 3

    def test_favorites_first(self) -> None:
        groups = build_groups([
            anime(1, "Яблоко", shikimori_id="1"),
            anime(2, "Ананас", shikimori_id="2", is_favorite=True),
        ])
        assert groups[0].title == "Ананас"

    def test_entries_sorted_alphabetically(self) -> None:
        groups = build_groups([
            anime(1, "Блич: ТКВ", shikimori_id="60636", franchise="bleach"),
            anime(2, "Блич", shikimori_id="269", franchise="bleach"),
        ])
        assert [e.title for e in groups[0].entries] == ["Блич", "Блич: ТКВ"]
