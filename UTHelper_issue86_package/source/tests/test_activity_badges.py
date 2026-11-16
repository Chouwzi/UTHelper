import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.core.theme import C
from gui.core.utils import get_submission_badge
from gui.components.activity_card import ActivityCard


@pytest.mark.parametrize(
    ("status", "expected_label", "expected_color"),
    [
        ("not_submitted", "Chưa làm", C.TEXT_SECONDARY),
        ("submitted", "Đã làm", C.SAFE),
        ("in_progress", "Đang làm", C.WARNING),
        ("overdue", "Quá hạn", C.CRITICAL),
        ("abandoned", "Chưa hoàn thành", C.WARNING),
        ("attempted", "Đã bắt đầu", C.WARNING),
    ],
)
def test_quiz_attempt_badges(status, expected_label, expected_color):
    assert get_submission_badge(
        {"type": "quiz", "submission_status": status, "details": {}}
    ) == (expected_label, expected_color)


def test_unknown_quiz_status_does_not_invent_a_badge():
    assert get_submission_badge(
        {"type": "quiz", "submission_status": "unknown", "details": {}}
    ) is None


def test_assignment_badge_wording_is_unchanged():
    assert get_submission_badge(
        {"type": "assignment", "submission_status": "not_submitted", "details": {}}
    ) == ("Chưa nộp", C.TEXT_SECONDARY)


@pytest.mark.parametrize("status", ["new", "Chưa nộp", "Not submitted"])
def test_assignment_badge_accepts_legacy_not_submitted_statuses(status):
    assert get_submission_badge(
        {"type": "assignment", "submission_status": status, "details": {}}
    ) == ("Chưa nộp", C.TEXT_SECONDARY)


def test_english_not_submitted_fallback_is_not_misread_as_submitted():
    assert get_submission_badge(
        {
            "type": "assignment",
            "submission_status": "unknown",
            "details": {
                "status_data": {"Submission status": "Not submitted"}
            },
        }
    ) == ("Chưa nộp", C.TEXT_SECONDARY)


def test_draft_status_has_an_explicit_badge():
    assert get_submission_badge(
        {"type": "assignment", "submission_status": "draft", "details": {}}
    ) == ("Bản nháp", C.WARNING)


def test_activity_card_renders_quiz_not_started_badge():
    card = ActivityCard(
        {
            "id": "quiz-1",
            "type": "quiz",
            "title": "Quiz",
            "submission_status": "not_submitted",
            "details": {},
        },
        on_tap=lambda _data: None,
    )

    assert len(card._optional_rows.controls) == 1
    assert card._optional_rows.controls[0].content.value == "Chưa làm"


def test_activity_card_rerenders_when_only_status_details_are_enriched():
    initial = {
        "id": "assignment-1",
        "type": "assignment",
        "title": "Assignment",
        "deadline": "2026-08-20 12:00:00",
        "submission_status": "unknown",
        "details": {},
    }
    card = ActivityCard(initial, on_tap=lambda _data: None)
    assert card._optional_rows.controls == []

    card.update_data(
        {
            **initial,
            "details": {
                "status_data": {"Trạng thái nộp bài": "Chưa nộp"}
            },
        }
    )

    assert card._optional_rows.controls[0].content.value == "Chưa nộp"
