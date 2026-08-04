"""Экран «Библиотека»: список подписок и детали выбранного аниме (ТЗ §6, §7)."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Anime, Release, SOURCE_STATE_LABELS, SourceState
from .models import ANIME_ROLE, RELEASE_ROLE, AnimeListModel, ReleaseTableModel

ROW_HEIGHT = 62


class AnimeDelegate(QStyledItemDelegate):
    """Строка подписки: избранное, счётчик новых, дата проверки, состояние."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        anime: Anime = index.data(ANIME_ROLE)
        if anime is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(6, 3, -6, -3)

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setBrush(QColor("#2f3648") if selected else QColor("#272d3c"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 8, 8)

        left = rect.left() + 12
        if anime.is_favorite:
            painter.setPen(QColor("#f5b544"))
            painter.drawText(
                rect.adjusted(0, 8, 0, 0), int(Qt.AlignmentFlag.AlignLeft), "  ★"
            )
            left += 16

        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor("#eceef4"))
        painter.drawText(
            rect.adjusted(left - rect.left(), 8, -60, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            anime.title,
        )

        painter.setFont(option.font)
        painter.setPen(QColor("#8b93a7"))
        painter.drawText(
            rect.adjusted(left - rect.left(), 30, -60, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            self._subtitle(anime),
        )

        if anime.new_count:
            self._paint_badge(painter, option, rect, str(anime.new_count))
        elif anime.last_check_ok is False:
            painter.setPen(QColor("#e5484d"))
            painter.drawText(
                rect.adjusted(0, 0, -14, 0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                "!",
            )
        painter.restore()

    @staticmethod
    def _subtitle(anime: Anime) -> str:
        checked = (
            anime.last_check_at.strftime("%d.%m %H:%M") if anime.last_check_at else "не проверялось"
        )
        parts = [anime.source, checked]
        if anime.last_episode:
            parts.insert(0, f"до {anime.last_episode} серии")
        if anime.auto_download:
            parts.append("авто")
        return " · ".join(parts)

    @staticmethod
    def _paint_badge(painter: QPainter, option, rect, text: str) -> None:
        badge = rect.adjusted(rect.width() - 46, (ROW_HEIGHT - 22) // 2 - 3, -12, 0)
        badge.setHeight(22)
        painter.setBrush(QColor("#5b3fe0"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 11, 11)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(option.font)
        painter.drawText(badge, int(Qt.AlignmentFlag.AlignCenter), text)


class LibraryView(QWidget):
    check_requested = Signal(object)          # Anime
    remove_requested = Signal(object)
    send_requested = Signal(object)           # Release
    save_requested = Signal(object)
    open_default_client_requested = Signal(object)
    copy_link_requested = Signal(object)
    copy_magnet_requested = Signal(object)
    mark_seen_requested = Signal(object)      # anime_id
    auto_download_toggled = Signal(object, bool)
    favorite_toggled = Signal(object, bool)
    open_page_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.anime_model = AnimeListModel()
        self.release_model = ReleaseTableModel()
        self._all_anime: list[Anime] = []
        self._current: Anime | None = None
        self._build()

    # --- построение ------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 700])
        layout.addWidget(splitter)

    def _build_left(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 8, 14)
        layout.setSpacing(8)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        layout.addWidget(self.search)

        self.filter_box = QComboBox()
        self.filter_box.addItems(["Все", "Только с новыми", "Избранное", "С ошибками"])
        self.filter_box.currentIndexChanged.connect(self._apply_filters)
        layout.addWidget(self.filter_box)

        self.anime_list = QListView()
        self.anime_list.setModel(self.anime_model)
        self.anime_list.setMouseTracking(True)
        self.anime_list.setItemDelegate(AnimeDelegate(self.anime_list))
        self.anime_list.setFrameShape(QListView.Shape.NoFrame)
        self.anime_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.anime_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.anime_list.customContextMenuRequested.connect(self._anime_menu)
        self.anime_list.selectionModel().currentChanged.connect(self._on_anime_selected)
        layout.addWidget(self.anime_list, 1)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 14, 14, 14)
        layout.setSpacing(10)

        self.detail_title = QLabel("Выберите подписку")
        self.detail_title.setObjectName("screenTitle")
        layout.addWidget(self.detail_title)

        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("muted")
        self.detail_meta.setWordWrap(True)
        layout.addWidget(self.detail_meta)

        actions = QHBoxLayout()
        self.check_button = QPushButton("Проверить")
        self.check_button.clicked.connect(lambda: self.check_requested.emit(self._current))
        actions.addWidget(self.check_button)

        self.open_page_button = QPushButton("Открыть страницу")
        self.open_page_button.setObjectName("secondaryButton")
        self.open_page_button.clicked.connect(lambda: self.open_page_requested.emit(self._current))
        actions.addWidget(self.open_page_button)

        self.auto_check = QCheckBox("Скачивать автоматически")
        self.auto_check.toggled.connect(self._on_auto_toggled)
        actions.addWidget(self.auto_check)

        self.favorite_check = QCheckBox("Избранное")
        self.favorite_check.toggled.connect(self._on_favorite_toggled)
        actions.addWidget(self.favorite_check)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.releases = QTableView()
        self.releases.setModel(self.release_model)
        self.releases.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.releases.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.releases.setAlternatingRowColors(True)
        self.releases.verticalHeader().setVisible(False)
        self.releases.setShowGrid(False)
        self.releases.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.releases.customContextMenuRequested.connect(self._release_menu)
        self.releases.doubleClicked.connect(self._on_release_double_click)

        header = self.releases.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.releases, 1)
        return panel

    # --- данные ----------------------------------------------------------

    def set_anime(self, items: list[Anime]) -> None:
        self._all_anime = items
        current_id = self._current.id if self._current else None
        self._apply_filters()
        if current_id is not None:
            self.select_anime(current_id)

    def _apply_filters(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter_box.currentIndex()

        def keep(anime: Anime) -> bool:
            if text and text not in anime.title.lower():
                return False
            match mode:
                case 1:
                    return anime.new_count > 0
                case 2:
                    return anime.is_favorite
                case 3:
                    return anime.last_check_ok is False
            return True

        self.anime_model.set_items([a for a in self._all_anime if keep(a)])

    def select_anime(self, anime_id: int) -> None:
        row = self.anime_model.row_of(anime_id)
        if row >= 0:
            index = self.anime_model.index(row, 0)
            self.anime_list.setCurrentIndex(index)

    def set_releases(self, releases: list[Release]) -> None:
        self.release_model.set_items(releases)

    def current_anime(self) -> Anime | None:
        return self._current

    def selected_releases(self) -> list[Release]:
        rows = {index.row() for index in self.releases.selectionModel().selectedRows()}
        return [r for r in (self.release_model.release_at(row) for row in sorted(rows)) if r]

    # --- реакции ---------------------------------------------------------

    def _on_anime_selected(self, current: QModelIndex, _previous: QModelIndex) -> None:
        anime = self.anime_model.anime_at(current.row()) if current.isValid() else None
        self._current = anime
        self._update_details(anime)

    def _update_details(self, anime: Anime | None) -> None:
        enabled = anime is not None
        for widget in (
            self.check_button,
            self.open_page_button,
            self.auto_check,
            self.favorite_check,
        ):
            widget.setEnabled(enabled)

        if anime is None:
            self.detail_title.setText("Выберите подписку")
            self.detail_meta.setText("")
            self.release_model.set_items([])
            return

        self.detail_title.setText(anime.title)
        checked = (
            anime.last_check_at.strftime("%d.%m.%Y %H:%M")
            if anime.last_check_at
            else "ещё не проверялось"
        )
        status = "ошибка: " + anime.last_error if anime.last_error else "в порядке"
        self.detail_meta.setText(
            f"Источник: {anime.source} · Проверено: {checked} · Состояние: {status}\n{anime.url}"
        )

        # Сигналы чекбоксов не должны срабатывать от программной установки.
        for widget, value in ((self.auto_check, anime.auto_download), (self.favorite_check, anime.is_favorite)):
            widget.blockSignals(True)
            widget.setChecked(value)
            widget.blockSignals(False)

    def _on_auto_toggled(self, checked: bool) -> None:
        if self._current:
            self.auto_download_toggled.emit(self._current, checked)

    def _on_favorite_toggled(self, checked: bool) -> None:
        if self._current:
            self.favorite_toggled.emit(self._current, checked)

    def _on_release_double_click(self, index: QModelIndex) -> None:
        release = self.release_model.release_at(index.row())
        if release:
            self.send_requested.emit(release)

    # --- контекстные меню -------------------------------------------------

    def _anime_menu(self, position: QPoint) -> None:
        index = self.anime_list.indexAt(position)
        anime = self.anime_model.anime_at(index.row()) if index.isValid() else None
        if anime is None:
            return

        menu = QMenu(self)
        check = QAction("Проверить обновления", menu)
        check.triggered.connect(lambda: self.check_requested.emit(anime))
        menu.addAction(check)

        seen = QAction("Пометить все просмотренными", menu)
        seen.triggered.connect(lambda: self.mark_seen_requested.emit(anime.id))
        menu.addAction(seen)

        favorite = QAction(
            "Убрать из избранного" if anime.is_favorite else "В избранное", menu
        )
        favorite.triggered.connect(
            lambda: self.favorite_toggled.emit(anime, not anime.is_favorite)
        )
        menu.addAction(favorite)
        menu.addSeparator()

        remove = QAction("Удалить подписку", menu)
        remove.triggered.connect(lambda: self.remove_requested.emit(anime))
        menu.addAction(remove)
        menu.exec(self.anime_list.viewport().mapToGlobal(position))

    def _release_menu(self, position: QPoint) -> None:
        index = self.releases.indexAt(position)
        release = self.release_model.release_at(index.row()) if index.isValid() else None
        if release is None:
            return

        menu = QMenu(self)
        send = QAction("Отправить в торрент-клиент", menu)
        send.triggered.connect(lambda: self.send_requested.emit(release))
        menu.addAction(send)

        save = QAction("Скачать torrent-файл…", menu)
        save.triggered.connect(lambda: self.save_requested.emit(release))
        menu.addAction(save)

        default_client = QAction("Открыть в клиенте по умолчанию", menu)
        default_client.triggered.connect(
            lambda: self.open_default_client_requested.emit(release)
        )
        menu.addAction(default_client)
        menu.addSeparator()

        copy_link = QAction("Копировать ссылку", menu)
        copy_link.triggered.connect(lambda: self.copy_link_requested.emit(release))
        menu.addAction(copy_link)

        # Источник может не отдавать magnet — тогда пункт неактивен (ТЗ §7).
        copy_magnet = QAction("Копировать magnet", menu)
        copy_magnet.setEnabled(bool(release.magnet))
        copy_magnet.triggered.connect(lambda: self.copy_magnet_requested.emit(release))
        menu.addAction(copy_magnet)
        menu.exec(self.releases.viewport().mapToGlobal(position))


def source_state_label(state: str) -> str:
    try:
        return SOURCE_STATE_LABELS[SourceState(state)]
    except ValueError:
        return state
