import html
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit
from core.client import MoodleClient
from core.activity_status import (
    normalize_submission_status,
    prefer_known_submission_status,
)
from core.demo_sandbox import is_demo_account, normalize_demo_username
from core.grade_monitor import GradeMonitor
from core.moodle_service import MoodleService
from config import settings
from models import Assignment

logger = logging.getLogger(__name__)

class DataOrchestrator:
    """
    Điều phối việc lấy dữ liệu trực tiếp từ server Moodle UTH.
    Sử dụng MoodleClient để kết nối và MoodleParser để phân tích.
    """
    
    def __init__(self):
        self.client = MoodleClient()
        self.moodle_service = MoodleService(
            self.client.call_ws_api,
            grade_report_fetcher=self.client.fetch_grade_report_html,
            grade_report_error_getter=lambda: getattr(
                self.client, "last_grade_report_error", ""
            ),
            client=self.client,
        )
        self.thnn_client = MoodleClient("https://thnn.ut.edu.vn")
        self.thnn_service = MoodleService(
            self.thnn_client.call_ajax_api,
            client=self.thnn_client,
        )
        self._thnn_lock = threading.RLock()
        self._thnn_generation: int = 0
        self._thnn_cache_by_account: dict[str, List[Dict[str, Any]]] = {}
        self._last_thnn_fetch_ts_by_account: dict[str, float] = {}
        self._ws_parallel_timeout: float = 25.0
        self._courses_cache = None
        self.is_logged_in = False
        self._detail_cache: dict = {}  # url → từ điển chi tiết đầy đủ của hoạt động
        self._detail_cache_saved_at: dict[str, float] = {}
        self._detail_cache_lru: OrderedDict[str, None] = OrderedDict()
        self._detail_cache_generation = 0
        self._detail_cache_ttl_seconds = max(60, int(getattr(settings, "DETAIL_CACHE_TTL_SECONDS", 1800)))
        self._detail_cache_max_entries = max(1, int(getattr(settings, "DETAIL_CACHE_MAX_ENTRIES", 100)))
        self._detail_lock = threading.RLock()
        self._detail_inflight: dict[str, threading.Event] = {}
        # Trạng thái cơ chế Smart polling
        self._last_fetch_ts: int = 0
        self._userid_cache: Optional[int] = None
        # Bộ nhớ cache ở cấp độ chu kỳ quét (được xóa sau mỗi lần refresh, tránh gọi API trùng lặp)
        self._courses_cache: Optional[List[Dict]] = None
        # Giám sát điểm số (Grade monitoring)
        self.grade_monitor = GradeMonitor()
        # Điểm theo môn (tab Môn học) - đồng bộ vào cùng chu kỳ cập nhật với Tổng quan
        from core.course_grade_monitor import CourseGradeMonitor
        self.course_grade_monitor = CourseGradeMonitor()
        self._course_grades_lock = threading.Lock()
        self._course_grades_generation: int = 0
        self.latest_course_grades: list = []
        self._account_state_lock = threading.RLock()
        self._active_account_key: str = self._current_account_key()

    @staticmethod
    def _current_account_key() -> str:
        raw = getattr(settings, "UTH_USERNAME", "")
        if is_demo_account(raw):
            return normalize_demo_username(raw)
        return str(raw or "").strip().casefold()

    def _sync_account_scope(self) -> None:
        """Reset account-scoped caches when `settings.UTH_USERNAME` changes."""
        current_key = self._current_account_key()
        lock = getattr(self, "_account_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._account_state_lock = lock
        with lock:
            if not hasattr(self, "_active_account_key"):
                self._active_account_key = current_key
                return
            previous_key = self._active_account_key
            if current_key == previous_key:
                return
            self._active_account_key = current_key
            if not previous_key:
                return
            self.is_logged_in = False
            self._userid_cache = None
            self._courses_cache = None
            self._last_fetch_ts = 0
            grades_lock = getattr(self, "_course_grades_lock", None)
            if grades_lock is not None:
                with grades_lock:
                    self._course_grades_generation = (
                        int(getattr(self, "_course_grades_generation", 0)) + 1
                    )
                    self.latest_course_grades = []
            else:
                self._course_grades_generation = (
                    int(getattr(self, "_course_grades_generation", 0)) + 1
                )
                self.latest_course_grades = []
            thnn_lock = getattr(self, "_thnn_lock", None)
            if thnn_lock is not None:
                with thnn_lock:
                    self._thnn_generation = (
                        int(getattr(self, "_thnn_generation", 0)) + 1
                    )
            if hasattr(self, "_detail_lock"):
                self.clear_detail_cache()
            client = getattr(self, "client", None)
            if client is not None and hasattr(client, "clear_session"):
                client.clear_session()
            thnn_client = getattr(self, "thnn_client", None)
            if thnn_client is not None and hasattr(thnn_client, "clear_session"):
                thnn_client.clear_session()
            for svc_attr in ("moodle_service", "thnn_service"):
                svc = getattr(self, svc_attr, None)
                clear_fn = getattr(svc, "clear_all_caches", None)
                if callable(clear_fn):
                    try:
                        clear_fn()
                    except Exception:
                        pass

    def _get_cached_detail(self, url: str):
        now = time.monotonic()
        with self._detail_lock:
            cached = self._detail_cache.get(url)
            saved_at = self._detail_cache_saved_at.get(url)
            if cached is None or saved_at is None:
                return None
            if now - saved_at > self._detail_cache_ttl_seconds:
                self._detail_cache.pop(url, None)
                self._detail_cache_saved_at.pop(url, None)
                self._detail_cache_lru.pop(url, None)
                return None
            self._detail_cache_lru[url] = None
            self._detail_cache_lru.move_to_end(url)
            return cached

    def _set_cached_detail(
        self,
        url: str,
        value: Dict[str, Any],
        generation: Optional[int] = None,
    ) -> bool:
        with self._detail_lock:
            if generation is not None and generation != self._detail_cache_generation:
                return False
            self._detail_cache[url] = value
            self._detail_cache_saved_at[url] = time.monotonic()
            self._detail_cache_lru[url] = None
            self._detail_cache_lru.move_to_end(url)
            while len(self._detail_cache_lru) > self._detail_cache_max_entries:
                old_url, _ = self._detail_cache_lru.popitem(last=False)
                self._detail_cache.pop(old_url, None)
                self._detail_cache_saved_at.pop(old_url, None)
            return True

    def get_cached_details_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._detail_lock:
            return dict(self._detail_cache)

    @staticmethod
    def _thnn_account_key() -> str:
        raw = getattr(settings, "UTH_USERNAME", "") or ""
        if is_demo_account(raw):
            return normalize_demo_username(raw)
        return str(raw).strip().lower() or "__default__"

    @property
    def _thnn_activities_cache(self) -> List[Dict[str, Any]]:
        with self._thnn_lock:
            return list(self._thnn_cache_by_account.get(self._thnn_account_key(), []))

    @_thnn_activities_cache.setter
    def _thnn_activities_cache(self, value: List[Dict[str, Any]]) -> None:
        with self._thnn_lock:
            self._thnn_cache_by_account[self._thnn_account_key()] = list(value or [])

    @property
    def _last_thnn_fetch_ts(self) -> float:
        with self._thnn_lock:
            return float(self._last_thnn_fetch_ts_by_account.get(self._thnn_account_key(), 0.0))

    @_last_thnn_fetch_ts.setter
    def _last_thnn_fetch_ts(self, value: float) -> None:
        with self._thnn_lock:
            self._last_thnn_fetch_ts_by_account[self._thnn_account_key()] = float(value or 0.0)

    def _thnn_state_matches_locked(self, start_key: str, start_gen: int) -> bool:
        return (
            self._thnn_generation == start_gen
            and self._thnn_account_key() == start_key
        )

    def clear_detail_cache(self):
        """Xóa sạch bộ nhớ đệm chi tiết (dùng khi refresh thủ công)."""
        with self._detail_lock:
            self._detail_cache_generation += 1
            self._detail_cache.clear()
            self._detail_cache_saved_at.clear()
            self._detail_cache_lru.clear()
        logger.debug("Đã xóa sạch bộ nhớ đệm chi tiết bài tập/quiz.")

    def clear_caches(self) -> None:
        """Clear all account-scoped in-memory caches on login/logout/account switch."""
        lock = getattr(self, "_account_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._account_state_lock = lock
        with lock:
            self._active_account_key = self._current_account_key()
            self._courses_cache = None
            self._userid_cache = None
            self._last_fetch_ts = 0
            self.is_logged_in = False
            grades_lock = getattr(self, "_course_grades_lock", None)
            if grades_lock is not None:
                with grades_lock:
                    self._course_grades_generation += 1
                    self.latest_course_grades = []
            else:
                self._course_grades_generation = (
                    int(getattr(self, "_course_grades_generation", 0)) + 1
                )
                self.latest_course_grades = []
            thnn_lock = getattr(self, "_thnn_lock", None)
            if thnn_lock is not None:
                with thnn_lock:
                    self._thnn_generation = (
                        int(getattr(self, "_thnn_generation", 0)) + 1
                    )
                    if hasattr(self, "_thnn_cache_by_account"):
                        self._thnn_cache_by_account.clear()
                    if hasattr(self, "_last_thnn_fetch_ts_by_account"):
                        self._last_thnn_fetch_ts_by_account.clear()
            if hasattr(self, "_detail_lock"):
                self.clear_detail_cache()
            for client_attr in ("client", "thnn_client"):
                client_obj = getattr(self, client_attr, None)
                clear_session = getattr(client_obj, "clear_session", None)
                if callable(clear_session):
                    clear_session()
            for svc_attr in ("moodle_service", "thnn_service"):
                svc = getattr(self, svc_attr, None)
                clear_fn = getattr(svc, "clear_all_caches", None)
                if callable(clear_fn):
                    try:
                        clear_fn()
                    except Exception:
                        pass

    @staticmethod
    def _build_time_policy():
        from core.activity_time_policy import ActivityTimePolicy

        crit = int(getattr(settings, "URGENCY_CRITICAL_HOURS", 24) or 24)
        warn = int(getattr(settings, "URGENCY_WARNING_HOURS", 72) or 72)
        return ActivityTimePolicy(critical_hours=crit, warning_hours=warn)

    @staticmethod
    def _assignment_prevents_late(assign: Dict[str, Any]) -> bool:
        if bool(assign.get("nosubmissions")):
            return True
        configs = assign.get("configs")
        if isinstance(configs, list):
            for cfg in configs:
                if (
                    isinstance(cfg, dict)
                    and cfg.get("name") == "preventlatesubmissions"
                    and str(cfg.get("value", "")).strip() in {"1", "true", "True"}
                ):
                    return True
        return False

    @classmethod
    def _compute_late_status(cls, assign: Dict[str, Any], now_ts: int) -> str:
        try:
            duedate = int(assign.get("duedate") or 0)
        except (TypeError, ValueError):
            duedate = 0
        try:
            cutoffdate = int(assign.get("cutoffdate") or 0)
        except (TypeError, ValueError):
            cutoffdate = 0
        if duedate <= 0 or now_ts < duedate:
            return "open"
        if cutoffdate > 0:
            return "late_allowed" if now_ts < cutoffdate else "closed"
        return "closed" if cls._assignment_prevents_late(assign) else "late_allowed"

    @staticmethod
    def _run_parallel_bounded(callables: List[Any], timeout: float = 25.0) -> List[Any]:
        deadline = time.monotonic() + max(0.01, float(timeout))
        pool = ThreadPoolExecutor(max_workers=max(1, len(callables)))
        futures = [pool.submit(fn) for fn in callables]
        try:
            outputs = []
            for fut in futures:
                remaining = max(0.0, deadline - time.monotonic())
                outputs.append(fut.result(timeout=remaining))
            return outputs
        finally:
            for fut in futures:
                fut.cancel()
            pool.shutdown(wait=False, cancel_futures=True)

    def update_cached_submission_status(self, url: str, new_status: str):
        """Cập nhật trạng thái nộp bài trực tiếp trong cache để đồng bộ giao diện."""
        canonical_status = normalize_submission_status(new_status)
        with self._detail_lock:
            cached = self._detail_cache.get(url)
            if cached:
                cached["submission_status"] = canonical_status
                details = cached.get("details", {})
                if isinstance(details, dict):
                    if cached.get("type") == "quiz":
                        details["quiz_attempt_status"] = canonical_status
                    status_data = details.get("status_data", {})
                    if isinstance(status_data, dict):
                        status_data["Trạng thái nộp bài"] = canonical_status

    def invalidate_detail_cache(self, url: str):
        """Xóa cache chi tiết của một URL cụ thể (khi có tác vụ ghi như nộp/xóa bài)."""
        with self._detail_lock:
            self._detail_cache.pop(url, None)
            self._detail_cache_saved_at.pop(url, None)
            self._detail_cache_lru.pop(url, None)
        logger.debug("Đã invalidate cache chi tiết cho URL: %s", url)

    def _get_userid(self) -> Optional[int]:
        """Get cached userid or fetch from site_info."""
        self._sync_account_scope()
        if self._userid_cache is not None:
            return self._userid_cache
        try:
            info = self.moodle_service.get_site_info()
            if info and 'userid' in info:
                self._userid_cache = info['userid']
                return self._userid_cache
        except Exception as e:
            logger.debug("Failed to get userid: %s", e)
        return None

    def get_updates_since(self, timestamp: int) -> Optional[List[int]]:
        """Kiểm tra các cập nhật môn học kể từ lần tải cuối bằng hàm core_course_get_updates_since.

        Returns:
            Danh sách ID môn học đã thay đổi nếu phát hiện thay đổi,
            Danh sách rỗng nếu không có thay đổi nào,
            None nếu xảy ra lỗi (bên gọi nên chuyển sang tải toàn bộ dữ liệu).
        """
        self._sync_account_scope()
        if timestamp <= 0:
            return None  # Chưa có dữ liệu của lần tải trước, bắt buộc tải đầy đủ
        try:
            # Sửa lỗi BUG-02: get_enrolled_courses nhận tham số phân loại chứ không nhận userid
            courses = self.moodle_service.get_enrolled_courses()
            if not courses:
                return None
            changed_courses = []
            for course in courses:
                cid = course.get('id')
                if not cid:
                    continue
                updates = self.moodle_service.get_course_updates_since(cid, timestamp)
                if updates:  # Không rỗng = có sự thay đổi dữ liệu
                    changed_courses.append(cid)
            if changed_courses:
                logger.info("Smart poll: %d courses changed since %d", len(changed_courses), timestamp)
            else:
                logger.debug("Smart poll: no changes since %d", timestamp)
            return changed_courses
        except Exception as e:
            logger.warning("Smart poll failed, falling back to full fetch: %s", e)
            return None

    def login(self) -> bool:
        """Đăng nhập bằng WS token (stateless, không kick browser)."""
        self._sync_account_scope()
        if self.is_logged_in:
            return True
        
        username = settings.UTH_USERNAME
        password = settings.UTH_PASSWORD
        
        if not username or not password:
            logger.error("Chưa cấu hình MSSV hoặc mật khẩu trong Settings.")
            return False
        
        self.is_logged_in = self.client.login()
        if self.is_logged_in:
            logger.info("Đăng nhập WS token thành công.")
        return self.is_logged_in

    def get_latest_activities(self) -> List[Dict[str, Any]]:
        """Đăng nhập và lấy danh sách hoạt động mới nhất qua WS API."""
        self._sync_account_scope()
        ws_result = self._fetch_via_ws_api()
        if ws_result is not None:
            return ws_result
        logger.error("Không thể lấy dữ liệu từ WS API.")
        return []

    def refresh_course_grades(self):
        """Tải điểm các môn đang học, lưu snapshot và trả (danh sách môn, thay đổi).

        Được gọi trong chu kỳ cập nhật Tổng quan và khi bấm làm mới ở tab Môn học.
        Khoá chỉ bảo vệ phần cập nhật snapshot/trạng thái để không chặn teardown
        khi đang chờ mạng, đồng thời loại bỏ kết quả cũ nếu tài khoản đã đổi.
        """
        self._sync_account_scope()
        from core.course_grades_service import fetch_current_courses_with_grades
        with self._course_grades_lock:
            start_gen = self._course_grades_generation
        courses = fetch_current_courses_with_grades(self)
        with self._course_grades_lock:
            if self._course_grades_generation != start_gen:
                return [], []
            changes = self.course_grade_monitor.update(courses) if courses else []
            self.latest_course_grades = courses
            return courses, changes

    def check_grade_changes(self) -> List:
        """Check for grade changes using GradeMonitor.

        Returns:
            List of GradeChange objects if changes detected, empty list otherwise.
        """
        self._sync_account_scope()
        userid = self._get_userid()
        if userid is None:
            return []
        return self.grade_monitor.check_for_changes(
            self.moodle_service, userid
        )
    

    def _should_sync_thnn(self, now_ts: float) -> bool:
        if self._last_thnn_fetch_ts == 0.0:
            return True
        from datetime import datetime
        now = datetime.fromtimestamp(now_ts)
        last = datetime.fromtimestamp(self._last_thnn_fetch_ts)
        
        def get_milestone(dt: datetime) -> int:
            if dt.hour < 6: return 0
            elif dt.hour < 12: return 6
            else: return 12
            
        if now.date() > last.date():
            return True
        if get_milestone(now) > get_milestone(last):
            return True
        return False

    def _download_thnn_calendar(self, opener, data: bytes) -> Optional[str]:
        """Download one bounded calendar export and reject login/malformed payloads."""
        import urllib.parse

        origin = self.thnn_client.moodle_site_origin.rstrip("/")
        export_url = f"{origin}/calendar/export.php"
        with opener.open(export_url, data=data, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            final_url = response.geturl()
            raw_body = response.read(2 * 1024 * 1024 + 1)
            headers = getattr(response, "headers", None)
            content_type = ""
            if headers is not None and hasattr(headers, "get_content_type"):
                content_type = str(headers.get_content_type() or "").casefold()

        parsed_origin = urllib.parse.urlsplit(origin)
        parsed_final = urllib.parse.urlsplit(final_url)
        if (
            not 200 <= status < 300
            or parsed_final.scheme.casefold() != parsed_origin.scheme.casefold()
            or parsed_final.netloc.casefold() != parsed_origin.netloc.casefold()
            or "/login/" in parsed_final.path.casefold()
            or len(raw_body) > 2 * 1024 * 1024
            or content_type == "text/html"
        ):
            return None

        decoded = raw_body.decode("utf-8", errors="replace").lstrip("\ufeff \t\r\n")
        if not decoded.startswith("BEGIN:VCALENDAR"):
            return None
        if not decoded.rstrip().endswith("END:VCALENDAR"):
            return None
        return decoded


    def _thnn_login_was_deferred(self) -> bool:
        login_err = str(
            getattr(self.thnn_client, "last_login_error", "")
            or getattr(self.thnn_client, "_last_login_error", "")
            or ""
        )
        return login_err == "quiz_session_active"

    def _fetch_thnn_activities(self, now_ts: int) -> List[Dict[str, Any]]:
        self._sync_account_scope()
        with self._thnn_lock:
            start_key = self._thnn_account_key()
            start_gen = self._thnn_generation
            last_known_good = list(self._thnn_cache_by_account.get(start_key, []))

        def _return_fallback() -> List[Dict[str, Any]]:
            with self._thnn_lock:
                if self._thnn_state_matches_locked(start_key, start_gen):
                    return list(last_known_good)
            return []

        def _clear_if_current() -> None:
            with self._thnn_lock:
                if self._thnn_state_matches_locked(start_key, start_gen):
                    self._thnn_cache_by_account[start_key] = []
                    self._last_thnn_fetch_ts_by_account[start_key] = 0.0
        try:
            if not self.thnn_client._sesskey:
                success = self.thnn_client.login_session()
                if not success:
                    if self._thnn_login_was_deferred():
                        return _return_fallback()
                    _clear_if_current()
                    return []
                    
            opener = self.thnn_client._ensure_html_opener()
            import urllib.parse
            import datetime
            import re
            from bs4 import BeautifulSoup
            
            # 1. Tải file ICS export từ THNN
            data = urllib.parse.urlencode({
                'sesskey': self.thnn_client._sesskey,
                '_qf__core_calendar_export_form': '1',
                'events[exportevents]': 'all',
                'period[timeperiod]': 'recentupcoming',
                'export': 'Export'
            }).encode('utf-8')
            
            ics_data = self._download_thnn_calendar(opener, data)
            if ics_data is None:
                if not self.thnn_client.login_session(force=True):
                    if self._thnn_login_was_deferred():
                        return _return_fallback()
                    _clear_if_current()
                    return []
                opener = self.thnn_client._ensure_html_opener()
                data = urllib.parse.urlencode({
                    'sesskey': self.thnn_client._sesskey,
                    '_qf__core_calendar_export_form': '1',
                    'events[exportevents]': 'all',
                    'period[timeperiod]': 'recentupcoming',
                    'export': 'Export'
                }).encode('utf-8')
                ics_data = self._download_thnn_calendar(opener, data)
                if ics_data is None:
                    return _return_fallback()
                
            with self._thnn_lock:
                if not self._thnn_state_matches_locked(start_key, start_gen):
                    return []

            events_ics = {}
            current_event = {}
            for line in ics_data.split('\n'):
                line = line.strip()
                if line == 'BEGIN:VEVENT':
                    current_event = {}
                elif line.startswith('UID:'):
                    m = re.match(r'UID:(\d+)@', line)
                    if m:
                        current_event['id'] = int(m.group(1))
                elif line.startswith('SUMMARY:'):
                    current_event['name'] = line[8:].replace(r'\,', ',')
                elif line.startswith('DTEND:'):
                    dt_str = line[6:]
                    if dt_str.endswith('Z'):
                        dt = datetime.datetime.strptime(dt_str, '%Y%m%dT%H%M%SZ')
                        dt = dt.replace(tzinfo=datetime.timezone.utc)
                        current_event['timestamp'] = int(dt.timestamp())
                elif line.startswith('CATEGORIES:'):
                    current_event['course_name'] = line[11:].replace(r'\,', ',')
                elif line == 'END:VEVENT':
                    if 'id' in current_event:
                        events_ics[current_event['id']] = current_event
                        
            # 2. Tải HTML các tháng để map URL
            now = datetime.datetime.now()
            months_to_fetch = []
            for d_month in [-1, 0, 1, 2]:
                m = now.month + d_month
                y = now.year + (m - 1) // 12
                m = ((m - 1) % 12) + 1
                months_to_fetch.append(int(datetime.datetime(y, m, 1).timestamp()))
                
            for ts in months_to_fetch:
                try:
                    url = f"https://thnn.ut.edu.vn/calendar/view.php?view=month&time={ts}"
                    r = opener.open(url, timeout=10)
                    soup = BeautifulSoup(r.read().decode('utf-8', errors='ignore'), 'html.parser')
                    for a in soup.find_all('a', attrs={'data-action': 'view-event'}):
                        ev_id = int(a.get('data-event-id', 0))
                        href = a.get('href', '')
                        if ev_id in events_ics and href:
                            events_ics[ev_id]['url'] = href
                            if 'mod/assign' in href:
                                events_ics[ev_id]['modulename'] = 'assign'
                            elif 'mod/quiz' in href:
                                events_ics[ev_id]['modulename'] = 'quiz'
                except Exception as e:
                    logger.warning(f"Lỗi tải THNN calendar month {ts}: {e}")
                    
            # 3. Chuyển đổi format sang định dạng chuẩn của UTHelper
            results = []
            import html
            from datetime import datetime
            policy = self._build_time_policy()
            
            for ev in events_ics.values():
                url = ev.get('url', '')
                modulename = ev.get('modulename')
                if url and modulename in ('assign', 'quiz'):
                    name = ev.get('name', '').replace(' tới hạn', '').replace(' closes', '')
                    cname = ev.get('course_name', 'THNN Course')
                    course_id = 0
                    
                    cm_id = ""
                    if 'id=' in url:
                        cm_id = url.split('id=')[-1].split('&')[0]
                    
                    evt_id = ev.get('id', '')
                    if not evt_id and modulename and cm_id:
                        evt_id = f"{modulename}_{cm_id}"
                        
                    timestart = ev.get('timestamp', 0)
                    deadline = ''
                    if timestart:
                        try:
                            deadline = datetime.fromtimestamp(timestart).strftime('%Y-%m-%d %H:%M:%S')
                        except Exception:
                            deadline = ''
                            
                    event_type = modulename
                    if event_type == 'assign':
                        event_type = 'assignment'
                    
                    act_dict = {
                        'id': str(evt_id),
                        'title': html.unescape(name.strip()),
                        'course_name': html.unescape(cname),
                        'course': html.unescape(cname),
                        'course_id': course_id,
                        'deadline': deadline,
                        'deadline_str': deadline,
                        'url': url,
                        'type': event_type,
                        'urgency': 'safe',
                        'source': 'thnn_sync',
                        'submission_status': 'unknown',
                        'details': {},
                        'is_open': False,
                        'site_identity': 'thnn'
                    }
                    act_dict['urgency'] = policy.evaluate(act_dict).urgency
                    results.append(act_dict)
                    
            with self._thnn_lock:
                if not self._thnn_state_matches_locked(start_key, start_gen):
                    return []
                self._last_thnn_fetch_ts_by_account[start_key] = float(now_ts)
                self._thnn_cache_by_account[start_key] = list(results)
            return results
        except Exception as e:
            logger.error(f"Lỗi khi tải THNN activities: {e}")
            return _return_fallback()

    def _format_assignment_from_ws(self, a: dict, url: str, identity: str) -> dict:
        import html
        from datetime import datetime
        dd = a.get("duedate", 0)
        dt = datetime.fromtimestamp(dd).isoformat() if dd > 0 else ""
        return {
            'id': str(a.get("id", "0")),
            'title': html.unescape(a.get("name") or "Bài tập THNN"),
            'course_name': "THNN Course",
            'course': "THNN Course",
            'course_id': str(a.get("course", "0")),
            'deadline': dt,
            'deadline_str': dt,
            'url': url,
            'type': "assignment",
            'urgency': "safe",
            'source': 'ws_api',
            'submission_status': "submitted" if a.get("nosubmissions") == 0 else "new",
            'details': {},
            'is_open': False,
            'site_identity': identity
        }

    def _fetch_via_ws_api(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy activities bằng Moodle Web Services API (stateless, JSON).
        
        PERF-OPT: Parallel API calls + eliminated redundant fetches.
        Kết hợp 3 nguồn:
        1. calendar action events (bài chưa nộp, có deadline tương lai)
        2. mod_assign_get_assignments (TẤT CẢ bài tập kể cả đã nộp)
        3. calendar events + catalog quiz có chọn lọc (quiz đã làm)
        Merge lại để hoạt động đã hoàn thành không bị mất khỏi danh sách.
        """
        self._sync_account_scope()
        try:
            from datetime import datetime
            now_ts = int(datetime.now().timestamp())
            
            # Tối ưu PERF: Xóa cache chu kỳ khi bắt đầu mỗi lần refresh
            self._courses_cache = None
            
            # Tối ưu PERF: Bước 1 - Lấy userid (cần thiết cho danh sách môn học)
            userid = self._get_userid()
            if userid is None:
                logger.warning("Không xác định được Moodle user id; snapshot không đầy đủ.")
                return None
            
            # Tối ưu PERF: Bước 2 - Tải song song: Lịch và danh sách môn học
            calendar_result = None
            courses_result = None
            
            def _fetch_calendar():
                page_size = 50  # Moodle validates limitnum in the inclusive range 1..50.
                events = []
                after_event_id = 0
                for _page in range(10):
                    response = self.moodle_service.get_action_events_by_timesort(
                        now_ts,
                        now_ts + (90 * 24 * 3600),
                        limit=page_size,
                        after_event_id=after_event_id,
                    )
                    if not isinstance(response, dict):
                        return None
                    page_events = response.get("events", [])
                    if not isinstance(page_events, list):
                        return None
                    events.extend(page_events)
                    more = bool(response.get("moreevents", len(page_events) >= page_size))
                    if not more:
                        return {"events": events, "moreevents": False}
                    last_id = response.get("lastid")
                    if not last_id and page_events:
                        last_id = page_events[-1].get("id")
                    try:
                        last_id = int(last_id)
                    except (TypeError, ValueError):
                        return None
                    if last_id <= after_event_id:
                        return None
                    after_event_id = last_id
                logger.warning("Calendar pagination vượt quá giới hạn an toàn.")
                return None
            
            def _fetch_courses():
                if not userid:
                    return None
                return self.moodle_service.get_user_courses(userid)
            
            calendar_result, courses_result = self._run_parallel_bounded(
                [_fetch_calendar, _fetch_courses],
                timeout=self._ws_parallel_timeout,
            )

            if not isinstance(calendar_result, dict) or not isinstance(courses_result, list):
                logger.warning("Moodle trả snapshot calendar/course không đầy đủ.")
                return None
            
            # Lưu cache danh sách môn học để tái sử dụng cho tiến trình kiểm tra điểm và thông báo
            if courses_result and isinstance(courses_result, list):
                self._courses_cache = courses_result
            
            events = []
            if calendar_result and isinstance(calendar_result, dict):
                events = calendar_result.get('events', [])
            
            # Chuyển đổi các sự kiện Web Service sang định dạng của UTHelper
            results = self.moodle_service.ws_events_to_assignments(events) if events else []
            
            # Đồng bộ THNN
            if self._should_sync_thnn(now_ts):
                thnn_results = self._fetch_thnn_activities(now_ts)
            else:
                thnn_results = self._thnn_activities_cache
            if thnn_results:
                results.extend(thnn_results)
            
            # Bước 3 - Gộp assignment và khôi phục quiz đã biến mất khỏi
            # action-events. Hai nhánh chạy song song; benchmark cho thấy nhánh
            # quiz có chọn lọc ngắn hơn call assignment hiện hữu.
            course_ids = [c['id'] for c in (self._courses_cache or []) if isinstance(c, dict) and 'id' in c]
            action_quiz_course_ids = {
                int(item["course_id"])
                for item in results
                if isinstance(item, dict)
                and item.get("type") == "quiz"
                and item.get("site_identity") != "thnn"
                and item.get("course_id")
            }
            action_quiz_urls = {
                item["url"]
                for item in results
                if isinstance(item, dict)
                and item.get("type") == "quiz"
                and item.get("site_identity") != "thnn"
                and item.get("url")
            }
            try:
                now = datetime.now()
                month_start = datetime(now.year, now.month, 1)
                fetch_months = max(1, min(int(getattr(settings, "FETCH_MONTHS", 1)), 3))
                future_month = now.month + fetch_months
                future_year = now.year + (future_month - 1) // 12
                future_month = ((future_month - 1) % 12) + 1
                window_start = int(month_start.timestamp())
                window_end = int(datetime(future_year, future_month, 1).timestamp())

                results, quiz_results = self._run_parallel_bounded(
                    [
                        lambda: self._merge_all_assignments(
                            results,
                            userid=userid,
                            course_ids=course_ids,
                        ),
                        lambda: self._fetch_calendar_quizzes(
                            course_ids,
                            window_start,
                            window_end,
                            action_quiz_course_ids,
                            action_quiz_urls,
                        ),
                    ],
                    timeout=self._ws_parallel_timeout,
                )
                if quiz_results is None:
                    raise RuntimeError("Calendar quiz snapshot is incomplete")

                existing_by_url = {
                    item.get("url"): item
                    for item in results
                    if isinstance(item, dict) and item.get("url")
                }
                for quiz_item in quiz_results:
                    existing = existing_by_url.get(quiz_item.get("url"))
                    if existing is not None:
                        if quiz_item.get("quiz_id"):
                            existing["quiz_id"] = quiz_item["quiz_id"]
                        quiz_security = quiz_item.get("quiz_security")
                        if isinstance(quiz_security, dict) and quiz_security:
                            existing["quiz_security"] = dict(quiz_security)
                        continue
                    if not quiz_item.get("catalog_only"):
                        results.append(quiz_item)
            except Exception as e:
                logger.warning("Merge activities failed; snapshot is partial: %s", e)
                return None
            
            if not results:
                logger.info("WS API trả về 0 activities (hợp lệ - có thể không có bài tập).")
                return results  # Sửa lỗi BUG-10: Trả về [] thay vì None cho kết quả rỗng hợp lệ
            
            logger.info("WS API trả về %d activities (merged).", len(results))
            self.is_logged_in = True  # Token Web Service hoạt động = đã xác thực thành công
            self._last_fetch_ts = int(datetime.now().timestamp())  # Cơ chế Smart poll: Đánh dấu thời gian lần tải cuối
            return results
        except Exception as e:
            logger.warning("WS API fetch thất bại: %s", e)
            return None

    def _fetch_calendar_quizzes(
        self,
        course_ids: List[int],
        time_start: int,
        time_end: int,
        action_quiz_course_ids: Optional[set[int]] = None,
        action_quiz_urls: Optional[set[str]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Return quiz cards from the complete calendar, scoped to relevant courses.

        Moodle action-events omit completed quizzes. The legacy calendar keeps
        those events, but exposes a quiz instance id rather than its course
        module id. Query only the courses that actually contain a quiz event in
        this window, then use the quiz catalog to construct canonical URLs.
        """
        from datetime import datetime

        if not course_ids:
            return []

        events = self.moodle_service.get_calendar_events(
            time_start,
            time_end,
            course_ids,
        )
        if events is None:
            return None

        calendar_quiz_course_ids = {
                int(event["courseid"])
                for event in events
                if isinstance(event, dict)
                and event.get("modulename") == "quiz"
                and event.get("courseid")
        }
        quiz_course_ids = sorted(
            calendar_quiz_course_ids | set(action_quiz_course_ids or ())
        )
        if not quiz_course_ids:
            return []

        quizzes = self.moodle_service.get_quizzes_by_courses(quiz_course_ids)
        if not isinstance(quizzes, list):
            return None

        course_names = {
            int(course["id"]): html.unescape(course.get("fullname", ""))
            for course in (self._courses_cache or [])
            if isinstance(course, dict) and course.get("id")
        }
        policy = self._build_time_policy()
        results: List[Dict[str, Any]] = []
        seen_cmids: set[int] = set()
        action_quiz_urls = set(action_quiz_urls or ())

        for quiz in quizzes:
            if not isinstance(quiz, dict):
                continue
            try:
                course_id = int(quiz.get("course") or 0)
                cmid = int(quiz.get("coursemodule") or 0)
                quiz_id = int(quiz.get("id") or 0)
                deadline_ts = int(quiz.get("timeclose") or 0)
            except (TypeError, ValueError):
                continue
            quiz_security = {
                field: quiz[field]
                for field in ("browsersecurity", "activerulenames")
                if field in quiz
            }
            quiz_url = f"{settings.MOODLE_BASE_URL}/mod/quiz/view.php?id={cmid}"
            is_action_quiz = quiz_url in action_quiz_urls
            if not cmid or cmid in seen_cmids or (
                course_id not in calendar_quiz_course_ids and not is_action_quiz
            ):
                continue

            seen_cmids.add(cmid)
            if (
                not deadline_ts
                or deadline_ts < time_start
                or deadline_ts > time_end
            ):
                if is_action_quiz:
                    catalog_item = {
                        "url": quiz_url,
                        "quiz_id": quiz_id,
                        "catalog_only": True,
                    }
                    if quiz_security:
                        catalog_item["quiz_security"] = quiz_security
                    results.append(catalog_item)
                continue
            deadline = datetime.fromtimestamp(deadline_ts).strftime("%Y-%m-%d %H:%M:%S")
            course_name = course_names.get(course_id, "")
            quiz_item = {
                "id": f"quiz_{quiz_id}",
                "title": html.unescape(quiz.get("name") or "Không tên"),
                "course_name": course_name,
                "course": course_name,
                "course_id": course_id,
                "quiz_id": quiz_id,
                "deadline": deadline,
                "deadline_str": deadline,
                "url": quiz_url,
                "type": "quiz",
                "urgency": "safe",
                "source": "ws_quiz_calendar_api",
                "submission_status": "unknown",
                "details": {},
                "is_open": False,
                "site_identity": "courses",
            }
            quiz_item["urgency"] = policy.evaluate(quiz_item).urgency
            if quiz_security:
                quiz_item["quiz_security"] = quiz_security
            results.append(quiz_item)

        return results
    
    async def _fetch_via_ws_api_async(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy activities bằng WS API bất đồng bộ (non-blocking).
        
        PERF-OPT: Delegates to parallel _fetch_via_ws_api in thread.
        """
        try:
            import asyncio
            result = await asyncio.to_thread(self._fetch_via_ws_api)
            return result
        except Exception as e:
            logger.warning("WS API async fetch thất bại: %s", e)
            return None

    def _merge_all_assignments(
        self,
        calendar_results: List[Dict[str, Any]],
        userid: Optional[int] = None,
        course_ids: Optional[List[int]] = None,
    ) -> List[Dict[str, Any]]:
        """Bổ sung bài tập đã nộp vào danh sách calendar events.
        
        PERF-OPT: Accepts pre-fetched userid and course_ids to avoid
        redundant API calls (previously called site_info + enrolled_courses
        again - wasting ~1.4s).
        
        Moodle calendar API loại bỏ events khi bài đã nộp (no action needed).
        Lấy tất cả assignments từ enrolled courses, chỉ giữ bài có deadline
        từ đầu tháng hiện tại → FETCH_MONTHS tháng tới (theo setting).
        """
        from datetime import datetime
        from urllib.parse import urlsplit
        
        now = datetime.now()
        # Đầu tháng hiện tại
        month_start = datetime(now.year, now.month, 1)
        min_ts = int(month_start.timestamp())
        
        # Cuối khoảng = FETCH_MONTHS tháng tới
        fetch_months = max(1, min(int(getattr(settings, "FETCH_MONTHS", 1)), 3))
        future_month = now.month + fetch_months
        future_year = now.year + (future_month - 1) // 12
        future_month = ((future_month - 1) % 12) + 1
        max_ts = int(datetime(future_year, future_month, 1).timestamp())
        
        # Tối ưu PERF: Sử dụng dữ liệu đã tải trước thay vì gọi lại các API
        if not course_ids:
            # Fallback: Tự động tải nếu bên gọi không cung cấp dữ liệu (tương thích ngược)
            try:
                if userid is None:
                    userid = self._get_userid()
                if not userid:
                    return calendar_results
                courses_result = self._courses_cache
                if not courses_result:
                    courses_result = self.moodle_service.get_user_courses(userid)
                if not courses_result or not isinstance(courses_result, list):
                    return calendar_results
                course_ids = [c['id'] for c in courses_result if isinstance(c, dict) and 'id' in c]
            except Exception:
                return calendar_results
        
        if not course_ids:
            return calendar_results
        
        # Lấy assignments
        all_assigns = self.moodle_service.get_assignments(course_ids)
        if not all_assigns:
            return calendar_results
        
        # Chỉ Courses activities được phép khớp với Courses assignments. Numeric
        # cmid chỉ duy nhất bên trong một Moodle site, không duy nhất giữa các site.
        courses_url = urlsplit(settings.MOODLE_BASE_URL)
        courses_origin = (courses_url.scheme.casefold(), courses_url.netloc.casefold())

        # Xây dựng bảng tra cứu: cmid → chỉ mục trong calendar_results (để bổ sung thông tin cutoffdate)
        existing_cmids = set()
        cmid_to_index: dict[int, int] = {}
        for idx, item in enumerate(calendar_results):
            url = item.get('url', '')
            parsed_url = urlsplit(url)
            item_origin = (
                parsed_url.scheme.casefold(),
                parsed_url.netloc.casefold(),
            )
            if item_origin != courses_origin:
                continue
            if 'id=' in url:
                try:
                    _cmid = int(url.split('id=')[-1].split('&')[0])
                    existing_cmids.add(_cmid)
                    cmid_to_index[_cmid] = idx
                except (ValueError, IndexError):
                    pass
        
        # Merge bài tập thiếu (chỉ trong khoảng thời gian)
        now_ts = int(now.timestamp())
        policy = self._build_time_policy()
        merged_count = 0
        
        for course_data in all_assigns:
            if not isinstance(course_data, dict):
                continue
            course_name = html.unescape(course_data.get('fullname', ''))
            course_id = course_data.get('id', '')
            
            for assign in course_data.get('assignments', []):
                cmid = assign.get('cmid')
                
                # Bổ sung thông tin cutoffdate vào các mục lịch hiện có
                if cmid and cmid in existing_cmids:
                    _idx = cmid_to_index.get(cmid)
                    if _idx is not None and _idx < len(calendar_results):
                        _existing = calendar_results[_idx]
                        if not _existing.get('course_id') and course_id:
                            _existing['course_id'] = course_id
                        if assign.get('id'):
                            _existing['assignment_id'] = assign['id']
                        _cutoff = assign.get('cutoffdate', 0)
                        _existing['late_status'] = self._compute_late_status(assign, now_ts)
                        _existing['cutoff_date'] = _cutoff
                    continue
                
                duedate = assign.get('duedate', 0)
                if not duedate or duedate < min_ts or duedate > max_ts:
                    continue
                
                cutoffdate = assign.get('cutoffdate', 0)
                late_status = self._compute_late_status(assign, now_ts)
                assign_url = f"{settings.MOODLE_BASE_URL}/mod/assign/view.php?id={cmid}" if cmid else ''
                deadline_str = datetime.fromtimestamp(duedate).strftime('%Y-%m-%d %H:%M:%S')
                assign_item = {
                    'id': f"assign_{assign.get('id', cmid)}",
                    'title': html.unescape(assign.get('name', 'Không tên')),
                    'course_name': course_name,
                    'course': course_name,
                    'course_id': course_id,
                    'assignment_id': assign.get('id'),
                    'deadline': deadline_str,
                    'deadline_str': deadline_str,
                    'url': assign_url,
                    'type': 'assignment',
                    'urgency': 'safe',
                    'source': 'ws_assign_api',
                    'submission_status': 'unknown',
                    'details': {},
                    'is_open': False,
                    'site_identity': 'courses',
                    'cutoff_date': cutoffdate,
                    'late_status': late_status,
                }
                assign_item['urgency'] = policy.evaluate(assign_item).urgency
                calendar_results.append(assign_item)
                merged_count += 1
        
        if merged_count > 0:
            logger.info("Merged %d assignments (tháng %d → +%d tháng).",
                        merged_count, now.month, fetch_months)
        
        return calendar_results
    
    async def get_latest_activities_async(self) -> List[Dict[str, Any]]:
        """Phiên bản async của get_latest_activities - dùng trực tiếp trong event loop."""
        self._sync_account_scope()
        ws_result = await self._fetch_via_ws_api_async()
        if ws_result is not None:
            return ws_result
        logger.error("Không thể lấy dữ liệu async từ WS API.")
        return []

    async def fetch_activity_outcome_async(self):
        """Return a typed result so failure cannot masquerade as an empty feed."""
        from core.sync_coordinator import FetchOutcome

        self._sync_account_scope()
        try:
            ws_result = await self._fetch_via_ws_api_async()
        except Exception as exc:
            return FetchOutcome.failed(str(exc))
        if ws_result is None:
            return FetchOutcome.failed("Moodle activity fetch failed")
        return FetchOutcome.complete(ws_result)


    @staticmethod
    def _merge_cached_detail_for_activity(
        activity_data: Dict[str, Any],
        cached: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply shared module detail without replacing the clicked event card."""
        result = {**cached, **activity_data}
        current_details = activity_data.get("details")
        cached_details = cached.get("details")
        current_details = (
            current_details if isinstance(current_details, dict) else {}
        )
        cached_details = cached_details if isinstance(cached_details, dict) else {}
        details = {**current_details, **cached_details}

        cached_status_data = cached_details.get("status_data")
        current_status_data = current_details.get("status_data")
        cached_status_data = (
            cached_status_data if isinstance(cached_status_data, dict) else {}
        )
        current_status_data = (
            current_status_data if isinstance(current_status_data, dict) else {}
        )
        if cached_status_data or current_status_data:
            details["status_data"] = {
                **cached_status_data,
                **current_status_data,
            }

        status = prefer_known_submission_status(
            activity_data.get("submission_status"),
            cached.get("submission_status"),
        )
        result["submission_status"] = status
        if result.get("type") == "quiz" and status != "unknown":
            details["quiz_attempt_status"] = status
        result["details"] = details
        return result


    @staticmethod
    def _detail_module_name(activity_data: Dict[str, Any]) -> str:
        """Resolve the actual Moodle module instead of defaulting cards to assign."""
        path = urlsplit(str(activity_data.get("url", ""))).path.lower()
        if "/mod/" in path:
            module = path.split("/mod/", 1)[1].split("/", 1)[0]
            if module:
                return module
        activity_type = str(activity_data.get("type", "other")).lower()
        return (
            "assign"
            if activity_type in {"assignment", "assign"}
            else activity_type
        )




    def fetch_full_details(
        self,
        activity_data: Dict[str, Any],
        force_refresh: bool = False,
        cache_generation: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Tải toàn bộ chi tiết (mô tả, trạng thái nộp bài) qua WS API."""
        self._sync_account_scope()
        url = activity_data.get("url")
        if not url:
            return activity_data

        owned_inflight: threading.Event | None = None
        if not force_refresh:
            with self._detail_lock:
                # Re-check cache and in-flight state atomically. This prevents
                # foreground navigation from duplicating an identical warm-up.
                cached = self._get_cached_detail(url)
                if cached is not None:
                    return self._merge_cached_detail_for_activity(
                        activity_data, cached
                    )
                inflight = self._detail_inflight.get(url)
                if inflight is None:
                    owned_inflight = threading.Event()
                    self._detail_inflight[url] = owned_inflight

            if owned_inflight is None:
                inflight.wait()
                cached = self._get_cached_detail(url)
                return (
                    self._merge_cached_detail_for_activity(activity_data, cached)
                    if cached is not None
                    else activity_data
                )

        if cache_generation is None:
            with self._detail_lock:
                cache_generation = self._detail_cache_generation

        try:
            # WS API (stateless, không kick browser)
            if settings.MOODLE_WS_TOKEN or (
                is_demo_account(getattr(settings, "UTH_USERNAME", ""))
                and bool(
                    getattr(
                        getattr(self, "client", None),
                        "has_site_credentials",
                        False,
                    )
                )
            ):
                ws_result = self._fetch_detail_via_ws(activity_data)
                if ws_result:
                    self._set_cached_detail(url, ws_result, cache_generation)
                    return ws_result

            logger.debug("WS API không lấy được chi tiết cho %s.", url)
            return activity_data
        finally:
            if owned_inflight is not None:
                with self._detail_lock:
                    if self._detail_inflight.get(url) is owned_inflight:
                        self._detail_inflight.pop(url, None)
                    owned_inflight.set()
    
    def _fetch_detail_via_ws(self, activity_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết bài tập qua WS API - không cần session, không kick browser."""
        # Xác định cmid và course_id từ activity_data
        url = str(activity_data.get("url") or "")
        activity_type = activity_data.get("type", "other")
        if (
            activity_data.get("site_identity") == "thnn"
            or urlsplit(url).netloc.lower().endswith("thnn.ut.edu.vn")
        ):
            # Tránh gửi nhầm cmid/course_id của THNN sang Moodle WS của courses.ut.edu.vn.
            return None
        
        # Extract cmid từ URL: /mod/assign/view.php?id=CMID
        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        
        if not cmid:
            return None
        
        # Extract course_id - có thể trong activity_data hoặc cần tìm
        course_id = activity_data.get("course_id")
        if not course_id:
            # Thử parse từ URL nếu có courseid param
            if "course=" in url:
                try:
                    course_id = int(url.split("course=")[-1].split("&")[0])
                except (ValueError, IndexError):
                    pass
        
        if not course_id:
            logger.debug("Không có course_id cho WS detail fetch.")
            return None
        
        modulename = self._detail_module_name(activity_data)
        
        try:
            cid = int(course_id) if not isinstance(course_id, int) else course_id
        except (ValueError, TypeError):
            logger.debug("course_id '%s' không hợp lệ, bỏ qua WS detail.", course_id)
            return None

        try:
            ws_details = self.moodle_service.get_assign_details_via_ws(
                cmid=cmid,
                course_id=cid,
                modulename=modulename,
            )
        except Exception as e:
            logger.debug("WS detail fetch error: %s", e)
            return None
        
        if not ws_details:
            return None
        
        # Merge WS details vào activity_data
        result = dict(activity_data)
        details = result.get("details", {})
        if isinstance(details, dict):
            details = dict(details)
        else:
            details = {}
        
        # Update details with WS data
        if ws_details.get('description_html'):
            details['description_html'] = ws_details['description_html']
        if ws_details.get('status_data'):
            details['status_data'] = ws_details['status_data']
        if ws_details.get('course_full_name'):
            details['course_full_name'] = ws_details['course_full_name']
        if ws_details.get('open_time'):
            details['open_time'] = ws_details['open_time']
        if ws_details.get('quiz_info'):
            details['quiz_info'] = ws_details['quiz_info']
        if ws_details.get('quiz_attempt_status'):
            details['quiz_attempt_status'] = ws_details['quiz_attempt_status']
        if ws_details.get('attempts_allowed'):
            details['attempts_allowed'] = ws_details['attempts_allowed']
        if ws_details.get('time_limit'):
            details['time_limit'] = ws_details['time_limit']
        for field in ("quiz_attempts_remaining", "quiz_has_unfinished_attempt"):
            if field in ws_details:
                details[field] = ws_details[field]
        quiz_security = ws_details.get("quiz_security")
        if isinstance(quiz_security, dict) and quiz_security:
            details["quiz_security"] = dict(quiz_security)
        
        result['details'] = details
        
        # Cập nhật submission_status ở top level nếu có
        status_data = ws_details.get('status_data', {})
        quiz_attempt_status = ws_details.get('quiz_attempt_status')
        if activity_type == 'quiz' and quiz_attempt_status:
            result['submission_status'] = normalize_submission_status(quiz_attempt_status)
        elif 'Trạng thái nộp bài' in status_data:
            result['submission_status'] = normalize_submission_status(
                status_data['Trạng thái nộp bài']
            )
        elif 'Trạng thái' in status_data:
            result['submission_status'] = normalize_submission_status(
                status_data['Trạng thái']
            )
        
        return result

    def _fetch_one_activity_status(
        self, activity_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Fetch student state using IDs already present in the catalog."""
        activity_type = activity_data.get("type")
        status = "unknown"

        if activity_type in {"assignment", "assign"}:
            assignment_id = activity_data.get("assignment_id")
            if not assignment_id:
                return None
            response = self.moodle_service.get_submission_status(int(assignment_id))
            if response is None:
                return None
            last_attempt = response.get("lastattempt", {})
            submission = last_attempt.get("submission", {})
            status = normalize_submission_status(submission.get("status"))
            if status == "unknown":
                status = normalize_submission_status(last_attempt.get("gradingstatus"))
        elif activity_type == "quiz":
            quiz_id = activity_data.get("quiz_id")
            if not quiz_id:
                return None
            attempts = self.moodle_service.get_quiz_attempts(int(quiz_id))
            if attempts is None:
                return None
            states = {
                normalize_submission_status(attempt.get("state"))
                for attempt in attempts
                if isinstance(attempt, dict)
            }
            if "submitted" in states:
                status = "submitted"
            elif "in_progress" in states:
                status = "in_progress"
            elif attempts:
                status = next((value for value in states if value != "unknown"), "attempted")
            else:
                status = "not_submitted"
        else:
            return None

        if status == "unknown":
            return None
        url = activity_data.get("url")
        if url:
            self.update_cached_submission_status(url, status)
        result = dict(activity_data)
        result["submission_status"] = status
        details = dict(result.get("details") or {})
        status_data = dict(details.get("status_data") or {})
        status_data["Trạng thái nộp bài"] = status
        details["status_data"] = status_data
        if activity_type == "quiz":
            details["quiz_attempt_status"] = status
        result["details"] = details
        return result

    def prefetch_activity_statuses(
        self,
        activities: List[Dict[str, Any]],
        workers: int = 4,
        cancel_flag=None,
    ) -> Dict[str, Dict[str, Any]]:
        """Hydrate status only, avoiding per-card assignment/quiz catalog calls."""
        self._sync_account_scope()
        pending = [activity for activity in activities if activity.get("url")]
        if not pending or (cancel_flag and cancel_flag()):
            return {}

        workers = max(1, min(workers, 4))
        results: Dict[str, Dict[str, Any]] = {}
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {
            pool.submit(self._fetch_one_activity_status, activity): activity
            for activity in pending
        }
        try:
            while futures:
                if cancel_flag and cancel_flag():
                    break
                completed, _ = wait(
                    futures,
                    timeout=0.05,
                    return_when=FIRST_COMPLETED,
                )
                if not completed:
                    continue
                for future in completed:
                    activity = futures.pop(future)
                    try:
                        enriched = future.result()
                    except Exception:
                        logger.debug(
                            "Status hydration failed for %s",
                            activity.get("url"),
                            exc_info=True,
                        )
                        continue
                    if enriched:
                        results[activity["url"]] = enriched
        finally:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
        return results

    def prefetch_one_detail(
        self,
        activity_data: Dict[str, Any],
        force_refresh: bool = False,
        cache_generation: Optional[int] = None,
    ) -> bool:
        """
        Prefetch chi tiết một hoạt động vào cache. Trả về True nếu đã cache sẵn hoặc fetch thành công.
        Dùng cho background prefetch - không raise exception.
        """
        self._sync_account_scope()
        url = activity_data.get("url")
        if not url:
            return True
        if not force_refresh and self._get_cached_detail(url) is not None:
            return True
        try:
            self.fetch_full_details(activity_data, force_refresh, cache_generation)
            return self._get_cached_detail(url) is not None
        except Exception:
            return False

    def prefetch_all_details(self, activities: List[Dict[str, Any]], workers: int = 4,
                              cancel_flag=None, force_refresh: bool = False) -> int:
        """
        Prefetch chi tiết tất cả hoạt động bằng ThreadPoolExecutor.
        cancel_flag: callable trả về True khi cần dừng.
        force_refresh: bỏ qua cache, load lại từ đầu
        Trả về số lượng đã fetch thành công.
        """
        self._sync_account_scope()
        if force_refresh:
            pending = [a for a in activities if a.get("url")]
        else:
            pending = [a for a in activities if a.get("url") and self._get_cached_detail(a["url"]) is None]
            
        if not pending:
            return 0

        if cancel_flag and cancel_flag():
            return 0

        workers = max(1, min(workers, 10))
        logger.debug(f"[Orchestrator] Prefetch {len(pending)} items với {workers} workers (force_refresh={force_refresh})")
        done = 0

        with self._detail_lock:
            cache_generation = self._detail_cache_generation

        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {
            pool.submit(
                self.prefetch_one_detail,
                activity,
                force_refresh,
                cache_generation,
            )
            for activity in pending
        }
        try:
            while futures:
                if cancel_flag and cancel_flag():
                    logger.debug("[Orchestrator] Prefetch bị huỷ")
                    break
                completed, _ = wait(
                    futures,
                    timeout=0.05,
                    return_when=FIRST_COMPLETED,
                )
                if not completed:
                    continue
                futures.difference_update(completed)
                for future in completed:
                    try:
                        ok = future.result()
                    except Exception:
                        ok = False
                    if ok:
                        done += 1
        finally:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)

        return done

    def _format_assignment(self, assign: Assignment) -> Dict[str, Any]:
        """Convert Assignment model to UI-friendly dict."""
        raw_type = assign.event_type
        is_open  = raw_type.endswith("_open")
        act_type = raw_type.removesuffix("_open") if is_open else raw_type

        crit = float(getattr(settings, "URGENCY_CRITICAL_HOURS", 24) or 24)
        warn = float(getattr(settings, "URGENCY_WARNING_HOURS", 72) or 72)
        data = {
            "id": assign.id,
            "type": act_type,       # module type: quiz/assignment/attendance/deadline/other
            "is_open": is_open,     # True when event = "bắt đầu" / opens
            "title": assign.title,
            "course": assign.course_name,
            "course_id": assign.course_id,
            "deadline": assign.deadline.isoformat(),
            "urgency": assign.urgency_for(crit, warn),
            "url": assign.url,
            "submission_status": normalize_submission_status(
                assign.submission_status
            ),
            "details": {}
        }

        if assign.details:
            data["details"] = assign.details.model_dump()
            if assign.details.open_time:
                data["details"]["open_time"] = assign.details.open_time.isoformat()

        return data
