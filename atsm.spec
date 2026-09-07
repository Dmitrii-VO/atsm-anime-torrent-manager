# PyInstaller spec для ATSM.
#
# Сборка:
#   python tools/make_icon.py
#   pyinstaller atsm.spec --noconfirm
#
# Результат: dist/ATSM/ATSM.exe

from pathlib import Path

project = Path(SPECPATH)

# Файлы, которые код читает с диска, а не импортирует.
datas = [
    (str(project / "atsm" / "db" / "schema.sql"), "atsm/db"),
    (str(project / "atsm" / "gui" / "theme.qss.tmpl"), "atsm/gui"),
]

# Модули парсеров подгружаются реестром динамически, статический анализ их не видит.
hiddenimports = [
    "atsm.parsers.astar",
    "atsm.parsers.anilibria",
    "apscheduler.schedulers.background",
    "apscheduler.executors.pool",
    "apscheduler.triggers.interval",
    "apscheduler.jobstores.memory",
    # Бэкенды keyring подбираются через точки входа — анализатор их не видит.
    "keyring.backends.Windows",
    "keyring.backends.fail",
]

# Ненужные части Qt и научный стек тянут сотни мегабайт.
excludes = [
    "tkinter",
    "unittest",
    "pytest",
    "numpy",
    "matplotlib",
    # APScheduler умеет хранить задания в БД, и хуки тянут SQLAlchemy с
    # драйверами PostgreSQL. Планировщик у нас только в памяти.
    "sqlalchemy",
    "psycopg",
    "psycopg2",
    "pymysql",
    "MySQLdb",
    "PIL",
    "zstandard",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.Qt3DCore",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
]

a = Analysis(
    ["run_atsm.py"],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ATSM",
    debug=False,
    strip=False,
    upx=False,
    console=False,  # приложение с окном, консоль не нужна
    icon=str(project / "build" / "atsm.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ATSM",
)
