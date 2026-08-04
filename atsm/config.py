"""Пути приложения и пользовательские настройки (ТЗ §23).

Каталог данных: %APPDATA%\\ATSM. Переопределяется переменной окружения
ATSM_DATA_DIR — так тесты и портативный запуск не трогают реальный профиль.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from . import APP_NAME

ENV_DATA_DIR = "ATSM_DATA_DIR"

# Реалистичные заголовки обязательны: без них astar.bz отвечает 403 (ТЗ §11, §26).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Домены источника ротируются, суффикс постоянен (ТЗ §3, §26).
DEFAULT_ASTAR_MIRRORS = [f"v{n}.astar.bz" for n in (30, 29, 28, 27, 26, 25, 20, 19)]


class Paths:
    """Раскладка файлов в каталоге данных."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.db = root / "atsm.db"
        self.settings = root / "settings.json"
        self.logs = root / "logs"
        self.log_file = self.logs / "atsm.log"
        self.torrents = root / "torrents"
        self.posters = root / "posters"

    def ensure(self) -> "Paths":
        for directory in (self.root, self.logs, self.torrents, self.posters):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def data_dir() -> Path:
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".local" / "share"
    return base / APP_NAME


@lru_cache(maxsize=1)
def paths() -> Paths:
    return Paths(data_dir())


class HttpSettings(BaseModel):
    user_agent: str = DEFAULT_USER_AGENT
    accept_language: str = "ru-RU,ru;q=0.9,en;q=0.8"
    timeout_sec: float = 15.0
    retries: int = 3
    backoff_sec: float = 1.5
    # Пауза между запросами к одному источнику, чтобы не создавать нагрузку.
    delay_between_requests_sec: float = 1.5


class QBittorrentSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    use_https: bool = False
    username: str = "admin"
    # TODO(открытый вопрос ТЗ §29.2): перенести в Windows Credential Manager.
    password: str = ""
    category: str = "anime"
    save_path: str = ""
    add_paused: bool = False

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"


class SourceSettings(BaseModel):
    """Базовый хост источника и список зеркал (ТЗ §3, §23)."""

    astar_host: str = DEFAULT_ASTAR_MIRRORS[0]
    astar_mirrors: list[str] = Field(default_factory=lambda: list(DEFAULT_ASTAR_MIRRORS))


class Settings(BaseModel):
    check_interval_minutes: int = 60
    check_on_startup: bool = True
    minimize_to_tray: bool = True
    autostart: bool = False
    notifications_enabled: bool = True
    # Подтягивать данные Shikimori/AniList сразу при добавлении подписки.
    metadata_autofetch: bool = True
    theme: str = "dark"
    log_level: str = "INFO"
    log_retention_days: int = 14
    sources: SourceSettings = Field(default_factory=SourceSettings)
    qbittorrent: QBittorrentSettings = Field(default_factory=QBittorrentSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)


def load_settings(path: Path | None = None) -> Settings:
    """Читает settings.json. Битый или отсутствующий файл — не повод падать."""
    target = path or paths().settings
    if not target.exists():
        return Settings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return Settings.model_validate(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return Settings()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Атомарная запись: сначала во временный файл, затем замена."""
    target = path or paths().settings
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(target)
