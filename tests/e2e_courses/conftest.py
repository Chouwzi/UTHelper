"""Shared fixtures, FakePage, and Flet control tree assertions for E2E Courses tests."""

from __future__ import annotations

import asyncio
import dataclasses
import http.client
import inspect
import math
import os
import types as py_types
import typing
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlsplit

import flet as ft
import pytest

import platform_utils
from core.courses.local_preview_server import LocalPreviewServer
from gui.core.theme import apply_theme

_PLATFORM_FLAG_NAMES: tuple[str, ...] = (
    "IS_WINDOWS",
    "IS_ANDROID",
    "IS_IOS",
    "IS_MOBILE",
    "IS_DESKTOP",
    "UI_PLATFORM_OVERRIDE",
    "UI_PLATFORM",
    "UI_IS_ANDROID",
    "UI_IS_IOS",
    "UI_IS_MOBILE",
    "UI_IS_DESKTOP",
)

_SINGLE_CHILD_ATTRS: tuple[str, ...] = (
    "content",
    "title",
    "subtitle",
    "leading",
    "trailing",
    "label",
    "bar_leading",
)

_SEQUENCE_CHILD_ATTRS: tuple[str, ...] = (
    "controls",
    "tabs",
    "actions",
    "columns",
    "rows",
    "cells",
    "leading_controls",
    "trailing_controls",
    "items",
    "destinations",
    "segments",
    "options",
    "bar_trailing",
)


class FakeView:
    """Minimal Flet View stub supporting Android back confirmation."""

    def __init__(self, *, can_pop: bool = True, on_confirm_pop: Any = None) -> None:
        self.can_pop = can_pop
        self.on_confirm_pop = on_confirm_pop
        self.confirmed_pops: list[bool] = []

    async def confirm_pop(self, allow: bool) -> None:
        self.confirmed_pops.append(bool(allow))


class FakeWindow:
    """Minimal Flet window stub synced with FakePage dimensions."""

    def __init__(self, width: float = 1120.0, height: float = 760.0) -> None:
        self.width = width
        self.height = height
        self.visible = True
        self.skip_task_bar = False
        self.on_event: Any = None


class FakePage:
    """Headless Flet Page double supporting resize, views, dialogs, overlay, and run_task."""

    def __init__(
        self,
        *,
        width: float = 1120.0,
        height: float = 760.0,
        platform: Any = ft.PagePlatform.WINDOWS,
        web: bool = False,
        eager_tasks: bool = True,
    ) -> None:
        self.width: float = float(width)
        self.height: float = float(height)
        self.platform: Any = platform
        self.web: bool = bool(web)
        self.eager_tasks: bool = bool(eager_tasks)
        self.title: str = "UTHelper"
        self.overlay: list[Any] = []
        self.dialogs: list[Any] = []
        self.views: list[FakeView] = [FakeView()]
        self.window = FakeWindow(width=self.width, height=self.height)
        self.on_resize: Any = None
        self.on_keyboard_event: Any = None
        self.on_view_pop: Any = None
        self.on_disconnect: Any = None
        self.snack_bar: Any = None
        self.update_count: int = 0
        self.scheduled_tasks: list[tuple[Any, tuple[Any, ...], dict[str, Any]]] = []
        self.launched_urls: list[str] = []

    def update(self) -> None:
        self.update_count += 1

    def run_task(self, handler: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        self.scheduled_tasks.append((handler, args, kwargs))
        if not self.eager_tasks:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if inspect.iscoroutinefunction(handler):
            coro = handler(*args, **kwargs)
            if loop is not None:
                return loop.create_task(coro)
            return asyncio.run(coro)
        result = handler(*args, **kwargs)
        if inspect.isawaitable(result):
            if loop is not None:
                return loop.create_task(result)  # type: ignore[arg-type]
            return asyncio.run(result)  # type: ignore[arg-type]
        return result

    def show_dialog(self, dialog: Any) -> None:
        if hasattr(dialog, "open"):
            dialog.open = True
        if dialog not in self.dialogs:
            self.dialogs.append(dialog)
        self.update()

    def pop_dialog(self) -> Any:
        if not self.dialogs:
            return None
        dlg = self.dialogs.pop()
        if hasattr(dlg, "open"):
            dlg.open = False
        self.update()
        return dlg

    def launch_url(self, url: str) -> None:
        self.launched_urls.append(str(url))

    def resize(self, width: float, height: float) -> None:
        self.width = float(width)
        self.height = float(height)
        self.window.width = self.width
        self.window.height = self.height
        if callable(self.on_resize):
            self.on_resize(
                py_types.SimpleNamespace(control=self, width=self.width, height=self.height)
            )


def bind_control_page(monkeypatch: pytest.MonkeyPatch, cls: type[Any], page: Any) -> None:
    """Monkeypatch read-only ``Control.page`` property on an unmounted Flet control class."""
    monkeypatch.setattr(cls, "page", property(lambda _self: page))


def set_platform_profile(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    *,
    page: Optional[Any] = None,
    is_windows: Optional[bool] = None,
) -> None:
    """Configure runtime and UI platform flags consistently for a test scenario."""
    norm = str(profile).strip().lower()
    if norm not in {"android", "ios", "desktop"}:
        raise ValueError(f"Unsupported platform profile: {profile!r}")
    monkeypatch.setenv("UTHELPER_UI_PLATFORM", norm)
    monkeypatch.setattr(platform_utils, "IS_ANDROID", norm == "android")
    monkeypatch.setattr(platform_utils, "IS_IOS", norm == "ios")
    monkeypatch.setattr(platform_utils, "IS_MOBILE", norm in {"android", "ios"})
    monkeypatch.setattr(platform_utils, "IS_DESKTOP", norm == "desktop")
    if is_windows is None:
        is_windows = norm == "desktop"
    monkeypatch.setattr(platform_utils, "IS_WINDOWS", bool(is_windows))
    if page is not None:
        if norm == "android":
            page.platform = ft.PagePlatform.ANDROID
        elif norm == "ios":
            page.platform = ft.PagePlatform.IOS
        else:
            page.platform = ft.PagePlatform.WINDOWS
    platform_utils.detect_platform(page)


@pytest.fixture(autouse=True)
def theme_restore() -> Iterator[None]:
    """Restore default Midnight Blue theme after every test."""
    yield
    apply_theme("midnight_blue")


@pytest.fixture(autouse=True)
def platform_restore() -> Iterator[None]:
    """Snapshot and restore all 11 platform_utils flags and UTHELPER_UI_PLATFORM."""
    snapshot = {name: getattr(platform_utils, name) for name in _PLATFORM_FLAG_NAMES}
    prev_env = os.environ.get("UTHELPER_UI_PLATFORM")
    try:
        yield
    finally:
        if prev_env is None:
            os.environ.pop("UTHELPER_UI_PLATFORM", None)
        else:
            os.environ["UTHELPER_UI_PLATFORM"] = prev_env
        for name, value in snapshot.items():
            setattr(platform_utils, name, value)


@pytest.fixture
def fake_page() -> FakePage:
    """Provide a fresh headless FakePage instance."""
    return FakePage()


@pytest.fixture
def platform_profile(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Helper fixture to switch platform_utils + page platform flags safely."""
    def _apply(
        profile: str,
        *,
        page: Optional[Any] = None,
        is_windows: Optional[bool] = None,
    ) -> None:
        set_platform_profile(monkeypatch, profile, page=page, is_windows=is_windows)

    return _apply


@pytest.fixture
def preview_server() -> Iterator[LocalPreviewServer]:
    """Provide a running LocalPreviewServer and guarantee clean shutdown."""
    srv = LocalPreviewServer()
    try:
        yield srv
    finally:
        srv.stop()


def walk_controls(root: Any) -> Iterator[ft.BaseControl]:
    """Recursively traverse Flet 0.86.5 BaseControl trees without duplicates."""
    seen: set[int] = set()

    def _visit(node: Any) -> Iterator[ft.BaseControl]:
        if isinstance(node, (list, tuple)):
            for item in node:
                yield from _visit(item)
            return
        if not isinstance(node, ft.BaseControl):
            return
        obj_id = id(node)
        if obj_id in seen:
            return
        seen.add(obj_id)
        yield node
        for attr in _SINGLE_CHILD_ATTRS:
            child = getattr(node, attr, None)
            if isinstance(child, ft.BaseControl):
                yield from _visit(child)
        for attr in _SEQUENCE_CHILD_ATTRS:
            children = getattr(node, attr, None)
            if isinstance(children, (list, tuple)):
                for child in children:
                    yield from _visit(child)

    yield from _visit(root)


def collect_texts(root: Any) -> list[str]:
    """Extract user-visible strings from Flet text, markdown, chip, button, input, and tooltip controls."""
    texts: list[str] = []
    button_types = (ft.Button, ft.OutlinedButton, ft.TextButton, ft.FilledButton)

    for ctrl in walk_controls(root):
        if isinstance(ctrl, (ft.Text, ft.Markdown)):
            val = getattr(ctrl, "value", None)
            if isinstance(val, str) and val:
                texts.append(val)
        elif isinstance(ctrl, ft.Chip):
            lbl = getattr(ctrl, "label", None)
            if isinstance(lbl, str) and lbl:
                texts.append(lbl)
        elif isinstance(ctrl, button_types):
            content = getattr(ctrl, "content", None)
            if isinstance(content, str) and content:
                texts.append(content)
        elif isinstance(ctrl, ft.TextField):
            for attr in ("label", "hint_text", "value"):
                val = getattr(ctrl, attr, None)
                if isinstance(val, str) and val:
                    texts.append(val)
        elif isinstance(ctrl, ft.Tooltip):
            msg = getattr(ctrl, "message", None)
            if isinstance(msg, str) and msg:
                texts.append(msg)

        tooltip = getattr(ctrl, "tooltip", None)
        if isinstance(tooltip, str) and tooltip:
            texts.append(tooltip)
        elif isinstance(tooltip, ft.Tooltip):
            msg = getattr(tooltip, "message", None)
            if isinstance(msg, str) and msg:
                texts.append(msg)

    return texts


def _mro_annotations(cls: type[Any]) -> dict[str, Any]:
    anns: dict[str, Any] = {}
    for base in reversed(cls.__mro__):
        anns.update(getattr(base, "__annotations__", {}))
    return anns


def _allows_none(tp: Any) -> bool:
    if tp is typing.Any or tp is type(None) or isinstance(tp, (str, typing.ForwardRef)):
        return True
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _allows_none(typing.get_args(tp)[0])
    if origin in (typing.Union, py_types.UnionType):
        return any(_allows_none(arg) for arg in typing.get_args(tp))
    return False


def _matches_mro_type(val: Any, tp: Any) -> bool:
    if tp is typing.Any or isinstance(tp, (str, typing.ForwardRef)):
        return True
    if val is None:
        return _allows_none(tp)
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _matches_mro_type(val, typing.get_args(tp)[0])
    if origin in (typing.Union, py_types.UnionType):
        return any(_matches_mro_type(val, arg) for arg in typing.get_args(tp))
    if origin in (list, tuple, dict, set):
        return isinstance(val, origin)
    if str(origin).endswith("Callable") or tp is callable:
        return callable(val)
    if isinstance(tp, type):
        if tp is float and isinstance(val, int) and not isinstance(val, bool):
            return True
        if tp is int and isinstance(val, bool):
            return False
        return isinstance(val, tp)
    return True


def assert_valid_flet_mro(root: Any) -> None:
    """Verify every reachable Flet control satisfies Flet 0.86.5 MRO dataclass field types."""
    mismatches: list[tuple[str, Any, Any]] = []
    visited: set[int] = set()

    def _walk_fields(obj: Any, path: str) -> None:
        if isinstance(obj, ft.BaseControl):
            obj_id = id(obj)
            if obj_id in visited:
                return
            visited.add(obj_id)
            cls = type(obj)
            if isinstance(obj, ft.AnimatedSwitcher) and obj.content is None:
                mismatches.append((f"{path}.AnimatedSwitcher.content", None, "Control (non-None)"))
            hints = _mro_annotations(cls)
            for f in dataclasses.fields(obj):
                if f.name.startswith("_"):
                    continue
                val = getattr(obj, f.name, None)
                if f.name in hints and not _matches_mro_type(val, hints[f.name]):
                    mismatches.append((f"{path}.{cls.__name__}.{f.name}", type(val) if val is not None else None, hints[f.name]))
                if val is not None:
                    _walk_fields(val, f"{path}.{cls.__name__}.{f.name}")
        elif isinstance(obj, (list, tuple)):
            for idx, item in enumerate(obj):
                _walk_fields(item, f"{path}[{idx}]")
        elif isinstance(obj, dict):
            for k, item in obj.items():
                _walk_fields(item, f"{path}[{k!r}]")

    _walk_fields(root, "root")
    assert not mismatches, "Flet MRO field type mismatches:\n" + "\n".join(
        f"  - {p}: got {got!r}, expected {exp!r}" for p, got, exp in mismatches
    )


def assert_no_fixed_width_overflow(
    root: Any,
    max_width: float,
    *,
    allow_horizontal_scroll_descendants: bool = False,
) -> None:
    """Fail if any visible control has an explicit numeric ``width`` exceeding ``max_width``."""
    violations: list[tuple[str, str, float]] = []
    visited: set[int] = set()

    def _check(node: Any, path: str, in_h_scroll: bool) -> None:
        if isinstance(node, (list, tuple)):
            for idx, item in enumerate(node):
                _check(item, f"{path}[{idx}]", in_h_scroll)
            return
        if not isinstance(node, ft.BaseControl):
            return
        if id(node) in visited:
            return
        visited.add(id(node))

        if getattr(node, "visible", True) is False:
            return

        cls_name = type(node).__name__
        node_path = f"{path}.{cls_name}"
        raw_w = getattr(node, "width", None)
        if isinstance(raw_w, (int, float)) and not isinstance(raw_w, bool):
            w_val = float(raw_w)
            if not math.isfinite(w_val) or (
                not (allow_horizontal_scroll_descendants and in_h_scroll)
                and w_val > float(max_width)
            ):
                violations.append((node_path, cls_name, w_val))

        child_h_scroll = in_h_scroll or (
            isinstance(node, ft.Row) and getattr(node, "scroll", None) is not None
        ) or isinstance(node, ft.InteractiveViewer)

        for attr in _SINGLE_CHILD_ATTRS:
            child = getattr(node, attr, None)
            if isinstance(child, ft.BaseControl):
                _check(child, f"{node_path}.{attr}", child_h_scroll)
        for attr in _SEQUENCE_CHILD_ATTRS:
            children = getattr(node, attr, None)
            if isinstance(children, (list, tuple)):
                for idx, child in enumerate(children):
                    _check(child, f"{node_path}.{attr}[{idx}]", child_h_scroll)

    _check(root, "root", False)
    assert not violations, (
        f"Fixed-width overflow (> {max_width}dp) detected on visible controls:\n"
        + "\n".join(f"  - {p} ({cls}): width={w}" for p, cls, w in violations)
    )


assert_no_fixed_overflow_widths = assert_no_fixed_width_overflow


def _extract_padding_sides(padding: Any) -> Optional[tuple[float, float, float, float]]:
    if padding is None:
        return None
    if isinstance(padding, (int, float)) and not isinstance(padding, bool):
        v = float(padding)
        return (v, v, v, v)
    if isinstance(padding, dict):
        # Flet ControlStateValue mapping
        for state_key in (ft.ControlState.DEFAULT, "default", None):
            if state_key in padding:
                return _extract_padding_sides(padding[state_key])
        if padding:
            return _extract_padding_sides(next(iter(padding.values())))
        return None
    left = getattr(padding, "left", None)
    top = getattr(padding, "top", None)
    right = getattr(padding, "right", None)
    bottom = getattr(padding, "bottom", None)
    if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (left, top, right, bottom)):
        return (float(left), float(top), float(right), float(bottom))
    return None


def _estimate_touch_inner_size(ctrl: ft.BaseControl) -> tuple[float, float]:
    """Estimate intrinsic content (width, height) when a control omits explicit width/height."""
    if isinstance(ctrl, ft.IconButton) or (
        isinstance(ctrl, ft.PopupMenuButton)
        and getattr(ctrl, "icon", None) is not None
        and getattr(ctrl, "content", None) is None
    ):
        icon_size_raw = getattr(ctrl, "icon_size", None)
        icon_sz = (
            float(icon_size_raw)
            if isinstance(icon_size_raw, (int, float)) and not isinstance(icon_size_raw, bool)
            else 24.0
        )
        return (icon_sz, icon_sz)

    content = getattr(ctrl, "content", None)
    if isinstance(content, ft.Icon):
        sz = getattr(content, "size", None)
        s = float(sz) if isinstance(sz, (int, float)) and not isinstance(sz, bool) else 24.0
        return (s, s)
    if isinstance(content, ft.Text):
        cw = getattr(content, "width", None)
        ch = getattr(content, "height", None)
        tsize_raw = getattr(content, "size", None)
        tsize = (
            float(tsize_raw)
            if isinstance(tsize_raw, (int, float)) and not isinstance(tsize_raw, bool)
            else 14.0
        )
        tval = str(getattr(content, "value", "") or "")
        ew = (
            float(cw)
            if isinstance(cw, (int, float)) and not isinstance(cw, bool)
            else max(8.0, len(tval) * tsize * 0.6)
        )
        eh = (
            float(ch)
            if isinstance(ch, (int, float)) and not isinstance(ch, bool)
            else round(tsize * 1.4, 1)
        )
        return (ew, eh)
    if isinstance(content, ft.BaseControl):
        cw = getattr(content, "width", None)
        ch = getattr(content, "height", None)
        c_cons = getattr(content, "size_constraints", None)
        c_min_w = getattr(c_cons, "min_width", None) if c_cons is not None else None
        c_min_h = getattr(c_cons, "min_height", None) if c_cons is not None else None
        ew = (
            float(cw)
            if isinstance(cw, (int, float)) and not isinstance(cw, bool)
            else (
                float(c_min_w)
                if isinstance(c_min_w, (int, float)) and not isinstance(c_min_w, bool)
                else 0.0
            )
        )
        eh = (
            float(ch)
            if isinstance(ch, (int, float)) and not isinstance(ch, bool)
            else (
                float(c_min_h)
                if isinstance(c_min_h, (int, float)) and not isinstance(c_min_h, bool)
                else 0.0
            )
        )
        return (ew, eh)
    if isinstance(content, str):
        return (max(8.0, len(content) * 8.0), 20.0)
    return (0.0, 0.0)


def assert_min_touch_target(ctrl: ft.BaseControl, min_dp: float = 44.0) -> None:
    """Verify an interactive control meets minimum effective touch box (>= min_dp)."""
    raw_w = getattr(ctrl, "width", None)
    raw_h = getattr(ctrl, "height", None)
    constraints = getattr(ctrl, "size_constraints", None)
    min_c_w = getattr(constraints, "min_width", None) if constraints is not None else None
    min_c_h = getattr(constraints, "min_height", None) if constraints is not None else None

    style = getattr(ctrl, "style", None)
    style_pad = _extract_padding_sides(getattr(style, "padding", None)) if style is not None else None
    ctrl_pad = _extract_padding_sides(getattr(ctrl, "padding", None))
    pad = style_pad if style_pad is not None else ctrl_pad

    icon_size_raw = getattr(ctrl, "icon_size", None)
    inner_w, inner_h = _estimate_touch_inner_size(ctrl)
    is_material_icon_target = isinstance(ctrl, ft.IconButton) or (
        isinstance(ctrl, ft.PopupMenuButton)
        and getattr(ctrl, "icon", None) is not None
        and getattr(ctrl, "content", None) is None
    )

    if isinstance(raw_w, (int, float)) and not isinstance(raw_w, bool):
        eff_w = float(raw_w)
    elif isinstance(min_c_w, (int, float)) and not isinstance(min_c_w, bool):
        eff_w = float(min_c_w)
    elif pad is not None:
        eff_w = inner_w + pad[0] + pad[2]
    elif is_material_icon_target:
        eff_w = 40.0 if getattr(ctrl, "visual_density", None) == ft.VisualDensity.COMPACT else 48.0
    else:
        eff_w = inner_w

    if isinstance(raw_h, (int, float)) and not isinstance(raw_h, bool):
        eff_h = float(raw_h)
    elif isinstance(min_c_h, (int, float)) and not isinstance(min_c_h, bool):
        eff_h = float(min_c_h)
    elif pad is not None:
        eff_h = inner_h + pad[1] + pad[3]
    elif is_material_icon_target:
        eff_h = 40.0 if getattr(ctrl, "visual_density", None) == ft.VisualDensity.COMPACT else 48.0
    else:
        eff_h = inner_h

    assert eff_w >= min_dp and eff_h >= min_dp, (
        f"Touch target too small on {type(ctrl).__name__} "
        f"(tooltip={getattr(ctrl, 'tooltip', None)!r}): "
        f"effective=({eff_w:.1f}dp x {eff_h:.1f}dp) < min={min_dp:.1f}dp "
        f"[width={raw_w!r}, height={raw_h!r}, icon_size={icon_size_raw!r}, pad={pad!r}]"
    )


def http_request(
    url: str,
    method: str = "GET",
    headers: Optional[dict[str, str]] = None,
) -> tuple[int, dict[str, str], bytes]:
    """Perform a loopback HTTP request and return (status, lower_headers, body_bytes)."""
    parsed = urlsplit(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    target = parsed.path or "/"
    if parsed.query:
        target = f"{target}?{parsed.query}"
    conn = http.client.HTTPConnection(host, port, timeout=5.0)
    try:
        conn.request(method.upper(), target, headers=dict(headers or {}))
        resp = conn.getresponse()
        body = resp.read()
        lower_headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, lower_headers, body
    finally:
        conn.close()
