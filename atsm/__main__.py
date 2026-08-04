"""Точка входа. Пока поднимает ядро без GUI (GUI появится на этапе 6)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from . import __version__
from .app import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atsm", description="Anime Torrent Subscription Manager")
    parser.add_argument("--data-dir", type=Path, help="каталог данных (по умолчанию %%APPDATA%%\\ATSM)")
    parser.add_argument("--version", action="version", version=f"ATSM {__version__}")
    args = parser.parse_args(argv)

    try:
        ctx = bootstrap(data_dir=args.data_dir)
    except Exception as exc:  # noqa: BLE001 — на старте показываем причину и выходим
        print(f"Не удалось запустить приложение: {exc}", file=sys.stderr)
        return 1

    logger.info("Каталог данных: {}", ctx.paths.root)
    logger.info("База данных: {}", ctx.paths.db)
    logger.info("Таблицы: {}", ", ".join(ctx.db.table_names()))
    logger.info("Интервал проверки: {} мин", ctx.settings.check_interval_minutes)
    logger.info("Источник astar: {}", ctx.settings.sources.astar_host)

    ctx.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
