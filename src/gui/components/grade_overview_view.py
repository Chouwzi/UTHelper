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
from core.academic_radar.models import CourseGradeItem, RadarProfile, SemesterGradeData
from core.academic_radar.normalizer import SemesterNormalizer
from gui.components.academic_radar.grade_list_view import AcademicGradeListView
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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._page_ref = page
        self._semesters: list[SemesterGradeData] = []
        self._profiles: list[RadarProfile] = []
        self._selected_semester_idx = 0

        # 1. Header (Back button on left, Centered Title in middle, Refresh on right)
        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Quay lại",
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
            on_click=self._handle_refresh,
        )
        self._header = ft.Row(
            controls=[
                self._back_btn,
                self._title,
                self._refresh_btn,
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

        # 3. Empty state
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

        # 4. Academic Radar Card & Grade List
        self._radar_card = AcademicRadarCard(
            profiles=[],
            on_semester_change=self._on_semester_changed,
            page=self._get_page(),
            visible=False,
        )
        self._grade_list_view = AcademicGradeListView(courses=[], visible=False)

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
        self._radar_card.visible = False
        self._grade_list_view.visible = False
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        try:
            self.update()
        except Exception:
            pass

    def update_academic_data(
        self,
        semesters: list[SemesterGradeData],
        profiles: list[RadarProfile],
    ) -> None:
        """Populates the view with full academic transcript and computed radar profiles."""
        self._loading_row.visible = False
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
        self._radar_card.visible = True
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

    def _on_semester_changed(self, index: int) -> None:
        self._selected_semester_idx = index
        if 0 <= index < len(self._semesters):
            self._grade_list_view.update_courses(self._semesters[index].courses)

    def _handle_refresh(self, e: ft.ControlEvent) -> None:
        if self._on_refresh:
            self._on_refresh()

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

        if self._radar_card.visible:
            self._radar_card._build_ui()
        if self._grade_list_view.visible:
            self._grade_list_view._build_list()

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
