from __future__ import annotations

import asyncio
import time as time_module
from datetime import date, datetime, time

from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    SchedulePhase,
    TodayScheduleCache,
    TodayScheduleCoordinator,
    TodayScheduleSnapshot,
    next_schedule_refresh_at,
    parse_portal_day,
)


def _session(
    *,
    start: datetime = datetime(2026, 8, 11, 7, 0),
    end: datetime = datetime(2026, 8, 11, 9, 30),
    cancelled: bool = False,
) -> ClassSession:
    return ClassSession(
        session_id="123",
        subject="Hệ quản trị cơ sở dữ liệu",
        course_code="CNS_CS1",
        start_at=start,
        end_at=end,
        room="A.101",
        campus="Cơ sở 1",
        period_start=1,
        period_end=3,
        cancelled=cancelled,
        note="",
    )


def test_parse_portal_day_preserves_academic_time_and_location_fields():
    payload = {
        "success": True,
        "body": [
            {
                "id": 123,
                "tenMonHoc": "Hệ quản trị cơ sở dữ liệu",
                "maLopHocPhan": "CNS_CS1",
                "tenPhong": "A.101",
                "coSoToDisplay": "Cơ sở 1",
                "tuTiet": 1,
                "denTiet": 3,
                "tuGio": "07:00",
                "denGio": "09:30",
                "isTamNgung": False,
                "ghiChu": "Mang theo laptop",
            },
            {
                "id": 456,
                "tenMonHoc": "Mạng máy tính",
                "maLopHocPhan": "NET_01",
                "roomToDisplay": "B.202",
                "coSoToDisplay": "Cơ sở 2",
                "tuTiet": 7,
                "denTiet": 9,
                "tuGio": "13:00:00",
                "denGio": "15:30:00",
                "isTamNgung": True,
            },
        ],
    }

    sessions = parse_portal_day(payload, date(2026, 8, 11))

    assert [item.subject for item in sessions] == [
        "Hệ quản trị cơ sở dữ liệu",
        "Mạng máy tính",
    ]
    assert sessions[0].start_at == datetime(2026, 8, 11, 7, 0)
    assert sessions[0].end_at == datetime(2026, 8, 11, 9, 30)
    assert sessions[0].room == "A.101"
    assert sessions[0].campus == "Cơ sở 1"
    assert (sessions[0].period_start, sessions[0].period_end) == (1, 3)
    assert sessions[0].note == "Mang theo laptop"
    assert sessions[1].cancelled is True


def test_parse_portal_day_rejects_unsuccessful_or_malformed_envelopes():
    for payload in (
        {"success": False, "body": []},
        {"success": True, "body": {}},
        [],
    ):
        try:
            parse_portal_day(payload, date(2026, 8, 11))
        except ValueError:
            pass
        else:
            raise AssertionError("invalid Portal envelope was accepted")


def test_parse_portal_day_skips_invalid_rows_but_keeps_a_valid_empty_day():
    payload = {
        "success": True,
        "body": [
            {"id": 1, "tenMonHoc": "Thiếu giờ", "tuGio": "", "denGio": ""},
            {"id": 2, "tenMonHoc": "Sai giờ", "tuGio": "xx", "denGio": "yy"},
        ],
    }

    assert parse_portal_day(payload, date(2026, 8, 11)) == ()
    assert parse_portal_day({"success": True, "body": []}, date(2026, 8, 11)) == ()


def test_class_session_phase_boundaries_and_cancellation_priority():
    session = _session()

    assert session.phase_at(datetime(2026, 8, 11, 6, 59, 59)) is SchedulePhase.UPCOMING
    assert session.phase_at(datetime(2026, 8, 11, 7, 0)) is SchedulePhase.IN_PROGRESS
    assert session.phase_at(datetime(2026, 8, 11, 9, 29, 59)) is SchedulePhase.IN_PROGRESS
    assert session.phase_at(datetime(2026, 8, 11, 9, 30)) is SchedulePhase.FINISHED
    assert _session(cancelled=True).phase_at(datetime(2026, 8, 11, 8, 0)) is SchedulePhase.CANCELLED


def test_today_schedule_cache_treats_an_empty_day_as_authoritative(tmp_path):
    cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student")
    snapshot = TodayScheduleSnapshot(
        schedule_date=date(2026, 8, 11),
        sessions=(),
        fetched_at=datetime(2026, 8, 11, 6, 0),
    )

    assert cache.save(snapshot) is True
    assert cache.load_for(date(2026, 8, 11)) == snapshot
    assert cache.load_for(date(2026, 8, 12)) is None


def test_today_schedule_cache_round_trips_sessions(tmp_path):
    cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student")
    snapshot = TodayScheduleSnapshot(
        schedule_date=date(2026, 8, 11),
        sessions=(_session(),),
        fetched_at=datetime(2026, 8, 11, 0, 0),
    )

    assert cache.save(snapshot) is True
    assert cache.load_for(date(2026, 8, 11)) == snapshot


def test_next_schedule_refresh_uses_midnight_and_six_am_boundaries():
    assert next_schedule_refresh_at(datetime(2026, 8, 11, 0, 0)) == datetime(
        2026, 8, 11, 6, 0
    )
    assert next_schedule_refresh_at(datetime(2026, 8, 11, 5, 59)) == datetime(
        2026, 8, 11, 6, 0
    )
    assert next_schedule_refresh_at(datetime(2026, 8, 11, 6, 0)) == datetime(
        2026, 8, 12, 0, 0
    )
    assert next_schedule_refresh_at(datetime(2026, 8, 11, 23, 59)) == datetime(
        2026, 8, 12, 0, 0
    )


def test_coordinator_fetches_on_open_only_when_today_has_no_cache(tmp_path):
    now = datetime(2026, 8, 11, 8, 0)
    cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student")
    calls: list[tuple[date, str, str]] = []
    states = []

    def fetcher(target_date, username, password):
        calls.append((target_date, username, password))
        return (_session(),)

    coordinator = TodayScheduleCoordinator(
        fetch_day=fetcher,
        cache=cache,
        credentials_provider=lambda: ("student", "secret"),
        state_sink=states.append,
        now_provider=lambda: now,
    )

    asyncio.run(coordinator.ensure_today())
    asyncio.run(coordinator.ensure_today())

    assert calls == [(date(2026, 8, 11), "student", "secret")]
    assert [state.status for state in states[:2]] == [
        ScheduleLoadStatus.LOADING,
        ScheduleLoadStatus.READY,
    ]
    assert states[-1].snapshot.sessions == (_session(),)


def test_coordinator_publishes_cached_data_without_network_on_open(tmp_path):
    now = datetime(2026, 8, 11, 8, 0)
    cache = TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student")
    snapshot = TodayScheduleSnapshot(
        schedule_date=now.date(),
        sessions=(_session(),),
        fetched_at=datetime.combine(now.date(), time(0, 0)),
    )
    cache.save(snapshot)
    states = []
    coordinator = TodayScheduleCoordinator(
        fetch_day=lambda *_: (_ for _ in ()).throw(AssertionError("network called")),
        cache=cache,
        credentials_provider=lambda: ("student", "secret"),
        state_sink=states.append,
        now_provider=lambda: now,
    )

    asyncio.run(coordinator.ensure_today())

    assert states[-1].status is ScheduleLoadStatus.READY
    assert states[-1].snapshot == snapshot
    assert states[-1].from_cache is True


def test_coordinator_exposes_auth_required_without_calling_network(tmp_path):
    states = []
    coordinator = TodayScheduleCoordinator(
        fetch_day=lambda *_: (_ for _ in ()).throw(AssertionError("network called")),
        cache=TodayScheduleCache(cache_dir=tmp_path, namespace="anonymous"),
        credentials_provider=lambda: ("", ""),
        state_sink=states.append,
        now_provider=lambda: datetime(2026, 8, 11, 8, 0),
    )

    asyncio.run(coordinator.ensure_today())

    assert states[-1].status is ScheduleLoadStatus.AUTH_REQUIRED


def test_concurrent_startup_and_fast_expand_coalesce_to_one_portal_fetch(tmp_path):
    calls = []

    def fetcher(*_args):
        calls.append("fetch")
        time_module.sleep(0.05)
        return (_session(),)

    coordinator = TodayScheduleCoordinator(
        fetch_day=fetcher,
        cache=TodayScheduleCache(cache_dir=tmp_path, namespace="portal|student"),
        credentials_provider=lambda: ("student", "secret"),
        state_sink=lambda _state: None,
        now_provider=lambda: datetime(2026, 8, 11, 8, 0),
    )

    async def run_both():
        await asyncio.gather(
            coordinator.ensure_today(),
            coordinator.ensure_today(),
        )

    asyncio.run(run_both())

    assert calls == ["fetch"]
