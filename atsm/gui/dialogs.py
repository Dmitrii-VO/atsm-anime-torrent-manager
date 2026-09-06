"""Диалоги: добавление подписки, настройки, журнал (ТЗ §3, §22, §23)."""

from __future__ import annotations

from inspect import Parameter, signature

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Settings
from ..logging_setup import log_buffer
from ..services.autostart import is_autostart_enabled
from .palette import THEME_LABELS
from .models import HistoryTableModel
from .workers import WorkerRunner


class AddSubscriptionDialog(QDialog):
    """Добавление по ссылке с предварительной проверкой (ТЗ §3, способ 1)."""

    def __init__(self, subscriptions, parent=None) -> None:
        super().__init__(parent)
        self.subscriptions = subscriptions
        self.workers = WorkerRunner()
        self.result_info = None

        self.setWindowTitle("Добавить подписку")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(QLabel("Ссылка на страницу аниме:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://v30.astar.bz/7788-pozhiratel-zvezd.html")
        self.url_edit.textChanged.connect(self._reset_preview)
        layout.addWidget(self.url_edit)

        self.auto_check = QCheckBox("Скачивать новые серии автоматически")
        layout.addWidget(self.auto_check)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.check_button = QPushButton("Проверить ссылку")
        self.check_button.setObjectName("secondaryButton")
        self.check_button.clicked.connect(self._preview)
        buttons.addWidget(self.check_button)
        buttons.addStretch(1)

        self.box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.box.button(QDialogButtonBox.StandardButton.Ok).setText("Добавить")
        self.box.accepted.connect(self._add)
        self.box.rejected.connect(self.reject)
        buttons.addWidget(self.box)
        layout.addLayout(buttons)

    def url(self) -> str:
        return self.url_edit.text().strip()

    def auto_download(self) -> bool:
        return self.auto_check.isChecked()

    def _reset_preview(self) -> None:
        self.status.setText("")

    def _busy(self, busy: bool, message: str = "") -> None:
        self.check_button.setEnabled(not busy)
        self.box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not busy)
        if message:
            self.status.setText(message)

    def _preview(self) -> None:
        if not self.url():
            self.status.setText("Введите ссылку")
            return
        self._busy(True, "Загружаем страницу…")

        self.workers.start(
            self.subscriptions.preview,
            self.url(),
            on_done=self._preview_done,
            on_failed=lambda exc: self._busy(False, f"Ошибка: {exc}"),
        )

    def _preview_done(self, info) -> None:
        self._busy(False)
        latest = info.releases[0] if info.releases else None
        latest_text = f", последняя — {latest.episode_raw}" if latest else ""
        self.status.setText(f"«{info.title}»: раздач {len(info.releases)}{latest_text}")

    def _add(self) -> None:
        if not self.url():
            self.status.setText("Введите ссылку")
            return
        self._busy(True, "Добавляем подписку…")

        self.workers.start(
            self.subscriptions.add,
            self.url(),
            auto_download=self.auto_download(),
            on_done=self._added,
            on_failed=lambda exc: self._busy(False, f"Ошибка: {exc}"),
        )

    def _added(self, anime) -> None:
        self.result_info = anime
        self.accept()

    def done(self, result: int) -> None:
        self.workers.shutdown()
        super().done(result)


class SettingsDialog(QDialog):
    """Настройки приложения (ТЗ §23)."""

    def __init__(self, settings: Settings, torrent_client_factory, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.draft = settings.model_copy(deep=True)
        self.torrent_client_factory = torrent_client_factory
        self.workers = WorkerRunner()

        self.setWindowTitle("Настройки")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "Общие")
        tabs.addTab(self._client_tab(), "Торрент-клиент")
        tabs.addTab(self._sources_tab(), "Источники")
        layout.addWidget(tabs)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _general_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.interval = QSpinBox()
        self.interval.setRange(5, 24 * 60)
        self.interval.setSuffix(" мин")
        self.interval.setValue(self.draft.check_interval_minutes)
        form.addRow("Интервал проверки:", self.interval)

        self.check_on_startup = QCheckBox("Проверять при запуске")
        self.check_on_startup.setChecked(self.draft.check_on_startup)
        form.addRow("", self.check_on_startup)

        self.minimize_to_tray = QCheckBox("Сворачивать в трей вместо выхода")
        self.minimize_to_tray.setChecked(self.draft.minimize_to_tray)
        form.addRow("", self.minimize_to_tray)

        self.autostart = QCheckBox("Запускать вместе с Windows")
        # Реестр — источник правды: пользователь мог убрать запись мимо приложения.
        self.autostart.setChecked(is_autostart_enabled() or self.draft.autostart)
        form.addRow("", self.autostart)

        self.notifications = QCheckBox("Показывать уведомления")
        self.notifications.setChecked(self.draft.notifications_enabled)
        form.addRow("", self.notifications)

        self.metadata_autofetch = QCheckBox(
            "Загружать справочные данные при добавлении подписки"
        )
        self.metadata_autofetch.setChecked(self.draft.metadata_autofetch)
        form.addRow("", self.metadata_autofetch)

        self.theme = QComboBox()
        for key, label in THEME_LABELS.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(self.theme.findData(self.draft.theme), 0))
        form.addRow("Тема оформления:", self.theme)

        self.log_level = QComboBox()
        self.log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.log_level.setCurrentText(self.draft.log_level)
        form.addRow("Уровень журнала:", self.log_level)
        return page

    def _client_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        group = QGroupBox("qBittorrent Web API")
        form = QFormLayout(group)
        qbt = self.draft.qbittorrent

        self.host = QLineEdit(qbt.host)
        form.addRow("Адрес:", self.host)

        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(qbt.port)
        form.addRow("Порт:", self.port)

        self.https = QCheckBox("HTTPS")
        self.https.setChecked(qbt.use_https)
        form.addRow("", self.https)

        self.username = QLineEdit(qbt.username)
        form.addRow("Логин:", self.username)

        self.password = QLineEdit(qbt.password)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Пароль:", self.password)

        self.category = QLineEdit(qbt.category)
        form.addRow("Категория:", self.category)

        path_row = QHBoxLayout()
        self.save_path = QLineEdit(qbt.save_path)
        self.save_path.setPlaceholderText("по умолчанию в настройках клиента")
        path_row.addWidget(self.save_path)
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._pick_folder)
        path_row.addWidget(browse)
        form.addRow("Папка загрузки:", path_row)

        self.add_paused = QCheckBox("Добавлять на паузе")
        self.add_paused.setChecked(qbt.add_paused)
        form.addRow("", self.add_paused)

        self.sequential = QCheckBox("Качать по порядку (можно смотреть до конца загрузки)")
        self.sequential.setChecked(qbt.sequential_download)
        form.addRow("", self.sequential)

        self.delete_replaced = QCheckBox("Удалять из клиента пачки, заменённые новыми")
        self.delete_replaced.setToolTip("Файлы на диске остаются — их использует новая раздача")
        self.delete_replaced.setChecked(qbt.delete_replaced)
        form.addRow("", self.delete_replaced)
        layout.addWidget(group)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("Проверить соединение")
        self.test_button.clicked.connect(self._test_connection)
        test_row.addWidget(self.test_button)
        self.test_result = QLabel("")
        self.test_result.setObjectName("muted")
        test_row.addWidget(self.test_result, 1)
        layout.addLayout(test_row)
        layout.addStretch(1)
        return page

    def _sources_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)

        self.astar_host = QLineEdit(self.draft.sources.astar_host)
        form.addRow("Текущее зеркало astar:", self.astar_host)

        self.mirrors = QPlainTextEdit("\n".join(self.draft.sources.astar_mirrors))
        self.mirrors.setPlaceholderText("по одному домену в строке")
        self.mirrors.setMaximumHeight(160)
        form.addRow("Список зеркал:", self.mirrors)

        hint = QLabel(
            "Домен astar периодически меняется. Приложение само переберёт зеркала "
            "и запомнит рабочее."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        form.addRow("", hint)
        return page

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Папка загрузки", self.save_path.text())
        if folder:
            self.save_path.setText(folder)

    def _test_connection(self) -> None:
        self.test_button.setEnabled(False)
        self.test_result.setText("Проверяем…")
        self._apply_to_settings()

        client = self._make_test_client()
        self.workers.start(
            client.test_connection,
            on_done=lambda version: self._test_done(f"qBittorrent {version} — соединение есть"),
            on_failed=lambda exc: self._test_done(f"Ошибка: {exc}"),
        )

    def _make_test_client(self):
        """Новый контракт принимает draft; фабрики без аргумента остаются рабочими."""
        parameters = signature(self.torrent_client_factory).parameters.values()
        accepts_draft = any(
            parameter.kind
            in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD, Parameter.VAR_POSITIONAL)
            for parameter in parameters
        )
        if accepts_draft:
            return self.torrent_client_factory(self.draft)
        return self.torrent_client_factory()

    def _test_done(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(message)

    def _apply_to_settings(self) -> None:
        self.draft.check_interval_minutes = self.interval.value()
        self.draft.check_on_startup = self.check_on_startup.isChecked()
        self.draft.minimize_to_tray = self.minimize_to_tray.isChecked()
        self.draft.autostart = self.autostart.isChecked()
        self.draft.notifications_enabled = self.notifications.isChecked()
        self.draft.metadata_autofetch = self.metadata_autofetch.isChecked()
        self.draft.log_level = self.log_level.currentText()
        self.draft.theme = self.theme.currentData()

        qbt = self.draft.qbittorrent
        qbt.host = self.host.text().strip()
        qbt.port = self.port.value()
        qbt.use_https = self.https.isChecked()
        qbt.username = self.username.text().strip()
        qbt.password = self.password.text()
        qbt.category = self.category.text().strip()
        qbt.save_path = self.save_path.text().strip()
        qbt.add_paused = self.add_paused.isChecked()
        qbt.sequential_download = self.sequential.isChecked()
        qbt.delete_replaced = self.delete_replaced.isChecked()

        self.draft.sources.astar_host = self.astar_host.text().strip()
        mirrors = [line.strip() for line in self.mirrors.toPlainText().splitlines() if line.strip()]
        if mirrors:
            self.draft.sources.astar_mirrors = mirrors

    def _save(self) -> None:
        self._apply_to_settings()
        accepted = self.draft.model_copy(deep=True)
        for field in type(self.settings).model_fields:
            setattr(self.settings, field, getattr(accepted, field))
        self.accept()

    def done(self, result: int) -> None:
        self.workers.shutdown()
        super().done(result)


class LogDialog(QDialog):
    """Журнал: история действий и последние записи лога (ТЗ §22)."""

    def __init__(self, repos, parent=None) -> None:
        super().__init__(parent)
        self.repos = repos
        self.setWindowTitle("Журнал")
        self.resize(900, 560)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        self.history_model = HistoryTableModel()
        table = QTableView()
        table.setModel(self.history_model)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        tabs.addTab(table, "История")

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        tabs.addTab(self.log_text, "Лог приложения")
        layout.addWidget(tabs)

        buttons = QHBoxLayout()
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        buttons.addStretch(1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        self.history_model.set_items(self.repos.history.recent(300))
        lines = [
            f"{record.time:%d.%m %H:%M:%S} | {record.level:<7} | {record.message}"
            for record in log_buffer.records()
        ]
        self.log_text.setPlainText("\n".join(lines[-800:]))
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())


def confirm(parent, title: str, text: str) -> bool:
    answer = QMessageBox.question(
        parent,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


class TitlePickerDialog(QDialog):
    """Выбор тайтла в справочнике (ТЗ §3 — сопоставление подписки).

    Нужен, потому что поиск по названию возвращает и сезоны, и полнометражки:
    «Пожиратель звёзд» находит ещё «Пожиратель звёзд 2/3» и два фильма.
    """

    def __init__(self, candidates, parent=None) -> None:
        super().__init__(parent)
        self.candidates = candidates
        self.selected = None

        self.setWindowTitle("Выбор тайтла")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Выберите, какому тайтлу справочника соответствует подписка.\n"
            "У сериала бывает несколько сезонов и фильмов с похожими названиями."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list = QListWidget()
        for candidate in candidates:
            self.list.addItem(candidate.label)
        if candidates:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda *_: self._accept())
        layout.addWidget(self.list, 1)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        box.button(QDialogButtonBox.StandardButton.Ok).setText("Привязать")
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def _accept(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.candidates):
            self.selected = self.candidates[row]
            self.accept()
