from __future__ import annotations

from datetime import datetime
from typing import Callable

import flet as ft

from core.today_schedule import (
    ClassSession,
    ScheduleLoadStatus,
    SchedulePhase,
    TodayScheduleViewState,
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

    def __init__(self, *, on_need_data: Callable[[], None]) -> None:
        self._on_need_data = on_need_data
        self.expanded = False
        self.state = TodayScheduleViewState(ScheduleLoadStatus.IDLE)
        self._rendered_phases: tuple[SchedulePhase, ...] = ()
        self._rendered_at = datetime.now()

        self.toggle_icon = ft.Icon(
            ft.Icons.CHEVRON_RIGHT_ROUNDED,
            size=18,
            color=C.TEXT_SECONDARY,
            rotate=ft.Rotate(angle=0, alignment=ft.Alignment.CENTER),
            animate_rotation=ft.Animation(
                duration=260,
                curve=ft.AnimationCurve.EASE_OUT_CUBIC,
            ),
        )
        self.subtitle = ft.Text("Đang chuẩn bị lịch học...", size=11, color=C.TEXT_SECONDARY)
        self.item_column = ft.Column(controls=[], spacing=8)
        self.loading = ft.Container(
            content=ft.Row(
                controls=[
                    ft.ProgressRing(width=16, height=16, stroke_width=2, color=C.ACCENT),
                    ft.Text("Đang lấy lịch học hôm nay...", size=11, color=C.TEXT_SECONDARY),
                ],
                spacing=8,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(vertical=10),
            visible=False,
        )
        self.empty = self._message_state(
            ft.Icons.EVENT_AVAILABLE_ROUNDED,
            "Hôm nay không có lịch học",
        )
        self.auth_required = self._message_state(
            ft.Icons.LOCK_OUTLINE_ROUNDED,
            "Đăng nhập để đồng bộ lịch học",
        )
        self.error = self._message_state(
            ft.Icons.CLOUD_OFF_ROUNDED,
            "Không thể lấy lịch học. Chạm để thử lại.",
        )
        self.body = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Container(height=1, bgcolor=C.BORDER),
                    self.loading,
                    self.auth_required,
                    self.error,
                    self.empty,
                    self.item_column,
                ],
                spacing=8,
            ),
            padding=ft.Padding.only(left=12, right=12, top=0, bottom=12),
            visible=False,
        )
        self.header = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CALENDAR_TODAY_ROUNDED, size=18, color=C.ACCENT),
                    ft.Column(
                        controls=[
                            ft.Text(
                                "Lịch học hôm nay",
                                size=13,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                            ),
                            self.subtitle,
                        ],
                        spacing=1,
                        expand=True,
                    ),
                    self.toggle_icon,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=12, right=12, top=12, bottom=12),
            on_click=lambda _: self.toggle(),
            ink=True,
        )
        self.accent_bar = ft.Container(
            width=3,
            border_radius=ft.BorderRadius.only(top_left=10, bottom_left=10),
            bgcolor=C.BORDER,
        )
        self.control = ft.Container(
            content=ft.Row(
                controls=[
                    self.accent_bar,
                    ft.Container(
                        content=ft.Column(
                            controls=[self.header, self.body],
                            spacing=0,
                        ),
                        expand=True,
                    ),
                ],
                spacing=0,
            ),
            margin=ft.Margin(left=10, right=10, top=0, bottom=0),
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=10,
        )
        self.set_state(self.state)

    @staticmethod
    def _message_state(icon: str, message: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Icon(icon, size=24, color=C.BORDER),
                    ft.Text(
                        message,
                        size=11,
                        color=C.TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=4,
            ),
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding.symmetric(vertical=8),
            visible=False,
        )

    def toggle(self) -> None:
        self.expanded = not self.expanded
        self.toggle_icon.rotate = ft.Rotate(
            angle=3.141592653589793 / 2 if self.expanded else 0,
            alignment=ft.Alignment.CENTER,
        )
        self.accent_bar.bgcolor = C.ACCENT if self.expanded else C.BORDER
        self.body.visible = self.expanded
        if self.expanded and self.state.status in {
            ScheduleLoadStatus.IDLE,
            ScheduleLoadStatus.ERROR,
        }:
            self._on_need_data()

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
        count = len(sessions)
        self.loading.visible = state.status is ScheduleLoadStatus.LOADING
        self.auth_required.visible = state.status is ScheduleLoadStatus.AUTH_REQUIRED and not sessions
        self.error.visible = state.status is ScheduleLoadStatus.ERROR and not sessions
        self.empty.visible = state.status is ScheduleLoadStatus.READY and count == 0
        self.item_column.visible = count > 0
        self.item_column.controls = [
            self._session_card(session, self._rendered_at) for session in sessions
        ]
        self._rendered_phases = tuple(
            session.phase_at(self._rendered_at) for session in sessions
        )
        if state.status is ScheduleLoadStatus.LOADING:
            self.subtitle.value = (
                f"Đang cập nhật {count} buổi học..."
                if count
                else "Đang lấy lịch học từ Portal..."
            )
        elif state.status is ScheduleLoadStatus.AUTH_REQUIRED:
            self.subtitle.value = "Cần đăng nhập Portal"
        elif state.status is ScheduleLoadStatus.ERROR:
            self.subtitle.value = (
                f"{count} buổi học · chưa thể cập nhật"
                if count
                else "Không thể tải lịch học"
            )
        elif state.status is ScheduleLoadStatus.READY:
            self.subtitle.value = f"{count} buổi học" if count else "Hôm nay không có lịch"
        else:
            self.subtitle.value = "Đang chuẩn bị lịch học..."
        self.body.visible = self.expanded

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
        self.accent_bar.bgcolor = C.ACCENT if self.expanded else C.BORDER
        self.subtitle.color = C.TEXT_SECONDARY
        self.toggle_icon.color = C.TEXT_SECONDARY
        self.set_state(self.state, now=self._rendered_at)

    @staticmethod
    def _session_card(session: ClassSession, now: datetime) -> ft.Container:
        phase = session.phase_at(now)
        status_text, color_provider = _PHASE_PRESENTATION[phase]
        status_color = color_provider()
        time_text = f"{session.start_at:%H:%M} – {session.end_at:%H:%M}"
        if session.period_start is not None and session.period_end is not None:
            time_text += f" · Tiết {session.period_start}–{session.period_end}"
        locations = []
        if session.room:
            locations.append(f"Phòng {session.room}")
        if session.campus:
            locations.append(session.campus)
        location_text = " · ".join(locations) or "Chưa cập nhật phòng/cơ sở"
        metadata = [
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.ACCESS_TIME_ROUNDED, size=13, color=C.TEXT_SECONDARY),
                    ft.Text(time_text, size=10, color=C.TEXT_SECONDARY),
                ],
                spacing=5,
            ),
            ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOCATION_ON_OUTLINED, size=13, color=C.TEXT_SECONDARY),
                    ft.Text(
                        location_text,
                        size=10,
                        color=C.TEXT_SECONDARY,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=5,
            ),
        ]
        if session.course_code:
            metadata.insert(
                0,
                ft.Text(session.course_code, size=9, color=C.TEXT_SECONDARY),
            )
        return ft.Container(
            content=ft.Row(
                controls=[
                    ft.Container(
                        width=3,
                        height=62,
                        bgcolor=status_color,
                        border_radius=3,
                    ),
                    ft.Column(
                        controls=[
                            ft.Text(
                                session.subject,
                                size=12,
                                weight=ft.FontWeight.W_600,
                                color=C.TEXT_PRIMARY,
                                max_lines=2,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            *metadata,
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Container(
                        content=ft.Text(
                            status_text,
                            size=9,
                            color=status_color,
                            weight=ft.FontWeight.W_600,
                        ),
                        padding=ft.Padding.symmetric(horizontal=7, vertical=4),
                        border=ft.Border.all(1, status_color),
                        border_radius=999,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=C.BG,
            border=ft.Border.all(1, C.BORDER),
            border_radius=8,
            padding=ft.Padding.only(left=10, right=10, top=8, bottom=8),
        )
