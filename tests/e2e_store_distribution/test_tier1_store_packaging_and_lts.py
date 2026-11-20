"""Tier 1 opaque-box E2E tests for Store Packaging, Hardening & LTS (F9-F13).

Covers:
- F9: CH Play Android manifest & Kotlin plugin stripping vs ``apk_lts`` retention
- F10: CH Play permissions, HTTPS-only & backup hardening, alarm fallback
- F11: CH Play ``.aab`` 16 KB ELF alignment & verification pipeline
- F12: Microsoft Store MSIX packaging, parameterized identity & scaled PNG assets
- F13: Store CI/verification, 7-asset GitHub LTS preservation & Store docs/ADRs
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DEFAULT_TRUSTED_SIGNER_SHA256,
    PYTHON_EXE,
    WORKTREE_ROOT,
    load_module,
    make_elf64_so,
    make_minimal_dex,
    make_synthetic_aab,
    make_synthetic_lts_release_dir,
    run_isolated_python,
    stage_distribution_channel_asset,
)

# ---------------------------------------------------------------------------
# Local Opaque-Box Helpers (no M1-M4 top-level imports)
# ---------------------------------------------------------------------------

_ANDROID_PLUGIN_ROOT = (
    WORKTREE_ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "android"
)

_REQUIRED_NOTIFICATION_RECEIVERS = (
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver",
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver",
    "com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver",
    "com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver",
    "com.uthelper.backgroundsync.notification.RescheduleReceiver",
    "com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver",
)

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


def _read_text(relative_path: str) -> str:
    return (WORKTREE_ROOT / relative_path).read_text(encoding="utf-8")


def _make_android_sdk_shim(
    sdk_dir: Path,
    *,
    manifest_xml: str = _CLEAN_CHPLAY_MANIFEST_XML,
    signer_sha256: str = DEFAULT_TRUSTED_SIGNER_SHA256,
    package_id: str = DEFAULT_ANDROID_PACKAGE_ID,
    version_name: str = "2.5.1",
    version_code: int = 2_005_001,
) -> Path:
    """Create a local ``ANDROID_HOME`` shim with ``apksigner`` and ``apkanalyzer``."""
    build_tools = sdk_dir / "build-tools" / "35.0.0"
    cmd_tools = sdk_dir / "cmdline-tools" / "latest" / "bin"
    build_tools.mkdir(parents=True, exist_ok=True)
    cmd_tools.mkdir(parents=True, exist_ok=True)

    manifest_file = sdk_dir / "manifest.xml"
    manifest_file.write_text(manifest_xml, encoding="utf-8")

    apksigner_py = sdk_dir / "apksigner_shim.py"
    apksigner_py.write_text(
        "import sys\n"
        f"print('Signer #1 certificate SHA-256 digest: {signer_sha256.lower()}')\n",
        encoding="utf-8",
    )
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


def _run_verify_android_chplay(
    *,
    artifact_path: Path,
    output_path: Path,
    sdk_dir: Path,
    version: str = "2.5.1",
    build_number: int = 2_005_001,
    architecture: str = "universal",
) -> subprocess.CompletedProcess[str]:
    """Invoke ``scripts/verify_android_release.py`` for ``chplay`` ``.aab`` verification."""
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

    if "--channel" not in help_text or (
        "--aab" not in help_text and "--package" not in help_text
    ):
        raise AssertionError(
            "scripts/verify_android_release.py does not yet support --channel chplay "
            f"and --aab/--package verification.\n--help output:\n{help_text}"
        )

    pkg_flag = "--aab" if "--aab" in help_text else "--package"
    cmd = [
        PYTHON_EXE,
        str(verifier_script),
        pkg_flag,
        str(artifact_path),
        "--channel",
        "chplay",
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
        "200001",
        "--output",
        str(output_path),
    ]
    if "--architecture" in help_text:
        cmd.extend(["--architecture", architecture])

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


def _find_makeappx() -> Path | None:
    kits_bin = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    if not kits_bin.is_dir():
        return None
    matches = sorted(kits_bin.glob("**/x64/makeappx.exe"), reverse=True)
    return matches[0] if matches else None


def _get_ps_parameters(script_path: Path) -> dict[str, str]:
    cmd = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        (
            f"$c = Get-Command '{script_path}'; "
            "$out = @{}; "
            "foreach ($k in $c.Parameters.Keys) { $out[$k] = $c.Parameters[$k].ParameterType.Name }; "
            "$out | ConvertTo-Json -Compress"
        ),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(WORKTREE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _make_dummy_pe_exe(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"MZ" + b"\x00" * 126)
    return dest


def _stage_msix_workspace_dirs() -> tuple[Path, Path, Path]:
    stage_root = WORKTREE_ROOT / "build" / f"e2e-msix-{uuid.uuid4().hex[:10]}"
    bundle_dir = stage_root / "bundle"
    out_dir = stage_root / "out"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _make_dummy_pe_exe(bundle_dir / "UTHelper.exe")
    _make_dummy_pe_exe(bundle_dir / "UTHelperAutostart.exe")
    (bundle_dir / "app" / "assets").mkdir(parents=True, exist_ok=True)
    return stage_root, bundle_dir, out_dir


def _run_package_msix_store(
    *,
    bundle_dir: Path,
    output_msix: Path,
    version: str = "2.5.1.0",
    publisher: str = "CN=UTHelper",
    identity_name: str = "UTHelper.UTHelper",
    store_product_id: str = "9NBLGGH4NNS1",
) -> subprocess.CompletedProcess[str]:
    script_path = WORKTREE_ROOT / "scripts" / "package_msix.ps1"
    params = _get_ps_parameters(script_path)
    param_lower = {k.lower(): k for k in params}

    identity_param = next(
        (param_lower[k] for k in ("identityname", "packagename", "name") if k in param_lower),
        None,
    )
    channel_param = next(
        (param_lower[k] for k in ("channel", "distributionchannel") if k in param_lower),
        None,
    )
    product_param = next(
        (param_lower[k] for k in ("storeproductid", "productid") if k in param_lower),
        None,
    )
    if identity_param is None or (channel_param is None and product_param is None):
        raise AssertionError(
            "scripts/package_msix.ps1 is not yet parameterized for Microsoft Store "
            f"(IdentityName / Channel / StoreProductId). Found params: {sorted(params)}"
        )

    args = [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script_path),
        "-BundleDir",
        str(bundle_dir),
        "-Version",
        version,
        "-Publisher",
        publisher,
        "-Output",
        str(output_msix),
        f"-{identity_param}",
        identity_name,
    ]
    if channel_param is not None:
        args.extend([f"-{channel_param}", "msstore"])
    if product_param is not None:
        args.extend([f"-{product_param}", store_product_id])

    return subprocess.run(
        args,
        cwd=str(WORKTREE_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _strip_hash_comments(text: str) -> str:
    return re.sub(r"(?m)^\s*#.*$", "", text)


def _strip_c_comments(text: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", no_block)


def _strip_xml_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _invoked_android_build_helper_texts(build_script_code: str) -> list[str]:
    """Return comment-stripped contents of non-verifier scripts invoked by ``build_android.ps1``."""
    helpers: list[str] = []
    for candidate in sorted((WORKTREE_ROOT / "scripts").glob("*.py")):
        if candidate.name.startswith("verify_"):
            continue
        if candidate.name in build_script_code:
            helpers.append(_strip_hash_comments(candidate.read_text(encoding="utf-8", errors="ignore")))
    return helpers


def _chplay_flavor_or_strip_evidence() -> dict[str, str]:
    """Collect CH Play Android build configuration artifacts across repo & plugin."""
    contents: dict[str, str] = {
        "pyproject.toml": _read_text("pyproject.toml"),
        "build_android.ps1": _read_text("scripts/build_android.ps1"),
        "build.gradle": (_ANDROID_PLUGIN_ROOT / "build.gradle").read_text(encoding="utf-8"),
        "main_manifest": (_ANDROID_PLUGIN_ROOT / "src/main/AndroidManifest.xml").read_text(
            encoding="utf-8"
        ),
        "main_plugin": (
            _ANDROID_PLUGIN_ROOT
            / "src/main/kotlin/com/uthelper/backgroundsync/UthBackgroundSyncPlugin.kt"
        ).read_text(encoding="utf-8"),
    }
    for rel in (
        "src/chplay/AndroidManifest.xml",
        "src/play/AndroidManifest.xml",
        "src/apkLts/AndroidManifest.xml",
        "src/apk_lts/AndroidManifest.xml",
        "src/lts/AndroidManifest.xml",
    ):
        candidate = _ANDROID_PLUGIN_ROOT / rel
        if candidate.is_file():
            contents[rel] = candidate.read_text(encoding="utf-8")
    return contents


# ---------------------------------------------------------------------------
# F9: CH Play Android Manifest & Kotlin Plugin Stripping vs apk_lts Retention
# ---------------------------------------------------------------------------


def test_t1_f09_chplay_config_strips_request_install_packages_and_file_provider(
    tmp_path: Path,
) -> None:
    """F9: ``chplay`` build config/manifest strips ``REQUEST_INSTALL_PACKAGES`` & ``UthUpdateFileProvider``."""
    artifacts = _chplay_flavor_or_strip_evidence()
    build_script = _strip_hash_comments(artifacts["build_android.ps1"])
    gradle_code = _strip_c_comments(artifacts["build.gradle"])
    pyproject_code = _strip_hash_comments(artifacts["pyproject.toml"])
    main_manifest = _strip_xml_comments(artifacts["main_manifest"])
    build_sources = "\n".join([build_script, *_invoked_android_build_helper_texts(build_script)])

    assert "chplay" in build_script.lower(), (
        "scripts/build_android.ps1 must support a dedicated chplay build channel"
    )

    # Main plugin manifest or flavor split must NOT force REQUEST_INSTALL_PACKAGES + UthUpdateFileProvider on chplay
    has_flavor_split = any(
        key.startswith(("src/chplay/", "src/play/", "src/apkLts/", "src/apk_lts/", "src/lts/"))
        for key in artifacts
    )
    if has_flavor_split:
        chplay_manifest = _strip_xml_comments(
            artifacts.get("src/chplay/AndroidManifest.xml")
            or artifacts.get("src/play/AndroidManifest.xml", "")
        )
        combined_chplay = chplay_manifest + "\n" + main_manifest
        assert "productflavors" in gradle_code.lower() and (
            "chplay" in gradle_code.lower() or "play" in gradle_code.lower()
        ), "build.gradle must wire the chplay/play productFlavor when flavor manifests are used"
        assert (
            "REQUEST_INSTALL_PACKAGES" not in main_manifest
            or 'tools:node="remove"' in chplay_manifest
        )
        assert (
            "UthUpdateFileProvider" not in main_manifest
            or 'tools:node="remove"' in chplay_manifest
        )
        assert "uth_update_paths" not in combined_chplay or 'tools:node="remove"' in chplay_manifest
    else:
        # Build-time stripping mode: build_android.ps1 / invoked build helper (not verifier) strips them for chplay
        if "REQUEST_INSTALL_PACKAGES" in main_manifest:
            assert "chplay" in build_sources.lower() and "REQUEST_INSTALL_PACKAGES" in build_sources, (
                "chplay build pipeline must strip REQUEST_INSTALL_PACKAGES from plugin manifest"
            )
        if "UthUpdateFileProvider" in main_manifest:
            assert "chplay" in build_sources.lower() and "UthUpdateFileProvider" in build_sources, (
                "chplay build pipeline must strip UthUpdateFileProvider from plugin manifest"
            )

    # Also verify pyproject.toml does not unconditionally force REQUEST_INSTALL_PACKAGES onto chplay builds
    if '"android.permission.REQUEST_INSTALL_PACKAGES" = true' in pyproject_code:
        assert "chplay" in build_sources.lower() and "REQUEST_INSTALL_PACKAGES" in build_sources, (
            "chplay build pipeline must strip REQUEST_INSTALL_PACKAGES from pyproject.toml"
        )

    # End-to-end verification pass with clean CH Play AAB
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "UTHelper-2.5.1-chplay.aab",
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={
            "channel": "chplay",
            "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
        },
    )
    out_evidence = tmp_path / "chplay.verification.json"
    proc = _run_verify_android_chplay(
        artifact_path=aab_path,
        output_path=out_evidence,
        sdk_dir=sdk_dir,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_t1_f09_chplay_strips_updater_kotlin_classes_and_method_channels(
    tmp_path: Path,
) -> None:
    """F9: ``chplay`` excludes ``ApkUpdateDownloadManager``, ``ApkUpdateInstaller``, & update MethodChannels."""
    gradle_code = _strip_c_comments((_ANDROID_PLUGIN_ROOT / "build.gradle").read_text(encoding="utf-8"))
    plugin_kt = _strip_c_comments(
        (
            _ANDROID_PLUGIN_ROOT
            / "src/main/kotlin/com/uthelper/backgroundsync/UthBackgroundSyncPlugin.kt"
        ).read_text(encoding="utf-8")
    )
    build_script = _strip_hash_comments(_read_text("scripts/build_android.ps1"))
    build_sources = "\n".join([build_script, *_invoked_android_build_helper_texts(build_script)])

    updater_files_in_main = list((_ANDROID_PLUGIN_ROOT / "src/main").rglob("ApkUpdate*.kt"))
    has_gradle_stripping = (
        ("chplay" in gradle_code.lower() or "play" in gradle_code.lower())
        and "sourcesets" in gradle_code.lower()
        and (
            not updater_files_in_main
            or "exclude" in gradle_code.lower()
        )
        and "ApkUpdateInstaller" not in plugin_kt
        and "ApkUpdateDownloadManager" not in plugin_kt
    )
    has_build_script_stripping = (
        "chplay" in build_sources.lower()
        and "ApkUpdateInstaller" in build_sources
        and "ApkUpdateDownloadManager" in build_sources
    )
    assert has_gradle_stripping or has_build_script_stripping, (
        "Android plugin Gradle sourceSets or chplay build script must concretely strip "
        "ApkUpdateInstaller, ApkUpdateDownloadManager, and update MethodChannel handlers from chplay builds"
    )

    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    clean_dex = (
        "Lcom/uthelper/MainActivity;",
        "Lcom/uthelper/backgroundsync/UthBackgroundSyncPlugin;",
        "Lcom/uthelper/backgroundsync/notification/AlarmScheduler;",
        "Lcom/uthelper/backgroundsync/security/CredentialVault;",
    )
    aab_path = make_synthetic_aab(
        tmp_path / "UTHelper-2.5.1-chplay.aab",
        p_align=0x4000,
        dex_strings=clean_dex,
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={
            "channel": "chplay",
            "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
        },
    )
    proc = _run_verify_android_chplay(
        artifact_path=aab_path,
        output_path=tmp_path / "evidence.json",
        sdk_dir=sdk_dir,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_t1_f09_apk_lts_retains_updater_manifest_provider_and_kotlin_classes() -> None:
    """F9: ``apk_lts`` retains ``REQUEST_INSTALL_PACKAGES``, ``UthUpdateFileProvider``, & updater classes."""
    all_manifests = "\n".join(
        p.read_text(encoding="utf-8")
        for p in _ANDROID_PLUGIN_ROOT.rglob("AndroidManifest.xml")
    )
    assert "REQUEST_INSTALL_PACKAGES" in all_manifests
    assert "UthUpdateFileProvider" in all_manifests
    assert "@xml/uth_update_paths" in all_manifests
    assert list(_ANDROID_PLUGIN_ROOT.rglob("uth_update_paths.xml"))
    assert list(_ANDROID_PLUGIN_ROOT.rglob("ApkUpdateDownloadManager.kt"))
    assert list(_ANDROID_PLUGIN_ROOT.rglob("ApkUpdateInstaller.kt"))

    all_plugin_kt = "\n".join(
        p.read_text(encoding="utf-8")
        for p in _ANDROID_PLUGIN_ROOT.rglob("*.kt")
    )
    for method_name in (
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "install_update",
        "cancel_update",
    ):
        assert f'"{method_name}"' in all_plugin_kt


def test_t1_f09_both_channels_preserve_notification_receivers_and_sync_worker() -> None:
    """F9: Both ``chplay`` and ``apk_lts`` retain notification receivers, ``CredentialVault``, & ``MoodleSyncWorker``."""
    main_manifest = (_ANDROID_PLUGIN_ROOT / "src/main/AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    assert "DeadlineAlarmReceiver" in main_manifest
    assert "RescheduleReceiver" in main_manifest
    assert "BOOT_COMPLETED" in main_manifest
    assert "TIMEZONE_CHANGED" in main_manifest
    assert "SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED" in main_manifest
    assert list(_ANDROID_PLUGIN_ROOT.rglob("CredentialVault.kt"))
    assert list(_ANDROID_PLUGIN_ROOT.rglob("MoodleSyncWorker.kt"))
    assert list(_ANDROID_PLUGIN_ROOT.rglob("AlarmScheduler.kt"))


def test_t1_f09_chplay_python_runtime_never_invokes_updater_method_channels(
    isolated_runtime_env: dict[str, Path],
    distribution_channel_stager,
    android_bridge,
    fake_page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F9: Under ``chplay``, ``AppController`` emits 0 calls to native APK updater MethodChannels."""
    distribution_channel_stager("chplay")
    app_controller_mod = load_module("gui.app_controller")
    platform_utils = load_module("platform_utils")

    monkeypatch.setattr(platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(platform_utils, "IS_MOBILE", True)

    controller = app_controller_mod.AppController.__new__(app_controller_mod.AppController)
    controller.page = fake_page
    controller._android_background = android_bridge
    controller._update_candidate = None
    controller._distribution_profile = load_module(
        "core.distribution"
    ).resolve_distribution_channel()
    controller._update_coordinator = controller._create_update_coordinator()

    assert controller._distribution_profile.channel.value == "chplay"
    assert controller._update_coordinator is None
    controller._check_updates_now()
    controller._request_update_download()
    controller._cancel_update_download()

    updater_methods = {
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "install_update",
        "cancel_update",
    }
    called_methods = {name for name, _args in android_bridge.calls}
    assert called_methods.isdisjoint(updater_methods)


# ---------------------------------------------------------------------------
# F10: CH Play Permissions, HTTPS-Only & Backup Hardening
# ---------------------------------------------------------------------------


def test_t1_f10_chplay_removes_foreground_service_permissions() -> None:
    """F10: ``FOREGROUND_SERVICE`` and ``FOREGROUND_SERVICE_DATA_SYNC`` are absent for ``chplay``."""
    pyproject_text = _strip_hash_comments(_read_text("pyproject.toml"))
    build_script = _strip_hash_comments(_read_text("scripts/build_android.ps1"))
    build_sources = "\n".join([build_script, *_invoked_android_build_helper_texts(build_script)])
    main_manifest = _strip_xml_comments(
        (_ANDROID_PLUGIN_ROOT / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    )
    chplay_manifest = ""
    for rel in ("src/chplay/AndroidManifest.xml", "src/play/AndroidManifest.xml"):
        p = _ANDROID_PLUGIN_ROOT / rel
        if p.is_file():
            chplay_manifest += _strip_xml_comments(p.read_text(encoding="utf-8"))

    # Either removed globally in pyproject.toml (unused) or explicitly stripped in chplay build
    fgs_in_pyproject = (
        '"android.permission.FOREGROUND_SERVICE" = true' in pyproject_text
        or '"android.permission.FOREGROUND_SERVICE_DATA_SYNC" = true' in pyproject_text
    )
    if fgs_in_pyproject:
        assert (
            ('tools:node="remove"' in chplay_manifest and "FOREGROUND_SERVICE" in chplay_manifest)
            or ("chplay" in build_sources.lower() and "FOREGROUND_SERVICE" in build_sources)
        ), "FOREGROUND_SERVICE* must be removed from chplay Android configuration"
    assert (
        "FOREGROUND_SERVICE" not in main_manifest
        or ('tools:node="remove"' in chplay_manifest and "FOREGROUND_SERVICE" in chplay_manifest)
        or ("chplay" in build_sources.lower() and "FOREGROUND_SERVICE" in build_sources)
    )
    assert "USE_EXACT_ALARM" not in main_manifest
    assert "USE_EXACT_ALARM" not in pyproject_text


def test_t1_f10_chplay_enforces_https_and_removes_cleartext_traffic() -> None:
    """F10: ``usesCleartextTraffic="true"`` is removed for ``chplay`` Android packaging."""
    pyproject_text = _strip_hash_comments(_read_text("pyproject.toml"))
    build_script = _strip_hash_comments(_read_text("scripts/build_android.ps1"))
    build_sources = "\n".join([build_script, *_invoked_android_build_helper_texts(build_script)])
    main_manifest = _strip_xml_comments(
        (_ANDROID_PLUGIN_ROOT / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    )
    assert 'usesCleartextTraffic="true"' not in main_manifest

    if '"usesCleartextTraffic" = "true"' in pyproject_text:
        chplay_manifest = ""
        for rel in ("src/chplay/AndroidManifest.xml", "src/play/AndroidManifest.xml"):
            p = _ANDROID_PLUGIN_ROOT / rel
            if p.is_file():
                chplay_manifest += _strip_xml_comments(p.read_text(encoding="utf-8"))
        assert 'usesCleartextTraffic="true"' not in chplay_manifest
        assert (
            'usesCleartextTraffic="false"' in chplay_manifest
            or ("chplay" in build_sources.lower() and "usesCleartextTraffic" in build_sources)
        ), "chplay build must remove usesCleartextTraffic='true'"


def test_t1_f10_chplay_enforces_allow_backup_false() -> None:
    """F10: ``allowBackup="false"`` is enforced for ``chplay`` and ``CredentialVault`` uses ``noBackupFilesDir``."""
    pyproject_text = _strip_hash_comments(_read_text("pyproject.toml"))
    build_script = _strip_hash_comments(_read_text("scripts/build_android.ps1"))
    build_sources = "\n".join([build_script, *_invoked_android_build_helper_texts(build_script)])
    manifests = "\n".join(
        _strip_xml_comments(p.read_text(encoding="utf-8"))
        for p in _ANDROID_PLUGIN_ROOT.rglob("AndroidManifest.xml")
    )
    assert (
        '"allowBackup" = "false"' in pyproject_text
        or 'android:allowBackup="false"' in manifests
        or (
            "chplay" in build_sources.lower()
            and "allowBackup" in build_sources
            and "false" in build_sources.lower()
        )
    ), "allowBackup='false' must be configured for chplay Android builds"

    vault_kt = next(_ANDROID_PLUGIN_ROOT.rglob("CredentialVault.kt")).read_text(
        encoding="utf-8"
    )
    assert "noBackupFilesDir" in vault_kt
    assert "AndroidKeyStore" in vault_kt


def test_t1_f10_alarm_scheduler_graceful_fallback_when_exact_alarm_denied() -> None:
    """F10: ``AlarmScheduler.kt`` degrades gracefully via ``setAndAllowWhileIdle`` when exact alarm denied."""
    alarm_kt = next(_ANDROID_PLUGIN_ROOT.rglob("AlarmScheduler.kt")).read_text(
        encoding="utf-8"
    )
    assert "canScheduleExactAlarms()" in alarm_kt
    assert "setExactAndAllowWhileIdle" in alarm_kt
    assert "setAndAllowWhileIdle" in alarm_kt

    reschedule_kt = next(_ANDROID_PLUGIN_ROOT.rglob("RescheduleReceiver.kt")).read_text(
        encoding="utf-8"
    )
    assert "reschedulePersisted()" in reschedule_kt
    main_manifest = (_ANDROID_PLUGIN_ROOT / "src/main/AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    assert "SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED" in main_manifest


def test_t1_f10_zero_http_backend_urls_in_moodle_portal_github_sentry_configs(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F10: All default/trusted Moodle, Portal, GitHub & Sentry backend URLs enforce HTTPS."""
    moodle_sites = load_module("core.moodle_sites")
    release_config = load_module("diagnostics.release_config")
    config_mod = load_module("config")

    for site in moodle_sites.TRUSTED_MOODLE_SITES:
        assert site.origin.startswith("https://")
        http_origin = "http://" + site.origin[len("https://") :]
        assert moodle_sites.moodle_site_from_origin(http_origin) is None

    s = config_mod.Settings()
    assert s.MOODLE_BASE_URL.startswith("https://")
    assert s.MOODLE_LOGIN_URL.startswith("https://")
    assert s.PORTAL_API_BASE.startswith("https://")

    valid_dsn = "https://publickey1234@o12345.ingest.sentry.io/67890"
    assert release_config.validate_public_sentry_dsn(valid_dsn) == valid_dsn


# ---------------------------------------------------------------------------
# F11: CH Play .aab 16 KB ELF Alignment & Verification Pipeline
# ---------------------------------------------------------------------------


def test_t1_f11_build_android_script_supports_chplay_aab_and_no_legacy_packaging() -> None:
    """F11: ``scripts/build_android.ps1`` supports ``chplay`` ``.aab`` with ``--no-android-legacy-packaging``."""
    build_ps1 = _read_text("scripts/build_android.ps1")
    assert "aab" in build_ps1
    assert "--no-android-legacy-packaging" in build_ps1
    assert "chplay" in build_ps1.lower()
    assert "distribution-channel.json" in build_ps1


def test_t1_f11_android_target_sdk_at_least_35_in_config() -> None:
    """F11: Android packaging config targets API level >= 35 for Google Play compliance."""
    pyproject_text = _strip_hash_comments(_read_text("pyproject.toml"))
    gradle_text = _strip_c_comments((_ANDROID_PLUGIN_ROOT / "build.gradle").read_text(encoding="utf-8"))
    build_ps1 = _strip_hash_comments(_read_text("scripts/build_android.ps1"))
    build_sources = "\n".join([build_ps1, *_invoked_android_build_helper_texts(build_ps1)])

    compile_match = re.search(r"compileSdk\s+(\d+)", gradle_text)
    assert compile_match is not None and int(compile_match.group(1)) >= 35

    app_target_candidates: list[int] = []
    for text in (pyproject_text, build_sources):
        for pat in (
            r"targetSdk(?:Version)?\s*[=:]?\s*\"?(\d+)\"?",
            r"target_sdk_version\s*=\s*(\d+)",
            r"--android-target-sdk(?:-version)?\s+(\d+)",
        ):
            app_target_candidates.extend(int(m.group(1)) for m in re.finditer(pat, text))
    assert app_target_candidates and all(sdk >= 35 for sdk in app_target_candidates), (
        f"Expected app-level targetSdk >= 35 in pyproject.toml/build_android.ps1, found: {app_target_candidates}"
    )

    plugin_targets = [
        int(m.group(1))
        for m in re.finditer(r"targetSdk(?:Version)?\s*[=:]?\s*\"?(\d+)\"?", gradle_text)
    ]
    assert all(sdk >= 35 for sdk in plugin_targets), (
        f"Plugin build.gradle targetSdk must not be < 35: {plugin_targets}"
    )


def test_t1_f11_verify_android_release_passes_synthetic_16kb_chplay_aab(
    tmp_path: Path,
) -> None:
    """F11: ``verify_android_release.py`` passes on a clean 16 KB ELF64 ``chplay`` ``.aab``."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "UTHelper-2.5.1-chplay.aab",
        p_align=0x4000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={
            "channel": "chplay",
            "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
        },
    )
    output_json = tmp_path / "chplay-evidence.json"
    proc = _run_verify_android_chplay(
        artifact_path=aab_path,
        output_path=output_json,
        sdk_dir=sdk_dir,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert output_json.is_file()
    evidence = json.loads(output_json.read_text(encoding="utf-8"))
    assert evidence["platform"] == "android"
    assert evidence["product_id"] == DEFAULT_ANDROID_PACKAGE_ID


def test_t1_f11_verify_android_release_accepts_64kb_aligned_multi_abi_aab(
    tmp_path: Path,
) -> None:
    """F11: Synthetic ``.aab`` with ``p_align = 0x10000`` (>= 16 KB) across all ABIs passes."""
    sdk_dir = _make_android_sdk_shim(tmp_path / "sdk")
    aab_path = make_synthetic_aab(
        tmp_path / "UTHelper-2.5.1-chplay.aab",
        abis=("arm64-v8a", "armeabi-v7a", "x86_64"),
        p_align=0x10000,
        dex_strings=("Lcom/uthelper/MainActivity;",),
        manifest_xml=_CLEAN_CHPLAY_MANIFEST_XML,
        distribution_channel={
            "channel": "chplay",
            "android_package_id": DEFAULT_ANDROID_PACKAGE_ID,
        },
        extra_files={"base/lib/arm64-v8a/libapp.so": make_elf64_so(0x4000)},
    )
    proc = _run_verify_android_chplay(
        artifact_path=aab_path,
        output_path=tmp_path / "evidence.json",
        sdk_dir=sdk_dir,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"


def test_t1_f11_chplay_runtime_target_resolves_to_chplay_channel(
    isolated_runtime_env: dict[str, Path],
) -> None:
    """F11: With ``chplay`` ``distribution-channel.json``, ``detect_runtime_target()`` reports ``chplay``."""
    stage_distribution_channel_asset(isolated_runtime_env["assets"], "chplay")
    code = (
        "import platform_utils\n"
        "platform_utils.IS_WINDOWS = False\n"
        "platform_utils.IS_ANDROID = True\n"
        "platform_utils.IS_IOS = False\n"
        "from core.distribution import resolve_distribution_channel\n"
        "from platform_utils.update_packages import detect_runtime_target\n"
        "profile = resolve_distribution_channel(is_windows=False, is_android=True, is_ios=False)\n"
        "target = detect_runtime_target(distribution=profile)\n"
        "assert profile.channel.value == 'chplay', profile\n"
        "assert profile.supports_github_self_update is False\n"
        "assert target.platform == 'android'\n"
        "assert target.install_channel == 'chplay', target\n"
    )
    proc = run_isolated_python(code, tmp_path=isolated_runtime_env["root"])
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# F12: Microsoft Store MSIX Packaging & Visual Assets
# ---------------------------------------------------------------------------


def _run_verify_msix_release(
    *,
    msix_path: Path,
    tmp_path: Path,
    identity_name: str = "UTHelperOrg.UTHelper",
    publisher: str = "CN=11111111-2222-3333-4444-555555555555",
    version: str = "2.5.1.0",
    store_product_id: str = "9NBLGGH4NNS1",
) -> subprocess.CompletedProcess[str]:
    verifier_ps1 = WORKTREE_ROOT / "scripts" / "verify_msix_release.ps1"
    if not verifier_ps1.is_file():
        raise AssertionError(
            "scripts/verify_msix_release.ps1 does not exist yet (required by M4)"
        )
    params = _get_ps_parameters(verifier_ps1)
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
            f"scripts/verify_msix_release.ps1 has no recognized MSIX path parameter; found: {sorted(params)}"
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
        (("identityname", "expectedidentityname", "packagename"), identity_name),
        (("publisher", "expectedpublisher"), publisher),
        (("version", "expectedversion"), version),
        (("storeproductid", "productid"), store_product_id),
        (("output", "outputpath"), str(tmp_path / "msix-verify-evidence.json")),
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
        timeout=60,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t1_f12_package_msix_builds_store_msix_with_parameterized_identity_and_unpacks(
    tmp_path: Path,
) -> None:
    """F12: ``package_msix.ps1`` builds parameterized ``msstore`` MSIX and passes ``makeappx unpack``."""
    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.fail("makeappx.exe was not found on Windows host")

    stage_root, bundle_dir, out_dir = _stage_msix_workspace_dirs()
    try:
        output_msix = out_dir / "UTHelper-Store.msix"
        proc = _run_package_msix_store(
            bundle_dir=bundle_dir,
            output_msix=output_msix,
            version="2.5.1.0",
            publisher="CN=11111111-2222-3333-4444-555555555555",
            identity_name="UTHelperOrg.UTHelper",
            store_product_id="9NBLGGH4NNS1",
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        assert output_msix.is_file()

        verify_proc = _run_verify_msix_release(
            msix_path=output_msix,
            tmp_path=tmp_path,
            identity_name="UTHelperOrg.UTHelper",
            publisher="CN=11111111-2222-3333-4444-555555555555",
            version="2.5.1.0",
            store_product_id="9NBLGGH4NNS1",
        )
        assert verify_proc.returncode == 0, (
            f"scripts/verify_msix_release.ps1 failed on valid Store MSIX:\n"
            f"stdout={verify_proc.stdout}\nstderr={verify_proc.stderr}"
        )

        unpacked = tmp_path / "unpacked"
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(output_msix), "/d", str(unpacked), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr

        manifest_text = (unpacked / "AppxManifest.xml").read_text(encoding="utf-8")
        assert "uap10:Parameters" not in manifest_text
        manifest_lower = manifest_text.lower()
        assert 'filesystemwritevirtualization="disabled"' not in manifest_lower
        assert 'registrywritevirtualization="disabled"' not in manifest_lower

        root = ET.fromstring(manifest_text)
        ns = {
            "f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
            "desktop": "http://schemas.microsoft.com/appx/manifest/desktop/windows10",
            "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
        }
        identity = root.find("f:Identity", ns)
        assert identity is not None
        assert identity.attrib["Name"] == "UTHelperOrg.UTHelper"
        assert identity.attrib["Publisher"] == "CN=11111111-2222-3333-4444-555555555555"
        assert identity.attrib["Version"] == "2.5.1.0"
        assert identity.attrib["ProcessorArchitecture"] == "x64"

        assert root.find(".//rescap:Capability[@Name='runFullTrust']", ns) is not None
        assert root.find(".//f:Capability[@Name='internetClient']", ns) is not None
        ext = root.find(".//desktop:Extension[@Category='windows.startupTask']", ns)
        assert ext is not None
        assert ext.attrib.get("Executable") == "UTHelperAutostart.exe"
        task = ext.find("desktop:StartupTask", ns)
        assert task is not None
        assert task.attrib.get("TaskId") == "UTHelperStartup"
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t1_f12_msix_assets_are_scaled_to_exact_store_tile_dimensions(
    tmp_path: Path,
) -> None:
    """F12: Unpacked MSIX ``Assets/*.png`` match exact Store dimensions (never raw 697x744)."""
    from PIL import Image

    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.fail("makeappx.exe was not found on Windows host")

    stage_root, bundle_dir, out_dir = _stage_msix_workspace_dirs()
    try:
        output_msix = out_dir / "UTHelper-Store.msix"
        proc = _run_package_msix_store(bundle_dir=bundle_dir, output_msix=output_msix)
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

        unpacked = tmp_path / "unpacked"
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(output_msix), "/d", str(unpacked), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr

        assets_dir = unpacked / "Assets"
        expected_sizes = {
            "Square44x44Logo.png": (44, 44),
            "StoreLogo.png": (50, 50),
            "Square150x150Logo.png": (150, 150),
            "Wide310x150Logo.png": (310, 150),
        }
        for filename, expected_size in expected_sizes.items():
            img_path = assets_dir / filename
            assert img_path.is_file(), f"Missing scaled Store tile asset: {filename}"
            with Image.open(img_path) as img:
                assert img.size == expected_size, (
                    f"{filename} expected {expected_size}, got {img.size}"
                )
                assert img.size != (697, 744)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t1_f12_msix_embeds_msstore_distribution_channel_asset(
    tmp_path: Path,
) -> None:
    """F12: Unpacked MSIX contains ``msstore`` ``distribution-channel.json`` resolving to ``MSSTORE``."""
    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.fail("makeappx.exe was not found on Windows host")

    distribution = load_module("core.distribution")
    stage_root, bundle_dir, out_dir = _stage_msix_workspace_dirs()
    try:
        output_msix = out_dir / "UTHelper-Store.msix"
        proc = _run_package_msix_store(
            bundle_dir=bundle_dir,
            output_msix=output_msix,
            store_product_id="9NBLGGH4NNS1",
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

        unpacked = tmp_path / "unpacked"
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(output_msix), "/d", str(unpacked), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr

        channel_candidates = list(unpacked.rglob("distribution-channel.json"))
        assert channel_candidates, "MSIX bundle must embed distribution-channel.json"
        channel_path = channel_candidates[0]
        payload = json.loads(channel_path.read_text(encoding="utf-8"))
        assert payload["channel"] == "msstore"
        assert payload.get("store_product_id") == "9NBLGGH4NNS1"

        profile = distribution.resolve_distribution_channel(
            config_path=channel_path,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: True,
        )
        assert profile.channel is distribution.DistributionChannel.MSSTORE
        assert profile.supports_github_self_update is False
        assert profile.store_url == "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX packaging requires Windows")
def test_t1_f12_msix_publisher_xml_escaping_preserves_special_characters(
    tmp_path: Path,
) -> None:
    """F12: Special XML characters in MSIX ``Publisher`` round-trip validly through ``makeappx``."""
    makeappx = _find_makeappx()
    if makeappx is None:
        pytest.fail("makeappx.exe was not found on Windows host")

    special_publisher = "CN=UTHelper & Education, O=UTH"
    stage_root, bundle_dir, out_dir = _stage_msix_workspace_dirs()
    try:
        output_msix = out_dir / "UTHelper-Escaped.msix"
        proc = _run_package_msix_store(
            bundle_dir=bundle_dir,
            output_msix=output_msix,
            publisher=special_publisher,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"

        unpacked = tmp_path / "unpacked"
        unpack_proc = subprocess.run(
            [str(makeappx), "unpack", "/p", str(output_msix), "/d", str(unpacked), "/o"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert unpack_proc.returncode == 0, unpack_proc.stderr
        root = ET.fromstring((unpacked / "AppxManifest.xml").read_text(encoding="utf-8"))
        ns = {"f": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}
        identity = root.find("f:Identity", ns)
        assert identity is not None
        assert identity.attrib["Publisher"] == special_publisher
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)
        shutil.rmtree(WORKTREE_ROOT / "build" / "msix-stage", ignore_errors=True)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows AppData virtualization test requires Windows")
def test_t1_f12_msstore_appdata_virtualization_never_writes_into_bundle_dir(
    tmp_path: Path,
) -> None:
    """F12: Simulated MSIX ``msstore`` env writes config strictly under ``%APPDATA%\\UTHelper``."""
    bundle_dir = tmp_path / "WindowsApps_Bundle"
    assets_dir = bundle_dir / "app" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    _make_dummy_pe_exe(bundle_dir / "UTHelper.exe")
    stage_distribution_channel_asset(
        assets_dir,
        "msstore",
        store_product_id="9NBLGGH4NNS1",
    )
    for item in bundle_dir.rglob("*"):
        if item.is_file():
            os.chmod(item, stat.S_IREAD)

    before_snapshot = sorted(
        (str(p.relative_to(bundle_dir)), p.read_bytes())
        for p in bundle_dir.rglob("*")
        if p.is_file()
    )

    appdata_dir = tmp_path / "roaming_appdata"
    appdata_dir.mkdir(parents=True, exist_ok=True)

    code = (
        "from pathlib import Path\n"
        "import config\n"
        "from core.distribution import DistributionChannel, resolve_distribution_channel\n"
        f"expected_base = Path({str(bundle_dir / 'app')!r})\n"
        f"expected_data = Path({str(appdata_dir)!r}) / 'UTHelper'\n"
        "assert config.BASE_DIR == expected_base, (config.BASE_DIR, expected_base)\n"
        "assert config.get_data_dir() == expected_data, (config.get_data_dir(), expected_data)\n"
        "assert config.CONFIG_FILE == expected_data / 'settings.json'\n"
        "profile = resolve_distribution_channel(\n"
        "    is_windows=True, is_android=False, is_ios=False, has_package_identity_fn=lambda: True\n"
        ")\n"
        "assert profile.channel is DistributionChannel.MSSTORE\n"
        "config._HAS_KEYRING = False\n"
        "config.settings.THEME = 'steel_light'\n"
        "assert config.save_settings() is True\n"
        "assert config.CONFIG_FILE.is_file()\n"
    )
    try:
        proc = run_isolated_python(
            code,
            env_overrides={
                "APPDATA": str(appdata_dir),
                "FLET_ASSETS_DIR": str(assets_dir),
                "FLET_APP_STORAGE_DATA": None,
                "FLET_APP_DATA": None,
            },
            tmp_path=tmp_path,
        )
        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        assert (appdata_dir / "UTHelper" / "settings.json").is_file()
        after_snapshot = sorted(
            (str(p.relative_to(bundle_dir)), p.read_bytes())
            for p in bundle_dir.rglob("*")
            if p.is_file()
        )
        assert after_snapshot == before_snapshot, (
            "MSIX runtime must never write or modify files inside BundleDir"
        )
    finally:
        for item in bundle_dir.rglob("*"):
            if item.is_file():
                os.chmod(item, stat.S_IWRITE | stat.S_IREAD)


def test_t1_f12_msix_runtime_uses_startup_task_and_packaged_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F12: Packaged MSIX runtime selects ``StartupTaskAutostartBackend`` & packaged AUMID notifier."""
    autostart_mod = load_module("platform_utils.autostart")
    win_notifier_mod = load_module("notifiers.windows")

    monkeypatch.setattr(autostart_mod, "has_package_identity", lambda: True)
    backend = autostart_mod.create_autostart_service()
    assert isinstance(backend, autostart_mod.StartupTaskAutostartBackend)
    assert backend.task_id == "UTHelperStartup"

    monkeypatch.setattr(
        win_notifier_mod, "_packaged_aumid", lambda: "UTHelperOrg.UTHelper_abc!UTHelper"
    )
    shortcut_calls: list[str] = []
    monkeypatch.setattr(
        win_notifier_mod.WindowsNotifier,
        "_ensure_shortcut",
        lambda _self: shortcut_calls.append("called"),
    )
    notifier = win_notifier_mod.WindowsNotifier(start_scheduler=False)
    assert notifier.is_packaged is True
    assert notifier.aumid == "UTHelperOrg.UTHelper_abc!UTHelper"
    assert shortcut_calls == []


# ---------------------------------------------------------------------------
# F13: Store CI/Verification, 7-Asset LTS Preservation & Store Docs/ADRs
# ---------------------------------------------------------------------------


def test_t1_f13_release_inventory_and_manifest_generator_pass_on_7_asset_lts_dir(
    tmp_path: Path,
) -> None:
    """F13: ``release_inventory.py`` and ``generate_release_manifest.py`` pass on 7-asset LTS dir."""
    release_dir = tmp_path / "release"
    evidence_dir = tmp_path / "evidence"
    make_synthetic_lts_release_dir(
        release_dir,
        version="2.5.1",
        evidence_dir=evidence_dir,
    )
    # Remove pre-seeded manifest/checksums so generate_release_manifest.py + release_inventory.py
    # execute the full end-to-end generation + verification pipeline from the 5 binary packages.
    manifest_out = release_dir / "release-manifest.json"
    checksums_out = release_dir / "SHA256SUMS"
    manifest_out.unlink()
    checksums_out.unlink()
    gen_proc = subprocess.run(
        [
            PYTHON_EXE,
            str(WORKTREE_ROOT / "scripts" / "generate_release_manifest.py"),
            "--version",
            "2.5.1",
            "--repository",
            "Chouwzi/UTHelper",
            "--release-dir",
            str(release_dir),
            "--evidence-dir",
            str(evidence_dir),
            "--minimum-supported-version",
            "2.5.1",
            "--output",
            str(manifest_out),
        ],
        cwd=str(WORKTREE_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert gen_proc.returncode == 0, gen_proc.stderr

    inv_proc = subprocess.run(
        [
            PYTHON_EXE,
            str(WORKTREE_ROOT / "scripts" / "release_inventory.py"),
            "--release-dir",
            str(release_dir),
            "--evidence-dir",
            str(evidence_dir),
            "--version",
            "2.5.1",
            "--repository",
            "Chouwzi/UTHelper",
            "--manifest",
            str(manifest_out),
            "--write-checksums",
            str(release_dir / "SHA256SUMS"),
        ],
        cwd=str(WORKTREE_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert inv_proc.returncode == 0, inv_proc.stderr

    checksum_lines = (release_dir / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert len(checksum_lines) == 6
    parsed_manifest = load_module("core.update_manifest").parse_manifest(
        json.loads(manifest_out.read_text(encoding="utf-8")),
        expected_release_version="2.5.1",
    )
    assert parsed_manifest.schema_version == 3
    assert len(parsed_manifest.packages) == 5


def test_t1_f13_release_yml_preserves_exact_7_asset_lts_pipeline() -> None:
    """F13: ``.github/workflows/release.yml`` preserves the 7-asset GitHub LTS release contract."""
    release_yml = _read_text(".github/workflows/release.yml")
    assert "scripts/release_inventory.py" in release_yml
    assert "scripts/generate_release_manifest.py" in release_yml
    assert "scripts/verify_android_release.py" in release_yml
    assert "--android-legacy-packaging" in release_yml
    assert "SHA256SUMS" in release_yml
    assert "release-manifest.json" in release_yml
    assert ".aab" not in release_yml
    assert ".msix" not in release_yml


def test_t1_f13_store_ci_workflows_and_verification_scripts_exist() -> None:
    """F13: Store CI workflows/jobs & Store verification scripts exist for CH Play and MS Store."""
    workflows_dir = WORKTREE_ROOT / ".github" / "workflows"
    all_workflows = {
        p.name: p.read_text(encoding="utf-8")
        for p in workflows_dir.glob("*.y*ml")
    }
    combined_workflows = "\n".join(all_workflows.values())

    assert "chplay" in combined_workflows.lower() or "aab" in combined_workflows.lower(), (
        "A Store CI workflow/job for CH Play .aab must exist"
    )
    assert "msstore" in combined_workflows.lower() or "package_msix.ps1" in combined_workflows, (
        "A Store CI workflow/job for Microsoft Store .msix must exist"
    )
    msix_verifiers = list((WORKTREE_ROOT / "scripts").glob("*msix*"))
    assert any("verify" in p.name.lower() for p in msix_verifiers), (
        "Expected an MSIX verification script in scripts/ (e.g., verify_msix_release.ps1)"
    )


def test_t1_f13_store_adr_and_submission_guides_exist_and_align() -> None:
    """F13: Store ADR, CH Play / MS Store submission guides, & refactoring-log updates exist."""
    adr_files = list((WORKTREE_ROOT / "docs" / "adr").glob("*.md"))
    adr_texts = {p.name: p.read_text(encoding="utf-8") for p in adr_files}
    combined_adrs = "\n".join(adr_texts.values())
    assert "chplay" in combined_adrs.lower() and "msstore" in combined_adrs.lower(), (
        "docs/adr/ must document the chplay and msstore distribution channels"
    )

    guide_files = [p.name.lower() for p in (WORKTREE_ROOT / "docs" / "guides").glob("*.md")]
    combined_guides = "\n".join(
        p.read_text(encoding="utf-8") for p in (WORKTREE_ROOT / "docs" / "guides").glob("*.md")
    )
    assert (
        any("play" in name or "store" in name or "chplay" in name for name in guide_files)
        and "chplay" in combined_guides.lower()
    ), "docs/guides/ must contain CH Play submission guidance"
    assert (
        any("store" in name or "msstore" in name or "msix" in name for name in guide_files)
        and "msstore" in combined_guides.lower()
        and ("wack" in combined_guides.lower() or "app certification" in combined_guides.lower())
        and "appdata" in combined_guides.lower()
        and ("virtualiz" in combined_guides.lower() or "ảo hóa" in combined_guides.lower())
    ), "docs/guides/ must contain Microsoft Store submission guidance covering WACK and AppData virtualization"

    ref_log = _read_text("docs/architecture/refactoring-log.md")
    assert "chplay" in ref_log.lower() or "store" in ref_log.lower()


def test_t1_f13_privacy_and_terms_cover_store_data_safety_and_android_storage() -> None:
    """F13: ``docs/PRIVACY.md`` & ``docs/TERMS.md`` cover Android storage, Data Safety, & Store channels."""
    privacy = _read_text("docs/PRIVACY.md")
    terms = _read_text("docs/TERMS.md")

    assert "Settings → Privacy" in privacy
    assert "FLET_APP_STORAGE_DATA" in privacy
    assert "Google Play" in privacy or "Data Safety" in privacy
    assert "Microsoft Store" in privacy
    assert "Moodle" in terms
    assert "Sentry" in terms
    assert "PolyForm" in terms
    assert ("Google Play" in terms or "CH Play" in terms) and "Microsoft Store" in terms
