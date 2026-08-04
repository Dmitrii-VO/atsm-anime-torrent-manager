"""Фоновые задачи (ТЗ §11): сеть не должна блокировать интерфейс.

Каждая задача — QRunnable в общем QThreadPool, результат приходит в главный
поток через сигналы (по умолчанию соединение очередью).
"""

from __future__ import annotations

from typing import Callable

from loguru import logger
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


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
        # Временем жизни управляет WorkerRunner: автоудаление вместе со
        # сборкой мусора в Python отправляло сигналы в никуда.
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 — иначе исключение утонет в пуле
            logger.exception("Фоновая задача завершилась ошибкой")
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)


class WorkerRunner:
    """Запускает задачи и держит на них ссылки до завершения.

    Без этого Python собирает Worker сразу после `pool.start()`, вместе с ним
    исчезает QObject с сигналами, и результат, отправленный из фонового потока,
    до главного не доходит. Внешне это выглядит как вечная «загрузка»:
    операция отработала, но интерфейс об этом не узнал.

    Проверено стресс-тестом: без удержания ссылок из 300 задач до обработчика
    доходило около десяти.
    """

    def __init__(self, pool: QThreadPool | None = None) -> None:
        self.pool = pool or QThreadPool.globalInstance()
        self._active: set[Worker] = set()

    def start(
        self,
        fn: Callable,
        *args,
        on_done: Callable | None = None,
        on_failed: Callable | None = None,
        **kwargs,
    ) -> Worker:
        worker = Worker(fn, *args, **kwargs)
        self._active.add(worker)

        if on_done is not None:
            worker.signals.finished.connect(on_done)
        if on_failed is not None:
            worker.signals.failed.connect(on_failed)

        # Освобождаем ссылку уже в главном потоке, после доставки результата.
        worker.signals.finished.connect(lambda *_: self._release(worker))
        worker.signals.failed.connect(lambda *_: self._release(worker))

        self.pool.start(worker)
        return worker

    def _release(self, worker: Worker) -> None:
        self._active.discard(worker)

    @property
    def active_count(self) -> int:
        return len(self._active)
