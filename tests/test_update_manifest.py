"""Schema and deterministic selection tests for trusted updates."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from core.update_manifest import ManifestError, parse_manifest, select_candidate
from core.update_models import RuntimeTarget


def _package(**changes):
    value = {
        "platform": "windows",
        "architecture": "x64",
        "package_type": "msi",
        "install_channel": "msi",
        "url": (
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            "v2.2.0/UTHelper-2.2.0.msi"
        ),
        "sha256": "a" * 64,
        "size": 4096,
        "signer_identity": "CN=UTHelper",
        "certificate_fingerprint": "B" * 64,
        "install_strategy": {"kind": "launch_msi"},
    }
    value.update(changes)
    return value


def _schema2(packages):
    return {
        "schema_version": 2,
        "release_version": "2.2.0",
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-08-04T00:00:00Z",
        "release_notes_url": (
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0"
        ),
        "packages": packages,
    }


def _schema3(package):
    return {
        "schema_version": 3,
        "release_version": "2.2.0",
        "minimum_supported_version": "2.1.0",
        "published_at": "2026-08-09T00:00:00Z",
        "release_notes_url": (
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0"
        ),
        "packages": [package],
    }


def _schema3_package(**changes):
    value = {
        **_package(),
        "signature_kind": "self-signed-pinned",
    }
    value.update(changes)
    return value


def test_schema2_selects_only_exact_runtime_target():
    manifest = parse_manifest(
        _schema2([_package()]),
        expected_release_version="2.2.0",
    )

    candidate = select_candidate(
        manifest,
        current_version="2.1.0",
        target=RuntimeTarget("windows", "x64", "msi"),
    )

    assert candidate is not None
    assert candidate.package.package_type == "msi"
    assert candidate.automatic_install_allowed is True
    assert isinstance(candidate.package.install_strategy, MappingProxyType)
    assert candidate.package.signature_kind == "certificate-pinned"


def test_schema3_accepts_unsigned_ios_sideload_package():
    package = _schema3_package(
        platform="ios",
        architecture="arm64",
        package_type="ipa",
        install_channel="sideload",
        url=(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            "v2.2.0/UTHelper-2.2.0.ipa"
        ),
        signer_identity="",
        certificate_fingerprint="",
        signature_kind="unsigned-resign-required",
        install_strategy={"kind": "manual_sideload"},
    )

    manifest = parse_manifest(
        _schema3(package),
        expected_release_version="2.2.0",
    )

    assert manifest.schema_version == 3
    assert manifest.packages[0].signature_kind == "unsigned-resign-required"

    candidate = select_candidate(
        manifest,
        current_version="2.1.0",
        target=RuntimeTarget("ios", "arm64", "sideload"),
    )

    assert candidate is not None
    assert candidate.automatic_install_allowed is False


def test_schema3_pinned_windows_package_allows_verified_in_app_install():
    manifest = parse_manifest(
        _schema3(_schema3_package()),
        expected_release_version="2.2.0",
    )

    candidate = select_candidate(
        manifest,
        current_version="2.1.0",
        target=RuntimeTarget("windows", "x64", "msi"),
    )

    assert candidate is not None
    assert candidate.automatic_install_allowed is True


@pytest.mark.parametrize("kind", ["apk-pinned", "self-signed-pinned"])
def test_schema3_pinned_signatures_require_identity_and_fingerprint(kind):
    target = (
        {
            "platform": "android",
            "architecture": "universal",
            "package_type": "apk",
            "install_channel": "sideload",
            "url": (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/UTHelper-2.2.0.apk"
            ),
            "install_strategy": {"kind": "android_package_installer"},
        }
        if kind == "apk-pinned"
        else {}
    )
    package = _schema3_package(
        **target,
        signature_kind=kind,
        signer_identity="",
        certificate_fingerprint="",
    )

    with pytest.raises(ManifestError, match="pinned signature identity"):
        parse_manifest(
            _schema3(package),
            expected_release_version="2.2.0",
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"signature_kind": "unsigned-resign-required"},
            "signature kind does not match package target",
        ),
        (
            {
                "platform": "ios",
                "architecture": "arm64",
                "package_type": "ipa",
                "install_channel": "sideload",
                "url": (
                    "https://github.com/Chouwzi/UTHelper/releases/download/"
                    "v2.2.0/UTHelper-2.2.0.ipa"
                ),
                "signature_kind": "unsigned-resign-required",
                "install_strategy": {"kind": "manual_sideload"},
            },
            "unsigned package identity",
        ),
        ({"signature_kind": "unknown"}, "signature_kind is invalid"),
    ],
)
def test_schema3_rejects_mismatched_signature_contract(changes, message):
    with pytest.raises(ManifestError, match=message):
        parse_manifest(
            _schema3(_schema3_package(**changes)),
            expected_release_version="2.2.0",
        )


def test_schema2_rejects_duplicate_candidates():
    manifest = parse_manifest(
        _schema2(
            [
                _package(),
                _package(
                    url=(
                        "https://github.com/Chouwzi/UTHelper/releases/download/"
                        "v2.2.0/UTHelper-copy-2.2.0.msi"
                    )
                ),
            ]
        ),
        expected_release_version="2.2.0",
    )

    with pytest.raises(ManifestError, match="ambiguous"):
        select_candidate(
            manifest,
            current_version="2.1.0",
            target=RuntimeTarget("windows", "x64", "msi"),
        )


@pytest.mark.parametrize(
    "change",
    [
        {
            "url": (
                "http://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/a.msi"
            )
        },
        {"url": "https://example.com/a.msi"},
        {"sha256": "0" * 63},
        {"sha256": "z" * 64},
        {"size": 0},
        {"certificate_fingerprint": ""},
        {"install_strategy": {"kind": "silent_install"}},
        {"install_strategy": {"kind": "launch_bootstrapper"}},
        {
            "url": (
                "https://github.com/Chouwzi/UTHelper/releases/download/"
                "v2.2.0/not-an-msi.exe"
            )
        },
        {"unexpected": "field"},
    ],
)
def test_schema2_rejects_unsafe_package_metadata(change):
    with pytest.raises(ManifestError):
        parse_manifest(
            _schema2([_package(**change)]),
            expected_release_version="2.2.0",
        )


def test_schema2_rejects_unknown_release_fields_and_version_mismatch():
    document = _schema2([_package()])
    document["token"] = "unexpected"
    with pytest.raises(ManifestError, match="unknown release fields"):
        parse_manifest(document, expected_release_version="2.2.0")

    with pytest.raises(ManifestError, match="does not match"):
        parse_manifest(
            _schema2([_package()]),
            expected_release_version="2.3.0",
        )


def test_schema1_is_discoverable_but_cannot_install_automatically():
    manifest = parse_manifest(
        {
            "schema": 1,
            "version": "2.2.0",
            "minimum_supported_version": "2.1.0",
            "assets": {
                "windows": {
                    **_package(),
                    "name": "UTHelper-2.2.0.msi",
                }
            },
        },
        expected_release_version="2.2.0",
    )

    candidate = select_candidate(
        manifest,
        current_version="2.1.0",
        target=RuntimeTarget("windows", "x64", "msi"),
    )

    assert candidate is not None
    assert candidate.automatic_install_allowed is False


def test_no_candidate_for_current_or_wrong_install_channel():
    manifest = parse_manifest(
        _schema2([_package()]),
        expected_release_version="2.2.0",
    )

    assert (
        select_candidate(
            manifest,
            current_version="2.2.0",
            target=RuntimeTarget("windows", "x64", "msi"),
        )
        is None
    )
    assert (
        select_candidate(
            manifest,
            current_version="2.1.0",
            target=RuntimeTarget("windows", "x64", "bootstrapper"),
        )
        is None
    )
