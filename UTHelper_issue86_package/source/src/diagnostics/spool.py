"""Atomic, bounded, local queue for validated diagnostic reports."""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import UUID

from diagnostics.models import DiagnosticReport

MAX_EVENTS = 20
MAX_TOTAL_BYTES = 1024 * 1024
MAX_AGE = timedelta(days=7)
LOCK_TIMEOUT_SECONDS = 5.0

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    _FILE_SHARE_ALL = _FILE_SHARE_READ_WRITE | 0x00000004
    _OPEN_EXISTING = 3
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_DISPOSITION_INFO_CLASS = 4
    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x80
    _WAIT_TIMEOUT = 0x102
    _INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
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

    class _FILE_DISPOSITION_INFO(ctypes.Structure):
        _fields_ = [("DeleteFile", ctypes.c_ubyte)]

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
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    ]
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
else:
    import errno
    import fcntl


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    """Outcome of attempting to add one report to the queue."""

    stored: bool = False
    deduplicated: bool = False
    too_large: bool = False


@dataclass(frozen=True, slots=True)
class QueuedReport:
    """A validated report and its owned direct-child queue path."""

    report: DiagnosticReport
    path: Path


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    key: tuple[int, int]
    size: int


@dataclass(frozen=True, slots=True)
class _ValidatedFile:
    queued: QueuedReport
    size: int
    identity: _FileIdentity


_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _stat_is_reparse(identity: os.stat_result) -> bool:
    return bool(
        getattr(identity, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _path_is_reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        return _stat_is_reparse(path.lstat())
    except OSError:
        return False


def _identity_from_stat(identity: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        key=(int(identity.st_dev), int(identity.st_ino)),
        size=int(identity.st_size),
    )


def _raise_windows_error(path: Path) -> None:
    error = ctypes.get_last_error()
    if error in {2, 3}:
        raise FileNotFoundError(error, ctypes.FormatError(error), str(path))
    raise OSError(error, ctypes.FormatError(error), str(path))


def _windows_open_path(
    path: Path,
    *,
    access: int,
    expect_directory: bool,
    share: int | None = None,
) -> tuple[int, _FileIdentity]:
    if share is None:
        share = _FILE_SHARE_ALL
    handle = _kernel32.CreateFileW(
        str(path),
        access,
        share,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OPEN_REPARSE_POINT | _FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        _raise_windows_error(path)
    information = _BY_HANDLE_FILE_INFORMATION()
    if not _kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
        error = ctypes.get_last_error()
        _kernel32.CloseHandle(handle)
        raise OSError(error, ctypes.FormatError(error), str(path))
    attributes = int(information.dwFileAttributes)
    is_directory = bool(attributes & _FILE_ATTRIBUTE_DIRECTORY)
    if attributes & _FILE_ATTRIBUTE_REPARSE_POINT or is_directory != expect_directory:
        _kernel32.CloseHandle(handle)
        kind = "directory" if expect_directory else "regular file"
        raise ValueError(f"{path} is not a safe {kind}")
    file_index = (int(information.nFileIndexHigh) << 32) | int(
        information.nFileIndexLow
    )
    size = (int(information.nFileSizeHigh) << 32) | int(information.nFileSizeLow)
    return int(handle), _FileIdentity(
        key=(int(information.dwVolumeSerialNumber), file_index),
        size=size,
    )


def _path_identity(path: Path, *, expect_directory: bool) -> _FileIdentity:
    if os.name == "nt":
        handle, identity = _windows_open_path(
            path,
            access=_FILE_READ_ATTRIBUTES,
            expect_directory=expect_directory,
        )
        _kernel32.CloseHandle(handle)
        return identity

    raw_identity = path.lstat()
    if _stat_is_reparse(raw_identity) or stat.S_ISLNK(raw_identity.st_mode):
        raise ValueError(f"{path} is a reparse point")
    expected_mode = stat.S_ISDIR if expect_directory else stat.S_ISREG
    if not expected_mode(raw_identity.st_mode):
        kind = "directory" if expect_directory else "regular file"
        raise ValueError(f"{path} is not a safe {kind}")
    return _identity_from_stat(raw_identity)


@contextmanager
def _pin_verified_root(root: Path, *, expected: _FileIdentity):
    if os.name == "nt":
        try:
            handle, identity = _windows_open_path(
                root,
                access=_FILE_READ_ATTRIBUTES,
                expect_directory=True,
                share=_FILE_SHARE_READ_WRITE,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError("diagnostic spool root is unavailable") from exc
        try:
            if identity.key != expected.key:
                raise RuntimeError(
                    "diagnostic spool root identity changed unexpectedly"
                )
            guard_path = root / ".diagnostic-spool.operation"
            try:
                guard = guard_path.open("xb")
            except FileExistsError:
                try:
                    stale_identity = _path_identity(
                        guard_path,
                        expect_directory=False,
                    )
                except (OSError, ValueError) as exc:
                    raise RuntimeError(
                        "diagnostic spool operation guard is unsafe"
                    ) from exc
                if not _delete_exact_regular(
                    guard_path,
                    expected=stale_identity,
                ):
                    raise RuntimeError(
                        "diagnostic spool operation guard is unavailable"
                    )
                guard = guard_path.open("xb")
            try:
                guard.write(b"UTHelper diagnostic spool operation\n")
                guard.flush()
                os.fsync(guard.fileno())
                guard_identity = _path_identity(
                    guard_path,
                    expect_directory=False,
                )
                yield
            finally:
                guard.close()
                if "guard_identity" in locals():
                    _delete_exact_regular(
                        guard_path,
                        expected=guard_identity,
                    )
        finally:
            _kernel32.CloseHandle(handle)
        return

    try:
        identity = _path_identity(root, expect_directory=True)
    except (OSError, ValueError) as exc:
        raise RuntimeError("diagnostic spool root is unavailable") from exc
    if identity.key != expected.key:
        raise RuntimeError("diagnostic spool root identity changed unexpectedly")
    yield


def _open_binary_no_follow(
    path: Path,
    *,
    expected: _FileIdentity,
) -> tuple[BinaryIO, _FileIdentity] | None:
    if os.name == "nt":
        try:
            handle, identity = _windows_open_path(
                path,
                access=_GENERIC_READ | _FILE_READ_ATTRIBUTES,
                expect_directory=False,
            )
        except (OSError, ValueError):
            return None
        if identity.key != expected.key:
            _kernel32.CloseHandle(handle)
            return None
        try:
            descriptor = msvcrt.open_osfhandle(
                handle,
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            _kernel32.CloseHandle(handle)
            return None
        return os.fdopen(descriptor, "rb"), identity

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    opened = os.fstat(descriptor)
    identity = _identity_from_stat(opened)
    if not stat.S_ISREG(opened.st_mode) or identity.key != expected.key:
        os.close(descriptor)
        return None
    return os.fdopen(descriptor, "rb"), identity


def _delete_exact_regular(path: Path, *, expected: _FileIdentity | None) -> bool:
    if os.name == "nt":
        try:
            handle, identity = _windows_open_path(
                path,
                access=_DELETE | _FILE_READ_ATTRIBUTES,
                expect_directory=False,
            )
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            return False
        try:
            if expected is not None and identity.key != expected.key:
                return False
            disposition = _FILE_DISPOSITION_INFO(DeleteFile=1)
            if not _kernel32.SetFileInformationByHandle(
                handle,
                _FILE_DISPOSITION_INFO_CLASS,
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                return False
            return True
        finally:
            _kernel32.CloseHandle(handle)

    try:
        raw_identity = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if _stat_is_reparse(raw_identity) or not stat.S_ISREG(raw_identity.st_mode):
        return False
    identity = _identity_from_stat(raw_identity)
    if expected is not None and identity.key != expected.key:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _windows_process_lock(root: Path):
    digest = sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    name = f"Local\\UTHelperDiagnosticSpool-{digest}"
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))
    acquired = False
    try:
        result = _kernel32.WaitForSingleObject(
            handle,
            int(LOCK_TIMEOUT_SECONDS * 1000),
        )
        if result in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
            acquired = True
        elif result == _WAIT_TIMEOUT:
            raise TimeoutError("diagnostic spool lock timed out")
        else:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error))
        yield
    finally:
        if acquired:
            _kernel32.ReleaseMutex(handle)
        _kernel32.CloseHandle(handle)


@contextmanager
def _posix_process_lock(root: Path):
    lock_path = root / ".diagnostic-spool.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode):
            raise ValueError("diagnostic spool lock is not a regular file")
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("diagnostic spool lock timed out") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def _process_lock(root: Path):
    if os.name == "nt":
        with _windows_process_lock(root):
            yield
    else:
        with _posix_process_lock(root):
            yield


class DiagnosticSpool:
    """Store only schema-valid reports under strict count, size, and age caps."""

    def __init__(
        self,
        root: Path,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        requested_root = Path(root)
        if _path_is_reparse(requested_root):
            raise ValueError(
                "diagnostic spool root must not be a symlink, junction, or reparse point"
            )
        requested_root.mkdir(parents=True, exist_ok=True)
        if _path_is_reparse(requested_root):
            raise ValueError(
                "diagnostic spool root must not be a symlink, junction, or reparse point"
            )
        requested_absolute = requested_root.absolute()
        requested_identity = _path_identity(
            requested_absolute,
            expect_directory=True,
        )
        resolved_root = requested_absolute.resolve(strict=True)
        resolved_identity = _path_identity(resolved_root, expect_directory=True)
        if requested_identity.key != resolved_identity.key:
            raise ValueError("diagnostic spool root changed during initialization")
        if not resolved_root.is_dir():
            raise ValueError("diagnostic spool root must be a regular directory")

        self.root = resolved_root
        self._root_identity = resolved_identity
        self._clock = clock
        self._lock = _lock_for(self.root)

    def enqueue(self, report: DiagnosticReport) -> EnqueueResult:
        """Atomically persist one report, deduplicating its fingerprint."""
        if not isinstance(report, DiagnosticReport):
            raise TypeError("report must be a validated DiagnosticReport")

        payload = report.model_dump_json().encode("utf-8")
        DiagnosticReport.model_validate_json(payload)
        if len(payload) > MAX_TOTAL_BYTES:
            return EnqueueResult(too_large=True)

        with self._exclusive():
            self._assert_root()
            current = self._prune_locked()
            if any(
                item.queued.report.fingerprint == report.fingerprint
                for item in current
            ):
                return EnqueueResult(deduplicated=True)

            temporary = self.root / f".{report.event_id}.tmp"
            final = self.root / self._filename(report)
            try:
                with temporary.open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, final)
            finally:
                self._safe_unlink_regular(temporary)

            remaining = self._prune_locked()
            stored = any(
                item.queued.report.event_id == report.event_id
                and item.queued.path == final
                for item in remaining
            )
            return EnqueueResult(stored=stored)

    def pending(self) -> tuple[QueuedReport, ...]:
        """Return validated pending reports in deterministic oldest-first order."""
        with self._exclusive():
            self._assert_root()
            return tuple(item.queued for item in self._prune_locked())

    def acknowledge(self, event_id: UUID) -> None:
        """Remove only validated queue entries matching ``event_id``."""
        if not isinstance(event_id, UUID):
            raise TypeError("event_id must be a UUID")
        with self._exclusive():
            self._assert_root()
            for item in self._prune_locked():
                if item.queued.report.event_id == event_id:
                    self._safe_unlink_regular(
                        item.queued.path,
                        expected=item.identity,
                    )

    def clear(self) -> None:
        """Delete owned direct regular JSON/temp children without following links."""
        with self._exclusive():
            self._assert_root()
            for path, identity in self._owned_regular_files(include_temps=True):
                self._safe_unlink_regular(path, expected=identity)

    @staticmethod
    def _filename(report: DiagnosticReport) -> str:
        timestamp = _as_utc(report.occurred_at).strftime("%Y%m%dT%H%M%S.%fZ")
        return f"{timestamp}-{report.event_id}.json"

    def _assert_root(self) -> None:
        try:
            identity = _path_identity(self.root, expect_directory=True)
        except (OSError, ValueError) as exc:
            raise RuntimeError("diagnostic spool root is unavailable") from exc
        if identity.key != self._root_identity.key:
            raise RuntimeError("diagnostic spool root identity changed unexpectedly")
        try:
            resolved = self.root.resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("diagnostic spool root is unavailable") from exc
        if resolved != self.root:
            raise RuntimeError("diagnostic spool root changed unexpectedly")

    @contextmanager
    def _exclusive(self):
        with self._lock:
            if os.name == "nt":
                with _process_lock(self.root):
                    with _pin_verified_root(
                        self.root,
                        expected=self._root_identity,
                    ):
                        yield
            else:
                with _pin_verified_root(
                    self.root,
                    expected=self._root_identity,
                ):
                    with _process_lock(self.root):
                        yield

    def _owned_regular_files(
        self,
        *,
        include_temps: bool,
    ) -> tuple[tuple[Path, _FileIdentity], ...]:
        owned: list[tuple[Path, _FileIdentity]] = []
        with os.scandir(self.root) as entries:
            for entry in entries:
                is_json = entry.name.endswith(".json")
                is_temp = entry.name.startswith(".") and entry.name.endswith(".tmp")
                if not is_json and not (include_temps and is_temp):
                    continue
                path = self.root / entry.name
                if path.parent != self.root:
                    continue
                try:
                    identity = _path_identity(path, expect_directory=False)
                except (OSError, ValueError):
                    continue
                owned.append((path, identity))
        return tuple(owned)

    def _prune_locked(self) -> tuple[_ValidatedFile, ...]:
        for path, identity in self._owned_regular_files(include_temps=True):
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                self._safe_unlink_regular(path, expected=identity)

        cutoff = _as_utc(self._clock()) - MAX_AGE
        valid: list[_ValidatedFile] = []
        for path, identity in self._owned_regular_files(include_temps=False):
            item = self._read_validated(path, identity)
            if item is None:
                self._safe_unlink_regular(path, expected=identity)
                continue
            if _as_utc(item.queued.report.occurred_at) < cutoff:
                self._safe_unlink_regular(path, expected=item.identity)
                continue
            valid.append(item)

        valid.sort(
            key=lambda item: (
                _as_utc(item.queued.report.occurred_at),
                str(item.queued.report.event_id),
                item.queued.path.name,
            )
        )

        deduplicated: list[_ValidatedFile] = []
        fingerprints: set[str] = set()
        for item in valid:
            fingerprint = item.queued.report.fingerprint
            if fingerprint in fingerprints:
                self._safe_unlink_regular(item.queued.path, expected=item.identity)
                continue
            fingerprints.add(fingerprint)
            deduplicated.append(item)

        total_bytes = sum(item.size for item in deduplicated)
        excess = max(0, len(deduplicated) - MAX_EVENTS)
        remove_count = excess
        retained_bytes = total_bytes - sum(
            item.size for item in deduplicated[:remove_count]
        )
        while remove_count < len(deduplicated) and retained_bytes > MAX_TOTAL_BYTES:
            retained_bytes -= deduplicated[remove_count].size
            remove_count += 1

        for item in deduplicated[:remove_count]:
            self._safe_unlink_regular(item.queued.path, expected=item.identity)
        return tuple(deduplicated[remove_count:])

    def _read_validated(
        self,
        path: Path,
        identity: _FileIdentity,
    ) -> _ValidatedFile | None:
        if identity.size > MAX_TOTAL_BYTES:
            return None
        opened = _open_binary_no_follow(path, expected=identity)
        if opened is None:
            return None
        stream, opened_identity = opened
        with stream:
            payload = stream.read(MAX_TOTAL_BYTES + 1)
        if len(payload) > MAX_TOTAL_BYTES:
            return None
        try:
            current_identity = _path_identity(path, expect_directory=False)
        except (OSError, ValueError):
            return None
        if not self._same_identity(identity, current_identity):
            return None
        try:
            report = DiagnosticReport.model_validate_json(payload)
        except (TypeError, ValueError):
            return None
        return _ValidatedFile(
            queued=QueuedReport(report=report, path=path),
            size=len(payload),
            identity=opened_identity,
        )

    def _safe_unlink_regular(
        self,
        path: Path,
        *,
        expected: _FileIdentity | None = None,
    ) -> bool:
        if path.parent != self.root:
            return False
        return _delete_exact_regular(path, expected=expected)

    @staticmethod
    def _same_identity(left: _FileIdentity, right: _FileIdentity) -> bool:
        return left.key == right.key


__all__ = [
    "MAX_AGE",
    "MAX_EVENTS",
    "MAX_TOTAL_BYTES",
    "DiagnosticSpool",
    "EnqueueResult",
    "QueuedReport",
]
