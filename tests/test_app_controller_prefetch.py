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
