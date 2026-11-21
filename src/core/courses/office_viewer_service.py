"""Prepare local, offline Office previews without sending document bytes to a service."""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import PurePosixPath
from urllib.parse import urlencode
import zipfile

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


def is_supported_office_package(data: bytes, file_name: str) -> bool:
    """Bound ZIP expansion before handing a package to a browser or local Office."""
    spec = _FORMATS.get(PurePosixPath(file_name.lower()).suffix)
    if spec is None or not data or len(data) > 32 * 1024 * 1024:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > 96 * 1024 * 1024:
                return False
            names = set()
            for item in entries:
                name = item.filename
                if item.flag_bits & 1 or "\\" in name or name.startswith("/") or ".." in PurePosixPath(name).parts:
                    return False
                if name in names or (name.endswith((".xml", ".rels")) and item.file_size > 16 * 1024 * 1024):
                    return False
                names.add(name)
            return spec[1] in names and "[Content_Types].xml" in names
    except (zipfile.BadZipFile, OSError, ValueError):
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
