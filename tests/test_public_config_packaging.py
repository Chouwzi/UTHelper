"""Tests for CI workflow wiring and fail-closed diagnostics-config.json packaging verifiers."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
import subprocess
import sys
import zipfile
import zlib

from PIL import Image
import pytest

from diagnostics.release_config import generate_public_config
from scripts.generate_msix_assets import MSIX_ASSET_SPECS
from scripts.package_unsigned_ipa import (
    IpaError,
    _REQUIRED_OFFICE_VIEWER_ASSETS,
    _REQUIRED_PDF_VIEWER_ASSETS,
    inspect_device_app,
    inspect_ipa,
    main as package_ipa_main,
    verify_ios_app_diagnostics_config,
    write_verification_evidence as write_ipa_evidence,
)
from scripts.release_inventory import _EXPECTED as INVENTORY_EXPECTED
from scripts.verify_android_release import (
    AndroidVerificationError,
    verify_android_release,
)
from scripts.verify_windows_bundle import (
    BundleVerificationError,
    inspect_bundle,
    verify_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
VERIFY_MSIX_PS1 = ROOT / "scripts" / "verify_msix_release.ps1"
BUILD_ANDROID_PS1 = ROOT / "scripts" / "build_android.ps1"

VALID_DSN = "https://0123456789abcdef@o123.ingest.sentry.io/456"
VALID_POSTHOG_KEY = "phc_AbCdEf0123456789_-GhIjKlMnOp"
VALID_POSTHOG_HOST = "https://us.i.posthog.com"

VERSION = "2.7.0"
BUILD_NUMBER = 2_007_000
PACKAGE_ID = "io.github.chouwzi.uthelper"
FINGERPRINT = "AB" * 32
COMMIT_SHA = "1" * 40

_RECEIVERS = (
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationReceiver",
    "com.dexterous.flutterlocalnotifications.ScheduledNotificationBootReceiver",
    "com.dexterous.flutterlocalnotifications.ActionBroadcastReceiver",
    "com.uthelper.backgroundsync.notification.DeadlineAlarmReceiver",
    "com.uthelper.backgroundsync.notification.RescheduleReceiver",
    "com.uthelper.backgroundsync.notification.ClassScheduleAlarmReceiver",
)

_VALID_MSIX_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap desktop rescap">
  <Identity Name="UTHelperOrg.UTHelper" Publisher="CN=UTHelper" Version="2.7.0.0" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>UTHelper</DisplayName>
    <PublisherDisplayName>UTHelper</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Resources>
    <Resource Language="en-us" />
    <Resource Language="vi-vn" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="UTHelper" Executable="UTHelper.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="UTHelper" Description="UTH activity assistant"
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


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CI Workflows & Build Script Forwarding Contracts
# ---------------------------------------------------------------------------


def test_workflows_and_android_script_forward_posthog_public_vars_safely():
    for wf_rel in (
        ".github/workflows/release.yml",
        ".github/workflows/build-android.yml",
        ".github/workflows/build-msstore.yml",
        ".github/workflows/build-ios.yml",
    ):
        wf = _read(wf_rel)
        assert "POSTHOG_PROJECT_API_KEY: ${{ vars.POSTHOG_PROJECT_API_KEY }}" in wf, wf_rel
        assert "POSTHOG_HOST: ${{ vars.POSTHOG_HOST }}" in wf, wf_rel
        assert "secrets.POSTHOG_PROJECT_API_KEY" not in wf, wf_rel
        assert "secrets.POSTHOG_HOST" not in wf, wf_rel
        invocations = [
            line
            for line in wf.splitlines()
            if "scripts/generate_public_runtime_config.py" in line
        ]
        assert invocations, wf_rel
        for line in invocations:
            assert "--posthog-key" in line, f"{wf_rel}: {line}"
            assert "--posthog-host" in line, f"{wf_rel}: {line}"
            assert not line.rstrip().endswith(("\\", "`")), f"{wf_rel}: {line}"

    # Ensure build-android.yml `build` job embeds PostHog vars so `sign-chplay-upload` inherits it
    android_wf = _read(".github/workflows/build-android.yml")
    build_job = android_wf.split("\n  build:\n", 1)[1].split(
        "\n  sign-chplay-upload:\n", 1
    )[0]
    assert "POSTHOG_PROJECT_API_KEY: ${{ vars.POSTHOG_PROJECT_API_KEY }}" in build_job
    assert "POSTHOG_HOST: ${{ vars.POSTHOG_HOST }}" in build_job
    assert "src/diagnostics/release_config.py" in android_wf

    msstore_wf = _read(".github/workflows/build-msstore.yml")
    assert "src/diagnostics/release_config.py" in msstore_wf
    assert "scripts/generate_public_runtime_*.py" in msstore_wf

    ps1 = _read("scripts/build_android.ps1")
    ps1_invocations = [
        line for line in ps1.splitlines() if "generate_public_runtime_config.py" in line
    ]
    assert len(ps1_invocations) == 2
    for line in ps1_invocations:
        assert '--posthog-key "$env:POSTHOG_PROJECT_API_KEY"' in line
        assert '--posthog-host "$env:POSTHOG_HOST"' in line

    ios_wf = _read(".github/workflows/build-ios.yml")
    assert "scripts/package_unsigned_ipa.py" in ios_wf
    assert "--require-diagnostics-config" in ios_wf

    release_wf = _read(".github/workflows/release.yml")
    ios_release_job = release_wf.split("\n  build-unsigned-ios:\n", 1)[1].split(
        "\n  build-signed-windows:\n", 1
    )[0]
    assert "scripts/package_unsigned_ipa.py" in ios_release_job
    assert "--require-diagnostics-config" in ios_release_job


# ---------------------------------------------------------------------------
# Windows Bundle Verifier (`scripts/verify_windows_bundle.py`)
# ---------------------------------------------------------------------------


def _write_minimal_windows_bundle(root: Path) -> None:
    (root / "Lib" / "encodings").mkdir(parents=True)
    (root / "app" / "assets" / "office_viewer").mkdir(parents=True)
    (root / "app" / "assets" / "pdf_viewer").mkdir(parents=True)
    (root / "site-packages" / "winrt").mkdir(parents=True)
    (root / "site-packages" / "win32" / "lib").mkdir(parents=True)
    (root / "site-packages" / "pywin32_system32").mkdir(parents=True)
    (root / "site-packages" / "flet_uth_quiz").mkdir(parents=True)

    (root / "UTHelper.exe").write_bytes(b"MZ-bundle")
    (root / "UTHelperAutostart.exe").write_bytes(b"MZ-bundle")
    (root / "python314.dll").write_bytes(b"dll")
    (root / "flutter_windows.dll").write_bytes(b"dll")
    (root / "flutter_inappwebview_windows_plugin.dll").write_bytes(b"dll")
    (root / "WebView2Loader.dll").write_bytes(b"dll")
    (root / "Lib" / "encodings" / "__init__.pyc").write_bytes(b"pyc")
    (root / "app" / "main.pyc").write_bytes(b"pyc")
    (root / "app" / "assets" / "release-version").write_bytes(b"2.7.0\n")

    for name in (
        "docx_host.html",
        "docx_host.js",
        "docx-preview.min.js",
        "jszip.min.js",
        "pptx_host.html",
        "pptx_host.js",
        "pptx_worker.js",
        "pptx_engine.js",
        "viewer.css",
        "viewer_common.js",
    ):
        (root / "app" / "assets" / "office_viewer" / name).write_bytes(b"ok")
    for name in (
        "pdf_host.html",
        "pdf_host.js",
        "pdf_viewer.css",
        "pdf.min.mjs",
        "pdf.worker.min.mjs",
    ):
        (root / "app" / "assets" / "pdf_viewer" / name).write_bytes(b"ok")

    (
        root
        / "site-packages"
        / "winrt"
        / "_winrt_windows_applicationmodel.cp314-win_amd64.pyd"
    ).write_bytes(b"pyd")
    for mod in ("win32api.pyd", "win32event.pyd", "win32security.pyd"):
        (root / "site-packages" / "win32" / mod).write_bytes(b"pyd")
    for mod in ("win32con.pyc", "winerror.pyc", "pywintypes.pyc"):
        (root / "site-packages" / "win32" / "lib" / mod).write_bytes(b"pyc")
    (root / "site-packages" / "pywin32_system32" / "pywintypes314.dll").write_bytes(b"dll")
    (root / "site-packages" / "flet_uth_quiz" / "__init__.pyc").write_bytes(b"pyc")
    (root / "site-packages" / "flet_uth_quiz" / "quiz_player.pyc").write_bytes(b"pyc")


def test_windows_bundle_verifier_enforces_diagnostics_config_fail_closed(tmp_path):
    _write_minimal_windows_bundle(tmp_path)

    with pytest.raises(BundleVerificationError, match="diagnostics-config.json"):
        verify_bundle(tmp_path, require_diagnostics_config=True)

    cfg_path = tmp_path / "app" / "assets" / "diagnostics-config.json"
    generate_public_config(
        cfg_path,
        VALID_DSN,
        build_environment="release",
        package_format="exe_msi",
        posthog_project_api_key=VALID_POSTHOG_KEY,
        posthog_host=VALID_POSTHOG_HOST,
    )
    assert inspect_bundle(tmp_path, require_diagnostics_config=True) == ()

    # Any leaked secret key, web UI host, or malformed schema fails closed even without --require
    for bad_payload in (
        {
            "schema_version": 3,
            "sentry_dsn": VALID_DSN,
            "build_environment": "release",
            "package_format": "exe_msi",
            "posthog_project_api_key": "phx_" + "a" * 28,
            "posthog_host": VALID_POSTHOG_HOST,
        },
        {
            "schema_version": 3,
            "sentry_dsn": VALID_DSN,
            "build_environment": "release",
            "package_format": "exe_msi",
            "posthog_project_api_key": VALID_POSTHOG_KEY,
            "posthog_host": "https://app.posthog.com",
        },
        {
            "schema_version": 3,
            "sentry_dsn": VALID_DSN,
            "build_environment": "release",
            "package_format": "exe_msi",
            "posthog_project_api_key": VALID_POSTHOG_KEY,
            "posthog_host": "https://127.0.0.1:8443",
        },
        {
            "schema_version": 3,
            "sentry_dsn": "https://sntrys_secret@o123.ingest.sentry.io/456",
            "build_environment": "release",
            "package_format": "exe_msi",
            "posthog_project_api_key": "",
            "posthog_host": "",
        },
    ):
        cfg_path.write_text(json.dumps(bad_payload), encoding="utf-8")
        with pytest.raises(BundleVerificationError, match="diagnostics-config.json"):
            verify_bundle(tmp_path)


# ---------------------------------------------------------------------------
# Android APK / AAB Verifier (`scripts/verify_android_release.py`)
# ---------------------------------------------------------------------------


def _sdk(tmp_path: Path) -> Path:
    sdk = tmp_path / "android-sdk"
    for path in (
        sdk
        / "build-tools"
        / "35.0.0"
        / ("apksigner.bat" if os.name == "nt" else "apksigner"),
        sdk
        / "cmdline-tools"
        / "latest"
        / "bin"
        / ("apkanalyzer.bat" if os.name == "nt" else "apkanalyzer"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("tool", encoding="utf-8")
    return sdk


def _fake_android_run(command, **_kwargs):
    if "apksigner" in Path(command[0]).name:
        stdout = f"Signer #1 certificate SHA-256 digest: {FINGERPRINT}\n"
    elif "application-id" in command:
        stdout = f"{PACKAGE_ID}\n"
    elif "version-name" in command:
        stdout = f"{VERSION}\n"
    elif "version-code" in command:
        stdout = f"{BUILD_NUMBER}\n"
    else:
        receivers = "".join(f'<receiver android:name="{name}" />' for name in _RECEIVERS)
        stdout = (
            '<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
            '<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="36" />'
            f'<application android:allowBackup="false">{receivers}</application></manifest>'
        )
    return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _pb_field(num: int, payload: bytes) -> bytes:
    tag = (num << 3) | 2
    size = len(payload)
    out = bytearray([tag])
    while True:
        b = size & 0x7F
        size >>= 7
        if size:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out) + payload


def _dex(strings: tuple[str, ...]) -> bytes:
    header = 0x70
    data_off = header + 4 * len(strings)
    blob = bytearray()
    offsets = []
    for text in strings:
        offsets.append(data_off + len(blob))
        val = len(text)
        uleb = bytearray()
        while True:
            b = val & 0x7F
            val >>= 7
            if val:
                uleb.append(b | 0x80)
            else:
                uleb.append(b)
                break
        blob += bytes(uleb) + text.encode("utf-8") + b"\x00"
    buf = bytearray(data_off + len(blob))
    buf[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", buf, 40, 0x12345678)
    struct.pack_into("<II", buf, 56, len(strings), header if strings else 0)
    for idx, off in enumerate(offsets):
        struct.pack_into("<I", buf, header + 4 * idx, off)
    buf[data_off:] = blob
    struct.pack_into("<I", buf, 8, zlib.adler32(bytes(buf[12:])) & 0xFFFFFFFF)
    return bytes(buf)


def _elf64_16k() -> bytes:
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8,
        3,
        183,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        1,
        64,
        0,
        0,
    )
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 64, 64, 0x4000)
    return ehdr + phdr


def _build_nested_app_zip(
    diag_payload: dict[str, object] | None,
    *,
    include_viewers: bool = False,
    channel: str | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as nested:
        if diag_payload is not None:
            nested.writestr(
                "assets/diagnostics-config.json",
                json.dumps(diag_payload),
            )
        if channel is not None:
            nested.writestr(
                "assets/distribution-channel.json",
                json.dumps({"channel": channel, "android_package_id": PACKAGE_ID}),
            )
        if include_viewers:
            for name in (
                "docx_host.html",
                "docx_host.js",
                "docx-preview.min.js",
                "jszip.min.js",
                "pptx_host.html",
                "pptx_host.js",
                "pptx_worker.js",
                "pptx_engine.js",
                "viewer.css",
                "viewer_common.js",
            ):
                nested.writestr(f"assets/office_viewer/{name}", b"ok")
            for name in (
                "pdf_host.html",
                "pdf_host.js",
                "pdf_viewer.css",
                "pdf.min.mjs",
                "pdf.worker.min.mjs",
            ):
                nested.writestr(f"assets/pdf_viewer/{name}", b"ok")
    return buf.getvalue()


def test_android_verifier_validates_nested_diagnostics_config_and_preserves_inventory_checks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(subprocess, "run", _fake_android_run)
    sdk_dir = _sdk(tmp_path)

    valid_v3 = {
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
        "build_environment": "release",
        "package_format": "apk",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "posthog_host": VALID_POSTHOG_HOST,
    }

    apk_path = tmp_path / f"UTHelper-{VERSION}.apk"
    with zipfile.ZipFile(apk_path, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
        zf.writestr(
            "assets/app.zip",
            _build_nested_app_zip(valid_v3, include_viewers=True),
        )

    evidence = verify_android_release(
        apk=apk_path,
        channel="apk_lts",
        version=VERSION,
        build_number=BUILD_NUMBER,
        package_id=PACKAGE_ID,
        certificate_sha256=FINGERPRINT,
        commit_sha=COMMIT_SHA,
        workflow_run_id="99",
        output=tmp_path / "apk.verification.json",
        android_home=sdk_dir,
        require_office_viewer=True,
        require_diagnostics_config=True,
    )
    # Exact lock check against scripts/release_inventory.py!
    expected_apk_checks = INVENTORY_EXPECTED[".apk"][3]
    assert frozenset(evidence["checks"]) == expected_apk_checks

    # Missing diagnostics-config.json when --require-diagnostics-config is enabled -> rejected
    apk_missing = tmp_path / "missing" / f"UTHelper-{VERSION}.apk"
    apk_missing.parent.mkdir()
    with zipfile.ZipFile(apk_missing, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
    with pytest.raises(AndroidVerificationError, match="diagnostics-config.json"):
        verify_android_release(
            apk=apk_missing,
            channel="apk_lts",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="99",
            output=tmp_path / "missing.json",
            android_home=sdk_dir,
            require_diagnostics_config=True,
        )

    # Leaked phx_ personal key inside CH Play AAB nested base/assets/app.zip -> rejected
    bad_aab = tmp_path / "leaked.aab"
    bundle_cfg = _pb_field(1, _pb_field(2, b"1.18.1")) + _pb_field(
        2, _pb_field(2, bytes([0x08, 1, 0x10, 2]))
    )
    recvs = "".join(f'<receiver android:name="{r}" />' for r in _RECEIVERS)
    manifest = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        f'package="{PACKAGE_ID}" android:versionCode="{BUILD_NUMBER}" '
        f'android:versionName="{VERSION}">'
        '<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="36" />'
        f'<application android:allowBackup="false">{recvs}</application></manifest>'
    )
    leaked_v3 = dict(valid_v3, posthog_project_api_key="phx_" + "s" * 28, package_format="aab")
    with zipfile.ZipFile(bad_aab, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("BundleConfig.pb", bundle_cfg)
        zf.writestr("base/manifest/AndroidManifest.xml", manifest.encode("utf-8"))
        zf.writestr("base/dex/classes.dex", _dex(("Lcom/uthelper/MainActivity;",)))
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"base/lib/{abi}/libflutter.so", _elf64_16k())
        zf.writestr(
            "base/assets/app.zip",
            _build_nested_app_zip(leaked_v3, channel="chplay"),
        )
    with pytest.raises(AndroidVerificationError, match="diagnostics-config.json"):
        verify_android_release(
            aab=bad_aab,
            channel="chplay",
            allow_unsigned=True,
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            commit_sha=COMMIT_SHA,
            workflow_run_id="99",
            output=tmp_path / "aab.json",
        )


def test_android_verifier_rejects_duplicate_zip_entries_symlinks_and_non_primary_config(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(subprocess, "run", _fake_android_run)
    sdk_dir = _sdk(tmp_path)

    valid_v3 = {
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
        "build_environment": "release",
        "package_format": "apk",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "posthog_host": VALID_POSTHOG_HOST,
    }
    leaked_v3 = dict(valid_v3, posthog_project_api_key="phx_" + "s" * 28)

    # 1. Duplicate assets/diagnostics-config.json inside nested app.zip (first phx_SECRET, second clean)
    dup_inner_buf = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(dup_inner_buf, "w") as nested:
            nested.writestr("assets/diagnostics-config.json", json.dumps(leaked_v3))
            nested.writestr("assets/diagnostics-config.json", json.dumps(valid_v3))

    dup_apk = tmp_path / "dup-inner" / f"UTHelper-{VERSION}.apk"
    dup_apk.parent.mkdir()
    with zipfile.ZipFile(dup_apk, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
        zf.writestr("assets/app.zip", dup_inner_buf.getvalue())

    for require_flag in (True, False):
        with pytest.raises(
            AndroidVerificationError,
            match="duplicate ZIP entry|secret, personal, or management tokens",
        ):
            verify_android_release(
                apk=dup_apk,
                channel="apk_lts",
                version=VERSION,
                build_number=BUILD_NUMBER,
                package_id=PACKAGE_ID,
                certificate_sha256=FINGERPRINT,
                commit_sha=COMMIT_SHA,
                workflow_run_id="99",
                output=tmp_path / f"dup-{require_flag}.json",
                android_home=sdk_dir,
                require_diagnostics_config=require_flag,
            )

    # 1b. Duplicate clean entries inside nested app.zip also rejected strictly as duplicate ZIP entry
    dup_clean_buf = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(dup_clean_buf, "w") as nested:
            nested.writestr("assets/diagnostics-config.json", json.dumps(valid_v3))
            nested.writestr("assets/diagnostics-config.json", json.dumps(valid_v3))

    dup_clean_apk = tmp_path / "dup-clean" / f"UTHelper-{VERSION}.apk"
    dup_clean_apk.parent.mkdir()
    with zipfile.ZipFile(dup_clean_apk, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
        zf.writestr("assets/app.zip", dup_clean_buf.getvalue())

    with pytest.raises(AndroidVerificationError, match="duplicate ZIP entry"):
        verify_android_release(
            apk=dup_clean_apk,
            channel="apk_lts",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="99",
            output=tmp_path / "dup-clean.json",
            android_home=sdk_dir,
            require_diagnostics_config=True,
        )

    # 2. Symlink entry inside nested app.zip -> rejected
    sym_inner_buf = io.BytesIO()
    with zipfile.ZipFile(sym_inner_buf, "w") as nested:
        sym_info = zipfile.ZipInfo("assets/diagnostics-config.json")
        sym_info.create_system = 3
        sym_info.external_attr = (stat.S_IFLNK | 0o777) << 16
        nested.writestr(sym_info, b"../../etc/passwd")

    sym_apk = tmp_path / "sym-inner" / f"UTHelper-{VERSION}.apk"
    sym_apk.parent.mkdir()
    with zipfile.ZipFile(sym_apk, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
        zf.writestr("assets/app.zip", sym_inner_buf.getvalue())

    with pytest.raises(AndroidVerificationError, match="symlink"):
        verify_android_release(
            apk=sym_apk,
            channel="apk_lts",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="99",
            output=tmp_path / "sym.json",
            android_home=sdk_dir,
            require_diagnostics_config=True,
        )

    # 3. Non-primary diagnostics-config.json inside nested app.zip (missing assets/diagnostics-config.json)
    decoy_inner_buf = io.BytesIO()
    with zipfile.ZipFile(decoy_inner_buf, "w") as nested:
        nested.writestr("decoy/diagnostics-config.json", json.dumps(valid_v3))

    decoy_apk = tmp_path / "decoy" / f"UTHelper-{VERSION}.apk"
    decoy_apk.parent.mkdir()
    with zipfile.ZipFile(decoy_apk, "w") as zf:
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libapp.so", b"so")
        zf.writestr("assets/app.zip", decoy_inner_buf.getvalue())

    with pytest.raises(AndroidVerificationError, match="primary assets/diagnostics-config.json"):
        verify_android_release(
            apk=decoy_apk,
            channel="apk_lts",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="99",
            output=tmp_path / "decoy.json",
            android_home=sdk_dir,
            require_diagnostics_config=True,
        )


# ---------------------------------------------------------------------------
# iOS App & Unsigned IPA Verifier (`scripts/package_unsigned_ipa.py`)
# ---------------------------------------------------------------------------


def _make_ios_app(
    root: Path,
    *,
    platform: str = "iPhoneOS",
    cpu_type: int = 0x0100000C,
    include_viewers: bool = True,
    diag_payload: dict[str, object] | None = None,
) -> Path:
    app = root / "UTHelper.app"
    app.mkdir(parents=True, exist_ok=True)
    info = {
        "CFBundleIdentifier": PACKAGE_ID,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": str(BUILD_NUMBER),
        "CFBundleExecutable": "UTHelper",
        "CFBundleSupportedPlatforms": [platform],
    }
    (app / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    (app / "UTHelper").write_bytes(struct.pack("<Iii", 0xFEEDFACF, cpu_type, 0))
    app_zip = app / "Frameworks" / "App.framework" / "flutter_assets" / "app" / "app.zip"
    app_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(app_zip, "w") as nested:
        if include_viewers:
            for asset in _REQUIRED_OFFICE_VIEWER_ASSETS:
                nested.writestr(f"assets/office_viewer/{asset}", b"ok")
            for asset in _REQUIRED_PDF_VIEWER_ASSETS:
                nested.writestr(f"assets/pdf_viewer/{asset}", b"ok")
        if diag_payload is not None:
            nested.writestr("assets/diagnostics-config.json", json.dumps(diag_payload))
    return app


def _write_ipa_from_app(ipa_path: Path, app: Path) -> Path:
    with zipfile.ZipFile(ipa_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(app.rglob("*")):
            rel = item.relative_to(app.parent).as_posix()
            archive.write(item, f"Payload/{rel}")
    return ipa_path


def test_ios_verifier_enforces_diagnostics_config_and_preserves_inventory_checks(
    tmp_path: Path,
) -> None:
    valid_ipa_v3 = {
        "schema_version": 3,
        "sentry_dsn": VALID_DSN,
        "build_environment": "release",
        "package_format": "ipa",
        "posthog_project_api_key": VALID_POSTHOG_KEY,
        "posthog_host": VALID_POSTHOG_HOST,
    }
    app = _make_ios_app(tmp_path / "valid", diag_payload=valid_ipa_v3)
    assert verify_ios_app_diagnostics_config(app, require=True) is True

    ipa_path = _write_ipa_from_app(tmp_path / f"UTHelper-{VERSION}.ipa", app)
    meta = inspect_ipa(
        ipa_path,
        version=VERSION,
        build_number=str(BUILD_NUMBER),
        bundle_id=PACKAGE_ID,
        require_office_viewer=True,
        require_diagnostics_config=True,
    )
    ev_out = tmp_path / "ipa.verification.json"
    write_ipa_evidence(
        ipa_path,
        meta,
        commit_sha=COMMIT_SHA,
        workflow_run_id="99",
        output=ev_out,
    )
    ev = json.loads(ev_out.read_text(encoding="utf-8"))
    assert frozenset(ev["checks"]) == INVENTORY_EXPECTED[".ipa"][3]

    # Simulator diagnostic CLI mode (--verify-app ... --expected-build-environment develop)
    sim_app = _make_ios_app(
        tmp_path / "sim",
        platform="iPhoneSimulator",
        diag_payload={
            "schema_version": 3,
            "sentry_dsn": "",
            "build_environment": "develop",
            "package_format": "ios_app",
            "posthog_project_api_key": "",
            "posthog_host": "",
        },
    )
    assert (
        package_ipa_main(
            [
                "--verify-app",
                str(sim_app),
                "--require-office-viewer",
                "--require-diagnostics-config",
                "--expected-build-environment",
                "develop",
            ]
        )
        == 0
    )
    # Default require=True rejects build_environment="develop" for release IPA verification
    with pytest.raises(IpaError, match="build_environment"):
        verify_ios_app_diagnostics_config(sim_app, require=True)

    # Missing diagnostics-config.json rejected when required
    missing_app = _make_ios_app(tmp_path / "missing", diag_payload=None)
    with pytest.raises(IpaError, match="diagnostics-config.json"):
        inspect_device_app(
            missing_app,
            version=VERSION,
            build_number=str(BUILD_NUMBER),
            bundle_id=PACKAGE_ID,
            require_diagnostics_config=True,
        )

    # Wrong package_format ("apk"), leaked phx_ key, or asymmetric PostHog fields rejected
    for idx, bad_cfg in enumerate(
        (
            dict(valid_ipa_v3, package_format="apk"),
            dict(valid_ipa_v3, posthog_project_api_key="phx_" + "k" * 28),
            dict(valid_ipa_v3, posthog_host=""),
        )
    ):
        bad_app = _make_ios_app(tmp_path / f"bad-ios-{idx}", diag_payload=bad_cfg)
        with pytest.raises(IpaError, match="diagnostics-config.json"):
            inspect_device_app(
                bad_app,
                version=VERSION,
                build_number=str(BUILD_NUMBER),
                bundle_id=PACKAGE_ID,
                require_diagnostics_config=False,
            )

    # Duplicate ZIP entries inside iOS nested app.zip (first phx_SECRET, second clean) -> rejected
    dup_app = _make_ios_app(tmp_path / "dup-ios", diag_payload=None)
    app_zip = dup_app / "Frameworks" / "App.framework" / "flutter_assets" / "app" / "app.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(app_zip, "a") as nested:
            nested.writestr(
                "assets/diagnostics-config.json",
                json.dumps(dict(valid_ipa_v3, posthog_project_api_key="phx_" + "x" * 28)),
            )
            nested.writestr("assets/diagnostics-config.json", json.dumps(valid_ipa_v3))
    with pytest.raises(
        IpaError,
        match="duplicate member|secret, personal, or management tokens",
    ):
        verify_ios_app_diagnostics_config(dup_app, require=True)

    dup_clean_app = _make_ios_app(tmp_path / "dup-clean-ios", diag_payload=valid_ipa_v3)
    clean_app_zip = dup_clean_app / "Frameworks" / "App.framework" / "flutter_assets" / "app" / "app.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(clean_app_zip, "a") as nested:
            nested.writestr("assets/diagnostics-config.json", json.dumps(valid_ipa_v3))
    with pytest.raises(IpaError, match="duplicate member"):
        verify_ios_app_diagnostics_config(dup_clean_app, require=True)


# ---------------------------------------------------------------------------
# Microsoft Store MSIX Verifier (`scripts/verify_msix_release.ps1`)
# ---------------------------------------------------------------------------


def _stage_msix_dir(root: Path, diag_payload: dict[str, object] | None) -> Path:
    assets_dir = root / "Assets"
    app_assets = root / "app" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    app_assets.mkdir(parents=True, exist_ok=True)
    (root / "UTHelper.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (root / "UTHelperAutostart.exe").write_bytes(b"MZ" + b"\x00" * 126)
    (root / "AppxManifest.xml").write_text(_VALID_MSIX_MANIFEST, encoding="utf-8")
    for filename, size in MSIX_ASSET_SPECS.items():
        Image.new("RGBA", size, (15, 23, 42, 255)).save(
            assets_dir / filename,
            format="PNG",
        )
    (app_assets / "distribution-channel.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "msstore",
                "store_product_id": "9NBLGGH4NNS1",
                "android_package_id": PACKAGE_ID,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if diag_payload is not None:
        (app_assets / "diagnostics-config.json").write_text(
            json.dumps(diag_payload) + "\n",
            encoding="utf-8",
        )
    return root


def _run_ps51(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            *args,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="PowerShell 5.1 check requires Windows")
def test_owned_powershell_scripts_parse_cleanly_in_ps51() -> None:
    script_list = ",".join(f"'{p}'" for p in (VERIFY_MSIX_PS1, BUILD_ANDROID_PS1))
    cmd = (
        f"foreach ($p in @({script_list})) {{ "
        "$tokens = $null; $errors = $null; "
        "[void][System.Management.Automation.Language.Parser]::ParseFile($p, [ref]$tokens, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.ToString() }; exit 1 } "
        "}"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", cmd],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=20,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="MSIX verifier requires Windows")
def test_msix_verifier_validates_schema_v3_and_rejects_secrets_without_changing_checks(
    tmp_path: Path,
) -> None:
    valid_stage = _stage_msix_dir(
        tmp_path / "valid-v3",
        {
            "schema_version": 3,
            "sentry_dsn": VALID_DSN,
            "build_environment": "release",
            "package_format": "msix",
            "posthog_project_api_key": VALID_POSTHOG_KEY,
            "posthog_host": "https://eu.i.posthog.com/ingest",
        },
    )
    evidence_out = tmp_path / "msix-evidence.json"
    ok = _run_ps51(
        VERIFY_MSIX_PS1,
        [
            "-MsixPath",
            str(valid_stage),
            "-Channel",
            "msstore",
            "-IdentityName",
            "UTHelperOrg.UTHelper",
            "-Publisher",
            "CN=UTHelper",
            "-Version",
            "2.7.0.0",
            "-StoreProductId",
            "9NBLGGH4NNS1",
            "-RequireDiagnosticsConfig",
            "-Output",
            str(evidence_out),
        ],
    )
    assert ok.returncode == 0, f"stdout={ok.stdout}\nstderr={ok.stderr}"
    evidence = json.loads(evidence_out.read_text(encoding="utf-8"))
    assert evidence["checks"] == [
        "appx_manifest_schema",
        "identity_and_version",
        "manifest_resources",
        "capabilities_full_trust",
        "appdata_virtualization_enabled",
        "startup_task_argument_free",
        "pe_executables",
        "visual_asset_dimensions",
        "distribution_channel_msstore",
    ]

    # Missing diagnostics-config.json with -RequireDiagnosticsConfig -> fail closed
    missing_stage = _stage_msix_dir(tmp_path / "missing-diag", None)
    missing_proc = _run_ps51(
        VERIFY_MSIX_PS1,
        [
            "-MsixPath",
            str(missing_stage),
            "-Channel",
            "msstore",
            "-RequireDiagnosticsConfig",
        ],
    )
    assert missing_proc.returncode != 0
    assert "diagnostics-config.json" in (missing_proc.stdout + missing_proc.stderr)

    # Valid public global IPv6 literal passes MSIX verification
    v6_stage = _stage_msix_dir(
        tmp_path / "valid-ipv6",
        {
            "schema_version": 3,
            "sentry_dsn": VALID_DSN,
            "build_environment": "release",
            "package_format": "msix",
            "posthog_project_api_key": VALID_POSTHOG_KEY,
            "posthog_host": "https://[2606:4700:4700::1111]/ingest",
        },
    )
    v6_ok = _run_ps51(
        VERIFY_MSIX_PS1,
        ["-MsixPath", str(v6_stage), "-Channel", "msstore", "-RequireDiagnosticsConfig"],
    )
    assert v6_ok.returncode == 0, f"stdout={v6_ok.stdout}\nstderr={v6_ok.stderr}"

    # Decoy diagnostics-config.json outside app\assets\diagnostics-config.json -> fail closed
    decoy_stage = _stage_msix_dir(tmp_path / "decoy-diag", None)
    (decoy_stage / "other").mkdir()
    (decoy_stage / "other" / "diagnostics-config.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": "",
                "posthog_host": "",
            }
        ),
        encoding="utf-8",
    )
    decoy_proc = _run_ps51(
        VERIFY_MSIX_PS1,
        ["-MsixPath", str(decoy_stage), "-Channel", "msstore", "-RequireDiagnosticsConfig"],
    )
    assert decoy_proc.returncode != 0
    assert "app\\assets\\diagnostics-config.json" in (decoy_proc.stdout + decoy_proc.stderr)

    # Leaked secrets / web UI host / private & transition IPv6 / bad ports / wrong package_format / asymmetric v3 -> fail closed
    for idx, bad_payload in enumerate(
        (
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": "phx_" + "z" * 28,
                "posthog_host": VALID_POSTHOG_HOST,
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://app.posthog.com",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://127.0.0.1:8443",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "exe_msi",
                "posthog_project_api_key": "",
                "posthog_host": "",
            },
            {
                "schema_version": 1,
                "sentry_dsn": VALID_DSN,
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://us.i.posthog.com:0",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://us.i.posthog.com:",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[64:ff9b::127.0.0.1]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[2002:7f00:1::1]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[2001:0000:4136:e378:8000:63bf:3fff:fdd2]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[2001:2::1]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[100::1]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[::ffff:8.8.8.8]",
            },
            {
                "schema_version": 3,
                "sentry_dsn": VALID_DSN,
                "build_environment": "release",
                "package_format": "msix",
                "posthog_project_api_key": VALID_POSTHOG_KEY,
                "posthog_host": "https://[::ffff:0:127.0.0.1]",
            },
            {
                "schema_version": True,
                "sentry_dsn": VALID_DSN,
            },
        )
    ):
        bad_stage = _stage_msix_dir(tmp_path / f"bad-{idx}", bad_payload)
        bad_proc = _run_ps51(
            VERIFY_MSIX_PS1,
            ["-MsixPath", str(bad_stage), "-Channel", "msstore"],
        )
        assert bad_proc.returncode != 0, f"Case {idx} unexpectedly passed"
        assert "diagnostics-config.json" in (bad_proc.stdout + bad_proc.stderr)


def test_privacy_adr_and_store_docs_stay_aligned_with_runtime_and_packaging_contracts() -> None:
    privacy = _read("docs/PRIVACY.md")
    adr7 = _read("docs/adr/0007-privacy-preserving-posthog-analytics.md")
    store_guide = _read("docs/guides/store-distribution-submission.md")
    ref_log = _read("docs/architecture/refactoring-log.md")

    assert "per-process rolling 24h" in privacy
    assert "FLET_APP_STORAGE_DATA" in privacy and "FLET_APP_DATA" in privacy
    assert "Reset Settings" in privacy

    assert "ipa_lts" in adr7
    assert "ios_sideload" not in adr7
    assert "switching accounts, opting out, or exiting stamps `uth_session_ended`" not in adr7
    assert "Reset Settings" in adr7

    assert "--build-environment release --package-format msix" in store_guide
    assert "configured together as a pair" in " ".join(store_guide.split())

    assert 'and `privacy_section.py` (`apply_analytics_consent`, `reset_analytics_state`)' not in ref_log
    assert "Reset Settings" in ref_log
