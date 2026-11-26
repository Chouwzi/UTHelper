import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.today_schedule import ScheduleLoadStatus, TodayScheduleViewState
from gui.app_controller import AppController
from gui.components.login_dialog import show_login_dialog


@pytest.mark.parametrize("allow_cancel", [False, True])
def test_login_dialog_only_offers_cancel_when_requested(allow_cancel):
    shown_dialogs = []
    page = SimpleNamespace(
        width=400,
        show_dialog=shown_dialogs.append,
        pop_dialog=Mock(),
        update=Mock(),
    )
    client = SimpleNamespace(login=Mock())
    orchestrator = SimpleNamespace(client=client)

    asyncio.run(
        show_login_dialog(
            page,
            orchestrator,
            AsyncMock(),
            allow_cancel=allow_cancel,
        )
    )

    dialog = shown_dialogs[0]
    assert dialog.modal is True
    assert len(dialog.actions) == int(allow_cancel)
    if allow_cancel:
        cancel = dialog.actions[0]
        assert cancel.content == "Hủy"
        cancel.on_click(None)
        page.pop_dialog.assert_called_once_with()
    client.login.assert_not_called()


def test_successful_quiz_reauth_switches_account_state_before_refresh(monkeypatch):
    from config import settings
    from core.data_cache import DataCache

    controller = AppController.__new__(AppController)
    controller._settings_identity = ("old-user", "old-pass")
    controller.orchestrator = SimpleNamespace(
        moodle_service=SimpleNamespace(clear_all_caches=Mock()),
        clear_detail_cache=Mock(),
        _userid_cache=27,
        is_logged_in=True,
    )
    controller._portal_client = SimpleNamespace(clear_token=Mock())
    coordinator = SimpleNamespace(replace_cache=Mock(), refresh=Mock())
    controller._today_schedule_coordinator = coordinator
    controller._today_schedule_state = TodayScheduleViewState(
        ScheduleLoadStatus.READY
    )
    controller._today_schedule_component = SimpleNamespace(set_state=Mock())
    controller._reconcile_class_schedule_notifications = Mock()
    scheduled = []

    def safe_run_task(handler, *args, **kwargs):
        scheduled.append((handler, args, kwargs, controller._settings_identity))

    controller._safe_run_task = safe_run_task
    controller._load_data_async = AsyncMock()

    monkeypatch.setattr(settings, "UTH_USERNAME", "new-user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "new-pass")

    asyncio.run(AppController._complete_login_success(controller))

    assert controller._settings_identity == ("new-user", "new-pass")
    assert controller.orchestrator.moodle_service.clear_all_caches.call_count == 1
    controller.orchestrator.clear_detail_cache.assert_called_once_with()
    assert controller.orchestrator._userid_cache is None
    assert controller.orchestrator.is_logged_in is True
    assert controller._data_cache._cache_path == DataCache(
        namespace=f"{settings.MOODLE_BASE_URL}|new-user"
    )._cache_path
    assert coordinator.replace_cache.call_count == 1
    assert controller._today_schedule_state.status is ScheduleLoadStatus.IDLE
    controller._today_schedule_component.set_state.assert_called_once_with(
        controller._today_schedule_state
    )
    assert [call[0] for call in scheduled] == [
        controller._reconcile_class_schedule_notifications,
        coordinator.refresh,
    ]
    assert scheduled[0][2] == {"clear_only": True}
    assert all(call[3] == ("new-user", "new-pass") for call in scheduled)
    assert scheduled[1][1] == ("login",)
    controller._load_data_async.assert_awaited_once_with()


def test_both_login_flows_share_post_login_work_and_only_quiz_reauth_is_cancelable(
    monkeypatch,
):
    import gui.components.login_dialog as login_dialog

    controller = AppController.__new__(AppController)
    controller.page = object()
    controller.orchestrator = object()
    controller._diag_screen = Mock()
    controller._diag_breadcrumb = Mock()
    controller._show_snackbar = Mock()
    controller._complete_login_success = AsyncMock()
    captured = {}

    async def capture_dialog(
        page, orchestrator, on_success_callback, *, allow_cancel=False
    ):
        captured.update(
            page=page,
            orchestrator=orchestrator,
            callback=on_success_callback,
            allow_cancel=allow_cancel,
        )

    monkeypatch.setattr(login_dialog, "show_login_dialog", capture_dialog)
    asyncio.run(AppController._show_login_dialog(controller))
    asyncio.run(captured["callback"]())
    assert captured["allow_cancel"] is False
    controller._complete_login_success.assert_awaited_once_with()

    retry = AsyncMock()
    captured.clear()
    asyncio.run(AppController._show_quiz_auth_dialog(controller, retry))
    asyncio.run(captured["callback"]())
    assert captured["allow_cancel"] is True
    assert controller._complete_login_success.await_count == 2
    retry.assert_awaited_once_with()


@pytest.mark.parametrize(
    ("old_identity", "player_visible", "expect_clear"),
    [
        (("old-user", "old-pass"), False, True),
        (("old-user", "old-pass"), True, True),
        (("new-user", "new-pass"), False, False),
    ],
)
def test_login_dialog_account_switch_purges_quiz_sessions_on_identity_change(
    monkeypatch, old_identity, player_visible, expect_clear
):
    from config import settings

    controller = AppController.__new__(AppController)
    controller._settings_identity = old_identity
    controller.orchestrator = SimpleNamespace(
        moodle_service=SimpleNamespace(clear_all_caches=Mock()),
        clear_detail_cache=Mock(),
        _userid_cache=27,
        is_logged_in=True,
    )
    controller._portal_client = SimpleNamespace(clear_token=Mock())
    coordinator = SimpleNamespace(replace_cache=Mock(), refresh=Mock())
    controller._today_schedule_coordinator = coordinator
    controller._today_schedule_state = TodayScheduleViewState(
        ScheduleLoadStatus.READY
    )
    controller._today_schedule_component = SimpleNamespace(set_state=Mock())
    controller._reconcile_class_schedule_notifications = Mock()
    clear_persisted_sessions = AsyncMock(return_value=True)
    controller.quiz_player_view = SimpleNamespace(
        visible=player_visible,
        clear_persisted_sessions=clear_persisted_sessions,
    )
    scheduled = []

    def safe_run_task(handler, *args, **kwargs):
        scheduled.append((handler, args, kwargs))

    controller._safe_run_task = safe_run_task
    controller._load_data_async = AsyncMock()

    monkeypatch.setattr(settings, "UTH_USERNAME", "new-user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "new-pass")

    asyncio.run(AppController._complete_login_success(controller))

    clear_calls = [item for item in scheduled if item[0] is clear_persisted_sessions]
    assert bool(clear_calls) is expect_clear
