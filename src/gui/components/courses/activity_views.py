"""Native Pane-3 interactive activity views (Attendance, Groups, Quiz, Forum, Book, Assignment)."""

from __future__ import annotations

import asyncio
from datetime import datetime
import inspect
import threading
from typing import Any, Callable, Optional

import flet as ft

from core.courses.activity_service import (
    AttendanceSummary,
    CourseActivityService,
    CourseGroupEntry,
    ForumActivitySummary,
    ForumDiscussionEntry,
    ForumPostEntry,
    GroupActivitySummary,
    QuizActivitySummary,
    QuizAttemptInfo,
    QuizAttemptReviewReport,
    QuizReviewQuestion,
    RichActivityPage,
)
from core.courses.models import CourseModuleItem, CourseSource
from gui.components.courses._shared import (
    build_assignment_loading_skeleton,
    build_expand_toggle_button,
    build_filter_chip,
    build_offline_banner,
    build_skeleton_block,
    build_viewer_header,
    refresh_viewer_header,
    safe_control_update,
    sync_expand_toggle_button,
)
from gui.core.theme import C


def _format_unix_dt(ts: int) -> str:
    if not ts or ts <= 0:
        return "Không giới hạn"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%H:%M %d/%m/%Y")
    except Exception:
        return "Không xác định"


def _format_duration_sec(seconds: int) -> str:
    if not seconds or seconds <= 0:
        return "Không giới hạn"
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours} giờ {mins} phút"
    if mins > 0:
        return f"{mins} phút"
    return f"{secs} giây"


class _BasePaneActivityView(ft.Container):
    """Shared Pane-3 chrome with Back, Refresh, Expand (2+3), and In-App WebView trigger."""

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        source: CourseSource,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Optional[Callable[[str, str], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        extra_actions: Optional[list[ft.Control]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._source = source
        self._module = module
        self._svc = activity_service
        self._on_close = on_close
        self._on_open_webview = on_open_webview
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._disposed = False

        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: self._on_toggle_expand()
            if callable(self._on_toggle_expand)
            else None,
            visible=self._show_expand_toggle,
        )
        self._webview_btn = ft.IconButton(
            icon=ft.Icons.WEB_ROUNDED,
            icon_size=18,
            icon_color=C.TEXT_PRIMARY,
            tooltip="Mở bằng WebView ngay trong app",
            visible=bool((module.web_url or module.url) and on_open_webview is not None),
            on_click=lambda _e: self._launch_in_webview(),
        )
        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=18,
            icon_color=C.TEXT_PRIMARY,
            tooltip="Tải lại",
            on_click=lambda _e: self._load_async(),
        )

        trailing = list(extra_actions or []) + [
            self._refresh_btn,
            self._expand_btn,
            self._webview_btn,
        ]
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=title or module.name or "Hoạt động",
            subtitle=subtitle,
            back_tooltip="Quay lại môn học",
            on_back=lambda _e: self._handle_close(),
            trailing_actions=trailing,
        )
        self._body_host = ft.Container(expand=True, bgcolor=C.BG)
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[self._header_container, self._body_host],
        )

    def _launch_in_webview(self) -> None:
        target = (self._module.web_url or self._module.url or "").strip()
        if target and self._on_open_webview is not None:
            self._on_open_webview(self._module.name or "Hoạt động", target)

    def _set_loading(self, label: str = "Đang tải dữ liệu...") -> None:
        self._body_host.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                ft.ProgressBar(value=None, color=C.ACCENT, bgcolor=C.BORDER, bar_height=3),
                ft.Container(
                    expand=True,
                    padding=16,
                    content=ft.Column(
                        spacing=12,
                        controls=[
                            ft.Container(
                                bgcolor=C.SURFACE,
                                border=ft.Border.all(1, C.ACCENT),
                                border_radius=10,
                                padding=ft.Padding.symmetric(horizontal=14, vertical=10),
                                content=ft.Row(
                                    spacing=10,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                    controls=[
                                        ft.ProgressRing(
                                            width=18,
                                            height=18,
                                            stroke_width=2.5,
                                            color=C.ACCENT,
                                        ),
                                        ft.Text(
                                            label,
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=C.TEXT_PRIMARY,
                                            expand=True,
                                        ),
                                    ],
                                ),
                            ),
                            ft.Row(
                                spacing=10,
                                controls=[
                                    ft.Container(
                                        expand=True,
                                        bgcolor=C.SURFACE,
                                        border=ft.Border.all(1, C.BORDER),
                                        border_radius=10,
                                        padding=14,
                                        content=ft.Column(
                                            spacing=8,
                                            controls=[
                                                build_skeleton_block(width=90, height=10, subtle=True),
                                                build_skeleton_block(width=68, height=18),
                                            ],
                                        ),
                                    )
                                    for _ in range(2)
                                ],
                            ),
                            ft.Container(
                                bgcolor=C.SURFACE,
                                border=ft.Border.all(1, C.BORDER),
                                border_radius=10,
                                padding=16,
                                content=ft.Column(
                                    spacing=10,
                                    controls=[
                                        build_skeleton_block(width=180, height=14),
                                        build_skeleton_block(height=12, subtle=True),
                                        build_skeleton_block(width=240, height=12, subtle=True),
                                    ],
                                ),
                            ),
                        ],
                    ),
                ),
            ],
        )
        safe_control_update(self._body_host)

    def _load_async(self) -> None:
        raise NotImplementedError

    def _handle_close(self) -> None:
        self.dispose()
        self._on_close()

    def dispose(self) -> None:
        self._disposed = True

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        self._body_host.bgcolor = C.BG
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )
        self._webview_btn.icon_color = C.TEXT_PRIMARY
        self._refresh_btn.icon_color = C.TEXT_PRIMARY
        safe_control_update(self)


class AttendanceActivityView(_BasePaneActivityView):
    """Native Pane-3 attendance summary, session table, and self-attendance check-in."""

    def __init__(
        self,
        *,
        source: CourseSource,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Optional[Callable[[str, str], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        initial_summary: Optional[AttendanceSummary] = None,
    ) -> None:
        super().__init__(
            title=module.name or "Điểm danh",
            subtitle="Theo dõi chuyên cần & tự điểm danh",
            source=source,
            module=module,
            activity_service=activity_service,
            on_close=on_close,
            on_open_webview=on_open_webview,
            is_expanded=is_expanded,
            show_expand_toggle=show_expand_toggle,
            on_toggle_expand=on_toggle_expand,
        )
        self._summary: Optional[AttendanceSummary] = initial_summary
        self._feedback_banner: str = ""
        self._selected_status: str = ""
        self._pwd_value: str = ""
        if initial_summary is not None:
            self._render_summary(initial_summary)
        else:
            self._load_async()

    def _load_async(self) -> None:
        self._set_loading("Đang tải bảng điểm danh...")

        def _worker() -> None:
            summary = self._svc.fetch_attendance(self._source, self._module)
            if self._disposed:
                return
            self._summary = summary
            self._render_summary(summary)

        threading.Thread(target=_worker, name="CourseAttendanceFetch", daemon=True).start()

    def _submit_self_attendance(self, summary: AttendanceSummary) -> None:
        if not summary.submit_action_url or summary.submit_form is None:
            return
        fields = dict(summary.submit_form)
        if self._selected_status:
            fields["status"] = self._selected_status
        if summary.requires_password:
            fields["studentpassword"] = self._pwd_value
        self._set_loading("Đang gửi xác nhận điểm danh...")

        def _worker() -> None:
            ok, msg, updated = self._svc.submit_attendance(
                self._source,
                summary.submit_action_url,
                fields,
                fallback_title=summary.title,
            )
            if self._disposed:
                return
            self._feedback_banner = msg if ok else f"Lỗi: {msg}"
            final_summary = updated if updated is not None else self._svc.fetch_attendance(
                self._source, self._module
            )
            self._summary = final_summary
            self._render_summary(final_summary)

        threading.Thread(target=_worker, name="CourseAttendanceSubmit", daemon=True).start()

    def _render_summary(self, summary: AttendanceSummary) -> None:
        self._title_text.value = summary.title or self._module.name
        controls: list[ft.Control] = []

        if self._feedback_banner or summary.status_message:
            controls.append(
                build_offline_banner(
                    self._feedback_banner or summary.status_message,
                    icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                )
            )

        metric_cards: list[ft.Control] = []
        for label, value, color in (
            ("Số buổi ghi nhận", summary.taken_sessions or str(len(summary.records)), C.TEXT_PRIMARY),
            ("Điểm tích lũy", summary.points_over_taken or "--", C.ACCENT),
            ("Tỷ lệ chuyên cần", summary.percentage or "--", C.SAFE),
        ):
            metric_cards.append(
                ft.Container(
                    expand=True,
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=12,
                    content=ft.Column(
                        spacing=4,
                        controls=[
                            ft.Text(label, size=11, color=C.TEXT_SECONDARY),
                            ft.Text(
                                value,
                                size=16,
                                weight=ft.FontWeight.W_700,
                                color=color,
                            ),
                        ],
                    ),
                )
            )
        controls.append(ft.Row(spacing=10, controls=metric_cards))

        if summary.submit_form is not None and summary.submit_action_url:
            if not self._selected_status and summary.status_options:
                self._selected_status = summary.status_options[0][0]
            form_controls: list[ft.Control] = [
                ft.Row(
                    spacing=8,
                    controls=[
                        ft.Icon(ft.Icons.HOW_TO_REG_ROUNDED, size=20, color=C.ACCENT),
                        ft.Text(
                            "Đang mở lượt tự điểm danh",
                            size=14,
                            weight=ft.FontWeight.W_700,
                            color=C.TEXT_PRIMARY,
                        ),
                    ],
                )
            ]
            if summary.status_options:
                rg = ft.RadioGroup(
                    value=self._selected_status,
                    on_change=lambda e: setattr(
                        self, "_selected_status", str(e.control.value or "")
                    ),
                    content=ft.Row(
                        wrap=True,
                        spacing=12,
                        controls=[
                            ft.Radio(value=val, label=lbl)
                            for val, lbl in summary.status_options
                        ],
                    ),
                )
                form_controls.append(rg)
            if summary.requires_password:
                form_controls.append(
                    ft.TextField(
                        label="Mật khẩu điểm danh (nếu giảng viên cung cấp)",
                        value=self._pwd_value,
                        dense=True,
                        border_color=C.BORDER,
                        color=C.TEXT_PRIMARY,
                        on_change=lambda e: setattr(
                            self, "_pwd_value", str(e.control.value or "")
                        ),
                    )
                )
            form_controls.append(
                ft.FilledButton(
                    "Gửi điểm danh ngay",
                    icon=ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED,
                    on_click=lambda _e, s=summary: self._submit_self_attendance(s),
                )
            )
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE_ELEVATED,
                    border=ft.Border.all(1, C.ACCENT),
                    border_radius=12,
                    padding=14,
                    content=ft.Column(spacing=10, controls=form_controls),
                )
            )

        if not summary.records:
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=18,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        "Chưa có lịch sử buổi điểm danh nào trên Moodle.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                )
            )
        else:
            for rec in summary.records:
                status_lower = (rec.status or "").strip().lower()
                is_present = (
                    "present" in status_lower
                    or "có mặt" in status_lower
                    or status_lower == "p"
                    or status_lower.startswith(("p ", "p("))
                )
                is_absent = (
                    "absent" in status_lower
                    or "vắng" in status_lower
                    or status_lower == "a"
                    or status_lower.startswith(("a ", "a("))
                )
                badge_color = (
                    C.SAFE if is_present else (C.CRITICAL if is_absent else C.WARNING)
                )
                controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.BORDER),
                        border_radius=10,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
                        content=ft.Row(
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Column(
                                    expand=True,
                                    spacing=2,
                                    controls=[
                                        ft.Text(
                                            rec.date_str or "Buổi học",
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=C.TEXT_PRIMARY,
                                        ),
                                        ft.Text(
                                            " · ".join(
                                                p for p in (rec.description, rec.remarks) if p
                                            )
                                            or "Điểm danh lớp học",
                                            size=12,
                                            color=C.TEXT_SECONDARY,
                                        ),
                                    ],
                                ),
                                ft.Container(
                                    bgcolor=C.SURFACE_ELEVATED,
                                    border=ft.Border.all(1, badge_color),
                                    border_radius=8,
                                    padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                    content=ft.Text(
                                        rec.status or "--",
                                        size=11,
                                        weight=ft.FontWeight.W_600,
                                        color=badge_color,
                                    ),
                                ),
                                ft.Text(
                                    rec.points or "",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=C.TEXT_PRIMARY,
                                ),
                            ],
                        ),
                    )
                )

        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)


class GroupActivityView(_BasePaneActivityView):
    """Native Pane-3 interactive student group selector (groupselect/choicegroup/choice)."""

    def __init__(
        self,
        *,
        source: CourseSource,
        course_id: int,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Optional[Callable[[str, str], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
        initial_summary: Optional[GroupActivitySummary] = None,
    ) -> None:
        super().__init__(
            title=module.name or "Nhóm học phần",
            subtitle="Xem thành viên & tham gia / rời nhóm",
            source=source,
            module=module,
            activity_service=activity_service,
            on_close=on_close,
            on_open_webview=on_open_webview,
            is_expanded=is_expanded,
            show_expand_toggle=show_expand_toggle,
            on_toggle_expand=on_toggle_expand,
        )
        self._course_id = course_id
        self._summary: Optional[GroupActivitySummary] = initial_summary
        self._filter_query: str = ""
        self._status_banner: str = ""
        if initial_summary is not None:
            self._render_summary(initial_summary)
        else:
            self._load_async()

    def _load_async(self) -> None:
        self._set_loading("Đang tải danh sách nhóm...")

        def _worker() -> None:
            summary = self._svc.fetch_group_activity(
                self._source, self._module, course_id=self._course_id
            )
            if self._disposed:
                return
            self._summary = summary
            self._render_summary(summary)

        threading.Thread(target=_worker, name="CourseGroupsFetch", daemon=True).start()

    def _execute_group_action(self, entry: CourseGroupEntry) -> None:
        self._set_loading(f"Đang cập nhật lựa chọn nhóm '{entry.name}'...")

        def _worker() -> None:
            ok, msg, refreshed = self._svc.submit_group_action(
                self._source,
                self._module,
                action_url=entry.action_url,
                fields=dict(entry.action_fields),
                choice_option_id=entry.choice_option_id,
                course_id=self._course_id,
            )
            if self._disposed:
                return
            self._status_banner = msg if ok else f"Lỗi: {msg}"
            self._summary = refreshed
            self._render_summary(refreshed)

        threading.Thread(target=_worker, name="CourseGroupsSubmit", daemon=True).start()

    def _render_summary(self, summary: GroupActivitySummary) -> None:
        self._title_text.value = summary.title or self._module.name
        controls: list[ft.Control] = []

        if self._status_banner or summary.message:
            controls.append(
                build_offline_banner(
                    self._status_banner or summary.message,
                    icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                )
            )

        if summary.my_group_names:
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE_ELEVATED,
                    border=ft.Border.all(1, C.SAFE),
                    border_radius=10,
                    padding=12,
                    content=ft.Row(
                        spacing=8,
                        controls=[
                            ft.Icon(ft.Icons.GROUP_ROUNDED, size=18, color=C.SAFE),
                            ft.Text(
                                f"Nhóm hiện tại của bạn: {', '.join(summary.my_group_names)}",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                expand=True,
                            ),
                        ],
                    ),
                )
            )

        if summary.intro:
            controls.append(
                ft.Text(summary.intro, size=12, color=C.TEXT_SECONDARY, selectable=True)
            )

        controls.append(
            ft.TextField(
                value=self._filter_query,
                hint_text="Tìm tên nhóm hoặc thành viên...",
                dense=True,
                prefix_icon=ft.Icons.SEARCH_ROUNDED,
                border_color=C.BORDER,
                color=C.TEXT_PRIMARY,
                on_change=lambda e: self._apply_filter(str(e.control.value or "")),
            )
        )

        q = self._filter_query.strip().lower()
        filtered = [
            g
            for g in summary.groups
            if not q
            or q in g.name.lower()
            or q in g.description.lower()
            or any(q in m.lower() for m in g.members)
        ]

        if not filtered:
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=18,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        "Không tìm thấy nhóm phù hợp.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                )
            )
        else:
            for g in filtered:
                action_btn: Optional[ft.Control] = None
                if g.is_member and g.can_leave:
                    action_btn = ft.OutlinedButton(
                        "Rời nhóm",
                        icon=ft.Icons.EXIT_TO_APP_ROUNDED,
                        on_click=lambda _e, item=g: self._execute_group_action(item),
                    )
                elif not g.is_member and g.can_join:
                    action_btn = ft.FilledButton(
                        "Tham gia nhóm",
                        icon=ft.Icons.GROUP_ADD_ROUNDED,
                        on_click=lambda _e, item=g: self._execute_group_action(item),
                    )
                elif g.is_member:
                    action_btn = ft.Container(
                        bgcolor=C.SURFACE_ELEVATED,
                        border=ft.Border.all(1, C.SAFE),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                        content=ft.Text("Đã tham gia", size=11, color=C.SAFE, weight=ft.FontWeight.W_600),
                    )

                card_rows: list[ft.Control] = [
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(
                                ft.Icons.GROUPS_ROUNDED,
                                size=20,
                                color=C.SAFE if g.is_member else C.ACCENT,
                            ),
                            ft.Column(
                                expand=True,
                                spacing=2,
                                controls=[
                                    ft.Text(
                                        g.name,
                                        size=14,
                                        weight=ft.FontWeight.W_600,
                                        color=C.TEXT_PRIMARY,
                                    ),
                                    *(
                                        [
                                            ft.Text(
                                                g.description,
                                                size=12,
                                                color=C.TEXT_SECONDARY,
                                            )
                                        ]
                                        if g.description
                                        else []
                                    ),
                                ],
                            ),
                            *(
                                [
                                    ft.Text(
                                        f"Sĩ số: {g.member_count_label}",
                                        size=12,
                                        color=C.TEXT_SECONDARY,
                                    )
                                ]
                                if g.member_count_label
                                else []
                            ),
                            *([action_btn] if action_btn is not None else []),
                        ],
                    )
                ]
                if g.members:
                    card_rows.append(
                        ft.Text(
                            f"Thành viên ({len(g.members)}): {', '.join(g.members[:16])}"
                            + (" ..." if len(g.members) > 16 else ""),
                            size=11,
                            color=C.TEXT_SECONDARY,
                            selectable=True,
                        )
                    )
                controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.SAFE if g.is_member else C.BORDER),
                        border_radius=10,
                        padding=12,
                        content=ft.Column(spacing=6, controls=card_rows),
                    )
                )

        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)

    def _apply_filter(self, val: str) -> None:
        self._filter_query = val
        if self._summary is not None:
            self._render_summary(self._summary)


class QuizActivityView(_BasePaneActivityView):
    """Native Pane-3 Quiz details, attempt review, and in-app WebView attempt launch."""

    def __init__(
        self,
        *,
        source: CourseSource,
        course_id: int,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Callable[[str, str], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(
            title=module.name or "Bài kiểm tra / Quiz",
            subtitle="Thông tin bài kiểm tra & lịch sử làm bài",
            source=source,
            module=module,
            activity_service=activity_service,
            on_close=on_close,
            on_open_webview=on_open_webview,
            is_expanded=is_expanded,
            show_expand_toggle=show_expand_toggle,
            on_toggle_expand=on_toggle_expand,
        )
        self._course_id = course_id
        self._review_cache: dict[int, QuizAttemptReviewReport] = {}
        self._review_filter: str = "all"
        self._review_search: str = ""
        self._load_async()

    def _load_async(self) -> None:
        self._set_loading("Đang tải thông tin Quiz...")

        def _worker() -> None:
            summary = self._svc.fetch_quiz_activity(
                self._source, self._module, course_id=self._course_id
            )
            if self._disposed:
                return
            self._render_quiz(summary)

        threading.Thread(target=_worker, name="CourseQuizFetch", daemon=True).start()

    def _scroll_review_to_question(self, list_view: ft.ListView, q_num: str) -> None:
        async def _do_scroll() -> None:
            try:
                res = list_view.scroll_to(scroll_key=f"quiz-q-{q_num}", duration=220)
                if inspect.isawaitable(res):
                    await res
            except Exception:
                pass

        try:
            page = self.page
        except Exception:
            page = None
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            run_task(_do_scroll)
            return
        try:
            asyncio.get_running_loop().create_task(_do_scroll())
        except RuntimeError:
            pass

    @staticmethod
    def _question_semantic_color(state_kind: str) -> str:
        if state_kind == "correct":
            return C.SAFE
        if state_kind in {"incorrect", "unanswered"}:
            return C.CRITICAL
        if state_kind == "partial":
            return C.WARNING
        return C.ACCENT

    def _build_review_question_card(self, q: QuizReviewQuestion) -> ft.Control:
        accent = self._question_semantic_color(q.state_kind)
        is_info = q.display_number == "Thông tin"
        title_label = "Nội dung dẫn đề" if is_info else f"Câu {q.display_number}"
        score_part = f" · {q.mark}/{q.max_mark}" if (q.mark and q.max_mark) else ""
        status_icon = (
            ft.Icons.CHECK_CIRCLE_ROUNDED
            if q.state_kind == "correct"
            else (
                ft.Icons.CANCEL_ROUNDED
                if q.state_kind in {"incorrect", "unanswered"}
                else (
                    ft.Icons.TIMELAPSE_ROUNDED
                    if q.state_kind == "partial"
                    else ft.Icons.INFO_OUTLINE_ROUNDED
                )
            )
        )

        header_items: list[ft.Control] = [
            ft.Text(
                title_label,
                size=14,
                weight=ft.FontWeight.W_700,
                color=C.TEXT_PRIMARY,
            ),
        ]
        if q.question_code:
            header_items.append(
                ft.Container(
                    bgcolor=C.BG,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=5,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                    content=ft.Text(
                        q.question_code,
                        size=10,
                        color=C.TEXT_SECONDARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                )
            )
        header_items.append(ft.Container(expand=True))
        if not is_info and (q.status_label or score_part):
            header_items.append(
                ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.12, accent),
                    border=ft.Border.all(1, accent),
                    border_radius=999,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    content=ft.Row(
                        tight=True,
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(status_icon, size=13, color=accent),
                            ft.Text(
                                f"{q.status_label or 'Đã chấm'}{score_part}",
                                size=11,
                                weight=ft.FontWeight.W_600,
                                color=accent,
                            ),
                        ],
                    ),
                )
            )

        body_controls: list[ft.Control] = [
            ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=header_items,
            ),
            ft.Text(
                q.question_text or "(Không có văn bản câu hỏi)",
                size=14,
                color=C.TEXT_PRIMARY,
                selectable=True,
            ),
        ]

        # Render structured choices (A, B, C, D / True-False / Matching)
        if q.choices:
            choice_rows: list[ft.Control] = []
            for ch in q.choices:
                if ch.state == "correct":
                    ch_bg = ft.Colors.with_opacity(0.12, C.SAFE)
                    ch_border = C.SAFE
                    ch_icon = ft.Icons.CHECK_CIRCLE_ROUNDED
                    ch_icon_color = C.SAFE
                    tag_text = "Bạn chọn · Đúng" if ch.is_selected else "Đáp án đúng"
                    tag_color = C.SAFE
                elif ch.state == "incorrect":
                    ch_bg = ft.Colors.with_opacity(0.12, C.CRITICAL)
                    ch_border = C.CRITICAL
                    ch_icon = ft.Icons.CANCEL_ROUNDED
                    ch_icon_color = C.CRITICAL
                    tag_text = "Bạn chọn · Sai" if ch.is_selected else "Sai"
                    tag_color = C.CRITICAL
                elif ch.state == "partial":
                    ch_bg = ft.Colors.with_opacity(0.12, C.WARNING)
                    ch_border = C.WARNING
                    ch_icon = ft.Icons.ERROR_OUTLINE_ROUNDED
                    ch_icon_color = C.WARNING
                    tag_text = "Bạn chọn · Một phần" if ch.is_selected else ""
                    tag_color = C.WARNING
                elif ch.is_selected:
                    ch_bg = ft.Colors.with_opacity(0.10, C.ACCENT)
                    ch_border = C.ACCENT
                    ch_icon = ft.Icons.RADIO_BUTTON_CHECKED_ROUNDED
                    ch_icon_color = C.ACCENT
                    tag_text = "Bạn chọn"
                    tag_color = C.ACCENT
                else:
                    ch_bg = C.BG
                    ch_border = C.BORDER
                    ch_icon = ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED
                    ch_icon_color = C.TEXT_SECONDARY
                    tag_text = ""
                    tag_color = C.TEXT_SECONDARY

                option_children: list[ft.Control] = [
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Icon(ch_icon, size=17, color=ch_icon_color),
                            *(
                                [
                                    ft.Text(
                                        f"{ch.label}.",
                                        size=13,
                                        weight=ft.FontWeight.W_700,
                                        color=ch_icon_color
                                        if ch.state != "neutral" or ch.is_selected
                                        else C.TEXT_SECONDARY,
                                    )
                                ]
                                if ch.label
                                else []
                            ),
                            ft.Text(
                                ch.text,
                                size=13,
                                weight=ft.FontWeight.W_600
                                if (ch.is_selected or ch.state == "correct")
                                else ft.FontWeight.W_400,
                                color=C.TEXT_PRIMARY,
                                selectable=True,
                                expand=True,
                            ),
                            *(
                                [
                                    ft.Container(
                                        bgcolor=ft.Colors.with_opacity(0.14, tag_color),
                                        border_radius=6,
                                        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                                        content=ft.Text(
                                            tag_text,
                                            size=10,
                                            weight=ft.FontWeight.W_600,
                                            color=tag_color,
                                        ),
                                    )
                                ]
                                if tag_text
                                else []
                            ),
                        ],
                    )
                ]
                if ch.feedback:
                    option_children.append(
                        ft.Text(
                            f"↳ {ch.feedback}",
                            size=11,
                            italic=True,
                            color=C.TEXT_SECONDARY,
                            selectable=True,
                        )
                    )
                choice_rows.append(
                    ft.Container(
                        bgcolor=ch_bg,
                        border=ft.Border.all(1, ch_border),
                        border_radius=8,
                        padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                        content=ft.Column(spacing=4, controls=option_children),
                    )
                )
            body_controls.append(ft.Column(spacing=6, controls=choice_rows))
        elif q.student_response_text:
            body_controls.append(
                ft.Container(
                    bgcolor=C.BG,
                    border=ft.Border.all(1, accent),
                    border_radius=8,
                    padding=10,
                    content=ft.Column(
                        spacing=3,
                        controls=[
                            ft.Text(
                                "Câu trả lời đã nộp:",
                                size=11,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                q.student_response_text,
                                size=13,
                                color=C.TEXT_PRIMARY,
                                selectable=True,
                            ),
                        ],
                    ),
                )
            )

        # Official right answer / teacher explanation box
        already_highlighted_right = any(c.state == "correct" for c in q.choices)
        show_right_line = bool(
            q.right_answer_text
            and (q.state_kind != "correct" or not already_highlighted_right)
        )
        feedback_lines: list[ft.Control] = []
        if show_right_line:
            feedback_lines.append(
                ft.Row(
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Icon(ft.Icons.LIGHTBULB_OUTLINE_ROUNDED, size=15, color=C.SAFE),
                        ft.Text(
                            f"Đáp án đúng: {q.right_answer_text}",
                            size=12,
                            weight=ft.FontWeight.W_600,
                            color=C.TEXT_PRIMARY,
                            selectable=True,
                            expand=True,
                        ),
                    ],
                )
            )
        if q.specific_feedback:
            feedback_lines.append(
                ft.Text(
                    q.specific_feedback,
                    size=12,
                    color=C.TEXT_SECONDARY,
                    selectable=True,
                )
            )
        if q.general_feedback:
            feedback_lines.append(
                ft.Text(
                    f"Giải thích: {q.general_feedback}",
                    size=12,
                    color=C.TEXT_PRIMARY,
                    selectable=True,
                )
            )
        if feedback_lines:
            body_controls.append(
                ft.Container(
                    bgcolor=C.BG,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=8,
                    padding=10,
                    content=ft.Column(spacing=4, controls=feedback_lines),
                )
            )

        return ft.Container(
            key=ft.ScrollKey(f"quiz-q-{q.display_number}"),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            padding=14,
            content=ft.Column(spacing=10, controls=body_controls),
        )

    def _render_attempt_review(
        self,
        summary: QuizActivitySummary,
        att_info: Optional[QuizAttemptInfo],
        report: QuizAttemptReviewReport,
    ) -> None:
        all_questions = list(report.questions)
        graded_questions = [q for q in all_questions if q.display_number != "Thông tin"]
        total_q = len(graded_questions)
        wrong_total = report.incorrect_count + report.unanswered_count
        accuracy = (report.correct_count / total_q) if total_q > 0 else 0.0

        query_l = self._review_search.strip().lower()
        filtered: list[QuizReviewQuestion] = []
        for q in all_questions:
            if self._review_filter == "wrong" and q.state_kind not in {"incorrect", "unanswered"}:
                continue
            if self._review_filter == "correct" and q.state_kind != "correct":
                continue
            if self._review_filter == "partial" and q.state_kind != "partial":
                continue
            if query_l:
                hay = " ".join(
                    [
                        q.question_text,
                        q.right_answer_text,
                        q.general_feedback,
                        *(c.text for c in q.choices),
                    ]
                ).lower()
                if query_l not in hay:
                    continue
            filtered.append(q)

        def _set_filter(flt: str) -> None:
            self._review_filter = flt
            self._render_attempt_review(summary, att_info, report)

        def _set_search(val: str) -> None:
            self._review_search = str(val or "")
            self._render_attempt_review(summary, att_info, report)

        review_list_view = ft.ListView(
            expand=True,
            padding=14,
            spacing=12,
        )

        # 1. Top back + WebView action row
        review_web_url = (att_info.review_url if att_info is not None else "") or summary.web_url
        top_bar = ft.Row(
            wrap=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.OutlinedButton(
                    "← Quay lại tổng quan Quiz",
                    on_click=lambda _e: self._render_quiz(summary),
                ),
                *(
                    [
                        ft.TextButton(
                            "Xem bản gốc trên WebView",
                            icon=ft.Icons.WEB_ROUNDED,
                            on_click=lambda _e, u=review_web_url: self._on_open_webview(
                                summary.title, u
                            ),
                        )
                    ]
                    if review_web_url and self._on_open_webview is not None
                    else []
                ),
            ],
        )

        # 2. Score & Accuracy Hero Card
        score_headline = (
            report.grade_text
            or (att_info.score_text if att_info is not None else "")
            or f"{report.correct_count}/{total_q} câu đúng"
        )
        meta_bits = [f"Lần {att_info.attempt_number if att_info else report.attempt_number}"]
        if report.duration_text:
            meta_bits.append(f"Thời gian làm: {report.duration_text}")
        if report.finished_at_text or (att_info and att_info.time_submitted):
            meta_bits.append(
                f"Nộp lúc: {report.finished_at_text or (att_info.time_submitted if att_info else '')}"
            )

        filter_chips: list[ft.Control] = [
            build_filter_chip(
                f"Tất cả ({total_q})",
                selected=self._review_filter == "all",
                bold_selected=True,
                on_select=lambda _e: _set_filter("all"),
            ),
            build_filter_chip(
                f"Câu sai ({wrong_total})",
                selected=self._review_filter == "wrong",
                bold_selected=True,
                selected_color=C.CRITICAL,
                selected_text_color=C.ON_CRITICAL,
                on_select=lambda _e: _set_filter("wrong"),
            ),
            build_filter_chip(
                f"Câu đúng ({report.correct_count})",
                selected=self._review_filter == "correct",
                bold_selected=True,
                selected_color=C.SAFE,
                selected_text_color=C.ON_SAFE,
                on_select=lambda _e: _set_filter("correct"),
            ),
        ]
        if report.partial_count > 0:
            filter_chips.append(
                build_filter_chip(
                    f"Một phần ({report.partial_count})",
                    selected=self._review_filter == "partial",
                    bold_selected=True,
                    selected_color=C.WARNING,
                    selected_text_color=C.ON_WARNING,
                    on_select=lambda _e: _set_filter("partial"),
                )
            )

        jump_badges: list[ft.Control] = []
        for gq in graded_questions[:120]:
            q_col = self._question_semantic_color(gq.state_kind)
            is_colored = gq.state_kind in {"correct", "incorrect", "unanswered", "partial"}
            jump_badges.append(
                ft.Container(
                    width=34,
                    height=28,
                    alignment=ft.Alignment.CENTER,
                    bgcolor=ft.Colors.with_opacity(0.14, q_col) if is_colored else C.BG,
                    border=ft.Border.all(1, q_col if is_colored else C.BORDER),
                    border_radius=6,
                    ink=True,
                    tooltip=f"Nhảy tới Câu {gq.display_number} ({gq.status_label or 'Đã chấm'})",
                    on_click=lambda _e, qn=gq.display_number: self._scroll_review_to_question(
                        review_list_view, qn
                    ),
                    content=ft.Text(
                        gq.display_number,
                        size=11,
                        weight=ft.FontWeight.W_700,
                        color=q_col if is_colored else C.TEXT_PRIMARY,
                    ),
                )
            )

        summary_card = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=12,
            padding=14,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        wrap=True,
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Column(
                                spacing=2,
                                controls=[
                                    ft.Text(
                                        f"Kết quả: {score_headline}",
                                        size=16,
                                        weight=ft.FontWeight.W_700,
                                        color=C.TEXT_PRIMARY,
                                    ),
                                    ft.Text(
                                        " · ".join(meta_bits),
                                        size=12,
                                        color=C.TEXT_SECONDARY,
                                    ),
                                ],
                            ),
                            ft.Container(
                                bgcolor=C.SURFACE_ELEVATED,
                                border=ft.Border.all(1, C.BORDER),
                                border_radius=8,
                                padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                                content=ft.Text(
                                    f"Đúng {report.correct_count}/{total_q} ({int(round(accuracy * 100))}%)",
                                    size=12,
                                    weight=ft.FontWeight.W_600,
                                    color=C.TEXT_PRIMARY,
                                ),
                            ),
                        ],
                    ),
                    ft.Divider(height=1, thickness=0.6, color=C.BORDER),
                    ft.Row(
                        wrap=True,
                        spacing=8,
                        run_spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            *filter_chips,
                            ft.TextField(
                                value=self._review_search,
                                hint_text="Tìm câu hỏi / đáp án...",
                                width=210,
                                height=32,
                                dense=True,
                                text_size=12,
                                color=C.TEXT_PRIMARY,
                                bgcolor=C.BG,
                                border_color=C.BORDER,
                                on_submit=lambda e: _set_search(str(e.control.value or "")),
                            ),
                        ],
                    ),
                    *(
                        [
                            ft.Divider(height=4, color=C.BORDER),
                            ft.Row(
                                wrap=True,
                                spacing=5,
                                run_spacing=5,
                                controls=jump_badges,
                            ),
                        ]
                        if len(jump_badges) > 1
                        else []
                    ),
                ],
            ),
        )

        question_cards: list[ft.Control] = [
            self._build_review_question_card(q) for q in filtered
        ]
        if not question_cards:
            question_cards.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=20,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Text(
                        "Không có câu hỏi nào khớp bộ lọc hiện tại.",
                        size=13,
                        color=C.TEXT_SECONDARY,
                    ),
                )
            )

        review_list_view.controls = [top_bar, summary_card, *question_cards]
        self._body_host.content = review_list_view
        safe_control_update(self)

    def _open_attempt_review(
        self,
        summary: QuizActivitySummary,
        attempt_id: int,
        att_info: Optional[QuizAttemptInfo] = None,
    ) -> None:
        self._review_filter = "all"
        self._review_search = ""
        cached_report = self._review_cache.get(attempt_id)
        if cached_report is not None and cached_report.questions:
            self._render_attempt_review(summary, att_info, cached_report)
            return

        self._set_loading("Đang tải chi tiết bài đã làm, lựa chọn & đáp án...")

        def _worker() -> None:
            rev_url = att_info.review_url if att_info is not None else ""
            if hasattr(self._svc, "fetch_quiz_review_report"):
                report = self._svc.fetch_quiz_review_report(
                    attempt_id, source=self._source, review_url=rev_url
                )
            else:
                legacy_items = self._svc.fetch_quiz_review(attempt_id)
                report = QuizAttemptReviewReport(
                    attempt_id=attempt_id,
                    questions=tuple(
                        QuizReviewQuestion(
                            display_number=str(item.get("number") or idx),
                            slot=str(item.get("slot") or idx),
                            status_label=str(item.get("status") or ""),
                            mark=str(item.get("mark") or ""),
                            max_mark=str(item.get("maxmark") or ""),
                            question_text=str(item.get("question_text") or ""),
                        )
                        for idx, item in enumerate(legacy_items, start=1)
                    ),
                )
            if self._disposed:
                return
            if not report.questions:
                self._render_quiz(
                    summary,
                    notice="Bài làm này chưa cho phép xem lại chi tiết qua API; hãy mở WebView ngay bên dưới.",
                )
                return
            self._review_cache[attempt_id] = report
            self._render_attempt_review(summary, att_info, report)

        threading.Thread(target=_worker, name="CourseQuizReview", daemon=True).start()

    def _render_quiz(self, summary: QuizActivitySummary, notice: str = "") -> None:
        self._title_text.value = summary.title
        controls: list[ft.Control] = []
        if notice:
            controls.append(build_offline_banner(notice))

        meta_rows = [
            ("Mở bài", _format_unix_dt(summary.time_open)),
            ("Đóng bài", _format_unix_dt(summary.time_close)),
            ("Thời gian làm bài", _format_duration_sec(summary.time_limit_seconds)),
            (
                "Số lượt làm tối đa",
                str(summary.max_attempts) if summary.max_attempts > 0 else "Không giới hạn",
            ),
        ]
        if summary.grade_method:
            meta_rows.append(("Cách tính điểm", summary.grade_method))

        controls.append(
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=14,
                content=ft.Column(
                    spacing=8,
                    controls=[
                        *[
                            ft.Row(
                                controls=[
                                    ft.Text(k, size=12, color=C.TEXT_SECONDARY, width=150),
                                    ft.Text(
                                        v,
                                        size=12,
                                        weight=ft.FontWeight.W_600,
                                        color=C.TEXT_PRIMARY,
                                        expand=True,
                                    ),
                                ]
                            )
                            for k, v in meta_rows
                        ],
                        *(
                            [
                                ft.Divider(height=8, color=C.BORDER),
                                ft.Text(summary.intro, size=13, color=C.TEXT_PRIMARY, selectable=True),
                            ]
                            if summary.intro
                            else []
                        ),
                    ],
                ),
            )
        )

        if summary.prevent_reasons:
            controls.append(
                build_offline_banner(
                    "Lưu ý truy cập: " + " · ".join(summary.prevent_reasons),
                    icon=ft.Icons.LOCK_OUTLINE_ROUNDED,
                )
            )

        if summary.web_url and self._on_open_webview is not None:
            controls.append(
                ft.FilledButton(
                    "Làm bài / Xem chi tiết ngay trong app (WebView)",
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    on_click=lambda _e: self._on_open_webview(summary.title, summary.web_url),
                )
            )

        if summary.attempts:
            controls.append(
                ft.Text(
                    f"Lịch sử làm bài ({len(summary.attempts)} lượt)",
                    size=13,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                )
            )
            for att in summary.attempts:
                controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.BORDER),
                        border_radius=10,
                        padding=12,
                        content=ft.Row(
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Column(
                                    expand=True,
                                    spacing=2,
                                    controls=[
                                        ft.Text(
                                            f"Lần {att.attempt_number} · {att.state}",
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=C.TEXT_PRIMARY,
                                        ),
                                        ft.Text(
                                            f"Điểm: {att.score_text or '--'}"
                                            + (
                                                f" · Nộp lúc {att.time_submitted}"
                                                if att.time_submitted
                                                else ""
                                            ),
                                            size=12,
                                            color=C.TEXT_SECONDARY,
                                        ),
                                    ],
                                ),
                                ft.OutlinedButton(
                                    "Xem lại",
                                    icon=ft.Icons.FACT_CHECK_OUTLINED,
                                    on_click=lambda _e, aid=att.attempt_id, a_obj=att: self._open_attempt_review(
                                        summary, aid, a_obj
                                    ),
                                ),
                            ],
                        ),
                    )
                )

        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)


class ForumActivityView(_BasePaneActivityView):
    """Native Pane-3 Moodle Forum discussions & threaded posts viewer."""

    def __init__(
        self,
        *,
        source: CourseSource,
        course_id: int,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Optional[Callable[[str, str], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(
            title=module.name or "Diễn đàn",
            subtitle="Chủ đề thảo luận & thông báo",
            source=source,
            module=module,
            activity_service=activity_service,
            on_close=on_close,
            on_open_webview=on_open_webview,
            is_expanded=is_expanded,
            show_expand_toggle=show_expand_toggle,
            on_toggle_expand=on_toggle_expand,
        )
        self._course_id = course_id
        self._summary: Optional[ForumActivitySummary] = None
        self._load_async()

    def _load_async(self) -> None:
        self._set_loading("Đang tải danh sách chủ đề...")

        def _worker() -> None:
            summary = self._svc.fetch_forum_activity(
                self._source, self._module, course_id=self._course_id
            )
            if self._disposed:
                return
            self._summary = summary
            self._render_discussions(summary)

        threading.Thread(target=_worker, name="CourseForumFetch", daemon=True).start()

    def _open_discussion(self, summary: ForumActivitySummary, disc: ForumDiscussionEntry) -> None:
        if disc.discussion_id <= 0 and disc.discussion_url and self._on_open_webview is not None:
            self._on_open_webview(disc.name, disc.discussion_url)
            return
        self._set_loading(f"Đang tải bài viết trong '{disc.name}'...")

        def _worker() -> None:
            posts = self._svc.fetch_discussion_posts(self._source, disc.discussion_id)
            if self._disposed:
                return
            if not posts and disc.discussion_url and self._on_open_webview is not None:
                self._on_open_webview(disc.name, disc.discussion_url)
                return
            self._render_posts(summary, disc, posts)

        threading.Thread(target=_worker, name="CourseForumPosts", daemon=True).start()

    def _render_discussions(self, summary: ForumActivitySummary) -> None:
        self._title_text.value = summary.title
        controls: list[ft.Control] = []
        if summary.intro:
            controls.append(
                ft.Text(summary.intro, size=12, color=C.TEXT_SECONDARY, selectable=True)
            )
        if not summary.discussions:
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=20,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Column(
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                        controls=[
                            ft.Text(
                                "Chưa có chủ đề thảo luận nào được trả về qua API.",
                                size=13,
                                color=C.TEXT_SECONDARY,
                            ),
                            ft.FilledButton(
                                "Mở diễn đàn trong WebView",
                                icon=ft.Icons.WEB_ROUNDED,
                                on_click=lambda _e: self._launch_in_webview(),
                            ),
                        ],
                    ),
                )
            )
        else:
            for disc in summary.discussions:
                controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.ACCENT if disc.pinned else C.BORDER),
                        border_radius=10,
                        padding=12,
                        ink=True,
                        on_click=lambda _e, d=disc: self._open_discussion(summary, d),
                        content=ft.Column(
                            spacing=4,
                            controls=[
                                ft.Row(
                                    spacing=6,
                                    controls=[
                                        *(
                                            [ft.Icon(ft.Icons.PUSH_PIN_ROUNDED, size=14, color=C.ACCENT)]
                                            if disc.pinned
                                            else []
                                        ),
                                        ft.Text(
                                            disc.name,
                                            size=14,
                                            weight=ft.FontWeight.W_600,
                                            color=C.TEXT_PRIMARY,
                                            expand=True,
                                        ),
                                        ft.Text(
                                            f"{disc.num_replies} phản hồi",
                                            size=11,
                                            color=C.TEXT_SECONDARY,
                                        ),
                                    ],
                                ),
                                *(
                                    [
                                        ft.Text(
                                            disc.author,
                                            size=11,
                                            color=C.ACCENT,
                                        )
                                    ]
                                    if disc.author
                                    else []
                                ),
                                *(
                                    [
                                        ft.Text(
                                            disc.message_preview,
                                            size=12,
                                            color=C.TEXT_SECONDARY,
                                            max_lines=2,
                                            overflow=ft.TextOverflow.ELLIPSIS,
                                        )
                                    ]
                                    if disc.message_preview
                                    else []
                                ),
                            ],
                        ),
                    )
                )

        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)

    def _render_posts(
        self,
        summary: ForumActivitySummary,
        disc: ForumDiscussionEntry,
        posts: list[ForumPostEntry],
    ) -> None:
        controls: list[ft.Control] = [
            ft.Row(
                spacing=8,
                controls=[
                    ft.OutlinedButton(
                        "← Danh sách chủ đề",
                        on_click=lambda _e: self._render_discussions(summary),
                    ),
                    ft.Text(
                        disc.name,
                        size=14,
                        weight=ft.FontWeight.W_700,
                        color=C.TEXT_PRIMARY,
                        expand=True,
                    ),
                ],
            )
        ]
        for post in posts:
            controls.append(
                ft.Container(
                    bgcolor=C.SURFACE,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=12,
                    content=ft.Column(
                        spacing=6,
                        controls=[
                            ft.Row(
                                controls=[
                                    ft.Text(
                                        post.author,
                                        size=12,
                                        weight=ft.FontWeight.W_600,
                                        color=C.ACCENT,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        post.subject,
                                        size=11,
                                        color=C.TEXT_SECONDARY,
                                    ),
                                ]
                            ),
                            ft.Markdown(
                                post.message_text or "(Không có nội dung)",
                                selectable=True,
                                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                            ),
                            *(
                                [
                                    ft.Text(
                                        "Đính kèm: " + ", ".join(post.attachments),
                                        size=11,
                                        color=C.TEXT_SECONDARY,
                                    )
                                ]
                                if post.attachments
                                else []
                            ),
                        ],
                    ),
                )
            )
        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)


class RichContentActivityView(_BasePaneActivityView):
    """Native Pane-3 chapter/page reader for Moodle ``mod_book`` and ``mod_page``."""

    def __init__(
        self,
        *,
        source: CourseSource,
        course_id: int,
        module: CourseModuleItem,
        activity_service: CourseActivityService,
        on_close: Callable[[], None],
        on_open_webview: Optional[Callable[[str, str], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(
            title=module.name or "Nội dung bài học",
            subtitle=f"{module.modname.upper()} · Đọc trực tiếp",
            source=source,
            module=module,
            activity_service=activity_service,
            on_close=on_close,
            on_open_webview=on_open_webview,
            is_expanded=is_expanded,
            show_expand_toggle=show_expand_toggle,
            on_toggle_expand=on_toggle_expand,
        )
        self._course_id = course_id
        self._active_chapter_idx = 0
        self._load_async()

    def _load_async(self) -> None:
        self._set_loading("Đang tải nội dung bài học...")

        def _worker() -> None:
            rich_page = self._svc.fetch_rich_content(
                self._source, self._module, course_id=self._course_id
            )
            if self._disposed:
                return
            self._render_page(rich_page)

        threading.Thread(target=_worker, name="CourseRichPageFetch", daemon=True).start()

    def _render_page(self, rich_page: RichActivityPage) -> None:
        self._title_text.value = rich_page.title
        chapters = rich_page.chapters
        if not chapters:
            self._body_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                padding=24,
                content=ft.Column(
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                    controls=[
                        ft.Text(
                            rich_page.intro_markdown or "Chưa có nội dung văn bản cho mục này.",
                            size=13,
                            color=C.TEXT_SECONDARY,
                        ),
                        ft.FilledButton(
                            "Mở trang trong WebView",
                            icon=ft.Icons.WEB_ROUNDED,
                            on_click=lambda _e: self._launch_in_webview(),
                        ),
                    ],
                ),
            )
            safe_control_update(self)
            return

        self._active_chapter_idx = max(0, min(self._active_chapter_idx, len(chapters) - 1))
        current = chapters[self._active_chapter_idx]
        controls: list[ft.Control] = []
        if len(chapters) > 1:
            controls.append(
                ft.Row(
                    wrap=True,
                    spacing=6,
                    run_spacing=6,
                    controls=[
                        ft.OutlinedButton(
                            ch.title,
                            on_click=lambda _e, idx=i: self._select_chapter(rich_page, idx),
                        )
                        for i, ch in enumerate(chapters)
                    ],
                )
            )
        controls.append(
            ft.Container(
                bgcolor=C.SURFACE,
                border=ft.Border.all(1, C.BORDER),
                border_radius=12,
                padding=16,
                content=ft.Markdown(
                    current.content_markdown or "(Chương trống — nhấn mở WebView để xem chi tiết)",
                    selectable=True,
                    extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                    on_tap_link=lambda e: (
                        self._on_open_webview(current.title, str(getattr(e, "data", "") or ""))
                        if self._on_open_webview is not None and getattr(e, "data", "")
                        else None
                    ),
                ),
            )
        )
        self._body_host.content = ft.ListView(
            expand=True,
            padding=14,
            spacing=10,
            controls=controls,
        )
        safe_control_update(self)

    def _select_chapter(self, rich_page: RichActivityPage, idx: int) -> None:
        self._active_chapter_idx = idx
        self._render_page(rich_page)


class PaneAssignmentView(ft.Container):
    """Wraps a DetailView instance inside Pane 3 with animated loading, error recovery & 2+3 expand."""

    def __init__(
        self,
        *,
        detail_view: Any,
        title: str,
        on_close: Callable[[], None],
        on_refresh: Optional[Callable[[], None]] = None,
        on_open_webview: Optional[Callable[[], None]] = None,
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._detail_view = detail_view
        self._title = title or "Bài tập"
        self._on_close = on_close
        self._on_refresh = on_refresh
        self._on_open_webview = on_open_webview
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand
        self._is_loading = True

        self._refresh_btn = ft.IconButton(
            icon=ft.Icons.REFRESH_ROUNDED,
            icon_size=18,
            icon_color=C.TEXT_PRIMARY,
            tooltip="Tải lại chi tiết bài tập",
            visible=callable(on_refresh),
            on_click=lambda _e: self._on_refresh() if callable(self._on_refresh) else None,
        )
        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: self._on_toggle_expand()
            if callable(self._on_toggle_expand)
            else None,
            visible=self._show_expand_toggle,
        )
        self._webview_btn = ft.IconButton(
            icon=ft.Icons.WEB_ROUNDED,
            icon_size=18,
            icon_color=C.TEXT_PRIMARY,
            tooltip="Mở bằng trình duyệt trong ứng dụng",
            visible=callable(on_open_webview),
            on_click=lambda _e: self._on_open_webview() if callable(self._on_open_webview) else None,
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=self._title,
            subtitle="Đang đồng bộ chi tiết bài tập từ Moodle...",
            back_tooltip="Quay lại môn học",
            on_back=lambda _e: self._on_close(),
            trailing_actions=[self._refresh_btn, self._expand_btn, self._webview_btn],
        )

        self._loading_skeleton = build_assignment_loading_skeleton(
            "Đang tải đề bài, hạn nộp và trạng thái nộp bài từ Moodle...",
            title=self._title,
        )
        self._detail_view.visible = True
        self._detail_view.opacity = 1.0
        self._error_strip = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.WARNING),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
            margin=ft.Margin.only(left=16, right=16, top=10),
            visible=False,
            content=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=18, color=C.WARNING),
                    ft.Text(
                        "Không thể tải đầy đủ đề bài hoặc trạng thái nộp bài.",
                        size=12,
                        color=C.TEXT_PRIMARY,
                        expand=True,
                    ),
                    *(
                        [
                            ft.TextButton(
                                "Thử lại",
                                on_click=lambda _e: self._on_refresh()
                                if callable(self._on_refresh)
                                else None,
                            )
                        ]
                        if callable(on_refresh)
                        else []
                    ),
                    *(
                        [
                            ft.OutlinedButton(
                                "Mở trong app",
                                on_click=lambda _e: self._on_open_webview()
                                if callable(self._on_open_webview)
                                else None,
                            )
                        ]
                        if callable(on_open_webview)
                        else []
                    ),
                ],
            ),
        )
        self._body_switcher = ft.AnimatedSwitcher(
            content=self._loading_skeleton,
            expand=True,
            duration=180,
            reverse_duration=140,
            transition=ft.AnimatedSwitcherTransition.FADE,
        )
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                self._error_strip,
                self._body_switcher,
            ],
        )

    def show_loading(self, data: dict[str, Any]) -> None:
        self._is_loading = True
        item_title = str(data.get("title") or self._title or "Bài tập")
        self._title_text.value = item_title
        self._subtitle_text.value = "Đang tải chi tiết & trạng thái nộp bài từ Moodle..."
        self._error_strip.visible = False
        self._loading_skeleton = build_assignment_loading_skeleton(
            "Đang tải đề bài, hạn nộp và trạng thái nộp bài từ Moodle...",
            title=item_title,
        )
        self._body_switcher.content = self._loading_skeleton
        self._detail_view.show_loading(data)
        safe_control_update(self)

    def update_detail(self, full_data: dict[str, Any]) -> None:
        self._is_loading = False
        self._error_strip.visible = False
        self._subtitle_text.value = "Xem đề bài & nộp bài trực tiếp trong môn học"
        self._detail_view.update_detail(full_data)
        self._body_switcher.content = self._detail_view
        safe_control_update(self)

    def show_error_banner(self) -> None:
        self._is_loading = False
        self._subtitle_text.value = "Không thể đồng bộ đầy đủ dữ liệu"
        self._error_strip.visible = True
        self._detail_view.show_error_banner()
        self._body_switcher.content = self._detail_view
        safe_control_update(self)

    def set_expanded(self, expanded: bool, *, visible: bool = True) -> None:
        self._expanded = bool(expanded)
        self._show_expand_toggle = bool(visible)
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )

    def dispose(self) -> None:
        self._detail_view.visible = False

    def update_theme(self) -> None:
        self.bgcolor = C.BG
        refresh_viewer_header(
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        )
        sync_expand_toggle_button(
            self._expand_btn,
            expanded=self._expanded,
            visible=self._show_expand_toggle,
        )
        self._refresh_btn.icon_color = C.TEXT_PRIMARY
        self._webview_btn.icon_color = C.TEXT_PRIMARY
        updater = getattr(self._detail_view, "update_theme", None)
        if callable(updater):
            updater()
        safe_control_update(self)
