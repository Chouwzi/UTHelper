"""
Flet Compatibility Shim for Android.

The Android bundle of Flet (via serious_python) may be a different
version or have a stripped-down module layout compared to the desktop
pip package.  This module patches missing convenience helpers so the
rest of the codebase can use them unconditionally.

Call ``patch_flet()`` once during startup, **before** any GUI modules
are imported.
"""

import flet as ft
import logging

logger = logging.getLogger(__name__)


def patch_flet():
    """Add missing convenience functions to the ``flet`` namespace."""
    _patched = []

    # ── ft.border.all ──────────────────────────────────────────────
    border_mod = getattr(ft, "border", None)
    if border_mod is None:
        # Create a fake border module
        import types
        border_mod = types.ModuleType("flet.border")
        ft.border = border_mod
        _patched.append("ft.border (module)")

    if not hasattr(border_mod, "all"):
        def _border_all(width=1, color=None):
            side = ft.BorderSide(width, color)
            return ft.Border(left=side, top=side, right=side, bottom=side)
        border_mod.all = _border_all
        _patched.append("ft.border.all")

    if not hasattr(border_mod, "only"):
        def _border_only(left=None, top=None, right=None, bottom=None):
            return ft.Border(
                left=left or ft.BorderSide(0),
                top=top or ft.BorderSide(0),
                right=right or ft.BorderSide(0),
                bottom=bottom or ft.BorderSide(0),
            )
        border_mod.only = _border_only
        _patched.append("ft.border.only")

    # ── ft.Padding.symmetric ──────────────────────────────────────
    _Padding = getattr(ft, "Padding", None)
    if _Padding is not None and not hasattr(_Padding, "symmetric"):
        @classmethod
        def _padding_symmetric(cls, horizontal=0, vertical=0):
            return cls(left=horizontal, top=vertical, right=horizontal, bottom=vertical)
        _Padding.symmetric = _padding_symmetric
        _patched.append("ft.Padding.symmetric")
    elif _Padding is None:
        # Padding class itself may be missing; alias to padding module
        pass

    # ── ft.margin.only ────────────────────────────────────────────
    margin_mod = getattr(ft, "margin", None)
    if margin_mod is not None and not hasattr(margin_mod, "only"):
        def _margin_only(left=0, top=0, right=0, bottom=0):
            return ft.Margin(left=left, top=top, right=right, bottom=bottom)
        margin_mod.only = _margin_only
        _patched.append("ft.margin.only")

    # ── ft.margin.all ─────────────────────────────────────────────
    if margin_mod is not None and not hasattr(margin_mod, "all"):
        def _margin_all(value):
            return ft.Margin(left=value, top=value, right=value, bottom=value)
        margin_mod.all = _margin_all
        _patched.append("ft.margin.all")

    # ── ft.padding.all / ft.padding.only ──────────────────────────
    padding_mod = getattr(ft, "padding", None)
    if padding_mod is not None and not hasattr(padding_mod, "all"):
        def _padding_all(value):
            return ft.Padding(left=value, top=value, right=value, bottom=value)
        padding_mod.all = _padding_all
        _patched.append("ft.padding.all")

    if padding_mod is not None and not hasattr(padding_mod, "only"):
        def _padding_only(left=0, top=0, right=0, bottom=0):
            return ft.Padding(left=left, top=top, right=right, bottom=bottom)
        padding_mod.only = _padding_only
        _patched.append("ft.padding.only")

    # ── ft.Border.only (class method) ─────────────────────────────
    _Border = getattr(ft, "Border", None)
    if _Border is not None and not hasattr(_Border, "only"):
        @classmethod
        def _border_cls_only(cls, left=None, top=None, right=None, bottom=None):
            return cls(
                left=left or ft.BorderSide(0),
                top=top or ft.BorderSide(0),
                right=right or ft.BorderSide(0),
                bottom=bottom or ft.BorderSide(0),
            )
        _Border.only = _border_cls_only
        _patched.append("ft.Border.only")

    if _patched:
        logger.info("Flet compat: patched %s", ", ".join(_patched))
    else:
        logger.info("Flet compat: all APIs present, no patching needed")
