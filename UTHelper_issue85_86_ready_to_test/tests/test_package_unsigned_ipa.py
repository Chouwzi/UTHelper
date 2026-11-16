from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import struct
import zipfile

import pytest

from scripts.package_unsigned_ipa import (
    IpaError,
    inspect_device_app,
    inspect_ipa,
    write_verification_evidence,
)


VERSION = "2.2.0"
BUILD = "2002000"
BUNDLE_ID = "com.uthelper.UTHelper"


def _make_app(
    root: Path,
    *,
    cpu_type: int = 0x0100000C,
    platform: str = "iPhoneOS",
    bundle_id: str = BUNDLE_ID,
    version: str = VERSION,
    build: str = BUILD,
) -> Path:
    app = root / "UTHelper.app"
    app.mkdir(parents=True)
    info = {
        "CFBundleIdentifier": bundle_id,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": build,
        "CFBundleExecutable": "UTHelper",
        "CFBundleSupportedPlatforms": [platform],
    }
    (app / "Info.plist").write_bytes(plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    (app / "UTHelper").write_bytes(struct.pack("<Iii", 0xFEEDFACF, cpu_type, 0))
    return app


def _write_ipa(path: Path, app: Path, *, duplicate: bool = False) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(app.rglob("*")):
            relative = item.relative_to(app.parent).as_posix()
            archive.write(item, f"Payload/{relative}")
        if duplicate:
            archive.writestr("Payload/UTHelper.app/Info.plist", b"duplicate")


def test_inspector_accepts_only_iphoneos_arm64_app(tmp_path):
    app = _make_app(tmp_path)

    metadata = inspect_device_app(
        app,
        version=VERSION,
        build_number=BUILD,
        bundle_id=BUNDLE_ID,
    )

    assert metadata.architectures == ("arm64",)
    assert metadata.platforms == ("iPhoneOS",)
    assert metadata.executable_name == "UTHelper"


@pytest.mark.parametrize(
    ("cpu_type", "platform"),
    [
        (0x01000007, "iPhoneSimulator"),
        (0x01000007, "iPhoneOS"),
        (0x0100000C, "iPhoneSimulator"),
    ],
)
def test_inspector_rejects_simulator_or_non_arm64_binary(
    tmp_path,
    cpu_type,
    platform,
):
    app = _make_app(tmp_path, cpu_type=cpu_type, platform=platform)

    with pytest.raises(IpaError, match="iPhoneOS arm64"):
        inspect_device_app(
            app,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"bundle_id": "com.attacker.app"}, "bundle identifier"),
        ({"version": "9.9.9"}, "short version"),
        ({"build": "999"}, "build number"),
    ],
)
def test_inspector_rejects_wrong_bundle_metadata(tmp_path, change, message):
    app = _make_app(tmp_path, **change)

    with pytest.raises(IpaError, match=message):
        inspect_device_app(
            app,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )


def test_inspector_rejects_embedded_provisioning_profile(tmp_path):
    app = _make_app(tmp_path)
    (app / "embedded.mobileprovision").write_bytes(b"profile")

    with pytest.raises(IpaError, match="provisioning profile"):
        inspect_device_app(
            app,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )


def test_ipa_inspector_rejects_duplicate_and_traversal_members(tmp_path):
    app = _make_app(tmp_path / "source")
    duplicate = tmp_path / "duplicate.ipa"
    with pytest.warns(UserWarning, match="Duplicate name"):
        _write_ipa(duplicate, app, duplicate=True)

    with pytest.raises(IpaError, match="duplicate"):
        inspect_ipa(
            duplicate,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )

    traversal = tmp_path / "traversal.ipa"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../escape", b"bad")
    with pytest.raises(IpaError, match="unsafe IPA member"):
        inspect_ipa(
            traversal,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )


def test_ipa_inspector_rejects_symlink_and_extra_payload_root(tmp_path):
    app = _make_app(tmp_path / "source")
    symlink_ipa = tmp_path / "symlink.ipa"
    _write_ipa(symlink_ipa, app)
    with zipfile.ZipFile(symlink_ipa, "a") as archive:
        link = zipfile.ZipInfo("Payload/UTHelper.app/link")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "../../escape")

    with pytest.raises(IpaError, match="unsafe IPA member"):
        inspect_ipa(
            symlink_ipa,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )

    extra_ipa = tmp_path / "extra.ipa"
    _write_ipa(extra_ipa, app)
    with zipfile.ZipFile(extra_ipa, "a") as archive:
        archive.writestr("Payload/README.txt", b"unexpected")
    with pytest.raises(IpaError, match="exactly one Payload application"):
        inspect_ipa(
            extra_ipa,
            version=VERSION,
            build_number=BUILD,
            bundle_id=BUNDLE_ID,
        )


def test_ipa_inspector_accepts_one_payload_app(tmp_path):
    app = _make_app(tmp_path / "source")
    ipa = tmp_path / "UTHelper.ipa"
    _write_ipa(ipa, app)

    metadata = inspect_ipa(
        ipa,
        version=VERSION,
        build_number=BUILD,
        bundle_id=BUNDLE_ID,
    )

    assert metadata.bundle_id == BUNDLE_ID
    assert metadata.architectures == ("arm64",)


def test_evidence_declares_unsigned_resign_required_without_identity(tmp_path):
    app = _make_app(tmp_path / "source")
    ipa = tmp_path / "UTHelper-2.2.0.ipa"
    _write_ipa(ipa, app)
    metadata = inspect_ipa(
        ipa,
        version=VERSION,
        build_number=BUILD,
        bundle_id=BUNDLE_ID,
    )
    evidence = tmp_path / "evidence.json"

    write_verification_evidence(
        ipa,
        metadata,
        commit_sha="1" * 40,
        workflow_run_id="12345",
        output=evidence,
    )

    record = json.loads(evidence.read_text(encoding="utf-8"))
    assert record["sha256"] == hashlib.sha256(ipa.read_bytes()).hexdigest()
    assert record["signature_kind"] == "unsigned-resign-required"
    assert record["signer_identity"] == ""
    assert record["certificate_fingerprint"] == ""
    assert record["signature_valid"] is False
    assert record["timestamp_valid"] is None
    assert record["checks"] == [
        "arm64",
        "build_number",
        "bundle_id",
        "iphoneos",
        "ipa_container",
        "no_embedded_profile",
        "sha256",
        "version",
    ]
