import flet as ft
from gui.core.theme import C
from gui.core.utils import get_urgency_color, get_countdown_color, get_type_label, get_type_color, clean_course_name, format_deadline, get_countdown, get_progress_value, urgency_str, get_urgency_badge, get_status_tag, get_submission_badge

class ActivityCard(ft.Container):
    def __init__(self, data: dict, on_tap, animate: bool = False):
        super().__init__()
        self.data = {}
        self.on_tap_cb = on_tap

        # Các control có thể thay đổi dữ liệu
        self._type_text = ft.Text(size=10, weight=ft.FontWeight.W_600)
        self._type_container = ft.Container(
            content=self._type_text, padding=ft.Padding.symmetric(horizontal=7, vertical=2), border_radius=4
        )

        self._status_text = ft.Text(size=10, weight=ft.FontWeight.W_600)
        self._status_container = ft.Container(
            content=self._status_text, padding=ft.Padding.symmetric(horizontal=7, vertical=2), border_radius=4
        )

        self._urgency_text = ft.Text(size=9, weight=ft.FontWeight.W_600)
        self._urgency_container = ft.Container(
            content=self._urgency_text, padding=ft.Padding.symmetric(horizontal=7, vertical=2), border_radius=4
        )

        self._course_text = ft.Text(size=10, color=C.ACCENT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_500)
        self._title_text = ft.Text(size=14, weight=ft.FontWeight.W_600, color=C.TEXT_PRIMARY, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self._deadline_text = ft.Text(size=11, color=C.TEXT_SECONDARY, expand=True)

        self._countdown_ctrl = ft.Text(size=12, weight=ft.FontWeight.W_600)
        self._progress_ctrl = ft.ProgressBar(bgcolor=C.BORDER, height=2)

        self._optional_rows = ft.Row(controls=[])
        
        body = ft.Container(
            content=ft.Column(
                controls=[
                    # Cột 1 - chip loại và nhãn cảnh báo
                    ft.Row(
                        controls=[
                            ft.Row(
                                controls=[self._type_container, self._status_container],
                                spacing=6,
                            ),
                            self._urgency_container,
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    # Dòng 2 - tên môn học
                    self._course_text,
                    # Dòng 3 - Tiêu đề
                    self._title_text,
                    # Dòng 4 - Thời hạn
                    ft.Row(
                        controls=[self._deadline_text, self._countdown_ctrl],
                        spacing=8,
                    ),
                    # Dòng 5 - Thanh tiến độ
                    self._progress_ctrl,
                    # Dòng 6 - Trạng thái nộp bài
                    self._optional_rows,
                ],
                spacing=5,
            ),
            padding=ft.Padding.only(left=14, right=14, top=12, bottom=12),
            expand=True,
        )

        self._bar_widget = ft.Container(
            width=3,
            border_radius=ft.BorderRadius.only(top_left=10, bottom_left=10, top_right=0, bottom_right=0),
        )

        self.content = ft.Row([self._bar_widget, body], spacing=0)
        self.bgcolor = C.SURFACE
        self.border_radius = 10
        self.ink = True
        self.mouse_cursor = ft.MouseCursor.CLICK
        
        # Only enable container animation for reveal (animate=True). Otherwise None = instant.
        self.animate = ft.Animation(800, ft.AnimationCurve.EASE_IN_OUT) if animate else None
        self.on_click = lambda _: self.on_tap_cb(self.data)

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

    # Pre-allocated shadow constants — avoid GC pressure from pulse loop
    _CRITICAL_SHADOW = [ft.BoxShadow(spread_radius=1, blur_radius=8, color="#88EF4444", offset=ft.Offset(0, 0))]
    _PULSE_SHADOW_HIGH = [ft.BoxShadow(spread_radius=1, blur_radius=4, color="#BBEF4444", offset=ft.Offset(0, 0))]
    _PULSE_SHADOW_LOW = [ft.BoxShadow(spread_radius=0, blur_radius=3, color="#33EF4444", offset=ft.Offset(0, 0))]

    def update_data(self, data: dict, force: bool = False, on_tap=None):
        # C7: Lightweight identity check instead of deep dict equality
        if not force and getattr(self, '_initialized', False):
            old_id = (self.data.get("id"), self.data.get("deadline"), self.data.get("submission_status"))
            new_id = (data.get("id"), data.get("deadline"), data.get("submission_status"))
            if old_id == new_id:
                if on_tap:
                    self.on_tap_cb = on_tap
                return
        self.data = data
        if on_tap:
            self.on_tap_cb = on_tap

        urgency = data.get("urgency", "safe")
        act_type = data.get("type", "other")
        
        color = get_urgency_color(urgency)
        type_color = get_type_color(act_type)
        type_label = get_type_label(act_type)
        status_label, status_color = get_status_tag(data)
        urgency_label, urgency_color = get_urgency_badge(urgency)

        deadline_str = data.get("deadline", "")
        formatted_dl = format_deadline(deadline_str)
        cd_text, overdue = get_countdown(deadline_str, act_type)
        progress_val = get_progress_value(deadline_str)
        submission = get_submission_badge(data)

        _details = data.get("details", {})
        _full_name = _details.get("course_full_name", "")
        # WS API provides course_name directly (cleaner than HTML scraping)
        _ws_course = data.get("course_name", "")
        course_clean = clean_course_name(_ws_course or _full_name or data.get("course", ""))

        # UX-7: Time-based countdown color (red < 24h, orange < 3d, green > 3d)
        cd_color = C.CRITICAL if overdue else get_countdown_color(deadline_str)
        bar_color = get_countdown_color(deadline_str) if not overdue else C.CRITICAL

        self._type_text.value = type_label
        self._type_text.color = type_color
        self._type_container.border = ft.border.all(1, type_color)

        self._status_text.value = status_label
        self._status_text.color = status_color
        self._status_container.border = ft.border.all(1, status_color)

        self._urgency_text.value = urgency_label
        self._urgency_text.color = urgency_color
        self._urgency_container.border = ft.border.all(1, urgency_color)

        self._course_text.value = course_clean
        self._title_text.value = data.get("title", "Không có tiêu đề")
        self._deadline_text.value = formatted_dl

        self._countdown_ctrl.value = cd_text
        self._countdown_ctrl.color = cd_color
        self._progress_ctrl.value = progress_val
        self._progress_ctrl.color = bar_color

        self._optional_rows.controls.clear()
        if submission:
            sub_label, sub_color = submission
            self._optional_rows.controls.append(
                ft.Container(
                    content=ft.Text(sub_label, size=10, color=sub_color, weight=ft.FontWeight.W_500),
                    padding=ft.Padding.symmetric(horizontal=7, vertical=2),
                    border=ft.border.all(1, sub_color),
                    border_radius=4,
                )
            )

        self._bar_widget.bgcolor = bar_color

        _sub_status = data.get("submission_status", "unknown")
        _is_submitted = _sub_status in ("submitted", "graded")
        is_critical_active = (urgency_str(urgency) == "critical") and not overdue and not _is_submitted

        if is_critical_active:
            self.border = ft.border.all(1, C.CRITICAL)
            self.shadow = ActivityCard._CRITICAL_SHADOW
            self._is_critical_active = True
        else:
            _card_border_color = C.SAFE if _is_submitted else C.BORDER
            self.border = ft.border.all(1, _card_border_color)
            self.shadow = None
            self._is_critical_active = False
            
        self._initialized = True

    def update_countdown(self):
        deadline_str = self.data.get("deadline", "")
        if not deadline_str:
            return False
        act_type = self.data.get("type", "")
        cd_text, overdue = get_countdown(deadline_str, act_type)
        progress_val     = get_progress_value(deadline_str)
        # UX-7: Time-based color for countdown
        cd_color = C.CRITICAL if overdue else get_countdown_color(deadline_str)
        changed = (self._countdown_ctrl.value != cd_text)
        self._countdown_ctrl.value = cd_text
        self._countdown_ctrl.color = cd_color
        self._progress_ctrl.value  = progress_val
        self._progress_ctrl.color  = cd_color
        return changed
