"""Path-safe interactive archive explorer for .zip, .tar.gz, and optional desktop .rar."""

from __future__ import annotations

import io
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Callable, Iterator, Optional

from core.courses.models import PreviewKind
from core.courses.providers import classify_preview_kind

_TAR_EXTENSIONS = (".tar", ".tar.gz", ".tgz")
_READ_CHUNK_SIZE = 65536


class ArchiveSecurityError(ValueError):
    """Raised when an archive violates ZipSlip/TarSlip, link, or resource caps."""


class _RarInspectionError(RuntimeError):
    """Internal error raised when a RAR CLI tool fails or times out."""


class _RarEncryptedError(_RarInspectionError):
    """Internal error raised when a RAR archive is password-protected."""


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

    normalized = raw_path.replace("\\", "/").strip()
    if not normalized:
        raise ArchiveSecurityError("Blank archive member path")
    if normalized.startswith("/"):
        raise ArchiveSecurityError(f"Absolute or UNC path forbidden: {raw_path!r}")
    if ":" in normalized:
        raise ArchiveSecurityError(f"Drive letter or colon forbidden: {raw_path!r}")

    segments: list[str] = []
    for segment in normalized.split("/"):
        if segment in ("", "."):
            continue
        if segment.strip() == "..":
            raise ArchiveSecurityError(f"Path traversal forbidden: {raw_path!r}")
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
    for fixed in (
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "WinRAR" / "UnRAR.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "WinRAR" / "UnRAR.exe",
    ):
        if fixed.is_file():
            return str(fixed)
    return None


find_rar_cli = find_windows_rar_cli


def _is_root_dot_dir(raw_name: str, is_dir: bool) -> bool:
    if not is_dir:
        return False
    return raw_name.replace("\\", "/").strip() in {".", "./", "./."}


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


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
        if existing is None:
            node_is_dir = is_dir if last else True
            existing = ArchiveNode(
                name=part,
                full_path=rel,
                is_dir=node_is_dir,
                size=0 if node_is_dir else size,
                compressed_size=0 if node_is_dir else comp_size,
                preview_kind="unsupported" if node_is_dir else classify_preview_kind(part),
            )
            curr.children.append(existing)
            if index is not None:
                index[rel] = existing
        elif not last and not existing.is_dir:
            existing.is_dir = True
            existing.size = 0
            existing.compressed_size = 0
            existing.preview_kind = "unsupported"
        curr = existing


def _sort_and_count_files(node: ArchiveNode) -> int:
    node.children.sort(key=lambda child: (0 if child.is_dir else 1, child.name.lower(), child.name))
    total_files = 0
    for child in node.children:
        if child.is_dir:
            total_files += _sort_and_count_files(child)
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
            tmp_path.unlink(missing_ok=True)


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
            except (tarfile.TarError, EOFError, OSError, zlib.error, struct.error, ValueError):
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

    def _inspect_zip(self, file_name: str, data: bytes, root: ArchiveNode) -> ArchiveTreeResult:
        index: dict[str, ArchiveNode] = {}
        entry_count = 0
        total_bytes = 0
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            for info in zf.infolist():
                is_dir = info.is_dir() or info.filename.endswith(("/", "\\"))
                if _is_root_dot_dir(info.filename, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, info.file_size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                clean = sanitize_archive_member_path(info.filename)
                if _is_zip_symlink(info):
                    continue
                _insert_entry(
                    root,
                    clean,
                    is_dir,
                    entry_size,
                    max(0, info.compress_size),
                    index=index,
                )
        total_files = _sort_and_count_files(root)
        return ArchiveTreeResult(file_name, "zip", True, root, total_files=total_files)

    def _inspect_tar(self, file_name: str, data: bytes, root: ArchiveNode) -> ArchiveTreeResult:
        index: dict[str, ArchiveNode] = {}
        entry_count = 0
        total_bytes = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                is_dir = member.isdir()
                if _is_root_dot_dir(member.name, is_dir):
                    continue
                entry_count += 1
                entry_size = max(0, member.size)
                total_bytes += entry_size
                self._check_caps(entry_count, total_bytes)
                clean = sanitize_archive_member_path(member.name)
                if not (member.isfile() or is_dir):
                    continue
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

        index: dict[str, ArchiveNode] = {}
        for clean, is_dir, size in entries:
            _insert_entry(root, clean, is_dir, size, 0, index=index)
        total_files = _sort_and_count_files(root)
        return ArchiveTreeResult(file_name, "rar", True, root, total_files=total_files)

    def extract_entry_bytes(self, file_name: str, data: bytes, member_path: str) -> bytes:
        """Extract a single validated archive entry into memory with strict size caps."""
        target = sanitize_archive_member_path(member_path)
        lower = (file_name or "").strip().lower()

        if lower.endswith(".zip"):
            return self._extract_zip_entry(data, target)

        if lower.endswith(_TAR_EXTENSIONS):
            return self._extract_tar_entry(data, target)

        if lower.endswith(".rar"):
            if target.startswith(("-", "@")):
                raise ArchiveSecurityError(f"Unsafe RAR member prefix: {target!r}")
            cli = self._rar_finder()
            if not cli:
                raise RuntimeError("No RAR extractor CLI available")
            with _temporary_rar_file(data) as tmp_path:
                return self._extract_rar_entry_cli(cli, tmp_path, target)

        raise ValueError(f"Unsupported archive format: {file_name}")

    def _extract_zip_entry(self, data: bytes, target: str) -> bytes:
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            for info in zf.infolist():
                is_dir = info.is_dir() or info.filename.endswith(("/", "\\"))
                if _is_root_dot_dir(info.filename, is_dir) or is_dir:
                    continue
                clean = sanitize_archive_member_path(info.filename)
                if clean != target:
                    continue
                if _is_zip_symlink(info):
                    raise ArchiveSecurityError(f"Symlink entry cannot be extracted: {target}")
                if info.file_size > self._max_entry:
                    raise ArchiveSecurityError("Selected entry exceeds max single entry size")
                with zf.open(info, "r") as stream:
                    return _read_stream_capped(stream, self._max_entry)
        raise FileNotFoundError(target)

    def _extract_tar_entry(self, data: bytes, target: str) -> bytes:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            for member in tf.getmembers():
                is_dir = member.isdir()
                if _is_root_dot_dir(member.name, is_dir) or is_dir:
                    continue
                clean = sanitize_archive_member_path(member.name)
                if clean != target:
                    continue
                if not member.isfile():
                    raise ArchiveSecurityError(f"Link or special entry cannot be extracted: {target}")
                if member.size > self._max_entry:
                    raise ArchiveSecurityError("Selected entry exceeds max single entry size")
                extracted = tf.extractfile(member)
                if extracted is None:
                    raise FileNotFoundError(target)
                with extracted:
                    return _read_stream_capped(extracted, self._max_entry)
        raise FileNotFoundError(target)

    def _list_rar_cli(self, cli: str, rar_path: Path) -> list[tuple[str, bool, int]]:
        is_7z = "7z" in Path(cli).name.lower()
        cmd = (
            [cli, "l", "-slt", "-p-", "--", str(rar_path)]
            if is_7z
            else [cli, "lb", "-p-", "--", str(rar_path)]
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            details = "\n".join(part for part in (proc.stderr, proc.stdout) if part).strip()
            lower_details = details.lower()
            if "password" in lower_details or "encrypted" in lower_details:
                raise _RarEncryptedError(details or "Encrypted RAR archive")
            raise _RarInspectionError(details or "Failed to list RAR")

        if is_7z:
            return self._parse_7z_slt_output(proc.stdout)
        return self._parse_unrar_lb_output(proc.stdout)

    def _parse_7z_slt_output(self, stdout: str) -> list[tuple[str, bool, int]]:
        results: list[tuple[str, bool, int]] = []
        in_entries = False
        header_encrypted = False
        any_encrypted = False
        entry_count = 0
        total_bytes = 0

        cur_path = ""
        cur_size = 0
        cur_dir = False
        cur_encrypted = False
        cur_symlink = False

        for line in stdout.splitlines() + [""]:
            stripped = line.strip()
            if not in_entries:
                if stripped == "----------":
                    in_entries = True
                elif stripped == "Encrypted = +":
                    header_encrypted = True
                continue

            if not stripped:
                if cur_path:
                    if not _is_root_dot_dir(cur_path, cur_dir):
                        entry_count += 1
                        total_bytes += cur_size
                        self._check_caps(entry_count, total_bytes)
                        clean = sanitize_archive_member_path(cur_path)
                        if cur_encrypted:
                            any_encrypted = True
                        if not cur_symlink:
                            results.append((clean, cur_dir, cur_size))
                cur_path = ""
                cur_size = 0
                cur_dir = False
                cur_encrypted = False
                cur_symlink = False
            elif line.startswith("Path = "):
                cur_path = line[7:].strip()
            elif line.startswith("Size = "):
                cur_size = _parse_nonneg_int(line[7:])
            elif line.startswith("Folder = "):
                cur_dir = line[9:].strip() == "+"
            elif line.startswith("Encrypted = "):
                cur_encrypted = line[12:].strip() == "+"
            elif line.startswith("Symbolic Link = ") and line[16:].strip():
                cur_symlink = True

        if header_encrypted or any_encrypted:
            raise _RarEncryptedError("Encrypted RAR archive")
        return results

    def _parse_unrar_lb_output(self, stdout: str) -> list[tuple[str, bool, int]]:
        results: list[tuple[str, bool, int]] = []
        entry_count = 0
        for raw_line in stdout.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            is_dir = stripped.endswith(("/", "\\"))
            if _is_root_dot_dir(stripped, True):
                continue
            entry_count += 1
            self._check_caps(entry_count, 0)
            clean = sanitize_archive_member_path(stripped)
            results.append((clean, is_dir, 0))
        return results

    def _extract_rar_entry_cli(self, cli: str, rar_path: Path, target: str) -> bytes:
        target = sanitize_archive_member_path(target)
        if target.startswith(("-", "@")):
            raise ArchiveSecurityError(f"Unsafe RAR member prefix: {target!r}")
        is_7z = "7z" in Path(cli).name.lower()
        cmd = (
            [cli, "x", "-so", "-p-", "--", str(rar_path), target]
            if is_7z
            else [cli, "p", "-inul", "-p-", "--", str(rar_path), target]
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=20,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            err_lower = (proc.stderr or b"").lower()
            if b"password" in err_lower or b"encrypted" in err_lower:
                raise RuntimeError("Encrypted RAR entry cannot be extracted")
            raise FileNotFoundError(target)
        if len(proc.stdout) > self._max_entry:
            raise ArchiveSecurityError("Extracted RAR entry exceeds size cap")
        return proc.stdout
