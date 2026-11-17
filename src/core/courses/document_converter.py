"""Pure-Python XSS-safe converters for DOCX, XLSX, CSV/TSV, PPTX, and text previews."""

from __future__ import annotations

import base64
import csv
import datetime as dt
import html
import io
import posixpath
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import Callable, Iterator, Literal, Optional
from xml.parsers import expat

from core.courses.models import PreviewKind

try:
    import olefile as _olefile
except Exception:  # pragma: no cover
    _olefile = None  # type: ignore[assignment]

try:
    import xlrd as _xlrd
except Exception:  # pragma: no cover
    _xlrd = None  # type: ignore[assignment]

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MAX_LEGACY_STREAM_BYTES = 32 * 1024 * 1024

_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_XML_BYTES = 48 * 1024 * 1024
_MAX_ZIP_ENTRIES = 5000
_MAX_SHARED_STRINGS = 100_000
_MAX_SHARED_STRING_CHARS = 32_768
_MAX_MEDIA_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_MEDIA_BYTES = 12 * 1024 * 1024
_MAX_MEDIA_COUNT = 50
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_MAX_CSV_BYTES = 4 * 1024 * 1024
_MAX_XLSX_SHEETS = 20
_MAX_TABLE_ROWS = 1000
_MAX_TABLE_COLS = 100
_MAX_CELL_PREVIEW_CHARS = 4096
_MAX_RENDER_CHARS = 4 * 1024 * 1024
_HTML_SHELL_OVERHEAD_RESERVE = 4096
_MAX_PPTX_SLIDES = 200
_READ_CHUNK_SIZE = 65536

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V_NS = "urn:schemas-microsoft-com:vml"
_XL_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"

_HEADING_STYLE_PATTERN = re.compile(r"^(?:heading\s*|tiêu\s*đề\s*)(\d+)$", re.IGNORECASE)
_HEADING_NEWLINE_PATTERN = re.compile(r"\s*[\r\n]+\s*")
_CELL_REF_PATTERN = re.compile(r"^([A-Za-z]+)")
_PPTX_SLIDE_PATH_PATTERN = re.compile(r"ppt/slides/slide(\d+)\.xml")
_RENDER_TRUNCATION_NOTICE = (
    "… [Nội dung đã được rút gọn do vượt quá giới hạn hiển thị 4 MiB]"
)

_SAFE_RASTER_MIMES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_CONVERSION_ERRORS = (
    zipfile.BadZipFile,
    ET.ParseError,
    csv.Error,
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
class DocxRichBlock:
    """Typed structured block preserved for rich in-app Word rendering."""

    kind: Literal["heading", "paragraph", "list_item", "table", "image"]
    text: str = ""
    level: int = 0
    anchor_id: str = ""
    rows: tuple[tuple[str, ...], ...] = ()
    image_bytes: bytes = b""
    image_name: str = ""


@dataclass(frozen=True, slots=True)
class PptxSlideRich:
    """Rich slide payload with text, notes, tables, and embedded raster media."""

    slide_number: int
    anchor_id: str
    body_text: str
    notes_text: str = ""
    images: tuple[tuple[str, bytes], ...] = ()
    tables: tuple[tuple[tuple[str, ...], ...], ...] = ()


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
    docx_blocks: tuple[DocxRichBlock, ...] = ()
    rich_slides: tuple[PptxSlideRich, ...] = ()


def convert_document_bytes(
    file_name: str,
    data: bytes,
    kind: PreviewKind,
    *,
    dark_mode: bool = False,
) -> ConvertedDocument:
    """Convert raw document bytes into escaped HTML, Markdown, and structured metadata."""
    title = file_name or "Tài liệu"
    is_ole2 = len(data) >= 8 and data[:8] == _OLE2_MAGIC
    try:
        if kind == "docx":
            if is_ole2:
                return _convert_legacy_doc(title, data, dark_mode=dark_mode)
            return _convert_docx(title, data, dark_mode=dark_mode)
        if kind == "xlsx":
            if is_ole2:
                return _convert_legacy_xls(title, data, dark_mode=dark_mode)
            return _convert_xlsx(title, data, dark_mode=dark_mode)
        if kind == "csv":
            return _convert_csv(title, data, dark_mode=dark_mode)
        if kind == "pptx":
            if is_ole2:
                return _convert_legacy_ppt(title, data, dark_mode=dark_mode)
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
        "vertical-align:top;white-space:pre-wrap;word-break:break-word;}"
        "thead th{background:var(--th-bg);font-weight:600;}"
        ".table-wrap.sheet .sheet-table th,.table-wrap.sheet .sheet-table td{"
        "white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis;}"
        ".table-wrap.sheet .sheet-table thead th{position: sticky;top:0;z-index:2;}"
        ".table-wrap.sheet .sheet-table th:first-child,.table-wrap.sheet .sheet-table td:first-child{"
        "position: sticky;left:0;z-index:1;background:var(--surface);}"
        ".table-wrap.sheet .sheet-table thead th:first-child{z-index:3;background:var(--th-bg);}"
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


@dataclass
class _RenderBudget:
    """Mutable HTML and Markdown character budgets for a single conversion."""

    html_remaining: int
    md_remaining: int
    truncated: bool = False

    @classmethod
    def for_title(cls, title: str) -> "_RenderBudget":
        title_overhead = len(html.escape(title))
        notice_overhead = len(_RENDER_TRUNCATION_NOTICE) + 32
        return cls(
            html_remaining=max(
                0,
                _MAX_RENDER_CHARS - _HTML_SHELL_OVERHEAD_RESERVE - title_overhead,
            ),
            md_remaining=max(0, _MAX_RENDER_CHARS - notice_overhead),
        )

    def try_consume(self, html_len: int, md_len: int) -> bool:
        """Reserve character budget for HTML and Markdown fragments if both fit."""
        if self.truncated:
            return False
        if html_len > self.html_remaining or md_len > self.md_remaining:
            self.truncated = True
            return False
        self.html_remaining -= html_len
        self.md_remaining -= md_len
        return True


def _truncate_preview_text(
    text: str, max_chars: int = _MAX_CELL_PREVIEW_CHARS
) -> str:
    """Truncate overly long cell or block text with an ellipsis."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _append_render_truncation_notice(
    html_blocks: list[str], md_blocks: list[str]
) -> None:
    """Append an escaped HTML and Markdown truncation notice when budget is hit."""
    html_blocks.append(f'<p class="notice">{html.escape(_RENDER_TRUNCATION_NOTICE)}</p>')
    md_blocks.append(_RENDER_TRUNCATION_NOTICE)


def _open_ooxml_zip(data: bytes) -> zipfile.ZipFile:
    """Open an OOXML ZIP archive while enforcing the maximum entry count limit."""
    zf = zipfile.ZipFile(io.BytesIO(data), "r")
    try:
        if len(zf.infolist()) > _MAX_ZIP_ENTRIES:
            raise ValueError(f"ZIP archive exceeds {_MAX_ZIP_ENTRIES} entry limit")
    except BaseException:
        zf.close()
        raise
    return zf


def _read_zip_entry_capped(
    zf: zipfile.ZipFile,
    name: str,
    max_bytes: int,
    *,
    xml_budget: Optional[list[int]] = None,
) -> bytes:
    """Read a ZIP member incrementally, enforcing entry and cumulative byte caps."""
    info = zf.getinfo(name)
    effective_max = min(max_bytes, xml_budget[0]) if xml_budget is not None else max_bytes
    buf = bytearray()
    with zf.open(info, "r") as stream:
        while True:
            chunk = stream.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > effective_max:
                raise ValueError(
                    f"ZIP entry exceeds {effective_max} byte limit: {name}"
                )
    if xml_budget is not None:
        xml_budget[0] -= len(buf)
    return bytes(buf)


def _parse_xml_safe(raw: bytes) -> ET.Element:
    """Parse XML bytes via Expat while rejecting DTD and entity declarations."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(None, "}")
    parser.buffer_text = True
    names: dict[str, str] = {}

    def _fix_name(name: str) -> str:
        fixed = names.get(name)
        if fixed is None:
            fixed = ("{" + name) if "}" in name else name
            names[name] = fixed
        return fixed

    def _reject_dtd(*_args: object, **_kwargs: object) -> None:
        raise ValueError("XML DTD/entity declarations are forbidden")

    def _start_element(tag: str, attrib: dict[str, str]) -> None:
        fixed_attrib = (
            {_fix_name(key): value for key, value in attrib.items()} if attrib else {}
        )
        builder.start(_fix_name(tag), fixed_attrib)

    def _end_element(tag: str) -> None:
        builder.end(_fix_name(tag))

    parser.StartDoctypeDeclHandler = _reject_dtd
    parser.EntityDeclHandler = _reject_dtd
    parser.UnparsedEntityDeclHandler = _reject_dtd
    parser.ExternalEntityRefHandler = _reject_dtd
    parser.StartElementHandler = _start_element
    parser.EndElementHandler = _end_element
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(raw, True)
    except expat.ExpatError as exc:
        raise ET.ParseError(str(exc)) from exc
    return builder.close()


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
    xml_budget: Optional[list[int]] = None,
) -> dict[str, str]:
    """Parse an OOXML .rels file into a safe {rId: archive_member_path} map."""
    if rels_path not in zf_names:
        return {}
    raw = _read_zip_entry_capped(
        zf, rels_path, _MAX_XML_BYTES, xml_budget=xml_budget
    )
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


def _decode_text_bytes(raw: bytes, *, truncated: bool = False) -> str:
    """Decode text bytes using UTF-8-SIG, trimming truncated UTF-8 tails before CP1258."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if (
            truncated
            and exc.reason == "unexpected end of data"
            and exc.end == len(exc.object)
            and exc.start >= len(exc.object) - 3
        ):
            try:
                return exc.object[: exc.start].decode("utf-8")
            except UnicodeDecodeError:
                pass
        try:
            return unicodedata.normalize("NFC", raw.decode("cp1258"))
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig", errors="replace")


def _escape_md_cell(text: str) -> str:
    """Escape cell content for single-line Markdown pipe tables."""
    single_line = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return single_line.replace("|", "\\|")


def _render_table_html_and_markdown(
    rows: list[list[str]],
    *,
    sheet_style: bool = False,
    budget: Optional[_RenderBudget] = None,
) -> tuple[str, str]:
    """Render rectangular table HTML and Markdown pipe table within render budgets."""
    active_budget = budget if budget is not None else _RenderBudget.for_title("")
    if not rows:
        empty_html = '<p class="muted">(Bảng trống)</p>'
        if not active_budget.try_consume(len(empty_html) + 1, 0):
            return "", ""
        return empty_html, ""
    capped_rows = rows[:_MAX_TABLE_ROWS]
    col_count = min(_MAX_TABLE_COLS, max((len(row) for row in capped_rows), default=0))
    if col_count == 0:
        empty_html = '<p class="muted">(Bảng trống)</p>'
        if not active_budget.try_consume(len(empty_html) + 1, 0):
            return "", ""
        return empty_html, ""

    wrap_cls = "table-wrap sheet" if sheet_style else "table-wrap"
    table_open = (
        f'<div class="{wrap_cls}"><table class="sheet-table">'
        if sheet_style
        else f'<div class="{wrap_cls}"><table>'
    )
    table_close = "</table></div>"

    header_row = [
        _truncate_preview_text(capped_rows[0][idx] if idx < len(capped_rows[0]) else "")
        for idx in range(col_count)
    ]
    header_cells = "".join(f"<th>{html.escape(cell)}</th>" for cell in header_row)
    thead_html = f"<thead><tr>{header_cells}</tr></thead>"
    md_header = "| " + " | ".join(_escape_md_cell(cell) for cell in header_row) + " |"
    md_sep = "| " + " | ".join("---" for _ in range(col_count)) + " |"

    fixed_html_len = (
        len(table_open) + len(thead_html) + len("<tbody></tbody>") + len(table_close)
    )
    fixed_md_len = len(md_header) + 1 + len(md_sep)
    if not active_budget.try_consume(fixed_html_len, fixed_md_len):
        return "", ""

    body_rows_html: list[str] = []
    md_lines = [md_header, md_sep]
    for row in capped_rows[1:]:
        cells = [
            _truncate_preview_text(row[idx] if idx < len(row) else "")
            for idx in range(col_count)
        ]
        row_html = (
            "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells) + "</tr>"
        )
        row_md = "| " + " | ".join(_escape_md_cell(cell) for cell in cells) + " |"
        if not active_budget.try_consume(len(row_html), len(row_md) + 1):
            break
        body_rows_html.append(row_html)
        md_lines.append(row_md)

    tbody_html = f"<tbody>{''.join(body_rows_html)}</tbody>" if body_rows_html else ""
    table_html = f"{table_open}{thead_html}{tbody_html}{table_close}"
    return table_html, "\n".join(md_lines)


def _is_valid_raster_magic(ext: str, raw: bytes) -> bool:
    """Validate raster image magic bytes matching the archive file extension."""
    if ext == ".png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in {".jpg", ".jpeg"}:
        return raw.startswith(b"\xff\xd8\xff")
    if ext == ".gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if ext == ".webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    if ext == ".bmp":
        return raw.startswith(b"BM")
    return False


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
        if raw_lvl.isascii() and raw_lvl.isdigit():
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


def _append_bounded_text_block(
    html_blocks: list[str],
    md_blocks: list[str],
    budget: _RenderBudget,
    *,
    raw_text: str,
    build_html: Callable[[str], str],
    build_md: Callable[[str], str],
) -> tuple[bool, bool]:
    """Append a text block within render budget. Returns (was_appended, can_continue)."""
    if budget.truncated:
        return False, False
    max_raw = min(budget.html_remaining, budget.md_remaining)
    candidate = raw_text
    pre_truncated = False
    if len(candidate) > max_raw:
        if html_blocks:
            budget.truncated = True
            return False, False
        limit = max(1, min(budget.html_remaining, budget.md_remaining) // 6)
        candidate = _truncate_preview_text(candidate, limit)
        pre_truncated = True

    block_html = build_html(candidate)
    block_md = build_md(candidate)
    if not budget.try_consume(len(block_html) + 1, len(block_md) + 2):
        if html_blocks or pre_truncated:
            return False, False
        limit = max(1, min(budget.html_remaining, budget.md_remaining) // 6)
        candidate = _truncate_preview_text(raw_text, limit)
        block_html = build_html(candidate)
        block_md = build_md(candidate)
        budget.html_remaining = max(0, budget.html_remaining - len(block_html) - 1)
        budget.md_remaining = max(0, budget.md_remaining - len(block_md) - 2)
        budget.truncated = True
    html_blocks.append(block_html)
    md_blocks.append(block_md)
    if pre_truncated:
        budget.truncated = True
    return True, not budget.truncated


def _convert_docx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert DOCX bytes into outline, Markdown, rich blocks, and XSS-safe HTML."""
    outline: list[DocumentOutlineItem] = []
    docx_blocks: list[DocxRichBlock] = []
    html_blocks: list[str] = []
    md_blocks: list[str] = []
    rendered_media: set[str] = set()
    media_state = {"count": 0, "bytes": 0}
    xml_budget = [_MAX_TOTAL_XML_BYTES]
    render_budget = _RenderBudget.for_title(title)

    with _open_ooxml_zip(data) as zf:
        zf_names = set(zf.namelist())
        doc_raw = _read_zip_entry_capped(
            zf, "word/document.xml", _MAX_XML_BYTES, xml_budget=xml_budget
        )
        doc_root = _parse_xml_safe(doc_raw)
        rels = _parse_relationships(
            zf,
            zf_names,
            "word/_rels/document.xml.rels",
            "word",
            xml_budget=xml_budget,
        )

        def _try_embed_media(zip_path: str) -> Optional[tuple[str, str, bytes]]:
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
            if not raw_img or not _is_valid_raster_magic(ext, raw_img):
                return None
            if media_state["bytes"] + len(raw_img) > _MAX_TOTAL_MEDIA_BYTES:
                return None
            rendered_media.add(zip_path)
            media_state["count"] += 1
            media_state["bytes"] += len(raw_img)
            encoded = base64.b64encode(raw_img).decode("ascii")
            img_name = posixpath.basename(zip_path)
            alt = html.escape(img_name)
            img_html = (
                '<figure class="doc-figure">'
                f'<img class="doc-image" src="data:{mime};base64,{encoded}" alt="{alt}"/>'
                "</figure>"
            )
            return (img_html, img_name, raw_img)

        body = doc_root.find(f"{{{_W_NS}}}body")
        if body is not None:
            for block in _iter_docx_body_blocks(body):
                if render_budget.truncated:
                    break
                if block.tag == f"{{{_W_NS}}}p":
                    level = _docx_heading_level(block)
                    text = _docx_paragraph_text(block).strip()
                    if level is not None and text:
                        anchor_id = f"sec-{len(outline) + 1}"
                        appended, can_continue = _append_bounded_text_block(
                            html_blocks,
                            md_blocks,
                            render_budget,
                            raw_text=text,
                            build_html=lambda t, lv=level, aid=anchor_id: (
                                f'<h{lv} id="{aid}">{html.escape(t)}</h{lv}>'
                            ),
                            build_md=lambda t, lv=level: (
                                f"{'#' * lv} "
                                f"{_HEADING_NEWLINE_PATTERN.sub(' ', t).strip()}"
                            ),
                        )
                        if appended:
                            outline.append(
                                DocumentOutlineItem(
                                    anchor_id=anchor_id, level=level, title=text
                                )
                            )
                            docx_blocks.append(
                                DocxRichBlock(
                                    kind="heading",
                                    text=text,
                                    level=level,
                                    anchor_id=anchor_id,
                                )
                            )
                        if not can_continue:
                            break
                    elif text:
                        p_pr = block.find(f"{{{_W_NS}}}pPr")
                        is_list = (
                            p_pr is not None
                            and p_pr.find(f"{{{_W_NS}}}numPr") is not None
                        )
                        appended, can_continue = _append_bounded_text_block(
                            html_blocks,
                            md_blocks,
                            render_budget,
                            raw_text=text,
                            build_html=lambda t: (
                                f"<p>{html.escape(t).replace(chr(10), '<br/>')}</p>"
                            ),
                            build_md=lambda t, lst=is_list: f"- {t}" if lst else t,
                        )
                        if appended:
                            docx_blocks.append(
                                DocxRichBlock(
                                    kind="list_item" if is_list else "paragraph",
                                    text=text,
                                )
                            )
                        if not can_continue:
                            break

                    for rid in _docx_paragraph_image_rids(block):
                        target = rels.get(rid)
                        if target:
                            media_res = _try_embed_media(target)
                            if media_res is not None:
                                img_html, img_name, raw_img = media_res
                                html_blocks.append(img_html)
                                docx_blocks.append(
                                    DocxRichBlock(
                                        kind="image",
                                        image_bytes=raw_img,
                                        image_name=img_name,
                                    )
                                )

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
                    tbl_html, tbl_md = _render_table_html_and_markdown(
                        table_rows, sheet_style=False, budget=render_budget
                    )
                    if tbl_html:
                        html_blocks.append(tbl_html)
                        docx_blocks.append(
                            DocxRichBlock(
                                kind="table",
                                rows=tuple(tuple(r) for r in table_rows),
                            )
                        )
                    if tbl_md:
                        md_blocks.append(tbl_md)
                    if render_budget.truncated:
                        break

        if not render_budget.truncated:
            for name in sorted(zf_names):
                if name.startswith("word/media/") and name not in rendered_media:
                    media_res = _try_embed_media(name)
                    if media_res is not None:
                        img_html, img_name, raw_img = media_res
                        html_blocks.append(img_html)
                        docx_blocks.append(
                            DocxRichBlock(
                                kind="image",
                                image_bytes=raw_img,
                                image_name=img_name,
                            )
                        )

    if render_budget.truncated:
        _append_render_truncation_notice(html_blocks, md_blocks)
    if not html_blocks:
        html_blocks.append('<p class="muted">(Tài liệu trống)</p>')
    return ConvertedDocument(
        kind="docx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_blocks), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_blocks),
        outline=outline,
        docx_blocks=tuple(docx_blocks),
    )


def _extract_xlsx_text_container(
    container: ET.Element, *, max_chars: Optional[int] = None
) -> str:
    """Extract text from an XLSX <si> or <is> element, skipping <rPh> phonetic runs."""
    direct_t = container.find(f"{{{_XL_NS}}}t")
    if direct_t is not None and direct_t.text is not None:
        return (
            direct_t.text[:max_chars]
            if max_chars is not None and len(direct_t.text) > max_chars
            else direct_t.text
        )
    parts: list[str] = []
    total_chars = 0
    for run in container.findall(f"{{{_XL_NS}}}r"):
        t_el = run.find(f"{{{_XL_NS}}}t")
        if t_el is not None and t_el.text:
            if max_chars is not None and total_chars + len(t_el.text) > max_chars:
                remaining = max_chars - total_chars
                if remaining > 0:
                    parts.append(t_el.text[:remaining])
                break
            parts.append(t_el.text)
            total_chars += len(t_el.text)
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
            if raw_idx.isascii() and raw_idx.isdigit():
                idx = int(raw_idx)
                if 0 <= idx < len(shared_strings):
                    return shared_strings[idx]
        return ""
    if cell_type == "inlineStr":
        return (
            _extract_xlsx_text_container(is_el, max_chars=_MAX_SHARED_STRING_CHARS)
            if is_el is not None
            else ""
        )
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
        return _extract_xlsx_text_container(is_el, max_chars=_MAX_SHARED_STRING_CHARS)
    return ""


def _convert_xlsx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert XLSX bytes into bounded sheets, sticky HTML tables, and Markdown."""
    sheets: list[SpreadsheetSheet] = []
    outline: list[DocumentOutlineItem] = []
    html_sections: list[str] = []
    md_sections: list[str] = []
    xml_budget = [_MAX_TOTAL_XML_BYTES]
    render_budget = _RenderBudget.for_title(title)

    with _open_ooxml_zip(data) as zf:
        zf_names = set(zf.namelist())
        wb_raw = _read_zip_entry_capped(
            zf, "xl/workbook.xml", _MAX_XML_BYTES, xml_budget=xml_budget
        )
        wb_root = _parse_xml_safe(wb_raw)

        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in zf_names:
            sst_raw = _read_zip_entry_capped(
                zf, "xl/sharedStrings.xml", _MAX_XML_BYTES, xml_budget=xml_budget
            )
            sst_root = _parse_xml_safe(sst_raw)
            for si in sst_root.findall(f"{{{_XL_NS}}}si"):
                if len(shared_strings) >= _MAX_SHARED_STRINGS:
                    break
                shared_strings.append(
                    _extract_xlsx_text_container(
                        si, max_chars=_MAX_SHARED_STRING_CHARS
                    )
                )

        wb_rels_path = "xl/_rels/workbook.xml.rels"
        has_wb_rels = wb_rels_path in zf_names
        rels = _parse_relationships(
            zf, zf_names, wb_rels_path, "xl", xml_budget=xml_budget
        )

        sheet_els = wb_root.findall(f".//{{{_XL_NS}}}sheet")[:_MAX_XLSX_SHEETS]
        for idx, sheet_el in enumerate(sheet_els):
            if render_budget.truncated:
                break
            sheet_name = sheet_el.attrib.get("name") or f"Sheet {idx + 1}"
            rid = (sheet_el.attrib.get(f"{{{_R_NS}}}id") or "").strip()
            sheet_path = rels.get(rid) if rid else None
            if sheet_path is None and (not has_wb_rels or not rid):
                sheet_path = f"xl/worksheets/sheet{idx + 1}.xml"
            if not sheet_path or sheet_path not in zf_names:
                continue

            ws_raw = _read_zip_entry_capped(
                zf, sheet_path, _MAX_XML_BYTES, xml_budget=xml_budget
            )
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

            anchor_id = f"sheet-{idx + 1}"
            header_html = f'<section><h2 id="{anchor_id}">{html.escape(sheet_name)}</h2>'
            footer_html = "</section>"
            header_md = f"## {sheet_name}\n\n"
            if not render_budget.try_consume(
                len(header_html) + len(footer_html) + 1, len(header_md) + 2
            ):
                break

            tbl_html, tbl_md = _render_table_html_and_markdown(
                rows, sheet_style=True, budget=render_budget
            )
            if not tbl_html:
                break

            sheets.append(SpreadsheetSheet(name=sheet_name, rows=rows))
            outline.append(
                DocumentOutlineItem(anchor_id=anchor_id, level=1, title=sheet_name)
            )
            html_sections.append(f"{header_html}{tbl_html}{footer_html}")
            md_sections.append(
                f"## {sheet_name}\n\n{tbl_md}" if tbl_md else f"## {sheet_name}"
            )
            if render_budget.truncated:
                break

    if render_budget.truncated:
        _append_render_truncation_notice(html_sections, md_sections)
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
    truncated_bytes = len(data) > _MAX_CSV_BYTES
    text = _decode_text_bytes(data[:_MAX_CSV_BYTES], truncated=truncated_bytes)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    rows: list[list[str]] = []
    for row in reader:
        if len(rows) >= _MAX_TABLE_ROWS:
            break
        rows.append(row[:_MAX_TABLE_COLS])

    sheet_name = posixpath.basename(title.replace("\\", "/")) or "CSV"
    sheet = SpreadsheetSheet(name=sheet_name, rows=rows)
    render_budget = _RenderBudget.for_title(title)
    tbl_html, tbl_md = _render_table_html_and_markdown(
        rows, sheet_style=True, budget=render_budget
    )
    html_parts = [tbl_html] if tbl_html else []
    md_parts = [tbl_md] if tbl_md else []
    if render_budget.truncated:
        _append_render_truncation_notice(html_parts, md_parts)
    return ConvertedDocument(
        kind="csv",
        title=title,
        html_document=_base_html_shell(
            title, "\n".join(html_parts), dark_mode=dark_mode
        ),
        markdown_text="\n\n".join(md_parts),
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


def _resolve_pptx_slide_paths(
    zf: zipfile.ZipFile, zf_names: set[str], *, xml_budget: list[int]
) -> list[str]:
    """Resolve PPTX slide archive paths in presentation order, with numeric fallback."""
    ordered: list[str] = []
    seen: set[str] = set()
    if "ppt/presentation.xml" in zf_names:
        pres_raw = _read_zip_entry_capped(
            zf, "ppt/presentation.xml", _MAX_XML_BYTES, xml_budget=xml_budget
        )
        pres_root = _parse_xml_safe(pres_raw)
        pres_rels = _parse_relationships(
            zf,
            zf_names,
            "ppt/_rels/presentation.xml.rels",
            "ppt",
            xml_budget=xml_budget,
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


def _format_pptx_notes_markdown(notes_text: str) -> str:
    """Format single- or multi-line PPTX speaker notes as a Markdown blockquote."""
    lines = notes_text.splitlines()
    if not lines:
        return ""
    formatted = [f"> **Ghi chú:** {lines[0]}"]
    for line in lines[1:]:
        formatted.append(f"> {line}" if line else ">")
    return "\n".join(formatted)


def _build_pptx_slide_rendered(
    idx: int, anchor_id: str, body_text: str, notes_text: str
) -> tuple[str, str]:
    """Render HTML card and Markdown section for a single PPTX slide."""
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
    card_html = (
        f'<section class="slide-card" id="{anchor_id}">'
        f"<h3>Slide {idx}</h3>{body_html}{notes_html}"
        "</section>"
    )
    md_parts = [f"### Slide {idx}"]
    if body_text:
        md_parts.append(body_text)
    if notes_text:
        md_parts.append(_format_pptx_notes_markdown(notes_text))
    return card_html, "\n\n".join(md_parts)


def _render_bounded_pptx_card(
    idx: int,
    anchor_id: str,
    body_text: str,
    notes_text: str,
    render_budget: _RenderBudget,
    *,
    has_prior_cards: bool,
) -> Optional[tuple[str, str, bool]]:
    """Build a PPTX slide card within render budget, returning (html, md, did_truncate)."""
    raw_limit = min(render_budget.html_remaining, render_budget.md_remaining)
    pre_truncated = False
    rendered_body = body_text
    rendered_notes = notes_text
    if len(rendered_body) > raw_limit or len(rendered_notes) > raw_limit:
        if has_prior_cards:
            render_budget.truncated = True
            return None
        half_limit = max(1, raw_limit // 12)
        rendered_body = _truncate_preview_text(rendered_body, half_limit)
        rendered_notes = _truncate_preview_text(rendered_notes, half_limit)
        pre_truncated = True

    card_html, card_md = _build_pptx_slide_rendered(
        idx, anchor_id, rendered_body, rendered_notes
    )
    if render_budget.try_consume(len(card_html) + 1, len(card_md) + 2):
        if pre_truncated:
            render_budget.truncated = True
        return card_html, card_md, pre_truncated

    if has_prior_cards or pre_truncated:
        return None

    half_limit = max(1, raw_limit // 12)
    card_html, card_md = _build_pptx_slide_rendered(
        idx,
        anchor_id,
        _truncate_preview_text(body_text, half_limit),
        _truncate_preview_text(notes_text, half_limit),
    )
    render_budget.html_remaining = max(
        0, render_budget.html_remaining - len(card_html) - 1
    )
    render_budget.md_remaining = max(
        0, render_budget.md_remaining - len(card_md) - 2
    )
    render_budget.truncated = True
    return card_html, card_md, True


def _extract_pptx_tables(slide_root: ET.Element) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Extract DrawingML ``<a:tbl>`` grids from a slide XML tree."""
    tables: list[tuple[tuple[str, ...], ...]] = []
    for tbl in slide_root.findall(f".//{{{_A_NS}}}tbl"):
        rows: list[tuple[str, ...]] = []
        for tr in tbl.findall(f"{{{_A_NS}}}tr")[:_MAX_TABLE_ROWS]:
            cells: list[str] = []
            for tc in tr.findall(f"{{{_A_NS}}}tc")[:_MAX_TABLE_COLS]:
                tx_body = tc.find(f"{{{_A_NS}}}txBody")
                if tx_body is None:
                    cells.append("")
                    continue
                paras: list[str] = []
                for p in tx_body.findall(f"{{{_A_NS}}}p"):
                    runs = [t.text or "" for t in p.findall(f".//{{{_A_NS}}}t")]
                    joined = "".join(runs).strip()
                    if joined:
                        paras.append(joined)
                cells.append("\n".join(paras))
            if any(c for c in cells):
                rows.append(tuple(cells))
        if rows:
            tables.append(tuple(rows))
    return tuple(tables)


def _convert_pptx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert PPTX bytes into ordered slides, mapped speaker notes, rich blocks, HTML, and Markdown."""
    slides: list[tuple[int, str, str]] = []
    rich_slides: list[PptxSlideRich] = []
    outline: list[DocumentOutlineItem] = []
    html_cards: list[str] = []
    md_sections: list[str] = []
    media_state = {"count": 0, "bytes": 0}
    media_cache: dict[str, bytes] = {}
    xml_budget = [_MAX_TOTAL_XML_BYTES]
    render_budget = _RenderBudget.for_title(title)

    with _open_ooxml_zip(data) as zf:
        zf_names = set(zf.namelist())
        slide_paths = _resolve_pptx_slide_paths(zf, zf_names, xml_budget=xml_budget)

        # First pass: resolve explicit per-slide .rels notes mappings and filename-matched
        # fallback notes so positional notesSlide{idx}.xml never steals another slide's note.
        explicit_notes: list[tuple[bool, Optional[str]]] = []
        claimed_notes: set[str] = set()
        slide_media_targets: list[list[str]] = []
        for slide_path in slide_paths:
            slide_dir = posixpath.dirname(slide_path)
            slide_name = posixpath.basename(slide_path)
            rels_path = f"{slide_dir}/_rels/{slide_name}.rels"
            has_rels = rels_path in zf_names
            mapped_note: Optional[str] = None
            media_targets: list[str] = []
            if has_rels:
                note_rels = _parse_relationships(
                    zf,
                    zf_names,
                    rels_path,
                    slide_dir,
                    type_suffix="/notesslide",
                    xml_budget=xml_budget,
                )
                for candidate in note_rels.values():
                    if candidate in zf_names:
                        mapped_note = candidate
                        claimed_notes.add(candidate)
                        break
                img_rels = _parse_relationships(
                    zf,
                    zf_names,
                    rels_path,
                    slide_dir,
                    type_suffix="/image",
                    xml_budget=xml_budget,
                )
                for target in img_rels.values():
                    if target in zf_names and target not in media_targets:
                        media_targets.append(target)
            explicit_notes.append((has_rels, mapped_note))
            slide_media_targets.append(media_targets)

        resolved_notes: list[Optional[str]] = []
        for slide_path, (has_rels, mapped_note) in zip(slide_paths, explicit_notes):
            note_path = mapped_note
            if note_path is None and not has_rels:
                slide_match = _PPTX_SLIDE_PATH_PATTERN.fullmatch(slide_path)
                if slide_match:
                    file_note = f"ppt/notesSlides/notesSlide{int(slide_match.group(1))}.xml"
                    if file_note in zf_names and file_note not in claimed_notes:
                        note_path = file_note
                        claimed_notes.add(file_note)
            resolved_notes.append(note_path)

        for idx, (slide_path, (has_rels, _), pre_resolved_note, media_targets) in enumerate(
            zip(slide_paths, explicit_notes, resolved_notes, slide_media_targets), start=1
        ):
            if render_budget.truncated:
                break
            slide_raw = _read_zip_entry_capped(
                zf, slide_path, _MAX_XML_BYTES, xml_budget=xml_budget
            )
            slide_root = _parse_xml_safe(slide_raw)
            body_text = _extract_drawingml_text(slide_root)
            slide_tables = _extract_pptx_tables(slide_root)

            slide_images: list[tuple[str, bytes]] = []
            for target in media_targets:
                ext = posixpath.splitext(target.lower())[1]
                if ext not in _SAFE_RASTER_MIMES:
                    continue
                if target in media_cache:
                    slide_images.append((posixpath.basename(target), media_cache[target]))
                    continue
                if media_state["count"] >= _MAX_MEDIA_COUNT:
                    continue
                try:
                    raw_img = _read_zip_entry_capped(zf, target, _MAX_MEDIA_BYTES)
                except _CONVERSION_ERRORS:
                    continue
                if not raw_img or not _is_valid_raster_magic(ext, raw_img):
                    continue
                if media_state["bytes"] + len(raw_img) > _MAX_TOTAL_MEDIA_BYTES:
                    continue
                media_state["count"] += 1
                media_state["bytes"] += len(raw_img)
                media_cache[target] = raw_img
                slide_images.append((posixpath.basename(target), raw_img))

            notes_path = pre_resolved_note
            if notes_path is None and not has_rels:
                idx_note = f"ppt/notesSlides/notesSlide{idx}.xml"
                if idx_note in zf_names and idx_note not in claimed_notes:
                    notes_path = idx_note
                    claimed_notes.add(idx_note)

            notes_text = ""
            if notes_path is not None:
                notes_raw = _read_zip_entry_capped(
                    zf, notes_path, _MAX_XML_BYTES, xml_budget=xml_budget
                )
                notes_root = _parse_xml_safe(notes_raw)
                notes_text = _extract_drawingml_text(notes_root, skip_slide_num=True)

            anchor_id = f"slide-{idx}"
            rendered = _render_bounded_pptx_card(
                idx,
                anchor_id,
                body_text,
                notes_text,
                render_budget,
                has_prior_cards=bool(html_cards),
            )
            if rendered is None:
                break
            card_html, card_md, stop_after = rendered

            slides.append((idx, body_text, notes_text))
            rich_slides.append(
                PptxSlideRich(
                    slide_number=idx,
                    anchor_id=anchor_id,
                    body_text=body_text,
                    notes_text=notes_text,
                    images=tuple(slide_images),
                    tables=slide_tables,
                )
            )
            first_line = next(
                (ln.strip() for ln in body_text.splitlines() if ln.strip()), ""
            )
            outline_label = (
                f"Slide {idx}: {first_line[:60]}" if first_line else f"Slide {idx}"
            )
            outline.append(
                DocumentOutlineItem(anchor_id=anchor_id, level=1, title=outline_label)
            )
            html_cards.append(card_html)
            md_sections.append(card_md)
            if stop_after:
                break

    if render_budget.truncated:
        _append_render_truncation_notice(html_cards, md_sections)
    if not html_cards:
        html_cards.append('<p class="muted">(Bài trình bày không có slide)</p>')
    return ConvertedDocument(
        kind="pptx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_cards), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_sections),
        outline=outline,
        slides=slides,
        rich_slides=tuple(rich_slides),
    )


def _convert_text(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert plain-text or source-code bytes while escaping HTML output."""
    truncated = len(data) > _MAX_TEXT_BYTES
    text = _decode_text_bytes(data[:_MAX_TEXT_BYTES], truncated=truncated)
    if truncated:
        text += "\n\n... [Nội dung đã được rút gọn do vượt quá 2 MiB]"
    body_html = f'<pre class="code-view"><code>{html.escape(text)}</code></pre>'
    return ConvertedDocument(
        kind="text",
        title=title,
        html_document=_base_html_shell(title, body_html, dark_mode=dark_mode),
        markdown_text=text,
    )


def _format_xls_cell(sheet: object, row_idx: int, col_idx: int, datemode: int) -> str:
    assert _xlrd is not None
    cell = sheet.cell(row_idx, col_idx)  # type: ignore[attr-defined]
    ctype = cell.ctype
    val = cell.value
    if ctype == _xlrd.XL_CELL_EMPTY or val is None:
        return ""
    if ctype == _xlrd.XL_CELL_TEXT:
        return str(val).strip()
    if ctype == _xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if val else "FALSE"
    if ctype == _xlrd.XL_CELL_DATE:
        try:
            dval = _xlrd.xldate.xldate_as_datetime(val, datemode)
            if isinstance(dval, dt.datetime) and dval.hour == 0 and dval.minute == 0 and dval.second == 0:
                return dval.strftime("%Y-%m-%d")
            return dval.isoformat(sep=" ", timespec="seconds")
        except Exception:
            return str(val)
    if ctype == _xlrd.XL_CELL_NUMBER:
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        return str(val)
    return ""


def _convert_legacy_xls(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert legacy binary ``.xls`` spreadsheets using ``xlrd``."""
    if _xlrd is None:
        return _build_notice_document(
            "xlsx",
            title,
            "Thiếu thư viện xlrd để đọc định dạng .xls cũ.",
            dark_mode=dark_mode,
        )
    try:
        workbook = _xlrd.open_workbook(file_contents=data, on_demand=True)
    except Exception as exc:
        err_lower = str(exc).lower()
        if "encrypt" in err_lower or "password" in err_lower:
            return _build_notice_document(
                "xlsx", title, "Bảng tính .xls được bảo vệ bằng mật khẩu.", dark_mode=dark_mode
            )
        return _build_notice_document(
            "xlsx", title, "Không thể đọc tệp Excel .xls cũ.", dark_mode=dark_mode
        )

    sheets: list[SpreadsheetSheet] = []
    outline: list[DocumentOutlineItem] = []
    html_sections: list[str] = []
    md_sections: list[str] = []
    render_budget = _RenderBudget.for_title(title)

    try:
        for sheet_idx, sheet_name in enumerate(workbook.sheet_names()[:_MAX_XLSX_SHEETS], start=1):
            if render_budget.truncated:
                break
            xl_sheet = workbook.sheet_by_name(sheet_name)
            row_limit = min(xl_sheet.nrows, _MAX_TABLE_ROWS)
            col_limit = min(xl_sheet.ncols, _MAX_TABLE_COLS)
            rows: list[list[str]] = []
            for r_idx in range(row_limit):
                row_vals = [
                    _format_xls_cell(xl_sheet, r_idx, c_idx, workbook.datemode)
                    for c_idx in range(col_limit)
                ]
                while row_vals and row_vals[-1] == "":
                    row_vals.pop()
                if any(v for v in row_vals):
                    rows.append(row_vals)
            sheets.append(SpreadsheetSheet(name=sheet_name, rows=rows))
            anchor_id = f"sheet-{sheet_idx}"
            outline.append(DocumentOutlineItem(anchor_id=anchor_id, level=1, title=sheet_name))
            tbl_html, tbl_md = _render_table_html_and_markdown(
                rows, sheet_style=True, budget=render_budget
            )
            html_sections.append(
                f'<section id="{anchor_id}"><h2>{html.escape(sheet_name)}</h2>{tbl_html}</section>'
            )
            md_sections.append(f"## {sheet_name}\n\n{tbl_md}" if tbl_md else f"## {sheet_name}")
    finally:
        try:
            workbook.release_resources()
        except Exception:
            pass

    if not html_sections:
        html_sections.append('<p class="muted">(Bảng tính trống)</p>')
    return ConvertedDocument(
        kind="xlsx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_sections), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_sections),
        outline=outline,
        sheets=sheets,
    )


def _strip_doc_field_controls(raw_text: str) -> str:
    """Clean Word binary field markers (\\x13..\\\\x14..\\\\x15) and control chars."""
    out: list[str] = []
    field_depth = 0
    in_result = True
    for ch in raw_text:
        code = ord(ch)
        if code == 0x13:
            field_depth += 1
            in_result = False
            continue
        if code == 0x14 and field_depth > 0:
            in_result = True
            continue
        if code == 0x15 and field_depth > 0:
            field_depth = max(0, field_depth - 1)
            in_result = field_depth == 0
            continue
        if field_depth > 0 and not in_result:
            continue
        if ch in ("\r", "\x07"):
            out.append("\n")
        elif code in (0x0B, 0x0C):
            out.append("\n")
        elif code == 0x09 or code >= 32:
            out.append(ch)
    return "".join(out)


def _extract_doc_clx_text(wd: bytes, tbl: bytes, flags: int) -> Optional[str]:
    """Decode Word97+ Clx piece table text stream when valid, else return None."""
    if len(wd) < 426:
        return None
    n_fib = struct.unpack_from("<H", wd, 2)[0]
    if n_fib < 0x00C0:
        fc_min, fc_mac = struct.unpack_from("<II", wd, 0x18)
        if 0 <= fc_min < fc_mac <= len(wd):
            return wd[fc_min:fc_mac].decode("cp1258", errors="replace")
        return None
    ccp_text = struct.unpack_from("<I", wd, 76)[0]
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, 418)
    if lcb_clx == 0 or fc_clx + lcb_clx > len(tbl):
        return None
    clx = tbl[fc_clx : fc_clx + lcb_clx]
    pos = 0
    while pos < len(clx):
        clxt = clx[pos]
        if clxt == 0x01:
            if pos + 3 > len(clx):
                return None
            cb_grpprl = struct.unpack_from("<H", clx, pos + 1)[0]
            pos += 3 + cb_grpprl
            continue
        if clxt == 0x02:
            if pos + 5 > len(clx):
                return None
            lcb_pcdt = struct.unpack_from("<I", clx, pos + 1)[0]
            plc = clx[pos + 5 : pos + 5 + lcb_pcdt]
            if len(plc) < 16 or (len(plc) - 4) % 12 != 0:
                return None
            n_pieces = (len(plc) - 4) // 12
            cps = struct.unpack_from(f"<{n_pieces + 1}I", plc, 0)
            pcd_offset = (n_pieces + 1) * 4
            chunks: list[str] = []
            max_cp = ccp_text if ccp_text > 0 else cps[-1]
            for idx in range(n_pieces):
                cp_start, cp_end = cps[idx], min(cps[idx + 1], max_cp)
                if cp_end <= cp_start:
                    continue
                _attr, fc_raw, _prm = struct.unpack_from("<HIH", plc, pcd_offset + idx * 8)
                is_ansi = bool(fc_raw & 0x40000000)
                fc_val = fc_raw & 0x3FFFFFFF
                char_count = cp_end - cp_start
                if is_ansi:
                    byte_start = fc_val // 2
                    raw_slice = wd[byte_start : byte_start + char_count]
                    chunks.append(raw_slice.decode("cp1252", errors="replace"))
                else:
                    byte_start = fc_val
                    raw_slice = wd[byte_start : byte_start + char_count * 2]
                    chunks.append(raw_slice.decode("utf-16-le", errors="replace"))
                if cps[idx + 1] >= max_cp:
                    break
            return "".join(chunks)
        return None
    return None


def _convert_legacy_doc(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert legacy binary ``.doc`` documents via OLE2 ``WordDocument`` parsing."""
    if _olefile is None:
        return _build_notice_document(
            "docx",
            title,
            "Thiếu thư viện olefile để đọc định dạng .doc cũ.",
            dark_mode=dark_mode,
        )
    try:
        with _olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("WordDocument"):
                return _build_notice_document(
                    "docx", title, "Tệp .doc không chứa luồng WordDocument.", dark_mode=dark_mode
                )
            wd = ole.openstream("WordDocument").read(_MAX_LEGACY_STREAM_BYTES)
            if len(wd) < 32:
                return _build_notice_document(
                    "docx", title, "Tệp .doc bị hỏng hoặc rỗng.", dark_mode=dark_mode
                )
            w_ident, _n_fib = struct.unpack_from("<HH", wd, 0)
            if w_ident != 0xA5EC:
                return _build_notice_document(
                    "docx", title, "Định dạng Word .doc không hợp lệ.", dark_mode=dark_mode
                )
            flags = struct.unpack_from("<H", wd, 10)[0]
            if flags & 0x0100:
                return _build_notice_document(
                    "docx",
                    title,
                    "Tài liệu .doc được bảo vệ bằng mật khẩu nên chưa thể xem trực tiếp.",
                    dark_mode=dark_mode,
                )
            table_stream_name = "1Table" if (flags & 0x0200) else "0Table"
            tbl = (
                ole.openstream(table_stream_name).read(_MAX_LEGACY_STREAM_BYTES)
                if ole.exists(table_stream_name)
                else b""
            )
    except Exception:
        return _build_notice_document(
            "docx", title, "Không thể đọc tệp Word .doc cũ.", dark_mode=dark_mode
        )

    decoded = _extract_doc_clx_text(wd, tbl, flags)
    if decoded is None:
        return _build_notice_document(
            "docx",
            title,
            "Không thể giải mã cấu trúc văn bản của tệp .doc này.",
            dark_mode=dark_mode,
        )

    cleaned = _strip_doc_field_controls(decoded)
    paragraphs = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    docx_blocks: list[DocxRichBlock] = []
    html_blocks: list[str] = []
    md_blocks: list[str] = []
    render_budget = _RenderBudget.for_title(title)

    for para in paragraphs:
        if render_budget.truncated:
            break
        appended, can_continue = _append_bounded_text_block(
            html_blocks,
            md_blocks,
            render_budget,
            raw_text=para,
            build_html=lambda t: f"<p>{html.escape(t)}</p>",
            build_md=lambda t: t,
        )
        if appended:
            docx_blocks.append(DocxRichBlock(kind="paragraph", text=para))
        if not can_continue:
            break

    if not html_blocks:
        html_blocks.append('<p class="muted">(Tài liệu .doc trống)</p>')
    return ConvertedDocument(
        kind="docx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_blocks), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_blocks),
        docx_blocks=tuple(docx_blocks),
    )


def _collect_ppt_binary_slides(stream: bytes) -> list[str]:
    """Walk PowerPoint 97-2003 binary record tree and collect text per slide."""
    slides_text: list[str] = []
    fallback_texts: list[str] = []
    limit = len(stream)

    def _collect_atoms_in(start: int, end: int, out_parts: list[str]) -> None:
        pos = start
        while pos + 8 <= end:
            ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", stream, pos)
            payload_start = pos + 8
            payload_end = min(end, payload_start + rec_len)
            if (ver_inst & 0x0F) == 0x0F:
                _collect_atoms_in(payload_start, payload_end, out_parts)
            elif rec_type == 0x0FA0:
                txt = stream[payload_start:payload_end].decode("utf-16-le", errors="replace").strip()
                if txt:
                    out_parts.append(txt.replace("\r", "\n"))
            elif rec_type == 0x0FA8:
                txt = stream[payload_start:payload_end].decode("cp1252", errors="replace").strip()
                if txt:
                    out_parts.append(txt.replace("\r", "\n"))
            pos = payload_start + max(0, rec_len)

    def _walk(start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", stream, pos)
            payload_start = pos + 8
            payload_end = min(end, payload_start + rec_len)
            is_container = (ver_inst & 0x0F) == 0x0F
            if is_container:
                if rec_type == 0x03EE:
                    parts: list[str] = []
                    _collect_atoms_in(payload_start, payload_end, parts)
                    if parts:
                        slides_text.append("\n".join(parts))
                elif rec_type == 0x0FF0:
                    current_parts: list[str] = []
                    cpos = payload_start
                    while cpos + 8 <= payload_end:
                        c_vi, c_rt, c_rl = struct.unpack_from("<HHI", stream, cpos)
                        cp_start = cpos + 8
                        cp_end = min(payload_end, cp_start + c_rl)
                        if c_rt == 0x03F3:
                            if current_parts:
                                fallback_texts.append("\n".join(current_parts))
                                current_parts = []
                        elif c_rt in (0x0FA0, 0x0FA8) or (c_vi & 0x0F) == 0x0F:
                            _collect_atoms_in(cpos, cp_end, current_parts)
                        cpos = cp_start + max(0, c_rl)
                    if current_parts:
                        fallback_texts.append("\n".join(current_parts))
                else:
                    _walk(payload_start, payload_end)
            pos = payload_start + max(0, rec_len)

    _walk(0, limit)
    return slides_text if slides_text else fallback_texts


def _convert_legacy_ppt(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert legacy binary ``.ppt`` presentations via OLE2 ``PowerPoint Document``."""
    if _olefile is None:
        return _build_notice_document(
            "pptx",
            title,
            "Thiếu thư viện olefile để đọc định dạng .ppt cũ.",
            dark_mode=dark_mode,
        )
    try:
        with _olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists("PowerPoint Document"):
                return _build_notice_document(
                    "pptx",
                    title,
                    "Tệp .ppt không chứa luồng PowerPoint Document.",
                    dark_mode=dark_mode,
                )
            ppt_stream = ole.openstream("PowerPoint Document").read(_MAX_LEGACY_STREAM_BYTES)
    except Exception:
        return _build_notice_document(
            "pptx", title, "Không thể đọc tệp PowerPoint .ppt cũ.", dark_mode=dark_mode
        )

    raw_slides = _collect_ppt_binary_slides(ppt_stream)[:_MAX_PPTX_SLIDES]
    slides: list[tuple[int, str, str]] = []
    rich_slides: list[PptxSlideRich] = []
    outline: list[DocumentOutlineItem] = []
    html_cards: list[str] = []
    md_sections: list[str] = []
    render_budget = _RenderBudget.for_title(title)

    for idx, body_text in enumerate(raw_slides, start=1):
        if render_budget.truncated:
            break
        anchor_id = f"slide-{idx}"
        rendered = _render_bounded_pptx_card(
            idx,
            anchor_id,
            body_text,
            "",
            render_budget,
            has_prior_cards=bool(html_cards),
        )
        if rendered is None:
            break
        card_html, card_md, stop_after = rendered
        slides.append((idx, body_text, ""))
        rich_slides.append(
            PptxSlideRich(slide_number=idx, anchor_id=anchor_id, body_text=body_text)
        )
        first_line = next((ln.strip() for ln in body_text.splitlines() if ln.strip()), "")
        outline_label = f"Slide {idx}: {first_line[:60]}" if first_line else f"Slide {idx}"
        outline.append(DocumentOutlineItem(anchor_id=anchor_id, level=1, title=outline_label))
        html_cards.append(card_html)
        md_sections.append(card_md)
        if stop_after:
            break

    if not html_cards:
        html_cards.append('<p class="muted">(Bài trình bày .ppt trống hoặc không chứa văn bản)</p>')
    return ConvertedDocument(
        kind="pptx",
        title=title,
        html_document=_base_html_shell(title, "\n".join(html_cards), dark_mode=dark_mode),
        markdown_text="\n\n".join(md_sections),
        outline=outline,
        slides=slides,
        rich_slides=tuple(rich_slides),
    )
