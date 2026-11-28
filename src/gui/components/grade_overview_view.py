"""Grade Overview View - Academic Radar & Course Transcript Analysis.

Displays a 6-axis Academic Radar character-stat profile, semester KPIs,
interactive metric detail modals, and comprehensive course grades.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import math
import os
import sys
from typing import Any, Awaitable, Callable, Optional

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.gpa_planner import CreditProgress
from core.academic_radar.models import RadarProfile, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from core.academic_radar.overall import OverallGpa, calculate_overall_gpa
from gui.components.academic_radar.course_components_dialog import (
    MoodleCourseGradesResult,
    show_course_components_dialog,
)
from gui.components.academic_radar.expected_grade_dialog import ExpectedGradeDialog
from gui.components.academic_radar.gpa_detail_sheet import GpaDetailSheet
from gui.components.academic_radar.grade_list_view import AcademicGradeListView
from gui.components.course_grade_table import find_moodle_course
from gui.components.academic_radar.gpa_target_card import GpaTargetCard
from gui.components.academic_radar.graduation_gpa_card import GraduationGpaCard
from gui.components.academic_radar.radar_card import AcademicRadarCard
from gui.core.theme import C, refresh_semantic_control_colors, semantic_palette

logger = logging.getLogger(__name__)


class GradeOverviewView(ft.Container):
    """Modern Academic Radar and Course Grades slide-in panel."""

    def __init__(
        self,
        on_close: Optional[Callable] = None,
        on_refresh: Optional[Callable] = None,
        page: Optional[ft.Page] = None,
        moodle_courses_provider: Optional[Callable[[], list]] = None,
        on_open_courses: Optional[Callable] = None,
        course_grades_lookup: Optional[
            Callable[[Any, str], MoodleCourseGradesResult]
        ] = None,
        course_grades_async_lookup: Optional[
            Callable[[Any, str], Awaitable[MoodleCourseGradesResult]]
        ] = None,
        on_telemetry_interaction: Optional[Callable[[str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._moodle_courses_provider = moodle_courses_provider
        self._course_grades_lookup = course_grades_lookup
        self._course_grades_async_lookup = course_grades_async_lookup
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._on_open_courses = on_open_courses
        self._on_telemetry_interaction = on_telemetry_interaction
        self._page_ref = page
        self._semesters: list[SemesterGradeData] = []
        self._profiles: list[RadarProfile] = []
        self._selected_semester_idx = 0
        self._defer_self_update = False
        self._viewport_width: float = 0.0
        self._saved_scroll_offset: float = 0.0
        self._open_dialog_tokens: dict[int, float] = {}
        self._next_dialog_token: int = 0
        self._scroll_restore_token: int = 0
        self._is_restoring_scroll: bool = False
        self._in_scroll_to_call: bool = False
        self._restore_in_post_cooldown: bool = False
        self._pending_post_restore_pin: bool = False
        self._dialog_closers: dict[int, Callable[[], None]] = {}
        self._active_dialog_token: Optional[int] = None
        self._active_dialog_closer: Optional[Callable[[], None]] = None

        try:
            page_w = float(getattr(self._get_page(), "width", 0) or 0)
        except (TypeError, ValueError):
            page_w = 0.0

        # 1. Header (Back button on left, Centered Title in middle, Refresh on right)
        btn_constraints = ft.BoxConstraints(
            min_width=44, max_width=44, min_height=44, max_height=44
        )
        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Quay lại",
            padding=0,
            size_constraints=btn_constraints,
            on_click=self._handle_close,
        )
        self._title = ft.Text(
            "Bảng điểm",
            size=15 if 0 < page_w < 360 else 16,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.CENTER,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            no_wrap=True,
            expand=True,
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Làm mới bảng điểm từ Portal",
            padding=0,
            size_constraints=btn_constraints,
            on_click=self._handle_refresh,
        )
        self._courses_btn = ft.TextButton(
            "Môn học",
            height=44,
            on_click=self._handle_open_courses,
            style=ft.ButtonStyle(
                color=C.ACCENT,
                padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            ),
        )
        self._planner_btn = ft.IconButton(
            icon=ft.Icons.CALCULATE_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Tính GPA mong muốn",
            padding=0,
            size_constraints=btn_constraints,
            on_click=self._open_planner,
        )
        self._header = ft.Row(
            controls=[
                ft.Semantics(label="Quay lại", button=True, content=self._back_btn),
                self._title,
                ft.Semantics(label="Môn học", button=True, content=self._courses_btn),
                ft.Semantics(label="Tính GPA mong muốn", button=True, content=self._planner_btn),
                ft.Semantics(label="Làm mới bảng điểm từ Portal", button=True, content=self._refresh_btn),
            ],
            spacing=2,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # 2. Loading indicator
        self._loading = ft.ProgressRing(
            width=24, height=24, stroke_width=3, color=C.ACCENT,
        )
        self._loading_text = ft.Text(
            "Đang tải hồ sơ học tập...", size=13, color=C.TEXT_SECONDARY
        )
        self._loading_row = ft.Row(
            controls=[self._loading, self._loading_text],
            alignment=ft.MainAxisAlignment.CENTER,
            visible=False,
        )

        # 3. Empty & Error states
        self._empty_text = ft.Text(
            "Chưa có dữ liệu bảng điểm.\nRadar học tập sẽ xuất hiện khi có kết quả học phần.",
            size=13,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            content=self._empty_text,
            alignment=ft.Alignment(0, 0),
            padding=40,
            visible=False,
        )
        self._error_icon = ft.Icon(
            ft.Icons.CLOUD_OFF_ROUNDED, size=36, color=C.CRITICAL
        )
        self._error_text = ft.Text(
            "Không tải được bảng điểm từ Portal.\nKiểm tra kết nối/tài khoản rồi thử lại.",
            size=13,
            color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._error_retry_btn = ft.Button(
            "Thử lại",
            icon=ft.Icons.REFRESH_ROUNDED,
            bgcolor=C.ACCENT,
            color=C.ON_ACCENT,
            height=44,
            on_click=self._handle_refresh,
        )
        self._error_state = ft.Container(
            content=ft.Column(
                controls=[
                    self._error_icon,
                    self._error_text,
                    self._error_retry_btn,
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            ),
            alignment=ft.Alignment(0, 0),
            padding=32,
            visible=False,
        )

        self._stale_icon = ft.Icon(
            ft.Icons.CLOUD_OFF_ROUNDED, size=15, color=C.WARNING
        )
        self._stale_text = ft.Text(
            "", size=11.5, color=C.TEXT_PRIMARY, expand=True
        )
        self._stale_banner = ft.Container(
            content=ft.Row(
                controls=[self._stale_icon, self._stale_text],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.SURFACE_ELEVATED,
            border=ft.Border.all(1, C.WARNING),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=10, vertical=7),
            visible=False,
        )

        # 4. Academic Radar Card & Grade List
        self._radar_card = AcademicRadarCard(
            profiles=[],
            on_semester_change=self._on_semester_changed,
            page=self._get_page(),
            on_before_dialog=self._begin_modal_scroll_guard,
            on_dialog_dismiss=self._end_modal_scroll_guard,
            on_dialog_opened=self._on_child_dialog_opened,
            on_dialog_closing=self._mark_dialog_closing,
            visible=False,
        )
        orig_metric_click = getattr(self._radar_card, "_on_metric_click", None)
        if callable(orig_metric_click):
            def _wrapped_metric_click(*m_args: Any, **m_kwargs: Any) -> Any:
                self._emit_telemetry("radar_metric_opened")
                return orig_metric_click(*m_args, **m_kwargs)

            self._radar_card._on_metric_click = _wrapped_metric_click
        # Form tính GPA mong muốn mở bằng nút trên header (dialog)
        self._gpa_target_card = GpaTargetCard()
        self._graduation_card = GraduationGpaCard()
        self._progress: Optional[CreditProgress] = None
        self._planner_dialog: Optional[ft.AlertDialog] = None
        self._grade_list_view = AcademicGradeListView(courses=[], on_course_click=self._on_course_clicked, visible=False)

        # Main scrollable body
        self._scrollable_content = ft.Column(
            controls=[
                self._radar_card,
                self._grade_list_view,
            ],
            key="grade_overview_scroll",
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            auto_scroll=False,
            scroll_interval=10,
            on_scroll=self._on_content_scroll,
            expand=True,
        )

        self._header_divider = ft.Divider(height=1, color=C.BORDER)
        self.content = ft.Column(
            controls=[
                self._header,
                self._header_divider,
                self._stale_banner,
                self._loading_row,
                self._empty_state,
                self._error_state,
                self._scrollable_content,
            ],
            spacing=8,
            expand=True,
        )

        self.bgcolor = C.BG
        self.padding = ft.Padding.only(left=16, right=16, top=20, bottom=16)
        self.expand = True
        self.visible = False
        self.offset = ft.Offset(1, 0)
        self.opacity = 0.0
        self.animate_offset = ft.Animation(250, ft.AnimationCurve.EASE_OUT)
        self.animate_opacity = ft.Animation(200)
        self._rendered_palette = semantic_palette()

    def _get_page(self) -> Optional[ft.Page]:
        if self._page_ref is not None:
            return self._page_ref
        try:
            return self.page
        except Exception:
            return None

    def set_width(self, width: float) -> None:
        """Update layout metrics when the host viewport width changes."""
        try:
            numeric = float(width or 0)
        except (TypeError, ValueError):
            return
        if numeric <= 0:
            return
        self._viewport_width = numeric
        target_title_size = 15 if numeric < 360 else 16
        if self._title.size != target_title_size:
            self._title.size = target_title_size
        self._radar_card.set_viewport_width(numeric)

    def _has_rendered_data(self) -> bool:
        return bool(self._semesters and self._profiles)

    def show_loading(self) -> None:
        """Shows loading state while keeping existing rendered radar/courses intact on refresh."""
        has_data = self._has_rendered_data()
        self._stale_banner.visible = False
        self._loading_row.visible = True
        self._empty_state.visible = False
        self._error_state.visible = False
        self._radar_card.visible = has_data
        self._grade_list_view.visible = has_data
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        if self._defer_self_update:
            return
        try:
            self.update()
        except Exception:
            pass

    def show_error(self, message: str = "") -> None:
        """Shows an explicit error state, preserving already-rendered radar/semester data on refresh."""
        resolved = (
            message
            if message
            else "Không tải được bảng điểm từ Portal.\nKiểm tra kết nối/tài khoản rồi thử lại."
        )
        self._loading_row.visible = False
        self._empty_state.visible = False
        self._error_text.value = resolved
        if self._has_rendered_data():
            self._radar_card.visible = True
            self._grade_list_view.visible = True
            self._error_state.visible = False
            self._stale_text.value = " · ".join(
                part.strip() for part in resolved.splitlines() if part.strip()
            )
            self._stale_banner.visible = True
        else:
            self._stale_banner.visible = False
            self._radar_card.visible = False
            self._grade_list_view.visible = False
            self._error_state.visible = True
        if self._defer_self_update:
            return
        try:
            self.update()
        except Exception:
            pass

    def reset(self) -> None:
        """Clear all account-specific transcript, radar, planner, and modal state."""
        open_closers = list(reversed(list(self._dialog_closers.values())))
        self._dialog_closers.clear()
        self._open_dialog_tokens.clear()
        self._active_dialog_token = None
        self._active_dialog_closer = None
        self._scroll_restore_token += 1
        self._is_restoring_scroll = False
        self._in_scroll_to_call = False
        self._restore_in_post_cooldown = False
        self._pending_post_restore_pin = False
        self._saved_scroll_offset = 0.0
        for closer in open_closers:
            try:
                closer()
            except Exception:
                logger.debug("GradeOverview dialog close on reset failed", exc_info=True)
        self._planner_dialog = None
        self._gpa_target_card = GpaTargetCard()
        self._graduation_card = GraduationGpaCard()
        self._semesters = []
        self._profiles = []
        self._progress = None
        self._selected_semester_idx = 0
        self._stale_text.value = ""
        self._stale_banner.visible = False
        self._loading_row.visible = False
        self._error_state.visible = False
        self._empty_state.visible = False
        self._radar_card.visible = False
        self._grade_list_view.visible = False
        self._radar_card.set_profiles([], selected_index=0)
        self._grade_list_view.update_courses([])
        if self._defer_self_update:
            return
        try:
            self.update()
        except Exception:
            pass

    clear = reset

    def update_academic_data(
        self,
        semesters: list[SemesterGradeData],
        profiles: list[RadarProfile],
        progress: Optional[CreditProgress] = None,
    ) -> None:
        """Populates the view with full academic transcript and computed radar profiles."""
        self._stale_banner.visible = False
        self._loading_row.visible = False
        self._error_state.visible = False
        self._semesters = semesters
        self._profiles = profiles
        self._selected_semester_idx = 0
        self._saved_scroll_offset = 0.0
        self._open_dialog_tokens.clear()
        self._dialog_closers.clear()
        self._scroll_restore_token += 1
        self._is_restoring_scroll = False
        self._in_scroll_to_call = False
        self._restore_in_post_cooldown = False
        self._pending_post_restore_pin = False
        self._active_dialog_token = None
        self._active_dialog_closer = None

        if not profiles or not semesters:
            self._empty_state.visible = True
            self._radar_card.visible = False
            self._grade_list_view.visible = False
            if self._defer_self_update:
                return
            try:
                self.update()
            except Exception:
                pass
            return

        self._empty_state.visible = False
        self._error_state.visible = False
        self._radar_card.visible = True
        if progress is not None:
            self._progress = progress
        self._grade_list_view.visible = True

        self._radar_card._page_ref = self._get_page()
        self._radar_card.set_profiles(profiles, selected_index=0)

        current_courses = semesters[0].courses if semesters else []
        self._grade_list_view.update_courses(current_courses)

        if self._defer_self_update:
            return
        try:
            self.update()
        except Exception:
            pass

    def update_grades(
        self,
        courses_grades: list[dict[str, Any]],
        grade_items: dict[str, list[dict]],
    ) -> None:
        """Backward-compatibility bridge for raw Moodle grade responses."""
        if not courses_grades:
            self.update_academic_data([], [])
            return

        # Normalize Moodle courses into standard SemesterGradeData
        normalizer = SemesterNormalizer()
        raw_courses = []
        for cg in courses_grades:
            cid = str(cg.get("courseid", ""))
            cname = cg.get("coursename", f"Course {cid}")
            g_str = cg.get("grade", "")
            try:
                final_g = float(str(g_str).replace(",", "."))
            except (ValueError, TypeError):
                final_g = None

            raw_courses.append({
                "id": cid,
                "tenMonHoc": cname,
                "soTinChi": 3,
                "diemTongKet": final_g,
                "diemTBThuongKy": final_g,
                "diemThi": final_g,
            })

        sem_data = normalizer.normalize_semester({
            "id": 1,
            "tenDot": "Bảng điểm hiện tại",
            "courses": raw_courses,
        })
        profile = calculate_radar_profile(sem_data)
        self.update_academic_data([sem_data], [profile])

    def set_credit_progress(self, progress: Optional[CreditProgress]) -> None:
        """Cập nhật số tín chỉ ra trường (tải sau khi bảng điểm đã hiện)."""
        self._progress = progress
        self._graduation_card.set_data(self._semesters, progress)

    def _notify(self, page: Optional[ft.Page], message: str) -> None:
        if page is None:
            return
        sb = ft.SnackBar(
            content=ft.Text(message, size=13, color=C.TEXT_PRIMARY),
            bgcolor=C.SURFACE,
            duration=3000,
        )
        overlay = getattr(page, "overlay", None)
        if isinstance(overlay, list):
            overlay[:] = [
                item
                for item in overlay
                if not (isinstance(item, ft.SnackBar) and not getattr(item, "open", False))
            ]
            overlay.append(sb)
            sb.open = True
            try:
                page.update()
            except Exception:
                logger.debug("Snackbar overlay update failed", exc_info=True)
            return
        try:
            page.show_dialog(sb)
        except Exception:
            logger.debug("Snackbar failed", exc_info=True)

    def _open_planner(self, e: ft.ControlEvent | None = None) -> None:
        """Mở form tính GPA mong muốn (trong kỳ + ra trường)."""
        logger.info("GPA planner button clicked (semesters=%d)", len(self._semesters))
        page = self._get_page()
        if page is None:
            try:
                page = e.control.page if e is not None else None
            except Exception:
                page = None
        try:
            if not self._semesters:
                self._notify(page, "Chưa có dữ liệu bảng điểm. Hãy đợi tải xong hoặc bấm làm mới.")
                return
            if page is None:
                logger.error("Planner dialog: no page available")
                return
            self._show_planner_dialog(page)
        except Exception as exc:
            logger.exception("Failed to open GPA planner dialog")
            self._notify(page, f"Không mở được form tính GPA: {exc}")

    def _show_planner_dialog(self, page: ft.Page) -> None:
        # Dựng thẻ mới theo theme hiện tại, giữ lại số đã nhập lần trước
        sem = (
            self._semesters[self._selected_semester_idx]
            if 0 <= self._selected_semester_idx < len(self._semesters)
            else self._semesters[0]
        )
        self._gpa_target_card = GpaTargetCard(target_text=self._gpa_target_card._target_text)
        self._gpa_target_card.set_semester(sem)
        self._graduation_card = GraduationGpaCard(target_text=self._graduation_card._target_text)
        self._graduation_card.set_data(self._semesters, self._progress)

        # Khung cuộn phải có chiều cao giới hạn, nếu không Flutter không dựng được dialog
        try:
            page_h = float(getattr(page, "height", None) or 0)
        except (TypeError, ValueError):
            page_h = 0.0
        body_h = max(260.0, min(520.0, page_h * 0.65)) if page_h else 460.0
        try:
            page_w = float(getattr(page, "width", None) or 0)
        except (TypeError, ValueError):
            page_w = 0.0
        is_narrow = 0 < page_w < 600
        body_w = max(240.0, min(440.0, page_w - 64.0)) if page_w else 440.0

        token = self._begin_modal_scroll_guard()
        used_overlay = False
        is_real_page = isinstance(page, ft.Page)

        def _close(_event=None) -> None:
            self._mark_dialog_closing(token)
            popped = self._close_planner(page, dialog, used_overlay=used_overlay)
            if not popped or not is_real_page:
                self._end_modal_scroll_guard(token)

        def _on_key_down(event) -> None:
            if getattr(event, "key", "") == "Escape":
                _close()

        dialog = ft.AlertDialog(
            modal=True,
            semantics_label="Tính GPA mong muốn",
            title=ft.Text(
                "Tính GPA mong muốn",
                size=16,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
                semantics_label="Tính GPA mong muốn",
            ),
            content=ft.KeyboardListener(
                autofocus=False,
                on_key_down=_on_key_down,
                content=ft.Container(
                    content=ft.Column(
                        controls=[self._gpa_target_card, self._graduation_card],
                        spacing=12,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    width=body_w,
                    height=body_h,
                ),
            ),
            actions=[ft.TextButton("Đóng", height=44, on_click=_close)],
            on_dismiss=lambda _e=None, t=token: self._end_modal_scroll_guard(t),
            inset_padding=ft.Padding.symmetric(horizontal=16 if is_narrow else 40, vertical=24),
            title_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=16, bottom=4),
            content_padding=ft.Padding.only(left=16 if is_narrow else 24, right=16 if is_narrow else 24, top=12, bottom=8),
            actions_padding=ft.Padding.only(left=16, right=16, top=0, bottom=8),
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )
        self._planner_dialog = dialog
        shown_ok = True
        try:
            page.show_dialog(dialog)
            logger.info("GPA planner dialog shown")
        except Exception:
            logger.warning("page.show_dialog failed; falling back to overlay", exc_info=True)
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                used_overlay = True
                overlay.append(dialog)
                dialog.open = True
                try:
                    page.update()
                except Exception:
                    pass
            else:
                shown_ok = False
        if shown_ok:
            self._set_active_dialog_closer(token, _close)
            self._pin_scroll_while_modal_open(token)
            self._emit_telemetry("gpa_planner_opened")
        else:
            self._open_dialog_tokens.pop(token, None)

    def open_gpa_detail_sheet(self, profile: Optional[RadarProfile] = None) -> None:
        """Mở hộp thoại chi tiết GPA học kỳ/tích lũy kèm khóa cuộn nền."""
        page = self._get_page()
        resolved_profile = profile or (
            self._profiles[self._selected_semester_idx]
            if 0 <= self._selected_semester_idx < len(self._profiles)
            else (self._profiles[0] if self._profiles else None)
        )
        if page is None or resolved_profile is None:
            return
        token = self._begin_modal_scroll_guard()
        closer = GpaDetailSheet.show(
            page,
            resolved_profile,
            on_dismiss=lambda t=token: self._end_modal_scroll_guard(t),
            on_closing=lambda t=token: self._mark_dialog_closing(t),
        )
        if closer is not None:
            self._set_active_dialog_closer(token, closer)
            self._pin_scroll_while_modal_open(token)
        else:
            self._open_dialog_tokens.pop(token, None)

    def open_expected_grade_dialog(self, overall: Optional[OverallGpa] = None) -> None:
        """Mở hộp thoại tính điểm kỳ vọng (có thể mở lồng trên hộp thoại khác) kèm khóa cuộn nền."""
        page = self._get_page()
        if page is None:
            return
        resolved_overall = (
            overall
            if overall is not None
            else (calculate_overall_gpa(self._semesters) if self._semesters else None)
        )
        token = self._begin_modal_scroll_guard()
        closer = ExpectedGradeDialog.show(
            page,
            resolved_overall,
            on_dismiss=lambda t=token: self._end_modal_scroll_guard(t),
            on_closing=lambda t=token: self._mark_dialog_closing(t),
        )
        if closer is not None:
            self._set_active_dialog_closer(token, closer)
            self._pin_scroll_while_modal_open(token)
        else:
            self._open_dialog_tokens.pop(token, None)

    def _close_planner(
        self, page: ft.Page, dialog: ft.AlertDialog, *, used_overlay: bool = False
    ) -> bool:
        if not used_overlay:
            try:
                page.pop_dialog()
                return True
            except Exception:
                pass
        dialog.open = False
        try:
            dialog.update()
        except Exception:
            try:
                page.update()
            except Exception:
                pass
        return False

    def _pinned_modal_offset(self) -> Optional[float]:
        if not self._open_dialog_tokens:
            return None
        return next(iter(self._open_dialog_tokens.values()))

    def _pin_scroll_while_modal_open(self, token: Any) -> None:
        if not isinstance(token, int) or token not in self._open_dialog_tokens:
            return
        pinned = self._pinned_modal_offset()
        if pinned is not None and pinned > 0.0:
            self._schedule_scroll_restore(pinned)

    def _on_content_scroll(self, event: Any) -> None:
        if self._in_scroll_to_call:
            return
        if self._is_restoring_scroll and not self._restore_in_post_cooldown:
            return
        raw_pixels = getattr(event, "pixels", None)
        if raw_pixels is None:
            return
        try:
            pixels = float(raw_pixels)
            minimum = float(getattr(event, "min_scroll_extent", 0.0) or 0.0)
        except (TypeError, ValueError):
            return
        if not math.isfinite(pixels) or not math.isfinite(minimum):
            return
        current_pixels = max(0.0, pixels - minimum)
        if self._is_restoring_scroll:
            pinned_cooldown = self._pinned_modal_offset()
            if pinned_cooldown is not None and abs(current_pixels - pinned_cooldown) > 2.0:
                self._pending_post_restore_pin = True
            return
        pinned = self._pinned_modal_offset()
        if pinned is not None:
            if abs(current_pixels - pinned) > 2.0:
                self._schedule_scroll_restore(pinned)
            return
        out_of_range = bool(getattr(event, "out_of_range", False))
        offset = current_pixels
        if (out_of_range and pixels <= minimum) or offset <= 5.0:
            offset = 0.0
        self._saved_scroll_offset = offset

    def _begin_modal_scroll_guard(self) -> int:
        pinned = self._pinned_modal_offset()
        captured = self._saved_scroll_offset if pinned is None else pinned
        self._next_dialog_token += 1
        token = self._next_dialog_token
        self._open_dialog_tokens[token] = captured
        return token

    def _sync_top_dialog_closer(self) -> None:
        if self._dialog_closers:
            top_token, top_closer = next(reversed(self._dialog_closers.items()))
            self._active_dialog_token = top_token
            self._active_dialog_closer = top_closer
        else:
            self._active_dialog_token = None
            self._active_dialog_closer = None

    def _on_child_dialog_opened(
        self, token: int, closer: Optional[Callable[[], None]]
    ) -> None:
        self._set_active_dialog_closer(token, closer)
        self._pin_scroll_while_modal_open(token)

    def _set_active_dialog_closer(
        self, token: int, closer: Optional[Callable[[], None]]
    ) -> None:
        if isinstance(token, int) and callable(closer):
            self._dialog_closers.pop(token, None)
            self._dialog_closers[token] = closer
        elif isinstance(token, int):
            self._dialog_closers.pop(token, None)
        self._sync_top_dialog_closer()

    def _mark_dialog_closing(self, token: Any) -> None:
        if isinstance(token, int):
            self._dialog_closers.pop(token, None)
        self._sync_top_dialog_closer()

    def _end_modal_scroll_guard(self, token: Any) -> None:
        if not isinstance(token, int):
            return
        self._dialog_closers.pop(token, None)
        self._sync_top_dialog_closer()
        captured_offset = self._open_dialog_tokens.pop(token, None)
        if captured_offset is None:
            return

        if not self._open_dialog_tokens and captured_offset > 0.0:
            self._schedule_scroll_restore(captured_offset)

    def _schedule_scroll_restore(self, target_offset: float) -> None:
        self._scroll_restore_token += 1
        restore_token = self._scroll_restore_token
        self._is_restoring_scroll = True
        self._restore_in_post_cooldown = False
        self._pending_post_restore_pin = False
        self._saved_scroll_offset = target_offset

        async def _invoke_scroll(offset_val: float) -> None:
            scroll_fn = getattr(self._scrollable_content, "scroll_to", None)
            if not callable(scroll_fn):
                return
            self._in_scroll_to_call = True
            try:
                res = scroll_fn(offset=offset_val, duration=0)
                if inspect.isawaitable(res):
                    await res
            finally:
                self._in_scroll_to_call = False

        async def _restore() -> None:
            try:
                await asyncio.sleep(0.03)
                if restore_token != self._scroll_restore_token:
                    return
                await _invoke_scroll(target_offset)
                if restore_token != self._scroll_restore_token:
                    return
                self._pending_post_restore_pin = False
                self._restore_in_post_cooldown = True
                await asyncio.sleep(0.02)
                self._restore_in_post_cooldown = False
                if (
                    restore_token == self._scroll_restore_token
                    and self._pending_post_restore_pin
                ):
                    self._pending_post_restore_pin = False
                    followup = self._pinned_modal_offset()
                    if followup is not None:
                        await _invoke_scroll(followup)
            except Exception:
                logger.debug("GradeOverview scroll restore failed", exc_info=True)
            finally:
                if restore_token == self._scroll_restore_token:
                    self._restore_in_post_cooldown = False
                    self._pending_post_restore_pin = False
                    self._is_restoring_scroll = False

        page = self._get_page()
        run_task = getattr(page, "run_task", None)
        if callable(run_task):
            try:
                run_task(_restore)
                return
            except Exception:
                logger.debug("page.run_task scroll restore failed", exc_info=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            scroll_fn = getattr(self._scrollable_content, "scroll_to", None)
            if callable(scroll_fn) and not inspect.iscoroutinefunction(scroll_fn):
                self._in_scroll_to_call = True
                try:
                    res = scroll_fn(offset=target_offset, duration=0)
                    if inspect.isawaitable(res):
                        close_fn = getattr(res, "close", None)
                        if callable(close_fn):
                            close_fn()
                except Exception:
                    pass
                finally:
                    self._in_scroll_to_call = False
            self._is_restoring_scroll = False
            return
        loop.create_task(_restore())

    def handle_back(self) -> bool:
        """Close the topmost open modal dialog before navigating away from GradeOverviewView."""
        if self._dialog_closers:
            top_token, closer = next(reversed(self._dialog_closers.items()))
            self._dialog_closers.pop(top_token, None)
            self._sync_top_dialog_closer()
            closer()
            return True
        closer = self._active_dialog_closer
        if callable(closer):
            self._active_dialog_token = None
            self._active_dialog_closer = None
            closer()
            return True
        return False

    def _on_course_clicked(self, e: ft.ControlEvent | None, course) -> None:
        """Bấm vào môn -> popup 2 tab (Portal | Course)."""
        page = self._get_page()
        if page is None and e is not None:
            try:
                page = e.control.page
            except Exception:
                page = None
        sem_name = (
            self._semesters[self._selected_semester_idx].name
            if 0 <= self._selected_semester_idx < len(self._semesters)
            else ""
        )
        moodle_course = None
        moodle_result: Optional[MoodleCourseGradesResult] = None
        if self._course_grades_lookup is not None:
            try:
                moodle_result = self._course_grades_lookup(course, sem_name)
            except Exception:
                logger.debug("Course grades lookup failed", exc_info=True)
        elif self._moodle_courses_provider is not None:
            try:
                moodle_course = find_moodle_course(course, self._moodle_courses_provider() or [])
            except Exception:
                logger.debug("Moodle course lookup failed", exc_info=True)

        async_loader = (
            (lambda c, s=sem_name: self._course_grades_async_lookup(c, s))
            if callable(self._course_grades_async_lookup)
            else None
        )

        token = self._begin_modal_scroll_guard()
        closer = show_course_components_dialog(
            page,
            course,
            moodle_course,
            moodle_result=moodle_result,
            async_loader=async_loader,
            on_dismiss=lambda t=token: self._end_modal_scroll_guard(t),
            on_closing=lambda t=token: self._mark_dialog_closing(t),
            on_tab_change=lambda _idx, t=token: self._pin_scroll_while_modal_open(t),
        )
        if closer is not None:
            self._set_active_dialog_closer(token, closer)
            self._pin_scroll_while_modal_open(token)
            self._emit_telemetry("course_popup_opened")
        else:
            self._open_dialog_tokens.pop(token, None)

    def _emit_telemetry(self, action: str) -> None:
        cb = getattr(self, "_on_telemetry_interaction", None)
        if callable(cb):
            try:
                cb(action)
            except Exception:
                pass

    def _on_semester_changed(self, index: int) -> None:
        self._selected_semester_idx = index
        self._saved_scroll_offset = 0.0
        if 0 <= index < len(self._semesters):
            self._grade_list_view.update_courses(self._semesters[index].courses)
        self._emit_telemetry("semester_switched")

    def _handle_refresh(self, e: ft.ControlEvent) -> None:
        if self._on_refresh:
            self._on_refresh()

    def _handle_open_courses(self, e: ft.ControlEvent | None = None) -> None:
        if self._on_open_courses:
            self._on_open_courses()

    def _handle_close(self, e: ft.ControlEvent) -> None:
        if self._on_close:
            self._on_close()

    def hide(self) -> None:
        """Hides the view with animation."""
        self.offset = ft.Offset(1, 0)
        self.opacity = 0.0

    def update_theme(self) -> None:
        """Updates colors dynamically on theme switch."""
        from gui.core.theme import C
        current_palette = semantic_palette()
        if current_palette == self._rendered_palette:
            return
        refresh_semantic_control_colors([self], self._rendered_palette, current_palette)
        self._rendered_palette = current_palette
        self.bgcolor = C.BG
        self._title.color = C.TEXT_PRIMARY
        self._back_btn.icon_color = C.TEXT_SECONDARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._header_divider.color = C.BORDER
        self._loading.color = C.ACCENT
        self._loading_text.color = C.TEXT_SECONDARY
        self._empty_text.color = C.TEXT_SECONDARY
        self._error_icon.color = C.CRITICAL
        self._error_text.color = C.TEXT_SECONDARY
        self._error_retry_btn.bgcolor = C.ACCENT
        self._error_retry_btn.color = C.ON_ACCENT
        self._stale_banner.bgcolor = C.SURFACE_ELEVATED
        self._stale_banner.border = ft.Border.all(1, C.WARNING)
        self._stale_icon.color = C.WARNING
        self._stale_text.color = C.TEXT_PRIMARY
        self._courses_btn.style = ft.ButtonStyle(
            color=C.ACCENT,
            padding=ft.Padding.symmetric(horizontal=8, vertical=0),
        )
        self._planner_btn.icon_color = C.TEXT_SECONDARY

        # Refresh child subtrees without emitting extra standalone updates.
        self._radar_card.update_theme(update=False)
        self._grade_list_view.update_theme(update=False)

        if self._defer_self_update:
            return
        try:
            self.update()
        except Exception:
            pass


def main(page: ft.Page):
    """Stub main function to support Flet Preview on this file directly."""
    try:
        from gui.flet_compat import patch_flet
        patch_flet()
    except Exception:
        pass
    from gui.app_controller import AppController
    AppController(page)


if __name__ == "__main__":
    ft.run(main=main, assets_dir=os.path.join(_project_root, "assets"))
