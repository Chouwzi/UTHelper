import logging
import requests
from typing import List
from models import Assignment, UrgencyLevel
from config import settings
from core.time_utils import format_remaining_time
from .base import BaseNotifier

logger = logging.getLogger(__name__)


def _get(obj, key, default=''):
    """Get attribute from both Assignment objects and dicts."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class DiscordNotifier(BaseNotifier):
    def notify(self, assignments: List[Assignment]) -> bool:
        if not getattr(settings, 'ENABLE_DISCORD', False) or not getattr(settings, 'DISCORD_WEBHOOK_URL', None):
            return False

        tasks = assignments
        if not tasks: return True

        webhook_url = settings.DISCORD_WEBHOOK_URL

        embeds = []
        for task in tasks:
            urgency = _get(task, 'urgency', 'safe')
            is_critical = urgency in ('critical', UrgencyLevel.CRITICAL)
            is_warning = urgency in ('warning', UrgencyLevel.WARNING)
            color = 15158332 if is_critical else (15105570 if is_warning else 3066993)

            title = _get(task, 'title', 'Không rõ tiêu đề')
            course = _get(task, 'course_name', '') or _get(task, 'course', 'Không rõ môn học')
            
            deadline = _get(task, 'deadline_str', '')
            if not deadline:
                dl = _get(task, 'deadline', None)
                if dl and hasattr(dl, 'strftime'):
                    deadline = dl.strftime('%H:%M %d/%m/%Y')
                elif not deadline:
                    deadline = 'Không rõ'
                
            open_time = 'Không có'
            details = _get(task, 'details', None)
            if details and not isinstance(details, dict) and getattr(details, 'open_time', None):
                open_time = details.open_time.strftime('%H:%M %d/%m/%Y')
            
            # Tính thời gian còn lại
            remaining = format_remaining_time(_get(task, 'deadline', None))

            url = _get(task, 'link', '') or _get(task, 'url', '')

            # Status text
            if is_critical:
                status_text = '🔴 Khẩn cấp'
            elif is_warning:
                status_text = '🟠 Sắp tới hạn'
            else:
                status_text = '🟢 An toàn'

            embed = {
                'title': title,
                'description': f'**Môn học:** {course}',
                'color': color,
                'fields': [
                    {'name': 'Hạn chót', 'value': f'{deadline}', 'inline': True},
                    {'name': 'Còn lại', 'value': remaining, 'inline': True},
                    {'name': 'Trạng thái', 'value': status_text, 'inline': False},
                ],
                'footer': {'text': 'Powered by UTHelper - Made by Chouwzi'}
            }
            if open_time != 'Không có':
                embed['fields'].insert(0, {'name': 'Ngày mở', 'value': open_time, 'inline': True})
            if url: embed['url'] = url
            embeds.append(embed)

        payload = {
            'username': 'UTHelper',
            'content': '🔔 **Thông báo có bài tập mới!**',
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
