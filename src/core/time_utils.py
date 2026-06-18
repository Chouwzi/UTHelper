from __future__ import annotations
from datetime import datetime
import logging
from dateutil import parser as _dateutil_parser

logger = logging.getLogger(__name__)

# F19: Cache local timezone — avoid recomputing on every parse call
_LOCAL_TZ = datetime.now().astimezone().tzinfo


def parse_datetime(s: str) -> datetime | None:
    """Parse a wide range of datetime strings into a naive local datetime.

    Behavior:
    - Try datetime.fromisoformat first for speed.
    - Fallback to dateutil.parser.parse for flexible formats.
    - If parsed datetime is timezone-aware, convert to local timezone then return a naive datetime
      (this keeps compatibility with existing code that uses naive datetime.now()).
    - Returns None if parsing fails.
    """
    if not s:
        return None

    try:
        dt = datetime.fromisoformat(s)
        # normalize timezone-aware -> local naive
        if dt.tzinfo:
            dt = dt.astimezone(_LOCAL_TZ).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    try:
        dt = _dateutil_parser.parse(s)
        if dt.tzinfo:
            dt = dt.astimezone(_LOCAL_TZ).replace(tzinfo=None)
        return dt
    except Exception as e:
        logger.debug(f"parse_datetime failed for '{s}': {e}")
        return None


def format_remaining_time(deadline: datetime | None) -> str:
    """Format the remaining time from now until deadline as a human-readable Vietnamese string.

    Used by all notifiers (Windows, Discord, Telegram, Email) to avoid duplicating
    the days/hours/minutes calculation logic.

    Returns:
        "Không rõ"     – if deadline is None
        "Quá hạn!"     – if deadline is in the past
        "Còn X ngày Yh" – if more than 1 day remains
        "Còn Xh Yp"    – if less than 1 day remains
    """
    if not deadline:
        return "Không rõ"

    delta = deadline - datetime.now()
    total_seconds = int(delta.total_seconds())

    if total_seconds < 0:
        return "Quá hạn!"

    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    minutes = (total_seconds % 3600) // 60

    if days > 0:
        return f"Còn {days} ngày {hours} giờ"
    if hours > 0:
        return f"Còn {hours} giờ {minutes} phút"
    return f"Còn {minutes} phút"

