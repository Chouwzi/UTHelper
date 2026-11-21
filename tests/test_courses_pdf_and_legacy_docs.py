"""Tests for native PDF rendering, disk cache, .7z archives, rich DOCX/PPTX, and legacy OLE2 formats."""

from __future__ import annotations

import io
from pathlib import Path
import struct
from types import SimpleNamespace
import zipfile

import flet as ft
import pytest

from core.courses import archive_service as archive_mod
from core.courses import document_converter as doc_mod
from core.courses.archive_service import ArchiveService
from core.courses.document_converter import (
    clear_converted_document_cache,
    convert_document_bytes,
)
from core.courses.local_preview_server import LocalPreviewServer
from core.courses.pdf_renderer import PdfDocumentSession, clear_rendered_pdf_cache
from core.courses.providers import classify_preview_kind
from gui.components.courses.document_reader_view import DocumentReaderView


def _walk_controls(root: ft.Control):
    yield root
    for attr in ("content", "leading", "trailing", "title", "subtitle"):
        child = getattr(root, attr, None)
        if isinstance(child, ft.Control):
            yield from _walk_controls(child)
    for attr in ("controls", "actions", "tabs", "views", "rows"):
        items = getattr(root, attr, None)
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, ft.Control):
                    yield from _walk_controls(item)


def _build_minimal_pdf(page_texts: list[str]) -> bytes:
    """Build a valid multi-page PDF 1.4 byte payload with searchable Helvetica text."""
    objects: list[bytes] = []

    def _add_obj(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_id = _add_obj(b"")
    pages_id = _add_obj(b"")
    font_id = _add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []

    for text in page_texts:
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_data = f"BT /F1 14 Tf 50 750 Td ({safe}) Tj ET".encode("latin-1")
        content_id = _add_obj(
            f"<< /Length {len(stream_data)} >>\nstream\n".encode("ascii")
            + stream_data
            + b"\nendstream"
        )
        page_id = _add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode(
                "ascii"
            )
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[catalog_id - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for idx, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{idx} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")

    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


def _build_minimal_ole2_cfb(streams: dict[str, bytes]) -> bytes:
    """Build a standard 512-byte-sector OLE2 Compound File Binary container."""
    sector_size = 512
    data_sectors: list[bytes] = []
    dir_entries: list[bytes] = []

    def _pack_dir_entry(name: str, obj_type: int, child_id: int, left: int, right: int, start_sec: int, size: int) -> bytes:
        entry = bytearray(128)
        if name:
            encoded = (name + "\x00").encode("utf-16-le")
            entry[: len(encoded)] = encoded
            name_len = len(encoded)
        else:
            name_len = 0
        struct.pack_into("<HBBIII", entry, 64, name_len, obj_type, 1, left, right, child_id)
        struct.pack_into("<II", entry, 116, start_sec, size)
        return bytes(entry)

    # First data sector allocation: stream sectors then directory sector, then FAT sector
    stream_starts: list[tuple[str, int, int]] = []
    for s_name, s_bytes in streams.items():
        if not s_bytes:
            stream_starts.append((s_name, 0xFFFFFFFE, 0))
            continue
        start_sec = len(data_sectors)
        padded_len = ((len(s_bytes) + sector_size - 1) // sector_size) * sector_size
        padded = s_bytes.ljust(padded_len, b"\x00")
        for off in range(0, padded_len, sector_size):
            data_sectors.append(padded[off : off + sector_size])
        stream_starts.append((s_name, start_sec, len(s_bytes)))

    dir_sec = len(data_sectors)
    # Build linked siblings chain (left=-1, right=next_idx)
    dir_entries.append(_pack_dir_entry("Root Entry", 5, 1 if stream_starts else 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFE, 0))
    for i, (s_name, s_start, s_size) in enumerate(stream_starts, start=1):
        right = i + 1 if i < len(stream_starts) else 0xFFFFFFFF
        dir_entries.append(_pack_dir_entry(s_name, 2, 0xFFFFFFFF, 0xFFFFFFFF, right, s_start, s_size))
    dir_blob = b"".join(dir_entries)
    dir_padded_len = ((len(dir_blob) + sector_size - 1) // sector_size) * sector_size
    dir_blob = dir_blob.ljust(dir_padded_len, b"\x00")
    dir_sector_count = len(dir_blob) // sector_size
    for off in range(0, len(dir_blob), sector_size):
        data_sectors.append(dir_blob[off : off + sector_size])

    fat_sec = len(data_sectors)
    fat = [0xFFFFFFFF] * (sector_size // 4)
    for s_name, s_start, s_size in stream_starts:
        if s_size == 0:
            continue
        sec_count = (s_size + sector_size - 1) // sector_size
        for k in range(sec_count):
            fat[s_start + k] = (s_start + k + 1) if k + 1 < sec_count else 0xFFFFFFFE
    for k in range(dir_sector_count):
        fat[dir_sec + k] = (dir_sec + k + 1) if k + 1 < dir_sector_count else 0xFFFFFFFE
    fat[fat_sec] = 0xFFFFFFFD  # FATSECT
    fat_blob = struct.pack(f"<{sector_size // 4}I", *fat)
    data_sectors.append(fat_blob)

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9, 6, 0)
    struct.pack_into("<IIIIII", header, 44, 1, dir_sec, 0, 4096, 0xFFFFFFFE, 0)
    difat = [0xFFFFFFFF] * 109
    difat[0] = fat_sec
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + b"".join(data_sectors)


def _build_ministream_ole2_cfb(streams: dict[str, bytes]) -> bytes:
    """Build a 512-byte-sector OLE2 CFB container placing all streams (< 4096 B) in the mini-stream."""
    sector_size = 512
    mini_sector_size = 64

    def _pack_dir_entry(
        name: str, obj_type: int, child_id: int, left: int, right: int, start_sec: int, size: int
    ) -> bytes:
        entry = bytearray(128)
        if name:
            encoded = (name + "\x00").encode("utf-16-le")
            entry[: len(encoded)] = encoded
            name_len = len(encoded)
        else:
            name_len = 0
        struct.pack_into("<HBBIII", entry, 64, name_len, obj_type, 1, left, right, child_id)
        struct.pack_into("<II", entry, 116, start_sec, size)
        return bytes(entry)

    mini_chunks: list[bytes] = []
    ssat_entries: list[int] = []
    stream_meta: list[tuple[str, int, int]] = []
    for s_name, s_bytes in streams.items():
        assert len(s_bytes) < 4096
        if not s_bytes:
            stream_meta.append((s_name, 0xFFFFFFFE, 0))
            continue
        mini_start = len(ssat_entries)
        mini_count = (len(s_bytes) + mini_sector_size - 1) // mini_sector_size
        padded = s_bytes.ljust(mini_count * mini_sector_size, b"\x00")
        for idx in range(mini_count):
            mini_chunks.append(padded[idx * mini_sector_size : (idx + 1) * mini_sector_size])
            ssat_entries.append((mini_start + idx + 1) if idx + 1 < mini_count else 0xFFFFFFFE)
        stream_meta.append((s_name, mini_start, len(s_bytes)))

    data_sectors: list[bytes] = []
    sscs_raw = b"".join(mini_chunks)
    sscs_size = len(sscs_raw)
    if sscs_size:
        sscs_start_sec = len(data_sectors)
        sscs_padded_len = ((sscs_size + sector_size - 1) // sector_size) * sector_size
        sscs_padded = sscs_raw.ljust(sscs_padded_len, b"\x00")
        for off in range(0, sscs_padded_len, sector_size):
            data_sectors.append(sscs_padded[off : off + sector_size])
        sscs_sec_count = sscs_padded_len // sector_size
    else:
        sscs_start_sec = 0xFFFFFFFE
        sscs_sec_count = 0

    if ssat_entries:
        entries_per_sec = sector_size // 4
        ssat_padded_count = ((len(ssat_entries) + entries_per_sec - 1) // entries_per_sec) * entries_per_sec
        ssat_entries.extend([0xFFFFFFFF] * (ssat_padded_count - len(ssat_entries)))
        ssat_start_sec = len(data_sectors)
        ssat_sec_count = ssat_padded_count // entries_per_sec
        ssat_blob = struct.pack(f"<{ssat_padded_count}I", *ssat_entries)
        for off in range(0, len(ssat_blob), sector_size):
            data_sectors.append(ssat_blob[off : off + sector_size])
    else:
        ssat_start_sec = 0xFFFFFFFE
        ssat_sec_count = 0

    dir_sec = len(data_sectors)
    dir_entries = [
        _pack_dir_entry(
            "Root Entry",
            5,
            1 if stream_meta else 0xFFFFFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
            sscs_start_sec,
            sscs_size,
        )
    ]
    for idx, (s_name, s_start, s_size) in enumerate(stream_meta, start=1):
        right = idx + 1 if idx < len(stream_meta) else 0xFFFFFFFF
        dir_entries.append(_pack_dir_entry(s_name, 2, 0xFFFFFFFF, 0xFFFFFFFF, right, s_start, s_size))
    dir_blob = b"".join(dir_entries)
    dir_padded_len = ((len(dir_blob) + sector_size - 1) // sector_size) * sector_size
    dir_blob = dir_blob.ljust(dir_padded_len, b"\x00")
    dir_sec_count = len(dir_blob) // sector_size
    for off in range(0, len(dir_blob), sector_size):
        data_sectors.append(dir_blob[off : off + sector_size])

    fat_sec = len(data_sectors)
    fat = [0xFFFFFFFF] * (sector_size // 4)
    for start, count in (
        (sscs_start_sec, sscs_sec_count),
        (ssat_start_sec, ssat_sec_count),
        (dir_sec, dir_sec_count),
    ):
        for k in range(count):
            fat[start + k] = (start + k + 1) if k + 1 < count else 0xFFFFFFFE
    fat[fat_sec] = 0xFFFFFFFD
    data_sectors.append(struct.pack(f"<{sector_size // 4}I", *fat))

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9, 6, 0)
    struct.pack_into(
        "<IIIIII", header, 44, 1, dir_sec, 0, 4096, ssat_start_sec, ssat_sec_count
    )
    struct.pack_into("<II", header, 68, 0xFFFFFFFE, 0)
    difat = [0xFFFFFFFF] * 109
    difat[0] = fat_sec
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + b"".join(data_sectors)


def _tiny_png_bytes() -> bytes:
    import zlib

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


# ---------------------------------------------------------------------------
# 1. Native PDF Rendering, Caching & DocumentReaderView PDF + Excel Pagination
# ---------------------------------------------------------------------------


def test_pdf_document_session_renders_caches_searches_and_fails_closed(tmp_path: Path):
    pdf_bytes = _build_minimal_pdf(
        [
            "UTH Syllabus Introduction Page One",
            "Midterm Assignment Rubric Page Two",
            "Final Project Deadline Summary",
        ]
    )
    cache_dir = tmp_path / "preview_cache"

    with PdfDocumentSession(pdf_bytes, cache_dir=cache_dir) as session:
        assert session.supported is True
        assert session.page_count == 3
        can_bitmap = session.can_render_images

        if can_bitmap:
            page0 = session.render_page_png(0, scale=1.2, dark_mode=False)
            assert page0 is not None
            assert page0.from_cache is False
            assert page0.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

            # Separate dark-mode cache entry + disk cache hit on fresh session
            page0_dark = session.render_page_png(0, scale=1.2, dark_mode=True)
            assert page0_dark is not None
            assert page0_dark.png_bytes != page0.png_bytes
        else:
            assert session.render_page_png(0, scale=1.2, dark_mode=False) is None

        text_p2 = session.extract_page_text(1)
        assert "Midterm" in text_p2
        hits = session.search("Rubric")
        assert len(hits) == 1
        assert hits[0].page_index == 1

    if can_bitmap:
        # Fresh session hits disk cache cleanly
        with PdfDocumentSession(pdf_bytes, cache_dir=cache_dir) as session2:
            cached = session2.render_page_png(0, scale=1.2, dark_mode=False)
            assert cached is not None
            assert cached.from_cache is True

        # LRU cache eviction respects tiny byte cap
        with PdfDocumentSession(pdf_bytes, cache_dir=cache_dir, cache_max_bytes=1024) as session3:
            for i in range(3):
                session3.render_page_png(i, scale=1.6, dark_mode=False)
            rendered_files = list((cache_dir / "rendered_pdf").glob("*.png"))
            assert len(rendered_files) <= 2

    removed = clear_rendered_pdf_cache(cache_dir)
    assert removed >= 0
    assert list((cache_dir / "rendered_pdf").glob("*.png")) == []

    # Invalid/corrupt bytes fail closed cleanly
    bad = PdfDocumentSession(b"%PDF-1.4\nnot-a-valid-pdf")
    assert bad.supported is False
    assert bad.page_count == 0


def test_document_reader_native_pdf_and_excel_pagination(tmp_path: Path):
    server = LocalPreviewServer()
    try:
        pdf_bytes = _build_minimal_pdf(["Alpha Topic 1", "Beta Topic 2", "Gamma Topic 3"])
        reader = DocumentReaderView(
            file_name="bai_giang.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=server,
            pdf_cache_dir=tmp_path,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
        )
        images = [c for c in _walk_controls(reader) if isinstance(c, ft.Image)]
        assert not any(isinstance(c, ft.InteractiveViewer) for c in _walk_controls(reader))
        assert "3 trang" in (reader._subtitle_text.value or "")

        if reader._pdf_session is not None and reader._pdf_session.can_render_images:
            assert len(images) >= 1
            # Zooming in must enlarge Image.width inside horizontal ScrollMode.AUTO Row
            # without introducing InteractiveViewer (which hijacked scroll-up into zoom).
            initial_width = images[0].width or 0
            zoom_in = next(
                c
                for c in _walk_controls(reader)
                if isinstance(c, ft.IconButton) and c.tooltip == "Phóng to"
            )
            zoom_reset = next(
                c
                for c in _walk_controls(reader)
                if isinstance(c, ft.TextButton) and c.tooltip == "Đặt lại mức thu phóng 100%"
            )
            zoom_in.on_click(None)
            assert reader._pdf_scale > 1.6
            zoomed_images = [c for c in _walk_controls(reader) if isinstance(c, ft.Image)]
            assert (zoomed_images[0].width or 0) > initial_width
            assert any(
                isinstance(c, ft.Row) and c.scroll == ft.ScrollMode.AUTO
                for c in _walk_controls(reader)
            )
            assert not any(isinstance(c, ft.InteractiveViewer) for c in _walk_controls(reader))

            zoom_reset.on_click(None)
            assert reader._pdf_scale == 1.6
        reader.dispose()

        # Large Excel > 120 rows & > 40 columns renders paginated DataTable
        lines = [",".join(f"Col{c}" for c in range(45))]
        for r in range(135):
            lines.append(",".join(f"r{r}_c{c}" for c in range(45)))
        csv_bytes = "\n".join(lines).encode("utf-8")

        xls_reader = DocumentReaderView(
            file_name="big.csv",
            data=csv_bytes,
            kind="csv",
            preview_server=server,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
        )
        tables = [c for c in _walk_controls(xls_reader) if isinstance(c, ft.DataTable)]
        assert len(tables) == 1
        assert len(tables[0].columns) == 40
        assert len(tables[0].rows) == 119
        texts = [c.value for c in _walk_controls(xls_reader) if isinstance(c, ft.Text) and c.value]
        assert any("120" in t and "40" in t for t in texts)
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# 2. .7z Support & Provider Classification
# ---------------------------------------------------------------------------


def test_archive_service_7z_inspection_and_rejection_of_unrar(monkeypatch):
    assert classify_preview_kind("source.7z") == "archive"
    assert classify_preview_kind("legacy.doc") == "docx"
    assert classify_preview_kind("grades.xls") == "xlsx"
    assert classify_preview_kind("slides.ppt") == "pptx"

    # UnRAR cannot inspect .7z (strictly requires 7z/7za)
    unrar_only = ArchiveService(rar_tool_finder=lambda: r"C:\WinRAR\UnRAR.exe")
    monkeypatch.setattr(archive_mod, "find_windows_7z_cli", lambda: None)
    tree_unavail = unrar_only.inspect_bytes("code.7z", b"7z\xbc\xaf\x27\x1c")
    assert tree_unavail.supported is False
    assert tree_unavail.format_name == "7z"

    # Mocked 7z CLI output inspects and extracts .7z entries cleanly
    listing_bytes = (
        b"----------\n"
        b"Path = docs/readme.txt\n"
        b"Folder = -\n"
        b"Size = 5\n"
        b"Encrypted = -\n\n"
    )

    class _FakeProc:
        def __init__(self, payload: bytes):
            self.stdout = io.BytesIO(payload)
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self):
            return 0

    calls: list[list[str]] = []

    def _fake_managed(cmd: list[str], _timeout: float):
        calls.append(cmd)
        proc = _FakeProc(b"hello" if cmd[1] == "x" else listing_bytes)

        class _Ctx:
            def __enter__(self):
                return proc, SimpleNamespace(is_set=lambda: False), [], SimpleNamespace(join=lambda **_k: None)

            def __exit__(self, *_a):
                return False

        return _Ctx()

    monkeypatch.setattr(archive_mod, "_managed_rar_process", _fake_managed)
    svc = ArchiveService(rar_tool_finder=lambda: "7z")
    tree = svc.inspect_bytes("bundle.7z", b"7z\xbc\xaf\x27\x1c")
    assert tree.supported is True
    assert tree.format_name == "7z"
    assert tree.total_files == 1
    assert calls[0][0] == "7z"
    extracted = svc.extract_entry_bytes("bundle.7z", b"7z\xbc\xaf\x27\x1c", "docs/readme.txt")
    assert extracted == b"hello"


# ---------------------------------------------------------------------------
# 3. Rich DOCX / PPTX Blocks & Legacy OLE2 (.doc, .xls, .ppt)
# ---------------------------------------------------------------------------


def test_rich_docx_and_pptx_extract_inline_images_and_tables():
    png = _tiny_png_bytes()

    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<w:body>"
                '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chuong 1</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>Doan van co hinh anh</w:t></w:r>'
                '<w:r><w:drawing><a:blip r:embed="rId5"/></w:drawing></w:r></w:p>'
                "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>"
                "<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                "</w:body></w:document>"
            ),
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/fig1.png"/>'
                "</Relationships>"
            ),
        )
        zf.writestr("word/media/fig1.png", png)

    converted_docx = convert_document_bytes("rich.docx", docx_buf.getvalue(), "docx")
    kinds = [b.kind for b in converted_docx.docx_blocks]
    assert kinds == ["heading", "paragraph", "image", "table"]
    assert converted_docx.docx_blocks[2].image_bytes == png
    assert converted_docx.docx_blocks[3].rows == (("A", "B"),)

    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        zf.writestr(
            "ppt/slides/slide1.xml",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                "<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Slide Heading</a:t></a:r></a:p></p:txBody></p:sp>"
                "<p:graphicFrame><a:graphic><a:graphicData><a:tbl>"
                "<a:tr><a:tc><a:txBody><a:p><a:r><a:t>K1</w:t></a:r></a:p></a:txBody></a:tc></a:tr>".replace(
                    "</w:t>", "</a:t>"
                )
                + "</a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>"
            ),
        )
        zf.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/chart.png"/>'
                "</Relationships>"
            ),
        )
        zf.writestr("ppt/media/chart.png", png)

    converted_pptx = convert_document_bytes("deck.pptx", pptx_buf.getvalue(), "pptx")
    assert len(converted_pptx.rich_slides) == 1
    assert converted_pptx.rich_slides[0].images[0] == ("chart.png", png)
    assert converted_pptx.rich_slides[0].tables == ((("K1",),),)


def test_legacy_ole2_doc_ppt_and_xls_converters(monkeypatch):
    # 1. Synthetic binary .doc OLE2 with FIB + Clx PlcPcd piece table (>= 4096 bytes for regular FAT)
    text_str = "Tai lieu mon lap trinh\rNoi dung bai hoc Word 97-2003."
    text_utf16 = text_str.encode("utf-16-le")
    wd = bytearray(4096)
    struct.pack_into("<HH", wd, 0, 0xA5EC, 0x00C1)
    struct.pack_into("<H", wd, 10, 0x0200)  # fWhichTblStm = 1Table
    struct.pack_into("<I", wd, 76, len(text_str))  # ccpText
    fc_text = 512
    wd[fc_text : fc_text + len(text_utf16)] = text_utf16
    struct.pack_into("<II", wd, 418, 0, 21)  # fcClx=0, lcbClx=21

    plc_pcd = struct.pack("<II", 0, len(text_str)) + struct.pack("<HIH", 0, fc_text, 0)
    clx = (struct.pack("<BI", 0x02, len(plc_pcd)) + plc_pcd).ljust(4096, b"\x00")
    doc_cfb = _build_minimal_ole2_cfb({"WordDocument": bytes(wd), "1Table": clx})

    doc_res = convert_document_bytes("de_cuong.doc", doc_cfb, "docx")
    assert "Tai lieu mon lap trinh" in doc_res.markdown_text
    assert "Word 97-2003" in doc_res.markdown_text

    # 2. Synthetic binary .ppt OLE2 with SlideContainer (0x03EE) + TextCharsAtom (0x0FA0)
    txt_atom_payload = "Bai giang Slide cu".encode("utf-16-le")
    txt_atom = struct.pack("<HHI", 0x0000, 0x0FA0, len(txt_atom_payload)) + txt_atom_payload
    slide_container = (
        struct.pack("<HHI", 0x000F, 0x03EE, len(txt_atom)) + txt_atom
    ).ljust(4096, b"\x00")
    ppt_cfb = _build_minimal_ole2_cfb({"PowerPoint Document": slide_container})

    ppt_res = convert_document_bytes("slide_cu.ppt", ppt_cfb, "pptx")
    assert len(ppt_res.slides) == 1
    assert "Bai giang Slide cu" in ppt_res.slides[0][1]

    # 3. Legacy .xls dispatched cleanly via xlrd
    class _FakeCell:
        def __init__(self, ctype: int, value: object):
            self.ctype = ctype
            self.value = value

    class _FakeSheet:
        nrows = 2
        ncols = 2
        _grid = [
            [_FakeCell(1, "MSSV"), _FakeCell(1, "Diem")],
            [_FakeCell(1, "22511"), _FakeCell(2, 9.0)],
        ]

        def cell(self, r: int, c: int):
            return self._grid[r][c]

    class _FakeBook:
        datemode = 0

        def sheet_names(self):
            return ["Bang1"]

        def sheet_by_name(self, _name: str):
            return _FakeSheet()

        def release_resources(self):
            pass

    fake_xlrd = SimpleNamespace(
        XL_CELL_EMPTY=0,
        XL_CELL_TEXT=1,
        XL_CELL_NUMBER=2,
        XL_CELL_DATE=3,
        XL_CELL_BOOLEAN=4,
        open_workbook=lambda **_k: _FakeBook(),
    )
    monkeypatch.setattr(doc_mod, "_xlrd", fake_xlrd)
    xls_cfb = _build_minimal_ole2_cfb({"Workbook": b"dummy"})
    xls_res = convert_document_bytes("diem.xls", xls_cfb, "xlsx")
    assert len(xls_res.sheets) == 1
    assert xls_res.sheets[0].rows == [["MSSV", "Diem"], ["22511", "9"]]


def test_legacy_ole2_ministream_malformed_and_no_olefile(monkeypatch, capsys):
    import sys

    # Ensure olefile is never required or imported by document_converter
    assert not hasattr(doc_mod, "_olefile")
    monkeypatch.setitem(sys.modules, "olefile", None)

    # 1. Mini-stream (< 4096 B) .doc OLE2 container
    doc_text = "Noi dung mini stream doc\rDong thu hai."
    doc_utf16 = doc_text.encode("utf-16-le")
    wd = bytearray(1024)
    struct.pack_into("<HH", wd, 0, 0xA5EC, 0x00C1)
    struct.pack_into("<H", wd, 10, 0x0200)  # 1Table
    struct.pack_into("<I", wd, 76, len(doc_text))
    fc_text = 512
    wd[fc_text : fc_text + len(doc_utf16)] = doc_utf16
    struct.pack_into("<II", wd, 418, 0, 21)
    plc_pcd = struct.pack("<II", 0, len(doc_text)) + struct.pack("<HIH", 0, fc_text, 0)
    clx = struct.pack("<BI", 0x02, len(plc_pcd)) + plc_pcd

    mini_doc_cfb = _build_ministream_ole2_cfb({"WordDocument": bytes(wd), "1Table": clx})
    mini_doc_res = convert_document_bytes("mini.doc", mini_doc_cfb, "docx")
    assert "Noi dung mini stream doc" in mini_doc_res.markdown_text
    assert "Dong thu hai." in mini_doc_res.markdown_text

    # 2. Mini-stream (< 4096 B) .ppt OLE2 container
    ppt_payload = "Slide mini stream".encode("utf-16-le")
    txt_atom = struct.pack("<HHI", 0x0000, 0x0FA0, len(ppt_payload)) + ppt_payload
    slide_container = struct.pack("<HHI", 0x000F, 0x03EE, len(txt_atom)) + txt_atom
    mini_ppt_cfb = _build_ministream_ole2_cfb({"PowerPoint Document": slide_container})
    mini_ppt_res = convert_document_bytes("mini.ppt", mini_ppt_cfb, "pptx")
    assert len(mini_ppt_res.slides) == 1
    assert "Slide mini stream" in mini_ppt_res.slides[0][1]

    # 3. Missing streams and malformed CFB inputs fail gracefully without stdout spam
    missing_doc = convert_document_bytes(
        "empty.doc", _build_minimal_ole2_cfb({"Other": b"abc"}), "docx"
    )
    assert "WordDocument" in missing_doc.markdown_text

    missing_ppt = convert_document_bytes(
        "empty.ppt", _build_minimal_ole2_cfb({"Other": b"abc"}), "pptx"
    )
    assert "PowerPoint Document" in missing_ppt.markdown_text

    # Truncated CFB + corrupted sector size / non-multiple-of-512 file size (CompDoc logs warnings if stdout used)
    truncated_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    corrupt_cfb = bytearray(mini_doc_cfb[:-13])
    struct.pack_into("<H", corrupt_cfb, 30, 99)  # preposterous sector size triggers CompDoc logfile warning
    for bad_name, bad_bytes, kind in (
        ("trunc.doc", truncated_header, "docx"),
        ("corrupt.doc", bytes(corrupt_cfb), "docx"),
        ("trunc.ppt", truncated_header, "pptx"),
        ("corrupt.ppt", bytes(corrupt_cfb), "pptx"),
    ):
        res = convert_document_bytes(bad_name, bad_bytes, kind)
        assert res.kind == kind
        assert res.markdown_text

    captured = capsys.readouterr()
    assert captured.out == ""

    # 4. Enforce _MAX_LEGACY_STREAM_BYTES cap and missing-xlrd graceful fallback
    with monkeypatch.context() as mp:
        mp.setattr(doc_mod, "_MAX_LEGACY_STREAM_BYTES", 16)
        capped_doc = convert_document_bytes("capped.doc", mini_doc_cfb, "docx")
        assert capped_doc.kind == "docx"
        assert "Noi dung mini stream doc" not in capped_doc.markdown_text

        capped_ppt = convert_document_bytes("capped.ppt", mini_ppt_cfb, "pptx")
        assert capped_ppt.kind == "pptx"
        assert capped_ppt.slides == []

    with monkeypatch.context() as mp:
        mp.setattr(doc_mod, "_xlrd", None)
        no_xlrd_doc = convert_document_bytes("missing_xlrd.doc", mini_doc_cfb, "docx")
        no_xlrd_ppt = convert_document_bytes("missing_xlrd.ppt", mini_ppt_cfb, "pptx")
        assert "xlrd" in no_xlrd_doc.markdown_text.lower()
        assert "xlrd" in no_xlrd_ppt.markdown_text.lower()


def _png_bytes(width: int = 12, height: int = 8) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (width, height), (37, 99, 235, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_rich_docx_alternate_content_omath_links_numbering_and_tables(tmp_path: Path):
    """DOCX parser deduplicates AlternateContent, extracts OMML/links/numbering/merged tables & routes links in-app."""
    img_bytes = _png_bytes(16, 12)
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
    xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <w:body>
    <mc:AlternateContent>
      <mc:Choice Requires="w14">
        <w:p><w:r><w:t>Choice Text Only</w:t></w:r></w:p>
      </mc:Choice>
      <mc:Fallback>
        <w:p><w:r><w:t>Fallback Duplicate</w:t></w:r></w:p>
      </mc:Fallback>
    </mc:AlternateContent>
    <w:p>
      <w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>Dam nghieng</w:t></w:r>
      <w:r><w:t> </w:t></w:r>
      <m:oMath><m:f><m:num><m:r><m:t>a</m:t></m:r></m:num><m:den><m:r><m:t>b</m:t></m:r></m:den></m:f></m:oMath>
      <w:hyperlink r:id="rIdSafe"><w:r><w:t>Mo bai tap</w:t></w:r></w:hyperlink>
      <w:hyperlink r:id="rIdXss"><w:r><w:t>Link doc hai</w:t></w:r></w:hyperlink>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
      <w:r><w:t>Muc thu nhat</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>
      <w:r><w:t>Muc thu hai</w:t></w:r>
    </w:p>
    <w:tbl>
      <w:tblPr>
        <w:tblBorders>
          <w:top w:val="none"/><w:left w:val="none"/><w:bottom w:val="none"/><w:right w:val="none"/>
          <w:insideH w:val="none"/><w:insideV w:val="none"/>
        </w:tblBorders>
      </w:tblPr>
      <w:tr>
        <w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>Tieu de gop</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:tcPr><w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Nhom A</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:drawing><a:blip r:embed="rIdImg"/></w:drawing></w:r><w:r><w:t>Hinh cell</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:sectPr>
      <w:headerReference w:type="default" r:id="rIdHdr"/>
    </w:sectPr>
  </w:body>
</w:document>"""

    header_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p><w:r><w:t>Header Mon Hoc</w:t></w:r></w:p>
</w:hdr>"""

    numbering_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="10">
    <w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="10"/></w:num>
</w:numbering>"""

    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>
  <Relationship Id="rIdSafe" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://courses.ut.edu.vn/mod/assign/view.php?id=5" TargetMode="External"/>
  <Relationship Id="rIdXss" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="javascript:alert(1)" TargetMode="External"/>
  <Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/cell.png"/>
</Relationships>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/header1.xml", header_xml)
        zf.writestr("word/numbering.xml", numbering_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/media/cell.png", img_bytes)

    res = convert_document_bytes("giao_trinh.docx", buf.getvalue(), "docx")
    joined_blocks = "\n".join(b.text for b in res.docx_blocks)
    assert "Choice Text Only" in joined_blocks
    assert "Fallback Duplicate" not in joined_blocks
    assert any(b.section_role == "header" and "Header Mon Hoc" in b.text for b in res.docx_blocks)

    styled = next(b for b in res.docx_blocks if "Dam nghieng" in b.text)
    assert any(r.bold and r.italic and "Dam nghieng" in r.text for r in styled.runs)
    assert any("(a)/(b)" in r.text for r in styled.runs)
    safe_run = next(r for r in styled.runs if "Mo bai tap" in r.text)
    unsafe_run = next(r for r in styled.runs if "Link doc hai" in r.text)
    assert safe_run.href == "https://courses.ut.edu.vn/mod/assign/view.php?id=5"
    assert unsafe_run.href == ""

    list_blocks = [b for b in res.docx_blocks if b.kind == "list_item"]
    assert [b.list_marker for b in list_blocks] == ["1.", "2."]

    table_block = next(b for b in res.docx_blocks if b.kind == "table")
    assert table_block.has_borders is False
    assert table_block.rich_rows[0][0].col_span == 2
    assert len(table_block.rich_rows[1][1].images) == 1

    opened_links: list[str] = []
    server = LocalPreviewServer()
    try:
        reader = DocumentReaderView(
            file_name="giao_trinh.docx",
            data=buf.getvalue(),
            kind="docx",
            preview_server=server,
            pdf_cache_dir=tmp_path,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
            on_open_link=opened_links.append,
        )
        link_spans = [
            sp
            for c in _walk_controls(reader)
            if isinstance(c, ft.Text)
            for sp in (getattr(c, "spans", None) or [])
            if isinstance(sp, ft.TextSpan) and sp.text == "Mo bai tap"
        ]
        assert len(link_spans) >= 1
        assert callable(link_spans[0].on_click)
        link_spans[0].on_click(None)
        assert opened_links == ["https://courses.ut.edu.vn/mod/assign/view.php?id=5"]
        reader.dispose()
    finally:
        server.stop()


def test_rich_pptx_layout_group_crop_smartart_chart_and_no_table_dup(tmp_path: Path):
    """PPTX parser handles layout inheritance, groups, cropped images, SmartArt, charts, and non-duplicated tables."""
    img_bytes = _png_bytes(40, 20)
    presentation_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation
    xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <p:sldSz cx="12192000" cy="6858000"/>
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
</p:presentation>"""
    pres_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>"""
    layout_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree>
    <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title Placeholder"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
      <p:txBody><a:lstStyle><a:lvl1pPr><a:defRPr sz="3600"/></a:lvl1pPr></a:lstStyle><a:p/></p:txBody>
    </p:sp>
  </p:spTree></p:cSld>
</p:sldLayout>"""
    slide1_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld
    xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">
  <p:cSld><p:spTree>
    <p:sp>
      <p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
      <p:txBody><a:p><a:r><a:t>Chuong 1: Gioi thieu</a:t></a:r></a:p></p:txBody>
    </p:sp>
    <mc:AlternateContent>
      <mc:Choice Requires="p14">
        <p:sp><p:spPr><a:xfrm><a:off x="500000" y="1500000"/><a:ext cx="4000000" cy="800000"/></a:xfrm></p:spPr>
          <p:txBody><a:p><a:r><a:t>Modern Shape</a:t></a:r></a:p></p:txBody></p:sp>
      </mc:Choice>
      <mc:Fallback>
        <p:sp><p:txBody><a:p><a:r><a:t>Legacy Shape</a:t></a:r></a:p></p:txBody></p:sp>
      </mc:Fallback>
    </mc:AlternateContent>
    <p:grpSp>
      <p:grpSpPr><a:xfrm><a:off x="500000" y="2500000"/><a:ext cx="3000000" cy="1000000"/><a:chOff x="0" y="0"/><a:chExt cx="3000000" cy="1000000"/></a:xfrm></p:grpSpPr>
      <p:sp>
        <p:spPr><a:xfrm><a:off x="100000" y="100000"/><a:ext cx="2000000" cy="500000"/></a:xfrm></p:spPr>
        <p:txBody><a:p><a:r><a:t>Trong nhom</a:t></a:r></a:p></p:txBody>
      </p:sp>
    </p:grpSp>
    <p:pic>
      <p:blipFill><a:blip r:embed="rIdImg"/><a:srcRect l="25000" t="0" r="25000" b="0"/></p:blipFill>
      <p:spPr><a:xfrm><a:off x="6500000" y="1500000"/><a:ext cx="4000000" cy="2000000"/></a:xfrm></p:spPr>
    </p:pic>
    <p:graphicFrame>
      <p:xfrm><a:off x="500000" y="4000000"/><a:ext cx="5000000" cy="1200000"/></p:xfrm>
      <a:graphic><a:graphicData><a:tbl>
        <a:tr><a:tc><a:txBody><a:p><a:r><a:t>Cot A</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>Cot B</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
        <a:tr><a:tc><a:txBody><a:p><a:r><a:t>1</a:t></a:r></a:p></a:txBody></a:tc><a:tc><a:txBody><a:p><a:r><a:t>2</a:t></a:r></a:p></a:txBody></a:tc></a:tr>
      </a:tbl></a:graphicData></a:graphic>
    </p:graphicFrame>
    <p:graphicFrame>
      <p:xfrm><a:off x="500000" y="5400000"/><a:ext cx="3000000" cy="800000"/></p:xfrm>
      <a:graphic><a:graphicData><dgm:relIds xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" r:dm="rIdDgm"/></a:graphicData></a:graphic>
    </p:graphicFrame>
    <p:graphicFrame>
      <p:xfrm><a:off x="4000000" y="5400000"/><a:ext cx="3000000" cy="800000"/></p:xfrm>
      <a:graphic><a:graphicData><c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" r:id="rIdChart"/></a:graphicData></a:graphic>
    </p:graphicFrame>
  </p:spTree></p:cSld>
</p:sld>"""
    slide1_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdLayout" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rIdImg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/img1.png"/>
  <Relationship Id="rIdDgm" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/diagramData" Target="../diagrams/data1.xml"/>
  <Relationship Id="rIdChart" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="../charts/chart1.xml"/>
</Relationships>"""
    dgm_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<dgm:dataModel xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <dgm:ptLst>
    <dgm:pt><dgm:t><a:p><a:r><a:t>Node A</a:t></a:r></a:p></dgm:t></dgm:pt>
    <dgm:pt><dgm:t><a:p><a:r><a:t>Node B</a:t></a:r></a:p></dgm:t></dgm:pt>
  </dgm:ptLst>
</dgm:dataModel>"""
    chart_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <c:chart>
    <c:title><c:tx><c:rich><a:p><a:r><a:t>Bieu do diem</a:t></a:r></a:p></c:rich></c:tx></c:title>
    <c:plotArea><c:barChart><c:ser><c:tx><c:v>Kiem tra</c:v></c:tx></c:ser></c:barChart></c:plotArea>
  </c:chart>
</c:chartSpace>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", layout_xml)
        zf.writestr("ppt/slides/slide1.xml", slide1_xml)
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", slide1_rels)
        zf.writestr("ppt/media/img1.png", img_bytes)
        zf.writestr("ppt/diagrams/data1.xml", dgm_xml)
        zf.writestr("ppt/charts/chart1.xml", chart_xml)

    res = convert_document_bytes("bai_giang.pptx", buf.getvalue(), "pptx")
    assert len(res.rich_slides) == 1
    rich = res.rich_slides[0]
    assert rich.title_text == "Chuong 1: Gioi thieu"
    assert round(rich.aspect_ratio, 2) == 1.78
    # Title & table text must not be duplicated into body_text/text elements
    element_texts = "\n".join(el.paragraphs[0].text for el in rich.elements if el.paragraphs)
    all_element_text = "\n".join(p.text for el in rich.elements for p in el.paragraphs)
    assert "Modern Shape" in all_element_text
    assert "Legacy Shape" not in all_element_text
    assert "Trong nhom" in all_element_text
    assert "Node A" in all_element_text and "Node B" in all_element_text
    assert "Bieu do diem" in all_element_text and "Kiem tra" in all_element_text
    assert "Cot A" not in element_texts
    assert rich.tables == ((("Cot A", "Cot B"), ("1", "2")),)
    # V1 plain slide tuple (num, body, notes) keeps table text for backwards compatibility
    assert "Cot A" in res.slides[0][1] and "Cot B" in res.slides[0][1]
    # Cropped 40x20 image (l=25%, r=25%) -> 20x20 PNG
    from PIL import Image

    cropped = Image.open(io.BytesIO(rich.images[0][1]))
    assert cropped.size == (20, 20)

    server = LocalPreviewServer()
    try:
        reader = DocumentReaderView(
            file_name="bai_giang.pptx",
            data=buf.getvalue(),
            kind="pptx",
            preview_server=server,
            pdf_cache_dir=tmp_path,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
        )
        texts = [c.value for c in _walk_controls(reader) if isinstance(c, ft.Text) and c.value]
        assert "Slide 1" in texts
        assert any("Chuong 1: Gioi thieu" in t for t in texts)
        assert len([c for c in _walk_controls(reader) if isinstance(c, ft.Image)]) >= 1
        reader.dispose()
    finally:
        server.stop()


def test_high_fidelity_docx_theme_styles_numbering_footnotes_tables_a4_sheets_and_lru_cache(
    tmp_path: Path,
):
    """DOCX OOXML engine resolves theme colors, cascading styles, %1.%2. lists, footnotes, vMerge tables, A4 sheets, zoom & LRU cache."""
    clear_converted_document_cache()
    img_bytes = _png_bytes(60, 30)

    theme_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="UTH Theme">
  <a:themeElements>
    <a:clrScheme name="UTH">
      <a:dk1><a:sysClr val="windowText" lastClr="111827"/></a:dk1>
      <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
      <a:accent1><a:srgbClr val="1E5AA8"/></a:accent1>
    </a:clrScheme>
    <a:fontScheme name="UTH Fonts">
      <a:majorFont><a:latin typeface="Times New Roman"/></a:majorFont>
      <a:minorFont><a:latin typeface="Times New Roman"/></a:minorFont>
    </a:fontScheme>
  </a:themeElements>
</a:theme>"""

    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:asciiTheme="minorHAnsi"/><w:sz w:val="26"/></w:rPr></w:rPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:styleId="BaseBody">
    <w:pPr><w:jc w:val="both"/><w:ind w:firstLine="480"/></w:pPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="AccentCaps">
    <w:basedOn w:val="BaseBody"/>
    <w:rPr>
      <w:caps/>
      <w:color w:val="1E5AA8" w:themeColor="accent1" w:themeTint="BF"/>
    </w:rPr>
  </w:style>
</w:styles>"""

    numbering_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="7">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2."/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="3"><w:abstractNumId w:val="7"/></w:num>
</w:numbering>"""

    footnotes_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>
  <w:footnote w:id="1">
    <w:p><w:r><w:t>Nguồn: Giáo trình CNXHKH trang 45.</w:t></w:r></w:p>
  </w:footnote>
</w:footnotes>"""

    doc_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/chart.png"/>
</Relationships>"""

    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
    xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
    xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"
    xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <w:body>
    <w:tbl>
      <w:tblPr>
        <w:tblBorders>
          <w:top w:val="none"/><w:left w:val="none"/><w:bottom w:val="none"/>
          <w:right w:val="none"/><w:insideH w:val="none"/><w:insideV w:val="none"/>
        </w:tblBorders>
      </w:tblPr>
      <w:tblGrid><w:gridCol w:w="4000"/><w:gridCol w:w="6000"/></w:tblGrid>
      <w:tr>
        <w:tc>
          <w:tcPr><w:vMerge w:val="restart"/><w:shd w:fill="EEF4FF"/></w:tcPr>
          <w:p><w:r><w:t>TRƯỜNG ĐH GTVT TP.HCM</w:t></w:r></w:p>
        </w:tc>
        <w:tc>
          <w:tcPr><w:tcBorders><w:bottom w:val="single" w:sz="6"/></w:tcBorders></w:tcPr>
          <w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:b/><w:t>CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM</w:t></w:r></w:p>
        </w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
        <w:tc><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>Độc lập - Tự do - Hạnh phúc</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="3"/></w:numPr></w:pPr>
      <w:r><w:t>Phần mở đầu</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr></w:pPr>
      <w:r><w:t>Mục tiêu nghiên cứu</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="3"/></w:numPr></w:pPr>
      <w:r><w:t>Phạm vi đề tài</w:t></w:r>
    </w:p>
    <w:p>
      <w:pPr><w:pStyle w:val="AccentCaps"/></w:pPr>
      <w:r><w:t>Nội dung trọng tâm</w:t></w:r>
      <w:r><w:footnoteReference w:id="1"/></w:r>
    </w:p>
    <w:p>
      <w:r>
        <w:drawing>
          <wp:inline>
            <wp:extent cx="2857500" cy="1428750"/>
            <a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rIdImg1"/></pic:blipFill></pic:pic></a:graphicData></a:graphic>
          </wp:inline>
        </w:drawing>
      </w:r>
    </w:p>
    <w:p><w:r><w:br w:type="page"/></w:r></w:p>
    <w:p><w:r><w:t>Nội dung trang thứ hai</w:t></w:r></w:p>
  </w:body>
</w:document>"""

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/theme/theme1.xml", theme_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/numbering.xml", numbering_xml)
        zf.writestr("word/footnotes.xml", footnotes_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/media/chart.png", img_bytes)
    docx_bytes = buf.getvalue()

    res1 = convert_document_bytes("tieu_luan.docx", docx_bytes, "docx")
    res2 = convert_document_bytes("tieu_luan.docx", docx_bytes, "docx")
    assert res1 is res2  # Thread-safe LRU cache hit!

    tbl_block = next(b for b in res1.docx_blocks if b.kind == "table")
    assert tbl_block.has_borders is False
    assert len(tbl_block.col_widths) == 2
    assert tbl_block.col_widths[1] > tbl_block.col_widths[0]
    assert tbl_block.rich_rows[0][0].row_span == 2
    assert tbl_block.rich_rows[0][0].bg_color.upper() == "#EEF4FF"
    assert tbl_block.rich_rows[0][1].has_bottom_rule is True

    list_prefixes = [b.list_marker for b in res1.docx_blocks if b.list_marker]
    assert list_prefixes[:3] == ["1.", "1.1.", "1.2."]

    styled_p = next(
        b
        for b in res1.docx_blocks
        if any("nội dung trọng tâm" in r.text.lower() for r in b.runs)
    )
    assert styled_p.align == "justify"
    assert styled_p.first_line_px == 32
    assert styled_p.runs[0].all_caps is True
    assert styled_p.runs[0].font_kind == "serif"
    assert styled_p.runs[0].font_size_pt == 13.0
    assert styled_p.runs[0].color.startswith("#")
    assert any(r.superscript and "[1]" in r.text for r in styled_p.runs)
    assert any(
        "Nguồn: Giáo trình CNXHKH trang 45." in (b.text or "".join(r.text for r in b.runs))
        for b in res1.docx_blocks
    )

    img_block = next(b for b in res1.docx_blocks if b.kind == "image")
    assert (img_block.image_width_px, img_block.image_height_px) == (300, 150)
    assert any(b.kind == "page_break" for b in res1.docx_blocks)

    server = LocalPreviewServer()
    try:
        reader = DocumentReaderView(
            file_name="tieu_luan.docx",
            data=docx_bytes,
            kind="docx",
            preview_server=server,
            preconverted=res1,
            pane_width=680.0,
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
        )
        texts = [c.value for c in _walk_controls(reader) if isinstance(c, ft.Text) and c.value]
        assert any("2 trang" in t for t in texts)
        # In narrow 680px Pane 3 at 100% zoom, sheet stretches fluidly (width=None) without right-edge overflow!
        lv = next(c for c in _walk_controls(reader) if isinstance(c, ft.ListView))
        assert isinstance(lv.controls[0], ft.Container)
        assert lv.controls[0].width is None

        # Expanding Pane 3 via sync_bounds (>= 850px) centers A4 paper at 794px max width
        reader.sync_bounds(73.0, 36.0, 1047.0, 720.0)
        lv_expanded = next(c for c in _walk_controls(reader) if isinstance(c, ft.ListView))
        assert isinstance(lv_expanded.controls[0], ft.Container)
        assert getattr(lv_expanded.controls[0].content, "width", None) == 794

        # Zooming in (> 100%) enables horizontal ScrollMode.AUTO so nothing is clipped
        initial_zoom = reader._docx_scale
        zoom_in = next(
            c
            for c in _walk_controls(reader)
            if isinstance(c, ft.IconButton) and c.tooltip == "Phóng to trang Word"
        )
        zoom_in.on_click(None)
        assert reader._docx_scale > initial_zoom
        lv_zoomed = next(c for c in _walk_controls(reader) if isinstance(c, ft.ListView))
        assert isinstance(lv_zoomed.controls[0], ft.Row)
        assert lv_zoomed.controls[0].scroll == ft.ScrollMode.AUTO
        reader.dispose()
    finally:
        server.stop()


def test_pdf_peek_ram_cache_luma_lut_and_zero_latency_theme_prewarm(tmp_path: Path):
    import time

    pdf_bytes = _build_minimal_pdf(["Page 1 Fast Luma", "Page 2 Smooth Toggle"])
    with PdfDocumentSession(pdf_bytes, cache_dir=tmp_path / "pdf_fast") as session:
        assert session.peek_cached_page_png(0, scale=1.6, dark_mode=False) is None
        if not session.can_render_images:
            pytest.skip("pypdfium2 + Pillow optional on minimal CI runtimes")
        rendered_light = session.render_page_png(0, scale=1.6, dark_mode=False)
        assert rendered_light is not None
        peek_light = session.peek_cached_page_png(0, scale=1.6, dark_mode=False)
        assert peek_light is not None
        assert peek_light.from_cache is True
        assert peek_light.png_bytes == rendered_light.png_bytes

        rendered_dark = session.render_page_png(0, scale=1.6, dark_mode=True)
        assert rendered_dark is not None
        assert rendered_dark.png_bytes != rendered_light.png_bytes
        assert session.peek_cached_page_png(0, scale=1.6, dark_mode=True) is not None

    server = LocalPreviewServer()
    try:
        pdf_reader = DocumentReaderView(
            file_name="fast_toggle.pdf",
            data=pdf_bytes,
            kind="pdf",
            preview_server=server,
            pdf_cache_dir=tmp_path / "pdf_reader_fast",
            on_close=lambda: None,
            on_save=lambda _n, _b: None,
            on_open_external=lambda _n, _b: None,
        )
        # Wait briefly for background PDF pre-render + opposite dark_mode pre-warm
        deadline = time.monotonic() + 2.0
        while (
            pdf_reader._pdf_session is not None
            and pdf_reader._pdf_session.peek_cached_page_png(0, scale=1.6, dark_mode=True) is None
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        assert pdf_reader._pdf_session is not None
        assert pdf_reader._pdf_session.peek_cached_page_png(0, scale=1.6, dark_mode=True) is not None

        # Toggling dark mode hits RAM immediately with actual PNG bytes (not None placeholder)
        pdf_reader._toggle_reader_mode()
        assert pdf_reader._dark_reader is True
        dark_imgs = [c for c in _walk_controls(pdf_reader) if isinstance(c, ft.Image)]
        assert dark_imgs and isinstance(dark_imgs[0].src, (bytes, bytearray))
        pdf_reader.dispose()
    finally:
        server.stop()

