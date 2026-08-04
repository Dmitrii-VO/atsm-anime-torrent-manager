"""Группировка подписок для списка библиотеки.

Две разные задачи, которые легко перепутать:

  * **дубликат** — один и тот же тайтл, заведённый с двух источников.
    Признак: одинаковый `shikimori_id`. Такие подписки сливаются в одну
    строку, их раздачи показываются вместе.
  * **франшиза** — разные тайтлы одной серии («Блич» и «Блич: Тысячелетняя
    кровавая война»). Признак: одинаковый `franchise` из справочника.
    Такие подписки лишь ставятся рядом — сливать их нельзя, у них своя
    нумерация серий.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Anime


@dataclass(slots=True)
class LibraryEntry:
    """Одна строка списка. Обычно одна подписка, у дубликатов — несколько."""

    animes: list[Anime]

    @property
    def primary(self) -> Anime:
        """Подписка, от имени которой показывается строка.

        Берётся та, у которой включена автозагрузка, иначе первая по порядку —
        так строка не «прыгает» между источниками при обновлении списка.
        """
        for anime in self.animes:
            if anime.auto_download:
                return anime
        return self.animes[0]

    @property
    def is_merged(self) -> bool:
        return len(self.animes) > 1

    @property
    def id(self) -> int:
        return self.primary.id

    @property
    def ids(self) -> list[int]:
        return [a.id for a in self.animes]

    @property
    def title(self) -> str:
        return self.primary.title

    @property
    def sources(self) -> list[str]:
        return sorted({a.source for a in self.animes})

    @property
    def new_count(self) -> int:
        return sum(a.new_count for a in self.animes)

    @property
    def last_episode(self) -> int | None:
        numbers = [a.last_episode for a in self.animes if a.last_episode]
        return max(numbers) if numbers else None

    @property
    def is_favorite(self) -> bool:
        return any(a.is_favorite for a in self.animes)

    @property
    def auto_download(self) -> bool:
        return any(a.auto_download for a in self.animes)

    @property
    def has_error(self) -> bool:
        return any(a.last_check_ok is False for a in self.animes)

    @property
    def last_check_at(self):
        checks = [a.last_check_at for a in self.animes if a.last_check_at]
        return max(checks) if checks else None

    def anime_by_source(self, source: str) -> Anime | None:
        return next((a for a in self.animes if a.source == source), None)


@dataclass(slots=True)
class LibraryGroup:
    """Франшиза. Группа из одной записи отображается без заголовка."""

    key: str | None
    title: str
    entries: list[LibraryEntry] = field(default_factory=list)

    @property
    def is_franchise(self) -> bool:
        return self.key is not None and len(self.entries) > 1

    @property
    def new_count(self) -> int:
        return sum(entry.new_count for entry in self.entries)


def merge_duplicates(animes: list[Anime]) -> list[LibraryEntry]:
    """Сливает подписки на один тайтл, заведённые с разных источников."""
    entries: list[LibraryEntry] = []
    by_title: dict[str, LibraryEntry] = {}

    for anime in animes:
        key = anime.shikimori_id
        # Без справки сливать нельзя: одинаковые названия у разных сезонов
        # встречаются постоянно, а ошибочное слияние перемешает раздачи.
        if not key:
            entries.append(LibraryEntry([anime]))
            continue

        existing = by_title.get(key)
        if existing is None:
            entry = LibraryEntry([anime])
            by_title[key] = entry
            entries.append(entry)
        else:
            existing.animes.append(anime)

    return entries


def build_groups(animes: list[Anime]) -> list[LibraryGroup]:
    """Строит дерево: франшизы с вложенными записями и одиночные записи."""
    entries = merge_duplicates(animes)

    groups: list[LibraryGroup] = []
    by_franchise: dict[str, LibraryGroup] = {}

    for entry in entries:
        key = entry.primary.franchise
        if not key:
            groups.append(LibraryGroup(key=None, title=entry.title, entries=[entry]))
            continue

        group = by_franchise.get(key)
        if group is None:
            group = LibraryGroup(key=key, title=entry.title, entries=[])
            by_franchise[key] = group
            groups.append(group)
        group.entries.append(entry)

    for group in groups:
        # Заголовок франшизы — самое короткое название: «Блич» вместо
        # «Блич: Тысячелетняя кровавая война — Бедствие».
        if group.is_franchise:
            group.title = min((e.title for e in group.entries), key=len)
        group.entries.sort(key=lambda e: e.title.lower())

    groups.sort(key=lambda g: (not any(e.is_favorite for e in g.entries), g.title.lower()))
    return groups
