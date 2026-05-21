import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from gui.app_controller import AppController


class _FakePage:
    def __init__(self):
        self.update_count = 0

    def update(self):
        self.update_count += 1


def test_background_prefetch_uses_cache_by_default():
    controller = AppController.__new__(AppController)
    controller.page = _FakePage()
    controller.status_text = SimpleNamespace(value="")
    controller.loading_bar = SimpleNamespace(visible=False)
    controller.all_data = []
    controller._prefetch_cancel = False
    controller._is_loading = False
    controller._update_footer = lambda: None
    controller._render_cards = lambda: None

    calls = []

    class FakeOrchestrator:
        def get_cached_details_snapshot(self):
            return {}

        def prefetch_all_details(self, activities, workers, cancel_flag, force_refresh):
            calls.append(force_refresh)
            return 0

    controller.orchestrator = FakeOrchestrator()

    asyncio.run(controller._prefetch_details_async([{"url": "https://example.com/1"}]))

    assert calls == [False]
