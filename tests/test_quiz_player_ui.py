from __future__ import annotations

import asyncio
import threading
import weakref
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


class FakeQuizEntryResult:
    def __init__(self, *, launch_url=None, secure_window=False, mode="resume"):
        self.exported_cookies = None
        self.launch_url = (
            "https://courses.ut.edu.vn/mod/quiz/attempt.php?attempt=77&cmid=321&page=0"
            if launch_url is None
            else launch_url
        )
        self.secure_window = secure_window
        self.mode = mode
        self.cleared = False

    def to_native_cookies(self):
        self.exported_cookies = [
            {
                "name": "MoodleSession",
                "value": "ephemeral-test-cookie-value",
                "domain": "courses.ut.edu.vn",
                "path": "/",
                "secure": True,
                "http_only": True,
                "same_site": None,
                "expires": None,
            }
        ]
        return self.exported_cookies

    def clear(self):
        if isinstance(self.exported_cookies, list):
            for cookie in self.exported_cookies:
                cookie.clear()
            self.exported_cookies.clear()
        self.cleared = True

    def __repr__(self):
        return "FakeQuizEntryResult(cookies=<redacted>)"


class FakePreparedEntry:
    def __init__(
        self,
        *,
        mode="resume",
        secure_window=False,
        password_required=False,
        reason=None,
        launch_url=None,
        enter_results=None,
        enter_hook=None,
    ):
        self.mode = mode
        self.secure_window = secure_window
        self.password_required = password_required
        self.reason = reason
        self.launch_url = launch_url
        self.enter_results = list(enter_results or [])
        self.enter_hook = enter_hook
        self.enter_calls = []
        self.results = []
        self.closed = False

    def enter(self, *, password=None, confirmed=False):
        self.enter_calls.append((password, confirmed))
        if self.enter_hook is not None:
            self.enter_hook(password, confirmed)
        if self.enter_results:
            result = self.enter_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            self.results.append(result)
            return result
        result = FakeQuizEntryResult(
            launch_url=self.launch_url,
            secure_window=self.secure_window,
            mode=self.mode,
        )
        self.results.append(result)
        return result

    def close(self):
        self.closed = True
        for result in self.results:
            result.clear()


def prepared_entry_provider(entries, factory=None):
    def provide(_target, _lease):
        entry = factory() if factory is not None else FakePreparedEntry()
        entries.append(entry)
        return entry

    return provide


class SessionFailure(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class EventSession:
    """Minimal Flet session used by BaseControl's real event dispatcher."""

    def __init__(self):
        self.index = {}

    async def after_event(self, _control=None):
        return None


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


def make_quiz_ui(
    monkeypatch,
    *,
    ready=True,
    on_closed=None,
    ready_timeout=0.2,
    entry_provider=None,
    lease_provider=None,
    account_id_provider=None,
    on_auth_required=None,
):
    enable_native_runtime(monkeypatch)
    page = HeadlessPage()
    lease = Lease(ready=ready)
    players = []
    prepared_entries = []

    def player_factory(**kwargs):
        player = ft.Container(expand=True)
        player.callbacks = kwargs
        async def bootstrap_session(cookies, *, launch_url=None):
            player.native_received_cookies = [dict(cookie) for cookie in cookies]
            player.native_launch_url = launch_url
            return True

        player.bootstrap_session = bootstrap_session
        players.append(player)
        return player

    if entry_provider is None:
        def entry_provider(_target, _lease):
            prepared = FakePreparedEntry()
            prepared_entries.append(prepared)
            return prepared

    player_view = QuizPlayerView(
        page,
        on_closed=on_closed,
        player_factory=player_factory,
        target_builder=build_quiz_launch_target,
        lease_provider=lease_provider or (lambda _session_key: lease),
        entry_provider=entry_provider,
        account_id_provider=account_id_provider,
        on_auth_required=on_auth_required,
        ready_timeout_seconds=ready_timeout,
    )
    detail_view = DetailView(
        page,
        lambda: None,
        quiz_player_view=player_view,
        get_quiz_account_id=lambda: "student@example.edu",
    )
    return page, lease, players, prepared_entries, player_view, detail_view


async def dispatch_control_event(control, event_name: str, event_data=None):
    """Dispatch through Flet's control event handler, including async callbacks."""
    session = EventSession()
    page = ft.Page(sess=session)
    control._parent = weakref.ref(page)
    session.index[control._i] = control
    await control._trigger_event(event_name, event_data)


def test_player_view_constructor_accepts_flet_page_like_object():
    view = QuizPlayerView(MagicMock(), player_factory=lambda **_kwargs: None)

    assert view.visible is False
    assert view._page is not None


def test_quiz_button_mounts_player_and_close_refreshes_from_moodle(monkeypatch):
    async def scenario():
        page, lease, players, prepared_entries, player_view, detail_view = make_quiz_ui(monkeypatch)
        activity = trusted_quiz()
        detail_view.update_detail(activity)

        assert detail_view._quiz_open_btn.visible is True
        assert detail_view._quiz_open_btn.content == "Làm bài"
        assert detail_view._open_btn.visible is False
        assert detail_view._header_open_btn.visible is True

        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        assert player_view.visible is True
        assert len(players) == 1
        assert prepared_entries[0].enter_calls == [(None, False)]
        assert player_view._entry_prompt_open is False
        assert player_view._player_host.content is players[0]
        # The overview remains the control URL; the server's returned attempt
        # URL travels separately through bootstrap_session.
        assert players[0].callbacks["url"] == activity["url"]
        assert players[0].callbacks["session_key"]
        assert players[0].callbacks["require_session"] is True
        assert not hasattr(players[0], "cookies")
        assert "ephemeral-test-cookie-value" not in repr(players[0])
        assert "ephemeral-test-cookie-value" not in repr(player_view)
        await players[0].callbacks["on_ready"](SimpleNamespace(data="ready"))
        assert players[0].native_received_cookies[0]["value"] == "ephemeral-test-cookie-value"
        assert players[0].native_launch_url == prepared_entries[0].results[0].launch_url
        assert prepared_entries[0].results[0].exported_cookies == []
        assert prepared_entries[0].results[0].cleared is True
        assert prepared_entries[0].closed is True
        assert player_view._ready is True
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


def test_failed_quiz_open_publishes_recovery_controls(monkeypatch):
    async def scenario():
        page, _lease, _players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch
        )
        detail_view.update_detail(trusted_quiz())
        page.update = MagicMock()
        player_view.open_activity_for_account = MagicMock(return_value=False)

        await detail_view._open_quiz(None)

        player_view.open_activity_for_account.assert_called_once()
        assert detail_view._open_btn.visible is True
        assert detail_view._quiz_open_btn.visible is False
        assert detail_view._quiz_runtime_notice.visible is True
        page.update.assert_called_once_with()

    asyncio.run(scenario())


def test_new_attempt_requires_confirmation_before_entry_post(monkeypatch):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(mode="start"),
        )
        page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        detail_view.update_detail(trusted_quiz())

        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        entry = prepared_entries[0]
        assert player_view._entry_prompt_open is True
        assert player_view._entry_dialog.open is True
        assert entry.enter_calls == []
        assert players == []

        await player_view._confirm_entry_prompt()

        assert entry.enter_calls == [(None, True)]
        assert entry.closed is True
        assert len(players) == 1
        assert players[0].callbacks["url"] == trusted_quiz()["url"]
        assert lease.released is False

    asyncio.run(scenario())


@pytest.mark.parametrize("dismiss", [False, True], ids=["cancel", "dismiss"])
def test_cancel_or_dismiss_start_confirmation_discards_without_entry(monkeypatch, dismiss):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(mode="start"),
        )
        page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        entry = prepared_entries[0]
        assert entry.enter_calls == []
        if dismiss:
            player_view._entry_dialog.open = False
            await dispatch_control_event(player_view._entry_dialog, "dismiss")
        else:
            cancel_button = player_view._entry_actions[0]
            await dispatch_control_event(cancel_button, "click")

        assert player_view.visible is False
        assert player_view._entry_prompt_open is False
        assert entry.closed is True
        assert entry.enter_calls == []
        assert players == []
        assert lease.released is True

    asyncio.run(scenario())


def test_password_retry_clears_rejection_and_enter_submits_through_flet_dispatch(
    monkeypatch,
):
    async def scenario():
        prepared_entries = []
        accepted_result = FakeQuizEntryResult(
            launch_url=(
                "https://courses.ut.edu.vn/mod/quiz/attempt.php"
                "?attempt=81&cmid=321&page=0"
            ),
            mode="start",
        )
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(
                mode="start",
                password_required=True,
                enter_results=[
                    SessionFailure("wrong_password"),
                    accepted_result,
                ],
            ),
        )
        _page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        entry = prepared_entries[0]
        field = player_view._entry_password_field
        assert field is not None
        assert player_view._entry_prompt_open is True
        assert entry.enter_calls == []

        field.value = "wrong-password"
        await dispatch_control_event(player_view._entry_actions[1], "click")
        assert entry.enter_calls == [("wrong-password", True)]
        assert player_view._entry_prompt_open is True
        assert field.value == ""
        assert player_view._entry_prompt_error.visible is True
        assert players == []

        field.value = "correct-password"
        await dispatch_control_event(field, "change", {"data": "correct-password"})
        assert player_view._entry_prompt_error.visible is False
        await dispatch_control_event(field, "submit", {"data": "Enter"})

        assert entry.enter_calls == [
            ("wrong-password", True),
            ("correct-password", True),
        ]
        assert player_view._entry_password_field.value == ""
        assert player_view._entry_prompt_open is False
        assert entry.closed is True
        assert accepted_result.cleared is True
        assert len(players) == 1
        assert players[0].callbacks["url"] == trusted_quiz()["url"]
        await players[0].callbacks["on_ready"](SimpleNamespace(data="ready"))
        assert players[0].native_launch_url == accepted_result.launch_url
        assert player_view._ready is True
        assert lease.released is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("remaining", "unfinished", "disabled", "label"),
    [
        (0, False, True, "Hết lượt làm bài"),
        (0, True, False, "Tiếp tục làm bài"),
        (0, None, False, "Làm bài"),
        (None, False, False, "Làm bài"),
    ],
)
def test_quiz_button_disables_only_authoritative_exhaustion(
    monkeypatch, remaining, unfinished, disabled, label
):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(prepared_entries)
        _page, _lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        activity = trusted_quiz(
            details={
                "quiz_attempts_remaining": remaining,
                "quiz_has_unfinished_attempt": unfinished,
            }
        )
        detail_view.update_detail(activity)

        assert detail_view._quiz_open_btn.visible is True
        assert detail_view._quiz_open_btn.disabled is disabled
        assert detail_view._quiz_open_btn.content == label
        assert detail_view._quiz_open_btn.bgcolor == (
            C.SURFACE if disabled else C.ACCENT
        )
        assert detail_view._quiz_open_btn.color == (
            C.TEXT_SECONDARY if disabled else C.ON_ACCENT
        )
        assert detail_view._open_btn.visible is (disabled is True)
        assert detail_view._header_open_btn.visible is True

        if unfinished is True:
            await detail_view._open_quiz(None)
            await asyncio.sleep(0.02)
            assert prepared_entries[0].mode == "resume"
            assert prepared_entries[0].enter_calls == [(None, False)]
            assert len(players) == 1
        else:
            assert prepared_entries == []
            if disabled:
                detail_view.update_detail(
                    trusted_quiz(
                        details={
                            "quiz_attempts_remaining": 1,
                            "quiz_has_unfinished_attempt": False,
                        }
                    )
                )
                assert detail_view._quiz_open_btn.disabled is False
                assert detail_view._quiz_open_btn.bgcolor == C.ACCENT
                assert detail_view._quiz_open_btn.color == C.ON_ACCENT

    asyncio.run(scenario())


def test_missing_launch_url_does_not_load_overview_as_attempt(monkeypatch):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(mode="resume", launch_url=""),
        )
        _page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        activity = trusted_quiz()
        detail_view.update_detail(activity)
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        assert prepared_entries[0].enter_calls == [(None, False)]
        assert prepared_entries[0].closed is True
        assert players == []
        assert player_view.visible is False
        assert player_view._launch_url is None
        assert detail_view._quiz_runtime_notice.visible is True
        assert detail_view._open_btn.visible is False
        assert detail_view._header_open_btn.visible is True
        assert lease.released is True

    asyncio.run(scenario())


def test_live_safe_exam_browser_requirement_preserves_browser_recovery(monkeypatch):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(
                mode="unavailable",
                reason="requires_safe_exam_browser",
            ),
        )
        _page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
        activity = trusted_quiz()
        detail_view.update_detail(activity)
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        assert prepared_entries[0].enter_calls == []
        assert prepared_entries[0].closed is True
        assert players == []
        assert player_view.visible is False
        assert detail_view._quiz_runtime_notice.visible is True
        assert "safe exam browser" in detail_view._quiz_runtime_notice.value.casefold()
        assert "trình duyệt" in detail_view._quiz_runtime_notice.value.casefold()
        assert detail_view._header_open_btn.visible is True
        assert lease.released is True

    asyncio.run(scenario())


def test_transient_quiz_load_error_keeps_mounted_form_and_session_lease(monkeypatch):
    async def scenario():
        page, lease, players, _sessions, player_view, detail_view = make_quiz_ui(monkeypatch)
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        player = players[0]
        await player.callbacks["on_ready"](SimpleNamespace(data="ready"))
        player.callbacks["on_error"](SimpleNamespace(data="navigation_error"))

        assert player_view._player is player
        assert player_view._player_host.content is player
        assert player_view._lease is lease
        assert lease.released is False
        assert player_view._status_panel.visible is True
        assert "kiểm tra kết nối" in player_view._status_text.value.casefold()

        player.callbacks["on_session_expired"](SimpleNamespace(data="session_expired"))
        assert player_view._player is player
        assert player_view._player_host.content is player
        assert player_view._lease is lease
        assert lease.released is False
        assert player_view._auth_button.visible is False
        assert "hết hạn" in player_view._status_text.value.casefold()

        player_view.handle_back()
        player_view._cancel_close()
        assert player_view._player_host.content is player
        assert lease.released is False

    asyncio.run(scenario())


def test_existing_login_wait_has_a_finite_timeout_and_releases_unmounted_lease(
    monkeypatch,
):
    async def scenario():
        page, lease, players, _sessions, player_view, detail_view = make_quiz_ui(
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
    _page, _lease, players, _sessions, player_view, detail_view = make_quiz_ui(monkeypatch)
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


def test_failed_source_client_build_keeps_explicit_browser_action(monkeypatch):
    enable_native_runtime(monkeypatch)
    from platform_utils import quiz_client_bootstrap

    monkeypatch.setattr(quiz_client_bootstrap, "_source_quiz_client_ready", False)
    _page, _lease, players, _sessions, player_view, detail_view = make_quiz_ui(
        monkeypatch
    )

    detail_view.update_detail(trusted_quiz())

    assert player_view.native_runtime_supported() is False
    assert detail_view._quiz_open_btn.visible is False
    assert detail_view._quiz_runtime_notice.visible is True
    assert "trình duyệt" in detail_view._quiz_runtime_notice.value.casefold()
    assert detail_view._open_btn.visible is True
    assert players == []


def test_missing_saved_account_opens_existing_app_login_and_rebuilds_quiz_target(
    monkeypatch,
):
    async def scenario():
        username = {"value": ""}
        login_requests = []

        async def show_app_login(retry_callback):
            login_requests.append("opened")
            username["value"] = "student@example.edu"
            await retry_callback()

        page, _lease, players, _sessions, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            account_id_provider=lambda: username["value"],
            on_auth_required=show_app_login,
        )
        detail_view._get_quiz_account_id = lambda: username["value"]

        activity = trusted_quiz()
        detail_view.update_detail(activity)
        assert detail_view._quiz_auth_btn.visible is True
        assert detail_view._quiz_open_btn.visible is False

        await detail_view._login_for_quiz(None)

        assert login_requests == ["opened"]
        assert detail_view._quiz_auth_btn.visible is False
        assert detail_view._quiz_open_btn.visible is True
        assert players == []
        assert player_view.visible is False

    asyncio.run(scenario())


def test_secure_window_quiz_remains_available_to_moodles_page(monkeypatch):
    _page, _lease, _players, _sessions, player_view, detail_view = make_quiz_ui(monkeypatch)
    activity = trusted_quiz(
        quiz_security={"browsersecurity": "securewindow"}
    )

    detail_view.update_detail(activity)
    target, state = player_view.prepare_activity(activity, "student@example.edu")

    assert state is None
    assert target.secure_window is True
    assert detail_view._quiz_open_btn.visible is True


def test_flet_web_runtime_on_windows_keeps_browser_fallback(monkeypatch):
    _page, _lease, players, _sessions, player_view, detail_view = make_quiz_ui(monkeypatch)
    player_view._page.web = True

    detail_view.update_detail(trusted_quiz())

    assert detail_view._quiz_open_btn.visible is False
    assert detail_view._open_btn.visible is True
    assert detail_view._quiz_runtime_notice.visible is False
    assert players == []
    assert player_view.visible is False


def test_normal_quiz_exit_waits_for_confirmation_without_fullscreen(monkeypatch):
    async def scenario():
        page, _lease, _players, _sessions, player_view, detail_view = make_quiz_ui(monkeypatch)
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
        await asyncio.sleep(0.02)

        controller = AppController.__new__(AppController)
        controller.quiz_player_view = player_view
        controller._destroy_window = AsyncMock()
        await controller._request_window_exit()

        assert page.window.full_screen is False
        assert player_view._close_dialog.open is True
        assert player_view._close_dialog.title.value == "Thoát ứng dụng?"
        controller._destroy_window.assert_not_awaited()

        player_view._cancel_close()
        assert player_view._player is _players[0]
        assert page.window.full_screen is False
        controller._destroy_window.assert_not_awaited()

        await controller._request_window_exit()
        await player_view._confirm_close()

        assert player_view.visible is False
        assert page.window.full_screen is False
        assert page.window.width == 420
        controller._destroy_window.assert_awaited_once_with()

    asyncio.run(scenario())


def test_securewindow_attempt_enters_fullscreen_and_restores_previous_window(
    monkeypatch,
):
    async def scenario():
        prepared_entries = []
        provider = prepared_entry_provider(
            prepared_entries,
            lambda: FakePreparedEntry(secure_window=True),
        )
        page, _lease, _players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=provider,
        )
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
        await asyncio.sleep(0.02)

        assert prepared_entries[0].enter_calls == [(None, False)]
        assert page.window.full_screen is True
        assert player_view._player is not None

        player_view.request_close()
        player_view._cancel_close()
        assert player_view._player is not None
        assert page.window.full_screen is True

        player_view.request_close()
        await player_view._confirm_close()
        assert player_view.visible is False
        assert page.window.full_screen is False
        assert page.window.width == 420
        assert page.window.height == 720
        assert page.window.resizable is False

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


def test_saved_session_failure_uses_app_login_then_mounts_with_fresh_cookies(monkeypatch):
    async def scenario():
        attempts = []
        prepared_entries = []
        auth_requests = []

        def entry_provider(_target, _lease):
            attempts.append("attempt")
            if len(attempts) == 1:
                raise SessionFailure("login_failed")
            prepared = FakePreparedEntry()
            prepared_entries.append(prepared)
            return prepared

        async def app_auth(retry_callback):
            auth_requests.append("shown")
            await retry_callback()

        page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=entry_provider,
            on_auth_required=app_auth,
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        assert player_view._player is None
        assert player_view._auth_button.visible is True
        assert player_view._browser_button.visible is True
        assert players == []
        assert lease.released is False

        await player_view._request_authentication()
        await asyncio.sleep(0.02)

        assert auth_requests == ["shown"]
        assert len(attempts) == 2
        assert len(players) == 1
        assert players[0].callbacks["require_session"] is True
        await players[0].callbacks["on_ready"](SimpleNamespace(data="ready"))
        assert player_view._ready is True
        assert player_view._auth_button.visible is False
        assert prepared_entries[0].results[0].exported_cookies == []

    asyncio.run(scenario())


def test_bootstrap_auth_retry_invalidates_old_player_callbacks(monkeypatch):
    async def scenario():
        prepared_entries = []
        auth_requests = []
        mount_count = 0

        async def app_auth(retry_callback):
            auth_requests.append("shown")
            await retry_callback()

        enable_native_runtime(monkeypatch)
        page = HeadlessPage()
        lease = Lease()
        players = []

        def player_factory(**kwargs):
            nonlocal mount_count
            mount_count += 1
            player = ft.Container(expand=True)
            player.callbacks = kwargs

            async def bootstrap_session(cookies, *, launch_url=None):
                player.native_received_cookies = [dict(cookie) for cookie in cookies]
                player.native_launch_url = launch_url
                if mount_count == 1:
                    raise RuntimeError("session_unavailable")
                return True

            player.bootstrap_session = bootstrap_session
            players.append(player)
            return player

        player_view = QuizPlayerView(
            page,
            player_factory=player_factory,
            target_builder=build_quiz_launch_target,
            lease_provider=lambda _key: lease,
            entry_provider=prepared_entry_provider(prepared_entries),
            on_auth_required=app_auth,
        )
        detail_view = DetailView(
            page,
            lambda: None,
            quiz_player_view=player_view,
            get_quiz_account_id=lambda: "student@example.edu",
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)

        first_player = players[0]
        old_ready = first_player.callbacks["on_ready"]
        old_error = first_player.callbacks["on_error"]
        await old_ready(SimpleNamespace(data="ready"))
        assert player_view._auth_button.visible is True
        assert player_view._ready is False

        await player_view._request_authentication()
        await asyncio.sleep(0.02)
        assert len(players) == 2

        second_player = players[1]
        await old_ready(SimpleNamespace(data="ready"))
        old_error(SimpleNamespace(data="webview_unavailable"))
        assert player_view._player is second_player
        assert player_view._player_host.content is second_player
        assert player_view.visible is True
        assert player_view._auth_button.visible is False
        assert len(auth_requests) == 1

        await second_player.callbacks["on_ready"](SimpleNamespace(data="ready"))
        assert player_view._ready is True
        assert [entry.results[0].exported_cookies for entry in prepared_entries] == [[], []]
        assert [entry.closed for entry in prepared_entries] == [True, True]

    asyncio.run(scenario())


def test_auth_retry_closes_prepared_entry_off_the_ui_loop(monkeypatch):
    async def scenario():
        enable_native_runtime(monkeypatch)
        close_started = threading.Event()
        release_close = threading.Event()
        close_thread = []

        class BlockingPreparedEntry:
            def close(self):
                close_thread.append(threading.get_ident())
                close_started.set()
                release_close.wait(2)

        page = HeadlessPage()
        player_view = QuizPlayerView(
            page,
            player_factory=lambda **_kwargs: ft.Container(expand=True),
        )
        player_view.visible = True
        player_view._activity = trusted_quiz()
        player_view._lease = Lease()
        player_view._account_id = "student@example.edu"
        player_view._prepared_entry = BlockingPreparedEntry()
        player_view._run_task = MagicMock(return_value=True)
        ui_thread = threading.get_ident()
        release_guard = threading.Timer(2, release_close.set)
        release_guard.start()

        try:
            retry = asyncio.create_task(player_view._retry_after_auth())
            assert await asyncio.to_thread(close_started.wait, 1)
            assert close_thread and close_thread[0] != ui_thread

            # The event loop continues dispatching while prepared-session close
            # is blocked in its worker thread.
            await asyncio.sleep(0)
            assert retry.done() is False

            release_close.set()
            await retry
        finally:
            release_close.set()
            release_guard.cancel()

        assert player_view._prepared_entry is None
        player_view._run_task.assert_called_once()

    asyncio.run(scenario())


def test_close_while_entry_preparation_is_pending_releases_late_result(monkeypatch):
    async def scenario():
        started = threading.Event()
        release = threading.Event()
        prepared_entries = []

        def entry_provider(_target, _lease):
            started.set()
            assert release.wait(2)
            prepared = FakePreparedEntry()
            prepared_entries.append(prepared)
            return prepared

        page, lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            entry_provider=entry_provider,
        )
        detail_view.update_detail(trusted_quiz())
        await detail_view._open_quiz(None)
        assert await asyncio.to_thread(started.wait, 1)

        player_view.request_close()
        await asyncio.sleep(0.02)
        assert player_view.visible is False
        assert lease.released is False

        release.set()
        await asyncio.sleep(0.05)
        assert lease.released is True
        assert players == []
        assert prepared_entries[0].closed is True
        assert player_view.visible is False
        assert player_view._player is None

    asyncio.run(scenario())


def test_late_old_session_error_cannot_clear_new_open_pending_cookies(monkeypatch):
    async def scenario():
        import gui.components.quiz_player_view as player_module

        monkeypatch.setattr(player_module, "_ENTRY_PREPARE_TIMEOUT_SECONDS", 0.01)
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []
        prepared_entries = []

        def entry_provider(_target, _lease):
            calls.append(len(calls) + 1)
            if calls[-1] == 1:
                first_started.set()
                assert release_first.wait(2)
                raise SessionFailure("login_failed")
            prepared = FakePreparedEntry()
            prepared_entries.append(prepared)
            return prepared

        enable_native_runtime(monkeypatch)
        page = HeadlessPage()
        first_lease = Lease()
        leases = [first_lease]
        players = []

        def player_factory(**kwargs):
            player = ft.Container(expand=True)
            player.callbacks = kwargs

            async def bootstrap_session(cookies, *, launch_url=None):
                player.native_received_cookies = [dict(cookie) for cookie in cookies]
                player.native_launch_url = launch_url
                return True

            player.bootstrap_session = bootstrap_session
            players.append(player)
            return player

        player_view = QuizPlayerView(
            page,
            player_factory=player_factory,
            target_builder=build_quiz_launch_target,
            lease_provider=lambda _key: leases[-1],
            entry_provider=entry_provider,
            ready_timeout_seconds=0.2,
        )
        first_activity = trusted_quiz()
        first_target = build_quiz_launch_target(
            first_activity["url"], "student@example.edu"
        )
        assert player_view.open_activity_for_account(
            first_target, first_activity, "student@example.edu"
        ) is True
        assert await asyncio.to_thread(first_started.wait, 1)
        await asyncio.sleep(0.03)
        assert "Chưa lấy được phiên Moodle" in player_view._status_text.value

        player_view.request_close()
        await asyncio.sleep(0.02)
        assert player_view.visible is False
        assert first_lease.released is False

        second_lease = Lease()
        leases.append(second_lease)
        second_activity = trusted_quiz(id="quiz-10", url="https://courses.ut.edu.vn/mod/quiz/view.php?id=322")
        second_target = build_quiz_launch_target(
            second_activity["url"], "student@example.edu"
        )
        assert player_view.open_activity_for_account(
            second_target, second_activity, "student@example.edu"
        ) is True
        await asyncio.sleep(0.03)
        assert len(players) == 1
        pending = player_view._pending_cookies
        assert pending and pending[0]["value"] == "ephemeral-test-cookie-value"

        release_first.set()
        await asyncio.sleep(0.05)
        assert first_lease.released is True
        assert player_view.visible is True
        assert player_view._target.course_module_id == 322
        assert player_view._pending_cookies is pending
        assert pending[0]["value"] == "ephemeral-test-cookie-value"
        assert "Chưa lấy được phiên Moodle" not in player_view._status_text.value

        await players[0].callbacks["on_ready"](SimpleNamespace(data="ready"))
        assert prepared_entries[0].results[0].exported_cookies == []
        assert player_view._ready is True

    asyncio.run(scenario())


def test_prebootstrap_account_change_releases_old_lease_and_rebuilds_target(
    monkeypatch,
):
    async def scenario():
        username = {"value": "old-student@example.edu"}
        leases = [Lease(), Lease()]
        lease_calls = []
        target_keys = []
        attempt = []

        def get_lease(session_key):
            lease_calls.append(session_key)
            return leases[len(lease_calls) - 1]

        def entry_provider(target, lease):
            target_keys.append((target.session_key, lease))
            attempt.append("called")
            if len(attempt) == 1:
                raise SessionFailure("account_mismatch")
            return FakePreparedEntry()

        async def app_auth(retry_callback):
            username["value"] = "new-student@example.edu"
            await retry_callback()

        page, _lease, players, _entries, player_view, detail_view = make_quiz_ui(
            monkeypatch,
            lease_provider=get_lease,
            entry_provider=entry_provider,
            account_id_provider=lambda: username["value"],
            on_auth_required=app_auth,
        )
        detail_view._get_quiz_account_id = lambda: username["value"]
        detail_view.update_detail(trusted_quiz())
        old_target = player_view.prepare_activity(
            trusted_quiz(), username["value"]
        )[0]
        await detail_view._open_quiz(None)
        await asyncio.sleep(0.02)
        assert player_view._auth_button.visible is True

        await player_view._request_authentication()
        await asyncio.sleep(0.02)

        new_target = player_view._target
        assert new_target.session_key != old_target.session_key
        assert player_view._account_id == username["value"]
        assert lease_calls == [old_target.session_key, new_target.session_key]
        assert leases[0].released is True
        assert leases[1].released is False
        assert target_keys == [
            (old_target.session_key, leases[0]),
            (new_target.session_key, leases[1]),
        ]
        assert len(players) == 1
        assert players[0].callbacks["url"] == new_target.url

    asyncio.run(scenario())
