from __future__ import annotations

import gzip
import json
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
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = max(1.0, float(timeout))
        self._opener = opener
        self._token = ""

    def clear_token(self) -> None:
        self._token = ""

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
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
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

    def _login(self, username: str, password: str) -> str:
        if not username or not password:
            raise PortalAuthenticationError("Portal credentials are unavailable")
        payload = self._request_json(
            "POST",
            "user/login",
            body={"username": username, "password": password},
        )
        token = payload.get("token") if payload.get("success") else ""
        if not isinstance(token, str) or not token:
            self.clear_token()
            raise PortalAuthenticationError("Portal login was rejected")
        self._token = token
        return token

    def fetch_day(
        self, target_date: date, username: str, password: str
    ) -> tuple[ClassSession, ...]:
        token = self._token or self._login(username, password)
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
            token = self._login(username, password)
            payload = self._request_json(
                "GET",
                "lichhoc/ngay",
                token=token,
                query={"date": target_date.isoformat()},
            )
        refreshed_token = payload.get("token")
        if isinstance(refreshed_token, str) and refreshed_token:
            self._token = refreshed_token
        try:
            return parse_portal_day(payload, target_date)
        except ValueError as exc:
            raise PortalProtocolError("Portal schedule contract was invalid") from exc
