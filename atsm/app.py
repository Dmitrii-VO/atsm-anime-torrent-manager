"""Сборка приложения: пути, настройки, логирование, БД.

Один контекст передаётся сервисам и GUI — вместо глобальных синглтонов,
чтобы тесты могли поднять приложение на временном каталоге.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from . import __version__
from .config import Paths, Settings, load_settings, paths, save_settings
from .db import Database
from .logging_setup import setup_logging


@dataclass(slots=True)
class AppContext:
    paths: Paths
    settings: Settings
    db: Database

    def save_settings(self) -> None:
        save_settings(self.settings, self.paths.settings)

    def shutdown(self) -> None:
        self.db.close()
        logger.info("Приложение остановлено")


def bootstrap(data_dir: Path | None = None, console_log: bool = True) -> AppContext:
    app_paths = (Paths(data_dir) if data_dir else paths()).ensure()

    settings = load_settings(app_paths.settings)
    setup_logging(
        app_paths.log_file,
        level=settings.log_level,
        retention_days=settings.log_retention_days,
        console=console_log,
    )
    logger.info("ATSM {} — запуск", __version__)
    logger.debug("Каталог данных: {}", app_paths.root)

    # Первый запуск: фиксируем настройки по умолчанию на диске, чтобы их
    # можно было править вручную.
    if not app_paths.settings.exists():
        save_settings(settings, app_paths.settings)
        logger.info("Создан файл настроек: {}", app_paths.settings)

    db = Database(app_paths.db)
    db.migrate()

    return AppContext(paths=app_paths, settings=settings, db=db)
