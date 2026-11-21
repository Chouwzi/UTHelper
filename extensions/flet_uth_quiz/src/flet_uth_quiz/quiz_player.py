"""Python facade for the native Moodle quiz WebView."""

import asyncio
import json
import re
from typing import Optional
from urllib.parse import urlsplit

import flet as ft
from flet.controls.base_control import control
from flet.controls.layout_control import LayoutControl

_SESSION_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
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
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
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
