"""Экран «Новые серии» — стартовый (ТЗ §5)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Release, ReleaseState
from .models import RELEASE_ROLE, FeedModel
from .palette import DARK, Palette

CARD_HEIGHT = 76
CARD_MARGIN = 6
BUTTON_WIDTH = 116
BUTTON_HEIGHT = 32
SEEN_WIDTH = 118
BUTTON_GAP = 8


class FeedDelegate(QStyledItemDelegate):
    """Карточка серии с кнопкой «Скачать» прямо в строке (макет из ТЗ §5)."""

    download_clicked = Signal(QModelIndex)
    seen_clicked = Signal(QModelIndex)

    def __init__(self, parent=None, palette: Palette = DARK) -> None:
        super().__init__(parent)
        self.palette = palette

    def set_palette(self, palette: Palette) -> None:
        self.palette = palette

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), CARD_HEIGHT + CARD_MARGIN)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        release: Release = index.data(RELEASE_ROLE)
        if release is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        card = self._card_rect(option.rect)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)

        colors = self.palette
        background = QColor(colors.surface)
        if selected:
            background = QColor(colors.selection)
        elif hovered:
            background = QColor(colors.hover)
        painter.setBrush(background)
        painter.setPen(QPen(QColor(colors.border), 1))
        painter.drawRoundedRect(card, 10, 10)

        failed = release.state == ReleaseState.ERROR
        # Полоска слева: акцент на новинке, красный — на неудачной отправке.
        painter.setBrush(QColor(colors.danger) if failed else QColor(colors.accent))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRect(card.left(), card.top(), 4, card.height()), 2, 2)

        text_left = card.left() + 18
        text_width = card.width() - BUTTON_WIDTH - SEEN_WIDTH - BUTTON_GAP - 48

        title_font = QFont(option.font)
        title_font.setBold(True)
        title_font.setPointSize(option.font.pointSize() + 1)
        painter.setFont(title_font)
        painter.setPen(QColor(colors.text))
        painter.drawText(
            QRect(text_left, card.top() + 12, text_width, 22),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            release.anime_title or "",
        )

        painter.setFont(option.font)
        painter.setPen(QColor(colors.danger) if failed else QColor(colors.text_muted))
        painter.drawText(
            QRect(text_left, card.top() + 38, text_width, 20),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._subtitle(release),
        )

        self._paint_button(painter, option, card, hovered, failed)
        painter.restore()

    @staticmethod
    def _subtitle(release: Release) -> str:
        if release.state == ReleaseState.ERROR and release.state_message:
            return f"{release.episode_label} · {release.state_message}"
        parts = [release.episode_label, release.size_label]
        if release.published_at:
            parts.append(release.published_at.strftime("%d.%m.%Y"))
        if release.seeders is not None:
            parts.append(f"сиды {release.seeders}")
        return " · ".join(parts)

    def _paint_button(self, painter: QPainter, option, card: QRect, hovered: bool, failed: bool):
        rect = self._button_rect(card)
        painter.setBrush(
            QColor(self.palette.accent_hover if hovered else self.palette.accent)
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 8, 8)

        painter.setFont(option.font)
        painter.setPen(QColor(self.palette.accent_text))
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), "Повторить" if failed else "Скачать")

        seen = self._seen_rect(card)
        painter.setBrush(QColor(self.palette.hover if hovered else self.palette.surface))
        painter.setPen(QPen(QColor(self.palette.border), 1))
        painter.drawRoundedRect(seen, 8, 8)
        painter.setPen(QColor(self.palette.text_muted))
        painter.drawText(seen, int(Qt.AlignmentFlag.AlignCenter), "Просмотрено")

    @staticmethod
    def _card_rect(rect: QRect) -> QRect:
        return QRect(rect.left() + 4, rect.top() + 3, rect.width() - 12, CARD_HEIGHT)

    @staticmethod
    def _button_rect(card: QRect) -> QRect:
        return QRect(
            card.right() - BUTTON_WIDTH - 16,
            card.top() + (CARD_HEIGHT - BUTTON_HEIGHT) // 2,
            BUTTON_WIDTH,
            BUTTON_HEIGHT,
        )

    @staticmethod
    def _seen_rect(card: QRect) -> QRect:
        return QRect(
            card.right() - BUTTON_WIDTH - SEEN_WIDTH - BUTTON_GAP - 16,
            card.top() + (CARD_HEIGHT - BUTTON_HEIGHT) // 2,
            SEEN_WIDTH,
            BUTTON_HEIGHT,
        )

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease:
            card = self._card_rect(option.rect)
            point = event.position().toPoint()
            if self._button_rect(card).contains(point):
                self.download_clicked.emit(index)
                return True
            if self._seen_rect(card).contains(point):
                self.seen_clicked.emit(index)
                return True
        return False


class FeedView(QWidget):
    """Лента со сводной кнопкой «Скачать всё» (ТЗ §14)."""

    download_requested = Signal(object)  # Release
    download_all_requested = Signal()
    mark_seen_requested = Signal(object)
    release_seen_requested = Signal(object)  # Release
    open_anime_requested = Signal(object)

    def __init__(self, palette: Palette = DARK) -> None:
        super().__init__()
        self.model = FeedModel()
        self._palette = palette
        self._build()

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.delegate.set_palette(palette)
        self.list.viewport().update()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.title = QLabel("🔥 Новые серии")
        self.title.setObjectName("screenTitle")
        header.addWidget(self.title)
        header.addStretch(1)

        self.mark_seen_button = QPushButton("Пометить просмотренными")
        self.mark_seen_button.setObjectName("secondaryButton")
        self.mark_seen_button.clicked.connect(lambda: self.mark_seen_requested.emit(None))
        header.addWidget(self.mark_seen_button)

        self.download_all_button = QPushButton("Скачать всё")
        self.download_all_button.setObjectName("primaryButton")
        self.download_all_button.clicked.connect(self.download_all_requested.emit)
        header.addWidget(self.download_all_button)
        layout.addLayout(header)

        self.empty_label = QLabel(
            "Новых серий нет.\nПроверьте обновления или добавьте подписку."
        )
        self.empty_label.setObjectName("emptyState")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setMouseTracking(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setUniformItemSizes(True)
        self.list.setFrameShape(QListView.Shape.NoFrame)

        self.delegate = FeedDelegate(self.list, self._palette)
        self.delegate.download_clicked.connect(self._on_button)
        self.delegate.seen_clicked.connect(self._on_seen_button)
        self.list.setItemDelegate(self.delegate)
        self.list.doubleClicked.connect(self._on_double_click)
        layout.addWidget(self.list, 1)

    def _on_button(self, index: QModelIndex) -> None:
        release = self.model.release_at(index.row())
        if release:
            self.download_requested.emit(release)

    def _on_seen_button(self, index: QModelIndex) -> None:
        release = self.model.release_at(index.row())
        if release:
            self.release_seen_requested.emit(release)

    def _on_double_click(self, index: QModelIndex) -> None:
        release = self.model.release_at(index.row())
        if release:
            self.open_anime_requested.emit(release.anime_id)

    def set_releases(self, releases: list[Release]) -> None:
        self.model.set_releases(releases)
        has_items = bool(releases)
        self.list.setVisible(has_items)
        self.empty_label.setVisible(not has_items)
        self.download_all_button.setEnabled(has_items)
        self.mark_seen_button.setEnabled(has_items)
        self.title.setText(f"🔥 Новые серии ({len(releases)})" if has_items else "🔥 Новые серии")

    def releases(self) -> list[Release]:
        return self.model.all()
