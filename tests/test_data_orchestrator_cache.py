import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.data_orchestrator import DataOrchestrator
from config import settings
from contextlib import contextmanager


@contextmanager
def _scraping_mode():
    """Tạm tắt WS API và bật session login để test HTML scraping path."""
    orig_ws = settings.USE_WS_API
    orig_session = settings.ALLOW_SESSION_LOGIN
    settings.USE_WS_API = False
    settings.ALLOW_SESSION_LOGIN = True
    try:
        yield
    finally:
        settings.USE_WS_API = orig_ws
        settings.ALLOW_SESSION_LOGIN = orig_session


def _make_orchestrator() -> DataOrchestrator:
    orch = DataOrchestrator()
    orch.is_logged_in = True
    return orch


def _activity(url: str) -> dict:
    return {
        "url": url,
        "title": "Cached activity",
        "deadline": "2099-12-31T23:59:59",
        "type": "assignment",
    }


def _html(title: str = "Cached activity") -> str:
    return f"<html><body><h3 class='h2'>{title}</h3><div id='intro'><p>desc</p></div></body></html>"


def test_fetch_full_details_reuses_fresh_cache():
    with _scraping_mode():
        orchestrator = _make_orchestrator()
        calls = []
        orchestrator.client.fetch_url = lambda url: calls.append(url) or _html()

        first = orchestrator.fetch_full_details(_activity("https://example.com/mod/assign/view.php?id=1"))
        second = orchestrator.fetch_full_details(_activity("https://example.com/mod/assign/view.php?id=1"))

        assert first == second
        assert calls == ["https://example.com/mod/assign/view.php?id=1"]


def test_fetch_full_details_refreshes_expired_cache():
    with _scraping_mode():
        orchestrator = _make_orchestrator()
        orchestrator._detail_cache_ttl_seconds = 0.01
        calls = []
        orchestrator.client.fetch_url = lambda url: calls.append(url) or _html(f"Activity {len(calls)}")

        first = orchestrator.fetch_full_details(_activity("https://example.com/mod/assign/view.php?id=2"))
        time.sleep(0.02)
        second = orchestrator.fetch_full_details(_activity("https://example.com/mod/assign/view.php?id=2"))

        assert first["title"] != second["title"]
        assert calls == [
            "https://example.com/mod/assign/view.php?id=2",
            "https://example.com/mod/assign/view.php?id=2",
        ]


def test_detail_cache_evicts_oldest_entry_when_full():
    with _scraping_mode():
        orchestrator = _make_orchestrator()
        orchestrator._detail_cache_max_entries = 2
        orchestrator.client.fetch_url = lambda url: _html(url.rsplit("=", 1)[-1])

        urls = [f"https://example.com/mod/assign/view.php?id={i}" for i in range(3)]
        for url in urls:
            orchestrator.fetch_full_details(_activity(url))

        assert urls[0] not in orchestrator._detail_cache
        assert urls[1] in orchestrator._detail_cache
        assert urls[2] in orchestrator._detail_cache
