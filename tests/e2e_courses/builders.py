"""Zero-external-dep pure-Python synthetic document/archive builders and service stubs."""

from __future__ import annotations

import html
import io
from pathlib import Path
import struct
import tarfile
from typing import Any, Optional, Sequence
import zipfile
import zlib

from core.courses.activity_service import (
    AttendanceRecord,
    AttendanceSummary,
    BookChapterEntry,
    CourseGroupEntry,
    ForumActivitySummary,
    ForumDiscussionEntry,
    ForumPostEntry,
    GroupActivitySummary,
    QuizActivitySummary,
    QuizAttemptInfo,
    QuizAttemptReviewReport,
    QuizReviewChoice,
    QuizReviewQuestion,
    RichActivityPage,
)
from core.courses.cache_store import CourseDetailSnapshot
from core.courses.catalog_service import CourseSyncResult
from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSource,
    CourseSummary,
    OpenStrategy,
    PreviewKind,
    RecentMaterialEntry,
)


def tiny_png_bytes() -> bytes:
    """Return a valid 1x1 RGB PNG payload without requiring Pillow."""

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\x20\x80\xff")
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _col_letters(idx_1_based: int) -> str:
    chars: list[str] = []
    n = idx_1_based
    while n > 0:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(ord("A") + rem))
    return "".join(reversed(chars))


def build_docx_bytes(
    *,
    paragraphs: Sequence[str] = ("Đoạn văn bản mẫu trong tài liệu Word.",),
    headings: Sequence[tuple[int, str] | str] = (),
    tables: Sequence[Sequence[Sequence[str]]] = (),
    omml_equations: Sequence[str] = (),
    include_image: bool = False,
    include_header_footer: bool = False,
    page_break_between: bool = False,
) -> bytes:
    """Build a valid OOXML .docx ZIP archive with optional headings, tables, OMML, images, headers/footers."""
    body_parts: list[str] = []

    def _maybe_break() -> None:
        if page_break_between and body_parts:
            body_parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    for h in headings:
        if isinstance(h, tuple):
            lvl, text = int(h[0]), str(h[1])
        else:
            lvl, text = 1, str(h)
        lvl = max(1, min(6, lvl))
        _maybe_break()
        body_parts.append(
            f'<w:p><w:pPr><w:pStyle w:val="Heading{lvl}"/><w:outlineLvl w:val="{lvl - 1}"/></w:pPr>'
            f"<w:r><w:t>{html.escape(text)}</w:t></w:r></w:p>"
        )

    for para in paragraphs:
        _maybe_break()
        body_parts.append(
            f"<w:p><w:r><w:t>{html.escape(str(para))}</w:t></w:r></w:p>"
        )

    for eq in omml_equations:
        _maybe_break()
        body_parts.append(
            "<w:p><m:oMathPara><m:oMath>"
            f"<m:r><m:t>{html.escape(str(eq))}</m:t></m:r>"
            "</m:oMath></m:oMathPara></w:p>"
        )

    for tbl in tables:
        _maybe_break()
        tr_xml: list[str] = []
        for r_idx, row in enumerate(tbl):
            tc_xml: list[str] = []
            for cell in row:
                shd = '<w:shd w:val="clear" w:color="auto" w:fill="E2E8F0"/>' if r_idx == 0 else ""
                tc_xml.append(
                    f"<w:tc><w:tcPr>{shd}</w:tcPr>"
                    f"<w:p><w:r><w:t>{html.escape(str(cell))}</w:t></w:r></w:p></w:tc>"
                )
            tr_xml.append(f"<w:tr>{''.join(tc_xml)}</w:tr>")
        body_parts.append(
            "<w:tbl><w:tblPr><w:tblBorders>"
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="94A3B8"/>'
            '<w:left w:val="single" w:sz="4" w:space="0" w:color="94A3B8"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="94A3B8"/>'
            '<w:right w:val="single" w:sz="4" w:space="0" w:color="94A3B8"/>'
            "</w:tblBorders></w:tblPr>"
            + "".join(tr_xml)
            + "</w:tbl>"
        )

    rels: list[str] = [
        '<Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    ]
    ct_overrides: list[str] = [
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>',
    ]

    if include_image:
        rels.append(
            '<Relationship Id="rIdImg1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="media/img1.png"/>'
        )
        body_parts.append(
            "<w:p><w:r><w:drawing>"
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            '<wp:extent cx="914400" cy="914400"/>'
            '<wp:docPr id="1" name="Picture 1"/>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:nvPicPr><pic:cNvPr id="1" name="img1.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="rIdImg1"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="914400" cy="914400"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
        )

    sect_refs = ""
    if include_header_footer:
        rels.append(
            '<Relationship Id="rIdHdr1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
            'Target="header1.xml"/>'
        )
        rels.append(
            '<Relationship Id="rIdFtr1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
            'Target="footer1.xml"/>'
        )
        ct_overrides.append(
            '<Override PartName="/word/header1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
        )
        ct_overrides.append(
            '<Override PartName="/word/footer1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
        )
        sect_refs = (
            '<w:headerReference w:type="default" r:id="rIdHdr1"/>'
            '<w:footerReference w:type="default" r:id="rIdFtr1"/>'
        )

    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
        f"<w:body>{''.join(body_parts)}"
        f'<w:sectPr>{sect_refs}<w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
        "</w:body></w:document>"
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>'
        '<w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>'
        '<w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/>'
        '<w:pPr><w:outlineLvl w:val="2"/></w:pPr></w:style>'
        "</w:styles>"
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        f"{''.join(ct_overrides)}</Types>"
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )

    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(rels)}</Relationships>"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/document.xml", doc_xml)
        zf.writestr("word/styles.xml", styles_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
        if include_image:
            zf.writestr("word/media/img1.png", tiny_png_bytes())
        if include_header_footer:
            zf.writestr(
                "word/header1.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:p><w:r><w:t>UTH Lecture Header</w:t></w:r></w:p></w:hdr>",
            )
            zf.writestr(
                "word/footer1.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:p><w:r><w:t>Trang 1</w:t></w:r></w:p></w:ftr>",
            )
    return buf.getvalue()


def build_pptx_bytes(
    *,
    slides: Sequence[tuple[str, str] | str] = (("Bài giảng mẫu", "Nội dung slide đầu tiên"),),
    aspect: str = "16:9",
    include_notes: bool = False,
    include_table: bool = False,
    include_chart: bool = False,
    include_shape: bool = True,
    include_image: bool = False,
) -> bytes:
    """Build a valid OOXML .pptx ZIP archive with master/layout/theme, shapes, tables, charts, images, and notes."""
    aspect_norm = str(aspect).strip()
    if aspect_norm == "4:3":
        cx, cy = 9144000, 6858000
    elif aspect_norm == "16:10":
        cx, cy = 10972800, 6858000
    else:
        cx, cy = 12192000, 6858000

    slide_list = list(slides) or [("Slide 1", "Nội dung")]
    sld_id_entries: list[str] = []
    pres_rels: list[str] = [
        '<Relationship Id="rIdMaster1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
        'Target="slideMasters/slideMaster1.xml"/>',
        '<Relationship Id="rIdTheme1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" '
        'Target="theme/theme1.xml"/>',
    ]
    ct_overrides: list[str] = [
        '<Override PartName="/ppt/presentation.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, item in enumerate(slide_list, start=1):
            if isinstance(item, tuple):
                s_title, s_body = str(item[0]), str(item[1])
            else:
                s_title, s_body = f"Slide {idx}", str(item)

            rid = f"rIdSlide{idx}"
            sld_id_entries.append(f'<p:sldId id="{255 + idx}" r:id="{rid}"/>')
            pres_rels.append(
                f'<Relationship Id="{rid}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" '
                f'Target="slides/slide{idx}.xml"/>'
            )
            ct_overrides.append(
                f'<Override PartName="/ppt/slides/slide{idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
            )

            sp_tree_items: list[str] = [
                '<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/>'
                '<p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="457200" y="274320"/><a:ext cx="{cx - 914400}" cy="914400"/></a:xfrm></p:spPr>'
                f"<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{html.escape(s_title)}</a:t></a:r></a:p></p:txBody></p:sp>",
                '<p:sp><p:nvSpPr><p:cNvPr id="3" name="Content 2"/><p:cNvSpPr/>'
                '<p:nvPr><p:ph idx="1"/></p:nvPr></p:nvSpPr>'
                f'<p:spPr><a:xfrm><a:off x="457200" y="1371600"/><a:ext cx="{cx - 914400}" cy="2286000"/></a:xfrm></p:spPr>'
                f"<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{html.escape(s_body)}</a:t></a:r></a:p></p:txBody></p:sp>",
            ]

            if include_shape:
                sp_tree_items.append(
                    '<p:sp><p:nvSpPr><p:cNvPr id="4" name="Accent Shape"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
                    '<p:spPr><a:xfrm><a:off x="457200" y="3800000"/><a:ext cx="2743200" cy="685800"/></a:xfrm>'
                    '<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
                    '<a:solidFill><a:srgbClr val="1E3A8A"/></a:solidFill></p:spPr>'
                    "<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Shape Callout</a:t></a:r></a:p></p:txBody></p:sp>"
                )

            if include_table and idx == 1:
                sp_tree_items.append(
                    '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="5" name="Table 1"/>'
                    "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
                    '<p:xfrm><a:off x="457200" y="4600000"/><a:ext cx="4572000" cy="1143000"/></p:xfrm>'
                    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
                    '<a:tbl><a:tblGrid><a:gridCol w="2286000"/><a:gridCol w="2286000"/></a:tblGrid>'
                    '<a:tr h="370840"><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Cột 1</a:t></a:r></a:p></a:txBody></a:tc>'
                    '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Cột 2</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
                    '<a:tr h="370840"><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Dữ liệu A</a:t></a:r></a:p></a:txBody></a:tc>'
                    '<a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Dữ liệu B</a:t></a:r></a:p></a:txBody></a:tc></a:tr>'
                    "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
                )

            slide_rels: list[str] = [
                '<Relationship Id="rIdLayout1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
                'Target="../slideLayouts/slideLayout1.xml"/>'
            ]

            if include_chart and idx == 1:
                slide_rels.append(
                    '<Relationship Id="rIdChart1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
                    'Target="../charts/chart1.xml"/>'
                )
                ct_overrides.append(
                    '<Override PartName="/ppt/charts/chart1.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
                )
                sp_tree_items.append(
                    '<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="6" name="Chart 1"/>'
                    "<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>"
                    '<p:xfrm><a:off x="5486400" y="3800000"/><a:ext cx="4572000" cy="2286000"/></p:xfrm>'
                    '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
                    '<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rIdChart1"/>'
                    "</a:graphicData></a:graphic></p:graphicFrame>"
                )
                chart_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<c:chart><c:plotArea><c:barChart><c:barDir val=\"col\"/><c:ser><c:idx val=\"0\"/><c:order val=\"0\"/>"
                    "<c:tx><c:v>Điểm TB</c:v></c:tx>"
                    "<c:cat><c:strRef><c:strCache><c:ptCount val=\"2\"/><c:pt idx=\"0\"><c:v>HK1</c:v></c:pt>"
                    "<c:pt idx=\"1\"><c:v>HK2</c:v></c:pt></c:strCache></c:strRef></c:cat>"
                    "<c:val><c:numRef><c:numCache><c:ptCount val=\"2\"/><c:pt idx=\"0\"><c:v>8.5</c:v></c:pt>"
                    "<c:pt idx=\"1\"><c:v>9.1</c:v></c:pt></c:numCache></c:numRef></c:val>"
                    "</c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"
                )
                zf.writestr("ppt/charts/chart1.xml", chart_xml)

            if include_image and idx == 1:
                slide_rels.append(
                    '<Relationship Id="rIdImg1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                    'Target="../media/img1.png"/>'
                )
                sp_tree_items.append(
                    '<p:pic><p:nvPicPr><p:cNvPr id="7" name="Image 1"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
                    '<p:blipFill><a:blip r:embed="rIdImg1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
                    '<p:spPr><a:xfrm><a:off x="5486400" y="1371600"/><a:ext cx="1828800" cy="1828800"/></a:xfrm>'
                    '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'
                )
                zf.writestr("ppt/media/img1.png", tiny_png_bytes())

            if include_notes:
                slide_rels.append(
                    '<Relationship Id="rIdNote1" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" '
                    f'Target="../notesSlides/notesSlide{idx}.xml"/>'
                )
                ct_overrides.append(
                    f'<Override PartName="/ppt/notesSlides/notesSlide{idx}.xml" '
                    'ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>'
                )
                notes_xml = (
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
                    "<p:cSld><p:spTree>"
                    '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
                    "<p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Notes\"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/>"
                    f"<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Ghi chú giảng viên slide {idx}</a:t></a:r></a:p></p:txBody>"
                    "</p:sp></p:spTree></p:cSld></p:notes>"
                )
                zf.writestr(f"ppt/notesSlides/notesSlide{idx}.xml", notes_xml)

            slide_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                "<p:cSld><p:spTree>"
                '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
                "<p:grpSpPr><a:xfrm/></p:grpSpPr>"
                f"{''.join(sp_tree_items)}"
                "</p:spTree></p:cSld></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{idx}.xml", slide_xml)
            zf.writestr(
                f"ppt/slides/_rels/slide{idx}.xml.rels",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f"{''.join(slide_rels)}</Relationships>",
            )

        presentation_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rIdMaster1"/></p:sldMasterIdLst>'
            f"<p:sldIdLst>{''.join(sld_id_entries)}</p:sldIdLst>"
            f'<p:sldSz cx="{cx}" cy="{cy}" type="screen16x9"/>'
            '<p:notesSz cx="6858000" cy="9144000"/>'
            "</p:presentation>"
        )
        slide_master_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            "<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val=\"FFFFFF\"/></a:solidFill></p:bgPr></p:bg>"
            "<p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree>"
            "</p:cSld>"
            '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" '
            'accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rIdLayout1"/></p:sldLayoutIdLst>'
            "</p:sldMaster>"
        )
        slide_layout_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" type="titleAndObj">'
            "<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld>"
            "</p:sldLayout>"
        )
        theme_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="UTH Theme">'
            "<a:themeElements><a:clrScheme name=\"UTH\">"
            '<a:dk1><a:srgbClr val="0F172A"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
            '<a:dk2><a:srgbClr val="1E293B"/></a:dk2><a:lt2><a:srgbClr val="F1F5F9"/></a:lt2>'
            '<a:accent1><a:srgbClr val="2563EB"/></a:accent1><a:accent2><a:srgbClr val="0EA5E9"/></a:accent2>'
            '<a:accent3><a:srgbClr val="10B981"/></a:accent3><a:accent4><a:srgbClr val="F59E0B"/></a:accent4>'
            '<a:accent5><a:srgbClr val="EF4444"/></a:accent5><a:accent6><a:srgbClr val="8B5CF6"/></a:accent6>'
            '<a:hlink><a:srgbClr val="2563EB"/></a:hlink><a:folHlink><a:srgbClr val="7C3AED"/></a:folHlink>'
            "</a:clrScheme>"
            '<a:fontScheme name="UTH"><a:majorFont><a:latin typeface="Calibri"/></a:majorFont>'
            '<a:minorFont><a:latin typeface="Calibri"/></a:minorFont></a:fontScheme>'
            "<a:fmtScheme name=\"UTH\"><a:fillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:fillStyleLst>"
            "<a:lnStyleLst><a:ln w=\"9525\"><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:ln></a:lnStyleLst>"
            "<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>"
            "<a:bgFillStyleLst><a:solidFill><a:schemeClr val=\"phClr\"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>"
            "</a:themeElements></a:theme>"
        )

        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="png" ContentType="image/png"/>'
            f"{''.join(ct_overrides)}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="ppt/presentation.xml"/></Relationships>',
        )
        zf.writestr("ppt/presentation.xml", presentation_xml)
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(pres_rels)}</Relationships>",
        )
        zf.writestr("ppt/slideMasters/slideMaster1.xml", slide_master_xml)
        zf.writestr(
            "ppt/slideMasters/_rels/slideMaster1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdLayout1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" '
            'Target="../slideLayouts/slideLayout1.xml"/>'
            '<Relationship Id="rIdTheme1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" '
            'Target="../theme/theme1.xml"/></Relationships>',
        )
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", slide_layout_xml)
        zf.writestr(
            "ppt/slideLayouts/_rels/slideLayout1.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdMaster1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" '
            'Target="../slideMasters/slideMaster1.xml"/></Relationships>',
        )
        zf.writestr("ppt/theme/theme1.xml", theme_xml)

    return buf.getvalue()


def build_xlsx_bytes(sheets: dict[str, list[list[str]]]) -> bytes:
    """Build a valid OOXML .xlsx ZIP archive from a mapping of sheet name to string rows."""
    sheet_items = list(sheets.items()) or [("Sheet1", [["Header"], ["Value"]])]
    shared_strings: list[str] = []
    shared_index: dict[str, int] = {}

    def _intern(text: str) -> int:
        if text not in shared_index:
            shared_index[text] = len(shared_strings)
            shared_strings.append(text)
        return shared_index[text]

    wb_sheets: list[str] = []
    wb_rels: list[str] = []
    ct_overrides: list[str] = [
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>',
    ]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for s_idx, (sheet_name, rows) in enumerate(sheet_items, start=1):
            rid = f"rId{s_idx}"
            wb_sheets.append(
                f'<sheet name="{html.escape(sheet_name)}" sheetId="{s_idx}" r:id="{rid}"/>'
            )
            wb_rels.append(
                f'<Relationship Id="{rid}" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                f'Target="worksheets/sheet{s_idx}.xml"/>'
            )
            ct_overrides.append(
                f'<Override PartName="/xl/worksheets/sheet{s_idx}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            )
            row_xml: list[str] = []
            for r_idx, row in enumerate(rows, start=1):
                cell_xml: list[str] = []
                for c_idx, val in enumerate(row, start=1):
                    ref = f"{_col_letters(c_idx)}{r_idx}"
                    sst_i = _intern(str(val))
                    cell_xml.append(f'<c r="{ref}" t="s"><v>{sst_i}</v></c>')
                row_xml.append(f'<row r="{r_idx}">{"".join(cell_xml)}</row>')
            zf.writestr(
                f"xl/worksheets/sheet{s_idx}.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<sheetData>{''.join(row_xml)}</sheetData></worksheet>",
            )

        sst_items = "".join(
            f"<si><t>{html.escape(s)}</t></si>" for s in shared_strings
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(shared_strings)}" uniqueCount="{len(shared_strings)}">'
            f"{sst_items}</sst>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f"<sheets>{''.join(wb_sheets)}</sheets></workbook>",
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f"{''.join(wb_rels)}"
            '<Relationship Id="rIdSst" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
            'Target="sharedStrings.xml"/></Relationships>',
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f"{''.join(ct_overrides)}</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )

    return buf.getvalue()


def _pdf_escape(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return safe.encode("latin-1", errors="replace")


def _pdf_text_string_bytes(text: str) -> bytes:
    """Encode a PDF Text String (ASCII or UTF-16BE with BOM) for /Outlines /Title."""
    if text.isascii():
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return safe.encode("ascii")
    raw = b"\xfe\xff" + text.encode("utf-16-be")
    return (
        raw.replace(b"\\", b"\\\\")
        .replace(b"(", b"\\(")
        .replace(b")", b"\\)")
        .replace(b"\r", b"\\r")
        .replace(b"\n", b"\\n")
    )


def build_pdf_bytes(
    page_texts: list[str],
    *,
    outline_titles: Optional[list[tuple[str, int]]] = None,
) -> bytes:
    """Build a valid multi-page PDF 1.4 byte payload with optional outline bookmarks."""
    objects: list[bytes] = []

    def _add_obj(payload: bytes) -> int:
        objects.append(payload)
        return len(objects)

    catalog_id = _add_obj(b"")
    pages_id = _add_obj(b"")
    font_id = _add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids: list[int] = []

    for text in page_texts or [""]:
        stream_data = b"BT /F1 14 Tf 50 750 Td (" + _pdf_escape(text) + b") Tj ET"
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

    outlines_ref = ""
    if outline_titles:
        valid_items = [
            (title, idx)
            for title, idx in outline_titles
            if 0 <= idx < len(page_ids) and str(title).strip()
        ]
        if valid_items:
            outlines_id = _add_obj(b"")
            item_ids = [_add_obj(b"") for _ in valid_items]
            objects[outlines_id - 1] = (
                f"<< /Type /Outlines /First {item_ids[0]} 0 R /Last {item_ids[-1]} 0 R /Count {len(item_ids)} >>".encode(
                    "ascii"
                )
            )
            for i, (title, page_idx) in enumerate(valid_items):
                prev_part = f" /Prev {item_ids[i - 1]} 0 R" if i > 0 else ""
                next_part = f" /Next {item_ids[i + 1]} 0 R" if i + 1 < len(item_ids) else ""
                title_bytes = _pdf_text_string_bytes(str(title))
                objects[item_ids[i] - 1] = (
                    b"<< /Title ("
                    + title_bytes
                    + f") /Parent {outlines_id} 0 R{prev_part}{next_part} /Dest [{page_ids[page_idx]} 0 R /XYZ 0 842 null] >>".encode(
                        "ascii"
                    )
                )
            outlines_ref = f" /Outlines {outlines_id} 0 R"

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[catalog_id - 1] = (
        f"<< /Type /Catalog /Pages {pages_id} 0 R{outlines_ref} >>".encode("ascii")
    )
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


def build_ole2_bytes(streams: dict[str, bytes]) -> bytes:
    """Build a valid 512-byte-sector OLE2 Compound File Binary container (with MiniFAT) for .doc/.ppt tests."""
    sector_size = 512
    mini_sector_size = 64
    mini_cutoff = 4096
    entries_per_fat = sector_size // 4
    data_sectors: list[bytes] = []
    mini_sectors: list[bytes] = []
    dir_entries: list[bytes] = []

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

    # (name, start_sector_or_minisector, size, is_mini)
    stream_starts: list[tuple[str, int, int, bool]] = []
    for s_name, s_bytes in streams.items():
        if not s_bytes:
            stream_starts.append((s_name, 0xFFFFFFFE, 0, False))
            continue
        if len(s_bytes) < mini_cutoff:
            mini_start = len(mini_sectors)
            padded_len = ((len(s_bytes) + mini_sector_size - 1) // mini_sector_size) * mini_sector_size
            padded = s_bytes.ljust(padded_len, b"\x00")
            for off in range(0, padded_len, mini_sector_size):
                mini_sectors.append(padded[off : off + mini_sector_size])
            stream_starts.append((s_name, mini_start, len(s_bytes), True))
        else:
            start_sec = len(data_sectors)
            padded_len = ((len(s_bytes) + sector_size - 1) // sector_size) * sector_size
            padded = s_bytes.ljust(padded_len, b"\x00")
            for off in range(0, padded_len, sector_size):
                data_sectors.append(padded[off : off + sector_size])
            stream_starts.append((s_name, start_sec, len(s_bytes), False))

    root_start_sec = 0xFFFFFFFE
    root_size = 0
    root_sector_count = 0
    minifat_start_sec = 0xFFFFFFFE
    minifat_sector_count = 0

    if mini_sectors:
        mini_blob = b"".join(mini_sectors)
        root_size = len(mini_blob)
        root_start_sec = len(data_sectors)
        padded_root_len = ((root_size + sector_size - 1) // sector_size) * sector_size
        padded_root = mini_blob.ljust(padded_root_len, b"\x00")
        root_sector_count = padded_root_len // sector_size
        for off in range(0, padded_root_len, sector_size):
            data_sectors.append(padded_root[off : off + sector_size])

        minifat_sector_count = (len(mini_sectors) + entries_per_fat - 1) // entries_per_fat
        minifat: list[int] = [0xFFFFFFFF] * (minifat_sector_count * entries_per_fat)
        for _s_name, s_start, s_size, is_mini in stream_starts:
            if not is_mini or s_size == 0:
                continue
            mc = (s_size + mini_sector_size - 1) // mini_sector_size
            for k in range(mc):
                minifat[s_start + k] = (s_start + k + 1) if k + 1 < mc else 0xFFFFFFFE
        minifat_start_sec = len(data_sectors)
        for k in range(minifat_sector_count):
            chunk = minifat[k * entries_per_fat : (k + 1) * entries_per_fat]
            data_sectors.append(struct.pack(f"<{entries_per_fat}I", *chunk))

    dir_sec = len(data_sectors)
    dir_entries.append(
        _pack_dir_entry(
            "Root Entry",
            5,
            1 if stream_starts else 0xFFFFFFFF,
            0xFFFFFFFF,
            0xFFFFFFFF,
            root_start_sec,
            root_size,
        )
    )
    for i, (s_name, s_start, s_size, _is_mini) in enumerate(stream_starts, start=1):
        right = i + 1 if i < len(stream_starts) else 0xFFFFFFFF
        dir_entries.append(
            _pack_dir_entry(s_name, 2, 0xFFFFFFFF, 0xFFFFFFFF, right, s_start, s_size)
        )
    dir_blob = b"".join(dir_entries)
    dir_padded_len = ((len(dir_blob) + sector_size - 1) // sector_size) * sector_size
    dir_blob = dir_blob.ljust(dir_padded_len, b"\x00")
    dir_sector_count = len(dir_blob) // sector_size
    for off in range(0, len(dir_blob), sector_size):
        data_sectors.append(dir_blob[off : off + sector_size])

    fat_sec_start = len(data_sectors)
    # Determine number of FAT sectors needed
    fat_count = 1
    while (fat_sec_start + fat_count) > fat_count * entries_per_fat:
        fat_count += 1

    fat: list[int] = [0xFFFFFFFF] * (fat_count * entries_per_fat)
    for _s_name, s_start, s_size, is_mini in stream_starts:
        if is_mini or s_size == 0:
            continue
        sec_count = (s_size + sector_size - 1) // sector_size
        for k in range(sec_count):
            fat[s_start + k] = (s_start + k + 1) if k + 1 < sec_count else 0xFFFFFFFE
    for k in range(root_sector_count):
        fat[root_start_sec + k] = (
            (root_start_sec + k + 1) if k + 1 < root_sector_count else 0xFFFFFFFE
        )
    for k in range(minifat_sector_count):
        fat[minifat_start_sec + k] = (
            (minifat_start_sec + k + 1) if k + 1 < minifat_sector_count else 0xFFFFFFFE
        )
    for k in range(dir_sector_count):
        fat[dir_sec + k] = (dir_sec + k + 1) if k + 1 < dir_sector_count else 0xFFFFFFFE
    for k in range(fat_count):
        fat[fat_sec_start + k] = 0xFFFFFFFD  # FATSECT

    for k in range(fat_count):
        chunk = fat[k * entries_per_fat : (k + 1) * entries_per_fat]
        data_sectors.append(struct.pack(f"<{entries_per_fat}I", *chunk))

    header = bytearray(512)
    header[:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHHH", header, 24, 0x003E, 0x0003, 0xFFFE, 9, 6, 0)
    struct.pack_into(
        "<IIIIIIII",
        header,
        44,
        fat_count,
        dir_sec,
        0,
        mini_cutoff,
        minifat_start_sec,
        minifat_sector_count,
        0xFFFFFFFE,
        0,
    )
    difat = [0xFFFFFFFF] * 109
    for k in range(min(fat_count, 109)):
        difat[k] = fat_sec_start + k
    struct.pack_into("<109I", header, 76, *difat)
    return bytes(header) + b"".join(data_sectors)


def build_zip_bytes(entries: dict[str, bytes]) -> bytes:
    """Create a compressed ZIP archive in memory from path -> bytes entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def build_targz_bytes(entries: dict[str, bytes]) -> bytes:
    """Create a gzipped tar archive (.tar.gz) in memory from path -> bytes entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, payload in entries.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Course Model Factories
# ---------------------------------------------------------------------------


def make_course(
    course_id: int = 101,
    *,
    source: CourseSource = "courses",
    display_name: str = "Lập trình Python",
    course_code: str = "010112345601",
    semester_label: str = "HKII 2025-2026",
    semester_sort_key: tuple[int, int] = (2025, 2),
    program_tag: str = "CLC",
    teacher_name: str = "TS. Nguyễn Văn A",
    teacher_email: str = "nva@ut.edu.vn",
    teacher_phone: str = "",
    meet_url: str = "https://meet.google.com/abc-defg-hij",
    progress: Optional[float] = 0.5,
    completed_count: int = 2,
    total_trackable: int = 4,
    summary_text: str = "",
    web_url: str = "",
    last_synced_at: int = 1_770_000_000,
    has_unseen_updates: bool = False,
    is_pinned: bool = False,
    is_current_term: bool = True,
    credits: int = 3,
) -> CourseSummary:
    """Create a CourseSummary instance matching src/core/courses/models.py."""
    resolved_web_url = web_url or (
        f"https://courses.ut.edu.vn/course/view.php?id={course_id}"
        if source == "courses"
        else f"https://thnn.ut.edu.vn/course/view.php?id={course_id}"
    )
    code_label = f"[{program_tag}]_{semester_label}_{display_name}_{course_code}"
    return CourseSummary(
        id=course_id,
        source=source,
        fullname=code_label,
        shortname=code_label,
        display_name=display_name,
        course_code=course_code,
        semester_label=semester_label,
        semester_sort_key=semester_sort_key,
        program_tag=program_tag,
        teacher_name=teacher_name,
        teacher_email=teacher_email,
        teacher_phone=teacher_phone,
        meet_url=meet_url,
        progress=progress,
        completed_count=completed_count,
        total_trackable=total_trackable,
        summary_text=summary_text,
        web_url=resolved_web_url,
        last_synced_at=last_synced_at,
        has_unseen_updates=has_unseen_updates,
        is_pinned=is_pinned,
        is_current_term=is_current_term,
        credits=credits,
    )


def make_section(
    section_id: int = 1,
    *,
    course_id: int = 101,
    source: CourseSource = "courses",
    section_num: int = 1,
    name: str = "Chương 1: Giới thiệu",
    summary_html: str = "",
    visible: bool = True,
    modules: tuple[CourseModuleItem, ...] = (),
) -> CourseSection:
    """Create a CourseSection instance matching src/core/courses/models.py."""
    return CourseSection(
        id=section_id,
        course_id=course_id,
        source=source,
        section_num=section_num,
        name=name,
        summary_html=summary_html,
        visible=visible,
        modules=tuple(modules),
    )


def make_attachment(
    file_name: str = "lecture.pdf",
    file_url: str = "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
    *,
    file_size: int = 2048,
    mime_type: str = "",
    time_modified: int = 1_770_000_000,
    extension: Optional[str] = None,
    preview_kind: Optional[PreviewKind] = None,
    is_archive: Optional[bool] = None,
) -> CourseAttachment:
    """Create a CourseAttachment instance with sensible extension/preview_kind inference."""
    ext = (extension if extension is not None else Path(file_name).suffix.lstrip(".")).lower()
    inferred_kind: PreviewKind
    if preview_kind is not None:
        inferred_kind = preview_kind
    elif ext == "pdf":
        inferred_kind = "pdf"
    elif ext in {"docx", "doc"}:
        inferred_kind = "docx"
    elif ext in {"pptx", "ppt"}:
        inferred_kind = "pptx"
    elif ext in {"xlsx", "xls"}:
        inferred_kind = "xlsx"
    elif ext in {"csv", "tsv"}:
        inferred_kind = "csv"
    elif ext in {"png", "jpg", "jpeg", "gif", "webp", "bmp"}:
        inferred_kind = "image"
    elif ext in {"txt", "md", "py", "json", "xml", "log"}:
        inferred_kind = "text"
    elif ext in {"zip", "tar", "gz", "tgz", "rar", "7z"}:
        inferred_kind = "archive"
    else:
        inferred_kind = "unsupported"
    archive_flag = (inferred_kind == "archive") if is_archive is None else bool(is_archive)
    return CourseAttachment(
        file_name=file_name,
        file_url=file_url,
        file_size=file_size,
        mime_type=mime_type,
        time_modified=time_modified,
        extension=ext,
        preview_kind=inferred_kind,
        is_archive=archive_flag,
    )


def make_module(
    cmid: int = 10,
    *,
    instance_id: Optional[int] = None,
    modname: str = "resource",
    name: str = "Tài liệu bài giảng",
    section_id: int = 1,
    url: str = "",
    web_url: str = "",
    description_html: str = "",
    inline_html: str = "",
    completion_mode: int = 0,
    completion_state: int = 0,
    timemodified: int = 1_770_000_000,
    attachments: tuple[CourseAttachment, ...] = (),
    open_strategy: Optional[OpenStrategy] = None,
    is_new: bool = False,
) -> CourseModuleItem:
    """Create a CourseModuleItem instance matching src/core/courses/models.py."""
    inst = cmid if instance_id is None else instance_id
    resolved_web = web_url or url or f"https://courses.ut.edu.vn/mod/{modname}/view.php?id={cmid}"
    resolved_url = url or resolved_web
    if open_strategy is not None:
        strategy = open_strategy
    elif modname == "assign":
        strategy = "assignment_detail"
    elif modname in {"quiz", "attendance", "groupselect", "choicegroup", "choice"}:
        strategy = "webview_activity"
    elif modname in {"page", "label"}:
        strategy = "inline_page"
    elif modname == "url":
        strategy = "external_url"
    elif modname == "folder":
        strategy = "folder_files"
    elif attachments and attachments[0].is_archive:
        strategy = "archive_tree"
    elif attachments:
        strategy = "preview_file"
    else:
        strategy = "web_fallback"
    return CourseModuleItem(
        cmid=cmid,
        instance_id=inst,
        modname=modname,
        name=name,
        section_id=section_id,
        url=resolved_url,
        web_url=resolved_web,
        description_html=description_html,
        inline_html=inline_html,
        completion_mode=completion_mode,
        completion_state=completion_state,
        timemodified=timemodified,
        attachments=tuple(attachments),
        open_strategy=strategy,
        is_new=is_new,
    )


def make_grade(
    grade_id: int = 1,
    *,
    item_name: str = "Kiểm tra giữa kỳ",
    item_module: str = "quiz",
    grade_formatted: str = "8.50",
    range_formatted: str = "0–10",
    percentage: str = "85.00 %",
    weight: str = "30.00 %",
    feedback_html: str = "",
) -> CourseGradeItem:
    """Create a CourseGradeItem instance matching src/core/courses/models.py."""
    return CourseGradeItem(
        id=grade_id,
        item_name=item_name,
        item_module=item_module,
        grade_formatted=grade_formatted,
        range_formatted=range_formatted,
        percentage=percentage,
        weight=weight,
        feedback_html=feedback_html,
    )


def make_recent(
    *,
    course_key: str = "courses:101",
    course_name: str = "Lập trình Python",
    cmid: int = 10,
    title: str = "Bài giảng Chương 1",
    attachment_url: str = "https://courses.ut.edu.vn/pluginfile.php/1/lecture.pdf",
    file_name: str = "lecture.pdf",
    opened_at: int = 1_770_000_000,
    source: CourseSource = "courses",
    course_id: int = 101,
) -> RecentMaterialEntry:
    """Create a RecentMaterialEntry instance matching src/core/courses/models.py."""
    return RecentMaterialEntry(
        course_key=course_key,
        course_name=course_name,
        cmid=cmid,
        title=title,
        attachment_url=attachment_url,
        file_name=file_name,
        opened_at=opened_at,
        source=source,
        course_id=course_id,
    )


# ---------------------------------------------------------------------------
# Service Stubs
# ---------------------------------------------------------------------------


class FakeCatalogService:
    """In-memory CourseCatalogService double supporting catalog, sections, grades, recent, attachments, and groups."""

    def __init__(
        self,
        courses: Optional[list[CourseSummary]] = None,
        recent: Optional[list[RecentMaterialEntry]] = None,
        detail_map: Optional[dict[tuple[str, int], CourseDetailSnapshot]] = None,
        *,
        attachment_bytes_map: Optional[dict[str, bytes]] = None,
        groups_map: Optional[dict[tuple[str, int], list[dict[str, Any]]]] = None,
    ) -> None:
        self.courses: list[CourseSummary] = list(courses or [])
        self.recent: list[RecentMaterialEntry] = list(recent or [])
        self.detail_map: dict[tuple[str, int], CourseDetailSnapshot] = dict(detail_map or {})
        self.attachment_bytes_map: dict[str, bytes] = dict(attachment_bytes_map or {})
        self.groups_map: dict[tuple[str, int], list[dict[str, Any]]] = dict(groups_map or {})
        self.catalog_synced_at: int = 1_770_000_000
        self.last_thnn_error: str = ""
        self.last_courses_error: str = ""
        self.stale: bool = False
        self.sync_results: dict[tuple[str, int], CourseSyncResult] = {}
        self.grades_map: dict[tuple[str, int], list[CourseGradeItem]] = {}
        self.resolved_urls: dict[int, str] = {}
        self.toggle_completion_ok: bool = True
        self.recorded_recent: list[RecentMaterialEntry] = []

    def load_cached_catalog(self) -> tuple[list[CourseSummary], list[RecentMaterialEntry], int]:
        return list(self.courses), list(self.recent), self.catalog_synced_at

    def is_catalog_stale(self, max_age_hours: int) -> bool:
        return self.stale

    def refresh_catalog(self, include_thnn: bool = False) -> list[CourseSummary]:
        return list(self.courses)

    def open_course_cached(self, source: str, course_id: int) -> CourseDetailSnapshot:
        return self.detail_map.get(
            (source, course_id),
            CourseDetailSnapshot(sections=[], grades=[], last_synced_at=0),
        )

    def sync_course_delta(
        self, source: str, course_id: int, force: bool = False
    ) -> CourseSyncResult:
        if (source, course_id) in self.sync_results:
            return self.sync_results[(source, course_id)]
        snap = self.open_course_cached(source, course_id)
        return CourseSyncResult(
            sections=list(snap.sections),
            grades=list(snap.grades),
            changed=False,
            new_cmids=set(),
            last_synced_at=snap.last_synced_at,
        )

    def load_course_grades(
        self, source: str, course_id: int, force: bool = False
    ) -> list[CourseGradeItem]:
        if (source, course_id) in self.grades_map:
            return list(self.grades_map[(source, course_id)])
        return list(self.open_course_cached(source, course_id).grades)

    def toggle_pin(self, course_key: str) -> list[CourseSummary]:
        updated: list[CourseSummary] = []
        for c in self.courses:
            if c.course_key == course_key:
                updated.append(
                    CourseSummary(
                        id=c.id,
                        source=c.source,
                        fullname=c.fullname,
                        shortname=c.shortname,
                        display_name=c.display_name,
                        course_code=c.course_code,
                        semester_label=c.semester_label,
                        semester_sort_key=c.semester_sort_key,
                        program_tag=c.program_tag,
                        teacher_name=c.teacher_name,
                        teacher_email=c.teacher_email,
                        teacher_phone=c.teacher_phone,
                        meet_url=c.meet_url,
                        progress=c.progress,
                        completed_count=c.completed_count,
                        total_trackable=c.total_trackable,
                        summary_text=c.summary_text,
                        web_url=c.web_url,
                        last_synced_at=c.last_synced_at,
                        has_unseen_updates=c.has_unseen_updates,
                        is_pinned=not c.is_pinned,
                        is_current_term=c.is_current_term,
                        credits=c.credits,
                    )
                )
            else:
                updated.append(c)
        self.courses = updated
        return list(self.courses)

    def mark_course_seen(self, course_key: str) -> list[CourseSummary]:
        self.courses = [
            CourseSummary(
                id=c.id,
                source=c.source,
                fullname=c.fullname,
                shortname=c.shortname,
                display_name=c.display_name,
                course_code=c.course_code,
                semester_label=c.semester_label,
                semester_sort_key=c.semester_sort_key,
                program_tag=c.program_tag,
                teacher_name=c.teacher_name,
                teacher_email=c.teacher_email,
                teacher_phone=c.teacher_phone,
                meet_url=c.meet_url,
                progress=c.progress,
                completed_count=c.completed_count,
                total_trackable=c.total_trackable,
                summary_text=c.summary_text,
                web_url=c.web_url,
                last_synced_at=c.last_synced_at,
                has_unseen_updates=False if c.course_key == course_key else c.has_unseen_updates,
                is_pinned=c.is_pinned,
                is_current_term=c.is_current_term,
                credits=c.credits,
            )
            for c in self.courses
        ]
        return list(self.courses)

    def toggle_completion(
        self, source: str, course_id: int, cmid: int, completed: bool
    ) -> bool:
        return self.toggle_completion_ok

    def resolve_module_url(self, source: str, module: CourseModuleItem) -> str:
        return self.resolved_urls.get(module.cmid, module.url or module.web_url)

    def resolve_module_attachments(
        self, source: str, module: CourseModuleItem
    ) -> tuple[CourseAttachment, ...]:
        return module.attachments

    def record_recent(self, entry: RecentMaterialEntry) -> list[RecentMaterialEntry]:
        self.recorded_recent.append(entry)
        self.recent = [entry, *[r for r in self.recent if r.attachment_url != entry.attachment_url]]
        return list(self.recent)

    def fetch_attachment_bytes(
        self,
        source: str,
        file_url: str,
        *,
        progress_callback: Any = None,
    ) -> bytes:
        data = self.attachment_bytes_map.get(file_url, b"")
        if callable(progress_callback) and data:
            progress_callback(len(data), len(data))
        return data

    def fetch_or_cache_preview_file(
        self, source: str, file_url: str, time_modified: int, file_name: str
    ) -> Optional[Path]:
        return None

    def get_user_course_groups(self, source: str, course_id: int) -> list[dict[str, Any]]:
        return list(self.groups_map.get((source, course_id), []))


def build_quiz_review_report(
    attempt_id: int = 5001,
    *,
    question_count: int = 50,
    attempt_number: int = 1,
) -> QuizAttemptReviewReport:
    """Build a realistic multi-question QuizAttemptReviewReport (e.g. 50 or 120 questions)."""
    questions: list[QuizReviewQuestion] = []
    correct = 0
    incorrect = 0
    partial = 0
    unanswered = 0
    for idx in range(1, question_count + 1):
        mod = idx % 4
        if mod == 1:
            state = "correct"
            status_lbl = "Đúng"
            mark = "1.00"
            correct += 1
            sel_idx = 0
        elif mod == 2:
            state = "incorrect"
            status_lbl = "Sai"
            mark = "0.00"
            incorrect += 1
            sel_idx = 1
        elif mod == 3:
            state = "partial"
            status_lbl = "Đúng một phần"
            mark = "0.50"
            partial += 1
            sel_idx = 2
        else:
            state = "unanswered"
            status_lbl = "Chưa trả lời"
            mark = "0.00"
            unanswered += 1
            sel_idx = -1

        choices = tuple(
            QuizReviewChoice(
                label=lbl,
                text=f"Phương án {lbl} cho câu {idx}",
                is_selected=(c_idx == sel_idx),
                state=(
                    "correct"
                    if c_idx == 0
                    else ("incorrect" if c_idx == sel_idx and sel_idx != 0 else "neutral")
                ),
                feedback="Chính xác" if c_idx == 0 and c_idx == sel_idx else "",
            )
            for c_idx, lbl in enumerate(("A", "B", "C", "D"))
        )
        questions.append(
            QuizReviewQuestion(
                display_number=str(idx),
                slot=str(idx),
                question_code=f"CLO1.1-C{idx:03d}",
                qtype="multichoice",
                state_kind=state,
                status_label=status_lbl,
                mark=mark,
                max_mark="1.00",
                question_text=f"Nội dung câu hỏi trắc nghiệm số {idx} trong đề thi học phần?",
                choices=choices,
                student_response_text="" if sel_idx < 0 else f"Phương án {('A', 'B', 'C', 'D')[sel_idx]}",
                right_answer_text="Phương án A",
                general_feedback=f"Xem lại mục {((idx - 1) % 5) + 1} trong giáo trình.",
            )
        )
    return QuizAttemptReviewReport(
        attempt_id=attempt_id,
        attempt_number=attempt_number,
        state_label="Đã hoàn thành",
        grade_text=f"{correct + 0.5 * partial:.2f} / {question_count:.2f}",
        raw_score_text=f"{correct + 0.5 * partial:.2f}/{question_count:.2f}",
        duration_text="25 phút 10 giây",
        finished_at_text="10:30 18/11/2026",
        correct_count=correct,
        incorrect_count=incorrect,
        partial_count=partial,
        unanswered_count=unanswered,
        questions=tuple(questions),
    )


def build_assignment_payload(
    *,
    cmid: int = 301,
    course_id: int = 101,
    title: str = "Bài tập lớn giữa kỳ",
    course_name: str = "Lập trình Python",
    submission_status: str = "Chưa nộp bài",
    description_html: str = "<p>Nộp file báo cáo PDF và mã nguồn ZIP trước hạn chót.</p>",
    url: str = "https://courses.ut.edu.vn/mod/assign/view.php?id=301",
    site_identity: str = "courses",
) -> dict[str, Any]:
    """Create a realistic assignment detail dict matching CoursesCoordinator._build_assignment_payload."""
    return {
        "id": str(cmid),
        "cmid": cmid,
        "course_id": str(course_id),
        "course": course_name,
        "title": title,
        "type": "assign",
        "event_type": "assign",
        "url": url,
        "site_identity": site_identity,
        "submission_status": submission_status,
        "deadline": "2026-12-01T23:59:00",
        "details": {
            "description_html": description_html,
            "course_full_name": course_name,
            "status_data": {
                "Trạng thái nộp": submission_status,
                "Trạng thái chấm điểm": "Chưa chấm điểm",
                "Thời gian còn lại": "3 ngày 5 giờ",
            },
        },
    }


class FakeActivityService:
    """In-memory CourseActivityService double for Attendance, Groups, 50/120-Q Quizzes, Forums, and Rich Books."""

    def __init__(
        self,
        *,
        attendance_map: Optional[dict[int, AttendanceSummary]] = None,
        groups_map: Optional[dict[int, GroupActivitySummary]] = None,
        quizzes_map: Optional[dict[int, QuizActivitySummary]] = None,
        quiz_reports_map: Optional[dict[int, QuizAttemptReviewReport]] = None,
        forums_map: Optional[dict[int, ForumActivitySummary]] = None,
        forum_posts_map: Optional[dict[int, list[ForumPostEntry]]] = None,
        rich_pages_map: Optional[dict[int, RichActivityPage]] = None,
    ) -> None:
        self.attendance_map: dict[int, AttendanceSummary] = dict(attendance_map or {})
        self.groups_map: dict[int, GroupActivitySummary] = dict(groups_map or {})
        self.quizzes_map: dict[int, QuizActivitySummary] = dict(quizzes_map or {})
        self.quiz_reports_map: dict[int, QuizAttemptReviewReport] = dict(quiz_reports_map or {})
        self.forums_map: dict[int, ForumActivitySummary] = dict(forums_map or {})
        self.forum_posts_map: dict[int, list[ForumPostEntry]] = dict(forum_posts_map or {})
        self.rich_pages_map: dict[int, RichActivityPage] = dict(rich_pages_map or {})
        self.submitted_attendance: list[tuple[str, str, dict[str, str]]] = []
        self.submitted_group_actions: list[tuple[str, int, Optional[int]]] = []

        # Default rich fixtures keyed by standard cmids/attempt_ids when caller omits them
        self.quiz_reports_map.setdefault(5001, build_quiz_review_report(5001, question_count=50))
        self.quiz_reports_map.setdefault(5120, build_quiz_review_report(5120, question_count=120))

    def fetch_attendance(
        self, source: CourseSource, module: CourseModuleItem
    ) -> AttendanceSummary:
        if module.cmid in self.attendance_map:
            return self.attendance_map[module.cmid]
        return AttendanceSummary(
            cmid=module.cmid,
            title=module.name or "Điểm danh lớp học",
            taken_sessions="8/10",
            points_over_taken="16/16",
            percentage="100%",
            records=(
                AttendanceRecord(
                    date_str="10/11/2026 07:30",
                    description="Buổi 1 - Lý thuyết",
                    status="Present",
                    points="2/2",
                    remarks="Đúng giờ",
                ),
                AttendanceRecord(
                    date_str="17/11/2026 07:30",
                    description="Buổi 2 - Thực hành",
                    status="Present",
                    points="2/2",
                    remarks="Hoàn thành bài lab",
                ),
            ),
            submit_form={"sessid": "99", "sesskey": "abc"},
            submit_action_url="https://courses.ut.edu.vn/mod/attendance/attendance.php",
            status_options=(("1", "Present"), ("2", "Late")),
            requires_password=False,
        )

    def submit_attendance(
        self,
        source: CourseSource,
        action_url: str,
        fields: dict[str, str],
        *,
        fallback_title: str = "Điểm danh",
    ) -> tuple[bool, str, Optional[AttendanceSummary]]:
        self.submitted_attendance.append((source, action_url, dict(fields)))
        updated = AttendanceSummary(
            cmid=0,
            title=fallback_title,
            taken_sessions="9/10",
            points_over_taken="18/18",
            percentage="100%",
            records=(
                AttendanceRecord(
                    date_str="18/11/2026 07:30",
                    description="Buổi 3 - Tự điểm danh",
                    status="Present",
                    points="2/2",
                    remarks="Đã ghi nhận",
                ),
            ),
            status_message="Đã gửi điểm danh thành công.",
        )
        return (True, "Đã gửi điểm danh thành công.", updated)

    def fetch_group_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> GroupActivitySummary:
        if module.cmid in self.groups_map:
            return self.groups_map[module.cmid]
        return GroupActivitySummary(
            cmid=module.cmid,
            modname=module.modname or "groupselect",
            title=module.name or "Đăng ký nhóm đồ án",
            intro="Mỗi nhóm tối đa 4 sinh viên.",
            my_group_names=("Nhóm 1 - Alpha",),
            groups=(
                CourseGroupEntry(
                    group_id="g1",
                    name="Nhóm 1 - Alpha",
                    description="Đề tài Web & Mobile",
                    member_count_label="3/4",
                    members=("Nguyễn Văn A", "Trần Thị B", "Lê Văn C"),
                    is_member=True,
                    can_join=False,
                    can_leave=True,
                    action_url="https://courses.ut.edu.vn/mod/groupselect/view.php",
                    action_fields=(("id", str(module.cmid)), ("group", "g1")),
                ),
                CourseGroupEntry(
                    group_id="g2",
                    name="Nhóm 2 - Beta",
                    description="Đề tài AI & Data",
                    member_count_label="2/4",
                    members=("Phạm Minh D", "Hoàng Anh E"),
                    is_member=False,
                    can_join=True,
                    can_leave=False,
                    action_url="https://courses.ut.edu.vn/mod/groupselect/view.php",
                    action_fields=(("id", str(module.cmid)), ("group", "g2")),
                ),
            ),
        )

    def submit_group_action(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        action_url: str = "",
        fields: Optional[dict[str, str]] = None,
        choice_option_id: Optional[int] = None,
        course_id: int = 0,
    ) -> tuple[bool, str, GroupActivitySummary]:
        self.submitted_group_actions.append((source, module.cmid, choice_option_id))
        target_gid = (fields or {}).get("group", "g2")
        updated = GroupActivitySummary(
            cmid=module.cmid,
            modname=module.modname or "groupselect",
            title=module.name or "Đăng ký nhóm đồ án",
            my_group_names=(f"Nhóm {target_gid}",),
            groups=(
                CourseGroupEntry(
                    group_id=target_gid,
                    name=f"Nhóm {target_gid}",
                    description="Đã cập nhật thành viên",
                    member_count_label="3/4",
                    members=("Bạn", "Phạm Minh D", "Hoàng Anh E"),
                    is_member=True,
                    can_join=False,
                    can_leave=True,
                    action_url=action_url,
                ),
            ),
            message="Đã cập nhật trạng thái nhóm.",
        )
        self.groups_map[module.cmid] = updated
        return (True, "Đã cập nhật trạng thái nhóm.", updated)

    def fetch_quiz_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> QuizActivitySummary:
        if module.cmid in self.quizzes_map:
            return self.quizzes_map[module.cmid]
        return QuizActivitySummary(
            cmid=module.cmid,
            quiz_id=module.instance_id or 701,
            title=module.name or "Kiểm tra trắc nghiệm giữa kỳ",
            intro="Gồm 50 câu hỏi trắc nghiệm tổng hợp kiến thức chương 1–3.",
            time_limit_seconds=2700,
            max_attempts=2,
            grade_method="Điểm cao nhất",
            access_rules=("Thời gian làm bài: 45 phút",),
            attempts=(
                QuizAttemptInfo(
                    attempt_number=1,
                    attempt_id=5001,
                    state="Đã hoàn thành",
                    score_text="8.50 / 10.00",
                    time_submitted="10:30 18/11/2026",
                    review_url="https://courses.ut.edu.vn/mod/quiz/review.php?attempt=5001",
                ),
                QuizAttemptInfo(
                    attempt_number=2,
                    attempt_id=5120,
                    state="Đã hoàn thành",
                    score_text="9.25 / 10.00",
                    time_submitted="15:45 18/11/2026",
                    review_url="https://courses.ut.edu.vn/mod/quiz/review.php?attempt=5120",
                ),
            ),
            can_attempt_in_webview=True,
            web_url=module.web_url or module.url or f"https://courses.ut.edu.vn/mod/quiz/view.php?id={module.cmid}",
        )

    def fetch_quiz_review_report(
        self,
        attempt_id: int,
        *,
        source: CourseSource = "courses",
        review_url: str = "",
    ) -> QuizAttemptReviewReport:
        if attempt_id in self.quiz_reports_map:
            return self.quiz_reports_map[attempt_id]
        q_count = 120 if attempt_id >= 5120 else 50
        return build_quiz_review_report(attempt_id, question_count=q_count)

    def fetch_quiz_review(self, attempt_id: int) -> list[dict[str, Any]]:
        report = self.fetch_quiz_review_report(attempt_id)
        return [
            {
                "number": q.display_number,
                "slot": q.slot,
                "question_code": q.question_code,
                "state_kind": q.state_kind,
                "status": q.status_label,
                "mark": q.mark,
                "maxmark": q.max_mark,
                "question_text": q.question_text,
                "right_answer": q.right_answer_text,
                "general_feedback": q.general_feedback,
            }
            for q in report.questions
        ]

    def fetch_forum_activity(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> ForumActivitySummary:
        if module.cmid in self.forums_map:
            return self.forums_map[module.cmid]
        return ForumActivitySummary(
            cmid=module.cmid,
            forum_id=module.instance_id or 801,
            title=module.name or "Diễn đàn trao đổi học phần",
            intro="Nơi giải đáp thắc mắc và thông báo lịch học.",
            discussions=(
                ForumDiscussionEntry(
                    discussion_id=9001,
                    name="Hướng dẫn đồ án cuối kỳ & chia nhóm",
                    author="TS. Nguyễn Văn A",
                    created_at="18/11/2026",
                    num_replies=3,
                    pinned=True,
                    message_preview="Sinh viên đọc kỹ tiêu chí chấm điểm và đăng ký nhóm trước thứ 6.",
                    discussion_url="https://courses.ut.edu.vn/mod/forum/discuss.php?d=9001",
                ),
                ForumDiscussionEntry(
                    discussion_id=9002,
                    name="Hỏi đáp bài tập tuần 4",
                    author="Trần Minh B",
                    created_at="17/11/2026",
                    num_replies=1,
                    pinned=False,
                    message_preview="Thầy cho em hỏi phần cấu hình Docker trong lab 4 ạ.",
                    discussion_url="https://courses.ut.edu.vn/mod/forum/discuss.php?d=9002",
                ),
            ),
        )

    def fetch_discussion_posts(
        self, source: CourseSource, discussion_id: int
    ) -> list[ForumPostEntry]:
        if discussion_id in self.forum_posts_map:
            return list(self.forum_posts_map[discussion_id])
        return [
            ForumPostEntry(
                post_id=discussion_id * 10 + 1,
                author="TS. Nguyễn Văn A",
                created_at="18/11/2026 08:00",
                subject=f"Chủ đề #{discussion_id}",
                message_text="Các em tải đề cương chi tiết đính kèm bên dưới và thực hiện theo từng tuần.",
                attachments=("DeCuong_DoAn.pdf",),
            ),
            ForumPostEntry(
                post_id=discussion_id * 10 + 2,
                author="Nguyễn Hoàng C",
                created_at="18/11/2026 09:15",
                subject=f"Re: Chủ đề #{discussion_id}",
                message_text="Dạ em cảm ơn thầy, nhóm em đã rõ yêu cầu ạ.",
                attachments=(),
            ),
        ]

    def fetch_rich_content(
        self,
        source: CourseSource,
        module: CourseModuleItem,
        *,
        course_id: int,
    ) -> RichActivityPage:
        if module.cmid in self.rich_pages_map:
            return self.rich_pages_map[module.cmid]
        return RichActivityPage(
            cmid=module.cmid,
            modname=module.modname or "book",
            title=module.name or "Giáo trình điện tử",
            intro_markdown="Tài liệu học tập trực tuyến gồm nhiều chương.",
            chapters=(
                BookChapterEntry(
                    chapter_id="1",
                    title="Chương 1: Tổng quan kiến trúc",
                    content_markdown="## Chương 1\n\nGiới thiệu kiến trúc hệ thống và mục tiêu môn học.",
                ),
                BookChapterEntry(
                    chapter_id="1.1",
                    title="1.1 Mô hình phân lớp",
                    content_markdown="### 1.1 Mô hình phân lớp\n\nTách biệt Core, Service và Presentation.",
                    subchapter=True,
                ),
                BookChapterEntry(
                    chapter_id="2",
                    title="Chương 2: Triển khai thực tế",
                    content_markdown="## Chương 2\n\nHướng dẫn đóng gói và kiểm thử đa nền tảng.",
                ),
            ),
        )
