"""Фоновые задачи (ТЗ §11): сеть не должна блокировать интерфейс.

Каждая задача — QRunnable в общем QThreadPool, результат приходит в главный
поток через сигналы (по умолчанию соединение очередью).
"""

from __future__ import annotations

from typing import Callable

from loguru import logger
from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    # Передаётся само исключение, а не текст: обработчику важен его тип,
    # чтобы отличить недоступный торрент-клиент от ошибки разбора страницы.
    failed = Signal(object)
    progress = Signal(str)


class Worker(QRunnable):
    """Выполняет функцию в пуле потоков и отдаёт результат сигналом."""

    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 — иначе исключение утонет в пуле
            logger.exception("Фоновая задача завершилась ошибкой")
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)
