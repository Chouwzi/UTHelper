"""Integration tests for TelemetryBridge, AppController, and presentation seams.

Verifies:
- Hidden Windows tray autostart + background sync/notification polls emit 0 events
  and create no ``analytics/installation.json`` until human tray restore.
- Unauthenticated pre-login dialog and ``uthelper_demo`` emit 0 events.
- Foreground authenticated non-demo usage emits all 12 allow-listed events with
  strict schema compliance and zero PII leakage.
- ``< 30m`` background resume vs ``>= 30m`` background expiry stamped strictly at
  ``last_foreground_active_utc`` (no midnight DAU spillover).
- Logout and account switch emit ``uth_session_ended(account_change)`` and rotate
  ``distinct_id``.
- Live opt-out via ``apply_analytics_consent("disabled")`` immediately purges RAM
  queue, ``analytics/pending/*`` spool batches, and ``analytics/installation.json``.
- ``main.py`` single-instance handoff performs zero analytics I/O and shutdown
  flushes ``analytics_runtime.close()`` before process termination.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.main as application
from analytics.models import (
    AnalyticsConfig,
    AnalyticsConsent,
    DeviceClass,
    DistributionChannelName,
    EventName,
    OsFamily,
    PackageFormatName,
    ReleaseChannel,
)
from analytics.runtime import AnalyticsRuntime, create_default_analytics_runtime
from config import settings
from core.courses.models import CourseAttachment, CourseModuleItem, CourseSummary
from core.submission_models import MutationOperation, SubmissionSnapshot
from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    TodayScheduleSnapshot,
    TodayScheduleViewState,
)
from core.use_cases.submission_workflow import SubmissionMutationResult
from gui.app_controller import AppController
from gui.components.calendar_view import CalendarView
from gui.components.courses.activity_views import QuizActivityView
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.detail_view import DetailView
from gui.components.grade_overview_view import GradeOverviewView
from gui.components.quiz_player_view import QuizPlayerView
from gui.controllers.courses_coordinator import CoursesCoordinator
from gui.controllers.telemetry_bridge import TelemetryBridge
from gui.view_manager import ViewManager

_VALID_KEY = "phc_1234567890abcdefghij1234567890"
_VALID_HOST = "https://us.i.posthog.com"


class _DeterministicClock:
    def __init__(self, start_utc: datetime | None = None) -> None:
        self.now_utc = start_utc or datetime(2026, 11, 28, 9, 0, 0, tzinfo=UTC)
        self.mono = 1000.0

    def __call__(self) -> datetime:
        return self.now_utc

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.mono += float(seconds)
        self.now_utc += timedelta(seconds=float(seconds))


def _make_runtime(
    data_dir: Path,
    clock: _DeterministicClock,
    *,
    consent: str = "enabled",
    web_mode: bool = False,
    test_mode: bool = False,
) -> AnalyticsRuntime:
    cfg = AnalyticsConfig(
        api_key=_VALID_KEY,
        host=_VALID_HOST,
        app_version="2.7.0",
        release_channel=ReleaseChannel.RELEASE,
        distribution_channel=DistributionChannelName.EXE_LTS,
        package_format=PackageFormatName.EXE,
        os_family=OsFamily.WINDOWS,
        device_class=DeviceClass.DESKTOP,
        web_mode=web_mode,
        test_mode=test_mode,
    )
    return AnalyticsRuntime(
        data_dir=data_dir,
        config=cfg,
        initial_consent=consent,
        clock=clock,
        monotonic=clock.monotonic,
    )


def _drain_spooled_events(runtime: AnalyticsRuntime) -> list[dict[str, Any]]:
    runtime.poll_timers()
    runtime.flush_ram_to_spool(fsync=False)
    events: list[dict[str, Any]] = []
    for queued in runtime.spool.pending():
        for ev in queued.batch.events:
            events.append(ev.model_dump(mode="json"))
    return events


class _FakeWindow:
    def __init__(self, *, visible: bool = True, minimized: bool = False) -> None:
        self.visible = visible
        self.minimized = minimized
        self.maximized = False
        self.width = 1200
        self.height = 800


class _FakePage:
    def __init__(self, *, visible: bool = True, minimized: bool = False) -> None:
        self.window = _FakeWindow(visible=visible, minimized=minimized)
        self.width = 1200
        self.height = 800
        self.web = False
        self.platform = "windows"
        self.overlay: list[Any] = []
        self.controls: list[Any] = []
        self.services: list[Any] = []
        self.on_app_lifecycle_state_change = None

    def update(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def show_dialog(self, dlg: Any) -> None:
        dlg.open = True

    def pop_dialog(self) -> None:
        return None

    def run_task(self, fn: Any, *args: Any, **kwargs: Any) -> None:
        res = fn(*args, **kwargs)
        if asyncio.iscoroutine(res):
            res.close()


def _build_lightweight_controller(
    runtime: AnalyticsRuntime,
    clock: _DeterministicClock,
    *,
    visible: bool = True,
    minimized: bool = False,
    force_visible: bool = False,
) -> AppController:
    ctrl = AppController.__new__(AppController)
    ctrl.page = _FakePage(visible=visible, minimized=minimized)
    ctrl.force_visible = force_visible
    ctrl._analytics_runtime = runtime
    ctrl._telemetry = TelemetryBridge(runtime, monotonic=clock.monotonic)
    ctrl._telemetry.install_page_lifecycle_hook(
        ctrl.page, ctrl._telemetry_current_screen
    )
    ctrl._account_epoch = 0
    ctrl._sync_generation = 0
    ctrl._active_sync_epoch = None
    ctrl._active_sync_generation = None
    ctrl._grades_request_epoch = 0
    ctrl._detail_request_epoch = 0
    ctrl._is_loading = False
    ctrl._skeleton_visible = False
    ctrl._manual_sync_requested = False
    ctrl._manual_today_schedule_requested = False
    ctrl._data_lock = __import__("threading").Lock()
    ctrl._cards_lock = __import__("threading").Lock()
    ctrl.all_data = []
    ctrl.active_cards = []
    ctrl._pending_updates = {}
    ctrl.active_urgency = "all"
    ctrl.active_type = "all"
    ctrl.active_course = "all"
    ctrl.active_search = ""
    ctrl._settings_identity = (settings.UTH_USERNAME, settings.UTH_PASSWORD)
    ctrl._notification_milestones_snapshot = ()
    ctrl._notification_class_schedule_snapshot = (True, (1,), False, 22, 7)
    ctrl._android_background = None
    ctrl.tray = SimpleNamespace(is_ready=True, notify=lambda *_a, **_kw: None)
    ctrl._tray_balloon_shown = True
    ctrl.refresh_btn = SimpleNamespace(disabled=False)
    ctrl.empty_state = SimpleNamespace(visible=False)
    ctrl.error_state = SimpleNamespace(visible=False)
    ctrl.error_text = SimpleNamespace(value="")
    ctrl._error_title = SimpleNamespace(value="")
    ctrl._sync_status = None
    ctrl.status_text = SimpleNamespace(value="")
    ctrl._nav_shell = SimpleNamespace(
        rail_visible=False,
        set_active=lambda _k: None,
        set_immersive=lambda _v: None,
        bar=None,
    )
    ctrl.urgency_filter_menu = SimpleNamespace(
        set_selected=lambda _k: None, set_options=lambda _o: None
    )
    ctrl.type_filter_menu = SimpleNamespace(
        set_selected=lambda _k: None, set_options=lambda _o: None
    )
    ctrl.course_filter_menu = SimpleNamespace(
        set_selected=lambda _k: None, set_options=lambda _o: None
    )
    ctrl.search_field = SimpleNamespace(value="")
    ctrl._reset_filters_btn = SimpleNamespace(visible=False)
    ctrl._search_clear_btn = SimpleNamespace(visible=False)
    ctrl.dashboard = SimpleNamespace(
        visible=True, opacity=1.0, offset=None
    )
    ctrl.settings_view = SimpleNamespace(
        visible=False,
        opacity=0.0,
        offset=None,
        has_changes=lambda: False,
        load_current_settings=lambda: asyncio.sleep(0),
        cancel_pending_load=lambda: None,
    )
    ctrl.calendar_view = SimpleNamespace(
        visible=False,
        opacity=0.0,
        offset=None,
        show=lambda _d=None: None,
        hide=lambda: None,
        reset_schedule=lambda: None,
    )
    ctrl.grade_overview_view = SimpleNamespace(
        visible=False,
        opacity=0.0,
        offset=None,
        show_loading=lambda: None,
        show_error=lambda _m="": None,
        update_theme=lambda: None,
        update_academic_data=lambda *_a, **_kw: None,
        set_credit_progress=lambda _p: None,
        hide=lambda: None,
        clear=lambda: None,
    )
    ctrl.courses_view = SimpleNamespace(
        visible=False,
        opacity=0.0,
        offset=None,
        on_tab_shown=lambda: None,
        on_tab_hidden=lambda: None,
    )
    ctrl.course_grades_view = SimpleNamespace(visible=False, clear=lambda: None)
    ctrl.detail_view = SimpleNamespace(
        visible=False,
        opacity=0.0,
        offset=None,
        show_loading=lambda _d: None,
        update_detail=lambda _d: None,
        show_error_banner=lambda: None,
    )
    ctrl.quiz_player_view = SimpleNamespace(visible=False)
    ctrl.tab_switcher = SimpleNamespace(content=ctrl.dashboard)
    ctrl._empty_detail = SimpleNamespace()
    ctrl.detail_switcher = SimpleNamespace(content=ctrl._empty_detail)
    ctrl._detail_host = SimpleNamespace(ignore_interactions=True)
    ctrl.view_manager = ViewManager(
        ctrl.page,
        ctrl.dashboard,
        ctrl.detail_view,
        ctrl.settings_view,
        ctrl.calendar_view,
        ctrl.grade_overview_view,
        ctrl,
        courses_view=ctrl.courses_view,
        course_grades_view=ctrl.course_grades_view,
        tab_switcher=ctrl.tab_switcher,
        detail_switcher=ctrl.detail_switcher,
        empty_detail=ctrl._empty_detail,
        on_destination_change=ctrl._nav_shell.set_active,
        on_screen_telemetry=ctrl._on_screen_telemetry,
    )
    ctrl._refresh_ui = lambda **_kw: None
    ctrl._render_cards_only = lambda: None
    ctrl._update_footer = lambda **_kw: None
    ctrl._show_snackbar = lambda *_a, **_kw: None
    ctrl._safe_run_task = lambda *_a, **_kw: None
    ctrl._clear_uth_maintenance_banner = lambda **_kw: None
    ctrl._schedule_card_detail_warmup = lambda: False
    ctrl._today_schedule_component = None
    ctrl._today_schedule_coordinator = None
    ctrl._week_schedule_cache = None
    ctrl._courses_coordinator = None
    ctrl.orchestrator = SimpleNamespace(
        is_logged_in=True,
        client=SimpleNamespace(clear_session=lambda: None),
        thnn_client=SimpleNamespace(clear_session=lambda: None),
        moodle_service=SimpleNamespace(clear_all_caches=lambda: None),
        clear_detail_cache=lambda: None,
        get_cached_details_snapshot=lambda: {},
        prefetch_activity_statuses=None,
        get_latest_activities=lambda: [
            {
                "id": "assign_101",
                "title": "Báo cáo Đồ án Bí mật MSSV 2251120001",
                "course": "Lập trình Mạng - K22",
                "course_id": 42,
                "cmid": 101,
                "type": "assignment",
                "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
                "deadline": "2026-12-05 23:59:00",
                "urgency": "warning",
                "submission_status": "new",
            }
        ],
    )
    ctrl._portal_client = SimpleNamespace(clear_token=lambda: None)
    ctrl._portal_week_client = SimpleNamespace(clear_token=lambda: None)
    ctrl._data_cache = SimpleNamespace(
        load=lambda: ([], None),
        save_server_snapshot=lambda _items: True,
        load_snapshot=lambda: SimpleNamespace(
            last_successful_sync_at="2026-11-28T09:00:00Z", activities=[]
        ),
        record_failed_attempt=lambda _err: None,
    )
    ctrl.notifier = SimpleNamespace(
        notifiers=[SimpleNamespace(__class__=type("WindowsNotifier", (), {}))],
        dispatch=lambda _items: asyncio.sleep(
            0,
            result=SimpleNamespace(delivered=2, filtered=0, failed_channels=()),
        ),
        reconcile_schedules=lambda _items: asyncio.sleep(
            0,
            result=SimpleNamespace(desired=3, scheduled=3, cancelled=0, failed=0),
        ),
        reconcile_class_schedule_notifications=lambda *_a, **_kw: asyncio.sleep(0),
        clear_account_state=lambda: None,
        set_namespace=lambda _ns: None,
    )
    return ctrl


@pytest.fixture(autouse=True)
def _restore_user_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret-pass")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "ws-token")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "ANALYTICS_CONSENT", "enabled")
    monkeypatch.setattr(settings, "MINIMIZE_TO_TRAY", True)
    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "ENABLE_DISCORD", True)
    monkeypatch.setattr(settings, "ENABLE_TELEGRAM", False)
    monkeypatch.setattr(settings, "ENABLE_GMAIL", False)
    yield


# ── (a) Hidden Windows Tray Autostart + Background Sync Guard ──


def test_hidden_windows_tray_autostart_and_background_polls_emit_zero_until_tray_restore(
    tmp_path: Path,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock)
    ctrl = _build_lightweight_controller(runtime, clock, visible=False)

    ctrl._telemetry.sync_account_state(
        username=settings.UTH_USERNAME,
        has_credentials=ctrl._has_sync_credentials(),
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=True,
        force_visible=False,
        window_hidden_or_minimized=ctrl._is_window_hidden_or_minimized(),
        screen="home",
    )

    # Simulate background sync, schedule load, and notification dispatch while hidden.
    asyncio.run(ctrl._perform_data_sync("timer"))
    ctrl._on_today_schedule_state(
        TodayScheduleViewState(status=ScheduleLoadStatus.LOADING)
    )
    clock.advance(0.4)
    ctrl._on_today_schedule_state(
        TodayScheduleViewState(
            status=ScheduleLoadStatus.READY,
            snapshot=TodayScheduleSnapshot(
                schedule_date=clock.now_utc.date(),
                fetched_at=clock.now_utc,
                sessions=(),
            ),
        )
    )
    asyncio.run(ctrl._reconcile_class_schedule_notifications(()))
    clock.advance(600.0)

    install_file = tmp_path / "analytics" / "installation.json"
    assert not install_file.exists()
    assert runtime.active_session_id is None
    assert _drain_spooled_events(runtime) == []

    # Human double-clicks the tray icon -> window becomes visible.
    ctrl.page.window.visible = True
    ctrl._on_window_restored()

    assert install_file.is_file()
    assert runtime.active_session_id is not None
    events = _drain_spooled_events(runtime)
    assert [e["event"] for e in events] == [EventName.SESSION_STARTED.value]
    assert events[0]["properties"]["launch_trigger"] == "tray_restore"
    assert events[0]["properties"]["initial_screen"] == "home"


# ── (b) Unauthenticated Pre-Login & uthelper_demo Suppression ──


def test_unauthenticated_and_demo_sessions_emit_zero_events_and_mint_no_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock)
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    # 1. Cold visible launch onto pre-login dialog (unauthenticated).
    ctrl._telemetry.sync_account_state(
        username="",
        has_credentials=ctrl._has_sync_credentials(),
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )
    ctrl.view_manager.show_calendar([])
    clock.advance(3.0)
    assert not (tmp_path / "analytics" / "installation.json").exists()
    assert _drain_spooled_events(runtime) == []

    # 2. Switch into uthelper_demo sandbox account -> still 0 events, 0 identity files.
    monkeypatch.setattr(settings, "UTH_USERNAME", "uthelper_demo")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "demo")
    ctrl._telemetry.sync_account_state(
        username=settings.UTH_USERNAME,
        has_credentials=ctrl._has_sync_credentials(),
        account_changed=True,
    )
    asyncio.run(ctrl._perform_data_sync("manual"))
    asyncio.run(ctrl._set_urgency("critical"))
    clock.advance(5.0)
    assert not (tmp_path / "analytics" / "installation.json").exists()
    assert _drain_spooled_events(runtime) == []

    # 3. Real authenticated student completes login while window is visible.
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120009")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "real-pass")
    ctrl._telemetry.sync_account_state(
        username=settings.UTH_USERNAME,
        has_credentials=ctrl._has_sync_credentials(),
        account_changed=True,
    )
    events = _drain_spooled_events(runtime)
    assert (tmp_path / "analytics" / "installation.json").is_file()
    assert [e["event"] for e in events] == [EventName.SESSION_STARTED.value]
    assert events[0]["properties"]["launch_trigger"] == "login_completed"


# ── (c) Foreground Allow-Listed Events & Zero-PII Verification ──


def test_foreground_authenticated_flow_emits_all_12_allowlisted_events_without_pii(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock)
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    # Cold visible startup for authenticated student -> Event 1: uth_session_started
    ctrl._telemetry.sync_account_state(
        username=settings.UTH_USERNAME,
        has_credentials=ctrl._has_sync_credentials(),
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )

    # Event 4: uth_screen_viewed (1.5s debounce + rail/tab_bar/card_tap/back)
    ctrl._nav_shell.rail_visible = True
    asyncio.run(ctrl._perform_destination_navigation("calendar"))
    clock.advance(2.0)
    runtime.poll_timers()

    ctrl.view_manager.show_detail_loading({"title": "Private Title", "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=99"})
    clock.advance(2.0)
    runtime.poll_timers()

    asyncio.run(ctrl._close_detail())
    clock.advance(2.0)
    runtime.poll_timers()

    # Event 5: uth_filter_applied across home, calendar, and courses (2.0s debounce)
    asyncio.run(ctrl._set_urgency("critical"))
    clock.advance(2.5)
    runtime.poll_timers()

    cal = CalendarView.__new__(CalendarView)
    cal._view_mode = "month"
    cal._detail_filter_mode = "day"

    def _fake_toggle_mode(mode: str) -> None:
        cal._view_mode = mode

    cal._toggle_view_mode = _fake_toggle_mode
    cal._set_detail_filter_mode = lambda mode: setattr(cal, "_detail_filter_mode", mode)
    ctrl._telemetry.attach_calendar_view(cal)
    cal._toggle_view_mode("week")
    clock.advance(2.5)
    runtime.poll_timers()

    courses_tab = CoursesTabView.__new__(CoursesTabView)
    courses_tab._state = SimpleNamespace(
        semester_filter="all",
        search_query="",
        courses=[SimpleNamespace(id=1), SimpleNamespace(id=2)],
        offline_notice="",
    )
    courses_tab._list_pane = SimpleNamespace(_on_filter_change=None, _on_refresh=None)

    def _fake_courses_filter(sem: str, q: str) -> None:
        courses_tab._state.semester_filter = sem
        courses_tab._state.search_query = q

    async def _fake_refresh_catalog(_inc: bool = True) -> None:
        clock.advance(0.3)

    courses_tab._handle_filter_change = _fake_courses_filter
    courses_tab.refresh = lambda: None
    courses_tab.refresh_catalog_async = _fake_refresh_catalog
    ctrl._telemetry.attach_courses_view(courses_tab)
    courses_tab._handle_filter_change("HK1 2026-2027", "Mạng máy tính")
    clock.advance(2.5)
    runtime.poll_timers()

    # Event 6: uth_sync_completed across all 4 providers (activities, today_schedule, courses_catalog, academic_grades)
    # Also triggers Event 12 (activity_deadline notification summary)
    asyncio.run(ctrl._perform_data_sync("manual"))

    ctrl._on_today_schedule_state(
        TodayScheduleViewState(status=ScheduleLoadStatus.LOADING)
    )
    clock.advance(0.35)
    ctrl._on_today_schedule_state(
        TodayScheduleViewState(
            status=ScheduleLoadStatus.READY,
            snapshot=TodayScheduleSnapshot(
                schedule_date=clock.now_utc.date(),
                fetched_at=clock.now_utc,
                sessions=(
                    ClassSession(
                        session_id="sess_1",
                        subject="Lập trình Bí mật",
                        course_code="IT001",
                        start_at=clock.now_utc,
                        end_at=clock.now_utc + timedelta(hours=2),
                        room="B.204",
                        campus="CS1",
                        period_start=1,
                        period_end=3,
                        cancelled=False,
                        note="",
                        lecturer="GV Nguyễn Văn A",
                    ),
                ),
            ),
        )
    )
    # Event 12 (class_schedule notification summary)
    asyncio.run(
        ctrl._reconcile_class_schedule_notifications(
            ("session_stub_1", "session_stub_2")
        )
    )

    courses_tab.refresh()
    asyncio.run(courses_tab.refresh_catalog_async(True))

    ctrl._academic_grade_service = SimpleNamespace(
        load_academic_data=lambda **_kw: [
            SimpleNamespace(courses=[SimpleNamespace(), SimpleNamespace(), SimpleNamespace()])
        ],
        get_radar_profiles=lambda _s: [SimpleNamespace()],
        load_credit_progress=lambda **_kw: None,
        last_maintenance_notice=None,
    )
    asyncio.run(ctrl._show_grades(force_refresh=True))

    # Event 7 & 8: uth_document_opened & uth_course_activity_opened via CoursesCoordinator
    coord = CoursesCoordinator.__new__(CoursesCoordinator)
    coord._telemetry = ctrl._telemetry
    coord._page = ctrl.page
    coord._account_epoch = 0
    coord._attachment_load_seq = 0
    coord._account_namespace = "ns"
    coord._webview_auth_service = None
    coord._thnn_client = None
    coord._courses_client = None
    coord._on_launch_url = lambda _u: None
    coord._on_assignment_status_changed = None
    coord._submission_workflow_factory = None
    coord._on_start_quiz = None
    coord._assignment_detail_fetcher = None
    coord._on_open_assignment = lambda _m: None
    coord._activity_service = SimpleNamespace(
        fetch_quiz_activity=lambda *_a, **_kw: SimpleNamespace(
            title="Quiz Secret",
            web_url="https://thnn.ut.edu.vn/mod/quiz/view.php?id=77",
            time_open=0,
            time_close=0,
            time_limit_seconds=600,
            max_attempts=1,
            grade_method="Highest",
            intro="",
            prevent_reasons=(),
            attempts=(),
        )
    )
    coord.preview_server = SimpleNamespace()
    cached_pdf = tmp_path / "cached_secret_syllabus.pdf"
    cached_pdf.write_bytes(b"%PDF-1.4\n%test\n")
    coord.preview_cache = SimpleNamespace(
        cache_dir=tmp_path,
        get_cached_path=lambda *_a, **_kw: cached_pdf,
    )
    coord.catalog_service = SimpleNamespace(record_recent=lambda _e: [])
    coord.courses_view = SimpleNamespace(
        push_overlay=lambda *_a, **_kw: None,
        pop_overlay=lambda: None,
        toggle_pane3_expanded=lambda: None,
        _dismiss_overlay_instance=lambda _t: None,
        _compute_pane3_bounds=lambda: (0.0, 0.0, 700.0, 600.0),
        _state=SimpleNamespace(recent_items=[]),
    )
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.is_embedded_webview_supported",
        lambda _p: True,
    )
    monkeypatch.setattr(
        "gui.controllers.courses_coordinator.prepare_pdf_viewer",
        lambda *_a, **_kw: SimpleNamespace(url="http://127.0.0.1:9999/viewer"),
    )
    coord.open_attachment_bytes_in_overlay = lambda *_a, **_kw: None

    sample_course = CourseSummary(
        id=88,
        source="thnn",
        fullname="Anh văn chuyên ngành K22 - Bí mật",
        shortname="ENG101",
        display_name="Anh văn chuyên ngành K22",
    )
    sample_mod = CourseModuleItem(
        cmid=501,
        instance_id=50,
        modname="resource",
        name="Đề cương chi tiết 2251120001.pdf",
        section_id=1,
        url="https://thnn.ut.edu.vn/mod/resource/view.php?id=501",
        web_url="https://thnn.ut.edu.vn/mod/resource/view.php?id=501",
    )
    sample_att = CourseAttachment(
        file_name="Bai_Tap_Bi_Mat_2251120001.pdf",
        file_url="https://thnn.ut.edu.vn/webservice/pluginfile.php/99/Bai_Tap_Bi_Mat_2251120001.pdf",
        file_size=1024,
        mime_type="application/pdf",
        time_modified=1700000000,
        preview_kind="pdf",
    )
    asyncio.run(coord.open_attachment_async(sample_course, sample_mod, sample_att))

    assign_mod = CourseModuleItem(
        cmid=502,
        instance_id=51,
        modname="assign",
        name="Nộp đồ án",
        section_id=1,
        url="https://courses.ut.edu.vn/mod/assign/view.php?id=502",
        web_url="https://courses.ut.edu.vn/mod/assign/view.php?id=502",
    )
    coord._handle_open_assignment(sample_course, assign_mod)
    coord.open_in_app_webview(
        "courses",
        "Portal Sinh Viên",
        "https://portal.ut.edu.vn/dashboard",
    )

    # Event 9: uth_quiz_action (review_opened, player_started, player_closed, player_blocked_seb)
    quiz_stub = QuizActivityView.__new__(QuizActivityView)
    quiz_stub._open_attempt_review = lambda *_a, **_kw: "reviewed"
    ctrl._telemetry.attach_quiz_activity_view(quiz_stub, "thnn")
    quiz_stub._open_attempt_review(None, 123, None)

    ctrl._on_quiz_telemetry("player_started", "courses", 0.42)
    clock.advance(120.0)
    ctrl._on_quiz_telemetry("player_closed", "courses", 120.0)

    qp = QuizPlayerView(
        ctrl.page,
        on_telemetry_quiz_action=ctrl._on_quiz_telemetry,
    )
    qp._activity = {
        "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=555",
        "site_identity": "thnn",
    }
    qp._generation = 1
    qp.visible = True
    qp._lease = SimpleNamespace(release=lambda: None)
    asyncio.run(
        qp._finish_entry_unavailable(
            "requires_safe_exam_browser",
            generation=1,
            lease=qp._lease,
        )
    )

    # Event 10: uth_submission_action via DetailView mutation helpers
    dv = DetailView(
        ctrl.page,
        on_close=lambda: None,
        get_client=lambda: object(),
        on_telemetry_submission=ctrl._on_submission_telemetry,
    )
    dv._current_data = {
        "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=101",
        "course_id": 42,
    }
    dv._current_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=101"
    dv._selected_files = [
        SimpleNamespace(
            name="secret_hw.pdf",
            size=128,
            bytes=b"abc",
            filepath="/",
            mime_type="application/pdf",
            path="",
        )
    ]
    dv._submitted_files = [{"name": "secret_hw.pdf", "filepath": "/"}]
    dv._submission_statement.value = True
    dv._submission_fingerprint = "fp-1"
    dv._render_submission_policy = lambda: None
    dv._apply_mutation_result = lambda *_a, **_kw: None
    dv._do_mutate_files_sync = lambda *_a, **_kw: SubmissionMutationResult(
        ok=True,
        snapshot=SubmissionSnapshot(
            assignment_id=101,
            raw_status="draft",
            grading_status="notgraded",
            remote_files=(),
            online_text="",
            can_edit=True,
            can_submit=True,
            statement_required=False,
        ),
        issue=None,
    )
    dv._do_finalize_submission_sync = dv._do_mutate_files_sync
    asyncio.run(dv._execute_file_mutation(MutationOperation.ADD, False))
    asyncio.run(dv._execute_finalize_submission())

    # Event 11: uth_grades_interaction via GradeOverviewView
    grades_view = GradeOverviewView(
        page=ctrl.page,
        on_telemetry_interaction=ctrl._on_grades_telemetry,
    )
    grades_view._semesters = [
        SimpleNamespace(name="HK1", courses=[]),
        SimpleNamespace(name="HK2", courses=[]),
    ]
    grades_view._on_semester_changed(1)
    grades_view._show_planner_dialog(ctrl.page)

    # Event 2: uth_session_heartbeat after 10 minutes of active foreground engagement
    clock.advance(605.0)
    ctrl._telemetry.record_interaction(screen="grades")
    runtime.poll_timers()

    # Event 3: uth_session_ended on clean app exit
    runtime.close()

    spooled_files = runtime.spool.pending()
    assert spooled_files
    raw_spool_blob = "\n".join(
        item.path.read_text(encoding="utf-8") for item in spooled_files
    )
    # Zero PII in raw spooled JSON
    for forbidden in (
        "2251120001",
        "secret-pass",
        "ws-token",
        "Bai_Tap_Bi_Mat",
        "secret_hw.pdf",
        "Lập trình Mạng",
        "Lập trình Bí mật",
        "GV Nguyễn Văn A",
        "Anh văn chuyên ngành",
        "pluginfile.php",
        "view.php?id=",
    ):
        assert forbidden not in raw_spool_blob

    all_events: list[dict[str, Any]] = []
    for item in spooled_files:
        payload = json.loads(item.path.read_text(encoding="utf-8"))
        all_events.extend(payload["events"])

    emitted_names = {ev["event"] for ev in all_events}
    assert emitted_names == {member.value for member in EventName}

    doc_events = [
        ev for ev in all_events if ev["event"] == EventName.DOCUMENT_OPENED.value
    ]
    assert len(doc_events) == 1
    assert doc_events[0]["properties"]["doc_format"] == "pdf"
    assert doc_events[0]["properties"]["viewer_kind"] == "pdfjs_webview"
    assert doc_events[0]["properties"]["source_site"] == "thnn"
    assert doc_events[0]["properties"]["cache_hit"] is True

    sync_providers = {
        ev["properties"]["provider"]
        for ev in all_events
        if ev["event"] == EventName.SYNC_COMPLETED.value
    }
    assert sync_providers == {
        "activities",
        "today_schedule",
        "courses_catalog",
        "academic_grades",
    }


# ── (d) 30m Background Resume vs Expiry & Midnight Boundary Stamping ──


def test_background_resume_under_30m_vs_midnight_timeout_stamping(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 11, 28, 23, 40, 0, tzinfo=UTC)
    clock = _DeterministicClock(start_utc=start)
    runtime = _make_runtime(tmp_path, clock)
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    ctrl._telemetry.sync_account_state(
        username=settings.UTH_USERNAME,
        has_credentials=True,
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )
    first_session_id = runtime.active_session_id
    assert first_session_id is not None

    # 1. Mobile PAUSE for 10 minutes (< 30m), then RESUME -> same $session_id
    ctrl.page.on_app_lifecycle_state_change(SimpleNamespace(data="pause"))
    clock.advance(600.0)  # 23:50:00Z
    runtime.poll_timers()
    ctrl.page.on_app_lifecycle_state_change(SimpleNamespace(data="resume"))
    assert runtime.active_session_id == first_session_id

    # 2. Hide to desktop tray at 23:52:00Z, then stay hidden across UTC midnight (35m -> 00:27:00Z)
    clock.advance(120.0)  # 23:52:00Z
    ctrl._telemetry.record_interaction(screen="home")
    last_human_utc = clock.now_utc  # 2026-11-28T23:52:00Z
    asyncio.run(ctrl._handle_window_close_request())
    assert ctrl.page.window.visible is False

    clock.advance(35 * 60.0)  # 2026-11-29T00:27:00Z (next UTC calendar day!)
    runtime.poll_timers()
    assert runtime.active_session_id is None

    events = _drain_spooled_events(runtime)
    ended = [e for e in events if e["event"] == EventName.SESSION_ENDED.value]
    assert len(ended) == 1
    assert ended[0]["properties"]["end_reason"] == "background_timeout"
    # Stamped strictly at 2026-11-28T23:52:00.000Z, NOT 2026-11-29T00:27:00.000Z!
    assert ended[0]["timestamp"] == last_human_utc.strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    assert ended[0]["timestamp"].startswith("2026-11-28T")

    # 3. Subsequent restore starts a fresh session ID on the new day.
    ctrl.page.window.visible = True
    ctrl._on_window_restored()
    second_session_id = runtime.active_session_id
    assert second_session_id is not None
    assert second_session_id != first_session_id


# ── (e) Logout & Account Switch distinct_id Rotation ──


def test_logout_and_account_switch_rotate_distinct_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock)
    monkeypatch.setattr("gui.app_controller.save_settings", lambda: None)
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    ctrl._telemetry.sync_account_state(
        username="2251120001",
        has_credentials=True,
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )
    events_before = _drain_spooled_events(runtime)
    first_distinct_id = events_before[0]["distinct_id"]
    install_path = tmp_path / "analytics" / "installation.json"
    assert install_path.is_file()

    # Sign out via AppController._handle_logout_async
    clock.advance(45.0)
    asyncio.run(ctrl._handle_logout_async())

    assert not install_path.exists()
    assert runtime.active_session_id is None
    events_after_logout = _drain_spooled_events(runtime)
    ended = [
        e for e in events_after_logout if e["event"] == EventName.SESSION_ENDED.value
    ]
    assert len(ended) == 1
    assert ended[0]["properties"]["end_reason"] == "account_change"
    assert ended[0]["distinct_id"] == first_distinct_id

    # Clear spool so we inspect only the second user's session events
    runtime.spool.clear()

    # Log in as a second real student (even starting with foreground_visible=False)
    # -> _complete_login_success marks foreground first, then emits login_completed!
    runtime.on_background("tray_hide")
    runtime.tracker._foreground_visible = False
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120099")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "new-secret")
    ctrl._settings_identity = ("", "")
    ctrl._load_data_async = lambda *_a, **_kw: asyncio.sleep(0)
    asyncio.run(ctrl._complete_login_success())

    assert install_path.is_file()
    second_events = _drain_spooled_events(runtime)
    assert [e["event"] for e in second_events] == [EventName.SESSION_STARTED.value]
    assert second_events[0]["properties"]["launch_trigger"] == "login_completed"
    second_distinct_id = second_events[0]["distinct_id"]
    assert second_distinct_id != first_distinct_id


# ── (f) Live Opt-Out Purge, Local Reset & main.py Single-Instance Contract ──


def test_live_opt_out_and_reset_purge_spool_and_installation_file(
    tmp_path: Path,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock)
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    ctrl._telemetry.sync_account_state(
        username="2251120001",
        has_credentials=True,
        account_changed=False,
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )
    ctrl._telemetry.record_grades_interaction("gpa_planner_opened")
    runtime.flush_ram_to_spool(fsync=False)

    install_path = tmp_path / "analytics" / "installation.json"
    assert install_path.is_file()
    assert len(runtime.spool.pending()) >= 1

    # Live opt-out via AppController seam
    ctrl.apply_analytics_consent("disabled")
    assert runtime.current_consent is AnalyticsConsent.DISABLED
    assert not install_path.exists()
    assert runtime.spool.pending() == ()
    assert runtime.active_session_id is None

    # Further UI events while opted out produce 0 files and 0 queued events
    ctrl._telemetry.record_grades_interaction("semester_switched")
    runtime.flush_ram_to_spool(fsync=False)
    assert not install_path.exists()
    assert runtime.spool.pending() == ()

    # Re-enable consent while still visible & authenticated -> starts session immediately
    ctrl.apply_analytics_consent("enabled")
    assert runtime.active_session_id is not None
    re_enabled_events = _drain_spooled_events(runtime)
    assert len(re_enabled_events) == 1
    id_before_reset = re_enabled_events[0]["distinct_id"]
    assert install_path.is_file()

    ctrl.reset_analytics_state()
    assert not install_path.exists()
    assert runtime.spool.pending() == ()

    ctrl._telemetry.on_window_restored(screen="home")
    post_reset_events = _drain_spooled_events(runtime)
    assert len(post_reset_events) == 1
    assert post_reset_events[0]["distinct_id"] != id_before_reset


def test_main_secondary_handoff_does_zero_analytics_io_and_primary_closes_before_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    # 1. Secondary single-instance process returns early with zero config/analytics calls.
    monkeypatch.setattr(application.sys, "platform", "win32")
    monkeypatch.setattr(application.sys, "argv", ["main.py"])
    monkeypatch.delenv("FLET_WEB", raising=False)
    monkeypatch.setattr(application, "_is_source_checkout", lambda _p: False)
    monkeypatch.setattr(application, "is_autostart_launch", lambda: False)
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **_kw: SimpleNamespace(exit_code=0, broker=None, force_visible=False),
    )
    monkeypatch.setattr(
        application,
        "load_runtime_public_config",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("secondary must not read public config")
        ),
    )
    monkeypatch.setattr(
        application,
        "create_default_analytics_runtime",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("secondary must not construct analytics runtime")
        ),
    )
    assert application.main() == 0

    # 2. Primary process loads public config once, threads analytics_runtime to compact_desktop,
    #    and closes analytics_runtime before _terminate_embedded_windows_process.
    public_cfg_reads = 0

    def _load_public_cfg(*, development: bool):
        nonlocal public_cfg_reads
        public_cfg_reads += 1
        return SimpleNamespace(
            dsn=None,
            build_environment="release",
            package_format="exe_msi",
            posthog_project_api_key=_VALID_KEY,
            posthog_host=_VALID_HOST,
        )

    class _AnalyticsSpy:
        def __init__(self, kwargs: dict[str, Any]) -> None:
            self.kwargs = kwargs

        def start(self) -> None:
            events.append("analytics.start")

        def close(self) -> None:
            events.append("analytics.close")

    created_spies: list[_AnalyticsSpy] = []

    def _create_analytics(*_a: Any, **kw: Any) -> _AnalyticsSpy:
        spy = _AnalyticsSpy(kw)
        created_spies.append(spy)
        return spy

    class _DiagSpy:
        def start(self) -> None:
            events.append("diag.start")

        def attach_page(self, _p: Any) -> None:
            pass

        def attach_asyncio_loop(self, _l: Any) -> None:
            pass

        def mark_phase(self, _ph: Any) -> None:
            pass

        def close(self, *, clean: bool) -> None:
            events.append(f"diag.close:{clean}")

    received_app_kwargs: dict[str, Any] = {}

    def _fake_app_main(
        page: Any,
        *,
        activation_broker: Any = None,
        force_visible: bool = False,
        error_reporter: Any = None,
        diagnostic_recorder: Any = None,
        analytics_runtime: Any = None,
    ) -> Any:
        del page, activation_broker, force_visible, error_reporter, diagnostic_recorder
        received_app_kwargs["analytics_runtime"] = analytics_runtime
        return SimpleNamespace(
            _prepare_async_shutdown=lambda: events.append("ctrl.prepare"),
            _shutdown_for_exit=lambda: events.append("ctrl.shutdown"),
        )

    def _fake_flet_run(**kwargs: Any) -> None:
        fake_win = SimpleNamespace(
            destroy=None,
            prevent_close=False,
            visible=True,
            width=1120,
            height=760,
            on_event=None,
            wait_until_ready_to_show=lambda: None,
            center=lambda: None,
        )
        fake_page = SimpleNamespace(
            window=fake_win,
            controls=[],
            update=lambda: None,
        )

        async def _drive() -> None:
            await kwargs["main"](fake_page)
            await fake_win.destroy()

        asyncio.run(_drive())

    monkeypatch.setitem(
        sys.modules,
        "flet",
        SimpleNamespace(
            Page=object,
            AppView=SimpleNamespace(
                WEB_BROWSER="web", FLET_APP_HIDDEN="hidden"
            ),
            WindowEventType=SimpleNamespace(CLOSE="close"),
            run=_fake_flet_run,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "gui.compact_desktop",
        SimpleNamespace(main=_fake_app_main),
    )
    monkeypatch.setattr(
        application,
        "bootstrap_windows_instance",
        lambda **_kw: SimpleNamespace(
            exit_code=None,
            broker=SimpleNamespace(
                close=lambda *, timeout_seconds: events.append("broker.close")
            ),
            force_visible=False,
        ),
    )
    monkeypatch.setattr(application, "load_runtime_public_config", _load_public_cfg)
    monkeypatch.setattr(
        "core.distribution.resolve_distribution_channel",
        lambda **_kw: SimpleNamespace(channel=SimpleNamespace(value="exe_lts")),
    )
    monkeypatch.setattr(
        "platform_utils.windows_update.read_install_channel",
        lambda: "msi",
    )
    monkeypatch.setattr(
        application, "create_default_analytics_runtime", _create_analytics
    )
    monkeypatch.setattr(
        application, "create_default_runtime", lambda *_a, **_kw: _DiagSpy()
    )
    monkeypatch.setattr(application, "_show_startup_screen", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        application, "_is_embedded_windows_runner", lambda **_kw: True
    )
    monkeypatch.setattr(
        application, "_hide_current_process_flutter_windows", lambda: None
    )
    monkeypatch.setattr(
        application,
        "_terminate_embedded_windows_process",
        lambda code: events.append(f"terminate:{code}"),
    )

    assert application.main() == 0
    assert public_cfg_reads == 1
    assert len(created_spies) == 1
    assert received_app_kwargs["analytics_runtime"] is created_spies[0]
    assert created_spies[0].kwargs["api_key"] == _VALID_KEY
    assert created_spies[0].kwargs["host"] == _VALID_HOST
    assert created_spies[0].kwargs["package_format"] == "msi"
    assert created_spies[0].kwargs["release_channel"] == "release"
    # Dynamic config.settings rebind honored by consent_provider
    import config as _cfg_mod

    monkeypatch.setattr(
        _cfg_mod, "settings", SimpleNamespace(ANALYTICS_CONSENT="disabled")
    )
    assert created_spies[0].kwargs["consent_provider"]() == "disabled"
    assert events.index("analytics.close") < events.index("terminate:0")


def test_create_default_analytics_runtime_with_preloaded_config_does_not_reread_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import analytics.runtime as rt_mod

    monkeypatch.setattr(
        rt_mod,
        "_resolve_public_posthog_pair",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("must not re-read release_config.json when preloaded")
        ),
    )
    monkeypatch.setattr(
        rt_mod,
        "_resolve_distribution_channel_name",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("must not re-read distribution channel when preloaded")
        ),
    )
    rt = create_default_analytics_runtime(
        tmp_path,
        development=False,
        api_key=_VALID_KEY,
        host=_VALID_HOST,
        app_version="2.7.0",
        release_channel="release",
        distribution_channel="exe_lts",
        package_format="exe",
        web_mode=False,
        test_mode=True,
        consent="enabled",
        consent_provider=lambda: "enabled",
    )
    assert rt.config.api_key == _VALID_KEY
    assert rt.config.host == _VALID_HOST
    assert rt.config.package_format is PackageFormatName.EXE


def test_consent_provider_refresh_opt_out_blocks_mobile_resume_identity_remint(
    tmp_path: Path,
) -> None:
    clock = _DeterministicClock()
    consent_box = {"value": "enabled"}
    cfg = AnalyticsConfig(
        api_key=_VALID_KEY,
        host=_VALID_HOST,
        app_version="2.7.0",
        release_channel=ReleaseChannel.RELEASE,
        distribution_channel=DistributionChannelName.CHPLAY,
        package_format=PackageFormatName.AAB,
        os_family=OsFamily.ANDROID,
        device_class=DeviceClass.MOBILE,
        web_mode=False,
        test_mode=False,
    )
    runtime = AnalyticsRuntime(
        data_dir=tmp_path,
        config=cfg,
        initial_consent="enabled",
        consent_provider=lambda: consent_box["value"],
        clock=clock,
        monotonic=clock.monotonic,
    )
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)
    ctrl._telemetry.sync_account_state(
        username="2251120001", has_credentials=True, account_changed=False
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )
    install_path = tmp_path / "analytics" / "installation.json"
    assert install_path.is_file()

    # Simulate settings save listener revoking consent directly on runtime
    consent_box["value"] = "disabled"
    runtime.set_consent("disabled")
    assert runtime.tracker.consent is AnalyticsConsent.DISABLED
    assert not install_path.exists()

    # (a) Direct runtime.on_foreground without TelemetryBridge must keep consent DISABLED,
    #     create no installation.json, and spool zero events.
    runtime.on_foreground("mobile_resume", screen="home")
    assert runtime.tracker.consent is AnalyticsConsent.DISABLED
    assert not install_path.exists()
    assert _drain_spooled_events(runtime) == []

    # Mobile pause + resume via TelemetryBridge while opted out also creates 0 files/events
    ctrl.page.on_app_lifecycle_state_change(SimpleNamespace(data="pause"))
    ctrl.page.on_app_lifecycle_state_change(SimpleNamespace(data="resume"))
    ctrl._telemetry.record_filter_applied("home", "urgency")
    assert not install_path.exists()
    assert _drain_spooled_events(runtime) == []


def test_cold_visible_boot_with_consent_disabled_sets_foreground_and_live_opt_in_starts_session(
    tmp_path: Path,
) -> None:
    clock = _DeterministicClock()
    runtime = _make_runtime(tmp_path, clock, consent="disabled")
    assert runtime.tracker.consent is AnalyticsConsent.DISABLED
    ctrl = _build_lightweight_controller(runtime, clock, visible=True)

    ctrl._telemetry.sync_account_state(
        username="2251120001", has_credentials=True, account_changed=False
    )
    ctrl._telemetry.on_startup_visibility(
        is_autostart=False,
        force_visible=False,
        window_hidden_or_minimized=False,
        screen="home",
    )

    install_path = tmp_path / "analytics" / "installation.json"
    assert ctrl._telemetry.foreground_visible is True
    assert runtime.active_session_id is None
    assert not install_path.exists()
    assert _drain_spooled_events(runtime) == []

    # (b) Toggling consent enabled later immediately emits uth_session_started without on_window_restored
    ctrl.apply_analytics_consent("enabled")
    assert runtime.tracker.consent is AnalyticsConsent.ENABLED
    assert runtime.active_session_id is not None
    assert install_path.is_file()
    events = _drain_spooled_events(runtime)
    assert [e["event"] for e in events] == [EventName.SESSION_STARTED.value]


