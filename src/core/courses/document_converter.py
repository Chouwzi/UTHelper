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
from urllib.parse import urlsplit
from xml.parsers import expat

from core.courses.models import PreviewKind

try:
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover
    _PILImage = None  # type: ignore[assignment]

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
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
_C_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"

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
class DocxTextRun:
    """Styled inline text span extracted from WordprocessingML or DrawingML."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    code: bool = False
    superscript: bool = False
    subscript: bool = False
    href: str = ""


@dataclass(frozen=True, slots=True)
class DocxTableCell:
    """Structured table cell supporting spans, alignment, header styling, and inline images."""

    text: str = ""
    runs: tuple[DocxTextRun, ...] = ()
    col_span: int = 1
    align: str = "left"
    is_header: bool = False
    images: tuple[tuple[str, bytes], ...] = ()


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
    runs: tuple[DocxTextRun, ...] = ()
    align: str = "left"
    indent_level: int = 0
    list_marker: str = ""
    rich_rows: tuple[tuple[DocxTableCell, ...], ...] = ()
    has_borders: bool = True
    section_role: str = "body"


@dataclass(frozen=True, slots=True)
class PptxTextParagraph:
    """Styled paragraph extracted from a PPTX text body."""

    text: str
    runs: tuple[DocxTextRun, ...] = ()
    level: int = 0
    bullet: str = ""
    align: str = "left"
    is_title: bool = False
    is_subtitle: bool = False


@dataclass(frozen=True, slots=True)
class PptxPlacedElement:
    """Visually positioned element inside a presentation slide canvas."""

    kind: Literal["text", "image", "table"]
    x_ratio: float = 0.0
    y_ratio: float = 0.0
    w_ratio: float = 1.0
    h_ratio: float = 0.2
    paragraphs: tuple[PptxTextParagraph, ...] = ()
    image_name: str = ""
    image_bytes: bytes = b""
    table_rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class PptxSlideRich:
    """Rich slide payload with text, notes, tables, layout elements, and embedded raster media."""

    slide_number: int
    anchor_id: str
    body_text: str
    notes_text: str = ""
    images: tuple[tuple[str, bytes], ...] = ()
    tables: tuple[tuple[tuple[str, ...], ...], ...] = ()
    title_text: str = ""
    subtitle_text: str = ""
    aspect_ratio: float = 16.0 / 9.0
    elements: tuple[PptxPlacedElement, ...] = ()


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
        ".table-wrap.borderless,.table-wrap.borderless table,.table-wrap.borderless td,.table-wrap.borderless th{"
        "border:none!important;background:transparent!important;}"
        ".doc-list{margin:0.4em 0;padding-left:1.35em;display:flex;gap:0.45em;align-items:baseline;}"
        ".doc-list-marker{color:var(--accent);font-weight:600;min-width:1.4em;flex-shrink:0;}"
        ".doc-role-note{font-size:0.84rem;color:var(--muted);padding-bottom:6px;"
        "border-bottom:1px dashed var(--border);margin-bottom:8px;}"
        ".doc-figure{margin:12px 0;}"
        ".doc-image{max-width:100%;height:auto;border-radius:6px;border:1px solid var(--border);}"
        ".slide-card{background:var(--surface);border:1px solid var(--border);"
        "border-radius:10px;padding:16px;margin:0 0 14px;}"
        ".slide-card h3{margin-top:0;color:var(--accent);}"
        ".slide-subtitle{color:var(--muted);font-size:0.98rem;margin:-4px 0 10px;font-weight:500;}"
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


def _safe_external_http_url(raw_url: str) -> str:
    """Allow only clean http(s) URLs from OOXML external relationships."""
    cleaned = str(raw_url or "").strip()
    if not cleaned:
        return ""
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return ""
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return cleaned
    return ""


def _parse_external_hyperlinks(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    rels_path: str,
    *,
    xml_budget: Optional[list[int]] = None,
) -> dict[str, str]:
    """Parse external hyperlink relationships into a safe {rId: http_url} map."""
    if rels_path not in zf_names:
        return {}
    raw = _read_zip_entry_capped(
        zf, rels_path, _MAX_XML_BYTES, xml_budget=xml_budget
    )
    root = _parse_xml_safe(raw)
    links: dict[str, str] = {}
    for rel in root:
        if _local_name(rel.tag) != "Relationship":
            continue
        if (rel.attrib.get("TargetMode") or "").strip().lower() != "external":
            continue
        rel_type = (rel.attrib.get("Type") or "").strip().lower()
        if not rel_type.endswith("/hyperlink"):
            continue
        rel_id = rel.attrib.get("Id") or ""
        safe_url = _safe_external_http_url(rel.attrib.get("Target", ""))
        if rel_id and safe_url:
            links[rel_id] = safe_url
    return links


def _iter_effective_children(parent: ET.Element) -> Iterator[ET.Element]:
    """Yield children while resolving <mc:AlternateContent> to Choice (or Fallback)."""
    for child in parent:
        if child.tag == f"{{{_MC_NS}}}AlternateContent":
            choice = child.find(f"{{{_MC_NS}}}Choice")
            branch = choice if choice is not None else child.find(f"{{{_MC_NS}}}Fallback")
            if branch is not None:
                yield from _iter_effective_children(branch)
        else:
            yield child


def _extract_omml_text(node: ET.Element) -> str:
    """Convert Word/PPTX OMML math XML into readable Unicode mathematical text."""
    m_t = f"{{{_M_NS}}}t"
    m_f = f"{{{_M_NS}}}f"
    m_num = f"{{{_M_NS}}}num"
    m_den = f"{{{_M_NS}}}den"
    m_ssup = f"{{{_M_NS}}}sSup"
    m_ssub = f"{{{_M_NS}}}sSub"
    m_ssubsup = f"{{{_M_NS}}}sSubSup"
    m_rad = f"{{{_M_NS}}}rad"
    m_deg = f"{{{_M_NS}}}deg"
    m_nary = f"{{{_M_NS}}}nary"
    m_d = f"{{{_M_NS}}}d"
    m_e = f"{{{_M_NS}}}e"
    m_sup = f"{{{_M_NS}}}sup"
    m_sub = f"{{{_M_NS}}}sub"
    m_pr = f"{{{_M_NS}}}chr"
    m_val = f"{{{_M_NS}}}val"

    def _collect(el: ET.Element) -> str:
        if el.tag == m_t:
            return el.text or ""
        if el.tag == m_f:
            num_el = el.find(m_num)
            den_el = el.find(m_den)
            num_s = _collect(num_el) if num_el is not None else ""
            den_s = _collect(den_el) if den_el is not None else ""
            return f"({num_s})/({den_s})" if den_s else num_s
        if el.tag == m_ssup:
            e_el = el.find(m_e)
            s_el = el.find(m_sup)
            base_s = _collect(e_el) if e_el is not None else ""
            sup_s = _collect(s_el) if s_el is not None else ""
            return f"{base_s}^({sup_s})" if len(sup_s) > 1 else f"{base_s}^{sup_s}"
        if el.tag == m_ssub:
            e_el = el.find(m_e)
            s_el = el.find(m_sub)
            base_s = _collect(e_el) if e_el is not None else ""
            sub_s = _collect(s_el) if s_el is not None else ""
            return f"{base_s}_({sub_s})" if len(sub_s) > 1 else f"{base_s}_{sub_s}"
        if el.tag == m_ssubsup:
            e_el = el.find(m_e)
            sub_el = el.find(m_sub)
            sup_el = el.find(m_sup)
            base_s = _collect(e_el) if e_el is not None else ""
            sub_s = _collect(sub_el) if sub_el is not None else ""
            sup_s = _collect(sup_el) if sup_el is not None else ""
            return f"{base_s}_{sub_s}^{sup_s}"
        if el.tag == m_rad:
            deg_el = el.find(m_deg)
            e_el = el.find(m_e)
            deg_s = _collect(deg_el).strip() if deg_el is not None else ""
            base_s = _collect(e_el) if e_el is not None else ""
            prefix = f"{deg_s}√" if deg_s else "√"
            return f"{prefix}({base_s})"
        if el.tag == m_nary:
            chr_el = el.find(f".//{m_pr}")
            op = (chr_el.attrib.get(m_val) if chr_el is not None else None) or "∑"
            sub_el = el.find(m_sub)
            sup_el = el.find(m_sup)
            e_el = el.find(m_e)
            sub_s = _collect(sub_el).strip() if sub_el is not None else ""
            sup_s = _collect(sup_el).strip() if sup_el is not None else ""
            base_s = _collect(e_el) if e_el is not None else ""
            limits = ""
            if sub_s:
                limits += f"_{sub_s}"
            if sup_s:
                limits += f"^{sup_s}"
            return f"{op}{limits}({base_s})"
        if el.tag == m_d:
            d_pr = el.find(f"{{{_M_NS}}}dPr")
            beg_el = d_pr.find(f"{{{_M_NS}}}begChr") if d_pr is not None else None
            end_el = d_pr.find(f"{{{_M_NS}}}endChr") if d_pr is not None else None
            beg_c = (beg_el.attrib.get(m_val) if beg_el is not None else None) or "("
            end_c = (end_el.attrib.get(m_val) if end_el is not None else None) or ")"
            inner = ", ".join(
                _collect(sub) for sub in el.findall(m_e)
            )
            return f"{beg_c}{inner}{end_c}"
        return "".join(_collect(sub) for sub in _iter_effective_children(el))

    return _collect(node)


def _coalesce_runs(runs: list[DocxTextRun]) -> tuple[DocxTextRun, ...]:
    """Merge adjacent text runs sharing identical style attributes and hyperlink target."""
    merged: list[DocxTextRun] = []
    for run in runs:
        if not run.text:
            continue
        if merged:
            prev = merged[-1]
            if (
                prev.bold == run.bold
                and prev.italic == run.italic
                and prev.underline == run.underline
                and prev.strike == run.strike
                and prev.code == run.code
                and prev.superscript == run.superscript
                and prev.subscript == run.subscript
                and prev.href == run.href
            ):
                merged[-1] = DocxTextRun(
                    text=prev.text + run.text,
                    bold=prev.bold,
                    italic=prev.italic,
                    underline=prev.underline,
                    strike=prev.strike,
                    code=prev.code,
                    superscript=prev.superscript,
                    subscript=prev.subscript,
                    href=prev.href,
                )
                continue
        merged.append(run)
    return tuple(merged)


def _render_runs_html(runs: tuple[DocxTextRun, ...], fallback_text: str = "") -> str:
    """Render styled text runs into XSS-escaped inline HTML."""
    if not runs:
        return html.escape(fallback_text).replace("\n", "<br/>")
    parts: list[str] = []
    for run in runs:
        seg = html.escape(run.text).replace("\n", "<br/>")
        if run.code:
            seg = f"<code>{seg}</code>"
        if run.bold:
            seg = f"<strong>{seg}</strong>"
        if run.italic:
            seg = f"<em>{seg}</em>"
        if run.underline:
            seg = f"<u>{seg}</u>"
        if run.strike:
            seg = f"<s>{seg}</s>"
        if run.superscript:
            seg = f"<sup>{seg}</sup>"
        elif run.subscript:
            seg = f"<sub>{seg}</sub>"
        if run.href:
            safe_href = html.escape(run.href, quote=True)
            seg = f'<a href="{safe_href}" target="_blank" rel="noopener noreferrer">{seg}</a>'
        parts.append(seg)
    return "".join(parts)


def _int_to_roman(num: int, *, upper: bool = True) -> str:
    val_map = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    out: list[str] = []
    rem = max(1, min(3999, num))
    for arabic, roman in val_map:
        while rem >= arabic:
            out.append(roman)
            rem -= arabic
    res = "".join(out)
    return res if upper else res.lower()


def _int_to_alpha(num: int, *, upper: bool = False) -> str:
    base = ord("A" if upper else "a")
    idx = (max(1, num) - 1) % 26
    return chr(base + idx)


def _format_docx_list_marker(num_fmt: str, count: int) -> str:
    fmt = (num_fmt or "").strip().lower()
    if fmt == "bullet":
        return "•"
    if fmt == "none":
        return ""
    if fmt == "lowerletter":
        return f"{_int_to_alpha(count, upper=False)})"
    if fmt == "upperletter":
        return f"{_int_to_alpha(count, upper=True)}."
    if fmt == "lowerroman":
        return f"{_int_to_roman(count, upper=False)}."
    if fmt == "upperroman":
        return f"{_int_to_roman(count, upper=True)}."
    return f"{count}."


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


_CONVERTIBLE_VECTOR_RASTER_EXTS = frozenset({".tif", ".tiff", ".wmf", ".emf"})


def _normalize_image_payload(ext: str, raw: bytes) -> Optional[tuple[str, bytes]]:
    """Validate raster image bytes or safely convert TIFF/WMF/EMF to PNG via Pillow."""
    mime = _SAFE_RASTER_MIMES.get(ext)
    if mime:
        if _is_valid_raster_magic(ext, raw):
            return mime, raw
        return None
    if ext in _CONVERTIBLE_VECTOR_RASTER_EXTS and _PILImage is not None:
        try:
            with _PILImage.open(io.BytesIO(raw)) as img:
                out = io.BytesIO()
                converted = img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img
                converted.save(out, format="PNG")
                return "image/png", out.getvalue()
        except Exception:
            return None
    return None


def _iter_docx_body_blocks(container: ET.Element) -> Iterator[ET.Element]:
    """Yield <w:p> and <w:tbl> blocks from a DOCX body, unwrapping SDT and AlternateContent."""
    for child in _iter_effective_children(container):
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


def _docx_paragraph_align(p_el: ET.Element) -> str:
    """Return normalized paragraph alignment ('left', 'center', 'right', 'justify')."""
    p_pr = p_el.find(f"{{{_W_NS}}}pPr")
    if p_pr is None:
        return "left"
    jc = p_pr.find(f"{{{_W_NS}}}jc")
    if jc is None:
        return "left"
    val = (jc.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower()
    if val in {"center", "mediumkashida"}:
        return "center"
    if val in {"right", "end"}:
        return "right"
    if val in {"both", "distribute", "thaiDistribute".lower()}:
        return "justify"
    return "left"


def _docx_flag_enabled(r_pr: Optional[ET.Element], tag_local: str) -> bool:
    if r_pr is None:
        return False
    el = r_pr.find(f"{{{_W_NS}}}{tag_local}")
    if el is None:
        return False
    val = (el.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower()
    return val not in {"0", "false", "off", "none"}


def _docx_run_style(r_el: ET.Element, *, href: str = "") -> dict[str, object]:
    """Extract inline formatting flags from a WordprocessingML <w:r> element."""
    r_pr = r_el.find(f"{{{_W_NS}}}rPr")
    bold = _docx_flag_enabled(r_pr, "b") or _docx_flag_enabled(r_pr, "bCs")
    italic = _docx_flag_enabled(r_pr, "i") or _docx_flag_enabled(r_pr, "iCs")
    underline = _docx_flag_enabled(r_pr, "u")
    strike = _docx_flag_enabled(r_pr, "strike") or _docx_flag_enabled(r_pr, "dstrike")
    superscript = False
    subscript = False
    code = False
    if r_pr is not None:
        vert = r_pr.find(f"{{{_W_NS}}}vertAlign")
        if vert is not None:
            vval = (vert.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower()
            superscript = vval == "superscript"
            subscript = vval == "subscript"
        rstyle = r_pr.find(f"{{{_W_NS}}}rStyle")
        if rstyle is not None:
            rs_val = (rstyle.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower()
            if "code" in rs_val or "verbatim" in rs_val:
                code = True
        rfonts = r_pr.find(f"{{{_W_NS}}}rFonts")
        if rfonts is not None:
            joined_fonts = " ".join(rfonts.attrib.values()).lower()
            if any(m in joined_fonts for m in ("consolas", "courier", "mono", "menlo")):
                code = True
    return {
        "bold": bold,
        "italic": italic,
        "underline": underline,
        "strike": strike,
        "code": code,
        "superscript": superscript,
        "subscript": subscript,
        "href": href,
    }


def _docx_paragraph_runs_and_text(
    p_el: ET.Element,
    hyperlinks: Optional[dict[str, str]] = None,
) -> tuple[str, tuple[DocxTextRun, ...]]:
    """Collect visible text and styled runs from a Word <w:p>, resolving AlternateContent & math."""
    raw_runs: list[DocxTextRun] = []
    link_map = hyperlinks or {}

    def _emit(text: str, style: dict[str, object]) -> None:
        if not text:
            return
        raw_runs.append(
            DocxTextRun(
                text=text,
                bold=bool(style.get("bold", False)),
                italic=bool(style.get("italic", False)),
                underline=bool(style.get("underline", False)),
                strike=bool(style.get("strike", False)),
                code=bool(style.get("code", False)),
                superscript=bool(style.get("superscript", False)),
                subscript=bool(style.get("subscript", False)),
                href=str(style.get("href", "")),
            )
        )

    default_style: dict[str, object] = {}

    def _walk(node: ET.Element, cur_href: str, cur_style: dict[str, object]) -> None:
        if node.tag in (f"{{{_W_NS}}}del", f"{{{_W_NS}}}delText", f"{{{_W_NS}}}instrText"):
            return
        if node.tag in (f"{{{_M_NS}}}oMathPara", f"{{{_M_NS}}}oMath"):
            math_txt = _extract_omml_text(node).strip()
            if math_txt:
                _emit(math_txt, cur_style)
            return
        if node.tag == f"{{{_W_NS}}}hyperlink":
            rid = node.attrib.get(f"{{{_R_NS}}}id") or ""
            next_href = link_map.get(rid, cur_href)
            for sub in _iter_effective_children(node):
                _walk(sub, next_href, cur_style)
            return
        if node.tag == f"{{{_W_NS}}}r":
            r_style = _docx_run_style(node, href=cur_href)
            for sub in _iter_effective_children(node):
                _walk(sub, cur_href, r_style)
            return
        if node.tag == f"{{{_W_NS}}}t" and node.text:
            _emit(node.text, cur_style)
            return
        if node.tag in (f"{{{_W_NS}}}br", f"{{{_W_NS}}}cr"):
            _emit("\n", cur_style)
            return
        if node.tag == f"{{{_W_NS}}}tab":
            _emit("\t", cur_style)
            return
        if node.tag == f"{{{_W_NS}}}txbxContent":
            # Separate paragraphs inside embedded drawing textboxes cleanly
            for idx_tb, tb_child in enumerate(_iter_effective_children(node)):
                if idx_tb > 0 and raw_runs and not raw_runs[-1].text.endswith("\n"):
                    _emit("\n", cur_style)
                _walk(tb_child, cur_href, cur_style)
            return
        for sub in _iter_effective_children(node):
            _walk(sub, cur_href, cur_style)

    for sub in _iter_effective_children(p_el):
        _walk(sub, "", default_style)

    coalesced = _coalesce_runs(raw_runs)
    full_text = "".join(r.text for r in coalesced)
    return full_text, coalesced


def _docx_paragraph_text(p_el: ET.Element) -> str:
    """Collect visible text from a WordprocessingML <w:p>, ignoring deleted runs."""
    text, _ = _docx_paragraph_runs_and_text(p_el)
    return text


def _docx_paragraph_image_rids(p_el: ET.Element) -> list[str]:
    """Extract embedded image relationship IDs inside <w:p>, skipping fallback duplicates."""
    rids: list[str] = []
    seen: set[str] = set()

    def _walk(node: ET.Element) -> None:
        if node.tag in (f"{{{_W_NS}}}del", f"{{{_W_NS}}}delText"):
            return
        if node.tag == f"{{{_A_NS}}}blip":
            rid = node.attrib.get(f"{{{_R_NS}}}embed")
            if rid and rid not in seen:
                seen.add(rid)
                rids.append(rid)
        elif node.tag == f"{{{_V_NS}}}imagedata":
            rid = node.attrib.get(f"{{{_R_NS}}}id")
            if rid and rid not in seen:
                seen.add(rid)
                rids.append(rid)
        for sub in _iter_effective_children(node):
            _walk(sub)

    _walk(p_el)
    return rids


def _parse_docx_numbering(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    *,
    xml_budget: list[int],
) -> tuple[dict[str, dict[int, tuple[str, int]]], dict[str, tuple[str, dict[int, int]]]]:
    """Parse ``word/numbering.xml`` into abstract formats and concrete numId overrides."""
    if "word/numbering.xml" not in zf_names:
        return {}, {}
    try:
        raw = _read_zip_entry_capped(
            zf, "word/numbering.xml", _MAX_XML_BYTES, xml_budget=xml_budget
        )
        root = _parse_xml_safe(raw)
    except _CONVERSION_ERRORS:
        return {}, {}

    abstract_map: dict[str, dict[int, tuple[str, int]]] = {}
    for abs_el in root.findall(f"{{{_W_NS}}}abstractNum"):
        abs_id = abs_el.attrib.get(f"{{{_W_NS}}}abstractNumId") or ""
        if not abs_id:
            continue
        levels: dict[int, tuple[str, int]] = {}
        for lvl in abs_el.findall(f"{{{_W_NS}}}lvl"):
            raw_ilvl = (lvl.attrib.get(f"{{{_W_NS}}}ilvl") or "").strip()
            if not (raw_ilvl.isascii() and raw_ilvl.isdigit()):
                continue
            ilvl = int(raw_ilvl)
            num_fmt_el = lvl.find(f"{{{_W_NS}}}numFmt")
            fmt = (
                (num_fmt_el.attrib.get(f"{{{_W_NS}}}val") or "bullet")
                if num_fmt_el is not None
                else "bullet"
            )
            start_el = lvl.find(f"{{{_W_NS}}}start")
            raw_start = (start_el.attrib.get(f"{{{_W_NS}}}val") or "1") if start_el is not None else "1"
            start = int(raw_start) if raw_start.isascii() and raw_start.isdigit() else 1
            levels[ilvl] = (fmt, max(1, start))
        abstract_map[abs_id] = levels

    num_map: dict[str, tuple[str, dict[int, int]]] = {}
    for num_el in root.findall(f"{{{_W_NS}}}num"):
        num_id = num_el.attrib.get(f"{{{_W_NS}}}numId") or ""
        abs_ref = num_el.find(f"{{{_W_NS}}}abstractNumId")
        abs_id = abs_ref.attrib.get(f"{{{_W_NS}}}val") if abs_ref is not None else ""
        if not num_id or not abs_id:
            continue
        overrides: dict[int, int] = {}
        for ov in num_el.findall(f"{{{_W_NS}}}lvlOverride"):
            raw_ilvl = (ov.attrib.get(f"{{{_W_NS}}}ilvl") or "").strip()
            so_el = ov.find(f"{{{_W_NS}}}startOverride")
            if raw_ilvl.isascii() and raw_ilvl.isdigit() and so_el is not None:
                raw_so = (so_el.attrib.get(f"{{{_W_NS}}}val") or "").strip()
                if raw_so.isascii() and raw_so.isdigit():
                    overrides[int(raw_ilvl)] = max(1, int(raw_so))
        num_map[num_id] = (abs_id, overrides)

    return abstract_map, num_map


def _docx_table_has_borders(tbl_el: ET.Element) -> bool:
    """Detect whether a Word table visually renders borders or acts as borderless grid."""
    tbl_pr = tbl_el.find(f"{{{_W_NS}}}tblPr")
    if tbl_pr is None:
        return True
    borders = tbl_pr.find(f"{{{_W_NS}}}tblBorders")
    if borders is None:
        return True
    edges = [
        borders.find(f"{{{_W_NS}}}{edge}")
        for edge in ("top", "bottom", "left", "right", "insideH", "insideV")
    ]
    present = [e for e in edges if e is not None]
    if not present:
        return True
    return any(
        (e.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower() not in {"none", "nil"}
        for e in present
    )


def _render_docx_table_html_and_markdown(
    rich_rows: tuple[tuple[DocxTableCell, ...], ...],
    *,
    has_borders: bool,
    budget: _RenderBudget,
) -> tuple[str, str]:
    """Render structured Word table with colspan, formatting, inline links, and border hint."""
    if not rich_rows:
        return "", ""
    max_cols = min(
        _MAX_TABLE_COLS,
        max(sum(max(1, c.col_span) for c in row) for row in rich_rows),
    )
    if max_cols == 0:
        return "", ""

    wrap_cls = "table-wrap" if has_borders else "table-wrap borderless"
    html_rows: list[str] = []
    md_lines: list[str] = []
    for r_idx, row in enumerate(rich_rows[:_MAX_TABLE_ROWS]):
        use_th = r_idx == 0 and has_borders and all(c.is_header or r_idx == 0 for c in row)
        cell_tag = "th" if use_th else "td"
        align_css = ""
        row_cells_html: list[str] = []
        flat_md_cells: list[str] = []
        for cell in row[:_MAX_TABLE_COLS]:
            col_span = max(1, cell.col_span)
            span_attr = f' colspan="{col_span}"' if col_span > 1 else ""
            if cell.align != "left":
                align_css = f' style="text-align:{cell.align}"'
            else:
                align_css = ""
            inner_html = _render_runs_html(cell.runs, _truncate_preview_text(cell.text))
            for img_name, img_bytes in cell.images:
                ext = posixpath.splitext(img_name.lower())[1]
                mime = _SAFE_RASTER_MIMES.get(ext, "image/png")
                b64 = base64.b64encode(img_bytes).decode("ascii")
                inner_html += (
                    f'<br/><img class="doc-image" src="data:{mime};base64,{b64}" '
                    f'alt="{html.escape(img_name)}"/>'
                )
            row_cells_html.append(f"<{cell_tag}{span_attr}{align_css}>{inner_html}</{cell_tag}>")
            flat_md_cells.append(_escape_md_cell(_truncate_preview_text(cell.text)))
            for _ in range(col_span - 1):
                flat_md_cells.append("")
        while len(flat_md_cells) < max_cols:
            flat_md_cells.append("")
        row_html = "<tr>" + "".join(row_cells_html) + "</tr>"
        row_md = "| " + " | ".join(flat_md_cells[:max_cols]) + " |"
        if not budget.try_consume(len(row_html), len(row_md) + 1):
            break
        html_rows.append(row_html)
        md_lines.append(row_md)
        if r_idx == 0:
            sep_md = "| " + " | ".join("---" for _ in range(max_cols)) + " |"
            budget.try_consume(0, len(sep_md) + 1)
            md_lines.append(sep_md)

    if not html_rows:
        return "", ""
    head_part = f"<thead>{html_rows[0]}</thead>" if has_borders else f"<tbody>{html_rows[0]}"
    if has_borders:
        body_part = f"<tbody>{''.join(html_rows[1:])}</tbody>" if len(html_rows) > 1 else ""
    else:
        body_part = f"{''.join(html_rows[1:])}</tbody>"
    table_html = f'<div class="{wrap_cls}"><table>{head_part}{body_part}</table></div>'
    return table_html, "\n".join(md_lines)


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
    """Convert DOCX bytes into outline, Markdown, rich formatted blocks, and XSS-safe HTML."""
    outline: list[DocumentOutlineItem] = []
    docx_blocks: list[DocxRichBlock] = []
    html_blocks: list[str] = []
    md_blocks: list[str] = []
    rendered_media: set[str] = set()
    media_cache: dict[str, tuple[str, str, bytes]] = {}
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
        hyperlinks = _parse_external_hyperlinks(
            zf,
            zf_names,
            "word/_rels/document.xml.rels",
            xml_budget=xml_budget,
        )
        abs_nums, concrete_nums = _parse_docx_numbering(
            zf, zf_names, xml_budget=xml_budget
        )
        list_counters: dict[tuple[str, int], int] = {}

        def _try_embed_media(zip_path: str) -> Optional[tuple[str, str, bytes]]:
            if zip_path in media_cache:
                rendered_media.add(zip_path)
                return media_cache[zip_path]
            if zip_path not in zf_names:
                return None
            if media_state["count"] >= _MAX_MEDIA_COUNT:
                return None
            ext = posixpath.splitext(zip_path.lower())[1]
            if ext not in _SAFE_RASTER_MIMES and ext not in _CONVERTIBLE_VECTOR_RASTER_EXTS:
                return None
            try:
                raw_img = _read_zip_entry_capped(zf, zip_path, _MAX_MEDIA_BYTES)
            except _CONVERSION_ERRORS:
                return None
            normalized = _normalize_image_payload(ext, raw_img) if raw_img else None
            if normalized is None:
                return None
            mime, final_bytes = normalized
            if media_state["bytes"] + len(final_bytes) > _MAX_TOTAL_MEDIA_BYTES:
                return None
            rendered_media.add(zip_path)
            media_state["count"] += 1
            media_state["bytes"] += len(final_bytes)
            encoded = base64.b64encode(final_bytes).decode("ascii")
            img_name = posixpath.basename(zip_path)
            alt = html.escape(img_name)
            img_html = (
                '<figure class="doc-figure">'
                f'<img class="doc-image" src="data:{mime};base64,{encoded}" alt="{alt}"/>'
                "</figure>"
            )
            payload = (img_html, img_name, final_bytes)
            media_cache[zip_path] = payload
            return payload

        def _next_list_info(p_el: ET.Element) -> tuple[bool, str, int]:
            p_pr = p_el.find(f"{{{_W_NS}}}pPr")
            if p_pr is None:
                return False, "", 0
            num_pr = p_pr.find(f"{{{_W_NS}}}numPr")
            if num_pr is None:
                return False, "", 0
            ilvl_el = num_pr.find(f"{{{_W_NS}}}ilvl")
            num_id_el = num_pr.find(f"{{{_W_NS}}}numId")
            raw_ilvl = (ilvl_el.attrib.get(f"{{{_W_NS}}}val") or "0") if ilvl_el is not None else "0"
            ilvl = int(raw_ilvl) if raw_ilvl.isascii() and raw_ilvl.isdigit() else 0
            num_id = (num_id_el.attrib.get(f"{{{_W_NS}}}val") or "") if num_id_el is not None else ""
            if not num_id or num_id == "0":
                return False, "", ilvl

            fmt = "bullet"
            start_val = 1
            if num_id in concrete_nums:
                abs_id, overrides = concrete_nums[num_id]
                levels = abs_nums.get(abs_id, {})
                if ilvl in levels:
                    fmt, start_val = levels[ilvl]
                if ilvl in overrides:
                    start_val = overrides[ilvl]

            for existing_key in list(list_counters.keys()):
                if existing_key[0] == num_id and existing_key[1] > ilvl:
                    del list_counters[existing_key]
            ck = (num_id, ilvl)
            cur = list_counters[ck] + 1 if ck in list_counters else start_val
            list_counters[ck] = cur
            marker = _format_docx_list_marker(fmt, cur)
            return True, marker, min(4, ilvl)

        def _process_block_sequence(
            container: ET.Element,
            part_rels: dict[str, str],
            part_links: dict[str, str],
            *,
            section_role: str = "body",
        ) -> None:
            for block in _iter_docx_body_blocks(container):
                if render_budget.truncated:
                    break
                if block.tag == f"{{{_W_NS}}}p":
                    level = _docx_heading_level(block) if section_role == "body" else None
                    align = _docx_paragraph_align(block)
                    text_raw, runs = _docx_paragraph_runs_and_text(block, part_links)
                    text = text_raw.strip()
                    if level is not None and text:
                        anchor_id = f"sec-{len(outline) + 1}"
                        align_attr = f' style="text-align:{align}"' if align != "left" else ""
                        inner_html = _render_runs_html(runs, text)
                        appended, can_continue = _append_bounded_text_block(
                            html_blocks,
                            md_blocks,
                            render_budget,
                            raw_text=text,
                            build_html=lambda _t, lv=level, aid=anchor_id, aa=align_attr, ih=inner_html: (
                                f'<h{lv} id="{aid}"{aa}>{ih}</h{lv}>'
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
                                    runs=runs,
                                    align=align,
                                    section_role=section_role,
                                )
                            )
                        if not can_continue:
                            break
                    elif text:
                        is_list, list_marker, indent_level = _next_list_info(block)
                        align_attr = f' style="text-align:{align}"' if align != "left" else ""
                        inner_html = _render_runs_html(runs, text)
                        if is_list:
                            marker_txt = list_marker or "•"
                            block_html = (
                                f'<div class="doc-list" style="margin-left:{indent_level * 18}px">'
                                f'<span class="doc-list-marker">{html.escape(marker_txt)}</span>'
                                f"<span>{inner_html}</span></div>"
                            )
                            md_prefix = f"{'  ' * indent_level}{marker_txt} "
                        else:
                            block_html = (
                                f'<p class="doc-role-note">{inner_html}</p>'
                                if section_role != "body"
                                else f"<p{align_attr}>{inner_html}</p>"
                            )
                            md_prefix = ""
                        appended, can_continue = _append_bounded_text_block(
                            html_blocks,
                            md_blocks,
                            render_budget,
                            raw_text=text,
                            build_html=lambda _t, bh=block_html: bh,
                            build_md=lambda t, mp=md_prefix: f"{mp}{t}",
                        )
                        if appended:
                            docx_blocks.append(
                                DocxRichBlock(
                                    kind="list_item" if is_list else "paragraph",
                                    text=text,
                                    runs=runs,
                                    align=align,
                                    indent_level=indent_level,
                                    list_marker=list_marker,
                                    section_role=section_role,
                                )
                            )
                        if not can_continue:
                            break

                    for rid in _docx_paragraph_image_rids(block):
                        target = part_rels.get(rid)
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
                                        section_role=section_role,
                                    )
                                )

                elif block.tag == f"{{{_W_NS}}}tbl":
                    has_borders = _docx_table_has_borders(block)
                    table_rows: list[list[str]] = []
                    rich_table_rows: list[tuple[DocxTableCell, ...]] = []
                    for tr_idx, tr in enumerate(block.findall(f"{{{_W_NS}}}tr")[:_MAX_TABLE_ROWS]):
                        tr_pr = tr.find(f"{{{_W_NS}}}trPr")
                        is_row_header = (
                            (tr_pr is not None and tr_pr.find(f"{{{_W_NS}}}tblHeader") is not None)
                            or (tr_idx == 0 and has_borders)
                        )
                        row_cells_text: list[str] = []
                        row_cells_rich: list[DocxTableCell] = []
                        for tc in tr.findall(f"{{{_W_NS}}}tc")[:_MAX_TABLE_COLS]:
                            tc_pr = tc.find(f"{{{_W_NS}}}tcPr")
                            grid_span = 1
                            is_vmerge_cont = False
                            if tc_pr is not None:
                                gs_el = tc_pr.find(f"{{{_W_NS}}}gridSpan")
                                if gs_el is not None:
                                    raw_gs = (gs_el.attrib.get(f"{{{_W_NS}}}val") or "").strip()
                                    if raw_gs.isascii() and raw_gs.isdigit():
                                        grid_span = max(1, min(int(raw_gs), _MAX_TABLE_COLS))
                                vm_el = tc_pr.find(f"{{{_W_NS}}}vMerge")
                                if vm_el is not None:
                                    vm_val = (vm_el.attrib.get(f"{{{_W_NS}}}val") or "").strip().lower()
                                    if vm_val != "restart":
                                        is_vmerge_cont = True

                            cell_paras_text: list[str] = []
                            cell_runs_acc: list[DocxTextRun] = []
                            cell_align = "left"
                            cell_imgs: list[tuple[str, bytes]] = []
                            for p_idx_cell, p in enumerate(tc.findall(f".//{{{_W_NS}}}p")):
                                p_txt_raw, p_runs = _docx_paragraph_runs_and_text(p, part_links)
                                p_txt = p_txt_raw.strip()
                                if p_txt:
                                    if p_idx_cell == 0:
                                        cell_align = _docx_paragraph_align(p)
                                    cell_paras_text.append(p_txt)
                                    if cell_runs_acc:
                                        cell_runs_acc.append(DocxTextRun(text="\n"))
                                    cell_runs_acc.extend(p_runs)
                                for rid in _docx_paragraph_image_rids(p):
                                    target = part_rels.get(rid)
                                    if target:
                                        m_res = _try_embed_media(target)
                                        if m_res is not None:
                                            cell_imgs.append((m_res[1], m_res[2]))

                            cell_text = "" if (is_vmerge_cont and not cell_paras_text) else "\n".join(cell_paras_text)
                            row_cells_text.append(cell_text)
                            row_cells_rich.append(
                                DocxTableCell(
                                    text=cell_text,
                                    runs=_coalesce_runs(cell_runs_acc),
                                    col_span=grid_span,
                                    align=cell_align,
                                    is_header=is_row_header,
                                    images=tuple(cell_imgs),
                                )
                            )
                        table_rows.append(row_cells_text)
                        rich_table_rows.append(tuple(row_cells_rich))

                    tbl_html, tbl_md = _render_docx_table_html_and_markdown(
                        tuple(rich_table_rows),
                        has_borders=has_borders,
                        budget=render_budget,
                    )
                    if tbl_html:
                        html_blocks.append(tbl_html)
                        docx_blocks.append(
                            DocxRichBlock(
                                kind="table",
                                rows=tuple(tuple(r) for r in table_rows),
                                rich_rows=tuple(rich_table_rows),
                                has_borders=has_borders,
                                section_role=section_role,
                            )
                        )
                    if tbl_md:
                        md_blocks.append(tbl_md)
                    if render_budget.truncated:
                        break

        body = doc_root.find(f"{{{_W_NS}}}body")
        header_targets: list[str] = []
        footer_targets: list[str] = []
        if body is not None:
            for sect_pr in body.findall(f".//{{{_W_NS}}}sectPr"):
                for href_el in sect_pr.findall(f"{{{_W_NS}}}headerReference"):
                    htype = (href_el.attrib.get(f"{{{_W_NS}}}type") or "default").lower()
                    rid = href_el.attrib.get(f"{{{_R_NS}}}id") or ""
                    target = rels.get(rid)
                    if htype == "default" and target and target not in header_targets:
                        header_targets.append(target)
                for fref_el in sect_pr.findall(f"{{{_W_NS}}}footerReference"):
                    ftype = (fref_el.attrib.get(f"{{{_W_NS}}}type") or "default").lower()
                    rid = fref_el.attrib.get(f"{{{_R_NS}}}id") or ""
                    target = rels.get(rid)
                    if ftype == "default" and target and target not in footer_targets:
                        footer_targets.append(target)

        def _process_hdr_ftr(zip_path: str, role: str) -> None:
            if zip_path not in zf_names or render_budget.truncated:
                return
            try:
                raw_hf = _read_zip_entry_capped(
                    zf, zip_path, _MAX_XML_BYTES, xml_budget=xml_budget
                )
                hf_root = _parse_xml_safe(raw_hf)
                part_name = posixpath.basename(zip_path)
                hf_rels_path = f"word/_rels/{part_name}.rels"
                hf_rels = _parse_relationships(
                    zf, zf_names, hf_rels_path, "word", xml_budget=xml_budget
                )
                hf_links = _parse_external_hyperlinks(
                    zf, zf_names, hf_rels_path, xml_budget=xml_budget
                )
                _process_block_sequence(
                    hf_root,
                    {**rels, **hf_rels},
                    {**hyperlinks, **hf_links},
                    section_role=role,
                )
            except _CONVERSION_ERRORS:
                pass

        for ht in header_targets[:2]:
            _process_hdr_ftr(ht, "header")

        if body is not None and not render_budget.truncated:
            _process_block_sequence(body, rels, hyperlinks, section_role="body")

        for ft_path in footer_targets[:2]:
            _process_hdr_ftr(ft_path, "footer")

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


def _pptx_align_from_algn(algn: str) -> str:
    val = (algn or "").strip().lower()
    if val == "ctr":
        return "center"
    if val == "r":
        return "right"
    if val in {"just", "dist"}:
        return "justify"
    return "left"


def _pptx_r_style(r_pr: Optional[ET.Element], *, href: str = "") -> dict[str, object]:
    bold = False
    italic = False
    underline = False
    strike = False
    superscript = False
    subscript = False
    if r_pr is not None:
        bold = str(r_pr.attrib.get("b") or "0") not in {"0", "false", ""}
        italic = str(r_pr.attrib.get("i") or "0") not in {"0", "false", ""}
        u_val = str(r_pr.attrib.get("u") or "none").lower()
        underline = u_val not in {"none", "0", "false", ""}
        st_val = str(r_pr.attrib.get("strike") or "noStrike").lower()
        strike = st_val not in {"nostrike", "0", "false", ""}
        raw_base = (r_pr.attrib.get("baseline") or "").strip()
        if raw_base:
            try:
                base_num = int(raw_base)
                if base_num > 0:
                    superscript = True
                elif base_num < 0:
                    subscript = True
            except ValueError:
                pass
    return {
        "bold": bold,
        "italic": italic,
        "underline": underline,
        "strike": strike,
        "superscript": superscript,
        "subscript": subscript,
        "href": href,
    }


def _pptx_tx_body_paragraphs(
    tx_body: ET.Element,
    hyperlinks: Optional[dict[str, str]] = None,
    *,
    is_title: bool = False,
    is_subtitle: bool = False,
    is_body_ph: bool = False,
) -> list[PptxTextParagraph]:
    """Extract formatted paragraphs, bullets, alignment, and math from DrawingML <p:txBody>."""
    link_map = hyperlinks or {}
    out: list[PptxTextParagraph] = []
    auto_counters: dict[int, int] = {}

    for p_el in tx_body.findall(f"{{{_A_NS}}}p"):
        p_pr = p_el.find(f"{{{_A_NS}}}pPr")
        level = 0
        align = "left"
        bullet = ""
        if p_pr is not None:
            raw_lvl = (p_pr.attrib.get("lvl") or "0").strip()
            if raw_lvl.isascii() and raw_lvl.isdigit():
                level = min(5, int(raw_lvl))
            align = _pptx_align_from_algn(p_pr.attrib.get("algn") or "")
            if p_pr.find(f"{{{_A_NS}}}buNone") is not None:
                bullet = ""
            elif p_pr.find(f"{{{_A_NS}}}buChar") is not None:
                bu_char = p_pr.find(f"{{{_A_NS}}}buChar")
                bullet = (bu_char.attrib.get("char") or "•") if bu_char is not None else "•"
            elif p_pr.find(f"{{{_A_NS}}}buAutoNum") is not None:
                bu_auto = p_pr.find(f"{{{_A_NS}}}buAutoNum")
                auto_type = (bu_auto.attrib.get("type") or "").lower() if bu_auto is not None else ""
                start_raw = (bu_auto.attrib.get("startAt") or "1") if bu_auto is not None else "1"
                start_at = int(start_raw) if start_raw.isascii() and start_raw.isdigit() else 1
                for k in list(auto_counters.keys()):
                    if k > level:
                        del auto_counters[k]
                cur = auto_counters[level] + 1 if level in auto_counters else start_at
                auto_counters[level] = cur
                if "alpha" in auto_type and "lc" in auto_type:
                    bullet = f"{_int_to_alpha(cur, upper=False)})"
                elif "alpha" in auto_type:
                    bullet = f"{_int_to_alpha(cur, upper=True)}."
                elif "roman" in auto_type and "lc" in auto_type:
                    bullet = f"{_int_to_roman(cur, upper=False)}."
                elif "roman" in auto_type:
                    bullet = f"{_int_to_roman(cur, upper=True)}."
                else:
                    bullet = f"{cur}."
            elif is_body_ph and not is_title and not is_subtitle:
                bullet = "•"
        elif is_body_ph and not is_title and not is_subtitle:
            bullet = "•"

        raw_runs: list[DocxTextRun] = []

        def _collect_inline(node: ET.Element) -> None:
            for sub in _iter_effective_children(node):
                if sub.tag == f"{{{_A_NS}}}r":
                    r_pr = sub.find(f"{{{_A_NS}}}rPr")
                    href = ""
                    if r_pr is not None:
                        hl = r_pr.find(f"{{{_A_NS}}}hlinkClick")
                        if hl is not None:
                            rid = hl.attrib.get(f"{{{_R_NS}}}id") or ""
                            href = link_map.get(rid, "")
                    st = _pptx_r_style(r_pr, href=href)
                    t_el = sub.find(f"{{{_A_NS}}}t")
                    if t_el is not None and t_el.text:
                        raw_runs.append(
                            DocxTextRun(
                                text=t_el.text,
                                bold=bool(st["bold"]),
                                italic=bool(st["italic"]),
                                underline=bool(st["underline"]),
                                strike=bool(st["strike"]),
                                superscript=bool(st["superscript"]),
                                subscript=bool(st["subscript"]),
                                href=str(st["href"]),
                            )
                        )
                elif sub.tag == f"{{{_A_NS}}}br":
                    raw_runs.append(DocxTextRun(text="\n"))
                elif sub.tag == f"{{{_A_NS}}}fld":
                    t_el = sub.find(f"{{{_A_NS}}}t")
                    if t_el is not None and t_el.text:
                        raw_runs.append(DocxTextRun(text=t_el.text))
                elif sub.tag in (f"{{{_M_NS}}}oMathPara", f"{{{_M_NS}}}oMath"):
                    math_txt = _extract_omml_text(sub).strip()
                    if math_txt:
                        raw_runs.append(DocxTextRun(text=math_txt))
                elif sub.tag == f"{{{_A_NS}}}m":
                    math_txt = _extract_omml_text(sub).strip()
                    if math_txt:
                        raw_runs.append(DocxTextRun(text=math_txt))
                elif sub.tag not in (f"{{{_A_NS}}}pPr", f"{{{_A_NS}}}endParaRPr"):
                    _collect_inline(sub)

        _collect_inline(p_el)
        coalesced = _coalesce_runs(raw_runs)
        joined = "".join(r.text for r in coalesced).strip()
        if joined:
            out.append(
                PptxTextParagraph(
                    text=joined,
                    runs=coalesced,
                    level=level,
                    bullet=bullet,
                    align=align,
                    is_title=is_title,
                    is_subtitle=is_subtitle,
                )
            )
    return out


def _extract_drawingml_text(root: ET.Element, *, skip_slide_num: bool = False) -> str:
    """Extract DrawingML <a:p> paragraph text lines from a PPTX slide or notes XML."""
    paragraphs: list[str] = []

    def _collect_from_tx_body(tx_body: ET.Element) -> None:
        for p_obj in _pptx_tx_body_paragraphs(tx_body):
            paragraphs.append(p_obj.text)

    if not skip_slide_num:
        def _walk(node: ET.Element) -> None:
            if node.tag == f"{{{_A_NS}}}tbl":
                return
            if node.tag in (f"{{{_P_NS}}}txBody", f"{{{_A_NS}}}txBody"):
                _collect_from_tx_body(node)
                return
            for sub in _iter_effective_children(node):
                _walk(sub)

        _walk(root)
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
) -> tuple[list[str], tuple[int, int]]:
    """Resolve PPTX slide archive paths and canonical EMU slide dimensions (cx, cy)."""
    ordered: list[str] = []
    seen: set[str] = set()
    slide_size = (9144000, 5143500)  # Default 16:9 widescreen EMU
    if "ppt/presentation.xml" in zf_names:
        pres_raw = _read_zip_entry_capped(
            zf, "ppt/presentation.xml", _MAX_XML_BYTES, xml_budget=xml_budget
        )
        pres_root = _parse_xml_safe(pres_raw)
        sld_sz = pres_root.find(f"{{{_P_NS}}}sldSz")
        if sld_sz is not None:
            try:
                cx = int(sld_sz.attrib.get("cx") or "0")
                cy = int(sld_sz.attrib.get("cy") or "0")
                if cx > 0 and cy > 0:
                    slide_size = (cx, cy)
            except ValueError:
                pass
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
                    return ordered, slide_size

    if ordered:
        return ordered, slide_size

    numbered: list[tuple[int, str]] = []
    for name in zf_names:
        match = _PPTX_SLIDE_PATH_PATTERN.fullmatch(name)
        if match:
            numbered.append((int(match.group(1)), name))
    numbered.sort(key=lambda item: item[0])
    return [name for _, name in numbered[:_MAX_PPTX_SLIDES]], slide_size


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
    idx: int,
    anchor_id: str,
    body_text: str,
    notes_text: str,
    *,
    title_text: str = "",
    subtitle_text: str = "",
    elements: tuple[PptxPlacedElement, ...] = (),
) -> tuple[str, str]:
    """Render HTML card and Markdown section for a single PPTX slide."""
    heading_label = f"Slide {idx}: {title_text}" if title_text else f"Slide {idx}"
    sub_html = (
        f'<div class="slide-subtitle">{html.escape(subtitle_text)}</div>'
        if subtitle_text
        else ""
    )
    elem_html_parts: list[str] = []
    for el in elements:
        if el.kind == "text":
            for p_obj in el.paragraphs:
                if p_obj.is_title or p_obj.is_subtitle:
                    continue
                inner = _render_runs_html(p_obj.runs, p_obj.text)
                if p_obj.bullet:
                    elem_html_parts.append(
                        f'<div class="doc-list" style="margin-left:{p_obj.level * 16}px">'
                        f'<span class="doc-list-marker">{html.escape(p_obj.bullet)}</span>'
                        f"<span>{inner}</span></div>"
                    )
                else:
                    align_attr = f' style="text-align:{p_obj.align}"' if p_obj.align != "left" else ""
                    elem_html_parts.append(f"<p{align_attr}>{inner}</p>")
        elif el.kind == "table" and el.table_rows:
            tbl_h, _ = _render_table_html_and_markdown(
                [list(r) for r in el.table_rows], sheet_style=False
            )
            if tbl_h:
                elem_html_parts.append(tbl_h)
        elif el.kind == "image" and el.image_bytes:
            ext = posixpath.splitext(el.image_name.lower())[1]
            mime = _SAFE_RASTER_MIMES.get(ext, "image/png")
            b64 = base64.b64encode(el.image_bytes).decode("ascii")
            elem_html_parts.append(
                '<figure class="doc-figure">'
                f'<img class="doc-image" src="data:{mime};base64,{b64}" '
                f'alt="{html.escape(el.image_name)}"/>'
                "</figure>"
            )

    if elem_html_parts:
        body_html = "".join(elem_html_parts)
    elif body_text:
        body_html = "<p>" + html.escape(body_text).replace("\n", "<br/>") + "</p>"
    elif title_text or subtitle_text:
        body_html = ""
    else:
        body_html = '<p class="muted">(Slide trống)</p>'

    notes_html = (
        '<div class="slide-notes"><strong>Ghi chú:</strong> '
        + html.escape(notes_text).replace("\n", "<br/>")
        + "</div>"
        if notes_text
        else ""
    )
    card_html = (
        f'<section class="slide-card" id="{anchor_id}">'
        f"<h3>{html.escape(heading_label)}</h3>{sub_html}{body_html}{notes_html}"
        "</section>"
    )
    md_parts = [f"### {heading_label}"]
    if subtitle_text:
        md_parts.append(f"*{subtitle_text}*")
    if body_text and body_text != title_text:
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
    title_text: str = "",
    subtitle_text: str = "",
    elements: tuple[PptxPlacedElement, ...] = (),
) -> Optional[tuple[str, str, bool]]:
    """Build a PPTX slide card within render budget, returning (html, md, did_truncate)."""
    raw_limit = min(render_budget.html_remaining, render_budget.md_remaining)
    pre_truncated = False
    rendered_body = body_text
    rendered_notes = notes_text
    rendered_elements = elements
    if len(rendered_body) > raw_limit or len(rendered_notes) > raw_limit:
        if has_prior_cards:
            render_budget.truncated = True
            return None
        half_limit = max(1, raw_limit // 12)
        rendered_body = _truncate_preview_text(rendered_body, half_limit)
        rendered_notes = _truncate_preview_text(rendered_notes, half_limit)
        rendered_elements = ()
        pre_truncated = True

    card_html, card_md = _build_pptx_slide_rendered(
        idx,
        anchor_id,
        rendered_body,
        rendered_notes,
        title_text=title_text,
        subtitle_text=subtitle_text,
        elements=rendered_elements,
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


def _extract_pptx_table_rows(tbl_el: ET.Element) -> tuple[tuple[str, ...], ...]:
    """Extract cell matrix from one DrawingML ``<a:tbl>`` element."""
    rows: list[tuple[str, ...]] = []
    for tr in tbl_el.findall(f"{{{_A_NS}}}tr")[:_MAX_TABLE_ROWS]:
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
    return tuple(rows)


def _extract_pptx_tables(slide_root: ET.Element) -> tuple[tuple[tuple[str, ...], ...], ...]:
    """Extract DrawingML ``<a:tbl>`` grids from a slide XML tree."""
    tables: list[tuple[tuple[str, ...], ...]] = []
    for tbl in slide_root.findall(f".//{{{_A_NS}}}tbl"):
        rows = _extract_pptx_table_rows(tbl)
        if rows:
            tables.append(rows)
    return tuple(tables)


def _parse_xfrm_emu(
    xfrm: Optional[ET.Element],
) -> Optional[tuple[int, int, int, int]]:
    if xfrm is None:
        return None
    off = xfrm.find(f"{{{_A_NS}}}off")
    ext = xfrm.find(f"{{{_A_NS}}}ext")
    if off is None or ext is None:
        return None
    try:
        x = int(off.attrib.get("x") or "0")
        y = int(off.attrib.get("y") or "0")
        cx = int(ext.attrib.get("cx") or "0")
        cy = int(ext.attrib.get("cy") or "0")
        return (x, y, max(1, cx), max(1, cy))
    except ValueError:
        return None


def _parse_pptx_layout_placeholders(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    layout_path: str,
    *,
    xml_budget: list[int],
) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    """Parse placeholder geometry overrides from a slideLayout XML file."""
    if not layout_path or layout_path not in zf_names:
        return {}
    try:
        raw = _read_zip_entry_capped(
            zf, layout_path, _MAX_XML_BYTES, xml_budget=xml_budget
        )
        root = _parse_xml_safe(raw)
    except _CONVERSION_ERRORS:
        return {}
    mapping: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for sp in root.findall(f".//{{{_P_NS}}}sp"):
        ph = sp.find(f".//{{{_P_NS}}}ph")
        if ph is None:
            continue
        ph_type = (ph.attrib.get("type") or "").strip().lower()
        ph_idx = (ph.attrib.get("idx") or "").strip()
        sp_pr = sp.find(f"{{{_P_NS}}}spPr")
        rect = _parse_xfrm_emu(sp_pr.find(f"{{{_A_NS}}}xfrm") if sp_pr is not None else None)
        if rect is not None:
            if (ph_type, ph_idx) not in mapping:
                mapping[(ph_type, ph_idx)] = rect
            if ph_type and (ph_type, "") not in mapping:
                mapping[(ph_type, "")] = rect
    return mapping


def _extract_smartart_lines(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    data_path: str,
    *,
    xml_budget: list[int],
) -> list[str]:
    """Extract logical node text from a SmartArt diagram data XML file."""
    if not data_path or data_path not in zf_names:
        return []
    try:
        raw = _read_zip_entry_capped(
            zf, data_path, _MAX_XML_BYTES, xml_budget=xml_budget
        )
        root = _parse_xml_safe(raw)
    except _CONVERSION_ERRORS:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for pt in root.findall(f".//{{{_DGM_NS}}}pt"):
        pt_type = (pt.attrib.get("type") or "").strip().lower()
        if pt_type in {"pres", "partrans", "sibtrans"}:
            continue
        t_el = pt.find(f"{{{_DGM_NS}}}t")
        if t_el is None:
            continue
        parts = ["".join(t.text or "" for t in p.findall(f".//{{{_A_NS}}}t")).strip() for p in t_el.findall(f".//{{{_A_NS}}}p")]
        txt = " — ".join(p for p in parts if p).strip()
        if txt and txt not in seen:
            seen.add(txt)
            lines.append(txt)
    return lines


def _extract_chart_lines(
    zf: zipfile.ZipFile,
    zf_names: set[str],
    chart_path: str,
    *,
    xml_budget: list[int],
) -> list[str]:
    """Extract chart title and series/category labels from a DrawingML chart XML file."""
    if not chart_path or chart_path not in zf_names:
        return []
    try:
        raw = _read_zip_entry_capped(
            zf, chart_path, _MAX_XML_BYTES, xml_budget=xml_budget
        )
        root = _parse_xml_safe(raw)
    except _CONVERSION_ERRORS:
        return []
    lines: list[str] = []
    title_el = root.find(f".//{{{_C_NS}}}title")
    if title_el is not None:
        title_parts = [t.text.strip() for t in title_el.findall(f".//{{{_A_NS}}}t") if t.text and t.text.strip()]
        if title_parts:
            lines.append("Biểu đồ: " + " ".join(title_parts))
    ser_names: list[str] = []
    for tx in root.findall(f".//{{{_C_NS}}}ser/{{{_C_NS}}}tx"):
        vals = [v.text.strip() for v in tx.findall(f".//{{{_C_NS}}}v") if v.text and v.text.strip()]
        if not vals:
            vals = [t.text.strip() for t in tx.findall(f".//{{{_A_NS}}}t") if t.text and t.text.strip()]
        if vals:
            ser_names.extend(vals)
    if ser_names:
        lines.append("Chuỗi dữ liệu: " + ", ".join(ser_names[:12]))
    return lines


def _maybe_crop_image_bytes(raw_bytes: bytes, ext: str, blip_fill: Optional[ET.Element]) -> bytes:
    """Apply DrawingML <a:srcRect> percentages to raster image bytes when present."""
    if _PILImage is None or blip_fill is None:
        return raw_bytes
    src_rect = blip_fill.find(f"{{{_A_NS}}}srcRect")
    if src_rect is None:
        return raw_bytes
    try:
        left_p = max(0, int(src_rect.attrib.get("l") or "0"))
        top_p = max(0, int(src_rect.attrib.get("t") or "0"))
        right_p = max(0, int(src_rect.attrib.get("r") or "0"))
        bottom_p = max(0, int(src_rect.attrib.get("b") or "0"))
    except ValueError:
        return raw_bytes
    if left_p == top_p == right_p == bottom_p == 0 or (left_p + right_p) >= 98_000 or (top_p + bottom_p) >= 98_000:
        return raw_bytes
    try:
        with _PILImage.open(io.BytesIO(raw_bytes)) as img:
            w, h = img.size
            x0 = int(round(w * (left_p / 100_000.0)))
            y0 = int(round(h * (top_p / 100_000.0)))
            x1 = max(x0 + 1, int(round(w * (1.0 - right_p / 100_000.0))))
            y1 = max(y0 + 1, int(round(h * (1.0 - bottom_p / 100_000.0))))
            cropped = img.crop((x0, y0, x1, y1))
            out = io.BytesIO()
            fmt = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
            if fmt == "JPEG" and cropped.mode not in ("RGB", "L"):
                cropped = cropped.convert("RGB")
            cropped.save(out, format=fmt)
            return out.getvalue()
    except Exception:
        return raw_bytes


def _convert_pptx(title: str, data: bytes, *, dark_mode: bool) -> ConvertedDocument:
    """Convert PPTX bytes into ordered visually-structured slides, speaker notes, HTML, and Markdown."""
    slides: list[tuple[int, str, str]] = []
    rich_slides: list[PptxSlideRich] = []
    outline: list[DocumentOutlineItem] = []
    html_cards: list[str] = []
    md_sections: list[str] = []
    media_state = {"count": 0, "bytes": 0}
    media_cache: dict[str, bytes] = {}
    layout_cache: dict[str, dict[tuple[str, str], tuple[int, int, int, int]]] = {}
    xml_budget = [_MAX_TOTAL_XML_BYTES]
    render_budget = _RenderBudget.for_title(title)

    with _open_ooxml_zip(data) as zf:
        zf_names = set(zf.namelist())
        slide_paths, (slide_w_emu, slide_h_emu) = _resolve_pptx_slide_paths(
            zf, zf_names, xml_budget=xml_budget
        )
        aspect_ratio = max(0.8, min(3.0, slide_w_emu / max(1, slide_h_emu)))

        explicit_notes: list[tuple[bool, Optional[str]]] = []
        claimed_notes: set[str] = set()
        slide_media_targets: list[list[str]] = []
        slide_all_rels: list[dict[str, str]] = []
        slide_hyperlinks: list[dict[str, str]] = []
        slide_layout_paths: list[str] = []
        for slide_path in slide_paths:
            slide_dir = posixpath.dirname(slide_path)
            slide_name = posixpath.basename(slide_path)
            rels_path = f"{slide_dir}/_rels/{slide_name}.rels"
            has_rels = rels_path in zf_names
            mapped_note: Optional[str] = None
            media_targets: list[str] = []
            all_rels: dict[str, str] = {}
            hlinks: dict[str, str] = {}
            layout_path = ""
            if has_rels:
                all_rels = _parse_relationships(
                    zf, zf_names, rels_path, slide_dir, xml_budget=xml_budget
                )
                hlinks = _parse_external_hyperlinks(
                    zf, zf_names, rels_path, xml_budget=xml_budget
                )
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
                layout_rels = _parse_relationships(
                    zf,
                    zf_names,
                    rels_path,
                    slide_dir,
                    type_suffix="/slidelayout",
                    xml_budget=xml_budget,
                )
                for candidate in layout_rels.values():
                    if candidate in zf_names:
                        layout_path = candidate
                        break
            explicit_notes.append((has_rels, mapped_note))
            slide_media_targets.append(media_targets)
            slide_all_rels.append(all_rels)
            slide_hyperlinks.append(hlinks)
            slide_layout_paths.append(layout_path)

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

        def _load_slide_image_target(target: str) -> Optional[tuple[str, bytes]]:
            if not target or target not in zf_names:
                return None
            ext = posixpath.splitext(target.lower())[1]
            if ext not in _SAFE_RASTER_MIMES and ext not in _CONVERTIBLE_VECTOR_RASTER_EXTS:
                return None
            if target in media_cache:
                return (posixpath.basename(target), media_cache[target])
            if media_state["count"] >= _MAX_MEDIA_COUNT:
                return None
            try:
                raw_img = _read_zip_entry_capped(zf, target, _MAX_MEDIA_BYTES)
            except _CONVERSION_ERRORS:
                return None
            normalized = _normalize_image_payload(ext, raw_img) if raw_img else None
            if normalized is None:
                return None
            _mime, final_bytes = normalized
            if media_state["bytes"] + len(final_bytes) > _MAX_TOTAL_MEDIA_BYTES:
                return None
            media_state["count"] += 1
            media_state["bytes"] += len(final_bytes)
            media_cache[target] = final_bytes
            return (posixpath.basename(target), final_bytes)

        for idx, (
            _slide_path,
            (has_rels, _),
            pre_resolved_note,
            media_targets,
            all_rels,
            hlinks,
            layout_path,
        ) in enumerate(
            zip(
                slide_paths,
                explicit_notes,
                resolved_notes,
                slide_media_targets,
                slide_all_rels,
                slide_hyperlinks,
                slide_layout_paths,
            ),
            start=1,
        ):
            if render_budget.truncated:
                break
            slide_raw = _read_zip_entry_capped(
                zf, _slide_path, _MAX_XML_BYTES, xml_budget=xml_budget
            )
            slide_root = _parse_xml_safe(slide_raw)
            if layout_path and layout_path not in layout_cache:
                layout_cache[layout_path] = _parse_pptx_layout_placeholders(
                    zf, zf_names, layout_path, xml_budget=xml_budget
                )
            layout_map = layout_cache.get(layout_path, {})

            placed_elements: list[PptxPlacedElement] = []
            placed_targets: set[str] = set()
            slide_tables_list: list[tuple[tuple[str, ...], ...]] = []
            slide_images: list[tuple[str, bytes]] = []

            def _norm_rect(
                emu_rect: Optional[tuple[int, int, int, int]],
                scale_x: float,
                scale_y: float,
                off_x: float,
                off_y: float,
            ) -> tuple[float, float, float, float]:
                if emu_rect is None:
                    return (0.04, 0.15, 0.92, 0.25)
                rx, ry, rcw, rch = emu_rect
                gx = off_x + rx * scale_x
                gy = off_y + ry * scale_y
                gw = rcw * scale_x
                gh = rch * scale_y
                return (
                    max(0.0, min(0.95, gx / max(1, slide_w_emu))),
                    max(0.0, min(0.95, gy / max(1, slide_h_emu))),
                    max(0.04, min(1.0, gw / max(1, slide_w_emu))),
                    max(0.04, min(1.0, gh / max(1, slide_h_emu))),
                )

            def _walk_sp_tree(
                container: ET.Element,
                *,
                scale_x: float = 1.0,
                scale_y: float = 1.0,
                off_x: float = 0.0,
                off_y: float = 0.0,
            ) -> None:
                for node in _iter_effective_children(container):
                    if node.tag == f"{{{_P_NS}}}grpSp":
                        grp_pr = node.find(f"{{{_P_NS}}}grpSpPr")
                        xfrm = grp_pr.find(f"{{{_A_NS}}}xfrm") if grp_pr is not None else None
                        outer = _parse_xfrm_emu(xfrm)
                        ch_off = xfrm.find(f"{{{_A_NS}}}chOff") if xfrm is not None else None
                        ch_ext = xfrm.find(f"{{{_A_NS}}}chExt") if xfrm is not None else None
                        next_sx, next_sy, next_ox, next_oy = scale_x, scale_y, off_x, off_y
                        if outer is not None and ch_off is not None and ch_ext is not None:
                            try:
                                cox = int(ch_off.attrib.get("x") or "0")
                                coy = int(ch_off.attrib.get("y") or "0")
                                ccx = max(1, int(ch_ext.attrib.get("cx") or "1"))
                                ccy = max(1, int(ch_ext.attrib.get("cy") or "1"))
                                sx_local = outer[2] / ccx
                                sy_local = outer[3] / ccy
                                next_ox = off_x + (outer[0] - cox * sx_local) * scale_x
                                next_oy = off_y + (outer[1] - coy * sy_local) * scale_y
                                next_sx = scale_x * sx_local
                                next_sy = scale_y * sy_local
                            except ValueError:
                                pass
                        _walk_sp_tree(
                            node,
                            scale_x=next_sx,
                            scale_y=next_sy,
                            off_x=next_ox,
                            off_y=next_oy,
                        )
                    elif node.tag == f"{{{_P_NS}}}sp":
                        nv_pr = node.find(f".//{{{_P_NS}}}nvPr")
                        ph = nv_pr.find(f"{{{_P_NS}}}ph") if nv_pr is not None else None
                        ph_type = (ph.attrib.get("type") or "").strip().lower() if ph is not None else ""
                        ph_idx = (ph.attrib.get("idx") or "").strip() if ph is not None else ""
                        if ph_type == "sldnum":
                            continue
                        tx_body = node.find(f"{{{_P_NS}}}txBody")
                        if tx_body is None:
                            continue
                        is_title = ph_type in {"title", "ctrtitle"}
                        is_sub = ph_type == "subtitle"
                        is_body_ph = ph_type in {"body", "obj", ""} and ph is not None
                        paras = _pptx_tx_body_paragraphs(
                            tx_body,
                            hlinks,
                            is_title=is_title,
                            is_subtitle=is_sub,
                            is_body_ph=is_body_ph,
                        )
                        if not paras:
                            continue
                        sp_pr = node.find(f"{{{_P_NS}}}spPr")
                        rect = _parse_xfrm_emu(
                            sp_pr.find(f"{{{_A_NS}}}xfrm") if sp_pr is not None else None
                        )
                        if rect is None and ph is not None:
                            rect = layout_map.get((ph_type, ph_idx)) or layout_map.get((ph_type, ""))
                        xr, yr, wr, hr = _norm_rect(rect, scale_x, scale_y, off_x, off_y)
                        placed_elements.append(
                            PptxPlacedElement(
                                kind="text",
                                x_ratio=xr,
                                y_ratio=yr,
                                w_ratio=wr,
                                h_ratio=hr,
                                paragraphs=tuple(paras),
                            )
                        )
                    elif node.tag == f"{{{_P_NS}}}pic":
                        blip_fill = node.find(f"{{{_P_NS}}}blipFill")
                        blip = blip_fill.find(f".//{{{_A_NS}}}blip") if blip_fill is not None else None
                        rid = blip.attrib.get(f"{{{_R_NS}}}embed") if blip is not None else None
                        target = all_rels.get(rid or "")
                        if not target:
                            continue
                        loaded = _load_slide_image_target(target)
                        if loaded is None:
                            continue
                        placed_targets.add(target)
                        img_name, img_bytes = loaded
                        ext = posixpath.splitext(target.lower())[1]
                        img_bytes = _maybe_crop_image_bytes(img_bytes, ext, blip_fill)
                        slide_images.append((img_name, img_bytes))
                        sp_pr = node.find(f"{{{_P_NS}}}spPr")
                        rect = _parse_xfrm_emu(
                            sp_pr.find(f"{{{_A_NS}}}xfrm") if sp_pr is not None else None
                        )
                        xr, yr, wr, hr = _norm_rect(rect, scale_x, scale_y, off_x, off_y)
                        placed_elements.append(
                            PptxPlacedElement(
                                kind="image",
                                x_ratio=xr,
                                y_ratio=yr,
                                w_ratio=wr,
                                h_ratio=hr,
                                image_name=img_name,
                                image_bytes=img_bytes,
                            )
                        )
                    elif node.tag == f"{{{_P_NS}}}graphicFrame":
                        xfrm = node.find(f"{{{_P_NS}}}xfrm")
                        rect = _parse_xfrm_emu(xfrm)
                        xr, yr, wr, hr = _norm_rect(rect, scale_x, scale_y, off_x, off_y)
                        tbl_el = node.find(f".//{{{_A_NS}}}tbl")
                        if tbl_el is not None:
                            rows = _extract_pptx_table_rows(tbl_el)
                            if rows:
                                slide_tables_list.append(rows)
                                placed_elements.append(
                                    PptxPlacedElement(
                                        kind="table",
                                        x_ratio=xr,
                                        y_ratio=yr,
                                        w_ratio=wr,
                                        h_ratio=hr,
                                        table_rows=rows,
                                    )
                                )
                            continue
                        rel_ids = node.find(f".//{{{_DGM_NS}}}relIds")
                        if rel_ids is not None:
                            dm_rid = rel_ids.attrib.get(f"{{{_R_NS}}}dm") or ""
                            dm_target = all_rels.get(dm_rid)
                            dgm_lines = _extract_smartart_lines(
                                zf, zf_names, dm_target or "", xml_budget=xml_budget
                            )
                            if dgm_lines:
                                dgm_paras = tuple(
                                    PptxTextParagraph(
                                        text=ln,
                                        runs=(DocxTextRun(text=ln),),
                                        bullet="•",
                                    )
                                    for ln in dgm_lines
                                )
                                placed_elements.append(
                                    PptxPlacedElement(
                                        kind="text",
                                        x_ratio=xr,
                                        y_ratio=yr,
                                        w_ratio=wr,
                                        h_ratio=hr,
                                        paragraphs=dgm_paras,
                                    )
                                )
                            continue
                        chart_el = node.find(f".//{{{_C_NS}}}chart")
                        if chart_el is not None:
                            ch_rid = chart_el.attrib.get(f"{{{_R_NS}}}id") or ""
                            ch_target = all_rels.get(ch_rid)
                            ch_lines = _extract_chart_lines(
                                zf, zf_names, ch_target or "", xml_budget=xml_budget
                            )
                            if ch_lines:
                                ch_paras = tuple(
                                    PptxTextParagraph(
                                        text=ln,
                                        runs=(DocxTextRun(text=ln),),
                                    )
                                    for ln in ch_lines
                                )
                                placed_elements.append(
                                    PptxPlacedElement(
                                        kind="text",
                                        x_ratio=xr,
                                        y_ratio=yr,
                                        w_ratio=wr,
                                        h_ratio=hr,
                                        paragraphs=ch_paras,
                                    )
                                )

            sp_tree = slide_root.find(f".//{{{_P_NS}}}cSld/{{{_P_NS}}}spTree")
            if sp_tree is not None:
                _walk_sp_tree(sp_tree)

            # Visual reading order (top-to-bottom, left-to-right within horizontal bands)
            placed_elements.sort(key=lambda el: (round(el.y_ratio, 2), round(el.x_ratio, 2)))

            # Include any remaining raster slide rels not referenced inside <p:pic>
            for target in media_targets:
                if target in placed_targets:
                    continue
                loaded = _load_slide_image_target(target)
                if loaded is not None:
                    slide_images.append(loaded)

            title_parts: list[str] = []
            subtitle_parts: list[str] = []
            body_lines: list[str] = []
            plain_slides_lines: list[str] = []
            for el in placed_elements:
                if el.kind == "text":
                    for p_obj in el.paragraphs:
                        body_lines.append(p_obj.text)
                        plain_slides_lines.append(p_obj.text)
                        if p_obj.is_title:
                            title_parts.append(p_obj.text)
                        elif p_obj.is_subtitle:
                            subtitle_parts.append(p_obj.text)
                elif el.kind == "table":
                    for row in el.table_rows:
                        for cell_txt in row:
                            if cell_txt:
                                plain_slides_lines.append(cell_txt)

            title_text = " ".join(title_parts).strip()
            subtitle_text = "\n".join(subtitle_parts).strip()
            body_text = "\n".join(body_lines)
            plain_body_text = "\n".join(plain_slides_lines)
            slide_tables = tuple(slide_tables_list)

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
                title_text=title_text,
                subtitle_text=subtitle_text,
                elements=tuple(placed_elements),
            )
            if rendered is None:
                break
            card_html, card_md, stop_after = rendered

            slides.append((idx, plain_body_text, notes_text))
            rich_slides.append(
                PptxSlideRich(
                    slide_number=idx,
                    anchor_id=anchor_id,
                    body_text=body_text,
                    notes_text=notes_text,
                    images=tuple(slide_images),
                    tables=slide_tables,
                    title_text=title_text,
                    subtitle_text=subtitle_text,
                    aspect_ratio=aspect_ratio,
                    elements=tuple(placed_elements),
                )
            )
            first_line = title_text or next(
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
