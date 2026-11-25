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
        / "variants/apk_lts/kotlin/com/uthelper/backgroundsync/update/UthUpdateFileProvider.kt"
    ).read_text(encoding="utf-8")
    alarms = (
        ANDROID
        / "src/main/kotlin/com/uthelper/backgroundsync/notification/AlarmScheduler.kt"
    ).read_text(encoding="utf-8")

    assert "class UthUpdateFileProvider : FileProvider()" in provider
    assert "Build.VERSION.SDK_INT < Build.VERSION_CODES.S" in alarms
    assert "setExactAndAllowWhileIdle" in alarms
    assert not (ANDROID / "src/main/kotlin/com/uthelper/backgroundsync/update").exists()
    assert not (ANDROID / "src/main/res/xml/uth_update_paths.xml").exists()
    assert not (ANDROID / "src/test/kotlin/com/uthelper/backgroundsync/update").exists()
    assert (ANDROID / "variants/apk_lts/res/xml/uth_update_paths.xml").is_file()
    assert list(
        (ANDROID / "variants/apk_lts/test/kotlin/com/uthelper/backgroundsync/update").glob("*Test.kt")
    )


def test_token_vault_and_app_secret_vault_use_keystore_and_strict_https():
    vault = next(ANDROID.rglob("CredentialVault.kt")).read_text(encoding="utf-8")
    trusted_uri = next(ANDROID.rglob("TrustedMoodleUri.kt")).read_text(encoding="utf-8")
    ws_client = next(ANDROID.rglob("MoodleWsClient.kt")).read_text(encoding="utf-8")
    dispatcher = next(ANDROID.rglob("NotificationDispatcher.kt")).read_text(encoding="utf-8")
    plugin = next(ANDROID.rglob("UthBackgroundSyncPlugin.kt")).read_text(encoding="utf-8")

    assert '"AndroidKeyStore"' in vault
    assert '"AES/GCM/NoPadding"' in vault
    assert "noBackupFilesDir" in vault
    assert "SharedPreferences" not in vault
    assert "class AppSecretVault" in vault
    assert "uthelper_app_secrets_v1" in vault
    assert "uth_app_secrets.json" in vault
    assert "http://" not in vault
    assert "normalizeTrustedOrigin" in vault
    assert "validateActivityUri" in trusted_uri
    assert "instanceFollowRedirects = false" in ws_client
    assert "validateActivityUri" in dispatcher
    assert "getLaunchIntentForPackage" in dispatcher
    assert '"read_app_secrets"' in plugin
    assert '"write_app_secrets"' in plugin
    assert '"clear_app_secrets"' in plugin
    logout_block = plugin.split('"logout" ->', 1)[1].split("else ->", 1)[0]
    assert "CredentialVault(context).clear()" in logout_block
    assert "AppSecretVault" not in logout_block


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


def test_android_updater_requires_local_checksum_and_package_installer():
    updater = next(ANDROID.rglob("ApkUpdateInstaller.kt")).read_text(encoding="utf-8")
    assert "copyAndVerifyLocalPackage" in updater
    assert "HttpURLConnection" not in updater
    assert "Update SHA-256 mismatch" in updater
    assert "canRequestPackageInstalls" in updater
    assert "application/vnd.android.package-archive" in updater
    assert "validateArchiveMetadata" in updater
    assert "expectedPackageId" in updater
    assert "expectedVersionCode" in updater
    assert "expectedCertificateSha256" in updater
    assert "AtomicBoolean" in updater
    assert '"stage" to update.stage' in next(
        ANDROID.rglob("ApkLtsUpdateBridgeDelegate.kt")
    ).read_text(encoding="utf-8")
    assert "REQUEST_INSTALL_PACKAGES" in (
        ANDROID / "src/main/AndroidManifest.xml"
    ).read_text(encoding="utf-8")


def test_updater_method_channel_lives_only_in_apk_lts_delegate():
    plugin = next(ANDROID.rglob("UthBackgroundSyncPlugin.kt")).read_text(encoding="utf-8")
    delegate = next(ANDROID.rglob("ApkLtsUpdateBridgeDelegate.kt")).read_text(
        encoding="utf-8"
    )
    chplay_factory = (
        ANDROID / "variants/chplay/kotlin/com/uthelper/backgroundsync/UpdateBridgeFactory.kt"
    ).read_text(encoding="utf-8")
    lts_factory = (
        ANDROID / "variants/apk_lts/kotlin/com/uthelper/backgroundsync/UpdateBridgeFactory.kt"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "ApkUpdateInstaller",
        "ApkUpdateDownloadManager",
        "install_update",
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "cancel_update",
    ):
        assert forbidden not in plugin
        assert forbidden not in chplay_factory
    for method in (
        "install_update",
        "start_update_download",
        "get_update_download_status",
        "cancel_update_download",
        "cancel_update",
    ):
        assert f'"{method}"' in delegate
    assert "ApkLtsUpdateBridgeDelegate(context)" in lts_factory
    assert "NoOpUpdateBridgeDelegate" in chplay_factory
    assert "ApkLtsUpdateBridgeDelegate" not in chplay_factory


def test_chplay_manifest_variant_strips_updater_and_foreground_permissions():
    manifest = (ANDROID / "variants/chplay/AndroidManifest.xml").read_text(
        encoding="utf-8"
    )
    main = (ANDROID / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    for token in (
        "REQUEST_INSTALL_PACKAGES",
        "FOREGROUND_SERVICE",
        "FOREGROUND_SERVICE_DATA_SYNC",
        "USE_EXACT_ALARM",
        "UthUpdateFileProvider",
    ):
        assert token in manifest
    assert manifest.count('tools:node="remove"') == 5
    assert "uses-permission android:name=\"android.permission.USE_EXACT_ALARM\" />" not in manifest
    assert 'android:allowBackup="false"' in manifest
    assert 'android:usesCleartextTraffic="false"' in manifest
    assert 'android:allowBackup="false"' in main
    assert 'android:usesCleartextTraffic="false"' in main
    assert "USE_EXACT_ALARM" not in main
    assert "FOREGROUND_SERVICE" not in main
    for receiver in (
        "DeadlineAlarmReceiver", "RescheduleReceiver", "ClassScheduleAlarmReceiver"
    ):
        assert receiver in manifest


def test_gradle_selects_chplay_sources_and_defaults_to_apk_lts():
    gradle = (ANDROID / "build.gradle").read_text(encoding="utf-8")
    assert "uthDistributionChannel" in gradle
    assert "UTH_DISTRIBUTION_CHANNEL" in gradle
    assert "?: 'apk_lts'" in gradle
    assert "variants/chplay/AndroidManifest.xml" in gradle
    assert "variants/chplay/kotlin" in gradle
    assert "variants/apk_lts/kotlin" in gradle
    assert "variants/apk_lts/res" in gradle
    assert "variants/apk_lts/test/kotlin" in gradle
    assert "inputs.property('uthDistributionChannel', uthDistributionChannel)" in gradle
    assert "kotlin.exclude" not in gradle
    assert "useLegacyPackaging = false" in gradle


def test_android_download_uses_pinned_durable_download_manager_request():
    downloader = next(ANDROID.rglob("ApkUpdateDownloadManager.kt")).read_text(
        encoding="utf-8"
    )
    manifest = (ANDROID / "src/main/AndroidManifest.xml").read_text(encoding="utf-8")

    assert "DownloadManager.Request" in downloader
    assert '"pending"' in downloader
    assert '"running"' in downloader
    assert '"paused"' in downloader
    assert '"completed"' in downloader
    assert '"failed"' in downloader
    assert '"missing"' in downloader
    assert "setDestinationInExternalFilesDir" in downloader
    assert "VISIBILITY_VISIBLE)" in downloader
    assert "VISIBILITY_VISIBLE_NOTIFY_COMPLETED" not in downloader
    assert '"Đang tải bản cập nhật"' in downloader
    assert 'uri.host?.equals("github.com", ignoreCase = true)' in downloader
    assert "REQUEST_INSTALL_PACKAGES" in manifest
    assert "getExternalFilesDir" in downloader


def test_python_service_forwards_configuration(monkeypatch):
    from flet_uth_background_sync import AndroidBackgroundSync

    service = AndroidBackgroundSync()
    captured = {}

    async def invoke(name, arguments=None, timeout=None):
        captured.update(name=name, arguments=arguments)
        if timeout is not None:
            captured["timeout"] = timeout
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
            "/data/user/0/com.uthelper.uthelper/cache/UTHelper-2.2.0.apk",
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
            "local_path": (
                "/data/user/0/com.uthelper.uthelper/cache/UTHelper-2.2.0.apk"
            ),
            "sha256": "ab" * 32,
            "expected_size": 123,
            "expected_package_id": "com.uthelper.uthelper",
            "expected_version_code": 2_002_000,
            "expected_certificate_sha256": "cd" * 32,
        },
        "timeout": 190,
    }

    asyncio.run(service.cancel_update())
    assert captured["name"] == "cancel_update"
    assert captured["arguments"] is None


def test_python_service_forwards_android_update_download_identity_and_deadlines(monkeypatch):
    from flet_uth_background_sync import AndroidBackgroundSync

    service = AndroidBackgroundSync()
    captured = {}

    async def invoke(name, arguments=None, timeout=None):
        captured.update(name=name, arguments=arguments, timeout=timeout)
        return {"status": "pending"}

    monkeypatch.setattr(service, "_invoke_method", invoke)
    identity = (
        "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.apk",
        "ab" * 32,
        123,
        "com.uthelper.uthelper",
        2_005_001,
        "cd" * 32,
    )
    asyncio.run(service.start_update_download(*identity))
    assert captured == {
        "name": "start_update_download",
        "arguments": {
            "url": identity[0],
            "sha256": identity[1],
            "expected_size": identity[2],
            "expected_package_id": identity[3],
            "expected_version_code": identity[4],
            "expected_certificate_sha256": identity[5],
        },
        "timeout": 30,
    }

    asyncio.run(service.get_update_download_status(*identity))
    assert captured["name"] == "get_update_download_status"
    assert captured["timeout"] == 30

    asyncio.run(service.cancel_update_download(*identity))
    assert captured["name"] == "cancel_update_download"
    assert captured["timeout"] == 30


def test_python_service_forwards_app_secret_vault_rpcs(monkeypatch):
    from flet_uth_background_sync import AndroidBackgroundSync

    service = AndroidBackgroundSync()
    calls = []

    async def invoke(name, arguments=None, timeout=None):
        calls.append((name, arguments, timeout))
        if name == "read_app_secrets":
            return {"UTH_PASSWORD": "pw", "BLANK": None}
        return True

    monkeypatch.setattr(service, "_invoke_method", invoke)

    secrets = asyncio.run(service.read_app_secrets())
    assert secrets == {"UTH_PASSWORD": "pw"}
    assert asyncio.run(service.write_app_secrets({"UTH_PASSWORD": "pw"})) is True
    assert asyncio.run(service.clear_app_secrets()) is True
    assert calls == [
        ("read_app_secrets", None, 5),
        ("write_app_secrets", {"secrets": {"UTH_PASSWORD": "pw"}}, 5),
        ("clear_app_secrets", None, 5),
    ]


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


def test_ios_plugin_implements_keychain_app_secret_vault():
    swift = (IOS / "Classes/UthBackgroundSyncPlugin.swift").read_text(
        encoding="utf-8"
    )
    assert "import Security" in swift
    for method in ('case "read_app_secrets":', 'case "write_app_secrets":', 'case "clear_app_secrets":'):
        assert method in swift
    assert "kSecClassGenericPassword" in swift
    assert "kSecAttrAccessibleWhenUnlockedThisDeviceOnly" in swift
    assert "SecItemCopyMatching" in swift
    assert "SecItemUpdate" in swift
    assert "SecItemAdd" in swift
    assert "SecItemDelete" in swift
    assert "errSecItemNotFound" in swift
    assert "FlutterError(" in swift


def test_android_vault_uses_process_wide_locks_unique_temp_files_and_io_error_safety():
    vault = next(ANDROID.rglob("CredentialVault.kt")).read_text(encoding="utf-8")
    assert "@Synchronized" not in vault
    assert vault.count("private val VAULT_LOCK = Any()") == 2
    assert "synchronized(VAULT_LOCK)" in vault
    assert "UUID.randomUUID()" in vault
    assert "stream.fd.sync()" in vault
    assert "catch (ioError: IOException)" in vault
    assert "throw ioError" in vault
    assert "catch (_: GeneralSecurityException)" in vault


def test_android_receivers_alarms_and_worker_handle_security_exceptions_and_logout_races():
    deadline_rx = next(ANDROID.rglob("DeadlineAlarmReceiver.kt")).read_text(encoding="utf-8")
    reschedule_rx = next(ANDROID.rglob("RescheduleReceiver.kt")).read_text(encoding="utf-8")
    class_rx = next(ANDROID.rglob("ClassScheduleAlarmReceiver.kt")).read_text(encoding="utf-8")
    alarm_sched = next(ANDROID.rglob("AlarmScheduler.kt")).read_text(encoding="utf-8")
    class_sched = next(ANDROID.rglob("ClassScheduleAlarmScheduler.kt")).read_text(encoding="utf-8")
    worker = next(ANDROID.rglob("MoodleSyncWorker.kt")).read_text(encoding="utf-8")
    plugin = next(ANDROID.rglob("UthBackgroundSyncPlugin.kt")).read_text(encoding="utf-8")

    for rx in (deadline_rx, reschedule_rx, class_rx):
        assert "goAsync()" in rx
        assert "CoroutineExceptionHandler" in rx
        assert "Dispatchers.IO" in rx
        assert "pending.finish()" in rx
        assert "catch (" in rx

    for sched in (alarm_sched, class_sched):
        assert "catch (_: SecurityException)" in sched
        assert "setAndAllowWhileIdle" in sched

    assert "internal val syncMutex = Mutex()" in worker
    assert "activeCredential != credential" in worker
    assert "catch (cancelled: CancellationException)" in worker
    assert "MoodleSyncWorker.syncMutex.withLock" in plugin
    logout_block = plugin.split('"logout" ->', 1)[1].split("else ->", 1)[0]
    import_block = plugin.split('"import_activities" ->', 1)[1].split('"get_diagnostics" ->', 1)[0]
    assert "MoodleSyncWorker.syncMutex.withLock" in logout_block
    assert "MoodleSyncWorker.syncMutex.withLock" in import_block


def test_sec06_ios_keychain_self_heals_corrupt_payload_without_wiping_os_errors():
    swift = (IOS / "Classes/UthBackgroundSyncPlugin.swift").read_text(encoding="utf-8")
    read_block = swift.split("private func readKeychainSecrets()", 1)[1].split(
        "private func writeKeychainSecrets(", 1
    )[0]
    # Genuine Keychain OS errors (e.g. errSecInteractionNotAllowed when locked) must still fail closed.
    os_error_branch = read_block.split("guard status == errSecSuccess else {", 1)[1].split(
        "guard let data = item as? Data", 1
    )[0]
    assert "return .failure(" in os_error_branch
    assert "SecItemDelete" not in os_error_branch

    # Corrupt/unreadable payload after errSecSuccess must self-heal via SecItemDelete + empty success
    # (and must NOT call clearKeychainSecrets() which would re-enter non-reentrant NSLock).
    decode_branch = read_block.split("guard let data = item as? Data", 1)[1].split(
        "var secrets: [String: String] = [:]", 1
    )[0]
    assert "SecItemDelete(baseKeychainQuery() as CFDictionary)" in decode_branch
    assert "clearKeychainSecrets()" not in decode_branch
    assert "return .success([:])" in decode_branch


def test_sec09_android_plugin_serializes_lifecycle_mutators_under_sync_mutex():
    plugin = next(ANDROID.rglob("UthBackgroundSyncPlugin.kt")).read_text(encoding="utf-8")
    configure_block = plugin.split('"configure" ->', 1)[1].split('"set_credentials" ->', 1)[0]
    set_creds_block = plugin.split('"set_credentials" ->', 1)[1].split('"read_app_secrets" ->', 1)[0]
    reconcile_block = plugin.split('"reconcile_cached" ->', 1)[1].split(
        '"request_exact_alarm_access" ->', 1
    )[0]
    assert "MoodleSyncWorker.syncMutex.withLock" in configure_block
    assert "MoodleSyncWorker.syncMutex.withLock" in set_creds_block
    assert "MoodleSyncWorker.syncMutex.withLock" in reconcile_block

