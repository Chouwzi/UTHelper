"""CH Play (``chplay``) vs GitHub LTS (``apk_lts``) Android packaging contract tests."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tomllib
import zipfile
import zlib

import pytest

from scripts.release_metadata import (
    ReleaseMetadataError,
    build_distribution_channel_payload,
    write_distribution_channel,
)
from scripts.verify_android_release import (
    AndroidVerificationError,
    decode_aab_manifest_bytes,
    extract_dex_strings,
    verify_android_release,
    verify_elf_16kb_alignment,
)


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = (
    ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "android"
)
VERSION = "2.5.1"
BUILD_NUMBER = 2_005_001
PACKAGE_ID = "com.uthelper.uthelper"
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


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _manifest(
    *,
    target_sdk: int | None = 35,
    permissions: tuple[str, ...] = (
        "android.permission.INTERNET",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.RECEIVE_BOOT_COMPLETED",
        "android.permission.SCHEDULE_EXACT_ALARM",
    ),
    application_attrs: str = 'android:allowBackup="false"',
    extra_application: str = "",
    receivers: tuple[str, ...] = _RECEIVERS,
) -> str:
    sdk = (
        f'<uses-sdk android:minSdkVersion="23" android:targetSdkVersion="{target_sdk}" />'
        if target_sdk is not None
        else ""
    )
    perms = "".join(f'<uses-permission android:name="{p}" />' for p in permissions)
    recvs = "".join(f'<receiver android:name="{r}" />' for r in receivers)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
        f'package="{PACKAGE_ID}" android:versionCode="{BUILD_NUMBER}" '
        f'android:versionName="{VERSION}">'
        f"{sdk}{perms}<application {application_attrs}>{extra_application}{recvs}"
        "</application></manifest>"
    )


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _dex(strings: tuple[str, ...]) -> bytes:
    header = 0x70
    data_off = header + 4 * len(strings)
    blob = bytearray()
    offsets = []
    for text in strings:
        offsets.append(data_off + len(blob))
        blob += _uleb(len(text)) + text.encode("utf-8") + b"\x00"
    buf = bytearray(data_off + len(blob))
    buf[0:8] = b"dex\n035\x00"
    struct.pack_into("<I", buf, 40, 0x12345678)
    struct.pack_into("<II", buf, 56, len(strings), header if strings else 0)
    for index, offset in enumerate(offsets):
        struct.pack_into("<I", buf, header + 4 * index, offset)
    buf[data_off:] = blob
    struct.pack_into("<I", buf, 8, zlib.adler32(bytes(buf[12:])) & 0xFFFFFFFF)
    return bytes(buf)


def _elf64(*aligns: int) -> bytes:
    aligns = aligns or (0x4000,)
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8,
        3, 183, 1, 0, 64, 0, 0, 64, 56, len(aligns), 64, 0, 0,
    )
    phdrs = b"".join(
        struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 64, 64, align) for align in aligns
    )
    return ehdr + phdrs


def _bundle_config_pb(*, enabled: int | None = 1, alignment: int | None = 2) -> bytes:
    uncompress = bytearray()
    if enabled is not None:
        uncompress += bytes([0x08, enabled])
    if alignment is not None:
        uncompress += bytes([0x10, alignment])
    optimizations = _pb_field(2, bytes(uncompress))
    bundletool = _pb_field(2, b"1.18.1")
    return _pb_field(1, bundletool) + _pb_field(2, optimizations)


def _aab(
    path: Path,
    *,
    manifest: str | None = None,
    align: int = 0x4000,
    dex_strings: tuple[str, ...] = ("Lcom/uthelper/MainActivity;",),
    channel: dict[str, object] | None = None,
    extra: dict[str, bytes] | None = None,
    extra_dex: dict[str, bytes] | None = None,
    bundle_config: bytes | None = None,
    include_bundle_config: bool = True,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        if include_bundle_config:
            archive.writestr(
                "BundleConfig.pb",
                bundle_config if bundle_config is not None else _bundle_config_pb(),
            )
        archive.writestr(
            "base/manifest/AndroidManifest.xml",
            (manifest if manifest is not None else _manifest()).encode(),
        )
        archive.writestr("base/dex/classes.dex", _dex(dex_strings))
        for name, content in (extra_dex or {}).items():
            archive.writestr(name, content)
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            archive.writestr(f"base/lib/{abi}/libflutter.so", _elf64(align))
        archive.writestr(
            "base/assets/flutter_assets/app/assets/distribution-channel.json",
            json.dumps(
                channel if channel is not None else {"channel": "chplay", "android_package_id": PACKAGE_ID}
            ),
        )
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return path


def _verify(aab: Path, tmp_path: Path, **overrides):
    options = dict(
        aab=aab,
        channel="chplay",
        version=VERSION,
        build_number=BUILD_NUMBER,
        package_id=PACKAGE_ID,
        commit_sha=COMMIT_SHA,
        workflow_run_id="42",
        output=tmp_path / "evidence.json",
        allow_unsigned=True,
    )
    options.update(overrides)
    return verify_android_release(**options)


# ---------------------------------------------------------------------------
# Static source / manifest / build-config contracts
# ---------------------------------------------------------------------------


def test_pyproject_is_https_only_backup_disabled_and_play_targeted():
    config = tomllib.loads(_read("pyproject.toml"))
    dependencies = config["project"]["dependencies"]
    android = config["tool"]["flet"]["android"]
    permissions = android["permission"]
    application = android["manifest_application"]
    gradle_props = android["gradle_properties"]

    assert any(dep.startswith("httpx>=") for dep in dependencies)
    assert android["target_sdk_version"] >= 35
    assert application["usesCleartextTraffic"] == "false"
    assert application["allowBackup"] == "false"
    assert gradle_props["kotlin.compiler.execution.strategy"] == "in-process"
    assert gradle_props["kotlin.incremental"] == "false"
    assert "android.permission.FOREGROUND_SERVICE" not in permissions
    assert "android.permission.FOREGROUND_SERVICE_DATA_SYNC" not in permissions
    assert "android.permission.USE_EXACT_ALARM" not in permissions
    assert permissions["android.permission.SCHEDULE_EXACT_ALARM"] is True
    assert permissions["android.permission.POST_NOTIFICATIONS"] is True
    # LTS keeps the sideload installer permission; chplay strips it at build time.
    assert permissions["android.permission.REQUEST_INSTALL_PACKAGES"] is True


def test_build_android_script_defines_channel_matrix_and_cleanup():
    script = _read("scripts/build_android.ps1")
    helper = _read("scripts/prepare_android_flutter_project.py")

    assert '[ValidateSet("apk_lts", "chplay")]' in script
    assert '[ValidateSet("apk", "aab")]' in script
    assert "[switch]$RequireSigned" in script
    assert "FLET_ANDROID_SIGNING_STORE_PASSWORD" in script
    assert '$fletOptions += @("--build-version", $buildVersion, "--build-number", $buildNumber)' in script
    assert "release_build_number" in script
    assert "--no-android-legacy-packaging" in script
    assert "--android-legacy-packaging" in script
    assert "distribution-channel.json" in script
    assert "--distribution-channel $Channel" in script
    assert "uthDistributionChannel" in script
    assert "prepare_android_flutter_project.py" in script
    for stripped in (
        "UthUpdateFileProvider",
        "ForegroundService",
        "REQUEST_INSTALL_PACKAGES",
        "FOREGROUND_SERVICE_DATA_SYNC",
        "USE_EXACT_ALARM",
        "usesCleartextTraffic",
        "allowBackup",
        "extractNativeLibs",
    ):
        assert stripped in helper
    finally_block = script.rsplit("} finally {", 1)[1]
    assert "Remove-Item -LiteralPath $channelAssetPath" in finally_block
    assert "UTH_DISTRIBUTION_CHANNEL" in finally_block


def test_distribution_channel_asset_is_never_tracked():
    assert "src/assets/distribution-channel.json" in _read(".gitignore").splitlines()
    tracked = subprocess.run(
        ["git", "ls-files", "--", "src/assets/distribution-channel.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.stdout.strip() == ""


def test_main_source_set_has_no_updater_symbols_outside_update_package():
    main_kotlin = PLUGIN / "src" / "main" / "kotlin" / "com" / "uthelper" / "backgroundsync"
    outside_update = [
        path
        for path in main_kotlin.rglob("*.kt")
        if "update" not in path.relative_to(main_kotlin).parts[:-1]
    ]
    assert outside_update
    forbidden = (
        "ApkUpdate",
        "UthUpdateFileProvider",
        "REQUEST_INSTALL_PACKAGES",
        "ACTION_MANAGE_UNKNOWN_APP_SOURCES",
        "application/vnd.android.package-archive",
    )
    for path in outside_update:
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path.name} leaks {token} into chplay"


def test_unused_foreground_service_is_never_requested_by_workers():
    for path in (PLUGIN / "src" / "main").rglob("*.kt"):
        text = path.read_text(encoding="utf-8")
        assert "setForeground" not in text
        assert "startForeground" not in text
    assert "USE_EXACT_ALARM" not in (PLUGIN / "src/main/AndroidManifest.xml").read_text(
        encoding="utf-8"
    )


def test_exact_alarm_and_notification_denial_degrade_gracefully():
    alarms = (PLUGIN / "src/main/kotlin/com/uthelper/backgroundsync/notification/AlarmScheduler.kt").read_text(encoding="utf-8")
    dispatcher = (PLUGIN / "src/main/kotlin/com/uthelper/backgroundsync/notification/NotificationDispatcher.kt").read_text(encoding="utf-8")

    assert "canScheduleExactAlarms()" in alarms
    assert "setAndAllowWhileIdle" in alarms
    assert "POST_NOTIFICATIONS" in dispatcher
    assert re.search(r"POST_NOTIFICATIONS,\s*\)\s*!=\s*PackageManager\.PERMISSION_GRANTED\s*\)\s*return false", dispatcher)


def test_android_workflow_builds_and_verifies_both_channels():
    workflow = _read(".github/workflows/build-android.yml")

    assert "channel: apk_lts" in workflow and "target: apk" in workflow
    assert "channel: chplay" in workflow and "target: aab" in workflow
    assert "branches: [develop, main]" in workflow
    assert "sign_upload:" in workflow
    assert "PLAY_UPLOAD_KEYSTORE_BASE64" in workflow
    assert "prepare_android_flutter_project.py" in workflow
    assert "~/.gradle/init.d" not in workflow
    assert "classes.jar" in workflow
    assert "--no-android-legacy-packaging" in workflow
    assert "--android-legacy-packaging" in workflow
    assert "scripts/verify_android_release.py" in workflow
    assert "--channel chplay" in workflow and "--allow-unsigned" in workflow
    assert "rm -f -- src/assets/distribution-channel.json" in workflow
    assert "trap cleanup EXIT" in workflow
    assert '--distribution-channel "$CHANNEL"' in workflow
    assert "UNSIGNED_DIAGNOSTIC_ONLY.txt" in workflow


# ---------------------------------------------------------------------------
# distribution-channel.json generation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("chplay", {"schema_version": 1, "channel": "chplay", "android_package_id": PACKAGE_ID}),
        ("apk_lts", {"schema_version": 1, "channel": "apk_lts", "android_package_id": PACKAGE_ID}),
        ("exe_lts", {"schema_version": 1, "channel": "exe_lts"}),
    ],
)
def test_distribution_channel_payload_matches_runtime_contract(channel, expected):
    assert build_distribution_channel_payload(channel) == expected


def test_distribution_channel_payload_validates_inputs(tmp_path):
    with pytest.raises(ReleaseMetadataError):
        build_distribution_channel_payload("appstore")
    with pytest.raises(ReleaseMetadataError):
        build_distribution_channel_payload("chplay", android_package_id="bad id!")
    with pytest.raises(ReleaseMetadataError):
        build_distribution_channel_payload("msstore", store_product_id="x/../y")
    out = write_distribution_channel(
        tmp_path / "a" / "distribution-channel.json", "msstore", store_product_id="9NBLGGH4NNS1"
    )
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "channel": "msstore",
        "store_product_id": "9NBLGGH4NNS1",
    }
    assert list(out.parent.glob("*.tmp")) == []


def test_generated_channel_asset_round_trips_through_runtime_loader(tmp_path):
    from core.distribution import DistributionChannel, resolve_distribution_channel

    target = write_distribution_channel(tmp_path / "distribution-channel.json", "chplay")
    profile = resolve_distribution_channel(
        config_path=target, is_windows=False, is_android=True, is_ios=False
    )
    assert profile.channel is DistributionChannel.CHPLAY
    assert profile.supports_github_self_update is False


def test_runtime_config_cli_writes_channel_and_requires_pair(tmp_path):
    script = ROOT / "scripts" / "generate_public_runtime_config.py"
    channel = tmp_path / "distribution-channel.json"
    ok = subprocess.run(
        [
            sys.executable, str(script), "--sentry-dsn", "", "--output",
            str(tmp_path / "diag.json"), "--distribution-channel", "chplay",
            "--distribution-output", str(channel),
        ],
        capture_output=True, text=True, timeout=30, cwd=ROOT,
    )
    assert ok.returncode == 0, ok.stderr
    assert json.loads(channel.read_text(encoding="utf-8"))["channel"] == "chplay"

    missing_output = subprocess.run(
        [
            sys.executable, str(script), "--sentry-dsn", "", "--output",
            str(tmp_path / "diag2.json"), "--distribution-channel", "chplay",
        ],
        capture_output=True, text=True, timeout=30, cwd=ROOT,
    )
    assert missing_output.returncode != 0


# ---------------------------------------------------------------------------
# AAB verifier
# ---------------------------------------------------------------------------


def test_verifier_accepts_compliant_chplay_aab(tmp_path):
    evidence = _verify(_aab(tmp_path / "ok.aab"), tmp_path)

    assert evidence["channel"] == "chplay"
    assert evidence["signature_kind"] == "unsigned-diagnostic"
    assert evidence["signature_valid"] is False
    assert {"elf_16kb_alignment", "play_dex_scan", "target_sdk", "distribution_channel"} <= set(
        evidence["checks"]
    )
    assert json.loads((tmp_path / "evidence.json").read_text(encoding="utf-8")) == evidence


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest(permissions=("android.permission.REQUEST_INSTALL_PACKAGES",)),
        _manifest(permissions=("android.permission.FOREGROUND_SERVICE",)),
        _manifest(permissions=("android.permission.FOREGROUND_SERVICE_DATA_SYNC",)),
        _manifest(permissions=("android.permission.FOREGROUND_SERVICE_LOCATION",)),
        _manifest(permissions=("android.permission.USE_EXACT_ALARM",)),
        _manifest(target_sdk=34),
        _manifest(target_sdk=None),
        _manifest(application_attrs=""),
        _manifest(application_attrs='android:allowBackup="true"'),
        _manifest(application_attrs='android:allowBackup="false" android:usesCleartextTraffic="true"'),
        _manifest(application_attrs='android:allowBackup="false" android:extractNativeLibs="true"'),
        _manifest(
            extra_application='<provider android:name=".update.UthUpdateFileProvider" '
            'android:authorities="com.uthelper.uthelper.uthupdates" />'
        ),
        _manifest(
            extra_application='<service android:name="com.dexterous.flutterlocalnotifications.ForegroundService" '
            'android:exported="false" android:foregroundServiceType="specialUse" />'
        ),
        _manifest(receivers=_RECEIVERS[:-1]),
    ],
)
def test_verifier_rejects_non_compliant_manifests(tmp_path, manifest):
    with pytest.raises(AndroidVerificationError):
        _verify(_aab(tmp_path / "bad.aab", manifest=manifest), tmp_path)


def test_verifier_rejects_missing_class_schedule_receiver(tmp_path):
    receivers = tuple(
        name for name in _RECEIVERS if not name.endswith(".ClassScheduleAlarmReceiver")
    )
    with pytest.raises(AndroidVerificationError, match="receiver wiring is incomplete"):
        _verify(
            _aab(tmp_path / "missing-class-schedule.aab", manifest=_manifest(receivers=receivers)),
            tmp_path,
        )


def test_verifier_ignores_manifest_nodes_marked_for_removal(tmp_path):
    marked = _manifest().replace(
        "<application",
        '<uses-permission xmlns:tools="http://schemas.android.com/tools" '
        'android:name="android.permission.REQUEST_INSTALL_PACKAGES" tools:node="remove" />'
        "<application",
        1,
    ).replace(
        "</application>",
        '<service xmlns:tools="http://schemas.android.com/tools" '
        'android:name="com.dexterous.flutterlocalnotifications.ForegroundService" '
        'tools:node="remove" /></application>',
        1,
    )
    _verify(_aab(tmp_path / "removed.aab", manifest=marked), tmp_path)


def test_verifier_rejects_missing_or_invalid_bundle_config_16kb_uncompressed(tmp_path):
    with pytest.raises(AndroidVerificationError, match="container layout is invalid"):
        _verify(_aab(tmp_path / "no-bundlecfg.aab", include_bundle_config=False), tmp_path)
    with pytest.raises(AndroidVerificationError, match="uncompress_native_libraries"):
        _verify(
            _aab(tmp_path / "disabled.aab", bundle_config=_bundle_config_pb(enabled=0, alignment=2)),
            tmp_path,
        )
    with pytest.raises(AndroidVerificationError, match="page alignment"):
        _verify(
            _aab(tmp_path / "align4k.aab", bundle_config=_bundle_config_pb(enabled=1, alignment=1)),
            tmp_path,
        )


def test_verifier_checks_apk_zip_stored_and_16kb_offset_alignment_for_chplay(
    tmp_path, monkeypatch
):
    from scripts import verify_android_release as var

    sdk_dir = tmp_path / "sdk"
    (sdk_dir / "build-tools" / "35.0.0").mkdir(parents=True)
    (sdk_dir / "cmdline-tools" / "latest" / "bin").mkdir(parents=True)
    for name in ("apksigner.bat", "apksigner"):
        (sdk_dir / "build-tools" / "35.0.0" / name).write_text("shim", encoding="utf-8")
    for name in ("apkanalyzer.bat", "apkanalyzer"):
        (sdk_dir / "cmdline-tools" / "latest" / "bin" / name).write_text("shim", encoding="utf-8")

    def fake_run(command):
        args = [str(part) for part in command]
        if "apksigner" in args[0]:
            return f"Signer #1 certificate SHA-256 digest: {FINGERPRINT.lower()}"
        if args[1:3] == ["manifest", "application-id"]:
            return PACKAGE_ID
        if args[1:3] == ["manifest", "version-name"]:
            return VERSION
        if args[1:3] == ["manifest", "version-code"]:
            return str(BUILD_NUMBER)
        if args[1:3] == ["manifest", "print"]:
            return _manifest()
        raise AssertionError(args)

    monkeypatch.setattr(var, "_run", fake_run)

    # 1. Deflated .so -> rejected
    deflated_apk = tmp_path / f"UTHelper-{VERSION}.apk"
    with zipfile.ZipFile(deflated_apk, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("classes.dex", _dex(("Lcom/uthelper/MainActivity;",)))
        zf.writestr(
            "assets/flutter_assets/app/assets/distribution-channel.json",
            json.dumps({"channel": "chplay", "android_package_id": PACKAGE_ID}),
        )
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libflutter.so", _elf64(0x4000))
    with pytest.raises(AndroidVerificationError, match="ZIP_STORED"):
        verify_android_release(
            apk=deflated_apk,
            channel="chplay",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="1",
            output=tmp_path / "out1.json",
            android_home=sdk_dir,
        )

    # 2. Stored .so without 16 KB ZIP offset alignment -> rejected
    unaligned_apk = tmp_path / f"UTHelper-{VERSION}.apk"
    with zipfile.ZipFile(unaligned_apk, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("classes.dex", _dex(("Lcom/uthelper/MainActivity;",)))
        zf.writestr(
            "assets/flutter_assets/app/assets/distribution-channel.json",
            json.dumps({"channel": "chplay", "android_package_id": PACKAGE_ID}),
        )
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            zf.writestr(f"lib/{abi}/libflutter.so", _elf64(0x4000))
    with pytest.raises(AndroidVerificationError, match="not 16 KB aligned"):
        verify_android_release(
            apk=unaligned_apk,
            channel="chplay",
            version=VERSION,
            build_number=BUILD_NUMBER,
            package_id=PACKAGE_ID,
            certificate_sha256=FINGERPRINT,
            commit_sha=COMMIT_SHA,
            workflow_run_id="1",
            output=tmp_path / "out2.json",
            android_home=sdk_dir,
        )

    # 3. Stored .so with exact 16 KB ZIP local header extra padding -> accepted
    aligned_apk = tmp_path / f"UTHelper-{VERSION}.apk"
    with zipfile.ZipFile(aligned_apk, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("classes.dex", _dex(("Lcom/uthelper/MainActivity;",)))
        zf.writestr(
            "assets/flutter_assets/app/assets/distribution-channel.json",
            json.dumps({"channel": "chplay", "android_package_id": PACKAGE_ID}),
        )
        zf.writestr("lib/armeabi-v7a/libflutter.so", _elf64(0x4000))
        for abi in ("arm64-v8a", "x86_64"):
            arcname = f"lib/{abi}/libflutter.so"
            zf.writestr(arcname, _elf64(0x4000))
    # Rewrite with 16 KB padding on 64-bit .so entries
    with zipfile.ZipFile(aligned_apk, "r") as src:
        entries = [(info, src.read(info.filename)) for info in src.infolist()]
    with zipfile.ZipFile(aligned_apk, "w", zipfile.ZIP_STORED) as dst:
        offset = 0
        for info, data in entries:
            name_bytes = info.filename.encode("utf-8")
            base_end = offset + 30 + len(name_bytes)
            zi = zipfile.ZipInfo(info.filename)
            zi.compress_type = zipfile.ZIP_STORED
            if info.filename.endswith(".so") and any(a in info.filename for a in ("arm64-v8a", "x86_64")):
                pad = (0x4000 - (base_end % 0x4000)) % 0x4000
                zi.extra = b"\x00" * pad
            dst.writestr(zi, data)
            offset = base_end + len(zi.extra) + len(data)
    verify_android_release(
        apk=aligned_apk,
        channel="chplay",
        version=VERSION,
        build_number=BUILD_NUMBER,
        package_id=PACKAGE_ID,
        certificate_sha256=FINGERPRINT,
        commit_sha=COMMIT_SHA,
        workflow_run_id="1",
        output=tmp_path / "out3.json",
        android_home=sdk_dir,
    )


@pytest.mark.parametrize(
    "symbol",
    [
        "REQUEST_INSTALL_PACKAGES",
        "ACTION_MANAGE_UNKNOWN_APP_SOURCES",
        "application/vnd.android.package-archive",
        "Lcom/uthelper/backgroundsync/update/UthUpdateFileProvider;",
        "Lcom/uthelper/backgroundsync/update/ApkUpdateInstaller;",
        "Lcom/uthelper/backgroundsync/update/ApkUpdateDownloadManager;",
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "install_update",
        "cancel_update",
    ],
)
def test_verifier_rejects_updater_symbols_in_primary_and_multidex(tmp_path, symbol):
    with pytest.raises(AndroidVerificationError, match="prohibited updater symbol"):
        _verify(_aab(tmp_path / "primary.aab", dex_strings=("LMain;", symbol)), tmp_path)
    with pytest.raises(AndroidVerificationError, match="classes2.dex"):
        _verify(
            _aab(
                tmp_path / "multidex.aab",
                extra_dex={"base/dex/classes2.dex": _dex((symbol,))},
            ),
            tmp_path,
        )


def test_verifier_rejects_leaked_update_paths_resource(tmp_path):
    aab = _aab(tmp_path / "paths.aab", extra={"base/res/xml/uth_update_paths.xml": b"<paths/>"})
    with pytest.raises(AndroidVerificationError, match="FileProvider paths"):
        _verify(aab, tmp_path)


@pytest.mark.parametrize("align", [0x1000, 0x2000, 0x3000])
def test_verifier_rejects_sub_16kb_or_unaligned_pt_load(tmp_path, align):
    with pytest.raises(AndroidVerificationError, match="p_align"):
        _verify(_aab(tmp_path / "align.aab", align=align), tmp_path)


def test_verifier_accepts_64kb_alignment_and_checks_every_pt_load_segment(tmp_path):
    _verify(_aab(tmp_path / "64k.aab", align=0x10000), tmp_path)
    with pytest.raises(AndroidVerificationError, match="segment #2"):
        _verify(
            _aab(
                tmp_path / "multi.aab",
                extra={"base/lib/arm64-v8a/libmulti.so": _elf64(0x4000, 0x1000)},
            ),
            tmp_path,
        )


def test_verifier_rejects_corrupt_or_32bit_misdeclared_native_libraries(tmp_path):
    with pytest.raises(AndroidVerificationError, match="valid ELF"):
        _verify(
            _aab(tmp_path / "corrupt.aab", extra={"base/lib/arm64-v8a/libbad.so": b"not-elf"}),
            tmp_path,
        )
    elf32 = bytearray(_elf64(0x4000))
    elf32[4] = 1
    with pytest.raises(AndroidVerificationError, match="must be ELF64"):
        _verify(
            _aab(tmp_path / "elf32.aab", extra={"base/lib/x86_64/libx.so": bytes(elf32)}),
            tmp_path,
        )


@pytest.mark.parametrize(
    "channel",
    [None, {"channel": "apk_lts"}, {"channel": "chplay", "android_package_id": "other.pkg"}],
)
def test_verifier_requires_matching_embedded_channel(tmp_path, channel):
    archive = _aab(tmp_path / "chan.aab")
    if channel is None:
        with zipfile.ZipFile(archive) as source:
            members = {
                n: source.read(n) for n in source.namelist() if not n.endswith("distribution-channel.json")
            }
        with zipfile.ZipFile(archive, "w") as target:
            for name, content in members.items():
                target.writestr(name, content)
    else:
        archive = _aab(tmp_path / "chan2.aab", channel=channel)
    with pytest.raises(AndroidVerificationError, match="distribution-channel"):
        _verify(archive, tmp_path)


def test_verifier_reads_channel_from_nested_flet_app_zip(tmp_path):
    for nested_member in (
        "base/assets/app.zip",
        "base/assets/flutter_assets/app/app.zip",
    ):
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as inner:
            inner.writestr("assets/distribution-channel.json", json.dumps({"channel": "chplay"}))
        archive = _aab(tmp_path / f"nested-{nested_member.replace('/', '_')}.aab")
        with zipfile.ZipFile(archive) as source:
            members = {
                n: source.read(n)
                for n in source.namelist()
                if not n.endswith("distribution-channel.json")
            }
        members[nested_member] = nested.getvalue()
        with zipfile.ZipFile(archive, "w") as target:
            for name, content in members.items():
                target.writestr(name, content)
        _verify(archive, tmp_path)


def test_verifier_enforces_artifact_selection_and_version_metadata(tmp_path):
    aab = _aab(tmp_path / "meta.aab")
    with pytest.raises(AndroidVerificationError, match="exactly one"):
        verify_android_release(
            apk=aab, aab=aab, version=VERSION, build_number=BUILD_NUMBER, package_id=PACKAGE_ID,
            commit_sha=COMMIT_SHA, workflow_run_id="1", output=tmp_path / "o.json", allow_unsigned=True,
        )
    with pytest.raises(AndroidVerificationError, match="canonical"):
        _verify(aab, tmp_path, build_number=BUILD_NUMBER + 1)
    with pytest.raises(AndroidVerificationError, match="versionName"):
        _verify(aab, tmp_path, version="2.5.2", build_number=2_005_002)
    with pytest.raises(AndroidVerificationError, match="requires an APK"):
        _verify(aab, tmp_path, channel="apk_lts")
    with pytest.raises(AndroidVerificationError, match="fingerprint"):
        _verify(aab, tmp_path, allow_unsigned=False, certificate_sha256="")


def test_verifier_rejects_non_zip_and_non_aab_inputs(tmp_path):
    bad = tmp_path / "bad.aab"
    bad.write_bytes(b"MZnot-a-bundle")
    with pytest.raises(AndroidVerificationError, match="ZIP magic"):
        _verify(bad, tmp_path)
    with pytest.raises(AndroidVerificationError, match="path is invalid"):
        _verify(_aab(tmp_path / "x.zip"), tmp_path)


def test_unsigned_diagnostics_are_chplay_only(tmp_path):
    apk = tmp_path / f"UTHelper-{VERSION}.apk"
    apk.write_bytes(b"PK\x03\x04")
    with pytest.raises(AndroidVerificationError, match="only supported for chplay"):
        verify_android_release(
            apk=apk, version=VERSION, build_number=BUILD_NUMBER, package_id=PACKAGE_ID,
            commit_sha=COMMIT_SHA, workflow_run_id="1", output=tmp_path / "o.json",
            allow_unsigned=True,
        )


def test_verifier_cli_exposes_aab_channel_and_diagnostic_flags(tmp_path):
    script = ROOT / "scripts" / "verify_android_release.py"
    help_text = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, timeout=30, cwd=ROOT
    ).stdout
    for flag in ("--aab", "--apk", "--channel", "--allow-unsigned", "--certificate-sha256"):
        assert flag in help_text
    aab = _aab(tmp_path / "cli.aab")
    env = {k: v for k, v in os.environ.items() if k not in {"ANDROID_HOME", "ANDROID_SDK_ROOT"}}
    done = subprocess.run(
        [
            sys.executable, str(script), "--aab", str(aab), "--channel", "chplay",
            "--allow-unsigned", "--version", VERSION, "--build-number", str(BUILD_NUMBER),
            "--package-id", PACKAGE_ID, "--commit-sha", COMMIT_SHA, "--workflow-run-id", "9",
            "--output", str(tmp_path / "cli.json"),
        ],
        capture_output=True, text=True, timeout=30, cwd=ROOT, env=env,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads((tmp_path / "cli.json").read_text(encoding="utf-8"))["channel"] == "chplay"


def test_prepare_android_flutter_project_strips_chplay_and_sets_sdk36_idempotently(tmp_path):
    from scripts.prepare_android_flutter_project import prepare_android_flutter_project

    flutter_root = tmp_path / "flutter"
    app_dir = flutter_root / "android" / "app"
    manifest_dir = app_dir / "src" / "main"
    manifest_dir.mkdir(parents=True)

    gradle_kts = app_dir / "build.gradle.kts"
    gradle_kts.write_text(
        "android {\n"
        "    compileSdk = 34\n"
        "    packaging { jniLibs {\n"
        "        useLegacyPackaging = true\n"
        "    } }\n"
        "    defaultConfig { targetSdk = 34 }\n"
        "}\n",
        encoding="utf-8",
    )
    manifest_xml = manifest_dir / "AndroidManifest.xml"
    manifest_xml.write_text(
        '<manifest xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <uses-permission android:name="android.permission.INTERNET" />\n'
        '    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />\n'
        '    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />\n'
        '    <application android:allowBackup="true" android:usesCleartextTraffic="true">\n'
        '        <service android:name="com.dexterous.flutterlocalnotifications.ForegroundService"\n'
        '            android:exported="false" android:foregroundServiceType="specialUse" />\n'
        '    </application>\n'
        '</manifest>\n',
        encoding="utf-8",
    )

    prepare_android_flutter_project(flutter_root, channel="chplay", target_sdk=36)
    prepare_android_flutter_project(flutter_root, channel="chplay", target_sdk=36)

    gradle_text = gradle_kts.read_text(encoding="utf-8")
    assert "compileSdk = 36" in gradle_text
    assert "targetSdk = 36" in gradle_text
    assert "useLegacyPackaging = false" in gradle_text
    assert "useLegacyPackaging = true" not in gradle_text

    props = (flutter_root / "android" / "gradle.properties").read_text(encoding="utf-8")
    assert "uthDistributionChannel=chplay" in props
    assert "kotlin.compiler.execution.strategy=in-process" in props

    manifest_text = manifest_xml.read_text(encoding="utf-8")
    assert manifest_text.count('tools:node="remove"') == 6
    assert 'android:allowBackup="false"' in manifest_text
    assert 'android:usesCleartextTraffic="false"' in manifest_text
    assert 'android:extractNativeLibs="false"' in manifest_text
    assert 'foregroundServiceType="specialUse"' not in manifest_text
    assert (
        '<service android:name="com.dexterous.flutterlocalnotifications.ForegroundService" '
        'tools:node="remove" />'
    ) in manifest_text
    assert (
        '<service android:name="androidx.work.impl.foreground.SystemForegroundService" '
        'tools:node="remove" />'
    ) in manifest_text


# ---------------------------------------------------------------------------
# Low-level decoders
# ---------------------------------------------------------------------------


def test_extract_dex_strings_and_rejects_bad_headers():
    assert extract_dex_strings(_dex(("a", "Lfoo/Bar;"))) == ["a", "Lfoo/Bar;"]
    assert extract_dex_strings(_dex(())) == []
    with pytest.raises(AndroidVerificationError):
        extract_dex_strings(b"not a dex")


def test_elf_alignment_helper_checks_class_and_header_bounds():
    verify_elf_16kb_alignment(_elf64(0x4000), library_name="a.so", abi="arm64-v8a")
    with pytest.raises(AndroidVerificationError):
        verify_elf_16kb_alignment(_elf64(0x1000), library_name="a.so", abi="arm64-v8a")
    with pytest.raises(AndroidVerificationError):
        verify_elf_16kb_alignment(_elf64(0x4000)[:70], library_name="a.so", abi="arm64-v8a")


def _pb_varint(value: int) -> bytes:
    return _uleb(value)


def _pb_field(number: int, payload: bytes) -> bytes:
    return _pb_varint((number << 3) | 2) + _pb_varint(len(payload)) + payload


def _pb_attr(namespace: str, name: str, value: str) -> bytes:
    return _pb_field(
        4,
        _pb_field(1, namespace.encode()) + _pb_field(2, name.encode()) + _pb_field(3, value.encode()),
    )


def _pb_element(name: str, attrs: bytes = b"", children: bytes = b"") -> bytes:
    return _pb_field(1, _pb_field(3, name.encode()) + attrs + children)


def test_decode_aab_manifest_handles_protobuf_and_plain_xml():
    ns = "http://schemas.android.com/apk/res/android"
    child = _pb_field(5, _pb_element("uses-sdk", _pb_attr(ns, "targetSdkVersion", "35")))
    node = _pb_element("manifest", _pb_attr("", "package", PACKAGE_ID), child)
    root = decode_aab_manifest_bytes(node)
    assert root.attrib["package"] == PACKAGE_ID
    assert root[0].attrib[f"{{{ns}}}targetSdkVersion"] == "35"
    assert decode_aab_manifest_bytes(_manifest().encode()).tag.endswith("manifest")
    with pytest.raises(AndroidVerificationError):
        decode_aab_manifest_bytes(b"\x0a\xff\xff")


def test_verifier_requires_office_viewer_assets_when_app_zip_present(tmp_path):
    sdk_dir = tmp_path / "sdk"
    (sdk_dir / "build-tools" / "35.0.0").mkdir(parents=True)
    app_zip = io.BytesIO()
    with zipfile.ZipFile(app_zip, "w") as inner:
        inner.writestr("assets/release-version", VERSION)
        inner.writestr("assets/distribution-channel.json", json.dumps({"channel": "chplay"}))
    bad_aab = _aab(tmp_path / "missing-office.aab")
    with zipfile.ZipFile(bad_aab, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("base/assets/flutter_assets/app/app.zip", app_zip.getvalue())
    with pytest.raises(AndroidVerificationError, match="office_viewer"):
        _verify(
            bad_aab,
            tmp_path,
            output=tmp_path / "out-missing-office.json",
            android_home=sdk_dir,
            require_office_viewer=True,
        )


def test_background_sync_gradle_and_android_workflow_guard_against_maven_central_429():
    mirror_url = "https://maven-central.storage-download.googleapis.com/maven2/"
    settings_gradle = _read(
        "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/settings.gradle"
    )
    build_gradle = _read(
        "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/build.gradle"
    )
    workflow = _read(".github/workflows/build-android.yml")

    assert settings_gradle.count(mirror_url) >= 2
    assert settings_gradle.index(mirror_url) < settings_gradle.index("mavenCentral()")
    assert mirror_url in build_gradle
    assert build_gradle.index(mirror_url) < build_gradle.index("mavenCentral()")
    assert "-Dorg.gradle.internal.http.retry.max.attempts=" in workflow
    assert workflow.count("run_gradle_with_retry") >= 2
    assert "~/.gradle/init.d" not in workflow
