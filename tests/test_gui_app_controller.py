import os
import sys
import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import gui.app_controller as app_controller_module
from core.today_schedule import ScheduleLoadStatus, TodayScheduleViewState
from core.update_coordinator import UpdateCoordinator, UpdateEvent, UpdateEventKind
from core.update_models import LaunchResult, ReleasePackage
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
    assert activity_type[0].trigger_label == "Loại bài"
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


def _portal_session(*, hours_from_now: float = 5.0, session_id: str = "session-1"):
    from core.today_schedule import ClassSession

    start = datetime.now() + timedelta(hours=hours_from_now)
    return ClassSession(
        session_id=session_id,
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


@pytest.mark.parametrize("use_android_bridge", [False, True], ids=["windows", "android"])
def test_reconcile_transient_tomorrow_failure_preserves_armed_schedule(
    monkeypatch, use_android_bridge
):
    """An incomplete timetable must not replace the last complete alarm set."""
    from unittest.mock import AsyncMock

    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="student-01", password="secret")

    async def fetch_day(_credentials):
        raise OSError("temporary Portal outage")

    applied = []
    manager = NotificationManager(tray_app=None)
    manager.notifiers.append(
        SimpleNamespace(reconcile_class_schedule_reminders=lambda reminders: applied.append(reminders))
    )
    bridge = (
        SimpleNamespace(
            available=True,
            schedule_class_sessions=AsyncMock(
                return_value={"scheduled": 1, "cancelled": 0}
            ),
        )
        if use_android_bridge
        else None
    )

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (_portal_session(),),
            fetch_day=fetch_day,
            android_bridge=bridge,
        )
    )

    assert applied == []
    if bridge is not None:
        bridge.schedule_class_sessions.assert_not_awaited()


def test_reconcile_successful_empty_timetable_clears_armed_schedule(monkeypatch):
    """A valid empty response remains authoritative and must clear old alarms."""
    from unittest.mock import AsyncMock

    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="student-01", password="secret")
    bridge = SimpleNamespace(
        available=True,
        schedule_class_sessions=AsyncMock(
            return_value={"scheduled": 0, "cancelled": 2}
        ),
    )
    manager = NotificationManager(tray_app=None)

    asyncio.run(
        manager.reconcile_class_schedule_notifications(
            (),
            fetch_day=AsyncMock(return_value=()),
            android_bridge=bridge,
        )
    )

    bridge.schedule_class_sessions.assert_awaited_once_with([], enabled=True)


def test_reconcile_pushes_payload_to_android_bridge_without_windows_fallback(
    monkeypatch,
):
    """With a native bridge available the payload is scheduled natively only."""
    from unittest.mock import AsyncMock

    from notifiers.manager import NotificationManager

    _class_reconcile_settings(monkeypatch, username="student-01", password="secret")
    today = _portal_session(hours_from_now=5)
    tomorrow = _portal_session(hours_from_now=28, session_id="session-tomorrow")

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
            (today,),
            fetch_day=AsyncMock(return_value=(tomorrow,)),
            android_bridge=bridge,
        )
    )

    assert windows_applied == []
    bridge.schedule_class_sessions.assert_awaited_once()
    (payload,) = bridge.schedule_class_sessions.await_args.args
    assert bridge.schedule_class_sessions.await_args.kwargs == {"enabled": True}
    assert {item["session_id"] for item in payload} == {
        "session-1",
        "session-tomorrow",
    }
    assert all(item["subject"] == "Mạng máy tính" for item in payload)
    assert all(item["delivery_mode"] == "in_person" for item in payload)
    assert all(item["room"] == "F.302" for item in payload)


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
        controller.course_grades_view = SimpleNamespace(visible=False)
        controller.courses_view = SimpleNamespace(visible=False)
        controller._close_detail = AsyncMock()
        controller._close_calendar = AsyncMock()
        controller._close_grades = AsyncMock()
        controller._close_course_grades = AsyncMock()
        controller._sync_rail_active = lambda *a, **k: None

        cases = (
            (controller.settings_view, controller.settings_view._handle_back),
            (controller.detail_view, controller._close_detail),
            (controller.course_grades_view, controller._close_course_grades),
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
        signer_identity="io.github.chouwzi.uthelper",
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
            "io.github.chouwzi.uthelper",
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

    for channel in (
        DistributionChannel.CHPLAY,
        DistributionChannel.MSSTORE,
        DistributionChannel.IOS_APP_STORE,
    ):
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
        "market://details?id=io.github.chouwzi.uthelper",
        "https://play.google.com/store/apps/details?id=io.github.chouwzi.uthelper",
    )
    assert ok is True
    assert calls == [
        ("market://details?id=io.github.chouwzi.uthelper", ft.LaunchMode.EXTERNAL_APPLICATION),
        (
            "https://play.google.com/store/apps/details?id=io.github.chouwzi.uthelper",
            ft.LaunchMode.EXTERNAL_APPLICATION,
        ),
    ]

    assert controller._open_store_url("javascript:alert(1)") is False

    assert controller._open_store_url(
        "itms-apps://apps.apple.com/app/id6750123456",
        "https://apps.apple.com/app/id6750123456",
    ) is True
    assert calls[-1] == (
        "itms-apps://apps.apple.com/app/id6750123456",
        ft.LaunchMode.EXTERNAL_APPLICATION,
    )


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
        "io.github.chouwzi.uthelper",
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


def test_android_startup_sequence_hydrates_migrates_and_scrubs_before_sync(
    monkeypatch, tmp_path
):
    import json
    import config

    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "UTH_USERNAME": "2251120001",
                "UTH_PASSWORD": "legacy-password",
                "THEME": "steel_light",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)

    loaded = config.load_settings()
    monkeypatch.setattr(config, "settings", loaded)
    monkeypatch.setattr(app_controller_module, "settings", loaded)

    events = []

    class _FakeBridge:
        available = True

        def __init__(self):
            self.vault = {"ws_token": "vault-ws", "ws_token_origin": "https://courses.ut.edu.vn"}

        async def read_app_secrets(self):
            events.append("read_vault")
            return dict(self.vault)

        async def write_app_secrets(self, secrets):
            events.append(("write_vault", dict(secrets)))
            self.vault = dict(secrets)
            return True

    scheduled_tasks = []

    def fake_run_task(handler, *args, **kwargs):
        task = asyncio.create_task(handler(*args, **kwargs))
        scheduled_tasks.append(task)
        return task

    bridge = _FakeBridge()
    controller = AppController.__new__(AppController)
    controller._android_background = bridge
    controller._page_alive = app_controller_module.threading.Event()
    controller._page_alive.set()
    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=fake_run_task,
    )
    controller._today_schedule_coordinator = SimpleNamespace(
        run=AsyncMock(side_effect=lambda: events.append("schedule"))
    )

    async def fake_init_bg():
        events.append("init_bg")

    async def fake_load_data(trigger="manual"):
        events.append(("load_data", trigger))

    async def fake_login():
        events.append("login")

    controller._initialize_android_background = fake_init_bg
    controller._load_data_async = fake_load_data
    controller._show_login_dialog = fake_login
    controller._schedule_cached_detail_hydration = lambda _has_cache: False
    controller._schedule_card_detail_warmup = lambda: False

    async def _run():
        await controller._startup_android_sequence(True, False)
        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks)

    try:
        asyncio.run(_run())

        assert events[0] == "read_vault"
        assert events[1] == (
            "write_vault",
            {
                "password": "legacy-password",
                "ws_token": "vault-ws",
                "ws_token_origin": "https://courses.ut.edu.vn",
            },
        )
        assert "init_bg" in events
        assert ("load_data", "startup") in events
        assert "login" not in events
        assert controller._settings_identity == ("2251120001", "legacy-password")
        assert loaded.MOODLE_WS_TOKEN == "vault-ws"
        scrubbed = json.loads(config_file.read_text(encoding="utf-8"))
        assert "UTH_PASSWORD" not in scrubbed
    finally:
        config.register_secret_backend(None)


def test_android_startup_sequence_preserves_legacy_json_when_vault_write_fails(
    monkeypatch, tmp_path
):
    import json
    import config

    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "UTH_USERNAME": "2251120001",
                "UTH_PASSWORD": "legacy-password",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config.sys, "_ANDROID_", True, raising=False)
    monkeypatch.delattr(config.sys, "_IOS_", raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    config.register_secret_backend(None)

    loaded = config.load_settings()
    monkeypatch.setattr(config, "settings", loaded)
    monkeypatch.setattr(app_controller_module, "settings", loaded)

    class _FailingBridge:
        available = True

        async def read_app_secrets(self):
            return {}

        async def write_app_secrets(self, _secrets):
            return False

    scheduled_tasks = []

    def fake_run_task(handler, *args, **kwargs):
        task = asyncio.create_task(handler(*args, **kwargs))
        scheduled_tasks.append(task)
        return task

    controller = AppController.__new__(AppController)
    controller._android_background = _FailingBridge()
    controller._page_alive = app_controller_module.threading.Event()
    controller._page_alive.set()
    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=fake_run_task,
    )
    controller._today_schedule_coordinator = SimpleNamespace(run=AsyncMock())
    controller._initialize_android_background = AsyncMock()
    controller._load_data_async = AsyncMock()
    controller._show_login_dialog = AsyncMock()
    controller._schedule_cached_detail_hydration = lambda _has_cache: False
    controller._schedule_card_detail_warmup = lambda: False

    async def _run():
        await controller._startup_android_sequence(False, True)
        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks)

    try:
        asyncio.run(_run())
        raw = json.loads(config_file.read_text(encoding="utf-8"))
        assert raw["UTH_PASSWORD"] == "legacy-password"
    finally:
        config.register_secret_backend(None)


def test_complete_login_success_resets_schedule_and_course_grade_state():
    import config

    calls = []
    controller = AppController.__new__(AppController)
    controller._portal_client = SimpleNamespace(clear_token=lambda: calls.append("clear_portal_day"))
    controller._portal_week_client = SimpleNamespace(clear_token=lambda: calls.append("clear_portal_week"))
    controller._week_schedule_cache = SimpleNamespace(
        set_namespace=lambda ns: calls.append(("cache_ns", ns))
    )
    controller.calendar_view = SimpleNamespace(reset_schedule=lambda: calls.append("reset_schedule"))
    controller._settings_identity = ("old_user", "old_pw")
    controller.orchestrator = SimpleNamespace(
        moodle_service=SimpleNamespace(clear_all_caches=lambda: calls.append("clear_moodle")),
        clear_detail_cache=lambda: calls.append("clear_detail"),
        _userid_cache=99,
        client=SimpleNamespace(clear_session=lambda: calls.append("clear_client_session")),
        thnn_client=SimpleNamespace(clear_session=lambda: calls.append("clear_thnn_session")),
        course_grade_monitor=SimpleNamespace(
            set_namespace=lambda ns: calls.append(("grade_ns", ns))
        ),
        latest_course_grades=["stale"],
    )
    controller.quiz_player_view = SimpleNamespace(visible=False)
    controller._today_schedule_coordinator = SimpleNamespace(
        replace_cache=lambda c: calls.append(("today_cache", c._path.name)),
        refresh="refresh_fn",
    )
    controller._today_schedule_component = SimpleNamespace(
        set_state=lambda s: calls.append(("today_state", s.status))
    )
    controller._load_data_async = AsyncMock()
    controller._safe_run_task = lambda fn, *args, **kwargs: calls.append(("task", getattr(fn, "__name__", str(fn)), args, kwargs))

    old_user, old_pass = config.settings.UTH_USERNAME, config.settings.UTH_PASSWORD
    try:
        config.settings.UTH_USERNAME = "student99"
        config.settings.UTH_PASSWORD = "pw"
        asyncio.run(controller._complete_login_success())
    finally:
        config.settings.UTH_USERNAME = old_user
        config.settings.UTH_PASSWORD = old_pass

    assert "clear_portal_day" in calls
    assert "clear_portal_week" in calls
    assert ("cache_ns", f"{config.settings.PORTAL_API_BASE}|student99") in calls
    assert "reset_schedule" in calls
    assert ("grade_ns", f"{config.settings.MOODLE_BASE_URL}|student99") in calls
    assert "clear_client_session" in calls
    assert "clear_thnn_session" in calls
    assert controller.orchestrator.latest_course_grades == []


def test_check_grades_background_skips_n_plus_1_html_scrape_when_courses_hidden():
    ws_calls = []
    html_calls = []
    controller = AppController.__new__(AppController)
    controller.orchestrator = SimpleNamespace(
        check_grade_changes=lambda: ws_calls.append(True) or [],
        refresh_course_grades=lambda: html_calls.append(True) or ([], []),
    )
    controller.notifier = SimpleNamespace(dispatch_grade_alert=AsyncMock())
    controller.courses_view = SimpleNamespace(visible=False)

    asyncio.run(controller._check_grades_background())
    assert ws_calls == [True]
    assert html_calls == []


def test_close_calendar_and_grades_sync_rail_active_and_show_grades_reports_error():
    syncs = []
    errors = []
    controller = AppController.__new__(AppController)
    controller.view_manager = SimpleNamespace(
        close_calendar=AsyncMock(side_effect=lambda: syncs.append("close_cal")),
        close_grades=AsyncMock(side_effect=lambda: syncs.append("close_grades")),
        show_grades_loading=lambda: syncs.append("loading"),
        show_grades_error=lambda msg: errors.append(msg),
    )
    controller._diag_screen = lambda s: syncs.append(("screen", s))
    controller._diag_breadcrumb = lambda *args, **kwargs: None
    controller._sync_rail_active = lambda: syncs.append("sync_rail")
    controller._academic_grade_service = SimpleNamespace(
        load_academic_data=MagicMock(side_effect=RuntimeError("Portal down"))
    )

    asyncio.run(controller._close_calendar())
    asyncio.run(controller._close_grades())
    asyncio.run(controller._show_grades(force_refresh=False))

    assert "close_cal" in syncs
    assert "close_grades" in syncs
    assert syncs.count("sync_rail") == 3
    assert len(errors) == 1
    assert "Portal" in errors[0]


def test_detail_view_routes_links_in_app_by_default_and_external_opt_out():
    from gui.components.detail_view import DetailView

    in_app = []
    external = []
    page = SimpleNamespace(
        width=390,
        height=800,
        dialogs=[],
        show_dialog=lambda _d: None,
        update=lambda: None,
    )
    view = DetailView(
        page,
        lambda: None,
        on_open_url=lambda u, t: in_app.append((u, t)),
        on_open_external=lambda u: external.append(u),
    )
    view._current_url = "https://courses.ut.edu.vn/mod/assign/view.php?id=55"
    view._current_data = {"title": "Bài tập 1"}

    asyncio.run(view._open_in_app(None))
    asyncio.run(view._open_external(None))
    asyncio.run(
        view._handle_link_tap(
            SimpleNamespace(data="https://courses.ut.edu.vn/mod/page/view.php?id=9")
        )
    )
    asyncio.run(
        view._handle_link_tap(SimpleNamespace(data="http://courses.ut.edu.vn/insecure"))
    )

    assert in_app == [
        ("https://courses.ut.edu.vn/mod/assign/view.php?id=55", "Bài tập 1"),
        ("https://courses.ut.edu.vn/mod/page/view.php?id=9", "Bài tập 1"),
    ]
    assert external == ["https://courses.ut.edu.vn/mod/assign/view.php?id=55"]


def test_open_external_update_url_validates_github_releases_strictly():
    launched = []
    controller = _update_event_controller()
    controller._safe_run_task = lambda fn, *args, **kwargs: (launched.append(fn), True)[1]

    assert (
        controller._open_external_update_url(
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1"
        )
        is True
    )
    assert len(launched) == 1

    for bad_url in (
        "http://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "https://evil.example/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "https://user@github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "https://github.com:444/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1?next=evil",
        "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1#frag",
        "https://github.com/Other/Repo/releases/tag/v2.5.1",
        "https://github.com/Chouwzi/UTHelper/releases/../issues",
        "https://github.com/Chouwzi/UTHelper/releases/%2e%2e/issues",
        "https://github.com/Chouwzi/UTHelper/releases/%2e%2e%5cissues",
        "https://github.com/Chouwzi/UTHelper/releases/tag\\v2.5.1",
        "https://github.com/Chouwzi/UTHelper/releases/tag/%5cv2.5.1",
        "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1\x7f",
        "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1\x85",
        "javascript:alert(1)",
    ):
        assert controller._open_external_update_url(bad_url) is False
    assert len(launched) == 1


def test_manual_download_required_is_not_overwritten_by_subsequent_update_available():
    controller = _update_event_controller()
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    dialogs = []
    controller._show_update_confirmation = lambda: dialogs.append("confirm")

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED, controller._update_candidate)
        )
    )
    assert controller._update_ready is False
    assert controller._update_manual_download is True
    assert controller._update_btn.content == "Mở trang tải"
    assert controller._update_text.value == "Có bản cập nhật"

    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.UPDATE_AVAILABLE, controller._update_candidate)
        )
    )
    assert controller._update_ready is False
    assert controller._update_btn.content == "Mở trang tải"
    assert controller._update_text.value == "Có bản cập nhật"
    controller._request_update_download()
    assert dialogs == ["confirm"]


@pytest.mark.parametrize(
    ("reason", "expected_release_page"),
    [
        ("sha256_mismatch", False),
        ("size_mismatch", False),
        ("installed_signer_mismatch", True),
        ("installer strategy is not allowed", True),
    ],
)
def test_install_failed_clears_update_ready_for_mismatches_and_release_page_errors(
    reason, expected_release_page
):
    controller = _update_event_controller()
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    controller._update_ready = True
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
                message=reason,
            )
        )
    )
    assert controller._update_ready is False
    assert controller._update_open_release_page is expected_release_page

    # A subsequent UPDATE_AVAILABLE must not show "Cài đặt" for a failed package.
    asyncio.run(
        controller._apply_update_event(
            UpdateEvent(UpdateEventKind.UPDATE_AVAILABLE, controller._update_candidate)
        )
    )
    assert controller._update_btn.content == "Cập nhật"
    controller._request_update_download()
    assert requested == [controller._update_candidate]


def test_app_controller_retry_after_sha256_mismatch_redownloads_with_real_coordinator(tmp_path):
    from datetime import datetime, timezone
    import threading
    from core.update_models import (
        ReleaseManifest,
        RuntimeTarget,
        UpdateCandidate,
        VerificationResult,
    )

    controller = _update_event_controller()
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    package = ReleasePackage(
        platform="android",
        architecture="arm64",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper.apk",
        sha256="a" * 64,
        size=7,
        signer_identity="io.github.chouwzi.uthelper",
        certificate_fingerprint="b" * 64,
        install_strategy={"kind": "android_package_installer"},
    )
    candidate = UpdateCandidate(
        manifest=ReleaseManifest(
            schema_version=2,
            release_version="2.5.1",
            minimum_supported_version="2.0.0",
            published_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
            release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
            packages=(package,),
        ),
        package=package,
        automatic_install_allowed=False,
        required_update=False,
    )
    controller._update_candidate = candidate
    events_seen = []
    ready_event = threading.Event()
    failed_event = threading.Event()

    def _sink(ev):
        events_seen.append(ev)
        controller._on_update_event(ev)
        if ev.kind is UpdateEventKind.READY_TO_INSTALL:
            ready_event.set()
        elif ev.kind is UpdateEventKind.INSTALL_FAILED:
            failed_event.set()

    def _run_task(coro_fn, *args, **kwargs):
        asyncio.run(coro_fn(*args, **kwargs))
        return True

    controller._safe_run_task = _run_task
    pkg_path = tmp_path / "UTHelper-2.5.1.apk"

    class _FakeDownloader:
        def __init__(self):
            self.calls = 0

        def download(self, _package, *, cancel, progress=None):
            del cancel, progress
            self.calls += 1
            pkg_path.write_bytes(b"corrupt" if self.calls == 1 else b"valid")
            return pkg_path

    class _FakeVerifier:
        def verify(self, _path, _candidate):
            return VerificationResult(True)

    class _FakeLauncher:
        supports_cached_package_retry = True

        def __init__(self):
            self.result = LaunchResult(False, "sha256_mismatch", status="failed")

        def launch(self, _path, _package):
            return self.result

        def cancel(self):
            return None

    class _FakeClient:
        def fetch_candidate(self, _version, _target, *, cancel=None):
            del cancel
            return candidate

    downloader = _FakeDownloader()
    launcher = _FakeLauncher()
    coordinator = UpdateCoordinator(
        client=_FakeClient(),
        downloader=downloader,
        verifier=_FakeVerifier(),
        launcher=launcher,
        target=RuntimeTarget("android", "arm64", "sideload"),
        current_version="2.5.0",
        event_sink=_sink,
        automatic_enabled=False,
    )
    controller._update_coordinator = coordinator

    try:
        coordinator.request_download(candidate)
        assert ready_event.wait(2.0)
        assert downloader.calls == 1
        assert controller._update_ready is True

        coordinator.confirm_install()
        assert failed_event.wait(2.0)
        assert controller._update_ready is False
        assert controller._update_btn.content == "Thử lại"
        assert not pkg_path.exists()

        ready_event.clear()
        launcher.result = LaunchResult(True, "installer_opened", status="installer_opened")
        controller._request_update_download()
        assert ready_event.wait(2.0)
        assert downloader.calls == 2
        assert controller._update_ready is True
    finally:
        assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_lookup_moodle_course_grades_cached_and_async_with_semester_disambiguation(tmp_path):
    from core.academic_radar.models import CourseGradeItem as RadarCourseGradeItem
    from core.courses.cache_store import CourseCacheStore
    from core.courses.models import (
        CourseGradeItem as CoreMoodleGradeItem,
        CourseSummary,
    )

    store = CourseCacheStore(cache_dir=tmp_path, namespace="test|user")
    old_course = CourseSummary(
        id=101,
        source="courses",
        fullname="[HKI_2024-2025] Lập trình Python (012000101)",
        shortname="HKI_2024-2025_012000101",
        display_name="Lập trình Python",
        course_code="012000101",
        semester_label="HKI 2024-2025",
        last_synced_at=100,
    )
    cur_course = CourseSummary(
        id=202,
        source="courses",
        fullname="[HKI_2025-2026] Lập trình Python (012000101)",
        shortname="HKI_2025-2026_012000101",
        display_name="Lập trình Python",
        course_code="012000101",
        semester_label="HKI 2025-2026",
        last_synced_at=200,
    )
    uncached_course = CourseSummary(
        id=303,
        source="courses",
        fullname="[HKI_2025-2026] Mạng máy tính (012000201)",
        shortname="HKI_2025-2026_012000201",
        display_name="Mạng máy tính",
        course_code="012000201",
        semester_label="HKI 2025-2026",
        last_synced_at=200,
    )
    store.save_catalog([old_course, cur_course, uncached_course], synced_at=200)
    store.save_course_detail(
        "courses",
        101,
        sections=[],
        grades=[CoreMoodleGradeItem(id=1, item_name="Old Grade", grade_formatted="5.0")],
        synced_at=100,
    )
    store.save_course_detail(
        "courses",
        202,
        sections=[],
        grades=[CoreMoodleGradeItem(id=2, item_name="New Grade", grade_formatted="9.5")],
        synced_at=200,
    )

    loaded_calls = []

    class _FakeCatalogService:
        def refresh_catalog(self, include_thnn=False):
            return [old_course, cur_course, uncached_course]

        def load_course_grades(self, source, course_id, force=False):
            loaded_calls.append((source, course_id, force))
            grades = [CoreMoodleGradeItem(id=3, item_name="Lab 1", grade_formatted="8.0")]
            store.save_course_detail(source, course_id, sections=[], grades=grades, synced_at=300)
            return grades

    controller = AppController.__new__(AppController)
    controller._course_cache_store = store
    controller._course_catalog_service = _FakeCatalogService()
    controller.orchestrator = SimpleNamespace(
        latest_course_grades=[],
        course_grade_monitor=SimpleNamespace(load_cached=lambda: []),
    )

    portal_python = RadarCourseGradeItem(
        course_id="1",
        course_code="012000101",
        course_name="Lập trình Python",
        credits=3,
        subject_code="0120001",
        class_code="012000101",
    )
    res_cached = controller._lookup_moodle_course_grades_cached(
        portal_python, "Học kỳ 1 năm học 2025-2026"
    )
    assert res_cached.course_id == 202
    assert res_cached.source == "courses"
    assert res_cached.needs_async_fetch is False
    assert [g.item_name for g in res_cached.grades] == ["New Grade"]

    # Matched summary with empty detail.grades triggers async fetch
    portal_net = RadarCourseGradeItem(
        course_id="2",
        course_code="012000201",
        course_name="Mạng máy tính",
        credits=3,
        subject_code="0120002",
        class_code="012000201",
    )
    res_pending = controller._lookup_moodle_course_grades_cached(
        portal_net, "Học kỳ 1 năm học 2025-2026"
    )
    assert res_pending.course_id == 303
    assert res_pending.grades == []
    assert res_pending.needs_async_fetch is True

    res_async = asyncio.run(
        controller._fetch_moodle_course_grades_async(
            portal_net, "Học kỳ 1 năm học 2025-2026"
        )
    )
    assert loaded_calls == [("courses", 303, False)]
    assert res_async.needs_async_fetch is False
    assert [g.item_name for g in res_async.grades] == ["Lab 1"]


def test_navigate_back_closes_grade_overview_dialog_before_closing_grades():
    async def scenario():
        from unittest.mock import AsyncMock, Mock

        controller = AppController.__new__(AppController)
        controller.settings_view = SimpleNamespace(visible=False, _handle_back=AsyncMock())
        controller.detail_view = SimpleNamespace(visible=False)
        controller.calendar_view = SimpleNamespace(visible=False)
        controller.course_grades_view = SimpleNamespace(visible=False)
        controller.grade_overview_view = SimpleNamespace(
            visible=True,
            handle_back=Mock(return_value=True),
        )
        controller._close_grades = AsyncMock()
        controller._sync_rail_active = lambda *a, **k: None

        assert await controller._navigate_back() is True
        controller.grade_overview_view.handle_back.assert_called_once_with()
        controller._close_grades.assert_not_awaited()

    asyncio.run(scenario())


def test_lookup_moodle_course_grades_cached_does_not_shadow_matched_course_with_legacy_monitor(
    tmp_path,
):
    from core.academic_radar.models import CourseGradeItem as RadarCourseGradeItem
    from core.course_grade_scraper import CourseGradeReport, GradeRow
    from core.course_grades_service import CourseWithGrades
    from core.courses.cache_store import CourseCacheStore
    from core.courses.models import CourseSummary

    store = CourseCacheStore(cache_dir=tmp_path, namespace="test|shadow")
    matched_summary = CourseSummary(
        id=501,
        source="courses",
        fullname="[HKI_2025-2026] Cơ sở dữ liệu (012005501)",
        shortname="HKI_2025-2026_012005501",
        display_name="Cơ sở dữ liệu",
        course_code="012005501",
        semester_label="HKI 2025-2026",
        last_synced_at=200,
    )
    store.save_catalog([matched_summary], synced_at=200)

    stale_monitor_course = CourseWithGrades(
        course_id=99,
        name="Cơ sở dữ liệu",
        shortname="HKI_2023-2024_OLD",
        report=CourseGradeReport(
            course_id=99,
            rows=[GradeRow(name="Old Exam", grade="5.0", percentage="50 %")],
        ),
    )

    controller = AppController.__new__(AppController)
    controller._course_cache_store = store
    controller._course_catalog_service = SimpleNamespace()
    controller.orchestrator = SimpleNamespace(
        latest_course_grades=[stale_monitor_course],
        course_grade_monitor=SimpleNamespace(load_cached=lambda: [stale_monitor_course]),
    )

    portal_db = RadarCourseGradeItem(
        course_id="501",
        course_code="012005501",
        course_name="Cơ sở dữ liệu",
        credits=3,
        subject_code="0120055",
        class_code="012005501",
    )
    res = controller._lookup_moodle_course_grades_cached(
        portal_db, "Học kỳ 1 năm học 2025-2026"
    )
    assert res.course_id == 501
    assert res.source == "courses"
    assert res.grades == []
    assert res.needs_async_fetch is True


def test_match_portal_course_to_summary_rejects_short_summary_course_code_prefix():
    from core.academic_radar.models import CourseGradeItem as RadarCourseGradeItem
    from core.courses.models import CourseSummary
    from gui.components.course_grade_table import match_portal_course_to_summary

    portal_course = RadarCourseGradeItem(
        course_id="12",
        course_code="012000901",
        course_name="Lập trình C",
        credits=3,
        subject_code="0120009",
        class_code="012000901",
    )
    short_code_summary = CourseSummary(
        id=10,
        source="courses",
        fullname="[HKI_2025-2026] Nhập môn AI (01)",
        shortname="HKI_2025-2026_01",
        display_name="Nhập môn AI",
        course_code="01",
        semester_label="HKI 2025-2026",
        last_synced_at=200,
    )
    assert (
        match_portal_course_to_summary(
            portal_course,
            [short_code_summary],
            semester_name="Học kỳ 1 năm học 2025-2026",
        )
        is None
    )


def test_perform_data_sync_discards_stale_account_result_on_account_switch(tmp_path, monkeypatch):
    from core.data_cache import DataCache
    from core.sync_coordinator import FetchOutcome
    import gui.app_controller as app_controller_module

    monkeypatch.setattr(app_controller_module.settings, "UTH_USERNAME", "userA")
    monkeypatch.setattr(app_controller_module.settings, "UTH_PASSWORD", "pwA")

    in_fetch = asyncio.Event()
    release_fetch = asyncio.Event()
    cache_a = DataCache(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|userA")
    cache_b = DataCache(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|userB")

    async def slow_fetch_a():
        in_fetch.set()
        await release_fetch.wait()
        return FetchOutcome.complete(
            [
                {
                    "title": "Secret User A Assignment",
                    "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=999",
                    "deadline": "2026-12-30 23:59:00",
                    "submission_status": "not_submitted",
                }
            ]
        )

    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller._active_sync_epoch = None
    controller._prefetch_epoch = 0
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._data_lock = threading.Lock()
    controller._cards_lock = threading.Lock()
    controller._is_loading = False
    controller._skeleton_visible = False
    controller.all_data = []
    controller.active_cards = []
    controller._pending_updates = {}
    controller._data_cache = cache_a
    controller.refresh_btn = SimpleNamespace(disabled=False)
    controller.empty_state = SimpleNamespace(visible=False)
    controller.error_state = SimpleNamespace(visible=False)
    controller.error_text = SimpleNamespace(value="")
    controller.page = SimpleNamespace(update=lambda: None, overlay=[])
    controller._android_background = SimpleNamespace(
        available=True,
        configure=AsyncMock(),
        import_activities=AsyncMock(),
        diagnostics=AsyncMock(return_value={}),
    )
    controller.notifier = SimpleNamespace(
        dispatch=AsyncMock(),
        reconcile_schedules=AsyncMock(),
    )
    controller.orchestrator = SimpleNamespace(
        fetch_activity_outcome_async=slow_fetch_a,
        get_cached_details_snapshot=lambda: {},
        moodle_service=SimpleNamespace(clear_all_caches=lambda: None),
        clear_detail_cache=lambda: None,
    )
    controller._diag_breadcrumb = lambda *a, **k: None
    controller._set_sync_status = lambda *a, **k: None
    controller._update_footer = lambda: None
    controller._show_snackbar = lambda *a, **k: None
    controller._safe_run_task = lambda *a, **k: None

    async def scenario():
        sync_task = asyncio.create_task(controller._perform_data_sync("auto"))
        await in_fetch.wait()
        # Switch to Account B while Account A's fetch is in flight.
        controller._account_epoch += 1
        controller._data_cache = cache_b
        release_fetch.set()
        await sync_task

    asyncio.run(scenario())

    assert controller.all_data == []
    assert cache_b.load_snapshot().activities == []
    assert cache_a.load_snapshot().activities == []
    controller._android_background.import_activities.assert_not_awaited()
    controller.notifier.dispatch.assert_not_awaited()


def test_prefetch_details_preserves_pending_submission_and_guards_account_epoch(tmp_path):
    from core.data_cache import DataCache

    url = "https://courses.ut.edu.vn/mod/assign/view.php?id=777"
    cache = DataCache(cache_dir=tmp_path, namespace="https://courses.ut.edu.vn|userA")
    controller = AppController.__new__(AppController)
    controller._account_epoch = 3
    controller._prefetch_epoch = 5
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._data_lock = threading.Lock()
    controller._is_loading = False
    controller._data_cache = cache
    controller.all_data = [
        {
            "title": "Lab 07",
            "url": url,
            "deadline": "2026-12-30 23:59:00",
            "submission_status": "submitted",
        }
    ]
    controller._pending_updates = {}
    controller._update_footer = lambda: None
    controller._schedule_card_detail_warmup = lambda: False

    def prefetch_statuses(_acts, _workers, _cancel):
        # User submits AFTER prefetch starts while server returns stale not_submitted.
        with controller._data_lock:
            controller._pending_updates[url] = (time.time() + 1.0, "submitted")
        return {url: {"submission_status": "not_submitted", "course": "CS"}}

    controller.orchestrator = SimpleNamespace(
        prefetch_activity_statuses=prefetch_statuses
    )

    asyncio.run(controller._prefetch_details_async(controller.all_data, prefetch_epoch=5))
    assert controller.all_data[0]["submission_status"] == "submitted"

    # Now verify account switch mid-prefetch discards stale results completely.
    def prefetch_and_switch(_acts, _workers, _cancel):
        controller._account_epoch += 1
        return {url: {"submission_status": "not_submitted", "course": "StaleA"}}

    controller.orchestrator.prefetch_activity_statuses = prefetch_and_switch
    controller.all_data = [{"title": "UserB", "url": url, "submission_status": "graded"}]
    asyncio.run(controller._prefetch_details_async(controller.all_data, prefetch_epoch=5))
    assert controller.all_data[0]["submission_status"] == "graded"


def test_handle_logout_clears_all_account_state_and_never_uses_none_namespace(monkeypatch):
    import gui.app_controller as app_controller_module
    from config import settings

    events = []
    monkeypatch.setattr(settings, "UTH_USERNAME", "sv_A")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "secret_A")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tok_A")
    monkeypatch.setattr(settings, "MOODLE_SESSION", "sess_A")
    monkeypatch.setattr(app_controller_module, "save_settings", lambda: events.append("save_settings") or True)

    controller = AppController.__new__(AppController)
    controller._settings_identity = ("sv_A", "secret_A")
    controller._account_epoch = 0
    controller._prefetch_epoch = 0
    controller._detail_request_epoch = 0
    controller._grades_request_epoch = 0
    controller._course_grades_request_epoch = 0
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._data_lock = threading.Lock()
    controller._cards_lock = threading.Lock()
    controller.all_data = [{"title": "A"}]
    controller._pending_updates = {"u": (1.0, "submitted")}
    controller.active_cards = [object()]
    controller._sync_coordinator = SimpleNamespace(
        invalidate=lambda account_key=None: events.append(("sync_invalidate", account_key))
    )
    controller.orchestrator = SimpleNamespace(
        clear_caches=lambda: events.append("orch_clear_caches"),
        moodle_service=SimpleNamespace(clear_all_caches=lambda: events.append("moodle_clear")),
        clear_detail_cache=lambda: events.append("detail_clear"),
        _userid_cache=42,
        _thnn_activities_cache=[{"title": "THNN_A"}],
        _last_thnn_fetch_ts=12345.0,
        is_logged_in=True,
        latest_course_grades=[object()],
        client=SimpleNamespace(clear_session=lambda: events.append("client_clear")),
        thnn_client=SimpleNamespace(clear_session=lambda: events.append("thnn_clear")),
        course_grade_monitor=SimpleNamespace(
            reset=lambda: events.append("cgm_reset"),
            set_namespace=lambda ns: events.append(("cgm_ns", ns)),
        ),
        grade_monitor=SimpleNamespace(
            reset=lambda: events.append("gm_reset"),
            set_namespace=lambda ns: events.append(("gm_ns", ns)),
        ),
    )
    controller.notifier = SimpleNamespace(
        clear_account_state=lambda: events.append("notif_clear"),
        set_namespace=lambda ns: events.append(("notif_ns", ns)),
    )
    controller._academic_grade_service = SimpleNamespace(
        clear_session=lambda: events.append("academic_clear")
    )
    controller._portal_client = SimpleNamespace(clear_token=lambda: events.append("portal_clear"))
    controller._portal_week_client = SimpleNamespace(clear_token=lambda: events.append("portal_week_clear"))
    controller._week_schedule_cache = SimpleNamespace(set_namespace=lambda ns: events.append(("week_ns", ns)))
    controller.calendar_view = SimpleNamespace(reset_schedule=lambda: events.append("cal_reset"))
    controller._today_schedule_coordinator = SimpleNamespace(
        replace_cache=lambda _c: events.append("today_replace"),
        refresh=AsyncMock(),
    )
    controller._today_schedule_component = SimpleNamespace(set_state=lambda _s: None)
    controller._courses_coordinator = SimpleNamespace(
        reset_for_account_switch=lambda ns: events.append(("courses_reset", ns))
    )
    # Quiz player is VISIBLE during logout — sessions MUST still be cleared!
    quiz_clear = AsyncMock()
    controller.quiz_player_view = SimpleNamespace(
        visible=True,
        force_close=lambda: events.append("quiz_force_close"),
        clear_persisted_sessions=quiz_clear,
    )
    controller._sync_quiz_immersive = lambda active: events.append(("quiz_immersive", active))
    controller._android_background = SimpleNamespace(available=True, logout=AsyncMock())
    controller.settings_view = SimpleNamespace(visible=True)
    controller._close_settings = AsyncMock()
    controller._close_app_webview = lambda update_page=False: events.append("close_wv")
    controller._schedule_webview2_profile_purge = lambda keep: events.append(("purge_wv2", keep))
    controller._show_snackbar = lambda *a, **k: events.append("snack")
    controller._show_login_dialog = AsyncMock()
    controller.page = SimpleNamespace()
    scheduled = []
    controller._safe_run_task = lambda fn, *a, **k: scheduled.append((fn, a, k))

    asyncio.run(controller._handle_logout_async())

    assert settings.UTH_USERNAME == ""
    assert settings.UTH_PASSWORD == ""
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_SESSION == ""
    assert controller.all_data == []
    assert controller._pending_updates == {}
    assert controller.orchestrator._thnn_activities_cache == []
    assert controller.orchestrator._last_thnn_fetch_ts == 0.0
    assert controller.quiz_player_view.visible is False
    from core.data_cache import DataCache

    assert controller._data_cache._cache_path == DataCache(namespace="__logged_out__")._cache_path
    assert controller._data_cache._cache_path != DataCache(namespace=None)._cache_path
    assert "save_settings" in events
    assert "orch_clear_caches" in events
    assert "client_clear" in events
    assert "thnn_clear" in events
    assert "cgm_reset" in events
    assert "gm_reset" in events
    assert "notif_clear" in events
    assert "academic_clear" in events
    assert "quiz_force_close" in events
    assert any(fn is quiz_clear for fn, _, _ in scheduled)
    assert any(fn is controller._android_background.logout for fn, _, _ in scheduled)
    assert any(fn is controller._show_login_dialog for fn, _, _ in scheduled)


def test_show_grades_logged_out_has_no_attribute_error_and_respects_account_epoch(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")

    shown = []
    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller._grades_request_epoch = 0
    controller._diag_screen = lambda *a, **k: None
    controller._diag_breadcrumb = lambda *a, **k: None
    controller._sync_rail_active = lambda: None
    controller.orchestrator = SimpleNamespace(_get_userid=lambda: None)
    controller.view_manager = SimpleNamespace(
        show_grades_loading=lambda: shown.append("loading"),
        show_academic_radar_data=lambda sems, profs, *_a: shown.append(("radar", sems)),
        show_grades_error=lambda msg: shown.append(("error", msg)),
    )
    controller._academic_grade_service = SimpleNamespace(
        load_academic_data=lambda **kw: [],
    )

    asyncio.run(controller._show_grades(force_refresh=False))
    assert ("radar", []) in shown
    assert not any(tag == "error" for tag in shown if isinstance(tag, tuple) and tag[0] == "error")

    # Verify stale epoch result is discarded on account switch mid-load.
    shown.clear()

    def load_then_switch(**_kw):
        controller._account_epoch += 1
        return [SimpleNamespace(name="OldSem")]

    controller._academic_grade_service = SimpleNamespace(
        load_academic_data=load_then_switch,
        get_radar_profiles=lambda s: s,
    )
    asyncio.run(controller._show_grades(force_refresh=True))
    assert shown == ["loading"]


def test_check_grades_background_checks_course_grades_view_not_courses_view():
    ws_calls = []
    html_calls = []
    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller.orchestrator = SimpleNamespace(
        check_grade_changes=lambda: ws_calls.append(True) or [],
        refresh_course_grades=lambda: html_calls.append(True) or ([], []),
    )
    controller.notifier = SimpleNamespace(dispatch_grade_alert=AsyncMock())
    # Courses tab is visible, but legacy course_grades_view is hidden -> must NOT scrape HTML!
    controller.courses_view = SimpleNamespace(visible=True)
    controller.course_grades_view = SimpleNamespace(visible=False)

    asyncio.run(controller._check_grades_background())
    assert ws_calls == [True]
    assert html_calls == []


def test_notify_grade_changes_routes_via_show_snackbar_and_prunes_overlay():
    controller = AppController.__new__(AppController)
    overlay = [ft.Container(), ft.SnackBar(content=ft.Text("old1")), ft.SnackBar(content=ft.Text("old2"))]
    controller.page = SimpleNamespace(overlay=overlay, update=lambda: None)
    controller.notifier = SimpleNamespace(dispatch_grade_alert=AsyncMock())

    change = SimpleNamespace(item_name="Midterm", old_grade="7.0", new_grade="9.0")
    asyncio.run(controller._notify_grade_changes([change]))

    snackbars = [c for c in overlay if isinstance(c, ft.SnackBar)]
    assert len(snackbars) == 1
    assert snackbars[0].open is True
    assert not hasattr(controller.page, "snack_bar")
    # Non-SnackBar overlay items remain untouched.
    assert any(isinstance(c, ft.Container) for c in overlay)


def test_navigate_back_from_courses_root_tab_returns_to_home():
    closed = []
    controller = AppController.__new__(AppController)
    controller.quiz_player_view = None
    controller._app_webview_host = None
    controller._app_webview_pane = None
    controller._today_schedule_component = None
    controller.settings_view = SimpleNamespace(visible=False)
    controller.detail_view = SimpleNamespace(visible=False)
    controller.course_grades_view = SimpleNamespace(visible=False)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller.grade_overview_view = SimpleNamespace(visible=False)
    controller.courses_view = SimpleNamespace(visible=True, handle_back=lambda: False)
    controller._close_courses = AsyncMock(side_effect=lambda: closed.append("courses"))
    controller._sync_rail_active = lambda: None

    handled = asyncio.run(controller._navigate_back())
    assert handled is True
    assert closed == ["courses"]


def test_window_hidden_or_minimized_skips_tray_loop_updates():
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(window=SimpleNamespace(visible=False, minimized=False))
    assert controller._is_window_hidden_or_minimized() is True

    controller.page = SimpleNamespace(window=SimpleNamespace(visible=True, minimized=True))
    assert controller._is_window_hidden_or_minimized() is True

    controller.page = SimpleNamespace(window=SimpleNamespace(visible=True, minimized=False))
    assert controller._is_window_hidden_or_minimized() is False


def test_shutdown_closes_windows_notifier_and_thnn_client_and_prepares_async():
    closed = []
    controller = AppController.__new__(AppController)
    controller._exit_cleanup_lock = threading.Lock()
    controller._exit_cleanup_done = False
    controller._page_alive = threading.Event()
    controller._page_alive.set()
    controller._prefetch_cancel_event = threading.Event()
    controller._card_detail_warm_cancel_event = threading.Event()
    controller._sync_coordinator = SimpleNamespace(close=lambda: closed.append("sync"))
    controller._today_schedule_coordinator = SimpleNamespace(close=lambda: closed.append("today"))
    controller._close_app_webview = lambda update_page=False: closed.append("wv")
    win_notifier = SimpleNamespace(close=lambda: closed.append("windows_notifier"))
    controller.notifier = SimpleNamespace(
        close=lambda: closed.append("notifier_mgr"),
        notifiers=[win_notifier],
    )
    controller.orchestrator = SimpleNamespace(
        client=SimpleNamespace(close=lambda: closed.append("client")),
        thnn_client=SimpleNamespace(close=lambda: closed.append("thnn_client")),
        notifier=controller.notifier,
    )

    controller._prepare_async_shutdown()
    assert "sync" in closed
    assert "today" in closed
    assert not controller._page_alive.is_set()

    controller._shutdown_for_exit()
    assert "notifier_mgr" in closed
    assert "windows_notifier" in closed
    assert "client" in closed
    assert "thnn_client" in closed


def test_webview_occlusion_seam_and_bottom_inset_64(monkeypatch):
    import gui.app_controller as app_controller_module

    occlusions = []
    monkeypatch.setattr(
        app_controller_module,
        "_set_webview_occlusion",
        lambda reason, occ: occlusions.append((reason, occ)),
    )

    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(
        web=False,
        width=500,
        height=800,
        show_dialog=lambda dlg: setattr(controller, "_last_dlg", dlg),
        pop_dialog=lambda: None,
    )
    controller._nav_shell = SimpleNamespace(
        rail_visible=False,
        set_immersive=lambda _a: None,
        bar=None,
    )
    controller._get_distribution_profile = lambda: SimpleNamespace(
        supports_github_self_update=True
    )

    _left, _top, _w, _h, bottom_inset = controller._compute_app_webview_bounds(500, 800)
    assert bottom_inset == 64.0

    controller._sync_quiz_immersive(True)
    controller._sync_quiz_immersive(False)
    assert ("quiz_player", True) in occlusions
    assert ("quiz_player", False) in occlusions

    controller._show_update_confirmation()
    assert ("modal_dialog", True) in occlusions
    controller._last_dlg.on_dismiss(None)
    assert ("modal_dialog", False) in occlusions


def test_test_notification_channels_offload_via_safe_run_task():
    controller = AppController.__new__(AppController)
    scheduled = []
    controller._safe_run_task = lambda fn, *a, **k: scheduled.append(fn) or object()

    controller._on_test_tele("critical")
    controller._on_test_discord("warning")
    controller._on_test_mail("safe")
    controller._on_test_tray("quiz")

    assert len(scheduled) == 4


def test_settings_account_login_persists_and_routes_through_unified_teardown(monkeypatch):
    import gui.app_controller as app_controller_module
    from config import settings

    calls = []
    monkeypatch.setattr(settings, "UTH_USERNAME", "old_user")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "old_pass")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "old_tok")
    monkeypatch.setattr(settings, "MOODLE_SESSION", "old_sess")
    monkeypatch.setattr(
        app_controller_module,
        "save_settings",
        lambda: calls.append(("save_settings", settings.UTH_USERNAME, settings.MOODLE_WS_TOKEN)) or True,
    )

    controller = AppController.__new__(AppController)
    controller._complete_login_success = AsyncMock(side_effect=lambda: calls.append(("complete_login", settings.UTH_USERNAME)))

    asyncio.run(controller._handle_settings_account_login_async("new_user", "new_pass"))

    assert settings.UTH_USERNAME == "new_user"
    assert settings.UTH_PASSWORD == "new_pass"
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_SESSION == ""
    assert calls == [
        ("save_settings", "new_user", ""),
        ("complete_login", "new_user"),
    ]


def test_show_course_grades_discards_stale_account_epoch():
    shown = []
    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller._course_grades_request_epoch = 0
    controller._diag_screen = lambda *a, **k: None
    controller._diag_breadcrumb = lambda *a, **k: None
    controller.view_manager = SimpleNamespace(
        show_course_grades_loading=lambda: shown.append("loading"),
        show_course_grades_data=lambda data: shown.append(("data", data)),
        show_course_grades_error=lambda: shown.append("error"),
    )

    def refresh_and_switch():
        controller._account_epoch += 1
        return (["StaleCourseA"], [SimpleNamespace(item_name="X", old_grade="5", new_grade="9")])

    controller.orchestrator = SimpleNamespace(
        latest_course_grades=[],
        course_grade_monitor=SimpleNamespace(load_cached=lambda: []),
        refresh_course_grades=refresh_and_switch,
    )
    controller._notify_grade_changes = AsyncMock()

    asyncio.run(controller._show_course_grades(force_refresh=True))

    assert shown == ["loading"]
    controller._notify_grade_changes.assert_not_awaited()


def test_ios_startup_hydrates_keychain_registers_backend_and_allows_save_settings(
    monkeypatch, tmp_path
):
    import json
    import config
    import gui.app_controller as app_controller_module

    config_file = tmp_path / "settings.json"
    config_file.write_text(
        json.dumps(
            {
                "UTH_USERNAME": "2251120099",
                "UTH_PASSWORD": "legacy-ios-password",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delattr(config.sys, "_ANDROID_", raising=False)
    monkeypatch.setattr(config.sys, "_IOS_", True, raising=False)
    monkeypatch.setattr(config, "_HAS_KEYRING", False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_IOS", True)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", True)
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_WINDOWS", False)
    config.register_secret_backend(None)
    config.register_secret_backend_factory(None)

    loaded = config.load_settings()
    monkeypatch.setattr(config, "settings", loaded)
    monkeypatch.setattr(app_controller_module, "settings", loaded)

    # Fail-closed before iOS startup registers the native Keychain backend.
    assert config.save_settings() is False

    events = []
    keychain_store = {
        "ws_token": "ios-keychain-ws",
        "ws_token_origin": "https://courses.ut.edu.vn",
    }

    class _FakeIosBridge:
        available = True

        async def read_app_secrets(self):
            events.append("read_keychain")
            return dict(keychain_store)

        async def write_app_secrets(self, secrets_map):
            events.append(("write_keychain", dict(secrets_map)))
            keychain_store.clear()
            keychain_store.update(secrets_map)
            return True

        async def clear_app_secrets(self):
            keychain_store.clear()
            return True

    bridge = _FakeIosBridge()

    # 1. Verify AppController.__init__ schedules _startup_android_sequence on iOS.
    scheduled_in_init = []
    with monkeypatch.context() as mp:
        mp.setattr(
            app_controller_module.AppController,
            "_init_window",
            lambda self: setattr(self, "_android_background", bridge),
        )
        mp.setattr(
            app_controller_module.AppController,
            "_create_update_coordinator",
            lambda self: None,
        )
        mp.setattr(
            app_controller_module.AppController, "_init_ui", lambda self: None
        )
        mp.setattr(
            app_controller_module.AppController,
            "_load_cached_snapshot",
            lambda self: False,
        )
        mp.setattr(
            app_controller_module.AppController,
            "_configure_mobile_back_navigation",
            lambda self: None,
        )
        mp.setattr(
            app_controller_module.AppController,
            "_safe_run_task",
            lambda self, fn, *args, **kwargs: scheduled_in_init.append(
                getattr(fn, "__name__", str(fn))
            ),
        )
        fake_init_page = SimpleNamespace(
            session=SimpleNamespace(connection=object()),
            on_disconnect=None,
            on_keyboard_event=None,
            on_resize=None,
            update=lambda: None,
        )
        init_ctrl = AppController(fake_init_page)
        del init_ctrl
    assert "_startup_android_sequence" in scheduled_in_init
    assert "_initialize_android_background" not in scheduled_in_init

    # 2. Verify _startup_android_sequence hydrates Keychain, registers backend,
    #    skips Android-only background init, and makes save_settings() succeed.
    scheduled_tasks = []

    def fake_run_task(handler, *args, **kwargs):
        task = asyncio.create_task(handler(*args, **kwargs))
        scheduled_tasks.append(task)
        return task

    controller = AppController.__new__(AppController)
    controller._android_background = bridge
    controller._page_alive = app_controller_module.threading.Event()
    controller._page_alive.set()
    controller.page = SimpleNamespace(
        session=SimpleNamespace(connection=object()),
        run_task=fake_run_task,
    )
    controller._today_schedule_coordinator = SimpleNamespace(
        run=AsyncMock(side_effect=lambda: events.append("schedule"))
    )
    controller._initialize_android_background = AsyncMock(
        side_effect=lambda: events.append("android_bg_init")
    )
    controller._load_data_async = AsyncMock(
        side_effect=lambda trigger="manual": events.append(("load_data", trigger))
    )
    controller._show_login_dialog = AsyncMock(
        side_effect=lambda: events.append("login")
    )
    controller._schedule_cached_detail_hydration = lambda _has_cache: False
    controller._schedule_card_detail_warmup = lambda: False

    async def _run():
        await controller._startup_android_sequence(True, False)
        if scheduled_tasks:
            await asyncio.gather(*scheduled_tasks)
        loaded.UTH_PASSWORD = "updated-ios-password"
        return await asyncio.to_thread(config.save_settings)

    try:
        saved_ok = asyncio.run(_run())
        assert saved_ok is True
        assert events[0] == "read_keychain"
        assert ("load_data", "startup") in events
        assert "login" not in events
        controller._initialize_android_background.assert_not_awaited()
        assert loaded.MOODLE_WS_TOKEN == "ios-keychain-ws"
        assert keychain_store["password"] == "updated-ios-password"
        persisted = json.loads(config_file.read_text(encoding="utf-8"))
        assert "UTH_PASSWORD" not in persisted
        assert "MOODLE_WS_TOKEN" not in persisted
    finally:
        config.register_secret_backend(None)
        config.register_secret_backend_factory(None)


def test_attach_modal_occlusion_release_awaits_async_flet_dismiss_and_clears_occlusion(
    monkeypatch,
):
    import inspect
    import warnings
    import gui.app_controller as app_controller_module

    occlusions = []
    monkeypatch.setattr(
        app_controller_module,
        "_set_webview_occlusion",
        lambda reason, occ: occlusions.append((reason, occ)),
    )

    dismissed = []
    dlg = SimpleNamespace(open=True, on_dismiss=None)
    dialogs_controls = [dlg]

    async def wrapped_on_dismiss(event=None):
        await asyncio.sleep(0)
        dismissed.append(event)
        if dlg in dialogs_controls:
            dialogs_controls.remove(dlg)

    dlg.on_dismiss = wrapped_on_dismiss

    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(
        _dialogs=SimpleNamespace(controls=dialogs_controls),
        overlay=[],
        dialog=None,
    )

    assert controller._attach_modal_occlusion_release() is True
    assert inspect.iscoroutinefunction(dlg.on_dismiss)

    sentinel_event = SimpleNamespace(data=None)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", RuntimeWarning)
        asyncio.run(dlg.on_dismiss(sentinel_event))

    assert dismissed == [sentinel_event]
    assert dialogs_controls == []
    assert occlusions == [("modal_dialog", False)]
    unawaited = [w for w in caught if "was never awaited" in str(w.message)]
    assert unawaited == []

    # Even if the wrapped dismiss handler raises, modal occlusion must still release.
    occlusions.clear()
    failing_dlg = SimpleNamespace(open=True, on_dismiss=None)

    async def failing_dismiss(_event=None):
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    failing_dlg.on_dismiss = failing_dismiss
    controller.page = SimpleNamespace(
        _dialogs=SimpleNamespace(controls=[failing_dlg]),
        overlay=[],
        dialog=None,
    )
    assert controller._attach_modal_occlusion_release() is True
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(failing_dlg.on_dismiss(sentinel_event))
    assert occlusions == [("modal_dialog", False)]


def test_perform_data_sync_skips_when_logged_out_without_error_state_or_fetch(monkeypatch):
    from unittest.mock import Mock
    import gui.app_controller as app_controller_module

    monkeypatch.setattr(app_controller_module.settings, "UTH_USERNAME", "")
    monkeypatch.setattr(app_controller_module.settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "")

    controller = AppController.__new__(AppController)
    controller._account_epoch = 1
    controller._active_sync_epoch = None
    controller._is_loading = False
    controller.all_data = []
    controller._data_cache = SimpleNamespace(load=Mock(return_value=([], None)))
    controller.refresh_btn = SimpleNamespace(disabled=False)
    controller.empty_state = SimpleNamespace(visible=False)
    controller.error_state = SimpleNamespace(visible=False)
    controller.error_text = SimpleNamespace(value="")
    controller.page = SimpleNamespace(update=Mock(), overlay=[])
    controller.orchestrator = SimpleNamespace(
        fetch_activity_outcome_async=AsyncMock(side_effect=RuntimeError("no credentials")),
        moodle_service=SimpleNamespace(clear_all_caches=Mock()),
        clear_detail_cache=Mock(),
    )
    controller._diag_breadcrumb = Mock()
    controller._set_sync_status = Mock()
    controller._update_footer = Mock()

    asyncio.run(controller._perform_data_sync("timer"))

    controller.orchestrator.fetch_activity_outcome_async.assert_not_awaited()
    assert controller._is_loading is False
    assert controller.refresh_btn.disabled is False
    assert controller.error_state.visible is False
    controller._set_sync_status.assert_not_called()

