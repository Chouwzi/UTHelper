import re
from datetime import datetime
from core.time_utils import parse_datetime
from gui.core.theme import C, _TYPE_LABELS, _TYPE_COLORS
from config import settings
from core.display_utils import clean_course_name, urgency_str  # canonical source

# clean_course_name is imported from core.display_utils above

def get_vi_weekday(dt: datetime) -> str:
    days = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
    return days[dt.weekday()]

def format_deadline(deadline_str: str) -> str:
    dt = parse_datetime(deadline_str)
    if not dt:
        return deadline_str
    if dt.year >= 2099:
        return "Không có thời hạn"
    return f"{get_vi_weekday(dt)}, {dt.strftime('%d/%m/%Y')} • {dt.strftime('%H:%M')}"

def get_countdown(deadline_str: str, act_type: str = "") -> tuple:
    """Return (text, is_overdue)."""
    dt = parse_datetime(deadline_str)
    if not dt:
        return "Không xác định", False

    # Nếu là năm 2099, tức là không có deadline
    if dt.year >= 2099:
        return "Không có thời hạn", False

    now = datetime.now()
    diff = dt - now
    total = abs(diff.total_seconds())
    if total < 60:
        return "Đang đến hạn!", True

    # Nếu đây là sự kiện "Mở từ" calendar, nó hiển thị text khác
    is_open_event = act_type == "open" or act_type.endswith("_open")

    days  = int(total // 86400)
    hours = int((total % 86400) // 3600)
    mins  = int((total % 3600) // 60)

    # Tính toán tháng nếu số ngày > 30
    months = days // 30
    remaining_days = days % 30

    if diff.total_seconds() < 0:
        if is_open_event:
            if months: return f"Đã mở {months} tháng {remaining_days} ngày", False
            if days: return f"Đã mở {days} ngày {hours} giờ", False
            if hours: return f"Đã mở {hours} giờ {mins} phút", False
            return f"Đã mở {mins} phút", False

        if months: return f"Quá hạn {months} tháng {remaining_days} ngày", True
        if days: return f"Quá hạn {days} ngày {hours} giờ", True
        if hours: return f"Quá hạn {hours} giờ {mins} phút", True
        return f"Quá hạn {mins} phút", True
    else:
        prefix = "Mở sau" if is_open_event else "Còn"
        if months: return f"{prefix} {months} tháng {remaining_days} ngày", False
        if days: return f"{prefix} {days} ngày {hours} giờ", False
        if hours: return f"{prefix} {hours} giờ {mins} phút", False
        return f"{prefix} {mins} phút", False

def get_progress_value(deadline_str: str) -> float:
    """1.0 = 7 days before deadline, 0.0 = at or past deadline."""
    dt = parse_datetime(deadline_str)
    if not dt:
        return 0.5
    diff = (dt - datetime.now()).total_seconds()
    if diff <= 0:
        return 0.0
    return min(diff / (7 * 86400), 1.0)

# urgency_str is imported from core.display_utils above

def get_urgency_color(urgency) -> str:
    u = urgency_str(urgency)
    if u == "critical":
        return C.CRITICAL
    if u == "warning":
        return C.WARNING
    return C.SAFE

def get_urgency_badge(urgency) -> tuple:
    """Return (label, color)."""
    u = urgency_str(urgency)
    if u == "critical":
        return "CẤP BÁCH", C.CRITICAL
    if u == "warning":
        return "SẮP TỚI", C.WARNING
    return "AN TOÀN", C.SAFE

def get_status_tag(data: dict) -> tuple:
    """Lifecycle state: SẮP MỞ / CHƯA MỞ / ĐANG MỞ / QUÁ HẠN."""
    status       = data.get("submission_status", "unknown")
    is_open      = data.get("is_open", False)
    deadline_str = data.get("deadline", "")
    details      = data.get("details", {})
    open_time_str = details.get("open_time", "")
    act_type     = data.get("type", "")
    
    is_open_event = act_type == "open" or act_type.endswith("_open")

    # Ưu tiên kiểm tra thời gian mở nếu có
    if open_time_str:
        ot = parse_datetime(open_time_str)
        if ot:
            diff_h = (ot - datetime.now()).total_seconds() / 3600
            if diff_h > settings.OPENING_SOON_HOURS:
                return "CHƯA MỞ", C.TEXT_SECONDARY
            elif diff_h > 0:
                return "SẮP MỞ", _TYPE_COLORS.get("open", C.TEXT_SECONDARY)
            else:
                # Đã qua thời gian mở, ghi đè trạng thái
                is_open = True
                if status == "not_opened":
                    status = "unknown"

    if status == "not_opened":
        return "CHƯA MỞ", C.TEXT_SECONDARY

    if deadline_str:
        dt = parse_datetime(deadline_str)
        if dt:
            if dt.year >= 2099:
                return "ĐANG MỞ", C.SAFE

            diff_h = (dt - datetime.now()).total_seconds() / 3600

            # Xử lý riêng sự kiện lịch thuộc loại bắt đầu mở
            if is_open_event or deadline_str == open_time_str:
                if diff_h <= 0:
                    return "ĐANG MỞ", C.SAFE
                else:
                    return "SẮP MỞ", _TYPE_COLORS.get("open", C.TEXT_SECONDARY)

            if diff_h < 0:
                return "QUÁ HẠN", C.CRITICAL
            if is_open:
                return "ĐANG MỞ", C.SAFE
            
    if is_open:
        return "SẮP MỞ", _TYPE_COLORS.get("open", C.TEXT_SECONDARY)
    return "ĐANG MỞ", C.SAFE

def get_type_label(activity_type: str) -> str:
    return _TYPE_LABELS.get(activity_type, "KHÁC")

def get_type_color(activity_type: str) -> str:
    return _TYPE_COLORS.get(activity_type, C.ACCENT)

def clean_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'<p[^>]*>', '',   text)
    text = re.sub(r'</p>',      '\n', text)
    text = re.sub(r'<[^>]+>',   '',   text)
    text = re.sub(r'\n{3,}',   '\n\n', text)
    return text.strip()

def get_submission_badge(data: dict):
    """Return (label, color) or None. Only for types that have submissions."""
    act_type = data.get("type", "")
    # "open" and "other" các sự kiện này không có trạng thái nộp bài
    if act_type in ("open", "other"):
        return None

    details      = data.get("details", {})
    open_time_str = details.get("open_time", "")
    
    # Nếu chưa tới thời gian mở thì hiển thị tuỳ theo số giờ
    if open_time_str:
        ot = parse_datetime(open_time_str)
        if ot:
            diff_h = (ot - datetime.now()).total_seconds() / 3600
            if diff_h > settings.OPENING_SOON_HOURS:
                return "Chưa mở", C.TEXT_SECONDARY
            elif diff_h > 0:
                return "Sắp mở", C.WARNING

    # Trạng thái nộp lấy ở cấp độ ngoài (top-level)
    ss = data.get("submission_status", "unknown")
    
    # Dự phòng rà quét status_data bằng cách khớp chuỗi chính xác
    details     = data.get("details", {})
    status_data = details.get("status_data", {})
    
    # Nếu quiz có kết quả điểm thì ưu tiên hiển thị điểm
    if act_type == "quiz" and "KẾT QUẢ" in status_data:
        grade_text = status_data["KẾT QUẢ"]
        # Thử tìm chuỗi "10.00/10.00"

        grade_match = re.search(r"(\d+(\.\d+)?/\d+(\.\d+)?)", grade_text)
        if grade_match:
            return f"Điểm: {grade_match.group(1)}", C.SAFE
        return "Đã có điểm", C.SAFE
        
    if ss == "not_opened":
        return "Chưa mở", C.TEXT_SECONDARY
    if ss == "graded":
        return "Đã chấm", C.SAFE
    if ss == "submitted":
        return ("Đã nộp" if act_type != "quiz" else "Đã làm"), C.SAFE
    if ss == "not_submitted":
        return ("Chưa nộp" if act_type != "quiz" else "Chưa làm"), C.TEXT_SECONDARY

    submission  = ""
    grading     = ""
    for k, v in status_data.items():
        kl = k.lower()
        if "submission status" in kl or "trạng thái nộp" in kl or "trạng thái bài nộp" in kl:
            submission = v
        elif "grading status" in kl or "trạng thái chấm" in kl:
            grading = v

    if grading:
        g_low = grading.lower()
        if ("graded" in g_low or "đã chấm" in g_low) and "not graded" not in g_low:
            return "Đã chấm", C.SAFE

    if submission:
        if any(x in submission.lower() for x in ["submitted", "đã nộp"]):
            return ("Đã nộp" if act_type != "quiz" else "Đã làm"), C.SAFE
        if any(x in submission.lower() for x in ["not submitted", "chưa nộp", "no submissions"]):
            return ("Chưa nộp" if act_type != "quiz" else "Chưa làm"), C.TEXT_SECONDARY
    return None
