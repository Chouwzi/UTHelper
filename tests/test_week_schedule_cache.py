from __future__ import annotations

from datetime import date, datetime, timedelta

from core.today_schedule import (
    WEEK_CACHE_RADIUS,
    ClassSession,
    WeekScheduleCache,
    monday_of,
)

TODAY = date(2026, 11, 19)  # Thursday
BASE = monday_of(TODAY)
FETCHED = datetime(2026, 11, 19, 10, 0)


def _session(day: date) -> ClassSession:
    return ClassSession(
        session_id="1",
        subject="Toán rời rạc",
        course_code="CNS_CS1",
        start_at=datetime.combine(day, datetime.min.time()).replace(hour=7),
        end_at=datetime.combine(day, datetime.min.time()).replace(hour=9),
        room="A.101",
        campus="Cơ sở 1",
        period_start=1,
        period_end=3,
        cancelled=False,
        note="",
        link="https://portal/secret-token",
        meeting_url="https://zoom/secret",
        subject_code="MT01",
        lecturer="Nguyễn Văn A",
        has_outline=True,
        outline="Đề cương",
    )


def _week(monday: date) -> dict[date, tuple[ClassSession, ...]]:
    return {monday: (_session(monday),)}


def test_window_is_current_week_plus_minus_four_nearest_first():
    window = WeekScheduleCache.window(TODAY)

    assert len(window) == 2 * WEEK_CACHE_RADIUS + 1
    assert window[0] == BASE
    assert set(window) == {BASE + timedelta(weeks=n) for n in range(-4, 5)}
    assert window[1:3] == [BASE + timedelta(weeks=1), BASE - timedelta(weeks=1)]


def test_week_round_trip_keeps_detail_fields_but_not_tokens(tmp_path):
    cache = WeekScheduleCache(tmp_path, "user")
    assert cache.save_week(BASE, _week(BASE), FETCHED, TODAY)

    entry = cache.load_window(TODAY)[BASE]
    session = entry.days[BASE][0]

    assert entry.fetched_at == FETCHED
    assert session.subject_code == "MT01"
    assert session.lecturer == "Nguyễn Văn A"
    assert session.has_outline is True
    assert session.outline == "Đề cương"
    assert session.link == ""
    assert session.meeting_url == ""


def test_weeks_outside_window_are_not_saved_and_old_ones_are_pruned(tmp_path):
    cache = WeekScheduleCache(tmp_path, "user")
    outside = BASE + timedelta(weeks=5)
    assert cache.save_week(outside, _week(outside), FETCHED, TODAY) is False

    for n in range(-4, 5):
        monday = BASE + timedelta(weeks=n)
        assert cache.save_week(monday, _week(monday), FETCHED, TODAY)
    assert len(cache.load_window(TODAY)) == 9

    # A week later the oldest week falls out of the window and is dropped.
    later = TODAY + timedelta(weeks=1)
    new_monday = monday_of(later) + timedelta(weeks=4)
    assert cache.save_week(new_monday, _week(new_monday), FETCHED, later)
    loaded = cache.load_window(later)
    assert BASE - timedelta(weeks=4) not in loaded
    assert new_monday in loaded


def test_namespace_isolates_accounts(tmp_path):
    cache = WeekScheduleCache(tmp_path, "alice")
    cache.save_week(BASE, _week(BASE), FETCHED, TODAY)

    cache.set_namespace("bob")
    assert cache.load_window(TODAY) == {}

    cache.set_namespace("alice")
    assert BASE in cache.load_window(TODAY)


def test_corrupt_cache_file_is_ignored(tmp_path):
    cache = WeekScheduleCache(tmp_path, "user")
    cache.save_week(BASE, _week(BASE), FETCHED, TODAY)
    for path in tmp_path.glob("week_schedule_*.json"):
        path.write_text("{not json", encoding="utf-8")

    assert cache.load_window(TODAY) == {}
