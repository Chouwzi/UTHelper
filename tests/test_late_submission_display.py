from datetime import datetime, timedelta

from core.filter_service import FilterService
from gui.core.theme import C
from gui.core.utils import get_card_countdown, get_status_tag, get_urgency_badge


def test_get_urgency_badge_overdue_not_safe():
    label, color = get_urgency_badge("overdue")
    assert label == "QUÁ HẠN"
    assert color == C.CRITICAL


def test_get_urgency_badge_critical():
    label, color = get_urgency_badge("critical")
    assert label == "KHẨN CẤP"
    assert color == C.CRITICAL


def test_get_urgency_badge_warning_and_safe():
    w_label, w_color = get_urgency_badge("warning")
    assert w_label == "SẮP HẠN"
    assert w_color == C.WARNING

    s_label, s_color = get_urgency_badge("safe")
    assert s_label == "AN TOÀN"
    assert s_color == C.SAFE


def test_get_status_tag_late_submission():
    now = datetime.now()
    deadline = (now - timedelta(hours=2)).isoformat()
    cutoff_ts = int((now + timedelta(days=2)).timestamp())
    item = {
        "deadline": deadline,
        "cutoff_date": cutoff_ts,
        "late_status": "late_allowed",
        "submission_status": "not_submitted",
        "type": "assignment",
    }
    label, color = get_status_tag(item)
    assert label == "NỘP MUỘN"
    assert color == C.WARNING


def test_get_status_tag_overdue_closed():
    now = datetime.now()
    deadline = (now - timedelta(hours=2)).isoformat()
    cutoff_ts = int((now - timedelta(minutes=10)).timestamp())
    item = {
        "deadline": deadline,
        "cutoff_date": cutoff_ts,
        "late_status": "closed",
        "submission_status": "not_submitted",
        "type": "assignment",
    }
    label, color = get_status_tag(item)
    assert label == "QUÁ HẠN"
    assert color == C.CRITICAL


def test_get_card_countdown_late_submission():
    now = datetime.now()
    deadline = (now - timedelta(hours=3)).isoformat()
    cutoff_ts = int((now + timedelta(days=2, hours=1)).timestamp())
    item = {
        "deadline": deadline,
        "cutoff_date": cutoff_ts,
        "late_status": "late_allowed",
        "submission_status": "not_submitted",
        "type": "assignment",
    }
    cd_text, overdue, color, progress = get_card_countdown(item)
    assert "nộp muộn" in cd_text
    assert "2 ngày" in cd_text
    assert overdue is False
    assert color == C.WARNING
    assert progress > 0.0


def test_filter_service_groups_late_submission_into_critical():
    now = datetime.now()
    deadline = (now - timedelta(hours=3)).isoformat()
    cutoff_ts = int((now + timedelta(days=2)).timestamp())
    items = [
        {
            "id": "late_1",
            "course": "Math",
            "title": "Late Assignment",
            "deadline": deadline,
            "cutoff_date": cutoff_ts,
            "late_status": "late_allowed",
            "submission_status": "not_submitted",
            "urgency": "critical",
            "type": "assignment",
        },
        {
            "id": "regular_overdue",
            "course": "Math",
            "title": "Closed Assignment",
            "deadline": deadline,
            "cutoff_date": int((now - timedelta(hours=1)).timestamp()),
            "late_status": "closed",
            "submission_status": "not_submitted",
            "urgency": "overdue",
            "type": "assignment",
        },
    ]

    filtered_all, counts = FilterService.filter_and_count(items, active_urgency="all")
    assert any(a["id"] == "late_1" for a in filtered_all)
    assert not any(a["id"] == "regular_overdue" for a in filtered_all)
    assert counts["urgency"]["critical"] == 1
    assert counts["urgency"]["overdue"] == 1

    filtered_critical, _ = FilterService.filter_and_count(items, active_urgency="critical")
    assert len(filtered_critical) == 1
    assert filtered_critical[0]["id"] == "late_1"


def test_filter_keeps_future_assignment_safe_with_later_cutoff():
    now = datetime.now()
    item = {
        "id": "future_assignment",
        "course": "Math",
        "title": "Future Assignment",
        "deadline": (now + timedelta(days=7)).isoformat(),
        "cutoff_date": int((now + timedelta(days=9)).timestamp()),
        "late_status": "open",
        "submission_status": "not_submitted",
        "urgency": "safe",
        "type": "assignment",
    }

    filtered, counts = FilterService.filter_and_count([item], active_urgency="safe")

    assert filtered == [item]
    assert counts["urgency"]["safe"] == 1
    assert counts["urgency"]["critical"] == 0
