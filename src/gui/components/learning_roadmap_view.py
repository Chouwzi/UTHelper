"""Learning Roadmap view - every curriculum course by semester, with status,
graduation-requirement tracking, search/filter, and offline support.

All classification logic lives in ``core.learning_roadmap`` (pure, unit-tested);
this module only renders a ``RoadmapLoadResult``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Optional

import flet as ft

from core.learning_roadmap.filtering import (
    FILTER_ACTIONABLE,
    FILTER_ALL,
    filter_semesters,
)
from core.learning_roadmap.models import (
    CourseStatus,
    RequirementStatus,
    RoadmapCourse,
    RoadmapSemester,
    RoadmapSnapshot,
)
from core.learning_roadmap.service import RoadmapLoadResult
from gui.core.theme import C, semantic_palette

logger = logging.getLogger(__name__)


def _icon(name: str, fallback: str = "CIRCLE_OUTLINED"):
    """Resolve an icon by name; never crash the app over a missing glyph."""
    icons = ft.Icons
    return getattr(icons, name, None) or getattr(icons, fallback, None) or icons.WARNING_AMBER_ROUNDED


_ENDPOINT_LABELS = {
    "curriculum": "chương trình khung",
    "exemptions": "điểm miễn",
    "certificates": "chứng chỉ",
    "graduation_certificates": "chứng chỉ tốt nghiệp",
    "graduation_info": "xét tốt nghiệp",
    "credit_progress": "tiến độ tín chỉ",
    "semesters": "học kỳ hiện tại",
    "current_semester": "học kỳ hiện tại",
    "transcript": "bảng điểm",
}

# (filter key, label)
_FILTERS: tuple[tuple[str, str], ...] = (
    (FILTER_ALL, "Tất cả"),
    (FILTER_ACTIONABLE, "Cần đăng ký"),
    (CourseStatus.COMPLETED.value, "Đã hoàn thành"),
    (CourseStatus.IN_PROGRESS.value, "Đang học"),
    (CourseStatus.FAILED.value, "Rớt / nợ"),
    (CourseStatus.NOT_TAKEN.value, "Chưa học"),
)


def _status_style(status: CourseStatus) -> tuple[str, str, str]:
    """(color, icon name, short label) - read at render time so themes apply."""
    return {
        CourseStatus.COMPLETED: (C.SAFE, "CHECK_CIRCLE_ROUNDED", "Hoàn thành"),
        CourseStatus.IN_PROGRESS: (C.ACCENT, "PLAY_CIRCLE_ROUNDED", "Đang học"),
        CourseStatus.FAILED: (C.CRITICAL, "CANCEL_ROUNDED", "Chưa đạt"),
        CourseStatus.NOT_TAKEN: (C.TEXT_SECONDARY, "RADIO_BUTTON_UNCHECKED_ROUNDED", "Chưa học"),
    }[status]


def _requirement_style(status: RequirementStatus) -> tuple[str, str]:
    return {
        RequirementStatus.MET: (C.SAFE, "CHECK_CIRCLE_ROUNDED"),
        RequirementStatus.IN_PROGRESS: (C.ACCENT, "PLAY_CIRCLE_ROUNDED"),
        RequirementStatus.NOT_MET: (C.CRITICAL, "CANCEL_ROUNDED"),
        RequirementStatus.UNKNOWN: (C.TEXT_SECONDARY, "HELP_OUTLINE_ROUNDED"),
    }[status]


def _fmt_time(value: Optional[datetime]) -> str:
    return value.strftime("%H:%M %d/%m/%Y") if value else "—"


class LearningRoadmapView(ft.Container):
    """Slide-in panel (same lifecycle as ``GradeOverviewView``)."""

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
        self._result: Optional[RoadmapLoadResult] = None
        self._snapshot: Optional[RoadmapSnapshot] = None
        self._query = ""
        self._status_filter = FILTER_ALL

        # Header ----------------------------------------------------------
        self._back_btn = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED, icon_color=C.TEXT_SECONDARY,
            icon_size=20, tooltip="Quay lại", on_click=self._handle_close,
        )
        self._title = ft.Text(
            "Lộ trình học tập", size=16, weight=ft.FontWeight.W_700,
            color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER, expand=True,
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED, icon_color=C.TEXT_SECONDARY,
            icon_size=20, tooltip="Cập nhật từ Portal", on_click=self._handle_refresh,
        )
        self._header = ft.Row(
            controls=[self._back_btn, self._title, self._refresh_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._header_divider = ft.Divider(height=1, color=C.BORDER)

        # Loading / empty -----------------------------------------------------
        self._loading = ft.ProgressRing(width=24, height=24, stroke_width=3, color=C.ACCENT)
        self._loading_text = ft.Text("Đang tải lộ trình học tập...", size=13, color=C.TEXT_SECONDARY)
        self._loading_row = ft.Row(
            controls=[self._loading, self._loading_text],
            alignment=ft.MainAxisAlignment.CENTER, visible=False,
        )
        self._empty_text = ft.Text("", size=13, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)
        self._empty_state = ft.Container(
            content=self._empty_text, alignment=ft.Alignment(0, 0), padding=40, visible=False,
        )

        # Body pieces (rebuilt from state) ------------------------------------
        self._updated_text = ft.Text("", size=11, color=C.TEXT_SECONDARY)
        self._banner = ft.Container(visible=False)
        self._summary = ft.Container()
        # The search box is created once and never rebuilt: rebuilding it on
        # each keystroke would steal focus.
        self._search_field = ft.TextField(
            hint_text="Tìm môn học (tên hoặc mã, có thể không dấu)",
            hint_style=ft.TextStyle(size=12, color=C.TEXT_SECONDARY),
            prefix_icon=ft.Icons.SEARCH,
            border_radius=10,
            border_color=C.BORDER,
            focused_border_color=C.ACCENT,
            bgcolor=C.SURFACE,
            text_size=12,
            height=44,
            content_padding=ft.Padding.only(left=12, right=10, top=10, bottom=10),
            on_change=self._on_search,
        )
        self._chips_row = ft.Row(controls=[], wrap=True, spacing=6, run_spacing=6)
        self._list = ft.Column(controls=[], spacing=8)

        self._body = ft.Column(
            controls=[
                self._updated_text, self._banner, self._summary,
                self._search_field, self._chips_row, self._list,
            ],
            spacing=12, scroll=ft.ScrollMode.AUTO, expand=True, visible=False,
        )

        self.content = ft.Column(
            controls=[
                self._header, self._header_divider, self._loading_row,
                self._empty_state, self._body,
            ],
            spacing=8, expand=True,
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

    # ------------------------------------------------------------ lifecycle
    def _safe_update(self) -> None:
        try:
            self.update()
        except Exception:
            pass

    def show_loading(self) -> None:
        self._loading_row.visible = True
        self._empty_state.visible = False
        self._body.visible = False
        self.visible = True
        self.offset = ft.Offset(0, 0)
        self.opacity = 1.0
        self._safe_update()

    def show_result(self, result: RoadmapLoadResult) -> None:
        self._loading_row.visible = False
        self._result = result
        self._snapshot = result.snapshot
        if self._snapshot is None:
            self._body.visible = False
            self._empty_state.visible = True
            reason = (result.error or "").strip()
            self._empty_text.value = (
                "Chưa có dữ liệu lộ trình học tập.\n"
                "Hãy kết nối mạng và đăng nhập Portal trong Cài đặt, rồi bấm làm mới."
                + (f"\n\n({reason})" if reason else "")
            )
            self._safe_update()
            return
        self._empty_state.visible = False
        self._body.visible = True
        self._render_all()
        self._safe_update()

    def hide(self) -> None:
        self.offset = ft.Offset(1, 0)
        self.opacity = 0.0

    def reset(self) -> None:
        """Forget the previous account's roadmap when the account changes."""
        self._result = None
        self._snapshot = None
        self._query = ""
        self._status_filter = FILTER_ALL
        self._search_field.value = ""
        self._list.controls.clear()
        self._chips_row.controls.clear()
        self._summary.content = None
        self._banner.content = None
        self._banner.visible = False
        self._updated_text.value = ""
        self._body.visible = False
        self._empty_state.visible = False
        self._loading_row.visible = False
        self.visible = False

    def update_theme(self) -> None:
        current = semantic_palette()
        self._rendered_palette = current
        self.bgcolor = C.BG
        self._title.color = C.TEXT_PRIMARY
        self._back_btn.icon_color = C.TEXT_SECONDARY
        self._refresh_btn.icon_color = C.TEXT_SECONDARY
        self._header_divider.color = C.BORDER
        self._loading.color = C.ACCENT
        self._loading_text.color = C.TEXT_SECONDARY
        self._empty_text.color = C.TEXT_SECONDARY
        self._updated_text.color = C.TEXT_SECONDARY
        self._search_field.border_color = C.BORDER
        self._search_field.focused_border_color = C.ACCENT
        self._search_field.bgcolor = C.SURFACE
        self._search_field.hint_style = ft.TextStyle(size=12, color=C.TEXT_SECONDARY)
        if self._snapshot is not None:
            self._render_all()
        self._safe_update()

    # ------------------------------------------------------------- handlers
    def _handle_close(self, e) -> None:
        if self._on_close:
            self._on_close()

    def _handle_refresh(self, e) -> None:
        if self._on_refresh:
            self._on_refresh()

    def _on_search(self, e) -> None:
        self._query = (getattr(e.control, "value", "") or "").strip()
        self._render_list()
        self._safe_update()

    def _set_filter(self, key: str) -> None:
        self._status_filter = key
        self._render_chips()
        self._render_list()
        self._safe_update()

    # ------------------------------------------------------------ rendering
    def _render_all(self) -> None:
        self._render_caption_and_banner()
        self._render_summary()
        self._render_chips()
        self._render_list()

    def _render_caption_and_banner(self) -> None:
        r, snap = self._result, self._snapshot
        if r is None or snap is None:
            return
        origin = {"network": "từ Portal", "cache": "từ bộ nhớ đệm"}.get(r.source, "từ dữ liệu đã lưu")
        self._updated_text.value = f"Cập nhật {_fmt_time(r.fetched_at)} · {origin}"
        self._updated_text.color = C.TEXT_SECONDARY

        messages: list[str] = []
        if r.is_offline:
            messages.append(
                "Không kết nối được Portal - đang hiển thị dữ liệu đã lưu "
                "(ngoại tuyến). Bấm làm mới khi có mạng."
            )
        if not snap.framework_available:
            messages.append(
                "Chưa có chương trình khung - chỉ hiển thị các môn có trong bảng điểm."
            )
        failed = sorted({
            _ENDPOINT_LABELS.get(k, k) for k in snap.endpoint_errors
            if not (k == "curriculum" and not snap.framework_available)
        })
        if failed:
            messages.append("Chưa cập nhật được: " + ", ".join(failed) + ".")
        if not messages:
            self._banner.visible = False
            return
        self._banner.visible = True
        self._banner.bgcolor = C.SURFACE_ELEVATED
        self._banner.border = ft.Border.all(1, C.WARNING)
        self._banner.border_radius = 10
        self._banner.padding = ft.Padding.symmetric(horizontal=12, vertical=8)
        self._banner.content = ft.Row(
            controls=[
                ft.Icon(_icon("CLOUD_OFF_ROUNDED" if r.is_offline else "WARNING_AMBER_ROUNDED"),
                        size=16, color=C.WARNING),
                ft.Text("\n".join(messages), size=12, color=C.WARNING, expand=True),
            ],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _render_summary(self) -> None:
        snap = self._snapshot
        if snap is None:
            return
        cp = snap.credits
        if cp.earned is not None and cp.required:
            credit_text = f"{cp.earned:g} / {cp.required:g} tín chỉ"
        elif cp.earned is not None:
            credit_text = f"{cp.earned:g} tín chỉ"
        else:
            credit_text = "Chưa có dữ liệu"
        credit_block: list[ft.Control] = [
            ft.Row(
                controls=[
                    ft.Text("TIẾN ĐỘ TÍN CHỈ", size=12, weight=ft.FontWeight.BOLD, color=C.TEXT_SECONDARY),
                    ft.Text(credit_text, size=12, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        ]
        if cp.ratio is not None:
            credit_block.append(ft.ProgressBar(value=cp.ratio, color=C.ACCENT, bgcolor=C.BORDER, height=6))

        req_rows: list[ft.Control] = [
            ft.Text("ĐIỀU KIỆN TỐT NGHIỆP", size=12, weight=ft.FontWeight.BOLD, color=C.TEXT_SECONDARY)
        ]
        for req in snap.requirements:
            color, icon_name = _requirement_style(req.status)
            req_rows.append(
                ft.Row(
                    controls=[
                        ft.Icon(_icon(icon_name), size=18, color=color),
                        ft.Column(
                            controls=[
                                ft.Text(req.label, size=13, color=C.TEXT_PRIMARY),
                                ft.Text(req.detail, size=11, color=C.TEXT_SECONDARY),
                            ],
                            spacing=1, expand=True,
                        ),
                    ],
                    spacing=10, vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
        if snap.portal_verdict is not None:
            ok = snap.portal_verdict
            req_rows.append(
                ft.Text(
                    ("Portal: đủ điều kiện xét tốt nghiệp" if ok else "Portal: chưa đủ điều kiện xét tốt nghiệp")
                    + (f" - {snap.portal_message}" if snap.portal_message else ""),
                    size=11, color=C.SAFE if ok else C.WARNING,
                )
            )

        self._summary.content = ft.Column(controls=[*credit_block, ft.Divider(height=1, color=C.BORDER), *req_rows], spacing=8)
        self._summary.bgcolor = C.SURFACE
        self._summary.border = ft.Border.all(1, C.BORDER)
        self._summary.border_radius = 12
        self._summary.padding = 14

    def _render_chips(self) -> None:
        snap = self._snapshot
        if snap is None:
            return
        counts = snap.counts()
        total = sum(counts.values())
        number = {
            FILTER_ALL: total,
            FILTER_ACTIONABLE: counts[CourseStatus.FAILED] + counts[CourseStatus.NOT_TAKEN],
            **{s.value: n for s, n in counts.items()},
        }
        chips: list[ft.Control] = []
        for key, label in _FILTERS:
            selected = key == self._status_filter
            chips.append(
                ft.Container(
                    content=ft.Text(
                        f"{label} ({number.get(key, 0)})", size=12,
                        color=C.ON_ACCENT if selected else C.TEXT_PRIMARY,
                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_400,
                    ),
                    bgcolor=C.ACCENT if selected else C.SURFACE,
                    border=ft.Border.all(1, C.ACCENT if selected else C.BORDER),
                    border_radius=16,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                    ink=True,
                    on_click=lambda _e, k=key: self._set_filter(k),
                )
            )
        self._chips_row.controls = chips

    def _course_row(self, course: RoadmapCourse) -> ft.Control:
        color, icon_name, label = _status_style(course.status)
        meta = [course.code or "—", f"{course.credits:g} TC"]
        if course.is_conditional:
            meta.append("Điều kiện")
        if course.required is False:
            meta.append("Tự chọn")
        if course.note:
            meta.append(course.note)
        trailing = course.grade_text or label
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(_icon(icon_name), size=18, color=color),
                    ft.Column(
                        controls=[
                            ft.Text(course.name, size=13, color=C.TEXT_PRIMARY, max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(" · ".join(meta), size=11, color=C.TEXT_SECONDARY,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=2, expand=True,
                    ),
                    ft.Text(trailing, size=12, weight=ft.FontWeight.W_600, color=color),
                ],
                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
        )

    def _semester_tile(self, sem: RoadmapSemester, expanded: bool) -> ft.Control:
        done = sem.count(CourseStatus.COMPLETED)
        running = sem.count(CourseStatus.IN_PROGRESS)
        failed = sem.count(CourseStatus.FAILED)
        if failed:
            icon_name, color = "WARNING_AMBER_ROUNDED", C.CRITICAL
        elif running:
            icon_name, color = "PLAY_CIRCLE_ROUNDED", C.ACCENT
        elif done == len(sem.courses):
            icon_name, color = "CHECK_CIRCLE_ROUNDED", C.SAFE
        else:
            icon_name, color = "RADIO_BUTTON_UNCHECKED_ROUNDED", C.TEXT_SECONDARY
        parts = [f"{done}/{len(sem.courses)} hoàn thành"]
        if running:
            parts.append(f"{running} đang học")
        if failed:
            parts.append(f"{failed} chưa đạt")

        body = ft.Column(controls=[self._course_row(c) for c in sem.courses], spacing=0)

        def on_change(event):
            raw = getattr(event, "data", None)
            if isinstance(raw, bool):
                is_open = raw
            elif isinstance(raw, str):
                is_open = raw.strip().lower() == "true"
            else:
                is_open = bool(getattr(event.control, "expanded", False))
            event.control.controls = [body] if is_open else []
            event.control.update()

        tile = ft.ExpansionTile(
            title=ft.Text(sem.label, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
            subtitle=ft.Text(" · ".join(parts), size=12, color=C.TEXT_SECONDARY),
            leading=ft.Icon(_icon(icon_name), size=20, color=color),
            controls=[body] if expanded else [],
            collapsed_text_color=C.TEXT_PRIMARY,
            text_color=C.ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            collapsed_shape=ft.RoundedRectangleBorder(radius=10),
            expanded=expanded,
            maintain_state=False,
            on_change=on_change,
        )
        return ft.Container(
            content=tile, bgcolor=C.SURFACE, border_radius=10,
            border=ft.Border.all(1, C.BORDER), padding=0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

    def _render_list(self) -> None:
        snap = self._snapshot
        if snap is None:
            return
        filtering = bool(self._query) or self._status_filter != FILTER_ALL
        shown = filter_semesters(snap.semesters, self._query, self._status_filter)
        if not shown:
            self._list.controls = [
                ft.Container(
                    content=ft.Text(
                        "Không có môn nào khớp bộ lọc." if filtering else "Chưa có học phần nào.",
                        size=13, color=C.TEXT_SECONDARY,
                    ),
                    alignment=ft.Alignment(0, 0), padding=24,
                )
            ]
            return
        # Unfiltered: open what needs attention. Filtered: open every match.
        attention = {s.key for s in shown
                     if s.count(CourseStatus.IN_PROGRESS) or s.count(CourseStatus.FAILED)}
        if not attention and not filtering:
            first_open = next((s.key for s in shown if s.count(CourseStatus.NOT_TAKEN)), None)
            attention = {first_open} if first_open else set()
        self._list.controls = [
            self._semester_tile(s, expanded=filtering or s.key in attention) for s in shown
        ]
