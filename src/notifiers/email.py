import logging
import html
import smtplib
from email.message import EmailMessage
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


class EmailNotifier(BaseNotifier):
    """
    Trình quản lý thông báo Email/Gmail với giao diện Pro Max.
    """
    def notify(self, assignments: List[Assignment]) -> bool:
        if not getattr(settings, 'ENABLE_GMAIL', False) or not getattr(settings, 'GMAIL_ADDRESS', ''):
            return False

        email_address = settings.GMAIL_ADDRESS
        app_password = getattr(settings, 'GMAIL_APP_PASSWORD', '')

        if not app_password:
            logger.warning("[Email] Chưa cấu hình mật khẩu ứng dụng Gmail (GMAIL_APP_PASSWORD).")
            return False

        tasks = assignments
        if not tasks: return True

        msg = EmailMessage()
        msg['Subject'] = f"[UTHelper] Bạn có {len(tasks)} bài tập cần chú ý!"
        msg['From'] = email_address
        msg['To'] = email_address

        html_content = """
        <html>
        <head>
            <style>
                body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f7f6; margin: 0; padding: 20px; }
                .container { max-width: 600px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 8px 30px rgba(0,0,0,0.08); overflow: hidden; }
                .header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px; text-align: center; }
                .header h2 { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: 0.5px; }
                .content { padding: 30px; }
                .task-card { background: #ffffff; border-left: 5px solid #4f46e5; margin-bottom: 20px; padding: 20px; border-radius: 8px; box-shadow: 0 4px 15px rgba(0,0,0,0.03); transition: transform 0.2s; }
                .task-card:hover { transform: translateY(-2px); }
                .task-card.critical { border-left-color: #ef4444; background: #fff5f5; }
                .task-card.warning { border-left-color: #f59e0b; background: #fffbeb; }
                .task-card.safe { border-left-color: #10b981; background: #f0fdf4; }
                .title { font-size: 18px; font-weight: bold; margin: 0 0 10px 0; color: #1f2937; }
                .meta { font-size: 14px; color: #4b5563; margin-bottom: 8px; display: flex; align-items: center; }
                .meta strong { min-width: 90px; display: inline-block; color: #374151; }
                .button { display: inline-block; padding: 10px 18px; background: #4f46e5; color: #ffffff !important; text-decoration: none; border-radius: 6px; font-size: 13px; margin-top: 15px; font-weight: 600; text-align: center; }
                .button:hover { background: #4338ca; }
                .footer { text-align: center; padding: 20px; color: #9ca3af; font-size: 13px; background: #f9fafb; border-top: 1px solid #f3f4f6; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🚨 Thông báo Bài tập UTH</h2>
                    <p style="margin: 8px 0 0 0; opacity: 0.9; font-size: 15px;">Đừng để miss deadline bạn nhé!</p>
                </div>
                <div class="content">
            """

        for task in tasks:
            urgency = _get(task, 'urgency', 'safe')
            is_critical = urgency in ('critical', UrgencyLevel.CRITICAL)
            is_warning = urgency in ('warning', UrgencyLevel.WARNING)
            css_class = 'critical' if is_critical else ('warning' if is_warning else 'safe')
            status_text = 'Khẩn cấp 🔴' if is_critical else ('Sắp tới hạn 🟠' if is_warning else 'An toàn 🟢')
            
            title = _get(task, 'title', 'Không rõ tiêu đề')
            course = _get(task, 'course_name', '') or _get(task, 'course', 'Không rõ môn')
            
            deadline = _get(task, 'deadline_str', '')
            if not deadline:
                dl = _get(task, 'deadline', None)
                if dl and hasattr(dl, 'strftime'):
                    deadline = dl.strftime('%H:%M %d/%m/%Y')
                elif not deadline:
                    deadline = 'Không rõ hạn'
                
            url = _get(task, 'link', '') or _get(task, 'url', '')
            open_time_str = None

            # Escape user data for HTML
            try:
                title = html.escape(str(title))
                course = html.escape(str(course))
                deadline = html.escape(str(deadline))
                url = html.escape(str(url), quote=True)
            except Exception:
                pass

            html_content += f"""
                      <div class="task-card {css_class}">
                          <p class="title">{title}</p>
                          <div class="meta"><strong>📚 Môn học:</strong> {course}</div>
            """
            details = _get(task, 'details', None)
            if details and not isinstance(details, dict) and getattr(details, 'open_time', None):
                open_time_str = details.open_time.strftime('%H:%M %d/%m/%Y')
                try:
                    open_time_str = html.escape(open_time_str)
                except Exception:
                    pass
                html_content += f'<div class="meta"><strong>🗓️ Ngày mở:</strong> {open_time_str}</div>'

            # Calculate remaining time using shared utility
            remaining = format_remaining_time(_get(task, 'deadline', None))

            html_content += f"""
                        <div class="meta"><strong>⏰ Hạn chót:</strong> <span style="font-weight: bold;">{deadline}</span></div>
                        <div class="meta"><strong>⏳ Còn lại:</strong> {remaining}</div>
                        <div class="meta"><strong>🚨 Trạng thái:</strong> {status_text}</div>
            """
            if url:
                html_content += f'<a href="{url}" class="button">Đến Trang Nộp Bài</a>'
            html_content += "</div>"

        html_content += """
                </div>
                <div class="footer">
                    Hệ thống cảnh báo tự động UTHelper. Không cần phản hồi email này.<br>
                    <i>Stay productive & Keep learning!</i>
                </div>
            </div>
        </body>
        </html>
            """

        msg.set_content(f"Bạn có {len(tasks)} thông báo bài tập mới. Vui lòng kiểm tra email và E-Learning để không bỏ lỡ hạn chót.")
        msg.add_alternative(html_content, subtype='html')

        try:
            with smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=30) as server:
                server.login(email_address, app_password)
                server.send_message(msg)
            logger.info(f"[Email] Đã gửi {len(tasks)} bài tập tới {email_address}")
            return True
        except Exception as e:
            logger.error(f"[Email] Lỗi gửi email: {e}")
            return False
