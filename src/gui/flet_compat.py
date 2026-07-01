"""
Flet Compatibility Shim for Android.

The Android bundle of Flet (via serious_python) may be a different
version or have a stripped-down module layout compared to the desktop
pip package.  This module patches missing convenience helpers so the
rest of the codebase can use them unconditionally.

Call ``patch_flet()`` once during startup, **before** any GUI modules
are imported.

Patched APIs (exhaustive list based on codebase audit):
- ft.border.all(width, color)         - module-level helper
- ft.border.only(left, top, ...)      - module-level helper
- ft.Border.only(left, top, ...)      - class method
- ft.Padding.symmetric(h, v)          - class method
- ft.Padding.only(left, top, ...)     - class method
- ft.padding.symmetric(h, v)          - module-level helper
- ft.padding.all(value)               - module-level helper
- ft.padding.only(left, top, ...)     - module-level helper
- ft.margin.only(left, top, ...)      - module-level helper
- ft.margin.all(value)                - module-level helper
- ft.BorderRadius.only(tl, tr, ...)   - class method
"""

import flet as ft
import logging

logger = logging.getLogger(__name__)


def patch_flet():
    """Add missing convenience functions to the ``flet`` namespace."""
    _patched = []

    # ══════════════════════════════════════════════════════════════
    #  BORDER
    # ══════════════════════════════════════════════════════════════

    # ft.border module
    border_mod = getattr(ft, "border", None)
    if border_mod is None:
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

    # ft.Border.only (class method)
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

    # ══════════════════════════════════════════════════════════════
    #  BORDER RADIUS
    # ══════════════════════════════════════════════════════════════

    _BorderRadius = getattr(ft, "BorderRadius", None)
    if _BorderRadius is not None and not hasattr(_BorderRadius, "only"):
        @classmethod
        def _br_only(cls, top_left=0, top_right=0, bottom_left=0, bottom_right=0):
            return cls(
                top_left=top_left,
                top_right=top_right,
                bottom_left=bottom_left,
                bottom_right=bottom_right,
            )
        _BorderRadius.only = _br_only
        _patched.append("ft.BorderRadius.only")

    # ══════════════════════════════════════════════════════════════
    #  PADDING (both class ft.Padding and module ft.padding)
    # ══════════════════════════════════════════════════════════════

    # ft.Padding class methods
    _Padding = getattr(ft, "Padding", None)
    if _Padding is not None:
        if not hasattr(_Padding, "symmetric"):
            @classmethod
            def _pad_sym(cls, horizontal=0, vertical=0):
                return cls(left=horizontal, top=vertical, right=horizontal, bottom=vertical)
            _Padding.symmetric = _pad_sym
            _patched.append("ft.Padding.symmetric")

        if not hasattr(_Padding, "only"):
            @classmethod
            def _pad_only(cls, left=0, top=0, right=0, bottom=0):
                return cls(left=left, top=top, right=right, bottom=bottom)
            _Padding.only = _pad_only
            _patched.append("ft.Padding.only")

    # ft.padding module helpers
    padding_mod = getattr(ft, "padding", None)
    if padding_mod is None:
        import types
        padding_mod = types.ModuleType("flet.padding")
        ft.padding = padding_mod
        _patched.append("ft.padding (module)")

    if not hasattr(padding_mod, "all"):
        def _padding_all(value):
            return ft.Padding(left=value, top=value, right=value, bottom=value)
        padding_mod.all = _padding_all
        _patched.append("ft.padding.all")

    if not hasattr(padding_mod, "only"):
        def _padding_only(left=0, top=0, right=0, bottom=0):
            return ft.Padding(left=left, top=top, right=right, bottom=bottom)
        padding_mod.only = _padding_only
        _patched.append("ft.padding.only")

    if not hasattr(padding_mod, "symmetric"):
        def _padding_symmetric(horizontal=0, vertical=0):
            return ft.Padding(left=horizontal, top=vertical, right=horizontal, bottom=vertical)
        padding_mod.symmetric = _padding_symmetric
        _patched.append("ft.padding.symmetric")

    # ══════════════════════════════════════════════════════════════
    #  MARGIN (module ft.margin)
    # ══════════════════════════════════════════════════════════════

    margin_mod = getattr(ft, "margin", None)
    if margin_mod is None:
        import types
        margin_mod = types.ModuleType("flet.margin")
        ft.margin = margin_mod
        _patched.append("ft.margin (module)")

    if not hasattr(margin_mod, "only"):
        def _margin_only(left=0, top=0, right=0, bottom=0):
            return ft.Margin(left=left, top=top, right=right, bottom=bottom)
        margin_mod.only = _margin_only
        _patched.append("ft.margin.only")

    if not hasattr(margin_mod, "all"):
        def _margin_all(value):
            return ft.Margin(left=value, top=value, right=value, bottom=value)
        margin_mod.all = _margin_all
        _patched.append("ft.margin.all")

    if not hasattr(margin_mod, "symmetric"):
        def _margin_symmetric(horizontal=0, vertical=0):
            return ft.Margin(left=horizontal, top=vertical, right=horizontal, bottom=vertical)
        margin_mod.symmetric = _margin_symmetric
        _patched.append("ft.margin.symmetric")

    # ══════════════════════════════════════════════════════════════
    #  DROPDOWN (module ft.dropdown)
    # ══════════════════════════════════════════════════════════════

    dropdown_mod = getattr(ft, "dropdown", None)
    if dropdown_mod is None:
        import types
        dropdown_mod = types.ModuleType("flet.dropdown")
        ft.dropdown = dropdown_mod
        _patched.append("ft.dropdown (module)")

    if not hasattr(dropdown_mod, "Option"):
        # ft.dropdown.Option is an alias for ft.Option in newer Flet
        _Option = getattr(ft, "Option", None)
        if _Option is not None:
            dropdown_mod.Option = _Option
            _patched.append("ft.dropdown.Option -> ft.Option")
        else:
            # Create a minimal Option class
            class _DropdownOption:
                def __init__(self, key=None, text=None, **kwargs):
                    self.key = key
                    self.text = text or key
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            dropdown_mod.Option = _DropdownOption
            _patched.append("ft.dropdown.Option (polyfill)")

    # ══════════════════════════════════════════════════════════════
    #  COLORS (ft.Colors.with_opacity)
    # ══════════════════════════════════════════════════════════════

    _Colors = getattr(ft, "Colors", None)
    if _Colors is not None and not hasattr(_Colors, "with_opacity"):
        @staticmethod
        def _with_opacity(opacity, color):
            # Convert opacity (0-1) to hex alpha (00-FF)
            alpha_hex = format(int(opacity * 255), "02x")
            # If color starts with #, insert alpha after #
            if isinstance(color, str) and color.startswith("#"):
                if len(color) == 7:  # #RRGGBB
                    return f"#{alpha_hex}{color[1:]}"
                return color
            return color
        _Colors.with_opacity = _with_opacity
        _patched.append("ft.Colors.with_opacity")

    # ══════════════════════════════════════════════════════════════
    #  LOGGING
    # ══════════════════════════════════════════════════════════════

    if _patched:
        logger.info("Flet compat: patched %d APIs: %s", len(_patched), ", ".join(_patched))
    else:
        logger.info("Flet compat: all APIs present, no patching needed")

    return _patched
