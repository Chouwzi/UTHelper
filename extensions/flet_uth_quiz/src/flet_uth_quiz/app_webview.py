"""Python facade for general-purpose mobile InAppWebView (Courses, SharePoint, SSO, PDF/Office)."""

from __future__ import annotations

import asyncio
from dataclasses import field
import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import flet as ft
from flet.controls.base_control import control
from flet.controls.layout_control import LayoutControl

_UNSAFE_URL_RE = re.compile(r"[\x00-\x20\x7f\\]")
_RAW_AUTHORITY_RE = re.compile(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", re.IGNORECASE)
_COOKIE_PAYLOAD_LIMIT = 32 * 1024
_MAX_COOKIES = 64


def _validate_app_webview_url(url: str) -> str:
    raw = str(url or "")
    if not raw or _UNSAFE_URL_RE.search(raw):
        raise ValueError("url must be a safe HTTPS or loopback HTTP URL")
    trimmed = raw.strip()
    try:
        parsed = urlsplit(trimmed)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("url must be a safe HTTPS or loopback HTTP URL") from exc

    raw_auth_match = _RAW_AUTHORITY_RE.match(trimmed)
    raw_auth = raw_auth_match.group(1) if raw_auth_match else ""
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not raw_auth
        or "@" in raw_auth
    ):
        raise ValueError("url must not contain credentials or empty host")

    scheme = parsed.scheme.lower()
    if scheme == "https":
        return trimmed
    if scheme == "http":
        host = parsed.hostname.rstrip(".").casefold()
        if host in {"127.0.0.1", "localhost"} and port is not None and 1 <= port <= 65535:
            return trimmed
    raise ValueError("url must be an HTTPS URL or explicit 127.0.0.1/localhost port URL")


@control("UthAppWebView", kw_only=True)
class UthAppWebView(LayoutControl):
    """General-purpose embedded InAppWebView control for mobile document & web panes.

    Cookies travel exclusively over Flet's private ``DataChannel`` so session
    tokens never appear in control properties or ``_invoke_method`` diagnostics.
    """

    url: str = ""
    html: Optional[str] = None
    base_url: Optional[str] = None
    bgcolor: Optional[str] = None
    viewer_locked: bool = False
    allowed_loopback_ports: list[int] = field(default_factory=list)
    require_initial_cookies: bool = False
    expand: bool | int | None = True
    on_page_started: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_page_ended: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_url_change: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_title_change: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_history_change: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_web_error: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_console_message: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_external_url: Optional[ft.ControlEventHandler["UthAppWebView"]] = None
    on_data_channel_open: Optional[ft.EventHandler[ft.DataChannelOpenEvent]] = None

    def __post_init__(self, ref: object | None = None) -> None:
        super().__post_init__(ref)
        if self.url:
            self.url = _validate_app_webview_url(self.url)
        if self.base_url:
            self.base_url = _validate_app_webview_url(self.base_url)
        self._cookie_channel: ft.DataChannel | None = None
        self._cookie_channel_id: int | None = None
        self._cookie_channel_ready = asyncio.Event()
        self.on_data_channel_open = self._handle_data_channel_open

    def _handle_data_channel_open(self, event: ft.DataChannelOpenEvent) -> None:
        if getattr(event, "channel_name", None) != "app_webview_cookies":
            return
        self._cookie_channel_id = event.channel_id
        self._cookie_channel = self.get_data_channel(event.channel_id)
        self._cookie_channel_ready.set()

    async def bootstrap_cookies(
        self,
        cookies: list[dict[str, Any]],
        *,
        target_url: Optional[str] = None,
    ) -> bool:
        """Send cookies privately over DataChannel before initial URL load."""
        resolved_target = _validate_app_webview_url(target_url or self.url)
        safe_cookies: list[dict[str, Any]] = []
        for item in (cookies or ())[:_MAX_COOKIES]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            value = item.get("value")
            if not name or value is None:
                continue
            safe_cookies.append(
                {
                    "name": name,
                    "value": str(value),
                    "domain": str(item.get("domain") or ""),
                    "path": str(item.get("path") or "/"),
                    "secure": bool(item.get("secure", True)),
                    "httpOnly": bool(
                        item.get("httpOnly", item.get("http_only", False))
                    ),
                    "expires": item.get("expires"),
                }
            )
        payload = json.dumps(
            {"target_url": resolved_target, "cookies": safe_cookies},
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        if len(payload) > _COOKIE_PAYLOAD_LIMIT:
            return False
        try:
            await asyncio.wait_for(self._cookie_channel_ready.wait(), timeout=5.0)
            channel = self._cookie_channel
            channel_id = self._cookie_channel_id
            if channel is None or channel_id is None:
                return False
            channel.send(payload)
            res = await self._invoke_method(
                "bootstrap_cookies",
                {"channel_id": channel_id},
                timeout=10,
            )
            return bool(res)
        except Exception:
            return False

    async def load_request(self, url: str) -> bool:
        safe_url = _validate_app_webview_url(url)
        self.url = safe_url
        try:
            return bool(
                await self._invoke_method("navigate", {"url": safe_url}, timeout=10)
            )
        except Exception:
            return False

    async def load_html(self, value: str, *, base_url: Optional[str] = None) -> bool:
        safe_base = _validate_app_webview_url(base_url) if base_url else None
        try:
            return bool(
                await self._invoke_method(
                    "load_html",
                    {"html": str(value or ""), "base_url": safe_base},
                    timeout=10,
                )
            )
        except Exception:
            return False

    async def run_javascript(self, script: str) -> None:
        if not script:
            return
        try:
            await self._invoke_method("eval_js", {"script": str(script)}, timeout=10)
        except Exception:
            return

    async def go_back(self) -> bool:
        try:
            return bool(await self._invoke_method("go_back", timeout=10))
        except Exception:
            return False

    async def go_forward(self) -> bool:
        try:
            return bool(await self._invoke_method("go_forward", timeout=10))
        except Exception:
            return False

    async def reload(self) -> bool:
        try:
            return bool(await self._invoke_method("reload", timeout=10))
        except Exception:
            return False
