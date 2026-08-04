"""Экран «Библиотека»: список подписок и детали выбранного аниме (ТЗ §6, §7)."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPixmap
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
from .palette import DARK, Palette

ROW_HEIGHT = 62


class AnimeDelegate(QStyledItemDelegate):
    """Строка подписки: избранное, счётчик новых, дата проверки, состояние."""

    def __init__(self, parent=None, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self.palette = palette

    def set_palette(self, palette: Palette) -> None:
        self.palette = palette

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
        colors = self.palette
        if selected or hovered:
            painter.setBrush(QColor(colors.selection) if selected else QColor(colors.hover))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 8, 8)

        left = rect.left() + 12
        if anime.is_favorite:
            painter.setPen(QColor(colors.star))
            painter.drawText(
                rect.adjusted(0, 8, 0, 0), int(Qt.AlignmentFlag.AlignLeft), "  ★"
            )
            left += 16

        title_font = QFont(option.font)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(colors.text))
        painter.drawText(
            rect.adjusted(left - rect.left(), 8, -60, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            anime.title,
        )

        painter.setFont(option.font)
        painter.setPen(QColor(colors.text_muted))
        painter.drawText(
            rect.adjusted(left - rect.left(), 30, -60, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
            self._subtitle(anime),
        )

        if anime.new_count:
            self._paint_badge(painter, option, rect, str(anime.new_count), colors)
        elif anime.last_check_ok is False:
            painter.setPen(QColor(colors.danger))
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
    def _paint_badge(painter: QPainter, option, rect, text: str, colors: Palette) -> None:
        badge = rect.adjusted(rect.width() - 46, (ROW_HEIGHT - 22) // 2 - 3, -12, 0)
        badge.setHeight(22)
        painter.setBrush(QColor(colors.accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(badge, 11, 11)
        painter.setPen(QColor(colors.accent_text))
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

    unseen_requested = Signal(object)         # Release — вернуть в новые
    open_magnet_requested = Signal(object)
    metadata_refresh_requested = Signal(object)   # Anime — обновить справку
    metadata_rebind_requested = Signal(object)    # Anime — выбрать другой тайтл

    def __init__(self, palette: Palette = DARK) -> None:
        super().__init__()
        self.anime_model = AnimeListModel()
        self.release_model = ReleaseTableModel()
        self._all_anime: list[Anime] = []
        self._current: Anime | None = None
        self._palette = palette
        self._build()

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.anime_list.itemDelegate().set_palette(palette)
        self.anime_list.viewport().update()
        self._update_sources_label()

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
        self.filter_box.addItems(
            ["Все", "Только с новыми", "Избранное", "С ошибками", "С автозагрузкой"]
        )
        self.filter_box.currentIndexChanged.connect(self._apply_filters)
        layout.addWidget(self.filter_box)

        # Фильтр по источнику (ТЗ §15); заполняется из самих подписок.
        self.source_box = QComboBox()
        self.source_box.addItem("Все источники", None)
        self.source_box.currentIndexChanged.connect(self._apply_filters)
        layout.addWidget(self.source_box)

        self.anime_list = QListView()
        self.anime_list.setModel(self.anime_model)
        self.anime_list.setMouseTracking(True)
        self.anime_list.setItemDelegate(AnimeDelegate(self.anime_list, self._palette))
        self.anime_list.setFrameShape(QListView.Shape.NoFrame)
        self.anime_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.anime_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.anime_list.customContextMenuRequested.connect(self._anime_menu)
        self.anime_list.selectionModel().currentChanged.connect(self._on_anime_selected)
        layout.addWidget(self.anime_list, 1)

        # Состояние источников (ТЗ §21): пользователь должен понимать,
        # почему новых серий нет — сайт лёг или изменилась вёрстка.
        self.sources_label = QLabel("Источники: нет данных")
        self.sources_label.setObjectName("muted")
        self.sources_label.setWordWrap(True)
        self.sources_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.sources_label)
        return panel

    def _build_right(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 14, 14, 14)
        layout.setSpacing(10)

        self.detail_title = QLabel("Выберите подписку")
        self.detail_title.setObjectName("screenTitle")
        layout.addWidget(self.detail_title)

        header = QHBoxLayout()
        header.setSpacing(14)
        self.poster = QLabel()
        self.poster.setFixedSize(96, 136)
        self.poster.setScaledContents(True)
        self.poster.setVisible(False)
        header.addWidget(self.poster, 0, Qt.AlignmentFlag.AlignTop)

        meta_column = QVBoxLayout()
        meta_column.setSpacing(6)
        self.detail_meta = QLabel("")
        self.detail_meta.setObjectName("muted")
        self.detail_meta.setWordWrap(True)
        meta_column.addWidget(self.detail_meta)

        # Справочные данные Shikimori/AniList (нумерация серий у них своя).
        self.metadata_label = QLabel("")
        self.metadata_label.setObjectName("muted")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextFormat(Qt.TextFormat.RichText)
        meta_column.addWidget(self.metadata_label)

        meta_buttons = QHBoxLayout()
        self.metadata_button = QPushButton("Обновить справку")
        self.metadata_button.setObjectName("secondaryButton")
        self.metadata_button.clicked.connect(
            lambda: self.metadata_refresh_requested.emit(self._current)
        )
        meta_buttons.addWidget(self.metadata_button)

        self.rebind_button = QPushButton("Выбрать тайтл…")
        self.rebind_button.setObjectName("secondaryButton")
        self.rebind_button.clicked.connect(
            lambda: self.metadata_rebind_requested.emit(self._current)
        )
        meta_buttons.addWidget(self.rebind_button)
        meta_buttons.addStretch(1)
        meta_column.addLayout(meta_buttons)

        meta_column.addStretch(1)
        header.addLayout(meta_column, 1)
        layout.addLayout(header)

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
        self._refresh_source_box()
        self._apply_filters()
        if current_id is not None:
            self.select_anime(current_id)

    def _refresh_source_box(self) -> None:
        sources = sorted({anime.source for anime in self._all_anime})
        if [self.source_box.itemData(i) for i in range(1, self.source_box.count())] == sources:
            return

        previous = self.source_box.currentData()
        self.source_box.blockSignals(True)
        self.source_box.clear()
        self.source_box.addItem("Все источники", None)
        for source in sources:
            self.source_box.addItem(source, source)
        index = self.source_box.findData(previous)
        self.source_box.setCurrentIndex(max(index, 0))
        self.source_box.blockSignals(False)

    def set_source_states(self, states: dict[str, dict]) -> None:
        """Диагностика источников (ТЗ §21)."""
        self._source_states = states
        self._update_sources_label()

    def _update_sources_label(self) -> None:
        states = getattr(self, "_source_states", {})
        if not states:
            self.sources_label.setText("Источники: проверок ещё не было")
            self.sources_label.setToolTip("")
            return

        parts, tips = [], []
        for source, info in sorted(states.items()):
            state = info.get("state", "")
            label = source_state_label(state)
            color = self._palette.text_muted if state == SourceState.OK else self._palette.danger
            parts.append(f"{source} — <span style='color:{color}'>{label}</span>")

            checked = info.get("checked_at")
            tip = f"{source}: {label}"
            if checked:
                tip += f", проверено {checked:%d.%m %H:%M}"
            if info.get("message"):
                tip += f"\n{info['message']}"
            tips.append(tip)

        self.sources_label.setText("Источники: " + " · ".join(parts))
        self.sources_label.setToolTip("\n\n".join(tips))

    def _apply_filters(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter_box.currentIndex()
        source = self.source_box.currentData()

        def keep(anime: Anime) -> bool:
            if text and text not in anime.title.lower():
                return False
            if source and anime.source != source:
                return False
            match mode:
                case 1:
                    return anime.new_count > 0
                case 2:
                    return anime.is_favorite
                case 3:
                    return anime.last_check_ok is False
                case 4:
                    return anime.auto_download
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
            self.metadata_button,
            self.rebind_button,
        ):
            widget.setEnabled(enabled)

        if anime is None:
            self.detail_title.setText("Выберите подписку")
            self.detail_meta.setText("")
            self.metadata_label.setText("")
            self.poster.setVisible(False)
            self.release_model.set_items([])
            return

        self._update_poster(anime)

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

    def set_metadata(self, data: dict | None) -> None:
        """Показывает справку. episodes_* сознательно не выводим как прогресс:
        нумерация справочника не совпадает с нумерацией раздач на трекере."""
        if not data:
            self.metadata_label.setText(
                "<i>Справочные данные не загружены — нажмите «Обновить справку»</i>"
            )
            return

        parts = []
        if data.get("title_romaji"):
            parts.append(f"<b>{data['title_romaji']}</b>")
        for key, fmt in (("kind", "{}"), ("status", "{}"), ("score", "оценка {}")):
            if data.get(key):
                parts.append(fmt.format(data[key]))
        if data.get("episodes_total"):
            parts.append(f"эпизодов по справочнику: {data['episodes_total']}")

        lines = [" · ".join(parts)] if parts else []
        if data.get("genres"):
            lines.append(", ".join(data["genres"][:6]))
        if data.get("next_episode_at"):
            number = data.get("next_episode_number")
            label = f"серия {number}" if number else "следующая серия"
            when = data["next_episode_at"].strftime("%d.%m.%Y %H:%M")
            colour = self._palette.accent
            lines.append(f"<span style='color:{colour}'>Ожидается {label}: {when}</span>")

        self.metadata_label.setText("<br>".join(lines))

    def _update_poster(self, anime: Anime) -> None:
        """Постер скачивается при добавлении подписки; если файла нет — просто прячем."""
        pixmap = QPixmap(anime.poster_path) if anime.poster_path else QPixmap()
        if pixmap.isNull():
            self.poster.setVisible(False)
            return
        self.poster.setPixmap(pixmap)
        self.poster.setVisible(True)

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

        # Источник может не отдавать magnet — тогда пункты неактивны (ТЗ §7).
        open_magnet = QAction("Открыть magnet", menu)
        open_magnet.setEnabled(bool(release.magnet))
        open_magnet.triggered.connect(lambda: self.open_magnet_requested.emit(release))
        menu.addAction(open_magnet)
        menu.addSeparator()

        copy_link = QAction("Копировать ссылку", menu)
        copy_link.triggered.connect(lambda: self.copy_link_requested.emit(release))
        menu.addAction(copy_link)

        copy_magnet = QAction("Копировать magnet", menu)
        copy_magnet.setEnabled(bool(release.magnet))
        copy_magnet.triggered.connect(lambda: self.copy_magnet_requested.emit(release))
        menu.addAction(copy_magnet)
        menu.addSeparator()

        # Обратное действие к пометке просмотренным (ТЗ §7).
        unseen = QAction("Вернуть в новые", menu)
        unseen.setEnabled(release.is_seen or release.state != "new")
        unseen.triggered.connect(lambda: self.unseen_requested.emit(release))
        menu.addAction(unseen)
        menu.exec(self.releases.viewport().mapToGlobal(position))


def source_state_label(state: str) -> str:
    try:
        return SOURCE_STATE_LABELS[SourceState(state)]
    except ValueError:
        return state
