"""Модели Qt поверх доменных объектов.

Списки виртуализованы: у одного аниме бывает больше двухсот раздач,
складывать их в виджеты по одному нельзя (ТЗ §24).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QAbstractTableModel, QModelIndex, Qt

from ..core.grouping import LibraryEntry, LibraryGroup
from ..core.models import Anime, HistoryEntry, Release, ReleaseState  # noqa: F401

RELEASE_ROLE = Qt.ItemDataRole.UserRole + 1
ANIME_ROLE = Qt.ItemDataRole.UserRole + 2
GROUP_ROLE = Qt.ItemDataRole.UserRole + 3
ENTRY_ROLE = Qt.ItemDataRole.UserRole + 4
ROW_KIND_ROLE = Qt.ItemDataRole.UserRole + 5
COLLAPSED_ROLE = Qt.ItemDataRole.UserRole + 6

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


class LibraryModel(QAbstractListModel):
    """Список подписок деревом: франшизы и вложенные записи (ТЗ §6).

    Плоская модель со строками двух видов вместо QTreeView: карточки и так
    рисуются делегатом вручную, а собственные строки-заголовки дешевле дерева.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[str, object]] = []
        self._groups: list[LibraryGroup] = []
        self._collapsed: set[str] = set()

    # --- построение строк ------------------------------------------------

    def set_groups(self, groups: list[LibraryGroup]) -> None:
        self.beginResetModel()
        self._groups = groups
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        rows: list[tuple[str, object]] = []
        for group in self._groups:
            if not group.is_franchise:
                rows.extend(("entry", entry) for entry in group.entries)
                continue
            rows.append(("group", group))
            if group.key not in self._collapsed:
                rows.extend(("child", entry) for entry in group.entries)
        self._rows = rows

    def toggle_group(self, key: str) -> None:
        self.beginResetModel()
        self._collapsed.symmetric_difference_update({key})
        self._rebuild()
        self.endResetModel()

    def is_collapsed(self, key: str) -> bool:
        return key in self._collapsed

    # --- контракт модели -------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def flags(self, index: QModelIndex):
        base = super().flags(index)
        # Заголовок франшизы не выбирается: он только сворачивается.
        if index.isValid() and self._rows[index.row()][0] == "group":
            return base & ~Qt.ItemFlag.ItemIsSelectable
        return base

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        kind, payload = self._rows[index.row()]

        if role == ROW_KIND_ROLE:
            return kind
        if role == GROUP_ROLE and kind == "group":
            return payload
        if role == COLLAPSED_ROLE and kind == "group":
            return payload.key in self._collapsed
        if role == ENTRY_ROLE and kind in ("entry", "child"):
            return payload
        if role == ANIME_ROLE and kind in ("entry", "child"):
            return payload.primary
        if role == Qt.ItemDataRole.DisplayRole:
            return payload.title
        if role == Qt.ItemDataRole.ToolTipRole and kind != "group":
            return self._tooltip(payload)
        return None

    @staticmethod
    def _tooltip(entry: LibraryEntry) -> str:
        lines = [entry.title]
        for anime in entry.animes:
            checked = (
                anime.last_check_at.strftime("%d.%m.%Y %H:%M")
                if anime.last_check_at
                else "не проверялось"
            )
            lines.append(f"{anime.source}: проверено {checked}")
            if anime.last_error:
                lines.append(f"   ошибка: {anime.last_error}")
        return "\n".join(lines)

    # --- доступ ----------------------------------------------------------

    def entry_at(self, row: int) -> LibraryEntry | None:
        if not 0 <= row < len(self._rows):
            return None
        kind, payload = self._rows[row]
        return payload if kind in ("entry", "child") else None

    def group_at(self, row: int) -> LibraryGroup | None:
        if not 0 <= row < len(self._rows):
            return None
        kind, payload = self._rows[row]
        return payload if kind == "group" else None

    def is_child(self, row: int) -> bool:
        return 0 <= row < len(self._rows) and self._rows[row][0] == "child"

    def anime_at(self, row: int) -> Anime | None:
        entry = self.entry_at(row)
        return entry.primary if entry else None

    def row_of(self, anime_id: int) -> int:
        for row, (kind, payload) in enumerate(self._rows):
            if kind in ("entry", "child") and anime_id in payload.ids:
                return row
        return -1


class ReleaseTableModel(QAbstractTableModel):
    """Таблица раздач выбранного аниме (ТЗ §7)."""

    HEADERS = ("Серия", "Источник", "Качество", "Размер", "Дата", "Сиды", "Статус")

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
                Qt.AlignmentFlag.AlignRight if section in (3, 5) else Qt.AlignmentFlag.AlignLeft
            )
            return int(alignment | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        release = self._items[index.row()]

        if role == RELEASE_ROLE:
            return release
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (3, 5):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        match index.column():
            case 0:
                return release.episode_label
            case 1:
                return release.source or "—"
            case 2:
                return release.quality or "—"
            case 3:
                return release.size_label
            case 4:
                return release.published_at.strftime("%d.%m.%Y") if release.published_at else "—"
            case 5:
                return "—" if release.seeders is None else str(release.seeders)
            case 6:
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


class SearchTableModel(QAbstractTableModel):
    """Результаты поиска по трекеру (ТЗ §3, способ 2)."""

    HEADERS = ("Название", "Раздел", "Размер", "Сиды", "Добавлен")

    def __init__(self) -> None:
        super().__init__()
        self._items: list = []

    def set_hits(self, hits: list) -> None:
        self.beginResetModel()
        self._items = list(hits)
        self.endResetModel()

    def hit_at(self, row: int):
        return self._items[row] if 0 <= row < len(self._items) else None

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        return self.HEADERS[section]

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        hit = self._items[index.row()]
        column = index.column()
        if column == 0:
            return hit.title
        if column == 1:
            return hit.category or "—"
        if column == 2:
            return _size_label(hit.size_bytes)
        if column == 3:
            return "—" if hit.seeders is None else str(hit.seeders)
        if column == 4:
            return hit.added.strftime("%d.%m.%Y") if hit.added else "—"
        return None


def _size_label(size_bytes: int | None) -> str:
    if not size_bytes:
        return "—"
    size = float(size_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024 or unit == "ГБ":
            return f"{size:.2f} {unit}".replace(".00", "")
        size /= 1024
    return "—"
