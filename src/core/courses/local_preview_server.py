"""Loopback HTTP preview server with per-item token auth, range support, and strict CSP."""

from __future__ import annotations

import re
import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlsplit

_ITEM_ID_RE = re.compile(r"[0-9a-f]{16}")
_CSP_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml", "image/svg+xml"})
_HTML_CSP = (
    "default-src 'none'; "
    "style-src 'unsafe-inline'; "
    "img-src data:; "
    "base-uri 'none'; "
    "form-action 'none'"
)
_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError)


@dataclass(frozen=True, slots=True)
class _PreviewEntry:
    """In-memory preview payload and per-item access token."""

    body: bytes
    content_type: str
    token: str


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

    def __init__(self, server_address: tuple[str, int], handler_cls: type[BaseHTTPRequestHandler], owner: "LocalPreviewServer") -> None:
        self.owner = owner
        super().__init__(server_address, handler_cls)

    def handle_error(self, request: object, client_address: object) -> None:
        return


class _PreviewRequestHandler(BaseHTTPRequestHandler):
    """Serve token-authenticated in-memory preview payloads over loopback HTTP."""

    server: _PreviewThreadingHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def finish(self) -> None:
        try:
            super().finish()
        except _DISCONNECT_ERRORS:
            pass

    def do_GET(self) -> None:
        self._serve(send_body=True)

    def do_HEAD(self) -> None:
        self._serve(send_body=False)

    def _send_security_headers(self, content_type: Optional[str] = None) -> None:
        self.send_header("Cache-Control", "no-store, private")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if content_type and _requires_strict_csp(content_type):
            self.send_header("Content-Security-Policy", _HTML_CSP)

    def _send_empty_status(self, status: int, *, content_range: Optional[str] = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            if content_range is not None:
                self.send_header("Content-Range", content_range)
            self._send_security_headers()
            self.end_headers()
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

        body = entry.body
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


class LocalPreviewServer:
    """Ephemeral ``127.0.0.1`` loopback server holding bounded, tokenized preview bytes."""

    def __init__(
        self,
        *,
        max_items: int = 32,
        max_total_bytes: int = 128 * 1024 * 1024,
    ) -> None:
        if isinstance(max_items, bool) or max_items < 1:
            raise ValueError("max_items must be an integer >= 1")
        if isinstance(max_total_bytes, bool) or max_total_bytes < 1:
            raise ValueError("max_total_bytes must be an integer >= 1")
        self._max_items = int(max_items)
        self._max_total_bytes = int(max_total_bytes)
        self._lock = threading.Lock()
        self._server: Optional[_PreviewThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._port: int = 0
        self._items: OrderedDict[str, _PreviewEntry] = OrderedDict()
        self._total_bytes: int = 0

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

    def register_bytes(self, body: bytes, content_type: str) -> str:
        """Store raw bytes with a fresh token and return the loopback preview URL."""
        if not isinstance(body, (bytes, bytearray, memoryview)):
            raise TypeError("body must be bytes-like")
        payload = bytes(body)
        if not isinstance(content_type, str):
            raise TypeError("content_type must be a string")
        cleaned_ct = content_type.strip()
        if not cleaned_ct or any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned_ct):
            raise ValueError("Invalid content_type")
        try:
            cleaned_ct.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise ValueError("content_type must be latin-1 encodable") from exc
        if len(payload) > self._max_total_bytes:
            raise ValueError(
                f"Preview item size ({len(payload)} bytes) exceeds max_total_bytes ({self._max_total_bytes})"
            )

        item_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(24)
        entry = _PreviewEntry(body=payload, content_type=cleaned_ct, token=token)

        with self._lock:
            port = self._ensure_started_locked()
            while self._items and (
                len(self._items) >= self._max_items
                or self._total_bytes + len(payload) > self._max_total_bytes
            ):
                _, evicted = self._items.popitem(last=False)
                self._total_bytes = max(0, self._total_bytes - len(evicted.body))
            self._items[item_id] = entry
            self._total_bytes += len(payload)

        return f"http://127.0.0.1:{port}/{item_id}?token={token}"

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
        """Idempotently shut down the loopback server and clear cached preview items."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
            self._port = 0
            self._items.clear()
            self._total_bytes = 0

        if server is not None:
            if thread is not None and thread.is_alive():
                try:
                    server.shutdown()
                except Exception:
                    pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None:
            thread.join(timeout=2.0)
