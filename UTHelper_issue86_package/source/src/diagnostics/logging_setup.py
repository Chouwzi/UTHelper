"""One bounded, redacted application log owned by diagnostics."""

from __future__ import annotations

import copy
import logging
import stat
import threading
import traceback
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

from diagnostics.redaction import sanitize_log_text

MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

_OWNER_MARKER = "_uthelper_diagnostic_handler"
_RUNTIME_ATTRIBUTE = "_uthelper_logging_runtime"
_CONFIGURE_LOCK = threading.RLock()
_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_NOISY_DEPENDENCY_LOGGERS = (
    "flet",
    "flet_core",
    "flet_transport",
    "httpcore",
    "httpx",
)


def startup_debug_enabled(environ: Mapping[str, str]) -> bool:
    """Enable early debug logging only through one explicit developer switch."""

    return environ.get("UTH_DEBUG_LOGGING") == "1"


def configure_dependency_logging() -> None:
    """Keep verbose dependency payloads out of the application-owned log."""

    for name in _NOISY_DEPENDENCY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class _RedactingFormatter(logging.Formatter):
    """Render a private record copy and scrub the complete persisted text."""

    def format(self, record: logging.LogRecord) -> str:
        safe_record = copy.copy(record)
        safe_record.args = ()
        safe_record.exc_info = None
        safe_record.exc_text = None
        safe_record.stack_info = None
        try:
            safe_record.msg = sanitize_log_text(record.getMessage())
        except Exception:
            safe_record.msg = "[unprintable]"

        try:
            rendered = super().format(safe_record)
        except Exception:
            rendered = "application log formatting failed"

        details: list[str] = []
        if record.exc_info:
            details.append(_format_exception(record.exc_info))
        if record.stack_info:
            details.append(sanitize_log_text(record.stack_info))
        if details:
            rendered = "\n".join((rendered, *details))
        return sanitize_log_text(rendered)


def _format_exception(
    exc_info: tuple[type[BaseException], BaseException, TracebackType | None],
) -> str:
    try:
        return "".join(traceback.format_exception(*exc_info))
    except Exception:
        return "Traceback (exception text unavailable)"


class LoggingRuntime:
    """Lifecycle owner for exactly one application rotating-file handler."""

    def __init__(
        self,
        root: logging.Logger,
        handler: RotatingFileHandler,
    ) -> None:
        self._root = root
        self._handler = handler
        self._closed = False

    @property
    def closed(self) -> bool:
        with _CONFIGURE_LOCK:
            return self._closed

    def close(self) -> None:
        """Remove and close only this runtime's handler; repeated calls are safe."""

        with _CONFIGURE_LOCK:
            if self._closed:
                return
            self._closed = True
            self._root.removeHandler(self._handler)
            self._handler.close()


def configure_logging(data_dir: Path, *, debug: bool) -> LoggingRuntime:
    """Configure or return the process-owned bounded application log."""

    root = logging.getLogger()
    level = logging.DEBUG if debug else logging.INFO
    with _CONFIGURE_LOCK:
        for handler in root.handlers:
            if not getattr(handler, _OWNER_MARKER, False):
                continue
            runtime = getattr(handler, _RUNTIME_ATTRIBUTE, None)
            if isinstance(runtime, LoggingRuntime) and not runtime._closed:
                handler.setLevel(level)
                root.setLevel(level)
                return runtime

        data_path = Path(data_dir)
        log_dir = data_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = _remove_unsafe_or_oversized_legacy_logs(data_path, log_dir)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(_RedactingFormatter(_FORMAT, datefmt=_DATE_FORMAT))
        setattr(handler, _OWNER_MARKER, True)
        runtime = LoggingRuntime(root, handler)
        setattr(handler, _RUNTIME_ATTRIBUTE, runtime)
        root.addHandler(handler)
        root.setLevel(level)
        return runtime


def _remove_unsafe_or_oversized_legacy_logs(
    data_dir: Path,
    log_dir: Path,
) -> Path:
    legacy_path = data_dir / "debug_app.log"
    active_path = log_dir / "app.log"
    recovery_path = log_dir / "app-recovery.log"
    selected_path = active_path
    paths = [legacy_path, active_path]
    paths.extend(
        log_dir / f"app.log.{index}"
        for index in range(1, BACKUP_COUNT + 1)
    )
    for path in paths:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        try:
            if stat.S_ISLNK(metadata.st_mode):
                path.unlink(missing_ok=True)
                continue
            if stat.S_ISREG(metadata.st_mode) and metadata.st_size >= MAX_LOG_BYTES:
                path.unlink(missing_ok=True)
        except OSError:
            # Previous packaged builds can keep files open with Windows delete
            # sharing disabled. A locked legacy file is never our destination.
            if path == legacy_path or path != active_path:
                continue
            # Never append to an already-oversized active file. Keep this run
            # bounded in a separate rotating recovery slot instead.
            selected_path = recovery_path

    if selected_path == recovery_path:
        recovery_paths = [recovery_path]
        recovery_paths.extend(
            log_dir / f"app-recovery.log.{index}"
            for index in range(1, BACKUP_COUNT + 1)
        )
        for path in recovery_paths:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            try:
                if stat.S_ISLNK(metadata.st_mode):
                    path.unlink(missing_ok=True)
                elif (
                    stat.S_ISREG(metadata.st_mode)
                    and metadata.st_size >= MAX_LOG_BYTES
                ):
                    path.unlink(missing_ok=True)
            except OSError:
                # Startup and activation hand-off must remain available even
                # while another Windows process still owns the recovery slot.
                continue
    return selected_path
