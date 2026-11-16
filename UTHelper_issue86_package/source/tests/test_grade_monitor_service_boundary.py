import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.grade_monitor import GradeMonitor


class _FakeGradeService:
    def __init__(self):
        self.item_calls = []

    def get_course_grades(self, userid):
        return [{"courseid": 1, "coursename": "Web", "grade": "8.0"}]

    def get_grade_items(self, courseid, userid):
        self.item_calls.append((courseid, userid))
        return [{"itemname": "CK", "gradeformatted": "9.0"}]


def test_grade_monitor_accepts_moodle_service_like_boundary(tmp_path):
    monitor = GradeMonitor(snapshot_path=str(tmp_path / "grades.json"))
    monitor._snapshot = {"1": {"_overall": "7.0", "CK": "6.0"}}
    service = _FakeGradeService()

    changes = monitor.check_for_changes(service, userid=42)

    assert service.item_calls == [(1, 42)]
    assert {change.item_name for change in changes} == {"Tổng kết", "CK"}
