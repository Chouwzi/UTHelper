import logging
from datetime import datetime
from typing import Any, List
from urllib.parse import urlsplit
from models import Assignment
from config import settings
from core.time_utils import format_remaining_time
from core.display_utils import get_type_display, get_urgency_display, urgency_str, clean_course_name
from .base import BaseNotifier

logger = logging.getLogger(__name__)

_ALLOWED_DISCORD_HOSTS = frozenset({
    "discord.com",
    "www.discord.com",
    "ptb.discord.com",
    "canary.discord.com",
    "discordapp.com",
    "www.discordapp.com",
    "ptb.discordapp.com",
    "canary.discordapp.com",
})

_httpx_mod: Any = None


def _get_httpx() -> Any:
    global _httpx_mod
    if _httpx_mod is None:
        import httpx as _loaded

        _httpx_mod = _loaded
    return _httpx_mod


def __getattr__(name: str) -> Any:
    if name == "httpx":
        return _get_httpx()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _is_valid_discord_webhook(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (
        parsed.scheme.lower() == "https"
        and parsed.username is None
        and parsed.password is None
        and port in (None, 443)
        and host in _ALLOWED_DISCORD_HOSTS
        and parsed.path.startswith("/api/")
    )


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
        if not tasks:
            return True

        webhook_url = str(settings.DISCORD_WEBHOOK_URL).strip()
        if not _is_valid_discord_webhook(webhook_url):
            logger.error("[Discord] Lỗi cấu hình webhook: URL không hợp lệ")
            return False

        all_grade_alerts = all(bool(_get(t, "is_grade_alert", False)) for t in tasks)
        embeds = []

        if all_grade_alerts:
            content_header = f"📊 **UTHelper** · {len(tasks)} cập nhật điểm mới"
        else:
            critical_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'critical')
            warning_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'warning')
            summary_parts = []
            if critical_count:
                summary_parts.append(f"🔴 {critical_count} khẩn cấp")
            if warning_count:
                summary_parts.append(f"🟠 {warning_count} sắp hạn")
            safe_count = len(tasks) - critical_count - warning_count
            if safe_count > 0:
                summary_parts.append(f"🟢 {safe_count} bình thường")
            summary = " · ".join(summary_parts)
            content_header = f"📋 **UTHelper** · {len(tasks)} hoạt động cần chú ý\n{summary}"

        for task in tasks:
            if bool(_get(task, "is_grade_alert", False)):
                title_raw = str(_get(task, "title", "📊 Điểm mới"))
                course_raw = _get(task, "course_name", "") or _get(task, "course", "Không rõ môn")
                course = clean_course_name(course_raw) if course_raw else course_raw
                item_name = str(_get(task, "item_name", "") or title_raw)
                old_grade = str(_get(task, "old_grade", "-") or "-")
                new_grade = str(_get(task, "new_grade", "-") or "-")
                embeds.append({
                    'title': title_raw,
                    'description': f'📚 **{course}**',
                    'color': 3447003,
                    'fields': [
                        {'name': '📝 Cột điểm', 'value': item_name, 'inline': True},
                        {'name': '📉 Điểm cũ', 'value': old_grade, 'inline': True},
                        {'name': '📈 Điểm mới', 'value': new_grade, 'inline': True},
                    ],
                    'footer': {
                        'text': f'UTHelper · {datetime.now().strftime("%H:%M %d/%m/%Y")}'
                    },
                })
                continue

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
            type_emoji, _type_label = get_type_display(task_type)
            urg_emoji, _urg_label = get_urgency_display(urgency)

            # Submission status
            submission = _get(task, 'submission_status', '')
            if submission in ('submitted', 'Đã nộp'):
                sub_display = '✅ Đã nộp'
            elif submission in ('graded', 'Đã chấm'):
                sub_display = '✅ Đã chấm'
            else:
                sub_display = '⏳ Chưa nộp'

            url = _get(task, 'link', '') or _get(task, 'url', '')
            embed_title = f"{urg_emoji} {title_raw}"

            embed = {
                'title': embed_title,
                'description': f'📚 **{course}** · {type_emoji} {_type_label}',
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
            'content': content_header,
            'embeds': embeds[:10]
        }

        http_client = globals().get("httpx") or _get_httpx()
        try:
            r = http_client.post(webhook_url, json=payload, timeout=5)
            if r is not None and hasattr(r, "raise_for_status"):
                r.raise_for_status()
            logger.info(f'[Discord] Đã gửi thông báo cho {len(tasks)} bài tập.')
            return True
        except http_client.HTTPStatusError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            logger.error("[Discord] Lỗi gửi thông báo: HTTP %s", status_code)
            return False
        except http_client.RequestError as e:
            logger.error("[Discord] Lỗi kết nối: %s", type(e).__name__)
            return False
        except Exception as e:
            logger.error("[Discord] Lỗi gửi thông báo: %s", type(e).__name__)
            return False
