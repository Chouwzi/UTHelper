import flet as ft
import threading
import base64
import asyncio
import logging
from gui.core.theme import C
from gui.core.utils import get_urgency_color, get_countdown_color, get_urgency_badge, clean_course_name, format_deadline, get_countdown, clean_html

logger = logging.getLogger(__name__)

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

# Moodle license shortnames → display names
_LICENSE_OPTIONS = [
    ("unknown", "Licence not specified"),
    ("allrightsreserved", "All rights reserved"),
    ("public", "Public domain"),
    ("cc-4.0", "Creative Commons - 4.0 International"),
    ("cc-nc-4.0", "CC - NonCommercial 4.0"),
    ("cc-nd-4.0", "CC - NoDerivatives 4.0"),
    ("cc-nc-nd-4.0", "CC - NonCommercial-NoDerivatives 4.0"),
    ("cc-nc-sa-4.0", "CC - NonCommercial-ShareAlike 4.0"),
    ("cc-sa-4.0", "CC - ShareAlike 4.0"),
]

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
        self._selected_files = []          # list of FilePickerFile objects
        self._is_uploading  = False

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
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border_radius=6,
            border=ft.Border.all(1, C.WARNING),
            visible=False,
        )
        self._content_col   = ft.Column(spacing=12)

        # ── FilePicker for in-app submission ──
        # FilePicker is a Service in Flet 0.82+ (not a Control)
        # Do NOT add to page.overlay — just instantiate and call methods

        # ── File preview area (files to upload) ──
        self._file_list_col = ft.Column(spacing=4, visible=False)
        self._upload_progress = ft.ProgressBar(color=C.SAFE, bgcolor=C.BORDER, visible=False)
        self._upload_status = ft.Text("", size=12, color=C.TEXT_SECONDARY, visible=False)

        # ── Submitted files area (files already on server) ──
        self._submitted_files: list = []  # [{name, size, url, timemodified}]
        self._editing_file_index: int = -1  # index of file being edited

        # ── File edit dialog fields ──
        self._edit_filename = ft.TextField(
            label="Tên", text_size=13, dense=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, label_style=ft.TextStyle(color=C.TEXT_SECONDARY),
            bgcolor=C.BG,
        )
        self._edit_author = ft.TextField(
            label="Tác giả", text_size=13, dense=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, label_style=ft.TextStyle(color=C.TEXT_SECONDARY),
            bgcolor=C.BG,
        )
        self._edit_license = ft.Dropdown(
            label="Chọn giấy phép", text_size=13, dense=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, label_style=ft.TextStyle(color=C.TEXT_SECONDARY),
            bgcolor=C.BG, filled=True, fill_color=C.BG,
            options=[ft.dropdown.Option(key=k, text=v) for k, v in _LICENSE_OPTIONS],
        )
        self._edit_filepath = ft.TextField(
            label="Đường dẫn", text_size=13, dense=True,
            border_color=C.BORDER, focused_border_color=C.ACCENT,
            color=C.TEXT_PRIMARY, label_style=ft.TextStyle(color=C.TEXT_SECONDARY),
            bgcolor=C.BG, value="/",
        )
        self._edit_status = ft.Text("", size=12, visible=False)
        self._file_edit_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Chỉnh sửa file", size=16, weight=ft.FontWeight.BOLD,
                          color=C.TEXT_PRIMARY),
            bgcolor=C.SURFACE,
            content=ft.Container(
                content=ft.Column(controls=[
                    self._edit_filename,
                    self._edit_author,
                    self._edit_license,
                    self._edit_filepath,
                    self._edit_status,
                ], spacing=12, tight=True),
                width=340,
            ),
            actions=[
                ft.TextButton("Hủy", on_click=self._close_edit_dialog,
                              style=ft.ButtonStyle(color=C.TEXT_SECONDARY)),
                ft.ElevatedButton(
                    "Cập nhật",
                    icon=ft.Icons.SAVE_ROUNDED,
                    on_click=lambda _: asyncio.ensure_future(self._on_update_file_metadata()),
                    bgcolor=C.ACCENT, color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._submitted_files_col = ft.Column(spacing=4, visible=False)
        self._edit_submitted_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.EDIT_ROUNDED, size=14, color=C.WARNING),
                    ft.Text("Chỉnh sửa bài nộp", size=12, color=C.WARNING,
                            weight=ft.FontWeight.W_500),
                ],
                spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.WARNING),
            padding=ft.Padding.symmetric(vertical=8, horizontal=12),
            border_radius=6,
            on_click=self._on_edit_submitted,
            ink=True,
            visible=False,
        )
        self._submitted_area = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Icon(ft.Icons.FOLDER_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text("File đã nộp:", size=13, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                ], spacing=6),
                self._submitted_files_col,
                self._edit_submitted_btn,
            ], spacing=8),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.SAFE + "40"),
            border_radius=8,
            padding=ft.Padding.all(14),
            visible=False,
        )

        # ── Pick file button ──
        self._pick_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE_ROUNDED, size=16, color=C.ACCENT),
                    ft.Text("Chọn file", size=13, color=C.ACCENT, weight=ft.FontWeight.W_600),
                ],
                spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.ACCENT),
            padding=ft.Padding.symmetric(vertical=10, horizontal=16),
            border_radius=8,
            on_click=self._on_pick_files,
            ink=True,
            visible=False,
        )

        # ── Submit button (for in-app submission) ──
        self._submit_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CLOUD_UPLOAD_ROUNDED, size=16, color=ft.Colors.WHITE),
                    ft.Text("Nộp bài", size=13, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
                ],
                spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SAFE,
            padding=ft.Padding.symmetric(vertical=13),
            border_radius=8,
            on_click=self._on_submit,
            ink=True,
            visible=False,
            expand=True,
        )

        # ── Submission area container ──
        self._submission_area = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text("Chọn file để nộp:", size=13, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                ], spacing=6),
                self._file_list_col,
                self._upload_progress,
                self._upload_status,
                ft.Row(controls=[
                    self._pick_btn,
                    self._submit_btn,
                ], spacing=8),
            ], spacing=10),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
            padding=ft.Padding.all(14),
            visible=False,
        )

        # UX-8: Dynamic CTA — changes based on submission status
        self._cta_icon = ft.Icon(ft.Icons.OPEN_IN_BROWSER_ROUNDED, size=16, color=ft.Colors.WHITE)
        self._cta_text = ft.Text("Xem trong trình duyệt", size=13, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600)
        self._open_btn = ft.Container(
            content=ft.Row(
                controls=[self._cta_icon, self._cta_text],
                spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
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
            padding=ft.Padding.all(14),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
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
                                content=self._submitted_area,
                                padding=ft.Padding.symmetric(horizontal=16),
                            ),
                            ft.Container(
                                content=self._submission_area,
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
        self._badge_ctrl.border       = ft.Border.all(1, badge_color)
        self._badge_ctrl.padding      = ft.Padding.symmetric(horizontal=8, vertical=3)
        self._badge_ctrl.border_radius = 5
        self._badge_ctrl.visible      = True

        deadline_str = data.get("deadline", "")
        act_type = data.get("type", "")
        cd_text, overdue = get_countdown(deadline_str, act_type)
        self._countdown_txt.value = cd_text
        # UX-7: Time-based countdown color consistent with cards
        self._countdown_txt.color = C.CRITICAL if overdue else get_countdown_color(deadline_str)
        
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

        # UX-8: Dynamic CTA based on submission status and type
        sub_status = data.get("submission_status", "unknown")
        act_type = data.get("type", "other")
        is_assignment = act_type in ("assignment", "assign")

        # Reset submission UI
        self._selected_files.clear()
        self._file_list_col.controls.clear()
        self._file_list_col.visible = False
        self._upload_progress.visible = False
        self._upload_status.visible = False
        self._is_uploading = False
        self._submitted_files.clear()
        self._submitted_files_col.controls.clear()
        self._submitted_area.visible = False

        if is_assignment and sub_status not in ("submitted", "graded", "Đã nộp", "Đã chấm điểm"):
            # Chưa nộp + là assignment → hiện submission area
            self._submission_area.visible = True
            self._pick_btn.visible = True
            self._submit_btn.visible = False  # chỉ hiện khi đã chọn file
            # CTA chính → mở browser (fallback)
            self._cta_icon.name = ft.Icons.OPEN_IN_BROWSER_ROUNDED
            self._cta_text.value = "Mở trong trình duyệt"
            self._open_btn.bgcolor = C.SURFACE
            self._open_btn.border = ft.Border.all(1, C.ACCENT)
            self._cta_text.color = C.ACCENT
            self._cta_icon.color = C.ACCENT
        elif sub_status in ("submitted", "graded", "Đã nộp", "Đã chấm điểm"):
            self._submission_area.visible = False
            self._cta_icon.name = ft.Icons.VISIBILITY_ROUNDED
            self._cta_text.value = "Xem bài nộp"
            self._open_btn.bgcolor = C.SURFACE
            self._open_btn.border = ft.Border.all(1, C.ACCENT)
            self._cta_text.color = C.ACCENT
            self._cta_icon.color = C.ACCENT

        # Load submitted files in background (for assignments)
        if is_assignment:
            url = data.get("url", "")
            course_id = data.get("course_id")
            if url and course_id and '/mod/assign/' in url:
                client = None
                try:
                    client = self._get_client() if self._get_client else None
                except Exception:
                    pass
                if client:
                    asyncio.ensure_future(
                        self._async_load_submitted_files(client, url, int(course_id))
                    )
        else:
            self._submission_area.visible = False
            self._cta_icon.name = ft.Icons.OPEN_IN_BROWSER_ROUNDED
            self._cta_text.value = "Mở trong trình duyệt"
            self._open_btn.bgcolor = C.ACCENT
            self._open_btn.border = None
            self._cta_text.color = ft.Colors.WHITE
            self._cta_icon.color = ft.Colors.WHITE

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
                        padding=ft.Padding.all(10),
                        border=ft.Border.all(1, C.BORDER),
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
                    padding=ft.Padding.all(12),
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
                    padding=ft.Padding.all(12),
                    border=ft.Border.all(1, C.BORDER),
                ),
            ], spacing=6),
        )

    async def _open_browser(self, e):
        if not self._current_url:
            return

        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass

        url_to_open = self._current_url
        if client:
            # Sử dụng autologin URL thông qua Moodle login endpoint
            url_to_open = client.build_autologin_url(self._current_url)

        await ft.UrlLauncher().launch_url(url_to_open)

    # ── In-App Submission ──────────────────────────────────────────────

    async def _on_pick_files(self, e):
        """Mở file picker để chọn file nộp bài."""
        if self._is_uploading:
            return
        try:
            files = await ft.FilePicker().pick_files(
                dialog_title="Chọn file nộp bài",
                allow_multiple=True,
                with_data=True,  # đọc bytes luôn cho mobile compatibility
            )
            if files:
                # Append mode: thêm vào danh sách hiện tại
                existing_names = {f.name for f in self._selected_files}
                for f in files:
                    if f.name not in existing_names:
                        self._selected_files.append(f)
                        existing_names.add(f.name)
                self._update_file_preview()
        except Exception as ex:
            logger.error("FilePicker error: %s", ex)

    def _update_file_preview(self):
        """Cập nhật danh sách file đã chọn trong UI."""
        self._file_list_col.controls.clear()
        for i, f in enumerate(self._selected_files):
            size_kb = (f.size or 0) / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            row = ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, size=16, color=C.ACCENT),
                        ft.Text(f.name, size=12, color=C.TEXT_PRIMARY, expand=True,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(size_str, size=11, color=C.TEXT_SECONDARY),
                        ft.IconButton(
                            ft.Icons.CLOSE_ROUNDED, icon_size=14,
                            icon_color=C.TEXT_SECONDARY,
                            on_click=lambda _, idx=i: self._remove_file(idx),
                            style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                        ),
                    ],
                    spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.BG,
                border=ft.Border.all(1, C.BORDER),
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            )
            self._file_list_col.controls.append(row)

        has_files = len(self._selected_files) > 0
        self._file_list_col.visible = has_files
        self._submit_btn.visible = has_files
        self._upload_status.visible = False
        self._upload_progress.visible = False
        self._page.update()

    def _remove_file(self, index: int):
        """Xóa file khỏi danh sách đã chọn."""
        if 0 <= index < len(self._selected_files):
            self._selected_files.pop(index)
            self._update_file_preview()

    async def _on_submit(self, e):
        """Upload files và nộp bài qua Moodle WS API."""
        if self._is_uploading or not self._selected_files:
            return

        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass

        if not client:
            self._show_upload_status("Chưa đăng nhập. Vui lòng đăng nhập lại.", C.CRITICAL)
            return

        # Resolve assign_id từ URL
        data = self._current_data
        url = data.get("url", "")
        course_id = data.get("course_id")

        if not url or not course_id or '/mod/assign/' not in url:
            self._show_upload_status("Không thể xác định bài tập. Thử mở trong trình duyệt.", C.CRITICAL)
            return

        self._is_uploading = True
        self._upload_progress.visible = True
        self._upload_progress.value = None  # indeterminate
        self._pick_btn.visible = False
        self._submit_btn.visible = False
        self._show_upload_status("Đang tải lên...", C.TEXT_SECONDARY)

        try:
            success = await asyncio.to_thread(
                self._do_submit_sync, client, url, int(course_id)
            )
            if success:
                self._show_upload_status("Nộp bài thành công!", C.SAFE)
                self._upload_progress.value = 1.0
                self._selected_files.clear()
                self._file_list_col.controls.clear()
                self._file_list_col.visible = False
                self._submit_btn.visible = False
                self._pick_btn.visible = True
                # Reload submitted files to show what's on server
                asyncio.ensure_future(
                    self._async_load_submitted_files(client, url, int(course_id))
                )
            else:
                self._show_upload_status("Nộp bài thất bại. Thử lại hoặc mở trình duyệt.", C.CRITICAL)
                self._pick_btn.visible = True
                self._submit_btn.visible = True
                self._upload_progress.visible = False
        except Exception as ex:
            logger.error("Submit error: %s", ex)
            self._show_upload_status(f"Lỗi: {ex}", C.CRITICAL)
            self._pick_btn.visible = True
            self._submit_btn.visible = True
            self._upload_progress.visible = False
        finally:
            self._is_uploading = False
            self._page.update()

    def _do_submit_sync(self, client, url: str, course_id: int) -> bool:
        """Thực hiện upload + submit đồng bộ (chạy trong thread)."""
        from core.ws_functions import (
            resolve_cmid_to_assign_id,
            save_assignment_submission,
        )

        # 1. Extract cmid từ URL
        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        if not cmid:
            logger.error("Không extract được cmid từ URL: %s", url)
            return False

        # 2. Resolve cmid → assign_id
        assign_id = resolve_cmid_to_assign_id(client.call_ws_api, cmid, course_id)
        if not assign_id:
            logger.error("Không resolve được assign_id từ cmid=%d, course=%d", cmid, course_id)
            return False

        # 3. Upload từng file qua /webservice/upload.php
        draft_itemid = 0
        for f in self._selected_files:
            file_bytes = f.bytes
            if not file_bytes:
                logger.warning("File '%s' không có dữ liệu, bỏ qua.", f.name)
                continue
            result_id = client.upload_draft_file(
                f.name, file_bytes, itemid=draft_itemid
            )
            if result_id is None:
                logger.error("Upload thất bại cho file '%s'", f.name)
                return False
            draft_itemid = result_id  # dùng lại cho file tiếp theo

        if draft_itemid == 0:
            logger.error("Không có file nào được upload thành công")
            return False

        # 4. Save submission
        return save_assignment_submission(client.call_ws_api, assign_id, draft_itemid)

    def _show_upload_status(self, text: str, color: str):
        """Hiện thông báo trạng thái upload."""
        self._upload_status.value = text
        self._upload_status.color = color
        self._upload_status.visible = True

    async def _async_load_submitted_files(self, client, url: str, course_id: int):
        """Async wrapper: load submitted files in bg thread, then update UI."""
        try:
            await asyncio.to_thread(
                self._load_submitted_files, client, url, course_id
            )
            self._build_submitted_files_ui()
            self._page.update()
        except Exception as ex:
            logger.debug("Load submitted files error: %s", ex)

    def _load_submitted_files(self, client, url: str, course_id: int):
        """Load danh sách file đã nộp từ server (chạy trong thread)."""
        from core.ws_functions import resolve_cmid_to_assign_id, get_submitted_files

        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        if not cmid:
            return

        assign_id = resolve_cmid_to_assign_id(client.call_ws_api, cmid, course_id)
        if not assign_id:
            return

        self._submitted_files = get_submitted_files(client.call_ws_api, assign_id)

    def _build_submitted_files_ui(self):
        """Xây dựng UI hiển thị file đã nộp trên server."""
        from datetime import datetime
        self._submitted_files_col.controls.clear()

        if not self._submitted_files:
            self._submitted_area.visible = False
            return

        for i, f in enumerate(self._submitted_files):
            size_b = f.get('size', 0)
            size_kb = size_b / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"

            # Format dates
            tmod = f.get('timemodified', 0)
            tcreated = f.get('timecreated', 0)
            mod_str = datetime.fromtimestamp(tmod).strftime('%d/%m/%Y %H:%M') if tmod else '—'
            created_str = datetime.fromtimestamp(tcreated).strftime('%d/%m/%Y %H:%M') if tcreated else '—'

            # Metadata lines
            meta_col = ft.Column(controls=[
                ft.Row([
                    ft.Text("Lần sửa đổi cuối", size=10, color=C.TEXT_SECONDARY, width=120),
                    ft.Text(mod_str, size=10, color=C.TEXT_PRIMARY),
                ], spacing=4),
                ft.Row([
                    ft.Text("Ngày tạo", size=10, color=C.TEXT_SECONDARY, width=120),
                    ft.Text(created_str, size=10, color=C.TEXT_PRIMARY),
                ], spacing=4),
                ft.Row([
                    ft.Text("Kích thước", size=10, color=C.TEXT_SECONDARY, width=120),
                    ft.Text(size_str, size=10, color=C.TEXT_PRIMARY),
                ], spacing=4),
            ], spacing=2)

            row = ft.Container(
                content=ft.Column(controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, size=16, color=C.SAFE),
                            ft.Text(f.get('name', ''), size=12, color=C.TEXT_PRIMARY,
                                    expand=True, max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    weight=ft.FontWeight.W_500),
                            ft.IconButton(
                                ft.Icons.EDIT_ROUNDED, icon_size=14,
                                icon_color=C.ACCENT,
                                tooltip="Chỉnh sửa",
                                on_click=lambda _, idx=i: self._show_file_edit_dialog(idx),
                                style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                            ),
                            ft.IconButton(
                                ft.Icons.CLOSE_ROUNDED, icon_size=14,
                                icon_color=C.CRITICAL,
                                tooltip="Xóa file này",
                                on_click=lambda _, idx=i: asyncio.ensure_future(
                                    self._on_remove_submitted_file(idx)
                                ),
                                style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                            ),
                        ],
                        spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=meta_col,
                        padding=ft.Padding.only(left=28),  # align with text after icon
                    ),
                ], spacing=4),
                bgcolor=C.BG,
                border=ft.Border.all(1, C.SAFE + "30"),
                border_radius=6,
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                on_click=lambda _, idx=i: self._show_file_edit_dialog(idx),
                ink=True,
            )
            self._submitted_files_col.controls.append(row)

        self._submitted_files_col.visible = True
        self._submitted_area.visible = True
        self._edit_submitted_btn.visible = True

    async def _on_remove_submitted_file(self, index: int):
        """Xóa file đã nộp: re-upload các file còn lại rồi re-submit.
        
        Workflow:
        1. Download tất cả file CÒN LẠI từ server
        2. Upload chúng vào draft area mới
        3. mod_assign_save_submission để re-submit
        """
        if self._is_uploading:
            return
        if index < 0 or index >= len(self._submitted_files):
            return
        
        file_to_remove = self._submitted_files[index]
        files_to_keep = [f for i, f in enumerate(self._submitted_files) if i != index]
        
        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass
        if not client:
            self._show_upload_status("Chưa đăng nhập.", C.CRITICAL)
            return

        data = self._current_data
        url = data.get("url", "")
        course_id = data.get("course_id")
        if not url or not course_id or '/mod/assign/' not in url:
            self._show_upload_status("Không xác định được bài tập.", C.CRITICAL)
            return

        self._is_uploading = True
        self._show_upload_status(
            f"Đang xóa '{file_to_remove.get('name', '')}'...", C.TEXT_SECONDARY
        )
        self._page.update()

        try:
            success = await asyncio.to_thread(
                self._do_remove_file_sync, client, url, int(course_id), files_to_keep
            )
            if success:
                self._show_upload_status(
                    f"Đã xóa '{file_to_remove.get('name', '')}'", C.SAFE
                )
                # Reload submitted files
                asyncio.ensure_future(
                    self._async_load_submitted_files(client, url, int(course_id))
                )
            else:
                self._show_upload_status("Xóa thất bại. Thử mở trình duyệt.", C.CRITICAL)
        except Exception as ex:
            logger.error("Remove submitted file error: %s", ex)
            self._show_upload_status(f"Lỗi: {ex}", C.CRITICAL)
        finally:
            self._is_uploading = False
            self._page.update()

    def _do_remove_file_sync(self, client, url: str, course_id: int,
                             files_to_keep: list) -> bool:
        """Re-upload các file cần giữ rồi re-submit (chạy trong thread)."""
        from core.ws_functions import resolve_cmid_to_assign_id, save_assignment_submission

        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        if not cmid:
            return False

        assign_id = resolve_cmid_to_assign_id(client.call_ws_api, cmid, course_id)
        if not assign_id:
            return False

        if not files_to_keep:
            # Xóa hết file → submit draft area rỗng
            draft_itemid = 0
        else:
            # Download và re-upload các file cần giữ
            draft_itemid = 0
            for f in files_to_keep:
                file_url = f.get('url', '')
                if not file_url:
                    logger.error("File '%s' không có URL", f.get('name'))
                    return False
                
                file_bytes = client.download_file(file_url)
                if not file_bytes:
                    logger.error("Không tải được file '%s'", f.get('name'))
                    return False
                
                result_id = client.upload_draft_file(
                    f.get('name', 'file'), file_bytes, itemid=draft_itemid
                )
                if result_id is None:
                    logger.error("Re-upload thất bại cho file '%s'", f.get('name'))
                    return False
                draft_itemid = result_id

        return save_assignment_submission(client.call_ws_api, assign_id, draft_itemid)

    async def _on_edit_submitted(self, e):
        """Mở trình duyệt để chỉnh sửa bài nộp."""
        if not self._current_url:
            return
        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass
        url = self._current_url
        if client:
            url = client.build_autologin_url(self._current_url)
        await ft.UrlLauncher().launch_url(url)

    # ── File Edit Dialog ──────────────────────────────────────

    def _show_file_edit_dialog(self, index: int):
        """Mở popup chỉnh sửa thông tin file đã nộp."""
        if index < 0 or index >= len(self._submitted_files):
            return
        self._editing_file_index = index
        f = self._submitted_files[index]

        self._edit_filename.value = f.get('name', '')
        self._edit_filepath.value = f.get('filepath', '/')

        # Lấy tên tác giả mặc định từ user profile
        default_author = ''
        try:
            client = self._get_client() if self._get_client else None
            if client:
                site_info = client.call_ws_api('core_webservice_get_site_info')
                if site_info and isinstance(site_info, dict):
                    default_author = site_info.get('fullname', '')
        except Exception:
            pass
        self._edit_author.value = default_author
        self._edit_license.value = 'unknown'
        self._edit_status.value = ""
        self._edit_status.visible = False

        dlg = self._file_edit_dialog
        if dlg not in self._page.overlay:
            self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _close_edit_dialog(self, e=None):
        """Đóng popup chỉnh sửa file."""
        self._file_edit_dialog.open = False
        self._page.update()

    async def _on_update_file_metadata(self):
        """Xử lý cập nhật thông tin file: đổi tên, author, license, filepath."""
        idx = self._editing_file_index
        if idx < 0 or idx >= len(self._submitted_files):
            return
        if self._is_uploading:
            return

        old_file = self._submitted_files[idx]
        new_name = (self._edit_filename.value or '').strip()
        new_author = (self._edit_author.value or '').strip()
        new_license = self._edit_license.value or 'unknown'
        new_filepath = (self._edit_filepath.value or '/').strip()

        if not new_name:
            self._edit_status.value = "Tên file không được trống"
            self._edit_status.color = C.WARNING
            self._edit_status.visible = True
            self._page.update()
            return

        # Check if anything changed (author/license luôn cập nhật vì API không trả chúng)
        name_changed = new_name != old_file.get('name', '')

        if not name_changed and not new_author:
            self._close_edit_dialog()
            return

        client = None
        try:
            client = self._get_client() if self._get_client else None
        except Exception:
            pass
        if not client:
            self._edit_status.value = "Chưa đăng nhập"
            self._edit_status.color = C.CRITICAL
            self._edit_status.visible = True
            self._page.update()
            return

        data = self._current_data
        url = data.get("url", "")
        course_id = data.get("course_id")
        if not url or not course_id or '/mod/assign/' not in url:
            self._edit_status.value = "Không xác định được bài tập"
            self._edit_status.color = C.CRITICAL
            self._edit_status.visible = True
            self._page.update()
            return

        self._is_uploading = True
        self._edit_status.value = "Đang cập nhật..."
        self._edit_status.color = C.TEXT_SECONDARY
        self._edit_status.visible = True
        self._page.update()

        meta = {
            'new_name': new_name,
            'author': new_author,
            'license': new_license,
            'filepath': new_filepath,
        }

        try:
            success = await asyncio.to_thread(
                self._do_update_metadata_sync,
                client, url, int(course_id), idx, meta,
            )
            if success:
                self._close_edit_dialog()
                self._show_upload_status(
                    f"Đã cập nhật '{new_name}'", C.SAFE
                )
                asyncio.ensure_future(
                    self._async_load_submitted_files(client, url, int(course_id))
                )
            else:
                self._edit_status.value = "Cập nhật thất bại"
                self._edit_status.color = C.CRITICAL
                self._edit_status.visible = True
        except Exception as ex:
            logger.error("Update metadata error: %s", ex)
            self._edit_status.value = f"Lỗi: {ex}"
            self._edit_status.color = C.CRITICAL
            self._edit_status.visible = True
        finally:
            self._is_uploading = False
            self._page.update()

    def _do_update_metadata_sync(self, client, url: str, course_id: int,
                                 target_idx: int, meta: dict) -> bool:
        """Re-upload tất cả file với metadata mới cho file target (chạy trong thread).

        Workflow:
        1. Download tất cả submitted files
        2. Upload lại vào draft area mới, file target dùng tên/metadata mới
        3. mod_assign_save_submission
        """
        from core.ws_functions import resolve_cmid_to_assign_id, save_assignment_submission

        cmid = None
        if "id=" in url:
            try:
                cmid = int(url.split("id=")[-1].split("&")[0])
            except (ValueError, IndexError):
                pass
        if not cmid:
            return False

        assign_id = resolve_cmid_to_assign_id(client.call_ws_api, cmid, course_id)
        if not assign_id:
            return False

        draft_itemid = 0
        for i, f in enumerate(self._submitted_files):
            file_url = f.get('url', '')
            if not file_url:
                return False

            file_bytes = client.download_file(file_url)
            if not file_bytes:
                return False

            if i == target_idx:
                # Upload with new metadata
                result_id = client.upload_draft_file(
                    meta['new_name'], file_bytes,
                    itemid=draft_itemid,
                    author=meta.get('author', ''),
                    license_key=meta.get('license', 'unknown'),
                )
            else:
                # Giữ nguyên file khác, không thay đổi metadata
                result_id = client.upload_draft_file(
                    f.get('name', 'file'), file_bytes,
                    itemid=draft_itemid,
                )

            if result_id is None:
                return False
            draft_itemid = result_id

        return save_assignment_submission(client.call_ws_api, assign_id, draft_itemid)