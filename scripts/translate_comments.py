import os
import re

GUI_DIR = os.path.abspath(r"E:\Projects\UTH-Elearning-Alert\src\gui")

replacements = {
    r"# ── Path setup": "# ── Cài đặt đường dẫn",
    r"# Thread synchronization": "# Đồng bộ luồng (Thread)",
    r"# Window": "# Cấu hình cửa sổ Flet",
    r"# all \| critical \| warning \| safe \| overdue": "# tất cả | cấp bách | nhắc nhở | an toàn | quá hạn",
    r"# all \| quiz \| assignment \| attendance": "# tất cả | quiz | bài tập | điểm danh",
    r"# search query": "# từ khóa tìm kiếm",
    r"# cache key: \(urgency, type\) → list\[ActivityCard\]": "# lưu trữ (cache) các thẻ theo (urgency, type)",
    r"# ── Status \/ footer controls": "# ── Trạng thái / Thanh công cụ dưới",
    r"# \(key, label, color\)": "# (khóa, nhãn hiển thị, màu sắc)",
    r"# ── Filter popup config": "# ── Cấu hình popup bộ lọc",
    r"# update checkmarks": "# cập nhật trạng thái chọn",
    r"# ── Card list area": "# ── Khu vực danh sách thẻ (Card items)",
    r"# Only use cache when no search query": "# Chỉ sử dụng cache khi không có truy vấn tìm kiếm",
    r"# Urgency filter": "# Lọc theo mức độ",
    r"# Type filter — \"open\" filter uses is_open flag, others use type field": "# Lọc theo loại - Nếu là 'Sắp mở' sẽ dùng cờ is_open thay vì type",
    r"# Search filter": "# Lọc theo từ khóa",
    r"# Don't cache search results — cache only plain urgency\+type filtered results": "# Không cache kết quả tìm kiếm (chỉ lưu cache bộ lọc thông thường)",
    r"# ── Data loading": "# ── Tải dữ liệu",
    r"# Merge enriched data \(type, submission_status, details\) back into all_data": "# Ghép dữ liệu chi tiết vào lại all_data",
    r"# Preserve original urgency/deadline \(detail page may not have it\)": "# Giữ nguyên thời hạn và mức độ cũ (trong chi tiết có thể không có)",
    r"# Cancel any ongoing prefetch before starting new load": "# Huỷ các tiến trình tải trước (prefetch) đang diễn ra",
    r"# Invalidate card cache since data changed": "# Xoá cache do dữ liệu đã thay đổi",
    r"# Kick off background prefetch for all details": "# Khởi chạy luồng lấy dữ liệu chi tiết chạy ngầm",
    r"# ── Views": "# ── Các màn hình",
    r"# Called from card on_click \(sync context\) — schedule async task": "# Được gọi khi nhấn vào Thẻ, tự chuyển thành async task",
    r"# ── Header": "# ── Khu vực Tiêu đề",
    r"# Checkbox \"Hiện quá hạn\" — toggles INCLUDE_PAST_DUE and re-renders": "# Checkbox Hiện quá hạn, cập nhật lại trạng thái",
    r"# ── Countdown timer \(async, runs on Flet's event loop\)": "# ── Bộ đếm lùi thời gian (chạy ngầm theo event loop của Flet)",
    r"# ── Pulse loop \(nhấp nháy glow cho critical cards\)": "# ── Luồng nhấp nháy tạo hiệu ứng glow cho thẻ Cấp bách",
    r"# ── Initial load": "# ── Tải dữ liệu lần đầu",
    r"# 16×16 solid blue icon as a placeholder": "# icon mặc định",
    r"# Same as assignment": "# giống màu bài tập",
    r"# violet": "# tím",
    r"# blue": "# xanh dương",
    r"# amber": "# cam",
    r"# same as assignment": "# màu tương tự bài tập",
    r"# cyan": "# xanh lơ",
    r"# gray": "# xám",
    r"# Format: \[ID\]_HKII2024-2025_TênMôn_MãHP": "# Định dạng ban đầu: [ID]_HKII..._Tên_Môn MãHP",
    r"# Format: \[ID\] \- Tên môn \- MãHP.*": "# Định dạng hiển thị bằng dấu gạch ngang",
    r"# Fallback: strip leading \[anything\]": "# Nếu không khớp, xoá tiền tố ngoặc vuông",
    r"events don't have submission status": "các sự kiện này không có trạng thái nộp bài",
    r"# Fast path: top-level submission_status \(from parse_activity_page\)": "# Trạng thái nộp lấy ở cấp độ ngoài (top-level)",
    r"# Fallback: status_data — strict key matching only": "# Dự phòng rà quét status_data bằng cách khớp chuỗi chính xác",
    r"# ── Activity Card": "# ── Thành phần Thẻ (Activity Card)",
    r"# Best available course name: prefer full_name from prefetched details": "# Dùng tên môn học đầy đủ nhất được lấy ra",
    r"# Optional submission pill": "# Nhãn đánh giá tuỳ chọn",
    r"# Row 1 — left chips · urgency badge \(right\)": "# Cột 1 - chip loại và nhãn cảnh báo",
    r"# Row 2 — course name \(nhỏ, trên tiêu đề\)": "# Dòng 2 - tên môn học",
    r"# Row 3 — title": "# Dòng 3 - Tiêu đề",
    r"# Row 4 — deadline · countdown \(combined, no labels\)": "# Dòng 4 - Thời hạn",
    r"# Row 5 — progress bar": "# Dòng 5 - Thanh tiến độ",
    r"# Row 6 — optional submission pill": "# Dòng 6 - Trạng thái nộp bài",
    r"# ── Detail View": "# ── Thành phần Màn hình chi tiết (Detail View)",
    r"# We need _cards_lock and DataOrchestrator maybe\? Wait, DetailView uses logger, _cards_lock, DataOrchestrator\?": "",
    r"# We'll see what it needs.": "",
    r"# callable → MoodleClient \(for autologin URL\)": "# hàm lấy MoodleClient để truy xuất đường dẫn đăng nhập",
    r"# ── public ──": "# ── Hàm công khai ──",
    r"# Caller responsible for page\.update\(\) — avoids double round-trip": "# Hàm gọi chịu trách nhiệm page.update() tránh việc gọi thừa",
    r"# Prefer full course name from detail page if available": "# Trích xuất thông tin Tên môn học",
    r"# ── Submission status ──": "# ── Trạng thái nộp bài ──",
    r"# ── Quiz info ──": "# ── Thông tin Quiz ──",
    r"# ── Attendance records ──": "# ── Thông tin Điểm danh ──",
    r"# Known semantic columns — rendered specially; rest rendered as generic rows": "# Các cột phổ biến, dùng layout đặc biệt",
    r"# Extra columns \(dynamic \- anything the parser extracted beyond known ones\)": "# Các cột phụ khác",
    r"# ── Description ──": "# ── Mô tả ──",
    r"# Caller responsible for page\.update\(\)": "# Chỉ thay đổi giao diện, không tự gọi update",
    r"# ── private ──": "# ── Hàm nội bộ (Private) ──",
    r"# Tab 1: autologin → course page \(establishes browser session\)": "# Mở tự động đăng nhập (Tab 1)",
    r"# Prefer course_id from details page if available": "# Ưu tiên dùng course_id nếu có",
    r"# Tab 2: activity URL after a short delay \(session cookie propagates\)": "# Delay mở môn học để đợi tự động đăng nhập thực thi (Tab 2)",
    r"# Fallback: no token / no course_id → open directly": "# Dự phòng mở thẳng khi không có cookie",
    r"# ── Settings View": "# ── Thành phần Màn hình Cài đặt (Settings)",
    r"# ignore": "",
    r"# ── Main": "# ── Hàm chạy chính (Main)",
    r"# Types that visually belong to the \"deadline\" filter bucket": "# Các thể loại hoạt động thuộc cùng bộ lọc bài tập",
    r"# Filter key → matching type set": "# Phân luồng bộ lọc loại tương ứng",
    r"# Chip color per type \(falls back to accent\)": "# Màu sắc chip cho từng loại (khác màu tổng)",
    r"# force_refresh": "# ép làm mới"
}

def clean_file(path):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    for old, new in replacements.items():
        content = re.sub(old, new, content, flags=re.IGNORECASE)
        
    # Xoá các khoảng trắng dư thừa do xoá comment
    content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

for root, _, files in os.walk(GUI_DIR):
    for fl in files:
        if fl.endswith(".py"):
            full_path = os.path.join(root, fl)
            clean_file(full_path)

print("Done cleaning comments.")
