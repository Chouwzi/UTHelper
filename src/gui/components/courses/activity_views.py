"""Native Pane-3 interactive activity views (Attendance, Groups, Quiz, Forum, Book, Assignment)."""

from __future__ import annotations

from datetime import datetime
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
    RichActivityPage,
)
from core.courses.models import CourseModuleItem, CourseSource
from gui.components.courses._shared import (
    build_expand_toggle_button,
    build_offline_banner,
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
        self._body_host.content = ft.Container(
            expand=True,
            alignment=ft.Alignment.CENTER,
            padding=24,
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=10,
                controls=[
                    ft.ProgressRing(width=26, height=26, stroke_width=3),
                    ft.Text(label, size=13, color=C.TEXT_SECONDARY),
                ],
            ),
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
                status_lower = (rec.status or "").lower()
                badge_color = (
                    C.SAFE
                    if any(k in status_lower for k in ("present", "có mặt", "p"))
                    else (
                        C.CRITICAL
                        if any(k in status_lower for k in ("absent", "vắng", "a"))
                        else C.WARNING
                    )
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

    def _open_attempt_review(self, summary: QuizActivitySummary, attempt_id: int) -> None:
        self._set_loading("Đang tải chi tiết bài đã làm...")

        def _worker() -> None:
            questions = self._svc.fetch_quiz_review(attempt_id)
            if self._disposed:
                return
            if not questions:
                self._render_quiz(summary, notice="Bài làm này chưa cho phép xem lại chi tiết qua API; hãy mở WebView.")
                return
            q_controls: list[ft.Control] = [
                ft.Row(
                    controls=[
                        ft.OutlinedButton(
                            "← Quay lại tổng quan Quiz",
                            on_click=lambda _e: self._render_quiz(summary),
                        )
                    ]
                )
            ]
            for q in questions:
                q_controls.append(
                    ft.Container(
                        bgcolor=C.SURFACE,
                        border=ft.Border.all(1, C.BORDER),
                        border_radius=10,
                        padding=12,
                        content=ft.Column(
                            spacing=4,
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Text(
                                            f"Câu {q.get('slot') or '?'}",
                                            size=13,
                                            weight=ft.FontWeight.W_600,
                                            color=C.ACCENT,
                                            expand=True,
                                        ),
                                        ft.Text(
                                            f"{q.get('status')} · {q.get('mark')}/{q.get('maxmark')}",
                                            size=11,
                                            color=C.TEXT_SECONDARY,
                                        ),
                                    ]
                                ),
                                ft.Text(
                                    q.get("question_text") or "(Không có văn bản câu hỏi)",
                                    size=13,
                                    color=C.TEXT_PRIMARY,
                                    selectable=True,
                                ),
                            ],
                        ),
                    )
                )
            self._body_host.content = ft.ListView(
                expand=True,
                padding=14,
                spacing=10,
                controls=q_controls,
            )
            safe_control_update(self)

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
                                            f"Điểm: {att.score_text or '--'}",
                                            size=12,
                                            color=C.TEXT_SECONDARY,
                                        ),
                                    ],
                                ),
                                ft.OutlinedButton(
                                    "Xem lại",
                                    on_click=lambda _e, aid=att.attempt_id: self._open_attempt_review(
                                        summary, aid
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
    """Wraps a DetailView instance inside Pane 3 with a 2+3 expand bar."""

    def __init__(
        self,
        *,
        detail_view: Any,
        title: str,
        on_close: Callable[[], None],
        is_expanded: bool = False,
        show_expand_toggle: bool = False,
        on_toggle_expand: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(expand=True, bgcolor=C.BG)
        self._detail_view = detail_view
        self._on_close = on_close
        self._expanded = bool(is_expanded)
        self._show_expand_toggle = bool(show_expand_toggle)
        self._on_toggle_expand = on_toggle_expand

        self._expand_btn = build_expand_toggle_button(
            expanded=self._expanded,
            on_toggle=lambda: self._on_toggle_expand()
            if callable(self._on_toggle_expand)
            else None,
            visible=self._show_expand_toggle,
        )
        (
            self._header_container,
            self._back_btn,
            self._title_text,
            self._subtitle_text,
        ) = build_viewer_header(
            title=title or "Bài tập",
            subtitle="Xem đề bài & nộp bài trực tiếp trong môn học",
            back_tooltip="Quay lại môn học",
            on_back=lambda _e: self._on_close(),
            trailing_actions=[self._expand_btn],
        )
        self._detail_view.visible = True
        self._detail_view.opacity = 1.0
        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self._header_container,
                ft.Container(expand=True, content=self._detail_view),
            ],
        )

    def show_loading(self, data: dict[str, Any]) -> None:
        self._detail_view.show_loading(data)

    def update_detail(self, full_data: dict[str, Any]) -> None:
        self._detail_view.update_detail(full_data)

    def show_error_banner(self) -> None:
        self._detail_view.show_error_banner()

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
        safe_control_update(self)
