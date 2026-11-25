"""Cross-slice TDD integration tests for PKG-01, D-05, and D-06."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import platform_utils
import tests.test_architecture_boundaries as arch_boundaries
from core.update_coordinator import UpdateCoordinator, UpdateEvent, UpdateEventKind
from core.update_manifest import parse_manifest, select_candidate
from core.update_models import (
    ReleaseManifest,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
)
from gui.app_controller import AppController
from gui.components.courses.embedded_webview_host import (
    clear_global_webview_occlusion,
    is_globally_occluded,
)
from platform_utils.android_updates import (
    PACKAGE_ID,
    AndroidPackageDownloader,
    AndroidPackageLauncher,
    AndroidPackageVerifier,
)


def _run_task(handler, *args):
    future: Future = Future()
    try:
        future.set_result(asyncio.run(handler(*args)))
    except Exception as exc:
        future.set_exception(exc)
    return future


def _mismatched_schema3_android_manifest(
    *,
    version: str = "2.5.1",
    signer_identity: str = "com.uthelper.uthelper",
) -> dict:
    return {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-11-25T00:00:00Z",
        "release_notes_url": (
            f"https://github.com/Chouwzi/UTHelper/releases/tag/v{version}"
        ),
        "packages": [
            {
                "platform": "android",
                "architecture": "arm64",
                "package_type": "apk",
                "install_channel": "sideload",
                "url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    f"v{version}/UTHelper-{version}.apk"
                ),
                "sha256": "a" * 64,
                "size": 52_428_800,
                "signer_identity": signer_identity,
                "certificate_fingerprint": "B" * 64,
                "signature_kind": "apk-pinned",
                "install_strategy": {"kind": "android_package_installer"},
            }
        ],
    }


def test_pkg01_cross_slice_android_signer_mismatch_routes_to_manual_release_page_without_download(
    monkeypatch,
):
    """End-to-end cross-slice test: update_manifest -> UpdateCoordinator -> AndroidPackageDownloader -> AppController."""
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)

    manifest = parse_manifest(
        _mismatched_schema3_android_manifest(signer_identity="com.uthelper.uthelper"),
        expected_release_version="2.5.1",
    )
    target = RuntimeTarget("android", "arm64", "sideload")
    candidate = select_candidate(
        manifest,
        current_version="2.5.0",
        target=target,
        expected_signer_identity=PACKAGE_ID,
    )
    assert candidate is not None
    assert candidate.automatic_install_allowed is False

    bridge_calls: list[str] = []

    class _ExplodingNativeBridge:
        available = True

        async def start_update_download(self, *_args):
            bridge_calls.append("start_update_download")
            raise AssertionError("Must not start native APK download on signer mismatch")

        async def get_update_download_status(self, *_args):
            bridge_calls.append("get_update_download_status")
            return {"status": "pending"}

        async def install_update(self, *_args):
            bridge_calls.append("install_update")
            raise AssertionError("Must not invoke native APK installer on signer mismatch")

    bridge = _ExplodingNativeBridge()
    downloader = AndroidPackageDownloader(bridge, _run_task, lambda: "2.5.1")
    verifier = AndroidPackageVerifier()
    launcher = AndroidPackageLauncher(bridge, _run_task, lambda: "2.5.1")

    shown_dialogs: list[object] = []
    opened_urls: list[str] = []

    controller = AppController.__new__(AppController)
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_manual_download = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._update_banner = SimpleNamespace(visible=False)
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(
        visible=True, disabled=False, content="Cập nhật", icon=None
    )
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(_check_update_btn=None)
    controller.page = SimpleNamespace(
        update=lambda: None,
        show_dialog=lambda dlg: shown_dialogs.append(dlg),
        pop_dialog=lambda: None,
    )
    controller._diag_breadcrumb = lambda *_a, **_kw: None
    controller._open_external_update_url = lambda url: opened_urls.append(url) or True
    controller._safe_run_task = lambda coro, *a, **kw: asyncio.run(coro(*a, **kw))

    available_ev = threading.Event()
    manual_ev = threading.Event()
    launched_ev = threading.Event()

    def sink(ev: UpdateEvent):
        controller._on_update_event(ev)
        if ev.kind is UpdateEventKind.UPDATE_AVAILABLE:
            available_ev.set()
        elif ev.kind is UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED:
            manual_ev.set()
        elif ev.kind is UpdateEventKind.INSTALL_LAUNCHED:
            launched_ev.set()

    class _FakeClient:
        def fetch_candidate(self, _v, _t, *, cancel=None):
            del cancel
            return candidate

    clear_global_webview_occlusion()
    coordinator = UpdateCoordinator(
        _FakeClient(),
        downloader,
        verifier,
        launcher,
        target,
        "2.5.0",
        sink,
        automatic_enabled=False,
        external_opener=controller._open_external_update_url,
    )
    controller._update_coordinator = coordinator
    try:
        coordinator.check_now()
        assert available_ev.wait(2.0)
        assert bridge_calls == []

        # Clicking "Cập nhật" transitions to MANUAL_DOWNLOAD_REQUIRED ("Mở trang tải")
        # without ever touching AndroidPackageDownloader.download().
        controller._request_update_download()
        assert manual_ev.wait(2.0), (
            f"Expected MANUAL_DOWNLOAD_REQUIRED, got btn={controller._update_btn.content!r} "
            f"text={controller._update_text.value!r}"
        )
        assert bridge_calls == []
        assert controller._update_manual_download is True
        assert controller._update_ready is False
        assert controller._update_btn.content == "Mở trang tải"

        # Clicking "Mở trang tải" shows Android manual-download confirmation (NOT "đã tải").
        controller._request_update_download()
        assert len(shown_dialogs) == 1
        dlg = shown_dialogs[0]
        body_text = dlg.content.value
        affirmative_btn = dlg.actions[1]
        assert "đã tải" not in body_text
        assert "GitHub Releases" in body_text
        assert affirmative_btn.content == "Mở trang tải APK"

        # Confirming opens the GitHub release page.
        affirmative_btn.on_click(None)
        assert launched_ev.wait(2.0)
        assert opened_urls == ["https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1"]
        assert bridge_calls == []
    finally:
        clear_global_webview_occlusion()
        assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_pkg01_update_coordinator_rejects_untrusted_manual_release_url_when_auto_install_disallowed():
    package = ReleasePackage(
        platform="android",
        architecture="arm64",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.apk",
        sha256="a" * 64,
        size=1024,
        signer_identity="com.uthelper.uthelper",
        certificate_fingerprint="B" * 64,
        install_strategy={"kind": "android_package_installer"},
        signature_kind="apk-pinned",
    )
    manifest = ReleaseManifest(
        schema_version=3,
        release_version="2.5.1",
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 11, 25, tzinfo=UTC),
        release_notes_url="https://github.com/evil-org/UTHelper/releases/tag/v2.5.1",
        packages=(package,),
    )
    candidate = UpdateCandidate(
        manifest=manifest,
        package=package,
        automatic_install_allowed=False,
        required_update=False,
    )

    events: list[UpdateEvent] = []
    failed_ev = threading.Event()

    def sink(ev: UpdateEvent):
        events.append(ev)
        if ev.kind is UpdateEventKind.FAILED:
            failed_ev.set()

    class _NeverDownload:
        def download(self, *_a, **_kw):
            raise AssertionError("Must not download when automatic_install_allowed=False")

    class _DummyVerifier:
        def verify(self, *_a, **_kw):
            raise AssertionError("Must not verify")

    class _DummyLauncher:
        def launch(self, *_a, **_kw):
            raise AssertionError("Must not launch")

        def cancel(self):
            pass

    coordinator = UpdateCoordinator(
        SimpleNamespace(fetch_candidate=lambda *_a, **_kw: candidate),
        _NeverDownload(),
        _DummyVerifier(),
        _DummyLauncher(),
        RuntimeTarget("android", "arm64", "sideload"),
        "2.5.0",
        sink,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        assert failed_ev.wait(2.0)
        assert coordinator._ready_external_url is None
        assert any(
            ev.kind is UpdateEventKind.FAILED and "release page" in ev.message
            for ev in events
        )
    finally:
        assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_d05_stacked_app_controller_modals_preserve_outer_occlusion_across_double_close(
    monkeypatch,
):
    """Closing an inner modal via action + on_dismiss must NOT un-occlude an outer modal."""
    import gui.components.login_dialog as login_dialog_mod

    clear_global_webview_occlusion()
    try:
        shown_dialogs: list[object] = []

        class _FakeDialog:
            def __init__(self):
                self.open = True
                self.on_dismiss = None

        outer_login_dlg = _FakeDialog()

        async def _fake_show_login_dialog(page, _orch, on_success, **_kw):
            page.overlay.append(outer_login_dlg)
            outer_login_dlg._on_success = on_success

        monkeypatch.setattr(login_dialog_mod, "show_login_dialog", _fake_show_login_dialog)
        monkeypatch.setattr(platform_utils, "IS_WINDOWS", True)
        monkeypatch.setattr(platform_utils, "IS_ANDROID", False)

        controller = AppController.__new__(AppController)
        controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
        controller._diag_screen = lambda *_a, **_kw: None
        controller._diag_breadcrumb = lambda *_a, **_kw: None
        controller.orchestrator = SimpleNamespace()
        controller._update_coordinator = SimpleNamespace(confirm_install=lambda: None)
        controller.page = SimpleNamespace(
            overlay=[],
            dialog=None,
            show_dialog=lambda dlg: shown_dialogs.append(dlg),
            pop_dialog=lambda: None,
        )

        # 1. Open outer LoginDialog -> occluded = True.
        asyncio.run(controller._show_login_dialog())
        assert is_globally_occluded() is True
        assert callable(outer_login_dlg.on_dismiss)

        # 2. Open stacked inner Update Confirmation dialog -> occluded = True.
        controller._show_update_confirmation()
        assert len(shown_dialogs) == 1
        inner_dlg = shown_dialogs[0]
        assert is_globally_occluded() is True

        # 3. Inner dialog closes: Flet runs BOTH cancel button callback AND on_dismiss!
        cancel_btn = inner_dlg.actions[0]
        cancel_btn.on_click(None)
        inner_dlg.on_dismiss(None)

        # Outer LoginDialog is STILL open -> WebView2 MUST remain occluded!
        assert is_globally_occluded() is True

        # 4. Dismissing the outer LoginDialog finally clears occlusion.
        res = outer_login_dlg.on_dismiss(None)
        if asyncio.iscoroutine(res):
            asyncio.run(res)
        assert is_globally_occluded() is False
    finally:
        clear_global_webview_occlusion()


def test_d06_architecture_boundary_scanner_catches_init_files_and_relative_imports(
    tmp_path: Path,
):
    fake_src = tmp_path / "src"
    (fake_src / "core" / "sub").mkdir(parents=True)
    (fake_src / "gui").mkdir(parents=True)

    init_file = fake_src / "core" / "__init__.py"
    init_file.write_text("from ..gui import app_controller\n", encoding="utf-8")

    rel_file = fake_src / "core" / "sub" / "worker.py"
    rel_file.write_text(
        "from ...gui.components import detail_view\nfrom . import local_mod\n",
        encoding="utf-8",
    )

    modules = arch_boundaries._python_modules(fake_src)
    assert "core" in modules
    assert "core.sub.worker" in modules

    init_imports = arch_boundaries._imports_for(init_file, src_root=fake_src)
    assert "gui.app_controller" in init_imports or "gui" in init_imports

    worker_imports = arch_boundaries._imports_for(rel_file, src_root=fake_src)
    assert "gui.components.detail_view" in worker_imports or "gui.components" in worker_imports
    assert "core.sub.local_mod" in worker_imports
