"""Tests for multi-channel and multi-environment Sentry telemetry (Task 5 / v2.7.0)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest

from core.distribution import (
    DISTRIBUTION_CHANNEL_FILENAME,
    DistributionChannel,
    build_distribution_profile,
    resolve_distribution_channel,
)
from diagnostics.models import (
    AppPhase,
    CrashConsent,
    DiagnosticContext,
    DiagnosticFrame,
    DiagnosticReport,
)
from diagnostics.redaction import build_report
from diagnostics.release_config import (
    PUBLIC_CONFIG_FILENAME,
    PublicConfigError,
    PublicDiagnosticsConfig,
    VALID_BUILD_ENVIRONMENTS,
    VALID_PACKAGE_FORMATS,
    generate_public_config,
    load_public_config,
    load_public_dsn,
    load_runtime_public_config,
    load_runtime_public_dsn,
)
from diagnostics.runtime import DiagnosticRuntime, create_default_runtime
from diagnostics.spool import DiagnosticSpool
from diagnostics.transport import _strict_before_send
from scripts.generate_public_runtime_config import main as generate_config_main
from scripts.release_metadata import (
    VALID_DISTRIBUTION_CHANNELS,
    build_distribution_channel_payload,
    write_distribution_channel,
)

ROOT = Path(__file__).resolve().parents[1]
VALID_DSN = "https://0123456789abcdef@o123.ingest.sentry.io/456"
NOW = datetime(2026, 11, 28, 2, 5, tzinfo=UTC)


def test_distribution_channel_supports_ipa_lts_and_packaged_ios_fallback(
    tmp_path: Path,
) -> None:
    assert DistributionChannel.IPA_LTS.value == "ipa_lts"
    assert DistributionChannel("ipa_lts") is DistributionChannel.IPA_LTS
    assert "ipa_lts" in VALID_DISTRIBUTION_CHANNELS

    profile = build_distribution_profile(DistributionChannel.IPA_LTS)
    assert profile.channel is DistributionChannel.IPA_LTS
    assert profile.supports_github_self_update is True
    assert profile.store_display_name == "GitHub Releases"
    assert profile.store_url is None
    assert profile.store_web_url is None

    payload = build_distribution_channel_payload("ipa_lts")
    assert payload == {"schema_version": 1, "channel": "ipa_lts"}

    cfg_path = write_distribution_channel(
        tmp_path / DISTRIBUTION_CHANNEL_FILENAME, "ipa_lts"
    )
    loaded_profile = resolve_distribution_channel(
        config_path=cfg_path,
        is_windows=False,
        is_android=False,
        is_ios=True,
    )
    assert loaded_profile.channel is DistributionChannel.IPA_LTS
    assert loaded_profile.supports_github_self_update is True

    missing_cfg = tmp_path / "missing-distribution-channel.json"
    packaged_ios = resolve_distribution_channel(
        config_path=missing_cfg,
        is_windows=False,
        is_android=False,
        is_ios=True,
        is_packaged=True,
    )
    assert packaged_ios.channel is DistributionChannel.IPA_LTS
    assert packaged_ios.supports_github_self_update is True

    source_ios = resolve_distribution_channel(
        config_path=missing_cfg,
        is_windows=False,
        is_android=False,
        is_ios=True,
        is_packaged=False,
    )
    assert source_ios.channel is DistributionChannel.SOURCE

    bundle_without_pyproject = resolve_distribution_channel(
        config_path=missing_cfg,
        is_windows=False,
        is_android=False,
        is_ios=True,
        pyproject_path=tmp_path / "absent-pyproject.toml",
    )
    assert bundle_without_pyproject.channel is DistributionChannel.IPA_LTS


def test_public_config_schema_v2_roundtrip_and_legacy_schema_v1_backward_compat(
    tmp_path: Path,
) -> None:
    assert VALID_BUILD_ENVIRONMENTS == frozenset(
        {"release", "develop", "development"}
    )
    assert VALID_PACKAGE_FORMATS == frozenset(
        {"aab", "apk", "msix", "exe_msi", "exe", "msi", "ipa", "ios_app", "source"}
    )

    # 2-arg call keeps exact 2-key schema 1 for backward compatibility
    v1_path = tmp_path / "v1" / PUBLIC_CONFIG_FILENAME
    generate_public_config(v1_path, VALID_DSN)
    assert json.loads(v1_path.read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "sentry_dsn": VALID_DSN,
    }
    v1_loaded = load_public_config(v1_path, development=False, environ={})
    assert v1_loaded == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="release",
        package_format=None,
    )
    assert load_public_dsn(v1_path, development=False, environ={}) == VALID_DSN

    # 4-arg call writes sorted 4-key schema 2
    v2_path = tmp_path / "v2" / PUBLIC_CONFIG_FILENAME
    generate_public_config(
        v2_path,
        VALID_DSN,
        build_environment="develop",
        package_format="apk",
    )
    raw_v2 = v2_path.read_text(encoding="utf-8")
    assert raw_v2 == (
        '{"build_environment":"develop","package_format":"apk",'
        f'"schema_version":2,"sentry_dsn":{json.dumps(VALID_DSN)}}}\n'
    )
    v2_loaded = load_runtime_public_config(
        development=False,
        environ={"FLET_ASSETS_DIR": str(v2_path.parent)},
    )
    assert v2_loaded == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="develop",
        package_format="apk",
    )
    assert (
        load_runtime_public_dsn(
            development=False,
            environ={"FLET_ASSETS_DIR": str(v2_path.parent)},
        )
        == VALID_DSN
    )

    # Unconfigured v2 preserves build_environment and package_format with dsn=None
    v2_empty = tmp_path / "v2-empty" / PUBLIC_CONFIG_FILENAME
    assert (
        generate_config_main(
            [
                "--sentry-dsn",
                "",
                "--output",
                str(v2_empty),
                "--build-environment",
                "develop",
                "--package-format",
                "ios_app",
            ]
        )
        == 0
    )
    assert load_public_config(v2_empty, development=False, environ={}) == (
        PublicDiagnosticsConfig(
            dsn=None,
            build_environment="develop",
            package_format="ios_app",
        )
    )

    # Development override resolves development/source
    dev_cfg = load_public_config(
        tmp_path / "missing.json",
        development=True,
        environ={"UTH_SENTRY_DSN": VALID_DSN},
    )
    assert dev_cfg == PublicDiagnosticsConfig(
        dsn=VALID_DSN,
        build_environment="development",
        package_format="source",
    )

    # Incomplete or invalid schema 2 fails closed
    for bad_payload in (
        {"schema_version": 2, "sentry_dsn": VALID_DSN},
        {
            "schema_version": 2,
            "sentry_dsn": VALID_DSN,
            "build_environment": "staging",
            "package_format": "apk",
        },
        {
            "schema_version": 2,
            "sentry_dsn": VALID_DSN,
            "build_environment": "develop",
            "package_format": "deb",
        },
        {
            "schema_version": 2,
            "sentry_dsn": VALID_DSN,
            "build_environment": "develop",
            "package_format": "apk",
            "extra": "nope",
        },
        {
            "schema_version": 2,
            "sentry_dsn": "https://sntrys_secret@o123.ingest.sentry.io/456",
            "build_environment": "release",
            "package_format": "apk",
        },
    ):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text(json.dumps(bad_payload), encoding="utf-8")
        failed = load_public_config(bad_file, development=False, environ={})
        assert failed.dsn is None
        assert load_public_dsn(bad_file, development=False, environ={}) is None

    # Generator rejects partial or invalid v2 arguments
    for bad_kwargs in (
        {"build_environment": "release", "package_format": None},
        {"build_environment": None, "package_format": "apk"},
        {"build_environment": "invalid", "package_format": "apk"},
        {"build_environment": "release", "package_format": "invalid"},
    ):
        with pytest.raises(PublicConfigError):
            generate_public_config(
                tmp_path / "rejected.json", VALID_DSN, **bad_kwargs
            )


@pytest.mark.parametrize(
    (
        "configured_channel",
        "configured_format",
        "configured_env",
        "development",
        "win_install_channel",
        "is_win",
        "is_android",
        "is_ios",
        "expected_env",
        "expected_channel",
        "expected_format",
    ),
    [
        (
            "chplay",
            "aab",
            "release",
            False,
            None,
            False,
            True,
            False,
            "release",
            "chplay",
            "aab",
        ),
        (
            "msstore",
            "msix",
            "release",
            False,
            None,
            True,
            False,
            False,
            "release",
            "msstore",
            "msix",
        ),
        (
            "apk_lts",
            "apk",
            "develop",
            False,
            None,
            False,
            True,
            False,
            "develop",
            "apk_lts",
            "apk",
        ),
        (
            "exe_lts",
            "exe_msi",
            "release",
            False,
            "bootstrapper",
            True,
            False,
            False,
            "release",
            "exe_lts",
            "exe",
        ),
        (
            "exe_lts",
            "exe_msi",
            "release",
            False,
            "msi",
            True,
            False,
            False,
            "release",
            "exe_lts",
            "msi",
        ),
        (
            "exe_lts",
            "exe_msi",
            "develop",
            False,
            None,
            True,
            False,
            False,
            "develop",
            "exe_lts",
            "exe_msi",
        ),
        (
            "ipa_lts",
            "ipa",
            "release",
            False,
            None,
            False,
            False,
            True,
            "release",
            "ipa_lts",
            "ipa",
        ),
        (
            "ipa_lts",
            "ios_app",
            "develop",
            False,
            None,
            False,
            False,
            True,
            "develop",
            "ipa_lts",
            "ios_app",
        ),
        (
            None,
            None,
            None,
            True,
            None,
            True,
            False,
            False,
            "development",
            "source",
            "source",
        ),
        (
            "exe_lts",
            None,
            "schema_v1",
            False,
            "msi",
            True,
            False,
            False,
            "release",
            "exe_lts",
            "msi",
        ),
    ],
)
def test_create_default_runtime_resolves_exact_channel_and_refines_windows_exe_vs_msi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_channel: str | None,
    configured_format: str | None,
    configured_env: str | None,
    development: bool,
    win_install_channel: str | None,
    is_win: bool,
    is_android: bool,
    is_ios: bool,
    expected_env: str,
    expected_channel: str,
    expected_format: str,
) -> None:
    import platform_utils
    import platform_utils.autostart as autostart_mod
    import platform_utils.windows_update as windows_update_mod

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLET_ASSETS_DIR", str(assets_dir))
    if development:
        monkeypatch.setenv("UTH_SENTRY_DSN", VALID_DSN)
    elif configured_env == "schema_v1":
        monkeypatch.delenv("UTH_SENTRY_DSN", raising=False)
        generate_public_config(assets_dir / PUBLIC_CONFIG_FILENAME, VALID_DSN)
        assert configured_channel is not None
        write_distribution_channel(
            assets_dir / DISTRIBUTION_CHANNEL_FILENAME,
            configured_channel,
        )
    else:
        monkeypatch.delenv("UTH_SENTRY_DSN", raising=False)
        assert configured_env is not None and configured_format is not None
        generate_public_config(
            assets_dir / PUBLIC_CONFIG_FILENAME,
            VALID_DSN,
            build_environment=configured_env,
            package_format=configured_format,
        )
        assert configured_channel is not None
        write_distribution_channel(
            assets_dir / DISTRIBUTION_CHANNEL_FILENAME,
            configured_channel,
        )

    monkeypatch.setattr(platform_utils, "IS_WINDOWS", is_win)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", is_android)
    monkeypatch.setattr(platform_utils, "IS_IOS", is_ios)
    monkeypatch.setattr(
        autostart_mod,
        "has_package_identity",
        lambda: configured_channel == "msstore",
    )
    monkeypatch.setattr(
        windows_update_mod,
        "read_install_channel",
        lambda: win_install_channel,
    )

    runtime = create_default_runtime(tmp_path / "data", development=development)
    try:
        ctx = runtime.context(AppPhase.GUI)
        assert ctx.release_channel == expected_env
        assert ctx.distribution_channel == expected_channel
        assert ctx.package_format == expected_format
        assert ctx.install_type == expected_channel

        report = build_report(ValueError("private-student-secret"), ctx)
        assert report.release_channel == expected_env
        assert report.distribution_channel == expected_channel
        assert report.package_format == expected_format
        assert report.install_type == expected_channel

        round_tripped = DiagnosticReport.model_validate_json(
            report.model_dump_json()
        )
        assert round_tripped.distribution_channel == expected_channel
        assert round_tripped.package_format == expected_format

        event = _strict_before_send({}, {"report": round_tripped})
        assert event is not None
        assert event["environment"] == expected_env
        assert (
            event["dist"]
            == f"{expected_channel}-{expected_format}-{report.architecture}"
        )
        assert event["tags"]["distribution_channel"] == expected_channel
        assert event["tags"]["package_format"] == expected_format
        assert event["tags"]["build_track"] == expected_env
        assert event["tags"]["install_type"] == expected_channel
        assert event["contexts"]["app"]["distribution_channel"] == expected_channel
        assert event["contexts"]["app"]["package_format"] == expected_format
        assert "private-student-secret" not in repr(event)
    finally:
        runtime.close(clean=True)


def test_legacy_report_without_channel_or_format_keeps_exact_json_and_event_keys() -> None:
    legacy = DiagnosticReport(
        schema_version=2,
        event_id=UUID("00000000-0000-0000-0000-000000000999"),
        fingerprint="b" * 64,
        occurred_at=NOW,
        app_version="2.6.3",
        release_channel="stable",
        install_type="msi",
        os_family="Windows",
        os_version="11",
        architecture="AMD64",
        python_version="3.13.5",
        flet_version="0.86.5",
        exception_type="ValueError",
        frames=(
            DiagnosticFrame(
                module="gui.app_controller",
                function="load",
                relative_path="gui/app_controller.py",
                line=42,
            ),
        ),
        phase=AppPhase.GUI,
        window_state="foreground",
        unclean_previous_exit=False,
    )
    assert legacy.distribution_channel is None
    assert legacy.package_format is None
    dumped = legacy.model_dump()
    assert "distribution_channel" not in dumped
    assert "package_format" not in dumped

    event = _strict_before_send({}, {"report": legacy})
    assert event is not None
    assert "dist" not in event
    assert "distribution_channel" not in event["tags"]
    assert "package_format" not in event["tags"]
    assert "build_track" not in event["tags"]
    assert "distribution_channel" not in event["contexts"]["app"]
    assert "package_format" not in event["contexts"]["app"]


def test_diagnostic_runtime_ignores_uth_maintenance_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    @dataclass(frozen=True, slots=True)
    class _FakeNotice:
        source: str = "portal"
        window_text: str = "01h00 - 04h00 (giờ Việt Nam)"
        reopen_text: str = "04h00 ngày 28/11/2026"
        raw_title: str = "Hệ thống tạm ngưng"
        dedupe_key: str = "2026-11-28|01h00-04h00|04h00"

    class UthMaintenanceError(RuntimeError):
        def __init__(self, notice: object) -> None:
            super().__init__("UTH maintenance")
            self.notice = notice

    class PortalMaintenanceError(UthMaintenanceError):
        pass

    chained_thread = Mock()
    monkeypatch.setattr(threading, "excepthook", chained_thread)

    spool = DiagnosticSpool(tmp_path / "telemetry" / "pending", clock=lambda: NOW)
    runtime = DiagnosticRuntime(
        data_dir=tmp_path,
        spool=spool,
        delivery=SimpleNamespace(flush_once=lambda _c: None),
        context_provider=lambda phase, unclean: DiagnosticContext(
            source_root=ROOT / "src",
            app_version="2.7.0",
            release_channel="release",
            install_type="exe_lts",
            distribution_channel="exe_lts",
            package_format="exe",
            os_family="Windows",
            os_version="11",
            architecture="AMD64",
            python_version="3.13.5",
            flet_version="0.86.5",
            phase=phase,
            window_state="foreground",
            unclean_previous_exit=unclean,
        ),
        consent_provider=lambda: CrashConsent.ENABLED,
        evidence_reader=lambda **_kw: (),
        clock=lambda: NOW,
    )

    chained_page = Mock()
    page = SimpleNamespace(on_error=chained_page)
    loop = asyncio.new_event_loop()
    chained_async = Mock()
    loop.set_exception_handler(chained_async)

    runtime.start()
    try:
        runtime.attach_page(page)
        runtime.attach_asyncio_loop(loop)

        notice = _FakeNotice()
        assert runtime.record_exception(UthMaintenanceError(notice)) is None
        assert runtime.record_exception(PortalMaintenanceError(notice)) is None

        # Also test real core.maintenance.UthMaintenanceError when integrated
        try:
            from core.maintenance import (  # type: ignore[import-not-found]
                MaintenanceNotice,
                UthMaintenanceError as RealUthMaintenanceError,
            )
        except ImportError:
            pass
        else:
            real_notice = MaintenanceNotice(
                source="courses",
                window_text="01h00 - 04h00 (giờ Việt Nam)",
                reopen_text="04h00 ngày 28/11/2026",
                dedupe_key="2026-11-28|01h00-04h00|04h00",
            )
            assert (
                runtime.record_exception(RealUthMaintenanceError(real_notice))
                is None
            )

        # Global, Flet page, and asyncio hooks also ignore maintenance while still chaining
        page.on_error(SimpleNamespace(exception=UthMaintenanceError(notice)))
        loop.get_exception_handler()(
            loop, {"message": "maintenance", "exception": PortalMaintenanceError(notice)}
        )
        threading.excepthook(
            SimpleNamespace(
                exc_type=UthMaintenanceError,
                exc_value=UthMaintenanceError(notice),
                exc_traceback=None,
                thread=threading.current_thread(),
            )
        )
        assert spool.pending() == ()
        assert chained_page.call_count == 1
        assert chained_async.call_count == 1
        assert chained_thread.call_count == 1

        # Genuine application crashes are still recorded normally
        ref = runtime.record_exception(RuntimeError("real crash"), AppPhase.GUI)
        assert ref is not None
        assert len(spool.pending()) == 1
    finally:
        runtime.close(clean=True)
        loop.close()


def test_workflows_pass_explicit_build_environment_channel_and_package_format_on_single_lines() -> None:
    targets = (
        ".github/workflows/release.yml",
        ".github/workflows/build-android.yml",
        ".github/workflows/build-ios.yml",
        ".github/workflows/build-msstore.yml",
        ".github/workflows/package-benchmark.yml",
        "scripts/build_android.ps1",
    )
    generator = "generate_public_runtime_config.py"

    for rel in targets:
        content = (ROOT / rel).read_text(encoding="utf-8")
        lines = content.splitlines()
        invocations = [line for line in lines if generator in line]
        assert invocations, f"Expected generator call in {rel}"
        for line in invocations:
            stripped = line.rstrip()
            assert not stripped.endswith(("\\", "`")), (
                f"{rel} must keep generate_public_runtime_config.py on a single line: {line}"
            )
            assert "--build-environment" in line, f"Missing --build-environment in {rel}: {line}"
            assert "--package-format" in line, f"Missing --package-format in {rel}: {line}"

    release_yml = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "--build-environment release --package-format apk" in release_yml
    assert (
        "--build-environment release --package-format ipa "
        "--distribution-channel ipa_lts --distribution-output src/assets/distribution-channel.json"
    ) in release_yml
    assert (
        "--build-environment release --package-format exe_msi "
        "--distribution-channel exe_lts --distribution-output src/assets/distribution-channel.json"
    ) in release_yml
    sentry_step = release_yml.split("uses: getsentry/action-release@", 1)[1]
    assert "environment: release" in sentry_step
    assert "environment: stable" not in sentry_step

    android_yml = (ROOT / ".github/workflows/build-android.yml").read_text(encoding="utf-8")
    assert '--build-environment "$BUILD_ENV"' in android_yml
    assert '--package-format "$BUILD_TARGET"' in android_yml
    assert "refs/heads/main" in android_yml
    assert "environment: release" not in android_yml

    msstore_yml = (ROOT / ".github/workflows/build-msstore.yml").read_text(encoding="utf-8")
    assert '--build-environment "$buildEnv"' in msstore_yml
    assert "--package-format msix" in msstore_yml
    assert "--distribution-channel msstore --distribution-output src/assets/distribution-channel.json" in msstore_yml

    ios_yml = (ROOT / ".github/workflows/build-ios.yml").read_text(encoding="utf-8")
    assert (
        "--build-environment develop --package-format ios_app "
        "--distribution-channel ipa_lts --distribution-output src/assets/distribution-channel.json"
    ) in ios_yml

    android_ps1 = (ROOT / "scripts/build_android.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("release", "develop", "development")]' in android_ps1
    assert "--build-environment $BuildEnvironment --package-format $Target" in android_ps1
