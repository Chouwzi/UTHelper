"""Cross-platform update verification and launch boundaries."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import platform_utils
from core.update_models import (
    LaunchResult,
    ReleasePackage,
    RuntimeTarget,
    UpdateCandidate,
    VerificationResult,
)


class PackageVerifier(Protocol):
    def verify(
        self,
        path: Path,
        candidate: UpdateCandidate,
    ) -> VerificationResult: ...


class PackageLauncher(Protocol):
    def launch(self, path: Path, package: ReleasePackage) -> LaunchResult: ...

    def cancel(self) -> None: ...


class DownloadedPackageVerifier:
    """Recheck portable package integrity before native identity verification.

    Android certificate/package/version verification remains inside the native
    PackageInstaller bridge, where the platform signing APIs are authoritative.
    """

    def verify(
        self,
        path: Path,
        candidate: UpdateCandidate,
    ) -> VerificationResult:
        candidate_path = Path(path)
        package = candidate.package
        try:
            if (
                package.platform != "android"
                or package.package_type != "apk"
                or candidate_path.suffix.lower() != ".apk"
                or candidate_path.is_symlink()
                or not candidate_path.is_file()
                or candidate_path.stat().st_size != package.size
            ):
                return VerificationResult(False, "portable package identity mismatch")
            digest = hashlib.sha256()
            with candidate_path.open("rb") as stream:
                magic = stream.read(4)
                digest.update(magic)
                for chunk in iter(lambda: stream.read(64 * 1024), b""):
                    digest.update(chunk)
            if magic != b"PK\x03\x04":
                return VerificationResult(False, "APK ZIP header mismatch")
            if digest.hexdigest().lower() != package.sha256.lower():
                return VerificationResult(False, "package SHA-256 mismatch")
            return VerificationResult(True)
        except OSError:
            return VerificationResult(False, "portable package verification failed")


def detect_runtime_target() -> RuntimeTarget:
    """Return the exact platform/architecture/install-channel target."""
    if platform_utils.IS_WINDOWS:
        from platform_utils.windows_update import detect_windows_runtime_target

        return detect_windows_runtime_target()
    if platform_utils.IS_ANDROID:
        return RuntimeTarget("android", "universal", "sideload")
    if platform_utils.IS_IOS:
        return RuntimeTarget("ios", "arm64", "app-store")
    return RuntimeTarget("other", "unknown", "source")


__all__ = [
    "DownloadedPackageVerifier",
    "PackageLauncher",
    "PackageVerifier",
    "RuntimeTarget",
    "detect_runtime_target",
]
