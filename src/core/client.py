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
from typing import Any, Callable, Optional
from config import settings
from core.moodle_sites import (
    COURSES_MOODLE_SITE,
    MoodleSite,
    moodle_site_from_origin,
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


class _SameMoodleOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: str):
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
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


def _read_bounded_response_bytes(
    response,
    *,
    max_bytes: Optional[int] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[bytes]:
    """Read HTTP response bytes in 64 KB chunks with size bound, gzip support, and progress callback."""
    info = response.info() if hasattr(response, "info") else None
    total_len = 0
    if info is not None:
        try:
            raw_cl = info.get("Content-Length") if hasattr(info, "get") else None
            total_len = max(0, int(raw_cl)) if raw_cl else 0
        except (TypeError, ValueError):
            total_len = 0

    # Stream in 64 KB chunks on real HTTPResponse / BufferedIOBase; read once on non-advancing test fakes.
    is_real_stream = isinstance(response, (http.client.HTTPResponse, io.IOBase)) or hasattr(
        getattr(response, "fp", None), "read"
    )
    if is_real_stream:
        buf = bytearray()
        chunk_size = 65536
        last_emit = 0.0
        while True:
            to_read = (
                min(chunk_size, (max_bytes + 1) - len(buf))
                if max_bytes is not None
                else chunk_size
            )
            if to_read <= 0:
                extra = response.read(1)
                if extra:
                    buf.extend(extra)
                break
            chunk = response.read(to_read)
            if not chunk:
                break
            buf.extend(chunk)
            if max_bytes is not None and len(buf) > max_bytes:
                logger.warning("Download exceeded max_bytes (%d)", max_bytes)
                return None
            if callable(progress_callback):
                now_m = time.monotonic()
                if (now_m - last_emit) >= 0.065:
                    last_emit = now_m
                    try:
                        progress_callback(len(buf), total_len)
                    except Exception:
                        pass
        body = bytes(buf)
        if callable(progress_callback):
            try:
                progress_callback(len(body), total_len or len(body))
            except Exception:
                pass
    else:
        body = response.read(max_bytes + 1) if max_bytes is not None else response.read()
        if max_bytes is not None and len(body) > max_bytes:
            logger.warning("Download exceeded max_bytes (%d)", max_bytes)
            return None
        if callable(progress_callback):
            try:
                progress_callback(len(body), total_len or len(body))
            except Exception:
                pass

    encoding = str(info.get("Content-Encoding") or "").lower() if info and hasattr(info, "get") else ""
    if encoding == "gzip":
        if max_bytes is None:
            return gzip.decompress(body)
        decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decompressed = decompressor.decompress(body, max_bytes + 1)
        if len(decompressed) > max_bytes or decompressor.unconsumed_tail:
            logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
            return None
        tail = decompressor.flush()
        if len(decompressed) + len(tail) > max_bytes:
            logger.warning("Gzip download exceeded max_bytes (%d)", max_bytes)
            return None
        return decompressed + tail
    return body


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


def _urlopen(req, timeout=15):
    """Thin wrapper around urllib.request.urlopen for uniform timeout & test monkeypatching."""
    return urllib.request.urlopen(req, timeout=timeout)


class MoodleClient:
    """HTTP client dùng urllib.request (stdlib) - zero dependency, mọi platform.
    
    Cloudflare KHÔNG chặn urllib (đã test).
    Chạy trên Desktop, iOS, Android mà không cần cài thêm gì.
    """
    
    # Giới hạn tần suất gọi API ở phía Client (Moodle không tự động bóp băng thông từ server-side)
    _MIN_INTERVAL = 0.05  # 50ms = tối đa 20 req/s (trước đây là 200ms - gây lãng phí 1.6s cho mỗi chu kỳ quét)

    def __init__(self, base_url: str | None = None):
        self._moodle_site: MoodleSite | None = moodle_site_from_origin(
            base_url if base_url is not None else settings.MOODLE_BASE_URL
        )
        self._last_login_error = ""
        self._portal_token: str = ""   # JWT lấy từ portal API - thời hạn khoảng 30 ngày
        self._last_call_time: float = 0.0  # Nhãn thời gian đơn điệu (monotonic)
        self._cookie_jar = CookieJar()
        self._sesskey = ""
        self._session_owner = ""
        self._html_opener = None
        self._session_lock = threading.RLock()
        self.last_grade_report_error = ""

    def clear_session(self) -> None:
        """Clear the HTML session cookies, sesskey, and owning username."""
        with self._session_lock:
            self._cookie_jar.clear()
            self._sesskey = ""
            self._session_owner = ""

    def _reset_session_if_account_changed(self, username: str | None = None) -> None:
        """Drop stale HTML session cookies/sesskey when the active account changes."""
        active_user = str(
            username if username is not None else getattr(settings, "UTH_USERNAME", "") or ""
        )
        with self._session_lock:
            if self._session_owner and active_user != self._session_owner:
                self._cookie_jar.clear()
                self._sesskey = ""
                self._session_owner = ""

    @property
    def moodle_site_origin(self) -> str:
        """The immutable trusted origin this client was configured to use."""
        return self._moodle_site.origin if self._moodle_site else ""

    @property
    def has_site_credentials(self) -> bool:
        """Whether this client can authenticate only to its configured site."""
        return bool(
            (
                settings.MOODLE_WS_TOKEN
                and settings.MOODLE_WS_TOKEN_ORIGIN == self.moodle_site_origin
            )
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

    def _throttle(self):
        """Ensure minimum interval between API calls."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)
        self._last_call_time = time.monotonic()

    def _ensure_html_opener(self):
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
        timeout: int = 20,
    ) -> tuple[str, str]:
        opener = self._ensure_html_opener()
        origin = self.moodle_site_origin
        url = urllib.parse.urljoin(f"{origin}/", path.lstrip("/"))
        encoded = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(
            url,
            data=encoded,
            method="POST" if data is not None else "GET",
            headers={"User-Agent": _DEFAULT_UA, "Accept": "text/html"},
        )
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            parsed = urllib.parse.urlsplit(final_url)
            final_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
            if final_origin != origin:
                raise ValueError("Moodle web response changed origin")
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError("Moodle web response was too large")
        return final_url, body.decode("utf-8", errors="replace")

    def get_sesskey(self) -> str:
        """Return current authenticated Moodle session ``sesskey`` if available."""
        with self._session_lock:
            self._reset_session_if_account_changed()
            return self._sesskey or ""

    def get_session_cookies(self, *, ensure_login: bool = True) -> list[dict[str, Any]]:
        """Return snapshot cookie dicts from this client's cookie jar for WebView injection."""
        with self._session_lock:
            self._reset_session_if_account_changed()
            if ensure_login and not self._sesskey:
                try:
                    self.login_session()
                except Exception as exc:
                    logger.warning("Session login before cookie export failed (%s)", type(exc).__name__)
                    return []
            cookies: list[dict[str, Any]] = []
            for cookie in list(self._cookie_jar):
                if not cookie.name or cookie.value is None:
                    continue
                cookies.append(
                    {
                        "name": cookie.name,
                        "value": cookie.value,
                        "domain": cookie.domain or "",
                        "path": cookie.path or "/",
                        "secure": bool(cookie.secure),
                        "expires": cookie.expires,
                        "httpOnly": bool(getattr(cookie, "_rest", {}).get("HttpOnly", False)),
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
        with self._session_lock:
            self._reset_session_if_account_changed()
            if not self._sesskey and not self.login_session():
                raise RuntimeError("Moodle session login failed")

            post_fields = {str(k): str(v) for k, v in (fields or {}).items()}
            for attempt in range(2):
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
                if urllib.parse.urlsplit(final_url).path == "/login/index.php":
                    if attempt == 0:
                        if not self.login_session(force=True):
                            raise RuntimeError("Moodle session re-login failed")
                        continue
                    raise RuntimeError("Moodle session expired")
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
        with self._session_lock:
            self._reset_session_if_account_changed()
            if not self._sesskey and not path.startswith("/login/index.php"):
                self.login_session()

            req_method = (method or "GET").upper()
            opener = self._ensure_html_opener()
            target_url = urllib.parse.urljoin(f"{origin}/", path.lstrip("/"))
            headers: dict[str, str] = {"User-Agent": _DEFAULT_UA, "Accept": accept}
            if body is not None and content_type:
                headers["Content-Type"] = content_type

            for attempt in range(2):
                request = urllib.request.Request(
                    target_url,
                    data=body if req_method == "POST" else None,
                    method=req_method,
                    headers=headers,
                )
                try:
                    with opener.open(request, timeout=timeout) as response:
                        status = int(getattr(response, "status", 200) or 200)
                        final_url = response.geturl()
                        resp_ct = str(
                            response.headers.get("Content-Type", "application/octet-stream")
                        )
                        raw = response.read(max_bytes + 1)
                except urllib.error.HTTPError as exc:
                    try:
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
                        err_body = exc.read(min(max_bytes, 512 * 1024))
                        return int(exc.code), str(exc.geturl() or target_url), err_ct, err_body
                    finally:
                        exc.close()

                parsed = urllib.parse.urlsplit(final_url)
                final_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
                if final_origin != origin:
                    raise ValueError("Moodle response changed origin")
                if (
                    parsed.path == "/login/index.php"
                    and not path.startswith("/login/index.php")
                    and attempt == 0
                    and self.login_session(force=True)
                ):
                    continue
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]
                return status, final_url, resp_ct, raw

            raise RuntimeError("Moodle session expired")

    def open_session_url(self, url: str) -> tuple[str, str, bool]:
        """Open a same-origin Moodle web page using cookie session, recovering once on expiry."""
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
            path = f"{parsed.path}?{parsed.query}" if parsed.query else (parsed.path or "/")
        else:
            resolved = urllib.parse.urljoin(f"{origin}/", raw.lstrip("/"))
            rp = urllib.parse.urlsplit(resolved)
            path = f"{rp.path}?{rp.query}" if rp.query else (rp.path or "/")

        with self._session_lock:
            self._reset_session_if_account_changed()
            if not self._sesskey and not self.login_session():
                raise RuntimeError("Moodle session login failed")

            for attempt in range(2):
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
                if urllib.parse.urlsplit(final_url).path == "/login/index.php":
                    if attempt == 0:
                        if not self.login_session(force=True):
                            raise RuntimeError("Moodle session re-login failed")
                        continue
                    raise RuntimeError("Moodle session expired")
                return final_url, html_text, False
            raise RuntimeError("Moodle session expired")

    def login_session(
        self,
        username: str = None,
        password: str = None,
        force: bool = False,
        *,
        quiz_lease: QuizSessionLease | None = None,
    ) -> bool:
        """Đăng nhập bằng form HTML để lấy MoodleSession và sesskey."""
        if not username:
            username = settings.UTH_USERNAME
        if not password:
            password = settings.UTH_PASSWORD

        self._reset_session_if_account_changed(username)

        if not force and self._sesskey and quiz_lease is None:
            return True

        if not self.moodle_site_origin or not username or not password:
            self._last_login_error = "invalid_credentials"
            return False

        try:
            session_key = quiz_session_key(self._moodle_site, username)
        except ValueError:
            self._last_login_error = "invalid_credentials"
            return False
        if quiz_lease is not None and (
            quiz_lease.session_key != session_key or not quiz_lease.is_active
        ):
            self._last_login_error = "quiz_session_lease_invalid"
            return False

        if not _begin_quiz_session_login(session_key, lease=quiz_lease):
            # Leave the existing cookie jar and sesskey intact while a quiz
            # player holds this account/site session.
            self._last_login_error = "quiz_session_active"
            return False

        try:
            self._cookie_jar.clear()
            self._sesskey = ""
            self._session_owner = ""

            _, login_page = self._open_html("/login/index.php")
            parsed_login = _MoodleHtmlFormParser()
            parsed_login.feed(login_page)
            login_token = parsed_login.inputs.get("logintoken", "")
            if not login_token:
                logger.error("Không tìm thấy logintoken trên trang đăng nhập.")
                self._last_login_error = "server_error"
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
                logger.warning("Đăng nhập session thất bại (sai tài khoản hoặc lỗi Moodle).")
                self._last_login_error = "invalid_credentials"
                return False

            # Lấy trang Dashboard để trích xuất sesskey
            _, dashboard_page = self._open_html("/my/")
            
            # sesskey thường nằm trong thẻ JS: "sesskey":"123xyz"
            import re
            match = re.search(r'"sesskey":"([a-zA-Z0-9]+)"', dashboard_page)
            if match:
                self._sesskey = match.group(1)
                self._session_owner = str(username)
                return True
            
            # Fallback 1: tìm biến sesskey=...
            match2 = re.search(r'sesskey=([a-zA-Z0-9]+)', dashboard_page)
            if match2:
                self._sesskey = match2.group(1)
                self._session_owner = str(username)
                return True

            logger.error("Không trích xuất được sesskey sau khi login.")
            self._last_login_error = "server_error"
            return False

        except Exception as e:
            if quiz_lease is not None:
                logger.warning(
                    "Quiz HTML session login failed (%s)", type(e).__name__
                )
            else:
                logger.error(f"Lỗi login_session: {e}")
            self._last_login_error = "network_error"
            return False
        finally:
            _end_quiz_session_login(session_key)

    def call_ajax_api(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle AJAX service. Trả về None nếu lỗi hoặc HTTP fail."""
        with self._session_lock:
            self._reset_session_if_account_changed()
            if not self._sesskey:
                if not self.login_session():
                    return None
            sesskey = self._sesskey

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
        
        opener = self._ensure_html_opener()
        try:
            with opener.open(request, timeout=20) as response:
                body = response.read()
                try:
                    data = json.loads(body)
                except ValueError:
                    logger.error("AJAX response không phải JSON.")
                    return None
                
                if isinstance(data, list) and len(data) > 0:
                    resp = data[0]
                    if resp.get("error"):
                        logger.error(f"AJAX API error: {resp.get('exception')} - {resp.get('message')}")
                        # Xóa sesskey nếu lỗi liên quan tới session
                        if "requireloginerror" in resp.get("errorcode", "") or "sesskey" in resp.get("message", "").lower():
                            self._sesskey = ""
                    return resp
                return None
        except Exception as e:
            logger.error(f"Lỗi call_ajax_api: {e}")
            return None


    def fetch_grade_report_html(self, course_id: int) -> Optional[str]:
        """Tải HTML /grade/report/user/index.php?id=<course_id> bằng session đăng nhập.

        Trả None nếu lỗi; lý do nằm ở ``last_grade_report_error``.
        """
        path = f"/grade/report/user/index.php?id={int(course_id)}"
        self.last_grade_report_error = ""
        for attempt in range(2):
            with self._session_lock:  # tránh nhiều luồng cùng đăng nhập/xóa cookie
                self._reset_session_if_account_changed()
                if not self._sesskey and not self.login_session():
                    self.last_grade_report_error = (
                        f"login_session:{self._last_login_error or 'failed'}"
                    )
                    logger.warning("Grade report: đăng nhập session thất bại (%s)",
                                   self._last_login_error)
                    return None
                try:
                    final_url, body = self._open_html(path)
                except Exception as exc:
                    self.last_grade_report_error = f"http:{type(exc).__name__}:{exc}"
                    logger.warning("Không tải được grade report môn %s: %s", course_id, exc)
                    return None
                if urllib.parse.urlsplit(final_url).path.endswith("/login/index.php"):
                    # Session hết hạn -> đăng nhập lại một lần
                    self._sesskey = ""
                    if attempt == 0 and self.login_session(force=True):
                        continue
                    self.last_grade_report_error = "session_expired"
                    return None
                return body
        return None

    def close(self):
        """Không cần cleanup - urllib không dùng connection pool."""
        pass

    # Low-level HTTP helpers

    def _post(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST form-encoded data. Returns (status, parsed_json_or_None)."""
        import gzip
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
            try:
                resp = _urlopen(req, timeout=timeout)
                body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                try:
                    return resp.status, json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    logger.warning(
                        "POST %s trả về HTTP %d. Đang thử lại sau %.2fs...",
                        url.split('?')[0], e.code, backoff
                    )
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d từ POST %s", e.code, url.split('?')[0])
                return e.code, None
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

    def _post_json(self, url: str, data: dict, timeout: float = 15) -> tuple:
        """POST JSON body. Returns (status, parsed_json_or_None)."""
        import gzip
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
            try:
                resp = _urlopen(req, timeout=timeout)
                resp_body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    resp_body = gzip.decompress(resp_body)
                try:
                    return resp.status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-JSON %s", e.code, url.split('?')[0])
                return e.code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(15.0, 0.5 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-JSON %s failed: %s", url.split('?')[0], e)
                    raise e

    def _post_multipart(self, url: str, fields: dict, files: dict, timeout: float = 60) -> tuple:
        """POST multipart/form-data (cho upload file). Returns (status, parsed_json_or_None)."""
        import uuid
        import gzip
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
            try:
                resp = _urlopen(req, timeout=timeout)
                resp_body = resp.read()
                if resp.info().get('Content-Encoding') == 'gzip':
                    resp_body = gzip.decompress(resp_body)
                try:
                    return resp.status, json.loads(resp_body)
                except (json.JSONDecodeError, ValueError):
                    return resp.status, None
            except urllib.error.HTTPError as e:
                if e.code >= 500 and attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                logger.warning("HTTP %d from POST-MP %s", e.code, url.split('?')[0])
                return e.code, None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_attempts - 1:
                    backoff = random.uniform(0, min(10.0, 1.0 * (2 ** attempt)))
                    time.sleep(backoff)
                    continue
                else:
                    logger.error("POST-MP %s failed: %s", url.split('?')[0], e)
                    raise e

    def _get(self, url: str, timeout: float = 15) -> tuple:
        """GET request. Returns (status, raw_bytes)."""
        import gzip
        req = urllib.request.Request(url)
        req.add_header('User-Agent', _DEFAULT_UA)
        req.add_header('Accept-Encoding', 'gzip')
        
        try:
            resp = _urlopen(req, timeout=timeout)
            body = resp.read()
            if resp.info().get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            return resp.status, body
        except urllib.error.HTTPError as e:
            logger.warning("HTTP %d from GET %s", e.code, url.split('?')[0])
            return e.code, None


    # Web Services API (Dựa trên Token, không lưu trạng thái/stateless)

    def _get_ws_token(self, username: str = None, password: str = None, force: bool = False) -> str:
        """Lấy Web Services token từ Moodle.
        
        Token này stateless, không ảnh hưởng browser session.
        Valid rất lâu (~30 ngày), cache trong settings.
        """
        if not self.moodle_site_origin:
            logger.warning("Moodle site is not explicitly trusted or configured.")
            self._last_login_error = "untrusted_site"
            return ""

        # Reuse only a token whose issuing Moodle origin is recorded exactly.
        if (
            not force
            and settings.MOODLE_WS_TOKEN
            and settings.MOODLE_WS_TOKEN_ORIGIN == self.moodle_site_origin
        ):
            return settings.MOODLE_WS_TOKEN
        
        credentials_are_explicit = username is not None or password is not None
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
        
        try:
            status, data = self._post(
                f"{self.moodle_site_origin}/login/token.php",
                {'username': user, 'password': pwd, 'service': 'moodle_mobile_app'},
                timeout=15
            )
            
            if data and 'token' in data:
                if credentials_are_explicit:
                    settings.UTH_USERNAME = user
                    settings.UTH_PASSWORD = pwd
                settings.UTH_CREDENTIALS_ORIGIN = self.moodle_site_origin
                settings.MOODLE_WS_TOKEN = data['token']
                settings.MOODLE_WS_TOKEN_ORIGIN = self.moodle_site_origin
                from config import save_settings
                save_settings()
                logger.info("Lấy WS API token thành công.")
                self._last_login_error = ""
                return data['token']
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
    
    def login(self, username: str = None, password: str = None, force: bool = False) -> bool:
        """Đăng nhập bằng WS token - KHÔNG tạo session, KHÔNG kick browser.
        
        Đây là phương thức login duy nhất. Sử dụng Moodle WS API,
        hoạt động trên mọi nền tảng (iOS, Android, Desktop).
        """
        if force:
            settings.MOODLE_WS_TOKEN = ""
            settings.MOODLE_WS_TOKEN_ORIGIN = ""
        token = self._get_ws_token(username, password, force=force)
        return bool(token)
    
    def call_ws_api(self, function: str, **params) -> Optional[dict]:
        """Gọi Moodle Web Services REST API.
        
        Args:
            function: Tên WS function (vd: core_calendar_get_action_events_by_timesort)
            **params: Tham số cho function
            
        Returns:
            JSON response dict, hoặc None nếu lỗi.
        """
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
                # Token đã hết hạn → bắt buộc làm mới (force refresh)
                settings.MOODLE_WS_TOKEN = ""
                settings.MOODLE_WS_TOKEN_ORIGIN = ""
                token = self._get_ws_token(force=True)
                if token:
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
        """Lấy Moodle user ID từ core_webservice_get_site_info (cached).
        
        Cần cho core_files_upload (instanceid) và các API khác cần userid.
        """
        if not refresh and hasattr(self, '_cached_user_id') and self._cached_user_id:
            return self._cached_user_id
        try:
            result = self.call_ws_api('core_webservice_get_site_info')
            if result and 'userid' in result:
                self._cached_user_id = int(result['userid'])
                return self._cached_user_id
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

    def download_file(
        self,
        url: str,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        """Tải file từ Moodle server bằng WS token trên đúng origin tin cậy."""
        origin = self.moodle_site_origin
        if not _is_same_moodle_origin_url(url, origin):
            logger.warning("Blocked off-origin or unsafe WS download URL")
            return None

        token = self._get_ws_token()
        if not token:
            return None

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
            self._throttle()
            with opener.open(request, timeout=60) as response:
                final_url = response.geturl()
                if not _is_same_moodle_origin_url(final_url, origin):
                    logger.warning("Download response changed origin")
                    return None
                status = getattr(response, "status", 200)
                if status != 200:
                    logger.error("Download failed (HTTP %d): %s", status, url)
                    return None
                return _read_bounded_response_bytes(
                    response,
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
        return None

    def download_session_file(
        self,
        url: str,
        *,
        max_bytes: int = 100 * 1024 * 1024,
        progress_callback: Optional[Callable[[int, int], None]] = None,
    ) -> Optional[bytes]:
        """Download a same-origin Moodle file authenticated by cookie session."""
        origin = self.moodle_site_origin
        if not _is_same_moodle_origin_url(url, origin):
            logger.warning("Blocked off-origin or unsafe session download URL")
            return None

        target_url = url.strip()
        try:
            with self._session_lock:
                self._reset_session_if_account_changed()
                if not self._sesskey and not self.login_session():
                    return None

                for attempt in range(2):
                    opener = self._ensure_html_opener()
                    request = urllib.request.Request(
                        target_url,
                        headers={"User-Agent": _DEFAULT_UA, "Accept-Encoding": "gzip"},
                    )
                    self._throttle()
                    with opener.open(request, timeout=60) as response:
                        final_url = response.geturl()
                        if not _is_same_moodle_origin_url(final_url, origin):
                            logger.warning("Session download response changed origin")
                            return None
                        if urllib.parse.urlsplit(final_url).path == "/login/index.php":
                            if attempt == 0 and self.login_session(force=True):
                                continue
                            return None
                        status = getattr(response, "status", 200)
                        if status != 200:
                            logger.error("Session download failed (HTTP %d): %s", status, url)
                            return None
                        return _read_bounded_response_bytes(
                            response,
                            max_bytes=max_bytes,
                            progress_callback=progress_callback,
                        )
        except urllib.error.HTTPError as e:
            try:
                logger.error("Session download failed (HTTP %d): %s", e.code, url)
            finally:
                e.close()
        except Exception as e:
            logger.error("Session download error: %s", e)
        return None

    def remove_assignment_submission(
        self,
        cmid: int,
        *,
        expected_user_id: int,
        before_commit: Callable[[], bool] | None = None,
    ) -> bool:
        """Remove all submission-plugin data through Moodle's confirmed web action."""
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
            if before_commit is not None and not before_commit():
                return False
            self._open_html(action_url, data=remove_fields)
            return True
        except Exception as exc:
            logger.warning(
                "Moodle web submission removal failed (%s)", type(exc).__name__
            )
            return False

    def delete_draft_file(self, draftitemid: int, filepath: str, filename: str) -> bool:
        """Xóa một file cụ thể từ draft area."""
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
        """Lấy JWT token từ Portal UTH (dùng cho autologin deep-link)."""
        if self._portal_token:
            return self._portal_token
        user = username or settings.UTH_USERNAME
        pwd  = password or settings.UTH_PASSWORD
        try:
            status, data = self._post_json(
                f"{settings.PORTAL_API_BASE}/user/login",
                {"username": user, "password": pwd},
                timeout=10,
            )
            if data and data.get("success") and data.get("token"):
                self._portal_token = data["token"]
                logger.info("Lấy Portal JWT token thành công.")
            elif data:
                logger.warning(f"Portal login không thành công: {data.get('message')}")
        except Exception as e:
            logger.error(f"Lỗi khi lấy Portal token: {e}")
        return self._portal_token

    def build_autologin_url(self, activity_url: str = "", username: str = None, password: str = None) -> str:
        """Tạo URL autologin dẫn thẳng tới activity/course."""
        token = self.get_portal_token(username, password)
        if not token:
            return activity_url
        if activity_url:
            autologin = (
                f"{settings.MOODLE_BASE_URL}/login/index.php"
                f"?token={token}"
                f"&wantsurl={urllib.parse.quote(activity_url, safe='')}"
            )
            return autologin
        return f"{settings.MOODLE_BASE_URL}/login/index.php?token={token}"

    @property
    def last_login_error(self) -> str:
        return getattr(self, '_last_login_error', '')
