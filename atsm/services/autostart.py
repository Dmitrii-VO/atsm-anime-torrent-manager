"""Автозапуск вместе с Windows (ТЗ §23).

Через ключ реестра Run для текущего пользователя — не требует прав
администратора и не трогает системные настройки.
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ATSM"


def _command() -> str:
    if getattr(sys, "frozen", False):  # собранный .exe
        return f'"{sys.executable}"'
    return f'"{sys.executable}" -m atsm'


def set_autostart(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False

    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            logger.info("Автозапуск включён")
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
                logger.info("Автозапуск выключен")
            except FileNotFoundError:
                pass
    return True


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False

    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except OSError:
        return False


def executable_path() -> Path:
    return Path(sys.executable)
