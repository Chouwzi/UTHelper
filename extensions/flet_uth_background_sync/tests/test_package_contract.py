import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLUTTER = ROOT / "flutter" / "flet_uth_background_sync"
ANDROID = FLUTTER / "android"
IOS = FLUTTER / "ios"


def test_native_dependency_versions_are_pinned():
    gradle = (ANDROID / "build.gradle").read_text(encoding="utf-8")
    assert "androidx.work:work-runtime-ktx:2.11.2" in gradle
    assert "androidx.room:room-runtime:2.8.4" in gradle
    assert "androidx.room:room-compiler:2.8.4" in gradle


def test_manifest_registers_alarm_lifecycle_receivers():
    manifest = (ANDROID / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "DeadlineAlarmReceiver" in manifest
    assert "RescheduleReceiver" in manifest
    assert "BOOT_COMPLETED" in manifest
    assert "TIMEZONE_CHANGED" in manifest
    assert "SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED" in manifest
    assert ".update.UthUpdateFileProvider" in manifest
    assert "@xml/uth_update_paths" in manifest


def test_update_provider_is_unique_and_pre_s_alarms_remain_exact():
    provider = (
        ANDROID
        / "src/main/kotlin/com/uthelper/backgroundsync/update/UthUpdateFileProvider.kt"
    ).read_text(encoding="utf-8")
    alarms = (
        ANDROID
        / "src/main/kotlin/com/uthelper/backgroundsync/notification/AlarmScheduler.kt"
    ).read_text(encoding="utf-8")

    assert "class UthUpdateFileProvider : FileProvider()" in provider
    assert "Build.VERSION.SDK_INT < Build.VERSION_CODES.S" in alarms
    assert "setExactAndAllowWhileIdle" in alarms


def test_token_vault_uses_keystore_and_no_backup_storage():
    vault = next(ANDROID.rglob("CredentialVault.kt")).read_text(encoding="utf-8")
    assert '"AndroidKeyStore"' in vault
    assert '"AES/GCM/NoPadding"' in vault
    assert "noBackupFilesDir" in vault
    assert "SharedPreferences" not in vault


def test_foreground_import_is_revision_and_submission_safe():
    importer = next(ANDROID.rglob("ForegroundActivityImporter.kt")).read_text(
        encoding="utf-8"
    )
    policy = next(ANDROID.rglob("NotificationPolicy.kt")).read_text(encoding="utf-8")
    assert 'incomingState == "unknown" && old != null' in importer
    assert 'sha256("$incomingRevision|$key|$deadline")' in importer
    assert 'Regex("/mod/([^/]+)/")' in importer
    assert "DateTimeFormatter.ISO_LOCAL_DATE_TIME" in importer
    assert 'activity.submissionState.lowercase() == "unknown"' not in policy


def test_android_updater_requires_https_checksum_and_package_installer():
    updater = next(ANDROID.rglob("ApkUpdateInstaller.kt")).read_text(encoding="utf-8")
    assert 'initialUri.scheme == "https"' in updater
    assert "Update SHA-256 mismatch" in updater
    assert "canRequestPackageInstalls" in updater
    assert "application/vnd.android.package-archive" in updater
    assert "validateArchiveMetadata" in updater
    assert "expectedPackageId" in updater
    assert "expectedVersionCode" in updater
    assert "expectedCertificateSha256" in updater
    assert "AtomicBoolean" in updater


def test_python_service_forwards_configuration(monkeypatch):
    from flet_uth_background_sync import AndroidBackgroundSync

    service = AndroidBackgroundSync()
    captured = {}

    async def invoke(name, arguments=None):
        captured.update(name=name, arguments=arguments)
        return {"enabled": True}

    monkeypatch.setattr(service, "_invoke_method", invoke)
    result = asyncio.run(
        service.configure({"enabled": True, "interval_minutes": 60})
    )
    assert result == {"enabled": True}
    assert captured == {
        "name": "configure",
        "arguments": {"settings": {"enabled": True, "interval_minutes": 60}},
    }

    imported = asyncio.run(
        service.import_activities([{"id": "quiz-1", "deadline_epoch": 99}])
    )
    assert imported == {"enabled": True}
    assert captured["name"] == "import_activities"
    assert captured["arguments"]["authoritative"] is True

    updated = asyncio.run(
        service.install_update(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            "v2.2.0/UTHelper-2.2.0.apk",
            "ab" * 32,
            123,
            "com.uthelper.uthelper",
            2_002_000,
            "cd" * 32,
        )
    )
    assert updated == {"enabled": True}
    assert captured == {
        "name": "install_update",
        "arguments": {
            "url": (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/UTHelper-2.2.0.apk"
            ),
            "sha256": "ab" * 32,
            "expected_size": 123,
            "expected_package_id": "com.uthelper.uthelper",
            "expected_version_code": 2_002_000,
            "expected_certificate_sha256": "cd" * 32,
        },
    }

    asyncio.run(service.cancel_update())
    assert captured["name"] == "cancel_update"
    assert captured["arguments"] is None


def test_flutter_package_registers_ios_user_notifications_adapter():
    pubspec = (FLUTTER / "pubspec.yaml").read_text(encoding="utf-8")
    swift = (IOS / "Classes/UthBackgroundSyncPlugin.swift").read_text(
        encoding="utf-8"
    )
    podspec = (IOS / "flet_uth_background_sync.podspec").read_text(
        encoding="utf-8"
    )

    assert "ios:" in pubspec
    assert "pluginClass: UthBackgroundSyncPlugin" in pubspec
    assert "UNUserNotificationCenter" in swift
    assert 'case "import_activities"' in swift
    assert "seenScheduleKeys" in swift
    assert "s.source_files = 'Classes/**/*.swift'" in podspec
