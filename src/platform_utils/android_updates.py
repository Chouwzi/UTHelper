"""Bounded coordinator adapters for Android's native update services.

Only the coordinator worker waits here; bridge coroutines run on the page loop.
DownloadManager owns transfers, so closing the page detaches the observer while
an explicit user cancellation removes the native download.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError, TimeoutError as FutureTimeoutError
import logging
from pathlib import Path
import threading
import time

from packaging.version import InvalidVersion, Version

from core.update_checker import DownloadCancelled, PackageIntegrityError
from core.update_models import LaunchResult, ReleasePackage


PACKAGE_ID = "com.uthelper.uthelper"
logger = logging.getLogger(__name__)


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

    def __init__(self, bridge, task_runner, release_version_provider):
        self.bridge = bridge
        self.task_runner = task_runner
        self.release_version_provider = release_version_provider
        self.stopped = threading.Event()

    def identity(self, package: ReleasePackage) -> tuple:
        if package.platform != "android" or package.package_type != "apk" or self.bridge is None or not self.bridge.available:
            raise RuntimeError("Android update service is unavailable")
        return (
            package.url,
            package.sha256,
            package.size,
            PACKAGE_ID,
            android_release_build_number(self.release_version_provider()),
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


class AndroidPackageLauncher:
    def __init__(self, bridge, task_runner, release_version_provider):
        self._native = NativeUpdateClient(bridge, task_runner, release_version_provider)

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
                logger.warning(
                    "Android update handoff: status=%s stage=%s reason=%s",
                    status,
                    result.get("stage", "unknown"),
                    reason,
                )
            return LaunchResult(status == "installer_opened", reason, status=status)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Android update bridge failed: %s", type(exc).__name__)
            return LaunchResult(False, "Android installer service failed", status="bridge_failed")

    def cancel(self) -> None:
        self._native.stopped.set()
        bridge = self._native.bridge
        if bridge is not None and bridge.available:
            self._native.task_runner(bridge.cancel_update)


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
    ):
        if not 0 < observation_timeout_seconds <= 1800:
            raise ValueError("Android download observation must be within 30 minutes")
        if not 0 < poll_interval_seconds <= 5:
            raise ValueError("Android download polling interval is invalid")
        self._native = NativeUpdateClient(bridge, task_runner, release_version_provider)
        self._detached = threading.Event()
        self._timeout = observation_timeout_seconds
        self._poll_interval = poll_interval_seconds

    def detach(self) -> None:
        self._detached.set()
        self._native.stopped.set()

    def has_pending_download(self, package: ReleasePackage) -> bool:
        identity = self._native.identity(package)
        result = self._native.call(self._native.bridge.get_update_download_status, *identity)
        return result["status"] in {"pending", "running", "paused", "completed"}

    def download(self, package: ReleasePackage, *, cancel: threading.Event, progress=None) -> Path:
        identity = self._native.identity(package)
        if self._detached.is_set():
            raise DownloadCancelled("download observer detached")
        if cancel.is_set():
            raise DownloadCancelled("download cancelled")
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
