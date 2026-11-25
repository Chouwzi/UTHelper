"""Milestone M3 mobile & landscape UI tests covering F9, F10, F11, and Flet 0.86.5 invariants."""

from __future__ import annotations

import asyncio
import dataclasses
import math
from pathlib import Path
from types import SimpleNamespace
import types as py_types
import typing
from unittest.mock import Mock

import flet as ft
import pytest

from core.courses.activity_service import (
    AttendanceRecord,
    AttendanceSummary,
    BookChapterEntry,
    CourseGroupEntry,
    ForumActivitySummary,
    ForumDiscussionEntry,
    ForumPostEntry,
    GroupActivitySummary,
    QuizActivitySummary,
    QuizAttemptReviewReport,
    QuizReviewChoice,
    QuizReviewQuestion,
    RichActivityPage,
)
from core.courses.archive_service import ArchiveNode, ArchiveTreeResult
from core.courses.catalog_service import CourseDetailSnapshot
from core.courses.models import (
    CourseAttachment,
    CourseGradeItem,
    CourseModuleItem,
    CourseSection,
    CourseSummary,
    RecentMaterialEntry,
)
from gui.app_controller import AppController
from gui.components.courses._shared import (
    PaneErrorOverlay,
    PaneLoadingOverlay,
    _build_preset_skeleton,
    build_course_tab_skeleton,
    build_offline_banner,
    build_viewer_header,
)
from gui.components.courses.activity_views import (
    AttendanceActivityView,
    ForumActivityView,
    GroupActivityView,
    PaneAssignmentView,
    QuizActivityView,
    RichContentActivityView,
)
from gui.components.courses.adaptive_nav import RAIL_WIDTH, AdaptiveNavShell
from gui.components.courses.archive_explorer_view import ArchiveExplorerView
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.course_list_pane import CourseListPane
from gui.components.courses.courses_tab_view import CoursesTabView
from gui.components.detail_view import DetailView
from gui.core.theme import C
from gui.view_models.courses_state import CoursesState, is_compact_height
import platform_utils


def _walk_controls(root: ft.Control):
    yield root
    for attr in ("content", "leading", "trailing", "title", "subtitle"):
        child = getattr(root, attr, None)
        if isinstance(child, ft.Control):
            yield from _walk_controls(child)
    for attr in ("controls", "actions", "tabs", "views"):
        items = getattr(root, attr, None)
        if isinstance(items, (list, tuple)):
            for item in items:
                if isinstance(item, ft.Control):
                    yield from _walk_controls(item)


def _walk_all_flet_fields(obj, path="root"):
    if isinstance(obj, ft.BaseControl):
        yield path, obj
        for f in dataclasses.fields(obj):
            if f.name.startswith("_"):
                continue
            val = getattr(obj, f.name, None)
            if val is not None:
                yield from _walk_all_flet_fields(val, f"{path}.{type(obj).__name__}.{f.name}")
    elif isinstance(obj, (list, tuple)):
        for idx, item in enumerate(obj):
            yield from _walk_all_flet_fields(item, f"{path}[{idx}]")
    elif isinstance(obj, dict):
        for k, item in obj.items():
            yield from _walk_all_flet_fields(item, f"{path}[{k!r}]")


def _matches_flet_type(val, tp) -> bool:
    if tp is typing.Any or val is None or isinstance(tp, (str, typing.ForwardRef)):
        return True
    origin = typing.get_origin(tp)
    if origin is typing.Annotated:
        return _matches_flet_type(val, typing.get_args(tp)[0])
    if origin in (typing.Union, py_types.UnionType):
        return any(_matches_flet_type(val, arg) for arg in typing.get_args(tp))
    if origin in (list, tuple, dict, set):
        return True
    if str(origin).endswith("Callable") or tp is callable:
        return callable(val)
    if isinstance(tp, type):
        return isinstance(val, tp)
    return True


def _mro_annotations(cls) -> dict:
    anns: dict = {}
    for base in reversed(cls.__mro__):
        anns.update(getattr(base, "__annotations__", {}))
    return anns


def _make_course(
    cid: int = 1,
    *,
    source: str = "courses",
    display_name: str = "Lập trình Python",
    teacher_name: str = "TS. Nguyễn Văn Giảng Viên Rất Dài Trên Mobile",
    teacher_email: str = "giangvien.ratdai@ut.edu.vn",
    semester_label: str = "HK2 2025-2026",
) -> CourseSummary:
    return CourseSummary(
        id=cid,
        source=source,  # type: ignore[arg-type]
        fullname=display_name,
        display_name=display_name,
        shortname=f"CS{cid}",
        course_code=f"010100{cid}",
        semester_label=semester_label,
        teacher_name=teacher_name,
        teacher_email=teacher_email,
        credits=3,
        progress=0.5,
        completed_count=2,
        total_trackable=4,
    )


def _fake_page(*, width: float = 390.0, height: float = 780.0):
    return SimpleNamespace(
        overlay=[],
        web=False,
        platform="android",
        width=width,
        height=height,
        window=SimpleNamespace(width=width, height=height, maximized=False),
        update=lambda: None,
        run_task=lambda fn, *a: asyncio.run(fn(*a)),
        launch_url=lambda _u: None,
    )


class _FakeCatalogService:
    def __init__(self, courses=None, detail_map=None):
        self._courses = list(courses or [])
        self._detail_map = dict(detail_map or {})
        self.last_courses_error = ""
        self.last_thnn_error = ""

    def load_cached_catalog(self):
        return list(self._courses), [], 1000

    def is_catalog_stale(self, _hours=6):
        return False

    def open_course_cached(self, source, cid):
        return self._detail_map.get((source, cid), CourseDetailSnapshot(sections=[], grades=[]))

    def sync_course_delta(self, source, cid, force=False):
        snap = self._detail_map.get((source, cid), CourseDetailSnapshot(sections=[], grades=[]))
        return SimpleNamespace(
            sections=snap.sections,
            grades=snap.grades,
            last_synced_at=1000,
            changed=False,
            error="",
        )

    def get_user_course_groups(self, _source, _cid):
        return []


# ---------------------------------------------------------------------------
# 1. State helpers & AdaptiveNavShell (F11)
# ---------------------------------------------------------------------------


def test_is_compact_height_and_courses_state_defaults():
    assert is_compact_height(None) is False
    assert is_compact_height(True) is False
    assert is_compact_height("bad") is False
    assert is_compact_height(-10) is False
    assert is_compact_height(0) is False
    assert is_compact_height(float("nan")) is False
    assert is_compact_height(float("inf")) is False
    assert is_compact_height(360) is True
    assert is_compact_height(499.9) is True
    assert is_compact_height(500.0) is False
    assert is_compact_height(760) is False

    state = CoursesState()
    assert state.layout_mode == "wide"
    assert state.viewport_height == 760.0


def test_adaptive_nav_shell_height_scrollable_and_immersive_bar():
    shell = AdaptiveNavShell(on_navigate=lambda _k: None)

    assert shell.apply_height(360) is True
    assert shell.rail.scrollable is True
    assert shell.apply_height(420) is False
    assert shell.rail.scrollable is False
    assert shell.apply_height(None) is False
    assert shell.apply_height(True) is False

    # Narrow phone (<600dp): bar visible by default, collapses in immersive mode
    assert shell.apply_width(390) is False
    assert shell.bar.visible is True
    shell.set_immersive(True)
    assert shell.bar.visible is False

    # Switching to landscape (>=600dp) uses rail; clearing immersive must NOT unhide bar while rail is active
    assert shell.apply_width(780) is True
    assert shell.rail_host.visible is True
    assert shell.bar.visible is False
    shell.set_immersive(False)
    assert shell.bar.visible is False

    # Back to portrait (<600dp): bar restores when not immersive, stays hidden if immersive
    shell.set_immersive(True)
    assert shell.apply_width(390) is False
    assert shell.bar.visible is False
    shell.set_immersive(False)
    assert shell.bar.visible is True


# ---------------------------------------------------------------------------
# 2. F9 — Android System Back Sub-Stack Delegation
# ---------------------------------------------------------------------------


def test_f9_courses_tab_handle_back_delegates_to_archive_quiz_and_forum(tmp_path: Path):
    course = _make_course(1)
    svc = _FakeCatalogService(courses=[course])
    view = CoursesTabView(page=None, catalog_service=svc)  # type: ignore[arg-type]
    view.set_width(390)
    view.load_initial()
    view._state.select_course(course.course_key)

    # 2a. ArchiveExplorerView nested folder stack
    nested_file = ArchiveNode(name="notes.txt", full_path="sub/notes.txt", is_dir=False, size=10)
    sub_dir = ArchiveNode(name="sub", full_path="sub", is_dir=True, children=[nested_file])
    root_dir = ArchiveNode(name="root", full_path="", is_dir=True, children=[sub_dir])
    fake_arch_svc = SimpleNamespace(
        inspect_bytes=lambda _n, _b: ArchiveTreeResult(
            file_name="pack.zip",
            format_name="zip",
            supported=True,
            root=root_dir,
            total_files=1,
        ),
        extract_entry_bytes=lambda _n, _b, _p: b"hello",
    )
    arch = ArchiveExplorerView(
        archive_name="pack.zip",
        archive_bytes=b"PK\x05\x06" + b"\x00" * 18,
        archive_service=fake_arch_svc,  # type: ignore[arg-type]
        on_close=view.pop_overlay,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    arch._open_node(sub_dir)
    assert len(arch._stack) == 2
    view.push_overlay(arch, tag="archive:test")
    assert view.handle_back() is True
    assert len(arch._stack) == 1
    assert arch in view._overlays  # stepped up folder, did NOT pop overlay yet
    assert view.handle_back() is True
    assert view._overlays == []  # popped overlay at root

    # 2b. QuizActivityView review -> summary -> pop overlay
    quiz_summary = QuizActivitySummary(cmid=10, quiz_id=20, title="Quiz 1", intro="", attempts=())
    report = QuizAttemptReviewReport(
        attempt_id=1,
        questions=(
            QuizReviewQuestion(
                display_number="1",
                slot="1",
                status_label="Đúng",
                state_kind="correct",
                question_text="Q1?",
            ),
        ),
        correct_count=1,
    )
    quiz_view = QuizActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=10, instance_id=20, modname="quiz", name="Quiz 1", section_id=1),
        activity_service=SimpleNamespace(  # type: ignore[arg-type]
            fetch_quiz_activity=lambda *_a, **_k: quiz_summary,
        ),
        on_close=view.pop_overlay,
        on_open_webview=lambda *_a: None,
    )
    quiz_view._render_attempt_review(quiz_summary, None, report)
    assert quiz_view._in_review is True
    view.push_overlay(quiz_view, tag="activity:quiz:10")
    assert view.handle_back() is True
    assert quiz_view._in_review is False
    assert quiz_view in view._overlays
    assert view.handle_back() is True
    assert view._overlays == []

    # 2c. ForumActivityView posts -> discussions -> pop overlay
    disc = ForumDiscussionEntry(discussion_id=5, name="Chủ đề 1", author="GV", created_at="Hôm nay")
    forum_summary = ForumActivitySummary(cmid=11, forum_id=21, title="Diễn đàn", intro="", discussions=(disc,))
    forum_view = ForumActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=11, instance_id=21, modname="forum", name="Diễn đàn", section_id=1),
        activity_service=SimpleNamespace(  # type: ignore[arg-type]
            fetch_forum_activity=lambda *_a, **_k: forum_summary,
        ),
        on_close=view.pop_overlay,
    )
    forum_view._render_posts(
        forum_summary,
        disc,
        [ForumPostEntry(post_id=1, subject="Re: Chủ đề 1", author="SV", created_at="Hôm nay", message_text="Chào")],
    )
    assert forum_view._in_posts is True
    view.push_overlay(forum_view, tag="activity:forum:11")
    assert view.handle_back() is True
    assert forum_view._in_posts is False
    assert forum_view in view._overlays
    assert view.handle_back() is True
    assert view._overlays == []

    # 2d. Mobile detail back -> list
    assert view._state.in_detail_mobile is True
    assert view.handle_back() is True
    assert view._state.in_detail_mobile is False
    assert view.handle_back() is False

    # 2e. Broken overlay handle_back() falls back cleanly to pop_overlay()
    broken_ov = ft.Container()
    broken_ov.handle_back = lambda: (_ for _ in ()).throw(RuntimeError("boom"))  # type: ignore[attr-defined]
    view.push_overlay(broken_ov, tag="broken")
    assert view.handle_back() is True
    assert view._overlays == []


# ---------------------------------------------------------------------------
# 3. F10 — Shared Overlays, Archive Explorer & Modal Sheets
# ---------------------------------------------------------------------------


def test_f10_shared_skeletons_error_overlay_offline_banner_and_header():
    doc_skel = _build_preset_skeleton("document")
    assert doc_skel[0].width is None
    for ctrl in _walk_controls(doc_skel[0]):
        w = getattr(ctrl, "width", None)
        if w is not None:
            assert w <= 240

    tab_skel = build_course_tab_skeleton("sections")
    status_texts = [
        c for c in _walk_controls(tab_skel)
        if isinstance(c, ft.Text) and c.value and "đồng bộ" in c.value.lower()
    ]
    assert len(status_texts) == 1 and status_texts[0].expand is True

    retried: list[bool] = []
    banner = build_offline_banner("Mất kết nối", on_retry=lambda: retried.append(True))
    retry_btns = [c for c in _walk_controls(banner) if isinstance(c, ft.TextButton)]
    assert len(retry_btns) == 1
    retry_btns[0].on_click(None)
    assert retried == [True]

    hdr, back_btn, _t, _s = build_viewer_header(
        title="Tiêu đề rất dài",
        subtitle="Phụ đề",
        back_tooltip="Quay lại",
        on_back=lambda _e: None,
        trailing_actions=[ft.IconButton(icon=ft.Icons.REFRESH_ROUNDED)],
    )
    assert back_btn.size_constraints is not None
    assert back_btn.size_constraints.min_width >= 44
    assert back_btn.size_constraints.min_height >= 44
    assert isinstance(hdr.content.controls[-1], ft.Row)

    err_ov = PaneErrorOverlay(title="Lỗi", message="Timeout", on_back=lambda: None)
    assert err_ov._card.width is None
    body_host = err_ov.content.controls[1]
    assert isinstance(body_host.content, ft.Column)
    assert body_host.content.scroll == ft.ScrollMode.AUTO
    body_host.on_size_change(SimpleNamespace(width=800))
    assert err_ov._card.width == 460.0
    body_host.on_size_change(SimpleNamespace(width=360))
    assert err_ov._card.width is None


def test_f10_archive_explorer_per_entry_save_and_responsive_unsupported():
    saved: list[tuple[str, bytes]] = []
    file_node = ArchiveNode(name="report.pdf", full_path="report.pdf", is_dir=False, size=2048, preview_kind="pdf")
    root = ArchiveNode(name="root", full_path="", is_dir=True, children=[file_node])

    should_fail = [False]

    def _extract(_n, _b, _p):
        if should_fail[0]:
            raise ValueError("Corrupt entry")
        return b"%PDF-1.4"

    fake_svc = SimpleNamespace(
        inspect_bytes=lambda _n, _b: ArchiveTreeResult(
            file_name="docs.zip",
            format_name="zip",
            supported=True,
            root=root,
            total_files=1,
        ),
        extract_entry_bytes=_extract,
    )
    view = ArchiveExplorerView(
        archive_name="docs.zip",
        archive_bytes=b"PK\x05\x06" + b"\x00" * 18,
        archive_service=fake_svc,  # type: ignore[arg-type]
        on_close=lambda: None,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda name, data: saved.append((name, data)),
    )
    save_btns = [
        c for c in _walk_controls(view._body_host)
        if isinstance(c, ft.IconButton) and c.tooltip == "Lưu tệp này"
    ]
    assert len(save_btns) == 1
    assert (save_btns[0].width or 0) >= 44
    assert (save_btns[0].height or 0) >= 44
    save_btns[0].on_click(None)
    assert saved == [("report.pdf", b"%PDF-1.4")]

    should_fail[0] = True
    save_btns[0].on_click(None)
    assert view._banner_host.visible is True
    assert "Corrupt entry" in view._error_message

    # Unsupported RAR view has no fixed 420dp overflow on mobile
    bad_svc = SimpleNamespace(
        inspect_bytes=lambda _n, _b: ArchiveTreeResult(
            file_name="archive_ten_rat_dai.rar",
            format_name="rar",
            supported=False,
            root=ArchiveNode(name="archive.rar", full_path="", is_dir=True),
            total_files=0,
            unsupported_reason="Chưa hỗ trợ RAR",
        )
    )
    unsup = ArchiveExplorerView(
        archive_name="archive_ten_rat_dai.rar",
        archive_bytes=b"Rar!",
        archive_service=bad_svc,  # type: ignore[arg-type]
        on_close=lambda: None,
        on_open_entry=lambda *_a: None,
        on_save_raw=lambda *_a: None,
    )
    card_containers = [
        c for c in _walk_controls(unsup._body_host)
        if isinstance(c, ft.Container) and getattr(c, "width", None) == 420
    ]
    assert card_containers == []


def test_f10_courses_tab_modal_overlay_mobile_fullscreen_and_tablet_clamp():
    course = _make_course(1)
    att1 = CourseAttachment(file_name="slide1.pdf", file_url="https://courses.ut.edu.vn/1.pdf")
    att2 = CourseAttachment(file_name="slide2.pdf", file_url="https://courses.ut.edu.vn/2.pdf")
    m_folder = CourseModuleItem(
        cmid=10,
        instance_id=10,
        modname="folder",
        name="Thư mục bài giảng",
        section_id=1,
        attachments=(att1, att2),
        open_strategy="folder_files",
    )
    m_page = CourseModuleItem(
        cmid=11,
        instance_id=11,
        modname="page",
        name="Thông báo học vụ",
        section_id=1,
        inline_html="<p>Đọc kỹ đề cương.</p>",
        open_strategy="inline_page",
    )
    on_dl = Mock()
    view = CoursesTabView(
        page=None,
        catalog_service=_FakeCatalogService(courses=[course]),  # type: ignore[arg-type]
        on_download_attachment=on_dl,
    )
    view.load_initial()
    view._state.select_course(course.course_key)

    # Mobile (<600dp): full-screen sheet, zero width=720, header back pops, folder row click works
    view.set_width(390)
    asyncio.run(view._handle_open_module_async(m_folder))
    ov = view._overlays[-1]
    assert not any(getattr(c, "width", None) == 720 for c in _walk_controls(ov))
    folder_rows = [
        c for c in _walk_controls(ov)
        if isinstance(c, ft.Container) and c.on_click is not None
    ]
    assert len(folder_rows) >= 2
    folder_rows[0].on_click(None)
    on_dl.assert_called_once_with(course, att1)
    assert view.handle_back() is True
    assert view._overlays == []

    # Inline overlay on mobile uses build_viewer_header back button
    asyncio.run(view._handle_open_module_async(m_page))
    ov_inline = view._overlays[-1]
    back_btns = [
        c for c in _walk_controls(ov_inline)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.ARROW_BACK_ROUNDED
    ]
    assert len(back_btns) == 1
    back_btns[0].on_click(None)
    assert view._overlays == []

    # Tablet portrait (650dp): clamps card width <= 618
    view.set_width(650)
    asyncio.run(view._handle_open_module_async(m_page))
    ov_tab = view._overlays[-1]
    assert any(getattr(c, "width", None) == 618.0 for c in _walk_controls(ov_tab))
    view.pop_overlay()

    # Wide desktop (1100dp): preserves 720dp modal card
    view.set_width(1100)
    asyncio.run(view._handle_open_module_async(m_page))
    ov_desk = view._overlays[-1]
    assert any(getattr(c, "width", None) == 720.0 for c in _walk_controls(ov_desk))


# ---------------------------------------------------------------------------
# 4. F11 — Scroll Preservation, Immersive Mode, List & Detail Mobile/Landscape
# ---------------------------------------------------------------------------


def test_f11_persistent_list_pane_in_stack_and_immersive_mode():
    course = _make_course(1)
    sec = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="S1", modules=())
    svc = _FakeCatalogService(
        courses=[course],
        detail_map={("courses", 1): CourseDetailSnapshot(sections=[sec], grades=[])},
    )
    immersive_events: list[bool] = []
    view = CoursesTabView(
        page=None,
        catalog_service=svc,  # type: ignore[arg-type]
        on_immersive_changed=immersive_events.append,
    )
    view.load_initial()

    # Portrait mobile (390dp): list mounted in _narrow_stack behind _narrow_workspace_host
    view.set_width(390)
    assert view._narrow_list_host.content is view._list_pane
    assert view._narrow_list_host.opacity == 1.0
    assert view._narrow_list_host.ignore_interactions is False
    assert view._narrow_workspace_host.visible is False

    asyncio.run(view._select_course_async(course.course_key))
    assert view._narrow_list_host.content is view._list_pane  # NOT unmounted!
    assert view._narrow_list_host.opacity == 0.0
    assert view._narrow_list_host.ignore_interactions is True
    assert view._narrow_workspace_host.visible is True

    # Immersive triggers when overlay pushed on mobile
    ov = ft.Container(expand=True)
    view.push_overlay(ov, tag="reader:1")
    assert immersive_events[-1] is True
    assert view._compute_pane3_bounds()[3] > 600.0  # bottom_inset cleared

    # Tab hidden restores nav bar, tab shown re-collapses
    view.on_tab_hidden()
    assert immersive_events[-1] is False
    view.on_tab_shown()
    assert immersive_events[-1] is True

    # _dismiss_overlay_instance restores immersive to False
    view._dismiss_overlay_instance(ov)
    assert immersive_events[-1] is False

    # Tablet portrait (750dp) also keeps _list_pane mounted inside _narrow_stack inside _tablet_wrapper
    view.set_width(750)
    assert view._main_host.content is view._tablet_wrapper
    assert view._tablet_content.content is view._narrow_stack
    assert view._narrow_list_host.content is view._list_pane


def test_f11_app_controller_resize_height_and_immersive_wiring():
    ctrl = AppController.__new__(AppController)
    ctrl.page = _fake_page(width=390, height=780)
    ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)

    course = _make_course(1)
    view = CoursesTabView(
        page=ctrl.page,  # type: ignore[arg-type]
        catalog_service=_FakeCatalogService(courses=[course]),  # type: ignore[arg-type]
    )
    view.visible = True
    view.set_on_immersive_changed(ctrl._on_courses_immersive_changed)
    ctrl.courses_view = view

    ctrl._apply_responsive_layout()
    assert ctrl._nav_shell.bar.visible is True
    view.push_overlay(ft.Container(), tag="ov:1")
    assert ctrl._nav_shell.bar.visible is False
    view.pop_overlay()
    assert ctrl._nav_shell.bar.visible is True

    # Landscape 780x360: rail becomes scrollable AND compact height propagates
    ctrl._on_page_resize(SimpleNamespace(width=780, height=360))
    assert ctrl._nav_shell.rail_visible is True
    assert ctrl._nav_shell.rail.scrollable is True
    assert view._state.viewport_height == 360.0
    assert view._compact_height is True


def test_f11_course_list_pane_mobile_ergonomics_recent_expand_and_landscape(monkeypatch):
    c1 = _make_course(1, display_name="Môn 1")
    c2 = _make_course(2, display_name="Môn 2")
    recents = [
        RecentMaterialEntry(
            course_key=c1.course_key,
            course_name=c1.display_name,
            cmid=100 + i,
            title=f"Tài liệu {i}",
            attachment_url=f"https://courses.ut.edu.vn/{i}.pdf",
            file_name=f"file_{i}.pdf",
            opened_at=1770000000 - i,
        )
        for i in range(6)
    ]

    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", True)
    opened_recent: list[RecentMaterialEntry] = []
    refreshed: list[bool] = []
    pane = CourseListPane(
        on_open_recent=opened_recent.append,
        on_refresh_catalog=lambda: refreshed.append(True),
    )
    assert "(Ctrl+K)" not in (pane._search_field.hint_text or "")

    state = CoursesState(
        courses=[c1, c2],
        recent_items=recents,
        selected_course_key=c1.course_key,
        offline_notice="Ngoại tuyến",
        in_detail_mobile=False,
        layout_mode="mobile",
        viewport_height=780.0,
    )
    pane.render(state, update=False)

    # Selected border hidden on mobile list when not in_detail_mobile; trailing chevron shown
    card1 = pane._card_map[c1.course_key]
    assert card1.border == ft.Border.all(1, C.BORDER)
    assert any(
        isinstance(c, ft.Icon) and c.icon == ft.Icons.CHEVRON_RIGHT_ROUNDED
        for c in _walk_controls(card1)
    )
    # Pin IconButton >= 44dp
    pin_btn = next(c for c in _walk_controls(card1) if isinstance(c, ft.IconButton))
    assert (pin_btn.width or 0) >= 44 and (pin_btn.height or 0) >= 44

    # Banners moved inside _cards_list on mobile
    assert pane._notice_slot.visible is False
    assert pane._recent_slot.visible is False
    assert len(pane._cards_list.controls) == 4  # offline + recent + 2 cards

    # Offline retry CTA works
    retry_btn = next(
        c for c in _walk_controls(pane._cards_list)
        if isinstance(c, ft.TextButton) and getattr(c, "content", None) == "Thử lại"
    )
    retry_btn.on_click(None)
    assert refreshed == [True]

    # Expand "Tiếp tục nhanh" shows up to 5 recent items (1 primary + 4 extra)
    expand_btn = next(
        c for c in _walk_controls(pane._cards_list)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.EXPAND_MORE_ROUNDED
    )
    expand_btn.on_click(None)
    recent_texts = [
        c.value for c in _walk_controls(pane._cards_list.controls[1])
        if isinstance(c, ft.Text) and c.value and "Môn 1 •" in c.value
    ]
    assert len(recent_texts) == 5

    # Click 2nd recent item
    extra_rows = [
        c for c in _walk_controls(pane._cards_list.controls[1])
        if isinstance(c, ft.Container) and c.on_click is not None
    ]
    extra_rows[-1].on_click(None)
    assert len(opened_recent) == 1 and opened_recent[0].cmid == 104

    # O(1) selection updates border cleanly when in_detail_mobile flips True
    card1_cur = pane._card_map[c1.course_key]
    state.in_detail_mobile = True
    pane.render(state, update=False)
    assert pane._card_map[c1.course_key] is card1_cur
    assert card1_cur.border == ft.Border.all(1.5, C.ACCENT)

    # Landscape tablet_portrait (viewport_height=360) also puts banners in _cards_list
    state.layout_mode = "tablet_portrait"
    state.viewport_height = 360.0
    pane.render(state, update=False)
    assert pane._notice_slot.visible is False and pane._recent_slot.visible is False

    # Desktop restores (Ctrl+K)
    monkeypatch.setattr(platform_utils, "UI_IS_MOBILE", False)
    pane.update_theme(update=False, rerender=False)
    assert "(Ctrl+K)" in (pane._search_field.hint_text or "")


def test_f10_f11_course_detail_pane_mobile_and_landscape():
    course = _make_course(1)
    att = CourseAttachment(file_name="bai_giang.pdf", file_url="https://courses.ut.edu.vn/b.pdf")
    mod_file = CourseModuleItem(
        cmid=1, instance_id=1, modname="resource", name="Bài giảng", section_id=1, attachments=(att,)
    )
    mod_lbl = CourseModuleItem(
        cmid=2,
        instance_id=2,
        modname="label",
        name="Meet",
        section_id=1,
        inline_html='<p>Link: <a href="https://meet.google.com/abc-defg-hij-klo-pqr-stu">https://meet.google.com/abc-defg-hij-klo-pqr-stu</a></p>',
    )
    sec = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="Tuần 1", modules=(mod_file, mod_lbl))
    grade = CourseGradeItem(
        id=1,
        item_name="Điểm quá trình (40%)",
        grade_formatted="9.25 / 10 (Đạt)",
        range_formatted="0–10",
    )
    refreshed: list[bool] = []
    pane = CourseDetailPane(on_refresh_course=lambda: refreshed.append(True))
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=[sec],
        grades=[grade],
        user_groups=[{"name": "Nhóm Thực Hành Công Nghệ Phần Mềm Rất Dài 01"}],
        offline_notice="Dữ liệu cache",
        active_subtab=2,
        layout_mode="mobile",
        viewport_height=780.0,
    )
    pane.render(state, show_back=True, update=False)

    # Padding & segmented pills
    assert pane.padding == ft.Padding.symmetric(horizontal=12, vertical=12)
    assert all(p.height == 36 for p in pane._segment_pills)
    assert all(lbl.max_lines == 1 and lbl.overflow == ft.TextOverflow.ELLIPSIS for lbl in pane._segment_labels)

    # Teacher Chip preserves ft.Text label with ellipsis + width cap
    chips = [c for c in _walk_controls(pane._header_slot) if isinstance(c, ft.Chip)]
    assert len(chips) == 1
    assert isinstance(chips[0].label, ft.Text)
    assert chips[0].label.overflow == ft.TextOverflow.ELLIPSIS
    assert chips[0].width == 290

    # Download IconButton >= 44dp
    dl_btns = [
        c for c in _walk_controls(pane._content_tab_body)
        if isinstance(c, ft.IconButton) and c.icon == ft.Icons.DOWNLOAD_ROUNDED
    ]
    assert len(dl_btns) == 1 and (dl_btns[0].width or 0) >= 44

    # Inline label link button preserves "meet.google.com" string and constrains length on mobile
    meet_btn = next(
        c for c in _walk_controls(pane._content_tab_body)
        if isinstance(c, ft.OutlinedButton) and "meet.google.com" in str(c.content)
    )
    assert len(str(meet_btn.content)) <= 32
    assert meet_btn.width == 248

    # Grades hero & trailing score
    grade_rows = [
        r for r in _walk_controls(pane._grades_tab_body)
        if isinstance(r, ft.Row) and r.wrap is True
    ]
    assert len(grade_rows) >= 2  # score+badge Row & kpi_pills Row

    # Offline retry CTA
    retry_btn = next(
        c for c in _walk_controls(pane._notice_slot)
        if isinstance(c, ft.TextButton) and getattr(c, "content", None) == "Thử lại"
    )
    retry_btn.on_click(None)
    assert refreshed == [True]

    # Landscape viewport_height=360 collapses metadata behind toggle
    state.viewport_height = 360.0
    pane.render(state, show_back=True, update=False)
    toggle_containers = [
        c for c in _walk_controls(pane._header_slot)
        if isinstance(c, ft.Container) and c.on_click is not None
    ]
    assert len(toggle_containers) == 1
    assert pane._meta_expanded is False
    toggle_containers[0].on_click(None)
    assert pane._meta_expanded is True


# ---------------------------------------------------------------------------
# 5. F10 — Activity Views & DetailView Narrow Layouts + Wrap/MRO Invariants
# ---------------------------------------------------------------------------


def test_f10_activity_views_and_detail_view_narrow_360_layouts():
    # AttendanceActivityView: 2-col wrapped metric cards without expand=True
    att_view = AttendanceActivityView(
        source="courses",
        module=CourseModuleItem(cmid=1, instance_id=1, modname="attendance", name="Điểm danh", section_id=1),
        activity_service=SimpleNamespace(),  # type: ignore[arg-type]
        on_close=lambda: None,
        pane_width=360,
        initial_summary=AttendanceSummary(
            cmid=1,
            title="Điểm danh",
            taken_sessions="5",
            points_over_taken="10/10",
            percentage="100%",
            records=(
                AttendanceRecord(
                    date_str="12/05",
                    description="Ca 1",
                    status="Present",
                    points="2/2",
                    remarks="",
                ),
            ),
        ),
    )
    metric_row = next(
        c for c in _walk_controls(att_view._body_host)
        if isinstance(c, ft.Row) and c.wrap is True and len(c.controls) == 3
    )
    assert all(not getattr(child, "expand", False) for child in metric_row.controls)
    assert all((getattr(child, "width", 0) or 0) <= 170 for child in metric_row.controls)

    # GroupActivityView: 2nd row for count & Join button
    grp_view = GroupActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=2, instance_id=2, modname="groupselect", name="Nhóm", section_id=1),
        activity_service=SimpleNamespace(),  # type: ignore[arg-type]
        on_close=lambda: None,
        pane_width=360,
        initial_summary=GroupActivitySummary(
            cmid=2,
            modname="groupselect",
            title="Chọn nhóm",
            groups=(
                CourseGroupEntry(
                    group_id="1",
                    name="Nhóm 1",
                    description="Chiều thứ 5",
                    member_count_label="3/4",
                    can_join=True,
                ),
            ),
        ),
    )
    join_btns = [
        c for c in _walk_controls(grp_view._body_host)
        if isinstance(c, ft.FilledButton) and getattr(c, "content", None) == "Tham gia nhóm"
    ]
    assert len(join_btns) == 1
    # On narrow 360dp, join button is NOT in the same Row as group name text
    for row in _walk_controls(grp_view._body_host):
        if isinstance(row, ft.Row) and join_btns[0] in row.controls:
            row_texts = [t.value for t in _walk_controls(row) if isinstance(t, ft.Text) and t.value]
            assert "Nhóm 1" not in row_texts

    # QuizActivityView: collapsed jump grid on 360dp, tag pill on own row, meta label width 115
    q_summary = QuizActivitySummary(cmid=3, quiz_id=3, title="Quiz", intro="", attempts=())
    q_report = QuizAttemptReviewReport(
        attempt_id=1,
        questions=(
            QuizReviewQuestion(
                display_number="1",
                slot="1",
                status_label="Sai",
                state_kind="incorrect",
                question_code="CLO1",
                question_text="Câu hỏi mẫu?",
                choices=(
                    QuizReviewChoice(label="A", text="Phương án A", is_selected=True, state="incorrect"),
                    QuizReviewChoice(label="B", text="Phương án B", is_selected=False, state="correct"),
                ),
            ),
            QuizReviewQuestion(display_number="2", slot="2", status_label="Đúng", state_kind="correct", question_text="Q2?"),
        ),
        correct_count=1,
        incorrect_count=1,
    )
    quiz_view = QuizActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=3, instance_id=3, modname="quiz", name="Quiz", section_id=1),
        activity_service=SimpleNamespace(fetch_quiz_activity=lambda *_a, **_k: q_summary),  # type: ignore[arg-type]
        on_close=lambda: None,
        on_open_webview=lambda *_a: None,
        pane_width=360,
    )
    quiz_view._render_quiz(q_summary)
    meta_labels = [
        c for c in _walk_controls(quiz_view._body_host)
        if isinstance(c, ft.Text) and c.value == "Mở bài"
    ]
    assert len(meta_labels) == 1 and meta_labels[0].width == 115

    quiz_view._render_attempt_review(q_summary, None, q_report)
    exp_tiles = [c for c in _walk_controls(quiz_view._body_host) if isinstance(c, ft.ExpansionTile)]
    assert len(exp_tiles) == 1
    assert exp_tiles[0].expanded is False
    assert isinstance(exp_tiles[0].shape, ft.OutlinedBorder)
    quiz_view.set_width(1000)
    exp_tiles_wide = [c for c in _walk_controls(quiz_view._body_host) if isinstance(c, ft.ExpansionTile)]
    assert exp_tiles_wide[0].expanded is True

    # ForumActivityView: wrapped top row without expand=True, ellipsized subject
    disc = ForumDiscussionEntry(discussion_id=1, name="Thảo luận rất dài", author="GV", created_at="Hôm nay")
    f_summary = ForumActivitySummary(cmid=4, forum_id=4, title="Diễn đàn", intro="", discussions=(disc,))
    forum_view = ForumActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=4, instance_id=4, modname="forum", name="Diễn đàn", section_id=1),
        activity_service=SimpleNamespace(fetch_forum_activity=lambda *_a, **_k: f_summary),  # type: ignore[arg-type]
        on_close=lambda: None,
        pane_width=360,
    )
    forum_view._render_posts(
        f_summary,
        disc,
        [
            ForumPostEntry(
                post_id=1,
                subject="Tiêu đề bài viết rất dài",
                author="Nguyễn Văn A",
                created_at="Hôm nay",
                message_text="Nội dung",
            )
        ],
    )
    subj_txt = next(
        c for c in _walk_controls(forum_view._body_host)
        if isinstance(c, ft.Text) and c.value == "Tiêu đề bài viết rất dài"
    )
    assert subj_txt.max_lines == 1 and subj_txt.overflow == ft.TextOverflow.ELLIPSIS

    # RichContentActivityView: horizontal chip row + prev/next chapter footer
    rich_view = RichContentActivityView(
        source="courses",
        course_id=1,
        module=CourseModuleItem(cmid=5, instance_id=5, modname="book", name="Sách", section_id=1),
        activity_service=SimpleNamespace(  # type: ignore[arg-type]
            fetch_rich_content=lambda *_a, **_k: RichActivityPage(cmid=5, title="Sách", modname="book")
        ),
        on_close=lambda: None,
        pane_width=360,
    )
    rich_page = RichActivityPage(
        cmid=5,
        title="Sách",
        modname="book",
        chapters=(
            BookChapterEntry(chapter_id="1", title="Chương 1", content_markdown="A"),
            BookChapterEntry(chapter_id="2", title="Chương 2", content_markdown="B"),
            BookChapterEntry(chapter_id="3", title="Chương 3", content_markdown="C"),
        ),
    )
    rich_view._render_page(rich_page)
    ch_chips = [c for c in _walk_controls(rich_view._body_host) if isinstance(c, ft.Chip)]
    assert len(ch_chips) == 3 and ch_chips[0].selected is True
    next_btn = next(
        c for c in _walk_controls(rich_view._body_host)
        if isinstance(c, ft.OutlinedButton) and getattr(c, "content", None) == "Chương sau →"
    )
    next_btn.on_click(None)
    assert rich_view._active_chapter_idx == 1

    # PaneAssignmentView & DetailView width forwarding and narrow 360dp wrapping
    fake_page = _fake_page(width=1000, height=800)
    dv = DetailView(fake_page, on_close=lambda: None, embedded_in_pane=True)  # type: ignore[arg-type]
    pav = PaneAssignmentView(
        detail_view=dv,
        title="Bài tập 1",
        on_close=lambda: None,
        on_refresh=lambda: None,
        on_open_webview=lambda: None,
    )
    assert pav._error_strip.content.wrap is True
    assert all(not getattr(c, "expand", False) for c in pav._error_strip.content.controls)

    pav.set_width(360)
    assert dv._submission_actions_row.wrap is True
    assert dv._submit_btn.expand is False
    assert dv._finalize_btn.expand is False
    assert dv._file_edit_dialog_content.width <= 260
    assert dv._delete_confirm_dialog_content.width <= 260

    dv.update_detail(
        {
            "id": "10",
            "title": "Quiz",
            "course": "CS",
            "deadline": 0,
            "url": "https://courses.ut.edu.vn/mod/quiz/view.php?id=10",
            "details": {"attempts_allowed": "3 lần", "time_limit": "45 phút"},
        }
    )
    attempt_val = next(
        c for c in _walk_controls(dv._content_col)
        if isinstance(c, ft.Text) and c.value == "3 lần"
    )
    assert attempt_val.expand is True

    pav.set_width(1000)
    assert dv._submission_actions_row.wrap is False
    assert dv._submit_btn.expand is True
    assert dv._finalize_btn.expand is True

    # Verify zero Row(wrap=True) with expand=True child AND zero Flet MRO mismatches across ALL views
    roots = [
        ("Attendance", att_view),
        ("Group", grp_view),
        ("Quiz", quiz_view),
        ("Forum", forum_view),
        ("Rich", rich_view),
        ("PaneAssign", pav),
    ]
    wrap_violations: list[str] = []
    mro_mismatches: list[tuple] = []
    for name, root in roots:
        for ctrl in _walk_controls(root):
            if isinstance(ctrl, ft.Row) and bool(getattr(ctrl, "wrap", False)):
                for idx, ch in enumerate(ctrl.controls):
                    if getattr(ch, "expand", None):
                        wrap_violations.append(f"{name}.Row.controls[{idx}]({type(ch).__name__}).expand={ch.expand}")
        for path, ctrl in _walk_all_flet_fields(root, name):
            hints = _mro_annotations(type(ctrl))
            for f in dataclasses.fields(ctrl):
                if f.name.startswith("_") or f.name not in hints:
                    continue
                val = getattr(ctrl, f.name, None)
                if val is not None and not _matches_flet_type(val, hints[f.name]):
                    mro_mismatches.append((f"{path}.{type(ctrl).__name__}.{f.name}", type(val), hints[f.name]))

    assert wrap_violations == []
    assert mro_mismatches == []


def test_flet_mro_and_wrap_invariants_across_mobile_course_panes():
    course = _make_course(1)
    att = CourseAttachment(file_name="a.pdf", file_url="https://courses.ut.edu.vn/a.pdf")
    mod = CourseModuleItem(cmid=1, instance_id=1, modname="resource", name="Tài liệu", section_id=1, attachments=(att,))
    sec = CourseSection(id=1, course_id=1, source="courses", section_num=1, name="Chương 1", modules=(mod,))
    grade = CourseGradeItem(id=1, item_name="Giữa kỳ (30%)", grade_formatted="8.5", range_formatted="0–10")
    rec = RecentMaterialEntry(
        course_key=course.course_key,
        course_name=course.display_name,
        cmid=1,
        title="Tài liệu",
        attachment_url="https://courses.ut.edu.vn/a.pdf",
        file_name="a.pdf",
        opened_at=1_770_000_000,
    )
    state = CoursesState(
        courses=[course],
        selected_course_key=course.course_key,
        sections=[sec],
        grades=[grade],
        recent_items=[rec, rec],
        active_subtab=2,
        offline_notice="Offline",
        layout_mode="mobile",
        viewport_height=360.0,
        user_groups=[{"name": "Nhóm 1"}],
    )
    lp = CourseListPane()
    lp.render(state, update=False)
    dp = CourseDetailPane()
    dp.render(state, show_back=True, update=False)

    wrap_violations = []
    mro_mismatches = []
    for name, root in [("ListPaneMobile", lp), ("DetailPaneMobile", dp)]:
        for ctrl in _walk_controls(root):
            if isinstance(ctrl, ft.Row) and bool(getattr(ctrl, "wrap", False)):
                for idx, ch in enumerate(ctrl.controls):
                    if getattr(ch, "expand", None):
                        wrap_violations.append(f"{name}.Row[{idx}]")
        for path, ctrl in _walk_all_flet_fields(root, name):
            hints = _mro_annotations(type(ctrl))
            for f in dataclasses.fields(ctrl):
                if f.name.startswith("_") or f.name not in hints:
                    continue
                val = getattr(ctrl, f.name, None)
                if val is not None and not _matches_flet_type(val, hints[f.name]):
                    mro_mismatches.append((f"{path}.{type(ctrl).__name__}.{f.name}", type(val), hints[f.name]))

    assert wrap_violations == []
    assert mro_mismatches == []
    assert math.isfinite(state.viewport_height)

    # F6: _refresh_btn has >= 44dp touch constraints; _chips_row wraps in portrait and scrolls in compact height
    assert lp._refresh_btn.size_constraints.min_width >= 44
    assert lp._refresh_btn.size_constraints.min_height >= 44
    assert lp._chips_row.wrap is False
    assert lp._chips_row.scroll == ft.ScrollMode.AUTO

    portrait_state = CoursesState(
        courses=[course],
        layout_mode="mobile",
        viewport_height=780.0,
    )
    lp.render(portrait_state, update=False)
    assert lp._chips_row.wrap is True
    assert lp._chips_row.scroll is None


def test_app_controller_responsive_layout_prefers_viewport_size_and_updates_grade_view():
    ctrl = AppController.__new__(AppController)
    ctrl.page = _fake_page(width=360, height=780)
    ctrl._nav_shell = AdaptiveNavShell(on_navigate=lambda _k: None)

    viewport_calls = []
    ctrl.courses_view = SimpleNamespace(
        set_viewport_size=lambda w, h: viewport_calls.append((w, h))
    )
    grade_widths = []
    ctrl.grade_overview_view = SimpleNamespace(set_width=grade_widths.append)

    ctrl._apply_responsive_layout()
    assert viewport_calls == [(360.0, 780.0)]
    assert grade_widths == [360.0]
