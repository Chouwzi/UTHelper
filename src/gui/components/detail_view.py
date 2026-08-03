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
from collections import Counter
from gui.core.theme import C
from gui.core.utils import get_countdown_color, get_urgency_badge, clean_course_name, format_deadline, get_countdown
from gui.components.detail.submitted_files_table import build_submitted_files_ui
from gui.components.detail.submission_presenter import (
    SubmissionUiPolicy,
    mutation_message,
)
from core.submission_models import (
    FileIdentity,
    FileMutationIntent,
    MutationOperation,
    RemoteFile,
    SelectedFile,
    SubmissionSnapshot,
    normalize_filepath,
)
from core.use_cases.submission_workflow import (
    FinalizeSubmissionIntent,
    SelectedSubmissionFile,
    SubmissionMutationResult,
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
        self._submission_snapshot: SubmissionSnapshot | None = None
        self._submission_fingerprint = ""
        self._submission_policy: SubmissionUiPolicy | None = None
        self._pending_file_mutation: tuple[MutationOperation, bool] | None = None
        self._view_generation = 0
        self._snapshot_load_generation = 0

    def _submission_workflow(self, client):
        if self._submission_workflow_factory:
            return self._submission_workflow_factory(client)
        raise RuntimeError("Submission workflow factory is not configured.")

    def _submission_target(self, url: str, course_id: int) -> SubmissionTarget:
        return SubmissionTarget(url=url, course_id=course_id)

    @staticmethod
    def _submitted_file_dicts(files: tuple[RemoteFile, ...]) -> list[dict]:
        return [
            {
                "name": file_item.name,
                "url": file_item.url,
                "filepath": file_item.filepath,
                "size": file_item.size,
                "mimetype": file_item.mimetype,
                "timemodified": file_item.modified_time,
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
        self._submission_status_value = ft.Text(
            "", size=12, color=C.TEXT_PRIMARY, expand=True
        )

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

        self._replace_confirm_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Xác nhận thay thế", size=16, weight=ft.FontWeight.BOLD,
                          color=C.WARNING),
            bgcolor=C.SURFACE,
            content=ft.Text(
                "Thao tác này sẽ thay thế toàn bộ danh sách file hiện có trên Moodle.",
                size=13,
                color=C.TEXT_PRIMARY,
            ),
            actions=[
                ft.TextButton("Hủy", on_click=self._close_replace_confirmation,
                              style=ft.ButtonStyle(color=C.TEXT_SECONDARY)),
                ft.Button(
                    "Tiếp tục",
                    icon=ft.Icons.SWAP_HORIZ_ROUNDED,
                    on_click=self._confirm_replace_mutation,
                    bgcolor=C.WARNING,
                    color=ft.Colors.WHITE,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

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
            on_click=self._on_confirm_batch_delete_async,
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
            on_click=self._on_toggle_multiselect_async,
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
                    ft.Text("Lưu bài nộp", size=13, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
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

        self._finalize_btn = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.SEND_ROUNDED, size=16, color=ft.Colors.WHITE),
                    ft.Text("Nộp bài", size=13, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600),
                ],
                spacing=6,
                alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SAFE,
            padding=ft.Padding.symmetric(vertical=13),
            border_radius=8,
            on_click=self._on_finalize,
            ink=True,
            visible=False,
            expand=True,
        )

        self._submission_statement = ft.Checkbox(
            label="Tôi xác nhận bài nộp này là sản phẩm của mình.",
            value=False,
            active_color=C.ACCENT,
            visible=False,
        )
        self._submission_policy_text = ft.Text(
            "", size=11, color=C.TEXT_SECONDARY, visible=False
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
            on_click=self._on_mode_overwrite_async,
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
            on_click=self._on_mode_append_async,
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
                self._submission_policy_text,
                self._submission_statement,
                self._upload_progress,
                self._upload_status,
                ft.Row(controls=[
                    self._pick_btn,
                    self._submit_btn,
                    self._finalize_btn,
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
        self._view_generation += 1
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
        self._pending_file_mutation = None
        self._replace_confirm_dialog.open = False
        self._upload_mode_row.visible = False
        self._submitted_files.clear()
        self._submission_snapshot = None
        self._submission_fingerprint = ""
        self._submission_policy = None
        self._submission_statement.value = False
        self._submission_statement.visible = False
        self._submission_policy_text.visible = False
        self._pick_btn.visible = False
        self._submit_btn.visible = False
        self._finalize_btn.visible = False
        self._submitted_files_col.controls.clear()
        self._submitted_area.visible = False

        if is_assignment and sub_status not in ("submitted", "graded", "Đã nộp", "Đã chấm điểm"):
            # CTA chính → mở browser (fallback)
            self._cta_icon.name = ft.Icons.OPEN_IN_BROWSER_ROUNDED
            self._cta_text.value = "Mở trong trình duyệt"
            self._open_btn.bgcolor = C.SURFACE
            self._open_btn.border = ft.Border.all(1, C.ACCENT)
            self._cta_text.color = C.ACCENT
            self._cta_icon.color = C.ACCENT
        elif sub_status in ("submitted", "Đã nộp"):
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
                    import logging as _fb_log
                    _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
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
        if not isinstance(status_data, dict):
            status_data = {}
        rows = []
        has_submission_status = False
        self._submission_status_value.value = "Đang đồng bộ với Moodle..."
        self._submission_status_value.color = C.TEXT_SECONDARY
        self._submission_status_value.weight = ft.FontWeight.W_600
        for k, v in status_data.items():
            if (k not in _SKIP_KEYS
                and v
                and str(v).strip() not in ("-", "")
                and "___" not in str(v)):

                translated_key = _translate_key(k)
                translated_val = _translate_status(str(v))
                val_color = C.TEXT_PRIMARY
                is_submission_status = (
                    k in ("Submission status", "Trạng thái nộp bài")
                    or translated_key == "Trạng thái nộp bài"
                )

                if is_submission_status:
                    if not is_assignment:
                        continue
                    has_submission_status = True
                    val_str = str(v).lower()
                    is_submitted = (
                        "submit" in val_str
                        or "finish" in val_str
                        or "nộp" in translated_val.lower()
                        or "hoàn thành" in translated_val.lower()
                    ) and "chưa" not in translated_val.lower() and "no" not in val_str
                    val_color = C.SAFE if is_submitted else C.CRITICAL
                    self._submission_status_value.value = translated_val
                    self._submission_status_value.color = val_color
                    value_control = self._submission_status_value
                else:
                    value_control = ft.Text(
                        translated_val,
                        size=12,
                        color=val_color,
                        expand=True,
                        weight=ft.FontWeight.NORMAL,
                    )

                rows.append(
                    ft.Row(controls=[
                        ft.Text(translated_key, size=12, color=C.TEXT_SECONDARY, width=130),
                        value_control,
                    ], spacing=8)
                )
        if is_assignment and not has_submission_status:
            rows.insert(
                0,
                ft.Row(
                    controls=[
                        ft.Text(
                            "Trạng thái nộp bài",
                            size=12,
                            color=C.TEXT_SECONDARY,
                            width=130,
                        ),
                        self._submission_status_value,
                    ],
                    spacing=8,
                ),
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
                            on_click=self._create_remove_handler(i),
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
        self._render_submission_policy()
        self._page.update()


    async def _on_confirm_batch_delete_async(self, e):
        self._confirm_batch_delete()

    async def _on_toggle_multiselect_async(self, e):
        self._toggle_multiselect()

    async def _on_mode_overwrite_async(self, e):
        self._set_upload_mode(True)

    async def _on_mode_append_async(self, e):
        self._set_upload_mode(False)

    def _create_remove_handler(self, idx):
        async def handler(e):
            self._remove_file(idx)
        return handler

    def _remove_file(self, index: int):
        """Xóa file khỏi danh sách đã chọn."""
        if 0 <= index < len(self._selected_files):
            self._selected_files.pop(index)
            self._update_file_preview()

    def _set_upload_mode(self, overwrite: bool):
        """Chuyển chế độ upload: ghi đè hoặc thêm file."""
        if self._is_uploading:
            return
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

    def _apply_submission_snapshot(self, snapshot: SubmissionSnapshot):
        """Replace displayed submission state with one authoritative snapshot."""
        self._submission_snapshot = snapshot
        self._submission_fingerprint = snapshot.fingerprint
        self._submission_policy = SubmissionUiPolicy.from_snapshot(snapshot)
        self._submitted_files = DetailView._submitted_file_dicts(snapshot.remote_files)
        self._last_server_status = DetailView._map_submission_status(snapshot.raw_status)
        DetailView._update_visible_submission_status(self, snapshot.raw_status)
        self._render_submission_policy()
        self._build_submitted_files_ui()

        DetailView._notify_submission_status(self, snapshot, self._current_url)

    @staticmethod
    def _map_submission_status(raw_status: str) -> str:
        return {
            "submitted": "Đã nộp",
            "draft": "Bản nháp",
            "new": "Chưa nộp",
            "graded": "Đã chấm điểm",
        }.get(raw_status, raw_status)

    def _update_visible_submission_status(self, raw_status: str):
        status = DetailView._map_submission_status(raw_status)
        colors = {
            "new": C.CRITICAL,
            "draft": C.WARNING,
            "submitted": C.SAFE,
            "graded": C.SAFE,
        }
        self._submission_status_value.value = status
        self._submission_status_value.color = colors.get(raw_status, C.TEXT_PRIMARY)
        self._submission_status_value.weight = ft.FontWeight.W_600

    def _notify_submission_status(
        self, snapshot: SubmissionSnapshot, target_url: str
    ):
        status = DetailView._map_submission_status(snapshot.raw_status)
        if target_url == self._current_url:
            self._last_server_status = status
            self._current_data["submission_status"] = status
        if status and self._on_status_changed and target_url:
            self._on_status_changed(target_url, status)

    def _apply_mutation_result(self, result: SubmissionMutationResult):
        """Render verified server truth, retaining the last truth if refresh failed."""
        if result.snapshot is not None:
            self._apply_submission_snapshot(result.snapshot)
            if result.partial:
                changed = DetailView._reconcile_selected_files(
                    self, result.snapshot
                )
                if changed and hasattr(self, "_update_file_preview"):
                    self._update_file_preview()
        color = C.SAFE if result.ok else C.CRITICAL
        self._show_upload_status(mutation_message(result), color)

    def _reconcile_selected_files(self, snapshot: SubmissionSnapshot) -> bool:
        """Drop pending picker entries already verified in a partial result."""
        verified = Counter(
            (normalize_filepath(item.filepath), item.name, item.size)
            for item in snapshot.remote_files
        )
        pending = []
        for item in self._selected_files:
            identity = (
                normalize_filepath(getattr(item, "filepath", "/") or "/"),
                getattr(item, "name", "file"),
                int(getattr(item, "size", 0) or 0),
            )
            if verified[identity]:
                verified[identity] -= 1
            else:
                pending.append(item)
        changed = len(pending) != len(self._selected_files)
        if changed:
            self._selected_files[:] = pending
        return changed

    def _selected_file_models(self) -> tuple[SelectedFile, ...]:
        selected = []
        for item in self._selected_files:
            selected.append(
                SelectedFile(
                    name=getattr(item, "name", "file"),
                    size=int(getattr(item, "size", 0) or 0),
                    mimetype=getattr(item, "mime_type", "") or "",
                    source_path=getattr(item, "path", "") or "",
                )
            )
        return tuple(selected)

    def _selected_submission_files(self) -> tuple[SelectedSubmissionFile, ...]:
        """Capture picker-owned bytes before leaving the current view turn."""
        payloads = []
        for item in self._selected_files:
            content = getattr(item, "bytes", None)
            if isinstance(content, bytes):
                payloads.append(
                    SelectedSubmissionFile(
                        name=getattr(item, "name", "file"),
                        bytes=content,
                        filepath=getattr(item, "filepath", "/") or "/",
                    )
                )
        return tuple(payloads)

    def _build_file_intent(
        self,
        operation: MutationOperation,
        *,
        finalize: bool,
        remove_identities: tuple[FileIdentity, ...] = (),
        rename_identity: FileIdentity | None = None,
        new_name: str = "",
        new_filepath: str = "/",
    ) -> FileMutationIntent:
        statement_value = getattr(self._submission_statement, "value", False)
        accepted = statement_value if isinstance(statement_value, bool) else False
        return FileMutationIntent(
            operation=operation,
            selected_files=DetailView._selected_file_models(self),
            remove_identities=remove_identities,
            rename_identity=rename_identity,
            new_name=new_name,
            new_filepath=new_filepath,
            finalize=finalize,
            accept_statement=accepted,
            expected_fingerprint=self._submission_fingerprint,
        )

    def _render_submission_policy(self):
        policy = self._submission_policy
        if policy is None:
            return
        busy = self._is_uploading
        has_selected = bool(self._selected_files)
        has_any_files = has_selected or bool(self._submitted_files)
        has_submission_content = has_any_files or bool(
            self._submission_snapshot
            and self._submission_snapshot.online_text.strip()
        )
        self._file_list_col.disabled = busy
        self._upload_mode_row.disabled = busy
        self._upload_mode_row.visible = (
            has_selected
            and bool(self._submitted_files)
            and policy.show_picker
            and not busy
        )
        self._submission_area.visible = policy.show_picker or bool(policy.edit_reason)
        self._pick_btn.visible = policy.show_picker and not busy
        self._submit_btn.visible = (
            has_selected
            and not busy
            and (policy.show_save_submission or policy.show_save_draft)
        )
        self._submit_btn.content.controls[1].value = policy.primary_action_label
        self._finalize_btn.visible = (
            has_submission_content and policy.show_finalize and not busy
        )
        self._submission_statement.visible = (
            has_submission_content and policy.show_statement and not busy
        )
        self._submission_policy_text.value = policy.edit_reason or policy.limit_text
        self._submission_policy_text.color = (
            C.WARNING if policy.edit_reason else C.TEXT_SECONDARY
        )
        self._submission_policy_text.visible = True
        self._build_submitted_files_ui()

    async def _on_submit(self, e=None):
        operation = (
            MutationOperation.REPLACE
            if self._upload_mode_overwrite
            else MutationOperation.ADD
        )
        await self._request_file_mutation(operation, finalize=False)

    async def _on_finalize(self, e=None):
        await self._request_finalize_submission()

    async def _request_finalize_submission(self):
        if self._is_uploading:
            return
        policy = self._submission_policy
        if policy is None or not policy.show_finalize:
            return
        if policy.show_statement:
            if getattr(self._submission_statement, "value", False) is not True:
                self._show_upload_status(
                    "Bạn cần xác nhận cam kết trước khi nộp bài.", C.WARNING
                )
                return
        await self._execute_finalize_submission()

    async def _request_file_mutation(
        self, operation: MutationOperation, *, finalize: bool
    ):
        if self._is_uploading:
            return
        policy = self._submission_policy
        if finalize and policy and policy.show_statement:
            if getattr(self._submission_statement, "value", False) is not True:
                self._show_upload_status(
                    "Bạn cần xác nhận cam kết trước khi nộp bài.", C.WARNING
                )
                return
        if operation is MutationOperation.REPLACE and self._submitted_files:
            self._pending_file_mutation = (operation, finalize)
            self._show_replace_confirmation()
            return
        await self._execute_file_mutation(operation, finalize)

    def _show_replace_confirmation(self):
        dialog = self._replace_confirm_dialog
        if dialog not in self._page.overlay:
            self._page.overlay.append(dialog)
        dialog.open = True
        self._page.update()

    def _close_replace_confirmation(self, e=None):
        self._replace_confirm_dialog.open = False
        self._pending_file_mutation = None
        self._page.update()

    async def _confirm_replace_mutation(self, e=None):
        pending = self._pending_file_mutation
        self._pending_file_mutation = None
        dialog = getattr(self, "_replace_confirm_dialog", None)
        if dialog is not None:
            dialog.open = False
        page = getattr(self, "_page", None)
        if page is not None:
            page.update()
        if pending is not None:
            await self._execute_file_mutation(*pending)

    async def _execute_file_mutation(
        self,
        operation: MutationOperation,
        finalize: bool,
        *,
        remove_identities: tuple[FileIdentity, ...] = (),
        rename_identity: FileIdentity | None = None,
        new_name: str = "",
        new_filepath: str = "/",
    ):
        client = self._get_client() if self._get_client else None
        data = self._current_data
        url = data.get("url", "")
        course_id = data.get("course_id")
        if not client:
            self._show_upload_status("Chưa đăng nhập. Vui lòng đăng nhập lại.", C.CRITICAL)
            return
        if not url or not course_id or "/mod/assign/" not in url:
            self._show_upload_status("Không thể xác định bài tập trên Moodle.", C.CRITICAL)
            return

        generation = self._view_generation
        target_url = url
        target_course_id = int(course_id)
        intent = self._build_file_intent(
            operation,
            finalize=finalize,
            remove_identities=remove_identities,
            rename_identity=rename_identity,
            new_name=new_name,
            new_filepath=new_filepath,
        )
        selected_files = DetailView._selected_submission_files(self)
        self._is_uploading = True
        self._upload_progress.visible = True
        self._upload_progress.value = None
        self._show_upload_status("Đang đồng bộ với Moodle...", C.TEXT_SECONDARY)
        self._render_submission_policy()
        self._page.update()
        try:
            result = await asyncio.to_thread(
                self._do_mutate_files_sync,
                client,
                target_url,
                target_course_id,
                intent,
                selected_files,
            )
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._apply_mutation_result(result)
                if result.ok:
                    self._selected_files.clear()
                    self._file_list_col.controls.clear()
                    self._file_list_col.visible = False
                    self._upload_progress.value = 1.0
            elif result.snapshot is not None:
                DetailView._notify_submission_status(
                    self, result.snapshot, target_url
                )
        except Exception:
            logger.error("Submission mutation failed unexpectedly")
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._show_upload_status(
                    "Không thể đồng bộ bài nộp với Moodle.", C.CRITICAL
                )
        finally:
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._is_uploading = False
                self._upload_progress.visible = False
                self._render_submission_policy()
                self._page.update()

    async def _execute_finalize_submission(self):
        client = self._get_client() if self._get_client else None
        data = self._current_data
        url = data.get("url", "")
        course_id = data.get("course_id")
        if not client:
            self._show_upload_status(
                "Chưa đăng nhập. Vui lòng đăng nhập lại.", C.CRITICAL
            )
            return
        if not url or not course_id:
            self._show_upload_status(
                "Không thể xác định bài tập trên Moodle.", C.CRITICAL
            )
            return

        generation = self._view_generation
        target_url = url
        target_course_id = int(course_id)
        finalize_intent = FinalizeSubmissionIntent(
            accept_statement=(
                getattr(self._submission_statement, "value", False) is True
            ),
            expected_fingerprint=self._submission_fingerprint,
        )
        self._is_uploading = True
        self._upload_progress.visible = True
        self._upload_progress.value = None
        self._show_upload_status("Đang đồng bộ với Moodle...", C.TEXT_SECONDARY)
        self._render_submission_policy()
        self._page.update()
        try:
            result = await asyncio.to_thread(
                self._do_finalize_submission_sync,
                client,
                target_url,
                target_course_id,
                finalize_intent,
            )
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._apply_mutation_result(result)
                if result.ok:
                    self._upload_progress.value = 1.0
            elif result.snapshot is not None:
                DetailView._notify_submission_status(
                    self, result.snapshot, target_url
                )
        except Exception:
            logger.error("Submission finalization failed unexpectedly")
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._show_upload_status(
                    "Không thể đồng bộ bài nộp với Moodle.", C.CRITICAL
                )
        finally:
            if self._is_current_mutation_context(
                generation, target_url, target_course_id
            ):
                self._is_uploading = False
                self._upload_progress.visible = False
                self._render_submission_policy()
                self._page.update()

    def _is_current_mutation_context(
        self, generation: int, target_url: str, target_course_id: int
    ) -> bool:
        try:
            current_course_id = int(self._current_data.get("course_id"))
        except (TypeError, ValueError):
            return False
        return (
            self._view_generation == generation
            and self._current_url == target_url
            and current_course_id == target_course_id
        )

    def _do_mutate_files_sync(
        self,
        client,
        url: str,
        course_id: int,
        intent: FileMutationIntent,
        selected_files: tuple[SelectedSubmissionFile, ...] = (),
    ) -> SubmissionMutationResult:
        return self._submission_workflow(client).mutate_files(
            self._submission_target(url, course_id),
            intent,
            selected_files=selected_files,
        )

    def _do_finalize_submission_sync(
        self,
        client,
        url: str,
        course_id: int,
        intent: FinalizeSubmissionIntent,
    ) -> SubmissionMutationResult:
        return self._submission_workflow(client).finalize_submission(
            self._submission_target(url, course_id), intent
        )

    def _show_upload_status(self, text: str, color: str):
        """Hiện thông báo trạng thái upload."""
        self._upload_status.value = text
        self._upload_status.color = color
        self._upload_status.visible = True

    async def _async_load_submitted_files(self, client, url: str, course_id: int, prefetched_status: Optional[dict] = None):
        """Async wrapper: load submitted files in bg thread, then update UI."""
        self._snapshot_load_generation += 1
        load_generation = self._snapshot_load_generation
        view_generation = self._view_generation
        try:
            result = await asyncio.to_thread(
                self._load_submission_snapshot,
                client,
                url,
                course_id,
                prefetched_status,
            )
            if not self._is_current_snapshot_load(
                load_generation,
                view_generation,
                url,
                course_id,
            ):
                return
            if result.ok and result.snapshot is not None:
                self._apply_submission_snapshot(result.snapshot)
            elif result.issue is not None:
                failed = SubmissionMutationResult.failure(result.issue, None)
                self._show_upload_status(mutation_message(failed), C.CRITICAL)
            self._page.update()
        except Exception:
            logger.exception("Load submitted snapshot failed unexpectedly")
            if not self._is_current_snapshot_load(
                load_generation,
                view_generation,
                url,
                course_id,
            ):
                return
            self._show_upload_status(
                "Không thể kiểm tra trạng thái bài nộp mới nhất.", C.CRITICAL
            )
            self._page.update()

    def _is_current_snapshot_load(
        self,
        load_generation: int,
        view_generation: int,
        url: str,
        course_id: int,
    ) -> bool:
        try:
            current_course_id = int(self._current_data.get("course_id"))
        except (TypeError, ValueError):
            return False
        return (
            self._snapshot_load_generation == load_generation
            and self._view_generation == view_generation
            and self._current_url == url
            and current_course_id == course_id
        )

    def _load_submission_snapshot(self, client, url: str, course_id: int,
                                  prefetched_status: Optional[dict] = None):
        return self._submission_workflow(client).load_snapshot(
            target=self._submission_target(url, course_id),
            prefetched_status=prefetched_status,
        )

    def _build_submitted_files_ui(self):
        policy = self._submission_policy
        show_actions = bool(
            policy and policy.show_file_actions and not self._is_uploading
        )
        build_submitted_files_ui(self, show_file_actions=show_actions)

    async def _on_remove_submitted_files(self, indices: list):
        """Remove the selected server identities through the verified workflow."""
        if self._is_uploading:
            return
        valid = sorted({i for i in indices if 0 <= i < len(self._submitted_files)})
        identities = tuple(
            (
                self._submitted_files[i].get("filepath", "/"),
                self._submitted_files[i].get("name", ""),
            )
            for i in valid
        )
        if not identities:
            return
        operation = (
            MutationOperation.CLEAR
            if len(identities) == len(self._submitted_files)
            else MutationOperation.REMOVE
        )
        await self._execute_file_mutation(
            operation,
            False,
            remove_identities=identities,
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
        if len(self._submitted_files) == 1:
            self._delete_confirm_text.value = (
                "Bạn đang xóa toàn bộ file. Moodle sẽ lưu một bản ghi bài nộp "
                f"không có file.\n  • {name}"
            )
        else:
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
        deleting_all = len(indices) == len(self._submitted_files)
        if deleting_all:
            file_list = "\n".join(f"  • {n}" for n in names)
            self._delete_confirm_text.value = (
                "Bạn đang xóa toàn bộ file. Moodle sẽ lưu một bản ghi bài nộp "
                f"không có file.\n{file_list}"
            )
        elif len(names) == 1:
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
        if not self._submission_policy or not self._submission_policy.show_file_actions:
            return
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
        """Rename or move a submitted file through the verified workflow."""
        idx = self._editing_file_index
        if idx < 0 or idx >= len(self._submitted_files):
            return
        if self._is_uploading:
            return

        old_file = self._submitted_files[idx]
        new_name = (self._edit_filename.value or '').strip()
        new_filepath = (self._edit_filepath.value or '/').strip()

        if not new_name:
            self._edit_status.value = "Tên file không được trống"
            self._edit_status.color = C.WARNING
            self._edit_status.visible = True
            self._page.update()
            return

        name_changed = new_name != old_file.get('name', '')
        path_changed = new_filepath != old_file.get('filepath', '/')

        if not name_changed and not path_changed:
            self._close_edit_dialog()
            return
        self._file_edit_dialog.open = False
        await self._execute_file_mutation(
            MutationOperation.RENAME,
            False,
            rename_identity=(
                old_file.get("filepath", "/"),
                old_file.get("name", ""),
            ),
            new_name=new_name,
            new_filepath=new_filepath,
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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # Text Fields & Dropdowns (Edit metadata dialog)
        _edit_fields = [
            self._edit_filename, self._edit_filepath
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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

        # If data is currently shown, refresh dynamic content
        if self._current_data:
            self.update_detail(self._current_data)

        try:
            self.update()
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    # Apply compatibility shims if running directly
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        import logging as _fb_log
        _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
    from gui.app_controller import AppController
    AppController(page)

if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))
