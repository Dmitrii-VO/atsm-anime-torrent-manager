"""Контракт поставщика метаданных.

Это не парсер раздач: провайдер ничего не знает про торренты и не участвует
в проверке подписок. Он только обогащает карточку аниме справочными данными.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


class MetadataError(Exception):
    """Справочник недоступен или ответил неожиданно."""


@dataclass(slots=True)
class MetadataCandidate:
    """Вариант совпадения при поиске по названию."""

    provider: str
    external_id: str
    title_ru: str | None
    title_romaji: str | None
    kind: str | None = None
    year: int | None = None
    episodes: int | None = None

    @property
    def label(self) -> str:
        parts = [self.title_ru or self.title_romaji or self.external_id]
        if self.title_ru and self.title_romaji and self.title_ru != self.title_romaji:
            parts.append(f"/ {self.title_romaji}")
        tail = " · ".join(str(x) for x in (self.kind, self.year) if x)
        return f"{' '.join(parts)}" + (f" ({tail})" if tail else "")


@dataclass(slots=True)
class AnimeMetadata:
    """Справочные данные о тайтле.

    Внимание: episodes_total/episodes_aired относятся к нумерации справочника
    и НЕ совпадают с нумерацией раздач на трекере. Дунхуа на Shikimori разбиты
    по сезонам, а astar нумерует серии сквозняком (26 против 235 у одного и
    того же тайтла). Использовать их как прогресс подписки нельзя.
    """

    provider: str
    external_id: str
    title_ru: str | None = None
    title_romaji: str | None = None
    title_native: str | None = None
    kind: str | None = None
    status: str | None = None
    score: float | None = None
    episodes_total: int | None = None
    episodes_aired: int | None = None
    next_episode_number: int | None = None
    next_episode_at: datetime | None = None
    poster_url: str | None = None
    genres: list[str] = field(default_factory=list)
    description: str | None = None
    site_url: str | None = None

    def merge(self, other: "AnimeMetadata") -> "AnimeMetadata":
        """Дополняет пустые поля данными другого провайдера, не затирая свои."""
        for name in self.__slots__:
            if name in ("provider", "external_id"):
                continue
            mine = getattr(self, name)
            theirs = getattr(other, name)
            if not mine and theirs:
                setattr(self, name, theirs)
        return self


class MetadataProvider(ABC):
    name: str = ""
    display_name: str = ""

    @abstractmethod
    def search(self, title: str, limit: int = 5) -> list[MetadataCandidate]:
        """Кандидаты по названию — их может быть несколько (сезоны, фильмы)."""

    @abstractmethod
    def fetch(self, external_id: str) -> AnimeMetadata:
        """Полные данные по идентификатору справочника."""
