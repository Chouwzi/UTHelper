import logging
import html
import requests
from typing import List
from models import Assignment, UrgencyLevel
from config import settings
from .base import BaseNotifier

logger = logging.getLogger(__name__)

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

        # Combine all tasks into one beautifully formatted message
        # Telegram max length is 4096 chars.
        text = "🔔 <b>THÔNG BÁO BÀI TẬP UTH MỚI</b>\n"
        text += "<i>Đừng để nước đến chân mới nhảy nhé!</i>\n\n"
        text += "➖➖➖➖➖➖➖➖➖➖➖➖\n\n"

        for a in tasks:
            is_critical = a.urgency == UrgencyLevel.CRITICAL
            icon = "🔴" if a.urgency == UrgencyLevel.CRITICAL else ("🟠" if a.urgency == UrgencyLevel.WARNING else "🟢")

            title = getattr(a, "title", "Không rõ tiêu đề")
            course = getattr(a, "course_name", getattr(a, "course", "Không rõ môn"))
            
            if hasattr(a, "deadline_str") and a.deadline_str:
                deadline = a.deadline_str
            else:
                deadline = a.deadline.strftime("%H:%M %d/%m/%Y") if hasattr(a, "deadline") and a.deadline else "Không rõ hạn"
                
            open_time = None
            if hasattr(a, 'details') and a.details and getattr(a.details, 'open_time', None):
                open_time = a.details.open_time.strftime('%H:%M %d/%m/%Y')
                
            remaining = "Không rõ"
            if hasattr(a, 'deadline') and a.deadline:
                import datetime
                delta = a.deadline - datetime.datetime.now()
                days, seconds = delta.days, delta.seconds
                hours = seconds // 3600
                if days < 0:
                    remaining = "Quá hạn rồi!"
                elif days > 0:
                    remaining = f"Còn {days} ngày {hours} giờ"
                else:
                    remaining = f"Còn {hours} giờ {seconds % 3600 // 60} phút"

            url = getattr(a, "link", getattr(a, "url", ""))
            task_type = getattr(a, "type", getattr(a, "event_type", "Bài tập"))

            # Escape user-provided fields before embedding into HTML
            try:
                title = html.escape(str(title))
                course = html.escape(str(course))
                deadline = html.escape(str(deadline))
                task_type = html.escape(str(task_type))
                if open_time:
                    open_time = html.escape(str(open_time))
                url = html.escape(str(url), quote=True)
            except Exception:
                pass

            text += f"{icon} <b>Môn học:</b> {course}\n"
            text += f"📝 <b>Loại:</b> {task_type}\n"
            text += f"📌 <b>Tiêu đề:</b> {title}\n"
            if open_time:
                text += f"🗓️ <b>Ngày mở:</b> {open_time}\n"
            text += f"⏰ <b>Hạn chót:</b> <u>{deadline}</u> ({remaining})\n"
            if url:
                text += f"🔗 👉 <a href='{url}'>Nhấn vào đây để xem chi tiết</a>\n"
            text += "\n"

        text += "➖➖➖➖➖➖➖➖➖➖➖➖\n"
        text += "🎓 <i>UTH E-Learning Smart Alert</i>"

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
