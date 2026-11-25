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


def test_completed_activity_card_uses_calm_border_and_hides_redundant_chrome():
    completed = ActivityCard(
        {
            "id": "quiz-done",
            "type": "quiz",
            "title": "Quiz đã hoàn thành",
            "deadline": "2098-12-20 12:00:00",
            "urgency": "safe",
            "is_open": True,
            "submission_status": "submitted",
            "details": {},
        },
        on_tap=lambda _data: None,
    )
    active = ActivityCard(
        {
            "id": "assign-todo",
            "type": "assignment",
            "title": "Bài tập chưa nộp",
            "deadline": "2098-12-20 12:00:00",
            "urgency": "safe",
            "is_open": True,
            "submission_status": "not_submitted",
            "details": {},
        },
        on_tap=lambda _data: None,
    )

    # Completed items use calm neutral card border instead of shouting C.SAFE,
    # hide redundant urgency/progress chrome, and use soft tinted pills.
    assert completed.border.top.color == C.BORDER
    assert completed._urgency_container.visible is False
    assert completed._progress_ctrl.visible is False
    assert completed._status_container.visible is False
    assert completed._type_container.border is None
    assert completed._optional_rows.controls[0].border is None
    assert completed._optional_rows.controls[0].content.value == "Đã làm"

    # Active unsubmitted items keep urgency pill and progress indicator visible,
    # while hiding redundant "ĐANG MỞ" duplicate status pill.
    assert active.border.top.color == C.BORDER
    assert active._urgency_container.visible is True
    assert active._progress_ctrl.visible is True
    assert active._status_container.visible is False
    assert active._optional_rows.controls[0].content.value == "Chưa nộp"


def test_activity_card_hover_and_selected_state_transitions():
    from types import SimpleNamespace

    card = ActivityCard(
        {
            "id": "quiz-hover",
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=101",
            "type": "quiz",
            "title": "Quiz hover & selection",
            "deadline": "2098-12-20 12:00:00",
            "urgency": "safe",
            "is_open": True,
            "submission_status": "not_submitted",
            "details": {},
        },
        on_tap=lambda _data: None,
    )

    assert card.bgcolor == C.SURFACE
    assert card._selected_badge.visible is False
    assert card._urgency_container.visible is True

    # Pointer hover elevates surface and border without marking card as open.
    card.on_hover(SimpleNamespace(data="true"))
    assert card.bgcolor == C.SURFACE_HOVER
    assert card.shadow is not None
    assert card._selected_badge.visible is False

    # Selecting card (when opened in DetailView) shows "Đang xem" pill and C.ACCENT frame.
    assert card.set_selected(True) is True
    assert card.bgcolor == C.SURFACE_HOVER
    assert card.border.top.color == C.ACCENT
    assert card._bar_widget.bgcolor == C.ACCENT
    assert card._selected_badge.visible is True
    assert card._urgency_container.visible is False

    # Leaving hover while selected preserves the active selection chrome.
    card.on_hover(SimpleNamespace(data="false"))
    assert card._selected_badge.visible is True
    assert card.border.top.color == C.ACCENT

    # Closing DetailView clears selection and restores resting chrome.
    assert card.set_selected(False) is True
    assert card.bgcolor == C.SURFACE
    assert card.border.top.color == C.BORDER
    assert card._selected_badge.visible is False
    assert card._urgency_container.visible is True
