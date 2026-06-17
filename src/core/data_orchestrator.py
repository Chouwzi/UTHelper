import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from core.client import MoodleClient
from core.parser import MoodleParser
from models import Assignment
from config import settings
from core.time_utils import parse_datetime
from core import ws_functions

logger = logging.getLogger(__name__)

# Khởi tạo ThreadPool duy nhất để parse HTML giảm thiểu overhead của Process trên app Desktop
_PARSER_POOL = None
def get_parser_pool():
    global _PARSER_POOL
    if _PARSER_POOL is None:
        # Use configured prefetch workers (bounded)
        workers = max(1, int(getattr(settings, 'PREFETCH_WORKERS', 4)))
        _PARSER_POOL = ThreadPoolExecutor(max_workers=workers)
    return _PARSER_POOL

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

    def login(self) -> bool:
        """Thực hiện đăng nhập bằng thông tin từ settings."""
        if self.is_logged_in:
            return True
        
        # Lấy thông tin từ config (thông tin trong file .env)
        username = settings.UTH_USERNAME
        password = settings.UTH_PASSWORD
        
        if not username or not password:
            logger.error("Chưa cấu hình MSSV hoặc mật khẩu trong .env")
            return False
            
        self.is_logged_in = self.client.login(username, password)
        return self.is_logged_in

    def get_latest_activities(self) -> List[Dict[str, Any]]:
        """
        Đăng nhập và lấy danh sách hoạt động mới nhất.
        Ưu tiên dùng WS API (nhanh, JSON), fallback sang HTML scraping.
        """
        # Thử WS API trước nếu được bật
        if settings.USE_WS_API:
            ws_result = self._fetch_via_ws_api()
            if ws_result is not None:
                return ws_result
            logger.info("WS API không khả dụng, fallback sang HTML scraping.")
        
        return self._fetch_via_scraping()
    
    def _fetch_via_ws_api(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy activities bằng Moodle Web Services API (stateless, JSON)."""
        try:
            events = ws_functions.get_calendar_action_events(
                self.client.call_ws_api,
                limit=100,
            )
            if events is None:
                return None
            
            # Convert WS events to UTHelper format
            results = ws_functions.ws_events_to_assignments(events)
            logger.info("WS API trả về %d events thành công.", len(results))
            self.is_logged_in = True  # WS token works = we're authenticated
            return results
        except Exception as e:
            logger.warning("WS API fetch thất bại: %s", e)
            return None
    
    async def _fetch_via_ws_api_async(self) -> Optional[List[Dict[str, Any]]]:
        """Lấy activities bằng WS API bất đồng bộ (httpx, non-blocking)."""
        try:
            events = ws_functions.get_calendar_action_events(
                self.client.call_ws_api_async,  # async callable
                limit=100,
            )
            # If call_api is async, we need to await the inner calls
            # But ws_functions calls call_api synchronously, so we use the sync version
            # For true async, call WS API directly
            import asyncio
            result = await self.client.call_ws_api_async(
                'core_calendar_get_action_events_by_timesort',
                timesortfrom=int(__import__('datetime').datetime.now().timestamp()),
                timesortto=int(__import__('datetime').datetime.now().timestamp()) + (90 * 24 * 3600),
                limitnum=100,
            )
            if result is None:
                return None
            
            events = result.get('events', []) if isinstance(result, dict) else []
            if not events:
                return None
            
            results = ws_functions.ws_events_to_assignments(events)
            logger.info("WS API async trả về %d events.", len(results))
            self.is_logged_in = True
            return results
        except Exception as e:
            logger.warning("WS API async fetch thất bại: %s", e)
            return None
    
    async def get_latest_activities_async(self) -> List[Dict[str, Any]]:
        """Phiên bản async của get_latest_activities — dùng trực tiếp trong event loop."""
        if settings.USE_WS_API:
            ws_result = await self._fetch_via_ws_api_async()
            if ws_result is not None:
                return ws_result
            logger.info("WS API async không khả dụng, fallback sang scraping (sync thread).")
        
        # Fallback to sync scraping in thread
        import asyncio
        return await asyncio.to_thread(self._fetch_via_scraping)


    def _fetch_via_scraping(self) -> List[Dict[str, Any]]:
        """Lấy activities bằng HTML scraping (fallback)."""
        if not self.login():
            logger.error("Đăng nhập thất bại, không thể lấy dữ liệu.")
            return []
            
        all_htmls = []
        from datetime import datetime
        now = datetime.now()
        
        # Đảm bảo số tháng tải về trong quy định (1-3)
        months_to_fetch = max(1, min(int(getattr(settings, "FETCH_MONTHS", 1)), 3))
        
        for i in range(months_to_fetch):
            month = now.month + i
            year = now.year
            if month > 12:
                month -= 12
                year += 1
            
            html = self.client.fetch_calendar(month=month, year=year)
            if html:
                all_htmls.append(html)

        if not all_htmls:
            return []

        all_assignments = []
        
        # Parse từng trang lịch trả về
        for html in all_htmls:
            future = get_parser_pool().submit(MoodleParser.parse_assignments, html)
            try:
                # Merge assignments nhưng tránh duplicate do sự kiện vắt qua 2 tháng
                parsed_list = future.result()
                for p in parsed_list:
                    if not any(a.id == p.id for a in all_assignments):
                        all_assignments.append(p)
            except Exception as e:
                logger.error("[Orchestrator] Lỗi parse lịch: %s", e)

        # Chuyển đổi sang format chuẩn cho UI
        results = [self._format_assignment(a) for a in all_assignments]
        return results


    def fetch_full_details(self, activity_data: Dict[str, Any], force_refresh: bool = False) -> Dict[str, Any]:
        """
        Tải toàn bộ chi tiết (mô tả, trạng thái nộp bài) từ URL của hoạt động.
        Kết quả được cache theo URL để tránh gọi lại khi mở lại cùng hoạt động.
        Nếu force_refresh=True, bỏ qua cache và tải lại dữ liệu mới nhất.
        """
        url = activity_data.get("url")
        if not url:
            return activity_data

        # Trả về cache nếu đã tải trước đó và không force refresh
        if not force_refresh:
            cached = self._get_cached_detail(url)
            if cached is not None:
                return cached
            
        if not self.login():
            return activity_data
            
        html = self.client.fetch_url(url)
        if not html:
            return activity_data

        # Parse chi tiết trang trên ProcessPool (để giải phóng GIL trong ThreadPool hiện tại)
        future = get_parser_pool().submit(MoodleParser.parse_activity_page, html, url)
        full_activity = future.result()
        
        if full_activity:
            # Ghi đè lại deadline từ timeline (activity_data) nếu parse page không thấy deadline (bị trả về 2099)
            if full_activity.deadline.year >= 2099 and activity_data.get("deadline"):
                parsed_deadline = parse_datetime(activity_data["deadline"])
                if parsed_deadline:
                    full_activity.deadline = parsed_deadline
                    
            # Kế thừa title gốc từ lịch nếu không trang chi tiết trống hoặc không chính xác
            if full_activity.title == "Hoạt động không tên" and activity_data.get("title"):
                full_activity.title = activity_data["title"]
                
            # Kế thừa open_time nếu có (từ timeline sang model details)
            timeline_open_time = activity_data.get("details", {}).get("open_time")
            if timeline_open_time and full_activity.details:
                # Nếu trang chi tiết không có open_time riêng, kế thừa từ timeline
                if not full_activity.details.open_time:
                    parsed_ot = parse_datetime(timeline_open_time)
                    if parsed_ot:
                        full_activity.details.open_time = parsed_ot
            elif timeline_open_time and not full_activity.details:
                from models import ActivityDetail
                parsed_ot = parse_datetime(timeline_open_time)
                if parsed_ot:
                    full_activity.details = ActivityDetail(open_time=parsed_ot)

            # Kế thừa cờ is_open từ dữ liệu lịch
            if activity_data.get("is_open"):
                # Cập nhật event_type để _format_assignment nhận diện được đây là sự kiện open
                if not full_activity.event_type.endswith("_open"):
                    full_activity.event_type = f"{full_activity.event_type}_open"

            # Cập nhật dữ liệu cũ với thông tin chi tiết mới
            result = self._format_assignment(full_activity)
            self._set_cached_detail(url, result)
            return result

        return activity_data

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
                if future.result():
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
