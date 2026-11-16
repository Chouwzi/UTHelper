from datetime import datetime, timedelta

from core.activity_time_policy import ActivityTimePolicy


def test_policy_recomputes_cached_deadline_from_current_clock():
    now = datetime(2026, 7, 19, 13, 10)
    policy = ActivityTimePolicy(clock=lambda: now)
    activity = {"deadline": (now + timedelta(minutes=50)).isoformat(), "urgency": "safe"}

    state = policy.evaluate(activity)

    assert state.remaining_seconds == 50 * 60
    assert state.urgency == "critical"


def test_policy_crosses_deadline_without_network_fetch():
    now = datetime(2026, 7, 19, 13, 10)
    policy = ActivityTimePolicy(clock=lambda: now)

    state = policy.evaluate({"deadline": (now - timedelta(seconds=1)).isoformat()})

    assert state.overdue is True
    assert state.urgency == "overdue"


def test_policy_assigns_critical_when_overdue_but_late_submission_active():
    now = datetime(2026, 7, 19, 13, 10)
    policy = ActivityTimePolicy(clock=lambda: now)
    cutoff_ts = int((now + timedelta(days=2)).timestamp())
    activity = {
        "deadline": (now - timedelta(hours=5)).isoformat(),
        "cutoff_date": cutoff_ts,
        "late_status": "late_allowed",
        "submission_status": "not_submitted",
    }

    state = policy.evaluate(activity)

    assert state.overdue is True
    assert state.urgency == "critical"


def test_policy_assigns_overdue_when_cutoff_date_passed():
    now = datetime(2026, 7, 19, 13, 10)
    policy = ActivityTimePolicy(clock=lambda: now)
    cutoff_ts = int((now - timedelta(hours=1)).timestamp())
    activity = {
        "deadline": (now - timedelta(hours=5)).isoformat(),
        "cutoff_date": cutoff_ts,
        "late_status": "closed",
        "submission_status": "not_submitted",
    }

    state = policy.evaluate(activity)

    assert state.overdue is True
    assert state.urgency == "overdue"


def test_policy_assigns_overdue_when_already_submitted():
    now = datetime(2026, 7, 19, 13, 10)
    policy = ActivityTimePolicy(clock=lambda: now)
    cutoff_ts = int((now + timedelta(days=2)).timestamp())
    activity = {
        "deadline": (now - timedelta(hours=5)).isoformat(),
        "cutoff_date": cutoff_ts,
        "late_status": "late_allowed",
        "submission_status": "submitted",
    }

    state = policy.evaluate(activity)

    assert state.overdue is True
    assert state.urgency == "overdue"
