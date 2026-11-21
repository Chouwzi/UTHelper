"""Tier 1 opaque-box E2E tests for Features F5-F8:
- F5: Mobile LTS .apk.part -> .apk rename fix & Kotlin test unmasking
- F6: Mobile LTS bridge error propagation, null-candidate safety & cached fallback
- F7: PC LTS Windows launcher UAC/lock race & post-upgrade relaunch
- F8: PC LTS verifier timeout, Windows error mappings, Debug UI sync & shutdown

All M1-M4 target module imports are deferred via ``harness.load_module`` so
``pytest --collect-only`` remains parallel-safe.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any
import xml.etree.ElementTree as ET

import pytest

from tests.e2e_store_distribution.harness import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DEFAULT_MSI_UPGRADE_CODE,
    DEFAULT_SIGNER_SUBJECT,
    DEFAULT_TRUSTED_SIGNER_SHA256,
    FakePage,
    LocalReleaseServer,
    SimulatedAndroidBridge,
    WORKTREE_ROOT,
    WindowsShimEnv,
    load_module,
    make_minimal_apk_bytes,
    sha256_hex,
)


# ---------------------------------------------------------------------------
# Local Opaque-Box Helpers
# ---------------------------------------------------------------------------


def _wait_until(predicate, *, timeout: float = 8.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _build_minimal_controller(
    monkeypatch: pytest.MonkeyPatch,
    fake_page: FakePage,
    *,
    is_windows: bool,
    is_android: bool,
    android_bridge: Any = None,
) -> Any:
    """Create a real ``AppController`` configured for LTS auto-update tests."""
    if not hasattr(fake_page, "controls"):
        fake_page.controls = []  # type: ignore[attr-defined]
    if not hasattr(fake_page, "add"):
        fake_page.add = lambda *ctrls: fake_page.controls.extend(ctrls)  # type: ignore[attr-defined]
    if not fake_page.views:
        fake_page.views.append(SimpleNamespace(can_pop=True, on_confirm_pop=None))

    app_controller_mod = load_module("gui.app_controller")
    platform_utils = load_module("platform_utils")
    update_packages = load_module("platform_utils.update_packages")
    autostart_mod = load_module("platform_utils.autostart")
    settings_view_mod = load_module("gui.components.settings_view")
    tray_mod = load_module("gui.tray")

    is_mobile = bool(is_android)
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", is_android)
    monkeypatch.setattr(platform_utils, "IS_IOS", False)
    monkeypatch.setattr(platform_utils, "IS_MOBILE", is_mobile)
    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", is_mobile)
    monkeypatch.setattr(
        app_controller_mod,
        "detect_platform",
        lambda _page=None: "android" if is_android else "windows",
    )
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", is_android)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(settings_view_mod._pu, "IS_MOBILE", is_mobile)
    monkeypatch.setattr(settings_view_mod._pu, "UI_IS_MOBILE", is_mobile)
    monkeypatch.setattr(
        autostart_mod,
        "create_autostart_service",
        lambda: SimpleNamespace(
            status=lambda: SimpleNamespace(
                enabled=False, editable=False, supported=False, message=""
            ),
            apply_initial_default=lambda: None,
        ),
    )
    monkeypatch.setattr(tray_mod.TrayApp, "setup", lambda _self: False)

    if is_android:
        bg_mod = load_module("platform_utils.background_sync")
        monkeypatch.setattr(
            bg_mod,
            "create_mobile_background_bridge",
            lambda _page: android_bridge,
        )

    async def _noop_async(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        app_controller_mod.AppController, "_show_login_dialog", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_load_data_async", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_perform_data_sync", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController, "_initialize_android_background", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.TodayScheduleCoordinator, "run", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.ActivitySyncCoordinator, "run", _noop_async
    )
    monkeypatch.setattr(
        app_controller_mod.AppController,
        "_apply_initial_windows_autostart_default",
        _noop_async,
    )

    return app_controller_mod.AppController(fake_page)


def _android_candidate(
    local_server: LocalReleaseServer,
    apk_bytes: bytes,
    *,
    version: str = "2.6.0",
    architecture: str = "universal",
) -> Any:
    update_manifest = load_module("core.update_manifest")
    pkg_doc = local_server.register_package(
        tag_name=f"v{version}",
        filename=f"UTHelper-{version}.apk",
        data=apk_bytes,
        platform="android",
        architecture=architecture,
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    manifest_doc = local_server.build_manifest(
        release_version=version,
        packages=[pkg_doc],
    )
    manifest = update_manifest.parse_manifest(
        manifest_doc,
        expected_release_version=version,
    )
    return update_manifest.select_candidate(
        manifest,
        current_version="2.5.1",
        target=load_module("core.update_models").RuntimeTarget(
            "android", architecture, "sideload"
        ),
    )


def _windows_candidate(
    pkg_doc: dict[str, Any],
    *,
    version: str = "2.6.0",
    install_channel: str = "msi",
) -> Any:
    update_manifest = load_module("core.update_manifest")
    update_models = load_module("core.update_models")
    manifest_doc = {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": version,
        "published_at": "2026-11-18T12:00:00Z",
        "release_notes_url": f"https://github.com/Chouwzi/UTHelper/releases/tag/v{version}",
        "packages": [
            {
                **pkg_doc,
                "signature_kind": "self-signed-pinned",
            }
        ],
    }
    manifest = update_manifest.parse_manifest(
        manifest_doc,
        expected_release_version=version,
    )
    return update_manifest.select_candidate(
        manifest,
        current_version="2.5.1",
        target=update_models.RuntimeTarget("windows", "x64", install_channel),
    )


# ===========================================================================
# F5: Mobile LTS .apk.part fix & Kotlin test unmasking (5 tests)
# ===========================================================================


def test_f5_kotlin_installer_renames_apk_part_before_reading_archive_metadata() -> None:
    kt_path = (
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
    )
    source = kt_path.read_text(encoding="utf-8")

    # Either prepareVerifiedUpdatePackage renames partialApk -> cachedApk before
    # returning cachedApk, OR verifyLocalAndOpen performs renameTo(stagedApk)
    # strictly BEFORE readArchiveMetadata(...).
    prep_match = re.search(
        r"fun\s+prepareVerifiedUpdatePackage\b.*?\n\}", source, re.DOTALL
    )
    assert prep_match is not None, "prepareVerifiedUpdatePackage not found"
    prep_body = prep_match.group(0)
    prep_returns_partial = "PreparedUpdatePackage(\n" in prep_body and (
        "partialApk" in prep_body.split("PreparedUpdatePackage(")[-1].split(")")[0]
    ) or "PreparedUpdatePackage(partialApk" in prep_body

    verify_match = re.search(
        r"fun\s+verifyLocalAndOpen\b.*?\n\s*\}\s*\n\s*private\s+fun",
        source,
        re.DOTALL,
    )
    if verify_match is None:
        verify_match = re.search(r"fun\s+verifyLocalAndOpen\b.*", source, re.DOTALL)
    assert verify_match is not None, "verifyLocalAndOpen not found"
    verify_body = verify_match.group(0)

    rename_pos = verify_body.find("renameTo(stagedApk)")
    archive_pos = verify_body.find("readArchiveMetadata(")
    assert archive_pos != -1, "readArchiveMetadata call missing in verifyLocalAndOpen"

    if prep_returns_partial:
        assert rename_pos != -1 and rename_pos < archive_pos, (
            "ApkUpdateInstaller.kt must rename .apk.part -> .apk BEFORE calling "
            "readArchiveMetadata() / getPackageArchiveInfo()"
        )
    else:
        assert "renameTo(cachedApk)" in prep_body or (
            rename_pos != -1 and rename_pos < archive_pos
        )


def test_f5_kotlin_flow_test_no_longer_masks_apk_part_in_shadow_package_manager() -> None:
    test_kt = (
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
    )
    source = test_kt.read_text(encoding="utf-8")

    assert not re.search(
        r"setPackageArchiveInfo\s*\([^)]*\.apk\.part",
        source,
        re.DOTALL,
    ), "ApkUpdateInstallerFlowTest.kt must not register .apk.part in ShadowPackageManager"
    assert "update-$releaseKey.apk.part" not in source, (
        "ApkUpdateInstallerFlowTest.kt must not mask update-$releaseKey.apk.part"
    )
    assert "setPackageArchiveInfo" in source
    assert "update-$releaseKey.apk" in source


def test_f5_simulated_bridge_enforces_apk_suffix_and_completes_install_opened(
    android_bridge: SimulatedAndroidBridge,
) -> None:
    apk_bytes = make_minimal_apk_bytes(
        b"dex-v2.6.0",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    apk_sha = sha256_hex(apk_bytes)
    url = "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk"
    android_bridge.register_apk_payload(url, apk_bytes)
    android_bridge.enforce_apk_suffix_on_archive_parse = True

    part_path = android_bridge.verified_cache / "bogus.apk.part"
    part_path.write_bytes(apk_bytes)
    assert android_bridge.get_package_archive_info(part_path) is None

    async def _flow() -> dict[str, Any]:
        start_res = await android_bridge.start_update_download(
            url,
            apk_sha,
            len(apk_bytes),
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )
        status_res = start_res
        while status_res["status"] in {"pending", "running", "paused"}:
            status_res = await android_bridge.get_update_download_status(
                url,
                apk_sha,
                len(apk_bytes),
                DEFAULT_ANDROID_PACKAGE_ID,
                2_006_000,
                DEFAULT_TRUSTED_SIGNER_SHA256,
            )
        assert status_res["status"] == "completed"
        local_path = status_res["local_path"]
        assert isinstance(local_path, str) and local_path.endswith(".apk")
        return await android_bridge.install_update(
            local_path,
            apk_sha,
            len(apk_bytes),
            DEFAULT_ANDROID_PACKAGE_ID,
            2_006_000,
            DEFAULT_TRUSTED_SIGNER_SHA256,
        )

    install_res = asyncio.run(_flow())
    assert install_res["status"] == "installer_opened"
    assert install_res["stage"] == "installer_handoff"
    assert install_res["sha256"] == apk_sha
    assert (android_bridge.verified_cache / f"update-{apk_sha[:24]}.apk").is_file()
    assert not (android_bridge.verified_cache / f"update-{apk_sha[:24]}.apk.part").exists()


def test_f5_android_lts_end_to_end_discovery_download_verify_and_install(
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")
    android_updates = load_module("platform_utils.android_updates")

    apk_bytes = make_minimal_apk_bytes(
        b"release-2.6.0",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=local_release_server.build_manifest(
            release_version="2.6.0",
            packages=[pkg_doc],
        ),
    )

    events: list[Any] = []
    version_box = {"value": "2.6.0"}
    downloader = android_updates.AndroidPackageDownloader(
        android_bridge,
        fake_page.run_task,
        lambda: version_box["value"],
        poll_interval_seconds=0.01,
    )
    launcher = android_updates.AndroidPackageLauncher(
        android_bridge,
        fake_page.run_task,
        lambda: version_box["value"],
    )
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        downloader,
        update_packages.DownloadedPackageVerifier(),
        launcher,
        update_models.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=True,
    )
    try:
        coordinator.start()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
                for e in events
            )
        )
        coordinator.request_download()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.READY_TO_INSTALL
                for e in events
            )
        )
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.INSTALL_LAUNCHED
                for e in events
            )
        )
        call_names = [name for name, _args in android_bridge.calls]
        assert "start_update_download" in call_names
        assert "get_update_download_status" in call_names
        assert "install_update" in call_names
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


def test_f5_downloaded_package_verifier_requires_apk_suffix_and_zip_magic(
    tmp_path: Path,
) -> None:
    update_packages = load_module("platform_utils.update_packages")
    update_models = load_module("core.update_models")

    apk_bytes = make_minimal_apk_bytes(b"valid-apk")
    apk_path = tmp_path / "UTHelper-2.6.0.apk"
    part_path = tmp_path / "UTHelper-2.6.0.apk.part"
    apk_path.write_bytes(apk_bytes)
    part_path.write_bytes(apk_bytes)

    package = update_models.ReleasePackage(
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
    manifest = update_models.ReleaseManifest(
        schema_version=3,
        release_version="2.6.0",
        minimum_supported_version="2.6.0",
        published_at=datetime(2026, 11, 18, tzinfo=timezone.utc),
        release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
        packages=(package,),
    )
    candidate = update_models.UpdateCandidate(manifest, package, True, False)
    verifier = update_packages.DownloadedPackageVerifier()

    assert verifier.verify(apk_path, candidate).verified is True
    assert verifier.verify(part_path, candidate).verified is False

    bad_magic = tmp_path / "bad.apk"
    bad_magic.write_bytes(b"\x00" * len(apk_bytes))
    assert verifier.verify(bad_magic, candidate).verified is False


# ===========================================================================
# F6: Mobile LTS bridge error propagation, null-candidate safety & cache (6 tests)
# ===========================================================================


def test_f6_app_controller_android_bridge_error_propagates_instead_of_none(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("apk_lts")

    apk_bytes = make_minimal_apk_bytes(version_code=2_099_000)
    pkg_doc = local_release_server.register_package(
        tag_name="v2.99.0",
        filename="UTHelper-2.99.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.99.0",
        manifest=local_release_server.build_manifest(
            release_version="2.99.0",
            packages=[pkg_doc],
        ),
    )

    controller = _build_minimal_controller(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
        android_bridge=android_bridge,
    )
    try:
        coordinator = controller._update_coordinator
        assert coordinator is not None
        assert _wait_until(
            lambda: (
                controller._update_candidate is not None
                and controller._update_banner.visible is True
            )
        )

        android_bridge.inject_exception = RuntimeError("native_bridge_boom")
        controller._request_update_download()
        assert _wait_until(
            lambda: (
                controller._update_banner.visible is True
                and controller._update_btn.content == "Thử lại"
                and controller._update_btn.disabled is False
                and "Không thể tải hoặc xác minh" in controller._update_text.value
            )
        ), (
            f"Expected AppController UI failure state, got banner={controller._update_banner.visible}, "
            f"btn={controller._update_btn.content!r}, text={controller._update_text.value!r}"
        )

        pkg = controller._update_candidate.package
        identity = coordinator._downloader._native.identity(pkg)
        with pytest.raises(RuntimeError, match="native_bridge_boom"):
            coordinator._downloader._native.call(
                android_bridge.start_update_download, *identity
            )

        _, sha256, size, package_id, version_code, signer = (
            coordinator._launcher._native.identity(pkg)
        )
        apk_path = android_bridge.external_downloads / "UTHelper-2.99.0.apk"
        with pytest.raises(RuntimeError, match="native_bridge_boom"):
            coordinator._launcher._native.call(
                android_bridge.install_update,
                str(apk_path),
                sha256,
                size,
                package_id,
                version_code,
                signer,
            )
        launch_res = coordinator._launcher.launch(apk_path, pkg)
        assert launch_res.acknowledged is False
        assert launch_res.status == "bridge_failed"
    finally:
        controller._on_disconnect(None)


def test_f6_null_update_candidate_does_not_crash_release_version_provider(
    distribution_channel_stager,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update_models = load_module("core.update_models")
    distribution_channel_stager("apk_lts")

    controller = _build_minimal_controller(
        monkeypatch,
        fake_page,
        is_windows=False,
        is_android=True,
        android_bridge=android_bridge,
    )
    try:
        controller._update_candidate = None
        coordinator = controller._update_coordinator
        assert coordinator is not None
        pkg = update_models.ReleasePackage(
            platform="android",
            architecture="universal",
            package_type="apk",
            install_channel="sideload",
            url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
            sha256="a" * 64,
            size=128,
            signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
            certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
            install_strategy={"kind": "android_package_installer"},
        )
        pending = coordinator._downloader.has_pending_download(pkg)
        assert pending is False
        launcher_version = coordinator._launcher._native.release_version_provider()
        assert launcher_version is None or isinstance(launcher_version, str)
    finally:
        controller._on_disconnect(None)


def test_f6_permission_required_retry_reuses_verified_cache_after_source_deleted(
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")
    android_updates = load_module("platform_utils.android_updates")

    apk_bytes = make_minimal_apk_bytes(
        b"cached-fallback-2.6.0",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    apk_sha = sha256_hex(apk_bytes)
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=local_release_server.build_manifest(
            release_version="2.6.0",
            packages=[pkg_doc],
        ),
    )

    android_bridge.permission_granted = False
    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        android_updates.AndroidPackageDownloader(
            android_bridge,
            fake_page.run_task,
            lambda: "2.6.0",
            poll_interval_seconds=0.01,
        ),
        update_packages.DownloadedPackageVerifier(),
        android_updates.AndroidPackageLauncher(
            android_bridge,
            fake_page.run_task,
            lambda: "2.6.0",
        ),
        update_models.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=True,
    )
    try:
        coordinator.start()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
                for e in events
            )
        )
        coordinator.request_download()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.READY_TO_INSTALL
                for e in events
            )
        )
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind
                is update_coordinator.UpdateEventKind.INSTALL_PERMISSION_REQUIRED
                for e in events
            )
        )

        # Delete external DownloadManager APK while keeping verified cache
        for item in android_bridge.external_downloads.glob("*.apk"):
            item.unlink()
        assert (android_bridge.verified_cache / f"update-{apk_sha[:24]}.apk").is_file()

        android_bridge.permission_granted = True
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.INSTALL_LAUNCHED
                for e in events
            )
        )
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


def test_f6_android_arm64_falls_back_to_universal_apk_when_arm64_absent(
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")
    android_updates = load_module("platform_utils.android_updates")

    apk_bytes = make_minimal_apk_bytes(
        b"universal-fallback",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.apk",
        data=apk_bytes,
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        signer_identity=DEFAULT_ANDROID_PACKAGE_ID,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=local_release_server.build_manifest(
            release_version="2.6.0",
            packages=[pkg_doc],
        ),
    )

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        android_updates.AndroidPackageDownloader(
            android_bridge,
            fake_page.run_task,
            lambda: "2.6.0",
            poll_interval_seconds=0.01,
        ),
        update_packages.DownloadedPackageVerifier(),
        android_updates.AndroidPackageLauncher(
            android_bridge,
            fake_page.run_task,
            lambda: "2.6.0",
        ),
        update_models.RuntimeTarget("android", "arm64", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.check_now()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
                for e in events
            )
        )
        available = next(
            e
            for e in events
            if e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
        )
        assert available.candidate.package.architecture == "universal"
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


def test_f6_shutdown_detaches_android_downloader_without_cancelling_os_job(
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")
    android_updates = load_module("platform_utils.android_updates")

    apk_bytes = make_minimal_apk_bytes(
        b"durable-download",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    candidate = _android_candidate(local_release_server, apk_bytes)
    android_bridge.set_download_status_plan(["pending", "running", "running"])

    events: list[Any] = []
    downloader1 = android_updates.AndroidPackageDownloader(
        android_bridge,
        fake_page.run_task,
        lambda: "2.6.0",
        poll_interval_seconds=0.05,
    )
    coordinator1 = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        downloader1,
        update_packages.DownloadedPackageVerifier(),
        android_updates.AndroidPackageLauncher(
            android_bridge, fake_page.run_task, lambda: "2.6.0"
        ),
        update_models.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    coordinator1.request_download(candidate)
    assert _wait_until(
        lambda: any(
            name == "start_update_download" for name, _ in android_bridge.calls
        )
    )
    assert coordinator1.shutdown(timeout_seconds=2.0) is True
    assert not any(
        name == "cancel_update_download" for name, _ in android_bridge.calls
    )

    downloader2 = android_updates.AndroidPackageDownloader(
        android_bridge,
        fake_page.run_task,
        lambda: "2.6.0",
        poll_interval_seconds=0.01,
    )
    assert downloader2.has_pending_download(candidate.package) is True


def test_f6_user_cancel_invokes_cancel_update_download_and_emits_cancelled(
    local_release_server: LocalReleaseServer,
    android_bridge: SimulatedAndroidBridge,
    fake_page: FakePage,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")
    android_updates = load_module("platform_utils.android_updates")

    apk_bytes = make_minimal_apk_bytes(
        b"cancel-download",
        package_id=DEFAULT_ANDROID_PACKAGE_ID,
        version_code=2_006_000,
        signer_sha256=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    candidate = _android_candidate(local_release_server, apk_bytes)
    android_bridge.set_download_status_plan(["pending", "running", "running"])

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        android_updates.AndroidPackageDownloader(
            android_bridge,
            fake_page.run_task,
            lambda: "2.6.0",
            poll_interval_seconds=0.02,
        ),
        update_packages.DownloadedPackageVerifier(),
        android_updates.AndroidPackageLauncher(
            android_bridge, fake_page.run_task, lambda: "2.6.0"
        ),
        update_models.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.DOWNLOAD_PROGRESS
                for e in events
            )
        )
        coordinator.cancel_download()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.CANCELLED for e in events
            )
        )
        assert any(
            name == "cancel_update_download" for name, _ in android_bridge.calls
        )
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


# ===========================================================================
# F7: PC LTS Windows launcher UAC/lock race & post-upgrade relaunch (5 tests)
# ===========================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f7_exe_lts_msi_e2e_discovery_download_verify_and_launch(
    local_release_server: LocalReleaseServer,
    windows_shim_env: WindowsShimEnv,
    isolated_runtime_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    windows_update = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "source" / "UTHelper-2.6.0.msi")
    msi_bytes = msi_path.read_bytes()
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.msi",
        data=msi_bytes,
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=local_release_server.build_manifest(
            release_version="2.6.0", packages=[pkg_doc]
        ),
    )

    windows_shim_env.set_ps_mode("self_signed", version="2.6.0")
    windows_shim_env.set_msi_behavior(exit_code=0, delay_sec=11.0)

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        update_checker.VerifiedDownloader(cache_dir=isolated_runtime_env["cache"]),
        windows_update.WindowsPackageVerifier(),
        windows_update.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory
        ),
        update_models.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        events.append,
        automatic_enabled=True,
    )
    try:
        coordinator.start()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
                for e in events
            )
        )
        coordinator.request_download()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.READY_TO_INSTALL
                for e in events
            )
        )
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.INSTALL_LAUNCHED
                for e in events
            ),
            timeout=15.0,
        )
        calls = windows_shim_env.read_calls()
        assert any(c["tool"] == "powershell.exe" for c in calls)
        msi_calls = [c for c in calls if c["tool"] == "msiexec.exe"]
        assert msi_calls, f"Expected msiexec.exe call, got {calls}"
        assert "/i" in msi_calls[0]["argv"]
        assert "/passive" in msi_calls[0]["argv"]
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f7_exe_lts_bootstrapper_e2e_discovery_download_verify_and_launch(
    local_release_server: LocalReleaseServer,
    windows_shim_env: WindowsShimEnv,
    isolated_runtime_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    windows_update = load_module("platform_utils.windows_update")

    exe_path = windows_shim_env.make_setup_exe(
        tmp_path / "source" / "UTHelper-Setup-2.6.0.exe"
    )
    exe_bytes = exe_path.read_bytes()
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-Setup-2.6.0.exe",
        data=exe_bytes,
        platform="windows",
        architecture="x64",
        package_type="exe",
        install_channel="bootstrapper",
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    local_release_server.configure_latest_release(
        tag_name="v2.6.0",
        manifest=local_release_server.build_manifest(
            release_version="2.6.0", packages=[pkg_doc]
        ),
    )

    windows_shim_env.set_ps_mode("valid", version="2.6.0")
    windows_shim_env.set_msi_behavior(exit_code=0, delay_sec=11.0)

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        update_checker.VerifiedDownloader(cache_dir=isolated_runtime_env["cache"]),
        windows_update.WindowsPackageVerifier(),
        windows_update.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory
        ),
        update_models.RuntimeTarget("windows", "x64", "bootstrapper"),
        "2.5.1",
        events.append,
        automatic_enabled=True,
    )
    try:
        coordinator.start()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.UPDATE_AVAILABLE
                for e in events
            )
        )
        coordinator.request_download()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.READY_TO_INSTALL
                for e in events
            )
        )
        coordinator.confirm_install()
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.INSTALL_LAUNCHED
                for e in events
            ),
            timeout=15.0,
        )
        calls = windows_shim_env.read_calls()
        exe_calls = [
            c for c in calls if "uthelper-setup-2.6.0.exe" in c["tool"]
        ]
        assert exe_calls, f"Expected UTHelper-Setup-2.6.0.exe call, got {calls}"
        assert "/passive" in exe_calls[0]["argv"]
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f7_windows_uac_rejection_past_2s_is_not_prematurely_acknowledged(
    windows_shim_env: WindowsShimEnv,
    tmp_path: Path,
) -> None:
    update_models = load_module("core.update_models")
    windows_update = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    pkg = update_models.ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msi",
        sha256=sha256_hex(msi_path.read_bytes()),
        size=msi_path.stat().st_size,
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "launch_msi"},
    )
    # Simulate user dismissing UAC at 2.5s (> old 2.0s timeout race).
    windows_shim_env.set_msi_behavior(exit_code=1223, delay_sec=2.5)
    launcher = windows_update.WindowsPackageLauncher(
        process_factory=windows_shim_env.process_factory
    )
    result = launcher.launch(msi_path, pkg)
    assert result.acknowledged is False, (
        "UAC rejection arriving at 2.5s must not be prematurely acknowledged at 2.0s"
    )


def test_f7_app_controller_windows_install_launched_shuts_down_cleanly_before_destroy(
    distribution_channel_stager,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator_mod = load_module("core.update_coordinator")
    distribution_channel_stager("exe_lts")

    controller = _build_minimal_controller(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    order: list[str] = []
    try:
        original_destroy = fake_page.window.destroy

        async def _tracked_destroy() -> None:
            order.append("window_destroy")
            await original_destroy()

        fake_page.window.destroy = _tracked_destroy  # type: ignore[method-assign]
        controller.tray = SimpleNamespace(
            close=lambda timeout_seconds=1.0: order.append("tray_close")
        )
        controller.orchestrator = SimpleNamespace(
            client=SimpleNamespace(close=lambda: order.append("client_close"))
        )
        if controller._update_coordinator is not None:
            orig_shutdown = controller._update_coordinator.shutdown

            def _tracked_shutdown(timeout_seconds: float = 5.0) -> bool:
                order.append("coordinator_shutdown")
                return orig_shutdown(timeout_seconds=timeout_seconds)

            controller._update_coordinator.shutdown = _tracked_shutdown  # type: ignore[method-assign]

        candidate = SimpleNamespace(
            manifest=SimpleNamespace(
                release_version="2.6.0",
                release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
            )
        )
        asyncio.run(
            controller._apply_update_event(
                coordinator_mod.UpdateEvent(
                    coordinator_mod.UpdateEventKind.INSTALL_LAUNCHED,
                    candidate,
                )
            )
        )
        assert fake_page.window.destroyed is True
        assert "window_destroy" in order
        destroy_idx = order.index("window_destroy")
        for step in ("coordinator_shutdown", "tray_close", "client_close"):
            assert step in order, (
                f"AppController must run {step} during Windows INSTALL_LAUNCHED "
                f"(observed order={order})"
            )
            assert order.index(step) < destroy_idx, (
                f"{step} must execute BEFORE window_destroy (observed order={order})"
            )
    finally:
        controller._on_disconnect(None)


def test_f7_passive_msi_and_burn_upgrades_configure_post_upgrade_relaunch() -> None:
    package_wxs = (WORKTREE_ROOT / "packaging" / "windows" / "Package.wxs").read_text(
        encoding="utf-8"
    )
    bundle_wxs = (WORKTREE_ROOT / "packaging" / "windows" / "Bundle.wxs").read_text(
        encoding="utf-8"
    )
    windows_update_py = (
        WORKTREE_ROOT / "src" / "platform_utils" / "windows_update.py"
    ).read_text(encoding="utf-8")

    # 1) MSI WiX CustomAction scheduled in InstallExecuteSequence for UTHelper.exe relaunch
    package_root = ET.fromstring(package_wxs)
    scheduled_actions = {
        elem.attrib.get("Action", "")
        for elem in package_root.iter()
        if elem.tag.rsplit("}", 1)[-1] == "Custom"
    }
    wix_relaunch = any(
        elem.tag.rsplit("}", 1)[-1] == "CustomAction"
        and elem.attrib.get("Id", "") in scheduled_actions
        and (
            "UTHelper.exe" in " ".join(elem.attrib.values())
            or "relaunch" in elem.attrib.get("Id", "").lower()
        )
        for elem in package_root.iter()
    )
    # 2) Or Burn Bundle.wxs configures a passive-compatible relaunch action
    bundle_root = ET.fromstring(bundle_wxs)
    burn_relaunch = any(
        "relaunch" in " ".join(elem.attrib.values()).lower()
        for elem in bundle_root.iter()
    )

    # 3) Or windows_update.py non-comment/non-docstring AST contains actual relaunch logic
    tree = ast.parse(windows_update_py)
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring_nodes.add(id(node.body[0].value))

    code_strings = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstring_nodes
    ]
    call_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                call_names.add(node.func.attr)
    def_names = {
        node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    has_relaunch_literal = any(
        "UTHelper.exe" in s or "Start-Process" in s for s in code_strings
    )
    has_process_spawn = bool(call_names & {"Popen", "process_factory", "run"})
    has_relaunch_fn = any("relaunch" in name for name in def_names)
    python_relaunch = has_process_spawn and (has_relaunch_literal or has_relaunch_fn)

    assert wix_relaunch or burn_relaunch or python_relaunch, (
        "Passive MSI/Burn upgrades must configure or execute post-upgrade UTHelper.exe relaunch"
    )


# ===========================================================================
# F8: PC LTS verifier timeout, Windows error mappings & Debug UI sync (5 tests)
# ===========================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f8_windows_verifier_with_shim_handles_valid_and_self_signed_within_extended_timeout(
    windows_shim_env: WindowsShimEnv,
    tmp_path: Path,
) -> None:
    windows_update = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    pkg_doc = {
        "platform": "windows",
        "architecture": "x64",
        "package_type": "msi",
        "install_channel": "msi",
        "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msi",
        "sha256": sha256_hex(msi_path.read_bytes()),
        "size": msi_path.stat().st_size,
        "signer_identity": DEFAULT_SIGNER_SUBJECT,
        "certificate_fingerprint": DEFAULT_TRUSTED_SIGNER_SHA256,
        "install_strategy": {"kind": "launch_msi"},
    }
    candidate = _windows_candidate(pkg_doc, version="2.6.0")
    verifier = windows_update.WindowsPackageVerifier()

    windows_shim_env.clear_calls()
    windows_shim_env.set_ps_mode("valid", delay_sec=0.05, version="2.6.0")
    assert verifier.verify(msi_path, candidate).verified is True
    ps_calls = [c for c in windows_shim_env.read_calls() if c["tool"] == "powershell.exe"]

    # Either probes are combined into 1 PowerShell invocation OR default timeout >= 20s
    assert len(ps_calls) == 1 or verifier.timeout_seconds >= 20.0, (
        f"Expected combined PowerShell probe or extended timeout >= 20s; "
        f"got ps_calls={len(ps_calls)}, timeout={verifier.timeout_seconds}"
    )

    windows_shim_env.set_ps_mode("self_signed", delay_sec=0.05, version="2.6.0")
    assert verifier.verify(msi_path, candidate).verified is True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f8_transient_powershell_timeout_preserves_verified_downloaded_package(
    local_release_server: LocalReleaseServer,
    windows_shim_env: WindowsShimEnv,
    isolated_runtime_env: dict[str, Path],
    tmp_path: Path,
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    windows_update = load_module("platform_utils.windows_update")

    msi_path = windows_shim_env.make_msi_file(tmp_path / "src" / "UTHelper-2.6.0.msi")
    pkg_doc = local_release_server.register_package(
        tag_name="v2.6.0",
        filename="UTHelper-2.6.0.msi",
        data=msi_path.read_bytes(),
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
    )
    candidate = _windows_candidate(pkg_doc, version="2.6.0")

    # 1st verify hits PowerShell timeout; subsequent verify attempts succeed.
    windows_shim_env.set_ps_sequence(["timeout", "valid", "valid"])
    windows_shim_env.set_ps_mode("valid", version="2.6.0")

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        update_checker.VerifiedDownloader(cache_dir=isolated_runtime_env["cache"]),
        windows_update.WindowsPackageVerifier(timeout_seconds=0.3),
        windows_update.WindowsPackageLauncher(
            process_factory=windows_shim_env.process_factory
        ),
        update_models.RuntimeTarget("windows", "x64", "msi"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    try:
        coordinator.request_download(candidate)
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.FAILED for e in events
            )
        )
        cached_msi = isolated_runtime_env["cache"] / "UTHelper-2.6.0.msi"
        assert cached_msi.is_file(), (
            "Transient PowerShell verification timeout must preserve verified downloaded cache"
        )

        # Retry reuses the preserved cached file with 0 new package HTTP downloads.
        local_release_server.clear_requests()
        events.clear()
        coordinator.request_download(candidate)
        assert _wait_until(
            lambda: any(
                e.kind is update_coordinator.UpdateEventKind.READY_TO_INSTALL
                for e in events
            )
        )
        assert local_release_server.request_count == 0
    finally:
        coordinator.shutdown(timeout_seconds=2.0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows shim test")
def test_f8_windows_installer_error_reasons_map_to_distinct_vietnamese_messages(
    distribution_channel_stager,
    windows_shim_env: WindowsShimEnv,
    fake_page: FakePage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator_mod = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    windows_update = load_module("platform_utils.windows_update")

    distribution_channel_stager("exe_lts")
    msi_path = windows_shim_env.make_msi_file(tmp_path / "UTHelper-2.6.0.msi")
    pkg = update_models.ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type="msi",
        install_channel="msi",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.msi",
        sha256=sha256_hex(msi_path.read_bytes()),
        size=msi_path.stat().st_size,
        signer_identity=DEFAULT_SIGNER_SUBJECT,
        certificate_fingerprint=DEFAULT_TRUSTED_SIGNER_SHA256,
        install_strategy={"kind": "launch_msi"},
    )
    launcher = windows_update.WindowsPackageLauncher(
        process_factory=windows_shim_env.process_factory
    )
    controller = _build_minimal_controller(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    try:
        candidate = SimpleNamespace(
            manifest=SimpleNamespace(
                release_version="2.6.0",
                release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.6.0",
            )
        )
        generic_fallback = "Không mở được trình cài đặt. Hãy thử lại."
        rendered_messages: dict[int, str] = {}

        for exit_code in (1223, 1618):
            windows_shim_env.set_msi_behavior(exit_code=exit_code, delay_sec=0.05)
            launch_res = launcher.launch(msi_path, pkg)
            assert launch_res.acknowledged is False
            reason = launch_res.reason or launch_res.status
            asyncio.run(
                controller._apply_update_event(
                    coordinator_mod.UpdateEvent(
                        coordinator_mod.UpdateEventKind.INSTALL_FAILED,
                        candidate,
                        message=reason,
                    )
                )
            )
            rendered = controller._update_text.value
            rendered_messages[exit_code] = rendered
            assert rendered != generic_fallback, (
                f"Exit code {exit_code} ({reason!r}) fell back to generic message"
            )
            assert controller._update_btn.content == "Thử lại"
            assert controller._update_btn.disabled is False
            assert fake_page.window.destroyed is False

        assert rendered_messages[1223] != rendered_messages[1618]
    finally:
        controller._on_disconnect(None)


def test_f8_debug_force_check_update_updates_debug_update_text_when_wired_to_controller(
    distribution_channel_stager,
    local_release_server: LocalReleaseServer,
    fake_page: FakePage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distribution_channel_stager("exe_lts")
    windows_update = load_module("platform_utils.windows_update")
    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "bootstrapper")

    pkg_doc = local_release_server.register_package(
        tag_name="v2.5.1",
        filename="UTHelper-Setup-2.5.1.exe",
        data=b"MZ" + b"\x00" * 64,
        platform="windows",
        architecture="x64",
        package_type="exe",
        install_channel="bootstrapper",
    )
    local_release_server.configure_latest_release(
        tag_name="v2.5.1",
        manifest=local_release_server.build_manifest(
            release_version="2.5.1",
            packages=[pkg_doc],
        ),
    )

    controller = _build_minimal_controller(
        monkeypatch,
        fake_page,
        is_windows=True,
        is_android=False,
    )
    try:
        controller.settings_view._do_force_check_update()
        assert _wait_until(
            lambda: bool(controller.settings_view._debug_update_text.value.strip())
        ), (
            "Debug panel 'Force check update' must update _debug_update_text even when "
            "SettingsView is wired to AppController._check_updates_now"
        )
    finally:
        controller._on_disconnect(None)


def test_f8_update_coordinator_shutdown_is_non_blocking_under_slow_download(
    local_release_server: LocalReleaseServer,
    isolated_runtime_env: dict[str, Path],
) -> None:
    update_checker = load_module("core.update_checker")
    update_coordinator = load_module("core.update_coordinator")
    update_models = load_module("core.update_models")
    update_packages = load_module("platform_utils.update_packages")

    payload = make_minimal_apk_bytes(b"slow-payload")
    candidate = _android_candidate(local_release_server, payload)
    local_release_server.set_fault(
        "/Chouwzi/UTHelper/releases/download/v2.6.0/UTHelper-2.6.0.apk",
        delay_seconds=4.0,
    )

    events: list[Any] = []
    coordinator = update_coordinator.UpdateCoordinator(
        update_checker.GitHubReleaseClient(),
        update_checker.VerifiedDownloader(cache_dir=isolated_runtime_env["cache"]),
        update_packages.DownloadedPackageVerifier(),
        SimpleNamespace(launch=lambda *_a: None, cancel=lambda: None),
        update_models.RuntimeTarget("android", "universal", "sideload"),
        "2.5.1",
        events.append,
        automatic_enabled=False,
    )
    coordinator.request_download(candidate)
    assert _wait_until(
        lambda: any(
            e.kind is update_coordinator.UpdateEventKind.DOWNLOADING for e in events
        )
    )
    started = time.monotonic()
    coordinator.shutdown(timeout_seconds=0.5)
    elapsed = time.monotonic() - started
    # Must remain bounded/non-blocking rather than waiting for the 4.0s server stall.
    assert elapsed < 1.5
    with pytest.raises(update_coordinator.CoordinatorClosedError):
        coordinator.check_now()
