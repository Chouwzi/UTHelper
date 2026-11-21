"""Attempt-budget display uses server data without blocking resumable attempts."""

from unittest.mock import Mock

import pytest

from core import ws_functions
from core.data_orchestrator import DataOrchestrator


@pytest.mark.parametrize(
    ("limit", "states", "remaining", "unfinished"),
    [
        (1, ["finished"], 0, False),
        (1, ["finished", "inprogress"], 0, True),
        (2, ["finished", "overdue"], 0, True),
        (3, ["finished"], 2, False),
        (0, ["finished", "inprogress"], None, True),
        (2, [], 2, False),
    ],
)
def test_quiz_detail_exposes_authoritative_attempt_budget(
    monkeypatch, limit, states, remaining, unfinished
):
    monkeypatch.setattr(ws_functions, "_fill_course_name", lambda *_: None)
    monkeypatch.setattr(
        ws_functions, "get_quizzes_by_courses",
        lambda *_: [{"id": 88, "coursemodule": 52, "attempts": limit}],
    )
    monkeypatch.setattr(
        ws_functions, "get_quiz_attempts",
        lambda *_: [{"state": state} for state in states],
    )

    details = ws_functions._get_quiz_detail(
        Mock(), 52, 7, {"quiz_info": [], "status_data": {}}
    )

    assert details["quiz_attempts_remaining"] == remaining
    assert details["quiz_has_unfinished_attempt"] is unfinished


@pytest.mark.parametrize("attempts", [None, [{"state": "unknown"}]])
def test_unknown_attempt_response_does_not_claim_exhaustion(monkeypatch, attempts):
    monkeypatch.setattr(ws_functions, "_fill_course_name", lambda *_: None)
    monkeypatch.setattr(
        ws_functions, "get_quizzes_by_courses",
        lambda *_: [{"id": 88, "coursemodule": 52, "attempts": 1}],
    )
    monkeypatch.setattr(ws_functions, "get_quiz_attempts", lambda *_: attempts)

    details = ws_functions._get_quiz_detail(
        Mock(), 52, 7, {"quiz_info": [], "status_data": {}}
    )

    assert "quiz_attempts_remaining" not in details


def test_attempt_budget_reaches_the_display_data_path():
    orchestrator = DataOrchestrator()
    orchestrator.moodle_service = Mock()
    orchestrator.moodle_service.get_assign_details_via_ws.return_value = {
        "quiz_attempts_remaining": 0,
        "quiz_has_unfinished_attempt": True,
        "quiz_info": ["Lần 1: Đang làm"],
    }

    result = orchestrator._fetch_detail_via_ws({
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=52",
        "course_id": 7,
        "type": "quiz",
        "details": {},
    })

    assert result["details"]["quiz_attempts_remaining"] == 0
    assert result["details"]["quiz_has_unfinished_attempt"] is True


def test_numeric_quiz_id_fallback_does_not_disable_an_unresolved_module(monkeypatch):
    monkeypatch.setattr(ws_functions, "_fill_course_name", lambda *_: None)
    monkeypatch.setattr(
        ws_functions, "get_quizzes_by_courses",
        lambda *_: [{"id": 52, "coursemodule": 999, "attempts": 1}],
    )
    monkeypatch.setattr(
        ws_functions, "get_quiz_attempts", lambda *_: [{"state": "finished"}]
    )

    details = ws_functions._get_quiz_detail(
        Mock(), 52, 7, {"quiz_info": [], "status_data": {}}
    )

    assert "quiz_attempts_remaining" not in details
    assert "quiz_has_unfinished_attempt" not in details
