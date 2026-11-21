"""Prepare local, offline Office previews without sending document bytes to a service."""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import PurePosixPath
import stat
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


_MAX_ZIP_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
_MAX_XML_PART_BYTES = 16 * 1024 * 1024


def is_supported_office_package(data: bytes, file_name: str) -> bool:
    """Bound ZIP expansion before handing a package to a browser or local Office."""
    spec = _FORMATS.get(PurePosixPath(file_name.lower()).suffix)
    if spec is None or not data or len(data) > _MAX_ZIP_BYTES:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > _MAX_TOTAL_UNCOMPRESSED_BYTES:
                return False
            names: set[str] = set()
            xml_names: list[str] = []
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
                if is_xml_part and not item.is_dir():
                    xml_names.append(name)
            if spec[1] not in names or "[Content_Types].xml" not in names:
                return False
            total_xml_read = 0
            for part_name in xml_names:
                with package.open(part_name) as part_stream:
                    part_read = 0
                    while True:
                        chunk = part_stream.read(65536)
                        if not chunk:
                            break
                        part_read += len(chunk)
                        total_xml_read += len(chunk)
                        if (
                            part_read > _MAX_XML_PART_BYTES
                            or total_xml_read > _MAX_TOTAL_UNCOMPRESSED_BYTES
                        ):
                            return False
            return True
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
