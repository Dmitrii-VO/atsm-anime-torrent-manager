"""Запуск графического приложения."""

from __future__ import annotations

from loguru import logger
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ..app import AppContext
from .icons import app_icon
from .main_window import MainWindow
from .palette import palette_for, stylesheet


def run_gui(ctx: AppContext) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("ATSM")
    app.setOrganizationName("ATSM")
    app.setWindowIcon(app_icon())
    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    # Без доступного трея окно нельзя спрятать без способа вернуть его.
    app.setQuitOnLastWindowClosed(not (ctx.settings.minimize_to_tray and tray_available))

    app.setStyleSheet(stylesheet(palette_for(ctx.settings.theme)))

    window = MainWindow(ctx, tray_available=tray_available)
    window.show()

    if not tray_available:
        logger.warning("Системный трей недоступен")
        QMessageBox.information(
            window,
            "Трей недоступен",
            "Системный трей недоступен: приложение будет работать только при открытом окне.",
        )

    logger.info("Интерфейс запущен")
    return app.exec()
