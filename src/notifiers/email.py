import logging
import html
import smtplib
from datetime import datetime
from email.message import EmailMessage
from typing import List
from models import Assignment
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


class EmailNotifier(BaseNotifier):
    """Email/Gmail notifier with professional HTML template."""

    def notify(self, assignments: List[Assignment]) -> bool:
        if not getattr(settings, 'ENABLE_GMAIL', False) or not getattr(settings, 'GMAIL_ADDRESS', ''):
            return False

        email_address = settings.GMAIL_ADDRESS
        app_password = getattr(settings, 'GMAIL_APP_PASSWORD', '')

        if not app_password:
            logger.warning("[Email] Chưa cấu hình mật khẩu ứng dụng Gmail (GMAIL_APP_PASSWORD).")
            return False

        tasks = assignments
        if not tasks:
            return True

        all_grade_alerts = all(bool(_get(t, "is_grade_alert", False)) for t in tasks)
        time_str = datetime.now().strftime("%H:%M %d/%m/%Y")

        if all_grade_alerts:
            msg_subject = f"[UTHelper] 📊 {len(tasks)} cập nhật điểm mới"
            header_title = "📊 Cập nhật điểm mới"
            header_subtitle = f"{len(tasks)} thay đổi điểm được ghi nhận"
            summary = f"{len(tasks)} cập nhật điểm"
        else:
            critical_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'critical')
            warning_count = sum(1 for t in tasks if urgency_str(_get(t, 'urgency', 'safe')) == 'warning')

            if critical_count > 0:
                msg_subject = f"[UTHelper] ⚠️ {critical_count} bài tập khẩn cấp cần nộp!"
            else:
                msg_subject = f"[UTHelper] {len(tasks)} deadline sắp đến"

            summary_parts = []
            if critical_count:
                summary_parts.append(f"{critical_count} khẩn cấp")
            if warning_count:
                summary_parts.append(f"{warning_count} sắp hạn")
            safe_count = len(tasks) - critical_count - warning_count
            if safe_count > 0:
                summary_parts.append(f"{safe_count} bình thường")
            summary = " · ".join(summary_parts)
            header_title = "📋 Nhắc nhở deadline"
            header_subtitle = f"{len(tasks)} hoạt động cần chú ý"

        msg = EmailMessage()
        msg['Subject'] = msg_subject
        msg['From'] = email_address
        msg['To'] = email_address

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; color: #1f2937; }}
                .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 16px; overflow: hidden; border: 1px solid #e5e7eb; }}
                .header {{ background: linear-gradient(135deg, #4f46e5 0%, #7c3aed 100%); color: white; padding: 28px 24px; }}
                .header h2 {{ margin: 0; font-size: 22px; font-weight: 700; }}
                .header .subtitle {{ margin: 6px 0 0 0; opacity: 0.85; font-size: 14px; }}
                .summary {{ background: #f8fafc; padding: 14px 24px; border-bottom: 1px solid #e5e7eb; font-size: 13px; color: #64748b; }}
                .content {{ padding: 24px; }}
                .task-card {{ margin-bottom: 16px; padding: 18px; border-radius: 12px; border-left: 4px solid #4f46e5; background: #fafbfc; }}
                .task-card.critical {{ border-left-color: #ef4444; background: #fef2f2; }}
                .task-card.warning {{ border-left-color: #f59e0b; background: #fffbeb; }}
                .task-card.safe {{ border-left-color: #10b981; background: #f0fdf4; }}
                .task-title {{ font-size: 16px; font-weight: 600; margin: 0 0 10px 0; color: #111827; }}
                .task-meta {{ font-size: 13px; color: #4b5563; margin-bottom: 6px; line-height: 1.6; }}
                .task-meta strong {{ color: #374151; display: inline-block; min-width: 85px; }}
                .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
                .badge-critical {{ background: #fecaca; color: #dc2626; }}
                .badge-warning {{ background: #fef3c7; color: #d97706; }}
                .badge-safe {{ background: #d1fae5; color: #059669; }}
                .badge-submitted {{ background: #d1fae5; color: #059669; }}
                .badge-pending {{ background: #e5e7eb; color: #6b7280; }}
                .btn {{ display: inline-block; padding: 10px 20px; background: #4f46e5; color: #ffffff !important; text-decoration: none; border-radius: 8px; font-size: 13px; font-weight: 600; margin-top: 12px; }}
                .footer {{ text-align: center; padding: 18px 24px; color: #9ca3af; font-size: 12px; background: #f9fafb; border-top: 1px solid #f3f4f6; line-height: 1.6; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>{header_title}</h2>
                    <p class="subtitle">{header_subtitle}</p>
                </div>
                <div class="summary">
                    {summary} · Cập nhật lúc {time_str}
                </div>
                <div class="content">
        """

        for task in tasks:
            if bool(_get(task, "is_grade_alert", False)):
                g_title = html.escape(str(_get(task, "title", "📊 Điểm mới")))
                g_course_raw = _get(task, "course_name", "") or _get(task, "course", "Không rõ môn")
                g_course = html.escape(str(clean_course_name(g_course_raw) if g_course_raw else g_course_raw))
                g_item = html.escape(str(_get(task, "item_name", "") or "-"))
                g_old = html.escape(str(_get(task, "old_grade", "-") or "-"))
                g_new = html.escape(str(_get(task, "new_grade", "-") or "-"))
                html_content += f"""
                    <div class="task-card safe">
                        <p class="task-title">{g_title}</p>
                        <div class="task-meta"><strong>📚 Môn học</strong> {g_course}</div>
                        <div class="task-meta"><strong>📝 Cột điểm</strong> {g_item}</div>
                        <div class="task-meta"><strong>📉 Điểm cũ</strong> {g_old}</div>
                        <div class="task-meta"><strong>📈 Điểm mới</strong> <b>{g_new}</b></div>
                    </div>
                """
                continue

            urgency = _get(task, 'urgency', 'safe')
            urg_normalized = urgency_str(urgency)
            css_class = urg_normalized

            title = _get(task, 'title', 'Không rõ tiêu đề')
            course_raw = _get(task, 'course_name', '') or _get(task, 'course', 'Không rõ môn')
            course = clean_course_name(course_raw) if course_raw else course_raw

            deadline = _get(task, 'deadline_str', '')
            if not deadline:
                dl = _get(task, 'deadline', None)
                if dl and hasattr(dl, 'strftime'):
                    deadline = dl.strftime('%H:%M %d/%m/%Y')
                else:
                    deadline = 'Chưa rõ'

            url = _get(task, 'link', '') or _get(task, 'url', '')

            # Type display
            task_type = _get(task, 'type', '') or _get(task, 'event_type', '')
            type_emoji, type_label = get_type_display(task_type)
            urg_emoji, urg_label = get_urgency_display(urgency)

            # Submission status
            submission = _get(task, 'submission_status', '')
            if submission in ('submitted', 'Đã nộp'):
                sub_html = '<span class="badge badge-submitted">✅ Đã nộp</span>'
            elif submission in ('graded', 'Đã chấm'):
                sub_html = '<span class="badge badge-submitted">✅ Đã chấm</span>'
            else:
                sub_html = '<span class="badge badge-pending">⏳ Chưa nộp</span>'

            # Urgency badge
            urg_badge = f'<span class="badge badge-{css_class}">{urg_emoji} {urg_label}</span>'

            # Calculate remaining time
            remaining = format_remaining_time(_get(task, 'deadline', None))

            # Escape user data for HTML
            try:
                title = html.escape(str(title))
                course = html.escape(str(course))
                deadline = html.escape(str(deadline))
                url = html.escape(str(url), quote=True)
                remaining = html.escape(str(remaining))
                type_label = html.escape(type_label)
            except Exception:
                logger.debug("Ignored exception while escaping email HTML", exc_info=True)

            html_content += f"""
                    <div class="task-card {css_class}">
                        <p class="task-title">{urg_emoji} {title}</p>
                        <div class="task-meta"><strong>📚 Môn học</strong> {course}</div>
                        <div class="task-meta"><strong>{type_emoji} Loại</strong> {type_label}</div>
                        <div class="task-meta"><strong>⏰ Hạn chót</strong> <b>{deadline}</b></div>
                        <div class="task-meta"><strong>⏳ Còn lại</strong> {remaining}</div>
                        <div class="task-meta" style="margin-top: 8px;">{urg_badge} {sub_html}</div>
            """
            if url:
                html_content += f'<a href="{url}" class="btn">Mở bài tập →</a>'
            html_content += "</div>"

        html_content += """
                </div>
                <div class="footer">
                    Bạn nhận email này từ UTHelper - hệ thống nhắc nhở deadline tự động.<br>
                    Chỉnh cài đặt thông báo trong ứng dụng UTHelper.
                </div>
            </div>
        </body>
        </html>
        """

        # Plain text fallback
        plain_lines = [
            f"UTHelper · {len(tasks)} cập nhật điểm mới"
            if all_grade_alerts
            else f"UTHelper · {len(tasks)} hoạt động cần chú ý",
            "",
        ]
        for task in tasks:
            t_title = _get(task, 'title', 'Không rõ')
            t_course = _get(task, 'course_name', '') or _get(task, 'course', '')
            if bool(_get(task, "is_grade_alert", False)):
                t_old = _get(task, "old_grade", "-") or "-"
                t_new = _get(task, "new_grade", "-") or "-"
                plain_lines.append(f"• {t_title}")
                plain_lines.append(f"  Môn: {t_course} · Điểm: {t_old} -> {t_new}")
            else:
                t_remaining = format_remaining_time(_get(task, 'deadline', None))
                plain_lines.append(f"• {t_title}")
                plain_lines.append(f"  Môn: {t_course} · Còn: {t_remaining}")
            plain_lines.append("")
        plain_lines.append("-- UTHelper")

        msg.set_content("\n".join(plain_lines))
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
