import re
from datetime import datetime
from core.time_utils import parse_datetime
from gui.core.theme import C, _TYPE_LABELS, _TYPE_COLORS
from config import settings
from core.display_utils import urgency_str, clean_course_name  # re-exported

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


def get_countdown_color(deadline_str: str) -> str:
    """Time-based countdown color: red < 24h, orange < 3d, green > 3d.
    
    Unlike get_urgency_color (which uses the urgency tag), this function
    colors based on ACTUAL remaining time, giving a natural visual gradient.
    """
    dt = parse_datetime(deadline_str)
    if not dt:
        return C.TEXT_SECONDARY
    
    diff_seconds = (dt - datetime.now()).total_seconds()
    
    if diff_seconds <= 0:
        return C.CRITICAL      # Overdue
    if diff_seconds < 86400:     # < 24 hours
        return C.CRITICAL      # Red — urgent
    if diff_seconds < 259200:    # < 3 days (72h)
        return C.WARNING       # Orange — approaching
    return C.SAFE              # Green — safe

def get_urgency_badge(urgency) -> tuple:
    """Return (label, color)."""
    u = urgency_str(urgency)
    if u == "critical":
        return "KHẨN CẤP", C.CRITICAL
    if u == "warning":
        return "SẮP HẠN", C.WARNING
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


import hashlib
import os
import shutil
import urllib.parse
import urllib.request
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def get_active_assets_dir() -> Path:
    """Trả về thư mục assets đang hoạt động ở runtime."""
    flet_assets = os.environ.get("FLET_ASSETS_DIR")
    if flet_assets:
        return Path(flet_assets)
        
    # Relative fallback to src/assets
    utils_dir = Path(__file__).resolve().parent
    src_dir = utils_dir.parent.parent.parent
    assets_dir = src_dir / "assets"
    if assets_dir.exists():
        return assets_dir
        
    from config import _USER_DATA_DIR
    return _USER_DATA_DIR


def ensure_image_in_assets(cache_file_path: Path) -> str:
    """
    Đảm bảo tệp hình ảnh đã cache tồn tại trong thư mục assets đang hoạt động
    để Flet có thể load được trong cả chế độ Web và Desktop.
    Nếu chạy trên nền tảng di động (Android/iOS) nơi thư mục assets là read-only,
    hàm sẽ trả về đường dẫn tuyệt đối thô (raw absolute path) để tải trực tiếp từ persistent cache.
    """
    try:
        if not cache_file_path.exists():
            return ""
            
        assets_dir = get_active_assets_dir()
        if assets_dir == cache_file_path.parent.parent.parent:
            # Nếu thư mục assets trùng với USER_DATA_DIR
            return f"cache/images/{cache_file_path.name}"
            
        dest_dir = assets_dir / "cache" / "images"
        
        # Thử tạo thư mục (ném lỗi OSError nếu assets là read-only trên Mobile)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / cache_file_path.name
        
        # Sao chép tệp nếu chưa có hoặc kích thước khác nhau
        if not dest_file.exists() or dest_file.stat().st_size != cache_file_path.stat().st_size:
            shutil.copy2(cache_file_path, dest_file)
            logger.info(f"Đã sao chép ảnh cache vào assets: {dest_file}")
            
        return f"cache/images/{cache_file_path.name}"
    except OSError as e:
        # Trường hợp không ghi được vào assets (ví dụ: Read-only file system trên Android/iOS)
        logger.info(f"Thư mục assets không thể ghi (có thể đang ở Mobile). Sử dụng đường dẫn tuyệt đối: {e}")
        return str(cache_file_path)
    except Exception as e:
        logger.warning(f"Lỗi khi sao chép ảnh vào assets: {e}")
        return str(cache_file_path)


def cleanup_image_cache():
    """
    Dọn dẹp thư mục lưu trữ cache hình ảnh cục bộ dựa trên thời hạn lưu trữ (TTL)
    và giới hạn dung lượng đĩa tối đa (50MB).
    - Bước 1: Xoá tất cả các tệp cache đã tồn tại quá 14 ngày (TTL).
    - Bước 2: Nếu dung lượng vẫn vượt quá 50MB, xoá các tệp cũ nhất (LRU/MRU) cho tới khi về mức an toàn.
    """
    try:
        import time
        from config import _USER_DATA_DIR
        cache_dir = _USER_DATA_DIR / "cache" / "images"
        if not cache_dir.exists():
            return
            
        # Cấu hình thời hạn tối đa: 14 ngày (14 * 24 * 3600 giây)
        MAX_AGE_SECONDS = 14 * 24 * 3600
        # Giới hạn dung lượng tối đa: 50 MB
        MAX_SIZE = 50 * 1024 * 1024
        
        now = time.time()
        files = [cache_dir / f for f in os.listdir(cache_dir)]
        if not files:
            return
            
        assets_dir = get_active_assets_dir()
        assets_cache_dir = assets_dir / "cache" / "images"
        
        # Bước 1: Dọn dẹp theo thời hạn lưu trữ (TTL)
        remaining_files = []
        for f in files:
            if f.is_file():
                try:
                    stat = f.stat()
                    # Sử dụng mtime (thời gian ghi nhận/chỉnh sửa) vì atime có thể không được cập nhật trên một số HĐH di động
                    age = now - stat.st_mtime
                    if age > MAX_AGE_SECONDS:
                        f.unlink()
                        if assets_cache_dir.exists():
                            mirrored_file = assets_cache_dir / f.name
                            if mirrored_file.exists():
                                mirrored_file.unlink()
                        logger.info(f"Đã xoá ảnh cache hết hạn (tồn tại > 14 ngày): {f.name}")
                    else:
                        remaining_files.append((f, stat.st_size, stat.st_mtime))
                except Exception as e:
                    logger.warning(f"Lỗi khi kiểm tra tệp cache {f.name}: {e}")
                    
        # Bước 2: Dọn dẹp theo dung lượng tối đa (nếu vẫn vượt quá 50MB)
        remaining_files.sort(key=lambda x: x[2]) # Sắp xếp tệp cũ nhất lên trước
        total_size = sum(x[1] for x in remaining_files)
        
        if total_size <= MAX_SIZE:
            return
            
        logger.info(f"Dung lượng cache ảnh ({total_size} bytes) vẫn vượt giới hạn {MAX_SIZE} bytes. Tiến hành dọn dẹp thêm...")
        for f, size, _ in remaining_files:
            try:
                f.unlink()
                if assets_cache_dir.exists():
                    mirrored_file = assets_cache_dir / f.name
                    if mirrored_file.exists():
                        mirrored_file.unlink()
                        
                total_size -= size
                logger.info(f"Đã xoá ảnh cache cũ (vượt dung lượng): {f.name}")
                if total_size <= MAX_SIZE * 0.8:
                    break
            except Exception as e:
                logger.warning(f"Không thể xoá tệp cache {f.name}: {e}")
    except Exception as e:
        logger.warning(f"Lỗi khi chạy dọn dẹp cache ảnh: {e}")


def pre_cache_description_images(html_content: str) -> str:
    """
    Tải xuống và cache toàn bộ ảnh từ description_html vào ổ đĩa local,
    thay thế URL mạng bằng đường dẫn tương đối để Flet hiển thị.
    """
    if not html_content:
        return html_content

    img_tags = re.findall(r'<img[^>]+>', html_content)
    if not img_tags:
        return html_content

    from config import _USER_DATA_DIR, settings
    cache_dir = _USER_DATA_DIR / "cache" / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)

    token = settings.MOODLE_WS_TOKEN
    base_url = settings.MOODLE_BASE_URL or "https://courses.ut.edu.vn"
    base_domain = base_url.replace("https://", "").replace("http://", "")

    for tag in img_tags:
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            continue
        
        orig_url = src_match.group(1)
        if "pluginfile.php" not in orig_url or base_domain not in orig_url:
            continue

        parsed_url = urllib.parse.urlparse(orig_url)
        path = parsed_url.path
        
        # Chuẩn hoá sang webservice/pluginfile.php
        if not path.startswith("/webservice/"):
            path = path.replace("/pluginfile.php", "/webservice/pluginfile.php")
            
        auth_url = f"{parsed_url.scheme}://{parsed_url.netloc}{path}"
        if token:
            auth_url += f"?token={token}"
            
        url_hash = hashlib.sha256(parsed_url.path.encode('utf-8')).hexdigest()
        ext = os.path.splitext(parsed_url.path)[1]
        if not ext or len(ext) > 5:
            ext = ".png"
        
        cache_filename = f"{url_hash}{ext}"
        cache_file_path = cache_dir / cache_filename

        downloaded = True
        if not cache_file_path.exists():
            try:
                logger.info(f"Downloading Moodle image: {orig_url}")
                req = urllib.request.Request(auth_url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        with open(cache_file_path, 'wb') as f:
                            f.write(resp.read())
                        logger.info(f"Successfully cached image to {cache_file_path}")
                    else:
                        downloaded = False
            except Exception as e:
                logger.warning(f"Failed to cache image {orig_url}: {e}")
                downloaded = False

        if downloaded and cache_file_path.exists():
            # Mirror sang active assets và lấy đường dẫn tương đối
            assets_rel_path = ensure_image_in_assets(cache_file_path)
            new_tag = tag.replace(orig_url, assets_rel_path)
            html_content = html_content.replace(tag, new_tag)
        else:
            # Fallback sang link web service trực tuyến có kèm token
            new_tag = tag.replace(orig_url, auth_url)
            html_content = html_content.replace(tag, new_tag)

    # Chạy dọn dẹp cache sau mỗi lần tải ảnh mới
    cleanup_image_cache()

    return html_content


def html_to_markdown(html_content: str) -> str:
    """
    Chuyển đổi các thẻ HTML cơ bản từ Moodle sang cú pháp Markdown.
    """
    if not html_content:
        return ""

    text = re.sub(r'<br\s*/?>', '\n', html_content)
    text = re.sub(r'<p[^>]*>', '\n\n', text)
    text = re.sub(r'</p>', '\n\n', text)

    text = re.sub(r'<(b|strong)[^>]*>(.*?)</\1>', r'**\2**', text, flags=re.IGNORECASE)
    text = re.sub(r'<(i|em)[^>]*>(.*?)</\1>', r'*\2*', text, flags=re.IGNORECASE)

    text = re.sub(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.IGNORECASE)

    def _img_repl(match):
        tag = match.group(0)
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            return ""
        src = src_match.group(1)
        alt_match = re.search(r'alt="([^"]+)"', tag)
        alt = alt_match.group(1) if alt_match else "Hình ảnh"
        return f"\n\n![{alt}]({src})\n\n"
        
    text = re.sub(r'<img[^>]+>', _img_repl, text)

    text = re.sub(r'<ul[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</ul>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<ol[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</ol>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', text, flags=re.IGNORECASE)

    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()
