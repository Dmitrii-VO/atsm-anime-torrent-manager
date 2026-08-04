"""Логирование (ТЗ §22): файл с ротацией + буфер в памяти для экрана логов."""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger

FILE_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"
)
CONSOLE_FORMAT = "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}"


@dataclass(frozen=True, slots=True)
class LogRecord:
    time: datetime
    level: str
    message: str
    source: str


class LogBuffer:
    """Кольцевой буфер последних записей — источник данных для GUI-экрана логов.

    Хранит записи в памяти, чтобы не перечитывать файл при каждом открытии окна.
    """

    def __init__(self, capacity: int = 2000) -> None:
        self._records: deque[LogRecord] = deque(maxlen=capacity)

    def __call__(self, message) -> None:  # loguru sink
        record = message.record
        self._records.append(
            LogRecord(
                time=record["time"],
                level=record["level"].name,
                message=record["message"],
                source=f"{record['name']}:{record['line']}",
            )
        )

    def records(self) -> Iterable[LogRecord]:
        return tuple(self._records)

    def clear(self) -> None:
        self._records.clear()


log_buffer = LogBuffer()


def setup_logging(
    log_file: Path,
    level: str = "INFO",
    retention_days: int = 14,
    console: bool = True,
) -> None:
    logger.remove()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.add(
        log_file,
        level=level,
        format=FILE_FORMAT,
        rotation="5 MB",
        retention=f"{retention_days} days",
        encoding="utf-8",
        enqueue=True,  # проверки идут в фоновых потоках
        backtrace=False,
        diagnose=False,
    )
    logger.add(log_buffer, level=level, format="{message}")

    # В собранном .exe (--noconsole) stderr отсутствует.
    if console and sys.stderr is not None:
        _force_utf8_console()
        logger.add(sys.stderr, level=level, format=CONSOLE_FORMAT, colorize=True)


def _force_utf8_console() -> None:
    """Консоль Windows по умолчанию в cp866 — кириллица в логах превращается в кашу."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
