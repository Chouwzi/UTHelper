"""Tests for distribution channel resolution, platform target integration, and Store terms."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import flet as ft
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from core.distribution import (
    DEFAULT_ANDROID_PACKAGE_ID,
    DISTRIBUTION_CHANNEL_FILENAME,
    DistributionChannel,
    build_distribution_profile,
    resolve_distribution_channel,
)
import gui.components.terms_dialog as terms_dialog_module
import platform_utils.update_packages as update_packages
import platform_utils.windows_update as windows_update


@pytest.mark.parametrize(
    ("channel", "expected_self_update"),
    [
        (DistributionChannel.CHPLAY, False),
        (DistributionChannel.MSSTORE, False),
        (DistributionChannel.APK_LTS, True),
        (DistributionChannel.EXE_LTS, True),
        (DistributionChannel.SOURCE, True),
    ],
)
def test_distribution_channel_self_update_policy(channel, expected_self_update):
    profile = build_distribution_profile(channel)
    assert profile.channel is channel
    assert profile.supports_github_self_update is expected_self_update


def test_chplay_profile_urls_and_custom_package_id():
    assert DEFAULT_ANDROID_PACKAGE_ID == "io.github.chouwzi.uthelper"
    default_profile = build_distribution_profile(DistributionChannel.CHPLAY)
    assert default_profile.android_package_id == DEFAULT_ANDROID_PACKAGE_ID
    assert default_profile.store_url == f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}"
    assert (
        default_profile.store_web_url
        == f"https://play.google.com/store/apps/details?id={DEFAULT_ANDROID_PACKAGE_ID}"
    )
    assert default_profile.store_fallback_url == default_profile.store_web_url
    assert default_profile.store_display_name == "Google Play"

    custom = build_distribution_profile(
        DistributionChannel.CHPLAY,
        android_package_id="com.example.uthelper",
    )
    assert custom.android_package_id == "com.example.uthelper"
    assert custom.store_url == "market://details?id=com.example.uthelper"
    assert custom.store_fallback_url == "https://play.google.com/store/apps/details?id=com.example.uthelper"


def test_msstore_profile_with_and_without_product_id():
    without_id = build_distribution_profile(DistributionChannel.MSSTORE)
    assert without_id.supports_github_self_update is False
    assert without_id.store_url is None
    assert without_id.store_web_url is None
    assert without_id.store_fallback_url is None
    assert without_id.store_display_name == "Microsoft Store"

    with_id = build_distribution_profile(
        DistributionChannel.MSSTORE,
        store_product_id="9NBLGGH4NNS1",
    )
    assert with_id.store_product_id == "9NBLGGH4NNS1"
    assert with_id.store_url == "ms-windows-store://pdp/?ProductId=9NBLGGH4NNS1"
    assert with_id.store_web_url == "https://apps.microsoft.com/detail/9NBLGGH4NNS1"
    assert with_id.store_fallback_url == "https://apps.microsoft.com/detail/9NBLGGH4NNS1"


def test_resolve_reads_flet_assets_dir_config_for_all_channels(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    config_file = assets_dir / DISTRIBUTION_CHANNEL_FILENAME
    env = {"FLET_ASSETS_DIR": str(assets_dir)}

    config_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "CHPLAY",
                "android_package_id": "com.uthelper.play",
            }
        ),
        encoding="utf-8",
    )
    chplay = resolve_distribution_channel(
        environ=env, is_windows=False, is_android=True, is_ios=False
    )
    assert chplay.channel is DistributionChannel.CHPLAY
    assert chplay.supports_github_self_update is False
    assert chplay.store_url == "market://details?id=com.uthelper.play"

    config_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "channel": "msstore",
                "store_product_id": "9WZDNCRFJ3TJ",
            }
        ),
        encoding="utf-8",
    )
    msstore = resolve_distribution_channel(
        environ=env,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )
    assert msstore.channel is DistributionChannel.MSSTORE
    assert msstore.supports_github_self_update is False
    assert msstore.store_url == "ms-windows-store://pdp/?ProductId=9WZDNCRFJ3TJ"

    for channel_value, expected in (
        ("apk_lts", DistributionChannel.APK_LTS),
        ("exe_lts", DistributionChannel.EXE_LTS),
        ("source", DistributionChannel.SOURCE),
    ):
        config_file.write_text(
            json.dumps({"schema_version": 1, "channel": channel_value}),
            encoding="utf-8",
        )
        profile = resolve_distribution_channel(
            environ=env,
            is_windows=False,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: False,
        )
        assert profile.channel is expected
        assert profile.supports_github_self_update is True


@pytest.mark.parametrize(
    "bad_pkg",
    [
        "io.github.chouwzi.uthelper&referrer=evil",
        "io.github.chouwzi.uthelper#frag",
        "singleword",
        "123.invalid.pkg",
        "io.github.chouwzi/../../etc",
    ],
)
def test_uri_injection_in_android_package_id_falls_back_to_default(tmp_path, bad_pkg):
    config_file = tmp_path / DISTRIBUTION_CHANNEL_FILENAME
    config_file.write_text(
        json.dumps({"channel": "chplay", "android_package_id": bad_pkg}),
        encoding="utf-8",
    )
    profile = resolve_distribution_channel(
        config_path=config_file,
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    assert profile.channel is DistributionChannel.CHPLAY
    assert profile.android_package_id == DEFAULT_ANDROID_PACKAGE_ID
    assert profile.store_url == f"market://details?id={DEFAULT_ANDROID_PACKAGE_ID}"


@pytest.mark.parametrize(
    "bad_pid",
    [
        "9NBLGGH4NNS1&evil=1",
        "short",
        "../../etc/passwd",
        "9NBLGGH4-NNS1",
    ],
)
def test_uri_injection_in_store_product_id_is_rejected(tmp_path, bad_pid):
    config_file = tmp_path / DISTRIBUTION_CHANNEL_FILENAME
    config_file.write_text(
        json.dumps({"channel": "msstore", "store_product_id": bad_pid}),
        encoding="utf-8",
    )
    profile = resolve_distribution_channel(
        config_path=config_file,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: False,
    )
    assert profile.channel is DistributionChannel.MSSTORE
    assert profile.store_product_id is None
    assert profile.store_url is None
    assert profile.store_web_url is None


@pytest.mark.parametrize(
    "payload_text",
    [
        "{not-json",
        json.dumps(["not", "an", "object"]),
        json.dumps({"channel": "chplay", "unexpected": True}),
        json.dumps({"schema_version": 2, "channel": "chplay"}),
        json.dumps({"schema_version": True, "channel": "chplay"}),
        json.dumps({"channel": "app_store"}),
        json.dumps({"channel": 123}),
    ],
)
def test_malformed_or_invalid_config_fails_closed_to_runtime_fallback(tmp_path, payload_text):
    config_file = tmp_path / DISTRIBUTION_CHANNEL_FILENAME
    config_file.write_text(payload_text, encoding="utf-8")

    profile = resolve_distribution_channel(
        config_path=config_file,
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    assert profile.channel is DistributionChannel.APK_LTS


def test_oversized_or_symlink_config_fails_closed(tmp_path, monkeypatch):
    oversized = tmp_path / "oversized.json"
    oversized.write_text(
        json.dumps({"channel": "chplay"}) + (" " * 5000),
        encoding="utf-8",
    )
    profile = resolve_distribution_channel(
        config_path=oversized,
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    assert profile.channel is DistributionChannel.APK_LTS

    symlink_like = tmp_path / "symlink.json"
    symlink_like.write_text(json.dumps({"channel": "chplay"}), encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == symlink_like)
    profile_sym = resolve_distribution_channel(
        config_path=symlink_like,
        is_windows=False,
        is_android=True,
        is_ios=False,
    )
    assert profile_sym.channel is DistributionChannel.APK_LTS


def test_fallback_precedence_without_config_file(tmp_path):
    missing_cfg = tmp_path / "missing.json"
    missing_pyproject = tmp_path / "missing_pyproject.toml"
    present_pyproject = tmp_path / "pyproject.toml"
    present_pyproject.write_text("[project]\nname='uthelper'\n", encoding="utf-8")

    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: True,
        ).channel
        is DistributionChannel.MSSTORE
    )
    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: False,
            pyproject_path=missing_pyproject,
        ).channel
        is DistributionChannel.EXE_LTS
    )
    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: False,
            is_packaged=True,
            pyproject_path=present_pyproject,
        ).channel
        is DistributionChannel.EXE_LTS
    )
    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=lambda: False,
            pyproject_path=present_pyproject,
        ).channel
        is DistributionChannel.SOURCE
    )
    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=False,
            is_android=True,
            is_ios=False,
        ).channel
        is DistributionChannel.APK_LTS
    )
    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=False,
            is_android=False,
            is_ios=True,
        ).channel
        is DistributionChannel.SOURCE
    )

    def _boom():
        raise OSError("Win32 identity query failed")

    assert (
        resolve_distribution_channel(
            config_path=missing_cfg,
            is_windows=True,
            is_android=False,
            is_ios=False,
            has_package_identity_fn=_boom,
            pyproject_path=present_pyproject,
        ).channel
        is DistributionChannel.SOURCE
    )


def test_windows_exe_lts_config_is_coerced_to_msstore_when_package_identity_present(tmp_path):
    config_file = tmp_path / DISTRIBUTION_CHANNEL_FILENAME
    config_file.write_text(json.dumps({"channel": "exe_lts"}), encoding="utf-8")

    profile = resolve_distribution_channel(
        config_path=config_file,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: True,
    )
    assert profile.channel is DistributionChannel.MSSTORE
    assert profile.supports_github_self_update is False


def test_windows_source_config_is_coerced_to_msstore_when_package_identity_present(tmp_path):
    config_file = tmp_path / DISTRIBUTION_CHANNEL_FILENAME
    config_file.write_text(json.dumps({"channel": "source"}), encoding="utf-8")

    profile = resolve_distribution_channel(
        config_path=config_file,
        is_windows=True,
        is_android=False,
        is_ios=False,
        has_package_identity_fn=lambda: True,
    )
    assert profile.channel is DistributionChannel.MSSTORE
    assert profile.supports_github_self_update is False


def test_detect_windows_runtime_target_handles_package_identity_oserror(monkeypatch):
    def _boom():
        raise OSError("Win32 package identity error")

    monkeypatch.setattr(windows_update, "has_package_identity", _boom)
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "bootstrapper")
    monkeypatch.setattr(windows_update.platform, "machine", lambda: "AMD64")

    target = windows_update.detect_windows_runtime_target()
    assert target == update_packages.RuntimeTarget("windows", "x64", "bootstrapper")


def test_detect_runtime_target_integrates_distribution_channels(monkeypatch):
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", True)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(update_packages.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(update_packages.struct, "calcsize", lambda _fmt: 8)

    chplay_target = update_packages.detect_runtime_target(
        distribution=build_distribution_profile(DistributionChannel.CHPLAY)
    )
    assert chplay_target == update_packages.RuntimeTarget("android", "arm64", "chplay")

    apk_target = update_packages.detect_runtime_target(
        distribution=build_distribution_profile(DistributionChannel.APK_LTS)
    )
    assert apk_target == update_packages.RuntimeTarget("android", "arm64", "sideload")

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(
        windows_update,
        "read_install_channel",
        lambda: (_ for _ in ()).throw(AssertionError("registry should not be read for msstore")),
    )
    monkeypatch.setattr(windows_update.platform, "machine", lambda: "AMD64")

    msstore_target = windows_update.detect_windows_runtime_target(
        distribution=build_distribution_profile(DistributionChannel.MSSTORE)
    )
    assert msstore_target == update_packages.RuntimeTarget("windows", "x64", "msix")

    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "msi")
    exe_target = windows_update.detect_windows_runtime_target(
        distribution=build_distribution_profile(DistributionChannel.EXE_LTS)
    )
    assert exe_target == update_packages.RuntimeTarget("windows", "x64", "msi")


def test_detect_runtime_target_calls_windows_adapter_with_zero_args_when_default(monkeypatch):
    expected = update_packages.RuntimeTarget("windows", "x64", "msi")
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(windows_update, "detect_windows_runtime_target", lambda: expected)

    assert update_packages.detect_runtime_target() == expected


@pytest.mark.parametrize(
    ("channel", "expected_store_phrase", "expect_github"),
    [
        (DistributionChannel.CHPLAY, "google play", False),
        (DistributionChannel.MSSTORE, "microsoft store", False),
        (DistributionChannel.APK_LTS, "github", True),
        (DistributionChannel.EXE_LTS, "github", True),
        (DistributionChannel.SOURCE, "github", True),
    ],
)
def test_terms_sections_reflect_distribution_channel(
    channel, expected_store_phrase, expect_github
):
    profile = build_distribution_profile(channel)
    sections = terms_dialog_module.get_terms_sections(profile)
    section_4_title, section_4_body = sections[3]

    assert section_4_title.startswith("4.")
    lowered = section_4_body.lower()
    assert expected_store_phrase in lowered
    assert ("github" in lowered) is expect_github
    assert "moodle/portal uth" in lowered
    assert "sentry" in lowered


def test_build_terms_dialog_resolves_channel_when_profile_omitted(monkeypatch):
    page = Mock(spec=ft.Page)
    monkeypatch.setattr(
        terms_dialog_module,
        "resolve_distribution_channel",
        lambda: build_distribution_profile(DistributionChannel.CHPLAY),
    )

    dialog = terms_dialog_module.build_terms_dialog(page)
    texts = [
        child.value
        for col in dialog.content.content.controls
        if isinstance(col, ft.Column)
        for child in col.controls
        if isinstance(child, ft.Text)
    ]
    joined = " ".join(texts).lower()
    assert "google play" in joined
    # Section 4 must not mention GitHub on CH Play (section 7 still has support contact).
    section_4_texts = [t for t in texts if t.startswith("Ứng dụng có thể kết nối")]
    assert len(section_4_texts) == 1
    assert "github" not in section_4_texts[0].lower()
