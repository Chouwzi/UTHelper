"""Pure-stdlib UTH scheduled maintenance (`01h00 - 04h00`) and Moodle `sitemaintenance` detector."""

from __future__ import annotations

import html
import json
import re
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

MaintenanceSource = Literal["courses", "thnn", "portal", "uth"]
_VALID_SOURCES: frozenset[str] = frozenset({"courses", "thnn", "portal", "uth"})

_DASH_CHARS_RE = re.compile(r"[\u2010-\u2015\u2212]")
_HORIZ_SPACE_RE = re.compile(r"[ \t\xa0]+")
_HOUR_RANGE_DASH_RE = re.compile(r"(\d{1,2}h\d{0,2})\s*-\s*(\d{1,2}h\d{0,2})", re.IGNORECASE)
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_OPEN_AT_ID_RE = re.compile(
    r"<[^>]*\bid\s*=\s*['\"]openAt['\"][^>]*>(.*?)</[a-zA-Z0-9]+>",
    re.IGNORECASE | re.DOTALL,
)
_WINDOW_LINE_RE = re.compile(
    r"Thời\s+gian\s+tạm\s+ngưng\s*:\s*([^\r\n]+)",
    re.IGNORECASE,
)
_INLINE_WINDOW_RE = re.compile(
    r"bảo\s+trì\s*\(([^()\r\n]{3,64})\)",
    re.IGNORECASE,
)
_REOPEN_LINE_RE = re.compile(
    r"Hệ\s+thống\s+mở\s+lại\s+lúc\s*:\s*([^\r\n]+)",
    re.IGNORECASE,
)
_OPEN_HOUR_SCRIPT_RE = re.compile(r"\bOPEN_HOUR\s*=\s*(\d{1,2})\b")
_OPEN_MINUTE_SCRIPT_RE = re.compile(r"\bOPEN_MINUTE\s*=\s*(\d{1,2})\b")
_TZ_OFFSET_SCRIPT_RE = re.compile(r"\bTZ_OFFSET\s*=\s*([+-]?\d{1,2})\b")
_CLOCK_HM_RE = re.compile(r"^(\d{1,2})h(\d{2})?$", re.IGNORECASE)
_DATE_DMY_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_PAREN_NOTE_RE = re.compile(r"\([^)]*\)")

_CLOUDFLARE_CHALLENGE_MARKERS: tuple[str, ...] = (
    "_cf_chl_opt",
    "cf-browser-verification",
    "cdn-cgi/challenge-platform",
    "just a moment...",
    "attention required! | cloudflare",
)


def _normalize_source(raw: str) -> MaintenanceSource:
    cleaned = str(raw or "").strip().lower()
    if cleaned == "courses":
        return "courses"
    if cleaned == "thnn":
        return "thnn"
    if cleaned == "portal":
        return "portal"
    return "uth"


def normalize_maintenance_text(text: str) -> str:
    """Normalize Unicode dashes and collapse horizontal whitespace to clean plain text."""
    cleaned = html.unescape(str(text or ""))
    cleaned = _DASH_CHARS_RE.sub("-", cleaned)
    cleaned = _HORIZ_SPACE_RE.sub(" ", cleaned).strip()
    cleaned = _HOUR_RANGE_DASH_RE.sub(r"\1 - \2", cleaned)
    return cleaned


def _build_dedupe_key(
    *,
    window_text: str,
    reopen_text: str,
    fallback_date_iso: str = "",
) -> str:
    date_iso = fallback_date_iso
    date_match = _DATE_DMY_RE.search(reopen_text or "")
    if date_match:
        day_s, month_s, year_s = date_match.groups()
        try:
            date_iso = f"{int(year_s):04d}-{int(month_s):02d}-{int(day_s):02d}"
        except ValueError:
            pass
    if not date_iso:
        vn_now = datetime.now(timezone.utc) + timedelta(hours=7)
        date_iso = vn_now.date().isoformat()

    window_clean = _PAREN_NOTE_RE.sub("", normalize_maintenance_text(window_text))
    window_compact = re.sub(r"\s+", "", window_clean) or "sitemaintenance"

    reopen_clean = normalize_maintenance_text(reopen_text)
    if "ngày" in reopen_clean.lower():
        reopen_time = re.split(r"\bngày\b", reopen_clean, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    else:
        reopen_time = reopen_clean
    reopen_compact = re.sub(r"\s+", "", reopen_time) or "unknown"

    return f"{date_iso}|{window_compact}|{reopen_compact}"


@dataclass(frozen=True, slots=True)
class MaintenanceNotice:
    """Immutable descriptor of an active UTH / Moodle maintenance window."""

    source: MaintenanceSource
    window_text: str = ""
    reopen_text: str = ""
    raw_title: str = "Hệ thống UTH đang tạm ngưng bảo trì"
    dedupe_key: str = ""

    def __post_init__(self) -> None:
        norm_source = _normalize_source(self.source)
        if norm_source != self.source:
            object.__setattr__(self, "source", norm_source)
        norm_window = normalize_maintenance_text(self.window_text)
        if norm_window != self.window_text:
            object.__setattr__(self, "window_text", norm_window)
        norm_reopen = normalize_maintenance_text(self.reopen_text)
        if norm_reopen != self.reopen_text:
            object.__setattr__(self, "reopen_text", norm_reopen)
        if not self.dedupe_key:
            object.__setattr__(
                self,
                "dedupe_key",
                _build_dedupe_key(
                    window_text=self.window_text,
                    reopen_text=self.reopen_text,
                ),
            )

    def format_banner_text(self) -> str:
        """Format the single-line pinned banner copy for `AppController`."""
        parts: list[str] = []
        if self.window_text:
            parts.append(self.window_text)
        if self.reopen_text:
            parts.append(f"Mở lại: {self.reopen_text}")
        if parts:
            return f"Hệ thống UTH đang bảo trì ({' · '.join(parts)}) · Đang xem dữ liệu đã lưu"
        return "Hệ thống UTH đang bảo trì · Đang xem dữ liệu đã lưu"


class UthMaintenanceError(RuntimeError):
    """Raised when UTH LMS or Portal is temporarily unavailable due to maintenance."""

    def __init__(
        self,
        notice: MaintenanceNotice,
        message: str | None = None,
    ) -> None:
        self.notice = notice
        if not message:
            details: list[str] = []
            if notice.window_text:
                details.append(notice.window_text)
            if notice.reopen_text:
                details.append(f"Mở lại: {notice.reopen_text}")
            suffix = f" ({' · '.join(details)})" if details else ""
            message = f"Hệ thống UTH đang bảo trì{suffix}"
        super().__init__(message)


def _decompress_gzip_bounded(raw: bytes, max_decompressed_bytes: int) -> bytes:
    if not raw or max_decompressed_bytes <= 0:
        return b""
    out = bytearray()
    cursor = 0
    length = len(raw)
    step = 16384
    try:
        while cursor < length and len(out) < max_decompressed_bytes:
            decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
            member_start = cursor
            while cursor < length and len(out) < max_decompressed_bytes:
                end = min(cursor + step, length)
                pending = raw[cursor:end]
                cursor = end
                while pending and len(out) < max_decompressed_bytes:
                    remaining = max_decompressed_bytes - len(out)
                    piece = decomp.decompress(pending, remaining)
                    if piece:
                        out.extend(piece)
                    if decomp.eof:
                        cursor -= len(decomp.unused_data)
                        break
                    if decomp.unconsumed_tail:
                        if not piece and len(decomp.unconsumed_tail) >= len(pending):
                            return bytes(out[:max_decompressed_bytes])
                        pending = decomp.unconsumed_tail
                    else:
                        pending = b""
                if decomp.eof:
                    break
            if len(out) < max_decompressed_bytes and not decomp.eof:
                tail = decomp.flush(max_decompressed_bytes - len(out))
                if tail:
                    out.extend(tail)
            if cursor <= member_start:
                break
    except zlib.error:
        return bytes(out) if out else raw[:max_decompressed_bytes]
    return bytes(out[:max_decompressed_bytes])


def read_bounded_error_body(
    error_or_bytes: object,
    *,
    max_bytes: int = 65536,
    max_decompressed_bytes: int = 262144,
) -> tuple[bytes, str]:
    """Read at most `max_bytes` raw and `max_decompressed_bytes` gunzipped from an error/response."""
    raw_cap = max(1, int(max_bytes))
    decomp_cap = max(raw_cap, int(max_decompressed_bytes))

    if error_or_bytes is None:
        return b"", ""
    if isinstance(error_or_bytes, str):
        return error_or_bytes.encode("utf-8", errors="replace")[:raw_cap], ""
    if isinstance(error_or_bytes, (bytes, bytearray, memoryview)):
        raw_buf = bytes(error_or_bytes)[:raw_cap]
        if raw_buf[:2] == b"\x1f\x8b":
            return _decompress_gzip_bounded(raw_buf, decomp_cap), ""
        return raw_buf, ""

    headers = getattr(error_or_bytes, "headers", None)
    if headers is None and callable(getattr(error_or_bytes, "info", None)):
        try:
            headers = error_or_bytes.info()
        except Exception:
            headers = None

    content_type = ""
    content_encoding = ""
    if headers is not None and callable(getattr(headers, "get", None)):
        try:
            content_type = str(headers.get("Content-Type") or "")
            content_encoding = str(headers.get("Content-Encoding") or "").lower()
        except Exception:
            content_type = ""
            content_encoding = ""

    read_fn = getattr(error_or_bytes, "read", None)
    if not callable(read_fn):
        fp = getattr(error_or_bytes, "fp", None)
        read_fn = getattr(fp, "read", None) if fp is not None else None

    raw = b""
    if callable(read_fn):
        try:
            chunk = read_fn(raw_cap)
        except TypeError:
            try:
                chunk = read_fn()
            except Exception:
                chunk = b""
        except Exception:
            chunk = b""
        if isinstance(chunk, str):
            raw = chunk.encode("utf-8", errors="replace")[:raw_cap]
        elif isinstance(chunk, (bytes, bytearray, memoryview)):
            raw = bytes(chunk)[:raw_cap]

    if raw and ("gzip" in content_encoding or raw[:2] == b"\x1f\x8b"):
        return _decompress_gzip_bounded(raw, decomp_cap), content_type
    return raw, content_type


def _strip_html_to_lines(raw_html: str) -> str:
    with_breaks = _HTML_BR_RE.sub("\n", raw_html)
    without_tags = _HTML_TAG_RE.sub("", with_breaks)
    return html.unescape(without_tags)


def _resolve_utc_now(now_utc: datetime | None) -> datetime:
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def _is_moodle_sitemaintenance_json(payload: Any) -> bool:
    if isinstance(payload, dict):
        err_code = str(payload.get("errorcode") or "").strip().lower()
        if err_code == "sitemaintenance":
            return True
        exc_obj = payload.get("exception")
        if isinstance(exc_obj, dict):
            nested_code = str(exc_obj.get("errorcode") or "").strip().lower()
            if nested_code == "sitemaintenance":
                return True
        return False
    if isinstance(payload, list) and payload:
        return any(_is_moodle_sitemaintenance_json(item) for item in payload[:4])
    return False


def detect_uth_maintenance(
    *,
    status: int | None,
    body: bytes | str | None,
    content_type: str = "",
    source: str = "uth",
    now_utc: datetime | None = None,
) -> MaintenanceNotice | None:
    """Inspect an HTTP response/error payload and return `MaintenanceNotice` if in maintenance."""
    del content_type  # Status + structural body inspection handles mislabeled proxies safely.
    if body is None:
        return None

    if isinstance(body, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(body)
        if raw_bytes[:2] == b"\x1f\x8b":
            raw_bytes = _decompress_gzip_bounded(raw_bytes, 262144)
        text = raw_bytes.decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    else:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    lowered = stripped.lower()
    if any(marker in lowered for marker in _CLOUDFLARE_CHALLENGE_MARKERS):
        return None

    utc_dt = _resolve_utc_now(now_utc)
    norm_source = _normalize_source(source)

    # 1. Moodle Web Services / AJAX JSON `errorcode == "sitemaintenance"`
    if "sitemaintenance" in lowered and stripped[0] in ("{", "["):
        try:
            parsed_json = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            parsed_json = None
        if _is_moodle_sitemaintenance_json(parsed_json):
            vn_date = (utc_dt + timedelta(hours=7)).date().isoformat()
            return MaintenanceNotice(
                source=norm_source,
                window_text="",
                reopen_text="",
                raw_title="Hệ thống UTH đang tạm ngưng bảo trì",
                dedupe_key=f"{vn_date}|sitemaintenance|unknown",
            )

    # 2. UTH Gateway HTML maintenance page (`01h00 - 04h00`, HTTP 403 / 502 / 503 / 200)
    title_match = _TITLE_RE.search(stripped)
    title_text = normalize_maintenance_text(
        _HTML_TAG_RE.sub("", title_match.group(1)) if title_match else ""
    )
    title_lower = title_text.lower()

    h1_match = _H1_RE.search(stripped)
    h1_text = normalize_maintenance_text(
        _HTML_TAG_RE.sub("", h1_match.group(1)) if h1_match else ""
    )
    h1_lower = h1_text.lower()

    plain_lines = _strip_html_to_lines(stripped)
    plain_lower = plain_lines.lower()

    is_uth_gateway_maintenance = (
        "hệ thống tạm ngưng" in title_lower
        or "hệ thống đang tạm ngưng hoạt động" in plain_lower
        or (
            "tạm ngưng hoạt động" in h1_lower
            and (
                "thời gian tạm ngưng" in plain_lower
                or "hệ thống mở lại lúc" in plain_lower
                or "đang trong thời gian bảo trì" in plain_lower
            )
        )
        or (
            "thời gian tạm ngưng:" in plain_lower
            and "hệ thống mở lại lúc:" in plain_lower
        )
    )

    if is_uth_gateway_maintenance:
        window_match = _WINDOW_LINE_RE.search(plain_lines)
        if window_match:
            window_text = normalize_maintenance_text(window_match.group(1))
        else:
            inline_match = _INLINE_WINDOW_RE.search(plain_lines)
            window_text = (
                normalize_maintenance_text(inline_match.group(1))
                if inline_match
                else ""
            )

        open_at_match = _OPEN_AT_ID_RE.search(stripped)
        if open_at_match:
            reopen_raw = normalize_maintenance_text(
                _HTML_TAG_RE.sub("", open_at_match.group(1))
            )
        else:
            reopen_line_match = _REOPEN_LINE_RE.search(plain_lines)
            reopen_raw = (
                normalize_maintenance_text(reopen_line_match.group(1))
                if reopen_line_match
                else ""
            )

        tz_match = _TZ_OFFSET_SCRIPT_RE.search(stripped)
        try:
            tz_offset = int(tz_match.group(1)) if tz_match else 7
        except ValueError:
            tz_offset = 7
        if not -12 <= tz_offset <= 14:
            tz_offset = 7
        vn_now = utc_dt + timedelta(hours=tz_offset)

        reopen_text = reopen_raw
        has_explicit_date = bool(
            "ngày" in reopen_raw.lower() or _DATE_DMY_RE.search(reopen_raw)
        )
        open_hour_match = _OPEN_HOUR_SCRIPT_RE.search(stripped)
        if not has_explicit_date and open_hour_match:
            try:
                open_hour = int(open_hour_match.group(1))
            except ValueError:
                open_hour = -1
            if 0 <= open_hour <= 23:
                open_min_match = _OPEN_MINUTE_SCRIPT_RE.search(stripped)
                open_minute = 0
                if open_min_match:
                    try:
                        cand_min = int(open_min_match.group(1))
                        if 0 <= cand_min <= 59:
                            open_minute = cand_min
                    except ValueError:
                        open_minute = 0
                else:
                    hm_match = _CLOCK_HM_RE.fullmatch(reopen_raw)
                    if hm_match and int(hm_match.group(1)) == open_hour and hm_match.group(2):
                        cand_min = int(hm_match.group(2))
                        if 0 <= cand_min <= 59:
                            open_minute = cand_min

                if (vn_now.hour, vn_now.minute) >= (open_hour, open_minute):
                    reopen_date = vn_now.date() + timedelta(days=1)
                else:
                    reopen_date = vn_now.date()

                clock_label = (
                    reopen_raw
                    if _CLOCK_HM_RE.fullmatch(reopen_raw)
                    else f"{open_hour:02d}h{open_minute:02d}"
                )
                reopen_text = f"{clock_label} ngày {reopen_date:%d/%m/%Y}"

        dedupe_key = _build_dedupe_key(
            window_text=window_text,
            reopen_text=reopen_text,
            fallback_date_iso=vn_now.date().isoformat(),
        )
        return MaintenanceNotice(
            source=norm_source,
            window_text=window_text,
            reopen_text=reopen_text,
            raw_title="Hệ thống UTH đang tạm ngưng bảo trì",
            dedupe_key=dedupe_key,
        )

    # 3. Moodle native HTML maintenance (`HTTP 503` or `page-site-maintenance`)
    is_moodle_html_maintenance = (
        "page-site-maintenance" in lowered
        or (
            status == 503
            and any(
                marker in lowered
                for marker in (
                    "sitemaintenance",
                    "undergoing maintenance",
                    "site maintenance",
                    "đang bảo trì",
                    "tạm ngưng bảo trì",
                )
            )
        )
    )
    if is_moodle_html_maintenance:
        vn_date = (utc_dt + timedelta(hours=7)).date().isoformat()
        return MaintenanceNotice(
            source=norm_source,
            window_text="",
            reopen_text="",
            raw_title="Hệ thống UTH đang tạm ngưng bảo trì",
            dedupe_key=f"{vn_date}|sitemaintenance|unknown",
        )

    return None
