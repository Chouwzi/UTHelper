from __future__ import annotations

import gzip
import inspect
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Any

from core.today_schedule import (
    ClassSession,
    ScheduleAuthenticationError,
    parse_portal_day,
)

_MAX_PORTAL_RESPONSE_BYTES = 8 * 1024 * 1024


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
            self._token_generation += 1

    def _cached_token(self, username: str) -> str:
        """Return the cached token only when it was issued for ``username``."""
        with self._token_lock:
            if self._token and self._token_owner == username:
                return self._token
            return ""

    def _store_token(
        self, token: str, username: str, generation: int | None = None
    ) -> None:
        with self._token_lock:
            if generation is not None and generation != self._token_generation:
                # clear_token() ran while this request was in flight: the
                # caller no longer wants this session to exist.
                return
            self._token = token
            self._token_owner = username

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
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                    if len(raw) > _MAX_PORTAL_RESPONSE_BYTES:
                        raise PortalProtocolError("Portal response exceeded maximum size")
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

    def _login(
        self, username: str, password: str, generation: int | None = None
    ) -> str:
        if not username or not password:
            raise PortalAuthenticationError("Portal credentials are unavailable")
        payload = self._request_json(
            "POST",
            "user/login",
            body={"username": username, "password": password},
        )
        token = payload.get("token") if payload.get("success") else ""
        if not isinstance(token, str) or not token:
            self._discard_token(generation)
            raise PortalAuthenticationError("Portal login was rejected")
        self._store_token(token, username, generation)
        return token

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
