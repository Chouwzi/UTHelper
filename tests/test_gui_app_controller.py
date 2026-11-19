import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
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
)
from platform_utils.android_updates import (
    AndroidPackageLauncher as _AndroidPackageLauncher,
    android_release_build_number as _android_release_build_number,
)
from gui.components.activity_card import ActivityCard
from gui.components.today_schedule_panel import TodaySchedulePanel


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


def test_desktop_filter_press_atomically_replaces_active_popup():
    controller = AppController.__new__(AppController)
    updates = []
    controller.page = SimpleNamespace(width=420, update=lambda: updates.append(True))
    controller._window_header = ft.Container(height=34)
    controller._desktop_filter_popup_host = ft.Container(
        visible=True,
        opacity=0,
        scale=0.96,
        offset=ft.Offset(0, -0.025),
        ignore_interactions=True,
    )
    controller._desktop_filter_top_dismiss_layer = ft.Container(visible=False)
    controller._desktop_filter_upper_body_dismiss_layer = ft.Container(
        visible=False
    )
    controller._desktop_filter_dismiss_layer = ft.Container(visible=False)
    controller._active_desktop_filter_menu = None
    first_states = []
    second_states = []
    first = SimpleNamespace(
        trigger=ft.Container(height=40),
        popup_width=224,
        popup_panel=ft.Container(data="first"),
        _set_expanded=first_states.append,
    )
    second = SimpleNamespace(
        trigger=ft.Container(height=40),
        popup_width=232,
        popup_panel=ft.Container(data="second"),
        _set_expanded=second_states.append,
    )
    first_event = SimpleNamespace(
        global_position=ft.Offset(70, 165),
        local_position=ft.Offset(20, 20),
    )
    second_event = SimpleNamespace(
        global_position=ft.Offset(195, 165),
        local_position=ft.Offset(50, 20),
    )

    controller._toggle_desktop_filter_menu(first, first_event)
    assert controller._desktop_filter_popup_host.opacity == 0
    asyncio.run(controller._animate_desktop_filter_open(first))
    assert controller._desktop_filter_popup_host.opacity == 1
    controller._toggle_desktop_filter_menu(second, second_event)

    assert first_states == [True, False]
    assert second_states == [True]
    assert controller._active_desktop_filter_menu is second
    assert controller._desktop_filter_popup_host.content is second.popup_panel
    assert controller._desktop_filter_popup_host.visible is True
    assert controller._desktop_filter_popup_host.opacity == 1
    assert controller._desktop_filter_popup_host.ignore_interactions is False
    assert controller._desktop_filter_top_dismiss_layer.visible is True
    assert controller._desktop_filter_upper_body_dismiss_layer.visible is True
    assert controller._desktop_filter_dismiss_layer.visible is True
    assert len(updates) == 3


def test_pressing_active_desktop_filter_closes_it_without_reopening():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(width=420, update=lambda: None)
    controller._window_header = ft.Container(height=34)
    controller._desktop_filter_popup_host = ft.Container(
        visible=True,
        opacity=0,
        scale=0.96,
        offset=ft.Offset(0, -0.025),
        ignore_interactions=True,
    )
    controller._desktop_filter_top_dismiss_layer = ft.Container(visible=False)
    controller._desktop_filter_upper_body_dismiss_layer = ft.Container(
        visible=False
    )
    controller._desktop_filter_dismiss_layer = ft.Container(visible=False)
    states = []
    menu = SimpleNamespace(
        trigger=ft.Container(height=40),
        popup_width=224,
        popup_panel=ft.Container(),
        _set_expanded=states.append,
    )
    event = SimpleNamespace(
        global_position=ft.Offset(70, 165),
        local_position=ft.Offset(20, 20),
    )
    controller._active_desktop_filter_menu = None

    controller._toggle_desktop_filter_menu(menu, event)
    controller._toggle_desktop_filter_menu(menu, event)

    assert states == [True, False]
    assert controller._active_desktop_filter_menu is None
    assert controller._desktop_filter_popup_host.opacity == 0
    assert controller._desktop_filter_popup_host.ignore_interactions is True
    assert controller._desktop_filter_top_dismiss_layer.visible is False
    assert controller._desktop_filter_upper_body_dismiss_layer.visible is False
    assert controller._desktop_filter_dismiss_layer.visible is False


def test_dashboard_header_action_closes_filter_and_runs_on_same_click():
    controller = AppController.__new__(AppController)
    calls = []
    handler = object()
    controller._dismiss_desktop_filter_menu = lambda: calls.append("dismiss")
    controller._safe_run_task = lambda received: calls.append(received)

    controller._run_dashboard_header_action(handler)

    assert calls == ["dismiss", handler]


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


def test_countdown_tick_skips_schedule_panel_and_updates_real_activity_card():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    panel = TodaySchedulePanel(on_need_data=lambda: None)
    card = ActivityCard(
        {
            "title": "Offline countdown",
            "course": "Audit",
            "type": "quiz",
            "deadline": (datetime.now() + timedelta(minutes=5)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "submission_status": "unknown",
        },
        on_tap=lambda _event: None,
    )
    card.data["deadline"] = (datetime.now() - timedelta(minutes=1)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    controller._countdown_cards_once([panel, card])

    assert card._countdown_ctrl.value.startswith("Quá hạn")
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

    from unittest.mock import AsyncMock, Mock

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


def test_shared_back_navigation_closes_schedule_surface_before_child_views():
    from unittest.mock import AsyncMock, Mock

    async def scenario():
        controller = AppController.__new__(AppController)
        controller._today_schedule_component = SimpleNamespace(
            handle_back=Mock(return_value=True),
        )
        controller.settings_view = SimpleNamespace(
            visible=True,
            _handle_back=AsyncMock(),
        )
        controller.detail_view = SimpleNamespace(visible=False)
        controller.calendar_view = SimpleNamespace(visible=False)
        controller.grade_overview_view = SimpleNamespace(visible=False)

        assert await controller._navigate_back() is True
        controller._today_schedule_component.handle_back.assert_called_once_with()
        controller.settings_view._handle_back.assert_not_awaited()

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


def test_android_launcher_forwards_verified_local_path_identity_version_and_signer():
    calls = []

    class Bridge:
        available = True

        async def install_update(self, *args):
            calls.append(args)
            return {"status": "installer_opened"}

        async def cancel_update(self):
            pass

    def run_task(handler, *args):
        from concurrent.futures import Future
        result = Future()
        result.set_result(asyncio.run(handler(*args)))
        return result

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
            str(Path("UTHelper.apk")),
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
    assert shown[0].modal is False  # Escape remains a safe dismissal action.


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

    assert controller._update_text.value == "Đang tải v2.3.0 · 0%"
    assert controller._update_btn.content == "Đang tải"
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


def test_manual_discovery_failure_is_not_presented_as_download_failure():
    controller = _update_event_controller()
    controller._manual_update_check_requested = True
    controller._update_candidate = None
    messages = []
    controller._show_snackbar = lambda message, *_args: messages.append(message)

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(
                UpdateEventKind.FAILED,
                message="update service is temporarily unavailable",
            )
        )
    )

    assert messages == ["Không thể kiểm tra cập nhật. Hãy kiểm tra mạng và thử lại."]
    assert controller._update_text.value == ""
    assert controller._update_banner.visible is False


def test_android_install_banner_waits_for_native_installer_confirmation(monkeypatch):
    controller = _update_event_controller()
    controller._update_banner.visible = True
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_WINDOWS", False)

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.INSTALLING, controller._update_candidate)
        )
    )

    assert controller._update_banner.visible is True
    assert controller._update_text.value == "Đang mở trình cài đặt…"
    assert controller._update_btn.content == "Đang mở"
    assert controller._update_btn.disabled is True

    asyncio.run(controller._apply_update_event(
        UpdateEvent(UpdateEventKind.INSTALL_LAUNCHED, controller._update_candidate)
    ))
    assert controller._update_text.value == "Đã mở trình cài đặt"
    assert controller._update_btn.disabled is False


def test_show_snackbar_uses_page_overlay_when_available():
    controller = AppController.__new__(AppController)
    overlay_controls = []
    dialogs = []
    page = SimpleNamespace(
        overlay=overlay_controls,
        show_dialog=dialogs.append,
        update=lambda: None,
    )
    controller.page = page

    controller._show_snackbar("Đã cập nhật dữ liệu")

    assert len(overlay_controls) == 1
    assert isinstance(overlay_controls[0], ft.SnackBar)
    assert overlay_controls[0].open is True
    assert dialogs == []


def test_download_ready_keeps_app_usable_until_explicit_install_click():
    controller = _update_event_controller()
    dialogs = []
    controller._show_update_confirmation = lambda: dialogs.append("confirm")
    asyncio.run(controller._apply_update_event(
        UpdateEvent(UpdateEventKind.READY_TO_INSTALL, controller._update_candidate)
    ))
    assert dialogs == []
    assert controller._update_btn.content == "Cài đặt"
    controller._request_update_download()
    assert dialogs == ["confirm"]


def test_permission_retry_confirms_cached_package_without_downloading():
    controller = _update_event_controller()
    dialogs = []
    controller._show_update_confirmation = lambda: dialogs.append("confirm")
    # No downloader is installed: this action must go directly to confirmation.
    asyncio.run(controller._apply_update_event(
        UpdateEvent(UpdateEventKind.INSTALL_PERMISSION_REQUIRED, controller._update_candidate)
    ))
    assert "Cho phép cài" in controller._update_text.value
    assert controller._update_btn.disabled is False
    controller._request_update_download()
    assert dialogs == ["confirm"]


def test_install_failure_is_distinct_from_download_failure():
    controller = _update_event_controller()
    asyncio.run(controller._apply_update_event(UpdateEvent(
        UpdateEventKind.INSTALL_FAILED, controller._update_candidate,
        message="installed_signer_mismatch",
    )))
    assert controller._update_text.value == "Chữ ký bản cập nhật không khớp ứng dụng đang cài."
    assert controller._update_btn.disabled is False
    controller._update_candidate.manifest.release_notes_url = "https://github.com/Chouwzi/UTHelper/releases/tag/v2.3.0"
    opened = []
    controller._open_external_update_url = opened.append
    assert controller._update_btn.content == "Bản phát hành"
    controller._request_update_download()
    assert opened == [controller._update_candidate.manifest.release_notes_url]


def test_download_state_remains_available_in_settings():
    controller = _update_event_controller()
    statuses = []
    controller.settings_view.set_update_status = lambda *args, **kwargs: statuses.append((args, kwargs))
    asyncio.run(controller._apply_update_event(UpdateEvent(
        UpdateEventKind.DOWNLOAD_PROGRESS, controller._update_candidate, progress=0.42,
    )))
    assert statuses[0][0] == ("Đang tải v2.3.0 · 42%",)
    assert statuses[0][1]["progress"] == 0.42
    assert statuses[0][1]["action_label"] is None


def test_store_channel_suppresses_update_coordinator_and_github_client(monkeypatch):
    import pytest
    from core.distribution import DistributionChannel, build_distribution_profile

    for channel in (DistributionChannel.CHPLAY, DistributionChannel.MSSTORE):
        monkeypatch.setattr(
            app_controller_module,
            "resolve_distribution_channel",
            lambda c=channel: build_distribution_profile(c),
        )
        monkeypatch.setattr(
            app_controller_module,
            "GitHubReleaseClient",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("GitHubReleaseClient must not be instantiated on Store channels")
            ),
        )
        monkeypatch.setattr(
            app_controller_module,
            "UpdateCoordinator",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("UpdateCoordinator must not be instantiated on Store channels")
            ),
        )
        monkeypatch.setattr(
            app_controller_module,
            "detect_runtime_target",
            lambda *_a, **_kw: (_ for _ in ()).throw(
                AssertionError("detect_runtime_target should not be called when self-update is disabled")
            ),
        )

        controller = _update_event_controller()
        controller._distribution_profile = build_distribution_profile(channel)
        controller._update_coordinator = controller._create_update_coordinator()
        assert controller._update_coordinator is None

        controller._check_updates_now()
        controller._cancel_update_download()
        controller._request_update_download()
        controller._show_update_confirmation()
        controller._on_update_event(
            UpdateEvent(UpdateEventKind.UPDATE_AVAILABLE, controller._update_candidate)
        )
        asyncio.run(
            controller._apply_update_event(
                UpdateEvent(UpdateEventKind.UPDATE_AVAILABLE, controller._update_candidate)
            )
        )
        assert controller._update_banner.visible is False

        controller.activation_broker = None
        controller.settings_view = SimpleNamespace(cancel_pending_load=lambda: None)
        controller._prefetch_cancel_event = SimpleNamespace(set=lambda: None)
        controller._page_alive = SimpleNamespace(clear=lambda: None)
        controller.orchestrator = SimpleNamespace(client=SimpleNamespace(close=lambda: None))
        controller._on_disconnect(None)


def test_lts_and_source_channels_keep_update_coordinator_active(monkeypatch):
    from core.distribution import DistributionChannel, build_distribution_profile
    from core.update_models import RuntimeTarget

    for channel in (
        DistributionChannel.APK_LTS,
        DistributionChannel.EXE_LTS,
        DistributionChannel.SOURCE,
    ):
        controller = AppController.__new__(AppController)
        controller._distribution_profile = build_distribution_profile(channel)
        controller._android_background = None
        controller._update_candidate = None
        monkeypatch.setattr(
            app_controller_module,
            "detect_runtime_target",
            lambda: RuntimeTarget("other", "unknown", "source"),
        )
        coordinator = controller._create_update_coordinator()
        assert coordinator is not None


def test_open_store_url_uses_external_launch_and_https_fallback(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeUrlLauncher:
        async def launch_url(self, url, *, mode=None, **_kwargs):
            calls.append((url, mode))
            if url.startswith("market://"):
                raise RuntimeError("no market handler")

    monkeypatch.setattr(app_controller_module.ft, "UrlLauncher", FakeUrlLauncher)

    controller = AppController.__new__(AppController)

    def run_task(handler, *args):
        asyncio.run(handler(*args))
        return object()

    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=run_task,
    )

    ok = controller._open_store_url(
        "market://details?id=com.uthelper.uthelper",
        "https://play.google.com/store/apps/details?id=com.uthelper.uthelper",
    )
    assert ok is True
    assert calls == [
        ("market://details?id=com.uthelper.uthelper", ft.LaunchMode.EXTERNAL_APPLICATION),
        (
            "https://play.google.com/store/apps/details?id=com.uthelper.uthelper",
            ft.LaunchMode.EXTERNAL_APPLICATION,
        ),
    ]

    assert controller._open_store_url("javascript:alert(1)") is False


def test_run_page_task_propagates_exceptions_and_handles_missing_session():
    controller = AppController.__new__(AppController)
    reported = []
    controller._unhandled_error_reporter = reported.append

    controller.page = SimpleNamespace(session=None)
    assert controller._run_page_task(lambda: None) is None

    def run_task(handler):
        from concurrent.futures import Future

        fut = Future()
        try:
            fut.set_result(asyncio.run(handler()))
        except BaseException as exc:
            fut.set_exception(exc)
        return fut

    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=run_task,
    )

    async def boom():
        raise TimeoutError("bridge timeout")

    future = controller._run_page_task(boom)
    assert future is not None
    import pytest

    with pytest.raises(TimeoutError, match="bridge timeout"):
        future.result(timeout=1.0)
    assert reported == []

    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=lambda _handler: None,
    )
    assert controller._run_page_task(boom) is None


def test_create_update_coordinator_android_uses_run_page_task_verifier_and_safe_version(
    monkeypatch,
):
    from core.distribution import DistributionChannel, build_distribution_profile
    from core.update_models import RuntimeTarget
    from platform_utils.android_updates import AndroidPackageVerifier

    controller = AppController.__new__(AppController)
    controller._distribution_profile = build_distribution_profile(
        DistributionChannel.APK_LTS
    )
    controller._android_background = SimpleNamespace(available=True)
    controller._update_candidate = None
    controller.page = SimpleNamespace(session=None)

    monkeypatch.setattr(
        app_controller_module,
        "detect_runtime_target",
        lambda: RuntimeTarget("android", "arm64", "sideload"),
    )

    coordinator = controller._create_update_coordinator()
    assert coordinator is not None
    assert isinstance(coordinator._verifier, AndroidPackageVerifier)
    assert coordinator._launcher._native.task_runner == controller._run_page_task
    assert coordinator._downloader._native.task_runner == controller._run_page_task
    # Must return None safely without AttributeError when candidate is None.
    assert controller._current_update_release_version() is None
    pkg = ReleasePackage(
        "android",
        "arm64",
        "apk",
        "sideload",
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper.apk",
        "a" * 64,
        10,
        "com.uthelper.uthelper",
        "b" * 64,
        {"kind": "android_package_installer"},
    )
    launch_res = coordinator._launcher.launch(Path("UTHelper.apk"), pkg)
    assert launch_res.acknowledged is False
    assert launch_res.status == "bridge_failed"


def test_install_failed_source_unavailable_resets_ready_to_redownload():
    controller = _update_event_controller()
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    requested = []
    controller._update_coordinator = SimpleNamespace(
        request_download=lambda candidate: requested.append(candidate)
    )
    controller._show_update_confirmation = lambda: requested.append("confirm")

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(
                UpdateEventKind.INSTALL_FAILED,
                controller._update_candidate,
                message="source_unavailable",
            )
        )
    )

    assert controller._update_ready is False
    controller._request_update_download()
    assert requested == [controller._update_candidate]


def test_update_install_error_message_maps_windows_tokens_and_reasons():
    from gui.app_controller import _update_install_error_message

    generic = "Không mở được trình cài đặt. Hãy thử lại."
    for key in (
        "uac_cancelled",
        "installer cancelled by user",
        "installer_busy",
        "another Windows installation is in progress",
        "installer_unavailable",
        "Windows installer is unavailable",
        "strategy_not_allowed",
        "installer strategy is not allowed",
        "installer_already_running",
        "an installer is already running",
        "launch_failed",
        "installer launch failed",
        "installer_rejected",
        "installer rejected",
        "file_in_use",
        "install_timeout",
        "relaunch_failed",
    ):
        assert _update_install_error_message(key) != generic


def test_windows_install_failed_strategy_and_timeout_ui_states():
    controller = _update_event_controller()

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(
                UpdateEventKind.INSTALL_FAILED,
                controller._update_candidate,
                message="installer strategy is not allowed",
            )
        )
    )
    assert controller._update_open_release_page is True
    assert controller._update_btn.content == "Bản phát hành"

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(
                UpdateEventKind.FAILED,
                controller._update_candidate,
                message="Windows package verification timed out",
            )
        )
    )
    assert "quá thời gian" in controller._update_text.value


def test_windows_install_launched_runs_idempotent_shutdown_before_window_destroy(
    monkeypatch,
):
    from unittest.mock import AsyncMock

    order = []
    controller = _update_event_controller()
    controller._exit_cleanup_lock = __import__("threading").Lock()
    controller._exit_cleanup_done = False
    controller.activation_broker = SimpleNamespace(
        close=lambda timeout_seconds=1.0: order.append(("broker", timeout_seconds))
    )
    controller.tray = SimpleNamespace(
        close=lambda timeout_seconds=1.0: order.append(("tray", timeout_seconds))
    )
    controller._sync_coordinator = SimpleNamespace(close=lambda: order.append("sync"))
    controller._today_schedule_coordinator = SimpleNamespace(
        close=lambda: order.append("schedule")
    )
    controller._update_coordinator = SimpleNamespace(
        shutdown=lambda timeout_seconds=1.0: order.append(("update", timeout_seconds))
    )
    controller.orchestrator = SimpleNamespace(
        client=SimpleNamespace(close=lambda: order.append("client"))
    )
    controller.settings_view = SimpleNamespace(
        _check_update_btn=None,
        cancel_pending_load=lambda: order.append("settings"),
    )
    controller._page_alive = SimpleNamespace(clear=lambda: order.append("page_alive"))
    controller._prefetch_cancel_event = SimpleNamespace(
        set=lambda: order.append("prefetch")
    )

    async def destroy():
        order.append("destroy")

    controller.page = SimpleNamespace(
        update=lambda: None,
        window=SimpleNamespace(destroy=AsyncMock(side_effect=destroy)),
    )
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_ANDROID", False)

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.INSTALL_LAUNCHED, controller._update_candidate)
        )
    )

    assert "destroy" in order
    destroy_idx = order.index("destroy")
    assert order.index(("broker", 1.0)) < destroy_idx
    assert order.index(("tray", 1.0)) < destroy_idx
    assert order.index(("update", 1.0)) < destroy_idx
    assert order.index("client") < destroy_idx

    before_disconnect = list(order)
    controller._on_disconnect(None)
    assert order == before_disconnect


def test_checking_event_forwards_status_to_settings_view():
    controller = _update_event_controller()
    statuses = []
    controller.settings_view.set_update_status = lambda msg, **_kw: statuses.append(msg)

    asyncio.run(controller._apply_update_event(UpdateEvent(UpdateEventKind.CHECKING)))

    assert statuses == ["Đang kiểm tra cập nhật..."]

