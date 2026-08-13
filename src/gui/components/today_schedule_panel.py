from __future__ import annotations

from datetime import datetime
from typing import Callable

import flet as ft

from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    SchedulePhase,
    TodayScheduleViewState,
    select_spotlight_session,
)
from gui.core.theme import C


_PHASE_PRESENTATION = {
    SchedulePhase.UPCOMING: ("Chưa đến giờ", lambda: C.ACCENT),
    SchedulePhase.IN_PROGRESS: ("Đang học", lambda: C.SAFE),
    SchedulePhase.FINISHED: ("Đã kết thúc", lambda: C.TEXT_SECONDARY),
    SchedulePhase.CANCELLED: ("Tạm ngưng", lambda: C.CRITICAL),
}


class TodaySchedulePanel:
    """Compact Portal class-schedule disclosure for the dashboard."""

    def __init__(
        self,
        *,
        on_need_data: Callable[[], None],
        page=None,
        mobile: bool = False,
    ) -> None:
        self._on_need_data = on_need_data
        self.page = page
        self.mobile = mobile
        self.state = TodayScheduleViewState(ScheduleLoadStatus.IDLE)
        self._rendered_phases: tuple[SchedulePhase, ...] = ()
        self._rendered_at = datetime.now()
        self._spotlight_session: ClassSession | None = None
        self.spotlight_tap_target: ft.Container | None = None
        self.surface_open = False
        self._surface_mode: str | None = None
        self._surface: ft.BottomSheet | ft.AlertDialog | None = None
        self._surface_content: ft.Container | None = None
        self._selected_session: ClassSession | None = None
        self._detail_from_list = False
        self._list_rows: list[ft.Container] = []

        self.spotlight_content = ft.Column(controls=[], spacing=0)
        self.header = ft.Container(
            content=self.spotlight_content,
            padding=ft.Padding.only(left=12, right=12, top=12, bottom=8),
        )
        self.control = ft.Container(
            content=self.header,
            margin=ft.Margin(left=10, right=10, top=0, bottom=0),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        self.set_state(self.state)

    @staticmethod
    def _metadata_rows(session: ClassSession) -> list[ft.Row]:
        time_text = f"{session.start_at:%H:%M} – {session.end_at:%H:%M}"
        if session.period_start is not None and session.period_end is not None:
            time_text += f" · Tiết {session.period_start}–{session.period_end}"
        return [
            ft.Row(
                [
                    ft.Icon(ft.Icons.ACCESS_TIME_ROUNDED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text(time_text, size=11, color=C.TEXT_SECONDARY, expand=True),
                ],
                spacing=6,
            ),
            ft.Row(
                [
                    ft.Icon(ft.Icons.MEETING_ROOM_OUTLINED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text(
                        f"Phòng {session.room}" if session.room else "Chưa cập nhật phòng",
                        size=11,
                        color=C.TEXT_SECONDARY,
                        expand=True,
                    ),
                ],
                spacing=6,
            ),
            ft.Row(
                [
                    ft.Icon(ft.Icons.LOCATION_ON_OUTLINED, size=14, color=C.TEXT_SECONDARY),
                    ft.Text(
                        session.campus or "Chưa cập nhật cơ sở",
                        size=11,
                        color=C.TEXT_SECONDARY,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                        expand=True,
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        ]

    def _build_spotlight(self, sessions: tuple[ClassSession, ...]) -> ft.Control:
        count = len(sessions)
        cta_text = f"Xem lịch hôm nay · {count} buổi"
        session = select_spotlight_session(sessions, self._rendered_at)
        self._spotlight_session = session
        if session is None:
            self.spotlight_tap_target = None
            return ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.EVENT_AVAILABLE_ROUNDED, size=20, color=C.SAFE),
                            ft.Text(
                                "Đã hoàn thành lịch học hôm nay",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                expand=True,
                            ),
                        ],
                        spacing=8,
                    ),
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text(cta_text, size=11, color=C.ACCENT, weight=ft.FontWeight.W_600),
                                ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=18, color=C.ACCENT),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        height=48,
                        alignment=ft.Alignment.CENTER,
                        border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
                        on_click=lambda _event: self.open_schedule(),
                        ink=True,
                    ),
                ],
                spacing=8,
            )
        phase = session.phase_at(self._rendered_at)
        status_text, color_provider = _PHASE_PRESENTATION[phase]
        status_color = color_provider()
        self.spotlight_tap_target = ft.Container(
            content=ft.Column(
                [
                    ft.Text(
                        "ĐANG HỌC" if phase is SchedulePhase.IN_PROGRESS else "LỊCH HỌC TIẾP THEO",
                        size=9,
                        color=C.TEXT_SECONDARY,
                        weight=ft.FontWeight.W_600,
                    ),
                    ft.Row(
                        [
                            ft.Text(
                                session.subject,
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True,
                            ),
                            ft.Container(
                                content=ft.Text(status_text, size=9, color=status_color, weight=ft.FontWeight.W_600),
                                padding=ft.Padding.symmetric(horizontal=7, vertical=4),
                                border=ft.Border.all(1, status_color),
                                border_radius=999,
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    *self._metadata_rows(session),
                ],
                spacing=5,
            ),
            on_click=lambda _event, item=session: self.open_detail(
                item,
                from_list=False,
            ),
            ink=True,
            border_radius=8,
        )
        return ft.Column(
            [
                self.spotlight_tap_target,
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text(cta_text, size=11, color=C.ACCENT, weight=ft.FontWeight.W_600),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=18, color=C.ACCENT),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    height=48,
                    alignment=ft.Alignment.CENTER,
                    border=ft.Border.only(top=ft.BorderSide(1, C.BORDER)),
                    on_click=lambda _event: self.open_schedule(),
                    ink=True,
                ),
            ],
            spacing=5,
        )

    @staticmethod
    def _safe_update(control) -> None:
        try:
            control.update()
        except (AssertionError, RuntimeError):
            pass

    def _session_row(self, session: ClassSession) -> ft.Container:
        phase = session.phase_at(self._rendered_at)
        status_text, color_provider = _PHASE_PRESENTATION[phase]
        status_color = color_provider()
        row = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                session.subject,
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                expand=True,
                            ),
                            ft.Text(
                                status_text,
                                size=9,
                                weight=ft.FontWeight.W_600,
                                color=status_color,
                            ),
                            ft.Icon(
                                ft.Icons.CHEVRON_RIGHT_ROUNDED,
                                size=18,
                                color=C.TEXT_SECONDARY,
                            ),
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    *self._metadata_rows(session),
                ],
                spacing=4,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=ft.Padding.symmetric(horizontal=12, vertical=10),
            on_click=lambda _event, item=session: self.open_detail(
                item,
                from_list=True,
            ),
            ink=True,
        )
        return row

    def _surface_header(self, title: str, *, show_back: bool) -> ft.Row:
        leading: ft.Control = ft.IconButton(
            icon=ft.Icons.ARROW_BACK_ROUNDED,
            icon_color=C.TEXT_SECONDARY,
            icon_size=20,
            tooltip="Quay lại",
            on_click=lambda _event: self.handle_back(),
        ) if show_back else ft.Container(width=48, height=48)
        return ft.Row(
            [
                leading,
                ft.Text(
                    title,
                    size=15,
                    weight=ft.FontWeight.W_700,
                    color=C.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_color=C.TEXT_SECONDARY,
                    icon_size=20,
                    tooltip="Đóng",
                    on_click=lambda _event: self.close_surface(),
                ),
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_list_surface(self) -> ft.Control:
        snapshot = self.state.snapshot
        sessions = tuple(sorted(
            snapshot.sessions if snapshot is not None else (),
            key=lambda item: (item.start_at, item.end_at, item.session_id),
        ))
        self._list_rows = [self._session_row(session) for session in sessions]
        schedule_date = snapshot.schedule_date if snapshot is not None else self._rendered_at.date()
        return ft.Column(
            [
                self._surface_header("Lịch học hôm nay", show_back=False),
                ft.Text(
                    f"{schedule_date:%d/%m/%Y} · {len(sessions)} buổi",
                    size=10,
                    color=C.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Column(
                    self._list_rows,
                    spacing=8,
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
            ],
            spacing=8,
            expand=True,
        )

    def _detail_field(self, label: str, values: list[str]) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(label.upper(), size=9, color=C.TEXT_SECONDARY),
                    *[
                        ft.Text(
                            value,
                            size=12,
                            color=C.TEXT_PRIMARY,
                            max_lines=3,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        )
                        for value in values
                    ],
                ],
                spacing=3,
            ),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
            padding=12,
        )

    def _build_detail_surface(self) -> ft.Control:
        session = self._selected_session
        if session is None:
            return self._build_list_surface()
        phase = session.phase_at(self._rendered_at)
        status_text, color_provider = _PHASE_PRESENTATION[phase]
        status_color = color_provider()
        period = ""
        if session.period_start is not None and session.period_end is not None:
            period = f"Tiết {session.period_start}–{session.period_end}"
        fields = [
            self._detail_field(
                "Thời gian",
                [
                    f"{session.start_at:%H:%M} – {session.end_at:%H:%M}",
                    *([period] if period else []),
                ],
            ),
            self._detail_field(
                "Địa điểm",
                [
                    f"Phòng {session.room}" if session.room else "Chưa cập nhật phòng",
                    session.campus or "Chưa cập nhật cơ sở",
                ],
            ),
            self._detail_field("Trạng thái", [status_text]),
        ]
        if session.note:
            fields.append(self._detail_field("Ghi chú", [session.note]))
        return ft.Column(
            [
                self._surface_header("Chi tiết lớp học", show_back=True),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Text(
                                    status_text,
                                    size=9,
                                    color=status_color,
                                    weight=ft.FontWeight.W_600,
                                ),
                                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                                border=ft.Border.all(1, status_color),
                                border_radius=999,
                            ),
                            ft.Text(
                                session.subject,
                                size=17,
                                weight=ft.FontWeight.W_700,
                                color=C.TEXT_PRIMARY,
                                text_align=ft.TextAlign.CENTER,
                            ),
                            ft.Text(
                                session.course_code,
                                size=11,
                                color=C.TEXT_SECONDARY,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=5,
                    ),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(fields, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True),
            ],
            spacing=8,
            expand=True,
        )

    def _current_surface_content(self) -> ft.Control:
        if self._surface_mode == "detail":
            return self._build_detail_surface()
        return self._build_list_surface()

    def _surface_height(self) -> float:
        viewport = getattr(self.page, "height", None)
        maximum = min(620.0, float(viewport) * 0.78) if viewport else 560.0
        snapshot = self.state.snapshot
        count = len(snapshot.sessions) if snapshot is not None else 0
        desired = 430.0 if self._surface_mode == "detail" else 92.0 + count * 112.0
        return min(maximum, max(260.0, desired))

    def _refresh_surface_content(self) -> None:
        if self._surface_content is None:
            return
        self._surface_content.content = self._current_surface_content()
        if self.mobile:
            self._surface_content.height = self._surface_height()
        self._safe_update(self._surface_content)

    def _on_surface_dismissed(self, _event=None) -> None:
        self.surface_open = False
        self._surface_mode = None
        self._surface = None
        self._surface_content = None
        self._selected_session = None
        self._detail_from_list = False
        self._list_rows = []

    def _show_surface(self) -> bool:
        if self.page is None:
            return False
        self._surface_content = ft.Container(
            content=self._current_surface_content(),
            bgcolor=C.BG,
            padding=ft.Padding.only(left=14, right=14, bottom=14),
            height=self._surface_height() if self.mobile else 520,
            width=None if self.mobile else 430,
        )
        if self.mobile:
            self._surface = ft.BottomSheet(
                content=self._surface_content,
                bgcolor=C.BG,
                dismissible=True,
                draggable=True,
                show_drag_handle=True,
                use_safe_area=True,
                scrollable=True,
                elevation=12,
                barrier_color=C.SCRIM,
                shape=ft.RoundedRectangleBorder(radius=18),
                on_dismiss=self._on_surface_dismissed,
            )
        else:
            self._surface = ft.AlertDialog(
                content=self._surface_content,
                bgcolor=C.BG,
                modal=True,
                content_padding=0,
                inset_padding=20,
                shape=ft.RoundedRectangleBorder(radius=16),
                on_dismiss=self._on_surface_dismissed,
            )
        self.surface_open = True
        self.page.show_dialog(self._surface)
        return True

    def open_schedule(self) -> bool:
        snapshot = self.state.snapshot
        if snapshot is None or not snapshot.sessions:
            if self.state.status in {ScheduleLoadStatus.IDLE, ScheduleLoadStatus.ERROR}:
                self._on_need_data()
            return False
        self._surface_mode = "list"
        self._selected_session = None
        self._detail_from_list = False
        if self.surface_open:
            self._refresh_surface_content()
            return True
        return self._show_surface()

    def open_detail(self, session: ClassSession, *, from_list: bool) -> bool:
        self._surface_mode = "detail"
        self._selected_session = session
        self._detail_from_list = from_list
        if self.surface_open:
            self._refresh_surface_content()
            return True
        return self._show_surface()

    def close_surface(self) -> bool:
        if not self.surface_open:
            return False
        if self.page is not None:
            self.page.pop_dialog()
        self._on_surface_dismissed()
        return True

    def handle_back(self) -> bool:
        if not self.surface_open:
            return False
        if self._surface_mode == "detail" and self._detail_from_list:
            self._surface_mode = "list"
            self._selected_session = None
            self._detail_from_list = False
            self._refresh_surface_content()
            return True
        return self.close_surface()

    def _build_status_spotlight(self, text: str, icon: str) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=20, color=C.ACCENT),
                    ft.Text(text, size=12, color=C.TEXT_SECONDARY, expand=True),
                ],
                spacing=8,
            ),
            height=48,
            on_click=lambda _event: self._on_need_data(),
            ink=True,
        )

    def set_state(
        self,
        state: TodayScheduleViewState,
        *,
        now: datetime | None = None,
    ) -> None:
        self.state = state
        self._rendered_at = now or datetime.now()
        snapshot = state.snapshot
        sessions = snapshot.sessions if snapshot is not None else ()
        self._rendered_phases = tuple(
            session.phase_at(self._rendered_at) for session in sessions
        )
        if sessions:
            self.spotlight_content.controls = [self._build_spotlight(sessions)]
        elif state.status is ScheduleLoadStatus.LOADING:
            self._spotlight_session = None
            self.spotlight_content.controls = [
                self._build_status_spotlight(
                    "Đang lấy lịch học từ Portal...",
                    ft.Icons.SYNC_ROUNDED,
                )
            ]
        elif state.status is ScheduleLoadStatus.AUTH_REQUIRED:
            self._spotlight_session = None
            self.spotlight_content.controls = [
                self._build_status_spotlight(
                    "Đăng nhập để đồng bộ lịch học",
                    ft.Icons.LOCK_OUTLINE_ROUNDED,
                )
            ]
        elif state.status is ScheduleLoadStatus.ERROR:
            self._spotlight_session = None
            self.spotlight_content.controls = [
                self._build_status_spotlight(
                    "Không thể lấy lịch học. Chạm để thử lại.",
                    ft.Icons.CLOUD_OFF_ROUNDED,
                )
            ]
        elif state.status is ScheduleLoadStatus.READY:
            self._spotlight_session = None
            self.spotlight_content.controls = [
                self._build_status_spotlight(
                    "Hôm nay không có lịch học",
                    ft.Icons.EVENT_AVAILABLE_ROUNDED,
                )
            ]
        else:
            self._spotlight_session = None
            self.spotlight_content.controls = [
                self._build_status_spotlight(
                    "Đang chuẩn bị lịch học...",
                    ft.Icons.CALENDAR_TODAY_ROUNDED,
                )
            ]
        if self.surface_open:
            self._refresh_surface_content()

    def refresh_time_state(self, now: datetime | None = None) -> bool:
        snapshot = self.state.snapshot
        if snapshot is None or not snapshot.sessions:
            return False
        current = now or datetime.now()
        phases = tuple(session.phase_at(current) for session in snapshot.sessions)
        if phases == self._rendered_phases:
            return False
        self.set_state(self.state, now=current)
        return True

    def refresh_theme(self) -> None:
        self.control.bgcolor = C.SURFACE
        self.control.border = ft.Border.all(1, C.BORDER)
        if self._surface is not None:
            self._surface.bgcolor = C.BG
            self._surface.barrier_color = C.SCRIM
        if self._surface_content is not None:
            self._surface_content.bgcolor = C.BG
        self.set_state(self.state, now=self._rendered_at)
