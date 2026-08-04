"""Мелкие виджеты общего назначения."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """Однострочная метка, которая никогда не растягивает окно.

    Длинное сообщение об ошибке в строке состояния иначе задаёт минимальную
    ширину всему окну: колонки таблиц уезжают за край экрана.
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._update_elided()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_elided()

    def _update_elided(self) -> None:
        metrics = QFontMetrics(self.font())
        available = max(self.width() - 8, 60)
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available))
