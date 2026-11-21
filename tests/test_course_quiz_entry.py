"""Course quiz launch delegates to the app entry policy without replacing history."""

from unittest.mock import Mock

import flet as ft
import pytest

from core.courses.activity_service import QuizActivitySummary, QuizAttemptInfo
from core.courses.models import CourseModuleItem
from gui.components.courses.activity_views import QuizActivityView


def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)
    for item in getattr(control, "controls", ()):
        yield from _walk(item)


@pytest.mark.parametrize("state,limit,disabled,label", [
    ("finished", 1, True, "Mở bài"),
    ("inprogress", 1, False, "Tiếp tục"),
    ("overdue", 1, False, "Tiếp tục"),
    (None, 1, False, "Mở bài"),
    ("finished", 0, False, "Mở bài"),
])
def test_course_quiz_attempt_limit_keeps_existing_attempt_available(monkeypatch, state, limit, disabled, label):
    monkeypatch.setattr(QuizActivityView, "_load_async", lambda _self: None)
    start, web = Mock(), Mock()
    view = QuizActivityView(
        source="courses", course_id=42,
        module=CourseModuleItem(cmid=11, instance_id=99, modname="quiz", name="Quiz", section_id=1),
        activity_service=Mock(), on_close=Mock(), on_open_webview=web,
        on_start_quiz=start,
    )
    view._render_quiz(QuizActivitySummary(
        cmid=11, quiz_id=99, title="Quiz", max_attempts=limit,
        web_url="https://courses.ut.edu.vn/mod/quiz/view.php?id=11",
        attempts=(QuizAttemptInfo(attempt_number=1, attempt_id=501, state=state),) if state is not None else (),
    ))
    button = next(item for item in _walk(view) if isinstance(item, ft.FilledButton) and item.content == label)
    assert button.disabled is disabled
    if disabled:
        assert button.on_click is None
    else:
        button.on_click(None)
        start.assert_called_once_with()
    web.assert_not_called()
    assert view._summary.cmid == 11
    assert view._summary.quiz_id == 99
