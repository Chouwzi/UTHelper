"""Native cross-platform PDF rendering, text extraction, search, and disk bitmap caching."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import io
import os
from pathlib import Path
import threading
import time
from typing import Any, Optional
import uuid

_UNSET: Any = object()
_BACKEND_LOCK = threading.Lock()
_pdfium: Any = _UNSET
_pymupdf: Any = _UNSET
_pypdf: Any = _UNSET
_PilImage: Any = _UNSET


def _get_pdfium() -> Any:
    global _pdfium
    if _pdfium is not _UNSET:
        return _pdfium
    with _BACKEND_LOCK:
        if _pdfium is _UNSET:
            try:
                import pypdfium2 as mod
            except Exception:  # pragma: no cover - optional on minimal runtimes
                mod = None  # type: ignore[assignment]
            _pdfium = mod
        return _pdfium


def _get_pymupdf() -> Any:
    global _pymupdf
    if _pymupdf is not _UNSET:
        return _pymupdf
    with _BACKEND_LOCK:
        if _pymupdf is _UNSET:
            try:
                import pymupdf as mod
            except Exception:  # pragma: no cover - optional runtime fallback
                try:
                    import fitz as mod  # type: ignore[no-redef]
                except Exception:  # pragma: no cover
                    mod = None  # type: ignore[assignment]
            _pymupdf = mod
        return _pymupdf


def _get_pypdf() -> Any:
    global _pypdf
    if _pypdf is not _UNSET:
        return _pypdf
    with _BACKEND_LOCK:
        if _pypdf is _UNSET:
            try:
                import pypdf as mod
            except Exception:  # pragma: no cover
                mod = None  # type: ignore[assignment]
            _pypdf = mod
        return _pypdf


def _get_pil_image() -> Any:
    global _PilImage
    if _PilImage is not _UNSET:
        return _PilImage
    with _BACKEND_LOCK:
        if _PilImage is _UNSET:
            try:
                from PIL import Image as mod
            except Exception:  # pragma: no cover
                mod = None  # type: ignore[assignment]
            _PilImage = mod
        return _PilImage


_DEFAULT_RENDERED_PDF_MAX_BYTES = 96 * 1024 * 1024
_DEFAULT_MEMORY_CACHE_PAGES = 28
_DEFAULT_MEMORY_CACHE_BYTES = 32 * 1024 * 1024
_MIN_SCALE = 0.5
_MAX_SCALE = 3.5
_DARK_LUMA_LUT: list[int] = [
    max(22, min(234, 244 - int(val * 0.87))) for val in range(256)
]


@dataclass(frozen=True, slots=True)
class PdfOutlineEntry:
    """Single bookmark/outline entry extracted from a PDF."""

    title: str
    page_index: int
    level: int = 0


@dataclass(frozen=True, slots=True)
class PdfSearchHit:
    """Matching page result produced by in-PDF text search."""

    page_index: int
    snippet: str
    match_count: int = 1


@dataclass(frozen=True, slots=True)
class RenderedPdfPage:
    """Rendered raster PNG payload and metadata for a single PDF page."""

    page_index: int
    png_bytes: bytes
    width: int
    height: int
    scale: float
    dark_mode: bool
    from_cache: bool = False


def clear_rendered_pdf_cache(cache_dir: Optional[Path]) -> int:
    """Remove cached rendered PDF page PNGs under ``cache_dir / 'rendered_pdf'``."""
    if cache_dir is None:
        return 0
    rendered_dir = Path(cache_dir) / "rendered_pdf"
    if not rendered_dir.is_dir():
        return 0
    removed = 0
    for entry in rendered_dir.iterdir():
        if entry.is_file():
            try:
                entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


def _invert_image_luminance(image: Any) -> Any:
    """Invert luminance smoothly for dark reading using a C-speed lookup table."""
    pil_image = _get_pil_image()
    rgb = image.convert("RGB")
    y_chan, cb_chan, cr_chan = rgb.convert("YCbCr").split()
    mapped_y = y_chan.point(_DARK_LUMA_LUT)
    return pil_image.merge("YCbCr", (mapped_y, cb_chan, cr_chan)).convert("RGB")


def _encode_pil_png(image: Any) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", compress_level=1)
    return buf.getvalue()


def _extract_snippet(text: str, query_lower: str) -> tuple[str, int]:
    """Return first contextual snippet and occurrence count for ``query_lower``."""
    if not text or not query_lower:
        return ("", 0)
    normalized = " ".join(text.split())
    lower = normalized.lower()
    count = lower.count(query_lower)
    if count <= 0:
        return ("", 0)
    idx = lower.find(query_lower)
    start = max(0, idx - 42)
    end = min(len(normalized), idx + len(query_lower) + 64)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(normalized) else ""
    return (f"{prefix}{normalized[start:end].strip()}{suffix}", count)


class PdfDocumentSession:
    """Thread-safe native PDF document session with outline, text search, and LRU PNG cache."""

    def __init__(
        self,
        data: bytes,
        *,
        cache_dir: Optional[Path] = None,
        cache_max_bytes: int = _DEFAULT_RENDERED_PDF_MAX_BYTES,
    ) -> None:
        self._data = bytes(data or b"")
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._rendered_dir = (
            self._cache_dir / "rendered_pdf" if self._cache_dir is not None else None
        )
        self._cache_max_bytes = max(128, int(cache_max_bytes))
        self._lock = threading.RLock()
        self._doc: Any = None
        self._mupdf_doc: Any = None
        self._pypdf_reader: Any = None
        self._memory_cache: OrderedDict[tuple[int, int, bool], RenderedPdfPage] = OrderedDict()
        self._text_cache: dict[int, str] = {}
        self._closed = False

        self.doc_hash = hashlib.sha256(self._data).hexdigest()[:24]
        self.supported = False
        self.can_render_images = False
        self.page_count = 0
        self.error_message = ""
        self.outline: list[PdfOutlineEntry] = []

        self._initialize()

    def _initialize(self) -> None:
        if not self._data or b"%PDF-" not in self._data[:1024]:
            self.error_message = "Tệp không phải định dạng PDF hợp lệ."
            return

        pdfium_mod = _get_pdfium()
        pil_image = _get_pil_image()
        if pdfium_mod is not None and pil_image is not None:
            try:
                doc = pdfium_mod.PdfDocument(self._data)
                count = int(len(doc))
                if count <= 0:
                    doc.close()
                    self.error_message = "Tài liệu PDF không có trang nào."
                    return
                self._doc = doc
                self.page_count = count
                self.supported = True
                self.can_render_images = True
                self.outline = self._read_pdfium_outline(doc, count)
                return
            except Exception as exc:
                err_text = str(exc).lower()
                if "password" in err_text or "encrypt" in err_text:
                    self.error_message = "Tệp PDF được bảo vệ bằng mật khẩu."
                else:
                    self.error_message = f"Không thể mở PDF trực tiếp: {exc}"
                return

        pymupdf_mod = _get_pymupdf()
        if pymupdf_mod is not None and pil_image is not None:
            try:
                mupdf_doc = pymupdf_mod.open(stream=self._data, filetype="pdf")
                if getattr(mupdf_doc, "needs_pass", False):
                    try:
                        mupdf_doc.close()
                    except Exception:
                        pass
                    self.error_message = "Tệp PDF được bảo vệ bằng mật khẩu."
                    return
                count = int(getattr(mupdf_doc, "page_count", 0) or len(mupdf_doc))
                if count <= 0:
                    try:
                        mupdf_doc.close()
                    except Exception:
                        pass
                    self.error_message = "Tài liệu PDF không có trang nào."
                    return
                self._mupdf_doc = mupdf_doc
                self.page_count = count
                self.supported = True
                self.can_render_images = True
                self.outline = self._read_pymupdf_outline(mupdf_doc, count)
                return
            except Exception as exc:
                err_text = str(exc).lower()
                if "password" in err_text or "encrypt" in err_text:
                    self.error_message = "Tệp PDF được bảo vệ bằng mật khẩu."
                else:
                    self.error_message = f"Không thể mở PDF trực tiếp: {exc}"
                return

        pypdf_mod = _get_pypdf()
        if pypdf_mod is not None:
            try:
                reader = pypdf_mod.PdfReader(io.BytesIO(self._data))
                if getattr(reader, "is_encrypted", False):
                    self.error_message = "Tệp PDF được bảo vệ bằng mật khẩu."
                    return
                count = len(reader.pages)
                if count <= 0:
                    self.error_message = "Tài liệu PDF không có trang nào."
                    return
                self._pypdf_reader = reader
                self.page_count = count
                self.supported = True
                self.can_render_images = False
                self.outline = self._read_pypdf_outline(reader, count)
            except Exception as exc:
                self.error_message = f"Không thể đọc PDF: {exc}"
        else:
            self.error_message = "Thiếu thư viện đọc PDF trên nền tảng này."

    @staticmethod
    def _read_pdfium_outline(doc: Any, page_count: int) -> list[PdfOutlineEntry]:
        entries: list[PdfOutlineEntry] = []
        try:
            toc = doc.get_toc(max_depth=6)
        except Exception:
            return entries
        for item in toc:
            raw_title: Any = ""
            if callable(getattr(item, "get_title", None)):
                try:
                    raw_title = item.get_title()
                except Exception:
                    raw_title = ""
            else:
                raw_title = getattr(item, "title", "")
            title = str(raw_title or "").strip()

            page_idx: Any = None
            if callable(getattr(item, "get_dest", None)):
                try:
                    dest = item.get_dest()
                    if dest is not None and callable(getattr(dest, "get_index", None)):
                        page_idx = dest.get_index()
                except Exception:
                    page_idx = None
            if page_idx is None:
                page_idx = getattr(item, "page_index", None)

            if callable(getattr(item, "get_level", None)):
                try:
                    raw_level = item.get_level()
                except Exception:
                    raw_level = 0
            else:
                raw_level = getattr(item, "level", 0)
            level = int(raw_level or 0)

            if (
                title
                and isinstance(page_idx, int)
                and not isinstance(page_idx, bool)
                and 0 <= page_idx < page_count
            ):
                entries.append(
                    PdfOutlineEntry(title=title, page_index=page_idx, level=max(0, level))
                )
            if len(entries) >= 200:
                break
        return entries

    @staticmethod
    def _read_pypdf_outline(reader: Any, page_count: int) -> list[PdfOutlineEntry]:
        entries: list[PdfOutlineEntry] = []
        try:
            raw_outline = getattr(reader, "outline", []) or []
        except Exception:
            return entries

        def _walk(nodes: list[Any], level: int) -> None:
            for node in nodes:
                if len(entries) >= 200:
                    return
                if isinstance(node, list):
                    _walk(node, level + 1)
                    continue
                title = str(getattr(node, "title", "") or "").strip()
                try:
                    page_idx = reader.get_destination_page_number(node)
                except Exception:
                    page_idx = None
                if title and isinstance(page_idx, int) and 0 <= page_idx < page_count:
                    entries.append(
                        PdfOutlineEntry(title=title, page_index=page_idx, level=level)
                    )

        _walk(list(raw_outline), 0)
        return entries

    @staticmethod
    def _read_pymupdf_outline(doc: Any, page_count: int) -> list[PdfOutlineEntry]:
        entries: list[PdfOutlineEntry] = []
        try:
            toc = doc.get_toc(simple=True)
        except Exception:
            return entries
        for row in toc or ():
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            try:
                raw_level = int(row[0])
                title = str(row[1] or "").strip()
                page_idx = int(row[2]) - 1
            except (TypeError, ValueError):
                continue
            if title and 0 <= page_idx < page_count:
                entries.append(
                    PdfOutlineEntry(
                        title=title,
                        page_index=page_idx,
                        level=max(0, raw_level - 1),
                    )
                )
            if len(entries) >= 200:
                break
        return entries

    def _cache_key_parts(
        self, page_index: int, scale: float, dark_mode: bool
    ) -> tuple[int, int, bool]:
        clamped_scale = max(_MIN_SCALE, min(_MAX_SCALE, float(scale)))
        scale_pct = int(round(clamped_scale * 100))
        return (int(page_index), scale_pct, bool(dark_mode))

    def _disk_path_for(self, key: tuple[int, int, bool]) -> Optional[Path]:
        if self._rendered_dir is None:
            return None
        page_idx, scale_pct, dark_mode = key
        return self._rendered_dir / (
            f"{self.doc_hash}_p{page_idx}_s{scale_pct}_d{1 if dark_mode else 0}.png"
        )

    def _read_disk_cache(self, key: tuple[int, int, bool], scale: float) -> Optional[RenderedPdfPage]:
        path = self._disk_path_for(key)
        pil_image = _get_pil_image()
        if path is None or not path.is_file() or pil_image is None:
            return None
        try:
            png_bytes = path.read_bytes()
            if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                return None
            with pil_image.open(io.BytesIO(png_bytes)) as img:
                width, height = img.size
            now = time.time()
            try:
                os.utime(path, (now, now))
            except OSError:
                pass
            return RenderedPdfPage(
                page_index=key[0],
                png_bytes=png_bytes,
                width=int(width),
                height=int(height),
                scale=scale,
                dark_mode=key[2],
                from_cache=True,
            )
        except OSError:
            return None
        except Exception:
            return None

    def _write_disk_cache(self, key: tuple[int, int, bool], png_bytes: bytes) -> None:
        path = self._disk_path_for(key)
        if path is None or not png_bytes:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.parent / f".tmp.{uuid.uuid4().hex}"
            tmp_path.write_bytes(png_bytes)
            os.replace(tmp_path, path)
            self._evict_rendered_cache_if_needed()
        except OSError:
            pass

    def _evict_rendered_cache_if_needed(self) -> None:
        if self._rendered_dir is None or not self._rendered_dir.is_dir():
            return
        entries: list[tuple[float, int, Path]] = []
        total = 0
        for item in self._rendered_dir.iterdir():
            if not item.is_file() or item.name.startswith(".tmp."):
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            total += stat.st_size
            entries.append((stat.st_mtime, stat.st_size, item))
        if total <= self._cache_max_bytes:
            return
        entries.sort(key=lambda row: row[0])
        for _mtime, size, item in entries:
            if total <= self._cache_max_bytes:
                break
            try:
                item.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass

    def _remember_in_memory(self, key: tuple[int, int, bool], rendered: RenderedPdfPage) -> None:
        self._memory_cache[key] = rendered
        self._memory_cache.move_to_end(key)
        total_bytes = sum(len(item.png_bytes) for item in self._memory_cache.values())
        while (
            len(self._memory_cache) > _DEFAULT_MEMORY_CACHE_PAGES
            or (total_bytes > _DEFAULT_MEMORY_CACHE_BYTES and len(self._memory_cache) > 1)
        ):
            _, evicted = self._memory_cache.popitem(last=False)
            total_bytes -= len(evicted.png_bytes)

    def get_page_size(self, page_index: int) -> tuple[float, float]:
        """Return native page `(width, height)` in PDF points."""
        with self._lock:
            if not self.supported or page_index < 0 or page_index >= self.page_count:
                return (595.0, 842.0)
            if self._doc is not None and not self._closed:
                page = self._doc[page_index]
                try:
                    w, h = page.get_size()
                    return (float(w), float(h))
                finally:
                    page.close()
            if self._mupdf_doc is not None and not self._closed:
                page = self._mupdf_doc[page_index]
                rect = getattr(page, "rect", None)
                if rect is not None:
                    return (float(rect.width), float(rect.height))
            if self._pypdf_reader is not None:
                box = self._pypdf_reader.pages[page_index].mediabox
                return (float(box.width), float(box.height))
            return (595.0, 842.0)

    def peek_cached_page_png(
        self,
        page_index: int,
        *,
        scale: float = 1.6,
        dark_mode: bool = False,
    ) -> Optional[RenderedPdfPage]:
        """Return an already-rendered page from in-RAM LRU cache in O(1) without I/O."""
        with self._lock:
            if not self.supported or not self.can_render_images or self._closed:
                return None
            key = self._cache_key_parts(page_index, scale, dark_mode)
            cached = self._memory_cache.get(key)
            if cached is not None:
                self._memory_cache.move_to_end(key)
                return replace(cached, from_cache=True)
            return None

    def render_page_png(
        self,
        page_index: int,
        *,
        scale: float = 1.6,
        dark_mode: bool = False,
    ) -> Optional[RenderedPdfPage]:
        """Render a page as PNG bytes using memory + disk LRU caches."""
        with self._lock:
            pil_image = _get_pil_image()
            if (
                not self.supported
                or not self.can_render_images
                or (self._doc is None and self._mupdf_doc is None)
                or self._closed
                or page_index < 0
                or page_index >= self.page_count
                or pil_image is None
            ):
                return None

            key = self._cache_key_parts(page_index, scale, dark_mode)
            clamped_scale = key[1] / 100.0
            cached = self._memory_cache.get(key)
            if cached is not None:
                self._memory_cache.move_to_end(key)
                return cached

            disk_cached = self._read_disk_cache(key, clamped_scale)
            if disk_cached is not None:
                self._remember_in_memory(key, disk_cached)
                return disk_cached

            if self._doc is not None:
                page = self._doc[page_index]
                try:
                    bitmap = page.render(scale=clamped_scale)
                    pil_img = bitmap.to_pil()
                finally:
                    page.close()
            else:
                pymupdf_mod = _get_pymupdf()
                page = self._mupdf_doc[page_index]
                matrix = (
                    pymupdf_mod.Matrix(clamped_scale, clamped_scale)
                    if pymupdf_mod is not None and hasattr(pymupdf_mod, "Matrix")
                    else None
                )
                pix = (
                    page.get_pixmap(matrix=matrix, alpha=False)
                    if matrix is not None
                    else page.get_pixmap(alpha=False)
                )
                channels = int(getattr(pix, "n", 3) or 3)
                mode = "RGBA" if channels == 4 else ("L" if channels == 1 else "RGB")
                pil_img = pil_image.frombytes(
                    mode,
                    (int(pix.width), int(pix.height)),
                    bytes(pix.samples),
                )
                if mode != "RGB":
                    pil_img = pil_img.convert("RGB")

            if dark_mode:
                pil_img = _invert_image_luminance(pil_img)

            width, height = pil_img.size
            png_bytes = _encode_pil_png(pil_img)
            rendered = RenderedPdfPage(
                page_index=page_index,
                png_bytes=png_bytes,
                width=int(width),
                height=int(height),
                scale=clamped_scale,
                dark_mode=dark_mode,
                from_cache=False,
            )
            self._remember_in_memory(key, rendered)
            self._write_disk_cache(key, png_bytes)
            return rendered

    def extract_page_text(self, page_index: int) -> str:
        """Extract plain text from a single page with per-session caching."""
        with self._lock:
            if not self.supported or page_index < 0 or page_index >= self.page_count:
                return ""
            if page_index in self._text_cache:
                return self._text_cache[page_index]

            text = ""
            if self._doc is not None and not self._closed:
                page = self._doc[page_index]
                textpage = None
                try:
                    textpage = page.get_textpage()
                    text = str(textpage.get_text_bounded() or "")
                except Exception:
                    text = ""
                finally:
                    if textpage is not None:
                        try:
                            textpage.close()
                        except Exception:
                            pass
                    page.close()
            elif self._mupdf_doc is not None and not self._closed:
                try:
                    page = self._mupdf_doc[page_index]
                    text = str(page.get_text("text") or "")
                except Exception:
                    text = ""
            elif self._pypdf_reader is not None:
                try:
                    text = str(self._pypdf_reader.pages[page_index].extract_text() or "")
                except Exception:
                    text = ""

            if (
                not text.strip()
                and (self._doc is not None or self._mupdf_doc is not None)
            ):
                pypdf_mod = _get_pypdf()
                if pypdf_mod is not None:
                    try:
                        if self._pypdf_reader is None:
                            self._pypdf_reader = pypdf_mod.PdfReader(io.BytesIO(self._data))
                        if not getattr(self._pypdf_reader, "is_encrypted", False):
                            text = str(self._pypdf_reader.pages[page_index].extract_text() or "")
                    except Exception:
                        pass

            self._text_cache[page_index] = text
            return text

    def search(self, query: str, *, max_hits: int = 100) -> list[PdfSearchHit]:
        """Search all pages case-insensitively and return contextual hits."""
        clean_query = (query or "").strip().lower()
        if not clean_query or not self.supported:
            return []
        hits: list[PdfSearchHit] = []
        limit = max(1, int(max_hits))
        for page_idx in range(self.page_count):
            if len(hits) >= limit:
                break
            text = self.extract_page_text(page_idx)
            snippet, count = _extract_snippet(text, clean_query)
            if count > 0:
                hits.append(
                    PdfSearchHit(
                        page_index=page_idx,
                        snippet=snippet,
                        match_count=count,
                    )
                )
        return hits

    def close(self) -> None:
        """Close underlying PDF native handles and clear in-memory caches."""
        with self._lock:
            self._closed = True
            self._memory_cache.clear()
            self._text_cache.clear()
            self._pypdf_reader = None
            self._data = b""
            if self._doc is not None:
                try:
                    self._doc.close()
                except Exception:
                    pass
                self._doc = None
            if self._mupdf_doc is not None:
                try:
                    self._mupdf_doc.close()
                except Exception:
                    pass
                self._mupdf_doc = None

    def __enter__(self) -> "PdfDocumentSession":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
