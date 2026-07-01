from typing import Callable, Optional, Dict, Any, List
from core import ws_functions

class MoodleService:
    """Service encapsulating Moodle web service actions and caching."""

    def __init__(self, call_ws_api: Callable):
        self.call_ws_api = call_ws_api

    def get_site_info(self) -> Optional[Dict[str, Any]]:
        return ws_functions.get_site_info(self.call_ws_api)

    def get_calendar_action_events(self, time_start: int = 0) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_calendar_action_events(self.call_ws_api, time_start)

    def get_enrolled_courses(self) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_enrolled_courses(self.call_ws_api)

    def get_assignments(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        return ws_functions.get_assignments(self.call_ws_api, course_ids)

    def get_calendar_events(self, time_start: int, course_ids: List[int]) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_calendar_events(self.call_ws_api, time_start, course_ids)

    def get_submission_status(self, assign_id: int) -> Optional[Dict[str, Any]]:
        return ws_functions.get_submission_status(self.call_ws_api, assign_id)

    def get_submitted_files(self, assign_id: int) -> List[Dict[str, Any]]:
        return ws_functions.get_submitted_files(self.call_ws_api, assign_id)

    def get_quizzes_by_courses(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        return ws_functions.get_quizzes_by_courses(self.call_ws_api, course_ids)

    def get_quiz_attempts(self, quiz_id: int) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_quiz_attempts(self.call_ws_api, quiz_id)

    def clear_all_caches(self):
        ws_functions.clear_all_caches()

    def resolve_cmid_to_assign_id(self, cmid: int) -> Optional[int]:
        return ws_functions.resolve_cmid_to_assign_id(self.call_ws_api, cmid)

    def get_assign_details_via_ws(self, cmid: int) -> Optional[Dict[str, Any]]:
        return ws_functions.get_assign_details_via_ws(self.call_ws_api, cmid)

    def upload_file_to_draft(self, file_path: str, draft_id: int = 0) -> int:
        return ws_functions.upload_file_to_draft(self.call_ws_api, file_path, draft_id)

    def save_assignment_submission(self, assign_id: int, draft_id: int, onlinetext: str = "", item_id_text: int = 0) -> bool:
        return ws_functions.save_assignment_submission(self.call_ws_api, assign_id, draft_id, onlinetext, item_id_text)

    def submit_for_grading(self, assign_id: int) -> bool:
        return ws_functions.submit_for_grading(self.call_ws_api, assign_id)

    def get_course_grades(self, userid: int) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_course_grades(self.call_ws_api, userid)

    def get_grade_items(self, courseid: int, userid: int) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_grade_items(self.call_ws_api, courseid, userid)

    def get_unread_notification_count(self, userid: int) -> int:
        return ws_functions.get_unread_notification_count(self.call_ws_api, userid)

    def get_course_updates_since(self, courseid: int, since: int) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_course_updates_since(self.call_ws_api, courseid, since)

    def get_course_contents(self, courseid: int) -> Optional[List[Dict[str, Any]]]:
        return ws_functions.get_course_contents(self.call_ws_api, courseid)
