"""Plugin Manager (ТЗ §20).

Плагины собираются автоматически по подклассам BaseParser в пакете
atsm.parsers — добавление источника не требует правок в ядре.
"""

from __future__ import annotations

import importlib
import pkgutil

import requests
from loguru import logger

from ..config import Settings
from ..services.http import RateLimiter, build_session
from .base import BaseParser, ParserError


class ParserRegistry:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or build_session(settings.http)
        self.limiter = RateLimiter(settings.http.delay_between_requests_sec)
        self._parsers: dict[str, BaseParser] = {}
        self._discover()

    def _discover(self) -> None:
        package = importlib.import_module(__package__)
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name in {"base", "registry"}:
                continue
            importlib.import_module(f"{__package__}.{module_info.name}")

        for parser_cls in _all_subclasses(BaseParser):
            if not parser_cls.name:
                continue
            self._parsers[parser_cls.name] = self._instantiate(parser_cls)
        logger.debug("Загружены парсеры: {}", ", ".join(sorted(self._parsers)))

    def _instantiate(self, parser_cls: type[BaseParser]) -> BaseParser:
        return parser_cls(
            session=self.session,
            sources=self.settings.sources,
            timeout=self.settings.http.timeout_sec,
            limiter=self.limiter,
        )

    # --- доступ ----------------------------------------------------------

    def all(self) -> list[BaseParser]:
        return list(self._parsers.values())

    def get(self, name: str) -> BaseParser:
        try:
            return self._parsers[name]
        except KeyError:
            raise ParserError(f"Источник «{name}» не поддерживается") from None

    def for_url(self, url: str) -> BaseParser:
        for parser in self._parsers.values():
            if parser.match(url):
                return parser
        raise ParserError(
            "Ссылка не относится ни к одному поддерживаемому источнику. "
            "Поддерживаются: " + ", ".join(p.display_name for p in self._parsers.values())
        )

    def supports(self, url: str) -> bool:
        return any(parser.match(url) for parser in self._parsers.values())


def _all_subclasses(cls: type) -> list[type]:
    found = []
    for subclass in cls.__subclasses__():
        found.append(subclass)
        found.extend(_all_subclasses(subclass))
    return found
