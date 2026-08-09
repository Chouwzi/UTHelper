"""Single-owner lifecycle coordinator for trusted application updates."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import math
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Protocol
import urllib.parse
import webbrowser

from core.update_checker import DownloadCancelled
from core.update_models import RuntimeTarget, UpdateCandidate
from platform_utils.update_packages import PackageLauncher, PackageVerifier


logger = logging.getLogger(__name__)


class ReleaseClient(Protocol):
    def fetch_candidate(
        self,
        current_version: str,
        target: RuntimeTarget,
    ) -> UpdateCandidate | None: ...


class PackageDownloader(Protocol):
    def download(
        self,
        package,
        *,
        cancel: threading.Event,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path: ...


class CoordinatorClosedError(RuntimeError):
    """A command was submitted after coordinator shutdown."""


class UpdateEventKind(str, Enum):
    CHECKING = "checking"
    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    DOWNLOADING = "downloading"
    DOWNLOAD_PROGRESS = "download_progress"
    READY_TO_INSTALL = "ready_to_install"
    MANUAL_DOWNLOAD_REQUIRED = "manual_download_required"
    INSTALL_LAUNCHED = "install_launched"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class UpdateEvent:
    kind: UpdateEventKind
    candidate: UpdateCandidate | None = None
    progress: float | None = None
    message: str = ""


class UpdateCoordinator:
    """Serialize discovery, verification, confirmation, and shutdown.

    The worker is a daemon as a final process-exit safeguard, while every
    public lifecycle operation remains bounded and cooperatively cancellable.
    """

    _AUTOMATIC_CHECK = "automatic_check"
    _MANUAL_CHECK = "manual_check"
    _DOWNLOAD = "download"
    _CONFIRM = "confirm"
    _PREFERENCE_CHANGED = "preference_changed"
    _SHUTDOWN = "shutdown"

    def __init__(
        self,
        client: ReleaseClient,
        downloader: PackageDownloader,
        verifier: PackageVerifier,
        launcher: PackageLauncher,
        target: RuntimeTarget,
        current_version: str,
        event_sink: Callable[[UpdateEvent], None],
        automatic_enabled: bool = True,
        check_interval_seconds: float = 86400,
        external_opener: Callable[[str], bool] | None = None,
    ) -> None:
        interval = float(check_interval_seconds)
        if not math.isfinite(interval) or interval <= 0 or interval > 7 * 86400:
            raise ValueError("update interval must be within seven days")
        self._client = client
        self._downloader = downloader
        self._verifier = verifier
        self._launcher = launcher
        self._target = target
        self._current_version = current_version
        self._event_sink = event_sink
        self._automatic_enabled = bool(automatic_enabled)
        self._check_interval_seconds = interval
        self._external_opener = external_opener or webbrowser.open

        self._commands: queue.Queue[str] = queue.Queue(maxsize=8)
        self._cancel_download = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._automatic_check_queued = False
        self._manual_check_queued = False
        self._download_queued = False
        self._confirm_queued = False
        self._preference_queued = False
        self._candidate: UpdateCandidate | None = None
        self._ready_path: Path | None = None
        self._ready_external_url: str | None = None
        self._install_handed_off = False

    def start(self) -> None:
        with self._lock:
            self._ensure_open_locked()
            self._start_worker_locked(queue_initial_automatic=True)

    def check_now(self) -> None:
        with self._lock:
            self._ensure_open_locked()
            self._start_worker_locked(queue_initial_automatic=False)
            self._queue_check_locked(automatic=False)

    def set_automatic_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._ensure_open_locked()
            self._automatic_enabled = bool(enabled)
            if not enabled:
                self._cancel_download.set()
            if self._started:
                if (
                    not self._preference_queued
                    and self._enqueue_locked(self._PREFERENCE_CHANGED)
                ):
                    self._preference_queued = True
                if enabled:
                    self._queue_check_locked(automatic=True)

    def request_download(self, candidate: UpdateCandidate | None = None) -> None:
        with self._lock:
            self._ensure_open_locked()
            self._start_worker_locked(queue_initial_automatic=False)
            if candidate is not None:
                self._candidate = candidate
                self._ready_path = None
                self._ready_external_url = None
                self._install_handed_off = False
            if self._candidate is None or self._download_queued:
                return
            if self._enqueue_locked(self._DOWNLOAD):
                self._download_queued = True

    def confirm_install(self) -> None:
        with self._lock:
            self._ensure_open_locked()
            if (
                self._candidate is None
                or self._confirm_queued
                or (self._ready_path is None and self._ready_external_url is None)
            ):
                return
            if self._enqueue_locked(self._CONFIRM):
                self._confirm_queued = True

    def shutdown(self, timeout_seconds: float = 5.0) -> bool:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout):
            timeout = 5.0
        timeout = max(0.0, min(timeout, 5.0))
        with self._lock:
            if self._closed:
                thread = self._thread
            else:
                self._closed = True
                self._stop.set()
                self._cancel_download.set()
                if self._started and not self._install_handed_off:
                    self._cancel_launcher()
                self._enqueue_locked(self._SHUTDOWN)
                thread = self._thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def _start_worker_locked(self, *, queue_initial_automatic: bool) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._run,
            name="trusted-update-coordinator",
            daemon=True,
        )
        self._thread.start()
        if queue_initial_automatic and self._automatic_enabled:
            self._queue_check_locked(automatic=True)

    def _queue_check_locked(self, *, automatic: bool) -> None:
        if automatic:
            if self._automatic_check_queued:
                return
            if self._enqueue_locked(self._AUTOMATIC_CHECK):
                self._automatic_check_queued = True
            return
        if not self._manual_check_queued:
            if self._enqueue_locked(self._MANUAL_CHECK):
                self._manual_check_queued = True

    def _enqueue_locked(self, command: str) -> bool:
        try:
            self._commands.put_nowait(command)
            return True
        except queue.Full:
            logger.error("Update command queue reached its fixed bound")
            return False

    def _ensure_open_locked(self) -> None:
        if self._closed:
            raise CoordinatorClosedError("update coordinator is closed")

    def _run(self) -> None:
        next_automatic = time.monotonic() + self._check_interval_seconds
        while not self._stop.is_set():
            with self._lock:
                automatic = self._automatic_enabled
            timeout = 1.0
            if automatic:
                timeout = max(0.0, min(1.0, next_automatic - time.monotonic()))
            try:
                command = self._commands.get(timeout=timeout)
            except queue.Empty:
                if automatic and time.monotonic() >= next_automatic:
                    with self._lock:
                        self._queue_check_locked(automatic=True)
                    next_automatic = time.monotonic() + self._check_interval_seconds
                continue

            if self._stop.is_set() or command == self._SHUTDOWN:
                break
            if command == self._PREFERENCE_CHANGED:
                with self._lock:
                    self._preference_queued = False
                    if self._automatic_enabled:
                        next_automatic = time.monotonic() + self._check_interval_seconds
                continue
            if command == self._AUTOMATIC_CHECK:
                with self._lock:
                    self._automatic_check_queued = False
                    should_check = self._automatic_enabled
                if should_check:
                    self._perform_check()
                next_automatic = time.monotonic() + self._check_interval_seconds
            elif command == self._MANUAL_CHECK:
                with self._lock:
                    self._manual_check_queued = False
                self._perform_check()
            elif command == self._DOWNLOAD:
                with self._lock:
                    self._download_queued = False
                self._perform_download()
            elif command == self._CONFIRM:
                with self._lock:
                    self._confirm_queued = False
                self._perform_confirmation()

    def _perform_check(self) -> None:
        if self._stop.is_set():
            return
        self._emit(UpdateEventKind.CHECKING)
        try:
            candidate = self._client.fetch_candidate(
                self._current_version,
                self._target,
            )
        except Exception as exc:
            logger.warning("Update discovery failed", exc_info=True)
            self._emit(UpdateEventKind.FAILED, message=str(exc))
            return
        if self._stop.is_set():
            return
        with self._lock:
            self._candidate = candidate
            self._ready_path = None
            self._ready_external_url = None
            self._install_handed_off = False
        if candidate is None:
            self._emit(UpdateEventKind.UP_TO_DATE)
        else:
            self._emit(UpdateEventKind.UPDATE_AVAILABLE, candidate=candidate)

    def _perform_download(self) -> None:
        with self._lock:
            candidate = self._candidate
        if candidate is None or self._stop.is_set():
            return
        if candidate.manifest.schema_version == 1:
            with self._lock:
                self._ready_external_url = candidate.manifest.release_notes_url
            self._emit(
                UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED,
                candidate=candidate,
            )
            return
        if candidate.package.platform == "ios":
            if candidate.manifest.schema_version == 3:
                expected_url = (
                    "https://github.com/Chouwzi/UTHelper/releases/tag/"
                    f"v{candidate.manifest.release_version}"
                )
                package = candidate.package
                if (
                    package.install_channel != "sideload"
                    or package.signature_kind != "unsigned-resign-required"
                    or package.install_strategy.get("kind") != "manual_sideload"
                    or set(package.install_strategy) != {"kind"}
                    or candidate.manifest.release_notes_url != expected_url
                ):
                    self._emit(
                        UpdateEventKind.FAILED,
                        candidate=candidate,
                        message="invalid iOS sideload release page",
                    )
                    return
                with self._lock:
                    self._ready_external_url = expected_url
                self._emit(
                    UpdateEventKind.MANUAL_DOWNLOAD_REQUIRED,
                    candidate=candidate,
                )
                return

            store_url = candidate.package.install_strategy.get("url", "")
            try:
                parsed = urllib.parse.urlsplit(store_url)
                port = parsed.port
            except ValueError:
                parsed = urllib.parse.SplitResult("", "", "", "", "")
                port = None
            if (
                parsed.scheme != "https"
                or parsed.hostname not in {"apps.apple.com", "testflight.apple.com"}
                or parsed.username is not None
                or parsed.password is not None
                or port not in (None, 443)
                or parsed.fragment
            ):
                self._emit(UpdateEventKind.FAILED, candidate=candidate, message="missing store URL")
                return
            with self._lock:
                self._ready_external_url = store_url
            self._emit(UpdateEventKind.READY_TO_INSTALL, candidate=candidate)
            return

        self._cancel_download.clear()
        self._emit(UpdateEventKind.DOWNLOADING, candidate=candidate, progress=0.0)
        downloaded_path: Path | None = None
        try:
            downloaded_path = self._downloader.download(
                candidate.package,
                cancel=self._cancel_download,
                progress=lambda done, total: self._emit(
                    UpdateEventKind.DOWNLOAD_PROGRESS,
                    candidate=candidate,
                    progress=min(1.0, max(0.0, done / total)) if total else 0.0,
                ),
            )
            if self._cancel_download.is_set() or self._stop.is_set():
                raise DownloadCancelled("download cancelled")
            result = self._verifier.verify(downloaded_path, candidate)
            if not result.verified:
                downloaded_path.unlink(missing_ok=True)
                downloaded_path = None
                self._emit(UpdateEventKind.FAILED, candidate=candidate, message=result.reason)
                return
            with self._lock:
                self._ready_path = downloaded_path
            self._emit(UpdateEventKind.READY_TO_INSTALL, candidate=candidate, progress=1.0)
        except DownloadCancelled:
            self._remove_downloaded_path(downloaded_path)
            self._emit(UpdateEventKind.CANCELLED, candidate=candidate)
        except Exception as exc:
            self._remove_downloaded_path(downloaded_path)
            if self._cancel_download.is_set():
                self._emit(UpdateEventKind.CANCELLED, candidate=candidate)
            else:
                logger.warning("Update download or verification failed", exc_info=True)
                self._emit(UpdateEventKind.FAILED, candidate=candidate, message=str(exc))

    @staticmethod
    def _remove_downloaded_path(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove rejected update package", exc_info=True)

    def _perform_confirmation(self) -> None:
        with self._lock:
            candidate = self._candidate
            path = self._ready_path
            external_url = self._ready_external_url
        if candidate is None or self._stop.is_set():
            return
        try:
            if external_url is not None:
                acknowledged = bool(self._external_opener(external_url))
                reason = ""
            elif path is not None:
                result = self._launcher.launch(path, candidate.package)
                acknowledged = result.acknowledged
                reason = result.reason
            else:
                return
            if acknowledged:
                with self._lock:
                    self._install_handed_off = True
            self._emit(
                UpdateEventKind.INSTALL_LAUNCHED if acknowledged else UpdateEventKind.FAILED,
                candidate=candidate,
                message=reason,
            )
        except Exception as exc:
            logger.warning("Update installer launch failed", exc_info=True)
            self._emit(UpdateEventKind.FAILED, candidate=candidate, message=str(exc))

    def _cancel_launcher(self) -> None:
        try:
            self._launcher.cancel()
        except Exception:
            logger.debug("Update launcher cancellation failed", exc_info=True)

    def _emit(
        self,
        kind: UpdateEventKind,
        *,
        candidate: UpdateCandidate | None = None,
        progress: float | None = None,
        message: str = "",
    ) -> None:
        try:
            self._event_sink(UpdateEvent(kind, candidate, progress, message))
        except Exception:
            logger.warning("Update event sink failed", exc_info=True)


__all__ = [
    "CoordinatorClosedError",
    "UpdateCoordinator",
    "UpdateEvent",
    "UpdateEventKind",
]
