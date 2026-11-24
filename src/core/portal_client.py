from __future__ import annotations

import base64
import inspect
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections.abc import Callable
from datetime import date
from typing import Any

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


class PortalProtocolError(PortalError):
    """Portal returned a response outside the verified JSON contract."""


class _PortalHttpError(PortalError):
    def __init__(self, status: int):
        super().__init__(f"Portal HTTP {status}")
        self.status = status


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
        raw_base = str(base_url or "").strip()
        parsed = urllib.parse.urlsplit(raw_base)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "PortalClient requires an https:// base_url without userinfo, query, or fragment"
            )
        self._origin = f"https://{parsed.netloc.lower()}"
        self._base_url = raw_base.rstrip("/")
        self._timeout = max(1.0, float(timeout))
        if opener is None:
            default_opener = urllib.request.build_opener(
                _SamePortalOriginRedirectHandler(self._origin)
            )
            self._opener = default_opener.open
        else:
            self._opener = opener
        self._token = ""
        # The cached token belongs to exactly one account: after an account
        # switch a stale token must never be replayed for the new username.
        self._token_owner = ""
        self._token_role = ""
        # Invalidations are versioned: a fetch that started before
        # clear_token() must not re-store the token it obtained moments
        # earlier and resurrect the credential the caller just dropped.
        self._token_generation = 0
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

    def _discard_token(self, generation: int | None = None) -> None:
        """Clear the token, unless ``generation`` already went stale."""
        with self._token_lock:
            if generation is not None and generation != self._token_generation:
                return
            self._token = ""
            self._token_owner = ""
            self._token_role = ""
            self._token_generation += 1

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
    ) -> None:
        normalized_role = role.strip() if isinstance(role, str) else ""
        with self._token_lock:
            if generation is not None and generation != self._token_generation:
                # clear_token() ran while this request was in flight: the
                # caller no longer wants this session to exist.
                return
            same_owner = self._token_owner == username
            self._token = token
            self._token_owner = username
            if normalized_role:
                self._token_role = normalized_role
            elif not same_owner:
                self._token_role = ""

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        token: str = "",
        query: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{path.lstrip('/')}"
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
        try:
            with self._opener(request, timeout=self._timeout) as response:
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

    def _login_with_role(
        self, username: str, password: str, generation: int | None = None
    ) -> tuple[str, str]:
        _require_credentials(username, password)
        payload = self._request_json(
            "POST",
            "user/login",
            body={"username": username, "password": password},
        )
        raw_token = payload.get("token") if payload.get("success") else ""
        token = raw_token.strip() if isinstance(raw_token, str) else ""
        if not token:
            self._discard_token(generation)
            raise PortalAuthenticationError("Portal login was rejected")
        role = _extract_token_role(token, payload.get("body"))
        self._store_token(token, username, generation, role=role)
        return token, role

    def _login(
        self, username: str, password: str, generation: int | None = None
    ) -> str:
        token, _ = self._login_with_role(username, password, generation)
        return token

    def ensure_web_session(
        self, username: str, password: str, *, force: bool = False
    ) -> tuple[str, str]:
        """Return a valid ``(jwt_token, role)`` pair for Portal web sessions.

        Reuses the cached token for ``username`` unless ``force=True`` or the
        JWT ``exp`` claim is expired (or within the default skew window).
        """
        _require_credentials(username, password)
        with self._token_lock:
            generation = self._token_generation
            if not force and self._token and self._token_owner == username:
                cached_token = self._token
                cached_role = self._token_role
            else:
                cached_token = ""
                cached_role = ""
        if cached_token and not _is_jwt_expired(cached_token):
            return cached_token, cached_role or _extract_token_role(cached_token)
        return self._login_with_role(username, password, generation)

    def fetch_day(
        self, target_date: date, username: str, password: str
    ) -> tuple[ClassSession, ...]:
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
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
            token = self._login(username, password, generation)
            payload = self._request_json(
                "GET",
                "lichhoc/ngay",
                token=token,
                query={"date": target_date.isoformat()},
            )
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._store_token(refreshed_token, username, generation)
        try:
            return parse_portal_day(payload, target_date)
        except ValueError as exc:
            raise PortalProtocolError("Portal schedule contract was invalid") from exc

    def fetch_week(
        self, target_date: date, username: str, password: str
    ) -> dict[date, tuple[ClassSession, ...]]:
        """Fetch the Monday-based week containing ``target_date`` in one call
        (``lichhoc/lichTuan``) and group the sessions by calendar date."""
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
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
            token = self._login(username, password, generation)
            payload = self._request_json(
                "GET", "lichhoc/lichTuan", token=token, query=query
            )
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._store_token(refreshed_token, username, generation)
        try:
            return parse_portal_week(payload, target_date)
        except ValueError as exc:
            raise PortalProtocolError("Portal week contract was invalid") from exc

    def fetch_credit_progress(
        self, username: str, password: str
    ) -> dict[str, Any] | None:
        """Fetches graduation credit progress (``canDat`` / ``hienTai``) from Portal."""
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
            username, password, generation
        )
        try:
            payload = self._request_json("GET", "hoctap/tiendo", token=token)
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token = self._login(username, password, generation)
            payload = self._request_json("GET", "hoctap/tiendo", token=token)
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._store_token(refreshed_token, username, generation)
        body = payload.get("body")
        return body if isinstance(body, dict) else None

    def fetch_full_transcript(
        self, username: str, password: str
    ) -> list[dict[str, Any]]:
        """Fetches full academic transcript containing all semester grades from Portal."""
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
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
            token = self._login(username, password, generation)
            payload = self._request_json(
                "GET",
                "hoctap/bangdiem2",
                token=token,
            )
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._store_token(refreshed_token, username, generation)

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
        generation = self._current_generation()
        token = self._cached_token(username) or self._login(
            username, password, generation
        )
        try:
            hk_payload = self._request_json("GET", "hoctap/hocky", token=token)
        except _PortalHttpError as exc:
            if exc.status != 401:
                raise
            token = self._login(username, password, generation)
            hk_payload = self._request_json("GET", "hoctap/hocky", token=token)

        refreshed_token = hk_payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            token = refreshed_token
            self._store_token(refreshed_token, username, generation)

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
                token = self._login(username, password, generation)
                mon_payload = self._request_json(
                    "GET", f"hoctap/montheoky/{dot_id}", token=token
                )
            mon_refreshed = mon_payload.get("token")
            if isinstance(mon_refreshed, str) and mon_refreshed:
                token = mon_refreshed
                self._store_token(mon_refreshed, username, generation)
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
