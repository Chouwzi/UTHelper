"""Headless smoke tests for LearningRoadmapView / ViewManager wiring.

Real Flet is not required: a permissive stub lets *our* code run end to end
(rendering, search, filter chips, theme refresh, offline/empty states). This
verifies our logic and attribute usage, NOT Flet's own API surface.

Run from ``src/``:  python -m unittest tests.test_learning_roadmap_ui -v
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- flet stub
class _Meta(type):
    def __getattr__(cls, name):            # ft.Border.all(...), ft.Icons.X, ft.FontWeight.W_700
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: _Ctl(*a, **k)


class _Ctl(metaclass=_Meta):
    def __init__(self, *args, **kwargs):
        self._args = args
        self.__dict__.update(kwargs)
        self.updates = 0

    def update(self):
        self.updates += 1


def _install_flet_stub():
    mod = types.ModuleType("flet")
    mod.__getattr__ = lambda name: _Ctl if not name.startswith("__") else (_ for _ in ()).throw(AttributeError(name))
    mod.Container = type("Container", (_Ctl,), {})

    class Text(_Ctl):                      # real ft.Text takes `value` first
        def __init__(self, value=None, **kwargs):
            super().__init__(**kwargs)
            self.value = value

    mod.Text = Text
    mod.Page = _Ctl
    mod.Control = _Ctl
    sys.modules["flet"] = mod
    return mod


if "flet" not in sys.modules:
    _install_flet_stub()
if "config" not in sys.modules:
    _cfg = types.ModuleType("config")
    _cfg.settings = types.SimpleNamespace(PORTAL_API_BASE="x", UTH_USERNAME="", UTH_PASSWORD="",
                                          MOODLE_USERNAME="", MOODLE_PASSWORD="")
    _cfg.get_data_dir = lambda: Path("/tmp")
    sys.modules["config"] = _cfg

from core.learning_roadmap.builder import build_snapshot  # noqa: E402
from core.learning_roadmap.service import (  # noqa: E402
    SOURCE_NETWORK, SOURCE_NONE, SOURCE_OFFLINE_CACHE, RoadmapLoadResult,
)
from tests.test_learning_roadmap import bundle  # noqa: E402

from gui.components.learning_roadmap_view import LearningRoadmapView  # noqa: E402
from gui.view_manager import ViewManager  # noqa: E402


def result(source=SOURCE_NETWORK, **bundle_kw):
    snap = build_snapshot(bundle(**bundle_kw))
    return RoadmapLoadResult(snap, source, datetime(2026, 11, 25, 9, 30))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.closed = self.refreshed = 0
        self.view = LearningRoadmapView(
            on_close=lambda: setattr(self, "closed", self.closed + 1),
            on_refresh=lambda: setattr(self, "refreshed", self.refreshed + 1),
        )

    def test_starts_hidden_then_loading_shows_spinner_not_body(self):
        self.assertFalse(self.view.visible)
        self.view.show_loading()
        self.assertTrue(self.view.visible)
        self.assertTrue(self.view._loading_row.visible)
        self.assertFalse(self.view._body.visible)

    def test_result_renders_semesters_requirements_and_chips(self):
        self.view.show_loading()
        self.view.show_result(result())
        v = self.view
        self.assertFalse(v._loading_row.visible)
        self.assertTrue(v._body.visible)
        self.assertEqual(len(v._list.controls), 3)          # hk-1, hk-2, hk-3
        self.assertEqual(len(v._chips_row.controls), 6)     # all / actionable / 4 statuses
        self.assertIn("Cập nhật 09:30 25/11/2026", v._updated_text.value)
        self.assertFalse(v._banner.visible)                 # fresh network data: no warning

    def test_search_is_accent_insensitive_and_filters_the_list(self):
        self.view.show_result(result())
        e = types.SimpleNamespace(control=types.SimpleNamespace(value="co so du lieu"))
        self.view._on_search(e)
        self.assertEqual(len(self.view._list.controls), 1)  # only HK1 holds that course
        e.control.value = "khong co mon nay"
        self.view._on_search(e)
        self.assertEqual(len(self.view._list.controls), 1)  # single "no match" placeholder
        e.control.value = ""
        self.view._on_search(e)
        self.assertEqual(len(self.view._list.controls), 3)

    def test_status_chip_filters_and_rerenders_chip_selection(self):
        self.view.show_result(result())
        self.view._set_filter("actionable")
        shown = self.view._list.controls
        self.assertGreaterEqual(len(shown), 1)
        self.assertLess(len(shown), 3 + 1)
        self.view._set_filter("all")
        self.assertEqual(len(self.view._list.controls), 3)

    def test_search_does_not_rebuild_the_search_field(self):
        self.view.show_result(result())
        field = self.view._search_field
        self.view._on_search(types.SimpleNamespace(control=types.SimpleNamespace(value="triet")))
        self.assertIs(self.view._search_field, field)       # identity kept -> focus kept

    def test_offline_result_shows_warning_banner(self):
        self.view.show_result(RoadmapLoadResult(
            build_snapshot(bundle()), SOURCE_OFFLINE_CACHE, datetime(2026, 11, 20, 8, 0),
            "Portal network request failed"))
        self.assertTrue(self.view._banner.visible)
        self.assertIn("ngoại tuyến", self.view._banner.content.controls[1].value)

    def test_partial_failure_and_missing_framework_are_called_out(self):
        b = bundle()
        b["bodies"]["curriculum"] = None
        b["errors"] = {"curriculum": "HTTP 500", "exemptions": "HTTP 500"}
        self.view.show_result(RoadmapLoadResult(build_snapshot(b), SOURCE_NETWORK, datetime.now()))
        text = self.view._banner.content.controls[1].value
        self.assertIn("Chưa có chương trình khung", text)
        self.assertIn("điểm miễn", text)
        self.assertNotIn("Chưa cập nhật được: chương trình khung", text)  # not double-reported

    def test_no_data_shows_empty_state_with_reason(self):
        self.view.show_loading()
        self.view.show_result(RoadmapLoadResult(None, SOURCE_NONE, None, "Chưa đăng nhập Portal"))
        self.assertTrue(self.view._empty_state.visible)
        self.assertFalse(self.view._body.visible)
        self.assertIn("Chưa đăng nhập Portal", self.view._empty_text.value)

    def test_theme_refresh_rerenders_without_error(self):
        self.view.show_result(result())
        self.view._set_filter("completed")
        self.view.update_theme()
        self.assertTrue(len(self.view._list.controls) >= 1)
        self.assertEqual(len(self.view._chips_row.controls), 6)

    def test_header_callbacks_and_hide(self):
        self.view._handle_close(None)
        self.view._handle_refresh(None)
        self.assertEqual((self.closed, self.refreshed), (1, 1))
        self.view.show_loading()
        self.view.hide()
        self.assertEqual(self.view.opacity, 0.0)

    def test_empty_snapshot_does_not_crash_rendering(self):
        snap = build_snapshot({})
        self.view.show_result(RoadmapLoadResult(snap, SOURCE_NETWORK, datetime.now()))
        self.assertEqual(len(self.view._list.controls), 1)  # "Chưa có học phần nào."


class ViewManagerTests(unittest.TestCase):
    def make(self):
        mk = lambda: types.SimpleNamespace(visible=True, opacity=1.0, offset=None,
                                           show=mock.Mock(), hide=mock.Mock(), show_loading=mock.Mock(),
                                           update_grades=mock.Mock(), update_academic_data=mock.Mock(),
                                           load_current_settings=mock.AsyncMock(),
                                           cancel_pending_load=mock.Mock(), content=None)
        views = {n: mk() for n in ("dashboard", "detail", "settings", "calendar", "grades", "roadmap")}
        controller = types.SimpleNamespace(
            calendar_btn=types.SimpleNamespace(icon_color=None),
            grades_btn=types.SimpleNamespace(icon_color=None),
            roadmap_btn=types.SimpleNamespace(icon_color=None),
        )
        page = mock.Mock()
        switcher = types.SimpleNamespace(content=None)
        vm = ViewManager(page, views["dashboard"], views["detail"], views["settings"],
                         views["calendar"], views["grades"], controller,
                         tab_switcher=switcher, detail_switcher=types.SimpleNamespace(content=None),
                         roadmap_view=views["roadmap"])
        return vm, views, controller, switcher

    def test_show_roadmap_hides_everything_else_and_highlights_button(self):
        vm, v, ctl, sw = self.make()
        vm.show_roadmap_loading()
        for name in ("dashboard", "detail", "settings", "calendar", "grades"):
            self.assertFalse(v[name].visible, name)
        v["roadmap"].show_loading.assert_called_once()
        self.assertIs(sw.content, v["roadmap"])
        from gui.core.theme import C
        self.assertEqual(ctl.roadmap_btn.icon_color, C.ACCENT)
        self.assertEqual(ctl.grades_btn.icon_color, C.TEXT_SECONDARY)
        self.assertEqual(ctl.calendar_btn.icon_color, C.TEXT_SECONDARY)

    def test_every_other_screen_hides_the_roadmap(self):
        for call in (
            lambda vm: vm.show_dashboard(),
            lambda vm: vm.show_calendar({}),
            lambda vm: vm.show_grades_loading(),
            lambda vm: vm.show_detail_loading({}),
            lambda vm: asyncio.run(vm.show_settings()),
        ):
            vm, v, ctl, sw = self.make()
            v["roadmap"].visible = True
            call(vm)
            self.assertFalse(v["roadmap"].visible)

    def test_close_roadmap_returns_to_dashboard(self):
        vm, v, ctl, sw = self.make()
        vm.show_roadmap_loading()
        asyncio.run(vm.close_roadmap())
        self.assertFalse(v["roadmap"].visible)
        self.assertTrue(v["dashboard"].visible)
        self.assertIs(sw.content, v["dashboard"])

    def test_grades_close_path_is_unchanged(self):
        vm, v, ctl, sw = self.make()
        vm.show_grades_loading()
        self.assertTrue(v["grades"].visible)
        asyncio.run(vm.close_grades())
        self.assertFalse(v["grades"].visible)
        self.assertTrue(v["dashboard"].visible)

    def test_view_manager_still_works_without_roadmap_view(self):
        vm, v, ctl, sw = self.make()
        vm.roadmap_view = None
        vm.show_dashboard()             # must not raise


if __name__ == "__main__":
    unittest.main()
