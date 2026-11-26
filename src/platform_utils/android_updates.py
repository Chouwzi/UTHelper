"""Bounded coordinator adapters for Android's native update services.

Only the coordinator worker waits here; bridge coroutines run on the page loop.
DownloadManager owns transfers, so closing the page detaches the observer while
an explicit user cancellation removes the native download.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time

from packaging.version import InvalidVersion, Version

from core.update_checker import DownloadCancelled, PackageIntegrityError
from core.update_manifest import manual_install_reason
from core.update_models import (
    LaunchResult,
    ReleasePackage,
    UpdateCandidate,
    VerificationResult,
)


PACKAGE_ID = "io.github.chouwzi.uthelper"
logger = logging.getLogger(__name__)


class AndroidPackageIdentityMismatchError(PackageIntegrityError):
    """Raised before download/install when an Android package ID differs from installed app."""

    def __init__(
        self,
        *,
        expected_package_id: str,
        actual_package_id: str,
        release_url: str | None = None,
    ) -> None:
        self.expected_package_id = expected_package_id
        self.actual_package_id = actual_package_id
        self.release_url = release_url
        self.manual_reason = (
            f"Android package ID migration required: release targets {actual_package_id} "
            f"instead of installed {expected_package_id}; download the APK manually from "
            "GitHub Releases."
        )
        super().__init__(f"package_mismatch: {self.manual_reason}")


@dataclass(frozen=True, slots=True)
class AndroidManualDownloadFallback:
    """Manual GitHub Releases fallback descriptor for incompatible Android package IDs."""

    manual_download_required: bool
    reason: str
    release_url: str | None
    expected_package_id: str
    actual_package_id: str


def _resolve_expected_package_id(package_id: str | None) -> str:
    if isinstance(package_id, str) and package_id.strip():
        return package_id.strip()
    return PACKAGE_ID


def _is_package_id_mismatch(status: str, reason: str) -> bool:
    normalized_status = str(status or "").strip().lower()
    normalized_reason = str(reason or "").strip().lower()
    return (
        normalized_status == "package_mismatch"
        or normalized_reason == "package_mismatch"
        or "package_mismatch" in normalized_reason
        or "package id mismatch" in normalized_reason
        or "unexpected package identity" in normalized_reason
    )


def resolve_android_manual_fallback(
    candidate: UpdateCandidate,
    *,
    expected_package_id: str | None = None,
    install_result: LaunchResult | None = None,
) -> AndroidManualDownloadFallback | None:
    """Return manual GitHub Releases fallback metadata on Android package ID mismatch."""
    if candidate.package.platform != "android":
        return None
    expected = _resolve_expected_package_id(expected_package_id)
    actual = str(candidate.package.signer_identity or "").strip()
    pre_mismatch = bool(actual and actual != expected)
    post_mismatch = bool(
        install_result is not None
        and not install_result.acknowledged
        and _is_package_id_mismatch(install_result.status, install_result.reason)
    )
    if not pre_mismatch and not post_mismatch:
        return None
    reason = manual_install_reason(
        candidate,
        expected_signer_identity=expected,
    ) or (
        "Android package ID mismatch detected by installer; download the APK "
        "manually from GitHub Releases."
    )
    return AndroidManualDownloadFallback(
        manual_download_required=True,
        reason=reason,
        release_url=candidate.manifest.release_notes_url,
        expected_package_id=expected,
        actual_package_id=actual or "unknown",
    )

try:
    from flet import FletException as _FletException
except Exception:  # pragma: no cover - optional UI runtime dependency
    _BRIDGE_EXCEPTIONS: tuple[type[BaseException], ...] = (
        CancelledError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    )
else:
    _BRIDGE_EXCEPTIONS = (
        CancelledError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        _FletException,
    )


def android_release_build_number(version: str) -> int:
    """Use the same strict X.Y.Z build number as the release pipeline."""
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ValueError("invalid Android release version") from exc
    if (
        str(parsed) != version
        or len(parsed.release) != 3
        or parsed.pre
        or parsed.post
        or parsed.dev
        or parsed.local
        or any(part < 0 or part > 999 for part in parsed.release)
    ):
        raise ValueError("Android release version must be numeric X.Y.Z")
    major, minor, patch = parsed.release
    return major * 1_000_000 + minor * 1_000 + patch


class NativeUpdateClient:
    """Wait for a native response, never treating queued work as success."""

    def __init__(
        self,
        bridge,
        task_runner,
        release_version_provider,
        *,
        package_id: str | None = None,
    ):
        self.bridge = bridge
        self.task_runner = task_runner
        self.release_version_provider = release_version_provider
        self._package_id = package_id
        self.stopped = threading.Event()

    @property
    def expected_package_id(self) -> str:
        return _resolve_expected_package_id(self._package_id)

    def identity(self, package: ReleasePackage) -> tuple:
        if package.platform != "android" or package.package_type != "apk" or self.bridge is None or not self.bridge.available:
            raise RuntimeError("Android update service is unavailable")
        expected_package_id = self.expected_package_id
        actual_signer = str(package.signer_identity or "").strip()
        if actual_signer and actual_signer != expected_package_id:
            raise AndroidPackageIdentityMismatchError(
                expected_package_id=expected_package_id,
                actual_package_id=actual_signer,
            )
        version = self.release_version_provider()
        if not version:
            raise RuntimeError("Android update release version is unavailable")
        return (
            package.url,
            package.sha256,
            package.size,
            expected_package_id,
            android_release_build_number(version),
            package.certificate_fingerprint,
        )

    def call(self, method, *args, timeout: float = 30.0):
        if self.stopped.is_set():
            raise DownloadCancelled("Android update observer stopped")

        async def invoke():
            if self.stopped.is_set():
                raise DownloadCancelled("Android update observer stopped")
            return await asyncio.wait_for(method(*args), timeout=timeout)

        future = self.task_runner(invoke)
        if future is None:
            raise RuntimeError("Android update session is unavailable")
        deadline = time.monotonic() + timeout + 1
        try:
            while True:
                if self.stopped.is_set():
                    raise DownloadCancelled("Android update observer stopped")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Android update service did not respond")
                try:
                    result = future.result(timeout=min(0.2, remaining))
                    break
                except FutureTimeoutError:
                    if future.done():
                        raise
        except (TimeoutError, CancelledError, DownloadCancelled):
            future.cancel()
            raise
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise RuntimeError("Android update service returned an invalid response")
        return result


class AndroidPackageVerifier:
    """Fast O(1) pre-flight verifier for Android APKs.

    Full SHA-256 verification, archive identity checks, and APK signing
    certificate validation are enforced natively in ApkUpdateInstaller.
    """

    def __init__(self, *, package_id: str | None = None):
        self._package_id = package_id

    def verify(
        self,
        path: Path,
        candidate: UpdateCandidate,
    ) -> VerificationResult:
        candidate_path = Path(path)
        package = candidate.package
        expected_package_id = _resolve_expected_package_id(self._package_id)
        actual_signer = str(package.signer_identity or "").strip()
        if (
            package.platform == "android"
            and actual_signer
            and actual_signer != expected_package_id
        ):
            return VerificationResult(False, "package_mismatch")
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
            with candidate_path.open("rb") as stream:
                magic = stream.read(4)
            if magic != b"PK\x03\x04":
                return VerificationResult(False, "APK ZIP header mismatch")
            return VerificationResult(True)
        except OSError:
            return VerificationResult(False, "portable package verification failed")


class AndroidPackageLauncher:
    supports_cached_package_retry = True

    def __init__(
        self,
        bridge,
        task_runner,
        release_version_provider,
        *,
        package_id: str | None = None,
    ):
        self._native = NativeUpdateClient(
            bridge,
            task_runner,
            release_version_provider,
            package_id=package_id,
        )

    def launch(self, path: Path, package: ReleasePackage) -> LaunchResult:
        try:
            _, sha256, size, package_id, version_code, signer = self._native.identity(package)
            result = self._native.call(
                self._native.bridge.install_update,
                str(path),
                sha256,
                size,
                package_id,
                version_code,
                signer,
                timeout=190.0,
            )
            status = result["status"]
            reason = result.get("reason") or status
            if status != "installer_opened":
                if _is_package_id_mismatch(status, reason):
                    logger.warning(
                        "Android update handoff rejected package ID mismatch: status=%s stage=%s reason=%s",
                        status,
                        result.get("stage", "unknown"),
                        reason,
                    )
                    return LaunchResult(False, "package_mismatch", status="failed")
                logger.warning(
                    "Android update handoff: status=%s stage=%s reason=%s",
                    status,
                    result.get("stage", "unknown"),
                    reason,
                )
            return LaunchResult(status == "installer_opened", reason, status=status)
        except AndroidPackageIdentityMismatchError as exc:
            logger.warning(
                "Android update blocked pre-launch package ID mismatch: expected=%s actual=%s",
                exc.expected_package_id,
                exc.actual_package_id,
            )
            return LaunchResult(False, "package_mismatch", status="failed")
        except _BRIDGE_EXCEPTIONS as exc:
            logger.warning("Android update bridge failed: %s", type(exc).__name__)
            return LaunchResult(False, "Android installer service failed", status="bridge_failed")

    def cancel(self) -> None:
        self._native.stopped.set()
        bridge = self._native.bridge
        if bridge is not None and bridge.available:
            future = self._native.task_runner(bridge.cancel_update)
            if future is not None and hasattr(future, "add_done_callback"):
                def _consume_exception(done_future):
                    try:
                        if not done_future.cancelled():
                            done_future.exception()
                    except BaseException:
                        pass

                future.add_done_callback(_consume_exception)


class AndroidPackageDownloader:
    """Observe one persisted OS job without owning its process lifetime."""

    def __init__(
        self,
        bridge,
        task_runner,
        release_version_provider,
        *,
        observation_timeout_seconds: float = 1800.0,
        poll_interval_seconds: float = 0.5,
        package_id: str | None = None,
    ):
        if not 0 < observation_timeout_seconds <= 1800:
            raise ValueError("Android download observation must be within 30 minutes")
        if not 0 < poll_interval_seconds <= 5:
            raise ValueError("Android download polling interval is invalid")
        self._native = NativeUpdateClient(
            bridge,
            task_runner,
            release_version_provider,
            package_id=package_id,
        )
        self._detached = threading.Event()
        self._timeout = observation_timeout_seconds
        self._poll_interval = poll_interval_seconds

    def detach(self) -> None:
        self._detached.set()
        self._native.stopped.set()

    def has_pending_download(self, package: ReleasePackage) -> bool:
        if not self._native.release_version_provider():
            # No release is selected yet, so there is no OS transfer to resume.
            return False
        try:
            identity = self._native.identity(package)
        except AndroidPackageIdentityMismatchError:
            return False
        result = self._native.call(self._native.bridge.get_update_download_status, *identity)
        return result["status"] in {"pending", "running", "paused", "completed"}

    def download(self, package: ReleasePackage, *, cancel: threading.Event, progress=None) -> Path:
        identity = self._native.identity(package)
        if self._detached.is_set():
            raise DownloadCancelled("download observer detached")
        if cancel.is_set():
            raise DownloadCancelled("download cancelled")
        try:
            result = self._native.call(self._native.bridge.start_update_download, *identity)
            started = time.monotonic()
            while True:
                if self._detached.is_set():
                    raise DownloadCancelled("download observer detached")
                if cancel.is_set():
                    self._native.call(self._native.bridge.cancel_update_download, *identity)
                    raise DownloadCancelled("download cancelled")
                status = result["status"]
                if status == "completed":
                    path = Path(result.get("local_path", ""))
                    if not path.is_absolute() or path.suffix.lower() != ".apk":
                        raise PackageIntegrityError("Android download path is invalid")
                    if progress is not None:
                        progress(package.size, package.size)
                    return path
                if status not in {"pending", "running", "paused"}:
                    raise OSError("Android download failed: " + str(result.get("reason", status)))
                done = max(0, min(package.size, int(result.get("downloaded", 0))))
                if progress is not None:
                    progress(done, package.size)
                if time.monotonic() - started >= self._timeout:
                    # Leave the OS transfer recoverable on the next request/restart.
                    raise TimeoutError("Android download is still pending; retry to reconnect")
                self._detached.wait(self._poll_interval)
                if self._detached.is_set() or cancel.is_set():
                    continue
                result = self._native.call(self._native.bridge.get_update_download_status, *identity)
        except CancelledError as exc:
            if (
                self._detached.is_set()
                or cancel.is_set()
                or self._native.stopped.is_set()
            ):
                raise DownloadCancelled(
                    "download observer detached"
                    if self._detached.is_set()
                    else "download cancelled"
                ) from exc
            raise RuntimeError("Android update download task was cancelled") from exc
