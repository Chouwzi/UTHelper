"""Grade Overview View - shows all course grades in a dedicated panel.

Accessible via the 📊 icon in the header. Displays a summary of grades
for all enrolled courses with detail expansion per course.
"""
import os
import sys

# Patch path for direct execution / Flet preview compatibility
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import flet as ft
import logging
from typing import List, Dict, Any, Optional, Callable
from gui.core.theme import C

logger = logging.getLogger(__name__)


class GradeOverviewView(ft.Container):
    """Slide-in panel showing grade overview for all courses."""

    def __init__(self, on_close: Optional[Callable] = None, **kwargs):
        super().__init__(**kwargs)
        self._on_close = on_close

        # Header
        self._title = ft.Text(
            "📊 Bảng điểm", size=18,
            weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY,
        )
        self._close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_color=C.TEXT_SECONDARY, icon_size=20,
            tooltip="Đóng",
            on_click=self._handle_close,
        )
        self._header = ft.Row(
            controls=[self._title, self._close_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        # Loading indicator
        self._loading = ft.ProgressRing(
            width=24, height=24, stroke_width=3, color=C.ACCENT,
        )
        self._loading_row = ft.Row(
            controls=[self._loading, ft.Text("Đang tải điểm...", size=13, color=C.TEXT_SECONDARY)],
            alignment=ft.MainAxisAlignment.CENTER,
            visible=False,
        )

        # Empty state
        self._empty_text = ft.Text(
            "Chưa có dữ liệu điểm. Hãy thử làm mới.",
            size=13, color=C.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            content=self._empty_text,
            alignment=ft.Alignment(0, 0),
            padding=40,
            visible=False,
        )

        # Grade list
        self._grade_list = ft.Column(
            controls=[],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        # Main content
        self.content = ft.Column(
            controls=[
                self._header,
                ft.Divider(height=1, color=C.BORDER),
                self._loading_row,
                self._empty_state,
                self._grade_list,
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

    def show_loading(self):
        """Show loading state."""
        self._loading_row.visible = True
        self._empty_state.visible = False
        self._grade_list.controls.clear()
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0

    def update_grades(self, courses_grades: List[Dict[str, Any]], grade_items: Dict[str, List[Dict]]):
        """Update the view with grade data.

        Args:
            courses_grades: List of {courseid, coursename, grade, rank} from WS.
            grade_items: Dict of course_id -> list of grade items.
        """
        self._loading_row.visible = False
        self._grade_list.controls.clear()

        if not courses_grades:
            self._empty_state.visible = True
            return

        self._empty_state.visible = False

        for cg in courses_grades:
            course_id = str(cg.get('courseid', ''))
            course_name = cg.get('coursename', f'Course {course_id}')
            overall_grade = cg.get('grade', '-')
            rank = cg.get('rank', '')

            # Course header card
            grade_color = self._grade_color(overall_grade)
            grade_display = ft.Text(
                overall_grade if overall_grade and overall_grade != '-' else '-',
                size=20, weight=ft.FontWeight.W_700,
                color=grade_color,
            )

            course_card_content = [
                ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(course_name, size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(f"Xếp hạng: {rank}" if rank else "", size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                        grade_display,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ]

            # Grade items for this course
            items = grade_items.get(course_id, [])
            if items:
                items_column = ft.Column(spacing=2)
                for item in items:
                    item_name = item.get('itemname', '')
                    item_grade = item.get('gradeformatted', '-')
                    if not item_name:
                        continue
                    item_row = ft.Row(
                        controls=[
                            ft.Text(f"  • {item_name}", size=12, color=C.TEXT_SECONDARY,
                                    expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(item_grade, size=12, color=C.TEXT_PRIMARY,
                                    weight=ft.FontWeight.W_500),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    )
                    items_column.controls.append(item_row)
                course_card_content.append(ft.Divider(height=1, color=C.BORDER))
                course_card_content.append(items_column)

            course_card = ft.Container(
                content=ft.Column(controls=course_card_content, spacing=6),
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=12,
            )
            self._grade_list.controls.append(course_card)

    def _grade_color(self, grade_str: str) -> str:
        """Color-code the grade value."""
        try:
            val = float(grade_str.replace(',', '.'))
            if val >= 8.0:
                return "#22C55E"  # Green
            elif val >= 6.5:
                return "#3B82F6"  # Blue
            elif val >= 5.0:
                return "#F59E0B"  # Orange
            else:
                return "#EF4444"  # Red
        except (ValueError, AttributeError):
            return C.TEXT_PRIMARY

    def _handle_close(self, e):
        """Handle close button click."""
        if self._on_close:
            self._on_close()

    def hide(self):
        """Hide the view with animation."""
        self.offset = ft.Offset(1, 0)
        self.opacity = 0.0

    def update_theme(self):
        """Update colors of all grade overview controls dynamically on theme switch."""
        from gui.core.theme import C
        self.bgcolor = C.BG
        
        # Header title and close
        self._title.color = C.TEXT_PRIMARY
        self._close_btn.icon_color = C.TEXT_SECONDARY
        
        # Divider color in content
        try:
            self.content.controls[1].color = C.BORDER
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            
        # Loading controls
        self._loading.color = C.ACCENT
        try:
            self._loading_row.controls[1].color = C.TEXT_SECONDARY
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)
            
        # Empty text
        self._empty_text.color = C.TEXT_SECONDARY

        # Update any rendered cards inside _grade_list
        try:
            for card in self._grade_list.controls:
                card.bgcolor = C.SURFACE
                card.border = ft.Border.all(1, C.BORDER)
                
                col = card.content
                if not col:
                    continue
                # Header row
                header_row = col.controls[0]
                left_col = header_row.controls[0]
                left_col.controls[0].color = C.TEXT_PRIMARY
                left_col.controls[1].color = C.TEXT_SECONDARY
                
                grade_display = header_row.controls[1]
                if hasattr(grade_display, 'value'):
                    grade_display.color = self._grade_color(grade_display.value)
                
                if len(col.controls) > 2:
                    divider = col.controls[1]
                    divider.color = C.BORDER
                    
                    items_col = col.controls[2]
                    for item_row in items_col.controls:
                        item_row.controls[0].color = C.TEXT_SECONDARY
                        item_row.controls[1].color = C.TEXT_PRIMARY
        except Exception:
            import logging as _fb_log
            _fb_log.getLogger(__name__).debug("Ignored exception", exc_info=True)

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
