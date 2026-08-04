"""Цветовые схемы (ТЗ §23 «тема оформления», §24 «тёмная тема»).

Цвета нужны в двух местах: в таблице стилей и в делегатах, которые рисуют
карточки вручную. Поэтому единственный источник — этот модуль, а QSS
собирается из шаблона подстановкой.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Template

TEMPLATE = Path(__file__).with_name("theme.qss.tmpl")


@dataclass(frozen=True, slots=True)
class Palette:
    name: str
    bg: str            # фон окна
    surface: str       # панели, поля ввода, карточки
    surface_alt: str   # чередующиеся строки таблиц
    hover: str
    selection: str
    border: str
    text: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_text: str
    danger: str
    star: str
    disabled_bg: str
    disabled_text: str


DARK = Palette(
    name="dark",
    bg="#1b1f2b",
    surface="#232837",
    surface_alt="#1f2431",
    hover="#2a3040",
    selection="#2f3648",
    border="#39415a",
    text="#dfe3ec",
    text_muted="#8b93a7",
    accent="#5b3fe0",
    accent_hover="#6d4dff",
    accent_pressed="#4c33c4",
    accent_text="#ffffff",
    danger="#e5484d",
    star="#f5b544",
    disabled_bg="#232837",
    disabled_text="#5c6376",
)

LIGHT = Palette(
    name="light",
    bg="#f4f5f8",
    surface="#ffffff",
    surface_alt="#f7f8fb",
    hover="#eceef4",
    selection="#e2e5f5",
    border="#d2d6e0",
    text="#1e2230",
    text_muted="#6b7285",
    accent="#5b3fe0",
    accent_hover="#6d4dff",
    accent_pressed="#4c33c4",
    accent_text="#ffffff",
    danger="#c2313a",
    star="#d99512",
    disabled_bg="#eceef4",
    disabled_text="#a3a9b8",
)

PALETTES = {palette.name: palette for palette in (DARK, LIGHT)}
THEME_LABELS = {"dark": "Тёмная", "light": "Светлая"}


def palette_for(theme: str) -> Palette:
    return PALETTES.get(theme, DARK)


def stylesheet(palette: Palette) -> str:
    """Собирает QSS из шаблона.

    Подстановка через string.Template, а не format: в QSS фигурные скобки
    занимает сам синтаксис правил.
    """
    template = Template(TEMPLATE.read_text(encoding="utf-8"))
    return template.safe_substitute(
        {field: getattr(palette, field) for field in palette.__slots__}
    )
