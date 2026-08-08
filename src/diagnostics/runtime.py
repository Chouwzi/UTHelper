"""Chain-safe exception capture and process lifecycle diagnostics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import faulthandler
import json
import logging
import math
import os
from pathlib import Path
import platform
import re
import secrets
import stat
import sys
import threading
from typing import Any, Protocol, TextIO

from diagnostics.models import AppPhase, CrashConsent, DiagnosticContext
from diagnostics.redaction import build_report, sanitize_log_text
from diagnostics.release_config import load_runtime_public_dsn
from diagnostics.spool import (
    DiagnosticSpool,
    _delete_exact_regular,
    _FileIdentity,
    _open_binary_no_follow,
    _path_identity,
    MAX_AGE,
)
from diagnostics.transport import DiagnosticConsentGate, DiagnosticDeliveryWorker
from diagnostics.windows_evidence import (
    RUN_STATE_SCHEMA_VERSION,
    EventReader,
    RunState,
    WindowsCrashEvidence,
    find_recent_application_error,
    read_windows_application_errors,
)


MAX_RUN_STATE_BYTES = 4096
MAX_FAULT_LOG_BYTES = 256 * 1024
MAX_FLUTTER_BRIDGE_BYTES = 64 * 1024
MAX_FLUTTER_RECORD_BYTES = 4096
MAX_FLUTTER_RECORDS = 64
FLUTTER_FUTURE_TOLERANCE = timedelta(minutes=10)
HEARTBEAT_INTERVAL_SECONDS = 60.0
HEARTBEAT_JOIN_SECONDS = 0.5

_RUN_STATE_KEYS = frozenset(
    (
        "app_version",
        "clean",
        "executable_basename",
        "last_heartbeat",
        "phase",
        "schema_version",
        "started_at",
    )
)

_FLUTTER_RECORD_KEYS = frozenset(
    ("occurred_at", "phase", "runtime_type", "symbols")
)
_FLUTTER_SYMBOL = re.compile(r"^[A-Za-z_][A-Za-z0-9_.<>-]{0,127}$")
_FLUTTER_MINUTE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z$"
)
_LOGGER = logging.getLogger(__name__)

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_WRITE = 0x40000000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    _OPEN_EXISTING = 3
    _OPEN_ALWAYS = 4
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_ATTRIBUTE_DIRECTORY = 0x10
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


class _Delivery(Protocol):
    def flush_once(self, consent: CrashConsent) -> object: ...


@dataclass(frozen=True, slots=True)
class _HookSet:
    sys_hook: Any
    thread_hook: Any
    unraisable_hook: Any

    @classmethod
    def capture(cls) -> "_HookSet":
        return cls(sys.excepthook, threading.excepthook, sys.unraisablehook)


@dataclass(frozen=True, slots=True)
class _PageHook:
    page: Any
    previous: Any
    owned: Any


@dataclass(frozen=True, slots=True)
class _AsyncHook:
    loop: asyncio.AbstractEventLoop
    previous: Any
    owned: Any


@dataclass(frozen=True, slots=True)
class FlutterErrorEvidence:
    """One strictly parsed, path-free Flutter runner error record."""

    runtime_type: str
    symbols: tuple[str, ...]
    phase: AppPhase
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class _LoadedFlutterRecords:
    evidence: tuple[FlutterErrorEvidence, ...]
    identity: _FileIdentity


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.lstat()
    except OSError:
        return None
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    if stat.S_ISLNK(metadata.st_mode) or attributes & reparse_flag:
        return None
    return int(metadata.st_dev), int(metadata.st_ino)


def _is_regular(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISREG(metadata.st_mode) and not attributes & reparse_flag


def _normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _safe_process_basename(value: str | None) -> str:
    candidate = str(value or sys.executable).replace("\\", "/").rsplit("/", 1)[-1]
    if (
        not candidate
        or len(candidate) > 128
        or not candidate[0].isalnum()
        or any(not (character.isascii() and (character.isalnum() or character in "._-")) for character in candidate)
    ):
        return "python.exe"
    return candidate


def _pin_windows_directory(path: Path) -> int | None:
    if os.name != "nt":
        return None
    handle = _kernel32.CreateFileW(
        str(path),
        _FILE_READ_ATTRIBUTES,
        _FILE_SHARE_READ_WRITE,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        return None
    information = _ByHandleFileInformation()
    if not _kernel32.GetFileInformationByHandle(
        handle,
        ctypes.byref(information),
    ):
        _kernel32.CloseHandle(handle)
        return None
    attributes = int(information.dwFileAttributes)
    if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
        _kernel32.CloseHandle(handle)
        return None
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
        _kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _open_fresh_fault_file(path: Path) -> TextIO:
    if os.name == "nt":
        handle = _kernel32.CreateFileW(
            str(path),
            _GENERIC_WRITE | _FILE_READ_ATTRIBUTES,
            _FILE_SHARE_READ_WRITE,
            None,
            _OPEN_ALWAYS,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == _INVALID_HANDLE_VALUE:
            raise OSError("cannot open diagnostic fault file")
        information = _ByHandleFileInformation()
        if not _kernel32.GetFileInformationByHandle(
            handle,
            ctypes.byref(information),
        ):
            _kernel32.CloseHandle(handle)
            raise OSError("cannot inspect diagnostic fault file")
        attributes = int(information.dwFileAttributes)
        if attributes & (_FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT):
            _kernel32.CloseHandle(handle)
            raise OSError("diagnostic fault file is unsafe")
        try:
            descriptor = msvcrt.open_osfhandle(
                int(handle),
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            _kernel32.CloseHandle(handle)
            raise
    else:
        flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise OSError("diagnostic fault file is unsafe")
    try:
        os.ftruncate(descriptor, 0)
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except Exception:
        os.close(descriptor)
        raise


def _parse_flutter_record(payload: object) -> FlutterErrorEvidence:
    if not isinstance(payload, dict) or set(payload) != _FLUTTER_RECORD_KEYS:
        raise ValueError("invalid Flutter bridge fields")
    runtime_type = payload["runtime_type"]
    raw_symbols = payload["symbols"]
    phase = payload["phase"]
    raw_time = payload["occurred_at"]
    if not isinstance(runtime_type, str) or _FLUTTER_SYMBOL.fullmatch(runtime_type) is None:
        raise ValueError("invalid Flutter runtime type")
    if (
        not isinstance(raw_symbols, list)
        or len(raw_symbols) > 16
        or any(
            not isinstance(symbol, str) or _FLUTTER_SYMBOL.fullmatch(symbol) is None
            for symbol in raw_symbols
        )
    ):
        raise ValueError("invalid Flutter stack symbols")
    if phase not in (AppPhase.BOOT.value, AppPhase.GUI.value):
        raise ValueError("invalid Flutter phase")
    if not isinstance(raw_time, str) or _FLUTTER_MINUTE.fullmatch(raw_time) is None:
        raise ValueError("invalid Flutter occurrence time")
    try:
        occurred_at = datetime.strptime(
            raw_time,
            "%Y-%m-%dT%H:%M:00.000Z",
        ).replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError("invalid Flutter occurrence time") from exc
    return FlutterErrorEvidence(
        runtime_type=runtime_type,
        symbols=tuple(raw_symbols),
        phase=AppPhase(phase),
        occurred_at=occurred_at,
    )


def _parse_flutter_payload(payload: bytes) -> tuple[FlutterErrorEvidence, ...]:
    if not payload or len(payload) > MAX_FLUTTER_BRIDGE_BYTES or not payload.endswith(b"\n"):
        raise ValueError("invalid Flutter bridge size")
    lines = payload.splitlines()
    if not lines or len(lines) > MAX_FLUTTER_RECORDS:
        raise ValueError("invalid Flutter bridge record count")
    evidence: list[FlutterErrorEvidence] = []
    seen: set[FlutterErrorEvidence] = set()
    for line in lines:
        if not line or len(line) > MAX_FLUTTER_RECORD_BYTES:
            raise ValueError("invalid Flutter bridge record size")
        try:
            decoded = json.loads(line.decode("ascii"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Flutter bridge JSON") from exc
        record = _parse_flutter_record(decoded)
        if record not in seen:
            evidence.append(record)
            seen.add(record)
    return tuple(evidence)


def _discard_invalid_flutter_bridge(
    path: Path,
    identity: _FileIdentity,
    reason: str,
) -> None:
    if _delete_exact_regular(path, expected=identity):
        _LOGGER.warning(
            "%s",
            sanitize_log_text(
                f"discarded invalid Flutter diagnostics bridge ({reason})"
            ),
        )


def _load_flutter_records(path: Path) -> _LoadedFlutterRecords | None:
    candidate = Path(path)
    try:
        identity = _path_identity(candidate, expect_directory=False)
    except (OSError, ValueError):
        return None
    if identity.size > MAX_FLUTTER_BRIDGE_BYTES:
        _discard_invalid_flutter_bridge(candidate, identity, "oversized")
        return None
    opened = _open_binary_no_follow(candidate, expected=identity)
    if opened is None:
        return None
    stream, opened_identity = opened
    try:
        with stream:
            payload = stream.read(MAX_FLUTTER_BRIDGE_BYTES + 1)
        current = _path_identity(candidate, expect_directory=False)
    except (OSError, ValueError):
        return None
    if (
        opened_identity.key != current.key
        or opened_identity.size != current.size
        or len(payload) != opened_identity.size
    ):
        return None
    try:
        evidence = _parse_flutter_payload(payload)
    except ValueError:
        _discard_invalid_flutter_bridge(candidate, opened_identity, "malformed")
        return None
    return _LoadedFlutterRecords(evidence=evidence, identity=opened_identity)


def consume_flutter_records(path: Path) -> tuple[FlutterErrorEvidence, ...]:
    """Strictly consume one direct regular Flutter bridge file exactly once."""

    loaded = _load_flutter_records(Path(path))
    if loaded is None:
        return ()
    if not _delete_exact_regular(Path(path), expected=loaded.identity):
        return ()
    return loaded.evidence


class DiagnosticRuntime:
    """Own global diagnostic hooks for exactly one bounded application run."""

    def __init__(
        self,
        *,
        data_dir: Path,
        spool: DiagnosticSpool,
        delivery: _Delivery,
        context_provider: Callable[[AppPhase, bool], DiagnosticContext],
        consent_provider: Callable[[], CrashConsent],
        delivery_executor: Executor | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        emergency_writer: TextIO | None = None,
        executable_basename: str | None = None,
        evidence_reader: EventReader = read_windows_application_errors,
        heartbeat_interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        consent_gate: DiagnosticConsentGate | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.diagnostics_dir = self.data_dir / "diagnostics"
        self.run_state_path = self.diagnostics_dir / "run-state.json"
        self.fault_path = self.diagnostics_dir / "native-fault.log"
        self.flutter_bridge_path = self.diagnostics_dir / "flutter-errors.jsonl"
        self._guard_path = self.diagnostics_dir / ".diagnostic-runtime.operation"
        self.spool = spool
        self.delivery = delivery
        self._context_provider = context_provider
        self._consent_provider = consent_provider
        self._consent_gate = consent_gate or DiagnosticConsentGate()
        self._executor = delivery_executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="uthelper-diagnostics",
        )
        self._owns_executor = delivery_executor is None
        self._clock = clock
        self._executable_basename = _safe_process_basename(executable_basename)
        self._evidence_reader = evidence_reader
        heartbeat_interval = float(heartbeat_interval_seconds)
        if not math.isfinite(heartbeat_interval):
            heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS
        self._heartbeat_interval_seconds = max(0.01, min(heartbeat_interval, 300.0))
        self._emergency_writer = emergency_writer or sys.__stderr__
        self._local = threading.local()
        self._capture_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self._previous: _HookSet | None = None
        self._owned_hooks: _HookSet | None = None
        self._page_hooks: list[_PageHook] = []
        self._async_hooks: list[_AsyncHook] = []
        self._phase = AppPhase.BOOT
        self._unclean_previous_exit = False
        self._pending_windows_evidence: WindowsCrashEvidence | None = None
        self._run_started_at: datetime | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._diagnostics_identity: tuple[int, int] | None = None
        self._root_handle: int | None = None
        self._guard_stream: TextIO | None = None
        self._guard_identity: tuple[int, int] | None = None
        self._marker_identity: tuple[int, int] | None = None
        self._fault_identity: tuple[int, int] | None = None
        self._fault_stream: TextIO | None = None
        self._fault_owned = False
        self._fault_superseded = False
        self._previous_fault_enable: Any = None
        self._owned_fault_enable: Any = None
        self._started = False
        self._closed = False
        self._consent_unsubscribe: Callable[[], None] | None = None

    @property
    def started(self) -> bool:
        return self._started

    def context(self, phase: AppPhase | None = None) -> DiagnosticContext:
        """Return current safe context with prior unclean-state evidence."""
        selected_phase = self._phase if phase is None else phase
        return self._context_provider(
            selected_phase,
            self._unclean_previous_exit,
        )

    def bind_consent_changes(
        self,
        subscribe: Callable[[Callable[[], None]], Callable[[], None]],
    ) -> None:
        """Bind successful Settings saves to the live consent boundary once."""
        if not callable(subscribe):
            return
        with self._lifecycle_lock:
            if self._closed or self._consent_unsubscribe is not None:
                return
        try:
            unsubscribe = subscribe(
                lambda: self.apply_consent(self._consent_provider())
            )
        except Exception:
            self._write_emergency("diagnostic consent subscription failed\n")
            return
        if not callable(unsubscribe):
            self._write_emergency("diagnostic consent subscription failed\n")
            return
        release_immediately = False
        with self._lifecycle_lock:
            if self._closed or self._consent_unsubscribe is not None:
                release_immediately = True
            else:
                self._consent_unsubscribe = unsubscribe
        if release_immediately:
            try:
                unsubscribe()
            except Exception:
                self._write_emergency("diagnostic consent unsubscribe failed\n")

    def apply_consent(self, consent: CrashConsent | str) -> None:
        """Apply a newly persisted choice without waiting for process restart."""
        try:
            selected = CrashConsent(consent)
        except (TypeError, ValueError):
            return
        with self._lifecycle_lock:
            if not self._started or self._closed:
                return
        if selected is not CrashConsent.ENABLED:
            self._set_live_consent(selected, delete_pending=True)
            return
        self._set_live_consent(selected, delete_pending=False)
        try:
            self._executor.submit(self.delivery.flush_once, selected)
        except Exception:
            self._write_emergency("diagnostic delivery unavailable\n")

    def start(self) -> None:
        """Install hooks and submit one non-blocking delivery attempt."""
        with self._lifecycle_lock:
            if self._started or self._closed:
                return
            self._started = True
            self._prepare_diagnostics_dir()
            now = _normalized_utc(self._clock())
            previous_marker = self._read_previous_marker(now)
            self._unclean_previous_exit = previous_marker is not None
            if previous_marker is not None:
                self._pending_windows_evidence = find_recent_application_error(
                    previous_marker,
                    now=now,
                    reader=self._evidence_reader,
                )
            self._run_started_at = now
            self._write_run_state(AppPhase.BOOT)
            self._start_heartbeat()
            self._previous = _HookSet.capture()
            self._install_hooks()
            self._enable_faulthandler()
            try:
                consent = self._consent_provider()
            except Exception:
                consent = CrashConsent.NOT_ASKED
            self._set_live_consent(
                consent,
                delete_pending=consent is not CrashConsent.ENABLED,
            )
            if consent is CrashConsent.ENABLED:
                self._import_flutter_records()
            if consent is CrashConsent.ENABLED:
                try:
                    self._executor.submit(self.delivery.flush_once, consent)
                except Exception:
                    self._write_emergency("diagnostic delivery unavailable\n")

    def record_exception(
        self,
        exc: BaseException,
        phase: AppPhase = AppPhase.GUI,
    ) -> str | None:
        """Persist one scrubbed report and return its ephemeral event reference."""
        return self._record_exception(exc, phase, occurred_at=None)

    def _record_exception(
        self,
        exc: BaseException,
        phase: AppPhase,
        *,
        occurred_at: datetime | None,
    ) -> str | None:
        if not isinstance(exc, BaseException):
            return None
        if getattr(self._local, "capturing", False):
            return None
        self._local.capturing = True
        try:
            with self._capture_lock:
                allowed, reference = self._consent_gate.run_if_enabled(
                    lambda: self._capture_enabled_exception(
                        exc,
                        phase,
                        occurred_at=occurred_at,
                    )
                )
                return reference if allowed else None
        except Exception:
            self._write_emergency("diagnostic capture failed\n")
            return None
        finally:
            self._local.capturing = False

    def _capture_enabled_exception(
        self,
        exc: BaseException,
        phase: AppPhase,
        *,
        occurred_at: datetime | None,
    ) -> str | None:
        context = self.context(phase)
        evidence = self._peek_windows_evidence(context)
        report = build_report(
            exc,
            context,
            occurred_at=occurred_at,
            native_exception_code=(
                evidence.exception_code if evidence is not None else None
            ),
            faulting_module=(
                evidence.faulting_module_basename if evidence is not None else None
            ),
        )
        outcome = self.spool.enqueue(report)
        pending = self.spool.pending()
        if outcome.stored:
            durable = next(
                (
                    item.report
                    for item in pending
                    if item.report.event_id == report.event_id
                ),
                None,
            )
        elif outcome.deduplicated:
            durable = next(
                (
                    item.report
                    for item in pending
                    if item.report.fingerprint == report.fingerprint
                ),
                None,
            )
        else:
            durable = None
        if durable is not None and evidence is not None:
            self._commit_windows_evidence(evidence)
        return durable.event_id.hex if durable is not None else None

    def _set_live_consent(
        self,
        consent: CrashConsent,
        *,
        delete_pending: bool,
    ) -> None:
        def cleanup() -> None:
            try:
                self.spool.clear()
            except Exception:
                self._write_emergency("diagnostic consent cleanup failed\n")
            self._discard_flutter_bridge()

        try:
            self._consent_gate.set(
                consent,
                while_locked=cleanup if delete_pending else None,
            )
        except Exception:
            self._write_emergency("diagnostic consent update failed\n")

    def _discard_flutter_bridge(self) -> None:
        if not self._root_is_owned():
            return
        try:
            identity = _path_identity(
                self.flutter_bridge_path,
                expect_directory=False,
            )
        except (OSError, ValueError):
            return
        _delete_exact_regular(self.flutter_bridge_path, expected=identity)

    def _import_flutter_records(self) -> None:
        if not self._root_is_owned():
            return
        loaded = _load_flutter_records(self.flutter_bridge_path)
        if loaded is None:
            return
        now = _normalized_utc(self._clock())
        if any(
            evidence.occurred_at < now - MAX_AGE
            or evidence.occurred_at > now + FLUTTER_FUTURE_TOLERANCE
            for evidence in loaded.evidence
        ):
            _delete_exact_regular(
                self.flutter_bridge_path,
                expected=loaded.identity,
            )
            return
        durable = True
        for evidence in loaded.evidence:
            top_symbol = evidence.symbols[0] if evidence.symbols else "unknown"
            exception_name = (
                f"Flutter.{evidence.runtime_type[:56]}.{top_symbol[:56]}"
            )
            flutter_exception = type(exception_name, (RuntimeError,), {})()
            reference = self._record_exception(
                flutter_exception,
                evidence.phase,
                occurred_at=evidence.occurred_at,
            )
            if reference is None:
                durable = False
        if durable:
            _delete_exact_regular(
                self.flutter_bridge_path,
                expected=loaded.identity,
            )

    def mark_phase(self, phase: AppPhase) -> None:
        if not isinstance(phase, AppPhase):
            return
        with self._lifecycle_lock:
            if not self._started or self._closed:
                return
            self._phase = phase
            self._write_run_state(phase)

    def attach_page(self, page: Any) -> None:
        """Wrap a Flet page error callback without consuming string payloads."""
        with self._lifecycle_lock:
            if not self._started or self._closed:
                return
            if any(item.page is page and page.on_error is item.owned for item in self._page_hooks):
                return
            previous = getattr(page, "on_error", None)

            def page_error(event: Any) -> None:
                exc = getattr(event, "exception", None)
                if not isinstance(exc, BaseException):
                    candidate = getattr(event, "error", None)
                    exc = candidate if isinstance(candidate, BaseException) else None
                if exc is not None:
                    self.record_exception(exc, AppPhase.GUI)
                if callable(previous):
                    self._chain(previous, event)

            page.on_error = page_error
            self._page_hooks.append(_PageHook(page, previous, page_error))

    def attach_asyncio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture actual loop exceptions and preserve the prior loop handler."""
        with self._lifecycle_lock:
            if not self._started or self._closed:
                return
            if any(
                item.loop is loop and loop.get_exception_handler() is item.owned
                for item in self._async_hooks
            ):
                return
            previous = loop.get_exception_handler()

            def async_error(
                active_loop: asyncio.AbstractEventLoop,
                context: dict[str, Any],
            ) -> None:
                exc = context.get("exception")
                if isinstance(exc, BaseException):
                    self.record_exception(exc, AppPhase.GUI)
                if callable(previous):
                    self._chain(previous, active_loop, context)
                else:
                    try:
                        active_loop.default_exception_handler(context)
                    except Exception:
                        self._write_emergency("diagnostic hook chain failed\n")

            loop.set_exception_handler(async_error)
            self._async_hooks.append(_AsyncHook(loop, previous, async_error))

    def close(self, clean: bool) -> None:
        """Restore owned hooks and release resources without waiting on delivery."""
        heartbeat: threading.Thread | None = None
        consent_unsubscribe: Callable[[], None] | None = None
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            consent_unsubscribe = self._consent_unsubscribe
            self._consent_unsubscribe = None
            self.shutdown_event.set()
            heartbeat = self._heartbeat_thread
            self._heartbeat_thread = None
            self._restore_page_hooks()
            self._restore_async_hooks()
            self._restore_global_hooks()
            self._disable_faulthandler()
            if clean:
                self._remove_owned_marker()
            try:
                self._executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                self._write_emergency("diagnostic delivery shutdown failed\n")
            self._release_operation_guard()
            self._release_root_pin()
        if consent_unsubscribe is not None:
            try:
                consent_unsubscribe()
            except Exception:
                self._write_emergency("diagnostic consent unsubscribe failed\n")
        if heartbeat is not None and heartbeat is not threading.current_thread():
            heartbeat.join(timeout=HEARTBEAT_JOIN_SECONDS)

    def _peek_windows_evidence(
        self,
        context: DiagnosticContext,
    ) -> WindowsCrashEvidence | None:
        if not context.unclean_previous_exit:
            return None
        with self._lifecycle_lock:
            return self._pending_windows_evidence

    def _commit_windows_evidence(self, evidence: WindowsCrashEvidence) -> None:
        with self._lifecycle_lock:
            if self._pending_windows_evidence is evidence:
                self._pending_windows_evidence = None

    def _start_heartbeat(self) -> None:
        if not self._root_is_owned():
            return
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            name="uthelper-diagnostic-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread = heartbeat
        heartbeat.start()

    def _heartbeat_loop(self) -> None:
        while not self.shutdown_event.wait(self._heartbeat_interval_seconds):
            with self._lifecycle_lock:
                if self._closed:
                    return
                self._write_run_state(self._phase)

    def _install_hooks(self) -> None:
        previous = self._previous
        if previous is None:
            return

        def sys_hook(exc_type: type[BaseException], exc: BaseException, traceback) -> None:
            if isinstance(exc, BaseException):
                self.record_exception(exc, self._phase)
            self._chain(previous.sys_hook, exc_type, exc, traceback)

        def thread_hook(args: Any) -> None:
            exc = getattr(args, "exc_value", None)
            if isinstance(exc, BaseException):
                self.record_exception(exc, self._phase)
            self._chain(previous.thread_hook, args)

        def unraisable_hook(args: Any) -> None:
            exc = getattr(args, "exc_value", None)
            if isinstance(exc, BaseException):
                self.record_exception(exc, self._phase)
            self._chain(previous.unraisable_hook, args)

        self._owned_hooks = _HookSet(sys_hook, thread_hook, unraisable_hook)
        sys.excepthook = sys_hook
        threading.excepthook = thread_hook
        sys.unraisablehook = unraisable_hook

    def _restore_global_hooks(self) -> None:
        previous = self._previous
        owned = self._owned_hooks
        if previous is None or owned is None:
            return
        if sys.excepthook is owned.sys_hook:
            sys.excepthook = previous.sys_hook
        if threading.excepthook is owned.thread_hook:
            threading.excepthook = previous.thread_hook
        if sys.unraisablehook is owned.unraisable_hook:
            sys.unraisablehook = previous.unraisable_hook

    def _restore_page_hooks(self) -> None:
        for item in reversed(self._page_hooks):
            try:
                if getattr(item.page, "on_error", None) is item.owned:
                    item.page.on_error = item.previous
            except Exception:
                self._write_emergency("diagnostic page hook restore failed\n")
        self._page_hooks.clear()

    def _restore_async_hooks(self) -> None:
        for item in reversed(self._async_hooks):
            try:
                if item.loop.is_closed():
                    continue
                if item.loop.get_exception_handler() is item.owned:
                    item.loop.set_exception_handler(item.previous)
            except Exception:
                self._write_emergency("diagnostic async hook restore failed\n")
        self._async_hooks.clear()

    def _chain(self, function: Any, *args: Any) -> None:
        if not callable(function):
            return
        try:
            function(*args)
        except Exception:
            self._write_emergency("diagnostic hook chain failed\n")

    def _write_emergency(self, message: str) -> None:
        try:
            self._emergency_writer.write(message)
        except Exception:
            pass

    def _prepare_diagnostics_dir(self) -> None:
        try:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
            metadata = self.diagnostics_dir.lstat()
            attributes = int(getattr(metadata, "st_file_attributes", 0))
            reparse_flag = int(
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if not stat.S_ISDIR(metadata.st_mode) or attributes & reparse_flag:
                return
            identity = int(metadata.st_dev), int(metadata.st_ino)
            root_handle = _pin_windows_directory(self.diagnostics_dir)
            if os.name == "nt" and root_handle is None:
                return
            if _identity(self.diagnostics_dir) != identity:
                if root_handle is not None:
                    _kernel32.CloseHandle(root_handle)
                return
            self._diagnostics_identity = identity
            self._root_handle = root_handle
            self._create_operation_guard()
            if self._guard_stream is None:
                self._release_root_pin()
                self._diagnostics_identity = None
        except OSError:
            self._diagnostics_identity = None

    def _create_operation_guard(self) -> None:
        if not self._root_is_owned():
            return
        try:
            if self._guard_path.exists() or self._guard_path.is_symlink():
                if not _is_regular(self._guard_path):
                    return
                self._guard_path.unlink()
            stream = self._guard_path.open("x", encoding="ascii")
            stream.write("UTHelper diagnostic runtime\n")
            stream.flush()
            os.fsync(stream.fileno())
            identity = _identity(self._guard_path)
            if identity is None or not self._root_is_owned():
                stream.close()
                return
            self._guard_stream = stream
            self._guard_identity = identity
        except OSError:
            if "stream" in locals() and not stream.closed:
                stream.close()

    def _release_operation_guard(self) -> None:
        stream = self._guard_stream
        self._guard_stream = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if (
            self._root_is_owned()
            and self._guard_identity is not None
            and _identity(self._guard_path) == self._guard_identity
        ):
            try:
                self._guard_path.unlink()
            except OSError:
                pass
        self._guard_identity = None

    def _release_root_pin(self) -> None:
        handle = self._root_handle
        self._root_handle = None
        if handle is not None and os.name == "nt":
            _kernel32.CloseHandle(handle)

    def _root_is_owned(self) -> bool:
        return (
            self._diagnostics_identity is not None
            and _identity(self.diagnostics_dir) == self._diagnostics_identity
        )

    def _read_previous_marker(self, now: datetime) -> RunState | None:
        if not self._root_is_owned() or not _is_regular(self.run_state_path):
            return None
        try:
            if self.run_state_path.stat().st_size > MAX_RUN_STATE_BYTES:
                return None
            raw = self.run_state_path.read_bytes()
            if len(raw) > MAX_RUN_STATE_BYTES:
                return None
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or set(payload) != _RUN_STATE_KEYS:
            return None
        try:
            state = RunState.model_validate(payload)
        except ValueError:
            return None
        if state.started_at > state.last_heartbeat or state.last_heartbeat > now:
            return None
        return state

    def _write_run_state(self, phase: AppPhase) -> None:
        if not self._root_is_owned():
            return
        try:
            context = self.context(phase)
            started_at = self._run_started_at
            if started_at is None:
                return
            state = RunState(
                app_version=context.app_version,
                clean=False,
                executable_basename=self._executable_basename,
                last_heartbeat=_normalized_utc(self._clock()).replace(
                    second=0,
                    microsecond=0,
                ),
                phase=phase,
                schema_version=RUN_STATE_SCHEMA_VERSION,
                started_at=started_at.replace(second=0, microsecond=0),
            )
            payload = state.model_dump(mode="json")
            encoded = (
                json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
                + "\n"
            ).encode("ascii")
            if len(encoded) > MAX_RUN_STATE_BYTES:
                return
        except Exception:
            return
        temporary = self.diagnostics_dir / (
            f".{self.run_state_path.name}.{secrets.token_hex(8)}.tmp"
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if not self._root_is_owned():
                return
            os.replace(temporary, self.run_state_path)
            if _is_regular(self.run_state_path):
                self._marker_identity = _identity(self.run_state_path)
        except OSError:
            self._marker_identity = None
        finally:
            try:
                if _is_regular(temporary):
                    temporary.unlink()
            except OSError:
                pass

    def _remove_owned_marker(self) -> None:
        if not self._root_is_owned() or self._marker_identity is None:
            return
        try:
            if _identity(self.run_state_path) == self._marker_identity:
                self.run_state_path.unlink()
        except OSError:
            pass
        finally:
            self._marker_identity = None

    def _enable_faulthandler(self) -> None:
        if not self._root_is_owned() or faulthandler.is_enabled():
            return
        try:
            stream = _open_fresh_fault_file(self.fault_path)
            identity = _identity(self.fault_path)
            if identity is None or not self._root_is_owned():
                stream.close()
                return
            previous_enable = faulthandler.enable
            previous_enable(file=stream, all_threads=True)

            def tracked_enable(*args: Any, **kwargs: Any) -> Any:
                with self._lifecycle_lock:
                    result = previous_enable(*args, **kwargs)
                    self._fault_superseded = True
                    return result

            faulthandler.enable = tracked_enable
            self._previous_fault_enable = previous_enable
            self._owned_fault_enable = tracked_enable
            self._fault_stream = stream
            self._fault_identity = identity
            self._fault_owned = True
        except (OSError, RuntimeError, ValueError):
            if "stream" in locals() and not stream.closed:
                stream.close()

    def _disable_faulthandler(self) -> None:
        stream = self._fault_stream
        if (
            self._owned_fault_enable is not None
            and faulthandler.enable is self._owned_fault_enable
        ):
            faulthandler.enable = self._previous_fault_enable
        self._owned_fault_enable = None
        self._previous_fault_enable = None
        if self._fault_owned and not self._fault_superseded:
            try:
                faulthandler.disable()
            except RuntimeError:
                pass
        self._fault_owned = False
        self._fault_stream = None
        if stream is not None:
            try:
                stream.flush()
            except OSError:
                pass
            try:
                if os.fstat(stream.fileno()).st_size > MAX_FAULT_LOG_BYTES:
                    os.ftruncate(stream.fileno(), MAX_FAULT_LOG_BYTES)
            except OSError:
                pass
            try:
                stream.close()
            except OSError:
                pass


def create_default_runtime(
    data_dir: Path,
    *,
    development: bool,
) -> DiagnosticRuntime:
    """Build the production runtime without importing the Flet UI runtime."""
    from importlib.metadata import PackageNotFoundError, version

    from config import settings, subscribe_settings_saved
    from core.version import APP_VERSION

    try:
        flet_version = version("flet")
    except PackageNotFoundError:
        flet_version = "unknown"

    def consent_provider() -> CrashConsent:
        try:
            return CrashConsent(settings.CRASH_REPORTING_CONSENT)
        except (AttributeError, ValueError):
            return CrashConsent.NOT_ASKED

    def context_provider(phase: AppPhase, unclean: bool) -> DiagnosticContext:
        return DiagnosticContext(
            source_root=Path(__file__).resolve().parents[1],
            app_version=APP_VERSION,
            release_channel="stable",
            install_type="source" if development else "packaged",
            os_family=platform.system() or "unknown",
            os_version=platform.release() or "unknown",
            architecture=platform.machine() or "unknown",
            python_version=platform.python_version(),
            flet_version=flet_version,
            phase=phase,
            window_state="unknown",
            unclean_previous_exit=unclean,
        )

    spool = DiagnosticSpool(Path(data_dir) / "telemetry" / "pending")
    consent_gate = DiagnosticConsentGate()
    delivery = DiagnosticDeliveryWorker(
        spool,
        dsn=load_runtime_public_dsn(development=development),
        consent_gate=consent_gate,
    )
    runtime = DiagnosticRuntime(
        data_dir=Path(data_dir),
        spool=spool,
        delivery=delivery,
        context_provider=context_provider,
        consent_provider=consent_provider,
        executable_basename=(
            "UTHelper.exe"
            if sys.platform == "win32" and not development
            else None
        ),
        consent_gate=consent_gate,
    )
    runtime.bind_consent_changes(subscribe_settings_saved)
    return runtime


__all__ = [
    "MAX_FAULT_LOG_BYTES",
    "MAX_RUN_STATE_BYTES",
    "HEARTBEAT_INTERVAL_SECONDS",
    "RUN_STATE_SCHEMA_VERSION",
    "DiagnosticRuntime",
    "create_default_runtime",
]
