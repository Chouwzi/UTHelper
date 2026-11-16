import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.use_cases.grade_refresh import GradeRefreshService


class _FakeMoodleService:
    def __init__(self):
        self.current_user_id = 99
        self.unread_count = 3
        self.fetch_args = None

    def fetch_all_grades(self, userid: int, max_workers: int = 10):
        self.fetch_args = (userid, max_workers)
        return [{"courseid": 1}], {"1": []}

    def get_current_user_id(self):
        return self.current_user_id

    def get_unread_notification_count(self, userid: int):
        return self.unread_count if userid == self.current_user_id else 0


def test_fetch_all_grades_delegates_to_moodle_service():
    moodle = _FakeMoodleService()
    service = GradeRefreshService(moodle)

    result = service.fetch_all_grades(userid=99, max_workers=4)

    assert result == ([{"courseid": 1}], {"1": []})
    assert moodle.fetch_args == (99, 4)


def test_get_unread_notification_count_uses_cached_userid():
    moodle = _FakeMoodleService()
    service = GradeRefreshService(moodle)

    assert service.get_unread_notification_count(cached_userid=99) == 3


def test_get_unread_notification_count_resolves_userid_when_missing():
    moodle = _FakeMoodleService()
    service = GradeRefreshService(moodle)

    assert service.get_unread_notification_count() == 3

