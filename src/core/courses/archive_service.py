"""Path-safe interactive archive explorer for .zip, .tar.*, and optional desktop .rar."""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import threading
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Callable, Iterator, Optional

from core.courses.models import PreviewKind
from core.courses.providers import classify_preview_kind

_TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")
_READ_CHUNK_SIZE = 65536
_MAX_STDERR_BYTES = 64 * 1024
_MAX_LISTING_LINE_BYTES = 256 * 1024
_RAR_LIST_TIMEOUT_SECONDS = 15.0
_RAR_EXTRACT_TIMEOUT_SECONDS = 20.0
_TAR_BASE_HEADER_SLACK = 64 * 1024
_TAR_PER_ENTRY_SLACK = 4096


class ArchiveSecurityError(ValueError):
    """Raised when an archive violates ZipSlip/TarSlip, link, or resource caps."""


class _RarInspectionError(RuntimeError):
    """Internal error raised when a RAR CLI tool fails or times out."""


class _RarEncryptedError(_RarInspectionError):
    """Internal error raised when a RAR archive is password-protected."""


@dataclass(frozen=True, slots=True)
class _RarEntry:
    """Normalized entry parsed from 7z or UnRAR technical listing output."""

    raw_path: str
    clean_path: str
    is_dir: bool
    size: int
    is_link: bool = False
    encrypted: bool = False


@dataclass
class ArchiveNode:
    """Tree node representing a file or directory inside an archive."""

    name: str
    full_path: str
    is_dir: bool
    size: int = 0
    compressed_size: int = 0
    preview_kind: PreviewKind = "unsupported"
    children: list["ArchiveNode"] = field(default_factory=list)


@dataclass(frozen=True)
class ArchiveTreeResult:
    """Normalized inspection result for an archive's directory tree."""

    file_name: str
    format_name: str
    supported: bool
    root: ArchiveNode
    total_files: int = 0
    unsupported_reason: str = ""


def sanitize_archive_member_path(raw_path: str) -> str:
    """Normalize and validate an archive member path against traversal and unsafe forms."""
    if not isinstance(raw_path, str) or not raw_path:
        raise ArchiveSecurityError("Empty archive member path")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_path):
        raise ArchiveSecurityError(f"Control character forbidden in archive path: {raw_path!r}")

    normalized = raw_path.replace("\\", "/")
    if not normalized.strip():
        raise ArchiveSecurityError("Blank archive member path")
    if normalized.startswith("/"):
        raise ArchiveSecurityError(f"Absolute or UNC path forbidden: {raw_path!r}")
    if ":" in normalized:
        raise ArchiveSecurityError(f"Drive letter or colon forbidden: {raw_path!r}")

    segments: list[str] = []
    for segment in normalized.split("/"):
        if segment in ("", "."):
            continue
        if not segment.strip():
            raise ArchiveSecurityError(f"Whitespace-only path segment forbidden: {raw_path!r}")
        if segment.rstrip(". ") in ("", ".", ".."):
            raise ArchiveSecurityError(
                f"Unsafe or traversal path segment forbidden: {raw_path!r}"
            )
        segments.append(segment)

    if not segments:
        raise ArchiveSecurityError(f"Empty archive member path: {raw_path!r}")
    return "/".join(segments)


def find_windows_rar_cli() -> Optional[str]:
    """Locate 7-Zip or UnRAR CLI in PATH or standard Windows installation directories."""
    for candidate in ("7z", "7za", "UnRAR", "unrar"):
        found = shutil.which(candidate)
        if found:
            return found
    if os.name == "nt":
        prog_files = os.environ.get("ProgramFiles") or r"C:\Program Files"
        prog_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        for fixed in (
            Path(prog_files) / "7-Zip" / "7z.exe",
            Path(prog_files) / "WinRAR" / "UnRAR.exe",
            Path(prog_files_x86) / "7-Zip" / "7z.exe",
            Path(prog_files_x86) / "WinRAR" / "UnRAR.exe",
        ):
            if fixed.is_file():
                return str(fixed)
    return None


find_rar_cli = find_windows_rar_cli


def _is_root_dot_dir(raw_name: str, is_dir: bool) -> bool:
    if not is_dir:
        return False
    return raw_name.replace("\\", "/") in {".", "./", "./."}


def _zip_member_kind(info: zipfile.ZipInfo) -> str:
    mode = info.external_attr >> 16
    if (
        stat.S_ISLNK(mode)
        or stat.S_ISFIFO(mode)
        or stat.S_ISCHR(mode)
        or stat.S_ISBLK(mode)
        or stat.S_ISSOCK(mode)
    ):
        return "special"
    if info.is_dir() or info.filename.endswith(("/", "\\")):
        return "dir"
    return "regular"


def _parse_nonneg_int(raw_value: str) -> int:
    try:
        return max(0, int(raw_value.strip() or 0))
    except ValueError:
        return 0


def _read_stream_capped(stream: IO[bytes], max_bytes: int) -> bytes:
    """Read stream incrementally up to max_bytes + 1 to defeat spoofed header sizes."""
    if max_bytes < 0:
        raise ArchiveSecurityError("Negative single-entry byte cap")
    limit = max_bytes + 1
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = stream.read(min(_READ_CHUNK_SIZE, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > max_bytes:
        raise ArchiveSecurityError("Selected entry exceeds max single entry size")
    return b"".join(chunks)


def _drain_stream_bounded(
    stream: Optional[IO[bytes]], max_bytes: int, sink: list[bytes]
) -> None:
    """Drain an output stream completely to prevent pipe deadlock while capping kept bytes."""
    if stream is None:
        return
    kept = 0
    while True:
        try:
            chunk = stream.read(_READ_CHUNK_SIZE)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        if kept < max_bytes:
            take = chunk[: max_bytes - kept]
            sink.append(take)
            kept += len(take)


def _iter_lines_bounded(
    stream: Optional[IO[bytes]],
    max_line_bytes: int,
    max_total_bytes: int,
) -> Iterator[str]:
    """Stream UTF-8 lines incrementally from a byte pipe with line and total caps."""
    if stream is None:
        return
    buf = bytearray()
    total_bytes = 0
    while True:
        chunk = stream.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > max_total_bytes:
            raise ArchiveSecurityError("RAR listing output exceeds safety limits")
        buf.extend(chunk)
        while True:
            nl_idx = buf.find(b"\n")
            if nl_idx == -1:
                if len(buf) > max_line_bytes:
                    raise ArchiveSecurityError("RAR listing line exceeds safety limits")
                break
            if nl_idx > max_line_bytes:
                raise ArchiveSecurityError("RAR listing line exceeds safety limits")
            raw_line = bytes(buf[:nl_idx])
            del buf[: nl_idx + 1]
            yield raw_line.decode("utf-8", errors="replace").rstrip("\r")
    if buf:
        if len(buf) > max_line_bytes:
            raise ArchiveSecurityError("RAR listing line exceeds safety limits")
        yield bytes(buf).decode("utf-8", errors="replace").rstrip("\r")


class _ByteLimitStream:
    """Bounded stream wrapper for tarfile that halts decompression bombs early."""

    def __init__(self, stream: IO[bytes], max_bytes: int) -> None:
        self._stream = stream
        self._max_bytes = max(0, max_bytes)
        self._pos = 0
        self._bytes_consumed = 0

    def allow_entry_overhead(self, extra_bytes: int) -> None:
        """Grant modest tar header/padding allowance as real entries are parsed."""
        self._max_bytes += max(0, extra_bytes)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunks: list[bytes] = []
            while True:
                remaining = self._max_bytes - self._bytes_consumed
                chunk = self._stream.read(min(_READ_CHUNK_SIZE, remaining + 1))
                if not chunk:
                    break
                self._bytes_consumed += len(chunk)
                self._pos += len(chunk)
                if self._bytes_consumed > self._max_bytes:
                    raise ArchiveSecurityError("Archive exceeds safety limits")
                chunks.append(chunk)
            return b"".join(chunks)

        if size == 0:
            return b""
        remaining = self._max_bytes - self._bytes_consumed
        data = self._stream.read(min(size, remaining + 1))
        if data:
            self._bytes_consumed += len(data)
            self._pos += len(data)
            if self._bytes_consumed > self._max_bytes:
                raise ArchiveSecurityError("Archive exceeds safety limits")
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            raise OSError("SEEK_END is not supported on bounded tar stream")
        else:
            raise ValueError(f"Unsupported whence: {whence}")

        if target < 0:
            raise ValueError(f"Negative seek position: {target}")
        if target > self._max_bytes:
            raise ArchiveSecurityError("Archive exceeds safety limits")

        if target > self._pos:
            if isinstance(self._stream, io.BytesIO):
                new_pos = self._stream.seek(target, io.SEEK_SET)
                delta = max(0, new_pos - self._pos)
                self._bytes_consumed += delta
                self._pos = new_pos
                if self._bytes_consumed > self._max_bytes:
                    raise ArchiveSecurityError("Archive exceeds safety limits")
                return self._pos
            skip = target - self._pos
            while skip > 0:
                chunk = self.read(min(_READ_CHUNK_SIZE, skip))
                if not chunk:
                    break
                skip -= len(chunk)
            return self._pos

        if target < self._pos:
            self._pos = self._stream.seek(target, io.SEEK_SET)
        return self._pos


class _PathKindTracker:
    """Tracks normalized archive paths to reject file-vs-directory collisions."""

    def __init__(self) -> None:
        self._dirs: set[str] = set()
        self._files: set[str] = set()

    def record(self, clean_path: str, is_dir: bool) -> None:
        parts = clean_path.split("/")
        for idx in range(len(parts) - 1):
            ancestor = "/".join(parts[: idx + 1])
            if ancestor in self._files:
                raise ArchiveSecurityError(
                    f"Conflicting file and directory path in archive: {ancestor!r}"
                )
            self._dirs.add(ancestor)
        if is_dir:
            if clean_path in self._files:
                raise ArchiveSecurityError(
                    f"Conflicting file and directory path in archive: {clean_path!r}"
                )
            self._dirs.add(clean_path)
        else:
            if clean_path in self._dirs:
                raise ArchiveSecurityError(
                    f"Conflicting file and directory path in archive: {clean_path!r}"
                )
            self._files.add(clean_path)


def _insert_entry(
    root: ArchiveNode,
    clean_path: str,
    is_dir: bool,
    size: int,
    comp_size: int,
    *,
    index: Optional[dict[str, ArchiveNode]] = None,
) -> None:
    parts = [part for part in clean_path.split("/") if part]
    curr = root
    for idx, part in enumerate(parts):
        last = idx == len(parts) - 1
        rel = "/".join(parts[: idx + 1])
        existing = (
            index.get(rel)
            if index is not None
            else next((child for child in curr.children if child.name == part), None)
        )
        expected_is_dir = is_dir if last else True
        if existing is None:
            existing = ArchiveNode(
                name=part,
                full_path=rel,
                is_dir=expected_is_dir,
                size=0 if expected_is_dir else size,
                compressed_size=0 if expected_is_dir else comp_size,
                preview_kind="unsupported" if expected_is_dir else classify_preview_kind(part),
            )
            curr.children.append(existing)
            if index is not None:
                index[rel] = existing
        elif existing.is_dir != expected_is_dir:
            raise ArchiveSecurityError(f"Conflicting file and directory path in archive: {rel!r}")
        elif last and not expected_is_dir:
            existing.size = size
            existing.compressed_size = comp_size
            existing.preview_kind = classify_preview_kind(part)
        curr = existing


def _sort_and_count_files(root: ArchiveNode) -> int:
    """Sort every directory node children dirs-first alphabetically using an explicit stack."""
    total_files = 0
    stack: list[ArchiveNode] = [root]
    while stack:
        node = stack.pop()
        node.children.sort(
            key=lambda child: (0 if child.is_dir else 1, child.name.lower(), child.name)
        )
        for child in node.children:
            if child.is_dir:
                stack.append(child)
            else:
                total_files += 1
    return total_files


@contextmanager
def _temporary_rar_file(data: bytes) -> Iterator[Path]:
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".rar", delete=False) as tf:
            tmp_path = Path(tf.name)
            tf.write(data)
        yield tmp_path
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def _managed_rar_process(
    cmd: list[str], timeout_seconds: float
) -> Iterator[tuple[subprocess.Popen[bytes], threading.Event, list[bytes], threading.Thread]]:
    """Spawn a RAR CLI process with bounded stderr draining and a hard timeout watchdog."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    timed_out = threading.Event()

    def _on_timeout() -> None:
        timed_out.set()
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout_seconds, _on_timeout)
    timer.daemon = True
    timer.start()

    stderr_chunks: list[bytes] = []
    stderr_thread = threading.Thread(
        target=_drain_stream_bounded,
        args=(proc.stderr, _MAX_STDERR_BYTES, stderr_chunks),
        daemon=True,
    )
    stderr_thread.start()

    try:
        yield proc, timed_out, stderr_chunks, stderr_thread
    finally:
        timer.cancel()
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        stderr_thread.join(timeout=1.0)


def _is_unix_special_mode(attrs: str) -> bool:
    return (
        len(attrs) >= 10
        and attrs[0] in ("l", "b", "c", "p", "s")
        and attrs[1] in ("r", "-")
        and attrs[2] in ("w", "-")
    )


def _has_link_attribute(attrs: str) -> bool:
    return "L" in attrs or _is_unix_special_mode(attrs)


class _7zSltStreamParser:
    """Incremental parser for `7z l -slt -sccUTF-8` stdout lines."""

    def __init__(self, check_caps: Callable[[int, int], None]) -> None:
        self._check_caps = check_caps
        self.entries: list[_RarEntry] = []
        self.header_encrypted = False
        self.header_preview: list[str] = []
        self._preview_chars = 0
        self._in_entries = False
        self._entry_count = 0
        self._total_bytes = 0
        self._reset_current()

    def _reset_current(self) -> None:
        self._cur_path = ""
        self._cur_size = 0
        self._cur_dir = False
        self._cur_encrypted = False
        self._cur_link = False

    def _flush(self) -> None:
        if not self._cur_path:
            self._reset_current()
            return
        if not _is_root_dot_dir(self._cur_path, self._cur_dir):
            self._entry_count += 1
            self._total_bytes += self._cur_size
            self._check_caps(self._entry_count, self._total_bytes)
            clean = sanitize_archive_member_path(self._cur_path)
            self.entries.append(
                _RarEntry(
                    raw_path=self._cur_path,
                    clean_path=clean,
                    is_dir=self._cur_dir,
                    size=self._cur_size,
                    is_link=self._cur_link,
                    encrypted=self._cur_encrypted,
                )
            )
        self._reset_current()

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if not self._in_entries:
            if self._preview_chars < 8192 and stripped:
                self.header_preview.append(stripped)
                self._preview_chars += len(stripped)
            if stripped == "----------":
                self._in_entries = True
            elif stripped == "Encrypted = +":
                self.header_encrypted = True
            return

        if not stripped:
            self._flush()
            return

        if line.startswith("Path = "):
            if self._cur_path:
                self._flush()
            self._cur_path = line[7:]
        elif line.startswith("Size = "):
            self._cur_size = _parse_nonneg_int(line[7:])
        elif line.startswith("Folder = "):
            self._cur_dir = line[9:].strip() == "+"
        elif line.startswith("Encrypted = "):
            self._cur_encrypted = line[12:].strip() == "+"
        elif line.startswith("Symbolic Link = ") and line[16:].strip():
            self._cur_link = True
        elif line.startswith("Hard Link = ") and line[12:].strip():
            self._cur_link = True
        elif line.startswith("Attributes = "):
            if _has_link_attribute(line[13:].strip()):
                self._cur_link = True
        elif line.startswith("Mode = "):
            if line[7:].strip().lower().startswith(("l", "b", "c", "p", "s")):
                self._cur_link = True

    def finish(self) -> list[_RarEntry]:
        self._flush()
        return self.entries


class _UnrarLtStreamParser:
    """Incremental parser for `UnRAR lt -p- -scf` stdout lines."""

    def __init__(self, check_caps: Callable[[int, int], None]) -> None:
        self._check_caps = check_caps
        self.entries: list[_RarEntry] = []
        self.header_encrypted = False
        self.header_preview: list[str] = []
        self._preview_chars = 0
        self._in_entries = False
        self._entry_count = 0
        self._total_bytes = 0
        self._reset_current()

    def _reset_current(self) -> None:
        self._cur_name = ""
        self._cur_type = ""
        self._cur_size = 0
        self._cur_dir = False
        self._cur_link = False
        self._cur_encrypted = False
        self._attr_dir_hint = False

    def _flush(self) -> None:
        if not self._cur_name:
            self._reset_current()
            return
        is_dir = self._cur_dir or (self._attr_dir_hint and not self._cur_type)
        if not _is_root_dot_dir(self._cur_name, is_dir):
            self._entry_count += 1
            self._total_bytes += self._cur_size
            self._check_caps(self._entry_count, self._total_bytes)
            clean = sanitize_archive_member_path(self._cur_name)
            self.entries.append(
                _RarEntry(
                    raw_path=self._cur_name,
                    clean_path=clean,
                    is_dir=is_dir,
                    size=self._cur_size,
                    is_link=self._cur_link,
                    encrypted=self._cur_encrypted,
                )
            )
        self._reset_current()

    def feed(self, line: str) -> None:
        stripped = line.strip()
        if not stripped:
            if self._in_entries:
                self._flush()
            return

        left_trimmed = line.lstrip(" ")
        if left_trimmed.startswith("Name: "):
            self._in_entries = True
            if self._cur_name:
                self._flush()
            self._cur_name = left_trimmed[6:]
            return

        if not self._in_entries:
            if self._preview_chars < 8192:
                self.header_preview.append(stripped)
                self._preview_chars += len(stripped)
            if (
                left_trimmed.startswith("Details: ")
                and "encrypted headers" in left_trimmed.lower()
            ):
                self.header_encrypted = True
            return

        if left_trimmed.startswith("Type: "):
            self._cur_type = left_trimmed[6:].strip().lower()
            if self._cur_type == "directory":
                self._cur_dir = True
            elif self._cur_type != "file":
                self._cur_link = True
        elif left_trimmed.startswith("Target: "):
            if left_trimmed[8:].strip():
                self._cur_link = True
        elif left_trimmed.startswith("Size: "):
            self._cur_size = _parse_nonneg_int(left_trimmed[6:])
        elif left_trimmed.startswith("Attributes: "):
            attrs = left_trimmed[12:].strip()
            if _has_link_attribute(attrs):
                self._cur_link = True
            if "D" in attrs:
                self._attr_dir_hint = True
        elif left_trimmed.startswith("Flags: "):
            if "encrypted" in left_trimmed[7:].strip().lower():
                self._cur_encrypted = True

    def finish(self) -> list[_RarEntry]:
        self._flush()
        return self.entries


class ArchiveService:
    """Path-safe, resource-bounded archive tree builder and single-entry extractor."""

    def __init__(
        self,
        max_entries: int = 5000,
        max_uncompressed_bytes: int = 512 * 1024 * 1024,
        max_single_entry_bytes: int = 64 * 1024 * 1024,
        rar_tool_finder: Callable[[], Optional[str]] = find_windows_rar_cli,
    ) -> None:
        if max_entries < 0 or max_uncompressed_bytes < 0 or max_single_entry_bytes < 0:
            raise ValueError("Archive limits must be non-negative")
        self._max_entries = int(max_entries)
        self._max_total = int(max_uncompressed_bytes)
        self._max_entry = int(max_single_entry_bytes)
        self._rar_finder = rar_tool_finder

    def _check_caps(self, entry_count: int, total_bytes: int) -> None:
        if entry_count > self._max_entries or total_bytes > self._max_total:
            raise ArchiveSecurityError("Archive exceeds safety limits")

    def inspect_bytes(self, file_name: str, data: bytes) -> ArchiveTreeResult:
        """Inspect an archive in memory and return a sorted directory-first tree."""
        lower = (file_name or "").strip().lower()
        root = ArchiveNode(name=file_name, full_path="", is_dir=True)

        if lower.endswith(".zip"):
            try:
                return self._inspect_zip(file_name, data, root)
            except ArchiveSecurityError:
                raise
            except (zipfile.BadZipFile, EOFError, OSError, zlib.error, struct.error, ValueError):
                return ArchiveTreeResult(
                    file_name=file_name,
                    format_name="zip",
                    supported=False,
                    root=root,
                    unsupported_reason="Không thể đọc tệp ZIP (tệp bị hỏng hoặc không đúng định dạng ZIP).",
                )

        if lower.endswith(_TAR_EXTENSIONS):
            try:
                return self._inspect_tar(file_name, data, root)
            except ArchiveSecurityError:
                raise
            except (
                tarfile.TarError,
                EOFError,
                OSError,
                zlib.error,
                lzma.LZMAError,
                struct.error,
                ValueError,
            ):
                return ArchiveTreeResult(
                    file_name=file_name,
                    format_name="tar",
                    supported=False,
                    root=root,
                    unsupported_reason="Không thể đọc tệp TAR (tệp bị hỏng hoặc không đúng định dạng nén).",
                )

        if lower.endswith(".rar"):
            return self._inspect_rar(file_name, data, root)

        return ArchiveTreeResult(
            file_name=file_name,
            format_name="unknown",
            supported=False,
            root=root,
            unsupported_reason="Định dạng nén chưa được hỗ trợ.",
        )

    @contextmanager
    def _open_tar_stream(
        self, file_name: str, data: bytes
    ) -> Iterator[tuple[tarfile.TarFile, _ByteLimitStream]]:
        lower = (file_name or "").strip().lower()
        bio = io.BytesIO(data)
        decompressed: IO[bytes]
        if data.startswith(b"\x1f\x8b"):
            decompressed = gzip.GzipFile(fileobj=bio, mode="rb")
        elif data.startswith(b"BZh"):
            decompressed = bz2.BZ2File(bio, mode="rb")
        elif data.startswith(b"\xfd7zXZ\x00"):
            decompressed = lzma.LZMAFile(bio, mode="rb")
        elif lower.endswith((".tar.gz", ".tgz")):
            decompressed = gzip.GzipFile(fileobj=bio, mode="rb")
        elif lower.endswith((".tar.bz2", ".tbz2")):
            decompressed = bz2.BZ2File(bio, mode="rb")
        elif lower.endswith((".tar.xz", ".txz")):
            decompressed = lzma.LZMAFile(bio, mode="rb")
        else:
            decompressed = bio

        limit_stream = _ByteLimitStream(
            decompressed,
            max_bytes=self._max_total + _TAR_BASE_HEADER_SLACK,
        )
        tf: Optional[tarfile.TarFile] = None
        try:
            tf = tarfile.open(fileobj=limit_stream, mode="r:")
            yield tf, limit_stream
        finally:
            if tf is not None:
                tf.close()
            if decompressed is not bio:
                decompressed.close()
            bio.close()

    def _inspect_zip(self, file_name: str, data: bytes, root: ArchiveNode) -> ArchiveTreeResult:
        index: dict[str, ArchiveNode] = {}
        tracker = _PathKindTracker()
        entry_count = 0
        total_bytes = 0
        any_encrypted = False
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            for info in zf.infolist():
                kind = _zip_member_kind(info)
                is_dir = kind == "dir"
                if _is_root_dot_dir(info.filename, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, info.file_size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                clean = sanitize_archive_member_path(info.filename)
                if info.flag_bits & 0x1:
                    any_encrypted = True
                if kind == "special":
                    continue
                tracker.record(clean, is_dir)
                _insert_entry(
                    root,
                    clean,
                    is_dir,
                    entry_size,
                    max(0, info.compress_size),
                    index=index,
                )
        if any_encrypted:
            return ArchiveTreeResult(
                file_name=file_name,
                format_name="zip",
                supported=False,
                root=ArchiveNode(name=file_name, full_path="", is_dir=True),
                unsupported_reason=(
                    "Tệp ZIP được bảo vệ bằng mật khẩu nên chưa thể xem trực tiếp. "
                    "Hãy lưu về máy để mở bằng ứng dụng hỗ trợ."
                ),
            )
        total_files = _sort_and_count_files(root)
        return ArchiveTreeResult(file_name, "zip", True, root, total_files=total_files)

    def _inspect_tar(self, file_name: str, data: bytes, root: ArchiveNode) -> ArchiveTreeResult:
        index: dict[str, ArchiveNode] = {}
        tracker = _PathKindTracker()
        entry_count = 0
        total_bytes = 0
        with self._open_tar_stream(file_name, data) as (tf, limit_stream):
            while (member := tf.next()) is not None:
                is_dir = member.isdir()
                if _is_root_dot_dir(member.name, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, member.size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                limit_stream.allow_entry_overhead(_TAR_PER_ENTRY_SLACK)
                clean = sanitize_archive_member_path(member.name)
                if not (member.isfile() or is_dir):
                    continue
                tracker.record(clean, is_dir)
                _insert_entry(root, clean, is_dir, entry_size, 0, index=index)
        total_files = _sort_and_count_files(root)
        return ArchiveTreeResult(file_name, "tar", True, root, total_files=total_files)

    def _inspect_rar(self, file_name: str, data: bytes, root: ArchiveNode) -> ArchiveTreeResult:
        cli = self._rar_finder()
        if not cli:
            return ArchiveTreeResult(
                file_name=file_name,
                format_name="rar",
                supported=False,
                root=root,
                unsupported_reason=(
                    "Định dạng RAR không giải nén trực tiếp trên thiết bị di động hoặc thiếu 7-Zip/WinRAR trên Windows. "
                    "Hãy lưu về máy để mở nhanh bằng ZArchiver / Files / WinRAR."
                ),
            )
        try:
            with _temporary_rar_file(data) as tmp_path:
                entries = self._list_rar_cli(cli, tmp_path)
        except ArchiveSecurityError:
            raise
        except _RarEncryptedError:
            return ArchiveTreeResult(
                file_name=file_name,
                format_name="rar",
                supported=False,
                root=root,
                unsupported_reason=(
                    "Tệp RAR được bảo vệ bằng mật khẩu nên chưa thể xem trực tiếp. "
                    "Hãy lưu về máy để mở bằng WinRAR / 7-Zip / ZArchiver."
                ),
            )
        except (_RarInspectionError, subprocess.TimeoutExpired, OSError):
            return ArchiveTreeResult(
                file_name=file_name,
                format_name="rar",
                supported=False,
                root=root,
                unsupported_reason=(
                    "Không thể đọc tệp RAR bằng công cụ giải nén trên máy (tệp hỏng hoặc quá thời gian xử lý). "
                    "Hãy lưu về máy để mở bằng WinRAR / 7-Zip / ZArchiver."
                ),
            )

        if any(entry.encrypted for entry in entries):
            return ArchiveTreeResult(
                file_name=file_name,
                format_name="rar",
                supported=False,
                root=root,
                unsupported_reason=(
                    "Tệp RAR được bảo vệ bằng mật khẩu nên chưa thể xem trực tiếp. "
                    "Hãy lưu về máy để mở bằng WinRAR / 7-Zip / ZArchiver."
                ),
            )

        index: dict[str, ArchiveNode] = {}
        for entry in entries:
            if entry.is_link:
                continue
            _insert_entry(root, entry.clean_path, entry.is_dir, entry.size, 0, index=index)
        total_files = _sort_and_count_files(root)
        return ArchiveTreeResult(file_name, "rar", True, root, total_files=total_files)

    def extract_entry_bytes(self, file_name: str, data: bytes, member_path: str) -> bytes:
        """Extract a single validated archive entry into memory with strict size caps."""
        target = sanitize_archive_member_path(member_path)
        lower = (file_name or "").strip().lower()

        if lower.endswith(".zip"):
            return self._extract_zip_entry(data, target)

        if lower.endswith(_TAR_EXTENSIONS):
            return self._extract_tar_entry(file_name, data, target)

        if lower.endswith(".rar"):
            if target.startswith(("-", "@")) or "*" in target or "?" in target:
                raise ArchiveSecurityError(f"Unsafe RAR member target: {target!r}")
            cli = self._rar_finder()
            if not cli:
                raise RuntimeError("No RAR extractor CLI available")
            with _temporary_rar_file(data) as tmp_path:
                try:
                    entries = self._list_rar_cli(cli, tmp_path)
                except _RarEncryptedError as exc:
                    raise ArchiveSecurityError(
                        "Encrypted RAR archive cannot be extracted"
                    ) from exc
                matches = [entry for entry in entries if entry.clean_path == target]
                if not matches:
                    raise FileNotFoundError(target)
                if any(entry.is_link for entry in matches):
                    raise ArchiveSecurityError(f"Link entry cannot be extracted: {target}")
                selected = matches[-1]
                if selected.is_dir:
                    raise FileNotFoundError(target)
                if any(entry.encrypted for entry in matches) or selected.encrypted:
                    raise ArchiveSecurityError(
                        f"Encrypted RAR entry cannot be extracted: {target}"
                    )
                if selected.size > self._max_entry:
                    raise ArchiveSecurityError("Selected entry exceeds max single entry size")
                return self._extract_rar_entry_cli(
                    cli, tmp_path, selected.raw_path, target_name=target
                )

        raise ValueError(f"Unsupported archive format: {file_name}")

    def _extract_zip_entry(self, data: bytes, target: str) -> bytes:
        tracker = _PathKindTracker()
        entry_count = 0
        total_bytes = 0
        latest_regular: Optional[zipfile.ZipInfo] = None
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            for info in zf.infolist():
                kind = _zip_member_kind(info)
                is_dir = kind == "dir"
                if _is_root_dot_dir(info.filename, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, info.file_size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                clean = sanitize_archive_member_path(info.filename)
                if kind != "special":
                    tracker.record(clean, is_dir)
                if clean != target:
                    continue
                if kind == "special":
                    raise ArchiveSecurityError(f"Link or special entry cannot be extracted: {target}")
                if is_dir:
                    continue
                if info.flag_bits & 0x1:
                    raise ArchiveSecurityError(
                        f"Tệp ZIP được bảo vệ bằng mật khẩu (encrypted), không thể giải nén: {target}"
                    )
                latest_regular = info
            if latest_regular is None:
                raise FileNotFoundError(target)
            if latest_regular.file_size > self._max_entry:
                raise ArchiveSecurityError("Selected entry exceeds max single entry size")
            with zf.open(latest_regular, "r") as stream:
                return _read_stream_capped(stream, self._max_entry)

    def _extract_tar_entry(self, file_name: str, data: bytes, target: str) -> bytes:
        tracker = _PathKindTracker()
        entry_count = 0
        total_bytes = 0
        latest_bytes: Optional[bytes] = None
        with self._open_tar_stream(file_name, data) as (tf, limit_stream):
            while (member := tf.next()) is not None:
                is_dir = member.isdir()
                if _is_root_dot_dir(member.name, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, member.size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                limit_stream.allow_entry_overhead(_TAR_PER_ENTRY_SLACK)
                clean = sanitize_archive_member_path(member.name)
                is_regular = member.isfile()
                if is_regular or is_dir:
                    tracker.record(clean, is_dir)
                if clean != target:
                    continue
                if is_dir:
                    continue
                if not is_regular:
                    raise ArchiveSecurityError(
                        f"Link or special entry cannot be extracted: {target}"
                    )
                if member.size > self._max_entry:
                    raise ArchiveSecurityError("Selected entry exceeds max single entry size")
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise FileNotFoundError(target)
                with extracted:
                    latest_bytes = _read_stream_capped(extracted, self._max_entry)
        if latest_bytes is not None:
            return latest_bytes
        raise FileNotFoundError(target)

    def _list_rar_cli(self, cli: str, rar_path: Path) -> list[_RarEntry]:
        is_7z = "7z" in Path(cli).name.lower()
        cmd = (
            [cli, "l", "-slt", "-sccUTF-8", "-p-", "--", str(rar_path)]
            if is_7z
            else [cli, "lt", "-p-", "-scf", "--", str(rar_path)]
        )
        parser: _7zSltStreamParser | _UnrarLtStreamParser = (
            _7zSltStreamParser(self._check_caps)
            if is_7z
            else _UnrarLtStreamParser(self._check_caps)
        )
        max_listing_bytes = max(1024 * 1024, (self._max_entries + 16) * 4096)

        with _managed_rar_process(cmd, _RAR_LIST_TIMEOUT_SECONDS) as (
            proc,
            timed_out,
            stderr_chunks,
            stderr_thread,
        ):
            for line in _iter_lines_bounded(
                proc.stdout, _MAX_LISTING_LINE_BYTES, max_listing_bytes
            ):
                parser.feed(line)
            proc.wait()
            stderr_thread.join(timeout=2.0)
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(cmd, _RAR_LIST_TIMEOUT_SECONDS)

        stderr_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            header_text = "\n".join(parser.header_preview)
            details = "\n".join(part for part in (stderr_text, header_text) if part).strip()
            lower_details = details.lower()
            if (
                parser.header_encrypted
                or "password" in lower_details
                or "encrypted" in lower_details
            ):
                raise _RarEncryptedError(details or "Encrypted RAR archive")
            raise _RarInspectionError(details or "Failed to list RAR")

        if parser.header_encrypted:
            raise _RarEncryptedError("Encrypted RAR archive")
        entries = parser.finish()
        tracker = _PathKindTracker()
        for entry in entries:
            if not entry.is_link:
                tracker.record(entry.clean_path, entry.is_dir)
        return entries

    def _extract_rar_entry_cli(
        self,
        cli: str,
        rar_path: Path,
        raw_path: str,
        *,
        target_name: str,
    ) -> bytes:
        raw_stripped = raw_path.lstrip()
        if raw_stripped.startswith(("-", "@")) or "*" in raw_path or "?" in raw_path:
            raise ArchiveSecurityError(f"Unsafe RAR member target: {raw_path!r}")
        is_7z = "7z" in Path(cli).name.lower()
        cmd = (
            [cli, "x", "-so", "-sccUTF-8", "-p-", "--", str(rar_path), raw_path]
            if is_7z
            else [cli, "p", "-ierr", "-idq", "-p-", "-scf", "-@", "--", str(rar_path), raw_path]
        )
        with _managed_rar_process(cmd, _RAR_EXTRACT_TIMEOUT_SECONDS) as (
            proc,
            timed_out,
            stderr_chunks,
            stderr_thread,
        ):
            stdout_stream = proc.stdout if proc.stdout is not None else io.BytesIO(b"")
            payload = _read_stream_capped(stdout_stream, self._max_entry)
            proc.wait()
            stderr_thread.join(timeout=2.0)
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(cmd, _RAR_EXTRACT_TIMEOUT_SECONDS)

        if proc.returncode != 0:
            err_text = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            err_lower = err_text.lower()
            if "password" in err_lower or "encrypted" in err_lower:
                raise ArchiveSecurityError(
                    f"Encrypted RAR entry cannot be extracted: {target_name}"
                )
            raise FileNotFoundError(target_name)
        return payload
