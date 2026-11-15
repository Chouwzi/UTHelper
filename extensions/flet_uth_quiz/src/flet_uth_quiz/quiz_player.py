"""Python facade for the native Moodle quiz WebView."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlsplit

import flet as ft
from flet.controls.base_control import control
from flet.controls.layout_control import LayoutControl

_SESSION_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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
    expand: bool | int | None = True
    on_ready: Optional[ft.EventHandler[ft.ControlEvent]] = None
    on_close: Optional[ft.EventHandler[ft.ControlEvent]] = None
    on_error: Optional[ft.EventHandler[ft.ControlEvent]] = None
    on_external_link: Optional[ft.EventHandler[ft.ControlEvent]] = None

    def __post_init__(self, ref: object | None = None) -> None:
        super().__post_init__(ref)
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP or HTTPS URL")
        if not _SESSION_KEY_RE.fullmatch(self.session_key):
            raise ValueError("session_key must be a SHA-256 hex digest")
