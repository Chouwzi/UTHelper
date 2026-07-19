import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FLUTTER = ROOT / "flutter" / "flet_uth_background_sync"
ANDROID = FLUTTER / "android"


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
    assert "androidx.core.content.FileProvider" in manifest
    assert "@xml/uth_update_paths" in manifest


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
    assert 'sha256("$key|$deadline")' in importer
    assert 'Regex("/mod/([^/]+)/")' in importer
    assert "DateTimeFormatter.ISO_LOCAL_DATE_TIME" in importer
    assert 'activity.submissionState.lowercase() == "unknown"' in policy


def test_android_updater_requires_https_checksum_and_package_installer():
    updater = next(ANDROID.rglob("ApkUpdateInstaller.kt")).read_text(encoding="utf-8")
    assert 'url.startsWith("https://")' in updater
    assert "Update SHA-256 mismatch" in updater
    assert "canRequestPackageInstalls" in updater
    assert "application/vnd.android.package-archive" in updater


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
