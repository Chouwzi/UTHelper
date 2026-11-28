"""Shared, order-independent stubs for the Learning Roadmap tests.

Rules (so the suite behaves the same locally and in CI, in any collection order):

* The REAL ``config`` / ``flet`` are always preferred. A stub is installed only
  when the real package cannot be imported (e.g. a minimal CI image without
  pydantic or flet), never on top of an already-imported real module.
* ``tests/conftest.py`` puts ``src/`` on ``sys.path``; nothing here touches it.
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path


def ensure_config() -> None:
    """Make ``import config`` work (settings + get_data_dir only)."""
    if "config" in sys.modules:
        return
    try:
        import config  # noqa: F401  (real module wins)
        return
    except Exception:
        pass
    cfg = types.ModuleType("config")
    cfg.settings = types.SimpleNamespace(
        PORTAL_API_BASE="https://portal.test/api/v1",
        UTH_USERNAME="", UTH_PASSWORD="", MOODLE_USERNAME="", MOODLE_PASSWORD="",
    )
    cfg.get_data_dir = lambda: Path(tempfile.gettempdir())
    sys.modules["config"] = cfg


class _Meta(type):
    def __getattr__(cls, name):            # ft.Border.all(...), ft.Icons.X, ft.FontWeight.W_700
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: Ctl(*a, **k)


class Ctl(metaclass=_Meta):
    """Permissive stand-in for any Flet control."""

    def __init__(self, *args, **kwargs):
        self._args = args
        self.__dict__.update(kwargs)
        self.updates = 0

    def update(self):
        self.updates += 1


def ensure_flet() -> None:
    """Make ``import flet as ft`` work for headless view tests."""
    if "flet" in sys.modules:
        return
    try:
        import flet  # noqa: F401  (real module wins)
        return
    except Exception:
        pass
    mod = types.ModuleType("flet")
    mod.__getattr__ = lambda name: Ctl if not name.startswith("__") else (_ for _ in ()).throw(AttributeError(name))
    mod.Container = type("Container", (Ctl,), {})

    class Text(Ctl):                       # real ft.Text takes `value` first
        def __init__(self, value=None, **kwargs):
            super().__init__(**kwargs)
            self.value = value

    mod.Text = Text
    mod.Page = Ctl
    mod.Control = Ctl
    sys.modules["flet"] = mod
