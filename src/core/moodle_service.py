from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional, Dict, Any, List, Iterable
import logging
from core import ws_functions

logger = logging.getLogger(__name__)

class MoodleService:
    """Lớp dịch vụ đóng gói các hành động gọi Web Service Moodle và cơ chế lưu cache."""

    def __init__(self, call_ws_api: Callable):
        self.call_ws_api = call_ws_api

    def get_site_info(self) -> Optional[Dict[str, Any]]:
        """Lấy thông tin chung của tài khoản và cấu hình hệ thống e-learning."""
        return ws_functions.get_site_info(self.call_ws_api)

    def get_current_user_id(self) -> Optional[int]:
        """Lấy Moodle user id hiện tại từ site info."""
        info = self.get_site_info()
        if not info:
            return None
        try:
            return int(info.get("userid"))
        except (TypeError, ValueError):
            return None

    def get_calendar_action_events(self, time_start: int = 0) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các sự kiện lịch cần xử lý (action events) kể từ mốc thời gian quy định."""
        return ws_functions.get_calendar_action_events(self.call_ws_api, time_start)

    def get_action_events_by_timesort(
        self,
        timesort_from: int,
        timesort_to: int,
        limit: int = 50,
        after_event_id: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """Lấy action events theo khoảng thời gian, giữ nguyên shape trả về của Moodle."""
        params = {
            "timesortfrom": timesort_from,
            "timesortto": timesort_to,
            "limitnum": limit,
        }
        if after_event_id > 0:
            params["aftereventid"] = after_event_id
        return self.call_ws_api(
            "core_calendar_get_action_events_by_timesort",
            **params,
        )

    def get_enrolled_courses(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các môn học mà sinh viên đang đăng ký học."""
        return ws_functions.get_enrolled_courses(self.call_ws_api)

    def get_user_courses(self, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách môn học của một user cụ thể."""
        return self.call_ws_api("core_enrol_get_users_courses", userid=userid)

    def get_assignments(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Lấy danh sách tất cả các bài tập trong các môn học được cung cấp."""
        return ws_functions.get_assignments(self.call_ws_api, course_ids)

    def get_calendar_events(self, time_start: int, course_ids: List[int]) -> Optional[List[Dict[str, Any]]]:
        """Lấy các sự kiện lịch chung thuộc về các môn học được chọn."""
        return ws_functions.get_calendar_events(self.call_ws_api, time_start, course_ids)

    def get_submission_status(self, assign_id: int) -> Optional[Dict[str, Any]]:
        """Lấy trạng thái nộp bài chi tiết của sinh viên cho bài tập cụ thể."""
        return ws_functions.get_submission_status(self.call_ws_api, assign_id)

    def get_submitted_files(
        self,
        assign_id: int,
        status: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách các tệp tin sinh viên đã nộp lên hệ thống cho bài tập cụ thể."""
        return ws_functions.get_submitted_files(self.call_ws_api, assign_id, status=status)

    def get_quizzes_by_courses(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Lấy danh sách các hoạt động trắc nghiệm (quiz) của các môn học được chỉ định."""
        return ws_functions.get_quizzes_by_courses(self.call_ws_api, course_ids)

    def get_quiz_attempts(self, quiz_id: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các lượt làm bài trắc nghiệm của sinh viên."""
        return ws_functions.get_quiz_attempts(self.call_ws_api, quiz_id)

    def clear_all_caches(self):
        """Xóa toàn bộ bộ nhớ cache lưu tạm của các hàm Web Service."""
        ws_functions.clear_all_caches()

    def resolve_cmid_to_assign_id(self, cmid: int, course_id: int) -> Optional[int]:
        """Chuyển đổi ID hoạt động (cmid) sang ID bài nộp (Assignment ID) tương ứng."""
        return ws_functions.resolve_cmid_to_assign_id(self.call_ws_api, cmid, course_id)

    def get_assign_details_via_ws(
        self,
        cmid: int,
        course_id: int,
        modulename: str = "assign",
    ) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết cấu hình và mô tả của bài tập thông qua Web Service."""
        return ws_functions.get_assign_details_via_ws(
            self.call_ws_api,
            cmid,
            course_id,
            modulename,
        )

    def upload_file_to_draft(self, file_path: str, draft_id: int = 0) -> int:
        """Tải một tệp tin lên vùng nháp (draft area) trên server Moodle."""
        return ws_functions.upload_file_to_draft(self.call_ws_api, file_path, draft_id)

    def get_unused_draft_itemid(self) -> Optional[int]:
        """Allocate a separate Moodle draft area for an editor field."""
        try:
            result = self.call_ws_api("core_files_get_unused_draft_itemid")
            itemid = result.get("itemid") if isinstance(result, dict) else None
            if isinstance(itemid, bool) or itemid is None:
                return None
            parsed_itemid = int(itemid)
            return parsed_itemid if parsed_itemid > 0 else None
        except (TypeError, ValueError):
            return None
        except Exception as exc:
            logger.warning("Could not allocate Moodle draft area: %s", exc)
            return None

    def save_assignment_submission_result(
        self,
        assign_id: int,
        draft_itemid: int,
        online_text: str,
        online_text_format: int,
        text_draft_itemid: int,
    ) -> ws_functions.MoodleActionResult:
        """Save a submission with the snapshot text and its own editor draft ID."""
        return ws_functions.save_assignment_submission_result(
            self.call_ws_api,
            assign_id,
            draft_itemid,
            online_text,
            online_text_format,
            text_draft_itemid,
        )

    def save_assignment_submission(self, assign_id: int, draft_id: int, onlinetext: str = "", item_id_text: int = 0) -> bool:
        """Lưu bài nộp (bao gồm cả tệp nháp hoặc bài viết trực tuyến) vào hệ thống."""
        return self.save_assignment_submission_result(
            assign_id, draft_id, onlinetext, 1, item_id_text
        ).ok

    def submit_for_grading_result(
        self, assign_id: int, accept_submission_statement: bool
    ) -> ws_functions.MoodleActionResult:
        """Finalize a saved submission with an explicit statement choice."""
        return ws_functions.submit_for_grading_result(
            self.call_ws_api, assign_id, accept_submission_statement
        )

    def submit_for_grading(self, assign_id: int) -> bool:
        """Xác nhận nộp bài chính thức để giảng viên chấm điểm (submit for grading)."""
        return self.submit_for_grading_result(assign_id, True).ok

    def delete_draft_files(
        self, itemid: int, identities: Iterable[tuple[str, str]]
    ) -> bool:
        """Delete exactly the uploaded draft identities and require Moodle confirmation."""
        params: dict[str, Any] = {"draftitemid": itemid}
        for index, (filepath, filename) in enumerate(identities):
            params[f"files[{index}][filepath]"] = filepath
            params[f"files[{index}][filename]"] = filename
        if len(params) == 1:
            return True
        try:
            result = self.call_ws_api("core_files_delete_draft_files", **params)
        except Exception as exc:
            logger.warning("Could not delete Moodle draft files: %s", exc)
            return False
        return isinstance(result, dict) and "parentpaths" in result

    def ws_events_to_assignments(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Chuyển Moodle calendar events sang activity dictionaries của UTHelper."""
        return ws_functions.ws_events_to_assignments(events)

    def get_course_grades(self, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy điểm tổng quan của sinh viên đối với các môn học tham gia."""
        return ws_functions.get_course_grades(self.call_ws_api, userid)

    def get_grade_items(self, courseid: int, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy chi tiết từng cột điểm và đánh giá cụ thể trong một môn học."""
        return ws_functions.get_grade_items(self.call_ws_api, courseid, userid)

    def fetch_all_grades(self, userid: int, max_workers: int = 10) -> tuple[list, dict]:
        """Tải điểm tổng quan và chi tiết từng môn song song."""
        courses_grades = self.get_course_grades(userid)
        grade_items = {}

        if not courses_grades:
            return [], {}

        def fetch_single_course_grades(course_grade):
            course_id = str(course_grade.get("courseid", ""))
            if not course_id:
                return None
            try:
                items = self.get_grade_items(course_id, userid)
                return course_id, items
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(fetch_single_course_grades, cg) for cg in courses_grades]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    course_id, items = result
                    grade_items[course_id] = items

        return courses_grades, grade_items

    def get_unread_notification_count(self, userid: int) -> int:
        """Lấy số lượng thông báo Moodle chưa đọc của sinh viên."""
        return ws_functions.get_unread_notification_count(self.call_ws_api, userid)

    def get_course_updates_since(self, courseid: int, since: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các hoạt động thay đổi trong môn học kể từ một mốc thời gian (phục vụ Smart polling)."""
        return ws_functions.get_course_updates_since(self.call_ws_api, courseid, since)

    def get_course_contents(self, courseid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy nội dung toàn bộ bài học, tài liệu giảng dạy trong môn học."""
        return ws_functions.get_course_contents(self.call_ws_api, courseid)
