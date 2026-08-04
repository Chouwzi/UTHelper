"""One bounded, redacted application log owned by diagnostics."""

from __future__ import annotations

import copy
import logging
import stat
import threading
import traceback
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
        _remove_unsafe_or_oversized_legacy_logs(data_path, log_dir)
        handler = RotatingFileHandler(
            log_dir / "app.log",
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
) -> None:
    paths = [data_dir / "debug_app.log", log_dir / "app.log"]
    paths.extend(
        log_dir / f"app.log.{index}"
        for index in range(1, BACKUP_COUNT + 1)
    )
    for path in paths:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            path.unlink(missing_ok=True)
            continue
        if stat.S_ISREG(metadata.st_mode) and metadata.st_size >= MAX_LOG_BYTES:
            path.unlink(missing_ok=True)
