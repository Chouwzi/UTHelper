"""Tier 1 opaque-box E2E tests for Features F1-F4:
- F1: Core distribution channel model & asset loader
- F2: Store-compliant runtime update suppression
- F3: Store-compliant Settings & Banner UI + deep links
- F4: Android storage path & Privacy/Terms/Consent consistency

All M1-M4 target module imports are deferred via ``harness.load_module`` so
``pytest --collect-only`` remains parallel-safe.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    FakePage,
    LocalReleaseServer,
    WORKTREE_ROOT,
    load_module,
    make_minimal_apk_bytes,
    run_isolated_python,
)


# ---------------------------------------------------------------------------
# Local Opaque-Box Helpers (Zero Top-Level M1-M4 Imports)
# ---------------------------------------------------------------------------


def _collect_controls(control: Any) -> list[Any]:
    items = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        items.extend(_collect_controls(content))
    for child in getattr(control, "controls", None) or ():
        items.extend(_collect_controls(child))
    return items


def _collect_text_values(control: Any) -> list[str]:
    found: list[str] = []
    if isinstance(control, ft.Text) and isinstance(control.value, str):
        found.append(control.value)
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        found.extend(_collect_text_values(content))
    elif isinstance(content, str):
        found.append(content)
    for child in getattr(control, "controls", None) or ():
        found.extend(_collect_text_values(child))
    return found


def _build_controller_for_channel(
    monkeypatch: pytest.MonkeyPatch,
    fake_page: FakePage,
    *,
    is_windows: bool,
    is_android: bool,
    android_bridge: Any = None,
) -> Any:
    """Instantiate a real ``AppController`` with OS/Moodle I/O isolated."""
    if not hasattr(fake_page, "controls"):
        fake_page.controls = []  # type: ignore[attr-defined]
    if not hasattr(fake_page, "add"):
        fake_page.add = lambda *ctrls: fake_page.controls.extend(ctrls)  # type: ignore[attr-defined]
    if not fake_page.views:
        fake_page.views.append(SimpleNamespace(can_pop=True, on_confirm_pop=None))

    app_controller_mod = load_module("gui.app_controller")
    platform_utils = load_module("platform_utils")
    update_packages = load_module("platform_utils.update_packages")
    autostart_mod = load_module("platform_utils.autostart")
    settings_view_mod = load_module("gui.components.settings_view")
    tray_mod = load_module("gui.tray")

    is_mobile = bool(is_android)
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", is_android)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(platform_utils, "IS_MOBILE", is_mobile)
    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", is_mobile)
    monkeypatch.setattr(
        app_controller_mod,
        "detect_platform",
        lambda _page=None: "android" if is_android else "windows",
    )
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", is_android)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(settings_view_mod._pu, "IS_MOBILE", is_mobile)
    monkeypatch.setattr(settings_view_mod._pu, "UI_IS_MOBILE", is_mobile)
    monkeypatch.setattr(
        autostart_mod,
        "create_autostart_service",
        lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(
                enabled=False, editable=False, supported=False, message=""
            ),
            apply_initial_default=lambda: None,
        ),
    )
    monkeypatch.setattr(tray_mod.TrayApp, "setup", lambda _self: False)

    if is_android:
        bg_mod = load_module("platform_utils.background_sync")
        monkeypatch.setattr(
            bg_mod,
            "create_mobile_background_bridge",
            lambda _page: android_bridge,
        )

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        app_controller_mod.AppController, "_show_login_dialog", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_load_data_async", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_perform_data_sync", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_initialize_android_background", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.TodayScheduleCoordinator, "run", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.ActivitySyncCoordinator, "run", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController,
        "_apply_initial_windows_autostart_default",
        _noop_async,
    )

    return app_controller_mod.AppController(fake_page)


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# ===========================================================================
# F1: Core distribution channel model & asset loader (6 tests)
# ===========================================================================


@pytest.mark.parametrize(
    "channel_name,expected_self_update,expected_display,has_store_url",
    [
        ("chplay", False, "Google Play", True),
        ("apk_lts", True, "GitHub Releases", False),
        ("msstore", False, "Microsoft Store", True),
        ("exe_lts", True, "GitHub Releases", False),
    ],
)
def test_f1_resolve_channel_from_flet_assets_json_for_all_packaged_channels(
    distribution_channel_stager,
    isolated_runtime_env: dict[str, Path],
    channel_name: str,
    expected_self_update: bool,
    expected_display: str,
    has_store_url: bool,
) -> None:
    dist_mod = load_module("core.distribution")
    distribution_channel_stager(
        channel_name,
        store_product_id="9NBLGGH4NNS1" if channel_name == "msstore" else None,
    )

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=(channel_name in {"msstore", "exe_lts"}),
        is_android=(channel_name in {"chplay", "apk_lts"}),
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )

    assert profile.channel.value == channel_name
    assert profile.supports_github_self_update is expected_self_update
    assert profile.store_display_name == expected_display
    if has_store_url:
        assert profile.store_url is not None
    else:
        assert profile.store_url is None


def test_f1_chplay_profile_uses_custom_android_package_id_and_play_urls(
    distribution_channel_stager,
    isolated_runtime_env: dict[str, Path],
) -> None:
    dist_mod = load_module("core.distribution")
    distribution_channel_stager(
        "chplay",
        android_package_id="com.uthelper.customplay",
    )

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=False,
        is_android=True,
        is_ios=False,
    )

    assert profile.channel is dist_mod.DistributionChannel.CHPLAY
    assert profile.supports_github_self_update is False
    assert profile.store_url == "market://details?id=com.uthelper.customplay"
    assert (
        profile.store_web_url
        == "https://play.google.com/store/apps/details?id=com.uthelper.customplay"
    )
    assert profile.store_display_name == "Google Play"


def test_f1_msstore_profile_builds_pdp_deep_link_from_store_product_id(
    distribution_channel_stager,
    isolated_runtime_env: dict[str, Path],
) -> None:
    dist_mod = load_module("core.distribution")
    distribution_channel_stager("msstore", store_product_id="9P12345678AB")

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: True,
    )

    assert profile.channel is dist_mod.DistributionChannel.MSSTORE
    assert profile.supports_github_self_update is False
    assert profile.store_url == "ms-windows-store://pdp/?ProductId=9P12345678AB"
    assert profile.store_web_url == "https://apps.microsoft.com/detail/9P12345678AB"
    assert profile.store_display_name == "Microsoft Store"


def test_f1_windows_package_identity_fallback_resolves_msstore(
    isolated_runtime_env: dict[str, Path],
) -> None:
    dist_mod = load_module("core.distribution")

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: True,
    )

    assert profile.channel is dist_mod.DistributionChannel.MSSTORE
    assert profile.supports_github_self_update is False
    assert profile.store_display_name == "Microsoft Store"


def test_f1_android_fallback_without_config_resolves_apk_lts(
    isolated_runtime_env: dict[str, Path],
) -> None:
    dist_mod = load_module("core.distribution")

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=False,
        is_android=True,
        is_ios=False,
    )

    assert profile.channel is dist_mod.DistributionChannel.APK_LTS
    assert profile.supports_github_self_update is True
    assert profile.store_url is None


def test_f1_source_and_packaged_windows_fallbacks(
    isolated_runtime_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not (WORKTREE_ROOT / "src" / "assets" / "distribution-channel.json").exists(), (
        "Source working tree must not retain a packaged src/assets/distribution-channel.json"
    )
    dist_mod = load_module("core.distribution")
    env = {"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])}

    monkeypatch.delattr(sys, "frozen", raising=False)
    auto_source = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )
    assert auto_source.channel is dist_mod.DistributionChannel.SOURCE
    assert auto_source.supports_github_self_update is True

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    auto_frozen = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )
    assert auto_frozen.channel is dist_mod.DistributionChannel.EXE_LTS
    assert auto_frozen.supports_github_self_update is True

    monkeypatch.delattr(sys, "frozen", raising=False)
    auto_no_pyproject = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
        pyproject_path=isolated_runtime_env["root"] / "missing-pyproject.toml",
    )
    assert auto_no_pyproject.channel is dist_mod.DistributionChannel.EXE_LTS
    assert auto_no_pyproject.supports_github_self_update is True

    source_profile = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
        is_packaged=False,
    )
    exe_lts_profile = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
        is_packaged=True,
    )
    generic_source = dist_mod.resolve_distribution_channel(
        environ=env,
        is_windows=False,
        is_android=False,
        is_ios=False,
    )

    assert source_profile.channel is dist_mod.DistributionChannel.SOURCE
    assert source_profile.supports_github_self_update is True
    assert exe_lts_profile.channel is dist_mod.DistributionChannel.EXE_LTS
    assert exe_lts_profile.supports_github_self_update is True
    assert generic_source.channel is dist_mod.DistributionChannel.SOURCE
    assert generic_source.supports_github_self_update is True


# ===========================================================================
# F2: Store-compliant runtime update suppression (5 tests)
# ===========================================================================


def test_f2_chplay_suppresses_update_coordinator_and_zero_github_requests(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("chplay")
    apk_bytes = make_minimal_apk_bytes(version_code=2_099_000)
    local_release_server.register_package(
        tag_name="v2.99.0",
        filename="UTHelper-2.99.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
    )
    local_release_server.configure_latest_release(
        tag_name="v2.99.0",
        manifest=local_release_server.build_manifest(release_version="2.99.0"),
    )

    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
    )
    try:
        assert controller._update_coordinator is None
        controller._check_updates_now()
        controller._request_update_download()
        controller._show_update_confirmation()
        controller._cancel_update_download()
        assert local_release_server.request_count == 0
    finally:
        controller._on_disconnect(None)


def test_f2_msstore_suppresses_update_coordinator_and_zero_github_requests(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("msstore", store_product_id="9NBLGGH4NNS1")
    win_pkg = b"MZ" + b"\x00" * 64
    local_release_server.register_package(
        tag_name="v2.99.0",
        filename="UTHelper-Setup-2.99.0.exe",
        data=win_pkg,
        platform="windows",
        architecture="x64",
        package_type="exe",
        install_channel="bootstrapper",
    )
    local_release_server.configure_latest_release(
        tag_name="v2.99.0",
        manifest=local_release_server.build_manifest(release_version="2.99.0"),
    )

    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    try:
        assert controller._update_coordinator is None
        controller._check_updates_now()
        controller._request_update_download()
        controller._show_update_confirmation()
        assert local_release_server.request_count == 0
    finally:
        controller._on_disconnect(None)


def test_f2_detect_runtime_target_respects_chplay_apk_lts_and_msstore(
    distribution_channel_stager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_packages = load_module("platform_utils.update_packages")
    windows_update = load_module("platform_utils.windows_update")

    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)

    distribution_channel_stager("chplay")
    assert update_packages.detect_runtime_target().install_channel == "chplay"

    distribution_channel_stager("apk_lts")
    assert update_packages.detect_runtime_target().install_channel == "sideload"

    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(windows_update, "has_package_identity", lambda: True)
    distribution_channel_stager("msstore", store_product_id="9NBLGGH4NNS1")
    assert update_packages.detect_runtime_target().install_channel == "msix"


def test_f2_apk_lts_starts_update_coordinator_and_queries_local_release_server(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    android_bridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("apk_lts")
    apk_bytes = make_minimal_apk_bytes(version_code=2_099_000)
    local_release_server.register_package(
        tag_name="v2.99.0",
        filename="UTHelper-2.99.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
    )
    local_release_server.configure_latest_release(
        tag_name="v2.99.0",
        manifest=local_release_server.build_manifest(release_version="2.99.0"),
    )

    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
        android_bridge=android_bridge,
    )
    try:
        assert controller._update_coordinator is not None
        controller._check_updates_now()
        assert _wait_until(
            lambda: controller._update_candidate is not None
            and controller._update_candidate.manifest.release_version == "2.99.0"
        )
        paths = [req["path"] for req in local_release_server.requests]
        assert "/repos/Chouwzi/UTHelper/releases/latest" in paths
        assert "/Chouwzi/UTHelper/releases/download/v2.99.0/release-manifest.json" in paths
    finally:
        controller._on_disconnect(None)


def test_f2_exe_lts_starts_update_coordinator_and_queries_local_release_server(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows_update = load_module("platform_utils.windows_update")
    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "bootstrapper")
    distribution_channel_stager("exe_lts")

    local_release_server.register_package(
        tag_name="v2.99.0",
        filename="UTHelper-Setup-2.99.0.exe",
        data=b"MZ" + b"\x00" * 64,
        platform="windows",
        architecture="x64",
        package_type="exe",
        install_channel="bootstrapper",
    )
    local_release_server.configure_latest_release(
        tag_name="v2.99.0",
        manifest=local_release_server.build_manifest(release_version="2.99.0"),
    )

    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    try:
        assert controller._update_coordinator is not None
        controller._check_updates_now()
        assert _wait_until(
            lambda: controller._update_candidate is not None
            and controller._update_candidate.manifest.release_version == "2.99.0"
        )
        paths = [req["path"] for req in local_release_server.requests]
        assert "/repos/Chouwzi/UTHelper/releases/latest" in paths
    finally:
        controller._on_disconnect(None)


# ===========================================================================
# F3: Store-compliant Settings & Banner UI + deep links (6 tests)
# ===========================================================================


def test_f3_chplay_settings_hides_github_controls_and_shows_play_store_ui(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("chplay")
    settings_view_mod = load_module("gui.components.settings_view")
    system_section_mod = load_module("gui.components.settings.system_section")
    monkeypatch.setattr(settings_view_mod._pu, "IS_MOBILE", True)
    monkeypatch.setattr(settings_view_mod._pu, "UI_IS_MOBILE", True)

    view = settings_view_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
    )
    section = system_section_mod.build_system_section(view)
    controls = _collect_controls(section)
    texts = _collect_text_values(section)
    debug_texts = _collect_text_values(view._test_panel)

    assert view._sw_auto_update not in controls
    assert view._check_update_btn not in controls
    assert view._update_status_group not in controls
    assert view._sw_auto_update.visible is False
    assert view._check_update_btn.visible is False
    assert "Cập nhật được quản lý bởi Google Play" in texts
    assert view._store_link_btn in controls
    assert view._store_link_btn.visible is True
    assert view._store_link_btn.content == "Mở trên Google Play"
    assert "Force check update" not in debug_texts
    assert "Google Play" in view._hub_version.value


def test_f3_msstore_settings_hides_github_controls_and_shows_msstore_ui(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_view_mod = load_module("gui.components.settings_view")
    system_section_mod = load_module("gui.components.settings.system_section")
    autostart_mod = load_module("platform_utils.autostart")
    monkeypatch.setattr(settings_view_mod._pu, "IS_MOBILE", False)
    monkeypatch.setattr(settings_view_mod._pu, "UI_IS_MOBILE", False)
    monkeypatch.setattr(
        autostart_mod,
        "create_autostart_service",
        lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(
                enabled=False, editable=False, supported=False, message=""
            )
        ),
    )

    for product_id, expect_btn in (("9NBLGGH4NNS1", True), (None, False)):
        distribution_channel_stager("msstore", store_product_id=product_id)
        view = settings_view_mod.SettingsView(
            page=fake_page,
            orchestrator=None,
            on_close=lambda: None,
        )
        section = system_section_mod.build_system_section(view)
        controls = _collect_controls(section)
        texts = _collect_text_values(section)
        debug_texts = _collect_text_values(view._test_panel)

        assert view._sw_auto_update not in controls
        assert view._check_update_btn not in controls
        assert "Cập nhật được quản lý bởi Microsoft Store" in texts
        assert "Force check update" not in debug_texts
        assert "Microsoft Store" in view._hub_version.value
        assert view._store_link_btn.visible is expect_btn
        if expect_btn:
            assert view._store_link_btn.content == "Mở trong Microsoft Store"
        else:
            assert any("Thư viện" in text for text in texts)


def test_f3_chplay_store_link_launches_market_deep_link_and_https_fallback(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("chplay")
    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
    )
    try:
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}"
            in fake_page.launched_urls
        )

        fake_page.launched_urls.clear()
        fake_page.fail_url_schemes.add("market")
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: f"https://play.google.com/store/apps/details?id={DEFAULT_ANDROID_PACKAGE_ID}"
            in fake_page.launched_urls
        )
    finally:
        controller._on_disconnect(None)


def test_f3_msstore_store_link_launches_ms_windows_store_pdp_deep_link(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("msstore", store_product_id="9NBLGGH4NNS1")
    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    try:
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
            in fake_page.launched_urls
        )

        fake_page.launched_urls.clear()
        fake_page.fail_url_schemes.add("ms-windows-store")
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: "https://apps.microsoft.com/detail/9NBLGGH4NNS1"
            in fake_page.launched_urls
        )
    finally:
        controller._on_disconnect(None)


def test_f3_store_channels_keep_update_banner_hidden_and_reject_unsafe_schemes(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator_mod = load_module("core.update_coordinator")
    distribution_channel_stager("chplay")
    controller = _build_controller_for_channel(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
    )
    try:
        fake_candidate = SimpleNamespace(
            manifest=SimpleNamespace(
                release_version="2.99.0",
                release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.99.0",
            )
        )
        asyncio.run(
            controller._apply_update_event(
                coordinator_mod.UpdateEvent(
                    coordinator_mod.UpdateEventKind.UPDATE_AVAILABLE,
                    fake_candidate,
                )
            )
        )
        assert controller._update_banner.visible is False
        assert controller._open_store_url("javascript:alert(1)") is False
        assert controller._open_store_url("file:///C:/Windows/System32/calc.exe") is False
    finally:
        controller._on_disconnect(None)


@pytest.mark.parametrize(
    "channel_name,is_windows,is_android",
    [
        ("apk_lts", False, True),
        ("exe_lts", True, False),
    ],
)
def test_f3_lts_channels_retain_github_update_controls_and_debug_force_check(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
    channel_name: str,
    is_windows: bool,
    is_android: bool,
) -> None:
    distribution_channel_stager(channel_name)
    settings_view_mod = load_module("gui.components.settings_view")
    system_section_mod = load_module("gui.components.settings.system_section")
    autostart_mod = load_module("platform_utils.autostart")
    windows_update = load_module("platform_utils.windows_update")

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(settings_view_mod._pu, "IS_MOBILE", is_android)
    monkeypatch.setattr(settings_view_mod._pu, "UI_IS_MOBILE", is_android)
    monkeypatch.setattr(
        autostart_mod,
        "create_autostart_service",
        lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(
                enabled=False, editable=False, supported=False, message=""
            )
        ),
    )

    view = settings_view_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
    )
    section = system_section_mod.build_system_section(view)
    controls = _collect_controls(section)
    debug_texts = _collect_text_values(view._test_panel)

    assert view._sw_auto_update in controls
    assert view._sw_auto_update.visible is True
    assert view._check_update_btn in controls
    assert view._check_update_btn.visible is True
    assert view._check_update_btn.content == "Kiểm tra ngay"
    assert view._update_status_group in controls
    assert "Force check update" in debug_texts


# ===========================================================================
# F4: Android storage path & Privacy/Terms/Consent consistency (5 tests)
# ===========================================================================


def test_f4_android_config_data_dir_aligns_with_flet_app_storage_data(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "android_internal_storage"
    script = (
        "import json, sys\n"
        "sys._ANDROID_ = True\n"
        "import config\n"
        "print(json.dumps({\n"
        "  'is_android': config.is_android(),\n"
        "  'data_dir': str(config.get_data_dir()),\n"
        "  'config_parent': str(config.CONFIG_FILE.parent),\n"
        "}))\n"
    )
    proc = run_isolated_python(
        script,
        env_overrides={
            "FLET_APP_STORAGE_DATA": str(storage_dir),
            "FLET_APP_DATA": None,
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip())
    assert result["is_android"] is True
    assert Path(result["data_dir"]) == storage_dir / "UTHelper"
    assert Path(result["config_parent"]) == storage_dir / "UTHelper"


def test_f4_settings_form_snapshot_and_privacy_section_consent_round_trip(
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    privacy_section = load_module("gui.components.settings.privacy_section")
    settings_form = load_module("gui.view_models.settings_form")
    config_mod = load_module("config")

    monkeypatch.setattr(config_mod.settings, "CRASH_REPORTING_CONSENT", "enabled")
    view = SimpleNamespace(
        _page=fake_page,
        _themed_texts=[],
        _hint_containers=[],
        _make_themed_label=lambda text: ft.Text(text),
        _build_hint=lambda text: ft.Container(content=ft.Text(text)),
        _build_setting_group=lambda title, subtitle, controls, **_kw: ft.Column(controls),
    )
    privacy_section.init_privacy_controls(view)
    section = privacy_section.build_privacy_section(view)

    assert view._dd_crash_reporting_consent in _collect_controls(section)
    assert view._dd_crash_reporting_consent.value == "enabled"

    view._dd_crash_reporting_consent.value = "disabled"
    snap_disabled = settings_form.SettingsFormSnapshot.from_form_values(
        {"crash_reporting_consent": view._dd_crash_reporting_consent.value}
    )
    assert snap_disabled.crash_reporting_consent == "disabled"
    assert snap_disabled.to_settings_values()["CRASH_REPORTING_CONSENT"] == "disabled"

    restored = settings_form.SettingsFormSnapshot.from_settings(
        SimpleNamespace(**snap_disabled.to_settings_values())
    )
    assert restored.crash_reporting_consent == "disabled"

    snap_enabled = settings_form.SettingsFormSnapshot.from_form_values(
        {"crash_reporting_consent": "enabled"}
    )
    assert snap_enabled.to_settings_values()["CRASH_REPORTING_CONSENT"] == "enabled"


def test_f4_diagnostics_runtime_stops_sending_and_clears_spool_on_disabled_consent(
    tmp_path: Path,
) -> None:
    diag_models = load_module("diagnostics.models")
    diag_runtime = load_module("diagnostics.runtime")
    diag_spool = load_module("diagnostics.spool")

    consent_state = {"value": diag_models.CrashConsent.ENABLED}
    delivery_calls: list[diag_models.CrashConsent] = []

    class RecordingDelivery:
        def flush_once(self, consent: diag_models.CrashConsent) -> SimpleNamespace:
            delivery_calls.append(consent)
            return SimpleNamespace(skipped_unconfigured=False, attempted=1, retained=0)

    class ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):
            fn(*args, **kwargs)
            return SimpleNamespace(result=lambda timeout=None: None)

        def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
            return None

    spool = diag_spool.DiagnosticSpool(tmp_path / "telemetry" / "pending")
    runtime = diag_runtime.DiagnosticRuntime(
        data_dir=tmp_path,
        spool=spool,
        delivery=RecordingDelivery(),
        context_provider=lambda phase, unclean: diag_models.DiagnosticContext(
            source_root=WORKTREE_ROOT / "src",
            app_version="2.5.1",
            release_channel="stable",
            install_type="packaged",
            os_family="Windows",
            os_version="11",
            architecture="AMD64",
            python_version="3.13.12",
            flet_version="0.86.5",
            phase=phase,
            window_state="foreground",
            unclean_previous_exit=unclean,
        ),
        consent_provider=lambda: consent_state["value"],
        delivery_executor=ImmediateExecutor(),
        clock=lambda: datetime.now(timezone.utc),
    )

    runtime.start()
    try:
        ref_enabled = runtime.record_exception(RuntimeError("first"))
        assert ref_enabled is not None
        assert len(spool.pending()) == 1
        assert diag_models.CrashConsent.ENABLED in delivery_calls

        delivery_calls.clear()
        consent_state["value"] = diag_models.CrashConsent.DISABLED
        runtime.apply_consent("disabled")

        assert not spool.pending()
        assert runtime.record_exception(RuntimeError("after_disable")) is None
        assert delivery_calls == []
    finally:
        runtime.close(clean=True)


def test_f4_privacy_and_terms_docs_match_consent_and_allowlist_contract() -> None:
    terms_dialog = load_module("gui.components.terms_dialog")
    privacy_md = (WORKTREE_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    terms_md = (WORKTREE_ROOT / "docs" / "TERMS.md").read_text(encoding="utf-8")

    assert "enabled by default" in privacy_md
    assert "Settings → Privacy" in privacy_md
    assert "20 reports" in privacy_md
    assert "1 MiB" in privacy_md
    assert "7 days" in privacy_md
    assert "What is forbidden from remote reports" in privacy_md
    assert "MoodleSession" in privacy_md

    assert "PRIVACY.md" in terms_md
    assert "Cài đặt" in terms_md
    assert "PolyForm Noncommercial" in terms_md

    assert len(terms_dialog.TERMS_SECTIONS) == 7
    section_titles = [title for title, _body in terms_dialog.TERMS_SECTIONS]
    assert "3. Chẩn đoán sự cố" in section_titles
    assert "4. Dịch vụ bên thứ ba" in section_titles


def test_f4_terms_dialog_adapts_disclosures_per_store_vs_lts_and_opens_from_privacy(
    fake_page: FakePage,
) -> None:
    dist_mod = load_module("core.distribution")
    terms_dialog = load_module("gui.components.terms_dialog")
    privacy_section = load_module("gui.components.settings.privacy_section")

    chplay_sections = dict(
        terms_dialog.get_terms_sections(
            dist_mod.build_distribution_profile(dist_mod.DistributionChannel.CHPLAY)
        )
    )
    msstore_sections = dict(
        terms_dialog.get_terms_sections(
            dist_mod.build_distribution_profile(
                dist_mod.DistributionChannel.MSSTORE,
                store_product_id="9NBLGGH4NNS1",
            )
        )
    )
    apk_lts_sections = dict(
        terms_dialog.get_terms_sections(
            dist_mod.build_distribution_profile(dist_mod.DistributionChannel.APK_LTS)
        )
    )

    assert "Google Play" in chplay_sections["4. Dịch vụ bên thứ ba"]
    assert "GitHub" not in chplay_sections["4. Dịch vụ bên thứ ba"]
    assert "Microsoft Store" in msstore_sections["4. Dịch vụ bên thứ ba"]
    assert "GitHub" not in msstore_sections["4. Dịch vụ bên thứ ba"]
    assert "GitHub" in apk_lts_sections["4. Dịch vụ bên thứ ba"]
    assert "Sentry" in apk_lts_sections["4. Dịch vụ bên thứ ba"]

    view = SimpleNamespace(
        _page=fake_page,
        _themed_texts=[],
        _hint_containers=[],
        _make_themed_label=lambda text: ft.Text(text),
        _build_hint=lambda text: ft.Container(content=ft.Text(text)),
        _build_setting_group=lambda title, subtitle, controls, **_kw: ft.Column(controls),
    )
    privacy_section.init_privacy_controls(view)
    privacy_section.build_privacy_section(view)
    view._terms_policy_entry.on_click(None)
    assert len(fake_page.dialogs) == 1
    assert isinstance(fake_page.dialogs[0], ft.AlertDialog)
