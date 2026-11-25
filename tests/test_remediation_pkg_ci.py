"""TDD regression tests for PKG-01, GATE-01, PKG-02, PKG-03, and PKG-04."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path
import re
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import update_checker, update_manifest
from core.demo_sandbox import is_demo_credentials
from core.update_coordinator import UpdateCoordinator, UpdateEvent, UpdateEventKind
from core.update_manifest import (
    parse_manifest,
    select_candidate,
)
from core.update_models import (
    LaunchResult,
    ReleaseManifest,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
    VerificationResult,
)
from gui.components.courses.adaptive_nav import NAV_DESTINATIONS
from platform_utils import android_updates
from platform_utils.android_updates import (
    PACKAGE_ID,
    AndroidPackageDownloader,
    AndroidPackageLauncher,
    AndroidPackageVerifier,
)


ROOT = Path(__file__).resolve().parents[1]
BUILD_GRADLE = (
    ROOT
    / "extensions"
    / "flet_uth_background_sync"
    / "flutter"
    / "flet_uth_background_sync"
    / "android"
    / "build.gradle"
)
STORE_GUIDE = ROOT / "docs" / "guides" / "store-distribution-submission.md"
ADR_0004 = ROOT / "docs" / "adr" / "0004-multi-channel-store-and-lts-distribution.md"
LOGIN_DIALOG = ROOT / "src" / "gui" / "components" / "login_dialog.py"
QUIZ_FLUTTER_DIR = (
    ROOT / "extensions" / "flet_uth_quiz" / "flutter" / "flet_uth_quiz"
)


def _run_task(handler, *args):
    future: Future = Future()
    try:
        future.set_result(asyncio.run(handler(*args)))
    except Exception as exc:
        future.set_exception(exc)
    return future


def _schema3_android_manifest(
    *,
    version: str = "2.5.1",
    signer_identity: str = PACKAGE_ID,
    architecture: str = "arm64",
) -> dict:
    return {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-11-25T00:00:00Z",
        "release_notes_url": (
            f"https://github.com/Chouwzi/UTHelper/releases/tag/v{version}"
        ),
        "packages": [
            {
                "platform": "android",
                "architecture": architecture,
                "package_type": "apk",
                "install_channel": "sideload",
                "url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    f"v{version}/UTHelper-{version}.apk"
                ),
                "sha256": "a" * 64,
                "size": 100,
                "signer_identity": signer_identity,
                "certificate_fingerprint": "B" * 64,
                "signature_kind": "apk-pinned",
                "install_strategy": {"kind": "android_package_installer"},
            }
        ],
    }


def _schema3_windows_manifest(*, version: str = "2.5.1") -> dict:
    return {
        "schema_version": 3,
        "release_version": version,
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-11-25T00:00:00Z",
        "release_notes_url": (
            f"https://github.com/Chouwzi/UTHelper/releases/tag/v{version}"
        ),
        "packages": [
            {
                "platform": "windows",
                "architecture": "x64",
                "package_type": "msi",
                "install_channel": "msi",
                "url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    f"v{version}/UTHelper-{version}.msi"
                ),
                "sha256": "a" * 64,
                "size": 4096,
                "signer_identity": "CN=UTHelper",
                "certificate_fingerprint": "B" * 64,
                "signature_kind": "self-signed-pinned",
                "install_strategy": {"kind": "launch_msi"},
            }
        ],
    }


def _android_package(
    *,
    signer_identity: str = PACKAGE_ID,
    size: int = 100,
) -> ReleasePackage:
    return ReleasePackage(
        platform="android",
        architecture="arm64",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.apk",
        sha256="a" * 64,
        size=size,
        signer_identity=signer_identity,
        certificate_fingerprint="B" * 64,
        install_strategy={"kind": "android_package_installer"},
        signature_kind="apk-pinned",
    )


def _android_candidate(
    package: ReleasePackage,
    *,
    version: str = "2.5.1",
    automatic_install_allowed: bool = True,
) -> UpdateCandidate:
    manifest = ReleaseManifest(
        schema_version=3,
        release_version=version,
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 11, 25, tzinfo=UTC),
        release_notes_url=f"https://github.com/Chouwzi/UTHelper/releases/tag/v{version}",
        packages=(package,),
    )
    return UpdateCandidate(
        manifest=manifest,
        package=package,
        automatic_install_allowed=automatic_install_allowed,
        required_update=False,
    )


# ===========================================================================
# PKG-01: Pre-download Android signer_identity validation & manual fallback
# ===========================================================================


def test_pkg01_select_candidate_disables_auto_install_on_android_signer_mismatch_by_default():
    manifest = parse_manifest(
        _schema3_android_manifest(signer_identity="com.uthelper.uthelper"),
        expected_release_version="2.5.1",
    )

    candidate = select_candidate(
        manifest,
        current_version="2.5.0",
        target=RuntimeTarget("android", "arm64", "sideload"),
    )

    assert candidate is not None
    assert candidate.automatic_install_allowed is False
    reason = update_manifest.manual_install_reason(candidate)
    assert reason is not None
    assert "com.uthelper.uthelper" in reason
    assert PACKAGE_ID in reason
    assert "GitHub Releases" in reason


def test_pkg01_select_candidate_allows_matching_android_package_id_and_custom_override():
    manifest = parse_manifest(
        _schema3_android_manifest(signer_identity=PACKAGE_ID),
        expected_release_version="2.5.1",
    )

    matched = select_candidate(
        manifest,
        current_version="2.5.0",
        target=RuntimeTarget("android", "arm64", "sideload"),
    )
    assert matched is not None
    assert matched.automatic_install_allowed is True
    assert update_manifest.manual_install_reason(matched) is None

    qa_mismatch = select_candidate(
        manifest,
        current_version="2.5.0",
        target=RuntimeTarget("android", "arm64", "sideload"),
        expected_signer_identity="com.uthelper.qa",
    )
    assert qa_mismatch is not None
    assert qa_mismatch.automatic_install_allowed is False
    qa_reason = update_manifest.manual_install_reason(
        qa_mismatch,
        expected_signer_identity="com.uthelper.qa",
    )
    assert qa_reason is not None
    assert "com.uthelper.qa" in qa_reason
    assert PACKAGE_ID in qa_reason

    qa_manifest = parse_manifest(
        _schema3_android_manifest(signer_identity="com.uthelper.qa"),
        expected_release_version="2.5.1",
    )
    qa_matched = select_candidate(
        qa_manifest,
        current_version="2.5.0",
        target=RuntimeTarget("android", "arm64", "sideload"),
        expected_signer_identity="com.uthelper.qa",
    )
    assert qa_matched is not None
    assert qa_matched.automatic_install_allowed is True


def test_pkg01_select_candidate_preserves_windows_and_ios_signer_identities():
    win_manifest = parse_manifest(
        _schema3_windows_manifest(),
        expected_release_version="2.5.1",
    )
    win_candidate = select_candidate(
        win_manifest,
        current_version="2.5.0",
        target=RuntimeTarget("windows", "x64", "msi"),
        expected_signer_identity=PACKAGE_ID,
    )
    assert win_candidate is not None
    assert win_candidate.automatic_install_allowed is True

    ios_schema2 = parse_manifest(
        {
            "schema_version": 2,
            "release_version": "2.5.1",
            "minimum_supported_version": "2.1.0",
            "published_at": "2026-11-25T00:00:00Z",
            "release_notes_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
            "packages": [
                {
                    "platform": "ios",
                    "architecture": "arm64",
                    "package_type": "ipa",
                    "install_channel": "app-store",
                    "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.ipa",
                    "sha256": "a" * 64,
                    "size": 4096,
                    "signer_identity": "TEAM123456",
                    "certificate_fingerprint": "B" * 64,
                    "install_strategy": {
                        "kind": "app_store",
                        "url": "https://apps.apple.com/app/id123456789",
                    },
                }
            ],
        },
        expected_release_version="2.5.1",
    )
    ios_candidate = select_candidate(
        ios_schema2,
        current_version="2.5.0",
        target=RuntimeTarget("ios", "arm64", "app-store"),
        expected_signer_identity=PACKAGE_ID,
    )
    assert ios_candidate is not None
    assert ios_candidate.automatic_install_allowed is True


def test_pkg01_live_update_checker_degrades_mismatched_android_release_to_manual_url():
    release_doc = {
        "tag_name": "v2.5.1",
        "html_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": "release-manifest.json",
                "browser_download_url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    "v2.5.1/release-manifest.json"
                ),
            }
        ],
    }
    mismatched_manifest = _schema3_android_manifest(
        version="2.5.1",
        signer_identity="com.uthelper.uthelper",
    )
    with (
        patch.object(
            update_checker,
            "detect_runtime_target",
            return_value=RuntimeTarget("android", "arm64", "sideload"),
        ),
        patch.object(
            update_checker,
            "_request_json",
            side_effect=[release_doc, mismatched_manifest],
        ),
    ):
        info = update_checker.get_update_info("2.5.0")

    assert info.has_update is True
    assert info.version == "2.5.1"
    assert info.release_url == "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1"
    assert info.asset is None


def test_pkg01_android_downloader_blocks_mismatched_package_id_before_download():
    calls: list[tuple[str, tuple]] = []

    class _RecordingBridge:
        available = True

        async def start_update_download(self, *args):
            calls.append(("start", args))
            return {"status": "completed", "local_path": "/tmp/UTHelper.apk"}

        async def get_update_download_status(self, *args):
            calls.append(("status", args))
            return {"status": "running"}

    mismatched = _android_package(signer_identity="com.uthelper.uthelper")
    downloader = AndroidPackageDownloader(
        _RecordingBridge(),
        _run_task,
        lambda: "2.5.1",
        poll_interval_seconds=0.001,
    )

    assert downloader.has_pending_download(mismatched) is False
    mismatch_err = getattr(
        android_updates, "AndroidPackageIdentityMismatchError", None
    )
    assert mismatch_err is not None, "AndroidPackageIdentityMismatchError missing"
    with pytest.raises(mismatch_err) as exc_info:
        downloader.download(mismatched, cancel=threading.Event())

    assert exc_info.value.expected_package_id == PACKAGE_ID
    assert exc_info.value.actual_package_id == "com.uthelper.uthelper"
    assert calls == []


def test_pkg01_android_verifier_rejects_mismatched_signer_identity_before_disk_read(
    tmp_path: Path,
):
    payload = b"PK\x03\x04" + b"x" * 96
    apk = tmp_path / "UTHelper-2.5.1.apk"
    apk.write_bytes(payload)
    mismatched = _android_package(
        signer_identity="com.uthelper.uthelper",
        size=len(payload),
    )
    candidate = _android_candidate(mismatched, automatic_install_allowed=False)

    result = AndroidPackageVerifier().verify(apk, candidate)

    assert result.verified is False
    assert result.reason == "package_mismatch"


@pytest.mark.parametrize(
    "native_response",
    [
        {"status": "failed", "stage": "archive_metadata_validation", "reason": "package_mismatch"},
        {"status": "failed", "stage": "archive_metadata_validation", "reason": "APK package ID mismatch"},
        {"status": "failed", "stage": "archive_metadata_validation", "reason": "package ID mismatch"},
        {"status": "failed", "stage": "release_metadata_validation", "reason": "Unexpected package identity"},
        {"status": "package_mismatch"},
    ],
)
def test_pkg01_android_launcher_normalizes_native_package_mismatch_to_manual_release_fallback(
    tmp_path: Path,
    native_response: dict,
):
    package = _android_package(signer_identity=PACKAGE_ID)
    candidate = _android_candidate(package, automatic_install_allowed=True)

    async def install_update(*_args):
        return native_response

    launcher = AndroidPackageLauncher(
        SimpleNamespace(available=True, install_update=install_update),
        _run_task,
        lambda: "2.5.1",
    )

    result = launcher.launch(tmp_path / "UTHelper-2.5.1.apk", package)

    assert result.acknowledged is False
    assert result.status == "failed"
    assert result.reason == "package_mismatch"

    fallback = android_updates.resolve_android_manual_fallback(
        candidate, install_result=result
    )
    assert isinstance(fallback, android_updates.AndroidManualDownloadFallback)
    assert fallback.manual_download_required is True
    assert fallback.release_url == candidate.manifest.release_notes_url
    assert fallback.expected_package_id == PACKAGE_ID


def test_pkg01_android_launcher_rejects_mismatched_signer_without_calling_native_bridge(
    tmp_path: Path,
):
    invoked: list[tuple] = []

    async def install_update(*args):
        invoked.append(args)
        return {"status": "installer_opened"}

    mismatched = _android_package(signer_identity="com.uthelper.uthelper")
    launcher = AndroidPackageLauncher(
        SimpleNamespace(available=True, install_update=install_update),
        _run_task,
        lambda: "2.5.1",
    )

    result = launcher.launch(tmp_path / "UTHelper-2.5.1.apk", mismatched)

    assert invoked == []
    assert result == LaunchResult(False, "package_mismatch", status="failed")


def test_pkg01_coordinator_and_controller_switch_to_release_page_on_native_package_id_mismatch(
    tmp_path: Path,
):
    from gui.app_controller import AppController

    package = _android_package(signer_identity=PACKAGE_ID, size=8)
    candidate = _android_candidate(package, automatic_install_allowed=True)
    apk_path = tmp_path / "UTHelper-2.5.1.apk"
    apk_path.write_bytes(b"PK\x03\x04test")

    async def native_install(*_args):
        return {
            "status": "failed",
            "stage": "archive_metadata_validation",
            "reason": "APK package ID mismatch",
        }

    launcher = AndroidPackageLauncher(
        SimpleNamespace(available=True, install_update=native_install),
        _run_task,
        lambda: "2.5.1",
    )

    opened_urls: list[str] = []
    controller = AppController.__new__(AppController)
    controller._distribution_profile = SimpleNamespace(supports_github_self_update=True)
    controller._update_candidate = candidate
    controller._update_ready = False
    controller._update_manual_download = False
    controller._update_open_release_page = False
    controller._manual_update_check_requested = False
    controller._update_banner = SimpleNamespace(visible=False)
    controller._update_text = SimpleNamespace(value="")
    controller._update_btn = SimpleNamespace(visible=True, disabled=False, content="Cập nhật", icon=None)
    controller._update_progress = SimpleNamespace(visible=False, value=0.0)
    controller._update_cancel_btn = SimpleNamespace(visible=False)
    controller.settings_view = SimpleNamespace(_check_update_btn=None)
    controller.page = SimpleNamespace(update=lambda: None)
    controller._diag_breadcrumb = lambda *_a, **_kw: None
    controller._open_external_update_url = lambda url: opened_urls.append(url) or True
    controller._safe_run_task = lambda coro, *a, **kw: asyncio.run(coro(*a, **kw))

    ready_ev = threading.Event()
    failed_ev = threading.Event()

    def sink(ev: UpdateEvent):
        controller._on_update_event(ev)
        if ev.kind is UpdateEventKind.READY_TO_INSTALL:
            ready_ev.set()
        elif ev.kind is UpdateEventKind.INSTALL_FAILED:
            failed_ev.set()

    class _FakeDownloader:
        def __init__(self):
            self.calls = 0

        def download(self, _pkg, *, cancel, progress=None):
            del cancel, progress
            self.calls += 1
            return apk_path

    class _FakeVerifier:
        def verify(self, _path, _cand):
            return VerificationResult(True)

    class _FakeClient:
        def fetch_candidate(self, _v, _t, *, cancel=None):
            del cancel
            return candidate

    downloader = _FakeDownloader()
    coordinator = UpdateCoordinator(
        _FakeClient(),
        downloader,
        _FakeVerifier(),
        launcher,
        RuntimeTarget("android", "arm64", "sideload"),
        "2.5.0",
        sink,
        automatic_enabled=False,
    )
    controller._update_coordinator = coordinator
    try:
        coordinator.request_download(candidate)
        assert ready_ev.wait(2.0)
        assert downloader.calls == 1

        coordinator.confirm_install()
        assert failed_ev.wait(2.0)

        assert not apk_path.exists()
        assert controller._update_open_release_page is True
        assert controller._update_ready is False
        assert controller._update_btn.content == "Bản phát hành"

        # Clicking the button opens GitHub Releases rather than re-downloading.
        controller._request_update_download()
        assert opened_urls == [candidate.manifest.release_notes_url]
        assert downloader.calls == 1
    finally:
        assert coordinator.shutdown(timeout_seconds=1.0) is True


def test_pkg01_android_updates_respects_dynamic_package_id_monkeypatch(monkeypatch):
    monkeypatch.setattr(android_updates, "PACKAGE_ID", "com.uthelper.custom")
    pkg = _android_package(signer_identity="io.github.chouwzi.uthelper")
    candidate = _android_candidate(pkg, automatic_install_allowed=False)

    fallback = android_updates.resolve_android_manual_fallback(candidate)
    assert fallback is not None
    assert fallback.expected_package_id == "com.uthelper.custom"
    assert fallback.actual_package_id == "io.github.chouwzi.uthelper"
    assert fallback.release_url == candidate.manifest.release_notes_url


def test_pkg01_schema2_android_preserves_legacy_compat_unless_expected_signer_specified():
    schema2_doc = {
        "schema_version": 2,
        "release_version": "2.5.1",
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-11-25T00:00:00Z",
        "release_notes_url": "https://github.com/Chouwzi/UTHelper/releases/tag/v2.5.1",
        "packages": [
            {
                "platform": "android",
                "architecture": "universal",
                "package_type": "apk",
                "install_channel": "sideload",
                "url": "https://github.com/Chouwzi/UTHelper/releases/download/v2.5.1/UTHelper-2.5.1.apk",
                "sha256": "a" * 64,
                "size": 123,
                "signer_identity": "CN=UTHelper Android",
                "certificate_fingerprint": "B" * 64,
                "install_strategy": {"kind": "android_package_installer"},
            }
        ],
    }
    manifest = parse_manifest(schema2_doc, expected_release_version="2.5.1")
    target = RuntimeTarget("android", "universal", "sideload")

    default_cand = select_candidate(manifest, current_version="2.5.0", target=target)
    assert default_cand is not None
    assert default_cand.automatic_install_allowed is True

    strict_cand = select_candidate(
        manifest,
        current_version="2.5.0",
        target=target,
        expected_signer_identity=PACKAGE_ID,
    )
    assert strict_cand is not None
    assert strict_cand.automatic_install_allowed is False


def test_pkg01_android_launcher_does_not_rewrite_unrelated_native_errors(tmp_path: Path):
    package = _android_package(signer_identity=PACKAGE_ID)
    candidate = _android_candidate(package, automatic_install_allowed=True)

    async def install_update(*_args):
        return {"status": "failed", "stage": "downloaded_package_verification", "reason": "sha256_mismatch"}

    launcher = AndroidPackageLauncher(
        SimpleNamespace(available=True, install_update=install_update),
        _run_task,
        lambda: "2.5.1",
    )

    result = launcher.launch(tmp_path / "UTHelper-2.5.1.apk", package)

    assert result == LaunchResult(False, "sha256_mismatch", status="failed")
    assert android_updates.resolve_android_manual_fallback(candidate, install_result=result) is None


# ===========================================================================
# GATE-01: Robolectric Google Maven Central mirror in Android build.gradle
# ===========================================================================


def test_gate01_build_gradle_pins_robolectric_dependency_repo_to_google_mirror():
    gradle = BUILD_GRADLE.read_text(encoding="utf-8")

    match = re.search(
        r"tasks\.withType\(org\.gradle\.api\.tasks\.testing\.Test\)\.configureEach\s*\{(?P<body>.*?)\n\}",
        gradle,
        re.DOTALL,
    )
    assert match is not None, "Test task configureEach block missing from build.gradle"
    test_block = match.group("body")

    assert (
        "systemProperty 'robolectric.dependency.repo.url', "
        "'https://maven-central.storage-download.googleapis.com/maven2'"
    ) in test_block
    assert gradle.count("{") == gradle.count("}")


# ===========================================================================
# PKG-02: Google Play Console App access verbatim reviewer instructions
# ===========================================================================


def test_pkg02_play_console_app_access_block_matches_real_login_and_nav_ui():
    guide = STORE_GUIDE.read_text(encoding="utf-8")
    section = guide.split("### Play Console `App access`", 1)[1].split(
        "### Data Safety Flow Review", 1
    )[0]
    code_match = re.search(r"```text\r?\n(.*?)\r?\n```", section, re.DOTALL)
    assert code_match is not None, "Verbatim App access ```text block missing"
    verbatim = code_match.group(1)

    login_source = LOGIN_DIALOG.read_text(encoding="utf-8")
    assert is_demo_credentials("uthelper_demo", "UTH-Review-2026!") is True

    # Real labels present in both login_dialog.py and verbatim Play Console block.
    for label in (
        "Đăng nhập UTH",
        "Mã số sinh viên (MSSV)",
        "Mật khẩu",
        "Đăng nhập",
    ):
        assert f'"{label}"' in login_source
        assert f'"{label}"' in verbatim

    assert "uthelper_demo" in verbatim
    assert "UTH-Review-2026!" in verbatim

    # All 5 real top-level tabs from AdaptiveNavShell must match verbatim.
    for spec in NAV_DESTINATIONS:
        assert f'"{spec.label}"' in verbatim

    # Non-existent first-run Terms & Privacy dialog must be removed.
    assert "Đồng ý & Tiếp tục" not in verbatim
    assert "Điều khoản & Quyền riêng tư" not in verbatim


# ===========================================================================
# PKG-03 & PKG-04: Migration docs, ADR 0004 pure-Dart quiz fix, uv.lock ignore
# ===========================================================================


def test_pkg03_store_guide_and_adr0004_document_android_lts_and_ios_sideload_migrations():
    guide = STORE_GUIDE.read_text(encoding="utf-8")
    adr = ADR_0004.read_text(encoding="utf-8")

    for doc_name, text in (("store-distribution-submission.md", guide), ("ADR 0004", adr)):
        assert "Android đã từ chối gói cập nhật" in text, f"{doc_name} missing legacy apk_lts error"
        assert "com.uthelper.uthelper" in text, f"{doc_name} missing legacy Android package ID"
        assert "v2.5.1" in text, f"{doc_name} missing v2.5.1 release notes guidance"
        assert "com.uthelper.UTHelper" in text, f"{doc_name} missing legacy iOS CFBundleIdentifier"
        assert "Sideloadly" in text and "AltStore" in text, f"{doc_name} missing iOS sideload tools"
        assert "Keychain" in text, f"{doc_name} missing iOS Keychain partition note"
        assert "3-app" in text, f"{doc_name} missing free Apple ID 3-app cap note"


def test_pkg04_adr0004_does_not_claim_flet_uth_quiz_has_kotlin_namespace():
    assert not (QUIZ_FLUTTER_DIR / "android").exists()
    adr = ADR_0004.read_text(encoding="utf-8")
    assert "com.uthelper.quiz" not in adr
    assert "flet_uth_quiz" in adr
    assert "pure Dart" in adr


def test_pkg04_gitignore_ignores_uv_lock():
    proc = subprocess.run(
        ["git", "check-ignore", "-v", "uv.lock"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Expected uv.lock to be gitignored; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "uv.lock" in proc.stdout
