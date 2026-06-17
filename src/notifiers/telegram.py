import logging
import html
import requests
from typing import List
from models import Assignment, UrgencyLevel
from config import settings
from core.time_utils import format_remaining_time
from .base import BaseNotifier

logger = logging.getLogger(__name__)

# Telegram API max message length
_TELEGRAM_MAX_LENGTH = 4000


def _get(obj, key, default=''):
    """Get attribute from both Assignment objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class TelegramNotifier(BaseNotifier):
    """
    bot Telegram.
    """
    def notify(self, assignments: List['Assignment']) -> bool:
        if not settings.ENABLE_TELEGRAM or not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            return False

        tasks = assignments
        if not tasks:
            return False

        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"

        # Build task blocks, then truncate if total exceeds Telegram limit
        header = "<b>THÔNG BÁO BÀI TẬP UTH</b>\n"
        header += "<i>Thông tin nhắc nhở hạn nộp bài</i>\n\n"
        header += "--------------------------------\n\n"
        footer = "--------------------------------\n"
        footer += "<i>UTHelper</i>"

        task_blocks = []
        for a in tasks:
            block = self._format_task_block(a)
            task_blocks.append(block)

        # Assemble with truncation if needed
        text = header
        overhead = len(header) + len(footer) + 100  # buffer for omitted-count line
        remaining_budget = _TELEGRAM_MAX_LENGTH - overhead
        included_count = 0

        for block in task_blocks:
            if len(text) + len(block) + len(footer) + 80 > _TELEGRAM_MAX_LENGTH:
                break
            text += block
            included_count += 1

        omitted = len(tasks) - included_count
        if omitted > 0:
            text += f"<i>... và {omitted} bài tập khác (quá dài, không hiển thị hết)</i>\n\n"

        text += footer

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            r = requests.post(api_url, json=payload, timeout=5)
            r.raise_for_status()
            logger.info(f"[Telegram] Đã gửi thông báo tổng hợp cho {len(tasks)} bài tập.")
            return True
        except Exception as e:
            logger.error(f"[Telegram] Lỗi khi gửi: {e}")
            return False

    def _format_task_block(self, a) -> str:
        """Format a single task into an HTML text block for Telegram."""
        title = _get(a, "title", "Không rõ tiêu đề")
        title = html.escape(str(title))
        course = _get(a, "course_name", "") or _get(a, "course", "Không rõ môn")
        course = html.escape(str(course))

        deadline_str = _get(a, "deadline_str", "")
        if not deadline_str:
            dl = _get(a, "deadline", None)
            if dl and hasattr(dl, 'strftime'):
                deadline_str = dl.strftime("%H:%M %d/%m/%Y")
            else:
                deadline_str = "Không rõ hạn"
        deadline_str = html.escape(str(deadline_str))

        open_time = None
        details = _get(a, 'details', None)
        if details and not isinstance(details, dict) and getattr(details, 'open_time', None):
            open_time = details.open_time.strftime('%H:%M %d/%m/%Y')
        open_time = html.escape(str(open_time)) if open_time else None

        remaining = format_remaining_time(_get(a, 'deadline', None))

        url = _get(a, "link", "") or _get(a, "url", "")
        task_type = _get(a, "type", "") or _get(a, "event_type", "Bài tập")

        # Escape computed fields
        remaining = html.escape(str(remaining)) if remaining is not None else ""
        task_type = html.escape(str(task_type)) if task_type is not None else ""
        url_escaped = html.escape(str(url), quote=True) if url else ""

        block = f"<b>Môn học:</b> {course}\n"
        block += f"<b>Loại:</b> {task_type}\n"
        block += f"<b>Tiêu đề:</b> {title}\n"
        if open_time:
            block += f"<b>Ngày mở:</b> {open_time}\n"
        block += f"<b>Hạn chót:</b> <u>{deadline_str}</u> ({remaining})\n"
        if url:
            block += f'<a href="{url_escaped}">Nhấn vào đây để xem chi tiết</a>\n'
        block += "\n"
        return block
