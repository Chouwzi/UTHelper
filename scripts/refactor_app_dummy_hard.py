import re

with open("src/gui/app_controller.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
skip = False
for i, line in enumerate(lines):
    if "def _test_notification_base(" in line:
        new_lines.append(line)
        new_lines.extend(
'''        import random, datetime
        from models import Assignment, ActivityDetail

        # Mặc định
        delta = datetime.timedelta(hours=10)
        title = 'BÀI KIỂM THỬ KHẨN CẤP (< 24h)'
        event_type = 'deadline'
        course_name = 'Công nghệ Phần mềm'

        if mock_type == "warning":
            delta = datetime.timedelta(hours=48)
            title = 'BÀI KIỂM THỬ SẮP TỚI HẠN (2-3 ngày)'
        elif mock_type == "safe":
            delta = datetime.timedelta(days=5)
            title = 'BÀI KIỂM THỬ AN TOÀN (> 3 ngày)'
        elif mock_type == "quiz":
            delta = datetime.timedelta(hours=10)
            title = 'BÀI TRẮC NGHIỆM ĐANG MỞ (Quiz)'
            event_type = 'quiz'
            course_name = 'Mạng Máy Tính'
        elif mock_type == "attendance":
            delta = datetime.timedelta(hours=2)
            title = 'BÀI ĐIỂM DANH (Sắp đóng)'
            event_type = 'attendance'
            course_name = 'Triết học Mác - Lênin'
            
        now = datetime.datetime.now()
        dummy_details = ActivityDetail(
            open_time=now - datetime.timedelta(days=2),
            description_html="<p>Đây là bài tập kiểm thử được khởi tạo bởi Debug Mode.</p>",
        )

        return Assignment(
            id=str(random.randint(1000, 9999)),
            course_id="0",
            course_name=course_name,
            title=title,
            event_type=event_type,
            deadline=now + delta,
            url="https://lms.hcmut.edu.vn",
            details=dummy_details
        )
'''.splitlines(True)
        )
        skip = True
    elif skip and "def _on_test_tray" in line:
        skip = False
        new_lines.append(line)
    elif not skip:
        new_lines.append(line)

with open("src/gui/app_controller.py", "w", encoding="utf-8") as f:
    f.writelines(new_lines)
print("done")
