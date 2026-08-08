"""Cross-platform update verification and launch boundaries."""

from __future__ import annotations

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
    "PackageLauncher",
    "PackageVerifier",
    "RuntimeTarget",
    "detect_runtime_target",
]
