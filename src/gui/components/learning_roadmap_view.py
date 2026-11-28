"""Learning Roadmap view - every curriculum course by semester, with status,
graduation-requirement tracking, search/filter, and offline support.

All classification logic lives in ``core.learning_roadmap`` (pure, unit-tested);
this module only renders a ``RoadmapLoadResult``.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
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
from core.learning_roadmap.parsers import fold
from core.learning_roadmap.plan_store import PlanStore
from core.learning_roadmap.planner import (
    PlanResult,
    PlanState,
    PlanTerm,
    PlannedCourse,
    Term,
    build_plan,
    parse_term,
    move_course,
    set_limits,
    set_target,
    term_range,
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


TAB_PLAN = "plan"
TAB_FRAMEWORK = "framework"
_TABS = ((TAB_PLAN, "Kế hoạch ra trường"), (TAB_FRAMEWORK, "Chương trình khung"))


def _tc(value: float) -> str:
    return f"{value:g}"


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


# Graduation conditions shown in the UI (the snapshot still tracks IT / PE for the planner).
_SHOWN_REQUIREMENTS = ("english", "defense")


def _tint(color: str, opacity: float = 0.14) -> str:
    """Translucent version of a semantic colour (pill / icon-chip backgrounds)."""
    return ft.Colors.with_opacity(opacity, color)


def _pill(text: str, color: str, *, filled: bool = False) -> ft.Control:
    """Small rounded tag. ``filled`` = solid (status), otherwise tinted (meta)."""
    return ft.Container(
        content=ft.Text(
            text, size=10, weight=ft.FontWeight.W_700,
            color=C.ON_ACCENT if filled else color,
        ),
        bgcolor=color if filled else _tint(color),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=7, vertical=2),
    )


def _icon_chip(icon_name: str, color: str, size: int = 34) -> ft.Control:
    """Icon centred in a tinted circle."""
    return ft.Container(
        content=ft.Icon(_icon(icon_name), size=size * 0.55, color=color),
        width=size, height=size, border_radius=size / 2,
        bgcolor=_tint(color), alignment=ft.Alignment(0, 0),
    )


def _section_label(text: str, trailing: Optional[ft.Control] = None) -> ft.Control:
    label = ft.Text(text, size=11, weight=ft.FontWeight.W_700, color=C.TEXT_SECONDARY)
    if trailing is None:
        return label
    return ft.Row(
        controls=[label, trailing],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


_REQ_STATUS_LABEL = {
    RequirementStatus.MET: "Đạt",
    RequirementStatus.IN_PROGRESS: "Đang học",
    RequirementStatus.NOT_MET: "Chưa đạt",
    RequirementStatus.UNKNOWN: "Chưa rõ",
}


class LearningRoadmapView(ft.Container):
    """Slide-in panel (same lifecycle as ``GradeOverviewView``)."""

    def __init__(
        self,
        on_close: Optional[Callable] = None,
        on_refresh: Optional[Callable] = None,
        page: Optional[ft.Page] = None,
        plan_store: Optional[PlanStore] = None,
        today: Callable[[], date] = date.today,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._plan_store = plan_store
        self._today = today
        self._plan_state = PlanState()
        self._plan: Optional[PlanResult] = None
        self._plan_notice = ""
        self._tab = TAB_PLAN
        self._dialog = None
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
        self._chips_row = ft.Row(controls=[], spacing=6, scroll=ft.ScrollMode.AUTO)
        self._list = ft.Column(controls=[], spacing=8)
        self._tab_row = ft.Row(controls=[], spacing=6)
        self._plan_box = ft.Column(controls=[], spacing=10)
        self._framework_box = ft.Column(
            controls=[self._search_field, self._chips_row, self._list],
            spacing=12, visible=False,
        )

        self._body = ft.Column(
            controls=[
                self._updated_text, self._banner, self._summary,
                self._tab_row, self._plan_box, self._framework_box,
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
        self._plan_notice = ""
        self._load_plan_state()
        self._recompute()
        self._render_all()
        self._safe_update()

    def set_plan_owner(self, owner: str) -> None:
        """Plans are per Portal account; call before ``show_result``."""
        if self._plan_store is not None:
            self._plan_store.set_owner(owner)

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
        self._render_tabs()
        self._render_plan()
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

    def _credit_card(self, snap: RoadmapSnapshot) -> ft.Control:
        cp = snap.credits
        if cp.earned is not None:
            big = ft.Text(f"{cp.earned:g}", size=30, weight=ft.FontWeight.W_800, color=C.TEXT_PRIMARY)
            of = ft.Text(
                f"/ {cp.required:g} tín chỉ" if cp.required else "tín chỉ đã tích lũy",
                size=13, color=C.TEXT_SECONDARY,
            )
        else:
            big = ft.Text("—", size=30, weight=ft.FontWeight.W_800, color=C.TEXT_SECONDARY)
            of = ft.Text("Chưa có dữ liệu tín chỉ", size=13, color=C.TEXT_SECONDARY)
        head: list[ft.Control] = [
            ft.Row(
                controls=[big, of], spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.END, expand=True,
            )
        ]
        if cp.ratio is not None:
            head.append(_pill(f"{cp.ratio * 100:.0f}%", C.ACCENT))
        controls: list[ft.Control] = [
            _section_label("TIẾN ĐỘ TÍN CHỈ"),
            ft.Row(controls=head, vertical_alignment=ft.CrossAxisAlignment.END),
        ]
        if cp.ratio is not None:
            controls.append(ft.ProgressBar(
                value=cp.ratio, color=C.ACCENT, bgcolor=C.BORDER, height=8, border_radius=4,
            ))
            if cp.required and cp.earned is not None and cp.required > cp.earned:
                controls.append(ft.Text(
                    f"Còn {cp.required - cp.earned:g} tín chỉ nữa để đủ chương trình",
                    size=11, color=C.TEXT_SECONDARY,
                ))
        return self._card(controls)

    @staticmethod
    def _step_bar(done: int, total: int, color: str) -> ft.Control:
        """N short segments (one per level/course) instead of a full-width bar,
        so there is no long grey track."""
        segs = [
            ft.Container(height=6, expand=True, border_radius=3,
                         bgcolor=color if i < done else C.BORDER)
            for i in range(total)
        ]
        return ft.Container(content=ft.Row(controls=segs, spacing=4), width=180)

    def _requirement_row(self, req) -> ft.Control:
        color, icon_name = _requirement_style(req.status)
        right: list[ft.Control] = []
        if req.has_progress:
            right.append(ft.Text(req.progress_text, size=15, weight=ft.FontWeight.W_800, color=color))
        right.append(_pill(_REQ_STATUS_LABEL[req.status], color))
        text_col: list[ft.Control] = [
            ft.Text(req.label, size=13, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY),
            ft.Text(req.detail, size=11, color=C.TEXT_SECONDARY, max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ]
        if req.has_progress:
            text_col.append(self._step_bar(req.progress_done, req.progress_total, color))
        return ft.Container(
            content=ft.Row(
                controls=[
                    _icon_chip(icon_name, color),
                    ft.Column(controls=text_col, spacing=3, expand=True),
                    ft.Column(
                        controls=right, spacing=3,
                        horizontal_alignment=ft.CrossAxisAlignment.END,
                    ),
                ],
                spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=8),
        )

    def _requirements_card(self, snap: RoadmapSnapshot) -> ft.Control:
        reqs = tuple(r for r in snap.requirements if r.key in _SHOWN_REQUIREMENTS)
        met = sum(1 for r in reqs if r.status is RequirementStatus.MET)
        all_met = bool(reqs) and met == len(reqs)
        summary = _pill(f"{met}/{len(reqs)} đạt", C.SAFE if all_met else C.ACCENT)
        controls: list[ft.Control] = [_section_label("ĐIỀU KIỆN TỐT NGHIỆP", summary)]
        for i, req in enumerate(reqs):
            if i:
                controls.append(ft.Divider(height=1, color=C.BORDER))
            controls.append(self._requirement_row(req))
        if snap.portal_verdict is not None:
            ok = snap.portal_verdict
            controls.append(self._banner_line(
                ("Portal: đủ điều kiện xét tốt nghiệp" if ok else "Portal: chưa đủ điều kiện xét tốt nghiệp")
                + (f" - {snap.portal_message}" if snap.portal_message else ""),
                C.SAFE if ok else C.WARNING,
                "CHECK_CIRCLE_ROUNDED" if ok else "INFO_OUTLINE_ROUNDED",
            ))
        return self._card(controls)

    def _render_summary(self) -> None:
        snap = self._snapshot
        if snap is None:
            return
        self._summary.content = ft.Column(
            controls=[self._credit_card(snap), self._requirements_card(snap)], spacing=12,
        )
        self._summary.bgcolor = None
        self._summary.border = None
        self._summary.padding = 0

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
                    border_radius=18,
                    padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                    ink=True,
                    on_click=lambda _e, k=key: self._set_filter(k),
                )
            )
        self._chips_row.controls = chips

    def _course_row(self, course: RoadmapCourse) -> ft.Control:
        color, icon_name, label = _status_style(course.status)
        tags: list[ft.Control] = []
        if course.is_conditional:
            tags.append(_pill("Điều kiện", C.WARNING))
        if course.required is False:
            tags.append(_pill("Tự chọn", C.TEXT_SECONDARY))
        if course.note:
            tags.append(_pill(course.note, C.ACCENT if course.status is not CourseStatus.FAILED else C.CRITICAL))
        meta_row = ft.Row(
            controls=[
                ft.Text(f"{course.code or '—'} · {course.credits:g} TC", size=11,
                        color=C.TEXT_SECONDARY),
                *tags,
            ],
            spacing=6, wrap=True, run_spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        trailing = course.grade_text or label
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(width=3, height=36, bgcolor=color, border_radius=2),
                    ft.Icon(_icon(icon_name), size=18, color=color),
                    ft.Column(
                        controls=[
                            ft.Text(course.name, size=13, weight=ft.FontWeight.W_600,
                                    color=C.TEXT_PRIMARY, max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            meta_row,
                        ],
                        spacing=3, expand=True,
                    ),
                    _pill(trailing, color, filled=False),
                ],
                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=9),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
        )

    def _semester_tile(self, sem: RoadmapSemester, expanded: bool) -> ft.Control:
        total = len(sem.courses)
        if not total:                                    # a term with no registered course
            return ft.Container(
                content=ft.Row(
                    controls=[
                        _icon_chip("EVENT_BUSY_ROUNDED", C.TEXT_SECONDARY, 32),
                        ft.Column(
                            controls=[
                                ft.Text(sem.label, size=14, weight=ft.FontWeight.BOLD,
                                        color=C.TEXT_PRIMARY),
                                ft.Text("Không có môn nào trong bảng điểm", size=12,
                                        color=C.TEXT_SECONDARY),
                            ],
                            spacing=2, expand=True,
                        ),
                    ],
                    spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER), border_radius=10,
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            )
        done = sem.count(CourseStatus.COMPLETED)
        running = sem.count(CourseStatus.IN_PROGRESS)
        failed = sem.count(CourseStatus.FAILED)
        if failed:
            icon_name, color = "WARNING_AMBER_ROUNDED", C.CRITICAL
        elif running:
            icon_name, color = "PLAY_CIRCLE_ROUNDED", C.ACCENT
        elif done == total:
            icon_name, color = "CHECK_CIRCLE_ROUNDED", C.SAFE
        else:
            icon_name, color = "RADIO_BUTTON_UNCHECKED_ROUNDED", C.TEXT_SECONDARY
        parts = [f"{done}/{total} hoàn thành"]
        if running:
            parts.append(f"{running} đang học")
        if failed:
            parts.append(f"{failed} chưa đạt")

        subtitle = ft.Column(
            controls=[
                ft.Text(" · ".join(parts), size=12, color=C.TEXT_SECONDARY),
                ft.ProgressBar(
                    value=done / total if total else 0, color=color, bgcolor=C.BORDER,
                    height=4, width=200,
                ),
            ],
            spacing=5,
        )
        body = ft.Column(controls=[self._course_row(c) for c in sem.courses], spacing=0)
        return self._expansion(
            ft.Text(sem.label, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
            subtitle,
            _icon_chip(icon_name, color, 32),
            body, expanded,
        )

    @staticmethod
    def _expansion(title, subtitle, leading, body: ft.Control, expanded: bool) -> ft.Control:
        """Card-style ExpansionTile whose body is only mounted while open."""

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
            title=title, subtitle=subtitle, leading=leading,
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


    # ======================================================================
    # Graduation plan
    # ======================================================================
    def _load_plan_state(self) -> None:
        if self._plan_store is None:
            return
        try:
            self._plan_state = self._plan_store.load()
        except Exception:
            logger.warning("Could not load the graduation plan", exc_info=True)
            self._plan_state = PlanState()

    def _persist(self) -> None:
        if self._plan_store is not None:
            self._plan_store.save(self._plan_state)

    def _recompute(self, *, reset_auto: bool = False, reset_all: bool = False) -> None:
        """Reconcile the saved plan with the snapshot (and persist if it moved)."""
        snap = self._snapshot
        if snap is None:
            self._plan = None
            return
        try:
            new_state, plan = build_plan(
                snap, self._plan_state, today=self._today(),
                reset_auto=reset_auto, reset_all=reset_all,
            )
        except Exception:
            logger.error("Graduation plan failed", exc_info=True)
            self._plan = None
            return
        if new_state.to_json() != self._plan_state.to_json():
            self._plan_state = new_state
            self._persist()
        self._plan = plan

    def _refresh_plan(self) -> None:
        self._render_plan()
        self._safe_update()

    # ------------------------------------------------------------- actions
    def _set_tab(self, key: str) -> None:
        self._tab = key
        self._render_tabs()
        self._safe_update()

    def _apply_target(self, key: Optional[str]) -> None:
        self._close_dialog()
        self._plan_state = set_target(self._plan_state, key)
        self._plan_notice = ""
        self._recompute(reset_auto=True)
        self._persist()
        self._refresh_plan()

    def _autoplan(self, *, reset_all: bool = False) -> None:
        self._plan_notice = ""
        self._recompute(reset_auto=True, reset_all=reset_all)
        self._persist()
        self._refresh_plan()

    def _apply_limits(self, main_max: float, summer_max: float) -> None:
        self._close_dialog()
        self._plan_state = set_limits(self._plan_state, main_max, summer_max)
        self._plan_notice = ""
        self._recompute(reset_auto=True)
        self._persist()
        self._refresh_plan()

    def _move(self, key: str, term_key: Optional[str], *, notify: bool = True) -> str:
        """Move a course; returns the error text ('' on success)."""
        if self._snapshot is None:
            return ""
        new, err = move_course(
            self._snapshot, self._plan_state, key, term_key, today=self._today()
        )
        if err:
            if notify:
                self._plan_notice = err
                self._refresh_plan()
            return err
        self._plan_state = new
        self._plan_notice = ""
        self._persist()
        self._recompute()
        self._refresh_plan()
        return ""

    # ------------------------------------------------------------- dialogs
    def _page(self):
        # ``page`` raises until the control is mounted; the plan dialogs are
        # also opened from tests and early render passes, so treat a detached
        # control as "no page" instead of propagating RuntimeError.
        if self._page_ref is not None:
            return self._page_ref
        try:
            return getattr(self, "page", None)
        except RuntimeError:
            return None

    def _show_dialog(self, dlg) -> None:
        self._dialog = dlg
        show = getattr(self._page(), "show_dialog", None)
        if callable(show):
            show(dlg)

    def _close_dialog(self, *_args) -> None:
        if getattr(self, "_dialog", None) is None:
            return
        self._dialog = None
        pop = getattr(self._page(), "pop_dialog", None)
        if callable(pop):
            try:
                pop()
            except Exception:
                logger.debug("pop_dialog failed", exc_info=True)

    @staticmethod
    def _option_info(plan: PlanResult, term: Term) -> str:
        if term < plan.first_plan_term:
            return "Ra trường ngay sau kỳ đang học" if plan.outstanding_credits <= 0 \
                else "Không còn kỳ nào để học thêm"
        span = term_range(plan.first_plan_term, term)
        mains = [t for t in span if not t.is_summer]
        summers = len(span) - len(mains)
        parts = [f"{len(mains)} kỳ chính" + (f" + {summers} hè" if summers else "")]
        if mains and plan.outstanding_credits > 0:
            avg = -(-plan.outstanding_credits // len(mains))
            parts.append(f"≈ {_tc(avg)} TC/kỳ chính")
        return " · ".join(parts)

    def _open_target_dialog(self, _e=None) -> None:
        plan = self._plan
        if plan is None:
            return
        rows: list[ft.Control] = []
        for t in plan.target_options:
            too_soon = plan.earliest_target is not None and t < plan.earliest_target
            selected = plan.target == t
            color = C.TEXT_SECONDARY if too_soon else C.TEXT_PRIMARY
            subtitle = "Không kịp - vượt giới hạn tín chỉ" if too_soon else self._option_info(plan, t)
            rows.append(ft.Container(
                content=ft.Row(
                    controls=[
                        ft.Column(
                            controls=[
                                ft.Text(plan.target_label(t), size=13, color=color,
                                        weight=ft.FontWeight.W_700 if selected else ft.FontWeight.W_500),
                                ft.Text(subtitle, size=11, color=C.CRITICAL if too_soon else C.TEXT_SECONDARY),
                            ],
                            spacing=1, expand=True,
                        ),
                        ft.Icon(_icon("CHECK_CIRCLE_ROUNDED"), size=18, color=C.ACCENT, visible=selected),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
                bgcolor=C.SURFACE_ELEVATED if selected else None,
                ink=not too_soon,
                on_click=None if too_soon else (lambda _e, k=t.key: self._apply_target(k)),
            ))
        intro = f"Còn {_tc(plan.outstanding_credits)} TC bắt buộc chưa học."
        if plan.earliest_target is not None:
            intro += f" Sớm nhất có thể: {plan.target_label(plan.earliest_target)}."
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Bạn muốn ra trường khi nào?", size=16, weight=ft.FontWeight.BOLD,
                          color=C.TEXT_PRIMARY),
            content=ft.Container(
                width=360,
                content=ft.Column(
                    controls=[
                        ft.Text(intro, size=12, color=C.TEXT_SECONDARY),
                        ft.Column(controls=rows, spacing=0, scroll=ft.ScrollMode.AUTO, height=340),
                    ],
                    spacing=8, tight=True,
                ),
            ),
            actions=[ft.TextButton("Đóng", on_click=self._close_dialog)],
            shape=ft.RoundedRectangleBorder(radius=12),
            bgcolor=C.BG,
        )
        self._show_dialog(dlg)

    def _open_limits_dialog(self, _e=None) -> None:
        lim = self._plan_state.limits

        def field(label: str, value: float) -> ft.TextField:
            return ft.TextField(
                label=label, value=_tc(value), dense=True, text_size=13,
                border_color=C.BORDER, focused_border_color=C.ACCENT,
                keyboard_type=ft.KeyboardType.NUMBER,
            )

        main_f = field("Tối đa mỗi học kỳ chính (TC)", lim.main_max)
        summer_f = field("Tối đa học kỳ hè (TC)", lim.summer_max)
        error = ft.Text("", size=12, color=C.CRITICAL)

        def save(_e=None) -> None:
            try:
                m = float(str(main_f.value).replace(",", "."))
                h = float(str(summer_f.value).replace(",", "."))
            except ValueError:
                m = h = 0.0
            if not (0 < m <= 60 and 0 < h <= 60):
                error.value = "Nhập số tín chỉ từ 1 đến 60."
                try:
                    error.update()
                except Exception:
                    pass
                return
            self._apply_limits(m, h)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Giới hạn tín chỉ", size=16, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
            content=ft.Container(
                width=340,
                content=ft.Column(
                    controls=[
                        main_f, summer_f, error,
                        ft.Text(
                            "Mặc định theo Quy chế đào tạo tín chỉ UTH: tối đa 24 TC/kỳ chính "
                            "(14 TC nếu bị xếp học lực yếu). Quy chế không nêu mức tối đa cho "
                            "học kỳ hè nên mặc định 12 TC. Hãy sửa nếu quy định hiện hành khác.",
                            size=11, color=C.TEXT_SECONDARY,
                        ),
                    ],
                    spacing=10, tight=True,
                ),
            ),
            actions=[
                ft.TextButton("Hủy", on_click=self._close_dialog),
                ft.Button("Lưu", on_click=save, bgcolor=C.ACCENT, color=C.ON_ACCENT),
            ],
            shape=ft.RoundedRectangleBorder(radius=12),
            bgcolor=C.BG,
        )
        self._show_dialog(dlg)

    def _open_add_dialog(self, term_key: str) -> None:
        plan = self._plan
        term = Term.from_key(term_key)
        if plan is None or term is None:
            return
        tv = next((t for t in plan.terms if t.term == term), None)
        if tv is None:
            return
        pool: list[tuple[PlannedCourse, str]] = [(p, "Chưa xếp") for p in plan.unplaced]
        pool += [(p, "Tự chọn") for p in plan.optional]
        for other in plan.terms:
            if other.term != term and not other.is_current:
                pool += [(p, other.term.short_label) for p in other.courses if not p.locked]

        query = {"q": ""}
        listing = ft.Column(controls=[], spacing=0, scroll=ft.ScrollMode.AUTO, height=300)
        error = ft.Text("", size=12, color=C.CRITICAL)

        def pick(key: str) -> None:
            err = self._move(key, term_key, notify=False)
            if err:
                error.value = err
                try:
                    error.update()
                except Exception:
                    pass
                return
            self._close_dialog()

        def fill() -> None:
            terms = fold(query["q"]).split()
            rows: list[ft.Control] = []
            for p, where in pool:
                hay = fold(f"{p.course.code} {p.course.name}")
                if not all(t in hay for t in terms):
                    continue
                rows.append(ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Column(
                                controls=[
                                    ft.Text(p.course.name, size=13, color=C.TEXT_PRIMARY, max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS),
                                    ft.Text(f"{p.course.code or '—'} · {where}", size=11,
                                            color=C.TEXT_SECONDARY),
                                ],
                                spacing=1, expand=True,
                            ),
                            ft.Text(f"{_tc(p.course.credits)} TC", size=12, color=C.TEXT_SECONDARY),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=7),
                    border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
                    ink=True,
                    on_click=lambda _e, k=p.key: pick(k),
                ))
            if not rows:
                rows = [ft.Container(
                    content=ft.Text("Không có môn nào để thêm.", size=12, color=C.TEXT_SECONDARY),
                    padding=16, alignment=ft.Alignment(0, 0),
                )]
            listing.controls = rows

        def on_search(e) -> None:
            query["q"] = getattr(e.control, "value", "") or ""
            fill()
            try:
                listing.update()
            except Exception:
                pass

        fill()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Thêm môn vào {term.short_label}", size=16, weight=ft.FontWeight.BOLD,
                          color=C.TEXT_PRIMARY),
            content=ft.Container(
                width=380,
                content=ft.Column(
                    controls=[
                        ft.Text(
                            f"Đang có {_tc(tv.credits)}/{_tc(tv.max_credits)} TC · "
                            f"còn trống {_tc(tv.free_credits)} TC",
                            size=12, color=C.TEXT_SECONDARY,
                        ),
                        ft.TextField(
                            hint_text="Tìm môn (tên hoặc mã)", prefix_icon=ft.Icons.SEARCH,
                            dense=True, text_size=12, border_color=C.BORDER,
                            focused_border_color=C.ACCENT, on_change=on_search,
                        ),
                        error, listing,
                    ],
                    spacing=8, tight=True,
                ),
            ),
            actions=[ft.TextButton("Đóng", on_click=self._close_dialog)],
            shape=ft.RoundedRectangleBorder(radius=12),
            bgcolor=C.BG,
        )
        self._show_dialog(dlg)

    # ----------------------------------------------------------- rendering
    def _render_tabs(self) -> None:
        segments: list[ft.Control] = []
        for key, label in _TABS:
            selected = key == self._tab
            segments.append(ft.Container(
                content=ft.Text(
                    label, size=13, text_align=ft.TextAlign.CENTER,
                    color=C.ON_ACCENT if selected else C.TEXT_SECONDARY,
                    weight=ft.FontWeight.W_700 if selected else ft.FontWeight.W_500,
                ),
                bgcolor=C.ACCENT if selected else None,
                border_radius=10, expand=True, alignment=ft.Alignment(0, 0),
                padding=ft.Padding.symmetric(vertical=8),
                ink=True,
                on_click=lambda _e, k=key: self._set_tab(k),
            ))
        # Rendered as ONE segmented bar; ``_tab_row`` keeps the segments so the
        # selected one is easy to inspect.
        self._tab_row.controls = [ft.Container(
            content=ft.Row(controls=segments, spacing=4),
            bgcolor=C.SURFACE, border=ft.Border.all(1, C.BORDER),
            border_radius=12, padding=4, expand=True,
        )]
        self._plan_box.visible = self._tab == TAB_PLAN
        self._framework_box.visible = self._tab == TAB_FRAMEWORK

    def _card(self, controls: list[ft.Control], *, border: str = "") -> ft.Container:
        return ft.Container(
            content=ft.Column(controls=controls, spacing=10),
            bgcolor=C.SURFACE, border=ft.Border.all(1, border or C.BORDER),
            border_radius=14, padding=16,
        )

    def _goal_card(self, plan: PlanResult) -> ft.Control:
        label = ft.Text("MỤC TIÊU RA TRƯỜNG", size=12, weight=ft.FontWeight.BOLD, color=C.TEXT_SECONDARY)
        if not plan.has_target:
            hint = f"Còn {_tc(plan.outstanding_credits)} TC bắt buộc chưa học."
            if plan.earliest_target is not None:
                hint += f" Sớm nhất có thể: {plan.target_label(plan.earliest_target)}."
            return self._card([
                label,
                ft.Text("Bạn muốn ra trường khi nào?", size=15, weight=ft.FontWeight.W_700,
                        color=C.TEXT_PRIMARY),
                ft.Text(hint + " Chọn mốc, app sẽ chia môn cho từng kỳ theo giới hạn tín chỉ "
                        "của trường - bạn vẫn đổi được sau đó.", size=12, color=C.TEXT_SECONDARY),
                ft.Row(controls=[ft.Button(
                    "Chọn thời điểm ra trường", icon=ft.Icons.FLAG_ROUNDED,
                    on_click=self._open_target_dialog, bgcolor=C.ACCENT, color=C.ON_ACCENT,
                )]),
            ])

        placed = [p for t in plan.terms if not t.is_current for p in t.courses]
        n_terms = sum(1 for t in plan.terms if not t.is_current)
        lim = self._plan_state.limits
        info = (f"Còn {_tc(plan.outstanding_credits)} TC · {len(placed)} môn đã xếp trong "
                f"{n_terms} kỳ")
        menu = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT_ROUNDED, icon_color=C.TEXT_SECONDARY, bgcolor=C.SURFACE,
            tooltip="Tùy chọn kế hoạch",
            items=[
                ft.PopupMenuItem(content="Xếp lại tự động (giữ môn tôi đã chọn)",
                                 icon=ft.Icons.AUTO_FIX_HIGH_ROUNDED,
                                 on_click=lambda _e: self._autoplan()),
                ft.PopupMenuItem(content="Đặt lại toàn bộ kế hoạch",
                                 icon=ft.Icons.RESTART_ALT_ROUNDED,
                                 on_click=lambda _e: self._autoplan(reset_all=True)),
                ft.PopupMenuItem(content="Sửa giới hạn tín chỉ",
                                 icon=ft.Icons.TUNE_ROUNDED, on_click=self._open_limits_dialog),
            ],
        )
        controls: list[ft.Control] = [
            ft.Row(controls=[label, menu], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row(
                controls=[
                    ft.Icon(_icon("FLAG_ROUNDED"), size=20, color=C.SAFE if plan.feasible else C.WARNING),
                    ft.Text(plan.target_label(plan.target), size=15, weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY, expand=True),
                    ft.TextButton("Đổi mốc", on_click=self._open_target_dialog,
                                  style=ft.ButtonStyle(color=C.ACCENT)),
                ],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(info, size=12, color=C.TEXT_SECONDARY),
            ft.Text(f"Giới hạn: {_tc(lim.main_max)} TC/kỳ chính · {_tc(lim.summer_max)} TC/kỳ hè",
                    size=11, color=C.TEXT_SECONDARY),
        ]
        if not plan.feasible:
            hint = (f"Còn {len(plan.unplaced)} môn ({_tc(plan.shortfall_credits)} TC) chưa xếp được "
                    "vì các kỳ đã đầy.")
            if plan.earliest_target is not None and plan.target is not None \
                    and plan.earliest_target > plan.target:
                hint += f" Mốc sớm nhất khả thi: {plan.target_label(plan.earliest_target)}."
            else:
                hint += " Thử tăng giới hạn tín chỉ hoặc gỡ bớt môn đã xếp tay."
            controls.append(ft.Text(hint, size=12, color=C.WARNING))
        return self._card(controls, border=C.WARNING if not plan.feasible else "")

    def _banner_line(self, text: str, color: str, icon_name: str = "INFO_OUTLINE_ROUNDED") -> ft.Control:
        return ft.Container(
            content=ft.Row(
                controls=[ft.Icon(_icon(icon_name), size=16, color=color),
                          ft.Text(text, size=12, color=color, expand=True)],
                spacing=8, vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=C.SURFACE_ELEVATED, border=ft.Border.all(1, color), border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        )

    def _move_items(
        self, p: PlannedCourse, plan: PlanResult, here: Optional[Term]
    ) -> list[ft.Control]:
        items: list[ft.Control] = []
        for tv in plan.terms:
            if tv.is_current or tv.term == here:
                continue
            fits = tv.free_credits + 1e-9 >= p.course.credits
            items.append(ft.PopupMenuItem(
                content=f"Chuyển sang {tv.term.short_label} (trống {_tc(tv.free_credits)} TC)"
                if here is not None else f"Xếp vào {tv.term.short_label} (trống {_tc(tv.free_credits)} TC)",
                icon=ft.Icons.EVENT_AVAILABLE_ROUNDED if fits else ft.Icons.EVENT_BUSY_ROUNDED,
                on_click=lambda _e, k=p.key, tk=tv.term.key: self._move(k, tk),
            ))
        if here is not None:
            items.append(ft.PopupMenuItem(
                content="Bỏ khỏi kế hoạch", icon=ft.Icons.REMOVE_CIRCLE_OUTLINE_ROUNDED,
                on_click=lambda _e, k=p.key: self._move(k, None),
            ))
        return items

    def _plan_course_row(
        self, p: PlannedCourse, plan: PlanResult, here: Optional[Term]
    ) -> ft.Control:
        c = p.course
        meta = [c.code or "—", f"{_tc(c.credits)} TC"]
        if c.status is CourseStatus.FAILED:
            meta.append("Học lại")
        if c.is_conditional:
            meta.append("Điều kiện")
        if c.required is False:
            meta.append("Tự chọn")
        if p.note:
            meta.append(p.note)
        if p.manual:
            icon_name, color = "PUSH_PIN_ROUNDED", C.ACCENT
        elif p.locked:
            icon_name, color = "PLAY_CIRCLE_ROUNDED", C.ACCENT
        elif c.status is CourseStatus.FAILED:
            icon_name, color = "CANCEL_ROUNDED", C.CRITICAL
        else:
            icon_name, color = "RADIO_BUTTON_UNCHECKED_ROUNDED", C.TEXT_SECONDARY
        trailing: ft.Control
        if p.locked:
            trailing = ft.Text("Đang học", size=12, weight=ft.FontWeight.W_600, color=C.ACCENT)
        else:
            items = self._move_items(p, plan, here)
            trailing = ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT_ROUNDED, icon_color=C.TEXT_SECONDARY, bgcolor=C.SURFACE,
                icon_size=18, tooltip="Chuyển kỳ", items=items, visible=bool(items),
            )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(_icon(icon_name), size=18, color=color),
                    ft.Column(
                        controls=[
                            ft.Text(c.name, size=13, color=C.TEXT_PRIMARY, max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(" · ".join(meta), size=11, color=C.TEXT_SECONDARY,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ],
                        spacing=2, expand=True,
                    ),
                    trailing,
                ],
                spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border=ft.Border.only(bottom=ft.BorderSide(1, C.BORDER)),
        )

    def _badge(self, text: str, color: str) -> ft.Control:
        return ft.Container(
            content=ft.Text(text, size=10, weight=ft.FontWeight.W_700, color=color),
            border=ft.Border.all(1, color), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=6, vertical=1),
        )

    def _term_tile(self, tv: PlanTerm, plan: PlanResult, expanded: bool) -> ft.Control:
        t = tv.term
        if tv.is_current:
            icon_name, color = "PLAY_CIRCLE_ROUNDED", C.ACCENT
        elif tv.over_limit:
            icon_name, color = "WARNING_AMBER_ROUNDED", C.CRITICAL
        elif t.is_summer:
            icon_name, color = "WB_SUNNY_ROUNDED", C.WARNING
        elif tv.is_final:
            icon_name, color = "FLAG_ROUNDED", C.SAFE
        else:
            icon_name, color = "CALENDAR_MONTH_ROUNDED", C.TEXT_SECONDARY

        badges: list[ft.Control] = []
        if tv.is_current:
            badges.append(self._badge("ĐANG HỌC", C.ACCENT))
        if tv.is_final:
            badges.append(self._badge("RA TRƯỜNG", C.SAFE))
        title = ft.Row(
            controls=[ft.Text(t.label, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY), *badges],
            spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        sub = f"{_tc(tv.credits)}/{_tc(tv.max_credits)} TC · {len(tv.courses)} môn"
        sub_color = C.TEXT_SECONDARY
        if tv.over_limit:
            sub, sub_color = sub + " · vượt giới hạn", C.CRITICAL
        elif tv.below_min:
            sub, sub_color = sub + " · dưới mức tối thiểu 12 TC", C.WARNING
        elif not tv.courses and not tv.is_current:
            sub = f"Chưa có môn · tối đa {_tc(tv.max_credits)} TC"

        rows = [self._plan_course_row(p, plan, None if tv.is_current else t) for p in tv.courses]
        if not tv.is_current:
            rows.append(ft.Row(controls=[ft.TextButton(
                "Thêm môn", icon=ft.Icons.ADD_ROUNDED, style=ft.ButtonStyle(color=C.ACCENT),
                on_click=lambda _e, k=t.key: self._open_add_dialog(k),
            )]))
        return self._expansion(
            title, ft.Text(sub, size=12, color=sub_color),
            ft.Icon(_icon(icon_name), size=20, color=color),
            ft.Column(controls=rows, spacing=0), expanded,
        )

    def _pool_tile(
        self, title: str, courses: tuple[PlannedCourse, ...], plan: PlanResult,
        icon_name: str, color: str, expanded: bool,
    ) -> ft.Control:
        credits = sum(p.course.credits for p in courses)
        rows = [self._plan_course_row(p, plan, None) for p in courses]
        if not plan.has_target:
            rows.append(ft.Container(
                content=ft.Text("Chọn mốc ra trường để xếp các môn này vào từng kỳ.",
                                size=11, color=C.TEXT_SECONDARY),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            ))
        return self._expansion(
            ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
            ft.Text(f"{len(courses)} môn · {_tc(credits)} TC", size=12, color=C.TEXT_SECONDARY),
            ft.Icon(_icon(icon_name), size=20, color=color),
            ft.Column(controls=rows, spacing=0), expanded,
        )

    def _render_plan(self) -> None:
        plan, snap = self._plan, self._snapshot
        if snap is None:
            return
        if plan is None:
            self._plan_box.controls = [ft.Text(
                "Chưa lập được kế hoạch từ dữ liệu hiện có.", size=13, color=C.TEXT_SECONDARY)]
            return
        out: list[ft.Control] = [self._goal_card(plan)]
        for note in plan.notes:
            out.append(self._banner_line(note, C.WARNING, "INFO_OUTLINE_ROUNDED"))
        if self._plan_notice:
            out.append(self._banner_line(self._plan_notice, C.CRITICAL, "ERROR_OUTLINE_ROUNDED"))

        # Current term first, then upcoming terms in order.
        opened_upcoming = False
        for tv in plan.terms:
            expand = tv.is_current or tv.over_limit
            if not tv.is_current and not opened_upcoming:
                expand, opened_upcoming = True, True
            out.append(self._term_tile(tv, plan, expand))
        if plan.unplaced:
            out.append(self._pool_tile(
                "Môn chưa xếp kỳ", plan.unplaced, plan,
                "PENDING_ACTIONS_ROUNDED", C.WARNING, expanded=not plan.has_target or not plan.feasible,
            ))
        if plan.optional:
            out.append(self._pool_tile(
                "Môn tự chọn (không tự xếp)", plan.optional, plan,
                "LIST_ALT_ROUNDED", C.TEXT_SECONDARY, expanded=False,
            ))
        if snap.history:
            out.append(_section_label("CÁC HỌC KỲ ĐÃ QUA"))
            groups: dict[int, list[RoadmapSemester]] = {}
            for h in snap.history:                       # already newest first
                t = parse_term(h.label)
                groups.setdefault(t.year if t else 0, []).append(h)
            for year in sorted(groups, reverse=True):
                n = year - snap.entry_year + 1 if (snap.entry_year and year) else 0
                title = (f"Năm {n} · " if n >= 1 else "") + (f"{year}-{year + 1}" if year else "Khác")
                done = sum(len([c for c in h.courses if c.status is CourseStatus.COMPLETED])
                           for h in groups[year])
                total = sum(len(h.courses) for h in groups[year])
                out.append(ft.Row(
                    controls=[
                        ft.Text(title, size=13, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                        ft.Text(f"{done}/{total} môn hoàn thành", size=11, color=C.TEXT_SECONDARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ))
                out.extend(self._semester_tile(h, expanded=False) for h in groups[year])
        self._plan_box.controls = out

