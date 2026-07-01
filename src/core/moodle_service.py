from typing import Callable, Optional, Dict, Any, List
from core import ws_functions

class MoodleService:
    """Lớp dịch vụ đóng gói các hành động gọi Web Service Moodle và cơ chế lưu cache."""

    def __init__(self, call_ws_api: Callable):
        self.call_ws_api = call_ws_api

    def get_site_info(self) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chung của tài khoản và cấu hình hệ thống e-learning."""
        return ws_functions.get_site_info(self.call_ws_api)

    def get_calendar_action_events(self, time_start: int = 0) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các sự kiện lịch cần xử lý (action events) kể từ mốc thời gian quy định."""
        return ws_functions.get_calendar_action_events(self.call_ws_api, time_start)

    def get_enrolled_courses(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các môn học mà sinh viên đang đăng ký học."""
        return ws_functions.get_enrolled_courses(self.call_ws_api)

    def get_assignments(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Lấy danh sách tất cả các bài tập trong các môn học được cung cấp."""
        return ws_functions.get_assignments(self.call_ws_api, course_ids)

    def get_calendar_events(self, time_start: int, course_ids: List[int]) -> Optional[List[Dict[str, Any]]]:
        """Lấy các sự kiện lịch chung thuộc về các môn học được chọn."""
        return ws_functions.get_calendar_events(self.call_ws_api, time_start, course_ids)

    def get_submission_status(self, assign_id: int) -> Optional[Dict[str, Any]]:
        """Lấy trạng thái nộp bài chi tiết của sinh viên cho bài tập cụ thể."""
        return ws_functions.get_submission_status(self.call_ws_api, assign_id)

    def get_submitted_files(self, assign_id: int) -> List[Dict[str, Any]]:
        """Lấy danh sách các tệp tin sinh viên đã nộp lên hệ thống cho bài tập cụ thể."""
        return ws_functions.get_submitted_files(self.call_ws_api, assign_id)

    def get_quizzes_by_courses(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Lấy danh sách các hoạt động trắc nghiệm (quiz) của các môn học được chỉ định."""
        return ws_functions.get_quizzes_by_courses(self.call_ws_api, course_ids)

    def get_quiz_attempts(self, quiz_id: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các lượt làm bài trắc nghiệm của sinh viên."""
        return ws_functions.get_quiz_attempts(self.call_ws_api, quiz_id)

    def clear_all_caches(self):
        """Xóa toàn bộ bộ nhớ cache lưu tạm của các hàm Web Service."""
        ws_functions.clear_all_caches()

    def resolve_cmid_to_assign_id(self, cmid: int) -> Optional[int]:
        """Chuyển đổi ID hoạt động (cmid) sang ID bài nộp (Assignment ID) tương ứng."""
        return ws_functions.resolve_cmid_to_assign_id(self.call_ws_api, cmid)

    def get_assign_details_via_ws(self, cmid: int) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết cấu hình và mô tả của bài tập thông qua Web Service."""
        return ws_functions.get_assign_details_via_ws(self.call_ws_api, cmid)

    def upload_file_to_draft(self, file_path: str, draft_id: int = 0) -> int:
        """Tải một tệp tin lên vùng nháp (draft area) trên server Moodle."""
        return ws_functions.upload_file_to_draft(self.call_ws_api, file_path, draft_id)

    def save_assignment_submission(self, assign_id: int, draft_id: int, onlinetext: str = "", item_id_text: int = 0) -> bool:
        """Lưu bài nộp (bao gồm cả tệp nháp hoặc bài viết trực tuyến) vào hệ thống."""
        return ws_functions.save_assignment_submission(self.call_ws_api, assign_id, draft_id, onlinetext, item_id_text)

    def submit_for_grading(self, assign_id: int) -> bool:
        """Xác nhận nộp bài chính thức để giảng viên chấm điểm (submit for grading)."""
        return ws_functions.submit_for_grading(self.call_ws_api, assign_id)

    def get_course_grades(self, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy điểm tổng quan của sinh viên đối với các môn học tham gia."""
        return ws_functions.get_course_grades(self.call_ws_api, userid)

    def get_grade_items(self, courseid: int, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy chi tiết từng cột điểm và đánh giá cụ thể trong một môn học."""
        return ws_functions.get_grade_items(self.call_ws_api, courseid, userid)

    def get_unread_notification_count(self, userid: int) -> int:
        """Lấy số lượng thông báo Moodle chưa đọc của sinh viên."""
        return ws_functions.get_unread_notification_count(self.call_ws_api, userid)

    def get_course_updates_since(self, courseid: int, since: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các hoạt động thay đổi trong môn học kể từ một mốc thời gian (phục vụ Smart polling)."""
        return ws_functions.get_course_updates_since(self.call_ws_api, courseid, since)

    def get_course_contents(self, courseid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy nội dung toàn bộ bài học, tài liệu giảng dạy trong môn học."""
        return ws_functions.get_course_contents(self.call_ws_api, courseid)
