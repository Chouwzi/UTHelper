"""App Store packaging and distribution-policy regression tests."""

from __future__ import annotations

import plistlib
from pathlib import Path
import sys
import tomllib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from core.distribution import (
    DistributionChannel,
    build_distribution_profile,
    resolve_distribution_channel,
)
from platform_utils import background_sync
from platform_utils import update_packages
from scripts.prepare_ios_app_store import (
    IOSAppStorePreparationError,
    prepare_app_bundle,
    read_privacy_manifest,
)
from scripts.release_metadata import (
    ReleaseMetadataError,
    build_distribution_channel_payload,
)


def test_app_store_channel_disables_github_self_updates() -> None:
    profile = build_distribution_profile(DistributionChannel.IOS_APP_STORE)

    assert profile.supports_github_self_update is False
    assert profile.store_display_name == "Apple App Store"


def test_ios_runtime_defaults_to_app_store_update_policy(tmp_path: Path) -> None:
    profile = resolve_distribution_channel(
        config_path=tmp_path / "missing.json",
        is_windows=False,
        is_android=False,
        is_ios=True,
    )

    assert profile.channel is DistributionChannel.IOS_APP_STORE
    assert profile.supports_github_self_update is False


def test_mobile_secret_backend_has_legacy_android_compatibility_alias() -> None:
    assert background_sync.AndroidSecretBackend is background_sync.MobileSecretBackend


@pytest.mark.parametrize(
    ("channel", "expected_self_update", "install_channel"),
    [
        (DistributionChannel.IOS_APP_STORE, False, "app-store"),
        (DistributionChannel.IOS_LTS, True, "sideload"),
    ],
)
def test_ios_update_profiles_match_install_channel(
    monkeypatch: pytest.MonkeyPatch,
    channel: DistributionChannel,
    expected_self_update: bool,
    install_channel: str,
) -> None:
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", True)

    profile = build_distribution_profile(channel)

    assert profile.supports_github_self_update is expected_self_update
    assert update_packages.detect_runtime_target(profile) == update_packages.RuntimeTarget(
        "ios", "arm64", install_channel
    )


def test_release_metadata_accepts_ios_app_store_channels() -> None:
    assert build_distribution_channel_payload("ios_app_store") == {
        "schema_version": 1,
        "channel": "ios_app_store",
    }
    assert build_distribution_channel_payload("ios_lts") == {
        "schema_version": 1,
        "channel": "ios_lts",
    }
    with pytest.raises(ReleaseMetadataError):
        build_distribution_channel_payload("ios_store")


def test_privacy_manifest_declares_app_store_data_use() -> None:
    manifest = read_privacy_manifest()

    assert manifest["NSPrivacyTracking"] is False
    assert manifest["NSPrivacyTrackingDomains"] == []
    collected_types = {
        entry["NSPrivacyCollectedDataType"]
        for entry in manifest["NSPrivacyCollectedDataTypes"]
    }
    assert collected_types == {
        "NSPrivacyCollectedDataTypeCrashData",
        "NSPrivacyCollectedDataTypeOtherDiagnosticData",
    }
    required_api_reasons = {
        entry["NSPrivacyAccessedAPIType"]: entry["NSPrivacyAccessedAPITypeReasons"]
        for entry in manifest["NSPrivacyAccessedAPITypes"]
    }
    assert required_api_reasons == {
        "NSPrivacyAccessedAPICategoryFileTimestamp": ["C617.1"],
        "NSPrivacyAccessedAPICategorySystemBootTime": ["35F9.1"],
        "NSPrivacyAccessedAPICategoryUserDefaults": ["CA92.1"],
    }


def test_app_store_packaging_installs_privacy_manifest_and_enforces_ats(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "UTHelper.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "com.uthelper.UTHelper",
                "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": False},
                "ITSAppUsesNonExemptEncryption": False,
            }
        )
    )

    installed = prepare_app_bundle(bundle)

    assert installed.parent == bundle
    assert plistlib.loads(installed.read_bytes()) == read_privacy_manifest()


def test_app_store_packaging_rejects_ats_bypass(tmp_path: Path) -> None:
    bundle = tmp_path / "UTHelper.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
                "ITSAppUsesNonExemptEncryption": False,
            }
        )
    )

    with pytest.raises(IOSAppStorePreparationError, match="App Transport Security"):
        prepare_app_bundle(bundle)


def test_app_store_packaging_requires_encryption_declaration(tmp_path: Path) -> None:
    bundle = tmp_path / "UTHelper.app"
    bundle.mkdir()
    (bundle / "Info.plist").write_bytes(
        plistlib.dumps({"ITSAppUsesNonExemptEncryption": True})
    )

    with pytest.raises(IOSAppStorePreparationError, match="encryption"):
        prepare_app_bundle(bundle)


def test_ios_build_configuration_uses_ats_without_cleartext_exceptions() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    transport_security = project["tool"]["flet"]["ios"]["info"][
        "NSAppTransportSecurity"
    ]

    assert transport_security == {"NSAllowsArbitraryLoads": False}
    assert project["tool"]["flet"]["ios"]["info"][
        "ITSAppUsesNonExemptEncryption"
    ] is False


def test_ios_workflow_passes_optional_app_store_id_variable() -> None:
    workflow = (ROOT / ".github/workflows/build-ios.yml").read_text(encoding="utf-8")
    assert "APP_STORE_ID: ${{ vars.APP_STORE_ID }}" in workflow
    assert '--app-store-id "${APP_STORE_ID:-}"' in workflow


def test_ios_build_workflows_install_the_store_privacy_manifest() -> None:
    build_workflow = (ROOT / ".github/workflows/build-ios.yml").read_text(
        encoding="utf-8"
    )
    release_workflow = (ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )

    assert "--distribution-channel ios_app_store" in build_workflow
    assert "prepare_ios_app_store.py --app-bundle" in build_workflow
    assert "--distribution-channel ios_lts" in release_workflow
    assert "prepare_ios_app_store.py --app-bundle" in release_workflow
