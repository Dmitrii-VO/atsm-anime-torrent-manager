"""Точка входа для сборки .exe.

PyInstaller запускает скрипт как __main__ вне пакета, поэтому относительные
импорты внутри atsm/__main__.py там не работают — нужен обычный модуль.
"""

from atsm.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
