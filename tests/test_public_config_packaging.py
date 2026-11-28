"""Tests for CI workflow wiring and fail-closed diagnostics-config.json packaging verifiers."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import zipfile
import zlib

from PIL import Image
import pytest

from diagnostics.release_config import generate_public_config
from scripts.generate_msix_assets import MSIX_ASSET_SPECS
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

    # Leaked secrets / web UI host / private IP / boolean schema_version -> fail closed
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
                "schema_version": True,
                "sentry_dsn": VALID_DSN,
            },
        )
    ):
        bad_stage = _stage_msix_dir(tmp_path / f"bad-{idx}", bad_payload)
        bad_proc = _run_ps51(VERIFY_MSIX_PS1, ["-MsixPath", str(bad_stage)])
        assert bad_proc.returncode != 0, f"Case {idx} unexpectedly passed"
        assert "diagnostics-config.json" in (bad_proc.stdout + bad_proc.stderr)
