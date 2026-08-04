"""Готовит atsm.ico для сборки: иконка рисуется кодом, в репозитории её нет."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from atsm.gui.icons import app_pixmap  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "build" / "atsm.ico"


def main() -> int:
    # Отрисовка требует графического стека, но не окна.
    app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    if not app_pixmap(size=256).save(str(OUTPUT), "ICO"):
        print("Не удалось сохранить иконку", file=sys.stderr)
        return 1

    print(f"Иконка сохранена: {OUTPUT}")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
