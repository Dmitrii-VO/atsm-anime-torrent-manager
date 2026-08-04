"""Тесты интерфейса. Запускаются в offscreen-режиме, без реального экрана."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QPixmap, QPainter  # noqa: E402
from PySide6.QtWidgets import QStyleOptionViewItem  # noqa: E402

from atsm.app import bootstrap  # noqa: E402
from atsm.core.models import ReleaseState  # noqa: E402
from atsm.core.subscription_service import SubscriptionService  # noqa: E402
from atsm.gui.feed_view import FeedView  # noqa: E402
from atsm.gui.icons import app_icon  # noqa: E402
from atsm.gui.library_view import LibraryView  # noqa: E402
from atsm.gui.models import (  # noqa: E402
    RELEASE_ROLE,
    AnimeListModel,
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
        assert model.data(model.index(0, 1)).endswith("МБ")
        assert "не просмотрено" in model.data(model.index(0, 4))


class TestModels:
    def test_anime_tooltip(self, seeded_ctx) -> None:
        model = AnimeListModel()
        model.set_items(seeded_ctx.repos.anime.list())
        tooltip = model.data(model.index(0, 0), Qt.ItemDataRole.ToolTipRole)
        assert "Источник: fake" in tooltip

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
