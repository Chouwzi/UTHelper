"""Tier 4 real-world end-to-end application scenarios across F1-F13.

All M1-M4 target modules are imported lazily via ``harness.load_module(...)``
so ``pytest --collect-only`` never raises ``ImportError`` regardless of milestone
development order.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DEFAULT_TRUSTED_SIGNER_SHA256,
    FakePage,
    WORKTREE_ROOT,
    load_module,
    make_minimal_apk_bytes,
    make_synthetic_aab,
    make_synthetic_lts_release_dir,
    run_isolated_python,
    stage_distribution_channel_asset,
)


def _collect_text_values(control) -> list[str]:
    ft = load_module("flet")
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


def _wait_until(predicate, *, timeout: float = 8.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _prepare_full_page(fake_page: FakePage) -> FakePage:
    """Attach Page.controls / Page.add / mobile root View expected by full AppController."""
    fake_page.controls = []
    fake_page.add = lambda *items: fake_page.controls.extend(items)
    fake_page.views = [SimpleNamespace(can_pop=True, on_confirm_pop=None)]
    return fake_page


def _stub_app_controller_perimeter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep full AppController instantiation offline and free of OS tray side effects."""
    app_ctrl_mod = load_module("gui.app_controller")
    tray_mod = load_module("gui.tray")
    orch_mod = load_module("core.data_orchestrator")
    portal_mod = load_module("core.portal_client")
    cfg_mod = load_module("config")

    monkeypatch.setattr(cfg_mod.settings, "UTH_USERNAME", "")
    monkeypatch.setattr(cfg_mod.settings, "UTH_PASSWORD", "")
    monkeypatch.setattr(tray_mod.TrayApp, "setup", lambda _self, **_kw: False)
    monkeypatch.setattr(tray_mod.TrayApp, "close", lambda _self, **_kw: None)
    monkeypatch.setattr(orch_mod.DataOrchestrator, "get_cached_details_snapshot", lambda _self: {}, raising=False)
    monkeypatch.setattr(portal_mod.PortalClient, "fetch_day", lambda _self, *_a, **_kw: [])

    async def _noop_login(_self):
        return None

    monkeypatch.setattr(app_ctrl_mod.AppController, "_show_login_dialog", _noop_login)


def _make_fake_android_sdk(sdk_root: Path) -> Path:
    build_tools = sdk_root / "build-tools" / "35.0.0"
    cmdline_bin = sdk_root / "cmdline-tools" / "latest" / "bin"
    build_tools.mkdir(parents=True, exist_ok=True)
    cmdline_bin.mkdir(parents=True, exist_ok=True)

    helper_py = sdk_root / "_sdk_helper.py"
    helper_py.write_text(
        (
            "import sys, xml.etree.ElementTree as ET, zipfile\n"
            "tool = sys.argv[1]\n"
            "args = sys.argv[2:]\n"
            "if tool == 'apksigner':\n"
            "    fp = ':'.join(a+b for a,b in zip('"
            + DEFAULT_TRUSTED_SIGNER_SHA256
            + "'[::2], '"
            + DEFAULT_TRUSTED_SIGNER_SHA256
            + "'[1::2]))\n"
            "    print(f'Signer #1 certificate SHA-256 digest: {fp}')\n"
            "    raise SystemExit(0)\n"
            "if tool == 'apkanalyzer':\n"
            "    sub = args[1]\n"
            "    pkg_path = args[-1]\n"
            "    with zipfile.ZipFile(pkg_path) as zf:\n"
            "        for name in ('AndroidManifest.xml', 'base/manifest/AndroidManifest.xml'):\n"
            "            if name in zf.namelist():\n"
            "                xml_text = zf.read(name).decode('utf-8')\n"
            "                break\n"
            "        else:\n"
            "            raise SystemExit(2)\n"
            "    root = ET.fromstring(xml_text)\n"
            "    if sub == 'application-id':\n"
            "        print(root.attrib.get('package', 'com.uthelper.uthelper'))\n"
            "    elif sub == 'version-name':\n"
            "        print('2.5.1')\n"
            "    elif sub == 'version-code':\n"
            "        print('2005001')\n"
            "    elif sub == 'print':\n"
            "        print(xml_text)\n"
            "    raise SystemExit(0)\n"
        ),
        encoding="utf-8",
    )
    py_exe = sys.executable
    if os.name == "nt":
        (build_tools / "apksigner.bat").write_text(
            f'@echo off\r\n"{py_exe}" "{helper_py}" apksigner %*\r\n',
            encoding="utf-8",
        )
        (cmdline_bin / "apkanalyzer.bat").write_text(
            f'@echo off\r\n"{py_exe}" "{helper_py}" apkanalyzer %*\r\n',
            encoding="utf-8",
        )
    else:
        for name, tool in ((build_tools / "apksigner", "apksigner"), (cmdline_bin / "apkanalyzer", "apkanalyzer")):
            name.write_text(
                f'#!/bin/sh\nexec "{py_exe}" "{helper_py}" {tool} "$@"\n',
                encoding="utf-8",
            )
            name.chmod(0o755)
    return sdk_root


# ---------------------------------------------------------------------------
# Scenario 1: Fresh CH Play launch, Settings/Privacy/Terms, Store deep link
# ---------------------------------------------------------------------------


def test_t4_s01_chplay_fresh_launch_and_store_navigation(
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    local_release_server,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("chplay", android_package_id=DEFAULT_ANDROID_PACKAGE_ID)

    # 1. Verify cold subprocess storage + channel alignment on Android
    sub_code = (
        "import json, sys\n"
        "sys._ANDROID_ = True\n"
        "import config, platform_utils\n"
        "from core.distribution import resolve_distribution_channel\n"
        "platform_utils.IS_ANDROID = True\n"
        "platform_utils.IS_WINDOWS = False\n"
        "platform_utils.IS_IOS = False\n"
        "profile = resolve_distribution_channel()\n"
        "print(json.dumps({'data_dir': str(config.get_data_dir()), 'channel': profile.channel.value, 'store_url': profile.store_url}))\n"
    )
    sub = run_isolated_python(
        sub_code,
        env_overrides={"FLET_APP_STORAGE_DATA": str(isolated_runtime_env["flet_data"]), "FLET_APP_DATA": None},
        tmp_path=isolated_runtime_env["root"],
    )
    assert sub.returncode == 0, sub.stderr
    sub_info = json.loads(sub.stdout.strip().splitlines()[-1])
    assert Path(sub_info["data_dir"]) == isolated_runtime_env["flet_data"] / "UTHelper"
    assert sub_info["channel"] == "chplay"
    assert sub_info["store_url"] == f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}"

    # 2. Full AppController cold start routed to LocalReleaseServer
    local_release_server.register_package(
        tag_name="v9.9.9",
        filename="UTHelper-9.9.9.apk",
        data=make_minimal_apk_bytes(version_code=9_009_009),
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
    )
    manifest = local_release_server.build_manifest(release_version="9.9.9")
    local_release_server.configure_latest_release(tag_name="v9.9.9", manifest=manifest)
    local_release_server.clear_requests()

    pu_mod = load_module("platform_utils")
    app_ctrl_mod = load_module("gui.app_controller")
    sys_sec_mod = load_module("gui.components.settings.system_section")
    priv_sec_mod = load_module("gui.components.settings.privacy_section")

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", True)
    monkeypatch.setattr(pu_mod, "IS_IOS", False)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", True)
    monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", True)
    monkeypatch.setattr(app_ctrl_mod, "detect_platform", lambda _page: None)
    _stub_app_controller_perimeter(monkeypatch)

    class _FakeUrlLauncher:
        async def launch_url(self, url: str, **_kwargs):
            await fake_page.launch_url(url)

    monkeypatch.setattr(app_ctrl_mod.ft, "UrlLauncher", _FakeUrlLauncher)

    _prepare_full_page(fake_page)
    controller = app_ctrl_mod.AppController(fake_page, force_visible=True)
    try:
        assert controller._distribution_profile.channel.value == "chplay"
        assert controller._update_coordinator is None
        assert controller._update_banner.visible is False

        # Navigate into Settings -> System & Privacy -> Terms
        sys_section = sys_sec_mod.build_system_section(controller.settings_view)
        sys_texts = _collect_text_values(sys_section)
        assert any("Google Play" in t for t in sys_texts)
        assert controller.settings_view._sw_auto_update.visible is False
        assert controller.settings_view._check_update_btn.visible is False
        assert "Force check update" not in _collect_text_values(controller.settings_view._test_panel)

        priv_sec_mod.build_privacy_section(controller.settings_view)
        assert controller.settings_view._dd_crash_reporting_consent.value in {"enabled", "disabled"}
        controller.settings_view._terms_policy_entry.on_click(None)
        assert len(fake_page.dialogs) == 1
        terms_body = "\n".join(_collect_text_values(fake_page.dialogs[0].content))
        assert "Google Play" in terms_body
        assert "GitHub để kiểm tra bản phát hành" not in terms_body

        # Click Play Store deep-link button
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}" in fake_page.launched_urls
        )

        time.sleep(0.25)
        assert local_release_server.request_count == 0
    finally:
        controller._on_disconnect(None)


# ---------------------------------------------------------------------------
# Scenario 2: Mobile apk_lts end-to-end upgrade
# ---------------------------------------------------------------------------


def test_t4_s02_mobile_apk_lts_end_to_end_upgrade(
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    local_release_server,
    android_bridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("apk_lts", android_package_id=DEFAULT_ANDROID_PACKAGE_ID)

    # Verify M2 Kotlin .apk.part fix and test unmasking on disk
    installer_kt = (
        WORKTREE_ROOT
        / "extensions"
        / "flet_uth_background_sync"
        / "flutter"
        / "flet_uth_background_sync"
        / "android"
        / "variants"
        / "apk_lts"
        / "kotlin"
        / "com"
        / "uthelper"
        / "backgroundsync"
        / "update"
        / "ApkUpdateInstaller.kt"
    ).read_text(encoding="utf-8")
    installer_test_kt = (
        WORKTREE_ROOT
        / "extensions"
        / "flet_uth_background_sync"
        / "flutter"
        / "flet_uth_background_sync"
        / "android"
        / "variants"
        / "apk_lts"
        / "test"
        / "kotlin"
        / "com"
        / "uthelper"
        / "backgroundsync"
        / "update"
        / "ApkUpdateInstallerFlowTest.kt"
    ).read_text(encoding="utf-8")
    assert not re.search(
        r"setPackageArchiveInfo\s*\([^)]*\.apk\.part",
        installer_test_kt,
        re.DOTALL,
    ), "ApkUpdateInstallerFlowTest.kt must not register .apk.part in ShadowPackageManager"
    assert "update-$releaseKey.apk.part" not in installer_test_kt

    prep_match = re.search(
        r"fun\s+prepareVerifiedUpdatePackage\b.*?\n\}",
        installer_kt,
        re.DOTALL,
    )
    prep_body = prep_match.group(0) if prep_match is not None else ""
    prep_returns_partial = (
        "partialApk" in prep_body.split("PreparedUpdatePackage(")[-1].split(")")[0]
        if "PreparedUpdatePackage(" in prep_body
        else True
    )
    verify_match = re.search(
        r"fun\s+verifyLocalAndOpen\b.*?\n\s*\}\s*\n\s*private\s+fun",
        installer_kt,
        re.DOTALL,
    )
    verify_body = (
        verify_match.group(0)
        if verify_match is not None
        else installer_kt[installer_kt.find("fun verifyLocalAndOpen") :]
    )
    rename_match = re.search(r"renameTo\s*\(", verify_body)
    parse_match = re.search(
        r"(?:readArchiveMetadata|getPackageArchiveInfo)\s*\(",
        verify_body,
    )
    assert parse_match is not None, "readArchiveMetadata/getPackageArchiveInfo call missing"
    if prep_returns_partial:
        assert rename_match is not None and rename_match.start() < parse_match.start(), (
            "M2 must rename .apk.part -> .apk BEFORE calling readArchiveMetadata/getPackageArchiveInfo()"
        )
    else:
        assert re.search(r"renameTo\s*\(", prep_body) is not None or (
            rename_match is not None and rename_match.start() < parse_match.start()
        )

    version = "2.6.0"
    apk_bytes = make_minimal_apk_bytes(
        b"apk-lts-universal-260",
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=f"UTHelper-{version}.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
    )
    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    android_bridge.permission_granted = True
    android_bridge.enforce_apk_suffix_on_archive_parse = True
    android_bridge.set_download_status_plan(["pending", "running", "completed"])

    pu_mod = load_module("platform_utils")
    dist_mod = load_module("core.distribution")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    upkg_mod = load_module("platform_utils.update_packages")
    and_mod = load_module("platform_utils.android_updates")
    app_ctrl_mod = load_module("gui.app_controller")
    sv_mod = load_module("gui.components.settings_view")

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", True)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", True)
    monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", True)

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    controller = app_ctrl_mod.AppController.__new__(app_ctrl_mod.AppController)
    controller.page = fake_page
    controller._distribution_profile = profile
    controller._android_background = android_bridge
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._diagnostic_recorder = None
    controller._unhandled_error_reporter = None
    controller._show_snackbar = lambda *_a, **_kw: None
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(disabled=False, content="", icon=None, visible=True)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)
    controller.settings_view = sv_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
        autostart_coordinator=SimpleNamespace(),
        distribution_profile=profile,
    )
    controller.settings_view.update = lambda: None

    events: list = []
    orig_on_event = controller._on_update_event

    def _sink(ev):
        events.append(ev)
        orig_on_event(ev)

    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        and_mod.AndroidPackageDownloader(
            android_bridge,
            controller._safe_run_task,
            lambda: controller._update_candidate.manifest.release_version,
            poll_interval_seconds=0.01,
        ),
        upkg_mod.DownloadedPackageVerifier(),
        and_mod.AndroidPackageLauncher(
            android_bridge,
            controller._safe_run_task,
            lambda: controller._update_candidate.manifest.release_version,
        ),
        models_mod.RuntimeTarget("android", "arm64", "sideload"),
        "2.5.1",
        _sink,
        automatic_enabled=False,
    )
    controller._update_coordinator = coordinator
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events)
            and controller._update_banner.visible is True
        )
        assert controller._update_text.value == f"Có bản cập nhật v{version}"

        controller._request_update_download()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events)
            and controller._update_ready is True
        )
        assert controller._update_btn.content == "Cài đặt"

        controller._request_update_download()
        assert _wait_until(lambda: len(fake_page.dialogs) == 1)
        confirm_btn = fake_page.dialogs[-1].actions[1]
        confirm_btn.on_click(None)

        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events)
            and controller._update_text.value == "Đã mở trình cài đặt"
        )
        assert controller.settings_view._update_status_text.value == "Đã mở trình cài đặt"
        assert any(c[0] == "install_update" for c in android_bridge.calls)
        assert list(android_bridge.verified_cache.glob("*.apk"))
        assert not list(android_bridge.verified_cache.glob("*.apk.part"))
    finally:
        coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# Scenario 3: Mobile apk_lts detach/reconnect + permission recovery + cache reuse
# ---------------------------------------------------------------------------


def test_t4_s03_mobile_apk_lts_permission_recovery_and_detach_reconnect(
    local_release_server,
    android_bridge,
    fake_page: FakePage,
) -> None:
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    upkg_mod = load_module("platform_utils.update_packages")
    and_mod = load_module("platform_utils.android_updates")

    version = "2.6.0"
    apk_bytes = make_minimal_apk_bytes(
        b"apk-reconnect-cache-test",
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=f"UTHelper-{version}.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
    )
    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    # First session: slow running progression so shutdown detaches before completion
    android_bridge.permission_granted = False
    android_bridge.set_download_status_plan(["pending", "running", "running", "running", "completed"])

    def _run_task(handler, *args):
        fut: Future = Future()
        fut.set_result(asyncio.run(handler(*args)))
        return fut

    events_1: list = []
    cand_ver = {"v": version}
    coord_1 = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        and_mod.AndroidPackageDownloader(
            android_bridge,
            _run_task,
            lambda: cand_ver["v"],
            poll_interval_seconds=0.05,
        ),
        upkg_mod.DownloadedPackageVerifier(),
        and_mod.AndroidPackageLauncher(android_bridge, _run_task, lambda: cand_ver["v"]),
        models_mod.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events_1.append,
        automatic_enabled=False,
    )
    coord_1.check_now()
    assert _wait_until(
        lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events_1)
    )
    coord_1.request_download()
    assert _wait_until(
        lambda: any(e.kind is coord_mod.UpdateEventKind.DOWNLOAD_PROGRESS for e in events_1)
    )
    assert coord_1.shutdown(timeout_seconds=3.0) is True
    assert not any(c[0] == "cancel_update_download" for c in android_bridge.calls)

    # Second session: new coordinator detects pending OS job and resumes without duplicate start
    events_2: list = []
    coord_2 = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        and_mod.AndroidPackageDownloader(
            android_bridge,
            _run_task,
            lambda: cand_ver["v"],
            poll_interval_seconds=0.01,
        ),
        upkg_mod.DownloadedPackageVerifier(),
        and_mod.AndroidPackageLauncher(android_bridge, _run_task, lambda: cand_ver["v"]),
        models_mod.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events_2.append,
        automatic_enabled=False,
    )
    try:
        coord_2.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events_2)
        )
        start_calls = [c for c in android_bridge.calls if c[0] == "start_update_download"]
        # Re-attached job may query start/status idempotently without creating duplicate DownloadManager IDs
        assert len(android_bridge._jobs) == 1 and len(start_calls) <= 2

        # 1st install hits permission_required
        coord_2.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind is coord_mod.UpdateEventKind.INSTALL_PERMISSION_REQUIRED for e in events_2
            )
        )
        # External DownloadManager file vanishes before user returns from Android Settings
        for file_path in android_bridge.external_downloads.iterdir():
            file_path.unlink()
        assert list(android_bridge.verified_cache.glob("*.apk"))

        # Grant permission & retry -> cached verified_updates/*.apk succeeds
        android_bridge.permission_granted = True
        coord_2.confirm_install()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events_2)
        )
    finally:
        coord_2.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# Scenario 4: Windows exe_lts HTTP 503 + 206 resume + verify + UAC/lock-safe launch
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsShimEnv requires Windows")
def test_t4_s04_windows_exe_lts_range_resume_verify_and_relaunch(
    tmp_path: Path,
    local_release_server,
    windows_shim_env,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    win_mod = load_module("platform_utils.windows_update")
    pu_mod = load_module("platform_utils")

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", False)

    version = "2.5.2"
    filename = f"UTHelper-{version}.msi"
    msi_path = windows_shim_env.make_msi_file(tmp_path / "src" / filename, payload=os.urandom(16384))
    msi_bytes = msi_path.read_bytes()

    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=filename,
        data=msi_bytes,
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        signature_kind="self-signed-pinned",
    )
    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    # Inject transient 503 on /releases/latest and 50% stream drop on MSI download
    local_release_server.set_fault("/repos/Chouwzi/UTHelper/releases/latest", status_sequence=[503])
    local_release_server.set_fault(
        f"/Chouwzi/UTHelper/releases/download/v{version}/{filename}",
        truncate_sequence=[8192],
    )

    windows_shim_env.set_ps_mode("self_signed", version=version)
    windows_shim_env.set_msi_behavior(exit_code=0, delay_sec=2.5)
    windows_shim_env.clear_calls()

    profile = dist_mod.build_distribution_profile(dist_mod.DistributionChannel.EXE_LTS)
    app_ctrl_mod = load_module("gui.app_controller")
    controller = app_ctrl_mod.AppController.__new__(app_ctrl_mod.AppController)
    controller.page = fake_page
    controller._distribution_profile = profile
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._diagnostic_recorder = None
    controller._unhandled_error_reporter = None
    controller._show_snackbar = lambda *_a, **_kw: None
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(disabled=False, content="", icon=None, visible=True)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(_check_update_btn=None, set_update_status=lambda *_a, **_kw: None)

    events: list = []
    orig = controller._on_update_event

    def _sink(ev):
        events.append(ev)
        orig(ev)

    cache_dir = tmp_path / "cache"
    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=cache_dir),
        win_mod.WindowsPackageVerifier(timeout_seconds=10.0),
        win_mod.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory,
            acknowledgement_seconds=1.0,
        ),
        models_mod.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        _sink,
        automatic_enabled=False,
    )
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events)
        )
        coordinator.request_download()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events)
        )
        assert (cache_dir / filename).read_bytes() == msi_bytes
        assert not (cache_dir / f"{filename}.part").exists()
        assert any(req.get("range") == "bytes=8192-" for req in local_release_server.requests)

        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events)
        )
        calls = windows_shim_env.read_calls()
        msi_calls = [c for c in calls if c["tool"] == "msiexec.exe"]
        assert len(msi_calls) == 1
        assert "/passive" in msi_calls[0]["argv"] and "/norestart" in msi_calls[0]["argv"]
        # Shutdown after INSTALL_LAUNCHED must succeed quickly without killing detached msiexec.exe
        assert coordinator.shutdown(timeout_seconds=3.0) is True
        assert any(
            p.poll() is None for p in windows_shim_env._spawned_processes
        ), "Detached msiexec.exe must not be killed prematurely by coordinator shutdown"
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


# ---------------------------------------------------------------------------
# Scenario 5: Windows exe_lts cold PS timeout + UAC denial (1223) + Debug UI sync
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsShimEnv requires Windows")
def test_t4_s05_windows_exe_lts_uac_denial_and_cold_timeout_recovery(
    tmp_path: Path,
    local_release_server,
    windows_shim_env,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    win_mod = load_module("platform_utils.windows_update")
    pu_mod = load_module("platform_utils")
    app_ctrl_mod = load_module("gui.app_controller")
    sv_mod = load_module("gui.components.settings_view")

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", False)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", False)
    monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", False)

    version = "2.5.2"
    filename = f"UTHelper-{version}.msi"
    msi_path = windows_shim_env.make_msi_file(tmp_path / "src" / filename, payload=b"msi" * 1024)
    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=filename,
        data=msi_path.read_bytes(),
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        signature_kind="self-signed-pinned",
    )
    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    # The simulated MSI probe must report the release version under test (2.5.2),
    # otherwise the verifier correctly rejects it as an "MSI identity mismatch".
    windows_shim_env.set_ps_mode("valid", version=version)
    # Cold PS timeout on first probe, then self_signed passes on subsequent probes
    windows_shim_env.set_ps_sequence(
        ["timeout", "self_signed", "self_signed", "self_signed", "self_signed", "self_signed"]
    )
    windows_shim_env.set_msi_behavior(exit_code=1223, delay_sec=0.05)

    profile = dist_mod.build_distribution_profile(dist_mod.DistributionChannel.EXE_LTS)
    controller = app_ctrl_mod.AppController.__new__(app_ctrl_mod.AppController)
    controller.page = fake_page
    controller._distribution_profile = profile
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._diagnostic_recorder = None
    controller._unhandled_error_reporter = None
    controller._show_snackbar = lambda *_a, **_kw: None
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(disabled=False, content="", icon=None, visible=True)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)

    events: list = []
    orig = controller._on_update_event

    def _sink(ev):
        events.append(ev)
        orig(ev)

    cache_dir = tmp_path / "cache"
    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=cache_dir),
        win_mod.WindowsPackageVerifier(timeout_seconds=1.2),
        win_mod.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory,
            acknowledgement_seconds=1.5,
        ),
        models_mod.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        _sink,
        automatic_enabled=False,
    )
    controller._update_coordinator = coordinator
    controller.settings_view = sv_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
        autostart_coordinator=SimpleNamespace(),
        on_check_update=controller._check_updates_now,
        on_update_action=controller._request_update_download,
        on_cancel_update=controller._cancel_update_download,
        distribution_profile=profile,
    )
    controller.settings_view.update = lambda: None

    try:
        # 1. Debug "Force check update" triggers check & must sync _debug_update_text
        controller.settings_view._do_force_check_update()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events)
        )
        assert _wait_until(
            lambda: bool(controller.settings_view._debug_update_text.value),
            timeout=3.0,
        )

        # 2. First download hits cold PowerShell timeout -> cache must be preserved for immediate retry
        local_release_server.clear_requests()
        coordinator.request_download()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.FAILED for e in events),
            timeout=6.0,
        )
        assert (cache_dir / filename).is_file(), (
            "M2 must preserve downloaded binary across transient PowerShell verification timeout"
        )

        # 3. Retry succeeds verification, then UAC denial (1223) triggers INSTALL_FAILED without destroying window
        events.clear()
        coordinator.request_download()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events),
            timeout=6.0,
        )
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events),
            timeout=6.0,
        )
        assert fake_page.window.destroyed is False
        assert _wait_until(lambda: controller._update_btn.content == "Thử lại")
        assert controller._update_btn.disabled is False
        assert controller._update_text.value != "Không mở được trình cài đặt. Hãy thử lại."
    finally:
        coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# Scenario 6: msstore MSIX cold start + package_msix.ps1 + makeappx.exe unpack
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging and package identity checks require Windows")
def test_t4_s06_msstore_msix_cold_start_and_package_verification(
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    local_release_server,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    distribution_channel_stager("msstore", store_product_id="9NBLGGH4NNS1")

    dist_mod = load_module("core.distribution")
    pu_mod = load_module("platform_utils")
    autostart_mod = load_module("platform_utils.autostart")
    win_update_mod = load_module("platform_utils.windows_update")
    win_notif_mod = load_module("notifiers.windows")
    app_ctrl_mod = load_module("gui.app_controller")
    sys_sec_mod = load_module("gui.components.settings.system_section")

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", False)
    monkeypatch.setattr(pu_mod, "IS_IOS", False)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", False)
    monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", False)
    monkeypatch.setattr(autostart_mod, "has_package_identity", lambda: True)
    monkeypatch.setattr(win_update_mod, "has_package_identity", lambda: True)
    monkeypatch.setattr(win_notif_mod, "_packaged_aumid", lambda: "Chouwzi.UTHelper_abc!UTHelper")
    monkeypatch.setattr(
        win_notif_mod.WindowsNotifier,
        "_ensure_shortcut",
        lambda _self: (_ for _ in ()).throw(AssertionError("MSIX must not create Start Menu shortcut")),
    )
    monkeypatch.setattr(app_ctrl_mod, "detect_platform", lambda _page: None)
    _stub_app_controller_perimeter(monkeypatch)

    class _FakeUrlLauncher:
        async def launch_url(self, url: str, **_kwargs):
            await fake_page.launch_url(url)

    monkeypatch.setattr(app_ctrl_mod.ft, "UrlLauncher", _FakeUrlLauncher)

    assert type(autostart_mod.create_autostart_service(platform_name="win32")).__name__ == "StartupTaskAutostartBackend"
    notifier = win_notif_mod.WindowsNotifier(start_scheduler=False)
    assert notifier.is_packaged is True
    assert notifier.aumid == "Chouwzi.UTHelper_abc!UTHelper"
    assert win_update_mod.detect_windows_runtime_target().install_channel == "msix"

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: True,
    )
    assert profile.channel is dist_mod.DistributionChannel.MSSTORE
    assert profile.supports_github_self_update is False

    cfg_mod = load_module("config")
    monkeypatch.setattr(cfg_mod, "is_windows", lambda: True)
    monkeypatch.setattr(cfg_mod, "is_android", lambda: False)
    data_dir = cfg_mod.get_data_dir()
    assert data_dir == isolated_runtime_env["appdata"] / "UTHelper"
    assert not data_dir.is_relative_to(isolated_runtime_env["assets"])

    cold_bundle = isolated_runtime_env["root"] / "msix_bundle_ro"
    cold_assets = cold_bundle / "assets"
    stage_distribution_channel_asset(
        cold_assets,
        "msstore",
        store_product_id="9NBLGGH4NNS1",
    )
    cold_appdata = isolated_runtime_env["root"] / "msix_appdata"
    cold_proc = run_isolated_python(
        (
            "import json\n"
            "import config\n"
            "from core.distribution import resolve_distribution_channel\n"
            "p = resolve_distribution_channel(is_windows=True, is_android=False, is_ios=False, has_package_identity_fn=lambda: True)\n"
            "print(json.dumps({'channel': p.channel.value, 'data_dir': str(config.get_data_dir()), 'config_parent': str(config.CONFIG_FILE.parent)}))\n"
        ),
        env_overrides={
            "APPDATA": str(cold_appdata),
            "FLET_ASSETS_DIR": str(cold_assets),
        },
    )
    assert cold_proc.returncode == 0, cold_proc.stderr
    cold_info = json.loads(cold_proc.stdout.strip().splitlines()[-1])
    assert cold_info["channel"] == "msstore"
    assert Path(cold_info["data_dir"]) == cold_appdata / "UTHelper"
    assert Path(cold_info["config_parent"]) == cold_appdata / "UTHelper"
    assert not Path(cold_info["data_dir"]).is_relative_to(cold_bundle)

    local_release_server.clear_requests()
    _prepare_full_page(fake_page)
    controller = app_ctrl_mod.AppController(fake_page, force_visible=True)
    try:
        assert controller._update_coordinator is None
        sys_sec_mod.build_system_section(controller.settings_view)
        assert controller.settings_view._store_link_btn.visible is True
        controller.settings_view._store_link_btn.on_click(None)
        assert _wait_until(
            lambda: "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1" in fake_page.launched_urls
        )
        time.sleep(0.2)
        assert local_release_server.request_count == 0
    finally:
        controller._on_disconnect(None)

    # Real package_msix.ps1 + makeappx.exe unpack compliance check
    makeappx = next(
        iter(
            sorted(
                Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
                .joinpath(r"Windows Kits\10\bin")
                .rglob("x64/makeappx.exe"),
                reverse=True,
            )
        ),
        None,
    )
    assert makeappx is not None, "makeappx.exe is required for Scenario 6"

    build_root = WORKTREE_ROOT / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="e2e-t4-msix-", dir=build_root))
    bundle_dir = temp_root / "bundle"
    unpack_dir = temp_root / "unpacked"
    msix_out = temp_root / "UTHelper-Store.msix"
    ps1_script = WORKTREE_ROOT / "scripts" / "package_msix.ps1"
    try:
        shutil.rmtree(temp_root, ignore_errors=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 64)
        (bundle_dir / "UTHelperAutostart.exe").write_bytes(b"MZ" + b"\x00" * 64)
        stage_distribution_channel_asset(
            bundle_dir / "assets",
            "msstore",
            store_product_id="9NBLGGH4NNS1",
        )

        param_probe = subprocess.run(
            [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                f"(Get-Command '{ps1_script}').Parameters.Keys -join ','",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        params = {p.strip().lower(): p.strip() for p in param_probe.stdout.split(",") if p.strip()}
        assert any(k in params for k in ("identityname", "packagename")), (
            "M4 must parameterize Identity Name on scripts/package_msix.ps1 for Microsoft Store"
        )

        cmd = [
            "powershell",
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(ps1_script),
            "-BundleDir",
            str(bundle_dir),
            "-Version",
            "2.5.1.0",
            "-Publisher",
            "CN=UTHelperStoreTest",
            "-Output",
            str(msix_out),
        ]
        for cand_key, val in (
            ("identityname", "Chouwzi.UTHelperStore"),
            ("packagename", "Chouwzi.UTHelperStore"),
            ("channel", "msstore"),
            ("storeproductid", "9NBLGGH4NNS1"),
            ("productid", "9NBLGGH4NNS1"),
        ):
            if cand_key in params:
                cmd.extend([f"-{params[cand_key]}", val])

        pack_proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        assert pack_proc.returncode == 0, pack_proc.stderr or pack_proc.stdout
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(msix_out), "/d", str(unpack_dir), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr or unpack_proc.stdout

        manifest_xml = (unpack_dir / "AppxManifest.xml").read_text(encoding="utf-8")
        assert 'FileSystemWriteVirtualization="disabled"' not in manifest_xml, (
            "MSIX AppxManifest.xml must not disable FileSystemWriteVirtualization"
        )
        assert 'RegistryWriteVirtualization="disabled"' not in manifest_xml, (
            "MSIX AppxManifest.xml must not disable RegistryWriteVirtualization"
        )
        manifest_root = ET.fromstring(manifest_xml)
        ns = {
            "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
            "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
            "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
        }
        identity = manifest_root.find("m:Identity", ns)
        assert identity is not None
        assert identity.attrib.get("Name") == "Chouwzi.UTHelperStore"
        assert identity.attrib.get("Publisher") == "CN=UTHelperStoreTest"
        assert manifest_root.find(".//rescap:Capability[@Name='runFullTrust']", ns) is not None
        assert manifest_root.find(".//m:Capability[@Name='internetClient']", ns) is not None
        startup = manifest_root.find(".//desktop:StartupTask", ns)
        assert startup is not None and startup.attrib.get("TaskId") == "UTHelperStartup"

        with Image.open(unpack_dir / "Assets" / "Square44x44Logo.png") as im44:
            assert im44.size == (44, 44)
        with Image.open(unpack_dir / "Assets" / "StoreLogo.png") as im50:
            assert im50.size == (50, 50)
        with Image.open(unpack_dir / "Assets" / "Square150x150Logo.png") as im150:
            assert im150.size == (150, 150)
        with Image.open(unpack_dir / "Assets" / "Wide310x150Logo.png") as im310:
            assert im310.size == (310, 150)

        channel_files = list(unpack_dir.rglob("distribution-channel.json"))
        assert channel_files
        assert json.loads(channel_files[0].read_text(encoding="utf-8"))["channel"] == "msstore"

        verify_msix = WORKTREE_ROOT / "scripts" / "verify_msix_release.ps1"
        if verify_msix.is_file():
            v_param_probe = subprocess.run(
                [
                    "powershell",
                    "-NoLogo",
                    "-NoProfile",
                    "-Command",
                    f"(Get-Command '{verify_msix}').Parameters.Keys -join ','",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                stdin=subprocess.DEVNULL,
            )
            assert v_param_probe.returncode == 0, v_param_probe.stderr
            v_params = {
                p.strip().lower(): p.strip()
                for p in v_param_probe.stdout.split(",")
                if p.strip()
            }
            v_cmd = [
                "powershell",
                "-NoLogo",
                "-NoProfile",
                "-File",
                str(verify_msix),
            ]
            path_param = next(
                (v_params[k] for k in ("msixpath", "packagepath", "path", "msix") if k in v_params),
                "MsixPath",
            )
            v_cmd.extend([f"-{path_param}", str(msix_out)])
            for cand_key, val in (
                ("channel", "msstore"),
                ("identityname", "Chouwzi.UTHelperStore"),
                ("packagename", "Chouwzi.UTHelperStore"),
                ("publisher", "CN=UTHelperStoreTest"),
                ("version", "2.5.1.0"),
                ("storeproductid", "9NBLGGH4NNS1"),
                ("productid", "9NBLGGH4NNS1"),
            ):
                if cand_key in v_params:
                    v_cmd.extend([f"-{v_params[cand_key]}", val])
            verify_proc = subprocess.run(v_cmd, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            assert verify_proc.returncode == 0, verify_proc.stderr or verify_proc.stdout
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Scenario 7: CH Play .aab release compliance pipeline + fail-closed mutations
# ---------------------------------------------------------------------------


def test_t4_s07_chplay_aab_release_compliance_pipeline(tmp_path: Path) -> None:
    script_path = WORKTREE_ROOT / "scripts" / "verify_android_release.py"
    help_proc = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        timeout=15,
        stdin=subprocess.DEVNULL,
    )
    assert help_proc.returncode == 0
    assert "--aab" in help_proc.stdout or "--channel" in help_proc.stdout, (
        "M3 must add CH Play .aab verification support to scripts/verify_android_release.py"
    )

    sdk_root = _make_fake_android_sdk(tmp_path / "sdk")
    env = os.environ.copy()
    env["ANDROID_HOME"] = str(sdk_root)

    def _run_verify(aab_path: Path, out_name: str) -> subprocess.CompletedProcess[str]:
        cmd = [
            sys.executable,
            str(script_path),
            "--aab",
            str(aab_path),
            "--channel",
            "chplay",
            "--version",
            "2.5.1",
            "--build-number",
            "2005001",
            "--package-id",
            DEFAULT_ANDROID_PACKAGE_ID,
            "--commit-sha",
            "a" * 40,
            "--workflow-run-id",
            "999001",
            "--output",
            str(tmp_path / out_name),
        ]
        if "--certificate-sha256" in help_proc.stdout:
            cmd.extend(["--certificate-sha256", DEFAULT_TRUSTED_SIGNER_SHA256])
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)

    good_manifest = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{DEFAULT_ANDROID_PACKAGE_ID}">\n'
        '  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35" />\n'
        '  <uses-permission android:name="android.permission.INTERNET" />\n'
        '  <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />\n'
        '  <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />\n'
        '  <application android:allowBackup="false">\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver" />\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver" />\n'
        '    <receiver android:name="com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.RescheduleReceiver" />\n'
        '    <receiver android:name="com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver" />\n'
        "  </application>\n"
        "</manifest>\n"
    )
    channel_doc = {
        "schema_version": 1,
        "channel": "chplay",
        "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
    }
    good_aab = make_synthetic_aab(
        tmp_path / "good" / "UTHelper-2.5.1.aab",
        abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;", "Lcom/uthelper/backgroundsync/MoodleSyncWorker;"),
        manifest_xml=good_manifest,
        distribution_channel=channel_doc,
    )
    ok_proc = _run_verify(good_aab, "good.json")
    assert ok_proc.returncode == 0, ok_proc.stderr or ok_proc.stdout

    # Mutation 1: 4 KB ELF alignment
    bad_elf_aab = make_synthetic_aab(
        tmp_path / "bad_elf" / "UTHelper-2.5.1.aab",
        abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
        p_align=0x1000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=good_manifest,
        distribution_channel=channel_doc,
    )
    assert _run_verify(bad_elf_aab, "bad_elf.json").returncode != 0

    # Mutation 2: Leaked REQUEST_INSTALL_PACKAGES
    bad_perm_manifest = good_manifest.replace(
        "<application",
        '  <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n  <application',
    )
    bad_perm_aab = make_synthetic_aab(
        tmp_path / "bad_perm" / "UTHelper-2.5.1.aab",
        abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=bad_perm_manifest,
        distribution_channel=channel_doc,
    )
    assert _run_verify(bad_perm_aab, "bad_perm.json").returncode != 0

    # Mutation 3: Leaked ApkUpdateInstaller DEX class
    bad_dex_aab = make_synthetic_aab(
        tmp_path / "bad_dex" / "UTHelper-2.5.1.aab",
        abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;",),
        manifest_xml=good_manifest,
        distribution_channel=channel_doc,
    )
    assert _run_verify(bad_dex_aab, "bad_dex.json").returncode != 0


# ---------------------------------------------------------------------------
# Scenario 8: Dual-track release isolation (7-asset LTS vs Store .aab/.msix)
# ---------------------------------------------------------------------------


def test_t4_s08_dual_track_release_isolation_7_asset_lts(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    store_dir = tmp_path / "store_artifacts"
    store_dir.mkdir(parents=True, exist_ok=True)

    # Generate runtime metadata & diagnostics config via real CLIs
    runtime_ver = tmp_path / "release-version"
    diag_cfg = tmp_path / "diagnostics-config.json"
    meta_proc = subprocess.run(
        [
            sys.executable,
            str(WORKTREE_ROOT / "scripts" / "release_metadata.py"),
            "--pyproject",
            str(WORKTREE_ROOT / "pyproject.toml"),
            "--runtime-output",
            str(runtime_ver),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert meta_proc.returncode == 0, meta_proc.stderr
    version = runtime_ver.read_text(encoding="utf-8").strip()

    diag_proc = subprocess.run(
        [
            sys.executable,
            str(WORKTREE_ROOT / "scripts" / "generate_public_runtime_config.py"),
            "--sentry-dsn",
            "",
            "--output",
            str(diag_cfg),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert diag_proc.returncode == 0, diag_proc.stderr
    assert json.loads(diag_cfg.read_text(encoding="utf-8"))["schema_version"] == 1

    make_synthetic_lts_release_dir(release_dir, version, evidence_dir=evidence_dir)
    # Remove pre-generated manifest/checksums so the real CLI pipeline generates & verifies them
    (release_dir / "release-manifest.json").unlink()
    (release_dir / "SHA256SUMS").unlink()
    make_synthetic_aab(store_dir / f"UTHelper-{version}.aab", distribution_channel={"channel": "chplay"})
    (store_dir / f"UTHelper-{version}.msix").write_bytes(b"PK\x03\x04msix")

    # Regenerate release-manifest.json and verify exact 7-asset inventory via CLI
    manifest_out = release_dir / "release-manifest.json"
    gen_proc = subprocess.run(
        [
            sys.executable,
            str(WORKTREE_ROOT / "scripts" / "generate_release_manifest.py"),
            "--version",
            version,
            "--repository",
            "Chouwzi/UTHelper",
            "--release-dir",
            str(release_dir),
            "--evidence-dir",
            str(evidence_dir),
            "--minimum-supported-version",
            version,
            "--output",
            str(manifest_out),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert gen_proc.returncode == 0, gen_proc.stderr

    inv_proc = subprocess.run(
        [
            sys.executable,
            str(WORKTREE_ROOT / "scripts" / "release_inventory.py"),
            "--release-dir",
            str(release_dir),
            "--evidence-dir",
            str(evidence_dir),
            "--version",
            version,
            "--repository",
            "Chouwzi/UTHelper",
            "--manifest",
            str(manifest_out),
            "--write-checksums",
            str(release_dir / "SHA256SUMS"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert inv_proc.returncode == 0, inv_proc.stderr
    assert len(list(release_dir.iterdir())) == 7

    # Mixing either Store artifact into LTS release_dir must fail closed
    for store_file in (f"UTHelper-{version}.aab", f"UTHelper-{version}.msix"):
        mixed_path = release_dir / store_file
        shutil.copy2(store_dir / store_file, mixed_path)
        bad_inv = subprocess.run(
            [
                sys.executable,
                str(WORKTREE_ROOT / "scripts" / "release_inventory.py"),
                "--release-dir",
                str(release_dir),
                "--evidence-dir",
                str(evidence_dir),
                "--version",
                version,
                "--repository",
                "Chouwzi/UTHelper",
                "--manifest",
                str(manifest_out),
                "--checksums",
                str(release_dir / "SHA256SUMS"),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        mixed_path.unlink()
        assert bad_inv.returncode != 0
