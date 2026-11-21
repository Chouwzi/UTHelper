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
    _AndroidPackageLauncher,
    _android_release_build_number,
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


def test_auth_required_schedule_state_clears_class_reminders():
    """A logged-out Portal session must drop the previous account's reminders."""
    controller = AppController.__new__(AppController)
    calls = []
    controller._safe_run_task = (
        lambda handler, *args, **kwargs: calls.append((handler, args, kwargs))
    )
    controller._diag_breadcrumb = lambda *args: None
    controller._today_schedule_component = SimpleNamespace(set_state=lambda _s: None)
    controller.page = SimpleNamespace(update=lambda: None)

    controller._on_today_schedule_state(
        TodayScheduleViewState(ScheduleLoadStatus.AUTH_REQUIRED)
    )

    reconciliations = [
        (args[0], kwargs)
        for handler, args, kwargs in calls
        if handler == controller._reconcile_class_schedule_notifications
    ]
    assert reconciliations == [((), {"clear_only": True})]


def test_reconcile_clear_only_never_calls_portal_fetch(monkeypatch):
    """Logout/account-switch clearing must not fetch tomorrow's timetable."""
    from unittest.mock import Mock

    from config import settings
    from notifiers.manager import NotificationManager

    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-01")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")

    # The manager owns the use case now: a clear_only run must never reach
    # the network, so the fetch callable stays untouched.
    fetch_day = Mock()
    manager = NotificationManager(tray_app=None)
    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (), clear_only=True, fetch_day=fetch_day
        )
    )
    fetch_day.assert_not_called()

    # The controller forwards the flag unchanged to the manager.
    controller = AppController.__new__(AppController)
    forwarded = {}

    async def _reconcile(sessions, **kwargs):
        forwarded["sessions"] = sessions
        forwarded.update(kwargs)

    controller.notifier = SimpleNamespace(
        reconcile_class_schedule_notifications=_reconcile
    )
    asyncio.run(
        AppController._reconcile_class_schedule_notifications(
            controller, (), clear_only=True
        )
    )
    assert forwarded["sessions"] == ()
    assert forwarded.get("clear_only") is True


def test_reconcile_clear_only_never_schedules_even_when_enabled(monkeypatch):
    """clear_only is a teardown: it must drop reminders, never rebuild them."""
    from unittest.mock import Mock

    from config import settings
    from core.today_schedule import ClassSession
    from notifiers.manager import NotificationManager

    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "UTH_USERNAME", "student-01")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret")

    now = datetime.now()
    session = ClassSession(
        session_id="session-1",
        subject="Mạng máy tính",
        course_code="CN123",
        start_at=now + timedelta(hours=5),
        end_at=now + timedelta(hours=7),
        room="F.302",
        campus="Cơ sở Võ Oanh",
        period_start=1,
        period_end=3,
        cancelled=False,
        note="",
    )
    applied = []
    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda r: applied.append(r))
    )
    fetch_day = Mock()

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (session,), clear_only=True, fetch_day=fetch_day
        )
    )

    fetch_day.assert_not_called()
    assert applied == [()]


def test_reconcile_identity_is_captured_at_task_creation_not_after_lock(monkeypatch):
    """A task queued by the old account must abort even if it gets the lock later.

    Regression for the race where ``identity_before`` was read *after*
    ``async with lock``: a stale task waiting behind an in-flight one would
    capture the NEW identity, pass its own check, and rebuild the previous
    account's sessions under the new account's name.
    """
    from config import settings
    from notifiers.manager import NotificationManager

    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "UTH_USERNAME", "old-user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "old-pass")

    identity = {"value": ("old-user", "old-pass")}
    applied = []
    fetch_log = []
    # fetch_day is awaited directly on the event loop, so the gate must be a
    # cooperative asyncio primitive (blocking here would freeze the scenario).
    first_fetch_started = asyncio.Event()
    gate = asyncio.Event()

    async def fetch_day(credentials):
        fetch_log.append(credentials)
        if len(fetch_log) == 1:
            first_fetch_started.set()
            await gate.wait()
        return ()

    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda r: applied.append(r))
    )

    async def run_reconcile():
        return await manager.reconcile_class_schedule_notifications(
            (),
            fetch_day=fetch_day,
            identity_getter=lambda: identity["value"],
        )

    async def scenario():
        # 1. A task of the current account takes the lock and suspends in fetch.
        blocker = asyncio.create_task(run_reconcile())
        await first_fetch_started.wait()
        # 2. A second task of the SAME (soon stale) account queues behind it.
        stale = asyncio.create_task(run_reconcile())
        await asyncio.sleep(0)  # stale now waits on the reconcile lock
        # 3. The user switches accounts while both are in flight.
        identity["value"] = ("new-user", "new-pass")
        monkeypatch.setattr(settings, "UTH_USERNAME", "new-user")
        monkeypatch.setattr(settings, "UTH_PASSWORD", "new-pass")
        # 4. Let the blocker finish; the queued stale task gets the lock next.
        gate.set()
        await asyncio.gather(blocker, stale)
        # 5. A task created AFTER the switch must still reconcile normally.
        await run_reconcile()

    asyncio.run(scenario())

    # Both old-account tasks abort; only the new-account task applies.
    assert applied == [()]
    assert fetch_log[0] == ("old-user", "old-pass")
    assert fetch_log[-1] == ("new-user", "new-pass")
    assert len(fetch_log) == 2  # blocker fetched; stale aborted before fetch


def _portal_session(*, hours_from_now: float = 5.0):
    from core.today_schedule import ClassSession

    start = datetime.now() + timedelta(hours=hours_from_now)
    return ClassSession(
        session_id="session-1",
        subject="Mạng máy tính",
        course_code="CN123",
        start_at=start,
        end_at=start + timedelta(hours=2),
        room="F.302",
        campus="Cơ sở Võ Oanh",
        period_start=1,
        period_end=3,
        cancelled=False,
        note="",
    )


def _class_reconcile_settings(monkeypatch, *, username, password):
    from config import settings

    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_ENABLED", True)
    monkeypatch.setattr(settings, "NOTIFY_CLASS_SCHEDULE_LEAD_HOURS", (1,))
    monkeypatch.setattr(settings, "NOTIFY_DND_ENABLE", False)
    monkeypatch.setattr(settings, "UTH_USERNAME", username)
    monkeypatch.setattr(settings, "UTH_PASSWORD", password)


def test_reconcile_disables_schedule_when_portal_credentials_are_missing(monkeypatch):
    """No usable Portal identity must clear the armed schedule, not keep it.

    ``credentials_available`` gates the RESULT, not only the fetch: otherwise a
    logged-out account keeps firing reminders built from a snapshot nobody can
    refresh anymore.
    """
    from unittest.mock import Mock

    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="", password="")

    fetch_day = Mock()
    applied = []
    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda r: applied.append(r))
    )

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (_portal_session(),), fetch_day=fetch_day
        )
    )

    fetch_day.assert_not_called()
    # A live session was handed in, yet the pushed payload is empty: the
    # enabled flag was turned off instead of scheduling from stale data.
    assert applied == [()]


def test_reconcile_auth_failure_clears_the_previously_armed_schedule(monkeypatch):
    """A rejected Portal session must clear reminders instead of being swallowed."""
    from core.today_schedule import ScheduleAuthenticationError
    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="student-01", password="expired")

    async def fetch_day(_credentials):
        raise ScheduleAuthenticationError("Portal rejected the session")

    applied = []
    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda r: applied.append(r))
    )

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (_portal_session(),), fetch_day=fetch_day
        )
    )

    # The old ``except Exception`` path kept scheduling the cached sessions;
    # the auth branch must flip ``enabled`` off so the payload is empty.
    assert applied == [()]


def test_reconcile_pushes_payload_to_android_bridge_without_windows_fallback(
    monkeypatch,
):
    """With a native bridge available the payload is scheduled natively only."""
    from unittest.mock import AsyncMock

    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="student-01", password="secret")

    bridge = SimpleNamespace(
        available=True,
        schedule_class_sessions=AsyncMock(
            return_value={"scheduled": 1, "cancelled": 0}
        ),
    )
    windows_applied = []
    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(
            reconcile_class_schedule_reminders=lambda r: windows_applied.append(r)
        )
    )

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (_portal_session(),),
            fetch_day=AsyncMock(return_value=()),
            android_bridge=bridge,
        )
    )

    assert windows_applied == []
    bridge.schedule_class_sessions.assert_awaited_once()
    (payload,) = bridge.schedule_class_sessions.await_args.args
    assert bridge.schedule_class_sessions.await_args.kwargs == {"enabled": True}
    assert len(payload) == 1
    assert payload[0]["subject"] == "Mạng máy tính"
    assert payload[0]["delivery_mode"] == "in_person"
    assert payload[0]["room"] == "F.302"


def test_clear_only_reconcile_survives_the_account_switch_that_follows_it(monkeypatch):
    """identity_changed() must never abort a clear_only teardown.

    Regression: the account flip that triggers the teardown is exactly the one
    the stale-task guard used to see, so the previous account's reminders could
    outlive the logout they were queued to drop.
    """
    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="old-user", password="old-pass")

    identity = {"value": ("old-user", "old-pass")}
    applied = []
    fetch_log = []
    first_fetch_started = asyncio.Event()
    gate = asyncio.Event()

    async def fetch_day(credentials):
        fetch_log.append(credentials)
        first_fetch_started.set()
        await gate.wait()
        return ()

    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda r: applied.append(r))
    )

    async def run(clear_only):
        return await manager.reconcile_class_schedule_notifications(
            (),
            clear_only=clear_only,
            fetch_day=fetch_day,
            identity_getter=lambda: identity["value"],
        )

    async def scenario():
        # 1. A full reconcile of the old account holds the lock mid-fetch.
        blocker = asyncio.create_task(run(clear_only=False))
        await first_fetch_started.wait()
        # 2. The teardown queues behind it...
        teardown = asyncio.create_task(run(clear_only=True))
        await asyncio.sleep(0)
        # 3. ...and the account flips before either finishes.
        identity["value"] = ("new-user", "new-pass")
        gate.set()
        await asyncio.gather(blocker, teardown)

    asyncio.run(scenario())

    # The stale full reconcile aborted without applying (one fetch only), but
    # the teardown still pushed its empty payload.
    assert len(fetch_log) == 1
    assert applied == [()]


def test_settings_saved_updates_identity_before_scheduling_any_task(monkeypatch):
    """_settings_identity must flip before clear_only/refresh tasks are queued.

    Otherwise the first reconcile after login/switch compares against the old
    identity and is mistaken for an account change (or misses one).
    """
    from unittest.mock import Mock

    from config import settings

    controller = AppController.__new__(AppController)
    controller._settings_identity = ("old-user", "old-pass")
    controller._notification_milestones_snapshot = tuple(
        settings.NOTIFY_MILESTONES_MINUTES or []
    )
    controller._notification_class_schedule_snapshot = (
        bool(settings.NOTIFY_CLASS_SCHEDULE_ENABLED),
        tuple(settings.NOTIFY_CLASS_SCHEDULE_LEAD_HOURS),
        bool(settings.NOTIFY_DND_ENABLE),
        int(settings.NOTIFY_DND_START),
        int(settings.NOTIFY_DND_END),
    )
    controller.orchestrator = None
    controller._data_cache = None
    controller._portal_client = SimpleNamespace(clear_token=Mock())
    controller._portal_week_client = SimpleNamespace(clear_token=Mock())
    controller._week_schedule_cache = SimpleNamespace(set_namespace=Mock())
    controller.calendar_view = SimpleNamespace(reset_schedule=Mock())
    coordinator = SimpleNamespace(
        replace_cache=Mock(), ensure_today=Mock(), refresh=Mock()
    )
    controller._today_schedule_coordinator = coordinator
    controller._today_schedule_component = SimpleNamespace(set_state=Mock())
    controller._today_schedule_state = TodayScheduleViewState(ScheduleLoadStatus.READY)
    controller.page = SimpleNamespace()
    controller._rebuild_colors = Mock()
    controller._show_snackbar = Mock()
    controller._android_background = None
    controller._sync_coordinator = None
    controller._update_coordinator = None

    scheduled = []

    def _safe_run_task(handler, *args, **kwargs):
        # Snapshot the identity at the moment the task is queued.
        scheduled.append((handler, args, kwargs, controller._settings_identity))

    controller._safe_run_task = _safe_run_task

    monkeypatch.setattr(settings, "UTH_USERNAME", "new-user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "new-pass")

    AppController._on_settings_saved(controller)

    assert controller._settings_identity == ("new-user", "new-pass")
    assert scheduled, "expected at least the clear_only reconcile to be queued"
    for handler, args, kwargs, identity_at_schedule in scheduled:
        assert identity_at_schedule == ("new-user", "new-pass"), handler
    # The credentials path queues the clear_only teardown first.
    assert scheduled[0][2].get("clear_only") is True
    # ...and refreshes the schedule instead of serving whatever cache the new
    # namespace happens to hold (ensure_today would publish it as READY).
    assert scheduled[1][0] is coordinator.refresh
    assert scheduled[1][1] == ("credentials_changed",)
    controller._portal_client.clear_token.assert_called_once()
    controller._portal_week_client.clear_token.assert_called_once()
    controller._week_schedule_cache.set_namespace.assert_called_once_with(
        f"{settings.PORTAL_API_BASE}|new-user"
    )
    controller.calendar_view.reset_schedule.assert_called_once()
    coordinator.ensure_today.assert_not_called()
    coordinator.replace_cache.assert_called_once()


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
        controller.courses_view = SimpleNamespace(visible=False)
        controller._close_detail = AsyncMock()
        controller._close_calendar = AsyncMock()
        controller._close_grades = AsyncMock()
        controller._close_courses = AsyncMock()
        controller._sync_rail_active = lambda *a, **k: None

        cases = (
            (controller.settings_view, controller.settings_view._handle_back),
            (controller.detail_view, controller._close_detail),
            (controller.calendar_view, controller._close_calendar),
            (controller.grade_overview_view, controller._close_grades),
            (controller.courses_view, controller._close_courses),
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
            UpdateEvent(UpdateEventKind.INSTALL_LAUNCHED, controller._update_candidate)
        )
    )

    assert controller._update_banner.visible is True
    assert controller._update_text.value == "Đang mở trình cài đặt Android..."
    assert controller._update_btn.content == "Đang mở..."
    assert controller._update_btn.disabled is True

    controller._on_android_update_result({"status": "installer_opened"})

    assert controller._update_banner.visible is False


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
