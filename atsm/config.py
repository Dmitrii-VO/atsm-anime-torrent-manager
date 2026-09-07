"""Пути приложения и пользовательские настройки (ТЗ §23).

Каталог данных: %APPDATA%\\ATSM. Переопределяется переменной окружения
ATSM_DATA_DIR — так тесты и портативный запуск не трогают реальный профиль.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from loguru import logger
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
    # В settings.json не пишется: живёт в Windows Credential Manager (ТЗ §29.2).
    password: str = ""
    category: str = "anime"
    save_path: str = ""
    add_paused: bool = False
    # Качать части по порядку — тогда серию можно смотреть, не дожидаясь конца.
    sequential_download: bool = False
    # Пачка «1-4» превращается в «1-5»: старую раздачу из клиента убираем,
    # файлы не трогаем — они уже скачаны и нужны новой раздаче.
    delete_replaced: bool = True

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"


class SourceSettings(BaseModel):
    """Базовый хост источника и список зеркал (ТЗ §3, §23)."""

    astar_host: str = DEFAULT_ASTAR_MIRRORS[0]
    astar_mirrors: list[str] = Field(default_factory=lambda: list(DEFAULT_ASTAR_MIRRORS))

    # RuTracker закрыт Cloudflare: приложение ходит сессией, которую пользователь
    # открыл в браузере сам. Куки в settings.json не пишутся — они в хранилище
    # Windows, как и пароль qBittorrent.
    rutracker_host: str = "rutracker.org"
    rutracker_cookies: str = ""
    # cf_clearance привязан к User-Agent, поэтому UA обязан совпадать с браузерным.
    rutracker_user_agent: str = ""
    rutracker_proxy: str = ""


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


# Секреты хранятся в системном хранилище: на Windows это Credential Manager
# (ТЗ §29.2). Ключ записи — имя того, чей это доступ.
KEYRING_SERVICE = "ATSM"
KEYRING_USER = "qbittorrent"
KEYRING_RUTRACKER = "rutracker"
# Credential Manager принимает не больше 1280 символов на запись (2560 байт
# UTF-16) и на 1281-м отвечает «CredWrite: неправильные данные». Куки RuTracker
# длиннее, поэтому секрет режется на части: «rutracker», «rutracker.2», …
SECRET_CHUNK = 1000


def _keyring():
    """Возвращает модуль keyring или None, если хранилища в системе нет."""
    try:
        import keyring

        from keyring.errors import NoKeyringError  # noqa: F401 — проверка целостности

        return keyring
    except Exception as exc:  # noqa: BLE001 — бэкенд ломается по-разному
        logger.debug("Хранилище паролей недоступно: {}", exc)
        return None


def _part_key(key: str, index: int) -> str:
    return key if index == 0 else f"{key}.{index + 1}"


def read_secret(key: str = KEYRING_USER) -> str:
    store = _keyring()
    if store is None:
        return ""
    try:
        parts = []
        while True:
            part = store.get_password(KEYRING_SERVICE, _part_key(key, len(parts)))
            if not part:
                break
            parts.append(part)
        return "".join(parts)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Секрет «{}» из хранилища не прочитан: {}", key, exc)
        return ""


def write_secret(value: str, key: str = KEYRING_USER) -> bool:
    """True, если секрет ушёл в системное хранилище, а не остался в файле."""
    store = _keyring()
    if store is None:
        return False

    chunks = [value[i : i + SECRET_CHUNK] for i in range(0, len(value), SECRET_CHUNK)]
    try:
        for index, chunk in enumerate(chunks):
            store.set_password(KEYRING_SERVICE, _part_key(key, index), chunk)
        # Секрет мог стать короче — лишние части удаляем, иначе при чтении
        # к новому значению приклеится хвост старого.
        index = len(chunks)
        while True:
            part_key = _part_key(key, index)
            if not store.get_password(KEYRING_SERVICE, part_key):
                break
            store.delete_password(KEYRING_SERVICE, part_key)
            index += 1
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Секрет «{}» не сохранён в хранилище Windows: {}", key, exc)
        return False


# Имена из времён, когда в хранилище лежал только пароль qBittorrent.
def read_password() -> str:
    return read_secret(KEYRING_USER)


def write_password(password: str) -> bool:
    return write_secret(password, KEYRING_USER)


def load_settings(path: Path | None = None) -> Settings:
    """Читает settings.json. Битый или отсутствующий файл — не повод падать."""
    target = path or paths().settings
    if not target.exists():
        return Settings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        settings = Settings.model_validate(raw)
    except (json.JSONDecodeError, ValueError, OSError):
        return Settings()

    if settings.qbittorrent.password:
        # Пароль из старой версии: переносим в хранилище и вычищаем из файла.
        if write_password(settings.qbittorrent.password):
            save_settings(settings, target)
            logger.info("Пароль qBittorrent перенесён в хранилище Windows")
    else:
        settings.qbittorrent.password = read_password()

    if not settings.sources.rutracker_cookies:
        settings.sources.rutracker_cookies = read_secret(KEYRING_RUTRACKER)
    return settings


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Атомарная запись: сначала во временный файл, затем замена."""
    target = path or paths().settings
    target.parent.mkdir(parents=True, exist_ok=True)
    data = settings.model_dump()
    # Пароль пишем в файл, только если системное хранилище недоступно, —
    # иначе приложение просто перестанет помнить его между запусками.
    if write_password(settings.qbittorrent.password):
        data["qbittorrent"]["password"] = ""
    if write_secret(settings.sources.rutracker_cookies, KEYRING_RUTRACKER):
        data["sources"]["rutracker_cookies"] = ""

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
