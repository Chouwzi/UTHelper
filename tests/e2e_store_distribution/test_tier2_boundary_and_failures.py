"""Tier 2 opaque-box boundary, corner & failure tests across Features F1-F13.

Every test targets boundary values, corrupt/tampered inputs, denied OS
permissions, timeouts, invalid combinations, or fail-closed guardrails.
All M1-M4 target imports remain deferred via ``harness.load_module``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid
import zipfile

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DEFAULT_SIGNER_SUBJECT,
    DEFAULT_TRUSTED_SIGNER_SHA256,
    PYTHON_EXE,
    WORKTREE_ROOT,
    load_module,
    make_elf64_so,
    make_minimal_apk_bytes,
    make_minimal_dex,
    make_synthetic_aab,
    make_synthetic_apk,
    make_synthetic_lts_release_dir,
    run_isolated_python,
    sha256_hex,
    stage_distribution_channel_asset,
)

# ---------------------------------------------------------------------------
# Shared Helpers (parallel-safe, no M1-M4 top-level imports)
# ---------------------------------------------------------------------------

_CLEAN_CHPLAY_MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.uthelper.uthelper"
    android:versionCode="2005001"
    android:versionName="2.5.1">
  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35" />
  <uses-permission android:name="android.permission.INTERNET" />
  <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
  <uses-permission android:name="android.permission.RECEIVE_BOOT_COMPLETED" />
  <uses-permission android:name="android.permission.SCHEDULE_EXACT_ALARM" />
  <application android:allowBackup="false">
    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver" />
    <receiver android:name="com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver" />
    <receiver android:name="com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver" />
    <receiver android:name="com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver" />
    <receiver android:name="com.uthelper.backgroundsync.notification.RescheduleReceiver" />
    <receiver android:name="com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver" />
  </application>
</manifest>
"""

_ANDROID_PLUGIN_ROOT = (
    WORKTREE_ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "android"
)


def _make_android_sdk_shim(
    sdk_dir: Path,
    *,
    manifest_xml: str = _CLEAN_CHPLAY_MANIFEST_XML,
    signer_sha256: str = DEFAULT_TRUSTED_SIGNER_SHA256,
    package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    version_name: str = "2.5.1",
    version_code: int = 2_005_001,
) -> Path:
    build_tools = sdk_dir / "build-tools" / "35.0.0"
    cmd_tools = sdk_dir / "cmdline-tools" / "latest" / "bin"
    build_tools.mkdir(parents=True, exist_ok=True)
    cmd_tools.mkdir(parents=True, exist_ok=True)

    manifest_file = sdk_dir / "manifest.xml"
    manifest_file.write_text(manifest_xml, encoding="utf-8")

    apksigner_py = sdk_dir / "apksigner_shim.py"
    if signer_sha256:
        apksigner_code = (
            f"print('Signer #1 certificate SHA-256 digest: {signer_sha256.lower()}')\n"
        )
    else:
        apksigner_code = "import sys; print('DOES NOT VERIFY', file=sys.stderr); sys.exit(1)\n"
    apksigner_py.write_text(apksigner_code, encoding="utf-8")

    apkanalyzer_py = sdk_dir / "apkanalyzer_shim.py"
    apkanalyzer_py.write_text(
        "import pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['manifest', 'application-id']:\n"
        f"    print({package_id!r})\n"
        "elif args[:2] == ['manifest', 'version-name']:\n"
        f"    print({version_name!r})\n"
        "elif args[:2] == ['manifest', 'version-code']:\n"
        f"    print({str(version_code)!r})\n"
        "elif args[:2] == ['manifest', 'print']:\n"
        f"    print(pathlib.Path({str(manifest_file)!r}).read_text(encoding='utf-8'))\n"
        "else:\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )

    if os.name == "nt":
        (build_tools / "apksigner.bat").write_text(
            f'@echo off\r\n"{PYTHON_EXE}" "{apksigner_py}" %*\r\n',
            encoding="utf-8",
        )
        (cmd_tools / "apkanalyzer.bat").write_text(
            f'@echo off\r\n"{PYTHON_EXE}" "{apkanalyzer_py}" %*\r\n',
            encoding="utf-8",
        )
    else:
        for script_path, target_py in (
            (build_tools / "apksigner", apksigner_py),
            (cmd_tools / "apkanalyzer", apkanalyzer_py),
        ):
            script_path.write_text(
                f'#!/usr/bin/env sh\nexec "{PYTHON_EXE}" "{target_py}" "$@"\n',
                encoding="utf-8",
            )
            script_path.chmod(0o755)
    return sdk_dir


def _run_verify_android(
    *,
    artifact_path: Path,
    output_path: Path,
    sdk_dir: Path,
    channel: str = "chplay",
    version: str = "2.5.1",
    build_number: int = 2_005_001,
    architecture: str = "universal",
) -> subprocess.CompletedProcess[str]:
    verifier_script = WORKTREE_ROOT / "scripts" / "verify_android_release.py"
    help_proc = subprocess.run(
        [PYTHON_EXE, str(verifier_script), "--help"],
        cwd=str(WORKTREE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    help_text = help_proc.stdout

    if channel == "chplay" and (
        "--channel" not in help_text
        or ("--aab" not in help_text and "--package" not in help_text)
    ):
        raise AssertionError(
            "scripts/verify_android_release.py must support --channel chplay and --aab/--package"
        )

    pkg_flag = (
        "--aab"
        if (channel == "chplay" and "--aab" in help_text)
        else ("--package" if "--package" in help_text else "--apk")
    )
    cmd = [
        PYTHON_EXE,
        str(verifier_script),
        pkg_flag,
        str(artifact_path),
        "--version",
        version,
        "--build-number",
        str(build_number),
        "--package-id",
        DEFAULT_ANDROID_PACKAGE_ID,
        "--certificate-sha256",
        DEFAULT_TRUSTED_SIGNER_SHA256,
        "--commit-sha",
        "a" * 40,
        "--workflow-run-id",
        "200002",
        "--output",
        str(output_path),
    ]
    if channel != "chplay" and "--architecture" in help_text:
        cmd.extend(["--architecture", architecture])
    if "--channel" in help_text:
        cmd.extend(["--channel", channel])

    env = os.environ.copy()
    env["ANDROID_HOME"] = str(sdk_dir)
    env["ANDROID_SDK_ROOT"] = str(sdk_dir)
    return subprocess.run(
        cmd,
        cwd=str(WORKTREE_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _make_minimal_update_controller(fake_page, channel: str):
    app_controller_mod = load_module("gui.app_controller")
    dist_mod = load_module("core.distribution")

    controller = app_controller_mod.AppController.__new__(app_controller_mod.AppController)
    controller.page = fake_page
    controller._android_background = None
    controller._diagnostic_recorder = None
    controller._unhandled_error_reporter = None
    controller._manual_update_check_requested = False
    controller._update_candidate = None
    controller._update_ready = False
    controller._update_open_release_page = False
    controller._update_coordinator = None
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(
        disabled=False, content="", visible=True, icon=None
    )
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_banner = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(
        _check_update_btn=None,
        set_update_status=lambda *_a, **_kw: None,
    )
    controller._show_snackbar = lambda *_a, **_kw: None
    controller._distribution_profile = dist_mod.build_distribution_profile(
        dist_mod.DistributionChannel(channel),
        store_product_id="9NBLGGH4NNS1" if channel == "msstore" else None,
    )
    return controller


def _build_windows_candidate(
    server,
    *,
    tag_name: str = "v2.6.0",
    version: str = "2.6.0",
    filename: str = "UTHelper-2.6.0.msi",
    data: bytes = b"synthetic-msi",
    package_type: str = "msi",
    install_channel: str = "msi",
    signer_identity: str = DEFAULT_SIGNER_SUBJECT,
    certificate_fingerprint: str = DEFAULT_TRUSTED_SIGNER_SHA256,
):
    manifest_mod = load_module("core.update_manifest")
    models_mod = load_module("core.update_models")

    pkg_entry = server.register_package(
        tag_name=tag_name,
        filename=filename,
        data=data,
        platform="windows",
        architecture="x64",
        package_type=package_type,
        install_channel=install_channel,
        signer_identity=signer_identity,
        certificate_fingerprint=certificate_fingerprint,
    )
    manifest_doc = server.build_manifest(release_version=version, packages=[pkg_entry])
    server.configure_latest_release(tag_name=tag_name, manifest=manifest_doc)
    manifest = manifest_mod.parse_manifest(
        manifest_doc, expected_release_version=version
    )
    return manifest_mod.select_candidate(
        manifest,
        current_version="2.5.1",
        target=models_mod.RuntimeTarget("windows", "x64", install_channel),
    )


def _find_makeappx() -> Path | None:
    kits_bin = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if not kits_bin.is_dir():
        return None
    matches = sorted(kits_bin.glob("**/x64/makeappx.exe"), reverse=True)
    return matches[0] if matches else None


def _run_msix_verifier_on_dir(unpacked_dir: Path, msix_path: Path) -> subprocess.CompletedProcess[str]:
    verifier_ps1 = WORKTREE_ROOT / "scripts" / "verify_msix_release.ps1"
    verifier_py = next((WORKTREE_ROOT / "scripts").glob("*verify*msix*.py"), None)
    if verifier_ps1.is_file():
        probe = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    f"$c = Get-Command '{verifier_ps1}'; "
                    "$out = @{}; "
                    "foreach ($k in $c.Parameters.Keys) { $out[$k] = $c.Parameters[$k].ParameterType.Name }; "
                    "$out | ConvertTo-Json -Compress"
                ),
            ],
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        assert probe.returncode == 0, probe.stderr
        params = json.loads(probe.stdout)
        param_lower = {k.lower(): k for k in params}
        path_param = next(
            (
                param_lower[k]
                for k in ("msixpath", "packagepath", "path", "msix", "package")
                if k in param_lower
            ),
            None,
        )
        if path_param is None:
            raise AssertionError(
                f"scripts/verify_msix_release.ps1 has no recognized path parameter; found: {sorted(params)}"
            )
        args = [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(verifier_ps1),
            f"-{path_param}",
            str(msix_path),
        ]
        for candidates, value in (
            (("channel", "expectedchannel"), "msstore"),
            (("identityname", "expectedidentityname", "packagename"), "UTHelperOrg.UTHelper"),
            (("publisher", "expectedpublisher"), "CN=UTHelper"),
            (("version", "expectedversion"), "2.5.1.0"),
            (("storeproductid", "productid"), "9NBLGGH4NNS1"),
            (("output", "outputpath"), str(unpacked_dir / "msix-verify-evidence.json")),
        ):
            matched = next((param_lower[c] for c in candidates if c in param_lower), None)
            if matched is not None:
                args.extend([f"-{matched}", value])
        return subprocess.run(
            args,
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    if verifier_py is not None:
        return subprocess.run(
            [PYTHON_EXE, str(verifier_py), "--msix", str(msix_path)],
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    raise AssertionError(
        "No MSIX verifier script found in scripts/ (expected verify_msix_release.ps1 or *verify*msix*.py)"
    )


def _pack_custom_msix_for_verifier_test(
    tmp_path: Path,
    *,
    manifest_xml: str,
    tile_size: tuple[int, int] = (44, 44),
    tile_overrides: dict[str, tuple[int, int]] | None = None,
    channel: str = "msstore",
    include_distribution_channel: bool = True,
    use_zip_instead_of_makeappx: bool = False,
) -> Path:
    from PIL import Image

    stage = tmp_path / "msix_stage"
    assets = stage / "Assets"
    app_assets = stage / "app" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    app_assets.mkdir(parents=True, exist_ok=True)

    (stage / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (stage / "UTHelperAutostart.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (stage / "AppxManifest.xml").write_text(manifest_xml, encoding="utf-8")
    if include_distribution_channel:
        stage_distribution_channel_asset(
            app_assets,
            channel,
            store_product_id="9NBLGGH4NNS1" if channel == "msstore" else None,
        )

    spec_sizes = {
        "Square44x44Logo.png": tile_size,
        "StoreLogo.png": (50, 50) if tile_size == (44, 44) else tile_size,
        "Square150x150Logo.png": (150, 150) if tile_size == (44, 44) else tile_size,
        "Wide310x150Logo.png": (310, 150) if tile_size == (44, 44) else tile_size,
    }
    if tile_overrides:
        spec_sizes.update(tile_overrides)
    for name, size in spec_sizes.items():
        Image.new("RGBA", size, (15, 23, 42, 255)).save(assets / name)

    out_msix = tmp_path / "test.msix"
    if use_zip_instead_of_makeappx:
        with zipfile.ZipFile(out_msix, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(stage.rglob("*")):
                if item.is_file():
                    zf.write(item, arcname=item.relative_to(stage).as_posix())
        return out_msix

    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.fail("makeappx.exe was not found on Windows host")

    proc = subprocess.run(
        [str(makeappx), "pack", "/d", str(stage), "/p", str(out_msix), "/o"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return out_msix


# ---------------------------------------------------------------------------
# F1: Core Distribution Channel Model & Asset Loader (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f01_corrupt_json_distribution_channel_falls_back_safely(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: Corrupt/truncated ``distribution-channel.json`` falls back safely without crashing."""
    dist_mod = load_module("core.distribution")
    cfg = isolated_runtime_env["assets"] / "distribution-channel.json"
    cfg.write_text('{"channel": "chplay", ', encoding="utf-8")

    profile = dist_mod.resolve_distribution_channel(
        config_path=cfg,
        is_windows=False,
        is_android=False,
        is_ios=False,
    )
    assert profile.channel is dist_mod.DistributionChannel.SOURCE
    assert profile.supports_github_self_update is True


def test_t2_f01_unknown_channel_string_ignored_with_safe_fallback(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: Unknown channel string (e.g., ``appstore``) is rejected and falls back cleanly."""
    dist_mod = load_module("core.distribution")
    cfg = isolated_runtime_env["assets"] / "distribution-channel.json"
    cfg.write_text(json.dumps({"channel": "appstore"}), encoding="utf-8")

    profile = dist_mod.resolve_distribution_channel(
        config_path=cfg,
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    assert profile.channel is dist_mod.DistributionChannel.APK_LTS


def test_t2_f01_empty_file_and_non_object_json_fall_back(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: 0-byte file and JSON array ``["chplay"]`` safely fall back to platform default."""
    dist_mod = load_module("core.distribution")
    cfg = isolated_runtime_env["assets"] / "distribution-channel.json"

    for content in ("", "   \n", '["chplay"]', "123", "null"):
        cfg.write_text(content, encoding="utf-8")
        profile = dist_mod.resolve_distribution_channel(
            config_path=cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: False,
            is_packaged=False,
        )
        assert profile.channel is dist_mod.DistributionChannel.SOURCE


def test_t2_f01_missing_or_invalid_android_package_id_defaults_to_canonical(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: Missing/blank/invalid ``android_package_id`` defaults to ``com.uthelper.uthelper``."""
    dist_mod = load_module("core.distribution")
    cfg = isolated_runtime_env["assets"] / "distribution-channel.json"

    for bad_id in (None, "", "   ", "not valid!", "singleword", 42):
        payload: dict[str, object] = {"channel": "chplay"}
        if bad_id is not None:
            payload["android_package_id"] = bad_id
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        profile = dist_mod.resolve_distribution_channel(
            config_path=cfg,
            is_windows=False,
            is_android=True,
            is_ios=False,
        )
        assert profile.channel is dist_mod.DistributionChannel.CHPLAY
        assert profile.android_package_id == DEFAULT_ANDROID_PACKAGE_ID
        assert profile.store_url == f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}"


def test_t2_f01_missing_or_invalid_store_product_id_sets_none_store_url(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: Missing/invalid ``store_product_id`` yields ``MSSTORE`` with ``store_url=None``."""
    dist_mod = load_module("core.distribution")
    cfg = isolated_runtime_env["assets"] / "distribution-channel.json"

    for bad_pid in (None, "", "bad", "has spaces!", "9NBL/../../x"):
        payload: dict[str, object] = {"channel": "msstore"}
        if bad_pid is not None:
            payload["store_product_id"] = bad_pid
        cfg.write_text(json.dumps(payload), encoding="utf-8")
        profile = dist_mod.resolve_distribution_channel(
            config_path=cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: True,
        )
        assert profile.channel is dist_mod.DistributionChannel.MSSTORE
        assert profile.supports_github_self_update is False
        assert profile.store_product_id is None
        assert profile.store_url is None


def test_t2_f01_precedence_between_channel_asset_and_package_identity(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F1: Asset overrides Android default; MSIX ``has_package_identity`` overrides ``exe_lts``."""
    dist_mod = load_module("core.distribution")

    chplay_cfg = stage_distribution_channel_asset(
        isolated_runtime_env["assets"], "chplay"
    )
    assert (
        dist_mod.resolve_distribution_channel(
            config_path=chplay_cfg,
            is_windows=False,
            is_android=True,
            is_ios=False,
        ).channel
        is dist_mod.DistributionChannel.CHPLAY
    )

    exe_cfg = stage_distribution_channel_asset(
        isolated_runtime_env["assets"], "exe_lts"
    )
    assert (
        dist_mod.resolve_distribution_channel(
            config_path=exe_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: True,
        ).channel
        is dist_mod.DistributionChannel.MSSTORE
    )


# ---------------------------------------------------------------------------
# F2: Store-Compliant Runtime Update Suppression (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f02_chplay_with_auto_update_enabled_true_still_blocks_github_requests(
    local_release_server,
    fake_page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: ``chplay`` with ``AUTO_UPDATE_ENABLED=True`` never polls GitHub or creates coordinator."""
    config_mod = load_module("config")
    monkeypatch.setattr(config_mod.settings, "AUTO_UPDATE_ENABLED", True)

    controller = _make_minimal_update_controller(fake_page, "chplay")
    controller._update_coordinator = controller._create_update_coordinator()
    assert controller._update_coordinator is None

    controller._check_updates_now()
    controller._request_update_download()
    assert local_release_server.request_count == 0


def test_t2_f02_msstore_with_auto_update_enabled_true_suppresses_github_and_shims(
    local_release_server,
    windows_shim_env,
    fake_page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: ``msstore`` with ``AUTO_UPDATE_ENABLED=True`` triggers zero HTTP or PowerShell/MSI calls."""
    config_mod = load_module("config")
    monkeypatch.setattr(config_mod.settings, "AUTO_UPDATE_ENABLED", True)

    controller = _make_minimal_update_controller(fake_page, "msstore")
    controller._update_coordinator = controller._create_update_coordinator()
    assert controller._update_coordinator is None

    controller._check_updates_now()
    controller._request_update_download()
    assert local_release_server.request_count == 0
    assert windows_shim_env.read_calls() == []


def test_t2_f02_schema3_sideload_manifest_does_not_leak_onto_chplay(
    isolated_runtime_env: dict[str, Path],
    local_release_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: Schema-3 manifest with Android ``sideload`` never selects a candidate on ``chplay``."""
    dist_mod = load_module("core.distribution")
    manifest_mod = load_module("core.update_manifest")
    update_packages_mod = load_module("platform_utils.update_packages")
    platform_utils = load_module("platform_utils")

    monkeypatch.setattr(platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)

    chplay_profile = dist_mod.build_distribution_profile(dist_mod.DistributionChannel.CHPLAY)
    target = update_packages_mod.detect_runtime_target(distribution=chplay_profile)
    assert target.install_channel == "chplay"

    apk_bytes = make_minimal_apk_bytes()
    pkg_entry = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
    )
    doc = local_release_server.build_manifest(release_version="2.6.0", packages=[pkg_entry])
    manifest = manifest_mod.parse_manifest(doc, expected_release_version="2.6.0")

    assert manifest_mod.select_candidate(
        manifest, current_version="2.5.1", target=target
    ) is None


def test_t2_f02_msix_install_channel_never_falls_back_to_bootstrapper(
    local_release_server,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F2: MSIX target stays ``msix`` even when HKLM says ``msi``; never matches ``bootstrapper``."""
    win_mod = load_module("platform_utils.windows_update")
    manifest_mod = load_module("core.update_manifest")

    monkeypatch.setattr(win_mod, "has_package_identity", lambda: True)
    monkeypatch.setattr(win_mod, "read_install_channel", lambda: "bootstrapper")
    target = win_mod.detect_windows_runtime_target()
    assert target.install_channel == "msix"

    pkg_entry = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-Setup-2.6.0.exe",
        data=b"MZ" + b"\x00" * 64,
        platform="windows",
        architecture="x64",
        package_type="exe",
        install_channel="bootstrapper",
    )
    doc = local_release_server.build_manifest(release_version="2.6.0", packages=[pkg_entry])
    manifest = manifest_mod.parse_manifest(doc, expected_release_version="2.6.0")
    assert manifest_mod.select_candidate(
        manifest, current_version="2.5.1", target=target
    ) is None


def test_t2_f02_stale_update_event_does_not_surface_banner_on_store_channel(
    fake_page,
) -> None:
    """F2: Even a manually injected ``UPDATE_AVAILABLE`` event leaves Store banner hidden."""
    coord_mod = load_module("core.update_coordinator")
    controller = _make_minimal_update_controller(fake_page, "chplay")
    stale_candidate = SimpleNamespace(
        manifest=SimpleNamespace(
            release_version="9.9.9",
            release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v9.9.9",
        )
    )
    controller._update_banner.visible = False
    controller._on_update_event(
        coord_mod.UpdateEvent(coord_mod.UpdateEventKind.UPDATE_AVAILABLE, stale_candidate)
    )
    asyncio.run(
        controller._apply_update_event(
            coord_mod.UpdateEvent(coord_mod.UpdateEventKind.UPDATE_AVAILABLE, stale_candidate)
        )
    )
    assert controller._update_banner.visible is False
    assert controller._update_candidate is None


# ---------------------------------------------------------------------------
# F3: Store-Compliant Settings & Banner UI (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f03_msstore_missing_product_id_handles_store_link_gracefully(
    fake_page,
) -> None:
    """F3: ``msstore`` without ``store_product_id`` hides Store button & handles click safely."""
    import flet as ft

    dist_mod = load_module("core.distribution")
    sys_sec = load_module("gui.components.settings.system_section")

    profile = dist_mod.build_distribution_profile(
        dist_mod.DistributionChannel.MSSTORE,
        store_product_id=None,
    )
    view = SimpleNamespace(
        _distribution_profile=profile,
        _on_autostart_toggle=lambda _e: None,
        _toggle_bg_check_ui=lambda: None,
        _handle_check_update=lambda _e: None,
        _handle_open_store_url=lambda _e=None: None,
        _make_themed_label=lambda text: ft.Text(text),
        _build_hint=lambda text: ft.Container(content=ft.Text(text)),
        _update_status_group=ft.Column([]),
        _interval_field=ft.Dropdown(options=[]),
        _fetch_months_field=ft.TextField(),
    )
    sys_sec.init_system_controls(view)
    sys_sec.build_system_section(view)
    assert view._store_link_btn.visible is False
    assert profile.store_url is None

    controller = _make_minimal_update_controller(fake_page, "msstore")
    assert controller._open_store_url("") is False


def test_t2_f03_chplay_launch_url_market_failure_falls_back_to_https(
    fake_page,
) -> None:
    """F3: When ``market://`` scheme fails, ``_open_store_url`` falls back to Play HTTPS URL."""
    fake_page.fail_url_schemes.add("market")
    controller = _make_minimal_update_controller(fake_page, "chplay")
    profile = controller._distribution_profile

    ok = controller._open_store_url(profile.store_url, profile.store_fallback_url)
    assert ok is True
    deadline = time.monotonic() + 3.0
    while not fake_page.launched_urls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fake_page.launched_urls == [
        f"https://play.google.com/store/apps/details?id={DEFAULT_ANDROID_PACKAGE_ID}"
    ]


def test_t2_f03_settings_snapshot_save_does_not_re_enable_hidden_store_controls(
    fake_page,
) -> None:
    """F3: Applying ``SettingsFormSnapshot`` keeps Store update switch & button hidden."""
    dist_mod = load_module("core.distribution")
    sv_mod = load_module("gui.components.settings_view")
    form_mod = load_module("gui.view_models.settings_form")

    for channel in (dist_mod.DistributionChannel.CHPLAY, dist_mod.DistributionChannel.MSSTORE):
        view = sv_mod.SettingsView(
            page=fake_page,
            orchestrator=None,
            on_close=lambda: None,
            distribution_profile=dist_mod.build_distribution_profile(channel),
        )
        snap = form_mod.SettingsFormSnapshot.from_form_values({"auto_update_enabled": True})
        view._apply_snapshot_to_controls(snap)
        assert view._sw_auto_update.visible is False
        assert view._check_update_btn.visible is False


def test_t2_f03_debug_panel_update_check_hidden_and_safe_on_store_channels(
    fake_page,
    local_release_server,
) -> None:
    """F3: Debug force-check control is excluded on Store channels and calling it is a no-op."""
    dist_mod = load_module("core.distribution")
    sv_mod = load_module("gui.components.settings_view")

    view = sv_mod.SettingsView(
        page=fake_page,
        orchestrator=None,
        on_close=lambda: None,
        distribution_profile=dist_mod.build_distribution_profile(
            dist_mod.DistributionChannel.CHPLAY
        ),
    )
    view._do_force_check_update()
    assert local_release_server.request_count == 0


def test_t2_f03_open_store_url_rejects_disallowed_schemes(fake_page) -> None:
    """F3: Disallowed URL schemes (``javascript:``, ``file:``, ``http:``) are rejected."""
    controller = _make_minimal_update_controller(fake_page, "chplay")
    for bad_url in (
        "javascript:alert(1)",
        "file:///C:/Windows/System32/calc.exe",
        "http://play.google.com/store/apps/details?id=com.uthelper.uthelper",
    ):
        assert controller._open_store_url(bad_url) is False
    assert fake_page.launched_urls == []


# ---------------------------------------------------------------------------
# F4: Android Storage Path & Privacy / Consent Audit (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f04_android_storage_data_used_when_flet_app_data_unset(
    tmp_path: Path,
) -> None:
    """F4: With ``FLET_APP_STORAGE_DATA`` set and ``FLET_APP_DATA`` unset, Android uses it."""
    target_dir = tmp_path / "android_storage_data"
    code = (
        "import sys\n"
        "sys._ANDROID_ = True\n"
        "import config\n"
        "data_dir = config.get_data_dir()\n"
        f"assert str(data_dir).startswith({str(target_dir)!r}), data_dir\n"
    )
    proc = run_isolated_python(
        code,
        env_overrides={
            "FLET_APP_STORAGE_DATA": str(target_dir),
            "FLET_APP_DATA": None,
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_t2_f04_android_whitespace_and_missing_env_vars_fallback(
    tmp_path: Path,
) -> None:
    """F4: Whitespace/empty ``FLET_APP_STORAGE_DATA`` & ``FLET_APP_DATA`` fall back to ``~/.uthelper``."""
    fake_home = tmp_path / "home_dir"
    fake_home.mkdir(parents=True, exist_ok=True)
    code = (
        "import pathlib, sys\n"
        "sys._ANDROID_ = True\n"
        "import config\n"
        "data_dir = config.get_data_dir()\n"
        "expected = pathlib.Path.home() / '.uthelper'\n"
        "assert data_dir == expected, (data_dir, expected)\n"
    )
    proc = run_isolated_python(
        code,
        env_overrides={
            "FLET_APP_STORAGE_DATA": "   ",
            "FLET_APP_DATA": "",
            "USERPROFILE": str(fake_home),
            "HOME": str(fake_home),
        },
        tmp_path=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_t2_f04_crash_reporting_consent_disabled_purges_pending_spool(
    tmp_path: Path,
) -> None:
    """F4: Saving ``CRASH_REPORTING_CONSENT='disabled'`` clears pending telemetry spool immediately."""
    code = (
        "from pathlib import Path\n"
        "import config\n"
        "from diagnostics.models import AppPhase\n"
        "from diagnostics.runtime import create_default_runtime\n"
        "data_dir = config.get_data_dir()\n"
        "config.settings.CRASH_REPORTING_CONSENT = 'enabled'\n"
        "runtime = create_default_runtime(data_dir, development=True)\n"
        "runtime.start()\n"
        "try:\n"
        "    event_id = runtime.record_exception(RuntimeError('synthetic-probe'), AppPhase.GUI)\n"
        "    assert event_id is not None\n"
        "    assert len(runtime.spool.pending()) == 1\n"
        "    config.settings.CRASH_REPORTING_CONSENT = 'disabled'\n"
        "    assert config.save_settings() is True\n"
        "    assert runtime.spool.pending() == ()\n"
        "    blocked = runtime.record_exception(RuntimeError('after-disable'), AppPhase.GUI)\n"
        "    assert blocked is None\n"
        "    assert runtime.spool.pending() == ()\n"
        "finally:\n"
        "    runtime.close(clean=True)\n"
    )
    proc = run_isolated_python(code, tmp_path=tmp_path)
    assert proc.returncode == 0, proc.stderr


def test_t2_f04_migrate_settings_data_preserves_explicit_disabled_consent() -> None:
    """F4: ``migrate_settings_data`` preserves explicit ``'disabled'`` while migrating ``'not_asked'``."""
    config_mod = load_module("config")
    preserved = config_mod.migrate_settings_data({"CRASH_REPORTING_CONSENT": "disabled"})
    assert preserved["CRASH_REPORTING_CONSENT"] == "disabled"

    migrated = config_mod.migrate_settings_data({"CRASH_REPORTING_CONSENT": "not_asked"})
    assert migrated["CRASH_REPORTING_CONSENT"] == "enabled"


def test_t2_f04_privacy_and_terms_enforce_opt_out_and_android_storage_disclosure() -> None:
    """F4: ``PRIVACY.md`` & ``TERMS.md`` keep opt-out, forbidden-field, & Android storage disclosures."""
    privacy = (WORKTREE_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    terms = (WORKTREE_ROOT / "docs" / "TERMS.md").read_text(encoding="utf-8")

    assert "disabled" in privacy.lower() or "disable" in privacy.lower()
    assert "What is forbidden from remote reports" in privacy
    assert "FLET_APP_STORAGE_DATA" in privacy or "Android" in privacy
    assert "PRIVACY.md" in terms


# ---------------------------------------------------------------------------
# F5: Mobile LTS .apk.part Fix & Installer Integrity (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f05_simulated_bridge_strict_apk_suffix_and_kotlin_rename_order(
    android_bridge,
    tmp_path: Path,
) -> None:
    """F5: ``get_package_archive_info`` rejects ``.apk.part``/``.tmp``/no-ext; Kotlin renames before parse."""
    apk_bytes = make_minimal_apk_bytes(version_code=2_006_000)
    for bad_name in ("update-123.apk.part", "update-123.tmp", "update-123"):
        p = tmp_path / bad_name
        p.write_bytes(apk_bytes)
        assert android_bridge.get_package_archive_info(p) is None

    good_p = tmp_path / "update-123.apk"
    good_p.write_bytes(apk_bytes)
    info = android_bridge.get_package_archive_info(good_p)
    assert info is not None and info["package_id"] == DEFAULT_ANDROID_PACKAGE_ID

    installer_kt = next(_ANDROID_PLUGIN_ROOT.rglob("ApkUpdateInstaller.kt")).read_text(
        encoding="utf-8"
    )
    rename_match = re.search(r"renameTo\s*\(", installer_kt)
    read_match = re.search(
        r"(?:readArchiveMetadata|getPackageArchiveInfo)\s*\(", installer_kt
    )
    assert (
        rename_match is not None
        and read_match is not None
        and rename_match.start() < read_match.start()
    ), (
        "ApkUpdateInstaller.kt must rename partial APK to .apk BEFORE calling readArchiveMetadata()/getPackageArchiveInfo()"
    )

    flow_test_kt = next(_ANDROID_PLUGIN_ROOT.rglob("ApkUpdateInstallerFlowTest.kt")).read_text(
        encoding="utf-8"
    )
    assert not re.search(
        r"setPackageArchiveInfo\s*\([^)]*\.apk\.part", flow_test_kt, re.DOTALL
    ), "ApkUpdateInstallerFlowTest.kt must not register shadow archive info for .apk.part"
    assert "update-$releaseKey.apk.part" not in flow_test_kt, (
        "ApkUpdateInstallerFlowTest.kt must not mask the .apk.part bug by registering shadow info on .apk.part"
    )


def test_t2_f05_corrupt_zip_magic_rejected_by_verifier_and_bridge(
    android_bridge,
    tmp_path: Path,
) -> None:
    """F5: Non-ZIP magic bytes fail ``DownloadedPackageVerifier`` & ``install_update`` (``invalid_apk``)."""
    pkg_mod = load_module("platform_utils.update_packages")
    models_mod = load_module("core.update_models")

    bad_bytes = b"NOTAZIP" + b"\x00" * 120
    bad_apk = tmp_path / "corrupt.apk"
    bad_apk.write_bytes(bad_bytes)

    pkg = models_mod.ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
        sha256=sha256_hex(bad_bytes),
        size=len(bad_bytes),
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "android_package_installer"},
    )
    candidate = models_mod.UpdateCandidate(
        manifest=models_mod.ReleaseManifest(
            schema_version=3,
            release_version="2.6.0",
            minimum_supported_version="2.5.1",
            published_at="2026-11-18T12:00:00Z",
            release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
            packages=(pkg,),
        ),
        package=pkg,
        automatic_install_allowed=True,
        required_update=False,
    )
    ver = pkg_mod.DownloadedPackageVerifier().verify(bad_apk, candidate)
    assert ver.verified is False
    assert "ZIP" in ver.reason

    res = asyncio.run(
        android_bridge.install_update(
            str(bad_apk),
            pkg.sha256,
            pkg.size,
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    assert res["status"] == "failed"
    assert res["reason"] == "invalid_apk"


def test_t2_f05_sha256_mismatch_deletes_staged_partial_without_leaving_apk(
    android_bridge,
    tmp_path: Path,
) -> None:
    """F5: Tampered payload fails ``sha256_mismatch`` and leaves zero files in ``verified_cache``."""
    real_bytes = make_minimal_apk_bytes(b"real-payload", version_code=2_006_000)
    tampered_bytes = make_minimal_apk_bytes(b"evil-payload", version_code=2_006_000)
    src_apk = tmp_path / "tampered.apk"
    src_apk.write_bytes(tampered_bytes)

    res = asyncio.run(
        android_bridge.install_update(
            str(src_apk),
            sha256_hex(real_bytes),
            len(tampered_bytes),
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    assert res["status"] == "failed"
    assert res["reason"] == "sha256_mismatch"
    assert list(android_bridge.verified_cache.iterdir()) == []


def test_t2_f05_size_mismatch_rejected_by_downloader_and_installer(
    android_bridge,
    tmp_path: Path,
) -> None:
    """F5: Size mismatch fails both ``download`` (``download_size_mismatch``) and ``install_update``."""
    real_bytes = make_minimal_apk_bytes(b"actual-payload", version_code=2_006_000)
    url = "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk"
    android_bridge.register_apk_payload(url, real_bytes)

    dl_res = asyncio.run(
        android_bridge.start_update_download(
            url,
            sha256_hex(real_bytes),
            len(real_bytes) + 100,
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    while dl_res["status"] in {"pending", "running"}:
        dl_res = asyncio.run(
            android_bridge.get_update_download_status(
                url,
                sha256_hex(real_bytes),
                len(real_bytes) + 100,
                DEFAULT_ANDROID_PACKAGE_ID,
                2_006_000,
                DEFAULT_TRUSTED_SIGNER_SHA256,
            )
        )
    assert dl_res["status"] == "failed"
    assert dl_res["reason"] == "download_size_mismatch"

    src_apk = tmp_path / "short.apk"
    src_apk.write_bytes(real_bytes)
    inst_res = asyncio.run(
        android_bridge.install_update(
            str(src_apk),
            sha256_hex(real_bytes),
            len(real_bytes) + 100,
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    assert inst_res["status"] == "failed"
    assert inst_res["reason"] == "size_mismatch"


@pytest.mark.parametrize("bad_vc", [2_005_001, 2_004_000])
def test_t2_f05_already_installed_and_downgrade_version_code_rejected(
    android_bridge,
    tmp_path: Path,
    bad_vc: int,
) -> None:
    """F5: ``versionCode <= installed_version_code`` is rejected with ``already_installed``."""
    apk_bytes = make_minimal_apk_bytes(version_code=bad_vc)
    src_apk = tmp_path / f"vc-{bad_vc}.apk"
    src_apk.write_bytes(apk_bytes)

    res = asyncio.run(
        android_bridge.install_update(
            str(src_apk),
            sha256_hex(apk_bytes),
            len(apk_bytes),
            DEFAULT_ANDROID_PACKAGE_ID,
            bad_vc,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    assert res["status"] == "failed"
    assert res["reason"] == "already_installed"


# ---------------------------------------------------------------------------
# F6: Mobile LTS Bridge Error Propagation & Null Safety (Boundaries & Failures)
# ---------------------------------------------------------------------------


def test_t2_f06_safe_run_task_missing_session_surfaces_bridge_error(
    android_bridge,
    fake_page,
    tmp_path: Path,
) -> None:
    """F6: When ``page.session`` is ``None``, ``_safe_run_task`` returns ``None`` & launcher fails cleanly."""
    android_mod = load_module("platform_utils.android_updates")
    models_mod = load_module("core.update_models")

    fake_page.session = None
    controller = _make_minimal_update_controller(fake_page, "apk_lts")
    assert controller._safe_run_task(lambda: None) is None

    apk_bytes = make_minimal_apk_bytes(version_code=2_006_000)
    apk_path = tmp_path / "u.apk"
    apk_path.write_bytes(apk_bytes)
    pkg = models_mod.ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
        sha256=sha256_hex(apk_bytes),
        size=len(apk_bytes),
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "android_package_installer"},
    )
    launcher = android_mod.AndroidPackageLauncher(
        android_bridge, controller._safe_run_task, lambda: "2.6.0"
    )
    result = launcher.launch(apk_path, pkg)
    assert result.acknowledged is False
    assert result.status == "bridge_failed"


@pytest.mark.parametrize(
    "fault_kind",
    ["runtime_error", "timeout_error", "none_response", "non_dict_response"],
)
def test_t2_f06_bridge_exception_and_malformed_response_propagate_cleanly(
    android_bridge,
    fake_page,
    fault_kind: str,
) -> None:
    """F6: Bridge exceptions/malformed responses fail fast instead of hanging."""
    android_mod = load_module("platform_utils.android_updates")
    models_mod = load_module("core.update_models")

    if fault_kind == "runtime_error":
        android_bridge.inject_exception = RuntimeError("bridge crashed")
    elif fault_kind == "timeout_error":
        android_bridge.inject_exception = TimeoutError("bridge timeout")
    elif fault_kind == "none_response":
        android_bridge.malformed_response = None
        android_bridge.inject_exception = None
        # Force malformed_response path by wrapping method
        async def _ret_none(*_a):
            return None
        android_bridge.install_update = _ret_none
    else:
        android_bridge.malformed_response = ["unexpected", "list"]

    pkg = models_mod.ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
        sha256="a" * 64,
        size=100,
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "android_package_installer"},
    )
    launcher = android_mod.AndroidPackageLauncher(
        android_bridge, fake_page.run_task, lambda: "2.6.0"
    )
    result = launcher.launch(Path("/tmp/fake.apk"), pkg)
    assert result.acknowledged is False
    assert result.status == "bridge_failed"


def test_t2_f06_null_update_candidate_in_release_version_provider_never_raises_attribute_error(
    android_bridge,
    fake_page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F6: Calling coordinator's ``release_version_provider`` when ``_update_candidate is None`` is safe."""
    app_controller_mod = load_module("gui.app_controller")
    dist_mod = load_module("core.distribution")
    models_mod = load_module("core.update_models")
    platform_utils = load_module("platform_utils")

    monkeypatch.setattr(platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(
        app_controller_mod,
        "detect_runtime_target",
        lambda: models_mod.RuntimeTarget("android", "universal", "sideload"),
    )

    controller = _make_minimal_update_controller(fake_page, "apk_lts")
    controller._distribution_profile = dist_mod.build_distribution_profile(
        dist_mod.DistributionChannel.APK_LTS
    )
    controller._android_background = android_bridge
    controller._update_candidate = None
    coordinator = controller._create_update_coordinator()
    assert coordinator is not None

    try:
        # Must NOT raise AttributeError: 'NoneType' object has no attribute 'manifest'
        downloader_version = coordinator._downloader._native.release_version_provider()
        launcher_version = coordinator._launcher._native.release_version_provider()
        # With no selected release the provider has no version to report; the
        # contract is ``str | None`` (a fabricated version would corrupt the
        # native versionCode check), matching the sibling tier-1 F6 test.
        assert downloader_version is None or isinstance(downloader_version, str)
        assert launcher_version is None or isinstance(launcher_version, str)
        assert (
            coordinator._downloader.has_pending_download(
                models_mod.ReleasePackage(
                    platform="android",
                    architecture="universal",
                    package_type="apk",
                    install_channel="sideload",
                    url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
                    sha256="a" * 64,
                    size=128,
                    signer_identity="com.uthelper.uthelper",
                    certificate_fingerprint="b" * 64,
                    install_strategy={"kind": "android_package_installer"},
                )
            )
            is False
        )
    finally:
        coordinator.shutdown(timeout_seconds=1.0)


@pytest.mark.parametrize(
    ("reason", "expected_snippet"),
    [
        ("insufficient_space", "Không đủ dung lượng"),
        ("storage_unavailable", "Không truy cập được bộ nhớ"),
    ],
)
def test_t2_f06_insufficient_space_and_storage_unavailable_surface_clear_errors(
    fake_page,
    reason: str,
    expected_snippet: str,
) -> None:
    """F6: ``insufficient_space`` & ``storage_unavailable`` map to specific Vietnamese error copy."""
    coord_mod = load_module("core.update_coordinator")
    controller = _make_minimal_update_controller(fake_page, "apk_lts")
    controller._update_banner.visible = True
    candidate = SimpleNamespace(manifest=SimpleNamespace(release_version="2.6.0"))

    asyncio.run(
        controller._apply_update_event(
            coord_mod.UpdateEvent(
                coord_mod.UpdateEventKind.FAILED,
                candidate,
                message=f"Android download failed: {reason}",
            )
        )
    )
    assert expected_snippet in controller._update_text.value
    assert controller._update_btn.content == "Thử lại"


def test_t2_f06_installed_signer_mismatch_flips_cta_to_release_page(
    android_bridge,
    fake_page,
    tmp_path: Path,
) -> None:
    """F6: ``installed_signer_mismatch`` flips banner CTA to ``Bản phát hành`` opening release notes."""
    coord_mod = load_module("core.update_coordinator")
    android_bridge.installed_signer_sha256 = "F" * 64
    apk_bytes = make_minimal_apk_bytes(version_code=2_006_000)
    apk_path = tmp_path / "u.apk"
    apk_path.write_bytes(apk_bytes)

    res = asyncio.run(
        android_bridge.install_update(
            str(apk_path),
            sha256_hex(apk_bytes),
            len(apk_bytes),
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
    )
    assert res["status"] == "failed"
    assert res["reason"] == "installed_signer_mismatch"

    controller = _make_minimal_update_controller(fake_page, "apk_lts")
    release_notes = "https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0"
    controller._update_candidate = SimpleNamespace(
        manifest=SimpleNamespace(release_version="2.6.0", release_notes_url=release_notes)
    )
    asyncio.run(
        controller._apply_update_event(
            coord_mod.UpdateEvent(
                coord_mod.UpdateEventKind.INSTALL_FAILED,
                controller._update_candidate,
                message=res["reason"],
            )
        )
    )
    assert controller._update_btn.content == "Bản phát hành"
    assert controller._update_open_release_page is True
    opened: list[str] = []
    controller._open_external_update_url = opened.append
    controller._request_update_download()
    assert opened == [release_notes]


def test_t2_f06_user_cancel_update_download_invokes_native_cancel(
    android_bridge,
    fake_page,
) -> None:
    """F6: Cancelling an active Android download invokes ``cancel_update_download`` & emits ``CANCELLED``."""
    android_mod = load_module("platform_utils.android_updates")
    coord_mod = load_module("core.update_coordinator")
    checker_mod = load_module("core.update_checker")
    models_mod = load_module("core.update_models")
    pkg_mod = load_module("platform_utils.update_packages")

    apk_bytes = make_minimal_apk_bytes(version_code=2_006_000)
    url = "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk"
    android_bridge.register_apk_payload(url, apk_bytes)
    android_bridge.set_download_status_plan(["running", "running", "running", "running"])

    pkg = models_mod.ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url=url,
        sha256=sha256_hex(apk_bytes),
        size=len(apk_bytes),
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "android_package_installer"},
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

    events: list[object] = []
    downloader = android_mod.AndroidPackageDownloader(
        android_bridge, fake_page.run_task, lambda: "2.6.0", poll_interval_seconds=0.05
    )
    launcher = android_mod.AndroidPackageLauncher(
        android_bridge, fake_page.run_task, lambda: "2.6.0"
    )
    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        downloader,
        pkg_mod.DownloadedPackageVerifier(),
        launcher,
        models_mod.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        deadline = time.monotonic() + 3.0
        while not any(name == "start_update_download" for name, _ in android_bridge.calls):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        coordinator.cancel_download()
        while not any(e.kind is coord_mod.UpdateEventKind.CANCELLED for e in events):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert any(name == "cancel_update_download" for name, _ in android_bridge.calls)
    finally:
        coordinator.shutdown(timeout_seconds=1.0)


# ---------------------------------------------------------------------------
# F7: PC LTS Windows Launcher UAC/Lock Race & Error Codes (Boundaries & Failures)
# ---------------------------------------------------------------------------


def _run_msiexec_exit_code_scenario(
    local_release_server,
    windows_shim_env,
    fake_page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    exit_code: int,
):
    win_mod = load_module("platform_utils.windows_update")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")
    app_controller_mod = load_module("gui.app_controller")

    monkeypatch.setattr(app_controller_mod.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(app_controller_mod.platform_utils, "IS_ANDROID", False)

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    msi_bytes = msi_path.read_bytes()
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_bytes,
    )
    windows_shim_env.set_ps_mode("valid", version="2.6.0")
    windows_shim_env.set_msi_behavior(exit_code=exit_code, delay_sec=0.05)

    controller = _make_minimal_update_controller(fake_page, "exe_lts")
    controller._update_candidate = candidate
    events: list[object] = []

    def _on_event(ev):
        events.append(ev)
        controller._on_update_event(ev)

    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=tmp_path / "cache"),
        win_mod.WindowsPackageVerifier(timeout_seconds=10.0),
        win_mod.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory,
            acknowledgement_seconds=1.0,
        ),
        models_mod.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        _on_event,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        deadline = time.monotonic() + 10.0
        while not any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        coordinator.confirm_install()
        while not any(
            e.kind
            in {
                coord_mod.UpdateEventKind.INSTALL_FAILED,
                coord_mod.UpdateEventKind.INSTALL_LAUNCHED,
            }
            for e in events
        ):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        if any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events):
            while controller._update_btn.content != "Thử lại":
                assert time.monotonic() < deadline
                time.sleep(0.02)
        return controller, events, coord_mod
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f07_msiexec_1223_uac_denied_preserves_window_and_maps_error(
    local_release_server,
    windows_shim_env,
    fake_page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: UAC refusal (exit 1223) never destroys window and surfaces actionable error copy."""
    controller, events, coord_mod = _run_msiexec_exit_code_scenario(
        local_release_server,
        windows_shim_env,
        fake_page,
        tmp_path,
        monkeypatch,
        exit_code=1223,
    )
    assert fake_page.window.destroyed is False
    assert any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events)
    assert controller._update_btn.disabled is False
    assert controller._update_text.value != "Không mở được trình cài đặt. Hãy thử lại."


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f07_msiexec_1602_user_cancelled_preserves_window(
    local_release_server,
    windows_shim_env,
    fake_page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: User cancellation (exit 1602) keeps window alive with retry enabled."""
    controller, events, coord_mod = _run_msiexec_exit_code_scenario(
        local_release_server,
        windows_shim_env,
        fake_page,
        tmp_path,
        monkeypatch,
        exit_code=1602,
    )
    assert fake_page.window.destroyed is False
    assert any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events)
    assert controller._update_btn.disabled is False
    assert controller._update_text.value != "Không mở được trình cài đặt. Hãy thử lại."


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f07_msiexec_1618_installer_busy_preserves_window(
    local_release_server,
    windows_shim_env,
    fake_page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: Windows Installer busy (exit 1618) keeps window alive and maps clear message."""
    controller, events, coord_mod = _run_msiexec_exit_code_scenario(
        local_release_server,
        windows_shim_env,
        fake_page,
        tmp_path,
        monkeypatch,
        exit_code=1618,
    )
    assert fake_page.window.destroyed is False
    assert any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events)
    assert controller._update_btn.disabled is False
    assert controller._update_text.value != "Không mở được trình cài đặt. Hãy thử lại."


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f07_msiexec_1603_fatal_error_preserves_window(
    local_release_server,
    windows_shim_env,
    fake_page,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7: Installer fatal error (exit 1603) keeps window alive and emits ``INSTALL_FAILED``."""
    controller, events, coord_mod = _run_msiexec_exit_code_scenario(
        local_release_server,
        windows_shim_env,
        fake_page,
        tmp_path,
        monkeypatch,
        exit_code=1603,
    )
    assert fake_page.window.destroyed is False
    assert any(e.kind is coord_mod.UpdateEventKind.INSTALL_FAILED for e in events)
    assert controller._update_btn.disabled is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f07_launcher_missing_executable_and_double_launch_rejected(
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F7: Missing installer path and invalid strategy return ``acknowledged=False``."""
    win_mod = load_module("platform_utils.windows_update")
    models_mod = load_module("core.update_models")

    pkg = models_mod.ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msi",
        sha256="a" * 64,
        size=100,
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "launch_msi"},
    )
    launcher = win_mod.WindowsPackageLauncher(
        process_factory=windows_shim_env.process_factory,
        acknowledgement_seconds=0.5,
    )
    missing_res = launcher.launch(tmp_path / "missing.msi", pkg)
    assert missing_res.acknowledged is False
    assert "unavailable" in missing_res.reason

    msi_path = windows_shim_env.make_msi_file(tmp_path / "present.msi")
    bad_strategy_pkg = models_mod.ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        url=pkg.url,
        sha256=pkg.sha256,
        size=pkg.size,
        signer_identity=pkg.signer_identity,
        certificate_fingerprint=pkg.certificate_fingerprint,
        install_strategy={"kind": "manual_sideload"},
    )
    bad_res = launcher.launch(msi_path, bad_strategy_pkg)
    assert bad_res.acknowledged is False
    assert "not allowed" in bad_res.reason


# ---------------------------------------------------------------------------
# F8: PC LTS Verifier Timeout, Identity Rejections & Shutdown (Boundaries)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f08_cold_powershell_timeout_preserves_cache_for_immediate_retry(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F8: Cold PowerShell timeout fails cleanly; M2 timeout resilience preserves cache or retries."""
    win_mod = load_module("platform_utils.windows_update")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "src.msi")
    msi_bytes = msi_path.read_bytes()
    candidate = _build_windows_candidate(
        local_release_server, filename="UTHelper-2.6.0.msi", data=msi_bytes
    )

    cache_dir = tmp_path / "cache"
    verifier = win_mod.WindowsPackageVerifier(timeout_seconds=0.5)

    # First verification times out in PowerShell
    windows_shim_env.set_ps_mode("timeout")
    direct_res = verifier.verify(msi_path, candidate)
    assert direct_res.verified is False

    # Now exercise through coordinator with sequence: timeout first, then valid on retry
    windows_shim_env.set_ps_mode("valid", version="2.6.0")
    windows_shim_env.set_ps_sequence(["timeout", "valid", "valid", "valid", "valid"])
    events: list[object] = []
    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=cache_dir),
        verifier,
        win_mod.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory,
            acknowledgement_seconds=0.5,
        ),
        models_mod.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        deadline = time.monotonic() + 8.0
        while not any(
            e.kind in {coord_mod.UpdateEventKind.FAILED, coord_mod.UpdateEventKind.READY_TO_INSTALL}
            for e in events
        ):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert any(e.kind is coord_mod.UpdateEventKind.FAILED for e in events), (
            f"Expected FAILED on attempt 1 cold PowerShell timeout, got: {events}"
        )
        cached_msi = cache_dir / "UTHelper-2.6.0.msi"
        assert cached_msi.is_file(), (
            "Downloaded .msi cache file must remain on disk after cold PowerShell timeout"
        )

        windows_shim_env.set_ps_mode("valid", version="2.6.0")
        events.clear()
        local_release_server.clear_requests()
        coordinator.request_download(candidate)
        deadline = time.monotonic() + 8.0
        while not any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert any(e.kind is coord_mod.UpdateEventKind.READY_TO_INSTALL for e in events)
        assert local_release_server.request_count == 0, (
            f"Retry must reuse preserved cache without HTTP requests; got {local_release_server.requests}"
        )
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f08_untrusted_signer_sha256_rejected_never_launches_installer(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F8: Untrusted Authenticode SHA-256 fails verification and never launches ``msiexec.exe``."""
    win_mod = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
    )
    windows_shim_env.set_ps_mode("bad_signer")
    res = win_mod.WindowsPackageVerifier(timeout_seconds=5.0).verify(msi_path, candidate)
    assert res.verified is False
    assert not any(c["tool"] == "msiexec.exe" for c in windows_shim_env.read_calls())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f08_wrong_signer_subject_rejected(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F8: Mismatched signer subject is rejected by ``WindowsPackageVerifier``."""
    win_mod = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
    )
    windows_shim_env.set_ps_mode("bad_subject")
    res = win_mod.WindowsPackageVerifier(timeout_seconds=5.0).verify(msi_path, candidate)
    assert res.verified is False
    assert "subject" in res.reason.lower() or "verification" in res.reason.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f08_missing_authenticode_timestamp_rejected(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F8: Signature without Authenticode timestamp (``timestamped=False``) is rejected."""
    win_mod = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
    )
    windows_shim_env.set_ps_mode("not_timestamped")
    res = win_mod.WindowsPackageVerifier(timeout_seconds=5.0).verify(msi_path, candidate)
    assert res.verified is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
@pytest.mark.parametrize("ps_mode", ["bad_upgrade_code", "bad_version", "bad_template"])
def test_t2_f08_wrong_msi_metadata_rejected(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
    ps_mode: str,
) -> None:
    """F8: Wrong MSI ``UpgradeCode``, ``ProductVersion``, or non-x64 ``Template`` is rejected."""
    win_mod = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
    )
    windows_shim_env.set_ps_mode(ps_mode)
    res = win_mod.WindowsPackageVerifier(timeout_seconds=5.0).verify(msi_path, candidate)
    assert res.verified is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim compiler requires Windows")
def test_t2_f08_coordinator_shutdown_returns_within_bounded_budget_under_slow_probe(
    local_release_server,
    windows_shim_env,
    tmp_path: Path,
) -> None:
    """F8: ``UpdateCoordinator.shutdown()`` returns within bounded budget even under slow probe."""
    win_mod = load_module("platform_utils.windows_update")
    checker_mod = load_module("core.update_checker")
    coord_mod = load_module("core.update_coordinator")
    models_mod = load_module("core.update_models")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    candidate = _build_windows_candidate(
        local_release_server,
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
    )
    windows_shim_env.set_ps_mode("valid", delay_sec=3.0, version="2.6.0")

    coordinator = coord_mod.UpdateCoordinator(
        checker_mod.GitHubReleaseClient(),
        checker_mod.VerifiedDownloader(cache_dir=tmp_path / "cache"),
        win_mod.WindowsPackageVerifier(timeout_seconds=5.0),
        win_mod.WindowsPackageLauncher(process_factory=windows_shim_env.process_factory),
        models_mod.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        lambda _e: None,
        automatic_enabled=False,
    )
    coordinator.request_download(candidate)
    time.sleep(0.1)
    started = time.monotonic()
    coordinator.shutdown(timeout_seconds=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0


# ---------------------------------------------------------------------------
# F9: CH Play Stripping & apk_lts Retention Verifier Failures (Boundaries)
# ---------------------------------------------------------------------------


def test_t2_f09_chplay_verifier_rejects_request_install_packages(tmp_path: Path) -> None:
    """F9: ``chplay`` verifier rejects ``REQUEST_INSTALL_PACKAGES`` in manifest."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        "<application ",
        '<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n  <application ',
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "bad-chplay.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f09_chplay_verifier_rejects_uth_update_file_provider(tmp_path: Path) -> None:
    """F9: ``chplay`` verifier rejects ``UthUpdateFileProvider`` declaration in manifest."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        "<receiver ",
        '<provider android:name=".update.UthUpdateFileProvider" android:authorities="com.uthelper.uthelper.uthupdates" />\n    <receiver ',
        1,
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "bad-provider.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


@pytest.mark.parametrize(
    "forbidden_symbol",
    [
        "Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;",
        "Lcom/uthelper/backgroundsync/update/ApkUpdateDownloadManager;",
        "Lcom/uthelper/backgroundsync/update/UthUpdateFileProvider;",
        "ACTION_MANAGE_UNKNOWN_APP_SOURCES",
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "install_update",
        "cancel_update",
    ],
)
def test_t2_f09_chplay_verifier_rejects_updater_dex_symbols(
    tmp_path: Path,
    forbidden_symbol: str,
) -> None:
    """F9: ``chplay`` verifier rejects prohibited APK updater classes/strings in ``classes.dex``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "leaked-dex.aab",
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;", forbidden_symbol),
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f09_chplay_verifier_rejects_uth_update_paths_xml_resource(tmp_path: Path) -> None:
    """F9: ``chplay`` verifier rejects ``res/xml/uth_update_paths.xml`` left inside ``.aab``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "leaked-xml-paths.aab",
        p_align=0x4000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
        extra_files={
            "base/res/xml/uth_update_paths.xml": (
                b'<?xml version="1.0" encoding="utf-8"?>\n'
                b"<paths><cache-path name=\"verified_updates\" path=\"verified_updates/\" /></paths>\n"
            ),
        },
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f09_apk_lts_verifier_rejects_unsigned_or_wrong_signer_apk(
    tmp_path: Path,
) -> None:
    """F9: ``apk_lts`` verifier rejects unsigned or mismatched-cert APKs."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", signer_sha256="F" * 64)
    apk_path = make_synthetic_apk(
        tmp_path / "UTHelper-2.5.1.apk",
        p_align=0x4000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
    )
    proc = _run_verify_android(
        artifact_path=apk_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="apk_lts",
    )
    assert proc.returncode != 0


def test_t2_f09_apk_lts_verifier_rejects_missing_notification_receiver(
    tmp_path: Path,
) -> None:
    """F9: ``apk_lts`` verifier rejects APK missing any required notification ``<receiver>``."""
    incomplete_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        '<receiver android:name="com.uthelper.backgroundsync.notification.RescheduleReceiver" />',
        "",
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=incomplete_manifest)
    apk_path = make_synthetic_apk(
        tmp_path / "UTHelper-2.5.1.apk",
        p_align=0x4000,
        manifest_xml=incomplete_manifest,
    )
    proc = _run_verify_android(
        artifact_path=apk_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="apk_lts",
    )
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# F10: CH Play Permissions, HTTPS & Backup Hardening (Boundaries & Failures)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_perm",
    [
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
    ],
)
def test_t2_f10_chplay_verifier_rejects_foreground_service_permissions(
    tmp_path: Path,
    forbidden_perm: str,
) -> None:
    """F10: ``chplay`` verifier rejects ``FOREGROUND_SERVICE*`` permissions."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        "<application ",
        f'<uses-permission android:name="{forbidden_perm}" />\n  <application ',
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "fgs.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f10_chplay_verifier_rejects_use_exact_alarm_permission(
    tmp_path: Path,
) -> None:
    """F10: ``chplay`` verifier rejects ``android.permission.USE_EXACT_ALARM``."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        "<application ",
        '<uses-permission android:name="android.permission.USE_EXACT_ALARM" />\n  <application ',
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "use-exact-alarm.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f10_chplay_verifier_rejects_uses_cleartext_traffic_true(
    tmp_path: Path,
) -> None:
    """F10: ``chplay`` verifier rejects ``android:usesCleartextTraffic="true"``."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        'android:allowBackup="false"',
        'android:allowBackup="false" android:usesCleartextTraffic="true"',
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "cleartext.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


@pytest.mark.parametrize(
    "backup_attr",
    ['android:allowBackup="true"', ""],
)
def test_t2_f10_chplay_verifier_rejects_allow_backup_true_or_missing(
    tmp_path: Path,
    backup_attr: str,
) -> None:
    """F10: ``chplay`` verifier rejects ``allowBackup="true"`` or omitted ``allowBackup``."""
    bad_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        'android:allowBackup="false"',
        backup_attr,
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=bad_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "backup.aab",
        p_align=0x4000,
        manifest_xml=bad_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f10_http_urls_rejected_by_moodle_sentry_and_update_validators() -> None:
    """F10: ``http://`` URLs are rejected across Moodle, Sentry DSN, & release manifest validators."""
    moodle_sites = load_module("core.moodle_sites")
    release_config = load_module("diagnostics.release_config")
    manifest_mod = load_module("core.update_manifest")

    assert moodle_sites.moodle_site_from_origin("http://courses.ut.edu.vn") is None
    with pytest.raises(release_config.PublicConfigError):
        release_config.validate_public_sentry_dsn("http://publickey1234@o1.ingest.sentry.io/1")
    with pytest.raises(manifest_mod.ManifestError):
        manifest_mod.parse_manifest(
            {
                "schema_version": 3,
                "release_version": "2.6.0",
                "minimum_supported_version": "2.5.1",
                "published_at": "2026-11-18T12:00:00Z",
                "release_notes_url": "http://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
                "packages": [],
            },
            expected_release_version="2.6.0",
        )


# ---------------------------------------------------------------------------
# F11: CH Play .aab 16 KB Alignment & Verifier Failures (Boundaries)
# ---------------------------------------------------------------------------


def test_t2_f11_chplay_verifier_rejects_4kb_elf_alignment(tmp_path: Path) -> None:
    """F11: ``chplay`` verifier rejects 4 KB ELF64 ``.so`` (``p_align = 0x1000``)."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "4kb.aab",
        p_align=0x1000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_mixed_16kb_and_4kb_so_in_same_aab(
    tmp_path: Path,
) -> None:
    """F11: One 4 KB ``.so`` mixed alongside 16 KB ``.so`` files fails the entire ``.aab``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "mixed.aab",
        p_align=0x4000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
        extra_files={"base/lib/arm64-v8a/libbad.so": make_elf64_so(0x1000)},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_target_sdk_below_35(tmp_path: Path) -> None:
    """F11: ``chplay`` verifier rejects ``android:targetSdkVersion="34"`` (< 35)."""
    old_sdk_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        'android:targetSdkVersion="35"', 'android:targetSdkVersion="34"'
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=old_sdk_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "sdk34.aab",
        p_align=0x4000,
        manifest_xml=old_sdk_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_mismatched_distribution_channel_asset(
    tmp_path: Path,
) -> None:
    """F11: ``chplay`` verifier rejects ``.aab`` embedding ``"channel": "apk_lts"``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "mismatched-channel.aab",
        p_align=0x4000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "apk_lts", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_missing_notification_receivers(
    tmp_path: Path,
) -> None:
    """F11: ``chplay`` verifier rejects ``.aab`` missing ``DeadlineAlarmReceiver``."""
    broken_manifest = _CLEAN_CHPLAY_MANIFEST_XML.replace(
        '<receiver android:name="com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver" />',
        "",
    )
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk", manifest_xml=broken_manifest)
    aab_path = make_synthetic_aab(
        tmp_path / "missing-receiver.aab",
        p_align=0x4000,
        manifest_xml=broken_manifest,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_updater_symbol_in_multidex_classes2_dex(
    tmp_path: Path,
) -> None:
    """F11: ``chplay`` verifier scans multidex ``base/dex/classes2.dex`` for leaked updater symbols."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "multidex-leaked.aab",
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
        extra_dex_files={
            "base/dex/classes2.dex": make_minimal_dex(
                ["Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;"]
            ),
        },
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


def test_t2_f11_chplay_verifier_rejects_multi_pt_load_elf_with_4kb_second_segment(
    tmp_path: Path,
) -> None:
    """F11: ``chplay`` verifier rejects multi-``PT_LOAD`` ELF64 where a second segment has ``p_align=0x1000``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "multi-ptload.aab",
        p_align=0x4000,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={"channel": "chplay", "android_package_id": DEFAULT_ANDROID_PACKAGE_ID},
        extra_files={
            "base/lib/arm64-v8a/libmulti.so": make_elf64_so(segment_aligns=(0x4000, 0x1000)),
        },
    )
    proc = _run_verify_android(
        artifact_path=aab_path,
        output_path=tmp_path / "out.json",
        sdk_dir=sdk_dir,
        channel="chplay",
    )
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# F12: Microsoft Store MSIX Packaging & Verifier Failures (Boundaries)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t2_f12_package_msix_rejects_non_4_part_version() -> None:
    """F12: ``package_msix.ps1`` rejects 3-part version ``2.5.1``."""
    stage_root = WORKTREE_ROOT / "build" / f"e2e-msix-badver-{uuid.uuid4().hex[:8]}"
    bundle_dir = stage_root / "bundle"
    out_msix = stage_root / "out" / "bad.msix"
    try:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 64)
        (bundle_dir / "UTHelperAutostart.exe").write_bytes(b"MZ" + b"\x00" * 64)
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(WORKTREE_ROOT / "scripts" / "package_msix.ps1"),
                "-BundleDir",
                str(bundle_dir),
                "-Version",
                "2.5.1",
                "-Publisher",
                "CN=UTHelper",
                "-Output",
                str(out_msix),
            ],
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode != 0
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t2_f12_package_msix_rejects_empty_publisher_and_outside_workspace_path(
    tmp_path: Path,
) -> None:
    """F12: ``package_msix.ps1`` rejects empty Publisher and outside-workspace ``BundleDir``."""
    stage_root = WORKTREE_ROOT / "build" / f"e2e-msix-badpub-{uuid.uuid4().hex[:8]}"
    bundle_dir = stage_root / "bundle"
    out_msix = stage_root / "out" / "bad.msix"
    try:
        bundle_dir.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 64)
        empty_pub = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(WORKTREE_ROOT / "scripts" / "package_msix.ps1"),
                "-BundleDir",
                str(bundle_dir),
                "-Version",
                "2.5.1.0",
                "-Publisher",
                "   ",
                "-Output",
                str(out_msix),
            ],
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert empty_pub.returncode != 0

        outside_bundle = tmp_path / "outside_bundle"
        outside_bundle.mkdir(parents=True, exist_ok=True)
        (outside_bundle / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 64)
        outside_proc = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(WORKTREE_ROOT / "scripts" / "package_msix.ps1"),
                "-BundleDir",
                str(outside_bundle),
                "-Version",
                "2.5.1.0",
                "-Publisher",
                "CN=UTHelper",
                "-Output",
                str(out_msix),
            ],
            cwd=str(WORKTREE_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert outside_proc.returncode != 0
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


_VALID_MSIX_MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap desktop rescap">
  <Identity Name="UTHelperOrg.UTHelper" Publisher="CN=UTHelper" Version="2.5.1.0" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>UTHelper</DisplayName>
    <PublisherDisplayName>UTHelper</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Resources><Resource Language="vi-vn" /></Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="UTHelper" Executable="UTHelper.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="UTHelper" Description="UTH activity and deadline assistant"
                          BackgroundColor="transparent"
                          Square44x44Logo="Assets\\Square44x44Logo.png"
                          Square150x150Logo="Assets\\Square150x150Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
      <Extensions>
        <desktop:Extension Category="windows.startupTask"
                           Executable="UTHelperAutostart.exe"
                           EntryPoint="Windows.FullTrustApplication">
          <desktop:StartupTask TaskId="UTHelperStartup" Enabled="false" DisplayName="UTHelper" />
        </desktop:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
  </Capabilities>
</Package>
"""


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_unscaled_697x744_png_asset(tmp_path: Path) -> None:
    """F12: MSIX verifier rejects unscaled non-square ``697x744`` Store logo PNGs."""
    bad_msix = _pack_custom_msix_for_verifier_test(
        tmp_path,
        manifest_xml=_VALID_MSIX_MANIFEST_XML,
        tile_size=(697, 744),
        channel="msstore",
    )
    proc = _run_msix_verifier_on_dir(tmp_path, bad_msix)
    assert proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_missing_startup_task_or_run_full_trust(
    tmp_path: Path,
) -> None:
    """F12: MSIX verifier rejects ``AppxManifest.xml`` missing ``StartupTask`` (packed) or ``runFullTrust`` (ZIP)."""
    no_startup = re.sub(
        r"\s*<Extensions>.*?</Extensions>",
        "",
        _VALID_MSIX_MANIFEST_XML,
        flags=re.DOTALL,
    )
    assert "windows.startupTask" not in no_startup
    missing_startup_msix = _pack_custom_msix_for_verifier_test(
        tmp_path / "no_startup",
        manifest_xml=no_startup,
        channel="msstore",
        use_zip_instead_of_makeappx=False,
    )
    startup_proc = _run_msix_verifier_on_dir(tmp_path / "no_startup", missing_startup_msix)
    assert startup_proc.returncode != 0

    no_fulltrust = _VALID_MSIX_MANIFEST_XML.replace(
        '<rescap:Capability Name="runFullTrust" />', ""
    )
    missing_fulltrust_msix = _pack_custom_msix_for_verifier_test(
        tmp_path / "no_fulltrust",
        manifest_xml=no_fulltrust,
        channel="msstore",
        use_zip_instead_of_makeappx=True,
    )
    fulltrust_proc = _run_msix_verifier_on_dir(tmp_path / "no_fulltrust", missing_fulltrust_msix)
    assert fulltrust_proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_single_mis_sized_wide_tile(tmp_path: Path) -> None:
    """F12: MSIX verifier rejects single mis-sized ``Wide310x150Logo.png`` while other tiles match."""
    bad_msix = _pack_custom_msix_for_verifier_test(
        tmp_path,
        manifest_xml=_VALID_MSIX_MANIFEST_XML,
        tile_overrides={"Wide310x150Logo.png": (150, 150)},
        channel="msstore",
    )
    proc = _run_msix_verifier_on_dir(tmp_path, bad_msix)
    assert proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_missing_distribution_channel_in_msix(
    tmp_path: Path,
) -> None:
    """F12: MSIX verifier rejects MSIX bundle missing ``distribution-channel.json``."""
    bad_msix = _pack_custom_msix_for_verifier_test(
        tmp_path,
        manifest_xml=_VALID_MSIX_MANIFEST_XML,
        include_distribution_channel=False,
        channel="msstore",
    )
    proc = _run_msix_verifier_on_dir(tmp_path, bad_msix)
    assert proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_uap10_parameters_leakage(tmp_path: Path) -> None:
    """F12: MSIX verifier rejects ``uap10:Parameters`` in ``AppxManifest.xml``."""
    leaked_xml = _VALID_MSIX_MANIFEST_XML.replace(
        'Executable="UTHelperAutostart.exe"',
        'Executable="UTHelperAutostart.exe" uap10:Parameters="--autostart"',
    )
    # Note: uap10 prefix doesn't parse without xmlns:uap10, so declare xmlns:uap10 to pack valid XML
    leaked_xml = leaked_xml.replace(
        'xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"',
        'xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"\n'
        '         xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"',
    )
    bad_msix = _pack_custom_msix_for_verifier_test(
        tmp_path,
        manifest_xml=leaked_xml,
        channel="msstore",
    )
    proc = _run_msix_verifier_on_dir(tmp_path, bad_msix)
    assert proc.returncode != 0


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_t2_f12_msix_verifier_rejects_mismatched_distribution_channel_in_msix(
    tmp_path: Path,
) -> None:
    """F12: MSIX verifier rejects MSIX whose ``distribution-channel.json`` is not ``msstore``."""
    bad_msix = _pack_custom_msix_for_verifier_test(
        tmp_path,
        manifest_xml=_VALID_MSIX_MANIFEST_XML,
        channel="exe_lts",
    )
    proc = _run_msix_verifier_on_dir(tmp_path, bad_msix)
    assert proc.returncode != 0


# ---------------------------------------------------------------------------
# F13: Release Inventory Isolation, Checksums & Store Docs Failures
# ---------------------------------------------------------------------------


def test_t2_f13_release_inventory_fails_when_aab_mixed_into_lts_dir(
    tmp_path: Path,
) -> None:
    """F13: Mixing a Store ``.aab`` into the 7-asset LTS dir fails ``release_inventory`` closed."""
    inv_mod = load_module("scripts.release_inventory")
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    make_synthetic_lts_release_dir(release_dir, version="2.5.1", evidence_dir=evidence_dir)
    make_synthetic_aab(release_dir / "UTHelper-2.5.1.aab")

    with pytest.raises(inv_mod.InventoryError):
        inv_mod.verify_release_inventory(
            release_dir,
            evidence_dir,
            "2.5.1",
            "Chouwzi/UTHelper",
            manifest_path=release_dir / "release-manifest.json",
            checksums_path=release_dir / "SHA256SUMS",
        )


def test_t2_f13_release_inventory_fails_when_msix_mixed_into_lts_dir(
    tmp_path: Path,
) -> None:
    """F13: Mixing a Store ``.msix`` into the 7-asset LTS dir fails ``release_inventory`` closed."""
    inv_mod = load_module("scripts.release_inventory")
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    make_synthetic_lts_release_dir(release_dir, version="2.5.1", evidence_dir=evidence_dir)
    (release_dir / "UTHelper-2.5.1.msix").write_bytes(b"PK\x03\x04fake-msix")

    with pytest.raises(inv_mod.InventoryError):
        inv_mod.verify_release_inventory(
            release_dir,
            evidence_dir,
            "2.5.1",
            "Chouwzi/UTHelper",
            manifest_path=release_dir / "release-manifest.json",
            checksums_path=release_dir / "SHA256SUMS",
        )


@pytest.mark.parametrize(
    "missing_name",
    [
        "UTHelper-2.5.1.ipa",
        "UTHelper-2.5.1.apk",
        "UTHelper-2.5.1-arm64-v8a.apk",
        "UTHelper-Setup-2.5.1.exe",
        "UTHelper-2.5.1.msi",
        "release-manifest.json",
        "SHA256SUMS",
    ],
)
def test_t2_f13_release_inventory_fails_on_any_missing_lts_asset(
    tmp_path: Path,
    missing_name: str,
) -> None:
    """F13: Deleting any one of the 7 canonical LTS assets fails ``release_inventory`` closed."""
    inv_mod = load_module("scripts.release_inventory")
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    make_synthetic_lts_release_dir(release_dir, version="2.5.1", evidence_dir=evidence_dir)
    (release_dir / missing_name).unlink()

    with pytest.raises(inv_mod.InventoryError):
        inv_mod.verify_release_inventory(
            release_dir,
            evidence_dir,
            "2.5.1",
            "Chouwzi/UTHelper",
            manifest_path=release_dir / "release-manifest.json",
            checksums_path=release_dir / "SHA256SUMS",
        )


def test_t2_f13_release_inventory_fails_on_modified_sha256sums(tmp_path: Path) -> None:
    """F13: Tampering a single digest in ``SHA256SUMS`` fails ``release_inventory`` closed."""
    inv_mod = load_module("scripts.release_inventory")
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    make_synthetic_lts_release_dir(release_dir, version="2.5.1", evidence_dir=evidence_dir)
    sums_path = release_dir / "SHA256SUMS"
    raw = sums_path.read_text(encoding="ascii")
    flipped = ("1" if raw[0] == "0" else "0") + raw[1:]
    sums_path.write_text(flipped, encoding="ascii", newline="\n")

    with pytest.raises(inv_mod.InventoryError):
        inv_mod.verify_release_inventory(
            release_dir,
            evidence_dir,
            "2.5.1",
            "Chouwzi/UTHelper",
            manifest_path=release_dir / "release-manifest.json",
            checksums_path=sums_path,
        )


def test_t2_f13_schema3_rejects_unknown_store_target_injection_and_docs_present() -> None:
    """F13: Schema-3 manifest rejects injected Store targets; Store ADR & privacy sections exist."""
    manifest_mod = load_module("core.update_manifest")
    for bad_pkg in (
        {
            "platform": "android",
            "architecture": "universal",
            "package_type": "aab",
            "install_channel": "chplay",
            "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.aab",
            "sha256": "a" * 64,
            "size": 1024,
            "signature_kind": "apk-pinned",
            "signer_identity": DEFAULT_ANDROID_PACKAGE_ID,
            "certificate_fingerprint": DEFAULT_TRUSTED_SIGNER_SHA256,
            "install_strategy": {"kind": "android_package_installer"},
        },
        {
            "platform": "windows",
            "architecture": "x64",
            "package_type": "msix",
            "install_channel": "msstore",
            "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msix",
            "sha256": "b" * 64,
            "size": 1024,
            "signature_kind": "self-signed-pinned",
            "signer_identity": DEFAULT_SIGNER_SUBJECT,
            "certificate_fingerprint": DEFAULT_TRUSTED_SIGNER_SHA256,
            "install_strategy": {"kind": "launch_msi"},
        },
    ):
        with pytest.raises(manifest_mod.ManifestError):
            manifest_mod.parse_manifest(
                {
                    "schema_version": 3,
                    "release_version": "2.6.0",
                    "minimum_supported_version": "2.5.1",
                    "published_at": "2026-11-18T12:00:00Z",
                    "release_notes_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
                    "packages": [bad_pkg],
                },
                expected_release_version="2.6.0",
            )

    privacy = (WORKTREE_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    adrs = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (WORKTREE_ROOT / "docs" / "adr").glob("*.md")
    )
    assert "What a report may contain" in privacy
    assert "What is forbidden from remote reports" in privacy
    assert "chplay" in adrs.lower() and "msstore" in adrs.lower()
