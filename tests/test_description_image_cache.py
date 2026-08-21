import asyncio
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import config
from gui.app_controller import AppController
from gui.core import utils


IMAGE_URL = "https://courses.ut.edu.vn/pluginfile.php/42/mod_assign/intro/picture.png"
IMAGE_HTML = f'<p>Before</p><img src="{IMAGE_URL}" alt="sample"><p>After</p>'


class _Headers:
    def __init__(self, content_type):
        self._content_type = content_type

    def get_content_type(self):
        return self._content_type


class _ChunkedResponse:
    def __init__(self, payload=b"png-data", *, content_type="image/png", fail_after=None):
        self.status = 200
        self.headers = _Headers(content_type)
        self._payload = payload
        self._offset = 0
        self._fail_after = fail_after
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        if self._fail_after is not None and self._offset >= self._fail_after:
            raise OSError("connection lost")
        if size is None or size < 0:
            return self._payload[self._offset :]
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _GeneratedResponse(_ChunkedResponse):
    def __init__(self, byte_count):
        super().__init__(b"")
        self._remaining = byte_count

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            raise AssertionError("image response must be read with a bounded chunk size")
        count = min(size, self._remaining)
        self._remaining -= count
        return b"x" * count


@pytest.fixture
def image_cache_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(utils, "cleanup_image_cache", lambda: None)
    monkeypatch.setattr(
        utils,
        "ensure_image_in_assets",
        lambda path: f"cache/images/{path.name}",
    )
    return tmp_path / "cache" / "images"


def test_description_image_download_streams_bounded_chunks_and_publishes_complete_file(
    image_cache_env, monkeypatch
):
    response = _ChunkedResponse(b"a" * 150_000)
    monkeypatch.setattr(utils.urllib.request, "urlopen", lambda *_a, **_k: response)

    rewritten = utils.pre_cache_description_images(IMAGE_HTML)

    cached_files = list(image_cache_env.glob("*"))
    assert len(cached_files) == 1
    assert cached_files[0].read_bytes() == b"a" * 150_000
    assert all(0 < size <= 64 * 1024 for size in response.read_sizes)
    assert "cache/images/" in rewritten
    assert IMAGE_URL not in rewritten


def test_description_image_over_limit_keeps_online_fallback_and_no_partial_file(
    image_cache_env, monkeypatch
):
    response = _GeneratedResponse(8 * 1024 * 1024 + 1)
    monkeypatch.setattr(utils.urllib.request, "urlopen", lambda *_a, **_k: response)

    rewritten = utils.pre_cache_description_images(IMAGE_HTML)

    assert "webservice/pluginfile.php" in rewritten
    assert list(image_cache_env.glob("*")) == []
    assert all(0 < size <= 64 * 1024 for size in response.read_sizes)


def test_description_image_rejects_non_image_content_without_reading_body(
    image_cache_env, monkeypatch
):
    response = _ChunkedResponse(b"<html>login</html>", content_type="text/html")
    monkeypatch.setattr(utils.urllib.request, "urlopen", lambda *_a, **_k: response)

    rewritten = utils.pre_cache_description_images(IMAGE_HTML)

    assert "webservice/pluginfile.php" in rewritten
    assert response.read_sizes == []
    assert list(image_cache_env.glob("*")) == []


def test_description_image_download_failure_removes_temporary_file(
    image_cache_env, monkeypatch
):
    response = _ChunkedResponse(b"a" * 100_000, fail_after=64 * 1024)
    monkeypatch.setattr(utils.urllib.request, "urlopen", lambda *_a, **_k: response)

    rewritten = utils.pre_cache_description_images(IMAGE_HTML)

    assert "webservice/pluginfile.php" in rewritten
    assert list(image_cache_env.glob("*")) == []


def test_show_detail_keeps_event_loop_running_while_images_are_cached(monkeypatch):
    controller = AppController.__new__(AppController)
    controller.calendar_view = SimpleNamespace(visible=False)
    controller._diag_screen = lambda *_a, **_k: None
    controller._diag_breadcrumb = lambda *_a, **_k: None
    controller.orchestrator = SimpleNamespace(
        fetch_full_details=lambda _data: {
            "details": {"description_html": IMAGE_HTML}
        }
    )
    controller.view_manager = SimpleNamespace(
        show_detail_loading=lambda _data: None,
        show_detail_data=lambda _data: None,
        show_detail_error=lambda _data: pytest.fail("detail unexpectedly failed"),
    )
    cache_started = threading.Event()
    cache_finished = threading.Event()

    def slow_cache(html):
        cache_started.set()
        time.sleep(0.15)
        cache_finished.set()
        return html

    monkeypatch.setattr(utils, "pre_cache_description_images", slow_cache)
    ticks = []

    async def heartbeat():
        assert await asyncio.to_thread(cache_started.wait, 1)
        while not cache_finished.is_set():
            ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(0.01)

    async def scenario():
        await asyncio.gather(
            controller._show_detail_async({"id": "1"}),
            heartbeat(),
        )

    asyncio.run(scenario())
    assert len(ticks) >= 5
