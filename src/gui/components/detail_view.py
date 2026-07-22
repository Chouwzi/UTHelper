import os
import sys
from typing import Optional

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
import asyncio
import logging
from gui.core.theme import C
from gui.core.utils import get_countdown_color, get_urgency_badge, clean_course_name, format_deadline, get_countdown
from gui.components.detail.submitted_files_table import build_submitted_files_ui
from core.use_cases.submission_workflow import (
    FileMetadataUpdate,
    SelectedSubmissionFile,
    SubmittedFile,
    SubmissionTarget,
)

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
    def __init__(self, page: ft.Page, on_close, get_client=None, on_status_changed=None, submission_workflow_factory=None):
        super().__init__()
        self._init_variables(page, get_client, on_status_changed, submission_workflow_factory)
        self._init_controls()
        self._init_layout(on_close)

    def _init_variables(self, page: ft.Page, get_client, on_status_changed, submission_workflow_factory):
        self._page          = page
        self.visible        = False
        self.expand         = True
        self.bgcolor        = C.BG
        self._current_url   = ""
        self._current_data  = {}
        self._get_client    = get_client   
        self._on_status_changed = on_status_changed
        self._submission_workflow_factory = submission_workflow_factory
        self._selected_files = []          
        self._is_uploading  = False

    def _submission_workflow(self, client):
        if self._submission_workflow_factory:
            return self._submission_workflow_factory(client)
        raise RuntimeError("Submission workflow factory is not configured.")

    def _submission_target(self, url: str, course_id: int) -> SubmissionTarget:
        return SubmissionTarget(url=url, course_id=course_id)

    def _selected_submission_files(self) -> list[SelectedSubmissionFile]:
        return [
            SelectedSubmissionFile(
                name=getattr(file_item, "name", "file"),
                bytes=getattr(file_item, "bytes", b""),
            )
            for file_item in self._selected_files
        ]

    def _submitted_file_dtos(self, files: list[dict]) -> list[SubmittedFile]:
        return [
            SubmittedFile(
                name=file_item.get("name", "file"),
                url=file_item.get("url", ""),
                filepath=file_item.get("filepath", "/"),
            )
            for file_item in files
        ]

    def _submitted_file_dicts(self, files: list[SubmittedFile]) -> list[dict]:
        return [
            {
                "name": file_item.name,
                "url": file_item.url,
                "filepath": file_item.filepath,
            }
            for file_item in files
        ]

    def _init_controls(self):
        self._title_text    = ft.Text("", size=18, weight=ft.FontWeight.BOLD,
                                      color=C.TEXT_PRIMARY, max_lines=3)
        self._course_text   = ft.Text("", size=12, color=C.ACCENT)
        self._badge_ctrl    = ft.Container(visible=False)
        self._countdown_txt = ft.Text("", size=13, weight=ft.FontWeight.W_600)
        self._deadline_txt  = ft.Text("", size=13, color=C.TEXT_PRIMARY)
        self._opentime_txt  = ft.Text("", size=13, color=C.TEXT_PRIMARY)
        self._cutoff_txt    = ft.Text("", size=13, color=C.WARNING)
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

        self._file_list_col = ft.Column(spacing=4, visible=False)
        self._upload_progress = ft.ProgressBar(color=C.SAFE, bgcolor=C.BORDER, visible=False)
        self._upload_status = ft.Text("", size=12, color=C.TEXT_SECONDARY, visible=False)

        self._submitted_files = []  
        self._editing_file_index = -1  
        self._selected_file_indices = set()  
        self._is_multiselect_mode = False

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
                ft.Button(
                    "Cập nhật",
                    icon=ft.Icons.SAVE_ROUNDED,
                    on_click=self._on_update_file_metadata,
                    bgcolor=C.ACCENT, color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        self._delete_confirm_text = ft.Text("", size=13, color=C.TEXT_PRIMARY)
        self._delete_confirm_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Xác nhận xóa", size=16, weight=ft.FontWeight.BOLD,
                          color=C.CRITICAL),
            bgcolor=C.SURFACE,
            content=ft.Container(
                content=self._delete_confirm_text,
                width=320,
            ),
            actions=[
                ft.TextButton("Hủy", on_click=self._close_delete_confirm,
                              style=ft.ButtonStyle(color=C.TEXT_SECONDARY)),
                ft.Button(
                    "Xóa",
                    icon=ft.Icons.DELETE_ROUNDED,
                    on_click=self._do_confirmed_delete,
                    bgcolor=C.CRITICAL, color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._pending_delete_indices = []

        self._submitted_files_col = ft.Column(spacing=4, visible=False)

        self._batch_delete_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.DELETE_SWEEP_ROUNDED, size=14, color=C.CRITICAL),
                    ft.Text("Xóa đã chọn", size=12, color=C.CRITICAL,
                            weight=ft.FontWeight.W_500),
                ],
                spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.CRITICAL),
            padding=ft.Padding.symmetric(vertical=6, horizontal=12),
            border_radius=6,
            on_click=lambda _: self._confirm_batch_delete(),
            ink=True,
            visible=False,
        )

        self._multiselect_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHECKLIST_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text("Chọn nhiều", size=12, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                ],
                spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            padding=ft.Padding.symmetric(vertical=6, horizontal=12),
            border_radius=6,
            on_click=lambda _: self._toggle_multiselect(),
            ink=True,
            visible=False,
        )

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
                ft.Row(controls=[
                    self._multiselect_btn,
                    self._batch_delete_btn,
                ], spacing=8),
                self._edit_submitted_btn,
            ], spacing=8),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.SAFE + "40"),
            border_radius=8,
            padding=ft.Padding.all(14),
            visible=False,
        )

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

        self._upload_mode_overwrite = False  
        self._mode_overwrite_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.SWAP_HORIZ_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                ft.Text("Ghi đè", size=11, color=C.TEXT_SECONDARY, weight=ft.FontWeight.W_500),
            ], spacing=4, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=C.BG,
            border=ft.Border.all(1, C.BORDER),
            border_radius=6,
            padding=ft.Padding.symmetric(vertical=6, horizontal=12),
            on_click=lambda _: self._set_upload_mode(True),
            ink=True,
            expand=True,
        )
        self._mode_append_btn = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ADD_CIRCLE_OUTLINE_ROUNDED, size=14, color=ft.Colors.WHITE),
                ft.Text("Thêm file", size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
            ], spacing=4, alignment=ft.MainAxisAlignment.CENTER),
            bgcolor=C.ACCENT,
            border_radius=6,
            padding=ft.Padding.symmetric(vertical=6, horizontal=12),
            on_click=lambda _: self._set_upload_mode(False),
            ink=True,
            expand=True,
        )
        self._upload_mode_warning_icon = ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=12, color=C.WARNING)
        self._upload_mode_warning_text = ft.Text(
            "Sẽ tải lại file cũ trước khi nộp. Có thể lâu nếu file nặng.",
            size=10, color=C.WARNING, italic=True, expand=True,
        )
        self._upload_mode_warning = ft.Container(
            content=ft.Row([
                self._upload_mode_warning_icon,
                self._upload_mode_warning_text,
            ], spacing=4),
            visible=True,
        )
        self._upload_mode_row = ft.Column(
            controls=[
                ft.Row([
                    self._mode_overwrite_btn,
                    self._mode_append_btn,
                ], spacing=6),
                self._upload_mode_warning,
            ],
            spacing=4,
            visible=False,  
        )

        self._submission_area = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Icon(ft.Icons.ATTACH_FILE_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text("Chọn file để nộp:", size=13, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                ], spacing=6),
                self._file_list_col,
                self._upload_mode_row,
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

        self._opentime_row = ft.Row(controls=[
                    ft.Text("Mở từ", size=11, color=C.TEXT_SECONDARY,
                            weight=ft.FontWeight.W_500),
                    self._opentime_txt,
                ], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        self._opentime_row.visible = False

        self._cutoff_row = ft.Row(controls=[
                    ft.Text("Hạn nộp muộn", size=11, color=C.WARNING,
                            weight=ft.FontWeight.W_500),
                    self._cutoff_txt,
                ], spacing=8, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        self._cutoff_row.visible = False

        self._header_open_btn = ft.IconButton(
            ft.Icons.OPEN_IN_BROWSER_ROUNDED,
            icon_color=C.ACCENT, icon_size=20,
            tooltip="Mở trong trình duyệt",
            on_click=self._open_browser,
        )

    def _init_layout(self, on_close):
        back_btn = ft.TextButton(
            content=ft.Row(controls=[
                ft.Icon(ft.Icons.ARROW_BACK_ROUNDED, size=16, color=C.TEXT_SECONDARY),
                ft.Text("Quay lại", size=14, color=C.TEXT_SECONDARY),
            ], spacing=4, tight=True),
            on_click=lambda _: on_close(),
            style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=10)),
        )

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
                self._cutoff_row,
            ], spacing=10),
            bgcolor=C.SURFACE,
            padding=ft.Padding.all(14),
            border_radius=8,
            border=ft.Border.all(1, C.BORDER),
        )

        self.content = ft.Column(
            controls=[
                ft.Container(
                    content=ft.Row(controls=[back_btn, self._header_open_btn],
                                   alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    padding=ft.Padding.only(left=8, right=8, top=20, bottom=8),
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

    # Hàm công khai
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

        # Cutoff date display
        cutoff_ts = data.get('cutoff_date', 0)
        duedate_str = data.get('deadline', '')
        if cutoff_ts and cutoff_ts > 0:
            from datetime import datetime as _dt
            try:
                cutoff_dt = _dt.fromtimestamp(cutoff_ts)
                self._cutoff_txt.value = format_deadline(cutoff_dt.isoformat())
                # Only show if cutoff differs from deadline
                deadline_dt = None
                if duedate_str:
                    from core.time_utils import parse_datetime
                    deadline_dt = parse_datetime(duedate_str)
                if deadline_dt and abs((cutoff_dt - deadline_dt).total_seconds()) > 60:
                    self._cutoff_row.visible = True
                else:
                    self._cutoff_row.visible = False
            except (OSError, ValueError):
                self._cutoff_row.visible = False
        else:
            self._cutoff_row.visible = False

        self.visible = True
        # Hàm gọi chịu trách nhiệm page.update() tránh việc gọi thừa

    def refresh_countdown(self) -> bool:
        """Re-evaluate volatile deadline text while the detail view stays open."""
        data = self._current_data or {}
        deadline_str = data.get("deadline", "")
        if not deadline_str:
            return False
        countdown, overdue = get_countdown(deadline_str, data.get("type", ""))
        color = C.CRITICAL if overdue else get_countdown_color(deadline_str)
        changed = (
            self._countdown_txt.value != countdown
            or self._countdown_txt.color != color
        )
        self._countdown_txt.value = countdown
        self._countdown_txt.color = color
        return changed

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
        elif sub_status in ("submitted", "Đã nộp"):
            # Đã nộp nhưng chưa chấm → vẫn cho nộp thêm file
            self._submission_area.visible = True
            self._pick_btn.visible = True
            self._submit_btn.visible = False
            self._cta_icon.name = ft.Icons.VISIBILITY_ROUNDED
            self._cta_text.value = "Xem bài nộp"
            self._open_btn.bgcolor = C.SURFACE
            self._open_btn.border = ft.Border.all(1, C.ACCENT)
            self._cta_text.color = C.ACCENT
            self._cta_icon.color = C.ACCENT
        elif sub_status in ("graded", "Đã chấm điểm"):
            # Đã chấm điểm → ẩn submission area
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
                    prefetched = data.get("details", {}).get("raw_submission_status")
                    self._page.run_task(
                        self._async_load_submitted_files,
                        client, url, int(course_id), prefetched
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

        # Cutoff date display in detail panel
        cutoff_ts = data.get('cutoff_date', 0)
        deadline_str_raw = data.get('deadline', '')
        if cutoff_ts and cutoff_ts > 0:
            from datetime import datetime as _dt
            try:
                cutoff_dt = _dt.fromtimestamp(cutoff_ts)
                self._cutoff_txt.value = format_deadline(cutoff_dt.isoformat())
                deadline_dt = None
                if deadline_str_raw:
                    from core.time_utils import parse_datetime
                    deadline_dt = parse_datetime(deadline_str_raw)
                if deadline_dt and abs((cutoff_dt - deadline_dt).total_seconds()) > 60:
                    self._cutoff_row.visible = True
                else:
                    self._cutoff_row.visible = False
            except (OSError, ValueError):
                self._cutoff_row.visible = False
        else:
            self._cutoff_row.visible = False

        # Trích xuất thông tin Tên môn học
        full_name = details.get("course_full_name", "")
        self._course_text.value = clean_course_name(full_name or data.get("course", ""))

        # Trạng thái nộp bài
        status_data = details.get("status_data", {})
        # Keys to skip: raw HTML fields, duplicates (time shown in info box), comment templates
        _SKIP_KEYS = {
            "Online text", "Submission comments", "Mở từ",
            "Đăng tải các bình luận.",          # Moodle comment template (raw HTML)
            "Time remaining", "Thời gian còn lại",  # Already shown in info box above
        }
        if status_data:
            rows = []
            for k, v in status_data.items():
                if (k not in _SKIP_KEYS 
                    and v 
                    and str(v).strip() not in ("-", "") 
                    and "___" not in str(v)):
                    
                    translated_key = _translate_key(k)
                    translated_val = _translate_status(str(v))
                    val_color = C.TEXT_PRIMARY
                    
                    if k in ("Submission status", "Trạng thái nộp bài") or translated_key == "Trạng thái nộp bài":
                        val_str = str(v).lower()
                        is_submitted = (
                            "submit" in val_str or 
                            "finish" in val_str or 
                            "nộp" in translated_val.lower() or 
                            "hoàn thành" in translated_val.lower()
                        ) and "chưa" not in translated_val.lower() and "no" not in val_str
                        val_color = C.SAFE if is_submitted else C.CRITICAL
                        
                    rows.append(
                        ft.Row(controls=[
                            ft.Text(translated_key, size=12, color=C.TEXT_SECONDARY, width=130),
                            ft.Text(translated_val, size=12, color=val_color, expand=True, weight=ft.FontWeight.W_600 if val_color in (C.SAFE, C.CRITICAL) else ft.FontWeight.NORMAL),
                        ], spacing=8)
                    )
            if rows:
                self._content_col.controls.append(self._section("Trạng thái", rows))

        # Thông tin Quiz
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

        # Thông tin Điểm danh
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

        # Mô tả
        desc_html = details.get("description_html", "")
        if desc_html:
            from gui.core.utils import html_to_markdown
            desc_md = html_to_markdown(desc_html)
            self._content_col.controls.append(self._section("Mô tả", [
                ft.Container(
                    content=ft.Markdown(
                        desc_md,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        on_tap_link=lambda e: ft.UrlLauncher().launch_url(e.data),
                    ),
                    bgcolor=C.SURFACE, border_radius=8,
                    padding=ft.Padding.all(12),
                )
            ]))

        # Chỉ thay đổi giao diện, không tự gọi update

    def show_error_banner(self):
        self._error_banner.visible = True

    # Hàm nội bộ (Private)
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

        # UTH Moodle redirects to /my/courses.php if already logged in via autologin.
        # Direct navigation relies on active browser sessions or standard CAS SSO redirect.
        await ft.UrlLauncher().launch_url(self._current_url)

    # In-App Submission

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
        # Show upload mode toggle when there are both new files and existing submitted files
        self._upload_mode_row.visible = has_files and bool(self._submitted_files)
        self._page.update()

    def _remove_file(self, index: int):
        """Xóa file khỏi danh sách đã chọn."""
        if 0 <= index < len(self._selected_files):
            self._selected_files.pop(index)
            self._update_file_preview()

    def _set_upload_mode(self, overwrite: bool):
        """Chuyển chế độ upload: ghi đè hoặc thêm file."""
        self._upload_mode_overwrite = overwrite
        if overwrite:
            # Overwrite active
            self._mode_overwrite_btn.bgcolor = C.ACCENT
            self._mode_overwrite_btn.border = None
            self._mode_overwrite_btn.content.controls[0].color = ft.Colors.WHITE
            self._mode_overwrite_btn.content.controls[1].color = ft.Colors.WHITE
            self._mode_overwrite_btn.content.controls[1].weight = ft.FontWeight.W_600
            # Append inactive
            self._mode_append_btn.bgcolor = C.BG
            self._mode_append_btn.border = ft.Border.all(1, C.BORDER)
            self._mode_append_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._mode_append_btn.content.controls[1].color = C.TEXT_SECONDARY
            self._mode_append_btn.content.controls[1].weight = ft.FontWeight.W_500
            self._upload_mode_warning_icon.name = ft.Icons.WARNING_AMBER_ROUNDED
            self._upload_mode_warning_text.value = "Sẽ ghi đè toàn bộ file đã nộp trước đó."
        else:
            # Append active
            self._mode_append_btn.bgcolor = C.ACCENT
            self._mode_append_btn.border = None
            self._mode_append_btn.content.controls[0].color = ft.Colors.WHITE
            self._mode_append_btn.content.controls[1].color = ft.Colors.WHITE
            self._mode_append_btn.content.controls[1].weight = ft.FontWeight.W_600
            # Overwrite inactive
            self._mode_overwrite_btn.bgcolor = C.BG
            self._mode_overwrite_btn.border = ft.Border.all(1, C.BORDER)
            self._mode_overwrite_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._mode_overwrite_btn.content.controls[1].color = C.TEXT_SECONDARY
            self._mode_overwrite_btn.content.controls[1].weight = ft.FontWeight.W_500
            self._upload_mode_warning_icon.name = ft.Icons.INFO_OUTLINE_ROUNDED
            self._upload_mode_warning_text.value = (
                "Sẽ tải lại file cũ trước khi nộp. "
                "Có thể lâu nếu file nặng."
            )
        self._page.update()

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
        return self._submission_workflow(client).submit_files(
            target=self._submission_target(url, course_id),
            selected_files=self._selected_submission_files(),
            submitted_files=self._submitted_file_dtos(self._submitted_files),
            overwrite=self._upload_mode_overwrite,
        )

    def _show_upload_status(self, text: str, color: str):
        """Hiện thông báo trạng thái upload."""
        self._upload_status.value = text
        self._upload_status.color = color
        self._upload_status.visible = True

    async def _async_load_submitted_files(self, client, url: str, course_id: int, prefetched_status: Optional[dict] = None):
        """Async wrapper: load submitted files in bg thread, then update UI."""
        try:
            self._last_server_status = None
            await asyncio.to_thread(
                self._load_submitted_files, client, url, course_id, prefetched_status
            )
            
            # Cập nhật trạng thái nộp bài thông qua callback để đồng bộ các Activity Cards ở dashboard
            if self._last_server_status and self._on_status_changed and self._current_url:
                self._current_data["submission_status"] = self._last_server_status
                self._on_status_changed(self._current_url, self._last_server_status)
                
            self._build_submitted_files_ui()
            self._page.update()
        except Exception as ex:
            logger.debug("Load submitted files error: %s", ex)

    def _load_submitted_files(self, client, url: str, course_id: int, prefetched_status: Optional[dict] = None):
        """Load danh sách file đã nộp từ server (chạy trong thread)."""
        result = self._submission_workflow(client).load_submitted_files(
            target=self._submission_target(url, course_id),
            prefetched_status=prefetched_status,
        )
        self._last_server_status = result.last_server_status
        self._submitted_files = self._submitted_file_dicts(result.files)

    def _build_submitted_files_ui(self):
        build_submitted_files_ui(self)

    async def _on_remove_submitted_files(self, indices: list):
        """Xóa nhiều file đã nộp: re-upload các file còn lại rồi re-submit.
        
        Workflow:
        1. Download tất cả file CÒN LẠI từ server
        2. Upload chúng vào draft area mới
        3. mod_assign_save_submission để re-submit
        """
        if self._is_uploading:
            return
        indices_set = set(indices)
        
        removed_names = [self._submitted_files[i].get('name', '') for i in indices if 0 <= i < len(self._submitted_files)]
        files_to_keep = [f for i, f in enumerate(self._submitted_files) if i not in indices_set]
        
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
        count = len(removed_names)
        if count == 1:
            self._show_upload_status(f"Đang xóa '{removed_names[0]}'...", C.TEXT_SECONDARY)
        else:
            self._show_upload_status(f"Đang xóa {count} file...", C.TEXT_SECONDARY)
        self._page.update()

        try:
            success = await asyncio.to_thread(
                self._do_remove_file_sync, client, url, int(course_id), files_to_keep
            )
            if success:
                if count == 1:
                    self._show_upload_status(f"Đã xóa '{removed_names[0]}'", C.SAFE)
                else:
                    self._show_upload_status(f"Đã xóa {count} file", C.SAFE)
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
        return self._submission_workflow(client).remove_files(
            target=self._submission_target(url, course_id),
            files_to_keep=self._submitted_file_dtos(files_to_keep),
        )

    async def _on_edit_submitted(self, e):
        """Mở trình duyệt để chỉnh sửa bài nộp."""
        if not self._current_url:
            return
        # UTH Moodle redirects to /my/courses.php if already logged in via autologin.
        # Direct navigation relies on active browser sessions or standard CAS SSO redirect.
        await ft.UrlLauncher().launch_url(self._current_url)

    # File Edit Dialog

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

    # Delete Confirmation

    def _confirm_single_delete(self, index: int):
        """Hiện popup xác nhận xóa 1 file."""
        if index < 0 or index >= len(self._submitted_files):
            return
        name = self._submitted_files[index].get('name', 'file')
        self._pending_delete_indices = [index]
        self._delete_confirm_text.value = f"Bạn có chắc chắn muốn xóa file '{name}'?"
        dlg = self._delete_confirm_dialog
        if dlg not in self._page.overlay:
            self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _confirm_batch_delete(self):
        """Hiện popup xác nhận xóa nhiều file đã chọn."""
        if not self._selected_file_indices:
            return
        indices = sorted(self._selected_file_indices)
        names = [self._submitted_files[i].get('name', '') for i in indices]
        self._pending_delete_indices = indices
        if len(names) == 1:
            self._delete_confirm_text.value = f"Bạn có chắc chắn muốn xóa file '{names[0]}'?"
        else:
            file_list = "\n".join(f"  • {n}" for n in names)
            self._delete_confirm_text.value = f"Bạn có chắc chắn muốn xóa {len(names)} file?\n{file_list}"
        dlg = self._delete_confirm_dialog
        if dlg not in self._page.overlay:
            self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _close_delete_confirm(self, e=None):
        """Đóng popup xác nhận xóa."""
        self._delete_confirm_dialog.open = False
        self._pending_delete_indices = []
        self._page.update()

    async def _do_confirmed_delete(self, e=None):
        """Thực hiện xóa sau khi người dùng xác nhận."""
        self._delete_confirm_dialog.open = False
        self._page.update()
        indices = self._pending_delete_indices
        self._pending_delete_indices = []
        if not indices:
            return
        await self._on_remove_submitted_files(indices)

    # Multi-select Mode

    def _toggle_multiselect(self):
        """Bật/tắt chế độ chọn nhiều file."""
        self._is_multiselect_mode = not self._is_multiselect_mode
        self._selected_file_indices.clear()

        # Update button appearance
        btn_row = self._multiselect_btn.content
        if self._is_multiselect_mode:
            btn_row.controls[0].color = C.ACCENT
            btn_row.controls[1].value = "Hủy chọn"
            btn_row.controls[1].color = C.ACCENT
            self._multiselect_btn.border = ft.Border.all(1, C.ACCENT)
        else:
            btn_row.controls[0].color = C.TEXT_SECONDARY
            btn_row.controls[1].value = "Chọn nhiều"
            btn_row.controls[1].color = C.TEXT_SECONDARY
            self._multiselect_btn.border = ft.Border.all(1, C.BORDER)

        # Show/hide checkboxes
        for container in self._submitted_files_col.controls:
            header_row = container.content.controls[0]  # Row with [cb, icon, text, edit, delete]
            cb = header_row.controls[0]  # Checkbox
            cb.visible = self._is_multiselect_mode
            cb.value = False

        self._batch_delete_btn.visible = False
        self._page.update()

    def _on_file_checkbox_changed(self, index: int, checked: bool):
        """Xử lý khi checkbox file thay đổi."""
        if checked:
            self._selected_file_indices.add(index)
        else:
            self._selected_file_indices.discard(index)
        # Show/hide batch delete button
        self._batch_delete_btn.visible = len(self._selected_file_indices) > 0
        # Update batch delete text
        count = len(self._selected_file_indices)
        btn_row = self._batch_delete_btn.content
        btn_row.controls[1].value = f"Xóa đã chọn ({count})" if count > 0 else "Xóa đã chọn"
        self._page.update()

    async def _on_update_file_metadata(self, e=None):
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
        return self._submission_workflow(client).update_file_metadata(
            target=self._submission_target(url, course_id),
            submitted_files=self._submitted_file_dtos(self._submitted_files),
            target_idx=target_idx,
            meta=FileMetadataUpdate(
                new_name=meta["new_name"],
                author=meta.get("author", ""),
                license=meta.get("license", "unknown"),
                filepath=meta.get("filepath", "/"),
            ),
        )

    def update_theme(self):
        """Update colors of all detail view controls dynamically on theme switch."""
        from gui.core.theme import C
        self.bgcolor = C.BG
        
        # Static Text & Control Colors
        self._title_text.color = C.TEXT_PRIMARY
        self._course_text.color = C.ACCENT
        self._deadline_txt.color = C.TEXT_PRIMARY
        self._opentime_txt.color = C.TEXT_PRIMARY
        self._cutoff_txt.color = C.WARNING
        
        # Loading and status
        self._loading_bar.color = C.ACCENT
        self._loading_bar.bgcolor = C.BORDER
        self._upload_progress.color = C.SAFE
        self._upload_progress.bgcolor = C.BORDER
        self._upload_status.color = C.TEXT_SECONDARY

        # Error Banner
        try:
            self._error_banner.bgcolor = C.SURFACE
            self._error_banner.border = ft.Border.all(1, C.WARNING)
            self._error_banner.content.controls[0].color = C.WARNING
            self._error_banner.content.controls[1].color = C.WARNING
        except Exception:
            pass

        # Text Fields & Dropdowns (Edit metadata dialog)
        _edit_fields = [
            self._edit_filename, self._edit_author,
            self._edit_license, self._edit_filepath
        ]
        for f in _edit_fields:
            if f:
                f.border_color = C.BORDER
                f.focused_border_color = C.ACCENT
                f.color = C.TEXT_PRIMARY
                if hasattr(f, 'label_style') and f.label_style:
                    f.label_style.color = C.TEXT_SECONDARY
                f.bgcolor = C.BG
                if hasattr(f, 'fill_color') and f.fill_color:
                    f.fill_color = C.BG

        # Edit Dialog
        try:
            self._file_edit_dialog.title.color = C.TEXT_PRIMARY
            self._file_edit_dialog.bgcolor = C.SURFACE
            # Cancel Button
            self._file_edit_dialog.actions[0].style.color = C.TEXT_SECONDARY
            # Update Button
            self._file_edit_dialog.actions[1].bgcolor = C.ACCENT
        except Exception:
            pass

        # Delete Dialog
        try:
            self._delete_confirm_text.color = C.TEXT_PRIMARY
            self._delete_confirm_dialog.title.color = C.CRITICAL
            self._delete_confirm_dialog.bgcolor = C.SURFACE
            # Cancel Button
            self._delete_confirm_dialog.actions[0].style.color = C.TEXT_SECONDARY
            # Delete Button
            self._delete_confirm_dialog.actions[1].bgcolor = C.CRITICAL
        except Exception:
            pass

        # Buttons (Batch delete, multiselect, edit submitted)
        try:
            # Batch delete button
            self._batch_delete_btn.bgcolor = C.SURFACE
            self._batch_delete_btn.border = ft.Border.all(1, C.CRITICAL)
            self._batch_delete_btn.content.controls[0].color = C.CRITICAL
            self._batch_delete_btn.content.controls[1].color = C.CRITICAL
            
            # Multiselect button
            self._multiselect_btn.bgcolor = C.SURFACE
            self._multiselect_btn.border = ft.Border.all(1, C.BORDER)
            self._multiselect_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._multiselect_btn.content.controls[1].color = C.TEXT_SECONDARY
            
            # Edit submitted button
            self._edit_submitted_btn.bgcolor = C.SURFACE
            self._edit_submitted_btn.border = ft.Border.all(1, C.WARNING)
            self._edit_submitted_btn.content.controls[0].color = C.WARNING
            self._edit_submitted_btn.content.controls[1].color = C.WARNING
        except Exception:
            pass

        # Areas (submitted, submission, pick, submit, upload modes)
        try:
            self._submitted_area.bgcolor = C.SURFACE
            self._submitted_area.border = ft.Border.all(1, C.SAFE + "40")
            self._submitted_area.content.controls[0].controls[0].color = C.TEXT_SECONDARY
            self._submitted_area.content.controls[0].controls[1].color = C.TEXT_SECONDARY
            
            self._pick_btn.bgcolor = C.SURFACE
            self._pick_btn.border = ft.Border.all(1, C.ACCENT)
            self._pick_btn.content.controls[0].color = C.ACCENT
            self._pick_btn.content.controls[1].color = C.ACCENT
            
            self._submit_btn.bgcolor = C.SAFE
            
            self._mode_overwrite_btn.bgcolor = C.BG
            self._mode_overwrite_btn.border = ft.Border.all(1, C.BORDER)
            self._mode_overwrite_btn.content.controls[0].color = C.TEXT_SECONDARY
            self._mode_overwrite_btn.content.controls[1].color = C.TEXT_SECONDARY
            
            self._mode_append_btn.bgcolor = C.ACCENT
            self._upload_mode_warning_icon.color = C.WARNING
            self._upload_mode_warning_text.color = C.WARNING
            
            self._submission_area.bgcolor = C.SURFACE
            self._submission_area.border = ft.Border.all(1, C.BORDER)
            self._submission_area.content.controls[0].controls[0].color = C.TEXT_SECONDARY
            self._submission_area.content.controls[0].controls[1].color = C.TEXT_SECONDARY
            
            self._open_btn.bgcolor = C.ACCENT
            
            self._opentime_row.controls[0].color = C.TEXT_SECONDARY
            self._cutoff_row.controls[0].color = C.WARNING
            self._header_open_btn.icon_color = C.ACCENT
        except Exception:
            pass

        # Layout elements
        try:
            back_row = self.content.controls[0].content.controls[0].content
            back_row.controls[0].color = C.TEXT_SECONDARY
            back_row.controls[1].color = C.TEXT_SECONDARY
            
            detail_col = self.content.controls[2].content.controls[0].content
            detail_col.controls[3].color = C.BORDER
            
            info_box = detail_col.controls[4]
            info_box.bgcolor = C.SURFACE
            info_box.border = ft.Border.all(1, C.BORDER)
            info_box.content.controls[0].controls[0].color = C.TEXT_SECONDARY
            info_box.content.controls[1].controls[0].color = C.TEXT_SECONDARY
            info_box.content.controls[2].controls[0].color = C.TEXT_SECONDARY
            info_box.content.controls[3].controls[0].color = C.WARNING
        except Exception:
            pass

        # If data is currently shown, refresh dynamic content
        if self._current_data:
            self.update_detail(self._current_data)

        try:
            self.update()
        except Exception:
            pass

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        pass
    from gui.app_controller import AppController
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))
