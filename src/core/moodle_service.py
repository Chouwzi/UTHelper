import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List, Iterable
from core import ws_functions

logger = logging.getLogger(__name__)

class MoodleService:
    """Lớp dịch vụ đóng gói các hành động gọi Web Service Moodle và cơ chế lưu cache."""

    def __init__(
        self,
        call_ws_api: Callable,
        grade_report_fetcher: Optional[Callable[[int], Optional[str]]] = None,
        grade_report_error_getter: Optional[Callable[[], str]] = None,
        *,
        client: Any = None,
    ):
        self.call_ws_api = call_ws_api
        self._client = client if client is not None else getattr(call_ws_api, "__self__", None)
        self._grade_report_fetcher = grade_report_fetcher
        self._grade_report_error_getter = grade_report_error_getter

    @property
    def moodle_site_origin(self) -> Optional[str]:
        if self._client is None:
            return None
        return getattr(self._client, "moodle_site_origin", None)

    @property
    def last_grade_report_error(self) -> str:
        """Last error recorded when fetching a Moodle HTML grade report."""
        if callable(self._grade_report_error_getter):
            try:
                return str(self._grade_report_error_getter() or "")
            except Exception:
                return ""
        return ""

    def fetch_grade_report_html(self, course_id: int) -> Optional[str]:
        """Fetch /grade/report/user/index.php?id=<course_id> HTML via bound client."""
        if not callable(self._grade_report_fetcher):
            return None
        return self._grade_report_fetcher(course_id)

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

    def get_enrolled_courses(self, classification: str = "all") -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách các môn học mà sinh viên đang đăng ký học."""
        return ws_functions.get_enrolled_courses(self.call_ws_api, classification)

    def get_user_courses(self, userid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy danh sách môn học của một user cụ thể."""
        return self.call_ws_api("core_enrol_get_users_courses", userid=userid)

    def get_assignments(self, course_ids: List[int]) -> Optional[Dict[str, Any]]:
        """Lấy danh sách tất cả các bài tập trong các môn học được cung cấp."""
        return ws_functions.get_assignments(self.call_ws_api, course_ids)

    def get_calendar_events(
        self,
        time_start: int,
        time_end: int,
        course_ids: List[int],
    ) -> Optional[List[Dict[str, Any]]]:
        """Lấy các sự kiện lịch chung thuộc về các môn học được chọn."""
        return ws_functions.get_calendar_events(
            self.call_ws_api,
            time_start,
            time_end,
            course_ids,
        )

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

    def get_quizzes_by_courses(self, course_ids: List[int]) -> Optional[List[Dict[str, Any]]]:
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

    def upload_file_to_draft(
        self,
        file_path: str,
        draft_id: int = 0,
        *,
        user_id: Optional[int] = None,
    ) -> Optional[int]:
        """Tải một tệp tin lên vùng nháp (draft area) trên server Moodle."""
        try:
            path = Path(file_path)
            raw_bytes = path.read_bytes()
        except (OSError, TypeError, ValueError) as exc:
            logger.error("Không thể đọc file '%s' để upload nháp: %s", file_path, exc)
            return None

        resolved_user_id = user_id if user_id is not None else self.get_current_user_id()
        if not isinstance(resolved_user_id, int) or isinstance(resolved_user_id, bool) or resolved_user_id <= 0:
            logger.error("Không xác định được Moodle user_id để upload file '%s'", path.name)
            return None

        encoded = base64.b64encode(raw_bytes).decode("ascii")
        return ws_functions.upload_file_to_draft(
            self.call_ws_api,
            path.name,
            encoded,
            resolved_user_id,
            draft_id,
        )

    def upload_draft_file_record(
        self,
        filename: str,
        content: bytes,
        *,
        itemid: int = 0,
        filepath: str = "/",
    ) -> Any:
        """Upload raw bytes to a Moodle draft area via the bound client."""
        if self._client is None or not hasattr(self._client, "upload_draft_file_record"):
            return None
        return self._client.upload_draft_file_record(
            filename,
            content,
            itemid=itemid,
            filepath=filepath,
        )

    def download_file(self, url: str) -> Optional[bytes]:
        """Download an authenticated Moodle file payload via the bound client."""
        if self._client is None or not hasattr(self._client, "download_file"):
            return None
        return self._client.download_file(url)

    def get_user_id(self, *, refresh: bool = False) -> Optional[int]:
        """Return the active Moodle user ID, refreshing from server when requested."""
        if self._client is not None and hasattr(self._client, "get_user_id"):
            return self._client.get_user_id(refresh=refresh)
        return self.get_current_user_id()

    def remove_assignment_submission(
        self,
        cmid: int,
        *,
        expected_user_id: int,
        before_commit: Optional[Callable[[], bool]] = None,
    ) -> bool:
        """Remove an editable Moodle assignment submission via the bound client."""
        if self._client is None or not hasattr(self._client, "remove_assignment_submission"):
            return False
        return bool(
            self._client.remove_assignment_submission(
                cmid,
                expected_user_id=expected_user_id,
                before_commit=before_commit,
            )
        )

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

    def save_assignment_submission(
        self,
        assign_id: int,
        draft_id: int,
        online_text: Optional[str] = None,
        online_text_format: Optional[int] = None,
        text_draft_itemid: Optional[int] = None,
    ) -> bool:
        """Compatibility wrapper that refuses a save without a captured text snapshot."""
        if online_text is None or online_text_format is None or text_draft_itemid is None:
            return False
        return self.save_assignment_submission_result(
            assign_id, draft_id, online_text, online_text_format, text_draft_itemid
        ).ok

    def submit_for_grading_result(
        self, assign_id: int, accept_submission_statement: bool
    ) -> ws_functions.MoodleActionResult:
        """Finalize a saved submission with an explicit statement choice."""
        return ws_functions.submit_for_grading_result(
            self.call_ws_api, assign_id, accept_submission_statement
        )

    def submit_for_grading(
        self, assign_id: int, accept_submission_statement: bool = False
    ) -> bool:
        """Compatibility wrapper that only finalizes after explicit acceptance."""
        if not accept_submission_statement:
            return False
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
        return (
            isinstance(result, dict)
            and "parentpaths" in result
            and not result.get("warnings")
        )

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

    def get_pages_by_courses(self, courseid: int) -> Optional[List[Dict[str, Any]]]:
        """Lấy nội dung HTML chi tiết các mod_page trong môn học."""
        return ws_functions.get_pages_by_courses(self.call_ws_api, courseid)

    def set_activity_completion_manually(self, cmid: int, completed: bool) -> bool:
        """Cập nhật trạng thái hoàn thành thủ công của module."""
        return ws_functions.update_activity_completion_manually(
            self.call_ws_api, cmid, completed
        )

    def get_course_user_groups(
        self, course_id: int, user_id: int = 0
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách nhóm của sinh viên trong môn học."""
        return ws_functions.get_course_user_groups(
            self.call_ws_api, course_id, user_id=user_id
        )

    def get_forums_by_courses(self, course_ids: List[int]) -> List[Dict[str, Any]]:
        """Lấy danh sách diễn đàn theo môn học."""
        return ws_functions.get_forums_by_courses(self.call_ws_api, course_ids)

    def get_forum_discussions(
        self, forum_id: int, *, page: int = 0, perpage: int = 20
    ) -> List[Dict[str, Any]]:
        """Lấy danh sách chủ đề trong diễn đàn."""
        return ws_functions.get_forum_discussions(
            self.call_ws_api, forum_id, page=page, perpage=perpage
        )

    def get_discussion_posts(self, discussion_id: int) -> List[Dict[str, Any]]:
        """Lấy bài viết trong chủ đề thảo luận."""
        return ws_functions.get_discussion_posts(self.call_ws_api, discussion_id)

    def get_choice_options(self, choice_id: int) -> List[Dict[str, Any]]:
        """Lấy danh sách phương án bình chọn mod_choice."""
        return ws_functions.get_choice_options(self.call_ws_api, choice_id)

    def submit_choice_response(self, choice_id: int, responses: List[int]) -> bool:
        """Gửi lựa chọn bình chọn mod_choice."""
        return ws_functions.submit_choice_response(
            self.call_ws_api, choice_id, responses
        )

    def get_quiz_access_information(
        self, quiz_id: int
    ) -> Optional[Dict[str, Any]]:
        """Lấy thông tin điều kiện truy cập quiz."""
        return ws_functions.get_quiz_access_information(self.call_ws_api, quiz_id)

    def get_quiz_attempt_review(
        self, attempt_id: int, page: int = -1
    ) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết xem lại lượt làm quiz."""
        return ws_functions.get_quiz_attempt_review(
            self.call_ws_api, attempt_id, page=page
        )

    def get_books_by_courses(self, course_ids: List[int]) -> List[Dict[str, Any]]:
        """Lấy danh sách hoạt động Book trong môn học."""
        return ws_functions.get_books_by_courses(self.call_ws_api, course_ids)
