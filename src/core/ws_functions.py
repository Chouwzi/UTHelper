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


def get_submission_status(
    call_api: Callable,
    assign_id: int,
) -> Optional[Dict[str, Any]]:
    """mod_assign_get_submission_status — trạng thái nộp bài của user.
    
    LƯU Ý: assign_id là ID thật của assignment (từ mod_assign_get_assignments),
    KHÔNG PHẢI cmid (course module ID) từ calendar events.
    
    Returns dict with keys: lastattempt, feedback, warnings, etc.
    """
    try:
        result = call_api('mod_assign_get_submission_status', assignid=assign_id)
    except Exception as e:
        logger.error("Lỗi khi gọi mod_assign_get_submission_status: %s", e)
        return None

    if result and isinstance(result, dict) and 'exception' not in result:
        return result
    if isinstance(result, dict):
        logger.debug("Submission status error: %s", result.get('message', ''))
    return None


def get_quizzes_by_courses(
    call_api: Callable,
    course_ids: List[int],
) -> Optional[List[Dict[str, Any]]]:
    """mod_quiz_get_quizzes_by_courses — thông tin quiz theo course."""
    params: Dict[str, Any] = {}
    for i, cid in enumerate(course_ids):
        params[f'courseids[{i}]'] = cid
    
    try:
        result = call_api('mod_quiz_get_quizzes_by_courses', **params)
    except Exception as e:
        logger.error("Lỗi khi gọi mod_quiz_get_quizzes_by_courses: %s", e)
        return None

    if result and isinstance(result, dict) and 'quizzes' in result:
        return result['quizzes']
    return None


def get_quiz_attempts(
    call_api: Callable,
    quiz_id: int,
    status: str = 'all',
) -> Optional[List[Dict[str, Any]]]:
    """mod_quiz_get_user_attempts — lịch sử làm quiz của user."""
    try:
        result = call_api('mod_quiz_get_user_attempts', quizid=quiz_id, status=status)
    except Exception as e:
        logger.error("Lỗi khi gọi mod_quiz_get_user_attempts: %s", e)
        return None

    if result and isinstance(result, dict) and 'attempts' in result:
        return result['attempts']
    return None


def resolve_cmid_to_assign_id(
    call_api: Callable,
    cmid: int,
    course_id: int,
) -> Optional[int]:
    """Chuyển đổi cmid (course module ID) sang assign ID thật.
    
    Calendar events trả về `instance` = cmid, nhưng mod_assign_get_submission_status
    cần assign ID thật. Phải gọi mod_assign_get_assignments cho course để tìm mapping.
    """
    courses = get_assignments(call_api, course_ids=[course_id])
    if not courses:
        return None
    
    for course in courses:
        for assign in course.get('assignments', []):
            if assign.get('cmid') == cmid:
                return assign.get('id')
    
    return None


def get_assign_details_via_ws(
    call_api: Callable,
    cmid: int,
    course_id: int,
    modulename: str = 'assign',
) -> Optional[Dict[str, Any]]:
    """Lấy full chi tiết bài tập qua WS API — thay thế HTML scraping.
    
    Trả về dict tương thích với ActivityDetail format:
    {
        'description_html': str,
        'status_data': dict,
        'course_full_name': str,
        'open_time': str (ISO),
        'quiz_info': list,
        'attempts_allowed': str,
        'time_limit': str,
    }
    """
    details: Dict[str, Any] = {
        'description_html': '',
        'status_data': {},
        'course_full_name': '',
        'open_time': None,
        'quiz_info': [],
        'attempts_allowed': None,
        'time_limit': None,
    }
    
    if modulename == 'assign':
        return _get_assign_detail(call_api, cmid, course_id, details)
    elif modulename == 'quiz':
        return _get_quiz_detail(call_api, cmid, course_id, details)
    else:
        # Cho các module khác (groupselect, etc.) — chỉ lấy course name
        _fill_course_name(call_api, course_id, details)
        return details


def _fill_course_name(call_api: Callable, course_id: int, details: Dict[str, Any]):
    """Điền tên môn học từ enrolled courses."""
    try:
        from config import settings
        result = call_api('core_webservice_get_site_info')
        if result and 'userid' in result:
            courses = call_api('core_enrol_get_users_courses', userid=result['userid'])
            if isinstance(courses, list):
                for c in courses:
                    if c.get('id') == course_id:
                        details['course_full_name'] = c.get('fullname', '')
                        break
    except Exception:
        pass


def _get_assign_detail(
    call_api: Callable, cmid: int, course_id: int, details: Dict[str, Any]
) -> Dict[str, Any]:
    """Lấy chi tiết assignment qua WS API."""
    courses = get_assignments(call_api, course_ids=[course_id])
    if not courses:
        return details
    
    assign_data = None
    assign_id = None
    for course in courses:
        details['course_full_name'] = course.get('fullname', '')
        for assign in course.get('assignments', []):
            if assign.get('cmid') == cmid:
                assign_data = assign
                assign_id = assign.get('id')
                break
        if assign_data:
            break
    
    if not assign_data:
        return details
    
    # Mô tả
    intro = assign_data.get('intro', '') or ''
    details['description_html'] = intro
    
    # Thời gian mở
    allow_from = assign_data.get('allowsubmissionsfromdate', 0)
    if allow_from and allow_from > 0:
        try:
            details['open_time'] = datetime.fromtimestamp(allow_from).isoformat()
        except (OSError, ValueError):
            pass
    
    # Submission status
    if assign_id:
        sub_status = get_submission_status(call_api, assign_id)
        if sub_status:
            la = sub_status.get('lastattempt', {})
            if la:
                submission = la.get('submission', {})
                raw_status = submission.get('status', '')
                
                # Map Moodle status → Vietnamese
                status_map = {
                    'submitted': 'Đã nộp',
                    'new': 'Chưa nộp',
                    'draft': 'Bản nháp',
                    'reopened': 'Được mở lại',
                }
                
                grading_map = {
                    'notgraded': 'Chưa chấm',
                    'graded': 'Đã chấm điểm',
                    'released': 'Đã công bố',
                }
                
                details['status_data']['Trạng thái nộp bài'] = status_map.get(raw_status, raw_status)
                
                grading = la.get('gradingstatus', '')
                if grading:
                    details['status_data']['Chấm điểm'] = grading_map.get(grading, grading)
                
                # Thời gian nộp
                time_modified = submission.get('timemodified', 0)
                if time_modified and time_modified > 0:
                    try:
                        dt = datetime.fromtimestamp(time_modified)
                        details['status_data']['Thời gian nộp'] = dt.strftime('%H:%M %d/%m/%Y')
                    except (OSError, ValueError):
                        pass
                
                # Can edit / can submit
                if la.get('canedit'):
                    details['status_data']['Chỉnh sửa'] = 'Có thể chỉnh sửa'
                if la.get('locked'):
                    details['status_data']['Khóa'] = 'Bài nộp đã bị khóa'
            
            # Feedback / Grade
            fb = sub_status.get('feedback', {})
            if fb:
                grade = fb.get('grade', {})
                if grade and grade.get('grade') is not None:
                    details['status_data']['Điểm'] = str(grade['grade'])
    
    return details


def _get_quiz_detail(
    call_api: Callable, cmid: int, course_id: int, details: Dict[str, Any]
) -> Dict[str, Any]:
    """Lấy chi tiết quiz qua WS API."""
    quizzes = get_quizzes_by_courses(call_api, [course_id])
    if not quizzes:
        _fill_course_name(call_api, course_id, details)
        return details
    
    quiz_data = None
    quiz_id = None
    for q in quizzes:
        # Quiz cmid mapping: quiz's 'coursemodule' field or match by name
        if q.get('coursemodule') == cmid:
            quiz_data = q
            quiz_id = q.get('id')
            break
    
    if not quiz_data:
        # Fallback: try matching by ID
        for q in quizzes:
            if q.get('id') == cmid:
                quiz_data = q
                quiz_id = q['id']
                break
    
    _fill_course_name(call_api, course_id, details)
    
    if not quiz_data:
        return details
    
    # Mô tả
    details['description_html'] = quiz_data.get('intro', '') or ''
    
    # Quiz info
    time_limit = quiz_data.get('timelimit', 0)
    if time_limit:
        minutes = time_limit // 60
        details['time_limit'] = f"{minutes} phút"
    
    attempts_allowed = quiz_data.get('attempts', 0)
    if attempts_allowed:
        details['attempts_allowed'] = str(attempts_allowed) if attempts_allowed > 0 else 'Không giới hạn'
    
    # Open/close times
    time_open = quiz_data.get('timeopen', 0)
    if time_open and time_open > 0:
        try:
            details['open_time'] = datetime.fromtimestamp(time_open).isoformat()
        except (OSError, ValueError):
            pass
    
    # Attempts
    if quiz_id:
        attempts = get_quiz_attempts(call_api, quiz_id)
        if attempts:
            for att in attempts:
                state = att.get('state', '')
                state_map = {'finished': 'Hoàn thành', 'inprogress': 'Đang làm', 'overdue': 'Quá hạn'}
                grade = att.get('sumgrades', '')
                attempt_num = att.get('attempt', '?')
                info = f"Lần {attempt_num}: {state_map.get(state, state)}"
                if grade:
                    info += f" — Điểm: {grade}"
                details['quiz_info'].append(info)
            
            # Status data
            last = attempts[-1]
            last_state = last.get('state', '')
            details['status_data']['Trạng thái'] = {
                'finished': 'Đã hoàn thành',
                'inprogress': 'Đang làm',
                'overdue': 'Quá hạn',
            }.get(last_state, last_state)
            details['status_data']['Số lần đã làm'] = str(len(attempts))
    
    return details


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
            cm_id = evt.get('instance', '')
            if not url:
                course_data = evt.get('course') or {}
                course_id = course_data.get('id', '') if isinstance(course_data, dict) else ''
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
                'course_id': course_data.get('id', ''),
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
