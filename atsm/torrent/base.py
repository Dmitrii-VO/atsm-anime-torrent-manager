"""Контракт торрент-клиента (ТЗ §10).

Абстракция заготовлена под Transmission и Deluge в версии 2: ядру всё
равно, какой клиент стоит за интерфейсом.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class TorrentClientError(Exception):
    """Клиент недоступен, отверг данные или не авторизовал запрос."""


@dataclass(slots=True)
class AddResult:
    ok: bool
    message: str = ""
    info_hash: str | None = None


class BaseTorrentClient(ABC):
    name: str = ""
    display_name: str = ""

    @abstractmethod
    def test_connection(self) -> str:
        """Возвращает версию клиента или бросает TorrentClientError."""

    @abstractmethod
    def add_torrent_file(self, data: bytes, filename: str) -> AddResult:
        """Отправляет содержимое .torrent — основной путь для astar (ТЗ §10)."""

    @abstractmethod
    def add_magnet(self, magnet: str) -> AddResult:
        """Для источников, отдающих magnet."""
