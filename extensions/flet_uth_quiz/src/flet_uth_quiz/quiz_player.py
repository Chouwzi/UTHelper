"""Python facade for the native Moodle quiz WebView."""

import asyncio
from dataclasses import dataclass
import json
import re
from typing import Optional
from urllib.parse import urlsplit

import flet as ft
from flet.controls.base_control import control
from flet.controls.layout_control import LayoutControl
from flet.controls.services.service import Service

_SESSION_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_UNSAFE_URL_RE = re.compile(r"[\x00-\x20\x7f\\]")
_RAW_AUTHORITY_RE = re.compile(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", re.IGNORECASE)
_COOKIE_FIELDS = frozenset(
    {
        "name",
        "value",
        "domain",
        "path",
        "secure",
        "http_only",
        "same_site",
        "expires",
    }
)
_SESSION_PAYLOAD_LIMIT = 16 * 1024


@control("UthQuizPlayer", kw_only=True)
class UthQuizPlayer(LayoutControl):
    """Render a Moodle quiz in a platform WebView.

    ``session_key`` must be a SHA-256 hex digest for the pair of Moodle site and
    account identities. It is used only to select an isolated native WebView
    profile and is never sent back in events.

    Event payloads are short codes only. They never include a URL, page text,
    question, answer, exception detail, or quiz data.
    """

    url: str
    session_key: str
    require_session: bool = False
    expand: bool | int | None = True
    on_ready: Optional[ft.ControlEventHandler["UthQuizPlayer"]] = None
    on_close: Optional[ft.ControlEventHandler["UthQuizPlayer"]] = None
    on_error: Optional[ft.ControlEventHandler["UthQuizPlayer"]] = None
    on_external_link: Optional[ft.ControlEventHandler["UthQuizPlayer"]] = None
    on_session_expired: Optional[ft.ControlEventHandler["UthQuizPlayer"]] = None
    on_data_channel_open: Optional[ft.EventHandler[ft.DataChannelOpenEvent]] = None

    def __post_init__(self, ref: object | None = None) -> None:
        super().__post_init__(ref)
        if not self.url or _UNSAFE_URL_RE.search(self.url):
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        try:
            parsed = urlsplit(self.url)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("url must be an absolute HTTP or HTTPS URL") from exc
        raw_authority_match = _RAW_AUTHORITY_RE.match(self.url)
        raw_authority = raw_authority_match.group(1) if raw_authority_match else ""
        allowed_schemes = {"https"} if self.require_session else {"http", "https"}
        if (
            parsed.scheme.lower() not in allowed_schemes
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or not raw_authority
            or "@" in raw_authority
            or ":" in raw_authority
        ):
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        if not _SESSION_KEY_RE.fullmatch(self.session_key):
            raise ValueError("session_key must be a SHA-256 hex digest")
        self._session_channel: ft.DataChannel | None = None
        self._session_channel_id: int | None = None
        self._session_channel_ready = asyncio.Event()
        self.on_data_channel_open = self._on_data_channel_open

    def _on_data_channel_open(self, event: ft.DataChannelOpenEvent) -> None:
        if event.channel_name != "quiz_session":
            return
        self._session_channel_id = event.channel_id
        self._session_channel = self.get_data_channel(event.channel_id)
        self._session_channel_ready.set()

    async def bootstrap_session(
        self, cookies: list[dict], launch_url: str | None = None
    ) -> bool:
        """Install a one-shot saved Moodle session without storing its cookies.

        Cookies and an optional direct attempt URL travel over Flet's DataChannel.
        Invoke-method arguments contain only the channel ID so Flet diagnostics
        cannot print cookie values or the attempt URL.
        """
        if (
            not self.require_session
            or not isinstance(cookies, list)
            or not cookies
            or (launch_url is not None and not isinstance(launch_url, str))
            or (launch_url is not None and len(launch_url) > 2048)
        ):
            raise RuntimeError("session_unavailable") from None
        try:
            if len(cookies) > 32 or any(
                not isinstance(cookie, dict)
                or frozenset(cookie) != _COOKIE_FIELDS
                for cookie in cookies
            ):
                raise ValueError
            payload = json.dumps(
                {"cookies": cookies, "launch_url": launch_url},
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("utf-8")
            if len(payload) > _SESSION_PAYLOAD_LIMIT:
                raise ValueError
            await asyncio.wait_for(self._session_channel_ready.wait(), timeout=5)
            channel = self._session_channel
            channel_id = self._session_channel_id
            if channel is None or channel_id is None:
                raise ValueError
            channel.send(payload)
            result = await self._invoke_method(
                "bootstrap_session", {"channel_id": channel_id}, timeout=15
            )
            if result is not True:
                raise ValueError
            return True
        except Exception:
            raise RuntimeError("session_unavailable") from None


@control("UthQuizSessionCleaner")
@dataclass
class UthQuizSessionCleaner(Service):
    """Purges persisted WebView quiz profiles and shared mobile stores."""

    async def clear_sessions(self) -> bool:
        try:
            return bool(await self._invoke_method("clear_sessions", timeout=10))
        except Exception:
            return False
