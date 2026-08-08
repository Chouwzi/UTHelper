"""Platform target, package trust, and explicit-launch tests."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
import subprocess

from core.update_models import (
    ReleaseManifest,
    ReleasePackage,
    UpdateCandidate,
)
from platform_utils.update_packages import (
    DownloadedPackageVerifier,
    RuntimeTarget,
    detect_runtime_target,
)
from platform_utils.windows_update import (
    BURN_UPGRADE_CODE,
    MSI_UPGRADE_CODE,
    ExecutableDetails,
    MsiDetails,
    SignatureDetails,
    WindowsPackageLauncher,
    WindowsPackageVerifier,
)


def _candidate(path: Path, *, fingerprint: str = "AB" * 32, signer: str = "CN=UTHelper"):
    payload = path.read_bytes()
    package_type = path.suffix.lstrip(".").lower()
    channel = "msi" if package_type == "msi" else "bootstrapper"
    strategy = "launch_msi" if package_type == "msi" else "launch_bootstrapper"
    package = ReleasePackage(
        platform="windows",
        architecture="x64",
        package_type=package_type,
        install_channel=channel,
        url=(
            "https://github.com/Chouwzi/UTHelper/releases/download/"
            f"v2.2.0/{path.name}"
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        signer_identity=signer,
        certificate_fingerprint=fingerprint,
        install_strategy={"kind": strategy},
    )
    manifest = ReleaseManifest(
        schema_version=2,
        release_version="2.2.0",
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 8, 4, tzinfo=UTC),
        release_notes_url=(
            "https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0"
        ),
        packages=(package,),
    )
    return UpdateCandidate(manifest, package, True, False)


def _signature(subject="CN=UTHelper", fingerprint="AB" * 32):
    return SignatureDetails("Valid", subject, fingerprint, True)


def test_windows_target_uses_registry_install_channel(monkeypatch):
    from platform_utils import windows_update

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: False)
    monkeypatch.setattr(windows_update, "read_install_channel", lambda: "msi")
    monkeypatch.setattr(windows_update.platform, "machine", lambda: "AMD64")

    assert windows_update.detect_windows_runtime_target() == RuntimeTarget(
        "windows", "x64", "msi"
    )


def test_packaged_windows_target_is_msix_without_registry_guess(monkeypatch):
    from platform_utils import windows_update

    monkeypatch.setattr(windows_update, "has_package_identity", lambda: True)
    monkeypatch.setattr(
        windows_update,
        "read_install_channel",
        lambda: (_ for _ in ()).throw(AssertionError("registry must not be read")),
    )

    assert windows_update.detect_windows_runtime_target().install_channel == "msix"


def test_cross_platform_target_delegates_to_windows_adapter(monkeypatch):
    import platform_utils.update_packages as update_packages

    expected = RuntimeTarget("windows", "x64", "bootstrapper")
    monkeypatch.setattr(update_packages.platform_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(update_packages.platform_utils, "IS_ANDROID", False)
    monkeypatch.setattr(update_packages.platform_utils, "IS_IOS", False)
    monkeypatch.setattr(
        "platform_utils.windows_update.detect_windows_runtime_target",
        lambda: expected,
    )

    assert detect_runtime_target() == expected


def test_windows_verifier_requires_chain_fingerprint_subject_and_msi_identity(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"signed-msi")
    candidate = _candidate(path)
    metadata = MsiDetails("UTHelper", "2.2.0", MSI_UPGRADE_CODE, "x64")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: metadata,
    )

    assert verifier.verify(path, candidate).verified
    bad_subject = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(subject="CN=Other"),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: metadata,
    )
    assert not bad_subject.verify(path, candidate).verified


def test_tampered_manifest_and_attacker_package_cannot_redefine_windows_trust(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"attacker-msi")
    attacker = "CD" * 32
    candidate = _candidate(path, fingerprint=attacker, signer="CN=Attacker")
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(
            subject="CN=Attacker",
            fingerprint=attacker,
        ),
        trusted_fingerprints=frozenset({"AB" * 32}),
        msi_probe=lambda _path, _timeout: MsiDetails(
            "UTHelper", "2.2.0", MSI_UPGRADE_CODE, "x64"
        ),
    )

    assert not verifier.verify(path, candidate).verified


def test_burn_verifier_checks_version_resource_and_magic(tmp_path):
    path = tmp_path / "UTHelper-Setup-2.2.0.exe"
    path.write_bytes(b"MZ" + b"signed-burn")
    candidate = _candidate(path)
    verifier = WindowsPackageVerifier(
        signature_probe=lambda _path, _timeout: _signature(),
        trusted_fingerprints=frozenset({"AB" * 32}),
        executable_probe=lambda _path, _timeout: ExecutableDetails(
            "UTHelper", "2.2.0", BURN_UPGRADE_CODE
        ),
    )

    assert verifier.verify(path, candidate).verified


class _RunningProcess:
    def __init__(self):
        self.returncode = None

    def wait(self, timeout):
        raise subprocess.TimeoutExpired("installer", timeout)

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1

    def kill(self):
        self.returncode = 1


def test_windows_launcher_uses_msi_and_acknowledges_without_waiting_forever(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    candidate = _candidate(path)
    created = []
    launcher = WindowsPackageLauncher(
        process_factory=lambda argv: created.append(argv) or _RunningProcess()
    )

    result = launcher.launch(path, candidate.package)

    assert result.acknowledged
    assert created == [
        ["msiexec.exe", "/i", str(path), "/passive", "/norestart"]
    ]


def test_windows_launcher_releases_successfully_acknowledged_installer(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.msi"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"msi")
    process = _RunningProcess()
    launcher = WindowsPackageLauncher(process_factory=lambda _argv: process)

    assert launcher.launch(path, _candidate(path).package).acknowledged
    launcher.cancel()

    assert process.returncode is None


def test_portable_verifier_rechecks_size_hash_and_extension(tmp_path):
    path = tmp_path / "UTHelper-2.2.0.apk"
    payload = b"PK\x03\x04apk"
    path.write_bytes(payload)
    package = ReleasePackage(
        platform="android",
        architecture="universal",
        package_type="apk",
        install_channel="sideload",
        url="https://github.com/Chouwzi/UTHelper/releases/download/v2.2.0/UTHelper-2.2.0.apk",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        signer_identity="com.uthelper.uthelper",
        certificate_fingerprint="AB" * 32,
        install_strategy={"kind": "android_package_installer"},
    )
    manifest = ReleaseManifest(
        schema_version=2,
        release_version="2.2.0",
        minimum_supported_version="2.1.0",
        published_at=datetime(2026, 8, 8, tzinfo=UTC),
        release_notes_url="https://github.com/Chouwzi/UTHelper/releases/tag/v2.2.0",
        packages=(package,),
    )
    candidate = UpdateCandidate(manifest, package, True, False)
    verifier = DownloadedPackageVerifier()

    assert verifier.verify(path, candidate).verified
    path.write_bytes(payload + b"tampered")
    assert not verifier.verify(path, candidate).verified
