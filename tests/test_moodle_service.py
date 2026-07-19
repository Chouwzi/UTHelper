import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.moodle_service import MoodleService


def test_get_current_user_id_from_site_info(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)

    monkeypatch.setattr(service, "get_site_info", lambda: {"userid": "42"})

    assert service.get_current_user_id() == 42


def test_get_current_user_id_returns_none_for_missing_user(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)

    monkeypatch.setattr(service, "get_site_info", lambda: {})

    assert service.get_current_user_id() is None


def test_fetch_all_grades_loads_course_items(monkeypatch):
    service = MoodleService(lambda *args, **kwargs: None)
    courses = [{"courseid": 10, "coursename": "Math"}, {"courseid": 20, "coursename": "Physics"}]

    monkeypatch.setattr(service, "get_course_grades", lambda userid: courses)
    monkeypatch.setattr(
        service,
        "get_grade_items",
        lambda courseid, userid: [{"courseid": courseid, "grade": "10"}],
    )

    courses_grades, grade_items = service.fetch_all_grades(userid=7, max_workers=2)

    assert courses_grades == courses
    assert set(grade_items) == {"10", "20"}
    assert grade_items["10"] == [{"courseid": "10", "grade": "10"}]

