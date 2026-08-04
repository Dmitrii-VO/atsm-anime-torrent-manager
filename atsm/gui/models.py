"""Модели Qt поверх доменных объектов.

Списки виртуализованы: у одного аниме бывает больше двухсот раздач,
складывать их в виджеты по одному нельзя (ТЗ §24).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, Qt

from ..core.models import Anime, HistoryEntry, Release, ReleaseState  # noqa: F401

RELEASE_ROLE = Qt.ItemDataRole.UserRole + 1
ANIME_ROLE = Qt.ItemDataRole.UserRole + 2

STATE_LABELS = {
    ReleaseState.NEW: "Новая",
    ReleaseState.SENT: "Отправлена",
    ReleaseState.DOWNLOADED: "Скачана",
    ReleaseState.ERROR: "Ошибка",
    ReleaseState.IGNORED: "Пропущена",
}


def _state_label(release: Release) -> str:
    """Архивные раздачи не «новые»: у них состояние new, но они уже просмотрены.

    Иначе весь импортированный архив выглядит как двести новых серий.
    """
    if release.state == ReleaseState.NEW:
        return "Новая" if not release.is_seen else "В архиве"
    return STATE_LABELS.get(release.state, release.state)


class FeedModel(QAbstractListModel):
    """Лента новых серий — стартовый экран (ТЗ §5)."""

    def __init__(self, releases: list[Release] | None = None) -> None:
        super().__init__()
        self._releases: list[Release] = releases or []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._releases)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        release = self._releases[index.row()]
        if role == RELEASE_ROLE:
            return release
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{release.anime_title} — {release.episode_label}"
        return None

    def set_releases(self, releases: list[Release]) -> None:
        self.beginResetModel()
        self._releases = releases
        self.endResetModel()

    def release_at(self, row: int) -> Release | None:
        return self._releases[row] if 0 <= row < len(self._releases) else None

    def all(self) -> list[Release]:
        return list(self._releases)


class AnimeListModel(QAbstractListModel):
    """Список подписок с числом новых серий и состоянием проверки (ТЗ §6)."""

    def __init__(self) -> None:
        super().__init__()
        self._items: list[Anime] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        anime = self._items[index.row()]
        if role == ANIME_ROLE:
            return anime
        if role == Qt.ItemDataRole.DisplayRole:
            return anime.title
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(anime)
        return None

    @staticmethod
    def _tooltip(anime: Anime) -> str:
        checked = (
            anime.last_check_at.strftime("%d.%m.%Y %H:%M") if anime.last_check_at else "никогда"
        )
        lines = [anime.title, f"Источник: {anime.source}", f"Проверено: {checked}"]
        if anime.last_error:
            lines.append(f"Ошибка: {anime.last_error}")
        return "\n".join(lines)

    def set_items(self, items: list[Anime]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def anime_at(self, row: int) -> Anime | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, anime_id: int) -> int:
        for row, anime in enumerate(self._items):
            if anime.id == anime_id:
                return row
        return -1


class ReleaseTableModel(QAbstractTableModel):
    """Таблица раздач выбранного аниме (ТЗ §7)."""

    HEADERS = ("Серия", "Размер", "Дата", "Сиды", "Статус")

    def __init__(self) -> None:
        super().__init__()
        self._items: list[Release] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            # Заголовок широкой колонки, выровненный по центру, выглядит оторванным
            # от своих же значений слева.
            alignment = (
                Qt.AlignmentFlag.AlignRight if section in (1, 3) else Qt.AlignmentFlag.AlignLeft
            )
            return int(alignment | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        release = self._items[index.row()]

        if role == RELEASE_ROLE:
            return release
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (1, 3):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        match index.column():
            case 0:
                return release.episode_label
            case 1:
                return release.size_label
            case 2:
                return release.published_at.strftime("%d.%m.%Y") if release.published_at else "—"
            case 3:
                return "—" if release.seeders is None else str(release.seeders)
            case 4:
                return _state_label(release)
        return None

    def set_items(self, items: list[Release]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()

    def release_at(self, row: int) -> Release | None:
        return self._items[row] if 0 <= row < len(self._items) else None


class HistoryTableModel(QAbstractTableModel):
    """Журнал действий (ТЗ §22)."""

    HEADERS = ("Время", "Аниме", "Событие", "Подробности")
    ACTIONS = {
        "found": "Найдена серия",
        "sent": "Передано в клиент",
        "downloaded": "Скачана",
        "send_error": "Ошибка отправки",
        "parse_error": "Ошибка парсинга",
        "check": "Проверка",
    }

    def __init__(self) -> None:
        super().__init__()
        self._items: list[HistoryEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        entry = self._items[index.row()]
        match index.column():
            case 0:
                return entry.created_at.strftime("%d.%m %H:%M:%S") if entry.created_at else ""
            case 1:
                return entry.anime_title or "—"
            case 2:
                return self.ACTIONS.get(entry.action, entry.action)
            case 3:
                return entry.message or ""
        return None

    def set_items(self, items: list[HistoryEntry]) -> None:
        self.beginResetModel()
        self._items = items
        self.endResetModel()
