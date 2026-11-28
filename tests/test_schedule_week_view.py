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
    assert dialog.scrollable is True
    assert dialog.title is None
    assert not dialog.actions
    assert isinstance(dialog.content, ft.KeyboardListener)
    dialog.content.on_key_down(SimpleNamespace(key="Enter"))
    view._page_ref.pop_dialog.assert_not_called()
    dialog.content.on_key_down(SimpleNamespace(key="Escape"))
    view._page_ref.pop_dialog.assert_called_once()
    details = _text_values(dialog)
    assert "CHI TIẾT BUỔI HỌC" in details
    assert "GIẢNG VIÊN" in details
    assert "Nguyễn Văn An" in details
    assert "Cơ sở: Cơ sở 1" in details
    assert "Mã lớp: ECE-ENGR-302-2026" in details
    assert "Mã HP: CS-301" in details
    assert "Truy cập Zoom" in details
    assert "Đề cương chi tiết học phần" in details

    semantic_buttons = [
        item
        for item in _walk(dialog)
        if isinstance(item, ft.Semantics) and getattr(item, "button", False)
    ]
    assert semantic_buttons
    for sem in semantic_buttons:
        assert sem.content is not None
        assert getattr(sem.content, "visible", True) is True


def test_narrow_navigation_controls_keep_accessible_names_and_touch_targets():
    view = _view(390)

    assert view._prev_btn.icon == ft.Icons.CHEVRON_LEFT_ROUNDED
    assert view._prev_btn.tooltip == "Ngày trước"
    assert view._prev_semantics.label == "Ngày trước"
    assert view._prev_btn.size_constraints.min_width >= 44
    assert view._prev_btn.size_constraints.min_height >= 44

    assert view._next_btn.icon == ft.Icons.CHEVRON_RIGHT_ROUNDED
    assert view._next_btn.tooltip == "Ngày sau"
    assert view._next_semantics.label == "Ngày sau"
    assert view._next_btn.size_constraints.min_width >= 44
    assert view._next_btn.size_constraints.min_height >= 44

    assert view._today_btn.content == "Hôm nay"
    assert view._today_btn.width >= 44
    assert view._today_btn.height >= 44

    assert view._refresh_btn.icon == ft.Icons.REFRESH_ROUNDED
    assert view._refresh_btn.tooltip == "Tải lại lịch học"
    assert view._refresh_semantics.label == "Tải lại lịch học"
    assert view._refresh_btn.size_constraints.min_width >= 44
    assert view._refresh_btn.size_constraints.min_height >= 44
    assert view._nav_row.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
    assert view._nav_row.wrap is not True
    assert view._nav_row.controls[-1] is view._refresh_action_box


def test_week_navigation_still_moves_a_full_week_on_desktop():
    view = _view(1120)

    assert view._prev_btn.icon == ft.Icons.CHEVRON_LEFT_ROUNDED
    assert view._prev_btn.tooltip == "Tuần trước"
    assert view._prev_semantics.label == "Tuần trước"
    assert view._next_btn.icon == ft.Icons.CHEVRON_RIGHT_ROUNDED
    assert view._next_btn.tooltip == "Tuần sau"
    assert view._next_semantics.label == "Tuần sau"

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
        assert view._prev_btn.icon_color == C.TEXT_PRIMARY
        assert view._next_btn.icon_color == C.TEXT_PRIMARY
        assert view._picker_chevron_icon.color == C.TEXT_SECONDARY
        assert view._refresh_btn.icon_color == C.TEXT_SECONDARY
        assert view._refresh_action_box.bgcolor == C.SURFACE
        assert view._stepper_box.bgcolor == C.SURFACE
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


def test_schedule_reset_while_visible_and_prefetching_restarts_9_week_prefetch(monkeypatch):
    from core.today_schedule import WeekScheduleCache

    async def scenario():
        monkeypatch.setattr("gui.components.schedule_week_view._PREFETCH_GAP_S", 0.01)
        tasks = []
        fetch_calls = []
        gate = asyncio.Event()

        class _Page:
            width = 1120
            height = 844
            window = SimpleNamespace(width=1120)
            dialogs = []

            def run_task(self, coro_fn, *args, **kwargs):
                t = asyncio.create_task(coro_fn(*args, **kwargs))
                tasks.append(t)
                return t

            def update(self):
                pass

            def show_dialog(self, _d):
                pass

            def pop_dialog(self):
                pass

        account = {"name": "userA"}

        def _fetch_week(monday_date):
            who = account["name"]
            fetch_calls.append((who, monday_date))
            return {
                monday_date: (
                    _session(monday_date, subject=f"{who}-class"),
                )
            }

        view = ScheduleWeekView(page=_Page(), fetch_week=_fetch_week)
        view.show()
        # Let gen-0 start and enter prefetch sleep/next week
        await asyncio.sleep(0.03)
        assert view._prefetching is True

        # Switch account and reset while visible=True and prefetch is still running
        account["name"] = "userB"
        view.reset()
        gate.set()

        # Wait until all scheduled tasks finish
        while tasks:
            pending = [t for t in tasks if not t.done()]
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)

        user_b_weeks = {m for who, m in fetch_calls if who == "userB"}
        assert len(user_b_weeks) == len(WeekScheduleCache.window()) == 9
        assert all(
            sess.subject == "userB-class"
            for week in view._weeks.values()
            for sessions in week.values()
            for sess in sessions
        )

    asyncio.run(scenario())


def test_schedule_on_size_change_rerenders_on_small_shrink_in_wide_mode():
    view = _view(1120)
    render_calls = []
    orig_render = view._render

    def _spy_render():
        render_calls.append(view._layout_width)
        orig_render()

    view._render = _spy_render

    # Shrink by 12px (<24px) while staying in wide mode (>=920px)
    view._on_size_change(SimpleNamespace(width=1108, height=844))

    assert render_calls == [1108.0]
    day_columns = [
        item
        for item in _walk(view._body)
        if isinstance(item, ft.Container)
        and isinstance(item.data, dict)
        and item.data.get("schedule_day")
    ]
    used_width = sum(float(item.width or 0) for item in day_columns) + 6 * 8 + 28
    assert used_width <= 1108


def test_today_column_uses_bold_solid_accent_header_and_tinted_frame(monkeypatch):
    fake_today = date(2026, 11, 22)  # Chủ nhật

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return fake_today

    monkeypatch.setattr("gui.components.schedule_week_view.date", _FixedDate)
    view = _view(1120, selected=fake_today)
    day_columns = [
        item
        for item in _walk(view._body)
        if isinstance(item, ft.Container)
        and isinstance(item.data, dict)
        and item.data.get("schedule_day")
    ]
    assert len(day_columns) == 7

    today_cols = [c for c in day_columns if c.data.get("is_today")]
    other_cols = [c for c in day_columns if not c.data.get("is_today")]
    assert len(today_cols) == 1
    assert len(other_cols) == 6

    sunday_col = today_cols[0]
    assert sunday_col.data["schedule_day"] == "2026-11-22"
    sunday_header = sunday_col.content.controls[0]
    assert sunday_header.bgcolor == C.ACCENT
    header_texts = [t for t in _walk(sunday_header) if isinstance(t, ft.Text)]
    assert [t.value for t in header_texts] == ["Chủ nhật", "22/11"]
    assert all(t.color == C.ON_ACCENT for t in header_texts)
    assert all(t.weight == ft.FontWeight.W_700 for t in header_texts)

    monday_col = other_cols[0]
    monday_header = monday_col.content.controls[0]
    assert monday_header.bgcolor == C.SURFACE_ELEVATED
    assert monday_col.bgcolor == C.SURFACE


def test_session_detail_popup_clicks_dismiss_dialog_and_open_in_app_webview(monkeypatch):
    in_app_calls: list[tuple[str, str]] = []
    external_calls: list[str] = []
    dialogs: list[ft.AlertDialog] = []
    pops: list[bool] = []
    monkeypatch.setattr(
        "gui.components.schedule_week_view.ft.UrlLauncher.launch_url",
        lambda self, url: (_ for _ in ()).throw(AssertionError(f"Unexpected external launch: {url}")),
        raising=False,
    )
    page = SimpleNamespace(
        width=1120,
        height=760,
        window=SimpleNamespace(width=1120),
        dialogs=dialogs,
        show_dialog=dialogs.append,
        pop_dialog=lambda: pops.append(True),
        update=lambda: None,
        run_task=lambda *_a, **_k: None,
    )
    view = ScheduleWeekView(
        page=page,
        fetch_week=lambda _d: {},
        on_open_url=lambda url, title: in_app_calls.append((url, title)),
        on_open_external=lambda url: external_calls.append(url),
    )
    session = ClassSession(
        session_id="sess-lms-1",
        subject="Quản trị doanh nghiệp CNTT",
        course_code="012012113707",
        start_at=datetime(2026, 11, 21, 20, 10),
        end_at=datetime(2026, 11, 21, 22, 40),
        room="E-Learning",
        campus="Online",
        period_start=16,
        period_end=18,
        cancelled=False,
        note="Tài liệu bổ sung: https://docs.example.com/guide",
        link="https://courses.ut.edu.vn/course/view.php?id=121137",
        meeting_url="https://us02web.zoom.us/j/96538251192",
        subject_code="121137",
        lecturer="",
        has_outline=True,
        outline="Xem thêm [Đề cương PDF](https://portal.ut.edu.vn/syllabus/121137.pdf)",
    )

    view._show_session_details(session)
    assert len(dialogs) == 1
    detail_dialog = dialogs[-1]

    # Click primary LMS CTA -> pops detail modal and opens in-app WebView (NOT external)
    lms_btn = next(
        c
        for c in _walk(detail_dialog)
        if isinstance(c, ft.Container)
        and callable(getattr(c, "on_click", None))
        and "Mở khóa học LMS" in _text_values(c)
    )
    lms_btn.on_click(None)
    assert len(pops) == 1
    assert in_app_calls == [
        ("https://courses.ut.edu.vn/course/view.php?id=121137", "LMS · Quản trị doanh nghiệp CNTT")
    ]
    assert external_calls == []

    # Click Outline card -> pops detail modal, opens Outline modal; Back returns to detail; link opens in-app WebView
    outline_btn = next(
        c
        for c in _walk(detail_dialog)
        if isinstance(c, ft.Container)
        and callable(getattr(c, "on_click", None))
        and "Đề cương chi tiết học phần" in _text_values(c)
    )
    outline_btn.on_click(None)
    assert len(pops) == 2
    assert len(dialogs) == 2
    outline_dialog = dialogs[-1]
    md = next(c for c in _walk(outline_dialog) if isinstance(c, ft.Markdown))
    md.on_tap_link(SimpleNamespace(data="https://portal.ut.edu.vn/syllabus/121137.pdf"))
    assert len(pops) == 3
    assert in_app_calls[-1] == (
        "https://portal.ut.edu.vn/syllabus/121137.pdf",
        "Đề cương · Quản trị doanh nghiệp CNTT",
    )
    assert external_calls == []


def test_app_controller_open_url_in_app_mounts_embedded_webview_pane_and_handles_back(monkeypatch):
    from gui.app_controller import AppController

    mounted_panes = []

    class _FakeWebViewPane(ft.Container):
        def __init__(self, **kwargs):
            super().__init__()
            self.kwargs = kwargs
            self.bounds = None
            self.disposed = False
            self.back_steps = 1
            mounted_panes.append(self)

        def sync_bounds(self, left, top, width, height, *, nav_bottom_inset=0.0):
            self.bounds = (left, top, width, height, nav_bottom_inset)

        def handle_back(self) -> bool:
            if self.back_steps > 0:
                self.back_steps -= 1
                return True
            return False

        def dispose(self) -> None:
            self.disposed = True

    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._is_windows_webview2_platform",
        lambda _page: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.embedded_webview_host._webview2_runtime_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "gui.components.courses.webview_pane.CoursesWebViewPane",
        _FakeWebViewPane,
    )

    popped = []
    updated = []
    courses_client = object()
    controller = AppController.__new__(AppController)
    controller.page = SimpleNamespace(
        width=1280,
        height=820,
        web=False,
        platform=ft.PagePlatform.WINDOWS,
        pop_dialog=lambda: popped.append(True),
        update=lambda: updated.append(True),
    )
    controller._nav_shell = SimpleNamespace(rail_visible=True)
    controller._app_webview_host = ft.Container(visible=False)
    controller._app_webview_pane = None
    controller.quiz_player_view = SimpleNamespace(handle_back=lambda: False, visible=False)
    controller._courses_coordinator = SimpleNamespace(
        preview_server="preview_srv",
        _client_for=lambda src: courses_client if src == "courses" else None,
    )

    controller._open_url_in_app(
        "https://courses.ut.edu.vn/course/view.php?id=121137",
        "LMS · Quản trị doanh nghiệp CNTT",
    )

    assert popped == [True]
    assert controller._app_webview_host.visible is True
    assert len(mounted_panes) == 1
    pane = mounted_panes[0]
    assert pane.kwargs["url"] == "https://courses.ut.edu.vn/course/view.php?id=121137"
    assert pane.kwargs["source"] == "courses"
    assert pane.kwargs["session_client"] is courses_client
    assert pane.bounds is not None

    # First Back steps inside WebView history; second Back closes & disposes the overlay
    assert asyncio.run(controller._navigate_back()) is True
    assert controller._app_webview_host.visible is True
    assert pane.disposed is False

    assert asyncio.run(controller._navigate_back()) is True
    assert controller._app_webview_host.visible is False
    assert controller._app_webview_pane is None
    assert pane.disposed is True


def test_desktop_session_summary_cards_enforce_equal_172px_height_across_room_lengths():
    view = _view(1120)
    s_elearning = _session(
        TODAY,
        subject="Nhập môn Trí tuệ nhân tạo",
        room="E-Learning",
        cancelled=False,
    )
    s_long_cs1_address = _session(
        TODAY,
        subject="Phân tích và thiết kế hệ thống thông tin quản lý doanh nghiệp hiện đại",
        room="F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)",
        cancelled=True,
    )

    card_short = view._session_summary_card(s_elearning, narrow=False)
    card_long = view._session_summary_card(s_long_cs1_address, narrow=False)
    card_narrow = view._session_summary_card(s_long_cs1_address, narrow=True)

    assert card_short.height == 172
    assert card_long.height == 172
    assert card_narrow.height is None
    assert card_short.content.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
    assert card_short.bgcolor == C.SURFACE
    assert card_long.bgcolor == ft.Colors.with_opacity(0.16, C.CRITICAL)

    assert isinstance(card_long.content, ft.Stack)
    card_body = card_long.content.controls[0]
    watermark = card_long.content.controls[1].content
    assert watermark.bgcolor is None
    assert watermark.rotate.angle == -0.32
    assert "TẠM NGƯNG" in _text_values(watermark)
    watermark_text = watermark.content
    assert watermark_text.size == 20
    assert watermark_text.color == ft.Colors.with_opacity(0.32, C.CRITICAL)

    assert card_body.alignment == ft.MainAxisAlignment.SPACE_BETWEEN
    summary_col = card_body.controls[0]
    top_row = summary_col.controls[0]
    assert isinstance(top_row, ft.Row)
    assert "07:00 – 09:30" in _text_values(top_row)
    assert "TẠM NGƯNG" not in _text_values(top_row)

    title_txt, class_txt, room_txt = (
        summary_col.controls[1],
        summary_col.controls[2],
        summary_col.controls[3],
    )
    assert title_txt.max_lines == 2
    assert class_txt.max_lines == 1
    assert room_txt.max_lines == 2


def test_hydrate_and_ensure_weeks_hydrates_cache_immediately_and_debounces_rapid_month_swipes(
    tmp_path,
):
    from core.today_schedule import WeekScheduleCache

    today_monday = _monday(date.today())
    cache = WeekScheduleCache(cache_dir=tmp_path, namespace="student-1")
    cached_session = _session(today_monday, subject="Môn từ cache 0ms")
    cache.save_week(
        today_monday,
        {today_monday: (cached_session,)},
        datetime.now(),
    )

    month1_far = today_monday + timedelta(weeks=8)
    month2_far = today_monday + timedelta(weeks=14)
    fetched_mondays: list[date] = []
    change_notifications: list[int] = []

    async def scenario():
        tasks: list[asyncio.Task] = []

        class _AsyncPage:
            width = 1120
            height = 844
            window = SimpleNamespace(width=1120)

            def run_task(self, coro_fn, *args, **kwargs):
                t = asyncio.create_task(coro_fn(*args, **kwargs))
                tasks.append(t)
                return t

            def update(self):
                pass

        def _fetch_week(monday_date: date):
            fetched_mondays.append(monday_date)
            return {
                monday_date: (
                    _session(monday_date, subject=f"Live {monday_date.isoformat()}"),
                )
            }

        view = ScheduleWeekView(
            page=_AsyncPage(),
            fetch_week=_fetch_week,
            cache=cache,
            on_weeks_changed=lambda: change_notifications.append(1),
        )

        # First month swipe (debounced 250ms): cache must hydrate synchronously in 0ms
        view.hydrate_and_ensure_weeks([today_monday, month1_far], debounce_ms=250)
        hydrated = view.sessions_for_day(today_monday)
        assert len(hydrated) == 1
        assert hydrated[0].session_id == cached_session.session_id
        assert hydrated[0].subject == "Môn từ cache 0ms"
        assert hydrated[0].link == ""  # Disk cache never persists personal URLs
        assert today_monday in view.sessions_for_week(today_monday)

        # Within 50ms, user swipes to month2 -> month1 fetch MUST be cancelled!
        await asyncio.sleep(0.05)
        assert month1_far not in fetched_mondays
        view.hydrate_and_ensure_weeks([month2_far], debounce_ms=250)

        await asyncio.sleep(0.32)
        pending = [t for t in tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        assert month1_far not in fetched_mondays
        assert month2_far in fetched_mondays
        assert view.sessions_for_day(month2_far)[0].subject == f"Live {month2_far.isoformat()}"
        assert len(change_notifications) >= 1

    asyncio.run(scenario())


def test_schedule_week_view_duck_typed_maintenance_preserves_cache_and_notifies_callback():
    class PortalMaintenanceError(RuntimeError):
        def __init__(self, notice):
            super().__init__("UTH maintenance")
            self.notice = notice

    notice = SimpleNamespace(
        source="portal",
        window_text="01h00 - 04h00 (giờ Việt Nam)",
        reopen_text="04h00 ngày 28/11/2026",
        dedupe_key="2026-11-28|01h00-04h00|04h00",
    )
    maintenance_events: list[tuple[object, bool]] = []

    def _on_maint(received_notice, *, user_initiated: bool = False):
        maintenance_events.append((received_notice, user_initiated))

    fetch_calls: list[date] = []

    def _maint_fail(monday_date: date):
        fetch_calls.append(monday_date)
        raise PortalMaintenanceError(notice)

    async def scenario():
        tasks: list[asyncio.Task] = []

        class _AsyncPage:
            width = 1120
            height = 760
            window = SimpleNamespace(width=1120)

            def run_task(self, coro_fn, *args, **kwargs):
                t = asyncio.create_task(coro_fn(*args, **kwargs))
                tasks.append(t)
                return t

            def update(self):
                pass

        view = ScheduleWeekView(
            page=_AsyncPage(),
            fetch_week=_maint_fail,
            on_maintenance=_on_maint,
        )
        anchor = _monday(TODAY)
        cached_sess = _session(TODAY, subject="Lịch đã lưu an toàn")
        view._weeks[anchor] = {TODAY: (cached_sess,)}

        await view._load_async(anchor)
        assert view.sessions_for_day(TODAY) == (cached_sess,)
        assert view._auth_failed is False
        is_loading, err = view.week_status(anchor)
        assert is_loading is False
        assert "01h00 - 04h00" in err
        assert "04h00 ngày 28/11/2026" in err
        assert maintenance_events == [(notice, False)]

        # Prefetch should not hammer remaining weeks while _maintenance_active is True
        fetch_calls.clear()
        await view._prefetch_async()
        assert fetch_calls == []

        # Manual reload_week resets _maintenance_active and passes user_initiated=True
        view.reload_week(anchor)
        pending = [t for t in tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        assert maintenance_events[-1] == (notice, True)
        assert view.sessions_for_day(TODAY) == (cached_sess,)

    asyncio.run(scenario())


def test_clicking_week_range_label_opens_month_date_picker_and_jumps_to_selected_day():
    dialogs: list[ft.AlertDialog] = []
    pops: list[bool] = []
    requested_mondays: list[date] = []

    selected = date(2026, 11, 25)
    selected_monday = _monday(selected)  # 2026-11-23
    class_day = date(2026, 11, 24)

    page = SimpleNamespace(
        width=1120,
        height=760,
        window=SimpleNamespace(width=1120),
        dialogs=dialogs,
        show_dialog=dialogs.append,
        pop_dialog=lambda: pops.append(True),
        run_task=MagicMock(),
        update=MagicMock(),
    )

    def _fetch_week(monday_date: date):
        requested_mondays.append(monday_date)
        return {}

    view = ScheduleWeekView(page=page, fetch_week=_fetch_week)
    view._weeks[selected_monday] = {class_day: (_session(class_day),)}
    view._live_at[selected_monday] = datetime.now()
    view._selected = selected
    view.visible = True
    view._render()

    assert view._week_label.value == "23/11 – 29/11/2026"
    assert callable(view._week_label_box.on_click)

    view._week_label_box.on_click(None)
    assert len(dialogs) == 1
    dialog = dialogs[-1]
    assert dialog.modal is True
    assert dialog.semantics_label == "Chọn ngày trong tháng"
    assert "Tháng 11, 2026" in _text_values(dialog)

    nov_cells = {
        c.data["picker_date"]: c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and isinstance(c.data, dict)
        and "picker_date" in c.data
    }
    assert nov_cells["2026-11-25"].data["is_selected"] is True
    assert nov_cells["2026-11-23"].data["in_selected_week"] is True
    assert nov_cells["2026-11-29"].data["in_selected_week"] is True
    assert nov_cells["2026-11-24"].data["has_classes"] is True
    assert nov_cells["2026-11-26"].data["has_classes"] is False

    next_month_btn = next(
        c.content
        for c in _walk(dialog)
        if isinstance(c, ft.Semantics)
        and c.label == "Tháng sau"
        and isinstance(c.content, ft.IconButton)
    )
    prev_month_btn = next(
        c.content
        for c in _walk(dialog)
        if isinstance(c, ft.Semantics)
        and c.label == "Tháng trước"
        and isinstance(c.content, ft.IconButton)
    )

    # Verify month switching and year rollover (Nov 2026 -> Dec 2026 -> Jan 2027 -> Dec 2026)
    next_month_btn.on_click(None)
    assert "Tháng 12, 2026" in _text_values(dialog)
    next_month_btn.on_click(None)
    assert "Tháng 1, 2027" in _text_values(dialog)
    prev_month_btn.on_click(None)
    assert "Tháng 12, 2026" in _text_values(dialog)

    dec_cells = {
        c.data["picker_date"]: c
        for c in _walk(dialog)
        if isinstance(c, ft.Container)
        and isinstance(c.data, dict)
        and "picker_date" in c.data
    }
    target_day = date(2026, 12, 16)
    dec_cells[target_day.isoformat()].on_click(None)

    assert pops == [True]
    assert view._selected == target_day
    assert view._week_label.value == "14/12 – 20/12/2026"
    assert page.run_task.called
    assert page.run_task.call_args.args[1] == date(2026, 12, 14)


def test_month_date_picker_today_button_and_escape_key():
    dialogs: list[ft.AlertDialog] = []
    pops: list[bool] = []
    focused: list[bool] = []

    page = SimpleNamespace(
        width=390,
        height=844,
        window=SimpleNamespace(width=390),
        dialogs=dialogs,
        show_dialog=dialogs.append,
        pop_dialog=lambda: pops.append(True),
        run_task=MagicMock(),
        update=MagicMock(),
    )

    class _Trigger:
        async def focus(self):
            focused.append(True)

    far_date = date(2027, 1, 15)
    view = ScheduleWeekView(page=page, fetch_week=lambda _d: {})
    view._selected = far_date
    view.visible = True
    view._render()

    view._show_month_date_picker(return_focus=_Trigger())
    assert len(dialogs) == 1
    dialog = dialogs[-1]

    # Escape dismisses without changing _selected and restores focus on dismiss
    dialog.content.on_key_down(SimpleNamespace(key="Escape"))
    assert pops == [True]
    assert view._selected == far_date
    asyncio.run(dialog.on_dismiss(None))
    assert focused == [True]

    # Re-open via real _week_label_box click, pick "Hôm nay", and run real Flet on_dismiss lifecycle
    pops.clear()
    assert hasattr(view._week_label_box, "focus") and callable(view._week_label_box.focus)
    view._week_label_box.on_click(None)
    dialog2 = dialogs[-1]
    today_btn = next(
        c
        for c in _walk(dialog2)
        if isinstance(c, ft.TextButton) and c.content == "Hôm nay"
    )
    today_btn.on_click(None)
    asyncio.run(dialog2.on_dismiss(None))
    assert pops == [True]
    assert view._selected == date.today()

    # Also verify defensive focus guard when a non-focusable Container is passed to return_focus
    view._show_month_date_picker(return_focus=ft.Container())
    asyncio.run(dialogs[-1].on_dismiss(None))



