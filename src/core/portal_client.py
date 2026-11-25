from __future__ import annotations

import base64
import inspect
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from datetime import date
from typing import Any

from core.demo_sandbox import (
    get_demo_credit_progress,
    get_demo_current_semester_courses,
    get_demo_day_schedule,
    get_demo_full_transcript,
    get_demo_week_schedule,
    is_demo_account,
    verify_demo_credentials_cached,
)
from core.today_schedule import (
    ClassSession,
    ScheduleAuthenticationError,
    parse_portal_day,
    parse_portal_week,
)

_MAX_PORTAL_RESPONSE_BYTES = 8 * 1024 * 1024


def _decompress_gzip_bounded(compressed: bytes, max_bytes: int) -> bytes:
    """Incrementally decompress gzipped bytes while enforcing ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    step = 64 * 1024
    cursor = 0
    length = len(compressed)
    try:
        while cursor < length:
            decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
            member_start = cursor
            member_done = False
            while cursor < length:
                chunk_end = min(cursor + step, length)
                pending = compressed[cursor:chunk_end]
                cursor = chunk_end
                while pending:
                    remaining = max_bytes + 1 - total
                    if remaining <= 0:
                        raise PortalProtocolError("Portal response exceeded maximum size")
                    out = decomp.decompress(pending, min(256 * 1024, remaining))
                    if out:
                        total += len(out)
                        if total > max_bytes:
                            raise PortalProtocolError("Portal response exceeded maximum size")
                        chunks.append(out)
                    if decomp.eof:
                        cursor -= len(decomp.unused_data)
                        if cursor <= member_start:
                            raise PortalProtocolError("Portal response was not valid gzip")
                        member_done = True
                        break
                    if decomp.unconsumed_tail:
                        if not out and len(decomp.unconsumed_tail) >= len(pending):
                            raise PortalProtocolError("Portal response was not valid gzip")
                        pending = decomp.unconsumed_tail
                    else:
                        pending = b""
                if member_done:
                    break
            if not member_done:
                remaining = max_bytes + 1 - total
                if remaining <= 0:
                    raise PortalProtocolError("Portal response exceeded maximum size")
                tail = decomp.flush(remaining)
                if tail:
                    total += len(tail)
                    if total > max_bytes:
                        raise PortalProtocolError("Portal response exceeded maximum size")
                    chunks.append(tail)
                if decomp.unconsumed_tail:
                    raise PortalProtocolError("Portal response exceeded maximum size")
                if not decomp.eof:
                    raise PortalProtocolError("Portal response was not valid gzip")
    except zlib.error as exc:
        raise PortalProtocolError("Portal response was not valid gzip") from exc
    return b"".join(chunks)


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload section of a JWT without verifying its signature."""
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    segment = parts[1]
    padded = segment + ("=" * (-len(segment) % 4))
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_jwt_expired(token: str, *, skew_seconds: int = 300) -> bool:
    """Return True when ``token`` is a JWT whose ``exp`` claim is within ``skew_seconds``.

    Opaque tokens or JWTs without a numeric ``exp`` claim return ``False`` so
    non-JWT test tokens remain usable.
    """
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return False
    if not math.isfinite(exp):
        return False
    return int(exp) <= time.time() + skew_seconds


def _extract_token_role(token: str, response_body: Any = None) -> str:
    """Resolve the Portal web role from login ``body``, JWT ``data.role``, or ``'sv'``."""
    if isinstance(response_body, str) and response_body.strip():
        return response_body.strip()
    jwt_data = _decode_jwt_payload(token).get("data")
    if isinstance(jwt_data, dict):
        jwt_role = jwt_data.get("role")
        if isinstance(jwt_role, str) and jwt_role.strip():
            return jwt_role.strip()
    return "sv"


_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_trusted_portal_base_url(raw_base: object) -> tuple[str, str]:
    """Validate and normalize an HTTPS ``*.ut.edu.vn`` Portal API base URL.

    Returns ``(origin, normalized_base_url)`` or raises ``ValueError``.
    """
    if not isinstance(raw_base, str):
        raise ValueError("PortalClient requires a string base_url")
    cleaned = raw_base.strip()
    if (
        not cleaned
        or "\\" in cleaned
        or "?" in cleaned
        or "#" in cleaned
        or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in cleaned)
    ):
        raise ValueError(
            "PortalClient requires an https:// ut.edu.vn base_url without query, fragment, or control chars"
        )
    try:
        parsed = urllib.parse.urlsplit(cleaned)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid PortalClient base_url") from exc
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
        or not all(_DNS_LABEL_RE.fullmatch(label) for label in host.split("."))
    ):
        raise ValueError(
            "PortalClient requires an https:// ut.edu.vn base_url without port, userinfo, query, or fragment"
        )
    return f"https://{host}", cleaned.rstrip("/")


class _SamePortalOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, origin: str) -> None:
        super().__init__()
        self.origin = origin

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        parsed = urllib.parse.urlsplit(redirected.full_url)
        if (
            parsed.username is not None
            or parsed.password is not None
            or f"{parsed.scheme.lower()}://{parsed.netloc.lower()}" != self.origin
        ):
            raise urllib.error.HTTPError(
                redirected.full_url,
                code,
                "Untrusted Portal redirect",
                headers,
                fp,
            )
        return redirected


class PortalError(RuntimeError):
    """Base error for the authenticated UTH Portal boundary."""


class PortalAuthenticationError(PortalError, ScheduleAuthenticationError):
    """Portal rejected the supplied student identity or returned no token."""

    def __init__(
        self,
        message: object = "Portal login was rejected",
        *,
        status: int | None = None,
    ) -> None:
        if isinstance(message, int) and status is None:
            status = message
            super().__init__(f"Portal HTTP {message}")
        else:
            super().__init__(message)
        self.status = status
        self.status_code = status


PortalAuthError = PortalAuthenticationError


class PortalProtocolError(PortalError):
    """Portal returned a response outside the verified JSON contract."""


class _PortalHttpError(PortalError):
    def __init__(self, status: int):
        super().__init__(f"Portal HTTP {status}")
        self.status = status
        self.status_code = status


def _require_credentials(username: Any, password: Any) -> None:
    if (
        not isinstance(username, str)
        or not username.strip()
        or not isinstance(password, str)
        or not password
    ):
        raise PortalAuthenticationError("Portal credentials are unavailable")


class PortalClient:
    """Small stateless HTTP adapter for Portal authentication and daily classes.

    Credentials are supplied for an individual fetch and are never retained.
    The short-lived Bearer token is kept in memory only for the current client.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        origin, normalized_base = _validate_trusted_portal_base_url(base_url)
        self._origin = origin
        self._base_url = normalized_base
        self._timeout = max(1.0, float(timeout))
        self._opener: Callable[..., Any] | None = opener
        self._token = ""
        # The cached token belongs to exactly one account: after an account
        # switch a stale token must never be replayed for the new username.
        self._token_owner = ""
        self._token_role = ""
        # Invalidations are versioned: a fetch that started before
        # clear_token() must not re-store the token it obtained moments
        # earlier and resurrect the credential the caller just dropped.
        self._token_generation = 0
        self._clear_generation = 0
        self._token_lock = threading.Lock()

    def clear_token(self) -> None:
        """Drop the cached token and invalidate any login still in flight.

        Bumping the generation is what makes the invalidation stick: a fetch
        that started before this call holds a token from the rejected session,
        and without the bump ``_store_token`` would resurrect the credential
        the caller just cleared.
        """
        self._discard_token()

    def _current_generation(self) -> int:
        """Generation observed when a fetch starts; pass it back when storing."""
        with self._token_lock:
            return self._token_generation

    def _current_clear_generation(self) -> int:
        """Monotonic counter incremented only on token discard or account switch."""
        with self._token_lock:
            return self._clear_generation

    def _discard_token(self, generation: int | None = None) -> None:
        """Clear the token, unless ``generation`` already went stale."""
        with self._token_lock:
            if generation is not None and generation != self._token_generation:
                return
            self._token = ""
            self._token_owner = ""
            self._token_role = ""
            self._token_generation += 1
            self._clear_generation += 1

    def _cached_token(self, username: str) -> str:
        """Return the cached token only when it was issued for ``username``."""
        with self._token_lock:
            if self._token and self._token_owner == username:
                return self._token
            return ""

    def _store_token(
        self,
        token: str,
        username: str,
        generation: int | None = None,
        *,
        role: str = "",
    ) -> int | None:
        normalized_role = role.strip() if isinstance(role, str) else ""
        with self._token_lock:
            if generation is not None and generation != self._token_generation:
                # clear_token() or a newer login ran while this request was in flight:
                # do not resurrect or overwrite a newer session token.
                return None
            same_owner = self._token_owner == username
            if self._token_owner and not same_owner:
                self._clear_generation += 1
            self._token = token
            self._token_owner = username
            if normalized_role:
                self._token_role = normalized_role
            elif not same_owner:
                self._token_role = ""
            self._token_generation += 1
            return self._token_generation

    def _ensure_trusted_endpoint(self) -> str:
        try:
            expected_origin, normalized_base = _validate_trusted_portal_base_url(
                self._base_url
            )
        except ValueError as exc:
            raise PortalError("Untrusted Portal API base URL") from exc
        if self._origin != expected_origin:
            raise PortalError("Untrusted Portal API origin")
        return normalized_base

    def _ensure_opener(self) -> Callable[..., Any]:
        if self._opener is None:
            default_opener = urllib.request.build_opener(
                _SamePortalOriginRedirectHandler(self._origin)
            )
            self._opener = default_opener.open
        return self._opener

    @staticmethod
    def _verify_demo_credentials(username: str, password: str) -> None:
        if not verify_demo_credentials_cached(username, password):
            raise PortalAuthError(401)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str = "",
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        verified_base = self._ensure_trusted_endpoint()
        url = f"{verified_base}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        raw_body = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "User-Agent": "UTHelper/PortalSchedule",
        }
        if raw_body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            data=raw_body,
            headers=headers,
            method=method,
        )
        opener = self._ensure_opener()
        try:
            with opener(request, timeout=self._timeout) as response:
                geturl = getattr(response, "geturl", None)
                if callable(geturl):
                    final_url = str(geturl() or "")
                    if final_url:
                        final_parsed = urllib.parse.urlsplit(final_url)
                        final_origin = (
                            f"{final_parsed.scheme.lower()}://{final_parsed.netloc.lower()}"
                        )
                        if (
                            final_parsed.username is not None
                            or final_parsed.password is not None
                            or final_origin != self._origin
                        ):
                            raise PortalProtocolError(
                                "Portal response redirected outside trusted origin"
                            )
                read_fn = response.read
                try:
                    sig = inspect.signature(read_fn)
                    accepts_amt = any(
                        p.kind
                        in (
                            inspect.Parameter.POSITIONAL_ONLY,
                            inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            inspect.Parameter.VAR_POSITIONAL,
                        )
                        for p in sig.parameters.values()
                    )
                except (TypeError, ValueError):
                    accepts_amt = True
                raw = (
                    read_fn(_MAX_PORTAL_RESPONSE_BYTES + 1)
                    if accepts_amt
                    else read_fn()
                )
                if len(raw) > _MAX_PORTAL_RESPONSE_BYTES:
                    raise PortalProtocolError("Portal response exceeded maximum size")
                content_encoding = (response.headers.get("Content-Encoding") or "").lower()
                if "gzip" in content_encoding:
                    try:
                        raw = _decompress_gzip_bounded(raw, _MAX_PORTAL_RESPONSE_BYTES)
                    except zlib.error as exc:
                        raise PortalProtocolError("Portal response was not valid gzip") from exc
        except urllib.error.HTTPError as exc:
            raise _PortalHttpError(exc.code) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise PortalError("Portal network request failed") from exc
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise PortalProtocolError("Portal response was not JSON") from exc
        if not isinstance(payload, dict):
            raise PortalProtocolError("Portal response envelope was not an object")
        return payload

    def _login_with_role_and_generation(
        self, username: str, password: str, generation: int | None = None
    ) -> tuple[str, str, int | None]:
        _require_credentials(username, password)
        try:
            payload = self._request_json(
                "POST",
                "user/login",
                body={"username": username, "password": password},
            )
        except _PortalHttpError as exc:
            if exc.status in (401, 403):
                self._discard_token(generation)
                raise PortalAuthenticationError(exc.status) from exc
            raise
        raw_token = payload.get("token") if payload.get("success") else ""
        token = raw_token.strip() if isinstance(raw_token, str) else ""
        if not token:
            self._discard_token(generation)
            raise PortalAuthenticationError("Portal login was rejected")
        role = _extract_token_role(token, payload.get("body"))
        stored_gen = self._store_token(token, username, generation, role=role)
        return token, role, stored_gen

    def _login_with_role(
        self, username: str, password: str, generation: int | None = None
    ) -> tuple[str, str]:
        token, role, _stored_gen = self._login_with_role_and_generation(
            username, password, generation
        )
        return token, role

    def _login(
        self, username: str, password: str, generation: int | None = None
    ) -> str:
        token, _role, _stored_gen = self._login_with_role_and_generation(
            username, password, generation
        )
        return token

    def _login_with_generation(
        self, username: str, password: str, generation: int | None = None
    ) -> tuple[str, int | None]:
        token, _role, stored_gen = self._login_with_role_and_generation(
            username, password, generation
        )
        return token, stored_gen if stored_gen is not None else generation

    def ensure_web_session(
        self,
        username: str,
        password: str,
        *,
        force: bool = False,
        expected_generation: int | None = None,
        expected_clear_generation: int | None = None,
    ) -> tuple[str, str]:
        """Return a valid ``(jwt_token, role)`` pair for Portal web sessions.

        Reuses the cached token for ``username`` unless ``force=True`` or the
        JWT ``exp`` claim is expired (or within the default skew window).
        """
        _require_credentials(username, password)
        self._ensure_trusted_endpoint()
        if is_demo_account(username):
            return "", ""
        with self._token_lock:
            if (
                expected_clear_generation is not None
                and expected_clear_generation != self._clear_generation
            ):
                return "", ""
            if (
                expected_generation is not None
                and expected_generation != self._token_generation
            ):
                if not self._token or self._token_owner != username:
                    return "", ""
            start_clear_gen = self._clear_generation
            generation = self._token_generation
            if not force and self._token and self._token_owner == username:
                cached_token = self._token
                cached_role = self._token_role
            else:
                cached_token = ""
                cached_role = ""
        if cached_token and not _is_jwt_expired(cached_token):
            return cached_token, cached_role or _extract_token_role(cached_token)
        token, role, stored_gen = self._login_with_role_and_generation(
            username, password, generation
        )
        if stored_gen is None:
            with self._token_lock:
                same_user_uninterrupted = (
                    self._clear_generation == start_clear_gen
                    and bool(self._token)
                    and self._token_owner == username
                )
                if same_user_uninterrupted and role and not self._token_role:
                    self._token_role = role
                if not same_user_uninterrupted and (
                    expected_generation is not None
                    or expected_clear_generation is not None
                ):
                    return "", ""
        return token, role

    def _store_refreshed_token(
        self, payload: dict[str, Any], username: str, generation: int | None
    ) -> tuple[str | None, int | None]:
        refreshed = payload.get("token")
        if isinstance(refreshed, str) and refreshed:
            stored_gen = self._store_token(refreshed, username, generation)
            return refreshed, stored_gen if stored_gen is not None else generation
        return None, generation

    def fetch_day(
        self, target_date: date, username: str, password: str
    ) -> tuple[ClassSession, ...]:
        if is_demo_account(username):
            self._verify_demo_credentials(username, password)
            return get_demo_day_schedule(target_date).sessions
        generation: int | None = self._current_generation()
        token = self._cached_token(username)
        if not token:
            token, generation = self._login_with_generation(
                username, password, generation
            )
        try:
            payload = self._request_json(
                "GET",
                "lichhoc/ngay",
                token=token,
                query={"date": target_date.isoformat()},
            )
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token, generation = self._login_with_generation(
                username, password, generation
            )
            payload = self._request_json(
                "GET",
                "lichhoc/ngay",
                token=token,
                query={"date": target_date.isoformat()},
            )
        self._store_refreshed_token(payload, username, generation)
        try:
            return parse_portal_day(payload, target_date)
        except ValueError as exc:
            raise PortalProtocolError("Portal schedule contract was invalid") from exc

    def fetch_week(
        self, target_date: date, username: str, password: str
    ) -> dict[date, tuple[ClassSession, ...]]:
        """Fetch the Monday-based week containing ``target_date`` in one call
        (``lichhoc/lichTuan``) and group the sessions by calendar date."""
        if is_demo_account(username):
            self._verify_demo_credentials(username, password)
            return dict(get_demo_week_schedule(target_date).days)
        generation: int | None = self._current_generation()
        token = self._cached_token(username)
        if not token:
            token, generation = self._login_with_generation(
                username, password, generation
            )
        query = {"date": target_date.isoformat()}
        try:
            payload = self._request_json(
                "GET", "lichhoc/lichTuan", token=token, query=query
            )
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token, generation = self._login_with_generation(
                username, password, generation
            )
            payload = self._request_json(
                "GET", "lichhoc/lichTuan", token=token, query=query
            )
        self._store_refreshed_token(payload, username, generation)
        try:
            return parse_portal_week(payload, target_date)
        except ValueError as exc:
            raise PortalProtocolError("Portal week contract was invalid") from exc

    def _authorized_get(
        self,
        path: str,
        username: str,
        password: str,
        *,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """GET ``path`` with the cached Bearer token and one 401 re-login retry.

        Shared by the read-only profile/tuition endpoints so every call keeps
        the same token-owner and generation rules as ``fetch_day``.
        """
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
            username, password, generation
        )
        try:
            payload = self._request_json("GET", path, token=token, query=query)
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token = self._login(username, password, generation)
            payload = self._request_json("GET", path, token=token, query=query)
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._store_token(refreshed_token, username, generation)
        return payload

    @staticmethod
    def _list_body(payload: dict[str, Any], path: str) -> tuple[dict[str, Any], ...]:
        """Normalize a list envelope into dicts; ``null`` means an empty list."""
        body = payload.get("body")
        if body is None:
            return ()
        if not isinstance(body, list):
            raise PortalProtocolError(f"Portal {path} contract was invalid")
        return tuple(item for item in body if isinstance(item, dict))

    def fetch_summary_profile(
        self, username: str, password: str
    ) -> dict[str, Any]:
        """Fetch the compact student identity shown on the Portal dashboard.

        Returns the raw ``body`` of ``user/getSummaryProfile`` (hoDem/ten,
        maSinhVien, khoa, nganh, ...) so callers can tolerate optional
        fields the Portal does not always populate.
        """
        payload = self._authorized_get(
            "user/getSummaryProfile", username, password
        )
        body = payload.get("body")
        if not isinstance(body, dict):
            raise PortalProtocolError("Portal summary profile contract was invalid")
        return body

    def fetch_credit(self, username: str, password: str) -> str:
        """Fetch the UTH account balance as Portal renders it (``0.00Đ (UTH)``)."""
        payload = self._authorized_get("user/getCredit", username, password)
        body = payload.get("body")
        if body is None:
            return ""
        if not isinstance(body, str):
            raise PortalProtocolError("Portal credit contract was invalid")
        return body.strip()

    def fetch_profile_image(self, username: str, password: str) -> str:
        """Fetch the student's Portal avatar as an image source string.

        The Portal answers ``user/image`` with a ``data:image/...;base64,...``
        URI (or occasionally a bare base64 string); both are normalized here
        so callers can hand the result straight to ``ft.Image(src=...)``.
        An empty body means "no avatar" and returns ``""`` instead of raising.
        """
        payload = self._authorized_get("user/image", username, password)
        body = payload.get("body")
        if body is None:
            return ""
        if not isinstance(body, str):
            raise PortalProtocolError("Portal image contract was invalid")
        text = body.strip()
        if not text:
            return ""
        if text.startswith(("data:image/", "http://", "https://")):
            return text
        return f"data:image/png;base64,{text}"

    def fetch_semesters(
        self, username: str, password: str
    ) -> tuple[dict[str, Any], ...]:
        """Fetch semester references (``{"id", "tenDot"}``) in Portal order."""
        return self._list_body(
            self._authorized_get("hoctap/hocky", username, password),
            "hocky",
        )

    def fetch_tuition(
        self, id_dot: int | str, username: str, password: str
    ) -> tuple[dict[str, Any], ...]:
        """Fetch per-course tuition rows for ``id_dot`` (``0`` = every semester).

        The field names mirror the Portal's own ``/tuition`` table
        (tenDot, maLopHocPhan, noiDung, soTinChi, mucPhiBanDau, mucNop,
        ngayNop, soTienNop, khauTru, truNo, congNo, daDongHocPhi).
        """
        return self._list_body(
            self._authorized_get(
                "hocphi",
                username,
                password,
                query={"idDot": str(id_dot)},
            ),
            "hocphi",
        )

    def fetch_tuition_other(
        self, username: str, password: str
    ) -> tuple[dict[str, Any], ...]:
        """Fetch non-tuition collections (bảo hiểm, ký túc xá, ...)."""
        return self._list_body(
            self._authorized_get("hocphi/khoanthukhac", username, password),
            "hocphi/khoanthukhac",
        )

    def fetch_credit_progress(
        self, username: str, password: str
    ) -> dict[str, Any] | None:
        """Fetches graduation credit progress (``canDat`` / ``hienTai``) from Portal."""
        if is_demo_account(username):
            self._verify_demo_credentials(username, password)
            return get_demo_credit_progress()
        generation: int | None = self._current_generation()
        token = self._cached_token(username)
        if not token:
            token, generation = self._login_with_generation(
                username, password, generation
            )
        try:
            payload = self._request_json("GET", "hoctap/tiendo", token=token)
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token, generation = self._login_with_generation(
                username, password, generation
            )
            payload = self._request_json("GET", "hoctap/tiendo", token=token)
        self._store_refreshed_token(payload, username, generation)
        body = payload.get("body")
        return body if isinstance(body, dict) else None

    def fetch_full_transcript(
        self, username: str, password: str
    ) -> list[dict[str, Any]]:
        """Fetches full academic transcript containing all semester grades from Portal."""
        if is_demo_account(username):
            self._verify_demo_credentials(username, password)
            return get_demo_full_transcript()
        generation: int | None = self._current_generation()
        token = self._cached_token(username)
        if not token:
            token, generation = self._login_with_generation(
                username, password, generation
            )
        try:
            payload = self._request_json(
                "GET",
                "hoctap/bangdiem2",
                token=token,
            )
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token, generation = self._login_with_generation(
                username, password, generation
            )
            payload = self._request_json(
                "GET",
                "hoctap/bangdiem2",
                token=token,
            )
        refreshed, generation = self._store_refreshed_token(payload, username, generation)
        if refreshed:
            token = refreshed

        body = payload.get("body")
        if isinstance(body, list) and body:
            return body
        elif isinstance(body, dict):
            for k in ("semesters", "bangDiem", "dsDiemHocKy", "kqHocTap", "dsHocKy", "data", "list"):
                if k in body and isinstance(body[k], list) and body[k]:
                    return body[k]
            if any(k in body for k in ("diems", "danhSachMonHoc", "dsDiem", "courses")):
                return [body]

        # Fallback to /hoctap/hocky + /hoctap/kqtheoky/{idDot} if bangdiem2 yielded nothing
        try:
            hk_payload = self._request_json("GET", "hoctap/hocky", token=token)
            hk_list = hk_payload.get("body") or []
            if isinstance(hk_list, list) and hk_list:
                results: list[dict[str, Any]] = []
                for hk in hk_list[:12]:
                    dot_id = hk.get("id") or hk.get("idDot")
                    if not dot_id:
                        continue
                    try:
                        kq_payload = self._request_json("GET", f"hoctap/kqtheoky/{dot_id}", token=token)
                        kq_body = kq_payload.get("body")
                        if kq_body:
                            if isinstance(kq_body, list):
                                results.append({
                                    "id": dot_id,
                                    "tenDot": hk.get("tenDot") or f"Học kỳ {dot_id}",
                                    "danhSachMonHoc": kq_body,
                                })
                            elif isinstance(kq_body, dict):
                                kq_dict = dict(kq_body)
                                kq_dict.setdefault("id", dot_id)
                                kq_dict.setdefault("tenDot", hk.get("tenDot") or f"Học kỳ {dot_id}")
                                results.append(kq_dict)
                    except Exception:
                        pass
                if results:
                    return results
        except Exception:
            pass

        return []

    def fetch_current_semester_courses(
        self, username: str, password: str
    ) -> tuple[str, list[dict[str, Any]]]:
        """Fetch registered courses for the student's active semester via `/hoctap/hocky` & `/hoctap/montheoky/{idDot}`."""
        if is_demo_account(username):
            self._verify_demo_credentials(username, password)
            return get_demo_current_semester_courses()
        generation: int | None = self._current_generation()
        token = self._cached_token(username)
        if not token:
            token, generation = self._login_with_generation(
                username, password, generation
            )
        try:
            hk_payload = self._request_json("GET", "hoctap/hocky", token=token)
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token, generation = self._login_with_generation(
                username, password, generation
            )
            hk_payload = self._request_json("GET", "hoctap/hocky", token=token)

        refreshed_token, generation = self._store_refreshed_token(
            hk_payload, username, generation
        )
        if refreshed_token:
            token = refreshed_token

        hk_list = hk_payload.get("body")
        if not isinstance(hk_list, list) or not hk_list:
            return ("", [])

        # Sort newest semester first by numeric idDot when present, preserving server order on tie
        valid_semesters = [s for s in hk_list if isinstance(s, dict) and (s.get("id") or s.get("idDot"))]
        if not valid_semesters:
            return ("", [])

        def _sem_id_key(item: dict[str, Any]) -> int:
            raw_id = item.get("id") or item.get("idDot") or 0
            try:
                return int(raw_id)
            except (TypeError, ValueError):
                return 0

        ordered = sorted(valid_semesters, key=_sem_id_key, reverse=True)
        for sem_entry in ordered[:2]:
            dot_id = sem_entry.get("id") or sem_entry.get("idDot")
            ten_dot = str(sem_entry.get("tenDot") or sem_entry.get("name") or "").strip()
            if not dot_id:
                continue
            try:
                mon_payload = self._request_json(
                    "GET", f"hoctap/montheoky/{dot_id}", token=token
                )
            except _PortalHttpError as exc:
                if exc.status != 401:
                    raise
                token, generation = self._login_with_generation(
                    username, password, generation
                )
                mon_payload = self._request_json(
                    "GET", f"hoctap/montheoky/{dot_id}", token=token
                )
            mon_refreshed, generation = self._store_refreshed_token(
                mon_payload, username, generation
            )
            if mon_refreshed:
                token = mon_refreshed
            mon_body = mon_payload.get("body")
            if isinstance(mon_body, list) and mon_body:
                normalized_items: list[dict[str, Any]] = []
                for row in mon_body:
                    if not isinstance(row, dict):
                        continue
                    code = str(
                        row.get("maHocPhan")
                        or row.get("maLopHocPhan")
                        or row.get("course_code")
                        or ""
                    ).strip()
                    name = str(
                        row.get("tenMonHoc")
                        or row.get("tenLopHocPhan")
                        or row.get("name")
                        or ""
                    ).strip()
                    raw_tc = row.get("soTinChi") if row.get("soTinChi") is not None else row.get("stc", 0)
                    try:
                        credits = max(0, int(float(raw_tc or 0)))
                    except (TypeError, ValueError):
                        credits = 0
                    if code or name:
                        normalized_items.append(
                            {"maHocPhan": code, "tenMonHoc": name, "soTinChi": credits}
                        )
                if normalized_items:
                    return (ten_dot, normalized_items)

        first_ten = str(ordered[0].get("tenDot") or "").strip()
        return (first_ten, [])
