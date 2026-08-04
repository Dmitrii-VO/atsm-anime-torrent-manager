"""Планировщик проверок (ТЗ §11)."""

from __future__ import annotations

from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

JOB_ID = "check_all"


class CheckScheduler:
    """Периодический запуск проверки подписок.

    Колбэк вызывается в потоке планировщика, поэтому он обязан лишь
    отправить сигнал в главный поток, а не трогать интерфейс напрямую.
    """

    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self._scheduler = BackgroundScheduler(daemon=True)
        self._interval_minutes: int | None = None

    def start(self, interval_minutes: int) -> None:
        if not self._scheduler.running:
            self._scheduler.start()
        self.reschedule(interval_minutes)

    def reschedule(self, interval_minutes: int) -> None:
        if interval_minutes == self._interval_minutes:
            return
        self._interval_minutes = interval_minutes

        if self._scheduler.get_job(JOB_ID):
            self._scheduler.remove_job(JOB_ID)
        self._scheduler.add_job(
            self._run,
            trigger="interval",
            minutes=interval_minutes,
            id=JOB_ID,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("Автопроверка запланирована каждые {} мин", interval_minutes)

    def _run(self) -> None:
        try:
            self.callback()
        except Exception:  # noqa: BLE001 — падение задания не должно ронять планировщик
            logger.exception("Плановая проверка завершилась ошибкой")

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.debug("Планировщик остановлен")
