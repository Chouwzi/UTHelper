import os
import sys
import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import flet as ft

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import gui.app_controller as app_controller_module
from core.today_schedule import ScheduleLoadStatus, TodayScheduleViewState
from core.update_coordinator import UpdateEvent, UpdateEventKind
from core.update_models import ReleasePackage
from gui.app_controller import (
    AppController,
    _AndroidPackageLauncher,
    _android_release_build_number,
)


def test_render_cards_refreshes_current_filters():
    controller = AppController.__new__(AppController)
    calls = []

    controller._refresh_ui = lambda: calls.append("refresh")

    controller._render_cards()

    assert calls == ["refresh"]


def test_dashboard_filter_models_share_counts_and_short_trigger_labels():
    urgency = AppController._urgency_filter_options(
        {"all": 12, "critical": 1, "warning": 2, "safe": 9, "overdue": 0}
    )
    activity_type = AppController._type_filter_options(
        {"all": 12, "quiz": 4, "assignment": 8}
    )
    courses = AppController._course_filter_options(
        {"all": 12, "Course B": 5, "Course A": 7}
    )

    assert urgency[0].trigger_label == "Mức độ"
    assert urgency[1].count == 1
    assert activity_type[0].trigger_label == "Tất cả"
    assert activity_type[1].count == 4
    assert courses[0].trigger_label == "Môn học"
    assert [option.key for option in courses] == ["all", "Course A", "Course B"]


def test_every_dashboard_filter_option_has_an_icon_for_fast_scanning():
    option_groups = [
        AppController._urgency_filter_options(),
        AppController._type_filter_options(),
        AppController._course_filter_options({"all": 0, "Course": 1}),
    ]

    assert all(option.icon is not None for group in option_groups for option in group)


def test_dashboard_filter_row_distributes_removed_checkbox_space():
    controller = AppController.__new__(AppController)
    controller.urgency_filter_menu = SimpleNamespace(control=ft.Container())
    controller.type_filter_menu = SimpleNamespace(control=ft.Container())
    controller.course_filter_menu = SimpleNamespace(control=ft.Container())

    row = controller._build_dashboard_filter_row()

    assert row.spacing == 6
    assert [control.expand for control in row.controls] == [4, 4, 5]


class _FakePage:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


class _FakeCard:
    def __init__(self, critical: bool = False):
        self._is_critical_active = critical
        self.shadow = None
        self.update_count = 0
        self.countdown_count = 0

    def update(self):
        self.update_count += 1

    def update_countdown(self):
        self.countdown_count += 1
        return True  # Simulate changed countdown


def test_pulse_tick_batches_page_update_without_per_card_updates():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    cards = [_FakeCard(critical=True), _FakeCard(critical=False)]

    controller._pulse_cards_once(cards, pulse_high=True)

    assert cards[0].shadow is not None
    assert [c.update_count for c in cards] == [0, 0]
    assert controller.page.update_count == 1


def test_countdown_tick_batches_page_update_without_per_card_updates():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    cards = [_FakeCard(), _FakeCard()]

    controller._countdown_cards_once(cards)

    assert [c.countdown_count for c in cards] == [1, 1]
    assert [c.update_count for c in cards] == [0, 0]
    assert controller.page.update_count == 1


def test_today_schedule_retry_requests_portal_refresh_instead_of_moodle_data():
    controller = AppController.__new__(AppController)
    calls = []

    def refresh(trigger):
        return None

    controller._today_schedule_coordinator = SimpleNamespace(
        state=TodayScheduleViewState(ScheduleLoadStatus.ERROR),
        refresh=refresh,
        ensure_today=lambda: None,
    )
    controller._safe_run_task = lambda handler, *args: calls.append((handler, args))

    controller._request_today_schedule()

    assert calls == [(refresh, ("retry",))]


def test_today_schedule_state_updates_component_and_page_once():
    controller = AppController.__new__(AppController)
    calls = []
    controller._today_schedule_component = SimpleNamespace(
        set_state=lambda state: calls.append(("state", state))
    )
    controller.page = SimpleNamespace(update=lambda: calls.append(("update",)))
    state = TodayScheduleViewState(ScheduleLoadStatus.LOADING)

    controller._on_today_schedule_state(state)

    assert controller._today_schedule_state is state
    assert calls == [("state", state), ("update",)]


def test_safe_run_task_reports_unhandled_background_exception(caplog):
    scheduled = []
    reported = []
    page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=lambda handler, *args, **kwargs: scheduled.append(
            (handler, args, kwargs)
        ),
    )
    controller = AppController.__new__(AppController)
    controller.page = page
    controller._unhandled_error_reporter = reported.append

    async def fail():
        raise RuntimeError("private task detail")

    with caplog.at_level(logging.ERROR, logger="gui.app_controller"):
        controller._safe_run_task(fail)
        asyncio.run(scheduled[0][0]())

    assert len(reported) == 1
    assert isinstance(reported[0], RuntimeError)
    assert "Background UI task failed" in caplog.text


def test_mobile_root_view_intercepts_back_and_cancels_native_pop():
    async def scenario():
        root = SimpleNamespace(can_pop=True, on_confirm_pop=None)
        controller = AppController.__new__(AppController)
        controller.page = SimpleNamespace(views=[root])
        controller._navigate_back = AsyncMock(return_value=True)

        controller._configure_mobile_back_navigation()

        assert root.can_pop is False
        assert root.on_confirm_pop == controller._on_root_view_confirm_pop
        event_view = SimpleNamespace(confirm_pop=AsyncMock())
        await controller._on_root_view_confirm_pop(
            SimpleNamespace(control=event_view)
        )
        event_view.confirm_pop.assert_awaited_once_with(False)

    from unittest.mock import AsyncMock

    asyncio.run(scenario())


def test_mobile_root_view_allows_native_pop_from_dashboard():
    async def scenario():
        from unittest.mock import AsyncMock

        controller = AppController.__new__(AppController)
        controller._navigate_back = AsyncMock(return_value=False)
        event_view = SimpleNamespace(confirm_pop=AsyncMock())

        await controller._on_root_view_confirm_pop(
            SimpleNamespace(control=event_view)
        )

        event_view.confirm_pop.assert_awaited_once_with(True)

    asyncio.run(scenario())


def test_shared_back_navigation_handles_settings_dialog_and_grades():
    async def scenario():
        from unittest.mock import AsyncMock

        controller = AppController.__new__(AppController)
        controller.settings_view = SimpleNamespace(
            visible=True,
            _handle_back=AsyncMock(),
        )
        controller.detail_view = SimpleNamespace(visible=False)
        controller.calendar_view = SimpleNamespace(visible=False)
        controller.grade_overview_view = SimpleNamespace(visible=False)
        controller._close_detail = AsyncMock()
        controller._close_calendar = AsyncMock()
        controller._close_grades = AsyncMock()

        assert await controller._navigate_back() is True
        controller.settings_view._handle_back.assert_awaited_once()

        controller.settings_view.visible = False
        controller.grade_overview_view.visible = True
        assert await controller._navigate_back() is True
        controller._close_grades.assert_awaited_once_with()

    asyncio.run(scenario())


def test_shared_back_navigation_returns_to_dashboard_from_every_child_view():
    async def scenario():
        from unittest.mock import AsyncMock

        controller = AppController.__new__(AppController)
        controller.settings_view = SimpleNamespace(
            visible=False,
            _handle_back=AsyncMock(),
        )
        controller.detail_view = SimpleNamespace(visible=False)
        controller.calendar_view = SimpleNamespace(visible=False)
        controller.grade_overview_view = SimpleNamespace(visible=False)
        controller._close_detail = AsyncMock()
        controller._close_calendar = AsyncMock()
        controller._close_grades = AsyncMock()

        cases = (
            (controller.settings_view, controller.settings_view._handle_back),
            (controller.detail_view, controller._close_detail),
            (controller.calendar_view, controller._close_calendar),
            (controller.grade_overview_view, controller._close_grades),
        )
        for view, handler in cases:
            view.visible = True
            assert await controller._navigate_back() is True
            handler.assert_awaited_once()
            view.visible = False

        assert await controller._navigate_back() is False

    asyncio.run(scenario())


def test_window_close_treats_already_closed_session_as_clean_shutdown(monkeypatch):
    async def scenario():
        from unittest.mock import AsyncMock

        controller = AppController.__new__(AppController)
        controller.page = SimpleNamespace(
            window=SimpleNamespace(
                destroy=AsyncMock(side_effect=RuntimeError("Session closed"))
            )
        )
        monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", False)
        monkeypatch.setattr(app_controller_module.settings, "MINIMIZE_TO_TRAY", False)

        await controller._on_window_event(SimpleNamespace(type="close", data="close"))

        controller.page.window.destroy.assert_awaited_once_with()

    asyncio.run(scenario())


def test_android_build_number_is_deterministic_and_strict():
    assert _android_release_build_number("2.2.3") == 2_002_003


def test_android_launcher_forwards_native_identity_version_and_signer():
    calls = []

    class Bridge:
        available = True

        async def install_update(self, *args):
            calls.append(args)
            return {"status": "installer_opened"}

        async def cancel_update(self):
            pass

    def run_task(handler, *args):
        asyncio.run(handler(*args))
        return object()

    package = ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.2.3/UTHelper-2.2.3.apk",
        sha256="a" * 64,
        size=123,
        signer_identity="com.uthelper.uthelper",
        certificate_fingerprint="b" * 64,
        install_strategy={"kind": "android_package_installer"},
    )
    launcher = _AndroidPackageLauncher(Bridge(), run_task, lambda: "2.2.3")

    assert launcher.launch(Path("UTHelper.apk"), package).acknowledged
    assert calls == [
        (
            package.url,
            package.sha256,
            package.size,
            "com.uthelper.uthelper",
            2_002_003,
            package.certificate_fingerprint,
        )
    ]


def test_update_confirmation_uses_required_windows_affirmative_copy(monkeypatch):
    controller = AppController.__new__(AppController)
    shown = []
    controller.page = SimpleNamespace(show_dialog=shown.append)
    controller._update_coordinator = SimpleNamespace(confirm_install=lambda: None)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_ANDROID", False)

    controller._show_update_confirmation()

    assert shown[0].actions[1].content == "Cài đặt và thoát"


def test_update_confirmation_explains_ios_sideload_and_resigning(monkeypatch):
    controller = AppController.__new__(AppController)
    shown = []
    controller.page = SimpleNamespace(show_dialog=shown.append)
    controller._update_coordinator = SimpleNamespace(confirm_install=lambda: None)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_ANDROID", False)

    controller._show_update_confirmation()

    dialog = shown[0]
    assert dialog.actions[1].content == "Mở trang tải IPA"
    assert "Sideloadly/AltStore" in dialog.content.value
    assert "cùng Apple ID" in dialog.content.value


def _update_event_controller():
    controller = AppController.__new__(AppController)
    controller.settings_view = SimpleNamespace(_check_update_btn=None)
    controller._manual_update_check_requested = False
    controller._update_candidate = SimpleNamespace(
        manifest=SimpleNamespace(release_version="2.3.0")
    )
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(disabled=False, content="")
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)
    controller.page = _FakePage()
    return controller


def test_update_download_replaces_stale_available_copy_with_progress_state():
    controller = _update_event_controller()
    candidate = controller._update_candidate

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.DOWNLOADING, candidate, progress=0.0)
        )
    )

    assert controller._update_text.value == "Đang tải và xác minh v2.3.0..."
    assert controller._update_btn.content == "Đang tải và xác minh..."
    assert controller._update_btn.disabled is True
    assert controller._update_progress.visible is True


def test_update_failure_explains_retry_in_banner_instead_of_stale_ready_copy():
    controller = _update_event_controller()

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.FAILED, controller._update_candidate)
        )
    )

    assert controller._update_text.value == (
        "Không thể tải hoặc xác minh v2.3.0. Hãy thử lại."
    )
    assert controller._update_btn.content == "Thử lại"
    assert controller._update_btn.disabled is False
