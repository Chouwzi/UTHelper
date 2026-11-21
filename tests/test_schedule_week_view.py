from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft

from core.today_schedule import ClassSession, ScheduleAuthenticationError
from gui.components.schedule_week_view import ScheduleWeekView, _monday
from gui.core.theme import C, apply_theme


TODAY = date(2026, 11, 21)


def _session(
    day: date,
    *,
    subject: str = "Phân tích và thiết kế hệ thống nhúng thông minh",
    start: time = time(7, 0),
    end: time = time(9, 30),
    course_code: str = "ECE-ENGR-302-2026",
    room: str = "Phòng thực hành Cơ điện tử 03",
    cancelled: bool = False,
) -> ClassSession:
    return ClassSession(
        session_id=f"session-{day.isoformat()}-{start.isoformat()}",
        subject=subject,
        course_code=course_code,
        start_at=datetime.combine(day, start),
        end_at=datetime.combine(day, end),
        room=room,
        campus="Cơ sở 1",
        period_start=1,
        period_end=3,
        cancelled=cancelled,
        note="Phòng thực hành tầng 3" if cancelled else "",
        link="https://portal.invalid/course",
        meeting_url="https://zoom.invalid/class",
        subject_code="CS-301",
        lecturer="Nguyễn Văn An",
        has_outline=True,
        outline="Nội dung đề cương mẫu",
    )


def _page(width: float) -> SimpleNamespace:
    return SimpleNamespace(
        width=width,
        height=844,
        window=SimpleNamespace(width=width),
        run_task=MagicMock(),
        update=MagicMock(),
        show_dialog=MagicMock(),
        pop_dialog=MagicMock(),
    )


def _view(width: float, selected: date = TODAY) -> ScheduleWeekView:
    page = _page(width)
    view = ScheduleWeekView(page)
    monday = selected - timedelta(days=selected.weekday())
    if monday == TODAY - timedelta(days=TODAY.weekday()):
        week = {
            TODAY: (
                _session(TODAY),
                _session(
                    TODAY,
                    subject="Cơ sở dữ liệu nâng cao",
                    start=time(13, 0),
                    end=time(15, 30),
                    course_code="CS-ADV-410",
                    room="A.204",
                ),
            ),
            TODAY - timedelta(days=2): (
                _session(
                    TODAY - timedelta(days=2),
                    subject="Mạng máy tính",
                    course_code="CS-NET-305",
                    room="B.103",
                    cancelled=True,
                ),
            ),
        }
    else:
        week = {
            selected: (
                _session(
                    selected,
                    subject="Mạng máy tính",
                    course_code="CS-NET-305",
                    room="B.103",
                    cancelled=True,
                ),
            ),
            selected + timedelta(days=2): (
                _session(
                    selected + timedelta(days=2),
                    subject="Cơ sở dữ liệu nâng cao",
                    start=time(13, 0),
                    end=time(15, 30),
                    course_code="CS-ADV-410",
                    room="A.204",
                ),
            ),
        }
    view._weeks[monday] = week
    view._live_at[monday] = datetime.now()
    view._selected = selected
    view.visible = True
    view._render()
    return view


def _walk(control):
    seen: set[int] = set()

    def visit(item):
        if item is None or id(item) in seen:
            return
        if not callable(getattr(item, "update", None)):
            return
        seen.add(id(item))
        yield item
        for name in ("content", "title", "subtitle", "leading", "trailing"):
            yield from visit(getattr(item, name, None))
        for name in ("controls", "actions"):
            for child in getattr(item, name, None) or ():
                yield from visit(child)

    yield from visit(control)


def _text_values(control) -> list[str]:
    return [
        str(item.value)
        for item in _walk(control)
        if isinstance(item, ft.Text) and item.value is not None
    ]


def test_narrow_view_shows_only_selected_day_and_uses_day_navigation():
    view = _view(390)
    values = _text_values(view._body)

    assert view._week_label.value == "Thứ 7, 21/11/2026"
    assert "Phân tích và thiết kế hệ thống nhúng thông minh" in values
    assert "Cơ sở dữ liệu nâng cao" in values
    assert "Mạng máy tính" not in values
    assert "07:00 – 09:30" in values
    assert "ECE-ENGR-302-2026" in values
    assert any("Phòng thực hành Cơ điện tử 03" in value for value in values)
    assert all(
        not (isinstance(item, ft.Row) and item.scroll == ft.ScrollMode.AUTO)
        for item in _walk(view._body)
    )

    view._next_btn.on_click(None)
    assert view._selected == TODAY + timedelta(days=1)
    assert "Không có lịch học ngày này" in _text_values(view._body)


def test_narrow_day_navigation_crosses_week_boundary_without_resetting_day():
    sunday = TODAY + timedelta(days=1)
    view = _view(390, selected=sunday)
    next_monday = sunday + timedelta(days=1)
    view._weeks[next_monday] = {
        next_monday: (
        _session(next_monday, subject="Lập trình cơ bản"),
        )
    }
    view._live_at[next_monday] = datetime.now()

    view._next_btn.on_click(None)

    assert view._selected == next_monday
    assert "Lập trình cơ bản" in _text_values(view._body)


def test_desktop_view_shows_seven_days_within_default_window_width():
    view = _view(1120)
    day_columns = [
        item
        for item in _walk(view._body)
        if isinstance(item, ft.Container)
        and isinstance(item.data, dict)
        and item.data.get("schedule_day")
    ]

    assert len(day_columns) == 7
    assert sum(float(item.width or 0) for item in day_columns) <= 1120
    values = _text_values(view._body)
    assert all(day in values for day in ("Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"))
    assert "Phân tích và thiết kế hệ thống nhúng thông minh" in values
    assert "07:00 – 09:30" in values
    assert any("Phòng thực hành Cơ điện tử 03" in value for value in values)
    assert "ECE-ENGR-302-2026" in values


def test_desktop_week_fits_at_the_responsive_breakpoint():
    view = _view(920)
    day_columns = [
        item
        for item in _walk(view._body)
        if isinstance(item, ft.Container)
        and isinstance(item.data, dict)
        and item.data.get("schedule_day")
    ]

    assert len(day_columns) == 7
    used_width = sum(float(item.width or 0) for item in day_columns) + 6 * 8 + 28
    assert used_width <= 920


def test_desktop_empty_week_keeps_a_clear_state():
    view = _view(1120)
    monday = TODAY - timedelta(days=TODAY.weekday())
    view._weeks[monday] = {}
    view._render()

    assert "Tuần này không có lịch học" in _text_values(view._body)


def test_resize_reflows_without_changing_the_selected_date():
    view = _view(1120)
    selected = TODAY - timedelta(days=2)
    view._selected = selected

    view._on_size_change(SimpleNamespace(width=390, height=844))

    assert view._selected == selected
    assert view._week_label.value == "Thứ 5, 19/11/2026"
    assert "Mạng máy tính" in _text_values(view._body)
    assert "Cơ sở dữ liệu nâng cao" not in _text_values(view._body)

    view._on_size_change(SimpleNamespace(width=1120, height=844))

    assert view._selected == selected
    assert len(
        [
            item
            for item in _walk(view._body)
            if isinstance(item, ft.Container)
            and isinstance(item.data, dict)
            and item.data.get("schedule_day")
        ]
    ) == 7


def test_empty_day_and_loading_state_remain_clear():
    view = _view(390, selected=TODAY + timedelta(days=1))
    assert "Không có lịch học ngày này" in _text_values(view._body)

    monday = TODAY - timedelta(days=TODAY.weekday())
    view._weeks.pop(monday)
    view._loading.add(monday)
    view._render()

    assert view._status.visible is True
    assert any(isinstance(item, ft.ProgressRing) for item in _walk(view._status))
    assert any(
        value.startswith("Đang tải lịch học") for value in _text_values(view._status)
    )


def test_summary_detail_action_preserves_full_session_content():
    view = _view(390)
    buttons = [
        item
        for item in _walk(view._body)
        if isinstance(item, ft.TextButton)
        and item.content == "Chi tiết"
    ]

    assert len(buttons) == 2
    assert all(callable(button.on_click) for button in buttons)
    buttons[0].on_click(None)

    dialog = view._page_ref.show_dialog.call_args.args[0]
    assert dialog.semantics_label == "Chi tiết buổi học"
    assert isinstance(dialog.content, ft.KeyboardListener)
    dialog.content.on_key_down(SimpleNamespace(key="Enter"))
    view._page_ref.pop_dialog.assert_not_called()
    dialog.content.on_key_down(SimpleNamespace(key="Escape"))
    view._page_ref.pop_dialog.assert_called_once()
    details = _text_values(dialog)
    action_labels = [
        item.content
        for item in _walk(dialog)
        if isinstance(item, ft.TextButton) and isinstance(item.content, str)
    ]
    assert "Giảng viên: Nguyễn Văn An" in details
    assert "Cơ sở: Cơ sở 1" in details
    assert "Truy cập Zoom" in action_labels
    assert "Đề cương chi tiết học phần" in action_labels

    action_controls = [
        item for item in _walk(dialog) if isinstance(item, ft.TextButton)
    ]
    assert all(item.height >= 44 for item in action_controls)


def test_narrow_navigation_controls_keep_accessible_names_and_touch_targets():
    view = _view(390)

    for button, label in (
        (view._prev_btn, "Ngày trước"),
        (view._next_btn, "Ngày sau"),
        (view._today_btn, "Hôm nay"),
        (view._refresh_btn, "Tải lại lịch học"),
    ):
        assert button.content == label
        assert button.width >= 44
        assert button.height >= 44


def test_week_navigation_still_moves_a_full_week_on_desktop():
    view = _view(1120)

    assert view._prev_btn.content == "Tuần trước"
    assert view._next_btn.content == "Tuần sau"

    view._next_btn.on_click(None)

    assert view._selected == TODAY + timedelta(days=7)


def test_schedule_links_open_in_app_by_default_and_validate_origins(monkeypatch):
    in_app_calls = []
    external_calls = []
    launched = []
    monkeypatch.setattr(
        "gui.components.schedule_week_view.ft.UrlLauncher.launch_url",
        lambda self, url: launched.append(url),
        raising=False,
    )
    session = _session(TODAY)
    session = ClassSession(
        session_id=session.session_id,
        subject=session.subject,
        course_code=session.course_code,
        start_at=session.start_at,
        end_at=session.end_at,
        room=session.room,
        campus=session.campus,
        period_start=session.period_start,
        period_end=session.period_end,
        cancelled=False,
        note="",
        link="https://courses.ut.edu.vn/course/view.php?id=99",
        meeting_url="https://us02web.zoom.us/j/987654",
    )
    view = ScheduleWeekView(
        page=SimpleNamespace(width=390, height=700, dialogs=[], show_dialog=lambda _d: None),
        fetch_week=lambda _d: {},
        on_open_url=lambda url, title: in_app_calls.append((url, title)),
        on_open_external=lambda url: external_calls.append(url),
    )

    card = view._session_card(session, datetime(2026, 11, 21, 6, 0))
    ext_wrappers = [
        c
        for c in _walk(card)
        if isinstance(c, ft.Semantics)
        and isinstance(c.content, ft.IconButton)
        and c.content.icon == ft.Icons.OPEN_IN_NEW_ROUNDED
    ]
    assert len(ext_wrappers) == 2
    assert ext_wrappers[0].content.size_constraints.min_width == 44
    assert ext_wrappers[0].content.size_constraints.min_height == 44

    view._open_url(session.link, "LMS", require_uth_lms=True)
    view._open_url(session.meeting_url, "Zoom")
    view._open_url("https://evil.example/phish", "LMS", require_uth_lms=True)
    view._open_url("http://courses.ut.edu.vn/insecure", "LMS", require_uth_lms=True)
    for wrapper in ext_wrappers:
        wrapper.content.on_click(None)

    assert in_app_calls == [
        ("https://courses.ut.edu.vn/course/view.php?id=99", "LMS"),
        ("https://us02web.zoom.us/j/987654", "Zoom"),
    ]
    assert set(external_calls) == {
        "https://courses.ut.edu.vn/course/view.php?id=99",
        "https://us02web.zoom.us/j/987654",
    }
    assert launched == []


def test_schedule_theme_update_rebuilds_nav_button_styles():
    view = _view(1120)
    try:
        apply_theme("steel_light")
        view.update_theme()
        expected_secondary = C.TEXT_SECONDARY
        assert view._prev_btn.style.color == expected_secondary
        assert view._next_btn.style.color == expected_secondary
        assert view._refresh_btn.style.color == expected_secondary
        assert view._today_btn.style.color == C.ACCENT
    finally:
        apply_theme("midnight_blue")


def test_schedule_auth_error_clears_cached_weeks_and_shows_error():
    def _auth_fail(_target_date):
        raise ScheduleAuthenticationError("Hết phiên đăng nhập")

    view = ScheduleWeekView(
        page=SimpleNamespace(
            width=390,
            height=700,
            dialogs=[],
            show_dialog=lambda _d: None,
            update=lambda: None,
        ),
        fetch_week=_auth_fail,
    )
    anchor = _monday(TODAY)
    view._selected = TODAY
    view._weeks[anchor] = {TODAY: (_session(TODAY),)}
    view._live_at[anchor] = datetime.now()

    asyncio.run(view._load_async(anchor))

    assert view._weeks == {}
    assert view._live_at == {}
    assert view._auth_failed is True
    texts = [item.value for item in _walk(view) if isinstance(item, ft.Text)]
    assert any("Chưa đăng nhập Portal" in text for text in texts)


def test_schedule_outline_modal_clamped_width_escape_close_and_focus_restore():
    dialogs = []
    pop_calls = []
    focused = []
    page = SimpleNamespace(
        width=360,
        height=640,
        dialogs=dialogs,
        show_dialog=dialogs.append,
        pop_dialog=lambda: pop_calls.append(True),
        on_keyboard_event=None,
    )

    class _Trigger:
        async def focus(self):
            focused.append(True)

    view = ScheduleWeekView(page=page, fetch_week=lambda _d: {})
    view._show_outline(_session(TODAY), return_focus=_Trigger())

    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.modal is True
    assert "Đề cương" in dialog.semantics_label
    assert dialog.content.content.width == 312.0
    assert callable(dialog.content.on_key_down)
    dialog.content.on_key_down(SimpleNamespace(key="Escape"))
    assert pop_calls == [True]
    asyncio.run(dialog.on_dismiss(None))
    assert focused == [True]


def test_schedule_prefetch_aborts_immediately_when_reset_bumps_generation(monkeypatch):
    calls = []

    def _counting_fetch(target_date):
        calls.append(target_date)
        return {}

    view = ScheduleWeekView(
        page=SimpleNamespace(
            width=1120,
            height=700,
            dialogs=[],
            show_dialog=lambda _d: None,
            update=lambda: None,
        ),
        fetch_week=_counting_fetch,
    )

    async def _fake_load(_monday_date):
        calls.append(_monday_date)

    async def _fake_sleep(_seconds):
        view.reset()

    monkeypatch.setattr(view, "_load_async", _fake_load)
    monkeypatch.setattr("gui.components.schedule_week_view.asyncio.sleep", _fake_sleep)
    asyncio.run(view._prefetch_async())

    # Stops after first week because reset() bumped generation during _PREFETCH_GAP_S
    assert len(calls) == 1
