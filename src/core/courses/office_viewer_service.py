"""Prepare local, offline Office and PDF.js previews without sending bytes to a service."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
from pathlib import Path, PurePosixPath
import stat
import threading
from typing import Optional
from urllib.parse import urlencode
import zipfile
import zlib

from core.courses.local_preview_server import LocalPreviewServer

_FORMATS = {
    ".docx": ("docx", "word/document.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pptx": ("pptx", "ppt/presentation.xml", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
}


@dataclass(frozen=True, slots=True)
class OfficeViewerDescriptor:
    format: str
    viewer_url: str
    blob_url: str


_MAX_ZIP_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
_MAX_XML_PART_BYTES = 16 * 1024 * 1024
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_VALIDATION_CACHE_LIMIT = 16
_VALIDATION_CACHE_LOCK = threading.Lock()
_VALIDATION_CACHE: OrderedDict[tuple[int, bytes, str], bool] = OrderedDict()


def _validate_zip_package(package: zipfile.ZipFile, required_part: str) -> bool:
    entries = package.infolist()
    if len(entries) > 5000 or sum(item.file_size for item in entries) > _MAX_TOTAL_UNCOMPRESSED_BYTES:
        return False
    names: set[str] = set()
    xml_entries: list[zipfile.ZipInfo] = []
    compressed_non_xml_entries: list[zipfile.ZipInfo] = []
    stored_non_xml_total = 0
    for item in entries:
        name = item.filename
        if item.flag_bits & 1 or "\\" in name or name.startswith("/") or ".." in PurePosixPath(name).parts:
            return False
        mode = (item.external_attr >> 16) & 0o170000
        if mode != 0 and mode not in (stat.S_IFREG, stat.S_IFDIR):
            return False
        is_xml_part = name.lower().endswith((".xml", ".rels"))
        if name in names or (is_xml_part and item.file_size > _MAX_XML_PART_BYTES):
            return False
        names.add(name)
        if item.is_dir():
            if item.file_size > 0:
                return False
            continue
        if mode == stat.S_IFDIR and name in (required_part, "[Content_Types].xml"):
            return False
        if item.compress_type == zipfile.ZIP_STORED:
            if item.compress_size != item.file_size:
                return False
        elif item.compress_size > item.file_size + 256 + (item.file_size // 16):
            return False
        if is_xml_part:
            xml_entries.append(item)
        elif item.compress_type == zipfile.ZIP_STORED:
            stored_non_xml_total += item.file_size
        else:
            compressed_non_xml_entries.append(item)

    if required_part not in names or "[Content_Types].xml" not in names:
        return False
    if stored_non_xml_total > _MAX_TOTAL_UNCOMPRESSED_BYTES:
        return False

    total_read = stored_non_xml_total
    for item in xml_entries:
        with package.open(item) as part_stream:
            part_read = 0
            while True:
                chunk = part_stream.read(65536)
                if not chunk:
                    break
                part_read += len(chunk)
                total_read += len(chunk)
                if total_read > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                    return False
                if part_read > _MAX_XML_PART_BYTES:
                    return False
    for item in compressed_non_xml_entries:
        with package.open(item) as part_stream:
            part_read = 0
            while True:
                remaining_entry = item.file_size - part_read
                remaining_total = _MAX_TOTAL_UNCOMPRESSED_BYTES - total_read
                allowed_now = min(remaining_entry, remaining_total)
                if allowed_now < 0:
                    return False
                to_read = min(65536, allowed_now + 1)
                chunk = part_stream.read(to_read)
                if not chunk:
                    break
                part_read += len(chunk)
                total_read += len(chunk)
                if part_read > item.file_size or total_read > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                    return False
            if part_read != item.file_size:
                return False
    return True


def is_supported_office_package(data: bytes, file_name: str) -> bool:
    """Bound ZIP expansion before handing a package to a browser or local Office."""
    suffix = PurePosixPath(file_name.lower()).suffix
    spec = _FORMATS.get(suffix)
    if spec is None or not data or len(data) > _MAX_ZIP_BYTES:
        return False
    if len(data) < 22 or data[:4] not in _ZIP_MAGICS:
        return False

    cache_key = (len(data), hashlib.sha256(data).digest(), suffix)
    with _VALIDATION_CACHE_LOCK:
        cached = _VALIDATION_CACHE.get(cache_key)
        if cached is not None:
            _VALIDATION_CACHE.move_to_end(cache_key)
            return cached

    valid = False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            valid = _validate_zip_package(package, spec[1])
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, RuntimeError, zlib.error):
        valid = False

    with _VALIDATION_CACHE_LOCK:
        _VALIDATION_CACHE[cache_key] = valid
        _VALIDATION_CACHE.move_to_end(cache_key)
        while len(_VALIDATION_CACHE) > _VALIDATION_CACHE_LIMIT:
            _VALIDATION_CACHE.popitem(last=False)
    return valid


def is_supported_office_package_file(file_path: Path, file_name: str) -> bool:
    """Validate an on-disk OOXML package directly without loading full bytes into RAM."""
    suffix = PurePosixPath(file_name.lower()).suffix
    spec = _FORMATS.get(suffix)
    path = Path(file_path)
    if spec is None or not path.is_file():
        return False
    try:
        size = int(path.stat().st_size)
        if size < 22 or size > _MAX_ZIP_BYTES:
            return False
        with path.open("rb") as handle:
            if handle.read(4) not in _ZIP_MAGICS:
                return False
        with zipfile.ZipFile(path) as package:
            return _validate_zip_package(package, spec[1])
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, RuntimeError, zlib.error):
        return False


def prepare_office_viewer(
    data: bytes,
    file_name: str,
    preview_server: LocalPreviewServer,
    *,
    theme: str = "light",
) -> OfficeViewerDescriptor | None:
    """Return tokenized same-origin host/blob URLs; legacy formats keep their fallback."""
    if not is_supported_office_package(data, file_name):
        return None
    format_name, _part, content_type = _FORMATS[PurePosixPath(file_name.lower()).suffix]
    try:
        blob_url = preview_server.register_bytes(data, content_type)
    except ValueError:
        # A smaller caller-supplied preview budget must retain the native reader.
        return None
    query = urlencode({"src": blob_url, "theme": "dark" if theme == "dark" else "light"})
    return OfficeViewerDescriptor(
        format=format_name,
        viewer_url=f"{preview_server.viewer_base_url()}{format_name}_host.html?{query}",
        blob_url=blob_url,
    )


def prepare_office_viewer_from_file(
    file_path: Path,
    file_name: str,
    preview_server: LocalPreviewServer,
    *,
    theme: str = "light",
) -> OfficeViewerDescriptor | None:
    """Register an on-disk OOXML file for zero-copy streaming in the Office WebView."""
    if not is_supported_office_package_file(file_path, file_name):
        return None
    format_name, _part, content_type = _FORMATS[PurePosixPath(file_name.lower()).suffix]
    try:
        blob_url = preview_server.register_file(file_path, content_type)
    except (ValueError, OSError):
        return None
    query = urlencode({"src": blob_url, "theme": "dark" if theme == "dark" else "light"})
    return OfficeViewerDescriptor(
        format=format_name,
        viewer_url=f"{preview_server.viewer_base_url()}{format_name}_host.html?{query}",
        blob_url=blob_url,
    )


def prepare_pdf_viewer(
    data: bytes,
    preview_server: LocalPreviewServer,
    *,
    theme: str = "light",
    file_path: Optional[Path] = None,
) -> OfficeViewerDescriptor | None:
    """Return tokenized same-origin pdf.js host/blob URLs for offline PDF rendering."""
    if not data and (file_path is None or not Path(file_path).is_file()):
        return None
    try:
        if file_path is not None and Path(file_path).is_file():
            blob_url = preview_server.register_file(Path(file_path), "application/pdf")
        else:
            blob_url = preview_server.register_bytes(data, "application/pdf")
    except (ValueError, OSError):
        return None
    query = urlencode({"src": blob_url, "theme": "dark" if theme == "dark" else "light"})
    return OfficeViewerDescriptor(
        format="pdf",
        viewer_url=f"{preview_server.pdf_viewer_base_url()}pdf_host.html?{query}",
        blob_url=blob_url,
    )
