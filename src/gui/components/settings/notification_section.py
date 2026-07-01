import flet as ft
from gui.core.theme import C
from config import settings

def init_notification_controls(view):
    """Khởi tạo các control thiết lập cấu hình thông báo và chế độ Do Not Disturb (DND)."""
    # Định nghĩa cấu hình có sẵn của các Notification Profile
    _PROFILES = {
        "quiet": {"icon": ft.Icons.NOTIFICATIONS_OFF_OUTLINED, "label": "Yên tĩnh", "desc": "Chỉ deadline gấp", "milestones": [24, 1], "dnd": True, "dnd_start": 22, "dnd_end": 8, "min_before": 0},
        "balanced": {"icon": ft.Icons.NOTIFICATIONS_OUTLINED, "label": "Cân bằng", "desc": "Mặc định", "milestones": [72, 24, 3], "dnd": True, "dnd_start": 22, "dnd_end": 7, "min_before": 30},
        "exam_week": {"icon": ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED, "label": "Tuần thi", "desc": "Không bỏ lỡ gì", "milestones": [72, 24, 6, 1], "dnd": False, "dnd_start": 22, "dnd_end": 7, "min_before": 15},
    }
    view._current_profile = getattr(settings, 'NOTIFICATION_PROFILE', 'balanced')
    view._profile_cards = {}
    view._profile_summary = ft.Text("", size=12, color=C.TEXT_SECONDARY, italic=True)

    # Dựng các thẻ lựa chọn profile cấu hình thông báo nhanh
    for pkey, pval in _PROFILES.items():
        is_sel = (pkey == view._current_profile)
        card = ft.Container(
            content=ft.Column([
                ft.Icon(pval["icon"], size=24, color=C.ACCENT if is_sel else C.TEXT_SECONDARY),
                ft.Text(pval["label"], size=13, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER),
                ft.Text(pval["desc"], size=11, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
            ], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            width=110, padding=12, border_radius=12,
            bgcolor=C.ACCENT + "15" if is_sel else C.SURFACE,
            border=ft.Border.all(2, C.ACCENT) if is_sel else ft.Border.all(1, C.BORDER),
            on_click=view._handle_profile_select(pkey),
            ink=True,
        )
        view._profile_cards[pkey] = card

    view._profile_row = ft.Row(
        controls=list(view._profile_cards.values()),
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=10,
    )

    # Switch bật/tắt chế độ DND (Không làm phiền)
    view._sw_dnd_enable = ft.Switch(
        value=getattr(settings, 'NOTIFY_DND_ENABLE', False), active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Không làm phiền",
        on_change=lambda e: view._update_dnd_summary()
    )
    # Khung nhập giờ bắt đầu DND
    view._dnd_start_field = ft.TextField(
        value=str(getattr(settings, 'NOTIFY_DND_START', 22)),
        label="Từ (giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        width=150, text_align=ft.TextAlign.CENTER,
        prefix_icon=ft.Icons.DARK_MODE_OUTLINED,
        on_change=lambda e: view._update_dnd_summary()
    )
    # Khung nhập giờ kết thúc DND
    view._dnd_end_field = ft.TextField(
        value=str(getattr(settings, 'NOTIFY_DND_END', 7)),
        label="Đến (giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        width=150, text_align=ft.TextAlign.CENTER,
        prefix_icon=ft.Icons.LIGHT_MODE_OUTLINED,
        on_change=lambda e: view._update_dnd_summary()
    )
    view._dnd_summary = ft.Text("", size=12, color=C.TEXT_SECONDARY, italic=True)
    view._dnd_time_row = ft.Row(
        controls=[
            view._dnd_start_field,
            ft.Text("-", size=16, color=C.TEXT_SECONDARY, weight=ft.FontWeight.BOLD),
            view._dnd_end_field,
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=8,
    )

    # Switch tắt thông báo đối với các hoạt động đã hoàn tất nộp bài
    view._sw_ignore_sub = ft.Switch(
        value=getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True), active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Bỏ qua bài đã nộp"
    )

    # Danh sách các loại hoạt động Moodle được nhắc thông báo
    _current_types = getattr(settings, 'NOTIFY_TYPES', ["quiz", "assignment", "attendance"])
    view._notify_type_checks = {}
    _type_options = [
        ("quiz",       "Trắc nghiệm"),
        ("assignment", "Bài tập"),
        ("attendance", "Điểm danh"),
        ("forum",      "Thảo luận"),
        ("resource",   "Tài liệu"),
        ("choice",     "Khảo sát"),
    ]
    for key, label in _type_options:
        view._notify_type_checks[key] = ft.Checkbox(
            label=label,
            value=(key in _current_types),
            fill_color={ft.ControlState.SELECTED: C.ACCENT},
            check_color=C.BG,
            label_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        )
    view._notify_types_row = ft.Row(
        controls=list(view._notify_type_checks.values()),
        wrap=True,
        spacing=4,
        run_spacing=0,
    )

    # Các mốc thời gian nhắc nhở (milestones) trước deadline
    _current_milestones = getattr(settings, 'NOTIFY_MILESTONES', [72, 24, 3])
    view._milestone_chips = {}
    _milestone_options = [
        (168, "1 tuần"),
        (72, "3 ngày"),
        (24, "1 ngày"),
        (6, "6 giờ"),
        (3, "3 giờ"),
        (1, "1 giờ"),
    ]

    for hours, label in _milestone_options:
        view._milestone_chips[hours] = ft.Chip(
            label=ft.Text(label, size=12),
            selected=(hours in _current_milestones),
            show_checkmark=True,
            selected_color=C.ACCENT,
            bgcolor=C.SURFACE,
            on_select=view._handle_milestone_toggle(hours),
        )
    view._milestone_chips_row = ft.Row(
        controls=list(view._milestone_chips.values()),
        wrap=True,
        spacing=6,
        run_spacing=4,
    )
    _active_count = sum(1 for h in _current_milestones if h in view._milestone_chips)
    view._milestone_summary = ft.Text(
        f"Bạn sẽ nhận {_active_count} lần nhắc cho mỗi deadline" if _active_count else "Không có mốc nhắc nhở nào",
        size=12, color=C.TEXT_SECONDARY, italic=True
    )
    view._milestones_field = ft.TextField(
        value=", ".join(map(str, _current_milestones)),
        visible=False,
    )
    
    # Khu vực cấu hình môn học tắt thông báo (Muted Courses)
    view._muted_courses_list = ft.Column(spacing=2)
    view._muted_courses_drp = ft.ExpansionTile(
        title=ft.Text("Nhấn để mở danh sách chọn môn bỏ qua", size=13, color=C.TEXT_SECONDARY),
        controls=[
            ft.Container(
                content=view._muted_courses_list,
                padding=10,
                bgcolor=C.BG,
                border_radius=10,
                border=ft.Border.all(1, C.BORDER)
            )
        ],
        visible=False,
        collapsed_text_color=C.TEXT_PRIMARY,
        text_color=C.ACCENT
    )

    view._muted_courses_field = ft.TextField(
        value=", ".join(getattr(settings, 'NOTIFY_MUTED_COURSES', [])),
        label="Môn học tắt thông báo (cách nhau dấu phẩy)",
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        read_only=True,
        text_size=13,
        multiline=True,
        max_lines=3,
        visible=False 
    )

    # Nhắc nhở khẩn cấp phút cuối (X phút trước deadline)
    view._notify_min_field = ft.TextField( 
        value=str(settings.NOTIFY_MINUTES_BEFORE),
        label="Thông báo trước deadline (Phút)",
        keyboard_type=ft.KeyboardType.NUMBER,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, 
        bgcolor=C.BG, border_radius=10,
    )

def build_notification_section(view) -> ft.Container:
    """Xây dựng Container nhóm các thiết lập cấu hình thông báo cơ bản."""
    return view._build_setting_group(
        "Thông báo",
        "Chế độ và thời gian nhắc nhở",
        [
            view._make_themed_label("Chế độ thông báo"),
            view._profile_row,
            view._profile_summary,
            ft.Divider(height=10, color=C.BORDER),
            view._sw_dnd_enable,
            view._dnd_time_row,
            view._dnd_summary,
            ft.Divider(height=10, color=C.BORDER),
            view._sw_ignore_sub,
        ],
        icon=ft.Icons.NOTIFICATIONS_OUTLINED,
        default_open=True,
    )

def build_advanced_section(view) -> ft.Container:
    """Xây dựng Container nhóm các cấu hình nhắc nhở nâng cao và lọc môn học."""
    return view._build_setting_group(
        "Tùy chỉnh nâng cao",
        "Mốc nhắc, loại bài, tắt theo môn",
        [
            view._make_themed_label("Nhắc trước deadline"),
            view._milestone_chips_row,
            view._milestone_summary,
            view._milestones_field,
            ft.Divider(height=10, color=C.BORDER),
            view._make_themed_label("Nhắc phút cuối"),
            view._notify_min_field,
            view._build_hint("Gửi thêm 1 lần nhắc khi chỉ còn X phút. Đặt 0 để tắt."),
            ft.Divider(height=10, color=C.BORDER),
            view._make_themed_label("Loại hoạt động"),
            view._notify_types_row,
            ft.Divider(height=10, color=C.BORDER),
            view._make_themed_label("Tắt thông báo theo môn"),
            view._muted_courses_drp,
            view._muted_courses_field,
        ],
        icon=ft.Icons.TUNE_OUTLINED,
    )
