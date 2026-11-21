"""Tier 3 opaque-box pairwise combinatorial tests across F1-F13.

All M1-M4 target modules are imported lazily via ``harness.load_module(...)``
so ``pytest --collect-only`` never raises ``ImportError`` regardless of milestone
development order.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.parse
from unittest.mock import Mock
import xml.etree.ElementTree as ET
import zipfile

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DEFAULT_MSI_UPGRADE_CODE,
    DEFAULT_SIGNER_SUBJECT,
    DEFAULT_TRUSTED_SIGNER_SHA256,
    FakePage,
    WORKTREE_ROOT,
    load_module,
    make_minimal_apk_bytes,
    make_synthetic_aab,
    make_synthetic_apk,
    make_synthetic_lts_release_dir,
    run_isolated_python,
    sha256_hex,
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


def _collect_controls(control) -> list:
    ft = load_module("flet")
    items = [control]
    content = getattr(control, "content", None)
    if isinstance(content, ft.BaseControl):
        items.extend(_collect_controls(content))
    for child in getattr(control, "controls", None) or ():
        items.extend(_collect_controls(child))
    return items


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _make_controller_stub(
    fake_page: FakePage,
    profile,
    *,
    android_bridge=None,
    update_events: list | None = None,
):
    app_controller_mod = load_module("gui.app_controller")
    AppController = app_controller_mod.AppController
    controller = AppController.__new__(AppController)
    controller.page = fake_page
    controller._distribution_profile = profile
    controller._android_background = android_bridge
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._diagnostic_recorder = None
    controller._unhandled_error_reporter = None
    controller.settings_view = SimpleNamespace(
        _check_update_btn=None,
        set_update_status=lambda *_a, **_kw: None,
    )
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(disabled=False, content="", icon=None, visible=True)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)
    controller._show_snackbar = lambda *_a, **_kw: None

    if update_events is not None:
        orig_on_event = controller._on_update_event

        def _capture(event):
            update_events.append(event)
            orig_on_event(event)

        controller._on_update_event = _capture
    return controller


def _make_fake_android_sdk(sdk_root: Path) -> Path:
    """Create minimal ``apksigner`` and ``apkanalyzer`` shims for offline CLI verification."""
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
# P01: Channel x AUTO_UPDATE_ENABLED x AppController startup HTTP requests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "auto_update_enabled", "expect_http_hit"),
    [
        ("chplay", True, False),
        ("chplay", False, False),
        ("msstore", True, False),
        ("msstore", False, False),
        ("apk_lts", True, True),
        ("apk_lts", False, False),
        ("exe_lts", True, True),
        ("exe_lts", False, False),
    ],
)
def test_t3_p01_channel_x_auto_update_x_startup_http_requests(
    channel: str,
    auto_update_enabled: bool,
    expect_http_hit: bool,
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    local_release_server,
    android_bridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager(channel, store_product_id="9NBLGGH4NNS1")
    dist_mod = load_module("core.distribution")
    cfg_mod = load_module("config")
    pu_mod = load_module("platform_utils")
    win_update_mod = load_module("platform_utils.windows_update")

    is_win = channel in {"msstore", "exe_lts"}
    is_android = channel in {"chplay", "apk_lts"}
    monkeypatch.setattr(pu_mod, "IS_WINDOWS", is_win)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", is_android)
    monkeypatch.setattr(pu_mod, "IS_IOS", False)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", is_android)
    monkeypatch.setattr(
        win_update_mod,
        "has_package_identity",
        lambda: channel == "msstore",
    )
    monkeypatch.setattr(win_update_mod, "read_install_channel", lambda: "msi" if is_win else None)
    monkeypatch.setattr(win_update_mod, "_architecture", lambda: "x64")
    monkeypatch.setattr(cfg_mod.settings, "AUTO_UPDATE_ENABLED", auto_update_enabled)

    apk_bytes = make_minimal_apk_bytes(version_code=9_009_009, signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256)
    local_release_server.register_package(
        tag_name="v9.9.9",
        filename="UTHelper-9.9.9.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
    )
    local_release_server.register_package(
        tag_name="v9.9.9",
        filename="UTHelper-9.9.9.msi",
        data=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1payload",
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
    )
    manifest = local_release_server.build_manifest(release_version="9.9.9")
    local_release_server.configure_latest_release(tag_name="v9.9.9", manifest=manifest)
    local_release_server.clear_requests()

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=is_win,
        is_android=is_android,
        is_ios=False,
        has_package_identity_fn=lambda: channel == "msstore",
        is_packaged=True,
    )
    events = []
    controller = _make_controller_stub(
        fake_page,
        profile,
        android_bridge=android_bridge if is_android else None,
        update_events=events,
    )
    coordinator = controller._create_update_coordinator()
    try:
        if channel in {"chplay", "msstore"}:
            assert coordinator is None
            time.sleep(0.2)
            assert local_release_server.request_count == 0
        else:
            assert coordinator is not None
            coordinator.start()
            if expect_http_hit:
                assert _wait_until(lambda: local_release_server.request_count >= 1, timeout=4.0)
                assert any(
                    req["path"].endswith("/releases/latest")
                    for req in local_release_server.requests
                )
            else:
                time.sleep(0.25)
                assert local_release_server.request_count == 0
    finally:
        if coordinator is not None:
            coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# P02: Channel x CRASH_REPORTING_CONSENT x DiagnosticsRuntime + Snapshot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "consent"),
    [
        ("chplay", "enabled"),
        ("chplay", "disabled"),
        ("apk_lts", "disabled"),
        ("msstore", "enabled"),
        ("exe_lts", "disabled"),
        ("source", "enabled"),
    ],
)
def test_t3_p02_channel_x_crash_consent_x_diagnostics_and_snapshot(
    channel: str,
    consent: str,
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager(channel, store_product_id="9NBLGGH4NNS1")
    dist_mod = load_module("core.distribution")
    cfg_mod = load_module("config")
    diag_mod = load_module("diagnostics.runtime")
    form_mod = load_module("gui.view_models.settings_form")

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=channel in {"msstore", "exe_lts"},
        is_android=channel in {"chplay", "apk_lts"},
        is_ios=False,
        has_package_identity_fn=lambda: channel == "msstore",
        is_packaged=channel != "source",
    )
    assert profile.channel.value == channel

    monkeypatch.setattr(cfg_mod.settings, "CRASH_REPORTING_CONSENT", consent)
    monkeypatch.setattr(cfg_mod.settings, "AUTO_UPDATE_ENABLED", True)

    snapshot = form_mod.SettingsFormSnapshot.from_settings(cfg_mod.settings)
    assert snapshot.crash_reporting_consent == consent
    settings_dict = snapshot.to_settings_values()
    assert settings_dict["CRASH_REPORTING_CONSENT"] == consent
    assert settings_dict["AUTO_UPDATE_ENABLED"] is True
    round_trip = form_mod.SettingsFormSnapshot.from_form_values(
        {
            "crash_reporting_consent": settings_dict["CRASH_REPORTING_CONSENT"],
            "auto_update_enabled": settings_dict["AUTO_UPDATE_ENABLED"],
        }
    )
    assert round_trip.crash_reporting_consent == consent
    assert round_trip.auto_update_enabled is True

    runtime = diag_mod.create_default_runtime(
        isolated_runtime_env["flet_data"],
        development=(channel == "source"),
    )
    assert runtime._consent_provider().value == consent


# ---------------------------------------------------------------------------
# P03: Channel x UI_IS_MOBILE x Settings sections + Terms dialog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "ui_is_mobile"),
    [
        ("chplay", True),
        ("apk_lts", True),
        ("msstore", False),
        ("exe_lts", False),
        ("source", True),
        ("source", False),
    ],
)
def test_t3_p03_channel_x_ui_mobile_x_settings_and_terms(
    channel: str,
    ui_is_mobile: bool,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    pu_mod = load_module("platform_utils")
    sv_mod = load_module("gui.components.settings_view")
    sys_sec_mod = load_module("gui.components.settings.system_section")
    priv_sec_mod = load_module("gui.components.settings.privacy_section")
    terms_mod = load_module("gui.components.terms_dialog")

    monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", ui_is_mobile)
    monkeypatch.setattr(pu_mod, "IS_MOBILE", ui_is_mobile)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", ui_is_mobile)
    monkeypatch.setattr(pu_mod, "IS_WINDOWS", not ui_is_mobile)

    profile = dist_mod.build_distribution_profile(
        dist_mod.DistributionChannel(channel),
        store_product_id="9NBLGGH4NNS1" if channel == "msstore" else None,
    )
    view = sv_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
        autostart_coordinator=SimpleNamespace(),
        distribution_profile=profile,
    )
    sys_section = sys_sec_mod.build_system_section(view)
    sys_controls = _collect_controls(sys_section)
    sys_texts = _collect_text_values(sys_section)
    debug_texts = _collect_text_values(view._test_panel)

    if ui_is_mobile:
        assert view._sw_bg_check in sys_controls
        assert view._sw_start_with_windows not in sys_controls
    else:
        assert view._sw_start_with_windows in sys_controls
        assert view._sw_bg_check not in sys_controls

    if channel in {"chplay", "msstore"}:
        assert view._sw_auto_update not in sys_controls
        assert view._check_update_btn not in sys_controls
        assert "Force check update" not in debug_texts
        assert view._store_link_btn in sys_controls
        assert view._store_link_btn.visible is True
    else:
        assert view._sw_auto_update in sys_controls
        assert view._check_update_btn in sys_controls
        assert "Force check update" in debug_texts

    priv_section = priv_sec_mod.build_privacy_section(view)
    priv_tile = priv_section.content
    priv_tile.update = lambda: None
    priv_tile.on_change(SimpleNamespace(control=priv_tile, data="true"))
    priv_controls = _collect_controls(priv_section)
    assert view._dd_crash_reporting_consent in priv_controls
    assert view._terms_policy_entry in priv_controls

    terms_dlg = terms_mod.build_terms_dialog(fake_page, profile=profile)
    terms_texts = "\n".join(_collect_text_values(terms_dlg.content))
    if channel == "chplay":
        assert "Google Play" in terms_texts
        assert "GitHub để kiểm tra bản phát hành" not in terms_texts
        assert any("Google Play" in t for t in sys_texts)
    elif channel == "msstore":
        assert "Microsoft Store" in terms_texts
        assert "GitHub để kiểm tra bản phát hành" not in terms_texts
        assert any("Microsoft Store" in t for t in sys_texts)
    else:
        assert "GitHub" in terms_texts


# ---------------------------------------------------------------------------
# P04: Store channel x package/product identifier x deep link fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "pkg_id", "product_id", "fail_scheme", "expected_launched"),
    [
        (
            "chplay",
            "com.uthelper.play",
            None,
            None,
            ["market://details?id=com.uthelper.play"],
        ),
        (
            "chplay",
            "com.evil/../../inject",
            None,
            "market",
            [
                "market://details?id=com.uthelper.uthelper",
                "https://play.google.com/store/apps/details?id=com.uthelper.uthelper",
            ],
        ),
        (
            "msstore",
            None,
            "9NBLGGH4NNS1",
            "ms-windows-store",
            [
                "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1",
                "https://apps.microsoft.com/detail/9NBLGGH4NNS1",
            ],
        ),
        ("msstore", None, None, None, []),
    ],
)
def test_t3_p04_store_channel_x_identifier_x_deep_link_fallback(
    channel: str,
    pkg_id: str | None,
    product_id: str | None,
    fail_scheme: str | None,
    expected_launched: list[str],
    isolated_runtime_env: dict[str, Path],
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    app_ctrl_mod = load_module("gui.app_controller")

    payload: dict[str, object] = {"schema_version": 1, "channel": channel}
    if pkg_id is not None:
        payload["android_package_id"] = pkg_id
    if product_id is not None:
        payload["store_product_id"] = product_id
    cfg_file = isolated_runtime_env["assets"] / "distribution-channel.json"
    cfg_file.write_text(json.dumps(payload), encoding="utf-8")

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=(channel == "msstore"),
        is_android=(channel == "chplay"),
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )

    if fail_scheme:
        fake_page.fail_url_schemes.add(fail_scheme)

    class _FakeUrlLauncher:
        async def launch_url(self, url: str, **_kwargs):
            scheme = urllib.parse.urlsplit(url).scheme.lower()
            if scheme in fake_page.fail_url_schemes:
                fake_page.launched_urls.append(url)
                raise RuntimeError(f"blocked {scheme}")
            fake_page.launched_urls.append(url)

    monkeypatch.setattr(app_ctrl_mod.ft, "UrlLauncher", _FakeUrlLauncher)
    controller = _make_controller_stub(fake_page, profile)

    if profile.store_url is None:
        assert controller._open_store_url("javascript:alert(1)") is False
        assert fake_page.launched_urls == []
    else:
        ok = controller._open_store_url(profile.store_url, profile.store_fallback_url)
        assert ok is True
        assert _wait_until(lambda: len(fake_page.launched_urls) == len(expected_launched))
        assert fake_page.launched_urls == expected_launched


# ---------------------------------------------------------------------------
# P05: exe_lts (msi vs bootstrapper) x schema (2 vs 3) x Windows verifier/launcher
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsShimEnv requires Windows")
@pytest.mark.parametrize(
    ("package_type", "install_channel", "schema_version", "ps_mode"),
    [
        ("msi", "msi", 2, "valid"),
        ("msi", "msi", 3, "self_signed"),
        ("exe", "bootstrapper", 2, "valid"),
        ("exe", "bootstrapper", 3, "self_signed"),
    ],
)
def test_t3_p05_exe_lts_package_type_x_schema_x_win_verifier_launcher(
    package_type: str,
    install_channel: str,
    schema_version: int,
    ps_mode: str,
    tmp_path: Path,
    local_release_server,
    windows_shim_env,
) -> None:
    checker_mod = load_module("core.update_checker")
    models_mod = load_module("core.update_models")
    win_mod = load_module("platform_utils.windows_update")

    version = "2.6.0"
    if package_type == "msi":
        filename = f"UTHelper-{version}.msi"
        pkg_path = windows_shim_env.make_msi_file(tmp_path / "src" / filename, payload=b"msi" * 512)
    else:
        filename = f"UTHelper-Setup-{version}.exe"
        pkg_path = windows_shim_env.make_setup_exe(tmp_path / "src" / filename)

    pkg_bytes = pkg_path.read_bytes()
    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=filename,
        data=pkg_bytes,
        platform="windows",
        architecture="x64",
        package_type=package_type,
        install_channel=install_channel,
        signature_kind="self-signed-pinned" if schema_version == 3 else None,
    )
    manifest = local_release_server.build_manifest(
        release_version=version,
        schema_version=schema_version,
    )
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    windows_shim_env.set_ps_mode(ps_mode, version=version)
    windows_shim_env.set_msi_behavior(exit_code=0, delay_sec=2.4)
    windows_shim_env.clear_calls()

    client = checker_mod.GitHubReleaseClient()
    target = models_mod.RuntimeTarget("windows", "x64", install_channel)
    candidate = client.fetch_candidate("2.5.1", target)
    assert candidate is not None
    assert candidate.manifest.schema_version == schema_version

    downloader = checker_mod.VerifiedDownloader(cache_dir=tmp_path / "cache")
    downloaded = downloader.download(candidate.package, cancel=threading.Event())
    assert downloaded.is_file()

    verifier = win_mod.WindowsPackageVerifier(timeout_seconds=10.0)
    verification = verifier.verify(downloaded, candidate)
    assert verification.verified is True, verification.reason

    launcher = win_mod.WindowsPackageLauncher(
        process_factory=windows_shim_env.process_factory,
        acknowledgement_seconds=0.6,
    )
    launch = launcher.launch(downloaded, candidate.package)
    assert launch.acknowledged is True

    calls = windows_shim_env.read_calls()
    ps_calls = [c for c in calls if c["tool"] == "powershell.exe"]
    assert len(ps_calls) >= 2
    if package_type == "msi":
        msi_calls = [c for c in calls if c["tool"] == "msiexec.exe"]
        assert len(msi_calls) == 1
        assert "/i" in msi_calls[0]["argv"]
        assert "/passive" in msi_calls[0]["argv"]
        assert "/norestart" in msi_calls[0]["argv"]
    else:
        exe_calls = [c for c in calls if c["tool"] == filename.lower()]
        assert len(exe_calls) == 1
        assert "/passive" in exe_calls[0]["argv"]
        assert "/norestart" in exe_calls[0]["argv"]


# ---------------------------------------------------------------------------
# P06: exe_lts x Windows fault mode x coordinator & AppController error state
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="WindowsShimEnv requires Windows")
@pytest.mark.parametrize(
    ("package_type", "install_channel", "fault_mode"),
    [
        ("msi", "msi", "ps_timeout"),
        ("exe", "bootstrapper", "bad_signer"),
        ("msi", "msi", "uac_1223"),
        ("exe", "bootstrapper", "busy_1618"),
    ],
)
def test_t3_p06_exe_lts_x_win_fault_mode_x_error_handling(
    package_type: str,
    install_channel: str,
    fault_mode: str,
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

    version = "2.6.0"
    if package_type == "msi":
        filename = f"UTHelper-{version}.msi"
        pkg_path = windows_shim_env.make_msi_file(tmp_path / "src" / filename, payload=b"msi" * 256)
    else:
        filename = f"UTHelper-Setup-{version}.exe"
        pkg_path = windows_shim_env.make_setup_exe(tmp_path / "src" / filename)

    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=filename,
        data=pkg_path.read_bytes(),
        platform="windows",
        architecture="x64",
        package_type=package_type,
        install_channel=install_channel,
    )
    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    if fault_mode == "ps_timeout":
        windows_shim_env.set_ps_mode("timeout", version=version)
    elif fault_mode == "bad_signer":
        windows_shim_env.set_ps_mode("bad_signer", version=version)
    else:
        windows_shim_env.set_ps_mode("self_signed", version=version)

    if fault_mode == "uac_1223":
        windows_shim_env.set_msi_behavior(exit_code=1223, delay_sec=0.05)
    elif fault_mode == "busy_1618":
        windows_shim_env.set_msi_behavior(exit_code=1618, delay_sec=0.05)

    events: list = []
    profile = dist_mod.build_distribution_profile(dist_mod.DistributionChannel.EXE_LTS)
    controller = _make_controller_stub(fake_page, profile, update_events=events)

    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=tmp_path / "cache"),
        win_mod.WindowsPackageVerifier(timeout_seconds=1.2),
        win_mod.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory,
            acknowledgement_seconds=1.5,
        ),
        models_mod.RuntimeTarget("windows", "x64", install_channel),
        "2.5.1",
        controller._on_update_event,
        automatic_enabled=False,
    )
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events),
            timeout=5.0,
        )
        coordinator.request_download()
        if fault_mode in {"ps_timeout", "bad_signer"}:
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.FAILED for e in events),
                timeout=8.0,
            )
            calls = windows_shim_env.read_calls()
            assert not any(c["tool"] == "msiexec.exe" for c in calls)
            assert not any(c["tool"] == filename.lower() for c in calls)
            assert fake_page.window.destroyed is False
        else:
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events),
                timeout=8.0,
            )
            coordinator.confirm_install()
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events),
                timeout=8.0,
            )
            assert fake_page.window.destroyed is False
            assert _wait_until(lambda: controller._update_btn.content == "Thử lại", timeout=3.0)
            assert controller._update_btn.disabled is False
            assert controller._update_text.value != "Không mở được trình cài đặt. Hãy thử lại."
    finally:
        coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# P07: apk_lts (arm64 vs universal) x permission_granted x cache reuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target_arch", "include_arm64", "permission_granted"),
    [
        ("arm64", True, True),
        ("arm64", False, False),
        ("universal", False, True),
    ],
)
def test_t3_p07_apk_lts_arch_x_permission_x_cache_reuse(
    target_arch: str,
    include_arm64: bool,
    permission_granted: bool,
    local_release_server,
    android_bridge,
) -> None:
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    upkg_mod = load_module("platform_utils.update_packages")
    and_mod = load_module("platform_utils.android_updates")

    version = "2.6.0"
    univ_bytes = make_minimal_apk_bytes(b"universal-apk", version_code=2_006_000, signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256)
    local_release_server.register_package(
        tag_name=f"v{version}",
        filename=f"UTHelper-{version}.apk",
        data=univ_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
    )
    if include_arm64:
        arm_bytes = make_minimal_apk_bytes(b"arm64-apk", version_code=2_006_000, signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256)
        local_release_server.register_package(
            tag_name=f"v{version}",
            filename=f"UTHelper-{version}-arm64-v8a.apk",
            data=arm_bytes,
            platform="android",
            architecture="arm64",
            package_type="apk",
            install_channel="sideload",
            signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        )

    manifest = local_release_server.build_manifest(release_version=version, schema_version=3)
    local_release_server.configure_latest_release(tag_name=f"v{version}", manifest=manifest)

    android_bridge.permission_granted = permission_granted
    android_bridge.enforce_apk_suffix_on_archive_parse = True

    def _run_task(handler, *args):
        fut: Future = Future()
        fut.set_result(asyncio.run(handler(*args)))
        return fut

    events: list = []
    release_ver_holder = {"v": version}
    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        and_mod.AndroidPackageDownloader(
            android_bridge,
            _run_task,
            lambda: release_ver_holder["v"],
            poll_interval_seconds=0.01,
        ),
        upkg_mod.DownloadedPackageVerifier(),
        and_mod.AndroidPackageLauncher(android_bridge, _run_task, lambda: release_ver_holder["v"]),
        models_mod.RuntimeTarget("android", target_arch, "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events),
            timeout=5.0,
        )
        avail = next(e for e in events if e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE)
        expected_arch = "arm64" if (target_arch == "arm64" and include_arm64) else "universal"
        assert avail.candidate.package.architecture == expected_arch

        coordinator.request_download()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events),
            timeout=5.0,
        )
        coordinator.confirm_install()

        if permission_granted:
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events),
                timeout=5.0,
            )
        else:
            assert _wait_until(
                lambda: any(
                    e.kind is coord_mod.UpdateEventKind.INSTALL_PERMISSION_REQUIRED for e in events
                ),
                timeout=5.0,
            )
            # Delete external source download to prove retry uses verified_cache
            for item in android_bridge.external_downloads.iterdir():
                item.unlink()
            assert any(android_bridge.verified_cache.glob("*.apk"))
            android_bridge.permission_granted = True
            coordinator.confirm_install()
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events),
                timeout=5.0,
            )
    finally:
        coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# P08: apk_lts x Android bridge anomaly x AppController UI state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure_mode",
    [
        "insufficient_space",
        "storage_unavailable",
        "installed_signer_mismatch",
        "bridge_exception",
        "malformed_response",
    ],
)
def test_t3_p08_apk_lts_x_bridge_failure_x_controller_state(
    failure_mode: str,
    local_release_server,
    android_bridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    upkg_mod = load_module("platform_utils.update_packages")
    and_mod = load_module("platform_utils.android_updates")
    pu_mod = load_module("platform_utils")

    monkeypatch.setattr(pu_mod, "IS_ANDROID", True)
    monkeypatch.setattr(pu_mod, "IS_WINDOWS", False)

    version = "2.6.0"
    apk_bytes = make_minimal_apk_bytes(b"apk-fail", version_code=2_006_000, signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256)
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

    if failure_mode == "insufficient_space":
        android_bridge.insufficient_space = True
    elif failure_mode == "storage_unavailable":
        android_bridge.storage_unavailable = True
    elif failure_mode == "installed_signer_mismatch":
        android_bridge.installed_signer_sha256 = "F" * 64
    elif failure_mode == "bridge_exception":
        android_bridge.inject_exception = RuntimeError("MethodChannel detached")
    elif failure_mode == "malformed_response":
        android_bridge.malformed_response = {"unexpected": 123}

    profile = dist_mod.build_distribution_profile(dist_mod.DistributionChannel.APK_LTS)
    events: list = []
    controller = _make_controller_stub(
        fake_page, profile, android_bridge=android_bridge, update_events=events
    )

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
        models_mod.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        controller._on_update_event,
        automatic_enabled=False,
    )
    controller._update_coordinator = coordinator
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events),
            timeout=5.0,
        )
        coordinator.request_download()
        if failure_mode == "installed_signer_mismatch":
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events),
                timeout=5.0,
            )
            coordinator.confirm_install()
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events),
                timeout=5.0,
            )
            assert _wait_until(lambda: controller._update_btn.content == "Bản phát hành", timeout=3.0)
        else:
            assert _wait_until(
                lambda: any(e.kind is coord_mod.UpdateEventKind.FAILED for e in events),
                timeout=5.0,
            )
            assert _wait_until(lambda: controller._update_btn.content == "Thử lại", timeout=3.0)
            assert controller._update_btn.disabled is False
            if failure_mode == "insufficient_space":
                assert "dung lượng" in controller._update_text.value
            elif failure_mode == "storage_unavailable":
                assert "bộ nhớ" in controller._update_text.value
    finally:
        coordinator.shutdown(timeout_seconds=3.0)


# ---------------------------------------------------------------------------
# P09: msstore identity x autostart x WindowsNotifier x UpdateCoordinator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("has_identity", "channel_file", "expected_channel", "expected_self_update", "expected_autostart"),
    [
        (True, None, "msstore", False, "StartupTaskAutostartBackend"),
        (True, "exe_lts", "msstore", False, "StartupTaskAutostartBackend"),
        (False, "msstore", "msstore", False, "RunKeyAutostartBackend"),
        (False, "exe_lts", "exe_lts", True, "RunKeyAutostartBackend"),
    ],
)
def test_t3_p09_msstore_identity_x_autostart_x_notifier_x_suppression(
    has_identity: bool,
    channel_file: str | None,
    expected_channel: str,
    expected_self_update: bool,
    expected_autostart: str,
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist_mod = load_module("core.distribution")
    pu_mod = load_module("platform_utils")
    autostart_mod = load_module("platform_utils.autostart")
    win_update_mod = load_module("platform_utils.windows_update")
    win_notif_mod = load_module("notifiers.windows")

    if channel_file is not None:
        distribution_channel_stager(channel_file, store_product_id="9NBLGGH4NNS1")
    else:
        (isolated_runtime_env["assets"] / "distribution-channel.json").unlink(missing_ok=True)

    monkeypatch.setattr(pu_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(pu_mod, "IS_ANDROID", False)
    monkeypatch.setattr(pu_mod, "IS_IOS", False)
    monkeypatch.setattr(autostart_mod, "has_package_identity", lambda: has_identity)
    monkeypatch.setattr(win_update_mod, "has_package_identity", lambda: has_identity)
    monkeypatch.setattr(win_update_mod, "read_install_channel", lambda: "msi")
    monkeypatch.setattr(
        win_notif_mod,
        "_packaged_aumid",
        lambda: "UTHelper.Store_12345!UTHelper" if has_identity else "",
    )
    monkeypatch.setattr(
        win_notif_mod.WindowsNotifier,
        "_ensure_shortcut",
        lambda _self: (_ for _ in ()).throw(
            AssertionError("_ensure_shortcut must not run when packaged")
        )
        if has_identity
        else (lambda _self: None).__call__,
    )

    profile = dist_mod.resolve_distribution_channel(
        environ={"FLET_ASSETS_DIR": str(isolated_runtime_env["assets"])},
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: has_identity,
        is_packaged=True,
    )
    assert profile.channel.value == expected_channel
    assert profile.supports_github_self_update is expected_self_update

    backend = autostart_mod.create_autostart_service(platform_name="win32")
    assert type(backend).__name__ == expected_autostart

    notifier = win_notif_mod.WindowsNotifier(start_scheduler=False)
    assert notifier.is_packaged is has_identity
    if has_identity:
        assert notifier.aumid.endswith("!UTHelper")
    else:
        assert notifier.aumid == "UTHelper.App"

    target = win_update_mod.detect_windows_runtime_target(distribution=profile)
    assert target.install_channel == ("msix" if expected_channel == "msstore" else "msi")

    controller = _make_controller_stub(fake_page, profile)
    coordinator = controller._create_update_coordinator()
    try:
        if expected_self_update:
            assert coordinator is not None
        else:
            assert coordinator is None
    finally:
        if coordinator is not None:
            coordinator.shutdown(timeout_seconds=2.0)


# ---------------------------------------------------------------------------
# P10: Android artifact (.aab vs .apk) x ELF align x manifest/DEX compliance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("artifact_kind", "p_align", "dex_strings", "chplay_manifest", "channel_json", "expect_pass"),
    [
        ("aab", 0x4000, ("Lcom/uthelper/MainActivity;",), True, "chplay", True),
        ("aab", 0x1000, ("Lcom/uthelper/MainActivity;",), True, "chplay", False),
        (
            "aab",
            0x4000,
            ("Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;",),
            True,
            "chplay",
            False,
        ),
        ("aab", 0x4000, ("Lcom/uthelper/MainActivity;",), False, "chplay", False),
        (
            "apk",
            0x4000,
            ("Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;",),
            False,
            "apk_lts",
            True,
        ),
    ],
)
def test_t3_p10_android_artifact_x_elf_align_x_compliance(
    artifact_kind: str,
    p_align: int,
    dex_strings: tuple[str, ...],
    chplay_manifest: bool,
    channel_json: str,
    expect_pass: bool,
    tmp_path: Path,
) -> None:
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{DEFAULT_ANDROID_PACKAGE_ID}">\n'
        '  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35" />\n'
        '  <uses-permission android:name="android.permission.INTERNET" />\n'
        '  <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />\n'
        + (
            ""
            if chplay_manifest
            else '  <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n'
        )
        + f'  <application android:allowBackup="{"false" if chplay_manifest else "true"}">\n'
        + (
            ""
            if chplay_manifest
            else '    <provider android:name="com.uthelper.backgroundsync.update.UthUpdateFileProvider" />\n'
        )
        + '    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver" />\n'
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
        "channel": channel_json,
        "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
    }
    sdk_root = _make_fake_android_sdk(tmp_path / "sdk")

    if artifact_kind == "aab":
        bundle = make_synthetic_aab(
            tmp_path / "UTHelper-2.5.1.aab",
            abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
            p_align=p_align,
            dex_strings=dex_strings,
            manifest_xml=manifest,
            distribution_channel=channel_doc,
        )
        script_path = WORKTREE_ROOT / "scripts" / "verify_android_release.py"
        help_proc = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert help_proc.returncode == 0
        assert "--aab" in help_proc.stdout or "--channel" in help_proc.stdout, (
            "M3 must extend scripts/verify_android_release.py to verify CH Play .aab artifacts"
        )
        cmd = [
            sys.executable,
            str(script_path),
            "--aab",
            str(bundle),
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
            "12345",
            "--output",
            str(tmp_path / "aab.verification.json"),
        ]
        if "--certificate-sha256" in help_proc.stdout:
            cmd.extend(["--certificate-sha256", DEFAULT_TRUSTED_SIGNER_SHA256])
        env = os.environ.copy()
        env["ANDROID_HOME"] = str(sdk_root)
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30)
        if expect_pass:
            assert proc.returncode == 0, proc.stderr or proc.stdout
        else:
            assert proc.returncode != 0
    else:
        apk = make_synthetic_apk(
            tmp_path / "UTHelper-2.5.1.apk",
            abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
            p_align=p_align,
            dex_strings=dex_strings,
            manifest_xml=manifest,
            distribution_channel=channel_doc,
        )
        verify_mod = load_module("scripts.verify_android_release")
        evidence = verify_mod.verify_android_release(
            apk=apk,
            version="2.5.1",
            build_number=2_005_001,
            package_id=DEFAULT_ANDROID_PACKAGE_ID,
            certificate_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
            commit_sha="a" * 40,
            workflow_run_id="12345",
            output=tmp_path / "apk.verification.json",
            android_home=sdk_root,
            architecture="universal",
        )
        assert evidence["platform"] == "android"


# ---------------------------------------------------------------------------
# P11: FLET_APP_STORAGE_DATA vs FLET_APP_DATA x FLET_ASSETS_DIR x subprocess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("storage_mode", "channel"),
    [
        ("flet_storage_data", "chplay"),
        ("flet_storage_data", "apk_lts"),
        ("flet_app_data_only", "chplay"),
        ("fallback_home", "apk_lts"),
    ],
)
def test_t3_p11_storage_env_x_assets_channel_x_subprocess_isolation(
    storage_mode: str,
    channel: str,
    tmp_path: Path,
) -> None:
    assets_dir = tmp_path / "assets"
    stage_distribution_channel_asset(assets_dir, channel)
    storage_data_dir = tmp_path / "storage_data"
    flet_app_data_dir = tmp_path / "legacy_flet_data"
    fake_home = tmp_path / "fake_home"
    for d in (storage_data_dir, flet_app_data_dir, fake_home):
        d.mkdir(parents=True, exist_ok=True)

    env_overrides: dict[str, str | None] = {
        "FLET_ASSETS_DIR": str(assets_dir),
        "HOME": str(fake_home),
        "USERPROFILE": str(fake_home),
    }
    if storage_mode == "flet_storage_data":
        env_overrides["FLET_APP_STORAGE_DATA"] = str(storage_data_dir)
        env_overrides["FLET_APP_DATA"] = None
    elif storage_mode == "flet_app_data_only":
        env_overrides["FLET_APP_STORAGE_DATA"] = None
        env_overrides["FLET_APP_DATA"] = str(flet_app_data_dir)
    else:
        env_overrides["FLET_APP_STORAGE_DATA"] = None
        env_overrides["FLET_APP_DATA"] = None

    code = (
        "import json, sys\n"
        "sys._ANDROID_ = True\n"
        "import config, platform_utils\n"
        "from core.distribution import resolve_distribution_channel\n"
        "platform_utils.IS_ANDROID = True\n"
        "platform_utils.IS_WINDOWS = False\n"
        "platform_utils.IS_IOS = False\n"
        "profile = resolve_distribution_channel()\n"
        "print(json.dumps({\n"
        "  'data_dir': str(config.get_data_dir()),\n"
        "  'channel': profile.channel.value,\n"
        "  'self_update': profile.supports_github_self_update,\n"
        "}))\n"
    )
    proc = run_isolated_python(code, env_overrides=env_overrides, tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["channel"] == channel
    assert result["self_update"] is (channel != "chplay")
    if storage_mode == "flet_storage_data":
        assert Path(result["data_dir"]) == storage_data_dir / "UTHelper"
    elif storage_mode == "flet_app_data_only":
        assert Path(result["data_dir"]) == flet_app_data_dir / "UTHelper"
    else:
        assert Path(result["data_dir"]) == fake_home / ".uthelper"


# ---------------------------------------------------------------------------
# P12: Store artifacts (.aab, .msix) x release_inventory.py 7-asset LTS isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "contamination",
    [None, "UTHelper-2.5.1.aab", "UTHelper-2.5.1.msix", "missing_arm64_apk"],
)
def test_t3_p12_store_artifacts_x_release_inventory_lts_isolation(
    contamination: str | None,
    tmp_path: Path,
) -> None:
    inv_mod = load_module("scripts.release_inventory")
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    store_dir = tmp_path / "store_artifacts"
    store_dir.mkdir(parents=True, exist_ok=True)
    make_synthetic_lts_release_dir(release_dir, "2.5.1", evidence_dir=evidence_dir)

    # Store artifacts staged separately never affect LTS inventory
    make_synthetic_aab(store_dir / "UTHelper-2.5.1.aab", distribution_channel={"channel": "chplay"})
    (store_dir / "UTHelper-2.5.1.msix").write_bytes(b"PK\x03\x04msix")

    if contamination is None:
        inventory = inv_mod.verify_release_inventory(
            release_dir,
            evidence_dir,
            "2.5.1",
            "Chouwzi/UTHelper",
            manifest_path=release_dir / "release-manifest.json",
            checksums_path=release_dir / "SHA256SUMS",
        )
        assert len(inventory.packages) == 5
    elif contamination == "missing_arm64_apk":
        (release_dir / "UTHelper-2.5.1-arm64-v8a.apk").unlink()
        with pytest.raises(inv_mod.InventoryError):
            inv_mod.verify_release_inventory(
                release_dir,
                evidence_dir,
                "2.5.1",
                "Chouwzi/UTHelper",
                manifest_path=release_dir / "release-manifest.json",
                checksums_path=release_dir / "SHA256SUMS",
            )
    else:
        shutil.copy2(store_dir / contamination, release_dir / contamination)
        with pytest.raises(inv_mod.InventoryError):
            inv_mod.verify_release_inventory(
                release_dir,
                evidence_dir,
                "2.5.1",
                "Chouwzi/UTHelper",
                manifest_path=release_dir / "release-manifest.json",
                checksums_path=release_dir / "SHA256SUMS",
            )


# ---------------------------------------------------------------------------
# P13: VerifiedDownloader x HTTP fault matrix x resume & integrity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pkg_type", "fault_kind"),
    [
        ("msi", "503_then_206_resume"),
        ("exe", "503_then_206_resume"),
        ("apk", "bad_content_range"),
        ("msi", "sha256_tamper"),
    ],
)
def test_t3_p13_downloader_x_http_faults_x_resume_integrity(
    pkg_type: str,
    fault_kind: str,
    tmp_path: Path,
    local_release_server,
) -> None:
    checker_mod = load_module("core.update_checker")
    models_mod = load_module("core.update_models")

    payload = (b"MZ" if pkg_type == "exe" else b"PK\x03\x04") + os.urandom(16384)
    filename = f"UTHelper-2.6.0.{pkg_type}"
    dl_path = f"/Chouwzi/UTHelper/releases/download/v2.6.0/{filename}"
    entry = local_release_server.register_package(
        tag_name="v2.6.0",
        filename=filename,
        data=payload,
        platform="windows" if pkg_type in {"msi", "exe"} else "android",
        architecture="x64" if pkg_type in {"msi", "exe"} else "universal",
        package_type=pkg_type,
        install_channel=pkg_type if pkg_type == "msi" else ("bootstrapper" if pkg_type == "exe" else "sideload"),
    )
    pkg = models_mod.ReleasePackage(
        platform=entry["platform"],
        architecture=entry["architecture"],
        package_type=entry["package_type"],
        install_channel=entry["install_channel"],
        url=entry["url"],
        sha256=("0" * 64) if fault_kind == "sha256_tamper" else entry["sha256"],
        size=entry["size"],
        signer_identity=entry["signer_identity"],
        certificate_fingerprint=entry["certificate_fingerprint"],
        install_strategy=entry["install_strategy"],
    )
    cache_dir = tmp_path / "dl_cache"
    downloader = checker_mod.VerifiedDownloader(cache_dir=cache_dir)

    if fault_kind == "503_then_206_resume":
        local_release_server.set_fault(
            dl_path,
            status_sequence=[503],
            truncate_sequence=[None, 8192],
        )
        out_path = downloader.download(pkg, cancel=threading.Event())
        assert out_path.read_bytes() == payload
        assert any(req.get("range") == "bytes=8192-" for req in local_release_server.requests)
        assert not (cache_dir / f"{filename}.part").exists()
    elif fault_kind == "bad_content_range":
        local_release_server.set_fault(
            dl_path,
            truncate_sequence=[4096],
            bad_content_range="bytes 0-999999/9999999",
        )
        with pytest.raises(checker_mod.PackageIntegrityError):
            downloader.download(pkg, cancel=threading.Event())
        assert not (cache_dir / f"{filename}.part").exists()
    else:
        with pytest.raises(checker_mod.PackageIntegrityError):
            downloader.download(pkg, cancel=threading.Event())
        assert not (cache_dir / f"{filename}.part").exists()


# ---------------------------------------------------------------------------
# P14: UpdateCoordinator lifecycle x shutdown phase x platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("platform_mode", "shutdown_phase"),
    [
        ("android", "during_download"),
        ("windows", "during_download"),
        ("windows", "after_install_launched"),
        ("android", "after_install_launched"),
    ],
)
def test_t3_p14_coordinator_lifecycle_x_shutdown_phase_x_platform(
    platform_mode: str,
    shutdown_phase: str,
    tmp_path: Path,
) -> None:
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")

    pkg = models_mod.ReleasePackage(
        platform=platform_mode,
        architecture="universal" if platform_mode == "android" else "x64",
        package_type="apk" if platform_mode == "android" else "msi",
        install_channel="sideload" if platform_mode == "android" else "msi",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/pkg",
        sha256="a" * 64,
        size=4,
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID if platform_mode == "android" else DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "android_package_installer" if platform_mode == "android" else "launch_msi"},
    )
    manifest = models_mod.ReleaseManifest(
        schema_version=3,
        release_version="2.6.0",
        minimum_supported_version="2.5.1",
        published_at="2026-11-18T12:00:00Z",
        release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
        packages=(pkg,),
    )
    candidate = models_mod.UpdateCandidate(manifest, pkg, True, False)
    staged_file = tmp_path / ("pkg.apk" if platform_mode == "android" else "pkg.msi")
    staged_file.write_bytes(b"PK\x03\x04")

    download_started = threading.Event()
    calls: list[str] = []

    class _Client:
        def fetch_candidate(self, _v, _t):
            return candidate

    class _Downloader:
        def download(self, _package, *, cancel, progress=None):
            if shutdown_phase == "during_download":
                download_started.set()
                cancel.wait(timeout=3.0)
                raise load_module("core.update_checker").DownloadCancelled("cancelled")
            return staged_file

        def detach(self):
            calls.append("detach")

    class _Verifier:
        def verify(self, _path, _cand):
            return models_mod.VerificationResult(True)

    class _Launcher:
        def launch(self, _path, _pkg):
            calls.append("launch")
            return models_mod.LaunchResult(True, "ok", status="installer_opened" if platform_mode == "android" else "")

        def cancel(self):
            calls.append("launcher_cancel")

    events: list = []
    coord = coord_mod.UpdateCoordinator(
        _Client(),
        _Downloader(),
        _Verifier(),
        _Launcher(),
        models_mod.RuntimeTarget(platform_mode, pkg.architecture, pkg.install_channel),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    coord.check_now()
    assert _wait_until(lambda: any(e.kind is coord_mod.UpdateEventKind.UPDATE_AVAILABLE for e in events))
    coord.request_download()

    if shutdown_phase == "during_download":
        assert download_started.wait(timeout=3.0)
        assert coord.shutdown(timeout_seconds=3.0) is True
        assert "detach" in calls
    else:
        assert _wait_until(lambda: any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events))
        coord.confirm_install()
        assert _wait_until(lambda: any(e.kind is coord_mod.UpdateEventKind.INSTALL_LAUNCHED for e in events))
        assert coord.shutdown(timeout_seconds=3.0) is True
        assert "launcher_cancel" not in calls


# ---------------------------------------------------------------------------
# P15: Version x android_release_build_number x release_metadata x select_candidate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current_version", "remote_version", "expected_code", "expect_candidate"),
    [
        ("2.5.1", "2.5.2", 2_005_002, True),
        ("2.5.1", "2.5.1", 2_005_001, False),
        ("2.6.0", "2.5.9", 2_005_009, False),
        ("2.5.1", "2.10.0", 2_010_000, True),
    ],
)
def test_t3_p15_version_x_build_number_x_candidate_comparison(
    current_version: str,
    remote_version: str,
    expected_code: int,
    expect_candidate: bool,
) -> None:
    and_mod = load_module("platform_utils.android_updates")
    meta_mod = load_module("scripts.release_metadata")
    manifest_mod = load_module("core.update_manifest")
    models_mod = load_module("core.update_models")

    assert and_mod.android_release_build_number(remote_version) == expected_code
    assert meta_mod.release_build_number(remote_version) == expected_code

    doc = {
        "schema_version": 3,
        "release_version": remote_version,
        "minimum_supported_version": "2.0.0",
        "published_at": "2026-11-18T12:00:00Z",
        "release_notes_url": f"https://github.com/Chouwzi/UTHelper/releases/tag/v{remote_version}",
        "packages": [
            {
                "platform": "android",
                "architecture": "universal",
                "package_type": "apk",
                "install_channel": "sideload",
                "url": f"https://github.com/Chouwzi/UTHelper/releases/download/v{remote_version}/UTHelper-{remote_version}.apk",
                "sha256": "a" * 64,
                "size": 1024,
                "signature_kind": "apk-pinned",
                "signer_identity": DEFAULT_ANDROID_PACKAGE_ID,
                "certificate_fingerprint": DEFAULT_TRUSTED_SIGNER_SHA256,
                "install_strategy": {"kind": "android_package_installer"},
            }
        ],
    }
    parsed = manifest_mod.parse_manifest(doc, expected_release_version=remote_version)
    selected = manifest_mod.select_candidate(
        parsed,
        current_version=current_version,
        target=models_mod.RuntimeTarget("android", "arm64", "sideload"),
    )
    assert (selected is not None) is expect_candidate


# ---------------------------------------------------------------------------
# P16: SettingsView dirty/save state x DistributionChannel x SettingsFormSnapshot
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", ["chplay", "msstore", "apk_lts", "exe_lts"])
def test_t3_p16_settings_dirty_state_x_channel_x_form_snapshot(
    channel: str,
    isolated_runtime_env: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ft = load_module("flet")
    dist_mod = load_module("core.distribution")
    sv_mod = load_module("gui.components.settings_view")
    form_mod = load_module("gui.view_models.settings_form")
    cfg_mod = load_module("config")
    pu_mod = load_module("platform_utils")

    settings_obj = cfg_mod.settings
    saved_settings_vars = copy.deepcopy(vars(settings_obj))
    try:
        is_mobile = channel in {"chplay", "apk_lts"}
        monkeypatch.setattr(pu_mod, "IS_MOBILE", is_mobile)
        monkeypatch.setattr(pu_mod, "UI_IS_MOBILE", is_mobile)
        monkeypatch.setattr(cfg_mod, "CONFIG_FILE", isolated_runtime_env["appdata"] / "settings.json")
        monkeypatch.setattr(cfg_mod, "_HAS_KEYRING", False)

        page = Mock(spec=ft.Page, window=Mock(), theme_mode="dark", on_route_change=None)
        profile = dist_mod.build_distribution_profile(
            dist_mod.DistributionChannel(channel),
            store_product_id="9NBLGGH4NNS1" if channel == "msstore" else None,
        )
        view = sv_mod.SettingsView(
            page=page,
            orchestrator=None,
            on_close=lambda: None,
            autostart_coordinator=SimpleNamespace(),
            distribution_profile=profile,
        )
        view.update = lambda: None

        baseline = form_mod.SettingsFormSnapshot.from_form_values(
            {"auto_update_enabled": True, "crash_reporting_consent": "enabled"}
        )
        view._apply_snapshot_to_controls(baseline)
        view._baseline_snapshot = baseline
        view._loading = False

        assert view.has_changes() is False
        view._dd_crash_reporting_consent.value = "disabled"
        assert view.has_changes() is True

        saved = asyncio.run(view._save(None))
        assert saved is True
        assert cfg_mod.settings.CRASH_REPORTING_CONSENT == "disabled"
        assert cfg_mod.settings.AUTO_UPDATE_ENABLED is True
    finally:
        current_vars = vars(settings_obj)
        for key in list(current_vars.keys()):
            if key not in saved_settings_vars:
                current_vars.pop(key, None)
        current_vars.update(saved_settings_vars)


# ---------------------------------------------------------------------------
# P17: GitHubReleaseClient filtering x manifest security gates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "defect",
    ["draft_true", "prerelease_true", "duplicate_manifest_asset", "unapproved_package_host"],
)
def test_t3_p17_release_filtering_x_manifest_security_gates(
    defect: str,
    local_release_server,
) -> None:
    checker_mod = load_module("core.update_checker")
    models_mod = load_module("core.update_models")

    entry = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.msi",
        data=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1msi",
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
    )
    if defect == "unapproved_package_host":
        entry["url"] = "https://evil.example/UTHelper-2.6.0.msi"
    manifest = local_release_server.build_manifest(
        release_version="2.6.0",
        packages=[entry],
    )
    extra_assets = (
        [
            {
                "name": "release-manifest.json",
                "browser_download_url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/release-manifest.json",
            }
        ]
        if defect == "duplicate_manifest_asset"
        else ()
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=manifest,
        draft=(defect == "draft_true"),
        prerelease=(defect == "prerelease_true"),
        extra_assets=extra_assets,
    )

    client = checker_mod.GitHubReleaseClient()
    target = models_mod.RuntimeTarget("windows", "x64", "msi")
    if defect in {"draft_true", "prerelease_true", "duplicate_manifest_asset"}:
        assert client.fetch_candidate("2.5.1", target) is None
    else:
        with pytest.raises(checker_mod.UpdateDiscoveryError):
            client.fetch_candidate("2.5.1", target)


# ---------------------------------------------------------------------------
# P18: MSIX package_msix.ps1 x scaled PNG tiles x channel asset
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows makeappx.exe")
def test_t3_p18_msix_packaging_x_tile_dimensions_x_channel_asset() -> None:
    from PIL import Image

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
    assert makeappx is not None, "makeappx.exe is required on Windows for MSIX E2E verification"

    build_root = WORKTREE_ROOT / "build"
    build_root.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="e2e-t3-msix-", dir=build_root))
    bundle_dir = temp_root / "bundle"
    unpack_dir = temp_root / "unpacked"
    msix_out = temp_root / "UTHelper-msstore.msix"
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

        # Inspect parameterized options dynamically so M4 exact param names work cleanly
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
        )
        assert param_probe.returncode == 0, param_probe.stderr
        params = {p.strip().lower(): p.strip() for p in param_probe.stdout.split(",") if p.strip()}

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
            "CN=E2ETestPublisher",
            "-Output",
            str(msix_out),
        ]
        for cand_key, val in (
            ("identityname", "Chouwzi.UTHelperStoreTest"),
            ("packagename", "Chouwzi.UTHelperStoreTest"),
            ("channel", "msstore"),
            ("storeproductid", "9NBLGGH4NNS1"),
            ("productid", "9NBLGGH4NNS1"),
        ):
            if cand_key in params:
                cmd.extend([f"-{params[cand_key]}", val])

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr or proc.stdout
        assert msix_out.is_file()

        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(msix_out), "/d", str(unpack_dir), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr or unpack_proc.stdout

        manifest_xml = (unpack_dir / "AppxManifest.xml").read_text(encoding="utf-8")
        assert 'WriteVirtualization="disabled"' not in manifest_xml, (
            "MSIX AppxManifest.xml must preserve AppData/Registry virtualization"
        )
        manifest_root = ET.fromstring(manifest_xml)
        ns = {
            "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
            "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
            "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
        }
        identity = manifest_root.find("m:Identity", ns)
        assert identity is not None
        assert identity.attrib.get("ProcessorArchitecture") == "x64"
        assert identity.attrib.get("Version") == "2.5.1.0"
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
        wide_logo = unpack_dir / "Assets" / "Wide310x150Logo.png"
        assert wide_logo.is_file(), "Wide310x150Logo.png must be packaged in MSIX Assets/"
        with Image.open(wide_logo) as im310:
            assert im310.size == (310, 150)

        channel_files = list(unpack_dir.rglob("distribution-channel.json"))
        assert channel_files, "distribution-channel.json must be embedded inside MSIX"
        channel_data = json.loads(channel_files[0].read_text(encoding="utf-8"))
        assert channel_data["channel"] == "msstore"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
