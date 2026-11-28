"""Unit tests for ForegroundSessionTracker, AnalyticsRuntime, and create_default_analytics_runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import threading
import time

from analytics.identity import INSTALLATION_FILENAME
from analytics.models import (
    AnalyticsConfig,
    AnalyticsConsent,
    AnalyticsEvent,
    DeviceClass,
    DistributionChannelName,
    EventName,
    OsFamily,
    PackageFormatName,
    ReleaseChannel,
)
from analytics.runtime import AnalyticsRuntime, create_default_analytics_runtime
from analytics.transport import BatchDeliveryOutcome

VALID_KEY = "phc_0123456789abcdefghijKLMNOPQRST"
VALID_HOST = "https://us.i.posthog.com"


class _ManualClock:
    def __init__(self, start_utc: datetime, start_mono: float = 1_000.0) -> None:
        self.utc = start_utc
        self.mono = start_mono

    def advance(self, seconds: float) -> None:
        self.utc = self.utc + timedelta(seconds=seconds)
        self.mono += seconds

    def now_utc(self) -> datetime:
        return self.utc

    def now_mono(self) -> float:
        return self.mono


def _cfg(**overrides) -> AnalyticsConfig:
    base = {
        "api_key": VALID_KEY,
        "host": VALID_HOST,
        "app_version": "2.7.0",
        "release_channel": ReleaseChannel.RELEASE,
        "distribution_channel": DistributionChannelName.EXE_LTS,
        "package_format": PackageFormatName.EXE,
        "os_family": OsFamily.WINDOWS,
        "device_class": DeviceClass.DESKTOP,
    }
    base.update(overrides)
    return AnalyticsConfig(**base)


def _spooled_events(rt: AnalyticsRuntime) -> list[AnalyticsEvent]:
    rt.flush_ram_to_spool(fsync=False)
    return [ev for item in rt.spool.pending() for ev in item.batch.events]


def test_strict_prelogin_demo_unconfigured_and_hidden_tray_gates(tmp_path: Path):
    clk = _ManualClock(datetime(2026, 11, 28, 8, 0, 0, tzinfo=UTC))

    # 1. Unconfigured runtime: 100% disk and memory no-op
    unconf = AnalyticsRuntime(
        data_dir=tmp_path / "unconf",
        config=_cfg(api_key=None, host=None),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    unconf.set_account_state(authenticated=True, is_demo=False)
    unconf.on_foreground("cold_foreground", "home")
    assert not (tmp_path / "unconf" / "analytics").exists()
    assert _spooled_events(unconf) == []
    unconf.close()

    # 2. Configured runtime: pre-login screen + Play Store uthelper_demo crawler -> 0 events, 0 files!
    rt = AnalyticsRuntime(
        data_dir=tmp_path / "app",
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    # Window shown on login dialog (unauthenticated)
    rt.on_foreground("cold_foreground", "home")
    assert not (rt.analytics_dir / INSTALLATION_FILENAME).exists()
    assert _spooled_events(rt) == []

    # Bot logs in as uthelper_demo -> still 0 events, 0 installation.json!
    rt.set_account_state(authenticated=True, is_demo=True)
    rt.capture_grades_interaction("gpa_planner_opened")
    assert not (rt.analytics_dir / INSTALLATION_FILENAME).exists()
    assert _spooled_events(rt) == []

    # Real student completes login while window is already foreground-visible!
    rt.set_account_state(authenticated=True, is_demo=False)
    assert (rt.analytics_dir / INSTALLATION_FILENAME).is_file()
    evs = _spooled_events(rt)
    assert len(evs) == 1
    assert evs[0].event is EventName.SESSION_STARTED
    assert evs[0].properties["launch_trigger"] == "login_completed"
    rt.close()

    # 3. Hidden Windows tray autostart: authenticated at boot, window hidden -> 0 events!
    rt_tray = AnalyticsRuntime(
        data_dir=tmp_path / "tray",
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    rt_tray.set_account_state(authenticated=True, is_demo=False)
    assert rt_tray.capture_sync_completed(
        provider="activities",
        trigger="foreground_poll",
        outcome="succeeded",
        duration_seconds=0.4,
        item_count=8,
    ) is False
    assert rt_tray.capture_notification_summary(
        kind="activity_deadline",
        delivered_count=1,
        scheduled_count=2,
        had_channel_failure=False,
        channels_mask=1,
    ) is False
    assert not (rt_tray.analytics_dir / INSTALLATION_FILENAME).exists()
    assert _spooled_events(rt_tray) == []

    # Student clicks tray icon -> session starts cleanly!
    rt_tray.on_foreground("tray_restore", "home")
    tray_evs = _spooled_events(rt_tray)
    assert len(tray_evs) == 1
    assert tray_evs[0].properties["launch_trigger"] == "tray_restore"
    rt_tray.close()


def test_deferred_30m_background_resume_and_no_midnight_dau_spillover(tmp_path: Path):
    # Monday 2026-11-30 23:45:00 UTC
    clk = _ManualClock(datetime(2026, 11, 30, 23, 45, 0, tzinfo=UTC))
    rt = AnalyticsRuntime(
        data_dir=tmp_path,
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    rt.set_account_state(authenticated=True, is_demo=False)
    rt.on_foreground("cold_foreground", "home")
    s1 = rt.active_session_id
    assert s1 is not None

    # Minimize for 45 seconds to pick a file -> resumes SAME $session_id seamlessly!
    clk.advance(60.0)
    rt.on_background("window_minimize")
    clk.advance(45.0)
    rt.on_foreground("window_restore", "detail")
    assert rt.active_session_id == s1
    assert [e.event for e in _spooled_events(rt)] == [EventName.SESSION_STARTED]

    # At 23:50:00 Monday, student hides to tray and shuts down PC at 00:25:00 Tuesday (35m later)
    clk.utc = datetime(2026, 11, 30, 23, 50, 0, tzinfo=UTC)
    rt.on_background("tray_hide")

    # Background sync while hidden in tray is dropped and must NOT advance timestamp!
    clk.advance(600.0)  # 00:00 Tuesday
    assert rt.capture_sync_completed(
        provider="activities",
        trigger="foreground_poll",
        outcome="succeeded",
        duration_seconds=0.5,
        item_count=3,
    ) is False

    # Advance past 30m background timeout (00:25 Tuesday)
    clk.advance(1500.0)  # total 35m in background
    rt.poll_timers()

    evs = _spooled_events(rt)
    assert [e.event for e in evs] == [
        EventName.SESSION_STARTED,
        EventName.SESSION_ENDED,
    ]
    ended = evs[1]
    assert ended.properties["$session_id"] == s1
    assert ended.properties["end_reason"] == "background_timeout"
    # Stamped at 23:50 Monday (last_foreground_active_utc), NOT 00:25 Tuesday!
    assert ended.timestamp == "2026-11-30T23:50:00.000Z"
    rt.close()

    # Also verify close() after overnight sleep while window stayed open stamps last_foreground_active_utc!
    clk2 = _ManualClock(datetime(2026, 11, 30, 23, 30, 0, tzinfo=UTC))
    rt2 = AnalyticsRuntime(
        data_dir=tmp_path / "sleep_close",
        config=_cfg(),
        clock=clk2.now_utc,
        monotonic=clk2.now_mono,
    )
    rt2.set_account_state(authenticated=True, is_demo=False)
    rt2.on_foreground("cold_foreground", "home")
    clk2.advance(120.0)  # 23:32 Monday interaction
    rt2.record_interaction("home")
    clk2.advance(8 * 3600.0)  # 07:32 Tuesday morning wakeup -> immediate close()
    rt2.close()
    evs2 = _spooled_events(rt2)
    assert evs2[-1].event is EventName.SESSION_ENDED
    assert evs2[-1].timestamp == "2026-11-30T23:32:00.000Z"


def test_foreground_heartbeat_idle_timeout_passive_poll_and_23h_rotation(tmp_path: Path):
    clk = _ManualClock(datetime(2026, 11, 28, 8, 0, 0, tzinfo=UTC))
    rt = AnalyticsRuntime(
        data_dir=tmp_path,
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    rt.set_account_state(authenticated=True, is_demo=False)
    rt.on_foreground("cold_foreground", "courses")
    s1 = rt.active_session_id

    # 5m foreground heartbeat emits while active
    clk.advance(300.0)
    rt.poll_timers()
    evs = _spooled_events(rt)
    assert evs[-1].event is EventName.SESSION_HEARTBEAT
    assert evs[-1].properties["elapsed_bucket"] == "5m_15m"
    assert evs[-1].properties["active_screen"] == "courses"

    # Passive foreground_poll at +20m does NOT reset last_interaction_mono!
    clk.advance(900.0)  # total 20m since cold_foreground
    assert rt.capture_sync_completed(
        provider="courses_catalog",
        trigger="foreground_poll",
        outcome="succeeded",
        duration_seconds=1.2,
        item_count=12,
    ) is True

    # At +31m since last human interaction -> idle_timeout ends session s1!
    clk.advance(660.0)  # total 31m
    rt.poll_timers()
    assert rt.active_session_id is None
    evs = _spooled_events(rt)
    assert evs[-1].event is EventName.SESSION_ENDED
    assert evs[-1].properties["end_reason"] == "idle_timeout"

    # Passive poll while idle-expired is dropped (does NOT start a fake session!)
    assert rt.capture_sync_completed(
        provider="activities",
        trigger="foreground_poll",
        outcome="succeeded",
        duration_seconds=0.3,
        item_count=0,
    ) is False
    assert rt.active_session_id is None

    # Human interaction wakes with a brand-new UUIDv7 session (idle_resume)!
    clk.advance(10.0)
    assert rt.capture_document_opened(
        doc_format="pdf",
        viewer_kind="pdfjs_webview",
        source_site="courses",
        cache_hit=False,
        load_duration_seconds=0.8,
    ) is True
    s2 = rt.active_session_id
    assert s2 is not None and s2 != s1

    # Keep interacting across 23h -> rotates session before PostHog's 24h cap!
    for _ in range(50):
        clk.advance(1700.0)  # <30m steps totaling 85,000s (> 82,800s = 23h)
        rt.record_interaction("courses")

    s3 = rt.active_session_id
    assert s3 is not None and s3 != s2
    all_evs = _spooled_events(rt)
    assert any(
        e.event is EventName.SESSION_ENDED
        and e.properties.get("end_reason") == "max_age_rotate"
        for e in all_evs
    )
    rt.close()


def test_screen_1_5s_debounce_filter_2_0s_debounce_and_all_typed_captures(tmp_path: Path):
    clk = _ManualClock(datetime(2026, 11, 28, 10, 0, 0, tzinfo=UTC))
    rt = AnalyticsRuntime(
        data_dir=tmp_path,
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    rt.set_account_state(authenticated=True, is_demo=False)
    rt.on_foreground("cold_foreground", "home")

    # Rapid tab hopping < 1.5s: home -> calendar (0.3s) -> courses (0.4s) -> grades (settled >= 1.5s)
    rt.capture_screen_viewed("calendar", "tab_bar")
    clk.advance(0.3)
    rt.capture_screen_viewed("courses", "tab_bar")
    clk.advance(0.4)
    rt.capture_screen_viewed("grades", "tab_bar")
    clk.advance(1.6)
    rt.poll_timers()

    # Rapid filter toggles < 2.0s coalesce into one event
    rt.capture_filter_applied("home", "urgency")
    clk.advance(0.5)
    rt.capture_filter_applied("home", "search_used")
    clk.advance(2.1)
    rt.poll_timers()

    # Exercise remaining typed capture methods
    assert rt.capture_course_activity_opened(
        activity_kind="quiz_overview",
        source_site="courses",
    )
    assert rt.capture_quiz_action(
        action="player_closed",
        source_site="courses",
        duration_bucket="5m_15m",
    )
    assert rt.capture_submission_action(
        action="upload_add",
        outcome="succeeded",
        file_count=2,
    )
    assert rt.capture_grades_interaction("course_popup_opened")
    assert rt.capture_notification_summary(
        kind="class_schedule",
        delivered_count=0,
        scheduled_count=4,
        had_channel_failure=False,
        channels_mask=3,
    )
    rt.close()

    evs = _spooled_events(rt)
    screen_evs = [e for e in evs if e.event is EventName.SCREEN_VIEWED]
    assert len(screen_evs) == 1
    assert screen_evs[0].properties["screen"] == "grades"

    filter_evs = [e for e in evs if e.event is EventName.FILTER_APPLIED]
    assert len(filter_evs) == 1
    assert filter_evs[0].properties["filter_kind"] == "search_used"

    ended = evs[-1]
    assert ended.event is EventName.SESSION_ENDED
    assert ended.properties["end_reason"] == "app_exit"
    # home + grades = 2 screens visited -> "2_3"
    assert ended.properties["screens_visited_bucket"] == "2_3"


def test_120_events_per_session_and_300_events_per_24h_caps(tmp_path: Path):
    clk = _ManualClock(datetime(2026, 11, 28, 12, 0, 0, tzinfo=UTC))
    rt = AnalyticsRuntime(
        data_dir=tmp_path,
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    rt.set_account_state(authenticated=True, is_demo=False)

    # Session 1: pump 160 events -> strictly capped at 120
    rt.on_foreground("cold_foreground", "home")
    for _ in range(160):
        clk.advance(0.1)
        rt.capture_grades_interaction("semester_switched")
    assert len(_spooled_events(rt)) == 120

    # Force idle rotation for Session 2 (+35m): pump 160 -> capped at +120 (total 240)
    clk.advance(2100.0)
    rt.poll_timers()
    rt.on_foreground("idle_resume", "home")
    for _ in range(160):
        clk.advance(0.1)
        rt.capture_grades_interaction("semester_switched")
    assert len(_spooled_events(rt)) == 240

    # Force idle rotation for Session 3 (+35m): pump 160 -> rolling 24h cap stops total at 300!
    clk.advance(2100.0)
    rt.poll_timers()
    rt.on_foreground("idle_resume", "home")
    for _ in range(160):
        clk.advance(0.1)
        rt.capture_grades_interaction("semester_switched")
    # Clear spool periodically if >20 batches? Note: 300 events / 25 per batch = 12 batches (<=20)!
    assert len(_spooled_events(rt)) == 300

    # Advance > 24h: rolling 24h quota recovers!
    clk.advance(86_401.0)
    rt.on_foreground("idle_resume", "home")
    assert len(_spooled_events(rt)) > 300
    rt.close()


def test_account_switch_rotates_distinct_id_and_opt_out_and_close_are_nonblocking(
    tmp_path: Path,
):
    clk = _ManualClock(datetime(2026, 11, 28, 14, 0, 0, tzinfo=UTC))
    rt = AnalyticsRuntime(
        data_dir=tmp_path,
        config=_cfg(),
        clock=clk.now_utc,
        monotonic=clk.now_mono,
    )
    # Student A logs in
    rt.set_account_state(authenticated=True, is_demo=False)
    rt.on_foreground("cold_foreground", "home")
    id_a = rt.identity_store.peek_cached()
    sess_a = rt.active_session_id
    assert id_a is not None and sess_a is not None

    # Student B logs in on the same PC (account_changed=True)
    clk.advance(30.0)
    rt.set_account_state(authenticated=True, is_demo=False, account_changed=True)
    id_b = rt.identity_store.peek_cached()
    sess_b = rt.active_session_id
    assert id_b is not None and id_b != id_a
    assert sess_b is not None and sess_b != sess_a

    evs = _spooled_events(rt)
    assert [e.event for e in evs] == [
        EventName.SESSION_STARTED,
        EventName.SESSION_ENDED,
        EventName.SESSION_STARTED,
    ]
    assert evs[1].distinct_id == id_a
    assert evs[1].properties["end_reason"] == "account_change"
    assert evs[2].distinct_id == id_b

    # Opt-out purges RAM + pending spool + installation.json immediately
    rt.set_consent(AnalyticsConsent.DISABLED)
    assert rt.spool.pending() == ()
    assert not (rt.analytics_dir / INSTALLATION_FILENAME).exists()
    assert rt.identity_store.peek_cached() is None

    # Re-enable while visible -> mints brand-new ID C != B and starts fresh session
    rt.set_consent(AnalyticsConsent.ENABLED)
    id_c = rt.identity_store.peek_cached()
    assert id_c is not None and id_c not in {id_a, id_b}

    # Test non-blocking close() while background delivery thread is stuck in slow network I/O
    in_socket = threading.Event()
    release_socket = threading.Event()

    class HangingTransport:
        def send_batch(self, events, *, timeout_seconds):
            in_socket.set()
            release_socket.wait(timeout=2.0)
            return BatchDeliveryOutcome.retryable_failure("timeout")

    rt.delivery._transport = HangingTransport()  # type: ignore[assignment]
    rt.flush_ram_to_spool(fsync=False)
    rt._submit_network_flush()
    assert in_socket.wait(timeout=2.0)

    t0 = time.perf_counter()
    rt.close()
    close_ms = (time.perf_counter() - t0) * 1000.0
    release_socket.set()

    # close() never waits for the 2-second hanging socket!
    assert close_ms < 250.0
    final_evs = [ev for item in rt.spool.pending() for ev in item.batch.events]
    assert final_evs[-1].event is EventName.SESSION_ENDED
    assert final_evs[-1].properties["end_reason"] == "app_exit"


def test_create_default_analytics_runtime_dev_env_and_schema_v3_assets(
    tmp_path: Path,
    monkeypatch,
):
    import analytics.runtime as runtime_mod

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "diagnostics-config.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "sentry_dsn": "",
                "build_environment": "release",
                "package_format": "aab",
                "posthog_project_api_key": VALID_KEY,
                "posthog_host": "https://eu.i.posthog.com",
            }
        ),
        encoding="utf-8",
    )
    (assets / "distribution-channel.json").write_text(
        json.dumps({"schema_version": 1, "channel": "chplay"}),
        encoding="utf-8",
    )

    rt_prod = create_default_analytics_runtime(
        tmp_path / "prod_data",
        development=False,
        consent="enabled",
        environ={},
        assets_dir=assets,
    )
    assert rt_prod.config.configured is True
    assert rt_prod.config.active_eligible is True
    assert rt_prod.config.host == "https://eu.i.posthog.com"
    assert rt_prod.config.distribution_channel.value == "chplay"
    assert rt_prod.config.package_format.value == "aab"
    rt_prod.close()

    # Dev env override with only UTH_POSTHOG_KEY defaults host to https://us.i.posthog.com
    rt_dev = create_default_analytics_runtime(
        tmp_path / "dev_data",
        development=True,
        consent="enabled",
        environ={"UTH_POSTHOG_KEY": VALID_KEY},
        assets_dir=tmp_path / "empty_assets",
    )
    assert rt_dev.config.configured is True
    assert rt_dev.config.host == "https://us.i.posthog.com"
    rt_dev.close()

    # Dev env override with UTH_POSTHOG_HOST only combines with posthog_project_api_key from asset!
    rt_dev_host_override = create_default_analytics_runtime(
        tmp_path / "dev_host_override",
        development=True,
        consent="enabled",
        environ={"UTH_POSTHOG_HOST": "https://127.0.0.1:8443/ingest/"},
        assets_dir=assets,
    )
    assert rt_dev_host_override.config.configured is True
    assert rt_dev_host_override.config.api_key == VALID_KEY
    assert rt_dev_host_override.config.host == "https://127.0.0.1:8443/ingest"
    rt_dev_host_override.close()

    # UTH_TEST_MODE=1 suppresses active_eligible automatically
    rt_test = create_default_analytics_runtime(
        tmp_path / "test_data",
        development=False,
        consent="enabled",
        environ={"UTH_TEST_MODE": "1"},
        assets_dir=assets,
    )
    assert rt_test.config.active_eligible is False
    rt_test.close()

    # Pre-loaded args from main.py (even None on unconfigured builds) skip ALL cold-boot asset reads!
    def _fail_disk_read(*_a, **_kw):
        raise AssertionError("unexpected cold-boot asset disk read")

    monkeypatch.setattr(runtime_mod, "_read_small_json_asset", _fail_disk_read)
    monkeypatch.setattr(runtime_mod, "_resolve_default_app_version", _fail_disk_read)

    rt_preloaded = create_default_analytics_runtime(
        tmp_path / "preloaded_unconf",
        development=False,
        api_key=None,
        host=None,
        app_version="2.7.0",
        release_channel="release",
        distribution_channel="exe_lts",
        package_format="exe",
        consent="enabled",
        environ={},
        assets_dir=assets,
    )
    assert rt_preloaded.config.configured is False
    rt_preloaded.close()
