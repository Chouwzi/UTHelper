import logging
import requests
from typing import List
from models import Assignment, UrgencyLevel
from config import settings
from core.time_utils import format_remaining_time
from .base import BaseNotifier

logger = logging.getLogger(__name__)

class DiscordNotifier(BaseNotifier):
    def notify(self, assignments: List[Assignment]) -> bool:
        if not getattr(settings, 'ENABLE_DISCORD', False) or not getattr(settings, 'DISCORD_WEBHOOK_URL', None):
            return False

        tasks = assignments
        if not tasks: return True

        webhook_url = settings.DISCORD_WEBHOOK_URL

        embeds = []
        for task in tasks:
            is_critical = task.urgency == UrgencyLevel.CRITICAL
            color = 15158332 if task.urgency == UrgencyLevel.CRITICAL else (15105570 if task.urgency == UrgencyLevel.WARNING else 3066993)

            title = getattr(task, 'title', 'Không rõ tiêu đề')
            course = getattr(task, 'course_name', getattr(task, 'course', 'Không rõ môn học'))
            
            deadline = getattr(task, 'deadline_str', '')
            if not deadline and getattr(task, 'deadline', None):
                deadline = task.deadline.strftime('%H:%M %d/%m/%Y')
            elif not deadline:
                deadline = 'Không rõ'
                
            open_time = 'Không có'
            if hasattr(task, 'details') and task.details and getattr(task.details, 'open_time', None):
                open_time = task.details.open_time.strftime('%H:%M %d/%m/%Y')
            
            # Tính thời gian còn lại
            remaining = format_remaining_time(getattr(task, 'deadline', None))

            url = getattr(task, 'link', getattr(task, 'url', ''))

            embed = {
                'title': title,
                'description': f'**Môn học:** {course}',
                'color': color,
                'fields': [
                    {'name': 'Hạn chót', 'value': f'{deadline}', 'inline': True},
                    {'name': 'Còn lại', 'value': remaining, 'inline': True},
                    {'name': 'Trạng thái', 'value': '🔴 Khẩn cấp' if task.urgency == UrgencyLevel.CRITICAL else ('🟠 Sắp tới hạn' if task.urgency == UrgencyLevel.WARNING else '🟢 An toàn'), 'inline': False},
                ],
                'footer': {'text': 'Power by UTHelper - Made by Chouwzi'}
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
