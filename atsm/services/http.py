"""Общая HTTP-сессия для всех источников (ТЗ §11).

Без браузерных заголовков astar.bz отвечает 403 — это проверено, поэтому
заголовки не «на всякий случай», а обязательное условие работы.
"""

from __future__ import annotations

import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..config import HttpSettings


class RateLimiter:
    """Минимальная пауза между запросами к одному хосту."""

    def __init__(self, delay_sec: float) -> None:
        self.delay_sec = delay_sec
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        if self.delay_sec <= 0:
            return
        with self._lock:
            previous = self._last.get(host)
            now = time.monotonic()
            if previous is not None:
                remaining = self.delay_sec - (now - previous)
                if remaining > 0:
                    time.sleep(remaining)
                    now = time.monotonic()
            self._last[host] = now


def build_session(settings: HttpSettings) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": settings.accept_language,
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    retry = Retry(
        total=settings.retries,
        backoff_factor=settings.backoff_sec,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session
