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
        # Собственный пул позволяет дождаться только наших задач перед закрытием БД.
        self.pool = pool or QThreadPool()
        self._active: set[Worker] = set()
        self._accepting = True

    def start(
        self,
        fn: Callable,
        *args,
        on_done: Callable | None = None,
        on_failed: Callable | None = None,
        **kwargs,
    ) -> Worker:
        if not self._accepting:
            raise RuntimeError("Запуск фоновых задач после shutdown запрещён")
        worker = Worker(fn, *args, **kwargs)
        self._active.add(worker)

        worker.signals.finished.connect(
            lambda result: self._finish(worker, on_done, result)
        )
        worker.signals.failed.connect(
            lambda error: self._finish(worker, on_failed, error)
        )

        self.pool.start(worker)
        return worker

    def _release(self, worker: Worker) -> None:
        self._active.discard(worker)

    def _finish(self, worker: Worker, callback: Callable | None, value: object) -> None:
        # Освобождение и callback идут одним queued-вызовом: наблюдатель результата
        # уже не увидит завершённый Worker в active.
        self._release(worker)
        if self._accepting and callback is not None:
            callback(value)

    def shutdown(self, timeout_ms: int = -1) -> bool:
        """Запрещает новые задачи, отключает колбэки и ждёт свой пул.

        Вызывается при закрытии приложения: результат, доставленный после
        уничтожения окна, иначе падает с «Signal source has been deleted».
        """
        self._accepting = False
        for worker in tuple(self._active):
            for signal in (worker.signals.finished, worker.signals.failed):
                try:
                    signal.disconnect()
                except RuntimeError:
                    pass
        finished = self.pool.waitForDone(timeout_ms)
        if finished:
            self._active.clear()
        return finished

    def clear(self) -> None:
        """Совместимый алиас полного завершения управляемого пула."""
        self.shutdown()

    @property
    def active_count(self) -> int:
        return len(self._active)
