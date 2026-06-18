import flet as ft
import webbrowser
import threading
from gui.core.theme import C
from gui.core.utils import get_urgency_color, get_urgency_badge, clean_course_name, format_deadline, get_countdown, clean_html

_STATUS_TRANSLATIONS = {
    "No submissions have been made yet": "Chưa nộp bài",
    "No submission": "Chưa nộp",
    "Submitted for grading": "Đã nộp, chờ chấm",
    "Not graded": "Chưa chấm",
    "Graded": "Đã chấm",
    "No attempt": "Chưa thực hiện",
    "Finished": "Đã hoàn thành",
    "In progress": "Đang thực hiện",
    "Not yet open": "Chưa mở",
    "This submission is being graded": "Đang chấm bài",
}

def _translate_status(text: str) -> str:
    """Translate common Moodle English status strings to Vietnamese."""
    if not text:
        return text
    # Exact match first
    if text in _STATUS_TRANSLATIONS:
        return _STATUS_TRANSLATIONS[text]
    # Try case-insensitive
    text_lower = text.lower()
    for en, vi in _STATUS_TRANSLATIONS.items():
        if en.lower() == text_lower:
            return vi
    # Partial match for 'remaining' → 'còn lại'
    if 'remaining' in text_lower:
        return text.replace('remaining', 'còn lại').replace('Remaining', 'còn lại')
    return text

# Also translate status keys
_KEY_TRANSLATIONS = {
    "Submission status": "Trạng thái nộp bài",
    "Grading status": "Trạng thái chấm",
    "Time remaining": "Thời gian còn lại",
    "Last modified": "Sửa lần cuối",
    "Attempts allowed": "Số lần cho phép",
    "File submissions": "Bài nộp",
    "Grading method": "Phương pháp chấm",
    "Cut-off date": "Hạn cuối",
}

def _translate_key(key: str) -> str:
    return _KEY_TRANSLATIONS.get(key, key)


class DetailView(ft.Container):
    def __init__(self, page: ft.Page, on_close, get_client=None):
        super().__init__()
        self._page          = page
        self.visible        = False
        self.expand         = True
        self.bgcolor        = C.BG
        self._current_url   = ""
        self._current_data  = {}
        self._get_client    = get_client   # hàm lấy MoodleClient để truy xuất đường dẫn đăng nhập

        self._title_text    = ft.Text("", size=18, weight=ft.FontWeight.BOLD,
                                      color=C.TEXT_PRIMARY, max_lines=3)
        self._course_text   = ft.Text("", size=12, color=C.ACCENT)
        self._badge_ctrl    = ft.Container(visible=False)
        self._countdown_txt = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._deadline_txt  = ft.Text("", size=13, color=C.TEXT_PRIMARY)
        self._opentime_txt  = ft.Text("", size=13, color=C.TEXT_PRIMARY)
        self._loading_bar   = ft.ProgressBar(color=C.ACCENT, bgcolor=C.BORDER,
                                             visible=False)
        self._error_banner = ft.Container(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14, color=C.WARNING),
                ft.Text("Không thể tải chi tiết đầy đủ", size=11, color=C.WARNING),
            ], spacing=6),
            bgcolor=C.SURFACE,
            padding=ft.padding.symmetric(horizontal=12, vertical=6),
            border_radius=6,
            border=ft.border.all(1, C.WARNING),
            visible=False,
        )
        self._content_col   = ft.Column(spacing=12)

        self._open_btn = ft.Container(
            content=ft.Text("Xem trong trình duyệt", size=13, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600,
                            text_align=ft.TextAlign.CENTER),
            bgcolor=C.ACCENT,
            padding=ft.Padding.symmetric(vertical=13),
            border_radius=8,
            on_click=self._open_browser,
            ink=True,
            alignment=ft.Alignment(0, 0),
        )

        back_btn = ft.TextButton(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=16, color=C.TEXT_SECONDARY),
                ft.Text("Quay lại", size=14, color=C.TEXT_SECONDARY),
            ], spacing=4, tight=True),
            on_click=lambda _: on_close(),
            style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=10)),
        )

        self._opentime_row = ft.Row(controls=[
                    ft.Text("Mở từ", size=11, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                    self._opentime_txt,
                ], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        self._opentime_row.visible = False

        info_box = ft.Container(
            content=ft.Column(controls=[
                self._opentime_row,
                ft.Row(controls=[
                    ft.Text("Thời hạn", size=11, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                    self._deadline_txt,
                ], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Row(controls=[
                    ft.Text("Còn lại", size=11, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                    self._countdown_txt,
                ], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=10),
            bgcolor=C.SURFACE,
            padding=ft.padding.all(14),
            border_radius=8,
            border=ft.border.all(1, C.BORDER),
        )

        # UX: Compact open-in-browser button in header for quick access
        self._header_open_btn = ft.IconButton(
            ft.Icons.OPEN_IN_BROWSER_ROUNDED,
            icon_color=C.ACCENT, icon_size=20,
            tooltip="Mở trong trình duyệt",
            on_click=self._open_browser,
        )

        self.content = ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(controls=[back_btn, self._header_open_btn],
                                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=ft.Padding.only(left=8, right=8, top=16, bottom=8),
                ),
                self._loading_bar,
                self._error_banner,
                ft.Container(
                    content=ft.Column(
                        controls=[
                            ft.Container(
                                content=ft.Column(controls=[
                                    self._badge_ctrl,
                                    self._title_text,
                                    self._course_text,
                                    ft.Divider(height=16, color=C.BORDER),
                                    info_box,
                                ], spacing=8),
                                padding=ft.Padding.symmetric(horizontal=16),
                            ),
                            ft.Container(
                                content=self._content_col,
                                padding=ft.Padding.symmetric(horizontal=16),
                            ),
                            ft.Container(
                                content=self._open_btn,
                                padding=ft.Padding.only(
                                    left=16, right=16, top=8, bottom=20),
                            ),
                        ],
                        spacing=12,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
        )

    # ── Hàm công khai ──
    def show_loading(self, data: dict):
        self._title_text.value  = data.get("title", "Đang tải...")
        self._course_text.value = clean_course_name(data.get("course", ""))
        self._current_url       = data.get("url", "")
        self._current_data      = data
        self._content_col.controls.clear()
        self._loading_bar.visible = True

        badge_text, badge_color = get_urgency_badge(data.get("urgency", "safe"))
        self._badge_ctrl.content      = ft.Text(badge_text, size=10, color=badge_color,
                                                 weight=ft.FontWeight.BOLD)
        self._badge_ctrl.bgcolor      = None
        self._badge_ctrl.border       = ft.border.all(1, badge_color)
        self._badge_ctrl.padding      = ft.Padding.symmetric(horizontal=8, vertical=3)
        self._badge_ctrl.border_radius = 5
        self._badge_ctrl.visible      = True

        deadline_str = data.get("deadline", "")
        act_type = data.get("type", "")
        cd_text, overdue = get_countdown(deadline_str, act_type)
        self._countdown_txt.value = cd_text
        self._countdown_txt.color = C.CRITICAL if overdue else get_urgency_color(
            data.get("urgency", "safe"))
        
        details = data.get("details", {})
        open_time_str = details.get("open_time", "")
        if open_time_str:
            self._opentime_txt.value = format_deadline(open_time_str)
            self._opentime_row.visible = True
        else:
            self._opentime_row.visible = False
            
        self._deadline_txt.value = format_deadline(deadline_str)

        self.visible = True
        # Hàm gọi chịu trách nhiệm page.update() tránh việc gọi thừa

    def update_detail(self, data: dict):
        self._loading_bar.visible  = False
        self._error_banner.visible = False
        self._title_text.value     = data.get("title", "Không có tiêu đề")
        self._current_url          = data.get("url", "")
        self._current_data         = data
        self._content_col.controls.clear()

        details = data.get("details", {})

        open_time_str = details.get("open_time", "")
        if open_time_str:
            self._opentime_txt.value = format_deadline(open_time_str)
        else:
            self._opentime_txt.value = "Không giới hạn"
        self._opentime_row.visible = True

        # Trích xuất thông tin Tên môn học
        full_name = details.get("course_full_name", "")
        self._course_text.value = clean_course_name(full_name or data.get("course", ""))

        # ── Trạng thái nộp bài ──
        status_data = details.get("status_data", {})
        # Keys to skip: raw HTML fields, duplicates (time shown in info box), comment templates
        _SKIP_KEYS = {
            "Online text", "Submission comments", "Mở từ",
            "Đăng tải các bình luận.",          # Moodle comment template (raw HTML)
            "Time remaining", "Thời gian còn lại",  # Already shown in info box above
        }
        if status_data:
            rows = [
                ft.Row(controls=[
                    ft.Text(_translate_key(k), size=12, color=C.TEXT_SECONDARY, width=130),
                    ft.Text(_translate_status(str(v)), size=12, color=C.TEXT_PRIMARY, expand=True),
                ], spacing=8)
                for k, v in status_data.items()
                if k not in _SKIP_KEYS
                and v
                and str(v).strip() not in ("-", "")
                and "___" not in str(v)   # Strip leaked Moodle HTML templates
            ]
            if rows:
                self._content_col.controls.append(self._section("Trạng thái", rows))

        # ── Thông tin Quiz ──
        quiz_rows = []
        attempts   = details.get("attempts_allowed")
        time_limit = details.get("time_limit")
        if attempts:
            quiz_rows.append(ft.Row(controls=[
                ft.Text("Số lần làm:", size=12, color=C.TEXT_SECONDARY, width=130),
                ft.Text(attempts, size=12, color=C.TEXT_PRIMARY),
            ], spacing=8))
        if time_limit:
            quiz_rows.append(ft.Row(controls=[
                ft.Text("Thời gian:", size=12, color=C.TEXT_SECONDARY, width=130),
                ft.Text(time_limit, size=12, color=C.TEXT_PRIMARY),
            ], spacing=8))
        for info in details.get("quiz_info", []):
            if info and attempts not in info and time_limit not in info:
                quiz_rows.append(ft.Text(info, size=12, color=C.TEXT_SECONDARY))
        if quiz_rows:
            self._content_col.controls.append(self._section("Thông tin Quiz", quiz_rows))

        # ── Thông tin Điểm danh ──
        att_records = details.get("attendance_records", [])
        # Các cột phổ biến, dùng layout đặc biệt
        _ATT_SKIP = {"Description", "Status", "Points", "Ngày", "Ngay", "Date"}
        if att_records:
            att_rows = []
            for rec in att_records:
                status       = rec.get("Status", "")
                status_color = (C.SAFE     if any(x in status.lower() for x in ("present", "có mặt"))
                                else C.CRITICAL if any(x in status.lower() for x in ("absent", "vắng"))
                                else C.TEXT_SECONDARY)
                date_raw   = rec.get("Ngày", rec.get("Ngay", rec.get("Date", "")))
                date_parts = date_raw.split("\n")

                # Các cột phụ khác
                extra_rows = [
                    ft.Row(controls=[
                        ft.Text(k, size=11, color=C.TEXT_SECONDARY, width=110),
                        ft.Text(str(v), size=11, color=C.TEXT_PRIMARY, expand=True),
                    ], spacing=8)
                    for k, v in rec.items()
                    if k not in _ATT_SKIP and v
                ]

                att_rows.append(
                    ft.Container(
                        content=ft.Column(controls=[
                            ft.Row(controls=[
                                ft.Column(controls=[
                                    ft.Text(rec.get("Description", ""), size=12,
                                            weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                                    ft.Text(date_parts[0] if date_parts else "",
                                            size=11, color=C.TEXT_SECONDARY),
                                    *(
                                        [ft.Text(date_parts[1], size=10, color=C.TEXT_SECONDARY)]
                                        if len(date_parts) > 1 else []
                                    ),
                                ], spacing=2, expand=True),
                                ft.Column(controls=[
                                    ft.Text(status, size=12, color=status_color,
                                            weight=ft.FontWeight.BOLD),
                                    ft.Text(rec.get("Points", ""), size=11,
                                            color=C.TEXT_SECONDARY),
                                ], spacing=2,
                                   horizontal_alignment=ft.CrossAxisAlignment.END),
                            ]),
                            *extra_rows,
                        ], spacing=4),
                        bgcolor=C.SURFACE, border_radius=8,
                        padding=ft.padding.all(10),
                        border=ft.border.all(1, C.BORDER),
                    )
                )
            self._content_col.controls.append(self._section("Điểm danh", att_rows))

        # ── Mô tả ──
        desc = clean_html(details.get("description_html", ""))
        if desc:
            self._content_col.controls.append(self._section("Mô tả", [
                ft.Container(
                    content=ft.Text(desc, size=12, color=C.TEXT_SECONDARY),
                    bgcolor=C.SURFACE, border_radius=8,
                    padding=ft.padding.all(12),
                )
            ]))

        # Chỉ thay đổi giao diện, không tự gọi update

    def show_error_banner(self):
        self._error_banner.visible = True

    # ── Hàm nội bộ (Private) ──
    def _section(self, title: str, controls: list) -> ft.Container:
        return ft.Container(
            content=ft.Column(controls=[
                ft.Text(title.upper(), size=10, weight=ft.FontWeight.W_600,
                        color=C.TEXT_SECONDARY),
                ft.Container(
                    content=ft.Column(controls=controls, spacing=8),
                    bgcolor=C.SURFACE, border_radius=8,
                    padding=ft.padding.all(12),
                    border=ft.border.all(1, C.BORDER),
                ),
            ], spacing=6),
        )

    def _open_browser(self, e):
        if not self._current_url:
            return

        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass

        if client:
            # Sử dụng autologin URL thông qua Moodle login endpoint
            activity_url = self._current_url
            autologin_url = client.build_autologin_url(activity_url)
            webbrowser.open(autologin_url)
            return

        # Dự phòng mở thẳng khi không có cookie
        webbrowser.open(self._current_url)