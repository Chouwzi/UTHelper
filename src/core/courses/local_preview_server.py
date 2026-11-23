"""Loopback HTTP preview server with per-item token auth, range support, and strict CSP."""

from __future__ import annotations

import hashlib
import hmac
import html
import os
import re
import secrets
import shutil
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

_ITEM_ID_RE = re.compile(r"[0-9a-f]{16}")
_CSP_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "image/svg+xml"})
_HTML_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "base-uri 'none'; "
    "form-action 'none'"
)
_VIEWER_APP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "worker-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'none'"
)
_SESSION_HTML_CSP = (
    "default-src 'self' data: blob:; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
_VIEWER_MIME_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".wasm": "application/wasm",
}
_VIEWER_PATH_RE = re.compile(r"^/(viewer|pdf-viewer)/([^/]+)(?:/(.*))?$")
_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError)
_MAX_VIEWER_ASSET_CACHE_ITEMS = 64
_MAX_VIEWER_ASSET_CACHE_BYTES = 24 * 1024 * 1024
_FILE_STREAM_CHUNK = 65536


def _resolve_named_assets_dir(
    folder_name: str, override: Optional[Path] = None
) -> Path:
    if override is not None:
        return Path(override)
    flet_assets = os.environ.get("FLET_ASSETS_DIR")
    if flet_assets:
        return Path(flet_assets) / folder_name
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass = Path(getattr(sys, "_MEIPASS"))
        cand_assets = meipass / "assets" / folder_name
        if cand_assets.is_dir():
            return cand_assets
        cand_direct = meipass / folder_name
        if cand_direct.is_dir():
            return cand_direct
        return cand_assets
    return Path(__file__).resolve().parents[2] / "assets" / folder_name


def _resolve_viewer_assets_dir(override: Optional[Path] = None) -> Path:
    """Resolve the offline Office viewer static asset directory without GUI imports."""
    return _resolve_named_assets_dir("office_viewer", override)


def _resolve_pdf_viewer_assets_dir(override: Optional[Path] = None) -> Path:
    """Resolve the offline PDF.js viewer static asset directory without GUI imports."""
    return _resolve_named_assets_dir("pdf_viewer", override)


@dataclass(frozen=True, slots=True)
class _PreviewEntry:
    """Preview payload (in-memory bytes or disk file) and per-item access token."""

    content_type: str
    token: str
    size: int
    body: Optional[bytes] = None
    file_path: Optional[Path] = None


_MAX_SESSION_POST_BYTES = 4 * 1024 * 1024
_SESSION_PATH_RE = re.compile(r"^/session/([^/]+)/([^/]+)/(.*)$")
_ATTR_URL_RE = re.compile(
    r"""(?i)\b(href|src|action|data-url)\s*=\s*(["'])([^"']+)\2"""
)
_BASE_TAG_RE = re.compile(r"""<base\b(?:"[^"]*"|'[^']*'|[^>"'])*>""", re.IGNORECASE)


def _rewrite_session_html(
    raw_html: str,
    *,
    origin: str,
    final_url: str,
    port: int,
    bridge_prefix: str,
) -> str:
    """Rewrite same-origin Moodle links/forms in HTML through the local session bridge."""
    origin_lower = origin.rstrip("/").lower()
    base_dir = "/"
    final_parts = urlsplit(final_url or "")
    if final_parts.path:
        slash_idx = final_parts.path.rfind("/")
        base_dir = final_parts.path[: slash_idx + 1] if slash_idx >= 0 else "/"

    def _replace_attr(match: re.Match[str]) -> str:
        attr_name, quote, target = match.group(1), match.group(2), match.group(3)
        stripped = target.strip()
        if not stripped or stripped.startswith(("#", "javascript:", "mailto:", "tel:", "data:", "blob:")):
            return match.group(0)
        if stripped.startswith(bridge_prefix):
            return match.group(0)
        lowered = stripped.lower()
        if lowered.startswith(origin_lower + "/"):
            rel = stripped[len(origin_lower) :]
            return f"{attr_name}={quote}{bridge_prefix}{rel}{quote}"
        if lowered == origin_lower:
            return f"{attr_name}={quote}{bridge_prefix}/{quote}"
        if stripped.startswith("//"):
            return match.group(0)
        if stripped.startswith("/"):
            return f"{attr_name}={quote}{bridge_prefix}{stripped}{quote}"
        return match.group(0)

    rewritten = _ATTR_URL_RE.sub(_replace_attr, raw_html)
    rewritten = _BASE_TAG_RE.sub("", rewritten)
    base_href = f"http://127.0.0.1:{port}{bridge_prefix}{base_dir}"
    base_tag = f'<base href="{html.escape(base_href, quote=True)}">'
    head_match = re.search(r"<head\b[^>]*>", rewritten, flags=re.IGNORECASE)
    if head_match is not None:
        pos = head_match.end()
        rewritten = rewritten[:pos] + base_tag + rewritten[pos:]
    return rewritten


def _is_same_moodle_origin_url(candidate: str, origin: str) -> bool:
    """Return True only when ``candidate`` matches ``origin``'s scheme and netloc."""
    try:
        cand_parts = urlsplit(str(candidate or "").strip())
        orig_parts = urlsplit(str(origin or "").strip())
    except ValueError:
        return False
    if not cand_parts.scheme or not cand_parts.netloc or not orig_parts.scheme or not orig_parts.netloc:
        return False
    return (
        cand_parts.scheme.lower() == orig_parts.scheme.lower()
        and cand_parts.netloc.lower() == orig_parts.netloc.lower()
    )


def _requires_strict_csp(content_type: str) -> bool:
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime in _CSP_CONTENT_TYPES


def _is_ascii_digits(value: str) -> bool:
    return bool(value) and value.isascii() and value.isdigit()


def _parse_byte_range(range_header: str, total: int) -> Optional[tuple[int, int]]:
    """Parse a single HTTP ``Range: bytes=...`` header into inclusive bounds."""
    if total <= 0:
        return None
    raw = range_header.strip()
    if not raw.lower().startswith("bytes="):
        return None
    spec = raw[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        return None

    start_raw, sep, end_raw = spec.partition("-")
    if not sep or "-" in end_raw:
        return None
    start_str = start_raw.strip()
    end_str = end_raw.strip()

    if not start_str and not end_str:
        return None
    if not start_str:
        if not _is_ascii_digits(end_str):
            return None
        suffix_len = int(end_str)
        if suffix_len <= 0:
            return None
        start = max(0, total - suffix_len)
        return start, total - 1

    if not _is_ascii_digits(start_str):
        return None
    start = int(start_str)
    if start >= total:
        return None

    if not end_str:
        return start, total - 1
    if not _is_ascii_digits(end_str):
        return None
    end = int(end_str)
    if start > end:
        return None
    return start, min(end, total - 1)


class _PreviewThreadingHTTPServer(ThreadingHTTPServer):
    """Loopback-only threaded HTTP server suppressing noisy client-disconnect logs."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        owner: "LocalPreviewServer",
        *,
        session_only: bool = False,
    ) -> None:
        self.owner = owner
        self.session_only = session_only
        super().__init__(server_address, handler_cls)

    def handle_error(self, request: object, client_address: object) -> None:
        return


class _PreviewRequestHandler(BaseHTTPRequestHandler):
    """Serve token-authenticated preview payloads over loopback HTTP/1.1 Keep-Alive."""

    protocol_version = "HTTP/1.1"
    timeout = 15.0
    server: _PreviewThreadingHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except (*_DISCONNECT_ERRORS, TimeoutError):
            return

    def finish(self) -> None:
        try:
            super().finish()
        except _DISCONNECT_ERRORS:
            pass

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None) -> None:
        self.close_connection = True
        self._send_empty_status(code, close_connection=True)

    def do_GET(self) -> None:
        if getattr(self.server, "session_only", False):
            if self.path.startswith("/session/"):
                self._serve_session(method="GET", send_body=True)
            else:
                self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        if self.path.startswith("/session/"):
            self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        if self.path.startswith("/viewer/") or self.path.startswith("/pdf-viewer/"):
            self._serve_viewer(send_body=True)
            return
        self._serve(send_body=True)

    def do_HEAD(self) -> None:
        if getattr(self.server, "session_only", False):
            if self.path.startswith("/session/"):
                self._serve_session(method="HEAD", send_body=False)
            else:
                self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        if self.path.startswith("/session/"):
            self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        if self.path.startswith("/viewer/") or self.path.startswith("/pdf-viewer/"):
            self._serve_viewer(send_body=False)
            return
        self._serve(send_body=False)

    def do_POST(self) -> None:
        if not getattr(self.server, "session_only", False) or not self.path.startswith("/session/"):
            self._send_empty_status(HTTPStatus.METHOD_NOT_ALLOWED, close_connection=True)
            return
        self._serve_session(method="POST", send_body=True)

    def _serve_session(self, *, method: str, send_body: bool) -> None:
        parsed = urlsplit(self.path)
        match = _SESSION_PATH_RE.fullmatch(parsed.path)
        if match is None:
            self._send_empty_status(HTTPStatus.NOT_FOUND, close_connection=(method == "POST"))
            return
        token, source, remote_path = match.group(1), match.group(2), match.group(3)
        req_body: Optional[bytes] = None
        req_ct = str(self.headers.get("Content-Type", "") or "")
        if method == "POST":
            if "transfer-encoding" in self.headers:
                self._send_empty_status(HTTPStatus.NOT_IMPLEMENTED, close_connection=True)
                return
            raw_len = self.headers.get("Content-Length")
            try:
                content_len = int(raw_len) if raw_len is not None else 0
            except ValueError:
                self._send_empty_status(HTTPStatus.BAD_REQUEST, close_connection=True)
                return
            if content_len < 0 or content_len > _MAX_SESSION_POST_BYTES:
                self._send_empty_status(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, close_connection=True)
                return
            req_body = self.rfile.read(content_len) if content_len > 0 else b""
            if len(req_body) < content_len:
                self._send_empty_status(HTTPStatus.BAD_REQUEST, close_connection=True)
                return

        status_code, resp_ct, resp_body, redirect_url = self.server.owner._proxy_session_request(
            token=token,
            source=source,
            remote_path=remote_path,
            query=parsed.query,
            method=method,
            body=req_body,
            content_type=req_ct,
        )
        if redirect_url:
            try:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", redirect_url)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store, private")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Content-Security-Policy", _SESSION_HTML_CSP)
                self.end_headers()
            except _DISCONNECT_ERRORS:
                return
            return
        if status_code != HTTPStatus.OK and not resp_body:
            self._send_empty_status(status_code)
            return
        try:
            final_ct = resp_ct or "application/octet-stream"
            mime = final_ct.split(";", 1)[0].strip().lower()
            self.send_response(status_code)
            self.send_header("Content-Type", final_ct)
            self.send_header("Content-Length", str(len(resp_body)))
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if mime in ("text/html", "application/xhtml+xml"):
                self.send_header("Content-Security-Policy", _SESSION_HTML_CSP)
            elif mime == "image/svg+xml":
                self.send_header("Content-Security-Policy", _HTML_CSP)
            self.end_headers()
            if send_body and resp_body:
                self.wfile.write(resp_body)
        except _DISCONNECT_ERRORS:
            return

    def _send_security_headers(self, content_type: Optional[str] = None) -> None:
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type and _requires_strict_csp(content_type):
            self.send_header("Content-Security-Policy", _HTML_CSP)

    def _send_empty_status(
        self,
        status: int,
        *,
        content_range: Optional[str] = None,
        close_connection: bool = False,
    ) -> None:
        try:
            if close_connection:
                self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Length", "0")
            if close_connection:
                self.send_header("Connection", "close")
            if content_range is not None:
                self.send_header("Content-Range", content_range)
            self._send_security_headers()
            self.end_headers()
        except _DISCONNECT_ERRORS:
            return

    def _serve_viewer(self, *, send_body: bool) -> None:
        parsed = urlsplit(self.path)
        match = _VIEWER_PATH_RE.fullmatch(parsed.path)
        if match is None:
            self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        viewer_kind = match.group(1)
        token = match.group(2)
        raw_asset_path = match.group(3) or ""
        status, content_type, body, etag = self.server.owner._resolve_viewer_asset(
            token, raw_asset_path, kind=viewer_kind
        )
        if status != HTTPStatus.OK:
            self._send_empty_status(status)
            return
        is_html = content_type.startswith("text/html")
        cache_control = "no-store, private" if is_html else "public, max-age=3600"
        if not is_html and etag:
            inm = self.headers.get("If-None-Match")
            if inm and (inm.strip() == "*" or any(p.strip() == etag for p in inm.split(","))):
                try:
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header("Content-Length", "0")
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", cache_control)
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                    self.end_headers()
                except _DISCONNECT_ERRORS:
                    return
                return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            if not is_html and etag:
                self.send_header("ETag", etag)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            if is_html:
                self.send_header("Content-Security-Policy", _VIEWER_APP_CSP)
            elif content_type.startswith("image/svg+xml"):
                self.send_header("Content-Security-Policy", _HTML_CSP)
            self.end_headers()
            if send_body and body:
                self.wfile.write(body)
        except _DISCONNECT_ERRORS:
            return

    def _serve(self, *, send_body: bool) -> None:
        parsed = urlsplit(self.path)
        item_id = parsed.path.lstrip("/")
        if not _ITEM_ID_RE.fullmatch(item_id):
            self._send_empty_status(HTTPStatus.NOT_FOUND)
            return

        qs = parse_qs(parsed.query, keep_blank_values=True)
        tokens = qs.get("token")
        provided_token = tokens[0] if (tokens is not None and len(tokens) == 1) else None

        status_code, entry = self.server.owner._resolve_entry(item_id, provided_token)
        if entry is None:
            self._send_empty_status(status_code)
            return

        if entry.file_path is not None:
            self._serve_file_entry(entry, send_body=send_body)
            return

        body = entry.body or b""
        total = len(body)
        range_header = self.headers.get("Range")
        if range_header is not None:
            parsed_range = _parse_byte_range(range_header, total)
            if parsed_range is None:
                self._send_empty_status(
                    HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                    content_range=f"bytes */{total}",
                )
                return
            start, end = parsed_range
            chunk = body[start : end + 1]
            try:
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", entry.content_type)
                self.send_header("Content-Length", str(len(chunk)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                self._send_security_headers(entry.content_type)
                self.end_headers()
                if send_body and chunk:
                    self.wfile.write(chunk)
            except _DISCONNECT_ERRORS:
                return
            return

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", entry.content_type)
            self.send_header("Content-Length", str(total))
            self.send_header("Accept-Ranges", "bytes")
            self._send_security_headers(entry.content_type)
            self.end_headers()
            if send_body and body:
                self.wfile.write(body)
        except _DISCONNECT_ERRORS:
            return

    def _serve_file_entry(self, entry: _PreviewEntry, *, send_body: bool) -> None:
        assert entry.file_path is not None
        try:
            handle = entry.file_path.open("rb")
        except OSError:
            self._send_empty_status(HTTPStatus.NOT_FOUND)
            return
        try:
            with handle:
                total = int(os.fstat(handle.fileno()).st_size)
                range_header = self.headers.get("Range")
                if range_header is not None:
                    parsed_range = _parse_byte_range(range_header, total)
                    if parsed_range is None:
                        self._send_empty_status(
                            HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
                            content_range=f"bytes */{total}",
                        )
                        return
                    start, end = parsed_range
                    length = end - start + 1
                    self.send_response(HTTPStatus.PARTIAL_CONTENT)
                    self.send_header("Content-Type", entry.content_type)
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
                    self._send_security_headers(entry.content_type)
                    self.end_headers()
                    if send_body and length > 0:
                        handle.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = handle.read(min(_FILE_STREAM_CHUNK, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    return

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", entry.content_type)
                self.send_header("Content-Length", str(total))
                self.send_header("Accept-Ranges", "bytes")
                self._send_security_headers(entry.content_type)
                self.end_headers()
                if send_body and total > 0:
                    shutil.copyfileobj(handle, self.wfile, length=_FILE_STREAM_CHUNK)
        except _DISCONNECT_ERRORS:
            return


class LocalPreviewServer:
    """Ephemeral ``127.0.0.1`` loopback server holding bounded, tokenized preview bytes."""

    def __init__(
        self,
        *,
        max_items: int = 32,
        max_total_bytes: int = 128 * 1024 * 1024,
        viewer_assets_dir: Optional[Path] = None,
        pdf_viewer_assets_dir: Optional[Path] = None,
    ) -> None:
        if isinstance(max_items, bool) or max_items < 1:
            raise ValueError("max_items must be an integer >= 1")
        if isinstance(max_total_bytes, bool) or max_total_bytes < 1:
            raise ValueError("max_total_bytes must be an integer >= 1")
        self._max_items = int(max_items)
        self._max_total_bytes = int(max_total_bytes)
        self._viewer_assets_dir: Optional[Path] = (
            Path(viewer_assets_dir) if viewer_assets_dir is not None else None
        )
        self._pdf_viewer_assets_dir: Optional[Path] = (
            Path(pdf_viewer_assets_dir) if pdf_viewer_assets_dir is not None else None
        )
        self._lock = threading.Lock()
        self._server: Optional[_PreviewThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port: int = 0
        self._session_server: Optional[_PreviewThreadingHTTPServer] = None
        self._session_thread: Optional[threading.Thread] = None
        self._session_port: int = 0
        self._items: OrderedDict[str, _PreviewEntry] = OrderedDict()
        self._total_bytes: int = 0
        self._bridge_token: str = secrets.token_urlsafe(24)
        self._viewer_token: str = secrets.token_urlsafe(24)
        self._session_providers: dict[str, Any] = {}
        self._viewer_asset_cache: OrderedDict[tuple[str, str], tuple[int, int, str, bytes, str]] = OrderedDict()
        self._viewer_asset_cache_bytes: int = 0

    def viewer_base_url(self) -> str:
        """Return the authenticated loopback base URL for offline viewer assets."""
        with self._lock:
            port = self._ensure_started_locked()
            token = self._viewer_token
        return f"http://127.0.0.1:{port}/viewer/{token}/"

    def pdf_viewer_base_url(self) -> str:
        """Return the authenticated loopback base URL for offline PDF.js viewer assets."""
        with self._lock:
            port = self._ensure_started_locked()
            token = self._viewer_token
        return f"http://127.0.0.1:{port}/pdf-viewer/{token}/"

    def _resolve_viewer_asset(
        self, token: str, raw_asset_path: str, *, kind: str = "viewer"
    ) -> tuple[int, str, bytes, str]:
        with self._lock:
            expected_token = self._viewer_token
            assets_override = (
                self._pdf_viewer_assets_dir if kind == "pdf-viewer" else self._viewer_assets_dir
            )

        if not token or not hmac.compare_digest(
            token.encode("utf-8"), expected_token.encode("utf-8")
        ):
            return HTTPStatus.FORBIDDEN, "text/plain", b"", ""

        if not raw_asset_path or raw_asset_path.endswith("/"):
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        decoded = unquote(raw_asset_path)
        for variant in (raw_asset_path, decoded):
            if "\x00" in variant or "\\" in variant or ":" in variant:
                return HTTPStatus.FORBIDDEN, "text/plain", b"", ""
            if variant.startswith("/"):
                return HTTPStatus.FORBIDDEN, "text/plain", b"", ""
            segments = variant.split("/")
            if any(seg == ".." for seg in segments):
                return HTTPStatus.FORBIDDEN, "text/plain", b"", ""
            if any(seg in ("", ".") for seg in segments):
                return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        try:
            resolver = (
                _resolve_pdf_viewer_assets_dir
                if kind == "pdf-viewer"
                else _resolve_viewer_assets_dir
            )
            root = resolver(assets_override).resolve()
            candidate = (root / decoded).resolve()
        except OSError:
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        if candidate == root or not candidate.is_relative_to(root):
            return HTTPStatus.FORBIDDEN, "text/plain", b"", ""

        mime = _VIEWER_MIME_TYPES.get(candidate.suffix.lower())
        if mime is None:
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        try:
            st = candidate.stat()
        except OSError:
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        if not candidate.is_file():
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        cache_key = (str(root), str(candidate))
        with self._lock:
            cached = self._viewer_asset_cache.get(cache_key)
            if cached is not None and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                self._viewer_asset_cache.move_to_end(cache_key)
                return HTTPStatus.OK, cached[2], cached[3], cached[4]

        try:
            data = candidate.read_bytes()
        except OSError:
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        etag = f'"{hashlib.sha256(data).hexdigest()[:16]}"'
        if len(data) <= _MAX_VIEWER_ASSET_CACHE_BYTES:
            with self._lock:
                old = self._viewer_asset_cache.pop(cache_key, None)
                if old is not None:
                    self._viewer_asset_cache_bytes = max(
                        0, self._viewer_asset_cache_bytes - len(old[3])
                    )
                while self._viewer_asset_cache and (
                    len(self._viewer_asset_cache) >= _MAX_VIEWER_ASSET_CACHE_ITEMS
                    or self._viewer_asset_cache_bytes + len(data) > _MAX_VIEWER_ASSET_CACHE_BYTES
                ):
                    _, evicted = self._viewer_asset_cache.popitem(last=False)
                    self._viewer_asset_cache_bytes = max(
                        0, self._viewer_asset_cache_bytes - len(evicted[3])
                    )
                self._viewer_asset_cache[cache_key] = (
                    st.st_mtime_ns,
                    st.st_size,
                    mime,
                    data,
                    etag,
                )
                self._viewer_asset_cache_bytes += len(data)

        return HTTPStatus.OK, mime, data, etag

    def register_session_provider(self, source: str, client: Any) -> None:
        """Register or clear a Moodle session client for authenticated session proxying."""
        if source not in ("courses", "thnn"):
            return
        with self._lock:
            if client is None:
                self._session_providers.pop(source, None)
            else:
                self._session_providers[source] = client

    def build_session_bridge_url(self, source: str, target_url: str) -> Optional[str]:
        """Return a loopback reverse-proxy URL for a same-origin Moodle web page."""
        if source not in ("courses", "thnn"):
            return None
        with self._lock:
            client = self._session_providers.get(source)
            origin = str(getattr(client, "moodle_site_origin", "") or "").rstrip("/")
        if client is None or not origin:
            return None

        raw = str(target_url or "").strip()
        if not raw or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
            return None
        parsed = urlsplit(raw)
        origin_parts = urlsplit(origin)
        if parsed.scheme or parsed.netloc:
            if (
                parsed.scheme.lower() != origin_parts.scheme.lower()
                or parsed.netloc.lower() != origin_parts.netloc.lower()
            ):
                return None
            rel_path = (parsed.path or "/").lstrip("/")
            query = parsed.query
        else:
            joined = urljoin(f"{origin}/", raw.lstrip("/"))
            jp = urlsplit(joined)
            rel_path = (jp.path or "/").lstrip("/")
            query = jp.query

        with self._lock:
            port = self._ensure_session_started_locked()
            token = self._bridge_token
        suffix = f"?{query}" if query else ""
        return f"http://127.0.0.1:{port}/session/{token}/{source}/{rel_path}{suffix}"

    def _proxy_session_request(
        self,
        *,
        token: str,
        source: str,
        remote_path: str,
        query: str,
        method: str,
        body: Optional[bytes],
        content_type: str,
    ) -> tuple[int, str, bytes, str]:
        with self._lock:
            expected_token = self._bridge_token
            client = self._session_providers.get(source)
            port = self._session_port or self._port

        if not secrets.compare_digest(
            token.encode("utf-8"), expected_token.encode("utf-8")
        ):
            return HTTPStatus.FORBIDDEN, "text/plain", b"", ""
        if source not in ("courses", "thnn") or client is None:
            return HTTPStatus.NOT_FOUND, "text/plain", b"", ""

        origin = str(getattr(client, "moodle_site_origin", "") or "").rstrip("/")
        if not origin:
            return HTTPStatus.SERVICE_UNAVAILABLE, "text/plain", b"", ""

        rel_target = "/" + remote_path.lstrip("/")
        if query:
            rel_target = f"{rel_target}?{query}"
        remote_url = urljoin(f"{origin}/", rel_target.lstrip("/"))

        try:
            status_code, final_url, resp_ct, raw_bytes = client.open_session_raw(
                remote_url,
                method=method,
                body=body,
                content_type=content_type,
            )
        except ValueError:
            return HTTPStatus.FORBIDDEN, "text/plain", b"", ""
        except Exception:
            return HTTPStatus.BAD_GATEWAY, "text/plain", b"", ""

        if 300 <= status_code < 400 and final_url:
            if _is_same_moodle_origin_url(final_url, origin):
                proxied = self.build_session_bridge_url(source, final_url)
                if proxied:
                    return HTTPStatus.FOUND, "text/plain", b"", proxied
            return HTTPStatus.FORBIDDEN, "text/plain", b"", ""

        media_type = (resp_ct or "").split(";", 1)[0].strip().lower()
        if media_type in ("text/html", "application/xhtml+xml"):
            decoded = raw_bytes.decode("utf-8", errors="replace")
            bridge_prefix = f"/session/{expected_token}/{source}"
            rewritten = _rewrite_session_html(
                decoded,
                origin=origin,
                final_url=final_url or remote_url,
                port=port,
                bridge_prefix=bridge_prefix,
            )
            return status_code, resp_ct or "text/html; charset=utf-8", rewritten.encode("utf-8"), ""

        return status_code, resp_ct or "application/octet-stream", raw_bytes, ""

    @property
    def port(self) -> int:
        """Active bound TCP port on ``127.0.0.1``, or ``0`` when stopped."""
        with self._lock:
            return self._port

    def _ensure_started_locked(self) -> int:
        if (
            self._server is not None
            and self._thread is not None
            and self._thread.is_alive()
            and self._port > 0
        ):
            return self._port

        if self._server is not None:
            dead_server = self._server
            self._server = None
            self._thread = None
            self._port = 0
            try:
                dead_server.server_close()
            except Exception:
                pass

        server = _PreviewThreadingHTTPServer(
            ("127.0.0.1", 0),
            _PreviewRequestHandler,
            self,
        )
        port = int(server.server_address[1])
        thread = threading.Thread(
            target=server.serve_forever,
            name="UTHelper-LocalPreviewServer",
            daemon=True,
        )
        thread.start()
        self._server = server
        self._thread = thread
        self._port = port
        return port

    def _ensure_session_started_locked(self) -> int:
        if (
            self._session_server is not None
            and self._session_thread is not None
            and self._session_thread.is_alive()
            and self._session_port > 0
        ):
            return self._session_port

        if self._session_server is not None:
            dead_server = self._session_server
            self._session_server = None
            self._session_thread = None
            self._session_port = 0
            try:
                dead_server.server_close()
            except Exception:
                pass

        server = _PreviewThreadingHTTPServer(
            ("127.0.0.1", 0),
            _PreviewRequestHandler,
            self,
            session_only=True,
        )
        port = int(server.server_address[1])
        thread = threading.Thread(
            target=server.serve_forever,
            name="UTHelper-LocalSessionBridgeServer",
            daemon=True,
        )
        thread.start()
        self._session_server = server
        self._session_thread = thread
        self._session_port = port
        return port

    def is_active_loopback_origin(self, url: str) -> bool:
        """Return True when ``url`` targets an active loopback origin of this server."""
        if not isinstance(url, str) or not url:
            return False
        try:
            parsed = urlsplit(url)
            url_port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or url_port is None:
            return False
        with self._lock:
            main_live = (
                self._server is not None
                and self._thread is not None
                and self._thread.is_alive()
                and self._port > 0
                and url_port == self._port
            )
            session_live = (
                self._session_server is not None
                and self._session_thread is not None
                and self._session_thread.is_alive()
                and self._session_port > 0
                and url_port == self._session_port
            )
        return main_live or session_live

    def _store_entry_locked(
        self,
        *,
        size: int,
        entry: _PreviewEntry,
    ) -> str:
        item_id = secrets.token_hex(8)
        port = self._ensure_started_locked()
        while self._items and (
            len(self._items) >= self._max_items
            or self._total_bytes + size > self._max_total_bytes
        ):
            _, evicted = self._items.popitem(last=False)
            self._total_bytes = max(0, self._total_bytes - evicted.size)
        self._items[item_id] = entry
        self._total_bytes += size
        return f"http://127.0.0.1:{port}/{item_id}?token={entry.token}"

    @staticmethod
    def _validate_content_type(content_type: str) -> str:
        if not isinstance(content_type, str):
            raise TypeError("content_type must be a string")
        cleaned_ct = content_type.strip()
        if not cleaned_ct or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned_ct):
            raise ValueError("Invalid content_type")
        try:
            cleaned_ct.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("content_type must be latin-1 encodable") from exc
        return cleaned_ct

    def register_bytes(self, body: bytes, content_type: str) -> str:
        """Store raw bytes with a fresh token and return the loopback preview URL."""
        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise TypeError("body must be bytes-like")
        payload = bytes(body)
        cleaned_ct = self._validate_content_type(content_type)
        size = len(payload)
        if size > self._max_total_bytes:
            raise ValueError(
                f"Preview item size ({size} bytes) exceeds max_total_bytes ({self._max_total_bytes})"
            )

        token = secrets.token_urlsafe(24)
        entry = _PreviewEntry(
            content_type=cleaned_ct,
            token=token,
            size=size,
            body=payload,
        )
        with self._lock:
            return self._store_entry_locked(size=size, entry=entry)

    def register_file(self, file_path: Path, content_type: str) -> str:
        """Register an on-disk file for zero-copy streaming over loopback HTTP."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(str(path))
        cleaned_ct = self._validate_content_type(content_type)
        size = int(path.stat().st_size)
        if size > self._max_total_bytes:
            raise ValueError(
                f"Preview item size ({size} bytes) exceeds max_total_bytes ({self._max_total_bytes})"
            )

        token = secrets.token_urlsafe(24)
        entry = _PreviewEntry(
            content_type=cleaned_ct,
            token=token,
            size=size,
            file_path=path,
        )
        with self._lock:
            return self._store_entry_locked(size=size, entry=entry)

    def register_html(self, html_text: str) -> str:
        """Encode and register a UTF-8 HTML document with strict preview CSP."""
        if not isinstance(html_text, str):
            raise TypeError("html_text must be a string")
        return self.register_bytes(html_text.encode("utf-8"), "text/html; charset=utf-8")

    def has_active_url(self, url: str) -> bool:
        """Return True iff ``url`` references a currently live entry on this server."""
        if not isinstance(url, str) or not url:
            return False
        try:
            parsed = urlsplit(url)
            url_port = parsed.port
        except ValueError:
            return False
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            return False
        item_id = parsed.path.lstrip("/")
        if not _ITEM_ID_RE.fullmatch(item_id):
            return False
        qs = parse_qs(parsed.query, keep_blank_values=True)
        tokens = qs.get("token")
        if tokens is None or len(tokens) != 1:
            return False
        provided_token = tokens[0]

        with self._lock:
            if (
                self._server is None
                or self._thread is None
                or not self._thread.is_alive()
                or self._port <= 0
                or url_port != self._port
            ):
                return False
            entry = self._items.get(item_id)
            if entry is None:
                return False
            return secrets.compare_digest(
                provided_token.encode("utf-8"), entry.token.encode("utf-8")
            )

    def _resolve_entry(
        self, item_id: str, provided_token: Optional[str]
    ) -> tuple[int, Optional[_PreviewEntry]]:
        with self._lock:
            entry = self._items.get(item_id)
            if entry is None:
                return HTTPStatus.NOT_FOUND, None
            if provided_token is None:
                return HTTPStatus.FORBIDDEN, None
            if not secrets.compare_digest(
                provided_token.encode("utf-8"), entry.token.encode("utf-8")
            ):
                return HTTPStatus.FORBIDDEN, None
            self._items.move_to_end(item_id)
            return HTTPStatus.OK, entry

    def stop(self) -> None:
        """Idempotently shut down the loopback servers and clear cached preview items."""
        with self._lock:
            server = self._server
            thread = self._thread
            session_server = self._session_server
            session_thread = self._session_thread
            self._server = None
            self._thread = None
            self._port = 0
            self._session_server = None
            self._session_thread = None
            self._session_port = 0
            self._items.clear()
            self._total_bytes = 0
            self._viewer_asset_cache.clear()
            self._viewer_asset_cache_bytes = 0

        for srv, thr in ((server, thread), (session_server, session_thread)):
            if srv is not None:
                if thr is not None and thr.is_alive():
                    try:
                        srv.shutdown()
                    except Exception:
                        pass
                try:
                    srv.server_close()
                except Exception:
                    pass
            if thr is not None:
                thr.join(timeout=2.0)
