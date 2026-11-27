import gzip
import http.client
import io
import json
import os
import threading
import time
import zlib
from http.cookiejar import CookieJar
from html.parser import HTMLParser
import urllib.parse
import urllib.request
import urllib.error
from dataclasses import dataclass
from email.message import Message
from typing import Any, Callable, Optional
from config import settings
from core.demo_sandbox import (
    DEMO_USER_ID,
    get_demo_file_bytes,
    handle_demo_ajax_call,
    handle_demo_ws_call,
    is_demo_account,
    normalize_demo_username,
    render_demo_grade_report_html,
    render_demo_session_html,
    verify_demo_credentials_cached,
)
from core.moodle_sites import (
    COURSES_MOODLE_SITE,
    MoodleSite,
    moodle_site_from_origin,
    moodle_site_from_url,
)
from core.quiz_player import (
    QuizSessionLease,
    _begin_quiz_session_login,
    _end_quiz_session_login,
    quiz_session_key,
)
import logging
import sys as _sys

logger = logging.getLogger(__name__)


class _MoodleHtmlFormParser(HTMLParser):
    """Collect ordinary HTML forms without retaining page text or scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, object]] = []
        self.inputs: dict[str, str] = {}
        self._current: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "form":
            self._current = {
                "method": attributes.get("method", "get").lower(),
                "action": attributes.get("action", ""),
                "inputs": {},
            }
            self.forms.append(self._current)
        elif tag.lower() == "input":
            name = attributes.get("name", "")
            if not name:
                return
            value = attributes.get("value", "")
            self.inputs[name] = value
            if self._current is not None:
                current_inputs = self._current["inputs"]
                if isinstance(current_inputs, dict):
                    current_inputs[name] = value

    def handle_endtag(self, tag: str):
        if tag.lower() == "form":
            self._current = None


_OPEN_SESSION_REDIRECT_STATE = threading.local()


def _headers_have_set_cookie(headers: Any) -> bool:
    if headers is None:
        return False
    if hasattr(headers, "get_all"):
        values = headers.get_all("Set-Cookie")
        if values:
            return True
    if hasattr(headers, "items"):
        for key, val in headers.items():
            if str(key).lower() == "set-cookie" and val:
                return True
    elif hasattr(headers, "get"):
        if headers.get("Set-Cookie") or headers.get("set-cookie"):
            return True
    return False


class _SameMoodleOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: str):
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if _headers_have_set_cookie(headers):
            _OPEN_SESSION_REDIRECT_STATE.has_set_cookie = True
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        parsed = urllib.parse.urlsplit(redirected.full_url)
        if f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" != self.origin:
            raise urllib.error.HTTPError(
                redirected.full_url, code, "Untrusted Moodle redirect", headers, fp
            )
        return redirected


def _normalize_draft_filepath(filepath: str) -> str:
    """Return a Moodle draft path with exactly one leading and trailing slash."""
    parts = str(filepath or "").replace("\\", "/").strip("/")
    return f"/{parts}/" if parts else "/"


def _is_same_moodle_origin_url(url: str, origin: str) -> bool:
    """Validate an http/https URL has no control/whitespace chars and matches ``origin``."""
    if not origin or not isinstance(url, str):
        return False
    raw = url.strip()
    if not raw or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        return False
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" == origin.rstrip("/").lower()


def _stream_bounded_response(
    response,
    write_fn: Callable[[bytes], Any],
    *,
    max_bytes: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> bool:
    """Stream HTTP response bytes in 64 KB chunks with size bound, gzip support, and progress callback."""
    info = response.info() if hasattr(response, "info") else None
    total_len = 0
    raw_cl_str = ""
    if info is not None:
        try:
            raw_cl = info.get("Content-Length") if hasattr(info, "get") else None
            raw_cl_str = str(raw_cl).strip() if raw_cl is not None else ""
            total_len = max(0, int(raw_cl)) if raw_cl else 0
        except (TypeError, ValueError):
            total_len = 0

    encoding = (
        str(info.get("Content-Encoding") or "").lower()
        if info and hasattr(info, "get")
        else ""
    )
    is_gzip = encoding == "gzip"

    # Stream in 64 KB chunks on real HTTPResponse / BufferedIOBase; read once on non-advancing test fakes.
    is_real_stream = isinstance(response, (http.client.HTTPResponse, io.IOBase)) or hasattr(
        getattr(response, "fp", None), "read"
    )
    if is_real_stream:
        chunk_size = 65536
        last_emit = 0.0
        raw_read = 0
        out_total = 0
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS) if is_gzip else None
        while True:
            to_read = (
                min(chunk_size, (max_bytes + 1) - raw_read)
                if max_bytes is not None
                else chunk_size
            )
            if to_read <= 0:
                extra = response.read(1)
                if extra:
                    logger.warning("Download exceeded max_bytes (%d)", max_bytes)
                    return False
                break
            chunk = response.read(to_read)
            if not chunk:
                break
            raw_read += len(chunk)
            if max_bytes is not None and raw_read > max_bytes:
                logger.warning("Download exceeded max_bytes (%d)", max_bytes)
                return False
            if decompressor is not None:
                try:
                    if max_bytes is not None:
                        allowed = max(0, max_bytes - out_total)
                        piece = decompressor.decompress(chunk, allowed + 1)
                        if len(piece) > allowed or decompressor.unconsumed_tail:
                            logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
                            return False
                    else:
                        piece = decompressor.decompress(chunk)
                except zlib.error:
                    logger.warning("Corrupt gzip download stream")
                    return False
            else:
                piece = chunk
                if max_bytes is not None and out_total + len(piece) > max_bytes:
                    logger.warning("Download exceeded max_bytes (%d)", max_bytes)
                    return False
            if piece:
                write_fn(piece)
                out_total += len(piece)
            if callable(progress_callback):
                now_m = time.monotonic()
                if (now_m - last_emit) >= 0.065:
                    last_emit = now_m
                    try:
                        progress_callback(raw_read, total_len)
                    except Exception:
                        pass
        if total_len > 0 and raw_read < total_len:
            logger.warning(
                "Download truncated before Content-Length (%d < %d)",
                raw_read,
                total_len,
            )
            return False
        if decompressor is not None:
            try:
                if max_bytes is not None:
                    allowed = max(0, max_bytes - out_total)
                    tail = decompressor.flush(allowed + 1)
                    if len(tail) > allowed:
                        logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
                        return False
                else:
                    tail = decompressor.flush()
            except zlib.error:
                logger.warning("Corrupt gzip download trailer")
                return False
            if not decompressor.eof and not (raw_read == 0 and raw_cl_str == "0"):
                logger.warning("Gzip download truncated before EOF")
                return False
            if tail:
                write_fn(tail)
                out_total += len(tail)
        if callable(progress_callback):
            try:
                progress_callback(raw_read, total_len or raw_read)
            except Exception:
                pass
        return True

    try:
        body = response.read(max_bytes + 1) if max_bytes is not None else response.read()
    except TypeError:
        body = response.read()
    if max_bytes is not None and len(body) > max_bytes:
        logger.warning("Download exceeded max_bytes (%d)", max_bytes)
        return False
    if total_len > 0 and len(body) < total_len:
        logger.warning(
            "Download truncated before Content-Length (%d < %d)",
            len(body),
            total_len,
        )
        return False
    if callable(progress_callback):
        try:
            progress_callback(len(body), total_len or len(body))
        except Exception:
            pass

    if is_gzip:
        if not body and raw_cl_str == "0":
            return True
        if max_bytes is None:
            if not body:
                logger.warning("Gzip download truncated before EOF")
                return False
            try:
                write_fn(gzip.decompress(body))
            except (EOFError, zlib.error):
                logger.warning("Truncated or corrupt gzip download")
                return False
            return True
        try:
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decompressed = decompressor.decompress(body, max_bytes + 1)
            if len(decompressed) > max_bytes or decompressor.unconsumed_tail:
                logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
                return False
            tail = decompressor.flush()
        except zlib.error:
            logger.warning("Corrupt gzip download")
            return False
        if not decompressor.eof:
            logger.warning("Gzip download truncated before EOF")
            return False
        if len(decompressed) + len(tail) > max_bytes:
            logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
            return False
        if decompressed:
            write_fn(decompressed)
        if tail:
            write_fn(tail)
        return True

    if body:
        write_fn(body)
    return True


def _read_bounded_response_bytes(
    response,
    *,
    max_bytes: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[bytes]:
    """Read HTTP response bytes in 64 KB chunks with size bound, gzip support, and progress callback."""
    buf = bytearray()
    ok = _stream_bounded_response(
        response,
        buf.extend,
        max_bytes=max_bytes,
        progress_callback=progress_callback,
    )
    return bytes(buf) if ok else None


def _read_raw_with_optional_gzip(response, max_bytes: int) -> bytes:
    """Read raw response bytes up to ``max_bytes``, transparently gunzipping if gzip-encoded."""
    info = response.info() if hasattr(response, "info") else getattr(response, "headers", None)
    encoding = (
        str(info.get("Content-Encoding") or "").strip().lower()
        if info is not None and hasattr(info, "get")
        else ""
    )
    if encoding != "gzip":
        raw = response.read(max_bytes + 1)
        return raw[:max_bytes] if len(raw) > max_bytes else raw

    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    compressed_limit = max(max_bytes * 2 + 65536, 128 * 1024)
    compressed_read = 0
    chunk_size = 65536
    while len(out) < max_bytes and compressed_read < compressed_limit:
        chunk = response.read(min(chunk_size, compressed_limit - compressed_read))
        if not chunk:
            break
        compressed_read += len(chunk)
        remaining = max_bytes - len(out)
        try:
            piece = decompressor.decompress(chunk, remaining)
        except zlib.error:
            if not out:
                return chunk[:max_bytes]
            break
        if piece:
            out.extend(piece)
        if decompressor.unconsumed_tail and len(out) >= max_bytes:
            break
    if len(out) < max_bytes:
        try:
            tail = decompressor.flush(max_bytes - len(out))
            if tail:
                out.extend(tail)
        except zlib.error:
            pass
    return bytes(out[:max_bytes])


def _read_html_response_bytes(response, max_bytes: int = 2 * 1024 * 1024) -> bytes:
    """Read HTML response bytes up to ``max_bytes``, gunzipping when gzip-encoded."""
    info = (
        response.info()
        if hasattr(response, "info")
        else getattr(response, "headers", None)
    )
    encoding = (
        str(info.get("Content-Encoding") or "").strip().lower()
        if info is not None and hasattr(info, "get")
        else ""
    )
    if encoding != "gzip":
        try:
            body = response.read(max_bytes + 1)
        except TypeError:
            body = response.read()
        if len(body) > max_bytes:
            raise ValueError("Moodle web response was too large")
        return body

    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = bytearray()
    compressed_limit = max_bytes + 65536
    compressed_read = 0
    chunk_size = 65536
    while True:
        remaining_comp = compressed_limit - compressed_read
        if remaining_comp <= 0:
            extra = response.read(1)
            if extra:
                raise ValueError("Moodle web response was too large")
            break
        try:
            chunk = response.read(min(chunk_size, remaining_comp))
        except TypeError:
            chunk = response.read()
        if not chunk:
            break
        compressed_read += len(chunk)
        allowed = (max_bytes + 1) - len(out)
        try:
            piece = decompressor.decompress(chunk, allowed)
        except zlib.error:
            if not out:
                if len(chunk) > max_bytes:
                    raise ValueError("Moodle web response was too large")
                return chunk
            break
        if piece:
            out.extend(piece)
        if len(out) > max_bytes or (decompressor.unconsumed_tail and len(out) >= max_bytes):
            raise ValueError("Moodle web response was too large")
        if decompressor.eof:
            break
    try:
        tail = decompressor.flush((max_bytes + 1) - len(out))
        if tail:
            out.extend(tail)
    except zlib.error:
        pass
    if len(out) > max_bytes:
        raise ValueError("Moodle web response was too large")
    return bytes(out)


@dataclass(frozen=True)
class DraftFileRecord:
    """The exact identity Moodle assigned to an uploaded draft file."""

    itemid: int
    filepath: str
    filename: str

    @property
    def identity(self) -> tuple[str, str]:
        return self.filepath, self.filename


@dataclass(frozen=True)
class DraftUploadResult:
    """Structured draft upload outcome without response content or credentials."""

    record: Optional[DraftFileRecord] = None
    error_code: str = ""


def _draft_upload_error_code(payload: object) -> str:
    """Return an explicit, code-like Moodle ``errorcode`` or ``errortype``."""
    if not isinstance(payload, dict):
        return ""
    for field in ("errorcode", "errortype"):
        if field not in payload:
            continue
        value = payload[field]
        if not isinstance(value, str):
            return "moodleerror"
        raw_code = value.strip()
        if not raw_code:
            continue
        code_chars = raw_code.replace("_", "")
        return (
            raw_code
            if len(raw_code) <= 64 and code_chars.isascii() and code_chars.isalnum()
            else "moodleerror"
        )
    return ""

# Phát hiện hệ điều hành/nền tảng
_is_android = hasattr(_sys, '_ANDROID_') or 'android' in getattr(_sys, 'platform', '').lower()
_is_ios = _sys.platform == 'ios' or (
    _sys.platform == 'darwin' and os.environ.get('FLET_APP_STORAGE_DATA', '') != ''
)

if _is_android:
    _DEFAULT_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
elif _is_ios:
    _DEFAULT_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
else:
    _DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

# SSL: dùng urllib default trên MỌI platform
# Custom ssl.create_default_context() và raw http.client.HTTPSConnection bị Cloudflare chặn (403).
# urllib.request.urlopen giữ nguyên header/TLS chuẩn, tương thích 100% Cloudflare & Moodle.


_DEFAULT_URLLIB_URLOPEN = urllib.request.urlopen
_MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024


def _close_response_quietly(resp: Any) -> None:
    close_fn = getattr(resp, "close", None)
    if callable(close_fn):
        try:
            close_fn()
        except Exception:
            pass


def _url_origin(url: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _urlopen(req, timeout=15):
    """Thin wrapper around urllib.request.urlopen with origin-pinned redirects."""
    if urllib.request.urlopen is _DEFAULT_URLLIB_URLOPEN:
        target_url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        origin = _url_origin(target_url)
        if origin:
            opener = urllib.request.build_opener(_SameMoodleOriginRedirectHandler(origin))
            resp = opener.open(req, timeout=timeout)
            geturl = getattr(resp, "geturl", None)
            final_url = str(geturl() or "") if callable(geturl) else ""
            if final_url and _url_origin(final_url) != origin:
                _close_response_quietly(resp)
                raise urllib.error.URLError("Untrusted cross-origin redirect")
            return resp
    return urllib.request.urlopen(req, timeout=timeout)


def _verify_demo_credentials_cached(
    username: str | None, password: str | None
) -> bool:
    """Delegate to the shared bounded PBKDF2 demo credential cache in `demo_sandbox`."""
    return verify_demo_credentials_cached(username, password)


def _validated_portal_api_base(raw_base: object) -> str:
    """Return normalized HTTPS UTH Portal API base URL, or empty string if untrusted."""
    if not isinstance(raw_base, str) or not raw_base or raw_base != raw_base.strip():
        return ""
    if (
        "\\" in raw_base
        or "?" in raw_base
        or "#" in raw_base
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw_base)
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw_base)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    host = (parsed.hostname or "").casefold()
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or not host
        or parsed.netloc.casefold() != host
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or host.startswith(".")
        or host.endswith(".")
        or ".." in host
        or not (host == "ut.edu.vn" or host.endswith(".ut.edu.vn"))
    ):
        return ""
    for label in host.split("."):
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(ch.isascii() and (ch.isalnum() or ch == "-") for ch in label)
        ):
            return ""
    return raw_base.rstrip("/")


def _demo_mime_for_path(path: str) -> str:
    lower_path = (path or "").lower()
    if lower_path == "/calendar/export.php" or lower_path.endswith(".ics"):
        return "text/calendar; charset=utf-8"
    if lower_path.endswith(".pdf"):
        return "application/pdf"
    if lower_path.endswith(".zip"):
        return "application/zip"
    if lower_path.endswith(".md"):
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"


class _DemoHttpResponse:
    """In-memory stdlib response double for offline Demo Mode HTML/ICS/file reads."""

    def __init__(
        self, final_url: str, body: bytes, content_type: str, status: int = 200
    ) -> None:
        self._final_url = final_url
        self._stream = io.BytesIO(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))

    def geturl(self) -> str:
        return self._final_url

    def info(self) -> Message:
        return self.headers

    def read(self, amt: int = -1) -> bytes:
        return (
            self._stream.read()
            if amt is None or amt < 0
            else self._stream.read(amt)
        )

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> "_DemoHttpResponse":
        return self

    def __exit__(self, *_args: Any) -> bool:
        self.close()
        return False


class _DemoHtmlOpener:
    """Offline session opener resolving relative URLs against `origin` without network calls."""

    def __init__(self, origin: str) -> None:
        self.origin = origin.rstrip("/")

    def open(
        self, fullurl: Any, data: bytes | None = None, timeout: float = 20
    ) -> _DemoHttpResponse:
        del data, timeout
        raw_url = (
            fullurl.full_url
            if isinstance(fullurl, urllib.request.Request)
            else str(fullurl or "").strip()
        )
        parsed = urllib.parse.urlsplit(raw_url)
        if parsed.scheme or parsed.netloc:
            if self.origin and not _is_same_moodle_origin_url(raw_url, self.origin):
                raise urllib.error.HTTPError(
                    raw_url, 302, "Untrusted Moodle redirect", Message(), None
                )
            resolved_url = raw_url
        else:
            if not self.origin:
                raise ValueError("Moodle site origin is not configured")
            resolved_url = urllib.parse.urljoin(
                f"{self.origin}/", raw_url.lstrip("/")
            )

        if not MoodleClient._has_valid_demo_credentials():
            login_url = f"{self.origin}/login/index.php" if self.origin else resolved_url
            return _DemoHttpResponse(login_url, b"", "text/html; charset=utf-8")

        resolved_path = urllib.parse.urlsplit(resolved_url).path or "/"
        file_bytes = get_demo_file_bytes(resolved_url)
        if file_bytes is not None:
            return _DemoHttpResponse(
                resolved_url, file_bytes, _demo_mime_for_path(resolved_path)
            )
        final_url, html_or_ics, redirected = render_demo_session_html(resolved_url)
        if redirected:
            raise urllib.error.HTTPError(
                final_url, 302, "Untrusted Moodle redirect", Message(), None
            )
        ct = (
            "text/calendar; charset=utf-8"
            if urllib.parse.urlsplit(final_url).path == "/calendar/export.php"
            else "text/html; charset=utf-8"
        )
        return _DemoHttpResponse(final_url, html_or_ics.encode("utf-8"), ct)


class MoodleClient:
    """HTTP client dùng urllib.request (stdlib) - zero dependency, mọi platform.
    
    Cloudflare KHÔNG chặn urllib (đã test).
    Chạy trên Desktop, iOS, Android mà không cần cài thêm gì.
    """
    
    # Giới hạn tần suất gọi API ở phía Client (Moodle không tự động bóp băng thông từ server-side)
    _MIN_INTERVAL = 0.05  # 50ms = tối đa 20 req/s (trước đây là 200ms - gây lãng phí 1.6s cho mỗi chu kỳ quét)
    _LIVE_SESSION_PROBE_TIMEOUT = 2.5

    def __init__(self, base_url: str | None = None):
        self._moodle_site: MoodleSite | None = moodle_site_from_origin(
            base_url if base_url is not None else settings.MOODLE_BASE_URL
        )
        self._last_login_error = ""
        self._portal_token: str = ""   # JWT lấy từ portal API - thời hạn khoảng 30 ngày
        self._portal_token_username: str = ""
        self._cached_user_id: Optional[int] = None
        self._cached_user_id_username: Optional[str] = None
        self._last_call_time: float = 0.0  # Nhãn thời gian đơn điệu (monotonic)
        self._cookie_jar = CookieJar()
        self._sesskey = ""
        self._session_owner = ""
        self._session_authenticated_at: float = 0.0
        self._session_generation: int = 0
        self._html_opener = None
        self._session_lock = threading.RLock()
        self._login_lock = threading.Lock()
        self._ws_token_lock = threading.Lock()
        self._throttle_lock = threading.Lock()
        self._open_session_meta_local = threading.local()
        self.last_grade_report_error = ""

    def _get_lock(self, attr_name: str, *, reentrant: bool = False):
        lock = getattr(self, attr_name, None)
        if lock is None:
            lock = threading.RLock() if reentrant else threading.Lock()
            setattr(self, attr_name, lock)
        return lock

    def _get_open_session_meta_local(self) -> threading.local:
        meta = getattr(self, "_open_session_meta_local", None)
        if meta is None:
            meta = threading.local()
            self._open_session_meta_local = meta
        return meta

    def pop_last_open_session_has_set_cookie(self) -> bool:
        """Return and clear whether the current thread's last ``open_session_raw`` saw ``Set-Cookie``."""
        meta = self._get_open_session_meta_local()
        value = bool(getattr(meta, "has_set_cookie", False))
        meta.has_set_cookie = False
        return value

    def _record_session_login_error(self, code: str) -> None:
        self._last_login_error = code
        self._get_open_session_meta_local().session_login_error = code

    def get_last_cookie_export_error(self) -> str:
        """Return the current thread's last HTML login error observed during ``get_session_cookies``."""
        return str(
            getattr(self._get_open_session_meta_local(), "last_cookie_export_error", "")
            or ""
        )

    def clear_session(self) -> None:
        """Clear HTML cookies, sesskey, portal token, cached userid, and owning username.

        Swaps ``_cookie_jar`` and resets ``_html_opener`` under ``_session_lock`` so
        any in-flight opener holds only a detached jar and this call stays instant.
        """
        with self._get_lock("_session_lock", reentrant=True):
            self._cookie_jar = CookieJar()
            self._html_opener = None
            self._sesskey = ""
            self._session_owner = ""
            self._session_authenticated_at = 0.0
            self._session_generation = int(getattr(self, "_session_generation", 0)) + 1
            self._portal_token = ""
            self._portal_token_username = ""
            self._cached_user_id = None
            self._cached_user_id_username = None

    def _reset_session_if_account_changed(self, username: str | None = None) -> None:
        """Drop stale HTML session cookies/sesskey/identity when the active account changes."""
        active_user = str(
            username if username is not None else getattr(settings, "UTH_USERNAME", "") or ""
        )
        active_norm = active_user.strip().casefold()
        with self._get_lock("_session_lock", reentrant=True):
            owner_changed = bool(
                self._session_owner
                and self._session_owner.strip().casefold() != active_norm
            )
            portal_owner = getattr(self, "_portal_token_username", "")
            uid_owner = getattr(self, "_cached_user_id_username", None)
            if owner_changed:
                self._cookie_jar = CookieJar()
                self._html_opener = None
                self._sesskey = ""
                self._session_owner = ""
                self._session_authenticated_at = 0.0
                self._session_generation = int(getattr(self, "_session_generation", 0)) + 1
            if portal_owner and portal_owner != active_norm:
                self._portal_token = ""
                self._portal_token_username = ""
            if uid_owner is not None and uid_owner != active_norm:
                self._cached_user_id = None
                self._cached_user_id_username = None

    @staticmethod
    def _is_demo_mode() -> bool:
        return is_demo_account(getattr(settings, "UTH_USERNAME", ""))

    @property
    def is_demo_mode(self) -> bool:
        return self._is_demo_mode()

    @staticmethod
    def _has_valid_demo_credentials() -> bool:
        return _verify_demo_credentials_cached(
            getattr(settings, "UTH_USERNAME", ""),
            getattr(settings, "UTH_PASSWORD", ""),
        )

    @property
    def moodle_site_origin(self) -> str:
        """The immutable trusted origin this client was configured to use."""
        return self._moodle_site.origin if self._moodle_site else ""

    @staticmethod
    def _clean_setting_str(attr: str) -> str:
        raw = getattr(settings, attr, "")
        return raw.strip() if isinstance(raw, str) else ""

    def _cached_ws_token_matches_active_account(
        self, *, backfill_owner: bool = False
    ) -> bool:
        """Return True if cached MOODLE_WS_TOKEN matches site origin and active username."""
        token = self._clean_setting_str("MOODLE_WS_TOKEN")
        if not token or not self.moodle_site_origin:
            return False
        if self._clean_setting_str("MOODLE_WS_TOKEN_ORIGIN") != self.moodle_site_origin:
            return False
        active_user = self._clean_setting_str("UTH_USERNAME")
        if not active_user or is_demo_account(active_user):
            return False
        token_owner = self._clean_setting_str("MOODLE_WS_TOKEN_USERNAME")
        if token_owner:
            return token_owner.casefold() == active_user.casefold()
        if backfill_owner:
            try:
                settings.MOODLE_WS_TOKEN_USERNAME = active_user
            except Exception:
                pass
        return True

    @property
    def has_site_credentials(self) -> bool:
        """Whether this client can authenticate only to its configured site."""
        if self._is_demo_mode():
            return self._moodle_site is not None and self._has_valid_demo_credentials()
        return bool(
            self._cached_ws_token_matches_active_account()
            or self._stored_credentials_match_site()
        )

    def _stored_credentials_match_site(self) -> bool:
        """Keep exactly unstamped legacy credentials on courses, never on THNN."""
        if (
            not self.moodle_site_origin
            or not settings.UTH_USERNAME
            or not settings.UTH_PASSWORD
        ):
            return False
        credential_site = moodle_site_from_origin(
            settings.UTH_CREDENTIALS_ORIGIN
        )
        if credential_site is not None:
            return credential_site.origin == self.moodle_site_origin
        return (
            settings.UTH_CREDENTIALS_ORIGIN == ""
            and self.moodle_site_origin == COURSES_MOODLE_SITE.origin
        )

    def can_authenticate_html_session(self) -> bool:
        """Whether stored UTH SSO credentials may authenticate an HTML WebView session.

        Requires a trusted Moodle site origin, non-demo non-empty credentials, and an
        explicitly stamped ``UTH_CREDENTIALS_ORIGIN`` matching a trusted UTH Moodle site
        (never unstamped ``""``). Unlike ``_get_ws_token()``, verified UTH SSO credentials
        work across ``courses`` and ``thnn`` HTML login forms.
        """
        if self._is_demo_mode() or not self.moodle_site_origin:
            return False
        username = str(getattr(settings, "UTH_USERNAME", "") or "").strip()
        password = str(getattr(settings, "UTH_PASSWORD", "") or "")
        if not username or not password.strip():
            return False
        credential_site = moodle_site_from_origin(
            getattr(settings, "UTH_CREDENTIALS_ORIGIN", "")
        )
        return credential_site is not None

    def _throttle(self):
        """Ensure minimum interval between API calls across concurrent threads."""
        with self._get_lock("_throttle_lock"):
            now = time.monotonic()
            last_call = float(getattr(self, "_last_call_time", 0.0))
            next_allowed = last_call + self._MIN_INTERVAL
            if now < next_allowed:
                sleep_for = next_allowed - now
                self._last_call_time = next_allowed
            else:
                sleep_for = 0.0
                self._last_call_time = now
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _ensure_html_opener(self):
        if self._is_demo_mode():
            return _DemoHtmlOpener(self.moodle_site_origin)
        with self._get_lock("_session_lock", reentrant=True):
            if self._html_opener is None:
                self._html_opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(self._cookie_jar),
                    _SameMoodleOriginRedirectHandler(self.moodle_site_origin),
                )
            return self._html_opener

    def _open_html(
        self,
        path: str,
        *,
        data: dict[str, str] | None = None,
        timeout: float = 20,
    ) -> tuple[str, str]:
        tls_opener = getattr(self._get_open_session_meta_local(), "html_opener", None)
        opener = tls_opener if tls_opener is not None else self._ensure_html_opener()
        origin = self.moodle_site_origin
        url = urllib.parse.urljoin(f"{origin}/", path.lstrip("/"))
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            url,
            data=encoded,
            method="POST" if data is not None else "GET",
            headers={
                "User-Agent": _DEFAULT_UA,
                "Accept": "text/html",
                "Accept-Encoding": "gzip",
            },
        )
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            parsed = urllib.parse.urlsplit(final_url)
            final_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            if final_origin != origin:
                raise ValueError("Moodle web response changed origin")
            body = _read_html_response_bytes(response, max_bytes=2 * 1024 * 1024)
        return final_url, body.decode("utf-8", errors="replace")

    def get_sesskey(self) -> str:
        """Return current authenticated Moodle session ``sesskey`` if available."""
        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            return self._sesskey or ""

    @staticmethod
    def _is_ajax_session_error(resp: Any) -> bool:
        """Return whether a Moodle AJAX response dict signals an expired/invalid session."""
        if not isinstance(resp, dict) or not resp.get("error"):
            return False
        exc_info = resp.get("exception")
        exc_text = ""
        if isinstance(exc_info, dict):
            exc_text = (
                f"{exc_info.get('errorcode') or ''} {exc_info.get('message') or ''}"
            )
        elif exc_info:
            exc_text = str(exc_info)
        err_text = (
            f"{resp.get('errorcode') or ''} {resp.get('message') or ''} {exc_text}"
        ).lower()
        return any(
            marker in err_text
            for marker in ("requirelogin", "requireslogin", "invalidsesskey", "sesskey")
        )

    def ensure_live_session(
        self,
        *,
        max_idle_seconds: float = 900.0,
        probe_timeout: float = _LIVE_SESSION_PROBE_TIMEOUT,
    ) -> bool:
        """Verify the Moodle HTML cookie session is still alive without holding ``_session_lock`` across HTTP."""
        if self._is_demo_mode() or not self.moodle_site_origin:
            return False

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            entry_user = (
                str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
            )
            sesskey = self._sesskey
            has_cookie = self._has_moodle_session_cookie()
            auth_at = float(getattr(self, "_session_authenticated_at", 0.0) or 0.0)
            owner = str(getattr(self, "_session_owner", "") or "").strip().casefold()
            gen = int(getattr(self, "_session_generation", 0))

        if (
            bool(entry_user)
            and has_cookie
            and auth_at > 0.0
            and (time.monotonic() - auth_at) < max_idle_seconds
            and (not owner or owner == entry_user)
        ):
            return True
        if not sesskey or not has_cookie:
            return bool(
                self.login_session(force=True) if sesskey else self.login_session()
            )

        budget = min(self._LIVE_SESSION_PROBE_TIMEOUT, max(0.1, float(probe_timeout)))
        probe_start = time.perf_counter()

        meta = self._get_open_session_meta_local()
        prev_ajax_timeout = getattr(meta, "ajax_timeout", None)
        prev_no_auto_login = getattr(meta, "ajax_no_auto_login", False)
        prev_expected_gen = getattr(meta, "ajax_expected_gen", None)
        meta.ajax_timeout = budget
        meta.ajax_no_auto_login = True
        meta.ajax_expected_gen = gen
        try:
            resp = self.call_ajax_api("core_session_time_remaining")
        except Exception as exc:
            logger.debug("Session liveness AJAX probe failed (%s)", type(exc).__name__)
            resp = None
        finally:
            meta.ajax_timeout = prev_ajax_timeout
            meta.ajax_no_auto_login = prev_no_auto_login
            meta.ajax_expected_gen = prev_expected_gen

        probe_state = "fallback"
        if isinstance(resp, dict) and not resp.get("error"):
            data = resp.get("data")
            if (
                isinstance(data, dict)
                and "userid" in data
                and data.get("userid") is not None
            ):
                try:
                    userid = int(data["userid"])
                    remaining = int(data.get("timeremaining") or 0)
                except (TypeError, ValueError):
                    userid = -1
                    remaining = -1
                if userid > 0 and remaining > 60:
                    probe_state = "alive"
                elif userid == 0 or (userid > 0 and remaining <= 60):
                    probe_state = "expired"
        elif self._is_ajax_session_error(resp):
            probe_state = "expired"

        if probe_state == "fallback":
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                pre_html_user = (
                    str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
                )
                pre_html_gen = int(getattr(self, "_session_generation", 0))
                pre_html_sesskey = self._sesskey
                pre_html_has_cookie = self._has_moodle_session_cookie()
                if pre_html_user != entry_user or not pre_html_user:
                    return False
                if pre_html_gen != gen:
                    return bool(pre_html_sesskey and pre_html_has_cookie)
                if not pre_html_sesskey or not pre_html_has_cookie:
                    probe_state = "expired"
                    probe_opener = None
                else:
                    probe_opener = self._ensure_html_opener()

            if probe_state == "fallback":
                elapsed = max(0.0, time.perf_counter() - probe_start)
                remaining_budget = budget - elapsed
                if remaining_budget <= 0.05:
                    probe_state = "network_error"
                else:
                    prev_tls_opener = getattr(meta, "html_opener", None)
                    meta.html_opener = probe_opener
                    try:
                        final_url, _ = self._open_html("/my/", timeout=remaining_budget)
                    except urllib.error.HTTPError as exc:
                        try:
                            logger.debug(
                                "Session liveness HTML probe failed (HTTPError %s)",
                                getattr(exc, "code", "?"),
                            )
                        finally:
                            exc.close()
                        probe_state = "network_error"
                    except Exception as exc:
                        logger.debug(
                            "Session liveness HTML probe failed (%s)",
                            type(exc).__name__,
                        )
                        probe_state = "network_error"
                    else:
                        if urllib.parse.urlsplit(final_url).path.endswith(
                            "/login/index.php"
                        ):
                            probe_state = "expired"
                        else:
                            probe_state = "alive"
                    finally:
                        meta.html_opener = prev_tls_opener

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            current_user = (
                str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
            )
            current_gen = int(getattr(self, "_session_generation", 0))
            if current_user != entry_user or not current_user:
                return False
            if current_gen != gen:
                return bool(self._sesskey and self._has_moodle_session_cookie())
            if probe_state == "alive":
                if self._sesskey and self._has_moodle_session_cookie():
                    self._session_authenticated_at = time.monotonic()
                    return True
                return False
            if probe_state == "network_error":
                return self._has_moodle_session_cookie()

        return bool(self.login_session(force=True))

    def get_session_cookies(
        self,
        *,
        ensure_login: bool = True,
        verify_live: bool = False,
        force_relogin: bool = False,
    ) -> list[dict[str, Any]]:
        """Return host-only cookie dicts from this client's cookie jar for WebView injection."""
        site_host = (
            urllib.parse.urlsplit(self.moodle_site_origin).hostname or ""
        ).casefold()
        if not site_host or self._is_demo_mode():
            return []

        meta = self._get_open_session_meta_local()
        meta.last_cookie_export_error = ""
        action = "none"
        had_sesskey = False
        prev_shared_err = ""
        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            entry_user = (
                str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
            )
            had_sesskey = bool(self._sesskey)
            has_cookie = self._has_moodle_session_cookie()
            auth_at = float(getattr(self, "_session_authenticated_at", 0.0) or 0.0)
            owner = str(getattr(self, "_session_owner", "") or "").strip().casefold()
            cookie_fresh = bool(
                entry_user
                and has_cookie
                and auth_at > 0.0
                and (time.monotonic() - auth_at) < 900.0
                and (not owner or owner == entry_user)
            )
            prev_shared_err = self._last_login_error
            if force_relogin:
                action = "force"
            elif ensure_login and verify_live:
                action = "none" if cookie_fresh else "verify_live"
            elif ensure_login and not cookie_fresh and (not had_sesskey or not has_cookie):
                action = "login"

        if action != "none":
            meta.session_login_error = None

        auth_ok = True
        if action == "force":
            try:
                auth_ok = bool(self.login_session(force=True))
            except Exception as exc:
                logger.warning(
                    "Forced session login before cookie export failed (%s)",
                    type(exc).__name__,
                )
                return []
        elif action == "verify_live":
            try:
                if (
                    not had_sesskey
                    and not has_cookie
                    and getattr(self.login_session, "__func__", None)
                    is MoodleClient.login_session
                ):
                    auth_ok = bool(self.login_session(require_sesskey=False))
                else:
                    auth_ok = bool(self.ensure_live_session())
            except Exception as exc:
                logger.warning(
                    "Session liveness check before cookie export failed (%s)",
                    type(exc).__name__,
                )
                auth_ok = False
        elif action == "login":
            try:
                if (
                    not had_sesskey
                    and getattr(self.login_session, "__func__", None)
                    is MoodleClient.login_session
                ):
                    auth_ok = bool(self.login_session(require_sesskey=False))
                else:
                    auth_ok = bool(
                        self.login_session(force=True)
                        if had_sesskey
                        else self.login_session()
                    )
            except Exception as exc:
                logger.warning(
                    "Session login before cookie export failed (%s)",
                    type(exc).__name__,
                )
                auth_ok = False

        call_err = ""
        if action != "none":
            tls_err = getattr(meta, "session_login_error", None)
            if isinstance(tls_err, str):
                call_err = tls_err
            elif self._last_login_error != prev_shared_err:
                call_err = self._last_login_error
            meta.last_cookie_export_error = call_err

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            if self._is_demo_mode():
                return []
            current_user = (
                str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
            )
            if action != "none" and current_user != entry_user:
                return []
            if not auth_ok and not (
                call_err == "quiz_session_active"
                and self._has_moodle_session_cookie()
            ):
                return []

            cookies: list[dict[str, Any]] = []
            for cookie in list(self._cookie_jar):
                if not cookie.name or cookie.value is None:
                    continue
                cookie_domain = (
                    str(getattr(cookie, "domain", "") or "")
                    .strip()
                    .lstrip(".")
                    .casefold()
                )
                if cookie_domain != site_host:
                    continue
                rest = getattr(cookie, "_rest", None) or {}
                http_only = any(str(k).casefold() == "httponly" for k in rest.keys())
                same_site: str | None = None
                for key, val in rest.items():
                    if str(key).casefold() == "samesite":
                        text = str(val).strip() if val is not None else ""
                        same_site = text or None
                        break
                cookies.append(
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": site_host,
                        "domain_specified": False,
                        "path": cookie.path or "/",
                        "secure": bool(cookie.secure),
                        "expires": cookie.expires,
                        "httpOnly": http_only,
                        "sameSite": same_site,
                    }
                )
            return cookies

    def _resolve_same_origin_path(self, url: str) -> str:
        origin = self.moodle_site_origin
        if not origin:
            raise ValueError("Moodle site origin is not configured")
        raw = str(url or "").strip()
        if not raw or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
            raise ValueError("Invalid session URL")
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme or parsed.netloc:
            if not _is_same_moodle_origin_url(raw, origin):
                raise ValueError("URL origin does not match Moodle site origin")
            return f"{parsed.path}?{parsed.query}" if parsed.query else (parsed.path or "/")
        resolved = urllib.parse.urljoin(f"{origin}/", raw.lstrip("/"))
        rp = urllib.parse.urlsplit(resolved)
        return f"{rp.path}?{rp.query}" if rp.query else (rp.path or "/")

    def submit_session_form(
        self,
        url: str,
        fields: dict[str, str],
        *,
        form_timeout: int = 20,
    ) -> tuple[str, str, bool]:
        """Submit a same-origin Moodle web form over authenticated session with retry."""
        path = self._resolve_same_origin_path(url)
        if self._is_demo_mode():
            raise RuntimeError("Demo mode is read-only")
        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            needs_login = not self._sesskey

        if needs_login and not self.login_session():
            raise RuntimeError("Moodle session login failed")

        post_fields = {str(k): str(v) for k, v in (fields or {}).items()}
        for attempt in range(2):
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                gen = int(getattr(self, "_session_generation", 0))
                owner = str(getattr(self, "_session_owner", "") or "")
                if "sesskey" in post_fields and self._sesskey:
                    post_fields["sesskey"] = self._sesskey
            try:
                final_url, html_text = self._open_html(
                    path, data=post_fields, timeout=form_timeout
                )
            except urllib.error.HTTPError as exc:
                try:
                    reason_text = f"{getattr(exc, 'msg', '')} {getattr(exc, 'reason', '')}"
                    redirect_target = str(
                        getattr(exc, "filename", "") or getattr(exc, "url", "") or ""
                    )
                    if "Untrusted Moodle redirect" in reason_text and redirect_target:
                        return redirect_target, "", True
                    raise
                finally:
                    exc.close()
            with self._get_lock("_session_lock", reentrant=True):
                current_gen = int(getattr(self, "_session_generation", 0))
                current_owner = str(getattr(self, "_session_owner", "") or "")
                if current_gen != gen and (
                    not self._sesskey or (owner and current_owner != owner)
                ):
                    raise RuntimeError("Moodle session expired")
            if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) == gen:
                        self._session_authenticated_at = 0.0
                if attempt == 0:
                    if not self.login_session(force=True):
                        raise RuntimeError("Moodle session re-login failed")
                    continue
                raise RuntimeError("Moodle session expired")
            with self._get_lock("_session_lock", reentrant=True):
                if int(getattr(self, "_session_generation", 0)) == gen and self._sesskey:
                    self._session_authenticated_at = time.monotonic()
            return final_url, html_text, False
        raise RuntimeError("Moodle session expired")

    def open_session_raw(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str = "",
        accept: str = "*/*",
        max_bytes: int = 8 * 1024 * 1024,
        timeout: int = 20,
    ) -> tuple[int, str, str, bytes]:
        """Fetch raw response bytes for same-origin Moodle resources with auto-login recovery."""
        path = self._resolve_same_origin_path(url)
        origin = self.moodle_site_origin
        req_method = (method or "GET").upper()
        target_url = urllib.parse.urljoin(f"{origin}/", path.lstrip("/"))
        if self._is_demo_mode():
            meta = self._get_open_session_meta_local()
            meta.has_set_cookie = False
            if not self._has_valid_demo_credentials():
                self._last_login_error = "invalid_credentials"
                raise RuntimeError("Moodle session login failed")
            if req_method not in {"GET", "HEAD"}:
                return (
                    403,
                    target_url,
                    "text/plain; charset=utf-8",
                    b"Demo mode is read-only",
                )
            demo_bytes = get_demo_file_bytes(target_url)
            if demo_bytes is not None:
                ct = _demo_mime_for_path(urllib.parse.urlsplit(target_url).path)
                payload = b"" if req_method == "HEAD" else demo_bytes[:max_bytes]
                return 200, target_url, ct, payload
            final_url, html_text, redirected = render_demo_session_html(target_url)
            if redirected:
                return 302, final_url, "text/plain", b""
            ct = (
                "text/calendar; charset=utf-8"
                if urllib.parse.urlsplit(final_url).path == "/calendar/export.php"
                else "text/html; charset=utf-8"
            )
            raw = html_text.encode("utf-8")[:max_bytes]
            return 200, final_url, ct, (b"" if req_method == "HEAD" else raw)
        headers: dict[str, str] = {
            "User-Agent": _DEFAULT_UA,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        }
        if body is not None and content_type:
            headers["Content-Type"] = content_type

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            needs_login = bool(not self._sesskey and not path.startswith("/login/index.php"))
        if needs_login:
            self.login_session()
        with self._get_lock("_session_lock", reentrant=True):
            opener = self._ensure_html_opener()
            gen = int(getattr(self, "_session_generation", 0))
            owner = str(getattr(self, "_session_owner", "") or "")

        meta = self._get_open_session_meta_local()
        meta.has_set_cookie = False

        for attempt in range(2):
            request = urllib.request.Request(
                target_url,
                data=body if req_method == "POST" else None,
                method=req_method,
                headers=headers,
            )
            _OPEN_SESSION_REDIRECT_STATE.has_set_cookie = False
            attempt_has_set_cookie = False
            try:
                with opener.open(request, timeout=timeout) as response:
                    status = int(getattr(response, "status", 200) or 200)
                    final_url = response.geturl()
                    resp_headers = getattr(response, "headers", None)
                    if resp_headers is None and hasattr(response, "info"):
                        resp_headers = response.info()
                    attempt_has_set_cookie = bool(
                        getattr(_OPEN_SESSION_REDIRECT_STATE, "has_set_cookie", False)
                        or _headers_have_set_cookie(resp_headers)
                    )
                    resp_ct = str(
                        response.headers.get("Content-Type", "application/octet-stream")
                    )
                    raw = _read_raw_with_optional_gzip(response, max_bytes)
            except urllib.error.HTTPError as exc:
                try:
                    attempt_has_set_cookie = bool(
                        getattr(_OPEN_SESSION_REDIRECT_STATE, "has_set_cookie", False)
                        or _headers_have_set_cookie(getattr(exc, "headers", None))
                    )
                    meta.has_set_cookie = attempt_has_set_cookie
                    reason_text = f"{getattr(exc, 'msg', '')} {getattr(exc, 'reason', '')}"
                    redirect_target = str(
                        getattr(exc, "filename", "") or getattr(exc, "url", "") or ""
                    )
                    if "Untrusted Moodle redirect" in reason_text and redirect_target:
                        return 302, redirect_target, "text/plain", b""
                    err_ct = str(
                        exc.headers.get("Content-Type", "text/html; charset=utf-8")
                        if exc.headers
                        else "text/html; charset=utf-8"
                    )
                    err_body = _read_raw_with_optional_gzip(
                        exc, min(max_bytes, 512 * 1024)
                    )
                    return int(exc.code), str(exc.geturl() or target_url), err_ct, err_body
                finally:
                    exc.close()
            finally:
                _OPEN_SESSION_REDIRECT_STATE.has_set_cookie = False

            parsed = urllib.parse.urlsplit(final_url)
            final_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            if final_origin != origin:
                raise ValueError("Moodle response changed origin")
            if parsed.path.endswith("/login/index.php") and not path.startswith("/login/index.php"):
                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) == gen:
                        self._session_authenticated_at = 0.0
                if attempt == 0:
                    need_relogin = False
                    with self._get_lock("_session_lock", reentrant=True):
                        current_gen = int(getattr(self, "_session_generation", 0))
                        current_owner = str(getattr(self, "_session_owner", "") or "")
                        if current_gen != gen:
                            if not self._sesskey or (owner and current_owner != owner):
                                raise RuntimeError("Moodle session expired")
                            gen = current_gen
                            owner = current_owner
                            opener = self._ensure_html_opener()
                        else:
                            need_relogin = True
                    if need_relogin:
                        if not self.login_session(force=True):
                            raise RuntimeError("Moodle session expired")
                        with self._get_lock("_session_lock", reentrant=True):
                            gen = int(getattr(self, "_session_generation", 0))
                            owner = str(getattr(self, "_session_owner", "") or "")
                            opener = self._ensure_html_opener()
                    continue
                raise RuntimeError("Moodle session expired")

            with self._get_lock("_session_lock", reentrant=True):
                current_gen = int(getattr(self, "_session_generation", 0))
                current_owner = str(getattr(self, "_session_owner", "") or "")
                if current_gen != gen and (
                    not self._sesskey or (owner and current_owner != owner)
                ):
                    raise RuntimeError("Moodle session expired")
                if self._sesskey and not parsed.path.endswith("/login/index.php"):
                    self._session_authenticated_at = time.monotonic()
            meta.has_set_cookie = attempt_has_set_cookie
            return status, final_url, resp_ct, raw

        raise RuntimeError("Moodle session expired")

    def open_session_url(self, url: str) -> tuple[str, str, bool]:
        """Open a same-origin Moodle web page using cookie session, recovering once on expiry."""
        path = self._resolve_same_origin_path(url)
        if self._is_demo_mode():
            if not self._has_valid_demo_credentials():
                self._last_login_error = "invalid_credentials"
                raise RuntimeError("Moodle session login failed")
            resolved_url = urllib.parse.urljoin(f"{self.moodle_site_origin}/", path.lstrip("/"))
            return render_demo_session_html(resolved_url)
        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            needs_login = not self._sesskey

        if needs_login and not self.login_session():
            raise RuntimeError("Moodle session login failed")

        for attempt in range(2):
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                gen = int(getattr(self, "_session_generation", 0))
                owner = str(getattr(self, "_session_owner", "") or "")
            try:
                final_url, html_text = self._open_html(path)
            except urllib.error.HTTPError as exc:
                try:
                    reason_text = f"{getattr(exc, 'msg', '')} {getattr(exc, 'reason', '')}"
                    redirect_target = str(
                        getattr(exc, "filename", "") or getattr(exc, "url", "") or ""
                    )
                    if "Untrusted Moodle redirect" in reason_text and redirect_target:
                        return redirect_target, "", True
                    raise
                finally:
                    exc.close()
            with self._get_lock("_session_lock", reentrant=True):
                current_gen = int(getattr(self, "_session_generation", 0))
                current_owner = str(getattr(self, "_session_owner", "") or "")
                if current_gen != gen and (
                    not self._sesskey or (owner and current_owner != owner)
                ):
                    raise RuntimeError("Moodle session expired")
            if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) == gen:
                        self._session_authenticated_at = 0.0
                if attempt == 0:
                    if not self.login_session(force=True):
                        raise RuntimeError("Moodle session re-login failed")
                    continue
                raise RuntimeError("Moodle session expired")
            with self._get_lock("_session_lock", reentrant=True):
                if int(getattr(self, "_session_generation", 0)) == gen and self._sesskey:
                    self._session_authenticated_at = time.monotonic()
            return final_url, html_text, False
        raise RuntimeError("Moodle session expired")

    @staticmethod
    def _extract_sesskey_from_html(html: str) -> str:
        """Extract Moodle ``sesskey`` from page HTML without extra network requests."""
        if not isinstance(html, str) or not html:
            return ""
        import re

        match = re.search(r'"sesskey":"([a-zA-Z0-9]+)"', html)
        if match:
            return match.group(1)
        match2 = re.search(r"sesskey=([a-zA-Z0-9]+)", html)
        if match2:
            return match2.group(1)
        return ""

    def _has_moodle_session_cookie(self) -> bool:
        site_host = (
            urllib.parse.urlsplit(self.moodle_site_origin).hostname or ""
        ).casefold()
        with self._get_lock("_session_lock", reentrant=True):
            return any(
                getattr(cookie, "name", "").casefold() == "moodlesession"
                and bool(getattr(cookie, "value", ""))
                and (
                    not site_host
                    or str(getattr(cookie, "domain", "") or "")
                    .strip()
                    .lstrip(".")
                    .casefold()
                    == site_host
                )
                for cookie in list(self._cookie_jar)
            )

    def login_session(
        self,
        username: str = None,
        password: str = None,
        force: bool = False,
        *,
        quiz_lease: QuizSessionLease | None = None,
        require_sesskey: bool = True,
    ) -> bool:
        """Đăng nhập bằng form HTML để lấy MoodleSession và sesskey (non-blocking w.r.t clear_session)."""
        active_raw = str(getattr(settings, "UTH_USERNAME", "") or "")
        active_norm = normalize_demo_username(active_raw).casefold()
        if username is not None and (
            not isinstance(username, str) or not username.strip()
        ):
            self._record_session_login_error("invalid_credentials")
            return False
        if password is not None and (
            not isinstance(password, str) or password == ""
        ):
            if username is None or (
                bool(active_norm)
                and normalize_demo_username(username).casefold() == active_norm
            ):
                with self._get_lock("_session_lock", reentrant=True):
                    self._cookie_jar = CookieJar()
                    self._html_opener = None
                    self._sesskey = ""
                    self._session_owner = ""
                    self._session_authenticated_at = 0.0
                    self._session_generation = (
                        int(getattr(self, "_session_generation", 0)) + 1
                    )
            self._record_session_login_error("invalid_credentials")
            return False

        eff_user = active_raw if username is None else username
        target_norm = normalize_demo_username(eff_user).casefold()
        if password is None:
            if username is not None and (
                not active_norm or target_norm != active_norm
            ):
                self._record_session_login_error("invalid_credentials")
                return False
            eff_pwd = getattr(settings, "UTH_PASSWORD", "")
        else:
            eff_pwd = password

        if is_demo_account(eff_user):
            with self._get_lock("_session_lock", reentrant=True):
                canonical = normalize_demo_username(eff_user)
                is_mismatched_probe = (
                    username is not None
                    and bool(active_norm)
                    and canonical.casefold() != active_norm
                )
                if not is_mismatched_probe:
                    self._reset_session_if_account_changed(canonical)
                if not self.moodle_site_origin or not _verify_demo_credentials_cached(
                    eff_user, eff_pwd
                ):
                    if not is_mismatched_probe:
                        self._cookie_jar = CookieJar()
                        self._html_opener = None
                        self._sesskey = ""
                        self._session_owner = ""
                        self._session_authenticated_at = 0.0
                        self._session_generation = (
                            int(getattr(self, "_session_generation", 0)) + 1
                        )
                    self._record_session_login_error("invalid_credentials")
                    return False
                if username is None or (
                    bool(active_norm) and canonical.casefold() == active_norm
                ):
                    settings.MOODLE_WS_TOKEN = ""
                    settings.MOODLE_WS_TOKEN_ORIGIN = ""
                    settings.MOODLE_WS_TOKEN_USERNAME = ""
                if not is_mismatched_probe:
                    self._cookie_jar = CookieJar()
                    self._html_opener = None
                    self._sesskey = "demoSessKey2026"
                    self._session_owner = canonical
                    self._session_authenticated_at = 0.0
                    self._session_generation = (
                        int(getattr(self, "_session_generation", 0)) + 1
                    )
                self._record_session_login_error("")
                return True

        username = str(eff_user or "").strip()
        password = str(eff_pwd or "")

        with self._get_lock("_login_lock"):
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed(username)

                if not force and self._sesskey and quiz_lease is None:
                    self._record_session_login_error("")
                    return True

                if not self.moodle_site_origin or not username or not password:
                    self._record_session_login_error("invalid_credentials")
                    return False

                try:
                    session_key = quiz_session_key(self._moodle_site, username)
                except ValueError:
                    self._record_session_login_error("invalid_credentials")
                    return False
                if quiz_lease is not None and (
                    quiz_lease.session_key != session_key or not quiz_lease.is_active
                ):
                    self._record_session_login_error("quiz_session_lease_invalid")
                    return False

                if not _begin_quiz_session_login(session_key, lease=quiz_lease):
                    # Leave the existing cookie jar and sesskey intact while a quiz
                    # player holds this account/site session.
                    self._record_session_login_error("quiz_session_active")
                    return False

                self._cookie_jar = CookieJar()
                self._html_opener = None
                self._sesskey = ""
                self._session_owner = ""
                self._session_authenticated_at = 0.0
                self._session_generation = int(getattr(self, "_session_generation", 0)) + 1
                start_gen = self._session_generation

            authenticated = False
            login_jar = CookieJar()
            login_opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(login_jar),
                _SameMoodleOriginRedirectHandler(self.moodle_site_origin),
            )
            meta = self._get_open_session_meta_local()
            prev_tls_opener = getattr(meta, "html_opener", None)
            meta.html_opener = login_opener
            try:
                _, login_page = self._open_html("/login/index.php")
                parsed_login = _MoodleHtmlFormParser()
                parsed_login.feed(login_page)
                login_token = parsed_login.inputs.get("logintoken", "")
                if not login_token:
                    logger.error("Không tìm thấy logintoken trên trang đăng nhập.")
                    self._record_session_login_error("server_error")
                    return False

                final_url, post_html = self._open_html(
                    "/login/index.php",
                    data={
                        "anchor": "",
                        "logintoken": login_token,
                        "username": username,
                        "password": password,
                    },
                )
                if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                    logger.warning("Đăng nhập session thất bại (sai tài khoản hoặc lỗi Moodle).")
                    self._record_session_login_error("invalid_credentials")
                    return False

                sesskey = self._extract_sesskey_from_html(post_html)
                if not sesskey and require_sesskey:
                    # Lấy trang Dashboard để trích xuất sesskey khi POST redirect chưa có sẵn
                    _, dashboard_page = self._open_html("/my/")
                    sesskey = self._extract_sesskey_from_html(dashboard_page)

                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) != start_gen:
                        self._record_session_login_error("session_invalidated")
                        return False

                    if len(login_jar) > 0:
                        self._cookie_jar = login_jar
                        self._html_opener = login_opener

                    if sesskey:
                        self._sesskey = sesskey
                        self._session_owner = str(username)
                        self._session_authenticated_at = time.monotonic()
                        self._record_session_login_error("")
                        self._session_generation += 1
                        authenticated = True
                        return True

                    if not require_sesskey and self._has_moodle_session_cookie():
                        self._sesskey = ""
                        self._session_owner = str(username)
                        self._session_authenticated_at = time.monotonic()
                        self._record_session_login_error("")
                        self._session_generation += 1
                        authenticated = True
                        return True

                    self._cookie_jar = CookieJar()
                    self._html_opener = None
                    self._session_authenticated_at = 0.0
                    if not require_sesskey:
                        self._record_session_login_error("session_cookie_missing")
                        return False

                    logger.error("Không trích xuất được sesskey sau khi login.")
                    self._record_session_login_error("server_error")
                    return False

            except Exception as e:
                if quiz_lease is not None:
                    logger.warning(
                        "Quiz HTML session login failed (%s)", type(e).__name__
                    )
                else:
                    logger.error(f"Lỗi login_session: {e}")
                self._record_session_login_error("network_error")
                return False
            finally:
                meta.html_opener = prev_tls_opener
                if not authenticated:
                    with self._get_lock("_session_lock", reentrant=True):
                        if int(getattr(self, "_session_generation", 0)) == start_gen:
                            self._cookie_jar = CookieJar()
                            self._html_opener = None
                            self._sesskey = ""
                            self._session_owner = ""
                            self._session_authenticated_at = 0.0
                _end_quiz_session_login(session_key)

    def call_ajax_api(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle AJAX service. Trả về None nếu lỗi hoặc HTTP fail."""
        if self._is_demo_mode():
            if not self.moodle_site_origin:
                self._last_login_error = "untrusted_site"
                return None
            if not self._has_valid_demo_credentials():
                self._last_login_error = "invalid_credentials"
                return None
            return handle_demo_ajax_call(
                function, params, site_origin=self.moodle_site_origin
            )
        meta = self._get_open_session_meta_local()
        no_auto_login = bool(getattr(meta, "ajax_no_auto_login", False))
        expected_gen = getattr(meta, "ajax_expected_gen", None)

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed()
            current_gen = int(getattr(self, "_session_generation", 0))
            if expected_gen is not None and current_gen != expected_gen:
                return None
            needs_login = not self._sesskey
            if needs_login and no_auto_login:
                return None

        if needs_login and not self.login_session():
            return None

        with self._get_lock("_session_lock", reentrant=True):
            current_gen = int(getattr(self, "_session_generation", 0))
            if expected_gen is not None and current_gen != expected_gen:
                return None
            sesskey = self._sesskey
            gen = current_gen
            opener = self._ensure_html_opener()
        if not sesskey:
            return None

        origin = self.moodle_site_origin
        url = f"{origin}/lib/ajax/service.php?sesskey={sesskey}&info={function}"
        
        payload = [{
            "index": 0,
            "methodname": function,
            "args": params
        }]
        raw_body = json.dumps(payload).encode("utf-8")
        
        request = urllib.request.Request(
            url,
            data=raw_body,
            headers={
                "User-Agent": _DEFAULT_UA,
                "Content-Type": "application/json",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest"
            },
            method="POST",
        )
        
        req_timeout = getattr(
            self._get_open_session_meta_local(), "ajax_timeout", None
        ) or 20
        try:
            with opener.open(request, timeout=req_timeout) as response:
                body = _read_bounded_response_bytes(
                    response, max_bytes=_MAX_API_RESPONSE_BYTES
                )
                if body is None:
                    logger.error("AJAX response vượt quá giới hạn kích thước.")
                    return None
                try:
                    data = json.loads(body)
                except ValueError:
                    logger.error("AJAX response không phải JSON.")
                    return None
                
                if isinstance(data, list) and len(data) > 0:
                    resp = data[0]
                    if isinstance(resp, dict) and resp.get("error"):
                        if self._is_ajax_session_error(resp):
                            logger.debug(
                                "AJAX API session error (%s): %s - %s",
                                function,
                                resp.get("exception"),
                                resp.get("message"),
                            )
                            with self._get_lock("_session_lock", reentrant=True):
                                if int(getattr(self, "_session_generation", 0)) == gen:
                                    self._sesskey = ""
                                    self._session_authenticated_at = 0.0
                        else:
                            logger.error(
                                f"AJAX API error: {resp.get('exception')} - {resp.get('message')}"
                            )
                    elif isinstance(resp, dict):
                        with self._get_lock("_session_lock", reentrant=True):
                            if (
                                int(getattr(self, "_session_generation", 0)) == gen
                                and self._sesskey
                            ):
                                self._session_authenticated_at = time.monotonic()
                    return resp
                return None
        except Exception as e:
            logger.error(f"Lỗi call_ajax_api: {e}")
            return None


    def fetch_grade_report_html(self, course_id: int) -> Optional[str]:
        """Tải HTML /grade/report/user/index.php?id=<course_id> bằng session đăng nhập.

        Trả None nếu lỗi; lý do nằm ở ``last_grade_report_error``.
        """
        if self._is_demo_mode():
            self.last_grade_report_error = ""
            if not self.moodle_site_origin:
                self.last_grade_report_error = "untrusted_site"
                return None
            if not self._has_valid_demo_credentials():
                self._last_login_error = "invalid_credentials"
                self.last_grade_report_error = "login_session:invalid_credentials"
                return None
            return render_demo_grade_report_html(
                int(course_id), site_origin=self.moodle_site_origin
            )
        path = f"/grade/report/user/index.php?id={int(course_id)}"
        self.last_grade_report_error = ""
        for attempt in range(2):
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                needs_login = not self._sesskey
            if needs_login and not self.login_session():
                self.last_grade_report_error = (
                    f"login_session:{self._last_login_error or 'failed'}"
                )
                logger.warning(
                    "Grade report: đăng nhập session thất bại (%s)",
                    self._last_login_error,
                )
                return None
            with self._get_lock("_session_lock", reentrant=True):
                gen = int(getattr(self, "_session_generation", 0))
                owner = str(getattr(self, "_session_owner", "") or "")
            try:
                final_url, body = self._open_html(path)
            except Exception as exc:
                self.last_grade_report_error = f"http:{type(exc).__name__}:{exc}"
                logger.warning("Không tải được grade report môn %s: %s", course_id, exc)
                return None
            with self._get_lock("_session_lock", reentrant=True):
                current_gen = int(getattr(self, "_session_generation", 0))
                current_owner = str(getattr(self, "_session_owner", "") or "")
                if current_gen != gen and (
                    not self._sesskey or (owner and current_owner != owner)
                ):
                    self.last_grade_report_error = "session_expired"
                    return None
            if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                # Session hết hạn -> đăng nhập lại một lần
                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) == gen:
                        self._sesskey = ""
                        self._session_authenticated_at = 0.0
                if attempt == 0 and self.login_session(force=True):
                    continue
                self.last_grade_report_error = "session_expired"
                return None
            with self._get_lock("_session_lock", reentrant=True):
                if int(getattr(self, "_session_generation", 0)) == gen and self._sesskey:
                    self._session_authenticated_at = time.monotonic()
            return body
        return None

    def close(self):
        """Không cần cleanup - urllib không dùng connection pool."""
        pass

    # Low-level HTTP helpers

    def _post(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST form-encoded data. Returns (status, parsed_json_or_None)."""
        import random
        encoded = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept', 'application/json, */*')
        req.add_header('Accept-Language', 'en-US,en;q=0.9,vi;q=0.8')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 3
        for attempt in range(max_attempts):
            self._throttle()
            resp = None
            try:
                resp = _urlopen(req, timeout=timeout)
                status = int(getattr(resp, "status", 200) or 200)
                body = _read_bounded_response_bytes(resp, max_bytes=_MAX_API_RESPONSE_BYTES)
                if body is None:
                    return status, None
                try:
                    return status, json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    return status, None
            except urllib.error.HTTPError as e:
                code = e.code
                _close_response_quietly(e)
                if code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    logger.warning(
                        "POST %s trả về HTTP %d. Đang thử lại sau %.2fs...",
                        url.split('?')[0], code, backoff
                    )
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d từ POST %s", code, url.split('?')[0])
                return code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                is_timeout = isinstance(e, TimeoutError) or "time" in str(e).lower()
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    logger.warning(
                        "POST %s thất bại ở lần thử %d (%s): %s. Đang thử lại sau %.2fs...",
                        url.split('?')[0], attempt + 1, "Timeout" if is_timeout else "NetworkError", e, backoff
                    )
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST %s thất bại sau %d lần thử: %s", url.split('?')[0], max_attempts, e)
                    raise e
            finally:
                if resp is not None:
                    _close_response_quietly(resp)

    def _post_json(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST JSON body. Returns (status, parsed_json_or_None)."""
        import random
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', 'application/json')
        req.add_header('Accept', 'application/json')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 3
        for attempt in range(max_attempts):
            self._throttle()
            resp = None
            try:
                resp = _urlopen(req, timeout=timeout)
                status = int(getattr(resp, "status", 200) or 200)
                resp_body = _read_bounded_response_bytes(resp, max_bytes=_MAX_API_RESPONSE_BYTES)
                if resp_body is None:
                    return status, None
                try:
                    return status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return status, None
            except urllib.error.HTTPError as e:
                code = e.code
                _close_response_quietly(e)
                if code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-JSON %s", code, url.split('?')[0])
                return code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-JSON %s failed: %s", url.split('?')[0], e)
                    raise e
            finally:
                if resp is not None:
                    _close_response_quietly(resp)

    def _post_multipart(self, url: str, fields: dict, files: dict, timeout: float = 60) -> tuple:
        """POST multipart/form-data (cho upload file). Returns (status, parsed_json_or_None)."""
        import uuid
        import random
        boundary = uuid.uuid4().hex
        
        body_parts = []
        # Các trường biểu mẫu (form fields)
        for key, val in fields.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f'{val}\r\n'
            )
        # Các trường tệp tin (file fields)
        for field_name, (filename, file_bytes) in files.items():
            body_parts.append(
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f'Content-Type: application/octet-stream\r\n\r\n'
            )
        body_parts.append(f'--{boundary}--\r\n')
        
        # Dựng thân yêu cầu dạng nhị phân (binary body)
        body = b''
        file_items = list(files.items())
        file_idx = 0
        for i, part in enumerate(body_parts):
            body += part.encode('utf-8')
            # Chèn dữ liệu byte của tệp ngay sau phần đầu (header) của mỗi phần tệp
            if i < len(body_parts) - 1 and 'filename=' in part:
                _, (_, file_bytes) = file_items[file_idx]
                body += file_bytes + b'\r\n'
                file_idx += 1
        
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        req.add_header('Accept-Encoding', 'gzip')
        
        max_attempts = 2  # Kích thước tệp lớn, giảm số lần thử lại (retry)
        for attempt in range(max_attempts):
            self._throttle()
            resp = None
            try:
                resp = _urlopen(req, timeout=timeout)
                status = int(getattr(resp, "status", 200) or 200)
                resp_body = _read_bounded_response_bytes(resp, max_bytes=_MAX_API_RESPONSE_BYTES)
                if resp_body is None:
                    return status, None
                try:
                    return status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return status, None
            except urllib.error.HTTPError as e:
                code = e.code
                _close_response_quietly(e)
                if code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-MP %s", code, url.split('?')[0])
                return code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-MP %s failed: %s", url.split('?')[0], e)
                    raise e
            finally:
                if resp is not None:
                    _close_response_quietly(resp)

    def _get(self, url: str, timeout: float = 15) -> tuple:
        """GET request. Returns (status, raw_bytes)."""
        req = urllib.request.Request(url)
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept-Encoding', 'gzip')
        
        resp = None
        try:
            resp = _urlopen(req, timeout=timeout)
            status = int(getattr(resp, "status", 200) or 200)
            body = _read_bounded_response_bytes(resp, max_bytes=_MAX_API_RESPONSE_BYTES)
            return status, body
        except urllib.error.HTTPError as e:
            code = e.code
            _close_response_quietly(e)
            logger.warning("HTTP %d from GET %s", code, url.split('?')[0])
            return code, None
        finally:
            if resp is not None:
                _close_response_quietly(resp)


    # Web Services API (Dựa trên Token, không lưu trạng thái/stateless)

    def _get_ws_token(
        self,
        username: str = None,
        password: str = None,
        force: bool = False,
        *,
        persist: bool = True,
    ) -> str:
        """Lấy Web Services token từ Moodle.
        
        Token này stateless, không ảnh hưởng browser session.
        Valid rất lâu (~30 ngày), cache trong settings khi ``persist=True``.
        """
        if not self.moodle_site_origin:
            logger.warning("Moodle site is not explicitly trusted or configured.")
            self._last_login_error = "untrusted_site"
            return ""

        with self._get_lock("_ws_token_lock"):
            credentials_are_explicit = username is not None or password is not None
            probe_user = (
                username
                if credentials_are_explicit
                else getattr(settings, "UTH_USERNAME", "")
            )
            probe_pwd = (
                password
                if credentials_are_explicit
                else getattr(settings, "UTH_PASSWORD", "")
            )
            if is_demo_account(probe_user):
                valid_demo = _verify_demo_credentials_cached(probe_user, probe_pwd)
                self._last_login_error = "" if valid_demo else "invalid_credentials"
                if persist:
                    active_norm = (
                        str(getattr(settings, "UTH_USERNAME", "") or "")
                        .strip()
                        .casefold()
                    )
                    target_norm = normalize_demo_username(probe_user).casefold()
                    if not credentials_are_explicit or (
                        bool(active_norm) and target_norm == active_norm
                    ):
                        settings.MOODLE_WS_TOKEN = ""
                        settings.MOODLE_WS_TOKEN_ORIGIN = ""
                        settings.MOODLE_WS_TOKEN_USERNAME = ""
                return ""

            # Reuse only a token whose issuing Moodle origin and owning account match.
            if (
                persist
                and not force
                and not credentials_are_explicit
                and self._cached_ws_token_matches_active_account(backfill_owner=True)
            ):
                return settings.MOODLE_WS_TOKEN

            if credentials_are_explicit:
                user = username or ""
                pwd = password or ""
            elif self._stored_credentials_match_site():
                user = settings.UTH_USERNAME
                pwd = settings.UTH_PASSWORD
            else:
                logger.warning("Stored credentials are not verified for this Moodle site.")
                self._last_login_error = "credentials_site_mismatch"
                return ""

            if not user or not pwd:
                logger.warning("Chưa có thông tin đăng nhập để lấy WS token.")
                self._last_login_error = "missing_credentials"
                return ""

            with self._get_lock("_session_lock", reentrant=True):
                start_gen = int(getattr(self, "_session_generation", 0))
                start_active_norm = self._clean_setting_str("UTH_USERNAME").casefold()

            try:
                status, data = self._post(
                    f"{self.moodle_site_origin}/login/token.php",
                    {'username': user, 'password': pwd, 'service': 'moodle_mobile_app'},
                    timeout=15
                )

                if data and 'token' in data:
                    token_val = str(data['token'] or "")
                    if not token_val:
                        self._last_login_error = "server_error"
                        return ""
                    should_save = False
                    with self._get_lock("_session_lock", reentrant=True):
                        current_gen = int(getattr(self, "_session_generation", 0))
                        current_active_raw = self._clean_setting_str("UTH_USERNAME")
                        current_active_norm = current_active_raw.casefold()
                        session_valid = current_gen == start_gen
                        if session_valid:
                            if not credentials_are_explicit:
                                session_valid = (
                                    bool(current_active_norm)
                                    and not is_demo_account(current_active_raw)
                                    and current_active_norm == user.strip().casefold()
                                )
                            elif persist:
                                session_valid = current_active_norm == start_active_norm
                        if not session_valid:
                            logger.info(
                                "Discarding in-flight WS token: session or active account changed."
                            )
                            return ""
                        if persist:
                            if credentials_are_explicit:
                                settings.UTH_USERNAME = user
                                settings.UTH_PASSWORD = pwd
                            settings.UTH_CREDENTIALS_ORIGIN = self.moodle_site_origin
                            settings.MOODLE_WS_TOKEN = token_val
                            settings.MOODLE_WS_TOKEN_ORIGIN = self.moodle_site_origin
                            settings.MOODLE_WS_TOKEN_USERNAME = user.strip()
                            should_save = True
                    if should_save:
                        from config import save_settings
                        save_settings()
                    logger.info("Lấy WS API token thành công.")
                    self._last_login_error = ""
                    return token_val
                elif data:
                    error = data.get('error', 'Unknown error')
                    logger.warning(f"Không lấy được WS token: {error}")
                    if 'invalidlogin' in data.get('errorcode', ''):
                        self._last_login_error = "invalid_credentials"
                    else:
                        self._last_login_error = "server_error"
                    return ""
                else:
                    logger.error("Response không phải JSON khi lấy WS token.")
                    self._last_login_error = "server_error"
                    return ""
            except Exception as e:
                logger.error(f"Lỗi khi lấy WS token: {e}")
                self._last_login_error = "network_error"
                return ""

    def login(
        self,
        username: str = None,
        password: str = None,
        force: bool = False,
        persist: bool = True,
    ) -> bool:
        """Đăng nhập bằng WS token - KHÔNG tạo session, KHÔNG kick browser.
        
        Khi ``persist=False``, chỉ kiểm chứng thông tin đăng nhập và lấy token
        mà không ghi đè global ``settings`` hay gọi ``save_settings()``.
        """
        if persist:
            with self._get_lock("_session_lock", reentrant=True):
                self._portal_token = ""
                self._portal_token_username = ""
                self._cached_user_id = None
                self._cached_user_id_username = None

        credentials_are_explicit = username is not None or password is not None
        eff_user = (
            username
            if credentials_are_explicit
            else getattr(settings, "UTH_USERNAME", "")
        )
        eff_pwd = (
            password
            if credentials_are_explicit
            else getattr(settings, "UTH_PASSWORD", "")
        )
        if is_demo_account(eff_user):
            if not self.moodle_site_origin:
                self._last_login_error = "untrusted_site"
                return False
            if not _verify_demo_credentials_cached(eff_user, eff_pwd):
                self._last_login_error = "invalid_credentials"
                return False
            if persist:
                if credentials_are_explicit:
                    settings.UTH_USERNAME = normalize_demo_username(eff_user)
                    settings.UTH_PASSWORD = str(eff_pwd)
                    settings.UTH_CREDENTIALS_ORIGIN = self.moodle_site_origin
                settings.MOODLE_WS_TOKEN = ""
                settings.MOODLE_WS_TOKEN_ORIGIN = ""
                settings.MOODLE_WS_TOKEN_USERNAME = ""
            self._last_login_error = ""
            return True

        if force and persist:
            settings.MOODLE_WS_TOKEN = ""
            settings.MOODLE_WS_TOKEN_ORIGIN = ""
            settings.MOODLE_WS_TOKEN_USERNAME = ""
        token = self._get_ws_token(username, password, force=force, persist=persist)
        return bool(token)
    
    def call_ws_api(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle Web Services REST API.
        
        Args:
            function: Tên WS function (vd: core_calendar_get_action_events_by_timesort)
            **params: Tham số cho function
            
        Returns:
            JSON response dict, hoặc None nếu lỗi.
        """
        if self._is_demo_mode():
            if not self.moodle_site_origin:
                self._last_login_error = "untrusted_site"
                return None
            if not self._has_valid_demo_credentials():
                self._last_login_error = "invalid_credentials"
                return None
            result = handle_demo_ws_call(
                function,
                params,
                site_origin=self.moodle_site_origin,
            )
            if isinstance(result, dict) and "exception" in result:
                return None
            return result

        with self._get_lock("_session_lock", reentrant=True):
            start_gen = int(getattr(self, "_session_generation", 0))
            start_user_norm = self._clean_setting_str("UTH_USERNAME").casefold()

        token = self._get_ws_token()
        if not token:
            return None
        
        request_params = {
            'wstoken': token,
            'wsfunction': function,
            'moodlewsrestformat': 'json',
        }
        request_params.update(params)
        
        try:
            status, result = self._post(
                f"{self.moodle_site_origin}/webservice/rest/server.php",
                request_params,
                timeout=20
            )
            
            if result is None:
                logger.error(f"WS API [{function}] trả về response không phải JSON (status={status}).")
                return None
            
            # Kiểm tra xem token hết hạn hoặc không hợp lệ
            if isinstance(result, dict) and result.get('errorcode') in ('invalidtoken', 'accessexception'):
                logger.warning(f"WS token hết hạn hoặc không hợp lệ: {result.get('error', '')}")
                # Chỉ cho phép làm mới và gọi lại nếu phiên và tài khoản hiện hành không đổi
                with self._get_lock("_session_lock", reentrant=True):
                    cur_gen = int(getattr(self, "_session_generation", 0))
                    cur_user_raw = self._clean_setting_str("UTH_USERNAME")
                    cur_user_norm = cur_user_raw.casefold()
                    can_retry = (
                        cur_gen == start_gen
                        and bool(cur_user_norm)
                        and not is_demo_account(cur_user_raw)
                        and cur_user_norm == start_user_norm
                    )
                    if can_retry and getattr(settings, "MOODLE_WS_TOKEN", "") in ("", token):
                        settings.MOODLE_WS_TOKEN = ""
                        settings.MOODLE_WS_TOKEN_ORIGIN = ""
                        settings.MOODLE_WS_TOKEN_USERNAME = ""
                if not can_retry:
                    logger.info(
                        "Aborting WS API [%s] invalidtoken retry: session or active account changed.",
                        function,
                    )
                    return None
                token = self._get_ws_token(force=True)
                if token:
                    with self._get_lock("_session_lock", reentrant=True):
                        still_same_session = (
                            int(getattr(self, "_session_generation", 0)) == start_gen
                            and self._clean_setting_str("UTH_USERNAME").casefold()
                            == start_user_norm
                        )
                    if not still_same_session:
                        return None
                    request_params['wstoken'] = token
                    status, result = self._post(
                        f"{self.moodle_site_origin}/webservice/rest/server.php",
                        request_params,
                        timeout=20
                    )
                    if result is None:
                        logger.error(f"WS API [{function}] retry trả về response không phải JSON (status={status}).")
                        return None
                else:
                    return None
            
            # Kiểm tra các lỗi khác từ phía hệ thống
            if isinstance(result, dict) and 'exception' in result:
                errorcode = result.get('errorcode', '')
                message = result.get('message', result.get('error', 'Unknown'))
                # Các lỗi xác thực dữ liệu phía server (data validation) không thể thực hiện lại (non-retryable)
                if errorcode == 'invalidresponse':
                    logger.warning(f"WS API [{function}]: Server data validation (non-retryable): {message}")
                else:
                    logger.error(f"WS API error [{function}] ({errorcode}): {message}")
                return None
            
            return result
        except Exception as e:
            logger.error(f"Lỗi khi gọi WS API [{function}]: {e}")
            return None



    # Async Web Services API (Bất đồng bộ/non-blocking)

    async def call_ws_api_async(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle WS API bất đồng bộ.
        
        Dùng asyncio.to_thread() để chạy sync call_ws_api trong thread pool.
        Vẫn non-blocking từ event loop perspective.
        """
        import asyncio
        return await asyncio.to_thread(self.call_ws_api, function, **params)


    def get_user_id(self, *, refresh: bool = False) -> Optional[int]:
        """Lấy Moodle user ID từ core_webservice_get_site_info (cached theo tài khoản)."""
        if self._is_demo_mode():
            if not self.moodle_site_origin or not self._has_valid_demo_credentials():
                return None
            return DEMO_USER_ID
        active_user = str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
        with self._get_lock("_session_lock", reentrant=True):
            cached_id = getattr(self, "_cached_user_id", None)
            cached_owner = getattr(self, "_cached_user_id_username", None)
            gen = int(getattr(self, "_session_generation", 0))
            if not refresh and cached_id and (cached_owner is None or cached_owner == active_user):
                return cached_id
        try:
            result = self.call_ws_api('core_webservice_get_site_info')
            if result and 'userid' in result:
                uid = int(result['userid'])
                with self._get_lock("_session_lock", reentrant=True):
                    current_user = str(getattr(settings, "UTH_USERNAME", "") or "").strip().casefold()
                    if (
                        int(getattr(self, "_session_generation", 0)) == gen
                        and current_user == active_user
                    ):
                        self._cached_user_id = uid
                        self._cached_user_id_username = active_user
                return uid
        except Exception as e:
            logger.error(f"Lỗi khi lấy user ID: {e}")
        return None

    def upload_draft_file_result(
        self,
        filename: str,
        file_bytes: bytes,
        itemid: int = 0,
        filepath: str = "/",
    ) -> DraftUploadResult:
        """Upload a file and return its identity or a sanitized Moodle error code."""
        if self._is_demo_mode():
            return DraftUploadResult(error_code="demo_read_only")
        token = self._get_ws_token()
        if not token:
            logger.error("Không có WS token để upload file.")
            return DraftUploadResult(error_code="missingtoken")

        form_data = {
            'token': token,
            'itemid': str(itemid),
            'filearea': 'draft',
            'filepath': _normalize_draft_filepath(filepath),
        }

        try:
            status, result = self._post_multipart(
                f"{self.moodle_site_origin}/webservice/upload.php",
                fields=form_data,
                files={'file': (filename, file_bytes)},
                timeout=60.0,
            )
        except Exception as e:
            logger.error("Lỗi upload file '%s': %s", filename, e)
            return DraftUploadResult(error_code="transporterror")

        if not 200 <= status < 300:
            logger.warning("Draft upload failed with HTTP status %d", status)
            return DraftUploadResult(error_code="httpstatus")
        if isinstance(result, dict):
            return DraftUploadResult(
                error_code=_draft_upload_error_code(result) or "invalidresponse"
            )
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            logger.warning("Draft upload returned an unexpected response shape")
            return DraftUploadResult(error_code="invalidresponse")

        uploaded = result[0]
        error_code = _draft_upload_error_code(uploaded)
        if error_code:
            return DraftUploadResult(error_code=error_code)
        raw_itemid = uploaded.get("itemid")
        server_filename = uploaded.get("filename")
        server_filepath = uploaded.get("filepath")
        if isinstance(raw_itemid, bool) or not isinstance(server_filename, str) or not server_filename or not isinstance(server_filepath, str):
            logger.warning("Draft upload response did not include a valid file identity")
            return DraftUploadResult(error_code="invalidresponse")
        try:
            server_itemid = int(raw_itemid)
        except (TypeError, ValueError):
            logger.warning("Draft upload response did not include a valid item ID")
            return DraftUploadResult(error_code="invalidresponse")
        if server_itemid <= 0:
            logger.warning("Draft upload response contained a non-positive item ID")
            return DraftUploadResult(error_code="invalidresponse")
        return DraftUploadResult(
            record=DraftFileRecord(
                itemid=server_itemid,
                filepath=_normalize_draft_filepath(server_filepath),
                filename=server_filename,
            )
        )

    def upload_draft_file_record(
        self,
        filename: str,
        file_bytes: bytes,
        itemid: int = 0,
        filepath: str = "/",
    ) -> Optional[DraftFileRecord]:
        """Compatibility wrapper returning only the server-confirmed identity."""
        return self.upload_draft_file_result(
            filename, file_bytes, itemid, filepath
        ).record

    def upload_draft_file(self, filename: str, file_bytes: bytes,
                          itemid: int = 0,
                          author: str = None,
                          license_key: str = None) -> Optional[int]:
        """Compatibility wrapper returning only the Moodle draft item ID."""
        record = self.upload_draft_file_record(filename, file_bytes, itemid, "/")
        return record.itemid if record else None

    def _download_demo_stream(
        self,
        url: str,
        write_fn: Callable[[bytes], Any],
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        origin = self.moodle_site_origin
        if not _is_same_moodle_origin_url(url, origin):
            logger.warning("Blocked off-origin or unsafe demo download URL")
            return False
        if not self._has_valid_demo_credentials():
            return False
        payload = get_demo_file_bytes(url.strip())
        if payload is None:
            return False
        if max_bytes is not None and len(payload) > max_bytes:
            logger.warning("Demo download exceeded max_bytes (%d)", max_bytes)
            return False
        if payload:
            write_fn(payload)
        if callable(progress_callback):
            try:
                progress_callback(len(payload), len(payload))
            except Exception:
                pass
        return True

    def _download_ws_stream(
        self,
        url: str,
        write_fn: Callable[[bytes], Any],
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        if self._is_demo_mode():
            return self._download_demo_stream(
                url,
                write_fn,
                max_bytes=max_bytes,
                progress_callback=progress_callback,
            )
        origin = self.moodle_site_origin
        if not _is_same_moodle_origin_url(url, origin):
            logger.warning("Blocked off-origin or unsafe WS download URL")
            return False

        token = self._get_ws_token()
        if not token:
            return False

        parts = urllib.parse.urlsplit(url.strip())
        token_param = f"token={urllib.parse.quote(token, safe='')}"
        query = f"{parts.query}&{token_param}" if parts.query else token_param
        authed_url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, query, "")
        )

        opener = urllib.request.build_opener(_SameMoodleOriginRedirectHandler(origin))
        request = urllib.request.Request(
            authed_url,
            headers={"User-Agent": _DEFAULT_UA, "Accept-Encoding": "gzip"},
        )
        try:
            with opener.open(request, timeout=60) as response:
                final_url = response.geturl()
                if not _is_same_moodle_origin_url(final_url, origin):
                    logger.warning("Download response changed origin")
                    return False
                status = getattr(response, "status", 200)
                if status != 200:
                    logger.error("Download failed (HTTP %d): %s", status, url)
                    return False
                return _stream_bounded_response(
                    response,
                    write_fn,
                    max_bytes=max_bytes,
                    progress_callback=progress_callback,
                )
        except urllib.error.HTTPError as e:
            try:
                logger.error("Download failed (HTTP %d): %s", e.code, url)
            finally:
                e.close()
        except Exception as e:
            logger.error("Download error: %s", e)
        return False

    def download_file_to_path(
        self,
        url: str,
        dest_stream: Any,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """Stream a Moodle WS-authenticated file directly into ``dest_stream``."""
        return self._download_ws_stream(
            url,
            dest_stream.write,
            max_bytes=max_bytes,
            progress_callback=progress_callback,
        )

    def download_file(
        self,
        url: str,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        """Tải file từ Moodle server bằng WS token trên đúng origin tin cậy."""
        buf = bytearray()
        ok = self._download_ws_stream(
            url,
            buf.extend,
            max_bytes=max_bytes,
            progress_callback=progress_callback,
        )
        return bytes(buf) if ok else None

    def _download_session_stream(
        self,
        url: str,
        write_fn: Callable[[bytes], Any],
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        if self._is_demo_mode():
            return self._download_demo_stream(
                url,
                write_fn,
                max_bytes=max_bytes,
                progress_callback=progress_callback,
            )
        origin = self.moodle_site_origin
        if not _is_same_moodle_origin_url(url, origin):
            logger.warning("Blocked off-origin or unsafe session download URL")
            return False

        target_url = url.strip()
        try:
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                needs_login = not self._sesskey
            if needs_login and not self.login_session():
                return False
            with self._get_lock("_session_lock", reentrant=True):
                self._reset_session_if_account_changed()
                if not self._sesskey:
                    return False
                opener = self._ensure_html_opener()
                gen = int(getattr(self, "_session_generation", 0))

            for attempt in range(2):
                request = urllib.request.Request(
                    target_url,
                    headers={"User-Agent": _DEFAULT_UA, "Accept-Encoding": "gzip"},
                )
                with opener.open(request, timeout=60) as response:
                    final_url = response.geturl()
                    if not _is_same_moodle_origin_url(final_url, origin):
                        logger.warning("Session download response changed origin")
                        return False
                    if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                        with self._get_lock("_session_lock", reentrant=True):
                            same_gen = int(getattr(self, "_session_generation", 0)) == gen
                            if same_gen:
                                self._session_authenticated_at = 0.0
                        if attempt == 0 and same_gen:
                            if not self.login_session(force=True):
                                return False
                            with self._get_lock("_session_lock", reentrant=True):
                                self._reset_session_if_account_changed()
                                if not self._sesskey:
                                    return False
                                opener = self._ensure_html_opener()
                                gen = int(getattr(self, "_session_generation", 0))
                            continue
                        return False
                    status = getattr(response, "status", 200)
                    if status != 200:
                        logger.error("Session download failed (HTTP %d): %s", status, url)
                        return False
                    ok = _stream_bounded_response(
                        response,
                        write_fn,
                        max_bytes=max_bytes,
                        progress_callback=progress_callback,
                    )
                    if not ok:
                        return False
                    with self._get_lock("_session_lock", reentrant=True):
                        if int(getattr(self, "_session_generation", 0)) != gen:
                            return False
                        if self._sesskey and not urllib.parse.urlsplit(
                            final_url
                        ).path.endswith("/login/index.php"):
                            self._session_authenticated_at = time.monotonic()
                    return True
        except urllib.error.HTTPError as e:
            try:
                logger.error("Session download failed (HTTP %d): %s", e.code, url)
            finally:
                e.close()
        except Exception as e:
            logger.error("Session download error: %s", e)
        return False

    def download_session_file_to_path(
        self,
        url: str,
        dest_stream: Any,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> bool:
        """Stream a cookie-session-authenticated file directly into ``dest_stream``."""
        return self._download_session_stream(
            url,
            dest_stream.write,
            max_bytes=max_bytes,
            progress_callback=progress_callback,
        )

    def download_session_file(
        self,
        url: str,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        """Download a same-origin Moodle file authenticated by cookie session."""
        buf = bytearray()
        ok = self._download_session_stream(
            url,
            buf.extend,
            max_bytes=max_bytes,
            progress_callback=progress_callback,
        )
        return bytes(buf) if ok else None

    def remove_assignment_submission(
        self,
        cmid: int,
        *,
        expected_user_id: int,
        before_commit: Callable[[], bool] | None = None,
    ) -> bool:
        """Remove all submission-plugin data through Moodle's confirmed web action."""
        if self._is_demo_mode():
            return False
        if (
            isinstance(cmid, bool)
            or not isinstance(cmid, int)
            or cmid <= 0
            or isinstance(expected_user_id, bool)
            or not isinstance(expected_user_id, int)
            or expected_user_id <= 0
            or not self._stored_credentials_match_site()
        ):
            return False

        origin = self.moodle_site_origin
        username = settings.UTH_USERNAME
        password = settings.UTH_PASSWORD
        if not origin or not username or not password:
            return False

        try:
            session_key = quiz_session_key(self._moodle_site, username)
        except ValueError:
            return False

        with self._get_lock("_session_lock", reentrant=True):
            self._reset_session_if_account_changed(username)
            start_gen = int(getattr(self, "_session_generation", 0))

        if not _begin_quiz_session_login(session_key):
            return False

        isolated_jar = CookieJar()
        isolated_opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(isolated_jar),
            _SameMoodleOriginRedirectHandler(origin),
        )
        meta = self._get_open_session_meta_local()
        prev_tls_opener = getattr(meta, "html_opener", None)
        meta.html_opener = isolated_opener
        try:
            _, login_page = self._open_html("/login/index.php")
            parsed_login = _MoodleHtmlFormParser()
            parsed_login.feed(login_page)
            login_token = parsed_login.inputs.get("logintoken", "")
            if not login_token:
                return False

            final_url, _ = self._open_html(
                "/login/index.php",
                data={
                    "anchor": "",
                    "logintoken": login_token,
                    "username": username,
                    "password": password,
                },
            )
            if urllib.parse.urlsplit(final_url).path == "/login/index.php":
                return False

            query = urllib.parse.urlencode(
                {"id": cmid, "action": "removesubmissionconfirm"}
            )
            _, confirmation_page = self._open_html(f"/mod/assign/view.php?{query}")
            parsed_confirmation = _MoodleHtmlFormParser()
            parsed_confirmation.feed(confirmation_page)
            remove_fields: dict[str, str] | None = None
            remove_action = ""
            for form in parsed_confirmation.forms:
                inputs = form.get("inputs")
                if (
                    form.get("method") == "post"
                    and isinstance(inputs, dict)
                    and inputs.get("id") == str(cmid)
                    and inputs.get("action") == "removesubmission"
                    and str(inputs.get("userid", "")).isdigit()
                    and str(inputs.get("sesskey", "")).isalnum()
                ):
                    remove_fields = {
                        key: str(inputs[key])
                        for key in ("id", "action", "userid", "sesskey")
                    }
                    remove_action = str(form.get("action", ""))
                    break
            if remove_fields is None:
                return False
            if int(remove_fields["userid"]) != expected_user_id:
                return False

            action_url = urllib.parse.urljoin(
                f"{origin}/mod/assign/view.php", remove_action
            )
            parsed_action = urllib.parse.urlsplit(action_url)
            if (
                f"{parsed_action.scheme.lower()}://{parsed_action.netloc.lower()}" != origin
                or parsed_action.path != "/mod/assign/view.php"
                or parsed_action.query
                or parsed_action.fragment
            ):
                return False
            with self._get_lock("_session_lock", reentrant=True):
                if (
                    int(getattr(self, "_session_generation", 0)) != start_gen
                    or settings.UTH_USERNAME != username
                ):
                    return False
            if before_commit is not None and not before_commit():
                return False
            self._open_html(action_url, data=remove_fields)
            return True
        except Exception as exc:
            logger.warning(
                "Moodle web submission removal failed (%s)", type(exc).__name__
            )
            return False
        finally:
            meta.html_opener = prev_tls_opener
            _end_quiz_session_login(session_key)

    def delete_draft_file(self, draftitemid: int, filepath: str, filename: str) -> bool:
        """Xóa một file cụ thể từ draft area."""
        if self._is_demo_mode():
            return False
        try:
            result = self.call_ws_api(
                'core_files_delete_draft_files',
                draftitemid=draftitemid,
                **{
                    'files[0][filepath]': filepath,
                    'files[0][filename]': filename,
                }
            )
        except Exception as e:
            logger.error("Lỗi xóa file '%s' từ draft %d: %s", filename, draftitemid, e)
            return False

        if isinstance(result, dict) and 'parentpaths' in result:
            logger.info("Đã xóa file '%s' từ draft %d", filename, draftitemid)
            return True
        
        if isinstance(result, dict) and 'exception' in result:
            logger.error("Xóa file lỗi: %s", result.get('message', ''))
        return False

    def get_portal_token(self, username: str = None, password: str = None) -> str:
        """Lấy JWT token từ Portal UTH (dùng cho autologin deep-link), ràng buộc theo username."""
        if moodle_site_from_origin(self.moodle_site_origin) is None:
            return ""
        portal_base = _validated_portal_api_base(
            getattr(settings, "PORTAL_API_BASE", "")
        )
        if not portal_base:
            return ""
        user = username if username is not None else settings.UTH_USERNAME
        if is_demo_account(user):
            return ""
        pwd = password if password is not None else settings.UTH_PASSWORD
        owner = str(user or "").strip().casefold()
        if not owner or not pwd:
            return ""
        with self._get_lock("_session_lock", reentrant=True):
            cached_token = getattr(self, "_portal_token", "")
            cached_owner = getattr(self, "_portal_token_username", "")
            gen = int(getattr(self, "_session_generation", 0))
            if cached_token and cached_owner == owner:
                return cached_token
        try:
            status, data = self._post_json(
                f"{portal_base}/user/login",
                {"username": user, "password": pwd},
                timeout=10,
            )
            if data and data.get("success") and data.get("token"):
                token = str(data["token"])
                with self._get_lock("_session_lock", reentrant=True):
                    if int(getattr(self, "_session_generation", 0)) == gen:
                        self._portal_token = token
                        self._portal_token_username = owner
                logger.info("Lấy Portal JWT token thành công.")
                return token
            elif data:
                logger.warning(f"Portal login không thành công: {data.get('message')}")
        except Exception as e:
            logger.error(f"Lỗi khi lấy Portal token: {e}")
        return ""

    def build_autologin_url(self, activity_url: str = "", username: str = None, password: str = None) -> str:
        """Tạo URL autologin dẫn thẳng tới activity/course."""
        configured_site = moodle_site_from_origin(
            getattr(settings, "MOODLE_BASE_URL", "")
        )
        client_site = moodle_site_from_origin(self.moodle_site_origin)
        if configured_site is None or client_site is None or configured_site != client_site:
            return ""
        if activity_url is not None and not isinstance(activity_url, str):
            return ""
        clean_activity_url = activity_url or ""
        if clean_activity_url:
            if (
                clean_activity_url != clean_activity_url.strip()
                or "\\" in clean_activity_url
                or any(
                    ch.isspace() or ord(ch) < 32 or ord(ch) == 127
                    for ch in clean_activity_url
                )
                or moodle_site_from_url(clean_activity_url) != configured_site
            ):
                return ""
        token = self.get_portal_token(username, password)
        if not token:
            return clean_activity_url
        safe_token = urllib.parse.quote(token, safe="")
        if clean_activity_url:
            return (
                f"{configured_site.origin}/login/index.php"
                f"?token={safe_token}"
                f"&wantsurl={urllib.parse.quote(clean_activity_url, safe='')}"
            )
        return f"{configured_site.origin}/login/index.php?token={safe_token}"

    @property
    def last_login_error(self) -> str:
        return getattr(self, '_last_login_error', '')
