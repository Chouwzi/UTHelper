import logging
from typing import Optional

from core.moodle_service import MoodleService

logger = logging.getLogger(__name__)


class GradeRefreshService:
    """Use case tải dữ liệu điểm để UI chỉ còn điều phối hiển thị."""

    def __init__(self, moodle_service: MoodleService):
        self._moodle_service = moodle_service

    def fetch_all_grades(self, userid: int, max_workers: int = 10) -> tuple[list, dict]:
        return self._moodle_service.fetch_all_grades(userid, max_workers=max_workers)

    def resolve_user_id(self, cached_userid: Optional[int] = None) -> Optional[int]:
        if cached_userid is not None:
            return cached_userid
        return self._moodle_service.get_current_user_id()

    def get_unread_notification_count(self, cached_userid: Optional[int] = None) -> int:
        userid = self.resolve_user_id(cached_userid)
        if not userid:
            return 0
        return self._moodle_service.get_unread_notification_count(userid)
