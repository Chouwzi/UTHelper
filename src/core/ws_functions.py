"""Moodle Web Services API wrapper functions.

Cung cấp interface gọn cho các WS function thường dùng.
Tất cả function nhận `call_api` (callable gọi WS endpoint) làm tham số đầu.

Endpoints:
  - Token:  /login/token.php
  - API:    /webservice/rest/server.php
"""
import logging
from typing import Optional, List, Dict, Any, Callable
from datetime import datetime

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WS function wrappers
# ---------------------------------------------------------------------------

def get_site_info(call_api: Callable) -> Optional[Dict[str, Any]]:
    """core_webservice_get_site_info — xác thực token và lấy thông tin user/site."""
    try:
        return call_api('core_webservice_get_site_info')
    except Exception as e:
        logger.error("Lỗi khi gọi core_webservice_get_site_info: %s", e)
        return None


def get_calendar_action_events(
    call_api: Callable,
    timesort_from: Optional[int] = None,
    timesort_to: Optional[int] = None,
    limit: int = 50,
) -> Optional[List[Dict[str, Any]]]:
    """core_calendar_get_action_events_by_timesort — lấy events (assignments, quizzes).

    Args:
        call_api: Callable gọi WS API.
        timesort_from: Unix timestamp bắt đầu (mặc định: thời điểm hiện tại).
        timesort_to: Unix timestamp kết thúc (mặc định: +90 ngày).
        limit: Số event tối đa trả về.
    """
    if timesort_from is None:
        timesort_from = int(datetime.now().timestamp())
    if timesort_to is None:
        timesort_to = timesort_from + (90 * 24 * 3600)  # 90 ngày tới

    params = {
        'timesortfrom': timesort_from,
        'timesortto': timesort_to,
        'limitnum': limit,
    }
    try:
        result = call_api('core_calendar_get_action_events_by_timesort', **params)
    except Exception as e:
        logger.error("Lỗi khi gọi get_action_events_by_timesort: %s", e)
        return None

    if result and isinstance(result, dict) and 'events' in result:
        return result['events']
    return None


def get_enrolled_courses(
    call_api: Callable,
    classification: str = 'all',
) -> Optional[List[Dict[str, Any]]]:
    """core_course_get_enrolled_courses_by_timeline_classification."""
    params = {'classification': classification}
    try:
        result = call_api(
            'core_course_get_enrolled_courses_by_timeline_classification',
            **params,
        )
    except Exception as e:
        logger.error("Lỗi khi gọi get_enrolled_courses: %s", e)
        return None

    if result and isinstance(result, dict) and 'courses' in result:
        return result['courses']
    return None


def get_assignments(
    call_api: Callable,
    course_ids: Optional[List[int]] = None,
) -> Optional[List[Dict[str, Any]]]:
    """mod_assign_get_assignments — chi tiết assignments theo course."""
    params: Dict[str, Any] = {}
    if course_ids:
        for i, cid in enumerate(course_ids):
            params[f'courseids[{i}]'] = cid

    try:
        result = call_api('mod_assign_get_assignments', **params)
    except Exception as e:
        logger.error("Lỗi khi gọi mod_assign_get_assignments: %s", e)
        return None

    if result and isinstance(result, dict) and 'courses' in result:
        return result['courses']
    return None


def get_calendar_events(
    call_api: Callable,
    time_start: Optional[int] = None,
    time_end: Optional[int] = None,
) -> Optional[List[Dict[str, Any]]]:
    """core_calendar_get_calendar_events — legacy calendar events."""
    params: Dict[str, Any] = {}
    if time_start is not None:
        params['options[timestart]'] = time_start
    if time_end is not None:
        params['options[timeend]'] = time_end

    try:
        result = call_api('core_calendar_get_calendar_events', **params)
    except Exception as e:
        logger.error("Lỗi khi gọi core_calendar_get_calendar_events: %s", e)
        return None

    if result and isinstance(result, dict) and 'events' in result:
        return result['events']
    return None


# ---------------------------------------------------------------------------
# Event → Assignment converter
# ---------------------------------------------------------------------------

# Map WS modulename → loại bài tập trong UTHelper
_MODULE_TYPE_MAP: Dict[str, str] = {
    'assign':     'assignment',
    'quiz':       'quiz',
    'attendance': 'attendance',
    'scorm':      'quiz',
    'lesson':     'quiz',
}


def ws_events_to_assignments(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert WS API events sang dict format tương thích với phần còn lại của UTHelper.

    Maps WS API event fields sang cùng cấu trúc dict mà giao diện
    có thể sử dụng.  Trường ``source`` = ``'ws_api'`` để phân biệt
    với dữ liệu scrape HTML.
    """
    # Import settings ở đây tránh circular import ở module-level
    from config import settings

    if not events:
        return []

    assignments: List[Dict[str, Any]] = []
    now_ts = datetime.now().timestamp()

    for evt in events:
        if not isinstance(evt, dict):
            logger.debug("Bỏ qua event không phải dict: %r", type(evt))
            continue

        try:
            # --- Phân loại event ---
            modulename = evt.get('modulename', '') or ''
            event_type = _MODULE_TYPE_MAP.get(modulename, 'other')

            # Fallback: kiểm tra actionname cho event dạng "open"
            actionname = str(evt.get('actionname', '')).lower()
            if event_type == 'other' and 'open' in actionname:
                event_type = 'open'

            # --- Deadline ---
            timestart = evt.get('timesort') or evt.get('timestart') or 0
            if isinstance(timestart, str):
                try:
                    timestart = int(timestart)
                except (ValueError, TypeError):
                    timestart = 0

            deadline = ''
            if timestart:
                try:
                    deadline = datetime.fromtimestamp(timestart).strftime(
                        '%Y-%m-%d %H:%M:%S'
                    )
                except (OSError, ValueError, OverflowError):
                    deadline = ''

            # --- URL ---
            url = evt.get('url', '') or ''
            if not url:
                course_data = evt.get('course') or {}
                course_id = course_data.get('id', '') if isinstance(course_data, dict) else ''
                cm_id = evt.get('instance', '')
                if modulename and cm_id:
                    url = (
                        f"{settings.MOODLE_BASE_URL}/mod/{modulename}"
                        f"/view.php?id={cm_id}"
                    )

            # --- Urgency ---
            urgency = 'safe'
            if timestart:
                hours_left = (timestart - now_ts) / 3600
                if hours_left < 0:
                    urgency = 'overdue'
                elif hours_left < settings.URGENCY_CRITICAL_HOURS:
                    urgency = 'critical'
                elif hours_left < settings.URGENCY_WARNING_HOURS:
                    urgency = 'warning'

            # --- Course info (null-safe) ---
            course_data = evt.get('course') or {}
            if not isinstance(course_data, dict):
                course_data = {}
            course_fullname = course_data.get('fullname', '') or ''

            # --- Build id ---
            evt_id = evt.get('id', '') or ''
            if not evt_id and modulename and cm_id:
                evt_id = f"{modulename}_{cm_id}"

            # --- Build dict ---
            assignment: Dict[str, Any] = {
                'id': str(evt_id),
                'title': evt.get('name') or 'Không tên',
                'course_name': course_fullname,
                'course': course_fullname,
                'deadline': deadline,
                'deadline_str': deadline,
                'url': url,
                'type': event_type,
                'urgency': urgency,
                'source': 'ws_api',
                'submission_status': 'unknown',
                'details': {},
                'is_open': event_type == 'open',
            }
            assignments.append(assignment)

        except Exception as e:
            # Không để một event lỗi làm hỏng toàn bộ danh sách
            logger.warning(
                "Lỗi khi chuyển đổi WS event sang assignment: %s", e
            )

    logger.debug(
        "Đã chuyển đổi %d/%d WS events sang assignments",
        len(assignments),
        len(events),
    )
    return assignments
