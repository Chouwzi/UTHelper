import asyncio
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

import gui.app_controller as app_controller_module
from gui.app_controller import AppController


class _FakePage:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


def test_background_prefetch_uses_cache_by_default(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    controller.all_data = []
    controller._prefetch_cancel = False
    controller._is_loading = False
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._update_footer = lambda: None
    controller._render_cards = lambda: None
    controller._data_cache = SimpleNamespace(save_local_activities=lambda _items: True)

    calls = []

    class FakeOrchestrator:
        def get_cached_details_snapshot(self):
            return {}

        def prefetch_all_details(self, activities, workers, cancel_flag, force_refresh):
            calls.append((workers, force_refresh))
            return 0

    controller.orchestrator = FakeOrchestrator()

    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", False)
    asyncio.run(controller._prefetch_details_async([{"url": "https://example.com/1"}]))

    assert calls == [(4, False)]


def test_background_prefetch_caps_workers_on_mobile(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    controller.all_data = []
    controller._is_loading = False
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._update_footer = lambda: None
    controller._data_cache = SimpleNamespace(save_local_activities=lambda _items: True)
    calls = []

    class FakeOrchestrator:
        def get_cached_details_snapshot(self):
            return {}

        def prefetch_all_details(
            self, activities, workers, cancel_flag, force_refresh
        ):
            calls.append(workers)
            return 0

    controller.orchestrator = FakeOrchestrator()
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", True)

    asyncio.run(
        controller._prefetch_details_async([{"url": "https://example.com/1"}])
    )

    assert calls == [2]


def test_background_prefetch_persists_enriched_dataset_without_server_cursor(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    controller.all_data = [
        {
            "id": "assignment-1",
            "url": "https://example.com/1",
            "type": "assignment",
            "deadline": "2026-08-20 12:00:00",
            "submission_status": "unknown",
            "details": {},
        }
    ]
    controller._is_loading = False
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._update_footer = lambda: None
    persisted = []
    controller._data_cache = SimpleNamespace(
        save_local_activities=lambda items: persisted.append(items) or True
    )

    class FakeOrchestrator:
        def prefetch_all_details(self, *_args):
            return 1

        def get_cached_details_snapshot(self):
            return {
                "https://example.com/1": {
                    "submission_status": "Chưa nộp",
                    "details": {
                        "status_data": {"Trạng thái nộp bài": "Chưa nộp"}
                    },
                }
            }

    controller.orchestrator = FakeOrchestrator()
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", False)

    asyncio.run(controller._prefetch_details_async(list(controller.all_data)))

    assert controller.all_data[0]["submission_status"] == "not_submitted"
    assert persisted == [controller.all_data]


def test_cancelled_background_prefetch_neither_merges_nor_persists(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    original = {
        "id": "quiz-1",
        "url": "https://example.com/quiz/1",
        "type": "quiz",
        "deadline": "2026-08-20 12:00:00",
        "submission_status": "unknown",
        "details": {},
    }
    controller.all_data = [original]
    controller._is_loading = False
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._update_footer = lambda: None
    persisted = []
    controller._data_cache = SimpleNamespace(
        save_local_activities=lambda items: persisted.append(items) or True
    )

    class FakeOrchestrator:
        def prefetch_all_details(self, *_args):
            controller._prefetch_cancel_event.set()
            return 1

        def get_cached_details_snapshot(self):
            return {
                original["url"]: {
                    **original,
                    "submission_status": "not_submitted",
                }
            }

    controller.orchestrator = FakeOrchestrator()
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", False)

    asyncio.run(controller._prefetch_details_async([original]))

    assert controller.all_data == [original]
    assert persisted == []


def test_stale_background_prefetch_cannot_overwrite_newer_generation(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    current = {
        "id": "assignment-new",
        "url": "https://example.com/current",
        "type": "assignment",
        "deadline": "2026-08-21 12:00:00",
        "submission_status": "submitted",
        "details": {},
    }
    controller.all_data = [current]
    controller._is_loading = False
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._prefetch_epoch = 2
    controller._update_footer = lambda: None
    persisted = []
    controller._data_cache = SimpleNamespace(
        save_local_activities=lambda items: persisted.append(items) or True
    )

    class FakeOrchestrator:
        def prefetch_all_details(self, *_args):
            return 1

        def get_cached_details_snapshot(self):
            return {
                current["url"]: {
                    **current,
                    "submission_status": "not_submitted",
                }
            }

    controller.orchestrator = FakeOrchestrator()
    monkeypatch.setattr(app_controller_module.platform_utils, "IS_MOBILE", False)

    asyncio.run(controller._prefetch_details_async([current], prefetch_epoch=1))

    assert controller.all_data == [current]
    assert persisted == []
    assert controller.loading_bar.visible is True


def test_cached_unknown_statuses_are_hydrated_immediately(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.all_data = [
        {
            "url": "https://example.com/quiz/1",
            "submission_status": "unknown",
        }
    ]
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._prefetch_epoch = 0
    scheduled = []
    controller._safe_run_task = lambda *args: scheduled.append(args)
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "token")

    assert controller._schedule_cached_detail_hydration(True) is True
    assert controller._prefetch_epoch == 1
    assert scheduled[0][1] == controller.all_data
    assert scheduled[0][2] == 1


def test_cached_known_statuses_do_not_trigger_redundant_hydration(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.all_data = [
        {
            "url": "https://example.com/quiz/1",
            "submission_status": "submitted",
        }
    ]
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._prefetch_epoch = 0
    scheduled = []
    controller._safe_run_task = lambda *args: scheduled.append(args)
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "token")

    assert controller._schedule_cached_detail_hydration(True) is False
    assert scheduled == []


def test_cached_not_submitted_quiz_is_revalidated(monkeypatch):
    controller = AppController.__new__(AppController)
    stale_quiz = {
        "url": "https://example.com/mod/quiz/view.php?id=675077",
        "type": "quiz",
        "submission_status": "not_submitted",
    }
    stable_assignment = {
        "url": "https://example.com/mod/assign/view.php?id=1",
        "type": "assignment",
        "submission_status": "not_submitted",
    }
    controller.all_data = [stale_quiz, stable_assignment]
    controller._data_lock = threading.Lock()
    controller._prefetch_cancel_event = threading.Event()
    controller._prefetch_epoch = 0
    scheduled = []
    controller._safe_run_task = lambda *args: scheduled.append(args)
    monkeypatch.setattr(app_controller_module.settings, "MOODLE_WS_TOKEN", "token")

    assert controller._schedule_cached_detail_hydration(True) is True
    assert scheduled[0][1] == [stale_quiz]


def test_loading_a_nonempty_cached_snapshot_reports_success():
    controller = AppController.__new__(AppController)
    controller._data_cache = SimpleNamespace(
        load_snapshot=lambda: SimpleNamespace(
            activities=[
                {
                    "title": "Quiz",
                    "course": "Course",
                    "deadline": "2026-08-20 12:00:00",
                    "submission_status": "unknown",
                }
            ],
            last_successful_sync_at=None,
        )
    )
    controller._data_lock = threading.Lock()
    controller.all_data = []
    controller._refresh_activity_time_state = lambda: False
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=True)
    controller._update_footer = lambda: None
    controller.page = _FakePage()

    assert controller._load_cached_snapshot() is True
    assert controller.status_text.value == "Dữ liệu đã lưu · 1 hoạt động"
