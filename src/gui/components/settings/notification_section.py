import flet as ft
from gui.core.theme import C
from config import settings

def init_notification_controls(view):
    """Khởi tạo các control thiết lập cấu hình thông báo và chế độ Do Not Disturb (DND)."""
    # Định nghĩa cấu hình có sẵn của các Notification Profile
    _PROFILES = {
        "quiet": {"icon": ft.Icons.NOTIFICATIONS_OFF_OUTLINED, "label": "Yên tĩnh", "desc": "Chỉ deadline gấp", "milestones": [1440, 60], "dnd": True, "dnd_start": 22, "dnd_end": 8},
        "balanced": {"icon": ft.Icons.NOTIFICATIONS_OUTLINED, "label": "Cân bằng", "desc": "Mặc định", "milestones": [4320, 1440, 180, 60, 30, 5], "dnd": True, "dnd_start": 22, "dnd_end": 7},
        "exam_week": {"icon": ft.Icons.LOCAL_FIRE_DEPARTMENT_OUTLINED, "label": "Theo sát", "desc": "Không bỏ lỡ gì", "milestones": [4320, 1440, 360, 180, 60, 30, 5], "dnd": False, "dnd_start": 22, "dnd_end": 7},
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
            bgcolor=(
                ft.Colors.with_opacity(0.08, C.ACCENT)
                if is_sel
                else C.SURFACE
            ),
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
        on_change=lambda e: (view._update_dnd_summary(), getattr(view, "_on_form_field_change", lambda: None)())
    )
    # Khung nhập giờ bắt đầu DND
    view._dnd_start_field = ft.TextField(
        value=str(getattr(settings, 'NOTIFY_DND_START', 22)),
        label="Từ (giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        width=140, text_align=ft.TextAlign.CENTER,
        prefix_icon=ft.Icons.DARK_MODE_OUTLINED,
        on_change=lambda e: (view._update_dnd_summary(), getattr(view, "_on_form_field_change", lambda: None)())
    )
    # Khung nhập giờ kết thúc DND
    view._dnd_end_field = ft.TextField(
        value=str(getattr(settings, 'NOTIFY_DND_END', 7)),
        label="Đến (giờ)",
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        width=140, text_align=ft.TextAlign.CENTER,
        prefix_icon=ft.Icons.LIGHT_MODE_OUTLINED,
        on_change=lambda e: (view._update_dnd_summary(), getattr(view, "_on_form_field_change", lambda: None)())
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
        visible=bool(getattr(settings, 'NOTIFY_DND_ENABLE', False)),
    )

    # Switch tắt thông báo đối với các hoạt động đã hoàn tất nộp bài
    view._sw_ignore_sub = ft.Switch(
        value=getattr(settings, 'NOTIFY_IGNORE_SUBMITTED', True), active_color=C.ACCENT,
        label_text_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
        label="Bỏ qua bài đã nộp",
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)()
    )

    # Danh sách các loại hoạt động Moodle được nhắc thông báo (2-column layout)
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
            check_color=C.ON_ACCENT,
            label_style=ft.TextStyle(color=C.TEXT_PRIMARY, size=13),
            on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)()
        )

    # 2-column arrangement
    col1_checks = [view._notify_type_checks[k] for k in ("quiz", "attendance", "resource") if k in view._notify_type_checks]
    col2_checks = [view._notify_type_checks[k] for k in ("assignment", "forum", "choice") if k in view._notify_type_checks]
    view._notify_types_row = ft.Row(
        controls=[
            ft.Column(controls=col1_checks, spacing=6, expand=True),
            ft.Column(controls=col2_checks, spacing=6, expand=True),
        ],
        spacing=16,
    )

    # Các mốc thời gian nhắc nhở (milestones) trước deadline
    _current_milestones = getattr(settings, 'NOTIFY_MILESTONES_MINUTES', [4320, 1440, 180, 60, 30, 5])
    view._milestone_chips = {}
    _milestone_options = [
        (10080, "1 tuần"),
        (4320, "3 ngày"),
        (1440, "1 ngày"),
        (360, "6 giờ"),
        (180, "3 giờ"),
        (60, "1 giờ"),
        (30, "30 phút"),
        (5, "5 phút"),
    ]

    for minutes, label in _milestone_options:
        is_sel = minutes in _current_milestones
        view._milestone_chips[minutes] = ft.Chip(
            label=ft.Text(label, size=12, color=C.ON_ACCENT if is_sel else C.TEXT_PRIMARY),
            selected=is_sel,
            show_checkmark=True,
            check_color=C.ON_ACCENT,
            selected_color=C.ACCENT,
            bgcolor=C.SURFACE,
            on_select=view._handle_milestone_toggle(minutes),
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
    
    # Khu vực cấu hình môn học tắt thông báo (Bỏ qua môn học)
    view._muted_courses_list = ft.Column(spacing=2)
    view._muted_count_text = ft.Text("0 môn", size=12, color=C.TEXT_SECONDARY)
    view._muted_courses_drp = ft.ExpansionTile(
        title=ft.Row(
            controls=[
                ft.Text("Bỏ qua môn học", size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                view._muted_count_text,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
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
        label="Môn học bỏ qua (cách nhau dấu phẩy)",
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, bgcolor=C.BG, border_radius=10,
        read_only=True,
        text_size=13,
        multiline=True,
        max_lines=3,
        visible=False,
        on_change=lambda e: getattr(view, "_on_form_field_change", lambda: None)()
    )

    # Nhắc nhở khẩn cấp phút cuối (X phút trước deadline)
    view._notify_min_field = ft.TextField( 
        value="0",
        label="Thông báo trước deadline (Phút)",
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY, 
        bgcolor=C.BG, border_radius=10, visible=False,
    )

def build_notification_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết cấu hình toàn diện hệ thống thông báo."""
    # Ensure initial visibility of DND time row reflects switch state
    view._dnd_time_row.visible = bool(getattr(view._sw_dnd_enable, "value", False))

    # Channels layout with Title + Description + Switch
    gmail_row = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("Gmail", size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                                ft.Text("Gửi thông báo qua email", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        view._sw_email,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                view._gmail_addr_field,
                view._gmail_pw_field,
            ],
            spacing=6,
        ),
        padding=ft.Padding.symmetric(vertical=4),
    )

    discord_row = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("Discord", size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                                ft.Text("Gửi qua Discord", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        view._sw_discord,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                view._discord_wh_field,
            ],
            spacing=6,
        ),
        padding=ft.Padding.symmetric(vertical=4),
    )

    telegram_row = ft.Container(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text("Telegram", size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                                ft.Text("Gửi qua Telegram", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=1,
                            expand=True,
                        ),
                        view._sw_telegram,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                view._tel_token_field,
                view._tel_chat_field,
            ],
            spacing=6,
        ),
        padding=ft.Padding.symmetric(vertical=4),
    )

    return ft.Column(
        controls=[
            # 1. Chế độ thông báo
            view._make_themed_label("Chế độ thông báo"),
            view._profile_row,
            view._profile_summary,
            ft.Divider(height=16, color=C.BORDER),

            # 2. Không làm phiền (DND)
            view._make_themed_label("Không làm phiền"),
            view._sw_dnd_enable,
            view._dnd_time_row,
            view._dnd_summary,
            ft.Divider(height=16, color=C.BORDER),

            # 3. Mốc nhắc deadline
            view._make_themed_label("Nhắc deadline"),
            view._milestone_chips_row,
            view._milestone_summary,
            view._milestones_field,
            ft.Container(height=4),
            view._sw_ignore_sub,
            ft.Divider(height=16, color=C.BORDER),

            # 4. Loại hoạt động
            view._make_themed_label("Loại hoạt động"),
            view._notify_types_row,
            ft.Divider(height=16, color=C.BORDER),

            # 5. Kênh gửi thông báo
            view._make_themed_label("Kênh gửi"),
            gmail_row,
            discord_row,
            telegram_row,
            ft.Divider(height=16, color=C.BORDER),

            # 6. Bỏ qua môn học
            view._make_themed_label("Bỏ qua môn học"),
            view._build_hint("Chọn các môn bạn không muốn nhận thông báo."),
            view._muted_courses_drp,
            view._muted_courses_field,
        ],
        spacing=10,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )


def build_advanced_section(view) -> ft.Control:
    """Tương thích ngược: trỏ về build_notification_section."""
    return build_notification_section(view)
