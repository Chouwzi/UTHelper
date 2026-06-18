import logging
import requests
from datetime import datetime
from typing import List
from models import Assignment, UrgencyLevel
from config import settings
from core.time_utils import format_remaining_time
from core.display_utils import get_type_display, get_urgency_display, urgency_str, clean_course_name
from .base import BaseNotifier

logger = logging.getLogger(__name__)


def _get(obj, key, default=''):
    """Get attribute from both Assignment objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_deadline_str(task) -> str:
    """Extract deadline string from task, with fallback formatting."""
    deadline = _get(task, 'deadline_str', '')
    if not deadline:
        dl = _get(task, 'deadline', None)
        if dl and hasattr(dl, 'strftime'):
            deadline = dl.strftime('%H:%M %d/%m/%Y')
        else:
            deadline = 'Chưa rõ'
    return deadline


class DiscordNotifier(BaseNotifier):
    def notify(self, assignments: List[Assignment]) -> bool:
        if not getattr(settings, 'ENABLE_DISCORD', False) or not getattr(settings, 'DISCORD_WEBHOOK_URL', None):
            return False

        tasks = assignments
        if not tasks: return True

        webhook_url = settings.DISCORD_WEBHOOK_URL

        # Count urgencies for smart summary
        critical_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'critical')
        warning_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'warning')

        # Smart summary line
        summary_parts = []
        if critical_count:
            summary_parts.append(f"🔴 {critical_count} khẩn cấp")
        if warning_count:
            summary_parts.append(f"🟠 {warning_count} sắp hạn")
        safe_count = len(tasks) - critical_count - warning_count
        if safe_count > 0:
            summary_parts.append(f"🟢 {safe_count} bình thường")
        summary = " · ".join(summary_parts)

        embeds = []
        for task in tasks:
            urgency = _get(task, 'urgency', 'safe')
            urg_normalized = urgency_str(urgency)
            is_critical = urg_normalized == 'critical'
            is_warning = urg_normalized == 'warning'
            color = 15158332 if is_critical else (15105570 if is_warning else 3066993)

            title_raw = _get(task, 'title', 'Không rõ tiêu đề')
            course_raw = _get(task, 'course_name', '') or _get(task, 'course', 'Không rõ môn')
            course = clean_course_name(course_raw) if course_raw else course_raw
            
            deadline = _get_deadline_str(task)
            remaining = format_remaining_time(_get(task, 'deadline', None))

            # Type display
            task_type = _get(task, 'type', '') or _get(task, 'event_type', '')
            type_emoji, type_label = get_type_display(task_type)
            urg_emoji, urg_label = get_urgency_display(urgency)

            # Submission status
            submission = _get(task, 'submission_status', '')
            if submission in ('submitted', 'Đã nộp'):
                sub_display = '✅ Đã nộp'
            elif submission in ('graded', 'Đã chấm'):
                sub_display = '✅ Đã chấm'
            else:
                sub_display = '⏳ Chưa nộp'

            url = _get(task, 'link', '') or _get(task, 'url', '')

            # Title with urgency prefix
            embed_title = f"{urg_emoji} {title_raw}"

            embed = {
                'title': embed_title,
                'description': f'📚 **{course}** · {type_emoji} {type_label}',
                'color': color,
                'fields': [
                    {'name': '⏰ Hạn chót', 'value': deadline, 'inline': True},
                    {'name': '⏳ Còn lại', 'value': remaining, 'inline': True},
                    {'name': '📊 Nộp bài', 'value': sub_display, 'inline': True},
                ],
                'footer': {
                    'text': f'UTHelper · {datetime.now().strftime("%H:%M %d/%m/%Y")}'
                }
            }
            if url:
                embed['url'] = url
            embeds.append(embed)

        payload = {
            'username': 'UTHelper',
            'avatar_url': 'https://i.imgur.com/AfFp7pu.png',
            'content': f'📋 **UTHelper** · {len(tasks)} hoạt động cần chú ý\n{summary}',
            'embeds': embeds[:10]
        }

        try:
            r = requests.post(webhook_url, json=payload, timeout=5)
            r.raise_for_status()
            logger.info(f'[Discord] Đã gửi thông báo cho {len(tasks)} bài tập.')
            return True
        except Exception as e:
            logger.error(f'[Discord] Lỗi gửi thông báo: {e}')
            return False
