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
from core.courses.document_converter import convert_document_bytes
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

        page0 = session.render_page_png(0, scale=1.2, dark_mode=False)
        assert page0 is not None
        assert page0.from_cache is False
        assert page0.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

        # Separate dark-mode cache entry + disk cache hit on fresh session
        page0_dark = session.render_page_png(0, scale=1.2, dark_mode=True)
        assert page0_dark is not None
        assert page0_dark.png_bytes != page0.png_bytes

        text_p2 = session.extract_page_text(1)
        assert "Midterm" in text_p2
        hits = session.search("Rubric")
        assert len(hits) == 1
        assert hits[0].page_index == 1

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
        assert len(images) >= 1
        assert "3 trang" in (reader._subtitle_text.value or "")
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
