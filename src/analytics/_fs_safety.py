"""Reparse-point, handle-identity, and cross-process locking primitives for analytics."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from typing import BinaryIO

LOCK_TIMEOUT_SECONDS = 5.0

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ = 0x80000000
    _GENERIC_WRITE = 0x40000000
    _DELETE = 0x00010000
    _FILE_READ_ATTRIBUTES = 0x0080
    _FILE_SHARE_READ_WRITE = 0x00000001 | 0x00000002
    _FILE_SHARE_ALL = _FILE_SHARE_READ_WRITE | 0x00000004
    _CREATE_NEW = 1
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    _FILE_DISPOSITION_INFO_CLASS = 4
    _WAIT_OBJECT_0 = 0
    _WAIT_ABANDONED = 0x80
    _WAIT_TIMEOUT = 0x102
    _ERROR_FILE_EXISTS = 80
    _ERROR_ALREADY_EXISTS = 183
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
class FileIdentity:
    """Volume/device + file-index/inode identity and byte size of a verified path."""

    key: tuple[int, int]
    size: int


_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


def thread_lock_for(root: Path) -> threading.RLock:
    """Return a stable in-process reentrant lock keyed by normalized path."""
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _ROOT_LOCKS.setdefault(key, threading.RLock())


def _stat_is_reparse(identity: os.stat_result) -> bool:
    return bool(
        getattr(identity, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def path_is_reparse(path: Path) -> bool:
    """Return True if ``path`` is a symlink, Windows junction, or reparse point."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        return _stat_is_reparse(path.lstat())
    except OSError:
        return False


def _identity_from_stat(identity: os.stat_result) -> FileIdentity:
    return FileIdentity(
        key=(int(identity.st_dev), int(identity.st_ino)),
        size=int(identity.st_size),
    )


def _raise_windows_error(path: Path) -> None:
    error = ctypes.get_last_error()
    if error in {2, 3}:
        raise FileNotFoundError(error, ctypes.FormatError(error), str(path))
    if error in {_ERROR_FILE_EXISTS, _ERROR_ALREADY_EXISTS}:
        raise FileExistsError(error, ctypes.FormatError(error), str(path))
    raise OSError(error, ctypes.FormatError(error), str(path))


def _windows_open_path(
    path: Path,
    *,
    access: int,
    expect_directory: bool,
    share: int | None = None,
    creation: int | None = None,
    extra_flags: int = 0,
) -> tuple[int, FileIdentity]:
    if share is None:
        share = _FILE_SHARE_ALL
    if creation is None:
        creation = _OPEN_EXISTING
    flags = _FILE_FLAG_OPEN_REPARSE_POINT | extra_flags
    if expect_directory:
        flags |= _FILE_FLAG_BACKUP_SEMANTICS
    handle = _kernel32.CreateFileW(
        str(path),
        access,
        share,
        None,
        creation,
        flags,
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
    return int(handle), FileIdentity(
        key=(int(information.dwVolumeSerialNumber), file_index),
        size=size,
    )


def path_identity(path: Path, *, expect_directory: bool) -> FileIdentity:
    """Verify ``path`` without following links and return its handle/inode identity."""
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


def verify_non_reparse_target(
    root: Path,
    *,
    check_parent: bool = False,
    label: str = "analytics directory",
) -> None:
    """Reject symlink/junction/reparse points on ``root`` (and optional direct parent)."""
    candidate = Path(root)
    if check_parent and path_is_reparse(candidate.parent):
        raise ValueError(
            f"{label} parent must not be a symlink, junction, or reparse point"
        )
    if path_is_reparse(candidate):
        raise ValueError(
            f"{label} must not be a symlink, junction, or reparse point"
        )


def ensure_safe_directory(
    root: Path,
    *,
    check_parent: bool = False,
    label: str = "analytics directory",
) -> tuple[Path, FileIdentity]:
    """Create ``root`` safely and verify its directory handle/inode identity."""
    requested = Path(root)
    verify_non_reparse_target(requested, check_parent=check_parent, label=label)
    requested.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        try:
            os.chmod(requested, 0o700)
        except OSError:
            pass
    verify_non_reparse_target(requested, check_parent=check_parent, label=label)

    requested_absolute = requested.absolute()
    if check_parent:
        path_identity(requested_absolute.parent, expect_directory=True)
    requested_identity = path_identity(requested_absolute, expect_directory=True)
    resolved_root = requested_absolute.resolve(strict=True)
    resolved_identity = path_identity(resolved_root, expect_directory=True)
    if requested_identity.key != resolved_identity.key:
        raise ValueError(f"{label} changed during initialization")
    if not resolved_root.is_dir():
        raise ValueError(f"{label} must be a regular directory")
    return resolved_root, resolved_identity


def assert_directory_identity(
    root: Path,
    *,
    expected: FileIdentity,
    label: str = "analytics directory",
) -> None:
    """Verify ``root`` still resolves to the exact directory ``expected``."""
    try:
        identity = path_identity(root, expect_directory=True)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if identity.key != expected.key:
        raise RuntimeError(f"{label} identity changed unexpectedly")
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError(f"{label} is unavailable") from exc
    if resolved != root:
        raise RuntimeError(f"{label} changed unexpectedly")


def open_binary_no_follow(
    path: Path,
    *,
    expected: FileIdentity,
) -> tuple[BinaryIO, FileIdentity] | None:
    """Open regular file ``path`` without following links if it matches ``expected``."""
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


def _open_exclusive_new_binary(path: Path) -> tuple[BinaryIO, FileIdentity]:
    """Create a brand-new regular file without following reparse points."""
    if os.name == "nt":
        handle, identity = _windows_open_path(
            path,
            access=_GENERIC_WRITE | _FILE_READ_ATTRIBUTES,
            expect_directory=False,
            share=_FILE_SHARE_READ_WRITE,
            creation=_CREATE_NEW,
            extra_flags=_FILE_ATTRIBUTE_NORMAL,
        )
        try:
            descriptor = msvcrt.open_osfhandle(
                handle,
                os.O_WRONLY | getattr(os, "O_BINARY", 0),
            )
        except OSError:
            _kernel32.CloseHandle(handle)
            raise
        return os.fdopen(descriptor, "wb"), identity

    flags = (
        os.O_CREAT
        | os.O_EXCL
        | os.O_WRONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags, 0o600)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise ValueError(f"{path} is not a safe regular file")
    return os.fdopen(descriptor, "wb"), _identity_from_stat(opened)


def delete_exact_regular(
    path: Path,
    *,
    expected: FileIdentity | None = None,
) -> bool:
    """Delete ``path`` without following links, verifying ``expected`` when provided."""
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


def atomic_write_regular(
    directory: Path,
    *,
    dir_identity: FileIdentity,
    filename: str,
    payload: bytes,
    fsync: bool = True,
) -> FileIdentity:
    """Atomically write ``payload`` to ``directory / filename`` without following links."""
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise ValueError("filename must be a direct child name")
    assert_directory_identity(directory, expected=dir_identity)

    target = directory / filename
    if target.parent != directory:
        raise ValueError("target must reside directly inside directory")
    if path_is_reparse(target):
        raise ValueError(f"{target} must not be a symlink or reparse point")
    try:
        # Reject non-regular targets (e.g. directories/FIFOs) before replacing.
        path_identity(target, expect_directory=False)
    except FileNotFoundError:
        pass

    temporary = directory / f".{filename}.{secrets.token_hex(8)}.tmp"
    temp_identity: FileIdentity | None = None
    try:
        stream, temp_identity = _open_exclusive_new_binary(temporary)
        with stream:
            stream.write(payload)
            stream.flush()
            if fsync:
                os.fsync(stream.fileno())
        current_temp = path_identity(temporary, expect_directory=False)
        if current_temp.key != temp_identity.key:
            raise RuntimeError("temporary analytics file identity changed")
        assert_directory_identity(directory, expected=dir_identity)
        if path_is_reparse(target):
            raise ValueError(f"{target} became a reparse point during write")
        os.replace(temporary, target)
        temp_identity = None
        final_identity = path_identity(target, expect_directory=False)
        return final_identity
    finally:
        if temp_identity is not None or temporary.exists() or path_is_reparse(temporary):
            delete_exact_regular(temporary, expected=temp_identity)


@contextmanager
def pin_verified_directory(
    root: Path,
    *,
    expected: FileIdentity,
    guard_filename: str = ".analytics.operation",
):
    """Pin ``root`` against junction-swap TOCTOU attacks for the duration of an I/O op."""
    if os.name == "nt":
        try:
            handle, identity = _windows_open_path(
                root,
                access=_FILE_READ_ATTRIBUTES,
                expect_directory=True,
                share=_FILE_SHARE_READ_WRITE,
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError("analytics directory is unavailable") from exc
        try:
            if identity.key != expected.key:
                raise RuntimeError("analytics directory identity changed unexpectedly")
            guard_path = root / guard_filename
            guard_identity: FileIdentity | None = None
            try:
                guard, guard_identity = _open_exclusive_new_binary(guard_path)
            except FileExistsError:
                try:
                    stale_identity = path_identity(guard_path, expect_directory=False)
                except (OSError, ValueError) as exc:
                    raise RuntimeError("analytics operation guard is unsafe") from exc
                if not delete_exact_regular(guard_path, expected=stale_identity):
                    raise RuntimeError("analytics operation guard is unavailable")
                guard, guard_identity = _open_exclusive_new_binary(guard_path)
            try:
                guard.write(b"UTHelper analytics operation\n")
                guard.flush()
                yield
            finally:
                guard.close()
                if guard_identity is not None:
                    delete_exact_regular(guard_path, expected=guard_identity)
        finally:
            _kernel32.CloseHandle(handle)
        return

    try:
        identity = path_identity(root, expect_directory=True)
    except (OSError, ValueError) as exc:
        raise RuntimeError("analytics directory is unavailable") from exc
    if identity.key != expected.key:
        raise RuntimeError("analytics directory identity changed unexpectedly")
    yield


@contextmanager
def _windows_process_lock(root: Path, *, lock_prefix: str, timeout_seconds: float):
    digest = sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()
    name = f"Local\\{lock_prefix}-{digest}"
    handle = _kernel32.CreateMutexW(None, False, name)
    if not handle:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error))
    acquired = False
    try:
        result = _kernel32.WaitForSingleObject(
            handle,
            int(timeout_seconds * 1000),
        )
        if result in {_WAIT_OBJECT_0, _WAIT_ABANDONED}:
            acquired = True
        elif result == _WAIT_TIMEOUT:
            raise TimeoutError("analytics lock timed out")
        else:
            error = ctypes.get_last_error()
            raise OSError(error, ctypes.FormatError(error))
        yield
    finally:
        if acquired:
            _kernel32.ReleaseMutex(handle)
        _kernel32.CloseHandle(handle)


@contextmanager
def _posix_process_lock(root: Path, *, lock_filename: str, timeout_seconds: float):
    lock_path = root / lock_filename
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        identity = os.fstat(descriptor)
        if not stat.S_ISREG(identity.st_mode):
            raise ValueError("analytics lock is not a regular file")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError("analytics lock timed out") from exc
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@contextmanager
def exclusive_directory_lock(
    root: Path,
    *,
    expected: FileIdentity,
    lock_prefix: str = "UTHelperAnalytics",
    lock_filename: str = ".analytics.lock",
    guard_filename: str = ".analytics.operation",
    timeout_seconds: float = LOCK_TIMEOUT_SECONDS,
):
    """Acquire in-process + cross-process lock and pin verified directory ``root``."""
    with thread_lock_for(root):
        if os.name == "nt":
            with _windows_process_lock(
                root,
                lock_prefix=lock_prefix,
                timeout_seconds=timeout_seconds,
            ):
                with pin_verified_directory(
                    root,
                    expected=expected,
                    guard_filename=guard_filename,
                ):
                    yield
        else:
            with pin_verified_directory(
                root,
                expected=expected,
                guard_filename=guard_filename,
            ):
                with _posix_process_lock(
                    root,
                    lock_filename=lock_filename,
                    timeout_seconds=timeout_seconds,
                ):
                    yield


__all__ = [
    "FileIdentity",
    "LOCK_TIMEOUT_SECONDS",
    "assert_directory_identity",
    "atomic_write_regular",
    "delete_exact_regular",
    "ensure_safe_directory",
    "exclusive_directory_lock",
    "open_binary_no_follow",
    "path_identity",
    "path_is_reparse",
    "pin_verified_directory",
    "thread_lock_for",
    "verify_non_reparse_target",
]
