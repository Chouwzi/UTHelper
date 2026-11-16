"""Unit and security tests for pure-Python document preview conversion."""

from __future__ import annotations

import csv
import html
import io
import zipfile

from core.courses.document_converter import (
    ConvertedDocument,
    SpreadsheetSheet,
    convert_document_bytes,
)


def _minimal_docx(paragraph_text: str) -> bytes:
    escaped_text = html.escape(paragraph_text)
    doc_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chương 1</w:t></w:r></w:p>
        <w:p><w:r><w:t>{escaped_text}</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cột A</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      </w:body>
    </w:document>'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


def _minimal_xlsx() -> bytes:
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Diem" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sst = (
        '<?xml version="1.0"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>MSSV</t></si>"
        "<si><t>&lt;script&gt;xss&lt;/script&gt;</t></si>"
        "</sst>"
    )
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="n"><v>9.5</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>1</v></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", s1)
    return buf.getvalue()


def test_convert_docx_produces_outline_markdown_and_escaped_html():
    raw = _minimal_docx("Xin chào & <script>alert(1)</script>")
    converted = convert_document_bytes("bai_giang.docx", raw, "docx")

    assert isinstance(converted, ConvertedDocument)
    assert len(converted.outline) == 1
    assert converted.outline[0].anchor_id
    assert converted.outline[0].level == 1
    assert converted.outline[0].title == "Chương 1"
    assert f'id="{converted.outline[0].anchor_id}"' in converted.html_document
    assert "Cột A" in converted.markdown_text
    assert "<script>alert(1)</script>" not in converted.html_document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in converted.html_document


def test_convert_docx_outline_lvl_pipe_escape_and_media_whitelist():
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>Mục 1.1</w:t></w:r></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="9"/></w:pPr><w:r><w:t>Đoạn thường</w:t></w:r></w:p>
        <w:p><w:r><a:blip r:embed="rIdImg"/></w:r></w:p>
        <w:p><w:r><a:blip r:embed="rIdExt"/></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>A | B</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
      </w:body>
    </w:document>"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rIdImg"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        Target="media/diagram.png"/>
      <Relationship Id="rIdExt"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        Target="https://evil.example/leak.png" TargetMode="External"/>
    </Relationships>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/media/diagram.png", b"\x89PNG\r\n\x1a\nPNGDATA")
        zf.writestr("word/media/unreferenced.jpg", b"\xff\xd8\xff\xe0JPGDATA")
        zf.writestr("word/media/evil.svg", b"<svg onload='alert(1)'></svg>")
        zf.writestr("word/media/evil.html", b"<script>alert(1)</script>")

    converted = convert_document_bytes("notes.docx", buf.getvalue(), "docx")

    assert len(converted.outline) == 1
    assert converted.outline[0].level == 2
    assert converted.outline[0].title == "Mục 1.1"
    assert "A \\| B" in converted.markdown_text
    assert "data:image/png;base64," in converted.html_document
    assert "data:image/jpeg;base64," in converted.html_document
    assert "data:image/svg+xml" not in converted.html_document
    assert "data:text/html" not in converted.html_document
    assert "https://evil.example/leak.png" not in converted.html_document


def test_convert_docx_blocks_dtd_entities_zip_bombs_and_corrupt_archives():
    corrupt = convert_document_bytes("broken.docx", b"not-a-zip-file", "docx")
    assert corrupt.kind == "docx"
    assert "Không thể đọc nội dung tệp" in corrupt.html_document

    dtd_xml = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [ <!ENTITY lol "lol"> ]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>&lol;</w:t></w:r></w:p></w:body>
    </w:document>"""
    dtd_buf = io.BytesIO()
    with zipfile.ZipFile(dtd_buf, "w") as zf:
        zf.writestr("word/document.xml", dtd_xml)
    dtd_result = convert_document_bytes("dtd.docx", dtd_buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" in dtd_result.html_document

    bomb_payload = (
        b'<?xml version="1.0"?>'
        b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b"<w:body><w:p><w:r><w:t>"
        + (b"A" * (17 * 1024 * 1024))
        + b"</w:t></w:r></w:p></w:body></w:document>"
    )
    bomb_buf = io.BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", bomb_payload)
    bomb_result = convert_document_bytes("bomb.docx", bomb_buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" in bomb_result.html_document


def test_convert_xlsx_parses_shared_strings_sheets_and_escapes_xss():
    raw = _minimal_xlsx()
    converted = convert_document_bytes("bang_diem.xlsx", raw, "xlsx")

    assert len(converted.sheets) == 1
    assert isinstance(converted.sheets[0], SpreadsheetSheet)
    assert converted.sheets[0].name == "Diem"
    assert converted.sheets[0].rows[0] == ["MSSV", "9.5"]
    assert converted.sheets[0].rows[1][0] == "<script>xss</script>"
    assert "position: sticky" in converted.html_document
    assert "<script>xss</script>" not in converted.html_document


def test_convert_xlsx_handles_rel_normalization_cell_types_and_xfd_column_cap():
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="Main &lt;1&gt;" sheetId="1" r:id="rId1"/>'
        '<sheet name="Second" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="./worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="/xl/worksheets/sheet2.xml"/>'
        "</Relationships>"
    )
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1">'
        '<c t="b"><v>1</v></c>'
        '<c t="inlineStr"><is><t>Inline &amp; Ok</t></is></c>'
        '<c t="str"><v>CachedFormula</v></c>'
        '<c r="XFD1" t="n"><v>999</v></c>'
        "</row>"
        "</sheetData></worksheet>"
    )
    s2 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="B1" t="b"><v>0</v></c></row>'
        "</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", s1)
        zf.writestr("xl/worksheets/sheet2.xml", s2)

    converted = convert_document_bytes("multi.xlsx", buf.getvalue(), "xlsx")

    assert len(converted.sheets) == 2
    assert converted.sheets[0].name == "Main <1>"
    assert converted.sheets[0].rows[0] == ["TRUE", "Inline & Ok", "CachedFormula"]
    assert len(converted.sheets[0].rows[0]) <= 100
    assert "999" not in converted.sheets[0].rows[0]
    assert converted.sheets[1].rows[0] == ["", "FALSE"]
    assert "Main &lt;1&gt;" in converted.html_document


def test_convert_csv_and_tsv_decode_escape_xss_and_dark_mode():
    csv_bytes = "\ufeffTên,Ghi chú\nAn,<img src=x onerror=alert(1)>\n".encode("utf-8")
    light = convert_document_bytes("ds.csv", csv_bytes, "csv", dark_mode=False)
    dark = convert_document_bytes("ds.csv", csv_bytes, "csv", dark_mode=True)

    assert len(light.sheets) == 1
    assert light.sheets[0].rows == [["Tên", "Ghi chú"], ["An", "<img src=x onerror=alert(1)>"]]
    assert "position: sticky" in light.html_document
    assert "<img src=x onerror=alert(1)>" not in light.html_document
    assert "&lt;img src=x onerror=alert(1)&gt;" in light.html_document
    assert light.html_document != dark.html_document

    tsv_bytes = "Cột1\tCột2\n1\t2\n".encode("utf-8")
    tsv = convert_document_bytes("data.tsv", tsv_bytes, "csv")
    assert tsv.sheets[0].rows == [["Cột1", "Cột2"], ["1", "2"]]

    cp1258_bytes = b"\xd0i\xea\xd2m,Ghi\n10,T\xf4\xect\n"
    cp_doc = convert_document_bytes("legacy.csv", cp1258_bytes, "csv")
    assert cp_doc.sheets[0].rows == [["Điểm", "Ghi"], ["10", "Tốt"]]

    invalid_bytes = b"\x81\xff,ok\n"
    fallback_doc = convert_document_bytes("bad_bytes.csv", invalid_bytes, "csv")
    assert fallback_doc.sheets[0].rows[0][1] == "ok"


def test_convert_xlsx_caps_sheets_and_rows():
    wb_sheets = "".join(
        f'<sheet name="S{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, 25)
    )
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{wb_sheets}</sheets></workbook>"
    )
    rels_entries = "".join(
        f'<Relationship Id="rId{i}"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        f' Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, 25)
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels_entries}</Relationships>"
    )
    many_rows = "".join(
        f'<row r="{r}"><c r="A{r}" t="n"><v>{r}</v></c></row>' for r in range(1, 1050)
    )
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{many_rows}</sheetData></worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        for i in range(1, 25):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", s1 if i == 1 else '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData/></worksheet>')

    converted = convert_document_bytes("capped.xlsx", buf.getvalue(), "xlsx")
    assert len(converted.sheets) == 20
    assert len(converted.sheets[0].rows) == 1000


def test_convert_pptx_numeric_fallback_and_notes_skip_sldnum():
    slide1 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Fallback 1</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    slide2 = slide1.replace("Fallback 1", "Fallback 2")
    slide10 = slide1.replace("Fallback 1", "Fallback 10")
    notes1 = (
        '<?xml version="1.0"?>'
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree>"
        '<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
        "<p:txBody><a:p><a:r><a:t>99</a:t></a:r></a:p></p:txBody></p:sp>"
        "<p:sp><p:txBody><a:p><a:r><a:t>Ghi chú thực</a:t></a:r></a:p></p:txBody></p:sp>"
        "</p:spTree></p:cSld></p:notes>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide10.xml", slide10)
        zf.writestr("ppt/slides/slide2.xml", slide2)
        zf.writestr("ppt/slides/slide1.xml", slide1)
        zf.writestr("ppt/notesSlides/notesSlide1.xml", notes1)

    converted = convert_document_bytes("fallback.pptx", buf.getvalue(), "pptx")
    assert [body for _, body, _ in converted.slides] == [
        "Fallback 1",
        "Fallback 2",
        "Fallback 10",
    ]
    assert converted.slides[0][2] == "Ghi chú thực"


def test_convert_docx_rejects_utf16_dtd_and_truncates_large_text():
    utf16_dtd = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE lolz [ <!ENTITY a "1"> ]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'
    ).encode("utf-16")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", utf16_dtd)
    doc = convert_document_bytes("utf16_dtd.docx", buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" in doc.html_document

    huge_text = b"x" * (2 * 1024 * 1024 + 128)
    txt_doc = convert_document_bytes("huge.log", huge_text, "text")
    assert "rút gọn" in txt_doc.markdown_text
    assert len(txt_doc.markdown_text) < len(huge_text)


def test_convert_pptx_orders_slides_maps_rels_notes_and_escapes_xss():
    pres = (
        '<?xml version="1.0"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<p:sldIdLst>"
        '<p:sldId id="256" r:id="rIdFirst"/>'
        '<p:sldId id="257" r:id="rIdSecond"/>'
        "</p:sldIdLst></p:presentation>"
    )
    pres_rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdFirst"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide1.xml"/>'
        '<Relationship Id="rIdSecond"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide2.xml"/>'
        "</Relationships>"
    )
    slide1 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Slide Một &lt;b&gt;body&lt;/b&gt;</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    slide2 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Slide Hai</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    slide2_rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdNote"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"'
        ' Target="../notesSlides/notesSlide1.xml"/>'
        "</Relationships>"
    )
    notes1 = (
        '<?xml version="1.0"?>'
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Ghi chú <script>pwn()</script></a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
    )
    # Note: raw <script> cannot appear literally inside XML without escaping unless escaped;
    # test escaped XML entities representing literal `<script>pwn()</script>` text in notes.
    notes1 = notes1.replace("<script>pwn()</script>", "&lt;script&gt;pwn()&lt;/script&gt;")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", pres)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        zf.writestr("ppt/slides/slide1.xml", slide1)
        zf.writestr("ppt/slides/slide2.xml", slide2)
        zf.writestr("ppt/slides/_rels/slide2.xml.rels", slide2_rels)
        zf.writestr("ppt/notesSlides/notesSlide1.xml", notes1)

    converted = convert_document_bytes("deck.pptx", buf.getvalue(), "pptx")

    assert len(converted.slides) == 2
    assert converted.slides[0] == (1, "Slide Một <b>body</b>", "")
    assert converted.slides[1] == (2, "Slide Hai", "Ghi chú <script>pwn()</script>")
    assert "### Slide 1" in converted.markdown_text
    assert "### Slide 2" in converted.markdown_text
    assert "<script>pwn()</script>" not in converted.html_document
    assert "&lt;script&gt;pwn()&lt;/script&gt;" in converted.html_document
    assert "&lt;b&gt;body&lt;/b&gt;" in converted.html_document


def test_convert_pptx_respects_custom_presentation_order_and_table_text():
    pres = (
        '<?xml version="1.0"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<p:sldIdLst>"
        '<p:sldId id="257" r:id="rIdTwo"/>'
        '<p:sldId id="256" r:id="rIdOne"/>'
        "</p:sldIdLst></p:presentation>"
    )
    pres_rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdOne"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide1.xml"/>'
        '<Relationship Id="rIdTwo"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide2.xml"/>'
        "</Relationships>"
    )
    slide1 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Tiêu đề</a:t></a:r></a:p>"
        "</p:txBody></p:sp>"
        "<p:graphicFrame><a:graphic><a:graphicData><a:tbl><a:tr><a:tc>"
        "<a:txBody><a:p><a:r><a:t>Ô bảng</a:t></a:r></a:p></a:txBody>"
        "</a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
        "</p:spTree></p:cSld></p:sld>"
    )
    slide2 = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>Slide Hai Trước</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", pres)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        zf.writestr("ppt/slides/slide1.xml", slide1)
        zf.writestr("ppt/slides/slide2.xml", slide2)

    converted = convert_document_bytes("reordered.pptx", buf.getvalue(), "pptx")
    assert converted.slides == [
        (1, "Slide Hai Trước", ""),
        (2, "Tiêu đề\nÔ bảng", ""),
    ]


def test_convert_text_code_and_unsupported_escape_filename_and_content():
    text_bytes = "print('<script>alert(1)</script>')\n".encode("utf-8")
    converted = convert_document_bytes(
        "<script>bad</script>.py", text_bytes, "text", dark_mode=True
    )

    assert converted.kind == "text"
    assert converted.markdown_text == "print('<script>alert(1)</script>')\n"
    assert "<script>alert(1)</script>" not in converted.html_document
    assert "<script>bad</script>" not in converted.html_document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in converted.html_document

    unsupported = convert_document_bytes("mystery.bin", b"\x00\x01\x02", "unsupported")
    assert unsupported.kind == "unsupported"
    assert unsupported.html_document


def test_convert_csv_oversized_field_falls_back_gracefully_without_crashing():
    initial_limit = csv.field_size_limit()
    huge_cell = ("A" * 140_000).encode("utf-8")
    payload = b"col1,col2\n" + huge_cell + b",ok\n"

    converted = convert_document_bytes("oversized.csv", payload, "csv")

    assert converted.kind == "csv"
    assert "Không thể đọc nội dung tệp" in converted.html_document
    assert "Không thể đọc nội dung tệp" in converted.markdown_text
    assert csv.field_size_limit() == initial_limit


def test_convert_text_and_csv_truncate_across_multibyte_utf8_without_mojibake():
    phrase = "Tiếng Việt ".encode("utf-8")
    target_char = "ệ".encode("utf-8")  # 3 bytes: \xe1\xbb\x87
    text_limit = 2 * 1024 * 1024
    # Construct exact >2 MiB buffer where byte text_limit splits inside `target_char`
    base = phrase * 100
    filler_len = text_limit - len(base) - 2
    assert filler_len > 0
    for bom in (b"", b"\xef\xbb\xbf"):
        payload = bom + base + (b"x" * (filler_len - len(bom))) + target_char + b" tail"
        assert len(payload) > text_limit
        converted = convert_document_bytes("vietnamese.txt", payload, "text")
        assert "Tiếng Việt" in converted.markdown_text
        assert "Tiáº¿ng" not in converted.markdown_text
        assert "\ufffd" not in converted.markdown_text
        assert "rút gọn" in converted.markdown_text

    csv_limit = 4 * 1024 * 1024
    csv_header = "Tên,Ghi chú\nTiếng Việt,Hợp lệ\n".encode("utf-8")
    filler_line = b"row," + (b"y" * 1000) + b"\n"
    remaining = csv_limit - len(csv_header)
    repeat_count = (remaining - 10) // len(filler_line)
    middle = filler_line * repeat_count
    tail_pad = b"row," + (b"z" * (csv_limit - len(csv_header) - len(middle) - 4 - 2)) + target_char + b"\n"
    csv_payload = csv_header + middle + tail_pad
    assert len(csv_payload) > csv_limit
    csv_doc = convert_document_bytes("vietnamese.csv", csv_payload, "csv")
    assert csv_doc.sheets[0].rows[1] == ["Tiếng Việt", "Hợp lệ"]
    assert "Tiáº¿ng" not in csv_doc.html_document


def test_convert_xlsx_caps_cell_preview_render_budget_xml_budget_and_zip_entries():
    # 1. Cell truncation (>4096) + sharedStrings expansion bomb capped at <=4 MiB
    long_cell = "A" * 5000
    bomb_str = "B" * 3500
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Bomb" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    sst = (
        '<?xml version="1.0"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<si><t>{long_cell}</t></si>"
        f"<si><t>{bomb_str}</t></si>"
        "</sst>"
    )
    # 300 rows x 10 cols of 3500-char shared string = ~10.5 MB naive output
    bomb_row_cells = '<c t="s"><v>1</v></c>' * 10
    rows_xml = "".join(f'<row r="{r}">{bomb_row_cells}</row>' for r in range(2, 302))
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c></row>'
        f"{rows_xml}"
        "</sheetData></worksheet>"
    )
    bomb_buf = io.BytesIO()
    with zipfile.ZipFile(bomb_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", s1)

    bomb_doc = convert_document_bytes("bomb.xlsx", bomb_buf.getvalue(), "xlsx")
    assert "A" * 4097 not in bomb_doc.html_document
    assert "A" * 4097 not in bomb_doc.markdown_text
    assert "…" in bomb_doc.html_document
    assert "…" in bomb_doc.markdown_text
    assert len(bomb_doc.html_document) <= 4 * 1024 * 1024
    assert len(bomb_doc.markdown_text) <= 4 * 1024 * 1024
    assert "rút gọn" in bomb_doc.html_document
    assert "rút gọn" in bomb_doc.markdown_text

    # 2. Cumulative XML budget (>48 MiB across 4 sheets < 16 MiB each)
    wb_multi = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        + "".join(
            f'<sheet name="S{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1, 5)
        )
        + "</sheets></workbook>"
    )
    rels_multi = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{i}"'
            ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
            f' Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, 5)
        )
        + "</Relationships>"
    )
    chunk_13m = (
        b'<?xml version="1.0"?>'
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>'
        + (b"Z" * (13 * 1024 * 1024))
        + b"</t></is></c></row></sheetData></worksheet>"
    )
    xml_budget_buf = io.BytesIO()
    with zipfile.ZipFile(xml_budget_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/workbook.xml", wb_multi)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_multi)
        for i in range(1, 5):
            zf.writestr(f"xl/worksheets/sheet{i}.xml", chunk_13m)

    budget_doc = convert_document_bytes("xml_budget.xlsx", xml_budget_buf.getvalue(), "xlsx")
    assert "Không thể đọc nội dung tệp" in budget_doc.html_document

    # 3. ZIP entry count cap (>5000 entries)
    many_entries_buf = io.BytesIO()
    with zipfile.ZipFile(many_entries_buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
        for idx in range(5005):
            zf.writestr(f"dummy/f{idx}.txt", b"x")
    capped_zip_doc = convert_document_bytes("many.docx", many_entries_buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" in capped_zip_doc.html_document


def test_convert_xlsx_does_not_fallback_when_rels_exists_with_missing_or_external_rid():
    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        '<sheet name="MissingRel" sheetId="1" r:id="rIdMissing"/>'
        '<sheet name="ExternalRel" sheetId="2" r:id="rIdExternal"/>'
        '<sheet name="RealSheet" sheetId="3" r:id="rIdValid"/>'
        '<sheet name="NoRid" sheetId="4"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdExternal"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="https://evil.example/sheet.xml" TargetMode="External"/>'
        '<Relationship Id="rIdValid"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>RealOnly</t></is></c></row></sheetData>'
        "</worksheet>"
    )
    s2 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>ShouldNotLeak</t></is></c></row></sheetData>'
        "</worksheet>"
    )
    s4 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>NoRidFallback</t></is></c></row></sheetData>'
        "</worksheet>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels)
        zf.writestr("xl/worksheets/sheet1.xml", s1)
        zf.writestr("xl/worksheets/sheet2.xml", s2)
        zf.writestr("xl/worksheets/sheet4.xml", s4)

    converted = convert_document_bytes("rels_guard.xlsx", buf.getvalue(), "xlsx")
    assert [sheet.name for sheet in converted.sheets] == ["RealSheet", "NoRid"]
    assert "ShouldNotLeak" not in converted.html_document
    assert converted.sheets[0].rows == [["RealOnly"]]
    assert converted.sheets[1].rows == [["NoRidFallback"]]

    # Without workbook.xml.rels, fallback to sheet1.xml still works
    no_rels_buf = io.BytesIO()
    with zipfile.ZipFile(no_rels_buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/worksheets/sheet1.xml", s1)
    no_rels_doc = convert_document_bytes("norels.xlsx", no_rels_buf.getvalue(), "xlsx")
    assert [sheet.name for sheet in no_rels_doc.sheets] == ["MissingRel"]
    assert no_rels_doc.sheets[0].rows == [["RealOnly"]]


def test_convert_docx_rejects_disguised_bom_svg_and_validates_raster_magic_bytes():
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Media check</w:t></w:r></w:p></w:body>
    </w:document>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/media/evil_bom.png", b"\xef\xbb\xbf<svg onload='alert(1)'></svg>")
        zf.writestr("word/media/fake.jpg", b"NOT_A_JPEG_FILE")
        zf.writestr("word/media/real.gif", b"GIF89a\x01\x00")
        zf.writestr("word/media/real.webp", b"RIFF\x00\x00\x00\x00WEBP")
        zf.writestr("word/media/real.bmp", b"BM\x00\x00")

    converted = convert_document_bytes("media.docx", buf.getvalue(), "docx")
    assert "evil_bom.png" not in converted.html_document
    assert "fake.jpg" not in converted.html_document
    assert "data:image/gif;base64," in converted.html_document
    assert "data:image/webp;base64," in converted.html_document
    assert "data:image/bmp;base64," in converted.html_document


def test_convert_docx_and_xlsx_ignore_non_ascii_unicode_digits():
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:pPr><w:outlineLvl w:val="²"/></w:pPr><w:r><w:t>Superscript</w:t></w:r></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="①"/></w:pPr><w:r><w:t>Circled</w:t></w:r></w:p>
      </w:body>
    </w:document>"""
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    docx_res = convert_document_bytes("unicode_digits.docx", docx_buf.getvalue(), "docx")
    assert docx_res.outline == []
    assert "Superscript" in docx_res.markdown_text
    assert "Circled" in docx_res.markdown_text

    wb = (
        '<?xml version="1.0"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="S1" sheetId="1"/></sheets></workbook>'
    )
    sst = (
        '<?xml version="1.0"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Zero</t></si></sst>'
    )
    s1 = (
        '<?xml version="1.0"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData><row r="1"><c r="A1" t="s"><v>²</v></c><c r="B1" t="s"><v>0</v></c></row></sheetData>'
        "</worksheet>"
    )
    xlsx_buf = io.BytesIO()
    with zipfile.ZipFile(xlsx_buf, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/sharedStrings.xml", sst)
        zf.writestr("xl/worksheets/sheet1.xml", s1)
    xlsx_res = convert_document_bytes("unicode_digits.xlsx", xlsx_buf.getvalue(), "xlsx")
    assert xlsx_res.sheets[0].rows == [["", "Zero"]]


def test_convert_xml_allows_doctype_inside_comments_and_cdata_while_blocking_real_dtd():
    safe_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <!-- <!DOCTYPE harmless-comment> -->
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t><![CDATA[<!DOCTYPE literal-text> <!ENTITY x "y">]]></w:t></w:r></w:p>
      </w:body>
    </w:document>"""
    safe_buf = io.BytesIO()
    with zipfile.ZipFile(safe_buf, "w") as zf:
        zf.writestr("word/document.xml", safe_xml)
    safe_doc = convert_document_bytes("comment_cdata.docx", safe_buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" not in safe_doc.html_document
    assert "&lt;!DOCTYPE literal-text&gt;" in safe_doc.html_document

    ext_dtd_xml = b"""<?xml version="1.0"?>
    <!DOCTYPE foo SYSTEM "http://evil.example/evil.dtd">
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>"""
    ext_buf = io.BytesIO()
    with zipfile.ZipFile(ext_buf, "w") as zf:
        zf.writestr("word/document.xml", ext_dtd_xml)
    ext_doc = convert_document_bytes("ext_dtd.docx", ext_buf.getvalue(), "docx")
    assert "Không thể đọc nội dung tệp" in ext_doc.html_document


def test_convert_docx_vml_imagedata_del_instrtext_sdt_nested_table_and_table_css_wrap():
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                xmlns:v="urn:schemas-microsoft-com:vml">
      <w:body>
        <w:sdt><w:sdtContent>
          <w:p>
            <w:r><w:t>Giữ lại</w:t></w:r>
            <w:del><w:r><w:delText>Đã xóa</w:delText></w:r></w:del>
            <w:r><w:instrText> PAGE </w:instrText></w:r>
          </w:p>
        </w:sdtContent></w:sdt>
        <w:p><w:r><w:pict><v:shape><v:imagedata r:id="rIdVml"/></v:shape></w:pict></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc>
            <w:p><w:r><w:t>Ngoài</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Trong</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
          </w:tc></w:tr>
        </w:tbl>
      </w:body>
    </w:document>"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rIdVml"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
        Target="media/vml.png"/>
    </Relationships>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/_rels/document.xml.rels", rels_xml)
        zf.writestr("word/media/vml.png", b"\x89PNG\r\n\x1a\nVMLPNG")

    converted = convert_document_bytes("rich.docx", buf.getvalue(), "docx")
    assert "Giữ lại" in converted.markdown_text
    assert "Đã xóa" not in converted.markdown_text
    assert "PAGE" not in converted.markdown_text
    assert "data:image/png;base64," in converted.html_document
    assert "Ngoài" in converted.html_document
    assert "Trong" in converted.html_document
    assert '<div class="table-wrap"><table>' in converted.html_document
    assert '<table class="sheet-table">' not in converted.html_document
    assert ".table-wrap.sheet .sheet-table" in converted.html_document
    assert "white-space:pre-wrap;word-break:break-word;" in converted.html_document

    csv_doc = convert_document_bytes("grid.csv", b"A,B\n1,2\n", "csv")
    assert '<div class="table-wrap sheet"><table class="sheet-table">' in csv_doc.html_document


def test_convert_docx_multiline_heading_and_pptx_multiline_notes_and_rels_semantics():
    doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p>
          <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
          <w:r><w:t>Chương 1</w:t><w:br/><w:t>Mở đầu</w:t></w:r>
        </w:p>
      </w:body>
    </w:document>"""
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr("word/document.xml", doc_xml)
    docx_res = convert_document_bytes("heading_br.docx", docx_buf.getvalue(), "docx")
    assert "# Chương 1 Mở đầu" in docx_res.markdown_text

    # PPTX:
    # - presentation order: slide2.xml first (idx=1, NO .rels -> tries notesSlide2.xml BEFORE notesSlide1.xml)
    # - slide1.xml second (idx=2, HAS slide1.xml.rels WITHOUT notesSlide relationship -> MUST NOT pick up notesSlide1.xml)
    pres = (
        '<?xml version="1.0"?>'
        '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<p:sldIdLst>"
        '<p:sldId id="257" r:id="rIdTwo"/>'
        '<p:sldId id="256" r:id="rIdOne"/>'
        "</p:sldIdLst></p:presentation>"
    )
    pres_rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdOne"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide1.xml"/>'
        '<Relationship Id="rIdTwo"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"'
        ' Target="slides/slide2.xml"/>'
        "</Relationships>"
    )
    slide_tpl = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        "<a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    slide1_rels_no_notes = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdLayout"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"'
        ' Target="../slideLayouts/slideLayout1.xml"/>'
        "</Relationships>"
    )
    notes_tpl = (
        '<?xml version="1.0"?>'
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>{paras}</p:txBody></p:sp></p:spTree></p:cSld></p:notes>"
    )
    notes1 = notes_tpl.format(paras="<a:p><a:r><a:t>Orphan note 1</a:t></a:r></a:p>")
    notes2 = notes_tpl.format(
        paras="<a:p><a:r><a:t>Dòng 1</a:t></a:r></a:p><a:p><a:r><a:t>Dòng 2</a:t></a:r></a:p>"
    )
    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        zf.writestr("ppt/presentation.xml", pres)
        zf.writestr("ppt/_rels/presentation.xml.rels", pres_rels)
        zf.writestr("ppt/slides/slide1.xml", slide_tpl.format(title="Slide One"))
        zf.writestr("ppt/slides/slide2.xml", slide_tpl.format(title="Slide Two"))
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", slide1_rels_no_notes)
        zf.writestr("ppt/notesSlides/notesSlide1.xml", notes1)
        zf.writestr("ppt/notesSlides/notesSlide2.xml", notes2)

    pptx_res = convert_document_bytes("notes_order.pptx", pptx_buf.getvalue(), "pptx")
    assert pptx_res.slides == [
        (1, "Slide Two", "Dòng 1\nDòng 2"),
        (2, "Slide One", ""),
    ]
    assert "> **Ghi chú:** Dòng 1\n> Dòng 2" in pptx_res.markdown_text
    assert "Orphan note 1" not in pptx_res.markdown_text


def test_convert_pptx_render_budget_and_cumulative_xml_budget():
    # 1. Render budget across many slides (>4 MiB rendered)
    big_text = "M" * 120_000
    slide_xml = (
        '<?xml version="1.0"?>'
        '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        ' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<p:cSld><p:spTree><p:sp><p:txBody>"
        f"<a:p><a:r><a:t>{big_text}</a:t></a:r></a:p>"
        "</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    render_buf = io.BytesIO()
    with zipfile.ZipFile(render_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx in range(1, 51):
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)

    render_doc = convert_document_bytes("huge_deck.pptx", render_buf.getvalue(), "pptx")
    assert len(render_doc.html_document) <= 4 * 1024 * 1024
    assert len(render_doc.markdown_text) <= 4 * 1024 * 1024
    assert "rút gọn" in render_doc.html_document
    assert "rút gọn" in render_doc.markdown_text

    # 2. Cumulative XML budget (>48 MiB across 4 slides of ~13 MiB XML each, short text)
    slide_13m = (
        b'<?xml version="1.0"?>'
        b'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
        b' xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        b"<!-- "
        + (b"P" * (13 * 1024 * 1024))
        + b" --><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>OK</a:t></a:r></a:p>"
        b"</p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
    )
    xml_buf = io.BytesIO()
    with zipfile.ZipFile(xml_buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx in range(1, 5):
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_13m)

    xml_doc = convert_document_bytes("xml_bomb.pptx", xml_buf.getvalue(), "pptx")
    assert "Không thể đọc nội dung tệp" in xml_doc.html_document
