from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

import platform_utils
from config import settings
from core.quiz_player import build_quiz_launch_target
from gui.app_controller import AppController
from gui.components.detail_view import DetailView
from gui.components.quiz_player_view import QuizPlayerView
from gui.core.theme import C, apply_theme, semantic_palette


class HeadlessPage:
    def __init__(self):
        self.tasks = []
        self.dialogs = []

    def update(self):
        return None

    def run_task(self, callback, *args):
        task = asyncio.get_running_loop().create_task(callback(*args))
        self.tasks.append(task)
        return task

    def show_dialog(self, dialog):
        dialog.open = True
        self.dialogs.append(dialog)

    def pop_dialog(self):
        if self.dialogs:
            self.dialogs[-1].open = False


class Lease:
    def __init__(self, *, ready=True):
        self._ready_event = threading.Event()
        if ready:
            self._ready_event.set()
        self.released = False
        self.wait_timeout = None

    @property
    def is_ready(self):
        return self._ready_event.is_set()

    def wait_until_ready(self, timeout=None):
        self.wait_timeout = timeout
        return self._ready_event.wait(timeout)

    def release(self):
        self.released = True


def trusted_quiz(**changes):
    data = {
        "id": "quiz-9",
        "type": "quiz",
        "title": "Kiểm tra giữa kỳ",
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=321",
        "course": "Giải tích",
        "submission_status": "not_submitted",
        "details": {},
    }
    data.update(changes)
    return data


def enable_native_runtime(monkeypatch):
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)


def make_quiz_ui(monkeypatch, *, ready=True, on_closed=None, ready_timeout=0.2):
    enable_native_runtime(monkeypatch)
    page = HeadlessPage()
    lease = Lease(ready=ready)
    players = []

    def player_factory(**kwargs):
        player = ft.Container(expand=True)
        player.callbacks = kwargs
        players.append(player)
        return player

    player_view = QuizPlayerView(
        page,
        on_closed=on_closed,
        player_factory=player_factory,
        target_builder=build_quiz_launch_target,
        lease_provider=lambda _session_key: lease,
        ready_timeout_seconds=ready_timeout,
    )
    detail_view = DetailView(
        page,
        lambda: None,
        quiz_player_view=player_view,
        get_quiz_account_id=lambda: "student@example.edu",
    )
    return page, lease, players, player_view, detail_view


def test_player_view_constructor_accepts_flet_page_like_object():
    view = QuizPlayerView(MagicMock(), player_factory=lambda **_kwargs: None)

    assert view.visible is False
    assert view._page is not None


def test_quiz_button_mounts_player_and_close_refreshes_from_moodle(monkeypatch):
    async def scenario():
        page, lease, players, player_view, detail_view = make_quiz_ui(monkeypatch)
        activity = trusted_quiz()
        detail_view.update_detail(activity)

        assert detail_view._quiz_open_btn.visible is True
        assert detail_view._quiz_open_btn.content == "Làm bài"
        assert detail_view._open_btn.visible is False
        assert detail_view._header_open_btn.visible is True

        await detail_view._open_quiz(None)
        await asyncio.sleep(0)

        assert player_view.visible is True
        assert len(players) == 1
        assert player_view._player_host.content is players[0]
        assert players[0].callbacks["url"] == activity["url"]
        assert players[0].callbacks["session_key"]
        assert lease.released is False

        controller = AppController.__new__(AppController)
        controller.quiz_player_view = player_view
        controller.settings_view = SimpleNamespace(visible=False)
        controller.detail_view = SimpleNamespace(visible=True)
        controller._close_detail = AsyncMock()
        assert await controller._navigate_back() is True
        assert player_view._close_dialog.open is True
        assert player_view._player_host.content is players[0]

        player_view._cancel_close()
        assert player_view._close_dialog.open is False
        assert player_view._player_host.content is players[0]
        assert lease.released is False

        controller.orchestrator = SimpleNamespace(invalidate_detail_cache=MagicMock())
        controller.detail_view = SimpleNamespace(
            visible=True,
            _current_url=activity["url"],
            focus_quiz_opener=AsyncMock(),
        )
        controller._show_detail_async = AsyncMock()
        player_view.on_closed = controller._on_quiz_player_closed
        player_view.request_close()
        await player_view._confirm_close()

        assert player_view.visible is False
        assert player_view._player_host.content is None
        assert lease.released is True
        controller.orchestrator.invalidate_detail_cache.assert_called_once_with(
            activity["url"]
        )
        controller._show_detail_async.assert_awaited_once_with(activity)
        controller.detail_view.focus_quiz_opener.assert_awaited_once_with()

    asyncio.run(scenario())


def test_transient_quiz_load_error_keeps_mounted_form_and_session_lease(monkeypatch):
    async def scenario():
        page, lease, players, player_view, detail_view = make_quiz_ui(monkeypatch)
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0)

        player = players[0]
        player.callbacks["on_ready"](SimpleNamespace(data="ready"))
        player.callbacks["on_error"](SimpleNamespace(data="navigation_error"))

        assert player_view._player is player
        assert player_view._player_host.content is player
        assert player_view._lease is lease
        assert lease.released is False
        assert player_view._status_panel.visible is True
        assert "kiểm tra kết nối" in player_view._status_text.value.casefold()

        player_view.handle_back()
        player_view._cancel_close()
        assert player_view._player_host.content is player
        assert lease.released is False

    asyncio.run(scenario())


def test_existing_login_wait_has_a_finite_timeout_and_releases_unmounted_lease(
    monkeypatch,
):
    async def scenario():
        page, lease, players, player_view, detail_view = make_quiz_ui(
            monkeypatch, ready=False
        )
        detail_view.update_detail(trusted_quiz())

        await detail_view._open_quiz(None)
        await asyncio.gather(*page.tasks)

        assert lease.wait_timeout == 0.01
        assert lease.released is True
        assert players == []
        assert player_view._player_host.content is None
        assert player_view._error_actions.visible is True

    import gui.components.quiz_player_view as player_module

    monkeypatch.setattr(player_module, "_LOGIN_WAIT_TIMEOUT_SECONDS", 0.01)
    asyncio.run(scenario())


@pytest.mark.parametrize(
    "activity",
    [
        trusted_quiz(url="https://courses.ut.edu.vn/mod/scorm/view.php?id=321"),
        trusted_quiz(quiz_security={"browsersecurity": "safebrowser"}),
        trusted_quiz(
            url="https://outside.example/mod/quiz/view.php?id=321"
        ),
        {
            **trusted_quiz(),
            "type": "assignment",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=321",
        },
    ],
    ids=["scorm", "safe-exam-browser", "untrusted-site", "assignment"],
)
def test_non_quiz_or_restricted_activity_keeps_browser_path(activity, monkeypatch):
    _page, _lease, players, player_view, detail_view = make_quiz_ui(monkeypatch)
    detail_view.update_detail(activity)

    assert detail_view._quiz_open_btn.visible is False
    assert detail_view._open_btn.visible is True
    assert detail_view._header_open_btn.visible is True
    assert players == []
    assert player_view.visible is False


def test_missing_extension_shows_browser_recovery(monkeypatch):
    enable_native_runtime(monkeypatch)
    page = HeadlessPage()
    player_view = QuizPlayerView(
        page,
        target_builder=build_quiz_launch_target,
        player_factory=None,
    )
    monkeypatch.setattr(player_view, "_resolve_player_factory", lambda: None)
    detail_view = DetailView(
        page,
        lambda: None,
        quiz_player_view=player_view,
        get_quiz_account_id=lambda: "student@example.edu",
    )
    detail_view.update_detail(trusted_quiz())

    assert detail_view._quiz_open_btn.visible is False
    assert detail_view._open_btn.visible is True
    assert detail_view._quiz_runtime_notice.visible is True
    assert "trình duyệt" in detail_view._quiz_runtime_notice.value.casefold()


def test_secure_window_quiz_remains_available_to_moodles_page(monkeypatch):
    _page, _lease, _players, player_view, detail_view = make_quiz_ui(monkeypatch)
    activity = trusted_quiz(
        quiz_security={"browsersecurity": "securewindow"}
    )

    detail_view.update_detail(activity)
    target, state = player_view.prepare_activity(activity, "student@example.edu")

    assert state is None
    assert target.secure_window is True
    assert detail_view._quiz_open_btn.visible is True


def test_flet_web_runtime_on_windows_keeps_browser_fallback(monkeypatch):
    _page, _lease, players, player_view, detail_view = make_quiz_ui(monkeypatch)
    player_view._page.web = True

    detail_view.update_detail(trusted_quiz())

    assert detail_view._quiz_open_btn.visible is False
    assert detail_view._open_btn.visible is True
    assert detail_view._quiz_runtime_notice.visible is False
    assert players == []
    assert player_view.visible is False


def test_desktop_window_exit_waits_for_confirmation_and_restores_window(monkeypatch):
    async def scenario():
        page, _lease, _players, player_view, detail_view = make_quiz_ui(monkeypatch)
        page.window = SimpleNamespace(
            full_screen=False,
            width=420,
            height=720,
            min_width=420,
            max_width=420,
            min_height=720,
            max_height=720,
            resizable=False,
            maximizable=False,
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0)

        controller = AppController.__new__(AppController)
        controller.quiz_player_view = player_view
        controller._destroy_window = AsyncMock()
        await controller._request_window_exit()

        assert page.window.full_screen is True
        assert player_view._close_dialog.open is True
        assert player_view._close_dialog.title.value == "Thoát ứng dụng?"
        controller._destroy_window.assert_not_awaited()

        player_view._cancel_close()
        assert player_view._player is _players[0]
        assert page.window.full_screen is True
        controller._destroy_window.assert_not_awaited()

        await controller._request_window_exit()
        await player_view._confirm_close()

        assert player_view.visible is False
        assert page.window.full_screen is False
        assert page.window.width == 420
        controller._destroy_window.assert_awaited_once_with()

    asyncio.run(scenario())


def test_hidden_player_updates_theme_before_opening():
    previous_theme = settings.THEME
    try:
        apply_theme("midnight_blue")
        player_view = QuizPlayerView(
            SimpleNamespace(),
            player_factory=lambda **_kwargs: None,
        )
        assert player_view.visible is False
        assert player_view._close_dialog.bgcolor != "#FFFFFF"

        apply_theme("steel_light")
        player_view.update_theme()

        assert player_view.bgcolor == C.BG
        assert player_view._player_host.bgcolor == C.BG
        assert player_view._status_panel.bgcolor == C.SURFACE
        assert player_view._close_dialog.bgcolor == C.SURFACE
        assert player_view._close_dialog.title.color == C.TEXT_PRIMARY
        assert player_view._close_dialog.content.color == C.TEXT_SECONDARY
        assert player_view._rendered_palette == semantic_palette()
    finally:
        apply_theme(previous_theme)


def test_controller_rebuild_colors_refreshes_quiz_player_theme():
    previous_theme = settings.THEME
    try:
        apply_theme("midnight_blue")
        player_view = QuizPlayerView(
            SimpleNamespace(),
            player_factory=lambda **_kwargs: None,
        )
        controller = MagicMock()
        controller.dashboard = ft.Container()
        controller._rendered_palette = semantic_palette()
        controller.quiz_player_view = player_view

        apply_theme("steel_light")
        AppController._rebuild_colors(controller)

        assert player_view._rendered_palette == semantic_palette()
        assert player_view._close_dialog.bgcolor == C.SURFACE
        controller.page.update.assert_called_once_with()
    finally:
        apply_theme(previous_theme)


def test_failed_task_scheduler_releases_lease_and_shows_recovery(monkeypatch):
    enable_native_runtime(monkeypatch)

    class UnschedulablePage(HeadlessPage):
        def run_task(self, _callback, *_args):
            raise RuntimeError("task runner unavailable")

    page = UnschedulablePage()
    lease = Lease()
    player_view = QuizPlayerView(
        page,
        player_factory=lambda **_kwargs: ft.Container(expand=True),
        target_builder=build_quiz_launch_target,
        lease_provider=lambda _session_key: lease,
    )
    activity = trusted_quiz()
    target = build_quiz_launch_target(
        activity["url"], "student@example.edu"
    )

    opened = player_view.open_activity(target, activity)

    assert opened is False
    assert player_view.visible is True
    assert player_view._lease is None
    assert player_view._player is None
    assert lease.released is True
    assert player_view._error_actions.visible is True
    assert player_view._browser_button.visible is True
