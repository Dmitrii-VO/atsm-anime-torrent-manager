"""Системный трей и уведомления Windows (ТЗ §12, §16)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icons import app_icon


class Tray(QObject):
    check_requested = Signal()
    download_all_requested = Signal()
    show_requested = Signal()
    quit_requested = Signal()
    notification_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.icon = QSystemTrayIcon(app_icon(), parent)
        self.icon.setToolTip("ATSM — новых серий нет")

        menu = QMenu()
        self._add(menu, "Открыть приложение", self.show_requested)
        menu.addSeparator()
        self._add(menu, "Проверить обновления", self.check_requested)
        self.download_action = self._add(menu, "Скачать всё новое", self.download_all_requested)
        self.download_action.setEnabled(False)
        menu.addSeparator()
        self._add(menu, "Выход", self.quit_requested)

        self.menu = menu
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)
        self.icon.messageClicked.connect(self.notification_clicked.emit)

    @staticmethod
    def _add(menu: QMenu, title: str, signal: Signal) -> QAction:
        action = QAction(title, menu)
        action.triggered.connect(signal.emit)
        menu.addAction(action)
        return action

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_requested.emit()

    def show(self) -> None:
        self.icon.show()

    def hide(self) -> None:
        self.icon.hide()

    def set_new_count(self, count: int) -> None:
        self.icon.setIcon(app_icon(count))
        self.icon.setToolTip(
            f"ATSM — новых серий: {count}" if count else "ATSM — новых серий нет"
        )
        self.download_action.setEnabled(count > 0)

    def notify(self, title: str, message: str) -> None:
        if self.icon.isVisible():
            self.icon.showMessage(title, message, app_icon(), 6000)
