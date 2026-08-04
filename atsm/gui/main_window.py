"""Главное окно: лента, библиотека, трей, планировщик (ТЗ §4, §12, §14, §16)."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PySide6.QtCore import QThreadPool, QTimer, Qt, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app import AppContext
from ..config import save_settings
from ..core.models import Anime, Release
from ..core.subscription_service import SubscriptionExists, SubscriptionService
from ..core.torrent_service import TorrentService
from ..core.update_service import CheckSummary, UpdateService
from ..parsers import ParserRegistry
from ..services.scheduler import CheckScheduler
from ..torrent.base import TorrentClientError
from ..torrent.qbittorrent import QBittorrentClient
from .dialogs import AddSubscriptionDialog, LogDialog, SettingsDialog, confirm
from .feed_view import FeedView
from .icons import app_icon
from .library_view import LibraryView
from .tray import Tray
from .widgets import ElidedLabel
from .workers import Worker


class MainWindow(QMainWindow):
    scheduled_check = Signal()

    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self.ctx = ctx
        self.pool = QThreadPool.globalInstance()
        self._force_quit = False
        self._busy = False

        self.registry = ParserRegistry(ctx.settings)
        self.repos = ctx.repos
        self.torrent_client = self._make_client()
        self.torrents = TorrentService(
            self.repos, self.registry, self.torrent_client, ctx.paths.torrents
        )
        self.updates = UpdateService(self.repos, self.registry, self.torrents)
        self.subscriptions = SubscriptionService(self.repos, self.registry, ctx.paths.posters)

        self._build_ui()
        self._build_tray()
        self._connect()

        self.refresh_all()
        self._start_scheduler()

    # --- построение ------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle("ATSM — Anime Torrent Subscription Manager")
        self.setWindowIcon(app_icon())
        self.resize(1120, 720)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(18, 12, 18, 6)
        row.setSpacing(8)

        self.add_button = QPushButton("Добавить подписку")
        self.add_button.setObjectName("primaryButton")
        row.addWidget(self.add_button)

        self.check_all_button = QPushButton("Проверить все")
        row.addWidget(self.check_all_button)
        row.addStretch(1)

        self.log_button = QPushButton("Журнал")
        self.log_button.setObjectName("secondaryButton")
        row.addWidget(self.log_button)

        self.settings_button = QPushButton("Настройки")
        self.settings_button.setObjectName("secondaryButton")
        row.addWidget(self.settings_button)
        layout.addWidget(toolbar)

        self.tabs = QTabWidget()
        self.feed = FeedView()
        self.library = LibraryView()
        self.tabs.addTab(self.feed, "Лента")
        self.tabs.addTab(self.library, "Библиотека")
        layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.status_label = ElidedLabel("Готово")
        self.status_label.setObjectName("statusLabel")
        status.addWidget(self.status_label, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedWidth(140)
        self.progress.setVisible(False)
        status.addPermanentWidget(self.progress)
        self.setStatusBar(status)

        QShortcut(QKeySequence("Ctrl+N"), self, self.add_subscription)
        QShortcut(QKeySequence("F5"), self, self.check_all)

    def _build_tray(self) -> None:
        self.tray = Tray(self)
        self.tray.show()
        self.tray.check_requested.connect(self.check_all)
        self.tray.download_all_requested.connect(self.download_all)
        self.tray.show_requested.connect(self.show_window)
        self.tray.quit_requested.connect(self.quit_app)
        self.tray.notification_clicked.connect(self._open_feed)

    def _connect(self) -> None:
        self.add_button.clicked.connect(self.add_subscription)
        self.check_all_button.clicked.connect(self.check_all)
        self.settings_button.clicked.connect(self.open_settings)
        self.log_button.clicked.connect(self.open_log)

        self.feed.download_requested.connect(self.send_release)
        self.feed.download_all_requested.connect(self.download_all)
        self.feed.mark_seen_requested.connect(self.mark_seen)
        self.feed.open_anime_requested.connect(self.open_anime)

        self.library.check_requested.connect(self.check_anime)
        self.library.remove_requested.connect(self.remove_anime)
        self.library.send_requested.connect(self.send_release)
        self.library.save_requested.connect(self.save_release)
        self.library.open_default_client_requested.connect(self.open_in_default_client)
        self.library.copy_link_requested.connect(
            lambda r: self._copy(r.torrent_url, "Ссылка скопирована")
        )
        self.library.copy_magnet_requested.connect(
            lambda r: self._copy(r.magnet, "Magnet скопирован")
        )
        self.library.mark_seen_requested.connect(self.mark_seen)
        self.library.auto_download_toggled.connect(self.set_auto_download)
        self.library.favorite_toggled.connect(self.set_favorite)
        self.library.open_page_requested.connect(self.open_page)
        self.library.anime_list.selectionModel().currentChanged.connect(
            lambda *_: self._refresh_releases()
        )

        self.scheduled_check.connect(self.check_all)

    def _make_client(self) -> QBittorrentClient:
        return QBittorrentClient(self.ctx.settings.qbittorrent)

    def _start_scheduler(self) -> None:
        # Колбэк приходит из потока планировщика — только сигнал, никакого UI.
        self.scheduler = CheckScheduler(self.scheduled_check.emit)
        self.scheduler.start(self.ctx.settings.check_interval_minutes)
        if self.ctx.settings.check_on_startup and self.repos.anime.list():
            QTimer.singleShot(1500, self.check_all)

    # --- обновление данных ------------------------------------------------

    def refresh_all(self) -> None:
        self.library.set_anime(self.repos.anime.list())
        self._refresh_feed()
        self._refresh_releases()

    def _refresh_feed(self) -> None:
        releases = self.repos.releases.feed()
        self.feed.set_releases(releases)
        self.tabs.setTabText(0, f"Лента ({len(releases)})" if releases else "Лента")
        self.tray.set_new_count(len(releases))

    def _refresh_releases(self) -> None:
        anime = self.library.current_anime()
        self.library.set_releases(
            self.repos.releases.list_for_anime(anime.id) if anime else []
        )

    # --- состояние занятости ---------------------------------------------

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.check_all_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy)
        if message:
            self.status_label.setText(message)

    def _run(self, fn, on_done, *args, busy_message: str = "", **kwargs) -> None:
        self._set_busy(True, busy_message)
        worker = Worker(fn, *args, **kwargs)
        worker.signals.finished.connect(on_done)
        worker.signals.failed.connect(self._on_failure)
        self.pool.start(worker)

    def _on_failure(self, exc: Exception) -> None:
        message = str(exc)
        self._set_busy(False, f"Ошибка: {message}")
        logger.error("Операция не удалась: {}", message)

        # Ненастроенный торрент-клиент — самая частая причина, и одной строки
        # в статусе мало: подсказываем, что делать, и ведём в настройки.
        if isinstance(exc, TorrentClientError):
            self._offer_client_setup(message)

    def _offer_client_setup(self, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Торрент-клиент недоступен")
        box.setText(message)
        box.setInformativeText(
            "Раздачу можно сохранить в файл через меню правой кнопкой — «Скачать "
            "torrent-файл…» — и открыть её вручную.\n\n"
            "Чтобы отправлять раздачи автоматически, установите qBittorrent, включите "
            "в нём «Веб-интерфейс» и укажите адрес, логин и пароль в настройках."
        )
        open_settings = box.addButton("Открыть настройки", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        if box.clickedButton() is open_settings:
            self.open_settings()

    # --- подписки ---------------------------------------------------------

    def add_subscription(self) -> None:
        dialog = AddSubscriptionDialog(self.subscriptions, self)
        if dialog.exec() != AddSubscriptionDialog.DialogCode.Accepted:
            return
        anime = dialog.result_info
        if anime is None:
            return
        self.refresh_all()
        self.tabs.setCurrentIndex(1)
        self.library.select_anime(anime.id)
        self.status_label.setText(f"Добавлена подписка «{anime.title}»")

    def remove_anime(self, anime: Anime) -> None:
        if not confirm(self, "Удалить подписку", f"Удалить «{anime.title}» вместе с историей?"):
            return
        self.subscriptions.remove(anime.id)
        self.refresh_all()
        self.status_label.setText(f"Подписка «{anime.title}» удалена")

    def set_auto_download(self, anime: Anime, enabled: bool) -> None:
        self.repos.anime.set_flag(anime.id, "auto_download", enabled)
        self.library.set_anime(self.repos.anime.list())
        self.status_label.setText(
            f"«{anime.title}»: автозагрузка {'включена' if enabled else 'выключена'}"
        )

    def set_favorite(self, anime: Anime, enabled: bool) -> None:
        self.repos.anime.set_flag(anime.id, "is_favorite", enabled)
        self.library.set_anime(self.repos.anime.list())

    def open_anime(self, anime_id: int) -> None:
        self.tabs.setCurrentIndex(1)
        self.library.select_anime(anime_id)

    def open_page(self, anime: Anime | None) -> None:
        if anime:
            QDesktopServices.openUrl(QUrl(anime.url))

    # --- проверка ---------------------------------------------------------

    def check_all(self) -> None:
        if self._busy:
            return
        if not self.repos.anime.list():
            self.status_label.setText("Нет подписок — добавьте первую")
            return
        self._run(self.updates.check_all, self._on_check_done, busy_message="Проверяем подписки…")

    def check_anime(self, anime: Anime | None) -> None:
        if anime is None or self._busy:
            return
        self._run(
            lambda: self.updates.check_all([anime]),
            self._on_check_done,
            busy_message=f"Проверяем «{anime.title}»…",
        )

    def _on_check_done(self, summary: CheckSummary) -> None:
        self._set_busy(False)
        self.refresh_all()

        if summary.failed:
            first = summary.failed[0]
            self.status_label.setText(
                f"Проверено с ошибками ({len(summary.failed)}): {first.anime.title} — {first.error}"
            )
        elif summary.new_count:
            self.status_label.setText(f"Найдено новых серий: {summary.new_count}")
        else:
            self.status_label.setText("Новых серий нет")

        if summary.new_count and self.ctx.settings.notifications_enabled:
            self._notify_new(summary)

    def _notify_new(self, summary: CheckSummary) -> None:
        titles = [
            f"{result.anime.title} — {result.new_releases[0].episode_label}"
            for result in summary.results
            if result.new_releases
        ]
        body = "\n".join(titles[:5])
        if len(titles) > 5:
            body += f"\n…и ещё {len(titles) - 5}"
        self.tray.notify(f"Новых серий: {summary.new_count}", body)

    # --- действия над раздачами -------------------------------------------

    def send_release(self, release: Release) -> None:
        if self._busy:
            return
        self._run(
            self.torrents.send,
            self._on_send_done,
            release,
            busy_message=f"Отправляем {release.episode_label}…",
        )

    def _on_send_done(self, ok: bool) -> None:
        self._set_busy(False)
        self.refresh_all()
        self.status_label.setText(
            "Отправлено в торрент-клиент" if ok else "Торрент-клиент отклонил раздачу"
        )

    def download_all(self) -> None:
        releases = self.repos.releases.feed()
        if not releases or self._busy:
            return
        self._run(
            self.torrents.send_many,
            self._on_send_many_done,
            releases,
            busy_message=f"Отправляем раздач: {len(releases)}…",
        )

    def _on_send_many_done(self, result) -> None:
        sent, errors = result
        self._set_busy(False)
        self.refresh_all()
        if errors:
            self.status_label.setText(f"Отправлено {sent}, с ошибками {len(errors)}")
            QMessageBox.warning(
                self, "Не всё отправлено", "\n".join(errors[:10])
            )
        else:
            self.status_label.setText(f"Отправлено в торрент-клиент: {sent}")

    def save_release(self, release: Release) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Куда сохранить torrent-файл")
        if not directory:
            return
        self._run(
            self.torrents.save_to,
            lambda path: self._on_saved(path),
            release,
            Path(directory),
            busy_message="Скачиваем torrent-файл…",
        )

    def _on_saved(self, path: Path) -> None:
        self._set_busy(False)
        self.refresh_all()
        self.status_label.setText(f"Сохранено: {path}")

    def open_in_default_client(self, release: Release) -> None:
        self._run(
            self.torrents.open_in_default_client,
            lambda path: self._on_saved(path),
            release,
            busy_message="Открываем в торрент-клиенте…",
        )

    def mark_seen(self, anime_id: int | None) -> None:
        self.repos.releases.mark_all_seen(anime_id)
        self.refresh_all()
        self.status_label.setText("Отмечено как просмотренное")

    def _copy(self, text: str | None, message: str) -> None:
        if not text:
            self.status_label.setText("Нечего копировать")
            return
        QGuiApplication.clipboard().setText(text)
        self.status_label.setText(message)

    # --- настройки и журнал ------------------------------------------------

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.ctx.settings, self._make_client, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        save_settings(self.ctx.settings, self.ctx.paths.settings)
        # Настройки применяются на лету, без перезапуска (ТЗ §23).
        self.torrent_client = self._make_client()
        self.torrents.client = self.torrent_client
        self.scheduler.reschedule(self.ctx.settings.check_interval_minutes)
        self._apply_autostart()
        self.status_label.setText("Настройки сохранены")

    def open_log(self) -> None:
        LogDialog(self.repos, self).exec()

    def _apply_autostart(self) -> None:
        from ..services.autostart import set_autostart

        try:
            set_autostart(self.ctx.settings.autostart)
        except OSError as exc:
            logger.warning("Автозапуск не настроен: {}", exc)

    # --- окно и трей -------------------------------------------------------

    def _open_feed(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        """Закрытие окна не завершает работу — приложение живёт в трее (ТЗ §16)."""
        if self._force_quit or not self.ctx.settings.minimize_to_tray:
            self._shutdown()
            event.accept()
            return

        event.ignore()
        self.hide()
        self.tray.notify("ATSM продолжает работать", "Приложение свёрнуто в трей")

    def quit_app(self) -> None:
        self._force_quit = True
        self.close()
        from PySide6.QtWidgets import QApplication

        QApplication.quit()

    def _shutdown(self) -> None:
        self.scheduler.shutdown()
        self.tray.hide()
        self.ctx.shutdown()
