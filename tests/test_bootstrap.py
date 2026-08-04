from __future__ import annotations

from pathlib import Path

from atsm.__main__ import main
from atsm.db import SCHEMA_VERSION


def test_bootstrap_creates_layout(ctx) -> None:
    assert ctx.paths.db.exists()
    assert ctx.paths.settings.exists()
    assert ctx.paths.logs.is_dir()
    assert ctx.paths.torrents.is_dir()
    assert ctx.db.current_version() == SCHEMA_VERSION


def test_bootstrap_writes_log(ctx) -> None:
    from loguru import logger

    logger.info("проверка записи в лог")
    logger.complete()  # sink работает с enqueue=True
    assert ctx.paths.log_file.exists()
    assert "проверка записи в лог" in ctx.paths.log_file.read_text(encoding="utf-8")


def test_bootstrap_is_repeatable(tmp_path: Path) -> None:
    from atsm.app import bootstrap

    first = bootstrap(data_dir=tmp_path / "d", console_log=False)
    first.shutdown()
    second = bootstrap(data_dir=tmp_path / "d", console_log=False)
    assert second.db.current_version() == SCHEMA_VERSION
    second.shutdown()


def test_main_entry_point(tmp_path: Path) -> None:
    """Без --no-gui точка входа поднимает интерфейс и не возвращает управление."""
    assert main(["--data-dir", str(tmp_path / "cli"), "--no-gui"]) == 0
    assert (tmp_path / "cli" / "atsm.db").exists()
