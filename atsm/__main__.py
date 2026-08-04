"""Точка входа приложения."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

from . import __version__
from .app import bootstrap


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="atsm", description="Anime Torrent Subscription Manager")
    parser.add_argument(
        "--data-dir", type=Path, help="каталог данных (по умолчанию %%APPDATA%%\\ATSM)"
    )
    parser.add_argument(
        "--no-gui", action="store_true", help="только инициализация ядра, без интерфейса"
    )
    parser.add_argument("--version", action="version", version=f"ATSM {__version__}")
    args = parser.parse_args(argv)

    try:
        ctx = bootstrap(data_dir=args.data_dir)
    except Exception as exc:  # noqa: BLE001 — на старте показываем причину и выходим
        print(f"Не удалось запустить приложение: {exc}", file=sys.stderr)
        return 1

    if args.no_gui:
        from .parsers import ParserRegistry

        logger.info("Каталог данных: {}", ctx.paths.root)
        logger.info("Таблицы: {}", ", ".join(ctx.db.table_names()))
        # Заодно проверяет, что плагины находятся в собранном .exe.
        parsers = ParserRegistry(ctx.settings).all()
        logger.info("Источники: {}", ", ".join(p.display_name for p in parsers) or "нет")
        logger.info("Подписок: {}", len(ctx.repos.anime.list()))
        logger.info("Новых серий: {}", ctx.repos.releases.feed_count())
        ctx.shutdown()
        return 0

    from .gui.application import run_gui

    return run_gui(ctx)


if __name__ == "__main__":
    raise SystemExit(main())
