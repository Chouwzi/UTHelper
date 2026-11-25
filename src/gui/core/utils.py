import html as _html
import re
import urllib.parse as _urlparse
from datetime import datetime
from core.time_utils import parse_datetime
from core.activity_status import normalize_submission_status
from core.activity_time_policy import is_late_submission_active
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
            if months:
                return (
                    f"Đã mở {months} tháng {remaining_days} ngày {hours} giờ {mins} phút",
                    False,
                )
            if days:
                return f"Đã mở {days} ngày {hours} giờ {mins} phút", False
            if hours:
                return f"Đã mở {hours} giờ {mins} phút", False
            return f"Đã mở {mins} phút", False

        if months:
            return (
                f"Quá hạn {months} tháng {remaining_days} ngày {hours} giờ {mins} phút",
                True,
            )
        if days:
            return f"Quá hạn {days} ngày {hours} giờ {mins} phút", True
        if hours:
            return f"Quá hạn {hours} giờ {mins} phút", True
        return f"Quá hạn {mins} phút", True
    else:
        prefix = "Mở sau" if is_open_event else "Còn"
        if months:
            return (
                f"{prefix} {months} tháng {remaining_days} ngày {hours} giờ {mins} phút",
                False,
            )
        if days:
            return f"{prefix} {days} ngày {hours} giờ {mins} phút", False
        if hours:
            return f"{prefix} {hours} giờ {mins} phút", False
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
    if u in ("critical", "overdue"):
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
        return C.CRITICAL      # Red - urgent
    if diff_seconds < 259200:    # < 3 days (72h)
        return C.WARNING       # Orange - approaching
    return C.SAFE              # Green - safe

def get_urgency_badge(urgency) -> tuple:
    """Return (label, color)."""
    u = urgency_str(urgency)
    if u == "critical":
        return "KHẨN CẤP", C.CRITICAL
    if u == "warning":
        return "SẮP HẠN", C.WARNING
    if u == "overdue":
        return "QUÁ HẠN", C.CRITICAL
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
                if is_late_submission_active(data):
                    return "NỘP MUỘN", C.WARNING
                return "QUÁ HẠN", C.CRITICAL
            if is_open:
                return "ĐANG MỞ", C.SAFE
            
    if is_open:
        return "SẮP MỞ", _TYPE_COLORS.get("open", C.TEXT_SECONDARY)
    return "ĐANG MỞ", C.SAFE

def get_card_countdown(data: dict) -> tuple:
    """Return (text, is_overdue, color, progress_val).

    Handles active late submission by counting down to the cutoff deadline
    with warning styling, while overdue items with expired/missing cutoffs
    show critical overdue countdown.
    """
    deadline_str = data.get("deadline", "")
    act_type = data.get("type", "")
    if not deadline_str:
        return "", False, C.TEXT_SECONDARY, 0.0

    dt = parse_datetime(deadline_str)
    if not dt:
        return "Không xác định", False, C.TEXT_SECONDARY, 0.5
    if dt.year >= 2099:
        return "Không có thời hạn", False, C.SAFE, 1.0

    now = datetime.now()
    diff = dt - now
    if diff.total_seconds() < 0:
        if is_late_submission_active(data, now):
            cutoff = data.get("cutoff_date") or data.get("cutoffdate")
            cutoff_ts = None
            if isinstance(cutoff, (int, float)) and cutoff > 0:
                cutoff_ts = float(cutoff)
            elif isinstance(cutoff, str) and cutoff.strip():
                try:
                    cutoff_ts = float(cutoff)
                except ValueError:
                    parsed = parse_datetime(cutoff)
                    if parsed:
                        cutoff_ts = parsed.timestamp()

            if cutoff_ts and cutoff_ts > now.timestamp():
                remaining_s = int(cutoff_ts - now.timestamp())
                hours_left = remaining_s // 3600
                mins_left = (remaining_s % 3600) // 60
                if hours_left >= 24:
                    days_left = hours_left // 24
                    cd_text = f"Còn {days_left} ngày nộp muộn"
                elif hours_left > 0:
                    cd_text = f"Còn {hours_left} giờ {mins_left:02d} nộp muộn"
                else:
                    cd_text = f"Còn {mins_left} phút nộp muộn"
                progress_val = min(remaining_s / (7 * 86400), 1.0)
            else:
                cd_text = "Còn nộp muộn"
                progress_val = 0.0
            return cd_text, False, C.WARNING, progress_val

        cd_text, _ = get_countdown(deadline_str, act_type)
        return cd_text, True, C.CRITICAL, 0.0

    cd_text, overdue = get_countdown(deadline_str, act_type)
    cd_color = get_countdown_color(deadline_str)
    progress_val = get_progress_value(deadline_str)
    return cd_text, overdue, cd_color, progress_val

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
    ss = normalize_submission_status(data.get("submission_status", "unknown"))
    
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
    if ss == "draft":
        return "Bản nháp", C.WARNING
    if act_type == "quiz":
        if ss == "in_progress":
            return "Đang làm", C.WARNING
        if ss == "overdue":
            return "Quá hạn", C.CRITICAL
        if ss == "abandoned":
            return "Chưa hoàn thành", C.WARNING
        if ss == "attempted":
            return "Đã bắt đầu", C.WARNING

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
        fallback_status = normalize_submission_status(submission)
        if fallback_status in ("submitted", "graded"):
            return ("Đã nộp" if act_type != "quiz" else "Đã làm"), C.SAFE
        if fallback_status == "not_submitted":
            return ("Chưa nộp" if act_type != "quiz" else "Chưa làm"), C.TEXT_SECONDARY
        if fallback_status == "draft":
            return "Bản nháp", C.WARNING
    return None


import hashlib
import os
import shutil
import tempfile
import urllib.parse
import urllib.request
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DESCRIPTION_IMAGE_CHUNK_BYTES = 64 * 1024
_DESCRIPTION_IMAGE_MAX_BYTES = 8 * 1024 * 1024

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


def _clear_image_cache_directory(cache_dir: Path) -> int:
    """Safely remove regular files directly inside ``cache_dir`` without following symlinks."""
    removed = 0
    try:
        if cache_dir.is_symlink() or not cache_dir.exists() or not cache_dir.is_dir():
            return 0
        resolved_root = cache_dir.resolve()
        entries = list(cache_dir.iterdir())
    except OSError as exc:
        logger.debug("Skipping description image cache clear for %s: %s", cache_dir, exc)
        return 0

    for entry in entries:
        try:
            if entry.is_symlink():
                if entry.parent.resolve() == resolved_root:
                    entry.unlink(missing_ok=True)
                    removed += 1
                continue
            if not entry.is_file():
                continue
            if entry.resolve().parent != resolved_root:
                continue
            entry.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.debug("Could not remove cached description image %s: %s", entry, exc)
    return removed


def clear_cached_description_images() -> int:
    """Purge cached Moodle description images from user data and mirrored assets cache."""
    from config import _USER_DATA_DIR

    user_cache_dir = Path(_USER_DATA_DIR) / "cache" / "images"
    removed = _clear_image_cache_directory(user_cache_dir)

    try:
        assets_cache_dir = get_active_assets_dir() / "cache" / "images"
        if assets_cache_dir.exists() and not assets_cache_dir.is_symlink():
            if (
                user_cache_dir.is_symlink()
                or not user_cache_dir.exists()
                or assets_cache_dir.resolve() != user_cache_dir.resolve()
            ):
                removed += _clear_image_cache_directory(assets_cache_dir)
    except OSError as exc:
        logger.debug("Skipping mirrored assets image cache clear: %s", exc)

    return removed


_DEFAULT_URLLIB_URLOPEN = urllib.request.urlopen


def _parse_trusted_moodle_pluginfile_url(
    raw_url: str, trusted_origin: str
) -> tuple[str, str] | None:
    """Validate an untrusted ``<img src>`` strictly against the trusted HTTPS Moodle origin.

    Returns ``(normalized_ws_path, original_path)`` only when ``raw_url`` uses
    ``https``, has no userinfo or control characters, matches ``trusted_origin``
    exactly, and targets Moodle's ``/pluginfile.php`` endpoint.
    """
    if not raw_url or not trusted_origin:
        return None
    cleaned = raw_url.strip()
    if not cleaned or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        return None
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        origin_parts = urllib.parse.urlsplit(trusted_origin)
    except ValueError:
        return None

    if parsed.scheme.lower() != "https" or origin_parts.scheme.lower() != "https":
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if not parsed.netloc or parsed.netloc.lower() != origin_parts.netloc.lower():
        return None

    path = parsed.path or ""
    if "/pluginfile.php" not in path or ".." in path.split("/"):
        return None
    if not (
        path.startswith("/pluginfile.php/")
        or path.startswith("/webservice/pluginfile.php/")
        or path in ("/pluginfile.php", "/webservice/pluginfile.php")
    ):
        return None

    ws_path = path
    if not ws_path.startswith("/webservice/"):
        ws_path = ws_path.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
    return ws_path, path


def pre_cache_description_images(html_content: str) -> str:
    """
    Tải xuống và cache toàn bộ ảnh từ description_html vào ổ đĩa local,
    thay thế URL mạng bằng đường dẫn tương đối để Flet hiển thị.
    """
    if not html_content:
        return html_content

    from config import _USER_DATA_DIR, settings
    from core.demo_sandbox import is_demo_account

    if is_demo_account(getattr(settings, "UTH_USERNAME", "")):
        return html_content

    img_tags = re.findall(r'<img[^>]+>', html_content)
    if not img_tags:
        return html_content

    from core.client import _SameMoodleOriginRedirectHandler, _is_same_moodle_origin_url
    from core.moodle_sites import moodle_site_from_origin

    cache_dir = _USER_DATA_DIR / "cache" / "images"
    cache_dir.mkdir(parents=True, exist_ok=True)

    token = settings.MOODLE_WS_TOKEN
    base_url = settings.MOODLE_BASE_URL or "https://courses.ut.edu.vn"
    trusted_site = moodle_site_from_origin(base_url)
    if trusted_site is None:
        return html_content
    trusted_origin = trusted_site.origin
    token_matches_origin = bool(
        token
        and (
            not getattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")
            or settings.MOODLE_WS_TOKEN_ORIGIN == trusted_origin
        )
    )

    for tag in img_tags:
        src_match = re.search(r'src="([^"]+)"', tag)
        if not src_match:
            continue

        orig_url = src_match.group(1)
        validated = _parse_trusted_moodle_pluginfile_url(orig_url, trusted_origin)
        if validated is None:
            continue
        ws_path, orig_path = validated

        public_ws_url = f"{trusted_origin}{ws_path}"
        auth_url = public_ws_url
        if token_matches_origin:
            auth_url = f"{public_ws_url}?token={urllib.parse.quote(token, safe='')}"

        url_hash = hashlib.sha256(orig_path.encode('utf-8')).hexdigest()
        ext = os.path.splitext(orig_path)[1]
        if not ext or not re.fullmatch(r"\.[A-Za-z0-9]{1,5}", ext):
            ext = ".png"

        cache_filename = f"{url_hash}{ext}"
        cache_file_path = cache_dir / cache_filename

        downloaded = True
        if not cache_file_path.exists():
            temporary_path = None
            try:
                logger.info("Downloading Moodle image: %s", orig_url)
                req = urllib.request.Request(auth_url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
                if urllib.request.urlopen is _DEFAULT_URLLIB_URLOPEN:
                    opener = urllib.request.build_opener(
                        _SameMoodleOriginRedirectHandler(trusted_origin)
                    )
                    cm = opener.open(req, timeout=8)
                else:
                    cm = urllib.request.urlopen(req, timeout=8)
                with cm as resp:
                    geturl = getattr(resp, "geturl", None)
                    final_url = str(geturl() or "") if callable(geturl) else ""
                    if final_url and not _is_same_moodle_origin_url(final_url, trusted_origin):
                        raise ValueError("Image response redirected outside trusted Moodle origin")
                    if resp.status != 200:
                        raise ValueError(f"Image response status was {resp.status}")
                    headers = getattr(resp, "headers", None)
                    content_type = ""
                    if headers is not None and hasattr(headers, "get_content_type"):
                        content_type = str(headers.get_content_type() or "").casefold()
                    if content_type and not content_type.startswith("image/"):
                        raise ValueError(f"Image response content type was {content_type}")

                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        prefix=f".{cache_filename}.",
                        suffix=".tmp",
                        dir=cache_dir,
                        delete=False,
                    ) as temporary_file:
                        temporary_path = Path(temporary_file.name)
                        total_bytes = 0
                        while True:
                            chunk = resp.read(_DESCRIPTION_IMAGE_CHUNK_BYTES)
                            if not chunk:
                                break
                            total_bytes += len(chunk)
                            if total_bytes > _DESCRIPTION_IMAGE_MAX_BYTES:
                                raise ValueError("Image response exceeded 8 MiB")
                            temporary_file.write(chunk)
                        if total_bytes == 0:
                            raise ValueError("Image response was empty")

                    os.replace(temporary_path, cache_file_path)
                    temporary_path = None
                    logger.info("Successfully cached image to %s", cache_file_path)
            except Exception as e:
                logger.warning("Failed to cache image %s: %s", orig_url, e)
                downloaded = False
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        logger.debug(
                            "Failed to remove partial image %s",
                            temporary_path,
                            exc_info=True,
                        )

        if downloaded and cache_file_path.exists():
            # Mirror sang active assets và lấy đường dẫn tương đối
            assets_rel_path = ensure_image_in_assets(cache_file_path)
            new_tag = tag.replace(orig_url, assets_rel_path)
            html_content = html_content.replace(tag, new_tag)
        else:
            # Never embed WS token into rendered HTML/Markdown on fallback
            new_tag = tag.replace(orig_url, public_ws_url)
            html_content = html_content.replace(tag, new_tag)

    # Chạy dọn dẹp cache sau mỗi lần tải ảnh mới
    cleanup_image_cache()

    return html_content


_SAFE_CACHED_IMAGE_RE = re.compile(
    r"^cache/images/[0-9a-f]{64}\.[A-Za-z0-9]{1,5}$"
)


def _sanitize_markdown_url(raw_url: str, *, allow_cached_image: bool = False) -> str | None:
    """Allow only clean http(s) URLs (or internal cached Moodle image paths)."""
    unescaped = _html.unescape(raw_url or "").strip()
    if not unescaped:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in unescaped):
        return None
    if allow_cached_image and _SAFE_CACHED_IMAGE_RE.match(unescaped):
        return unescaped
    if "\\" in unescaped or any(ch.isspace() for ch in unescaped):
        return None
    try:
        parsed = _urlparse.urlsplit(unescaped)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    return unescaped.replace("(", "%28").replace(")", "%29")


def html_to_markdown(html_content: str) -> str:
    """
    Chuyển đổi các thẻ HTML cơ bản từ Moodle sang cú pháp Markdown.
    """
    if not html_content:
        return ""

    text = re.sub(r'<br\s*/?>', '\n', html_content, flags=re.IGNORECASE)
    text = re.sub(r'<p[^>]*>', '\n\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</p>', '\n\n', text, flags=re.IGNORECASE)

    text = re.sub(r'<(b|strong)[^>]*>(.*?)</\1>', r'**\2**', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<(i|em)[^>]*>(.*?)</\1>', r'*\2*', text, flags=re.IGNORECASE | re.DOTALL)

    def _link_repl(match):
        tag = match.group(0)
        label = match.group(1)
        href_match = re.search(
            r'''href\s*=\s*(?:"([^"]*)"|'([^']*)')''',
            tag,
            flags=re.IGNORECASE,
        )
        if not href_match:
            return label
        raw_href = href_match.group(1) if href_match.group(1) is not None else href_match.group(2)
        safe_href = _sanitize_markdown_url(raw_href)
        if safe_href is None:
            return label
        safe_label = label.replace("[", r"\[").replace("]", r"\]")
        return f"[{safe_label}]({safe_href})"

    text = re.sub(r'<a\b[^>]*>(.*?)</a>', _link_repl, text, flags=re.IGNORECASE | re.DOTALL)

    def _img_repl(match):
        tag = match.group(0)
        src_match = re.search(
            r'''src\s*=\s*(?:"([^"]*)"|'([^']*)')''',
            tag,
            flags=re.IGNORECASE,
        )
        if not src_match:
            return ""
        raw_src = src_match.group(1) if src_match.group(1) is not None else src_match.group(2)
        alt_match = re.search(
            r'''alt\s*=\s*(?:"([^"]*)"|'([^']*)')''',
            tag,
            flags=re.IGNORECASE,
        )
        raw_alt = (
            (alt_match.group(1) if alt_match.group(1) is not None else alt_match.group(2))
            if alt_match
            else ""
        )
        safe_src = _sanitize_markdown_url(raw_src, allow_cached_image=True)
        if safe_src is None:
            return raw_alt
        safe_alt = (
            raw_alt.replace("[", "").replace("]", "").replace("\r", " ").replace("\n", " ").strip()
            or "Hình ảnh"
        )
        return f"\n\n![{safe_alt}]({safe_src})\n\n"

    text = re.sub(r'<img\b[^>]*>', _img_repl, text, flags=re.IGNORECASE)

    text = re.sub(r'<ul[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</ul>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<ol[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</ol>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', text, flags=re.IGNORECASE)

    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text.strip()


DEMO_LMS_WEBVIEW_GUARD_MESSAGE: str = (
    "Phiên xem thử (Demo) sử dụng dữ liệu mẫu ngoại tuyến. "
    "Đăng nhập bằng tài khoản sinh viên UTH để mở cổng LMS trực tuyến."
)


def is_uth_lms_url(url: object) -> bool:
    """Return True when ``url`` is an HTTP(S) URL targeting a live UTH host (``*.ut.edu.vn``).

    Used as a Demo Mode navigation guard (never as a credential/trust allowlist;
    use ``core.url_validation.is_trusted_uth_lms_url`` for strict HTTPS trust).
    """
    if not isinstance(url, str):
        return False
    candidate = url.strip()
    if not candidate:
        return False
    if "\\" in candidate or any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in candidate
    ):
        return False
    try:
        parsed = _urlparse.urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False
    if port is not None and not (1 <= port <= 65535):
        return False
    raw_host = (parsed.hostname or "").casefold()
    host = raw_host[:-1] if raw_host.endswith(".") else raw_host
    if not host:
        return False
    labels = host.split(".")
    if any(not label for label in labels):
        return False
    if host == "ut.edu.vn" or host.endswith(".ut.edu.vn"):
        return True
    from core.url_validation import is_trusted_uth_lms_url

    return is_trusted_uth_lms_url(candidate)

