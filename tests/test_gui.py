"""Тесты интерфейса. Запускаются в offscreen-режиме, без реального экрана."""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem  # noqa: E402

from atsm.app import bootstrap  # noqa: E402
from atsm.core.models import ReleaseState  # noqa: E402
from atsm.core.models import Anime  # noqa: E402
from atsm.config import Settings  # noqa: E402
from atsm.core.subscription_service import SubscriptionService  # noqa: E402
from atsm.gui.feed_view import FeedView  # noqa: E402
from atsm.gui.icons import app_icon  # noqa: E402
from atsm.gui.library_view import LibraryView  # noqa: E402
from atsm.gui.models import (  # noqa: E402
    RELEASE_ROLE,
    LibraryModel,
    HistoryTableModel,
    ReleaseTableModel,
)
from atsm.gui.tray import Tray  # noqa: E402

from .fakes import URL, FakeParser, FakeRegistry, release  # noqa: E402


@pytest.fixture
def seeded_ctx(tmp_path):
    ctx = bootstrap(data_dir=tmp_path / "data", console_log=False)
    parser = FakeParser([release("1", 1), release("2", 2)])
    SubscriptionService(ctx.repos, FakeRegistry(parser)).add(URL)
    anime_id = ctx.repos.anime.list()[0].id
    ctx.repos.releases.add_many(anime_id, [release("3", 3)], seen=False)
    yield ctx
    ctx.shutdown()


class TestFeedView:
    def test_empty_state(self, qtbot) -> None:
        view = FeedView()
        qtbot.addWidget(view)
        view.set_releases([])

        assert view.empty_label.isVisible() is False or not view.list.isVisible()
        assert view.download_all_button.isEnabled() is False

    def test_shows_releases_and_count(self, qtbot, seeded_ctx) -> None:
        view = FeedView()
        qtbot.addWidget(view)
        view.set_releases(seeded_ctx.repos.releases.feed())

        assert view.model.rowCount() == 1
        assert "(1)" in view.title.text()
        assert view.download_all_button.isEnabled()

    def test_download_button_click_emits_release(self, qtbot, seeded_ctx) -> None:
        """Кнопка нарисована делегатом — проверяем попадание клика по ней."""
        view = FeedView()
        qtbot.addWidget(view)
        view.resize(900, 400)
        view.set_releases(seeded_ctx.repos.releases.feed())

        index = view.model.index(0, 0)
        rect = view.list.visualRect(index)
        card = view.delegate._card_rect(rect)
        button_center = view.delegate._button_rect(card).center()

        option = QStyleOptionViewItem()
        option.rect = rect
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(button_center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        with qtbot.waitSignal(view.download_requested, timeout=1000) as blocker:
            view.delegate.editorEvent(event, view.model, option, index)
        assert blocker.args[0].external_id == "3"

    def test_seen_button_click_emits_release(self, qtbot, seeded_ctx) -> None:
        """«Просмотрено» в карточке убирает одну серию из ленты (клик по делегату)."""
        view = FeedView()
        qtbot.addWidget(view)
        view.resize(900, 400)
        view.set_releases(seeded_ctx.repos.releases.feed())

        index = view.model.index(0, 0)
        rect = view.list.visualRect(index)
        card = view.delegate._card_rect(rect)
        center = view.delegate._seen_rect(card).center()

        option = QStyleOptionViewItem()
        option.rect = rect
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(center),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

        with qtbot.waitSignal(view.release_seen_requested, timeout=1000) as blocker:
            view.delegate.editorEvent(event, view.model, option, index)
        assert blocker.args[0].external_id == "3"

    def test_click_outside_button_does_nothing(self, qtbot, seeded_ctx) -> None:
        view = FeedView()
        qtbot.addWidget(view)
        view.set_releases(seeded_ctx.repos.releases.feed())

        index = view.model.index(0, 0)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 900, 82)
        event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(QPoint(30, 40)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        assert view.delegate.editorEvent(event, view.model, option, index) is False

    def test_delegate_paints_without_errors(self, qtbot, seeded_ctx) -> None:
        """Отрисовка карточки не должна падать ни в обычном, ни в ошибочном виде."""
        view = FeedView()
        qtbot.addWidget(view)
        repos = seeded_ctx.repos
        target = repos.releases.feed()[0]
        repos.releases.set_state(target.id, ReleaseState.ERROR, "нет связи")
        view.set_releases(repos.releases.feed())

        pixmap = QPixmap(900, 90)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 900, 82)
        view.delegate.paint(painter, option, view.model.index(0, 0))
        painter.end()

    def test_error_release_shows_retry_label(self, qtbot, seeded_ctx) -> None:
        repos = seeded_ctx.repos
        target = repos.releases.feed()[0]
        repos.releases.set_state(target.id, ReleaseState.ERROR, "нет связи")
        subtitle = FeedView().delegate._subtitle(repos.releases.feed()[0])
        assert "нет связи" in subtitle


class TestLibraryView:
    def test_selection_shows_details(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_anime(seeded_ctx.repos.anime.list())

        view.anime_list.setCurrentIndex(view.anime_model.index(0, 0))
        assert view.current_anime() is not None
        assert view.detail_title.text() == "Пожиратель звёзд"
        assert "Источник: fake" in view.detail_meta.text()

    def test_search_filters(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_anime(seeded_ctx.repos.anime.list())

        view.search.setText("несуществующее")
        assert view.anime_model.rowCount() == 0
        view.search.setText("пожиратель")
        assert view.anime_model.rowCount() == 1

    def test_filter_only_with_new(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_anime(seeded_ctx.repos.anime.list())

        view.filter_box.setCurrentIndex(1)
        assert view.anime_model.rowCount() == 1

        seeded_ctx.repos.releases.mark_all_seen()
        view.set_anime(seeded_ctx.repos.anime.list())
        assert view.anime_model.rowCount() == 0

    def test_auto_download_signal_not_fired_on_programmatic_set(
        self, qtbot, seeded_ctx
    ) -> None:
        """Переключение вкладок не должно случайно менять флаги подписки."""
        view = LibraryView()
        qtbot.addWidget(view)
        fired = []
        view.auto_download_toggled.connect(lambda *args: fired.append(args))

        view.set_anime(seeded_ctx.repos.anime.list())
        view.anime_list.setCurrentIndex(view.anime_model.index(0, 0))
        assert fired == []

    def test_anime_delegate_paints(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        repos = seeded_ctx.repos
        repos.anime.set_flag(repos.anime.list()[0].id, "is_favorite", True)
        view.set_anime(repos.anime.list())

        pixmap = QPixmap(320, 70)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 320, 62)
        view.anime_list.itemDelegate().paint(painter, option, view.anime_model.index(0, 0))
        painter.end()

    def test_release_table_columns(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        anime = seeded_ctx.repos.anime.list()[0]
        view.set_releases(seeded_ctx.repos.releases.list_for_anime(anime.id))

        model = view.release_model
        assert model.rowCount() == 3
        assert model.data(model.index(0, 0)) == "Серия 3"
        assert model.data(model.index(0, 3)).endswith("МБ")

    def test_archive_is_not_labelled_new(self, qtbot, seeded_ctx) -> None:
        """Импортированный архив не должен выглядеть как двести новых серий."""
        view = LibraryView()
        qtbot.addWidget(view)
        anime = seeded_ctx.repos.anime.list()[0]
        view.set_releases(seeded_ctx.repos.releases.list_for_anime(anime.id))

        model = view.release_model
        statuses = [model.data(model.index(row, 6)) for row in range(model.rowCount())]
        assert statuses[0] == "Новая"          # вышла после подписки
        assert statuses[1:] == ["В архиве", "В архиве"]

    def test_stream_request_reaches_service(self, qtbot, seeded_ctx, monkeypatch) -> None:
        """Сигнал «Смотреть потоком» доходит до сервиса, а не висит неподключённым."""
        from pathlib import Path

        from atsm.gui.main_window import MainWindow

        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)

        called: list = []
        monkeypatch.setattr(
            window.torrents, "stream", lambda release: called.append(release) or Path("x.mkv")
        )
        target = seeded_ctx.repos.releases.feed()[0]
        window.library.stream_requested.emit(target)
        qtbot.waitUntil(lambda: bool(called), timeout=2000)

        assert called[0].id == target.id
        window.scheduler.shutdown()

    def test_merged_check_and_mark_seen_use_all_ids(self, qtbot) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        animes = [
            Anime(1, "Тайтл", "astar", "https://astar/1", "1", shikimori_id="42"),
            Anime(2, "Тайтл", "anilibria", "https://anilibria/2", "2", shikimori_id="42"),
        ]
        view.set_anime(animes)
        view.anime_list.setCurrentIndex(view.anime_model.index(0, 0))

        checked = []
        seen = []
        view.check_requested.connect(checked.append)
        view.mark_seen_requested.connect(seen.append)
        view.check_button.click()
        view._request_mark_seen(view.current_entry())

        assert [anime.id for anime in checked[0]] == [1, 2]
        assert seen == [[1, 2]]
        assert view._remove_action_text(animes[0]) == "Удалить подписку источника «astar»"


class TestModels:
    def test_anime_tooltip(self, seeded_ctx) -> None:
        from atsm.core.grouping import build_groups

        model = LibraryModel()
        model.set_groups(build_groups(seeded_ctx.repos.anime.list()))
        tooltip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        assert "fake: проверено" in tooltip

    def test_release_role_returns_object(self, seeded_ctx) -> None:
        model = ReleaseTableModel()
        anime = seeded_ctx.repos.anime.list()[0]
        model.set_items(seeded_ctx.repos.releases.list_for_anime(anime.id))
        assert model.data(model.index(0, 0), RELEASE_ROLE).external_id == "3"

    def test_history_model(self, seeded_ctx) -> None:
        model = HistoryTableModel()
        model.set_items(seeded_ctx.repos.history.recent())
        assert model.rowCount() >= 1
        assert model.data(model.index(0, 2)) == "Проверка"


LONG_ERROR = (
    "Ошибка: qBittorrent недоступен по адресу http://127.0.0.1:8080: "
    "HTTPConnectionPool(host='127.0.0.1', port=8080): Max retries exceeded with url: "
    "/api/v2/auth/login (Caused by NewConnectionError('<urllib3.connection.HTTPConnection "
    "object at 0x0000028DF75AA>: Failed to establish a new connection'))"
)


class TestStatusBar:
    """Длинное сообщение в строке состояния однажды растянуло окно до 3578 px,
    из-за чего колонки таблицы уехали за край экрана."""

    def test_long_message_does_not_widen_window(self, qtbot, seeded_ctx) -> None:
        from atsm.gui.main_window import MainWindow

        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)
        window.resize(1200, 700)
        before = window.minimumSizeHint().width()

        window.status_label.setText(LONG_ERROR)
        after = window.minimumSizeHint().width()

        assert after == before
        window.scheduler.shutdown()

    def test_full_text_kept_in_tooltip(self, qtbot) -> None:
        from atsm.gui.widgets import ElidedLabel

        label = ElidedLabel()
        qtbot.addWidget(label)
        label.resize(200, 20)
        label.setText(LONG_ERROR)

        assert label.toolTip() == LONG_ERROR
        assert label.full_text() == LONG_ERROR
        assert len(label.text()) < len(LONG_ERROR)
        assert label.text().endswith("…")

    def test_release_table_fits_viewport(self, qtbot, seeded_ctx) -> None:
        """Все колонки раздач должны помещаться в окно без горизонтальной прокрутки."""
        from atsm.gui.main_window import MainWindow

        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)
        window.resize(1200, 700)
        window.show()
        window.tabs.setCurrentIndex(1)
        window.library.anime_list.setCurrentIndex(window.library.anime_model.index(0, 0))
        window.status_label.setText(LONG_ERROR)
        qtbot.wait(50)

        table = window.library.releases
        total = sum(table.columnWidth(c) for c in range(table.model().columnCount()))
        assert total <= table.viewport().width() + 2
        window.scheduler.shutdown()


class TestAddDialog:
    def test_preview_shows_title_and_count(self, qtbot, seeded_ctx) -> None:
        from atsm.core.subscription_service import SubscriptionService
        from atsm.gui.dialogs import AddSubscriptionDialog

        parser = FakeParser([release("1", 1), release("2", 2)])
        service = SubscriptionService(seeded_ctx.repos, FakeRegistry(parser))
        dialog = AddSubscriptionDialog(service, None)
        qtbot.addWidget(dialog)

        dialog.url_edit.setText("https://example.test/555-new.html")
        dialog._preview()
        qtbot.waitUntil(lambda: "раздач" in dialog.status.text(), timeout=3000)
        assert "Пожиратель звёзд" in dialog.status.text()

    def test_error_is_shown_to_user(self, qtbot, seeded_ctx) -> None:
        """Сигнал об ошибке несёт исключение — текст всё равно должен дойти."""
        from atsm.core.subscription_service import SubscriptionService
        from atsm.gui.dialogs import AddSubscriptionDialog
        from atsm.parsers.base import SourceUnreachable

        parser = FakeParser([])
        parser.fail_with(SourceUnreachable("все зеркала молчат"))
        service = SubscriptionService(seeded_ctx.repos, FakeRegistry(parser))
        dialog = AddSubscriptionDialog(service, None)
        qtbot.addWidget(dialog)

        dialog.url_edit.setText("https://example.test/555-new.html")
        dialog._preview()
        qtbot.waitUntil(lambda: "Ошибка" in dialog.status.text(), timeout=3000)
        assert "все зеркала молчат" in dialog.status.text()


class TestSettingsDialog:
    def test_connection_uses_draft_and_cancel_keeps_settings(self, qtbot) -> None:
        from atsm.gui.dialogs import SettingsDialog

        settings = Settings()
        tested = []

        class Client:
            def test_connection(self):
                return "5.0"

        dialog = SettingsDialog(settings, lambda draft: tested.append(draft) or Client())
        qtbot.addWidget(dialog)
        dialog.host.setText("draft.local")
        dialog._test_connection()
        qtbot.waitUntil(lambda: bool(tested) and dialog.test_button.isEnabled(), timeout=3000)
        dialog.reject()

        assert tested[0].qbittorrent.host == "draft.local"
        assert settings.qbittorrent.host == "127.0.0.1"

    def test_save_applies_deep_draft(self, qtbot) -> None:
        from atsm.gui.dialogs import SettingsDialog

        settings = Settings()
        original_qbt = settings.qbittorrent
        dialog = SettingsDialog(settings, lambda draft: None)
        qtbot.addWidget(dialog)
        dialog.host.setText("saved.local")
        dialog._save()

        assert dialog.result() == dialog.DialogCode.Accepted
        assert settings.qbittorrent.host == "saved.local"
        assert settings.qbittorrent is not original_qbt


class TestSourceDiagnostics:
    """ТЗ §21: пользователь должен понимать, почему новых серий нет."""

    def test_states_are_shown(self, qtbot, seeded_ctx) -> None:
        from atsm.core.models import SourceState

        view = LibraryView()
        qtbot.addWidget(view)
        seeded_ctx.repos.sources.set("astar", SourceState.LAYOUT_CHANGED, "нет блоков раздач")
        view.set_source_states(seeded_ctx.repos.sources.all())

        assert "Изменена структура сайта" in view.sources_label.text()
        assert "нет блоков раздач" in view.sources_label.toolTip()

    def test_ok_state(self, qtbot, seeded_ctx) -> None:
        from atsm.core.models import SourceState

        view = LibraryView()
        qtbot.addWidget(view)
        seeded_ctx.repos.sources.set("astar", SourceState.OK)
        view.set_source_states(seeded_ctx.repos.sources.all())
        assert "Работает" in view.sources_label.text()

    def test_no_checks_yet(self, qtbot) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_source_states({})
        assert "проверок ещё не было" in view.sources_label.text()

    def test_main_window_publishes_states(self, qtbot, seeded_ctx) -> None:
        from atsm.core.models import SourceState
        from atsm.gui.main_window import MainWindow

        seeded_ctx.repos.sources.set("fake", SourceState.UNREACHABLE, "сайт лёг")
        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)
        assert "Недоступен" in window.library.sources_label.text()
        window.scheduler.shutdown()


class TestFiltersAndActions:
    def test_source_filter(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_anime(seeded_ctx.repos.anime.list())

        assert view.source_box.count() == 2  # «Все источники» + fake
        view.source_box.setCurrentIndex(1)
        assert view.anime_model.rowCount() == 1

    def test_autodownload_filter(self, qtbot, seeded_ctx) -> None:
        view = LibraryView()
        qtbot.addWidget(view)
        view.set_anime(seeded_ctx.repos.anime.list())

        view.filter_box.setCurrentIndex(4)
        assert view.anime_model.rowCount() == 0

        seeded_ctx.repos.anime.set_flag(seeded_ctx.repos.anime.list()[0].id, "auto_download", True)
        view.set_anime(seeded_ctx.repos.anime.list())
        assert view.anime_model.rowCount() == 1

    def test_return_to_new(self, qtbot, seeded_ctx) -> None:
        from atsm.core.models import ReleaseState
        from atsm.gui.main_window import MainWindow

        repos = seeded_ctx.repos
        repos.releases.mark_all_seen()
        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)
        assert window.feed.model.rowCount() == 0

        anime = repos.anime.list()[0]
        target = repos.releases.list_for_anime(anime.id)[0]
        window.return_to_new(target)

        assert repos.releases.get(target.id).is_seen is False
        assert repos.releases.get(target.id).state == ReleaseState.NEW
        assert window.feed.model.rowCount() == 1
        window.scheduler.shutdown()

    def test_mark_seen_for_merged_ids_updates_both_sources(self, qtbot, seeded_ctx) -> None:
        from atsm.gui.main_window import MainWindow

        repos = seeded_ctx.repos
        first_id = repos.anime.list()[0].id
        second_id = repos.anime.add(
            title="Пожиратель звёзд",
            source="other",
            url="https://other.test/title",
            slug="title",
        )
        repos.releases.add_many(second_id, [release("other-1", 3)], seen=False)
        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)

        window.mark_seen([first_id, second_id])

        assert repos.releases.feed() == []
        window.scheduler.shutdown()


class TestDownloadStates:
    """ТЗ §13: статус «скачана» подтягивается из клиента после проверки."""

    def test_states_applied_after_check(self, qtbot, seeded_ctx) -> None:
        from atsm.core.models import ReleaseState
        from atsm.gui.main_window import MainWindow

        repos = seeded_ctx.repos
        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)

        anime = repos.anime.list()[0]
        target = repos.releases.list_for_anime(anime.id)[0]
        repos.releases.set_state(target.id, ReleaseState.SENT, "отправлено")
        repos.releases.set_info_hash(target.id, "abc123")

        class DoneClient:
            def torrent_states(self, hashes):
                return {"abc123": "stalledUP"}

        window.torrents.client = DoneClient()
        window._refresh_download_states()
        qtbot.waitUntil(
            lambda: repos.releases.get(target.id).state == ReleaseState.DOWNLOADED, timeout=3000
        )
        window.scheduler.shutdown()


class TestTheme:
    def test_both_palettes_render_fully(self) -> None:
        from atsm.gui.palette import PALETTES, stylesheet

        for name, palette in PALETTES.items():
            css = stylesheet(palette)
            assert "$" not in css, f"в теме {name} осталась неподставленная переменная"
            assert palette.accent in css

    def test_switching_theme_updates_delegates(self, qtbot, seeded_ctx) -> None:
        from atsm.gui.main_window import MainWindow
        from atsm.gui.palette import LIGHT

        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)
        assert window.feed.delegate.palette.name == "dark"

        seeded_ctx.settings.theme = "light"
        window._apply_theme()

        assert window.feed.delegate.palette is LIGHT
        assert window.library.anime_list.itemDelegate().palette is LIGHT
        window.scheduler.shutdown()

    def test_delegates_paint_in_light_theme(self, qtbot, seeded_ctx) -> None:
        from atsm.gui.palette import LIGHT

        view = FeedView(LIGHT)
        qtbot.addWidget(view)
        view.set_releases(seeded_ctx.repos.releases.feed())

        pixmap = QPixmap(900, 90)
        painter = QPainter(pixmap)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 900, 82)
        view.delegate.paint(painter, option, view.model.index(0, 0))
        painter.end()


class TestWorkers:
    """Потеря сигналов из фоновых потоков выглядела как вечная «загрузка»:
    задача отрабатывала, но интерфейс об этом не узнавал и оставался занят."""

    def test_all_results_are_delivered(self, qtbot) -> None:
        from PySide6.QtCore import QThreadPool

        from atsm.gui.workers import WorkerRunner

        runner = WorkerRunner()
        received: list[int] = []
        total = 200

        for i in range(total):
            runner.start(lambda value=i: value, on_done=received.append)

        qtbot.waitUntil(lambda: len(received) == total, timeout=10000)
        QThreadPool.globalInstance().waitForDone(3000)
        assert sorted(received) == list(range(total))
        # Ссылки освобождаются после доставки, иначе воркеры копились бы.
        assert runner.active_count == 0

    def test_failures_are_delivered_too(self, qtbot) -> None:
        from atsm.gui.workers import WorkerRunner

        runner = WorkerRunner()
        errors: list[Exception] = []

        def boom():
            raise ValueError("сбой в фоне")

        for _ in range(50):
            runner.start(boom, on_failed=errors.append)

        qtbot.waitUntil(lambda: len(errors) == 50, timeout=10000)
        assert all(isinstance(e, ValueError) for e in errors)
        assert runner.active_count == 0

    def test_busy_state_is_released_after_failure(self, qtbot, seeded_ctx) -> None:
        """Ошибка фоновой задачи обязана снимать блокировку интерфейса."""
        from atsm.gui.main_window import MainWindow

        window = MainWindow(seeded_ctx)
        qtbot.addWidget(window)

        def boom():
            raise RuntimeError("справочник недоступен")

        window._run(boom, lambda _: None, busy_message="Ищем…")
        assert window._busy is True

        qtbot.waitUntil(lambda: window._busy is False, timeout=5000)
        assert window.check_all_button.isEnabled()
        assert "справочник недоступен" in window.status_label.full_text()
        window.scheduler.shutdown()

    def test_shutdown_waits_own_pool_disables_callbacks_and_new_tasks(self, qtbot) -> None:
        from PySide6.QtCore import QThreadPool

        from atsm.gui.workers import WorkerRunner

        runner = WorkerRunner()
        started = threading.Event()
        release_task = threading.Event()
        callbacks = []

        def task():
            started.set()
            release_task.wait(3)
            return 1

        runner.start(task, on_done=callbacks.append)
        assert started.wait(1)
        threading.Timer(0.05, release_task.set).start()
        assert runner.shutdown() is True
        QApplication.processEvents()

        assert callbacks == []
        assert runner.active_count == 0
        assert runner.pool is not QThreadPool.globalInstance()
        with pytest.raises(RuntimeError, match="после shutdown"):
            runner.start(lambda: None)


class TestShutdown:
    @pytest.mark.parametrize(
        ("minimize_to_tray", "tray_available"),
        [(False, True), (True, False)],
    )
    def test_close_quits_when_window_cannot_safely_hide(
        self, qtbot, seeded_ctx, monkeypatch, minimize_to_tray, tray_available
    ) -> None:
        from atsm.gui.main_window import MainWindow

        seeded_ctx.settings.minimize_to_tray = minimize_to_tray
        window = MainWindow(seeded_ctx, tray_available=tray_available)
        qtbot.addWidget(window)
        calls = []
        monkeypatch.setattr(window, "_shutdown", lambda: calls.append("shutdown"))
        monkeypatch.setattr(QApplication, "quit", lambda: calls.append("quit"))

        class Event:
            accepted = False

            def accept(self):
                self.accepted = True

            def ignore(self):
                raise AssertionError("Окно не должно скрываться")

        event = Event()
        window.closeEvent(event)

        assert event.accepted
        assert calls == ["shutdown", "quit"]
        window.scheduler.shutdown()

    def test_shutdown_waits_workers_before_database(self, qtbot, seeded_ctx, monkeypatch) -> None:
        from atsm.gui.main_window import MainWindow

        window = MainWindow(seeded_ctx, tray_available=False)
        qtbot.addWidget(window)
        order = []
        monkeypatch.setattr(window.scheduler, "shutdown", lambda: order.append("scheduler"))
        monkeypatch.setattr(window.workers, "shutdown", lambda: order.append("workers"))
        monkeypatch.setattr(window.tray, "hide", lambda: order.append("tray"))
        monkeypatch.setattr(type(window.ctx), "shutdown", lambda _self: order.append("database"))

        window._shutdown()

        assert order == ["scheduler", "workers", "tray", "database"]


class TestAutofetchMetadata:
    """Справка подтягивается сразу при добавлении подписки, чтобы карточка
    не приезжала пустой и не требовала отдельного клика."""

    def _window(self, qtbot, ctx):
        from atsm.gui.main_window import MainWindow

        window = MainWindow(ctx)
        qtbot.addWidget(window)
        return window

    def test_enrich_started_after_add(self, qtbot, seeded_ctx) -> None:
        window = self._window(qtbot, seeded_ctx)
        calls: list[int] = []
        window.metadata.enrich = lambda anime_id, *a, **kw: calls.append(anime_id)

        anime = seeded_ctx.repos.anime.list()[0]
        window._after_subscription_added(anime)

        qtbot.waitUntil(lambda: calls == [anime.id], timeout=5000)
        window.scheduler.shutdown()

    def test_disabled_by_setting(self, qtbot, seeded_ctx) -> None:
        seeded_ctx.settings.metadata_autofetch = False
        window = self._window(qtbot, seeded_ctx)
        calls: list[int] = []
        window.metadata.enrich = lambda anime_id, *a, **kw: calls.append(anime_id)

        window._after_subscription_added(seeded_ctx.repos.anime.list()[0])

        qtbot.wait(300)
        assert calls == []
        window.scheduler.shutdown()

    def test_failure_does_not_block_ui(self, qtbot, seeded_ctx) -> None:
        """Отказ справочника не должен выглядеть как проблема с подпиской."""
        from atsm.metadata import MetadataError

        window = self._window(qtbot, seeded_ctx)

        def boom(*args, **kwargs):
            raise MetadataError("ничего не найдено")

        window.metadata.enrich = boom
        anime = seeded_ctx.repos.anime.list()[0]
        window._after_subscription_added(anime)

        qtbot.waitUntil(
            lambda: "справочные данные не найдены" in window.status_label.full_text(),
            timeout=5000,
        )
        # Интерфейс остаётся рабочим: обогащение идёт мимо состояния занятости.
        assert window._busy is False
        assert window.add_button.isEnabled()
        window.scheduler.shutdown()


class TestBackfill:
    def test_absent_franchise_is_not_refetched(self, seeded_ctx) -> None:
        """«Франшизы нет» и «ещё не спрашивали» — разные состояния, иначе
        одиночные тайтлы перезапрашивались бы при каждом запуске."""
        from atsm.metadata.base import AnimeMetadata

        repos = seeded_ctx.repos
        anime_id = repos.anime.list()[0].id
        repos.metadata.save(
            anime_id, AnimeMetadata("shikimori", "44218", title_ru="Тест", franchise=None)
        )

        assert repos.metadata.missing_franchise() == []
        assert repos.metadata.get(anime_id)["franchise"] == ""

    def test_missing_franchise_detected(self, seeded_ctx, db) -> None:
        from atsm.metadata.base import AnimeMetadata

        repos = seeded_ctx.repos
        anime_id = repos.anime.list()[0].id
        repos.metadata.save(anime_id, AnimeMetadata("shikimori", "44218", title_ru="Тест"))
        with seeded_ctx.db.transaction() as conn:
            conn.execute("UPDATE anime_metadata SET franchise = NULL")

        assert repos.metadata.missing_franchise() == [anime_id]


class TestTrayAndIcons:
    def test_badge_icon_renders(self) -> None:
        assert not app_icon().isNull()
        assert not app_icon(5).isNull()
        assert not app_icon(150).isNull()

    def test_tray_counter(self, qtbot) -> None:
        tray = Tray()
        tray.set_new_count(0)
        assert tray.download_action.isEnabled() is False
        assert "новых серий нет" in tray.icon.toolTip()

        tray.set_new_count(3)
        assert tray.download_action.isEnabled() is True
        assert "3" in tray.icon.toolTip()


class TestRuTrackerSearch:
    """Поиск по трекеру: вставка доступа, таблица результатов, действия."""

    def test_curl_parsing_takes_cookies_and_user_agent(self) -> None:
        """Пользователь вставляет cURL целиком — куки и UA достаём сами."""
        from atsm.gui.dialogs import _parse_curl

        curl = (
            "curl --url 'https://rutracker.org/forum/index.php' \\n"
            "  -H 'accept-language: ru-RU,ru;q=0.9' \\n"
            "  -b 'bb_session=abc; cf_clearance=xyz' \\n"
            "  -H 'user-agent: Mozilla/5.0 (Windows NT 10.0) Chrome/152.0.0.0'"
        )
        cookies, user_agent = _parse_curl(curl)

        assert cookies == "bb_session=abc; cf_clearance=xyz"
        assert "Chrome/152" in user_agent

    def test_curl_parsing_accepts_cookie_header_form(self) -> None:
        """Chrome в cmd-варианте кладёт куки в заголовок, а не в -b."""
        from atsm.gui.dialogs import _parse_curl

        cookies, _ = _parse_curl('curl "https://rutracker.org" -H "cookie: bb_session=abc"')
        assert cookies == "bb_session=abc"

    def test_search_dialog_disabled_without_access(self, qtbot) -> None:
        from atsm.gui.dialogs import SearchDialog

        class _Parser:
            display_name = "RuTracker"
            configured = False

            def search(self, query):  # pragma: no cover — не должен вызываться
                raise AssertionError("поиск без настроенного доступа")

        dialog = SearchDialog(_Parser())
        qtbot.addWidget(dialog)

        assert dialog.search_button.isEnabled() is False
        assert "не настроен" in dialog.status.text()

    def test_search_shows_results_and_returns_choice(self, qtbot) -> None:
        from atsm.parsers.base import SearchHit
        from atsm.gui.dialogs import SearchDialog

        hit = SearchHit(
            topic_id="42",
            title="Фильм",
            url="https://rutracker.org/forum/viewtopic.php?t=42",
            category="Зарубежное кино",
            size_bytes=2 * 1024**3,
            seeders=10,
        )

        class _Parser:
            display_name = "RuTracker"
            configured = True

            def search(self, query):
                return [hit]

        dialog = SearchDialog(_Parser())
        qtbot.addWidget(dialog)
        dialog.query.setText("фильм")
        dialog.search()
        qtbot.waitUntil(lambda: dialog.model.rowCount() == 1, timeout=2000)

        assert "Найдено раздач: 1" in dialog.status.text()
        dialog._finish(SearchDialog.STREAM)
        assert dialog.chosen is hit
        assert dialog.action == SearchDialog.STREAM

    def test_found_release_is_saved_once(self, qtbot, seeded_ctx) -> None:
        """Повторный выбор той же темы не плодит дубликаты подписок."""
        from atsm.core.subscription_service import SubscriptionService

        service = SubscriptionService(seeded_ctx.repos, seeded_ctx_registry(seeded_ctx))
        first = service.add_from_search(URL)
        second = service.add_from_search(URL)

        assert first.id == second.id
        assert len(seeded_ctx.repos.anime.list()) == 1


def seeded_ctx_registry(ctx):
    """Реестр с тем же фейковым парсером, что и в остальных тестах."""
    return FakeRegistry(FakeParser([release("3", 3)]))
