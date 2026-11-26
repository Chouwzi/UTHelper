"""TDD regression suite for pre-store Core/GUI/PC remediations.

Covers:
- SEC-PC-01: Cross-account & post-logout MOODLE_WS_TOKEN resurrection race
- WIN-PDF-12S: 12.0-second native Windows PDF loading stall on raw loopback items
- BUG-PC-03: Hidden CoursesTabView floating unsuspended native WebView2 dock
- BUG-PC-02: ensure_image_in_assets read-only FLET_ASSETS_DIR & cached image markdown
- UI-COPY-01 & SEC-PC-04: SettingsView system_desc platform copy, secret reset & cache globs
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flet as ft
import pytest

import config
from config import Settings, settings
from core.client import MoodleClient
from core.demo_sandbox import DEMO_USERNAME
from core.distribution import DistributionChannel, DistributionProfile
from gui import app_controller as ac_mod
from gui.app_controller import AppController
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.courses.embedded_webview_host import (
    EmbeddedWebViewHost,
    WindowsWebView2Dock,
)
from gui.components import settings_view as sv_mod
from gui.components.settings_view import SettingsView
from gui.core import utils as gui_utils


def _reviewer_demo_password() -> str:
    return "UTH-" + "Review-2026!"


# ---------------------------------------------------------------------------
# 1. SEC-PC-01: MOODLE_WS_TOKEN_USERNAME & in-flight _get_ws_token fencing
# ---------------------------------------------------------------------------


def test_sec_pc_01_config_exposes_non_secret_ws_token_username_field():
    s = Settings(
        UTH_USERNAME="2251120001",
        MOODLE_WS_TOKEN="secret_ws_tok",
        MOODLE_WS_TOKEN_ORIGIN="https://courses.ut.edu.vn",
        MOODLE_WS_TOKEN_USERNAME="2251120001",
    )
    dumped = s.model_dump()
    assert dumped["MOODLE_WS_TOKEN_USERNAME"] == "2251120001"
    assert "MOODLE_WS_TOKEN" not in dumped
    assert Settings().MOODLE_WS_TOKEN_USERNAME == ""


def test_sec_pc_01_in_flight_ws_token_aborted_on_concurrent_clear_session_logout(
    monkeypatch,
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "", raising=False)

    save_calls: list[bool] = []
    monkeypatch.setattr(config, "save_settings", lambda: save_calls.append(True) or True)

    client = MoodleClient("https://courses.ut.edu.vn")
    entered_post = threading.Event()
    release_post = threading.Event()

    def _slow_post(url, data, headers=None, timeout=15):
        entered_post.set()
        assert release_post.wait(timeout=2.0)
        return 200, {"token": "tok_A_resurrected"}

    monkeypatch.setattr(client, "_post", _slow_post)
    result_holder: list[str] = []

    worker = threading.Thread(
        target=lambda: result_holder.append(client._get_ws_token()),
        daemon=True,
    )
    worker.start()
    assert entered_post.wait(timeout=2.0)

    # Concurrent logout while _post(/login/token.php) is in flight
    settings.UTH_USERNAME = ""
    settings.UTH_PASSWORD = ""
    settings.UTH_CREDENTIALS_ORIGIN = ""
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""
    settings.MOODLE_WS_TOKEN_USERNAME = ""
    client.clear_session()

    release_post.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert result_holder == [""]
    assert save_calls == []
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""


@pytest.mark.parametrize("call_clear_session", [True, False])
def test_sec_pc_01_in_flight_ws_token_aborted_on_switch_student_a_to_student_b(
    monkeypatch, call_clear_session: bool
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "", raising=False)

    save_calls: list[bool] = []
    monkeypatch.setattr(config, "save_settings", lambda: save_calls.append(True) or True)

    client = MoodleClient("https://courses.ut.edu.vn")
    entered_post = threading.Event()
    release_post = threading.Event()

    def _slow_post(url, data, headers=None, timeout=15):
        entered_post.set()
        assert release_post.wait(timeout=2.0)
        return 200, {"token": "tok_A_stale"}

    monkeypatch.setattr(client, "_post", _slow_post)
    result_holder: list[str] = []

    worker = threading.Thread(
        target=lambda: result_holder.append(client._get_ws_token()),
        daemon=True,
    )
    worker.start()
    assert entered_post.wait(timeout=2.0)

    # Concurrent switch to Student B
    settings.UTH_USERNAME = "studentB"
    settings.UTH_PASSWORD = "passB"
    settings.MOODLE_WS_TOKEN = "tok_B_live"
    settings.MOODLE_WS_TOKEN_ORIGIN = "https://courses.ut.edu.vn"
    settings.MOODLE_WS_TOKEN_USERNAME = "studentB"
    if call_clear_session:
        client.clear_session()

    release_post.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert result_holder == [""]
    assert save_calls == []
    assert settings.UTH_USERNAME == "studentB"
    assert settings.MOODLE_WS_TOKEN == "tok_B_live"
    assert settings.MOODLE_WS_TOKEN_USERNAME == "studentB"


def test_sec_pc_01_in_flight_ws_token_aborted_on_switch_student_a_to_demo(monkeypatch):
    demo_pw = _reviewer_demo_password()
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "", raising=False)

    save_calls: list[bool] = []
    monkeypatch.setattr(config, "save_settings", lambda: save_calls.append(True) or True)

    client = MoodleClient("https://courses.ut.edu.vn")
    entered_post = threading.Event()
    release_post = threading.Event()

    def _slow_post(url, data, headers=None, timeout=15):
        entered_post.set()
        assert release_post.wait(timeout=2.0)
        return 200, {"token": "tok_A_into_demo"}

    monkeypatch.setattr(client, "_post", _slow_post)
    result_holder: list[str] = []

    worker = threading.Thread(
        target=lambda: result_holder.append(client._get_ws_token()),
        daemon=True,
    )
    worker.start()
    assert entered_post.wait(timeout=2.0)

    # Switch to Reviewer Demo while Student A token request is in flight
    settings.UTH_USERNAME = DEMO_USERNAME
    settings.UTH_PASSWORD = demo_pw
    settings.MOODLE_WS_TOKEN = ""
    settings.MOODLE_WS_TOKEN_ORIGIN = ""
    settings.MOODLE_WS_TOKEN_USERNAME = ""
    client.clear_session()

    release_post.set()
    worker.join(timeout=2.0)

    assert result_holder == [""]
    assert save_calls == []
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""

    # Demo _get_ws_token also clears stale MOODLE_WS_TOKEN_USERNAME when demo is active
    settings.MOODLE_WS_TOKEN = "stale_tok"
    settings.MOODLE_WS_TOKEN_ORIGIN = "https://courses.ut.edu.vn"
    settings.MOODLE_WS_TOKEN_USERNAME = "studentA"
    assert client._get_ws_token() == ""
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""


def test_sec_pc_01_legacy_upgrade_backfills_owner_and_blocks_cross_account_reuse(
    monkeypatch,
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120001")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass1")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "legacy_single_user_token")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "", raising=False)
    monkeypatch.setattr(config, "save_settings", lambda: True)

    client = MoodleClient("https://courses.ut.edu.vn")
    post_mock = Mock(return_value=(200, {"token": "fresh_user2_token"}))
    monkeypatch.setattr(client, "_post", post_mock)

    # 1. Benign single-user upgrade: MOODLE_WS_TOKEN_USERNAME == "" reuses token & backfills owner
    assert client.has_site_credentials is True
    assert client._get_ws_token() == "legacy_single_user_token"
    post_mock.assert_not_called()
    assert settings.MOODLE_WS_TOKEN_USERNAME == "2251120001"

    # 2. Case-insensitive owner match still reuses without hitting network
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "StudentA")
    monkeypatch.setattr(settings, "UTH_USERNAME", "  studenta  ")
    assert client._get_ws_token() == "legacy_single_user_token"
    post_mock.assert_not_called()

    # 3. Cross-user mismatch: MOODLE_WS_TOKEN_USERNAME == "StudentA" vs UTH_USERNAME == "2251120999"
    # MUST NOT reuse StudentA's token; fetches fresh token and stamps new owner!
    monkeypatch.setattr(settings, "UTH_USERNAME", "2251120999")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "pass999")
    assert client._get_ws_token() == "fresh_user2_token"
    post_mock.assert_called_once()
    assert settings.MOODLE_WS_TOKEN == "fresh_user2_token"
    assert settings.MOODLE_WS_TOKEN_USERNAME == "2251120999"


def test_sec_pc_01_app_controller_has_sync_credentials_and_logout_clear_token_owner(
    monkeypatch,
):
    monkeypatch.setattr(ac_mod, "save_settings", lambda: True)

    # Empty UTH_USERNAME with dangling MOODLE_WS_TOKEN is NEVER valid sync credentials
    monkeypatch.setattr(settings, "UTH_USERNAME", "")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "dangling_ws_token")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "", raising=False)
    assert AppController._has_sync_credentials() is False

    # Mismatched MOODLE_WS_TOKEN_USERNAME without password is rejected
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentB")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tokA")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "studentA", raising=False)
    assert AppController._has_sync_credentials() is False

    # Matching username + token is accepted
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "studentB", raising=False)
    assert AppController._has_sync_credentials() is True

    # Logout clears MOODLE_WS_TOKEN_USERNAME alongside MOODLE_WS_TOKEN
    ctrl = AppController.__new__(AppController)
    ctrl.page = SimpleNamespace(update=lambda: None, session=None)
    ctrl.orchestrator = SimpleNamespace(
        client=SimpleNamespace(clear_session=Mock()),
        thnn_client=SimpleNamespace(clear_session=Mock()),
        clear_caches=Mock(),
        clear_detail_cache=Mock(),
        moodle_service=SimpleNamespace(clear_all_caches=Mock()),
        is_logged_in=True,
    )
    ctrl._teardown_account_session = Mock()
    ctrl._safe_run_task = Mock()
    ctrl._show_snackbar = Mock()

    asyncio.run(ctrl._handle_logout_async())
    assert settings.UTH_USERNAME == ""
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""


# ---------------------------------------------------------------------------
# 2. WIN-PDF-12S: Native Windows PDF raw loopback item reveals without 12s stall
# ---------------------------------------------------------------------------


def test_win_pdf_12s_raw_loopback_pdf_marks_paint_ready_immediately_preserving_lock():
    from gui.components.courses import embedded_webview_host as host_mod

    expect_fn = getattr(host_mod, "_expects_viewer_ready_message", None)
    assert callable(expect_fn), "_expects_viewer_ready_message must exist"

    raw_pdf_url = "http://127.0.0.1:45678/0123456789abcdef?token=secret_tok"
    office_url = "http://127.0.0.1:45678/viewer/tok/docx_host.html"
    pdfjs_url = "http://127.0.0.1:45678/pdf-viewer/tok/pdf_host.html"

    assert expect_fn(raw_pdf_url) is False
    assert expect_fn(office_url) is True
    assert expect_fn(pdfjs_url) is True

    dock = WindowsWebView2Dock(page_title="UTHelper", url=raw_pdf_url)
    host = EmbeddedWebViewHost(backend="webview2", url=raw_pdf_url, dock=dock)
    callbacks: dict[str, object] = {}

    class _Core:
        can_go_back = False
        can_go_forward = False
        source = raw_pdf_url

        def add_document_title_changed(self, fn):
            callbacks["title"] = fn

        def add_source_changed(self, fn):
            callbacks["source"] = fn

        def add_navigation_starting(self, fn):
            callbacks["nav_start"] = fn

        def add_navigation_completed(self, fn):
            callbacks["nav_done"] = fn

        def add_history_changed(self, fn):
            callbacks["history"] = fn

        def add_web_message_received(self, fn):
            callbacks["web_msg"] = fn

        def add_new_window_requested(self, fn):
            callbacks["new_win"] = fn

    core = _Core()
    dock._core = core
    dock._bind_events(core)

    # Navigation security lock MUST stay intact on raw /<16-hex>
    assert dock._is_viewer_locked() is True
    assert dock._is_navigation_uri_allowed(raw_pdf_url) is True
    assert dock._is_navigation_uri_allowed("http://127.0.0.1:45678/fedcba9876543210") is False
    assert dock._is_navigation_uri_allowed("https://evil.example/") is False

    # NavigationCompleted on raw /<16-hex> PDF MUST mark paint_ready immediately (no 12s stall!)
    callbacks["nav_done"](core, SimpleNamespace(is_success=True))  # type: ignore[operator]
    assert dock._paint_ready is True
    assert dock._paint_deadline == 0.0
    host._handle_webview2_loading(False)
    assert host._loading_ring is not None and host._loading_ring.visible is False


# ---------------------------------------------------------------------------
# 3. BUG-PC-03: Hidden CoursesTabView overlay suspend/resume & bounds guards
# ---------------------------------------------------------------------------


class _FakeNativeOverlay(ft.Container):
    def __init__(self, name: str) -> None:
        super().__init__(expand=True)
        self.name = name
        self.events: list[str] = []
        self.suspended: bool = False

    def suspend(self) -> None:
        self.suspended = True
        self.events.append("suspend")

    def resume(self) -> None:
        self.suspended = False
        self.events.append("resume")

    def sync_bounds(self, *_args, **_kwargs) -> None:
        self.events.append("sync_bounds")

    def dispose(self) -> None:
        self.events.append("dispose")


def test_bug_pc_03_hidden_courses_tab_suspends_overlays_and_resumes_when_shown():
    page = SimpleNamespace(
        width=1200,
        height=800,
        web=False,
        overlay=[],
        update=lambda: None,
    )
    view = CoursesTabView(page=page)  # type: ignore[arg-type]
    view.visible = True

    ov1 = _FakeNativeOverlay("ov1")
    view.push_overlay(ov1, tag="reader:1")
    assert ov1.suspended is False
    assert "sync_bounds" in ov1.events

    # User switches to another tab (e.g. Lịch / Tổng quan)
    view.on_tab_hidden()
    view.visible = False
    assert ov1.suspended is True
    ov1.events.clear()

    # Async attachment finishes mid-background and pushes ov2 while CoursesTabView is hidden
    ov2 = _FakeNativeOverlay("ov2")
    view.push_overlay(ov2, tag="reader:2")
    assert ov2.suspended is True
    assert "suspend" in ov2.events
    assert "sync_bounds" not in ov2.events

    # Popping ov2 while hidden MUST NOT resume ov1 or sync native HWND bounds over other tabs
    assert view.pop_overlay() is True
    assert ov1.suspended is True
    assert "resume" not in ov1.events
    assert "sync_bounds" not in ov1.events

    # Pushing loading overlay ov3 and dismissing via _dismiss_overlay_instance while hidden
    # MUST NOT resume ov1 either!
    ov3 = _FakeNativeOverlay("loading")
    view.push_overlay(ov3, tag="viewer:load:1")
    assert ov3.suspended is True
    view._dismiss_overlay_instance(ov3)
    assert ov1.suspended is True
    assert "resume" not in ov1.events
    assert "sync_bounds" not in ov1.events

    # Returning to Môn học tab MUST resume top overlay ov1 and sync its bounds!
    view.visible = True
    view.on_tab_shown()
    assert ov1.suspended is False
    assert "resume" in ov1.events
    assert "sync_bounds" in ov1.events


# ---------------------------------------------------------------------------
# 4. BUG-PC-02: ensure_image_in_assets packaged guard & cached image markdown
# ---------------------------------------------------------------------------


def test_bug_pc_02_ensure_image_in_assets_skips_packaged_flet_assets_dir_writes(
    tmp_path: Path, monkeypatch
):
    user_data = tmp_path / "appdata" / "UTHelper"
    cache_dir = user_data / "cache" / "images"
    cache_dir.mkdir(parents=True)
    digest = "ab" * 32
    cached_img = cache_dir / f"{digest}.png"
    cached_img.write_bytes(b"\x89PNG\r\n\x1a\ncontent")

    readonly_assets = tmp_path / "WindowsApps" / "UTHelper" / "data" / "flutter_assets"
    monkeypatch.setenv("FLET_ASSETS_DIR", str(readonly_assets))
    monkeypatch.setattr(config, "_USER_DATA_DIR", user_data)

    copy_spy = Mock(wraps=shutil.copy2)
    monkeypatch.setattr(gui_utils.shutil, "copy2", copy_spy)

    result = gui_utils.ensure_image_in_assets(cached_img)
    assert result == str(cached_img.resolve())
    assert not readonly_assets.exists()
    copy_spy.assert_not_called()


def test_bug_pc_02_sanitize_markdown_url_accepts_canonical_cached_image_only(
    tmp_path: Path, monkeypatch
):
    user_data = tmp_path / "user store (vn)" / "UTHelper"
    cache_dir = user_data / "cache" / "images"
    cache_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "_USER_DATA_DIR", user_data)

    digest = "cd" * 32
    valid_img = cache_dir / f"{digest}.png"
    valid_img.write_bytes(b"\x89PNG\r\n\x1a\n")
    raw_abs = str(valid_img.resolve())

    # 1. Trusted image markdown (allow_cached_image=True) accepts canonical cached file
    sanitized = gui_utils._sanitize_markdown_url(raw_abs, allow_cached_image=True)
    assert sanitized is not None
    assert Path(sanitized).resolve() == valid_img.resolve()

    # html_to_markdown renders image syntax instead of stripping to alt text
    md = gui_utils.html_to_markdown(f'<p>Intro</p><img src="{raw_abs}" alt="Chart">')
    assert "![Chart](" in md

    # 2. Untrusted link / non-image context (allow_cached_image=False) MUST reject local cached paths
    assert gui_utils._sanitize_markdown_url(raw_abs, allow_cached_image=False) is None
    assert (
        gui_utils._sanitize_markdown_url(
            f"cache/images/{digest}.png", allow_cached_image=False
        )
        is None
    )
    link_md = gui_utils.html_to_markdown(f'<a href="{raw_abs}">LocalLeak</a>')
    assert link_md == "LocalLeak"

    # 3. Path traversal, .. bounce, UNC paths, arbitrary local files, bad filenames -> ALL rejected
    outside_same_name = tmp_path / f"{digest}.png"
    outside_same_name.write_bytes(b"secret")
    bad_name_inside = cache_dir / "win.ini"
    bad_name_inside.write_text("[fonts]", encoding="utf-8")

    malicious_inputs = [
        r"..\..\Windows\win.ini",
        "../../etc/passwd",
        str(cache_dir / ".." / ".." / f"{digest}.png"),
        str(cache_dir / ".." / "images" / f"{digest}.png"),
        rf"\\attacker\share\{digest}.png",
        f"//attacker/share/{digest}.png",
        str(outside_same_name),
        str(bad_name_inside),
    ]
    for payload in malicious_inputs:
        assert (
            gui_utils._sanitize_markdown_url(payload, allow_cached_image=True) is None
        ), f"Payload unexpectedly allowed: {payload}"

    # 4. Symlink / reparse escape inside cache/images pointing outside MUST be rejected
    symlink_img = cache_dir / f"{'ef' * 32}.png"
    created_real_symlink = False
    try:
        symlink_img.symlink_to(outside_same_name)
        created_real_symlink = True
    except OSError:
        symlink_img.write_bytes(b"placeholder")

    if created_real_symlink:
        assert (
            gui_utils._sanitize_markdown_url(str(symlink_img), allow_cached_image=True)
            is None
        )
    else:
        monkeypatch.setattr(
            gui_utils,
            "_is_reparse_point_or_symlink",
            lambda p: Path(p) == symlink_img,
        )
        assert (
            gui_utils._sanitize_markdown_url(str(symlink_img), allow_cached_image=True)
            is None
        )


# ---------------------------------------------------------------------------
# 5. UI-COPY-01 & SEC-PC-04: SettingsView system_desc, secret reset & cache globs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plat", "channel", "self_update", "expected_desc"),
    [
        (
            "win32",
            DistributionChannel.EXE_LTS,
            True,
            "Khởi động cùng Windows và cập nhật",
        ),
        (
            "linux",
            DistributionChannel.APK_LTS,
            True,
            "Cấu hình hệ thống, đồng bộ nền và cập nhật",
        ),
        (
            "win32",
            DistributionChannel.MSSTORE,
            False,
            "Cấu hình hệ thống và đồng bộ dữ liệu",
        ),
        (
            "linux",
            DistributionChannel.CHPLAY,
            False,
            "Cấu hình hệ thống và đồng bộ dữ liệu",
        ),
    ],
)
def test_ui_copy_01_settings_system_desc_matches_platform_and_channel(
    monkeypatch,
    plat: str,
    channel: DistributionChannel,
    self_update: bool,
    expected_desc: str,
):
    monkeypatch.setattr(sv_mod.sys, "platform", plat)
    view = SettingsView(
        page=SimpleNamespace(
            overlay=[],
            update=lambda: None,
            run_task=lambda *_a, **_kw: None,
        ),
        orchestrator=SimpleNamespace(),
        on_close=lambda: None,
        autostart_coordinator=SimpleNamespace(),
        distribution_profile=DistributionProfile(
            channel=channel,
            supports_github_self_update=self_update,
        ),
    )
    sys_entry = next(item for item in view._category_defs if item[0] == "system")
    assert sys_entry[4] == expected_desc
    if plat != "win32":
        assert "Windows" not in sys_entry[4]


def test_sec_pc_04_reset_settings_clears_secrets_and_cache_actions_cover_namespaced_files(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)

    class _FakeSecretBackend:
        def __init__(self) -> None:
            self.store = {
                "password": "persisted_password",
                "moodle_session": "persisted_sess",
                "ws_token": "persisted_ws_tok",
                "ws_token_origin": "https://courses.ut.edu.vn",
                "gmail_app_password": "app_pw",
                "discord_webhook": "https://discord.com/api/webhooks/1/secret",
                "telegram_bot_token": "bot_tok",
            }

        def read_secret(self, key: str) -> str:
            return self.store.get(key, "")

        def write_secret(self, key: str, value: str) -> bool:
            if value:
                self.store[key] = value
            else:
                self.store.pop(key, None)
            return True

        def snapshot_secrets(self, keys):
            return {k: self.store.get(k, "") for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            self.store = {k: v for k, v in snapshot.items() if v}
            return True

        def write_all(self, values) -> bool:
            for k, v in values.items():
                self.write_secret(k, v)
            return True

    backend = _FakeSecretBackend()
    monkeypatch.setattr(config, "_secret_backend", backend)
    monkeypatch.setattr(config, "_secret_backend_factory", None)
    (tmp_path / "settings.json").write_text(
        '{"UTH_USERNAME": "2251120001", "MOODLE_WS_TOKEN_USERNAME": "2251120001"}',
        encoding="utf-8",
    )
    for secret_attr in config._SECRET_FIELDS:
        monkeypatch.setattr(settings, secret_attr, getattr(settings, secret_attr, ""))
    monkeypatch.setattr(
        settings, "UTH_CREDENTIALS_ORIGIN", settings.UTH_CREDENTIALS_ORIGIN
    )
    monkeypatch.setattr(settings, "UTH_PASSWORD", "persisted_password")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "persisted_ws_tok")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "2251120001", raising=False)

    # Namespaced cache files created by multi-account NotificationManager & DataCache
    notif_ns = tmp_path / "notifications_cache_a1b2c3d4.json"
    act_ns = tmp_path / "activities_cache_e5f60718.json"
    notif_ns.write_text('{"evt_1": [1440]}', encoding="utf-8")
    act_ns.write_text('{"activities": [{"id": "1"}, {"id": "2"}]}', encoding="utf-8")

    tasks: list = []
    page = SimpleNamespace(
        overlay=[],
        update=lambda: None,
        run_task=lambda fn, *a, **kw: tasks.append((fn, a, kw)),
    )
    view = SimpleNamespace(
        _page=page,
        _orchestrator=SimpleNamespace(get_cached_details_snapshot=lambda: {}),
        _debug_cache_stats=ft.Text(),
        _debug_info_text=ft.Text(),
    )

    # 1. Cache stats must see namespaced notifications_cache_*.json and activities_cache_*.json
    SettingsView._do_show_cache_stats(view)
    stats_val = view._debug_cache_stats.value or ""
    assert "Cache thông báo: trống" not in stats_val
    assert "Cache offline: trống" not in stats_val

    # 2. Clear notif cache & data cache must delete namespaced files
    SettingsView._do_clear_notif_cache(view)
    assert not notif_ns.exists()
    SettingsView._do_clear_data_cache(view)
    assert not act_ns.exists()

    # 3. Reset settings must wipe BOTH settings.json AND persisted secrets in backend
    SettingsView._do_reset_settings(view)
    assert len(tasks) == 1
    coro_fn, args, kwargs = tasks[0]

    async def _drive_confirm() -> None:
        t = asyncio.create_task(coro_fn(*args, **kwargs))
        await asyncio.sleep(0.02)
        assert page.overlay, "Confirmation dialog should be mounted"
        dlg = page.overlay[-1]
        reset_btn = next(btn for btn in dlg.actions if getattr(btn, "content", None) == "Reset" or getattr(btn, "text", None) == "Reset")
        reset_btn.on_click(None)
        await t

    asyncio.run(_drive_confirm())

    assert not (tmp_path / "settings.json").exists()
    assert backend.store == {}
    reloaded = config.load_settings()
    assert reloaded.UTH_USERNAME == ""
    assert reloaded.UTH_PASSWORD == ""
    assert reloaded.MOODLE_WS_TOKEN == ""
    assert reloaded.DISCORD_WEBHOOK_URL == ""
    assert reloaded.TELEGRAM_BOT_TOKEN == ""


def test_sec_pc_01_legacy_upgrade_account_switch_never_reuses_unstamped_prior_token(
    tmp_path: Path, monkeypatch
):
    # 1a. load_settings() backfills MOODLE_WS_TOKEN_USERNAME on legacy JSON without that key
    monkeypatch.setattr(config, "_USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "_HAS_KEYRING", False)

    class _ReadOnlyVault:
        def read_secret(self, key: str) -> str:
            return {
                "ws_token": "tok_studentA_secret",
                "ws_token_origin": "https://courses.ut.edu.vn",
            }.get(key, "")

        def write_secret(self, key: str, value: str) -> bool:
            return True

        def snapshot_secrets(self, keys):
            return {k: self.read_secret(k) for k in keys}

        def restore_secrets(self, snapshot) -> bool:
            return True

        def write_all(self, values) -> bool:
            return True

    monkeypatch.setattr(config, "_secret_backend", _ReadOnlyVault())
    monkeypatch.setattr(config, "_secret_backend_factory", None)
    (tmp_path / "settings.json").write_text(
        '{"UTH_USERNAME": "studentA", "UTH_CREDENTIALS_ORIGIN": "https://courses.ut.edu.vn"}',
        encoding="utf-8",
    )
    loaded = config.load_settings()
    assert loaded.MOODLE_WS_TOKEN == "tok_studentA_secret"
    assert loaded.MOODLE_WS_TOKEN_USERNAME == "studentA"

    # 1b. AppController._teardown_account_session wipes unstamped legacy token on switch
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentB")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passB")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tok_studentA_secret")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "")

    ctrl = AppController.__new__(AppController)
    ctrl.page = SimpleNamespace(update=lambda: None, session=None)
    ctrl._safe_run_task = Mock()
    ctrl._teardown_detail_view_for_account_switch = Mock()
    ctrl._is_macos_native_runtime = lambda: False
    monkeypatch.setattr(
        ac_mod,
        "_build_account_data_cache",
        lambda _u: SimpleNamespace(),
    )
    monkeypatch.setattr(ac_mod.sys, "platform", "linux")

    ctrl._teardown_account_session(
        ("studentB", "passB"),
        reset_moodle_and_caches=True,
        account_switched_or_logged_out=True,
        schedule_refresh_trigger="login",
    )
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""

    # 1c. SettingsView._handle_test_login fallback (on_account_login=None) wipes unstamped/prior token
    monkeypatch.setattr(sv_mod, "save_settings", lambda: True)
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tok_studentA_secret")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "")

    orch = SimpleNamespace(
        client=SimpleNamespace(
            login=lambda username=None, password=None, force=False, persist=True: True
        ),
        is_logged_in=True,
        get_cached_details_snapshot=lambda: {},
    )
    sview = SettingsView(
        page=SimpleNamespace(
            overlay=[],
            update=lambda: None,
            run_task=lambda *_a, **_kw: None,
        ),
        orchestrator=orch,
        on_close=lambda: None,
        autostart_coordinator=None,
    )
    sview.update = Mock()
    sview._username_field.value = "studentB"
    sview._password_field.value = "passB"
    asyncio.run(sview._handle_test_login(None))

    assert settings.UTH_USERNAME == "studentB"
    assert settings.MOODLE_WS_TOKEN == ""
    assert settings.MOODLE_WS_TOKEN_ORIGIN == ""
    assert settings.MOODLE_WS_TOKEN_USERNAME == ""
    assert orch.is_logged_in is False


@pytest.mark.parametrize("call_clear_session", [True, False])
def test_sec_pc_01_call_ws_api_invalidtoken_never_retries_cross_account_request(
    monkeypatch, call_clear_session: bool
):
    monkeypatch.setattr(settings, "MOODLE_BASE_URL", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "UTH_USERNAME", "studentA")
    monkeypatch.setattr(settings, "UTH_PASSWORD", "passA")
    monkeypatch.setattr(settings, "UTH_CREDENTIALS_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN", "tok_A_expired")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_ORIGIN", "https://courses.ut.edu.vn")
    monkeypatch.setattr(settings, "MOODLE_WS_TOKEN_USERNAME", "studentA")
    monkeypatch.setattr(config, "save_settings", lambda: True)

    client = MoodleClient("https://courses.ut.edu.vn")
    entered_server = threading.Event()
    release_server = threading.Event()
    post_calls: list[tuple[str, dict]] = []

    def _fake_post(url, data, headers=None, timeout=20):
        post_calls.append((url, dict(data)))
        if len(post_calls) == 1:
            entered_server.set()
            assert release_server.wait(timeout=2.0)
            return 200, {
                "exception": "moodle_exception",
                "errorcode": "invalidtoken",
                "message": "Invalid token - token not found",
            }
        return 200, {"token": "minted_studentB_token", "ok": True}

    monkeypatch.setattr(client, "_post", _fake_post)
    results: list = []

    worker = threading.Thread(
        target=lambda: results.append(
            client.call_ws_api("mod_assign_save_submission", assignmentid=999)
        ),
        daemon=True,
    )
    worker.start()
    assert entered_server.wait(timeout=2.0)

    # User switches to studentB while studentA's WS mutation is in flight
    settings.UTH_USERNAME = "studentB"
    settings.UTH_PASSWORD = "passB"
    settings.UTH_CREDENTIALS_ORIGIN = "https://courses.ut.edu.vn"
    settings.MOODLE_WS_TOKEN = "tok_B_valid"
    settings.MOODLE_WS_TOKEN_ORIGIN = "https://courses.ut.edu.vn"
    settings.MOODLE_WS_TOKEN_USERNAME = "studentB"
    if call_clear_session:
        client.clear_session()

    release_server.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()

    assert results == [None]
    assert len(post_calls) == 1
    assert post_calls[0][1]["wstoken"] == "tok_A_expired"
    assert settings.MOODLE_WS_TOKEN == "tok_B_valid"
    assert settings.MOODLE_WS_TOKEN_USERNAME == "studentB"

