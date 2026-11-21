"""Grade Overview View - Academic Radar & Course Transcript Analysis.

Displays a 6-axis Academic Radar character-stat profile, semester KPIs,
interactive metric detail modals, and comprehensive course grades.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any, Callable, Optional

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
from core.academic_radar.calculator import calculate_radar_profile
from core.academic_radar.gpa_planner import CreditProgress
from core.academic_radar.models import RadarProfile, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from gui.components.academic_radar.course_components_dialog import show_course_components_dialog
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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._moodle_courses_provider = moodle_courses_provider
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._on_open_courses = on_open_courses
        self._page_ref = page
        self._semesters: list[SemesterGradeData] = []
        self._profiles: list[RadarProfile] = []
        self._selected_semester_idx = 0

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
            size=16,
            weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY,
            text_align=ft.TextAlign.CENTER,
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
            style=ft.ButtonStyle(color=C.ACCENT),
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

        # 4. Academic Radar Card & Grade List
        self._radar_card = AcademicRadarCard(
            profiles=[],
            on_semester_change=self._on_semester_changed,
            page=self._get_page(),
            visible=False,
        )
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
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self._header_divider = ft.Divider(height=1, color=C.BORDER)
        self.content = ft.Column(
            controls=[
                self._header,
                self._header_divider,
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

    def show_loading(self) -> None:
        """Shows loading state."""
        self._loading_row.visible = True
        self._empty_state.visible = False
        self._error_state.visible = False
        self._radar_card.visible = False
        self._grade_list_view.visible = False
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        try:
            self.update()
        except Exception:
            pass

    def show_error(self, message: str = "") -> None:
        """Shows an explicit error state with retry action."""
        self._loading_row.visible = False
        self._empty_state.visible = False
        self._radar_card.visible = False
        self._grade_list_view.visible = False
        if message:
            self._error_text.value = message
        else:
            self._error_text.value = (
                "Không tải được bảng điểm từ Portal.\nKiểm tra kết nối/tài khoản rồi thử lại."
            )
        self._error_state.visible = True
        try:
            self.update()
        except Exception:
            pass

    def update_academic_data(
        self,
        semesters: list[SemesterGradeData],
        profiles: list[RadarProfile],
        progress: Optional[CreditProgress] = None,
    ) -> None:
        """Populates the view with full academic transcript and computed radar profiles."""
        self._loading_row.visible = False
        self._error_state.visible = False
        self._semesters = semesters
        self._profiles = profiles
        self._selected_semester_idx = 0

        if not profiles or not semesters:
            self._empty_state.visible = True
            self._radar_card.visible = False
            self._grade_list_view.visible = False
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
        try:
            page.show_dialog(ft.SnackBar(ft.Text(message)))
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
        body_w = max(280.0, min(440.0, page_w - 80)) if page_w else 440.0

        used_overlay = False

        def _close(_event=None) -> None:
            self._close_planner(page, dialog, used_overlay=used_overlay)

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
                autofocus=True,
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
            bgcolor=C.BG,
            shape=ft.RoundedRectangleBorder(radius=16),
        )
        self._planner_dialog = dialog
        try:
            page.show_dialog(dialog)
            logger.info("GPA planner dialog shown")
        except Exception:
            logger.warning("page.show_dialog failed; falling back to overlay", exc_info=True)
            used_overlay = True
            page.overlay.append(dialog)
            dialog.open = True
            page.update()

    def _close_planner(
        self, page: ft.Page, dialog: ft.AlertDialog, *, used_overlay: bool = False
    ) -> None:
        if not used_overlay:
            try:
                page.pop_dialog()
                return
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

    def _on_course_clicked(self, e: ft.ControlEvent | None, course) -> None:
        """Bấm vào môn -> popup bảng điểm thành phần (ưu tiên báo cáo điểm Moodle)."""
        page = self._get_page()
        if page is None and e is not None:
            try:
                page = e.control.page
            except Exception:
                page = None
        moodle_course = None
        if self._moodle_courses_provider is not None:
            try:
                moodle_course = find_moodle_course(course, self._moodle_courses_provider() or [])
            except Exception:
                logger.debug("Moodle course lookup failed", exc_info=True)
        show_course_components_dialog(page, course, moodle_course)

    def _on_semester_changed(self, index: int) -> None:
        self._selected_semester_idx = index
        if 0 <= index < len(self._semesters):
            self._grade_list_view.update_courses(self._semesters[index].courses)

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
        self._courses_btn.style = ft.ButtonStyle(color=C.ACCENT)
        self._planner_btn.icon_color = C.TEXT_SECONDARY

        # These views are cached while detached from the desktop page. Rebuild
        # them unconditionally so their root surfaces cannot retain the theme
        # from the previous settings preview when they are shown again.
        self._radar_card.update_theme()
        self._grade_list_view.update_theme()

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
