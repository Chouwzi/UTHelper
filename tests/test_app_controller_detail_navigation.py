import asyncio
import os
import sys
import threading
from datetime import datetime
from types import SimpleNamespace


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController
import gui.app_controller as app_controller_module
from gui.components.detail_view import DetailView


def test_latest_detail_click_wins_when_older_request_finishes_last():
    """A late response for quiz A must never replace the newer quiz B view."""
    controller = AppController.__new__(AppController)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller._detail_request_epoch = 0
    controller._diag_screen = lambda *_args, **_kwargs: None
    controller._diag_breadcrumb = lambda *_args, **_kwargs: None

    started = {"A": threading.Event(), "B": threading.Event()}
    release = {"A": threading.Event(), "B": threading.Event()}

    def fetch_full_details(data):
        quiz_id = data["id"]
        started[quiz_id].set()
        assert release[quiz_id].wait(2)
        return {**data, "details": {}}

    displayed = []
    controller.orchestrator = SimpleNamespace(fetch_full_details=fetch_full_details)
    controller.view_manager = SimpleNamespace(
        show_detail_loading=lambda _data: None,
        show_detail_data=lambda data: displayed.append(data["id"]),
        show_detail_error=lambda data: displayed.append(f"error:{data['id']}"),
    )

    async def scenario():
        first = asyncio.create_task(
            controller._show_detail_async(
                {"id": "A", "url": "https://example.test/quiz/A"}
            )
        )
        assert await asyncio.to_thread(started["A"].wait, 1)

        second = asyncio.create_task(
            controller._show_detail_async(
                {"id": "B", "url": "https://example.test/quiz/B"}
            )
        )
        assert await asyncio.to_thread(started["B"].wait, 1)

        release["B"].set()
        await second
        release["A"].set()
        await first

    asyncio.run(scenario())

    assert displayed == ["B"]


def test_card_detail_warmup_is_ordered_and_uses_one_background_worker():
    controller = AppController.__new__(AppController)
    calls = []

    class Orchestrator:
        def prefetch_all_details(
            self, activities, workers, cancel_flag, force_refresh
        ):
            calls.append(
                {
                    "urls": [item["url"] for item in activities],
                    "workers": workers,
                    "cancelled": cancel_flag(),
                    "force_refresh": force_refresh,
                }
            )
            return len(activities)

    controller.orchestrator = Orchestrator()
    cancel_event = threading.Event()
    activities = [
        {
            "url": "https://example.test/quiz/far",
            "type": "quiz",
            "deadline": "2026-08-31 12:00:00",
        },
        {
            "url": "https://example.test/attendance/nearest",
            "type": "attendance",
            "deadline": "2026-08-21 12:15:00",
        },
        {
            "url": "https://example.test/assignment/near",
            "type": "assignment",
            "deadline": "2026-08-21 13:00:00",
        },
    ]

    asyncio.run(
        controller._warm_card_details_async(
            activities,
            cancel_event,
            now=datetime(2026, 8, 21, 12, 0, 0),
        )
    )

    assert calls == [
        {
            "urls": [
                "https://example.test/attendance/nearest",
                "https://example.test/assignment/near",
                "https://example.test/quiz/far",
            ],
            "workers": 1,
            "cancelled": False,
            "force_refresh": False,
        }
    ]


def test_scheduling_a_new_card_warmup_cancels_the_previous_pass(monkeypatch):
    controller = AppController.__new__(AppController)
    quiz = {
        "url": "https://example.test/quiz/near",
        "type": "quiz",
        "deadline": "2026-08-21 13:00:00",
    }
    controller.all_data = [quiz]
    controller._data_lock = threading.Lock()
    previous = threading.Event()
    controller._card_detail_warm_cancel_event = previous
    scheduled = []
    controller._safe_run_task = lambda *args: scheduled.append(args) or object()
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "token")

    assert controller._schedule_card_detail_warmup() is True

    assert previous.is_set()
    assert scheduled[0][0] == controller._warm_card_details_async
    assert scheduled[0][1] == [quiz]
    assert scheduled[0][2] is controller._card_detail_warm_cancel_event
    assert not controller._card_detail_warm_cancel_event.is_set()


def test_older_detail_is_rejected_when_it_finishes_image_caching_last(monkeypatch):
    from gui.core import utils

    controller = AppController.__new__(AppController)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller._detail_request_epoch = 0
    controller._diag_screen = lambda *_args, **_kwargs: None
    controller._diag_breadcrumb = lambda *_args, **_kwargs: None
    controller.orchestrator = SimpleNamespace(
        fetch_full_details=lambda data: {
            **data,
            "details": (
                {"description_html": '<img src="https://example.test/a.png">'}
                if data["id"] == "A"
                else {}
            ),
        }
    )
    cache_started = threading.Event()
    release_cache = threading.Event()

    def slow_image_cache(html):
        cache_started.set()
        assert release_cache.wait(2)
        return html

    monkeypatch.setattr(utils, "pre_cache_description_images", slow_image_cache)
    displayed = []
    controller.view_manager = SimpleNamespace(
        show_detail_loading=lambda _data: None,
        show_detail_data=lambda data: displayed.append(data["id"]),
        show_detail_error=lambda data: displayed.append(f"error:{data['id']}"),
    )

    async def scenario():
        first = asyncio.create_task(
            controller._show_detail_async(
                {"id": "A", "url": "https://example.test/quiz/A"}
            )
        )
        assert await asyncio.to_thread(cache_started.wait, 1)
        await controller._show_detail_async(
            {"id": "B", "url": "https://example.test/quiz/B"}
        )
        release_cache.set()
        await first

    asyncio.run(scenario())

    assert displayed == ["B"]


def test_closing_detail_invalidates_a_late_response():
    controller = AppController.__new__(AppController)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller._detail_request_epoch = 0
    controller._detail_from_calendar = False
    controller._diag_screen = lambda *_args, **_kwargs: None
    controller._diag_breadcrumb = lambda *_args, **_kwargs: None
    started = threading.Event()
    release = threading.Event()

    def fetch_full_details(data):
        started.set()
        assert release.wait(2)
        return {**data, "details": {}}

    displayed = []

    async def close_detail(_from_calendar):
        return None

    controller.orchestrator = SimpleNamespace(fetch_full_details=fetch_full_details)
    controller.view_manager = SimpleNamespace(
        show_detail_loading=lambda _data: None,
        show_detail_data=lambda data: displayed.append(data["id"]),
        show_detail_error=lambda data: displayed.append(f"error:{data['id']}"),
        close_detail=close_detail,
    )

    async def scenario():
        task = asyncio.create_task(
            controller._show_detail_async(
                {"id": "A", "url": "https://example.test/quiz/A"}
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        await controller._close_detail()
        release.set()
        await task

    asyncio.run(scenario())

    assert displayed == []


def test_loading_next_quiz_immediately_hides_previous_submission_controls():
    shell = SimpleNamespace(
        _view_generation=7,
        _title_text=SimpleNamespace(value="Old assignment"),
        _course_text=SimpleNamespace(value="Old course"),
        _current_url="https://example.test/assign/old",
        _current_data={},
        _content_col=SimpleNamespace(controls=[object()]),
        _loading_bar=SimpleNamespace(visible=False),
        _error_banner=SimpleNamespace(visible=True),
        _submission_area=SimpleNamespace(visible=True),
        _submitted_area=SimpleNamespace(visible=True),
        _open_btn=SimpleNamespace(visible=True),
        _header_open_btn=SimpleNamespace(visible=True),
        _badge_ctrl=SimpleNamespace(),
        _countdown_txt=SimpleNamespace(value="", color=None),
        _opentime_txt=SimpleNamespace(value=""),
        _opentime_row=SimpleNamespace(visible=True),
        _deadline_txt=SimpleNamespace(value=""),
        _cutoff_txt=SimpleNamespace(value=""),
        _cutoff_row=SimpleNamespace(visible=True),
        visible=False,
    )
    quiz = {
        "title": "New quiz",
        "course": "Course",
        "url": "https://example.test/quiz/new",
        "type": "quiz",
        "deadline": "",
        "details": {},
    }

    DetailView.show_loading(shell, quiz)

    assert shell._view_generation == 8
    assert shell._submission_area.visible is False
    assert shell._submitted_area.visible is False
    assert shell._open_btn.visible is False
    assert shell._header_open_btn.visible is False
    assert shell._error_banner.visible is False


def test_detail_view_update_detail_handles_none_time_limit_and_null_details():
    import flet as ft

    page = SimpleNamespace(
        width=900,
        height=760,
        overlay=[],
        update=lambda: None,
        run_task=lambda *_a, **_k: None,
    )
    view = DetailView(page, on_close=lambda: None)

    # Exact payload shape that previously crashed line 1410 with:
    # TypeError: 'in <string>' requires string as left operand, not NoneType
    untimed_quiz = {
        "id": "quiz-ch4",
        "title": "Bài tập tự học Chương 4 (Phần Dân chủ XHCN - CLO2.1 và CLO2.2)",
        "course": "[012000510704] - Chủ nghĩa xã hội khoa học - HTHN_LLCT",
        "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=55123",
        "type": "quiz",
        "urgency": "safe",
        "deadline": "2026-11-27 09:46:00",
        "submission_status": "submitted",
        "details": {
            "attempts_allowed": "Không giới hạn",
            "time_limit": None,
            "quiz_info": [
                "Số lần cho phép: Không giới hạn",
                "Phương thức tính điểm: Điểm cao nhất",
            ],
            "status_data": {"Trạng thái": "Đã hoàn thành"},
        },
    }
    view.update_detail(untimed_quiz)
    assert view._error_banner.visible is False
    assert any(
        isinstance(ctrl, ft.Container) for ctrl in view._content_col.controls
    )

    # Also handle details=None, string cutoff_date, and None attendance fields cleanly.
    view.show_loading(
        {
            "title": "Null details item",
            "course": "CS101",
            "url": "https://courses.ut.edu.vn/mod/assign/view.php?id=99",
            "type": "other",
            "deadline": "2026-12-01 12:00:00",
            "cutoff_date": "1796100000",
            "details": None,
        }
    )
    view.update_detail(
        {
            "title": "Attendance with nulls",
            "course": "CS101",
            "url": "https://courses.ut.edu.vn/mod/attendance/view.php?id=100",
            "type": "attendance",
            "deadline": None,
            "cutoff_date": "2026-12-02 12:00:00",
            "details": {
                "attempts_allowed": None,
                "time_limit": None,
                "quiz_info": None,
                "attendance_records": [
                    {"Description": None, "Status": None, "Points": None, "Ngày": None}
                ],
            },
        }
    )
    assert view._error_banner.visible is False


def test_orchestrator_skips_courses_ws_for_thnn_detail_urls():
    from core.data_orchestrator import DataOrchestrator

    orch = DataOrchestrator.__new__(DataOrchestrator)
    ws_calls = []
    orch.moodle_service = SimpleNamespace(
        get_assign_details_via_ws=lambda **kw: ws_calls.append(kw)
    )

    res = orch._fetch_detail_via_ws(
        {
            "url": "https://thnn.ut.edu.vn/mod/quiz/view.php?id=123",
            "course_id": 45,
            "type": "quiz",
            "site_identity": "thnn",
        }
    )
    assert res is None
    assert ws_calls == []
