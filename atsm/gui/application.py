"""Запуск графического приложения."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ..app import AppContext
from .icons import app_icon
from .main_window import MainWindow

THEME = Path(__file__).with_name("theme.qss")


def run_gui(ctx: AppContext) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ATSM")
    app.setOrganizationName("ATSM")
    app.setWindowIcon(app_icon())
    # Приложение живёт в трее и после закрытия окна (ТЗ §16).
    app.setQuitOnLastWindowClosed(False)

    if THEME.exists():
        app.setStyleSheet(THEME.read_text(encoding="utf-8"))

    window = MainWindow(ctx)
    window.show()

    if not QSystemTrayIcon.isSystemTrayAvailable():
        logger.warning("Системный трей недоступен")
        QMessageBox.information(
            window,
            "Трей недоступен",
            "Системный трей недоступен: приложение будет работать только при открытом окне.",
        )

    logger.info("Интерфейс запущен")
    return app.exec()
