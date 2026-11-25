import flet as ft
from core.activity_status import normalize_submission_status
from gui.core.theme import C
from datetime import datetime
from core.time_utils import parse_datetime
from gui.core.utils import (
    get_urgency_color,
    get_type_label,
    get_type_color,
    clean_course_name,
    format_deadline,
    urgency_str,
    get_urgency_badge,
    get_status_tag,
    get_submission_badge,
    get_card_countdown,
)

class ActivityCard(ft.Container):
    def __init__(self, data: dict, on_tap, animate: bool = False):
        super().__init__()
        self.data = {}
        self.on_tap_cb = on_tap

        # Các control có thể thay đổi dữ liệu
        self._type_text = ft.Text(size=10, weight=ft.FontWeight.W_700)
        self._type_container = ft.Container(
            content=self._type_text,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
        )

        self._status_text = ft.Text(size=10, weight=ft.FontWeight.W_600)
        self._status_container = ft.Container(
            content=self._status_text,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
        )

        self._urgency_text = ft.Text(size=10, weight=ft.FontWeight.W_600)
        self._urgency_container = ft.Container(
            content=self._urgency_text,
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            border_radius=6,
        )
        self._selected_icon = ft.Icon(
            ft.Icons.ARROW_FORWARD_IOS_ROUNDED, size=10, color=C.ACCENT
        )
        self._selected_text = ft.Text(
            "Đang xem", size=10, weight=ft.FontWeight.W_700, color=C.ACCENT
        )
        self._selected_badge = ft.Container(
            content=ft.Row(
                controls=[self._selected_text, self._selected_icon],
                spacing=3,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
            bgcolor=ft.Colors.with_opacity(0.14, C.ACCENT),
            border_radius=6,
            visible=False,
        )

        self._course_text = ft.Text(
            size=11,
            color=C.ACCENT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            weight=ft.FontWeight.W_500,
        )
        self._title_text = ft.Text(
            size=14,
            weight=ft.FontWeight.W_600,
            color=C.TEXT_PRIMARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        self._deadline_text = ft.Text(size=11, color=C.TEXT_SECONDARY, expand=True)

        self._countdown_ctrl = ft.Text(size=11.5, weight=ft.FontWeight.W_600)
        self._progress_ctrl = ft.ProgressBar(
            bgcolor=C.BORDER,
            height=2.5,
            border_radius=2,
        )

        self._optional_rows = ft.Row(controls=[], spacing=6, wrap=True)

        body = ft.Container(
            content=ft.Column(
                controls=[
                    # Dòng 1 - Nhãn loại hoạt động & mức độ ưu tiên
                    ft.Row(
                        controls=[
                            ft.Row(
                                controls=[self._type_container, self._status_container],
                                spacing=6,
                                tight=True,
                            ),
                            ft.Row(
                                controls=[self._urgency_container, self._selected_badge],
                                spacing=6,
                                tight=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # Dòng 2 - Tên môn học
                    self._course_text,
                    # Dòng 3 - Tiêu đề hoạt động
                    self._title_text,
                    # Dòng 4 - Thời hạn & đếm ngược
                    ft.Row(
                        controls=[self._deadline_text, self._countdown_ctrl],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    # Dòng 5 - Thanh tiến độ (chỉ hiện khi chưa hoàn thành & còn hạn)
                    self._progress_ctrl,
                    # Dòng 6 - Trạng thái nộp bài / hạn nộp muộn
                    self._optional_rows,
                ],
                spacing=5,
                tight=True,
            ),
            padding=ft.Padding.only(left=14, right=14, top=12, bottom=12),
            expand=True,
        )

        self._bar_widget = ft.Container(
            width=3,
            border_radius=ft.BorderRadius.only(
                top_left=12, bottom_left=12, top_right=0, bottom_right=0
            ),
        )

        self.content = ft.Row([self._bar_widget, body], spacing=0)
        self.bgcolor = C.SURFACE
        self.border_radius = 12
        self.margin = ft.Margin(left=10, right=10, top=0, bottom=0)
        self.ink = True
        self.mouse_cursor = ft.MouseCursor.CLICK
        self._animate_reveal = animate
        self.animate = ft.Animation(800, ft.AnimationCurve.EASE_IN_OUT) if animate else None
        self.on_click = lambda _: self.on_tap_cb(self.data)
        self.on_hover = self._handle_hover
        self._hovered = False
        self._selected = False
        self._is_submitted = False
        self._base_bar_color = C.BORDER
        self._base_border_color = C.BORDER
        self._base_shadow = None

        if animate:
            self.opacity = 0
            self.scale = 0.94
            self.offset = ft.Offset(0, 0.15)
            self.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_OUT)
            self.animate_offset = ft.Animation(300, ft.AnimationCurve.DECELERATE)
            self.animate_scale = ft.Animation(300, ft.AnimationCurve.EASE_OUT)
        else:
            self.opacity = 1.0
            self.scale = 1.0
            self.offset = ft.Offset(0, 0)
            self.animate_opacity = None
            self.animate_offset = None
            self.animate_scale = None

        self._is_critical_active = False
        self.update_data(data)



    @staticmethod
    def _render_fingerprint(data: dict) -> tuple:
        details = data.get("details", {})
        if not isinstance(details, dict):
            details = {}
        status_data = details.get("status_data", {})
        if not isinstance(status_data, dict):
            status_data = {}
        status_fingerprint = tuple(
            sorted((str(key), str(value)) for key, value in status_data.items())
        )
        return (
            data.get("id"),
            data.get("title"),
            data.get("course"),
            data.get("course_name"),
            data.get("deadline"),
            data.get("submission_status"),
            data.get("urgency"),
            data.get("type"),
            data.get("is_open"),
            data.get("late_status"),
            data.get("cutoff_date"),
            details.get("open_time"),
            details.get("course_full_name"),
            details.get("quiz_attempt_status"),
            status_fingerprint,
        )

    def update_data(self, data: dict, force: bool = False, on_tap=None):
        # Avoid rebuilding an unchanged Flet subtree while including every
        # field that can change the visible card or its submission badge.
        if not force and getattr(self, '_initialized', False):
            if self._render_fingerprint(self.data) == self._render_fingerprint(data):
                if on_tap:
                    self.on_tap_cb = on_tap
                self.update_countdown()
                self._apply_interaction_chrome()
                return
        self.data = data
        if on_tap:
            self.on_tap_cb = on_tap

        urgency = data.get("urgency", "safe")
        act_type = data.get("type", "other")
        
        get_urgency_color(urgency)
        type_color = get_type_color(act_type)
        type_label = get_type_label(act_type)
        status_label, status_color = get_status_tag(data)
        urgency_label, urgency_color = get_urgency_badge(urgency)

        deadline_str = data.get("deadline", "")
        formatted_dl = format_deadline(deadline_str)
        cd_text, overdue, cd_color, progress_val = get_card_countdown(data)
        bar_color = cd_color
        submission = get_submission_badge(data)

        _details = data.get("details")
        if not isinstance(_details, dict):
            _details = {}
        _full_name = str(_details.get("course_full_name") or "")
        _ws_course = str(data.get("course_name") or "")
        course_clean = clean_course_name(_ws_course or _full_name or str(data.get("course") or ""))

        _sub_status = normalize_submission_status(
            data.get("submission_status", "unknown")
        )
        _is_submitted = _sub_status in ("submitted", "graded") or (
            submission is not None and submission[1] == C.SAFE
        )
        self._is_submitted = _is_submitted

        self._type_text.value = type_label
        self._type_text.color = type_color
        self._type_container.bgcolor = ft.Colors.with_opacity(0.11, type_color)
        self._type_container.border = None

        show_status = (
            bool(status_label)
            and status_label != "ĐANG MỞ"
            and (status_label != urgency_label or _is_submitted)
            and not (_is_submitted and status_label == "QUÁ HẠN")
        )
        self._status_text.value = status_label
        self._status_text.color = status_color
        self._status_container.bgcolor = ft.Colors.with_opacity(0.11, status_color)
        self._status_container.border = None
        self._status_container.visible = show_status

        self._urgency_text.value = urgency_label
        self._urgency_text.color = urgency_color
        self._urgency_container.bgcolor = ft.Colors.with_opacity(0.11, urgency_color)
        self._urgency_container.border = None
        self._urgency_container.visible = not _is_submitted

        self.bgcolor = C.SURFACE

        self._course_text.value = course_clean
        self._course_text.color = C.ACCENT
        self._title_text.value = data.get("title", "Không có tiêu đề")
        self._title_text.color = C.TEXT_PRIMARY
        self._deadline_text.value = formatted_dl
        self._deadline_text.color = C.TEXT_SECONDARY

        effective_cd_color = C.TEXT_SECONDARY if _is_submitted else cd_color
        self._countdown_ctrl.value = cd_text
        self._countdown_ctrl.color = effective_cd_color
        self._progress_ctrl.value = progress_val
        self._progress_ctrl.color = bar_color
        self._progress_ctrl.bgcolor = C.BORDER
        self._progress_ctrl.visible = bool(deadline_str) and not _is_submitted and not overdue

        self._optional_rows.controls.clear()
        if submission:
            sub_label, sub_color = submission
            self._optional_rows.controls.append(
                ft.Container(
                    content=ft.Text(
                        sub_label,
                        size=10.5,
                        color=sub_color,
                        weight=ft.FontWeight.W_600,
                    ),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    bgcolor=ft.Colors.with_opacity(0.11, sub_color),
                    border_radius=6,
                )
            )

        # Late submission warning badge / cutoff date info
        is_late_active = (status_label == "NỘP MUỘN")
        late_status = data.get("late_status", "")
        cutoff_ts = data.get("cutoff_date", 0)
        if is_late_active and cutoff_ts and not _is_submitted:
            try:
                if isinstance(cutoff_ts, (int, float)) and cutoff_ts > 0:
                    cutoff_dt = datetime.fromtimestamp(cutoff_ts)
                else:
                    cutoff_dt = parse_datetime(str(cutoff_ts))
                if cutoff_dt:
                    cutoff_text = f"Hạn chót: {cutoff_dt.strftime('%d/%m %H:%M')}"
                    self._optional_rows.controls.append(
                        ft.Container(
                            content=ft.Text(
                                cutoff_text,
                                size=10.5,
                                color=C.WARNING,
                                weight=ft.FontWeight.W_600,
                            ),
                            padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                            bgcolor=ft.Colors.with_opacity(0.11, C.WARNING),
                            border_radius=6,
                        )
                    )
            except Exception:
                pass
        elif (
            not _is_submitted
            and (late_status == "closed" or overdue)
            and bool(cutoff_ts)
            and (
                (isinstance(cutoff_ts, (int, float)) and cutoff_ts > 0)
                or (isinstance(cutoff_ts, str) and cutoff_ts.strip() not in ("", "0"))
            )
        ):
            self._optional_rows.controls.append(
                ft.Container(
                    content=ft.Text(
                        "Hết hạn nộp",
                        size=10.5,
                        color=C.CRITICAL,
                        weight=ft.FontWeight.W_600,
                    ),
                    padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                    bgcolor=ft.Colors.with_opacity(0.11, C.CRITICAL),
                    border_radius=6,
                )
            )
        self._optional_rows.visible = bool(self._optional_rows.controls)

        self._base_bar_color = (
            ft.Colors.with_opacity(0.4, C.SAFE) if _is_submitted else bar_color
        )

        urg_key = urgency_str(urgency)
        is_critical_active = (urg_key == "critical") and not overdue and not _is_submitted
        is_warning_active = (urg_key == "warning") and not overdue and not _is_submitted

        if is_critical_active:
            self._base_border_color = C.CRITICAL
            self._base_shadow = [
                ft.BoxShadow(
                    spread_radius=0.8,
                    blur_radius=12,
                    color=ft.Colors.with_opacity(0.18, C.CRITICAL),
                    offset=ft.Offset(0, 0),
                )
            ]
            self._is_critical_active = True
            self.animate = ft.Animation(1000, ft.AnimationCurve.EASE_IN_OUT)
        else:
            self._base_border_color = (
                ft.Colors.with_opacity(0.45, C.WARNING)
                if is_warning_active
                else C.BORDER
            )
            self._base_shadow = None
            self._is_critical_active = False
            self.animate = (
                ft.Animation(800, ft.AnimationCurve.EASE_IN_OUT)
                if self._animate_reveal
                else None
            )

        self._apply_interaction_chrome()
        self._initialized = True

    def _apply_interaction_chrome(self) -> None:
        self._selected_icon.color = C.ACCENT
        self._selected_text.color = C.ACCENT
        self._selected_badge.bgcolor = ft.Colors.with_opacity(0.14, C.ACCENT)
        if self._selected:
            self.bgcolor = C.SURFACE_HOVER
            self.border = ft.Border.all(1.5, C.ACCENT)
            self._bar_widget.width = 4
            self._bar_widget.bgcolor = C.ACCENT
            self.shadow = [
                ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=12,
                    color=ft.Colors.with_opacity(0.16, C.ACCENT),
                    offset=ft.Offset(0, 2),
                )
            ]
            self._selected_badge.visible = True
            self._urgency_container.visible = False
            return

        self._selected_badge.visible = False
        self._urgency_container.visible = not self._is_submitted
        if self._hovered:
            self.bgcolor = C.SURFACE_HOVER
            hover_border = (
                C.CRITICAL
                if self._is_critical_active
                else ft.Colors.with_opacity(0.5, C.ACCENT)
            )
            self.border = ft.Border.all(1, hover_border)
            self._bar_widget.width = 3.5
            self._bar_widget.bgcolor = self._base_bar_color
            hover_shadow_color = C.CRITICAL if self._is_critical_active else C.ACCENT
            hover_shadow_alpha = 0.20 if self._is_critical_active else 0.08
            self.shadow = [
                ft.BoxShadow(
                    spread_radius=0,
                    blur_radius=10,
                    color=ft.Colors.with_opacity(hover_shadow_alpha, hover_shadow_color),
                    offset=ft.Offset(0, 2),
                )
            ]
            return

        self.bgcolor = C.SURFACE
        self.border = ft.Border.all(1, self._base_border_color)
        self._bar_widget.width = 3
        self._bar_widget.bgcolor = self._base_bar_color
        self.shadow = self._base_shadow

    def _handle_hover(self, event) -> None:
        raw = getattr(event, "data", False)
        hovered = raw is True or str(raw).lower() == "true"
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self._apply_interaction_chrome()
        try:
            if self.page is not None:
                self.update()
        except Exception:
            pass

    def set_selected(self, selected: bool) -> bool:
        target = bool(selected)
        if target == self._selected:
            return False
        self._selected = target
        self._apply_interaction_chrome()
        return True

    def update_countdown(self):
        deadline_str = self.data.get("deadline", "")
        if not deadline_str:
            return False
        cd_text, overdue, cd_color, progress_val = get_card_countdown(self.data)
        is_submitted = getattr(self, "_is_submitted", False)
        effective_cd_color = C.TEXT_SECONDARY if is_submitted else cd_color
        show_progress = not is_submitted and not overdue
        changed = (
            self._countdown_ctrl.value != cd_text
            or self._countdown_ctrl.color != effective_cd_color
            or self._progress_ctrl.value != progress_val
            or self._progress_ctrl.visible != show_progress
        )
        self._countdown_ctrl.value = cd_text
        self._countdown_ctrl.color = effective_cd_color
        self._progress_ctrl.value = progress_val
        self._progress_ctrl.color = cd_color
        self._progress_ctrl.visible = show_progress
        return changed
