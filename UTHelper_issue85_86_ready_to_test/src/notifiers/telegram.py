import logging
import html
import httpx
from datetime import datetime
from typing import List
from models import Assignment
from config import settings
from core.time_utils import format_remaining_time
from core.display_utils import get_type_display, get_urgency_display, urgency_str, clean_course_name
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
    """Telegram bot notifier with rich HTML formatting."""

    def notify(self, assignments: List['Assignment']) -> bool:
        if not settings.ENABLE_TELEGRAM or not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            return False

        tasks = assignments
        if not tasks:
            return False

        token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_CHAT_ID
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"

        # Count urgencies for smart summary
        critical_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'critical')
        warning_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'warning')

        # Smart summary
        summary_parts = []
        if critical_count:
            summary_parts.append(f"🔴 {critical_count} khẩn cấp")
        if warning_count:
            summary_parts.append(f"🟠 {warning_count} sắp hạn")
        safe_count = len(tasks) - critical_count - warning_count
        if safe_count > 0:
            summary_parts.append(f"🟢 {safe_count} bình thường")
        summary = " · ".join(summary_parts)

        # Build header
        header = "📋 <b>UTHelper</b> · Nhắc nhở deadline\n"
        header += f"<i>{summary}</i>\n\n"

        # Footer
        time_str = datetime.now().strftime("%H:%M %d/%m/%Y")
        footer = "━━━━━━━━━━━━━━━━━\n"
        footer += f"<i>UTHelper · {time_str}</i>"

        # Build task blocks
        task_blocks = []
        for a in tasks:
            block = self._format_task_block(a)
            task_blocks.append(block)

        # Assemble with truncation if needed
        text = header
        included_count = 0

        for block in task_blocks:
            if len(text) + len(block) + len(footer) + 80 > _TELEGRAM_MAX_LENGTH:
                break
            text += block
            included_count += 1

        omitted = len(tasks) - included_count
        if omitted > 0:
            text += f"<i>... và {omitted} hoạt động khác</i>\n\n"

        text += footer

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }

        try:
            r = httpx.post(api_url, json=payload, timeout=5)
            r.raise_for_status()
            logger.info(f"[Telegram] Đã gửi thông báo tổng hợp cho {len(tasks)} bài tập.")
            return True
        except Exception as e:
            logger.error(f"[Telegram] Lỗi khi gửi: {e}")
            return False

    def _format_task_block(self, a) -> str:
        """Format a single task into a rich HTML text block for Telegram."""
        # Title
        title = _get(a, "title", "Không rõ tiêu đề")
        title = html.escape(str(title))

        # Course (cleaned)
        course_raw = _get(a, "course_name", "") or _get(a, "course", "Không rõ môn")
        course = clean_course_name(str(course_raw))
        course = html.escape(course)

        # Deadline
        deadline_str = _get(a, "deadline_str", "")
        if not deadline_str:
            dl = _get(a, "deadline", None)
            if dl and hasattr(dl, 'strftime'):
                deadline_str = dl.strftime("%H:%M %d/%m/%Y")
            else:
                deadline_str = "Chưa rõ"
        deadline_str = html.escape(str(deadline_str))

        # Remaining time
        remaining = format_remaining_time(_get(a, 'deadline', None))
        remaining = html.escape(str(remaining)) if remaining else ""

        # Type display
        task_type = _get(a, "type", "") or _get(a, "event_type", "")
        type_emoji, type_label = get_type_display(task_type)
        type_label = html.escape(type_label)

        # Urgency display
        urgency = _get(a, "urgency", "safe")
        urg_emoji, urg_label = get_urgency_display(urgency)

        # Submission status
        submission = _get(a, 'submission_status', '')
        if submission in ('submitted', 'Đã nộp'):
            sub_display = '✅ Đã nộp'
        elif submission in ('graded', 'Đã chấm'):
            sub_display = '✅ Đã chấm'
        else:
            sub_display = '⏳ Chưa nộp'

        # URL
        url = _get(a, "link", "") or _get(a, "url", "")
        url_escaped = html.escape(str(url), quote=True) if url else ""

        # Build block
        block = "━━━━━━━━━━━━━━━━━\n"
        block += f"{urg_emoji} <b>{title}</b>\n"
        block += f"📚 {course} · {type_emoji} {type_label}\n"
        block += f"⏰ Hạn: <u>{deadline_str}</u> · {remaining}\n"
        block += f"📊 {sub_display}\n"
        if url:
            block += f'🔗 <a href="{url_escaped}">Mở bài tập</a>\n'
        block += "\n"
        return block
