"""Pure-Python XSS-safe converters for DOCX, XLSX, CSV/TSV, PPTX, and text previews."""

from __future__ import annotations

import base64
import csv
import html
import io
import posixpath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Iterator, Optional

from core.courses.models import PreviewKind

_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_MEDIA_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_MEDIA_BYTES = 12 * 1024 * 1024
_MAX_MEDIA_COUNT = 50
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_MAX_CSV_BYTES = 4 * 1024 * 1024
_MAX_XLSX_SHEETS = 20
_MAX_TABLE_ROWS = 1000
_MAX_TABLE_COLS = 100
_MAX_PPTX_SLIDES = 200
_READ_CHUNK_SIZE = 65536

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V_NS = "urn:schemas-microsoft-com:vml"
_XL_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

_DTD_PATTERN = re.compile(rb"<!\s*(?:doctype|entity)\b", re.IGNORECASE)
_HEADING_STYLE_PATTERN = re.compile(r"^(?:heading\s*|tiêu\s*đề\s*)(\d+)$", re.IGNORECASE)
_CELL_REF_PATTERN = re.compile(r"^([A-Za-z]+)")
_PPTX_SLIDE_PATH_PATTERN = re.compile(r"ppt/slides/slide(\d+)\.xml")

_SAFE_RASTER_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}
_DISGUISED_MARKUP_PREFIXES = (b"<svg", b"<!doctype", b"<html", b"<script", b"<?xml")

_CONVERSION_ERRORS = (
    zipfile.BadZipFile,
    ET.ParseError,
    KeyError,
    ValueError,
    EOFError,
    zlib.error,
    RuntimeError,
    OSError,
)


@dataclass(frozen=True, slots=True)
class DocumentOutlineItem:
    """Navigable section heading extracted from a converted document."""

    anchor_id: str
    level: int
    title: str


@dataclass(frozen=True, slots=True)
class SpreadsheetSheet:
    """Normalized tabular sheet extracted from XLSX or CSV/TSV."""

    name: str
    rows: list[list[str]]


@dataclass(frozen=True, slots=True)
class ConvertedDocument:
    """Normalized preview payload produced from document bytes."""

    kind: PreviewKind
    title: str
    html_document: str = ""
    markdown_text: str = ""
    outline: list[DocumentOutlineItem] = field(default_factory=list)
    sheets: list[SpreadsheetSheet] = field(default_factory=list)
    slides: list[tuple[int, str, str]] = field(default_factory=list)


def convert_document_bytes(
    file_name: str,
    data: bytes,
    kind: PreviewKind,
    *,
    dark_mode: bool = False,
) -> ConvertedDocument:
    """Convert raw document bytes into escaped HTML, Markdown, and structured metadata."""
    title = file_name or "Tài liệu"
    try:
        if kind == "docx":
            return _convert_docx(title, data, dark_mode=dark_mode)
        if kind == "xlsx":
            return _convert_xlsx(title, data, dark_mode=dark_mode)
        if kind == "csv":
            return _convert_csv(title, data, dark_mode=dark_mode)
        if kind == "pptx":
            return _convert_pptx(title, data, dark_mode=dark_mode)
        if kind == "text":
            return _convert_text(title, data, dark_mode=dark_mode)
        return _build_notice_document(
            kind,
            title,
            "Định dạng này không hỗ trợ chuyển đổi nội dung trực tiếp.",
            dark_mode=dark_mode,
        )
    except _CONVERSION_ERRORS:
        return _build_notice_document(
            kind,
            title,
            "Không thể đọc nội dung tệp do tệp bị hỏng, được bảo vệ mật khẩu hoặc vượt quá giới hạn an toàn.",
            dark_mode=dark_mode,
        )


def _build_notice_document(
    kind: PreviewKind,
    title: str,
    message: str,
    *,
    dark_mode: bool,
) -> ConvertedDocument:
    """Build a safe fallback ConvertedDocument containing an escaped notice."""
    body_html = f'<p class="notice">{html.escape(message)}</p>'
    return ConvertedDocument(
        kind=kind,
        title=title,
        html_document=_base_html_shell(title, body_html, dark_mode=dark_mode),
        markdown_text=message,
    )


def _base_html_shell(title: str, body_html: str, *, dark_mode: bool = False) -> str:
    """Wrap body HTML in a themed, self-contained responsive HTML5 document."""
    if dark_mode:
        palette = (
            "--bg:#0f172a;--fg:#e2e8f0;--surface:#1e293b;"
            "--border:#334155;--muted:#94a3b8;--accent:#60a5fa;"
            "--th-bg:#1e293b;--code-bg:#0b1220;"
        )
    else:
        palette = (
            "--bg:#ffffff;--fg:#0f172a;--surface:#f8fafc;"
            "--border:#cbd5e1;--muted:#475569;--accent:#2563eb;"
            "--th-bg:#f1f5f9;--code-bg:#f8fafc;"
        )
    escaped_title = html.escape(title)
    return (
        "<!DOCTYPE html><html lang=\"vi\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        f"<title>{escaped_title}</title>"
        "<style>"
        f":root{{{palette}}}"
        "*{box-sizing:border-box;}"
        "body{margin:0;padding:16px;background:var(--bg);color:var(--fg);"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "line-height:1.6;word-wrap:break-word;}"
        "h1,h2,h3,h4,h5,h6{margin:1.1em 0 0.45em;line-height:1.3;}"
        "p{margin:0.55em 0;}"
        ".muted,.notice{color:var(--muted);padding:12px;border:1px solid var(--border);"
        "border-radius:8px;background:var(--surface);}"
        ".table-wrap{overflow:auto;max-width:100%;max-height:75vh;margin:12px 0;"
        "border:1px solid var(--border);border-radius:8px;}"
        "table{border-collapse:collapse;width:100%;font-size:0.92rem;}"
        "th,td{border:1px solid var(--border);padding:6px 10px;text-align:left;"
        "white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis;}"
        "thead th{position: sticky;top:0;z-index:2;background:var(--th-bg);font-weight:600;}"
        "th:first-child,td:first-child{position: sticky;left:0;z-index:1;background:var(--surface);}"
        "thead th:first-child{z-index:3;background:var(--th-bg);}"
        ".doc-figure{margin:12px 0;}"
        ".doc-image{max-width:100%;height:auto;border-radius:6px;border:1px solid var(--border);}"
        ".slide-card{background:var(--surface);border:1px solid var(--border);"
        "border-radius:10px;padding:16px;margin:0 0 14px;}"
        ".slide-card h3{margin-top:0;color:var(--accent);}"
        ".slide-notes{margin-top:10px;padding-top:10px;border-top:1px dashed var(--border);"
        "color:var(--muted);font-size:0.92rem;}"
        ".code-view{background:var(--code-bg);border:1px solid var(--border);border-radius:8px;"
        "padding:14px;overflow:auto;font-family:Consolas,'Courier New',monospace;"
        "font-size:0.9rem;white-space:pre-wrap;word-break:break-word;}"
        "</style></head><body>"
        f"{body_html}"
        "</body></html>"
    )


def _read_zip_entry_capped(zf: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    """Read a ZIP member incrementally, aborting if uncompressed bytes exceed max_bytes."""
    info = zf.getinfo(name)
    buf = bytearray()
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > max_bytes:
                raise ValueError(f"ZIP entry exceeds {max_bytes} byte limit: {name}")
    return bytes(buf)


def _parse_xml_safe(raw: bytes) -> ET.Element:
    """Parse XML bytes after rejecting DTD and ENTITY declarations."""
    if _DTD_PATTERN.search(raw) or (b"\x00" in raw and _DTD_PATTERN.search(raw.replace(b"\x00", b""))):
        raise ValueError("XML DTD/ENTITY declarations are forbidden")
    return ET.fromstring(raw)


def _resolve_ooxml_target(base_dir: str, target: str) -> Optional[str]:
    """Normalize an OOXML relationship target inside the archive, rejecting traversal."""
    cleaned = (target or "").strip().replace("\\", "/")
    if not cleaned or ":" in cleaned:
        return None
    if cleaned.startswith("/"):
        normalized = posixpath.normpath(cleaned.lstrip("/"))
    elif base_dir:
        normalized = posixpath.normpath(posixpath.join(base_dir, cleaned))
    else:
        normalized = posixpath.normpath(cleaned)
    if normalized in ("", ".", "..") or normalized.startswith("../") or normalized.startswith("/"):
        return None
    return normalized


def _parse_relationships(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    rels_path: str,
    base_dir: str,
    *,
    type_suffix: Optional[str] = None,
) -> dict[str, str]:
    """Parse an OOXML .rels file into a safe {rId: archive_member_path} map."""
    if rels_path not in zf_names:
        return {}
    raw = _read_zip_entry_capped(zf, rels_path, _MAX_XML_BYTES)
    root = _parse_xml_safe(raw)
    mapping: dict[str, str] = {}
    for rel in root:
        if _local_name(rel.tag) != "Relationship":
            continue
        if (rel.attrib.get("TargetMode") or "").strip().lower() == "external":
            continue
        rel_type = (rel.attrib.get("Type") or "").strip().lower()
        if type_suffix and not rel_type.endswith(type_suffix.lower()):
            continue
        rel_id = rel.attrib.get("Id") or ""
        resolved = _resolve_ooxml_target(base_dir, rel.attrib.get("Target", ""))
        if rel_id and resolved:
            mapping[rel_id] = resolved
    return mapping


def _local_name(tag: str) -> str:
    """Return the local element name without Clark-notation namespace."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _decode_text_bytes(raw: bytes) -> str:
    """Decode text bytes using UTF-8-SIG, falling back to CP1258 then replacement."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            return unicodedata.normalize("NFC", raw.decode("cp1258"))
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig", errors="replace")


def _escape_md_cell(text: str) -> str:
    """Escape cell content for single-line Markdown pipe tables."""
    single_line = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return single_line.replace("|", "\\|")


def _render_table_html_and_markdown(rows: list[list[str]]) -> tuple[str, str]:
    """Render rectangular table HTML (with sticky CSS hooks) and Markdown pipe table."""
    if not rows:
        return '<p class="muted">(Bảng trống)</p>', ""
    capped_rows = rows[:_MAX_TABLE_ROWS]
    col_count = min(_MAX_TABLE_COLS, max((len(row) for row in capped_rows), default=0))
    if col_count == 0:
        return '<p class="muted">(Bảng trống)</p>', ""

    normalized = [
        [row[idx] if idx < len(row) else "" for idx in range(col_count)]
        for row in capped_rows
    ]

    header_cells = "".join(f"<th>{html.escape(cell)}</th>" for cell in normalized[0])
    body_rows_html = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
        for row in normalized[1:]
    )
    tbody_html = f"<tbody>{body_rows_html}</tbody>" if body_rows_html else ""
    table_html = (
        '<div class="table-wrap">'
        f"<table><thead><tr>{header_cells}</tr></thead>{tbody_html}</table>"
        "</div>"
    )

    md_lines = [
        "| " + " | ".join(_escape_md_cell(cell) for cell in normalized[0]) + " |",
        "| " + " | ".join("---" for _ in range(col_count)) + " |",
    ]
    for row in normalized[1:]:
        md_lines.append("| " + " | ".join(_escape_md_cell(cell) for cell in row) + " |")
    return table_html, "\n".join(md_lines)


def _iter_docx_body_blocks(container: ET.Element) -> Iterator[ET.Element]:
    """Yield <w:p> and <w:tbl> blocks from a DOCX body, unwrapping SDT containers."""
    for child in container:
        if child.tag == f"{{{_W_NS}}}sdt":
            sdt_content = child.find(f"{{{_W_NS}}}sdtContent")
            if sdt_content is not None:
                yield from _iter_docx_body_blocks(sdt_content)
        elif child.tag in (f"{{{_W_NS}}}p", f"{{{_W_NS}}}tbl"):
            yield child


def _docx_heading_level(p_el: ET.Element) -> Optional[int]:
    """Extract 1..6 heading level from <w:pStyle> or <w:outlineLvl> if present."""
    p_pr = p_el.find(f"{{{_W_NS}}}pPr")
    if p_pr is None:
        return None
    p_style = p_pr.find(f"{{{_W_NS}}}pStyle")
    if p_style is not None:
        val = (p_style.attrib.get(f"{{{_W_NS}}}val") or "").strip()
        match = _HEADING_STYLE_PATTERN.match(val)
        if match:
            return min(max(int(match.group(1)), 1), 6)
        lowered = val.lower()
        if lowered == "title":
            return 1
        if lowered == "subtitle":
            return 2

    outline_lvl = p_pr.find(f"{{{_W_NS}}}outlineLvl")
    if outline_lvl is not None:
        raw_lvl = (outline_lvl.attrib.get(f"{{{_W_NS}}}val") or "").strip()
        if raw_lvl.isdigit():
            num = int(raw_lvl)
            if 0 <= num <= 8:
                return min(num + 1, 6)
    return None


def _docx_paragraph_text(p_el: ET.Element) -> str:
    """Collect visible text from a WordprocessingML <w:p>, ignoring deleted runs."""
    parts: list[str] = []

    def _walk(node: ET.Element) -> None:
        if node.tag in (f"{{{_W_NS}}}del", f"{{{_W_NS}}}delText", f"{{{_W_NS}}}instrText"):
            return
        if node.tag == f"{{{_W_NS}}}t" and node.text:
            parts.append(node.text)
        elif node.tag in (f"{{{_W_NS}}}br", f"{{{_W_NS}}}cr"):
            parts.append("\n")
        elif node.tag == f"{{{_W_NS}}}tab":
            parts.append("\t")
        for sub in node:
            _walk(sub)

    _walk(p_el)
    return "".join(parts)


def _docx_paragraph_image_rids(p_el: ET.Element) -> list[str]:
    """Extract embedded image relationship IDs from DrawingML or VML nodes inside <w:p>."""
    rids: list[str] = []
    for node in p_el.iter():
        if node.tag == f"{{{_A_NS}}}blip":
            rid = node.attrib.get(f"{{{_R_NS}}}embed")
            if rid:
                rids.append(rid)
        elif node.tag == f"{{{_V_NS}}}imagedata":
            rid = node.attrib.get(f"{{{_R_NS}}}id")
            if rid:
                rids.append(rid)
    return rids


def _convert_docx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert DOCX bytes into outline, Markdown, and XSS-safe HTML with raster media."""
    outline: list[DocumentOutlineItem] = []
    html_blocks: list[str] = []
    md_blocks: list[str] = []
    rendered_media: set[str] = set()
    media_state = {"count": 0, "bytes": 0}

    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        zf_names = set(zf.namelist())
        doc_raw = _read_zip_entry_capped(zf, "word/document.xml", _MAX_XML_BYTES)
        doc_root = _parse_xml_safe(doc_raw)
        rels = _parse_relationships(
            zf, zf_names, "word/_rels/document.xml.rels", "word"
        )

        def _try_embed_media(zip_path: str) -> Optional[str]:
            if zip_path in rendered_media or zip_path not in zf_names:
                return None
            if media_state["count"] >= _MAX_MEDIA_COUNT:
                return None
            ext = posixpath.splitext(zip_path.lower())[1]
            mime = _SAFE_RASTER_MIMES.get(ext)
            if not mime:
                return None
            try:
                raw_img = _read_zip_entry_capped(zf, zip_path, _MAX_MEDIA_BYTES)
            except _CONVERSION_ERRORS:
                return None
            if not raw_img:
                return None
            head = raw_img.lstrip()[:20].lower()
            if head.startswith(_DISGUISED_MARKUP_PREFIXES):
                return None
            if media_state["bytes"] + len(raw_img) > _MAX_TOTAL_MEDIA_BYTES:
                return None
            rendered_media.add(zip_path)
            media_state["count"] += 1
            media_state["bytes"] += len(raw_img)
            encoded = base64.b64encode(raw_img).decode("ascii")
            alt = html.escape(posixpath.basename(zip_path))
            return (
                '<figure class="doc-figure">'
                f'<img class="doc-image" src="data:{mime};base64,{encoded}" alt="{alt}"/>'
                "</figure>"
            )

        body = doc_root.find(f"{{{_W_NS}}}body")
        if body is not None:
            for block in _iter_docx_body_blocks(body):
                if block.tag == f"{{{_W_NS}}}p":
                    level = _docx_heading_level(block)
                    text = _docx_paragraph_text(block).strip()
                    if level is not None and text:
                        anchor_id = f"sec-{len(outline) + 1}"
                        outline.append(
                            DocumentOutlineItem(
                                anchor_id=anchor_id, level=level, title=text
                            )
                        )
                        html_blocks.append(
                            f'<h{level} id="{anchor_id}">{html.escape(text)}</h{level}>'
                        )
                        md_blocks.append(f"{'#' * level} {text}")
                    elif text:
                        escaped_p = html.escape(text).replace("\n", "<br/>")
                        html_blocks.append(f"<p>{escaped_p}</p>")
                        p_pr = block.find(f"{{{_W_NS}}}pPr")
                        is_list = (
                            p_pr is not None
                            and p_pr.find(f"{{{_W_NS}}}numPr") is not None
                        )
                        md_blocks.append(f"- {text}" if is_list else text)

                    for rid in _docx_paragraph_image_rids(block):
                        target = rels.get(rid)
                        if target:
                            img_html = _try_embed_media(target)
                            if img_html:
                                html_blocks.append(img_html)

                elif block.tag == f"{{{_W_NS}}}tbl":
                    table_rows: list[list[str]] = []
                    for tr in block.findall(f"{{{_W_NS}}}tr")[:_MAX_TABLE_ROWS]:
                        row_cells: list[str] = []
                        for tc in tr.findall(f"{{{_W_NS}}}tc")[:_MAX_TABLE_COLS]:
                            cell_paras = [
                                _docx_paragraph_text(p).strip()
                                for p in tc.findall(f".//{{{_W_NS}}}p")
                            ]
                            row_cells.append(
                                "\n".join(p for p in cell_paras if p)
                            )
                        table_rows.append(row_cells)
                    tbl_html, tbl_md = _render_table_html_and_markdown(table_rows)
                    html_blocks.append(tbl_html)
                    if tbl_md:
                        md_blocks.append(tbl_md)

        for name in sorted(zf_names):
            if name.startswith("word/media/") and name not in rendered_media:
                img_html = _try_embed_media(name)
                if img_html:
                    html_blocks.append(img_html)

    if not html_blocks:
        html_blocks.append('<p class="muted">(Tài liệu trống)</p>')
    return ConvertedDocument(
        kind="docx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_blocks), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_blocks),
        outline=outline,
    )


def _extract_xlsx_text_container(container: ET.Element) -> str:
    """Extract text from an XLSX <si> or <is> element, skipping <rPh> phonetic runs."""
    direct_t = container.find(f"{{{_XL_NS}}}t")
    if direct_t is not None and direct_t.text is not None:
        return direct_t.text
    parts: list[str] = []
    for run in container.findall(f"{{{_XL_NS}}}r"):
        t_el = run.find(f"{{{_XL_NS}}}t")
        if t_el is not None and t_el.text:
            parts.append(t_el.text)
    return "".join(parts)


def _parse_cell_col_index(ref: str) -> Optional[int]:
    """Convert an A1-style column reference like 'AA12' to a 0-based column index."""
    match = _CELL_REF_PATTERN.match((ref or "").strip())
    if not match:
        return None
    col = 0
    for ch in match.group(1).upper():
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return col - 1


def _parse_xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    """Extract normalized display string from a Worksheet <c> element."""
    cell_type = (cell.attrib.get("t") or "").strip()
    v_el = cell.find(f"{{{_XL_NS}}}v")
    is_el = cell.find(f"{{{_XL_NS}}}is")

    if cell_type == "s":
        if v_el is not None and v_el.text is not None:
            raw_idx = v_el.text.strip()
            if raw_idx.isdigit():
                idx = int(raw_idx)
                if 0 <= idx < len(shared_strings):
                    return shared_strings[idx]
        return ""
    if cell_type == "inlineStr":
        return _extract_xlsx_text_container(is_el) if is_el is not None else ""
    if cell_type == "b":
        if v_el is None or v_el.text is None:
            return ""
        raw_bool = v_el.text.strip().lower()
        if raw_bool in {"1", "true"}:
            return "TRUE"
        if raw_bool in {"0", "false"}:
            return "FALSE"
        return v_el.text.strip()
    if v_el is not None and v_el.text is not None:
        return v_el.text
    if is_el is not None:
        return _extract_xlsx_text_container(is_el)
    return ""


def _convert_xlsx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert XLSX bytes into bounded sheets, sticky HTML tables, and Markdown."""
    sheets: list[SpreadsheetSheet] = []
    outline: list[DocumentOutlineItem] = []
    html_sections: list[str] = []
    md_sections: list[str] = []

    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        zf_names = set(zf.namelist())
        wb_raw = _read_zip_entry_capped(zf, "xl/workbook.xml", _MAX_XML_BYTES)
        wb_root = _parse_xml_safe(wb_raw)

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf_names:
            sst_raw = _read_zip_entry_capped(zf, "xl/sharedStrings.xml", _MAX_XML_BYTES)
            sst_root = _parse_xml_safe(sst_raw)
            for si in sst_root.findall(f"{{{_XL_NS}}}si"):
                shared_strings.append(_extract_xlsx_text_container(si))

        rels = _parse_relationships(
            zf, zf_names, "xl/_rels/workbook.xml.rels", "xl"
        )

        sheet_els = wb_root.findall(f".//{{{_XL_NS}}}sheet")[:_MAX_XLSX_SHEETS]
        for idx, sheet_el in enumerate(sheet_els):
            sheet_name = sheet_el.attrib.get("name") or f"Sheet {idx + 1}"
            rid = sheet_el.attrib.get(f"{{{_R_NS}}}id") or ""
            sheet_path = rels.get(rid) or f"xl/worksheets/sheet{idx + 1}.xml"
            if sheet_path not in zf_names:
                continue

            ws_raw = _read_zip_entry_capped(zf, sheet_path, _MAX_XML_BYTES)
            ws_root = _parse_xml_safe(ws_raw)
            sheet_data = ws_root.find(f"{{{_XL_NS}}}sheetData")
            rows: list[list[str]] = []
            if sheet_data is not None:
                for row_el in sheet_data.findall(f"{{{_XL_NS}}}row")[:_MAX_TABLE_ROWS]:
                    row_cells: dict[int, str] = {}
                    next_col = 0
                    for cell in row_el.findall(f"{{{_XL_NS}}}c"):
                        ref = cell.attrib.get("r", "")
                        parsed_col = _parse_cell_col_index(ref) if ref else None
                        col_idx = parsed_col if parsed_col is not None else next_col
                        next_col = col_idx + 1
                        if col_idx >= _MAX_TABLE_COLS:
                            continue
                        val = _parse_xlsx_cell_value(cell, shared_strings)
                        if val != "":
                            row_cells[col_idx] = val
                    if row_cells:
                        max_col = max(row_cells.keys())
                        rows.append([row_cells.get(c, "") for c in range(max_col + 1)])
                    else:
                        rows.append([])

            while rows and not rows[-1]:
                rows.pop()

            sheets.append(SpreadsheetSheet(name=sheet_name, rows=rows))
            anchor_id = f"sheet-{idx + 1}"
            outline.append(
                DocumentOutlineItem(anchor_id=anchor_id, level=1, title=sheet_name)
            )
            tbl_html, tbl_md = _render_table_html_and_markdown(rows)
            html_sections.append(
                f'<section><h2 id="{anchor_id}">{html.escape(sheet_name)}</h2>{tbl_html}</section>'
            )
            md_sections.append(
                f"## {sheet_name}\n\n{tbl_md}" if tbl_md else f"## {sheet_name}"
            )

    if not html_sections:
        html_sections.append('<p class="muted">(Bảng tính trống)</p>')
    return ConvertedDocument(
        kind="xlsx",
        title=title,
        html_document=_base_html_shell(
            title, "\n".join(html_sections), dark_mode=dark_mode
        ),
        markdown_text="\n\n".join(md_sections),
        outline=outline,
        sheets=sheets,
    )


def _convert_csv(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert CSV or TSV bytes into a bounded SpreadsheetSheet and sticky HTML table."""
    delimiter = "\t" if title.strip().lower().endswith(".tsv") else ","
    text = _decode_text_bytes(data[:_MAX_CSV_BYTES])
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        if len(rows) >= _MAX_TABLE_ROWS:
            break
        rows.append(row[:_MAX_TABLE_COLS])

    sheet_name = posixpath.basename(title.replace("\\", "/")) or "CSV"
    sheet = SpreadsheetSheet(name=sheet_name, rows=rows)
    tbl_html, tbl_md = _render_table_html_and_markdown(rows)
    return ConvertedDocument(
        kind="csv",
        title=title,
        html_document=_base_html_shell(title, tbl_html, dark_mode=dark_mode),
        markdown_text=tbl_md,
        sheets=[sheet],
    )


def _extract_drawingml_text(root: ET.Element, *, skip_slide_num: bool = False) -> str:
    """Extract DrawingML <a:p> paragraph text lines from a PPTX slide or notes XML."""
    paragraphs: list[str] = []

    def _collect_from_tx_body(tx_body: ET.Element) -> None:
        for p_el in tx_body.findall(f".//{{{_A_NS}}}p"):
            parts: list[str] = []
            for node in p_el.iter():
                if node.tag == f"{{{_A_NS}}}t" and node.text:
                    parts.append(node.text)
                elif node.tag == f"{{{_A_NS}}}br":
                    parts.append("\n")
            line = "".join(parts).strip()
            if line:
                paragraphs.append(line)

    if not skip_slide_num:
        for elem in root.iter():
            if elem.tag in (f"{{{_P_NS}}}txBody", f"{{{_A_NS}}}txBody"):
                _collect_from_tx_body(elem)
        return "\n".join(paragraphs)

    for sp in root.findall(f".//{{{_P_NS}}}sp"):
        ph = sp.find(f".//{{{_P_NS}}}ph")
        if ph is not None and (ph.attrib.get("type") or "").strip().lower() == "sldnum":
            continue
        tx_body = sp.find(f"{{{_P_NS}}}txBody")
        if tx_body is not None:
            _collect_from_tx_body(tx_body)
    return "\n".join(paragraphs)


def _resolve_pptx_slide_paths(zf: zipfile.ZipFile, zf_names: set[str]) -> list[str]:
    """Resolve PPTX slide archive paths in presentation order, with numeric fallback."""
    ordered: list[str] = []
    seen: set[str] = set()
    if "ppt/presentation.xml" in zf_names:
        pres_raw = _read_zip_entry_capped(zf, "ppt/presentation.xml", _MAX_XML_BYTES)
        pres_root = _parse_xml_safe(pres_raw)
        pres_rels = _parse_relationships(
            zf, zf_names, "ppt/_rels/presentation.xml.rels", "ppt"
        )
        for sld_id in pres_root.findall(f".//{{{_P_NS}}}sldId"):
            rid = sld_id.attrib.get(f"{{{_R_NS}}}id") or ""
            target = pres_rels.get(rid)
            if target and target in zf_names and target not in seen:
                seen.add(target)
                ordered.append(target)
                if len(ordered) >= _MAX_PPTX_SLIDES:
                    return ordered

    if ordered:
        return ordered

    numbered: list[tuple[int, str]] = []
    for name in zf_names:
        match = _PPTX_SLIDE_PATH_PATTERN.fullmatch(name)
        if match:
            numbered.append((int(match.group(1)), name))
    numbered.sort(key=lambda item: item[0])
    return [name for _, name in numbered[:_MAX_PPTX_SLIDES]]


def _convert_pptx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert PPTX bytes into ordered slides, mapped speaker notes, HTML, and Markdown."""
    slides: list[tuple[int, str, str]] = []
    outline: list[DocumentOutlineItem] = []
    html_cards: list[str] = []
    md_sections: list[str] = []

    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        zf_names = set(zf.namelist())
        slide_paths = _resolve_pptx_slide_paths(zf, zf_names)

        # First pass: resolve explicit per-slide .rels notes mappings so fallback
        # never misattributes another slide's notesSlideN.xml.
        explicit_notes: list[tuple[bool, Optional[str]]] = []
        claimed_notes: set[str] = set()
        for slide_path in slide_paths:
            slide_dir = posixpath.dirname(slide_path)
            slide_name = posixpath.basename(slide_path)
            rels_path = f"{slide_dir}/_rels/{slide_name}.rels"
            has_rels = rels_path in zf_names
            mapped_note: Optional[str] = None
            if has_rels:
                note_rels = _parse_relationships(
                    zf, zf_names, rels_path, slide_dir, type_suffix="/notesslide"
                )
                for candidate in note_rels.values():
                    if candidate in zf_names:
                        mapped_note = candidate
                        claimed_notes.add(candidate)
                        break
            explicit_notes.append((has_rels, mapped_note))

        for idx, (slide_path, (has_rels, mapped_note)) in enumerate(
            zip(slide_paths, explicit_notes), start=1
        ):
            slide_raw = _read_zip_entry_capped(zf, slide_path, _MAX_XML_BYTES)
            slide_root = _parse_xml_safe(slide_raw)
            body_text = _extract_drawingml_text(slide_root)

            notes_path = mapped_note
            if notes_path is None and not has_rels:
                fallback_note = f"ppt/notesSlides/notesSlide{idx}.xml"
                if fallback_note in zf_names and fallback_note not in claimed_notes:
                    notes_path = fallback_note

            notes_text = ""
            if notes_path is not None:
                notes_raw = _read_zip_entry_capped(zf, notes_path, _MAX_XML_BYTES)
                notes_root = _parse_xml_safe(notes_raw)
                notes_text = _extract_drawingml_text(notes_root, skip_slide_num=True)

            slides.append((idx, body_text, notes_text))
            anchor_id = f"slide-{idx}"
            first_line = next((ln.strip() for ln in body_text.splitlines() if ln.strip()), "")
            outline_label = (
                f"Slide {idx}: {first_line[:60]}" if first_line else f"Slide {idx}"
            )
            outline.append(
                DocumentOutlineItem(anchor_id=anchor_id, level=1, title=outline_label)
            )

            body_html = (
                "<p>" + html.escape(body_text).replace("\n", "<br/>") + "</p>"
                if body_text
                else '<p class="muted">(Slide trống)</p>'
            )
            notes_html = (
                '<div class="slide-notes"><strong>Ghi chú:</strong> '
                + html.escape(notes_text).replace("\n", "<br/>")
                + "</div>"
                if notes_text
                else ""
            )
            html_cards.append(
                f'<section class="slide-card" id="{anchor_id}">'
                f"<h3>Slide {idx}</h3>{body_html}{notes_html}"
                "</section>"
            )

            md_parts = [f"### Slide {idx}"]
            if body_text:
                md_parts.append(body_text)
            if notes_text:
                md_parts.append(f"> **Ghi chú:** {notes_text}")
            md_sections.append("\n\n".join(md_parts))

    if not html_cards:
        html_cards.append('<p class="muted">(Bài trình bày không có slide)</p>')
    return ConvertedDocument(
        kind="pptx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_cards), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_sections),
        outline=outline,
        slides=slides,
    )


def _convert_text(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert plain-text or source-code bytes while escaping HTML output."""
    truncated = len(data) > _MAX_TEXT_BYTES
    text = _decode_text_bytes(data[:_MAX_TEXT_BYTES])
    if truncated:
        text += "\n\n... [Nội dung đã được rút gọn do vượt quá 2 MiB]"
    body_html = f'<pre class="code-view"><code>{html.escape(text)}</code></pre>'
    return ConvertedDocument(
        kind="text",
        title=title,
        html_document=_base_html_shell(title, body_html, dark_mode=dark_mode),
        markdown_text=text,
    )
