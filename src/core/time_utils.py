from __future__ import annotations
from datetime import datetime
import logging
from dateutil import parser as _dateutil_parser

logger = logging.getLogger(__name__)


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
            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.astimezone(local_tz).replace(tzinfo=None)
        return dt
    except Exception:
        pass

    try:
        dt = _dateutil_parser.parse(s)
        if dt.tzinfo:
            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.astimezone(local_tz).replace(tzinfo=None)
        return dt
    except Exception as e:
        logger.debug(f"parse_datetime failed for '{s}': {e}")
        return None
