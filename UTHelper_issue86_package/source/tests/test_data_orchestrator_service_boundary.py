import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.data_orchestrator import DataOrchestrator


class _FakeMoodleService:
    def __init__(self):
        self.update_calls = []
        self.detail_calls = []

    def get_site_info(self):
        return {"userid": 42}

    def get_enrolled_courses(self):
        return [{"id": 101}, {"id": 202}]

    def get_course_updates_since(self, courseid, since):
        self.update_calls.append((courseid, since))
        return [{"name": "changed"}] if courseid == 202 else []

    def get_assign_details_via_ws(self, cmid, course_id, modulename="assign"):
        self.detail_calls.append((cmid, course_id, modulename))
        return {
            "description_html": "<p>Chi tiết</p>",
            "status_data": {"Trạng thái nộp bài": "Đã nộp"},
            "course_full_name": "Kiến trúc phần mềm",
        }


def test_get_updates_since_uses_moodle_service_boundary():
    orchestrator = DataOrchestrator()
    fake_service = _FakeMoodleService()
    orchestrator.moodle_service = fake_service

    assert orchestrator.get_updates_since(1000) == [202]
    assert fake_service.update_calls == [(101, 1000), (202, 1000)]


def test_fetch_detail_via_ws_uses_moodle_service_boundary():
    orchestrator = DataOrchestrator()
    fake_service = _FakeMoodleService()
    orchestrator.moodle_service = fake_service

    result = orchestrator._fetch_detail_via_ws(
        {
            "url": "https://example.test/mod/assign/view.php?id=77",
            "type": "assignment",
            "course_id": "12",
            "details": {},
        }
    )

    assert fake_service.detail_calls == [(77, 12, "assign")]
    assert result["details"]["description_html"] == "<p>Chi tiết</p>"
    assert result["submission_status"] == "submitted"


def test_fetch_quiz_detail_promotes_normalized_attempt_status():
    orchestrator = DataOrchestrator()
    fake_service = _FakeMoodleService()
    fake_service.get_assign_details_via_ws = lambda **_kwargs: {
        "status_data": {},
        "quiz_info": [],
        "quiz_attempt_status": "not_submitted",
    }
    orchestrator.moodle_service = fake_service

    result = orchestrator._fetch_detail_via_ws(
        {
            "url": "https://example.test/mod/quiz/view.php?id=77",
            "type": "quiz",
            "course_id": "12",
            "submission_status": "unknown",
            "details": {},
        }
    )

    assert result["submission_status"] == "not_submitted"
    assert result["details"]["quiz_attempt_status"] == "not_submitted"


def test_fetch_non_assignment_card_uses_its_real_moodle_module():
    fake_service = _FakeMoodleService()
    calls = []

    def fetch_details(**kwargs):
        calls.append(kwargs)
        return {"course_full_name": "Network Security"}

    fake_service.get_assign_details_via_ws = fetch_details
    orchestrator = DataOrchestrator.__new__(DataOrchestrator)
    orchestrator.moodle_service = fake_service

    result = orchestrator._fetch_detail_via_ws(
        {
            "url": "https://courses.test/mod/attendance/view.php?id=77",
            "course_id": 12,
            "type": "attendance",
            "details": {},
        }
    )

    assert result["details"]["course_full_name"] == "Network Security"
    assert calls == [{
        "cmid": 77,
        "course_id": 12,
        "modulename": "attendance",
    }]
