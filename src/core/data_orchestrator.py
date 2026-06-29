import html
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from core.client import MoodleClient
from core.grade_monitor import GradeMonitor
from config import settings
from models import Assignment
from core import ws_functions

logger = logging.getLogger(__name__)

class DataOrchestrator:
    """
    Điều phối việc lấy dữ liệu trực tiếp từ server Moodle UTH.
    Sử dụng MoodleClient để kết nối và MoodleParser để phân tích.
    """
    
    def __init__(self):
        self.client = MoodleClient()
        self.is_logged_in = False
        self._detail_cache: dict = {}  # url → full activity dict
        self._detail_cache_saved_at: dict[str, float] = {}
        self._detail_cache_lru: OrderedDict[str, None] = OrderedDict()
        self._detail_cache_ttl_seconds = max(60, int(getattr(settings, "DETAIL_CACHE_TTL_SECONDS", 1800)))
        self._detail_cache_max_entries = max(1, int(getattr(settings, "DETAIL_CACHE_MAX_ENTRIES", 100)))
        self._detail_lock = threading.Lock()
        # Smart polling state
        self._last_fetch_ts: int = 0
        self._userid_cache: Optional[int] = None
        # Cycle-level caches (cleared each refresh, avoids redundant API calls)
        self._courses_cache: Optional[List[Dict]] = None
        # Grade monitoring
        self.grade_monitor = GradeMonitor()

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

    def _set_cached_detail(self, url: str, value: Dict[str, Any]):
        with self._detail_lock:
            self._detail_cache[url] = value
            self._detail_cache_saved_at[url] = time.monotonic()
            self._detail_cache_lru[url] = None
            self._detail_cache_lru.move_to_end(url)
            while len(self._detail_cache_lru) > self._detail_cache_max_entries:
                old_url, _ = self._detail_cache_lru.popitem(last=False)
                self._detail_cache.pop(old_url, None)
                self._detail_cache_saved_at.pop(old_url, None)

    def get_cached_details_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._detail_lock:
            return dict(self._detail_cache)

    def clear_detail_cache(self):
        """Xóa sạch bộ nhớ đệm chi tiết (dùng khi refresh thủ công)."""
        with self._detail_lock:
            self._detail_cache.clear()
            self._detail_cache_saved_at.clear()
            self._detail_cache_lru.clear()
        logger.debug("Đã xóa sạch bộ nhớ đệm chi tiết bài tập/quiz.")

    def update_cached_submission_status(self, url: str, new_status: str):
        """Cập nhật trạng thái nộp bài trực tiếp trong cache để đồng bộ giao diện."""
        with self._detail_lock:
            cached = self._detail_cache.get(url)
            if cached:
                cached["submission_status"] = new_status
                details = cached.get("details", {})
                if isinstance(details, dict):
                    status_data = details.get("status_data", {})
                    if isinstance(status_data, dict):
                        status_data["Trạng thái nộp bài"] = new_status

    def _get_userid(self) -> Optional[int]:
        """Get cached userid or fetch from site_info."""
        if self._userid_cache is not None:
            return self._userid_cache
        try:
            info = ws_functions.get_site_info(self.client.call_ws_api)
            if info and 'userid' in info:
                self._userid_cache = info['userid']
                return self._userid_cache
        except Exception as e:
            logger.debug("Failed to get userid: %s", e)
        return None

    def get_updates_since(self, timestamp: int) -> Optional[List[int]]:
        """Check for course updates since last fetch using core_course_get_updates_since.

        Returns:
            list of changed course IDs if changes detected,
            empty list if no changes,
            None if error (caller should do full fetch).
        """
        if timestamp <= 0:
            return None  # No previous fetch, must do full fetch
        try:
            # BUG-02 fix: get_enrolled_courses takes classification, not userid
            courses = ws_functions.get_enrolled_courses(
                self.client.call_ws_api
            )
            if not courses:
                return None
            changed_courses = []
            for course in courses:
                cid = course.get('id')
                if not cid:
                    continue
                updates = ws_functions.get_course_updates_since(
                    self.client.call_ws_api, cid, timestamp
                )
                if updates:  # Non-empty = something changed
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
        if self.is_logged_in:
            return True
        
        username = settings.UTH_USERNAME
        password = settings.UTH_PASSWORD
        
        if not username or not password:
            logger.error("Chưa cấu hình MSSV hoặc mật khẩu trong Settings.")
            return False
        
        self.is_logged_in = self.client.login(username, password)
        if self.is_logged_in:
            logger.info("Đăng nhập WS token thành công.")
        return self.is_logged_in

    def get_latest_activities(self) -> List[Dict[str, Any]]:
        """Đăng nhập và lấy danh sách hoạt động mới nhất qua WS API."""
        ws_result = self._fetch_via_ws_api()
        if ws_result is not None:
            return ws_result
        logger.error("Không thể lấy dữ liệu từ WS API.")
        return []

    def check_grade_changes(self) -> List:
        """Check for grade changes using GradeMonitor.

        Returns:
            List of GradeChange objects if changes detected, empty list otherwise.
        """
        userid = self._get_userid()
        if userid is None:
            return []
        return self.grade_monitor.check_for_changes(
            self.client.call_ws_api, userid
        )
    
    def _fetch_via_ws_api(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy activities bằng Moodle Web Services API (stateless, JSON).
        
        PERF-OPT: Parallel API calls + eliminated redundant fetches.
        Kết hợp 2 nguồn:
        1. calendar action events (bài chưa nộp, có deadline tương lai)
        2. mod_assign_get_assignments (TẤT CẢ bài tập kể cả đã nộp)
        Merge lại để bài đã nộp không bị mất khỏi danh sách.
        """
        try:
            from datetime import datetime
            now_ts = int(datetime.now().timestamp())
            
            # ── PERF: Clear cycle caches at start of each refresh ──
            self._courses_cache = None
            
            # ── PERF: Step 1 — Get userid (needed for courses) ──
            userid = self._get_userid()
            
            # ── PERF: Step 2 — Parallel fetch: calendar + courses ──
            calendar_result = None
            courses_result = None
            
            def _fetch_calendar():
                return self.client.call_ws_api(
                    'core_calendar_get_action_events_by_timesort',
                    timesortfrom=now_ts,
                    timesortto=now_ts + (90 * 24 * 3600),
                    limitnum=50,
                )
            
            def _fetch_courses():
                if not userid:
                    return None
                return self.client.call_ws_api(
                    'core_enrol_get_users_courses', userid=userid
                )
            
            with ThreadPoolExecutor(max_workers=2) as pool:
                f_cal = pool.submit(_fetch_calendar)
                f_courses = pool.submit(_fetch_courses)
                calendar_result = f_cal.result(timeout=25)
                courses_result = f_courses.result(timeout=25)
            
            # Cache courses for reuse by grade_check/notification
            if courses_result and isinstance(courses_result, list):
                self._courses_cache = courses_result
            
            events = []
            if calendar_result and isinstance(calendar_result, dict):
                events = calendar_result.get('events', [])
            
            # Convert WS events to UTHelper format
            results = ws_functions.ws_events_to_assignments(events) if events else []
            
            # ── PERF: Step 3 — Merge assignments using PRE-FETCHED data ──
            course_ids = [c['id'] for c in (self._courses_cache or []) if isinstance(c, dict) and 'id' in c]
            try:
                results = self._merge_all_assignments(results, userid=userid, course_ids=course_ids)
            except Exception as e:
                logger.debug("Merge assignments failed (non-critical): %s", e)
            
            if not results:
                logger.info("WS API trả về 0 activities (hợp lệ — có thể không có bài tập).")
                return results  # BUG-10 fix: return [] not None for valid empty result
            
            logger.info("WS API trả về %d activities (merged).", len(results))
            self.is_logged_in = True  # WS token works = we're authenticated
            self._last_fetch_ts = int(datetime.now().timestamp())  # Smart poll: mark last fetch
            return results
        except Exception as e:
            logger.warning("WS API fetch thất bại: %s", e)
            return None
    
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
        again — wasting ~1.4s).
        
        Moodle calendar API loại bỏ events khi bài đã nộp (no action needed).
        Lấy tất cả assignments từ enrolled courses, chỉ giữ bài có deadline
        từ đầu tháng hiện tại → FETCH_MONTHS tháng tới (theo setting).
        """
        from datetime import datetime
        
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
        
        # ── PERF: Use pre-fetched data instead of re-calling APIs ──
        if not course_ids:
            # Fallback: fetch if caller didn't provide (backward compat)
            try:
                if userid is None:
                    userid = self._get_userid()
                if not userid:
                    return calendar_results
                courses_result = self._courses_cache
                if not courses_result:
                    courses_result = self.client.call_ws_api(
                        'core_enrol_get_users_courses', userid=userid
                    )
                if not courses_result or not isinstance(courses_result, list):
                    return calendar_results
                course_ids = [c['id'] for c in courses_result if isinstance(c, dict) and 'id' in c]
            except Exception:
                return calendar_results
        
        if not course_ids:
            return calendar_results
        
        # Lấy assignments
        all_assigns = ws_functions.get_assignments(self.client.call_ws_api, course_ids)
        if not all_assigns:
            return calendar_results
        
        # Build lookup: cmid → index in calendar_results (for cutoff enrichment)
        existing_cmids = set()
        cmid_to_index: dict[int, int] = {}
        for idx, item in enumerate(calendar_results):
            url = item.get('url', '')
            if 'id=' in url:
                try:
                    _cmid = int(url.split('id=')[-1].split('&')[0])
                    existing_cmids.add(_cmid)
                    cmid_to_index[_cmid] = idx
                except (ValueError, IndexError):
                    pass
        
        # Merge bài tập thiếu (chỉ trong khoảng thời gian)
        now_ts = int(now.timestamp())
        merged_count = 0
        
        for course_data in all_assigns:
            if not isinstance(course_data, dict):
                continue
            course_name = html.unescape(course_data.get('fullname', ''))
            course_id = course_data.get('id', '')
            
            for assign in course_data.get('assignments', []):
                cmid = assign.get('cmid')
                
                # Enrich existing calendar items with cutoff data
                if cmid and cmid in existing_cmids:
                    _idx = cmid_to_index.get(cmid)
                    if _idx is not None and _idx < len(calendar_results):
                        _existing = calendar_results[_idx]
                        _cutoff = assign.get('cutoffdate', 0)
                        _due = assign.get('duedate', 0)
                        if _cutoff > 0 and _cutoff != _due:
                            if now_ts > _due and now_ts < _cutoff:
                                _existing['late_status'] = 'late_allowed'
                            elif now_ts >= _cutoff:
                                _existing['late_status'] = 'closed'
                            else:
                                _existing['late_status'] = 'open'
                        else:
                            _existing['late_status'] = 'open' if now_ts < _due else 'closed'
                        _existing['cutoff_date'] = _cutoff
                    continue
                
                duedate = assign.get('duedate', 0)
                if not duedate or duedate < min_ts or duedate > max_ts:
                    continue
                
                cutoffdate = assign.get('cutoffdate', 0)
                
                # Compute late_status based on cutoffdate vs duedate
                if cutoffdate > 0 and cutoffdate != duedate:
                    if now_ts > duedate and now_ts < cutoffdate:
                        late_status = 'late_allowed'
                    elif now_ts >= cutoffdate:
                        late_status = 'closed'
                    else:
                        late_status = 'open'
                else:
                    late_status = 'open' if now_ts < duedate else 'closed'
                
                assign_url = f"{settings.MOODLE_BASE_URL}/mod/assign/view.php?id={cmid}" if cmid else ''
                
                # BUG-12 fix: standardize to ISO format for consistent sorting
                deadline_str = datetime.fromtimestamp(duedate).strftime('%Y-%m-%d %H:%M:%S')
                remaining = duedate - now_ts
                if remaining < 0:
                    urgency = 'overdue'
                elif remaining < 86400:
                    urgency = 'critical'
                elif remaining < 3 * 86400:
                    urgency = 'warning'
                else:
                    urgency = 'safe'
                
                calendar_results.append({
                    'id': f"assign_{assign.get('id', cmid)}",
                    'title': html.unescape(assign.get('name', 'Không tên')),
                    'course_name': course_name,
                    'course': course_name,
                    'course_id': course_id,
                    'deadline': deadline_str,
                    'deadline_str': deadline_str,
                    'url': assign_url,
                    'type': 'assignment',
                    'urgency': urgency,
                    'source': 'ws_assign_api',
                    'submission_status': 'unknown',
                    'details': {},
                    'is_open': False,
                    'cutoff_date': cutoffdate,
                    'late_status': late_status,
                })
                merged_count += 1
        
        if merged_count > 0:
            logger.info("Merged %d assignments (tháng %d → +%d tháng).",
                        merged_count, now.month, fetch_months)
        
        return calendar_results
    
    async def get_latest_activities_async(self) -> List[Dict[str, Any]]:
        """Phiên bản async của get_latest_activities — dùng trực tiếp trong event loop."""
        ws_result = await self._fetch_via_ws_api_async()
        if ws_result is not None:
            return ws_result
        logger.error("Không thể lấy dữ liệu async từ WS API.")
        return []




    def fetch_full_details(self, activity_data: Dict[str, Any], force_refresh: bool = False) -> Dict[str, Any]:
        """Tải toàn bộ chi tiết (mô tả, trạng thái nộp bài) qua WS API."""
        url = activity_data.get("url")
        if not url:
            return activity_data

        # Trả về cache nếu đã tải trước đó và không force refresh
        if not force_refresh:
            cached = self._get_cached_detail(url)
            if cached is not None:
                return cached
        
        # ── WS API (stateless, không kick browser) ──
        if settings.MOODLE_WS_TOKEN:
            ws_result = self._fetch_detail_via_ws(activity_data)
            if ws_result:
                self._set_cached_detail(url, ws_result)
                return ws_result
        
        logger.debug("WS API không lấy được chi tiết cho %s.", url)
        return activity_data
    
    def _fetch_detail_via_ws(self, activity_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết bài tập qua WS API — không cần session, không kick browser."""
        from core.ws_functions import get_assign_details_via_ws
        
        # Xác định cmid và course_id từ activity_data
        url = activity_data.get("url", "")
        activity_type = activity_data.get("type", "other")
        
        # Extract cmid từ URL: /mod/assign/view.php?id=CMID
        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        
        if not cmid:
            return None
        
        # Extract course_id — có thể trong activity_data hoặc cần tìm
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
        
        # Xác định modulename từ type
        type_to_module = {
            'assignment': 'assign',
            'assign': 'assign',
            'quiz': 'quiz',
        }
        modulename = type_to_module.get(activity_type, 'assign')
        
        # Nếu URL chứa /mod/quiz/ thì là quiz
        if '/mod/quiz/' in url:
            modulename = 'quiz'
        elif '/mod/assign/' in url:
            modulename = 'assign'
        
        try:
            cid = int(course_id) if not isinstance(course_id, int) else course_id
        except (ValueError, TypeError):
            logger.debug("course_id '%s' không hợp lệ, bỏ qua WS detail.", course_id)
            return None

        try:
            ws_details = get_assign_details_via_ws(
                self.client.call_ws_api,
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
        if ws_details.get('attempts_allowed'):
            details['attempts_allowed'] = ws_details['attempts_allowed']
        if ws_details.get('time_limit'):
            details['time_limit'] = ws_details['time_limit']
        
        result['details'] = details
        
        # Cập nhật submission_status ở top level nếu có
        status_data = ws_details.get('status_data', {})
        if 'Trạng thái nộp bài' in status_data:
            result['submission_status'] = status_data['Trạng thái nộp bài']
        elif 'Trạng thái' in status_data:
            result['submission_status'] = status_data['Trạng thái']
        
        return result

    def prefetch_one_detail(self, activity_data: Dict[str, Any], force_refresh: bool = False) -> bool:
        """
        Prefetch chi tiết một hoạt động vào cache. Trả về True nếu đã cache sẵn hoặc fetch thành công.
        Dùng cho background prefetch - không raise exception.
        """
        url = activity_data.get("url")
        if not url:
            return True
        if not force_refresh and self._get_cached_detail(url) is not None:
            return True
        try:
            self.fetch_full_details(activity_data, force_refresh)
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
        if force_refresh:
            pending = [a for a in activities if a.get("url")]
        else:
            pending = [a for a in activities if a.get("url") and self._get_cached_detail(a["url"]) is None]
            
        if not pending:
            return 0

        workers = max(1, min(workers, 10))
        logger.debug(f"[Orchestrator] Prefetch {len(pending)} items với {workers} workers (force_refresh={force_refresh})")
        done = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.prefetch_one_detail, a, force_refresh): a for a in pending}
            for future in as_completed(futures):
                if cancel_flag and cancel_flag():
                    pool.shutdown(wait=False, cancel_futures=True)
                    logger.debug("[Orchestrator] Prefetch bị huỷ")
                    break
                try:
                    ok = future.result(timeout=30)
                except TimeoutError:
                    logger.warning("Prefetch detail timeout after 30s")
                    ok = False
                if ok:
                    done += 1

        return done

    def _format_assignment(self, assign: Assignment) -> Dict[str, Any]:
        """Convert Assignment model to UI-friendly dict."""
        raw_type = assign.event_type
        is_open  = raw_type.endswith("_open")
        act_type = raw_type.removesuffix("_open") if is_open else raw_type

        data = {
            "id": assign.id,
            "type": act_type,       # module type: quiz/assignment/attendance/deadline/other
            "is_open": is_open,     # True when event = "bắt đầu" / opens
            "title": assign.title,
            "course": assign.course_name,
            "course_id": assign.course_id,
            "deadline": assign.deadline.isoformat(),
            "urgency": assign.urgency,
            "url": assign.url,
            "submission_status": assign.submission_status,
            "details": {}
        }

        if assign.details:
            data["details"] = assign.details.model_dump()
            if assign.details.open_time:
                data["details"]["open_time"] = assign.details.open_time.isoformat()

        return data
