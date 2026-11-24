"""Adaptive Master-Detail Courses workspace orchestrating catalog and detail panes."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import inspect
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

import flet as ft

from core.courses.models import (
    CourseAttachment,
    CourseModuleItem,
    CourseSummary,
    RecentMaterialEntry,
)
from gui.components.courses._shared import PaneLoadingOverlay, build_viewer_header
from gui.components.courses.adaptive_nav import RAIL_BREAKPOINT, RAIL_WIDTH
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.course_list_pane import CourseListPane
from gui.core.theme import C, refresh_semantic_control_colors, semantic_palette
from gui.view_models.courses_state import (
    CoursesLayoutMode,
    CoursesState,
    compute_courses_layout_mode,
    extract_readable_html_and_links,
    format_file_size,
    is_compact_height,
    real_module_attachments,
    safe_http_url,
)
import platform_utils

if TYPE_CHECKING:
    from core.courses.catalog_service import CourseCatalogService

logger = logging.getLogger(__name__)


def _format_err(exc: BaseException) -> str:
    return str(exc).strip() or type(exc).__name__


async def _invoke_callback(fn: Optional[Callable[..., Any]], *args: Any) -> Any:
    if not callable(fn):
        return None
    result = fn(*args)
    if inspect.isawaitable(result):
        return await result
    return result


class CoursesTabView(ft.Container):
    """Master-Detail Courses tab container with overlay stack and responsive modes."""

    CATALOG_STALE_HOURS = 6

    def __init__(
        self,
        *,
        page: Optional[ft.Page] = None,
        catalog_service: Optional[CourseCatalogService] = None,
        on_open_assignment: Optional[Callable[[CourseSummary, CourseModuleItem], Any]] = None,
        on_open_webview_activity: Optional[Callable[[CourseSummary, CourseModuleItem], Any]] = None,
        on_open_attachment_viewer: Optional[
            Callable[[CourseSummary, CourseModuleItem, CourseAttachment], Any]
        ] = None,
        on_download_attachment: Optional[Callable[[CourseSummary, CourseAttachment], Any]] = None,
        on_launch_url: Optional[Callable[[str], Any]] = None,
        on_open_inline_page: Optional[Callable[[CourseSummary, CourseModuleItem], Any]] = None,
        on_open_custom_activity: Optional[Callable[[CourseSummary, CourseModuleItem], Any]] = None,
        on_course_loaded: Optional[Callable[[CourseSummary, list[Any]], Any]] = None,
        on_immersive_changed: Optional[Callable[[bool], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(expand=True, **kwargs)
        self._page_ref = page
        self._svc = catalog_service
        self._on_open_assignment = on_open_assignment
        self._on_open_webview_activity = on_open_webview_activity
        self._on_open_attachment_viewer = on_open_attachment_viewer
        self._on_download_attachment = on_download_attachment
        self._on_launch_url = on_launch_url
        self._on_open_inline_page = on_open_inline_page
        self._on_open_custom_activity = on_open_custom_activity
        self._on_course_loaded = on_course_loaded
        self._on_immersive_changed = on_immersive_changed

        self._state = CoursesState()
        self._current_width: float = 1100.0
        self._layout_mode: CoursesLayoutMode = "wide"
        self._current_height: float = 760.0
        self._compact_height: bool = False
        self._has_explicit_width: bool = False
        self._immersive_active: bool = False
        self._tab_hidden: bool = False
        self._state.layout_mode = self._layout_mode
        self._state.viewport_height = self._current_height
        self._overlays: list[ft.Control] = []
        self._theme_snapshot = semantic_palette()
        self._catalog_request_seq = 0
        self._detail_request_seq = 0
        self._grade_request_seq = 0
        self._module_open_seq = 0
        self._user_chose_semester = False

        self._list_pane = CourseListPane(
            on_select_course=lambda key: self._schedule_task(
                self._on_user_select_course_async, key
            ),
            on_toggle_pin=lambda key: self._schedule_task(self._toggle_pin_async, key),
            on_open_recent=lambda entry: self._schedule_task(self._open_recent_async, entry),
            on_filter_change=self._handle_filter_change,
            on_refresh_catalog=self.refresh,
        )
        self._detail_pane = CourseDetailPane(
            on_back_mobile=self.handle_back,
            on_refresh_course=lambda: self._schedule_task(self._refresh_single_course_async),
            on_open_module=lambda mod: self._schedule_task(self._handle_open_module_async, mod),
            on_download_attachment=lambda att: self._schedule_task(
                self._download_selected_attachment_async, att
            ),
            on_toggle_completion=lambda mod, done: self._schedule_task(
                self._toggle_completion_async, mod, done
            ),
            on_copy_text=lambda text: self._schedule_task(self._copy_text_async, text),
            on_launch_url=lambda url: self._schedule_task(self._launch_url_safe, url),
            on_subtab_change=lambda idx: self._schedule_task(self._on_subtab_change_async, idx),
            on_toggle_expand=self.toggle_pane3_expanded,
        )

        self._workspace_stack = ft.Stack(expand=True, controls=[self._detail_pane])
        self._list_pane_host = ft.Container(
            content=self._list_pane,
            width=320,
            opacity=1.0,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
            animate_opacity=ft.Animation(180, ft.AnimationCurve.EASE_OUT),
        )
        self._split_divider = ft.VerticalDivider(
            width=1, thickness=1, color=C.BORDER, visible=True
        )
        self._pane3_host = ft.Container(expand=True)
        self._wide_row = ft.Row(
            expand=True,
            spacing=0,
            controls=[self._list_pane_host, self._split_divider, self._pane3_host],
        )

        self._tablet_content = ft.Container(expand=True, width=820.0)
        self._tablet_wrapper = ft.Container(
            expand=True, alignment=ft.Alignment.TOP_CENTER, content=self._tablet_content
        )
        self._narrow_list_host = ft.Container(expand=True)
        self._narrow_workspace_host = ft.Container(
            expand=True, bgcolor=C.BG, visible=False
        )
        self._narrow_stack = ft.Stack(
            expand=True,
            controls=[self._narrow_list_host, self._narrow_workspace_host],
        )
        self._main_host = ft.Container(expand=True)
        # Keep _workspace_stack permanently mounted at a constant depth inside
        # _root_stack so rotating a tablet (wide <-> tablet_portrait) or entering
        # Samsung Pop-up View (<600dp mobile) never unmounts active PlatformViews.
        self._workspace_host = ft.Container(
            expand=True, bgcolor=C.BG, content=self._workspace_stack
        )
        self._workspace_layer = ft.Container(
            left=321.0,
            top=0.0,
            right=0.0,
            bottom=0.0,
            opacity=1.0,
            ignore_interactions=False,
            animate_position=ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC),
            content=self._workspace_host,
        )
        self._root_stack = ft.Stack(
            expand=True, controls=[self._main_host, self._workspace_layer]
        )

        self.bgcolor = C.BG
        self.content = self._root_stack
        self._rebuild_layout()

    @property
    def control(self) -> ft.Control:
        """Return root Flet control for host mounting."""
        return self

    def _get_page(self) -> Optional[ft.Page]:
        if self._page_ref is not None:
            return self._page_ref
        try:
            return self.page
        except RuntimeError:
            return None

    def _safe_update(self) -> None:
        try:
            if self.page is not None:
                self.update()
                return
        except Exception:
            pass
        if self._page_ref is not None and hasattr(self._page_ref, "update"):
            try:
                self._page_ref.update()
            except Exception:
                pass

    def _schedule_task(self, coro_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
        page = self._get_page()
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            run_task(coro_fn, *args, **kwargs)
            return True
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_fn(*args, **kwargs))
            return True
        except RuntimeError:
            return False

    def _dispatch_callback(self, fn: Optional[Callable[..., Any]], *args: Any) -> None:
        if not callable(fn):
            return
        if inspect.iscoroutinefunction(fn):
            self._schedule_task(fn, *args)
            return
        result = fn(*args)
        if inspect.isawaitable(result):
            async def _await_res() -> None:
                await result

            if not self._schedule_task(_await_res) and inspect.iscoroutine(result):
                result.close()

    def _collect_provider_notice(
        self, course: Optional[CourseSummary] = None, explicit_error: str = ""
    ) -> str:
        if explicit_error:
            return explicit_error
        if self._svc is None:
            return ""
        courses_err = str(getattr(self._svc, "last_courses_error", "") or "").strip()
        thnn_err = str(getattr(self._svc, "last_thnn_error", "") or "").strip()
        if course is not None:
            return thnn_err if course.source == "thnn" else courses_err
        return " | ".join(p for p in (courses_err, thnn_err) if p)

    def _rebuild_layout(self) -> None:
        self._workspace_host.bgcolor = C.BG
        if self._layout_mode == "wide":
            expanded = bool(self._state.pane3_expanded)
            self._tablet_content.content = None
            self._narrow_list_host.content = None
            self._narrow_workspace_host.visible = False
            self._list_pane_host.content = self._list_pane
            self._list_pane_host.width = 0 if expanded else 320
            self._list_pane_host.opacity = 0.0 if expanded else 1.0
            self._split_divider.color = C.BORDER
            self._split_divider.visible = not expanded
            self._main_host.content = self._wide_row
            self._workspace_layer.left = 0.0 if expanded else 321.0
            self._workspace_layer.right = 0.0
            self._workspace_layer.opacity = 1.0
            self._workspace_layer.ignore_interactions = False
            return

        self._list_pane_host.content = None
        show_workspace = bool(self._overlays) or (
            self._state.in_detail_mobile and self._state.selected_course() is not None
        )
        self._narrow_list_host.content = self._list_pane
        self._narrow_list_host.opacity = 0.0 if show_workspace else 1.0
        self._narrow_list_host.ignore_interactions = show_workspace
        self._narrow_workspace_host.bgcolor = C.BG
        self._narrow_workspace_host.visible = show_workspace
        if self._layout_mode == "tablet_portrait":
            tablet_w = min(820.0, max(320.0, self._current_width))
            side_pad = max(0.0, (self._current_width - tablet_w) / 2.0)
            self._tablet_content.content = self._narrow_stack
            self._tablet_content.width = tablet_w
            self._main_host.content = self._tablet_wrapper
            self._workspace_layer.left = side_pad
            self._workspace_layer.right = side_pad
        else:
            self._tablet_content.content = None
            self._main_host.content = self._narrow_stack
            self._workspace_layer.left = 0.0
            self._workspace_layer.right = 0.0
        self._workspace_layer.opacity = 1.0 if show_workspace else 0.0
        self._workspace_layer.ignore_interactions = not show_workspace

    def set_on_immersive_changed(
        self, callback: Optional[Callable[[bool], None]]
    ) -> None:
        """Attach or replace the callback notifying shell nav of mobile immersive overlays."""
        self._on_immersive_changed = callback
        self._sync_immersive_state(force=True)

    def _compute_immersive(self) -> bool:
        if (
            not self._overlays
            or self._tab_hidden
            or not bool(getattr(self, "visible", True))
        ):
            return False
        return self._layout_mode == "mobile" or (
            bool(platform_utils.UI_IS_MOBILE) and not self._has_explicit_width
        )

    def _sync_immersive_state(self, *, force: bool = False) -> None:
        desired = self._compute_immersive()
        if not force and desired == self._immersive_active:
            return
        self._immersive_active = desired
        if callable(self._on_immersive_changed):
            try:
                self._on_immersive_changed(desired)
            except Exception:
                logger.debug("on_immersive_changed callback failed", exc_info=True)

    def _overlay_pane_width(self) -> float:
        if self._has_explicit_width and self._current_width > 0:
            if self._layout_mode == "wide":
                list_w = 0.0 if self._state.pane3_expanded else 321.0
                return max(240.0, self._current_width - list_w)
            if self._layout_mode == "tablet_portrait":
                return min(820.0, max(240.0, self._current_width))
            return max(240.0, self._current_width)
        return self._compute_pane3_bounds()[2]

    def _sync_overlay_widths(self) -> None:
        if not self._overlays:
            return
        pane_w = self._overlay_pane_width()
        for item in self._overlays:
            setter = getattr(item, "set_width", None)
            if callable(setter):
                setter(pane_w)

    def _compute_pane3_bounds(self) -> tuple[float, float, float, float]:
        page = self._get_page()
        raw_page_w = float(
            getattr(page, "width", 0)
            or getattr(getattr(page, "window", None), "width", 0)
            or 0.0
        )
        page_h = float(
            getattr(page, "height", 0)
            or getattr(getattr(page, "window", None), "height", 0)
            or self._current_height
            or 760.0
        )
        is_web = bool(getattr(page, "web", False))
        if raw_page_w > 0:
            has_rail = raw_page_w >= float(RAIL_BREAKPOINT)
            rail_w = float(RAIL_WIDTH + 1) if has_rail else 0.0
            content_w = max(240.0, raw_page_w - rail_w)
        elif self._current_width > 0:
            content_w = self._current_width
            has_rail = (content_w + float(RAIL_WIDTH)) >= float(RAIL_BREAKPOINT)
            rail_w = float(RAIL_WIDTH + 1) if has_rail else 0.0
        else:
            has_rail = True
            rail_w = float(RAIL_WIDTH + 1)
            content_w = 1120.0 - rail_w
        list_w = (
            321.0
            if (self._layout_mode == "wide" and not self._state.pane3_expanded)
            else 0.0
        )
        if self._layout_mode == "tablet_portrait":
            tablet_w = min(820.0, max(320.0, content_w))
            left = rail_w + max(0.0, (content_w - tablet_w) / 2.0)
            width = max(240.0, tablet_w)
        else:
            left = rail_w + list_w
            width = max(240.0, content_w - list_w)
        top = 0.0 if is_web else 36.0
        bottom_inset = 0.0 if (has_rail or self._immersive_active) else 64.0
        height = max(200.0, page_h - top - bottom_inset)
        return (left, top, width, height)

    def _sync_pane3_bounds(self) -> None:
        if not self._overlays:
            return
        top = self._overlays[-1]
        sync_fn = getattr(top, "sync_bounds", None)
        if callable(sync_fn):
            left, top_px, width, height = self._compute_pane3_bounds()
            page = self._get_page()
            raw_page_w = float(
                getattr(page, "width", 0)
                or getattr(getattr(page, "window", None), "width", 0)
                or 0.0
            )
            has_rail = (
                raw_page_w >= float(RAIL_BREAKPOINT)
                if raw_page_w > 0
                else (
                    (self._current_width + float(RAIL_WIDTH)) >= float(RAIL_BREAKPOINT)
                    if self._current_width > 0
                    else True
                )
            )
            nav_inset = 0.0 if (has_rail or self._immersive_active) else 64.0
            try:
                sig = inspect.signature(sync_fn)
                if "bottom_inset" in sig.parameters:
                    sync_fn(left, top_px, width, height, bottom_inset=nav_inset)
                    return
                if "nav_bottom_inset" in sig.parameters:
                    sync_fn(left, top_px, width, height, nav_bottom_inset=nav_inset)
                    return
            except (TypeError, ValueError):
                pass
            sync_fn(left, top_px, width, height)

    def _sync_overlay_expand_buttons(self) -> None:
        show_toggle = self._layout_mode == "wide"
        expanded = bool(self._state.pane3_expanded)
        for item in self._overlays:
            setter = getattr(item, "set_expanded", None)
            if callable(setter):
                setter(expanded, visible=show_toggle)

    @property
    def pane3_expanded(self) -> bool:
        return bool(self._state.pane3_expanded)

    def set_pane3_expanded(self, expanded: bool) -> None:
        """Expand Pane 3 over Pane 2 (2+3) or collapse back to 3 in wide mode."""
        target = bool(expanded) if self._layout_mode == "wide" else False
        if self._state.pane3_expanded == target:
            return
        self._state.pane3_expanded = target
        self._sync_overlay_expand_buttons()
        self._sync_overlay_widths()
        self._render()
        self._sync_pane3_bounds()

    def toggle_pane3_expanded(self) -> None:
        """Toggle Pane 3 expanded state between default (3) and full-course (2+3)."""
        self.set_pane3_expanded(not self._state.pane3_expanded)

    def on_tab_hidden(self) -> None:
        """Suspend active native overlays (e.g. WebView2) when switching away."""
        self._tab_hidden = True
        self._sync_immersive_state()
        for item in self._overlays:
            suspend_fn = getattr(item, "suspend", None)
            if callable(suspend_fn):
                suspend_fn()

    def on_tab_shown(self) -> None:
        """Resume top active overlay (e.g. WebView2) when returning to Courses tab."""
        self._tab_hidden = False
        self._sync_immersive_state()
        if not self._overlays:
            return
        for item in self._overlays[:-1]:
            suspend_fn = getattr(item, "suspend", None)
            if callable(suspend_fn):
                suspend_fn()
        resume_fn = getattr(self._overlays[-1], "resume", None)
        if callable(resume_fn):
            resume_fn()
        self._sync_pane3_bounds()

    def _render(self) -> None:
        self._state.layout_mode = self._layout_mode
        self._state.viewport_height = self._current_height
        self._rebuild_layout()
        self._list_pane.render(self._state, update=False)
        self._detail_pane.render(
            self._state, show_back=(self._layout_mode != "wide"), update=False
        )
        self._safe_update()
        self._sync_pane3_bounds()

    def _apply_width_state(self, width: float) -> None:
        try:
            numeric = float(width)
        except (TypeError, ValueError):
            numeric = 0.0
        self._has_explicit_width = True
        self._current_width = numeric if numeric > 0 else 0.0
        self._layout_mode = compute_courses_layout_mode(width)
        self._state.layout_mode = self._layout_mode
        if self._layout_mode != "wide" and self._state.pane3_expanded:
            self._state.pane3_expanded = False
        self._sync_overlay_expand_buttons()
        self._sync_overlay_widths()
        self._sync_immersive_state()

    def _apply_height_state(self, height: float) -> None:
        if height is not None and not isinstance(height, bool):
            try:
                numeric = float(height)
            except (TypeError, ValueError):
                numeric = 0.0
            if numeric > 0:
                self._current_height = numeric
        self._compact_height = is_compact_height(self._current_height)
        self._state.viewport_height = self._current_height

    def set_width(self, width: float) -> None:
        """Apply viewport width and adapt Master-Detail layout breakpoint."""
        self._apply_width_state(width)
        self._render()

    def set_viewport_height(self, height: float) -> None:
        """Apply viewport height and adapt compact landscape presentation."""
        self._apply_height_state(height)
        self._render()

    def set_height(self, height: float) -> None:
        """Alias for set_viewport_height."""
        self.set_viewport_height(height)

    def set_viewport_size(self, width: float, height: float) -> None:
        """Apply both viewport width and height in a single render pass."""
        self._apply_width_state(width)
        self._apply_height_state(height)
        self._render()

    @staticmethod
    def _dispose_overlay(control: ft.Control) -> None:
        dispose_fn = getattr(control, "dispose", None)
        if callable(dispose_fn):
            try:
                dispose_fn()
            except Exception:
                pass

    def push_overlay(self, control: ft.Control, *, tag: str = "") -> None:
        """Push an in-app viewer/activity pane onto Pane 3, preserving Panes 1 & 2."""
        for existing in self._overlays:
            suspend_fn = getattr(existing, "suspend", None)
            if callable(suspend_fn):
                suspend_fn()
        if tag:
            control.data = tag
            is_modal = tag.startswith("courses_modal:")
            kept: list[ft.Control] = []
            for item in self._overlays:
                item_tag = str(getattr(item, "data", "") or "")
                should_drop = item_tag == tag or (
                    is_modal and item_tag.startswith("courses_modal:")
                )
                if should_drop:
                    self._dispose_overlay(item)
                else:
                    kept.append(item)
            self._overlays = kept
        if hasattr(control, "opacity") and getattr(control, "animate_opacity", None) is None:
            try:
                control.animate_opacity = ft.Animation(180, ft.AnimationCurve.EASE_OUT)
            except Exception:
                pass
        width_setter = getattr(control, "set_width", None)
        if callable(width_setter):
            width_setter(self._overlay_pane_width())
        setter = getattr(control, "set_expanded", None)
        if callable(setter):
            setter(self._state.pane3_expanded, visible=(self._layout_mode == "wide"))
        self._overlays.append(control)
        self._workspace_stack.controls = [self._detail_pane, *self._overlays]
        self._rebuild_layout()
        self._sync_immersive_state()
        self._safe_update()
        self._sync_pane3_bounds()

    def pop_overlay(self) -> bool:
        """Pop top Pane-3 overlay if present; return True if popped."""
        if not self._overlays:
            return False
        removed = self._overlays.pop()
        self._dispose_overlay(removed)
        self._workspace_stack.controls = [self._detail_pane, *self._overlays]
        if self._overlays:
            resume_fn = getattr(self._overlays[-1], "resume", None)
            if callable(resume_fn):
                resume_fn()
        self._rebuild_layout()
        self._sync_immersive_state()
        self._safe_update()
        self._sync_pane3_bounds()
        return True

    def clear_overlays(self, *, update: bool = True) -> None:
        """Dispose and remove all Pane-3 overlays."""
        if not self._overlays:
            return
        for item in self._overlays:
            self._dispose_overlay(item)
        self._overlays.clear()
        self._workspace_stack.controls = [self._detail_pane]
        self._rebuild_layout()
        self._sync_immersive_state()
        if update:
            self._safe_update()

    def reset_for_account_switch(self) -> None:
        """Clear overlays and cached course state when the signed-in account changes."""
        self._catalog_request_seq += 1
        self._detail_request_seq += 1
        self._grade_request_seq += 1
        self._module_open_seq += 1
        self._user_chose_semester = False
        self.clear_overlays(update=False)
        self._state = CoursesState(
            layout_mode=self._layout_mode,
            viewport_height=self._current_height,
        )
        self._render()

    def handle_back(self) -> bool:
        """Delegate back to top overlay sub-stack, dismiss overlay, collapse 2+3 expand, or exit mobile detail."""
        if self._overlays:
            top = self._overlays[-1]
            top_back = getattr(top, "handle_back", None)
            if callable(top_back):
                try:
                    if top_back():
                        return True
                except Exception:
                    logger.exception("Overlay handle_back() failed")
            return self.pop_overlay()
        if self._layout_mode == "wide" and self._state.pane3_expanded:
            self.set_pane3_expanded(False)
            return True
        if self._layout_mode != "wide" and self._state.in_detail_mobile:
            self._state.in_detail_mobile = False
            self._render()
            return True
        return False

    def _apply_smart_default_semester(self, courses: list[CourseSummary]) -> None:
        """Default to current-semester courses when Portal registration or current-term flags exist."""
        if self._user_chose_semester or not courses:
            return
        has_portal = bool(getattr(self._svc, "has_portal_current_term", False))
        has_explicit_current = any(c.is_current_term for c in courses)
        if (has_portal or has_explicit_current) and self._state.semester_filter == "all":
            self._state.semester_filter = "current"

    def load_initial(self) -> None:
        """Schedule initial 0ms cached catalog load and background staleness check."""
        if self._schedule_task(self.load_initial_async):
            return
        if self._svc is not None:
            try:
                courses, recent, synced_at = self._svc.load_cached_catalog()
                self._state.set_courses(courses)
                self._apply_smart_default_semester(courses)
                self._state.recent_items = list(recent)
                if self._state.selected_course() is None:
                    self._state.last_synced_at = synced_at
                self._state.offline_notice = self._collect_provider_notice()
            except Exception as exc:
                logger.warning("Failed to load cached catalog: %s", exc)
                self._state.offline_notice = f"Không thể tải danh sách môn học: {_format_err(exc)}"
            self._render()

    async def load_initial_async(self) -> None:
        """Load cached catalog immediately and refresh if stale or empty."""
        if self._svc is None:
            return
        cat_seq = self._catalog_request_seq
        try:
            courses, recent, synced_at = await asyncio.to_thread(self._svc.load_cached_catalog)
            if cat_seq != self._catalog_request_seq:
                return
            self._state.set_courses(courses)
            self._apply_smart_default_semester(courses)
            self._state.recent_items = list(recent)
            if self._state.selected_course() is None:
                self._state.last_synced_at = synced_at
            self._state.offline_notice = self._collect_provider_notice()
            self._render()

            stale = await asyncio.to_thread(self._svc.is_catalog_stale, self.CATALOG_STALE_HOURS)
            has_portal = bool(getattr(self._svc, "has_portal_current_term", False))
            missing_portal_flags = has_portal and bool(courses) and not any(
                c.is_current_term for c in courses
            )
            if cat_seq != self._catalog_request_seq:
                return
            if stale or not courses or missing_portal_flags:
                await self.refresh_catalog_async(include_thnn=True)
        except Exception as exc:
            logger.warning("Initial catalog load failed: %s", exc)
            if cat_seq == self._catalog_request_seq:
                self._state.offline_notice = f"Không thể tải danh sách môn học: {_format_err(exc)}"
                self._render()

    def refresh(self) -> None:
        """Trigger asynchronous course catalog refresh."""
        self._schedule_task(self.refresh_catalog_async, True)

    async def refresh_catalog_async(self, include_thnn: bool = True) -> None:
        """Refresh course catalog from providers while preserving offline cache."""
        if self._svc is None:
            return
        self._catalog_request_seq += 1
        req_id = self._catalog_request_seq
        self._state.loading_catalog = True
        self._render()
        explicit_err = ""
        try:
            courses = await asyncio.to_thread(self._svc.refresh_catalog, include_thnn)
            if req_id != self._catalog_request_seq:
                return
            _, recent, synced_at = await asyncio.to_thread(self._svc.load_cached_catalog)
            if req_id != self._catalog_request_seq:
                return
            self._state.set_courses(courses)
            self._apply_smart_default_semester(courses)
            self._state.recent_items = list(recent)
            if self._state.selected_course() is None:
                self._state.last_synced_at = synced_at
        except Exception as exc:
            logger.warning("Failed to refresh course catalog: %s", exc)
            explicit_err = _format_err(exc)
        finally:
            if req_id == self._catalog_request_seq:
                self._state.offline_notice = self._collect_provider_notice(
                    explicit_error=explicit_err
                )
                self._state.loading_catalog = False
                self._render()

    def _handle_filter_change(self, semester: str, query: str) -> None:
        if semester != self._state.semester_filter:
            self._user_chose_semester = True
        self._state.semester_filter = semester
        self._state.search_query = query
        self._render()

    async def _toggle_pin_async(self, course_key: str) -> None:
        if self._svc is None:
            return
        try:
            courses = await asyncio.to_thread(self._svc.toggle_pin, course_key)
            self._state.set_courses(courses)
        except Exception as exc:
            logger.warning("Failed to toggle course pin (%s): %s", course_key, exc)
            self._state.offline_notice = f"Không thể ghim môn học: {_format_err(exc)}"
        self._render()

    def _is_active_detail(self, req_id: int, target_key: str) -> bool:
        return req_id == self._detail_request_seq and self._state.selected_course_key == target_key

    async def _on_user_select_course_async(self, course_key: str) -> Optional[int]:
        """Handle explicit tap on a course card in Pane 1 (dismisses any open Pane-3 overlay)."""
        if self._overlays:
            self.clear_overlays(update=False)
        return await self._select_course_async(course_key)

    async def _select_course_async(self, course_key: str) -> Optional[int]:
        course = next((c for c in self._state.courses if c.course_key == course_key), None)
        if course is None:
            return None
        self._detail_request_seq += 1
        self._grade_request_seq += 1
        req_id = self._detail_request_seq
        grade_req_id = self._grade_request_seq
        target_key = course.course_key
        prev_key = self._state.selected_course_key
        self._state.select_course(target_key)
        if target_key != prev_key:
            self._state.sections = []
            self._state.grades = []
            self._state.user_groups = []
            self._state.last_synced_at = 0
            self.clear_overlays(update=False)
        self._state.loading_detail = True
        self._state.offline_notice = ""
        self._render()

        if self._svc is None:
            self._state.loading_detail = False
            self._render()
            return req_id

        try:
            cached = await asyncio.to_thread(self._svc.open_course_cached, course.source, course.id)
            if not self._is_active_detail(req_id, target_key):
                return req_id
            self._state.sections = list(cached.sections)
            self._state.grades = list(cached.grades)
            self._state.last_synced_at = cached.last_synced_at
            if cached.sections:
                # 0ms instant cached view: clear blocking skeleton immediately while delta sync runs
                self._state.loading_detail = False
            self._render()

            if course.has_unseen_updates:
                updated_courses = await asyncio.to_thread(self._svc.mark_course_seen, target_key)
                self._state.set_courses(updated_courses)
                if not self._is_active_detail(req_id, target_key):
                    return req_id

            has_groups_api = hasattr(self._svc, "get_user_course_groups")
            if has_groups_api and course.source == "courses":
                res, groups = await asyncio.gather(
                    asyncio.to_thread(
                        self._svc.sync_course_delta, course.source, course.id, False
                    ),
                    asyncio.to_thread(
                        self._svc.get_user_course_groups, course.source, course.id
                    ),
                )
            else:
                res = await asyncio.to_thread(
                    self._svc.sync_course_delta, course.source, course.id, False
                )
                groups = (
                    await asyncio.to_thread(
                        self._svc.get_user_course_groups, course.source, course.id
                    )
                    if has_groups_api
                    else []
                )
            if not self._is_active_detail(req_id, target_key):
                return req_id
            self._state.sections = list(res.sections)
            if res.grades:
                self._state.grades = list(res.grades)
            self._state.user_groups = list(groups or [])
            self._state.last_synced_at = res.last_synced_at
            self._state.offline_notice = self._collect_provider_notice(
                course, explicit_error=res.error
            )

            if res.changed:
                refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
                self._state.set_courses(refreshed)
                if not self._is_active_detail(req_id, target_key):
                    return req_id

            if self._state.active_subtab == 2 and not self._state.grades:
                grades = await asyncio.to_thread(
                    self._svc.load_course_grades, course.source, course.id, False
                )
                if not self._is_active_detail(req_id, target_key) or (
                    grade_req_id != self._grade_request_seq
                ):
                    return req_id
                self._state.grades = list(grades)

            if self._state.sections and callable(self._on_course_loaded):
                self._dispatch_callback(
                    self._on_course_loaded, course, list(self._state.sections)
                )
        except Exception as exc:
            logger.warning("Failed to select course %s: %s", target_key, exc)
            if self._is_active_detail(req_id, target_key):
                self._state.offline_notice = f"Không thể tải nội dung môn học: {_format_err(exc)}"
        finally:
            if self._is_active_detail(req_id, target_key):
                self._state.loading_detail = False
                self._render()
        return req_id

    async def _refresh_single_course_async(self) -> None:
        course = self._state.selected_course()
        if course is None or self._svc is None:
            return
        self._detail_request_seq += 1
        self._grade_request_seq += 1
        req_id = self._detail_request_seq
        grade_req_id = self._grade_request_seq
        target_key = course.course_key
        self._state.loading_detail = True
        self._render()
        try:
            res = await asyncio.to_thread(
                self._svc.sync_course_delta, course.source, course.id, True
            )
            if not self._is_active_detail(req_id, target_key):
                return
            self._state.sections = list(res.sections)
            self._state.last_synced_at = res.last_synced_at
            if self._state.active_subtab == 2:
                grades = await asyncio.to_thread(
                    self._svc.load_course_grades, course.source, course.id, True
                )
                if not self._is_active_detail(req_id, target_key) or (
                    grade_req_id != self._grade_request_seq
                ):
                    return
                self._state.grades = list(grades)
            refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
            self._state.set_courses(refreshed)
            if not self._is_active_detail(req_id, target_key):
                return
            self._state.offline_notice = self._collect_provider_notice(
                course, explicit_error=res.error
            )
        except Exception as exc:
            logger.warning("Failed to refresh course %s: %s", target_key, exc)
            if self._is_active_detail(req_id, target_key):
                self._state.offline_notice = f"Không thể đồng bộ môn học: {_format_err(exc)}"
        finally:
            if self._is_active_detail(req_id, target_key):
                self._state.loading_detail = False
                self._render()

    async def _on_subtab_change_async(self, index: int) -> None:
        self._state.active_subtab = index
        course = self._state.selected_course()
        if index != 2 or self._svc is None or course is None:
            self._detail_pane.sync_subtab(index, update=False)
            self._safe_update()
            return
        self._grade_request_seq += 1
        grade_req_id = self._grade_request_seq
        target_key = course.course_key
        self._state.loading_detail = True
        self._render()
        try:
            grades = await asyncio.to_thread(
                self._svc.load_course_grades, course.source, course.id, False
            )
            if grade_req_id != self._grade_request_seq or (
                self._state.selected_course_key != target_key
            ):
                return
            self._state.grades = list(grades)
            self._state.offline_notice = self._collect_provider_notice(course)
        except Exception as exc:
            logger.warning("Failed to load grades for %s: %s", target_key, exc)
            if grade_req_id == self._grade_request_seq and (
                self._state.selected_course_key == target_key
            ):
                self._state.offline_notice = f"Không thể tải điểm môn học: {_format_err(exc)}"
        finally:
            if grade_req_id == self._grade_request_seq and (
                self._state.selected_course_key == target_key
            ):
                self._state.loading_detail = False
                self._render()

    async def _toggle_completion_async(self, mod: CourseModuleItem, completed: bool) -> None:
        course = self._state.selected_course()
        if course is None or self._svc is None:
            return
        target_key = course.course_key
        detail_req_id = self._detail_request_seq
        try:
            ok = await asyncio.to_thread(
                self._svc.toggle_completion, course.source, course.id, mod.cmid, completed
            )
            if not self._is_active_detail(detail_req_id, target_key):
                return
            if ok:
                cached = await asyncio.to_thread(
                    self._svc.open_course_cached, course.source, course.id
                )
                if not self._is_active_detail(detail_req_id, target_key):
                    return
                self._state.sections = list(cached.sections)
                refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
                self._state.set_courses(refreshed)
                if not self._is_active_detail(detail_req_id, target_key):
                    return
            else:
                self._state.offline_notice = (
                    self._collect_provider_notice(course)
                    or "Không thể cập nhật trạng thái hoàn thành."
                )
        except Exception as exc:
            logger.warning("Failed to toggle completion (cmid=%s): %s", mod.cmid, exc)
            if not self._is_active_detail(detail_req_id, target_key):
                return
            self._state.offline_notice = f"Không thể cập nhật hoàn thành: {_format_err(exc)}"
        self._render()

    async def _record_recent_best_effort(
        self,
        course: CourseSummary,
        mod: CourseModuleItem,
        attachment: Optional[CourseAttachment] = None,
    ) -> None:
        if self._svc is None or not hasattr(self._svc, "record_recent"):
            return
        entry = RecentMaterialEntry(
            course_key=course.course_key,
            course_name=course.display_name or course.fullname,
            cmid=mod.cmid,
            title=mod.name,
            attachment_url=attachment.file_url if attachment else (mod.url or mod.web_url),
            file_name=attachment.file_name if attachment else "",
            opened_at=int(time.time()),
            source=course.source,
            course_id=course.id,
        )
        try:
            updated = await asyncio.to_thread(self._svc.record_recent, entry)
            self._state.recent_items = list(updated)
            self._list_pane.render(self._state, update=False)
            self._safe_update()
        except Exception:
            logger.debug("Ignoring record_recent failure", exc_info=True)

    async def _open_recent_async(self, entry: RecentMaterialEntry) -> None:
        try:
            started_seq = await self._select_course_async(entry.course_key)
            if (
                started_seq is None
                or started_seq != self._detail_request_seq
                or self._state.selected_course_key != entry.course_key
            ):
                return
            if self._layout_mode != "wide" and not self._state.in_detail_mobile:
                return
            for sec in self._state.sections:
                for mod in sec.modules:
                    if mod.cmid == entry.cmid:
                        await self._handle_open_module_async(mod)
                        return
        except Exception as exc:
            logger.warning("Failed to open recent material (%s): %s", entry.cmid, exc)
            self._state.offline_notice = f"Không thể mở tài liệu gần đây: {_format_err(exc)}"
            self._render()

    async def _download_selected_attachment_async(self, att: CourseAttachment) -> None:
        course = self._state.selected_course()
        if course is not None:
            await _invoke_callback(self._on_download_attachment, course, att)

    async def _copy_text_async(self, text: str) -> None:
        if not text:
            return
        try:
            await ft.Clipboard().set(text)
        except Exception:
            logger.debug("Clipboard copy failed", exc_info=True)
            return
        page = self._get_page()
        if page is None:
            return
        sb = ft.SnackBar(
            content=ft.Text(f"Đã sao chép: {text}", size=13, color=C.TEXT_PRIMARY),
            bgcolor=C.SURFACE,
            duration=2000,
        )
        overlay = getattr(page, "overlay", None)
        if overlay is not None:
            overlay[:] = [item for item in overlay if not isinstance(item, ft.SnackBar)]

            def _on_dismiss(_e=None) -> None:
                sb.open = False
                try:
                    overlay.remove(sb)
                except ValueError:
                    pass

            sb.on_dismiss = _on_dismiss
            overlay.append(sb)
            sb.open = True
        else:
            show_dialog = getattr(page, "show_dialog", None)
            if not callable(show_dialog):
                return
            try:
                show_dialog(sb)
            except (AttributeError, TypeError):
                return
        if callable(getattr(page, "update", None)):
            try:
                page.update()
            except Exception:
                pass

    async def _launch_url_safe(self, url: str) -> None:
        safe = safe_http_url(url)
        if safe:
            await _invoke_callback(self._on_launch_url, safe)

    def _handle_inline_link_click(self, href: str) -> None:
        safe = safe_http_url(href)
        if safe:
            self._dispatch_callback(self._on_launch_url, safe)

    def _handle_folder_item_click(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        if self._schedule_task(self._open_folder_attachment_async, course, mod, att):
            return
        target = self._on_open_attachment_viewer
        if callable(target):
            self._dispatch_callback(target, course, mod, att)
        else:
            self._dispatch_callback(self._on_download_attachment, course, att)

    def _handle_folder_download_click(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        if self._schedule_task(self._download_folder_attachment_async, course, mod, att):
            return
        self._dispatch_callback(self._on_download_attachment, course, att)

    async def _open_folder_attachment_async(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        await self._record_recent_best_effort(course, mod, att)
        if callable(self._on_open_attachment_viewer):
            await _invoke_callback(self._on_open_attachment_viewer, course, mod, att)
        else:
            await _invoke_callback(self._on_download_attachment, course, att)

    async def _download_folder_attachment_async(
        self, course: CourseSummary, mod: CourseModuleItem, att: CourseAttachment
    ) -> None:
        await self._record_recent_best_effort(course, mod, att)
        await _invoke_callback(self._on_download_attachment, course, att)

    def _push_inline_reader_overlay(self, mod: CourseModuleItem) -> None:
        body_text, links = extract_readable_html_and_links(
            mod.inline_html or mod.description_html
        )
        link_controls: list[ft.Control] = [
            ft.TextButton(
                content=label,
                icon=ft.Icons.OPEN_IN_NEW_ROUNDED,
                tooltip=href,
                on_click=lambda _e, u=href: self._handle_inline_link_click(u),
            )
            for label, href in links
        ]
        body_controls: list[ft.Control] = [
            ft.Text(
                body_text or "Không có nội dung hiển thị.",
                size=14,
                color=C.TEXT_PRIMARY,
                selectable=True,
            ),
            *([ft.Divider(color=C.BORDER), *link_controls] if link_controls else []),
        ]
        self.push_overlay(
            self._build_modal_overlay(
                mod.name, body_controls, subtitle="Nội dung chi tiết"
            ),
            tag=f"courses_modal:inline:{mod.cmid}",
        )

    def _push_folder_overlay(self, course: CourseSummary, mod: CourseModuleItem) -> None:
        rows: list[ft.Control] = []
        for att in mod.attachments:
            size_label = format_file_size(att.file_size)
            rows.append(
                ft.Container(
                    bgcolor=C.SURFACE_ELEVATED,
                    border=ft.Border.all(1, C.BORDER),
                    border_radius=10,
                    padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                    ink=True,
                    on_click=lambda _e, item=att: self._handle_folder_item_click(
                        course, mod, item
                    ),
                    content=ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.INSERT_DRIVE_FILE_OUTLINED, size=18, color=C.ACCENT),
                            ft.Column(
                                expand=True,
                                spacing=1,
                                controls=[
                                    ft.Text(
                                        att.file_name,
                                        size=13,
                                        color=C.TEXT_PRIMARY,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    *(
                                        [ft.Text(size_label, size=11, color=C.TEXT_SECONDARY)]
                                        if size_label
                                        else []
                                    ),
                                ],
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DOWNLOAD_ROUNDED,
                                icon_size=18,
                                icon_color=C.TEXT_SECONDARY,
                                tooltip="Tải xuống",
                                on_click=lambda _e, item=att: self._handle_folder_download_click(
                                    course, mod, item
                                ),
                            ),
                        ],
                    ),
                )
            )
        self.push_overlay(
            self._build_modal_overlay(
                mod.name,
                rows,
                subtitle=f"{len(mod.attachments)} tệp đính kèm",
            ),
            tag=f"courses_modal:folder:{mod.cmid}",
        )

    def _build_modal_overlay(
        self,
        title: str,
        body_controls: list[ft.Control],
        *,
        subtitle: str = "",
    ) -> ft.Control:
        is_mobile = self._layout_mode == "mobile" or (
            bool(platform_utils.UI_IS_MOBILE) and not self._has_explicit_width
        )
        if is_mobile:
            header_ctrl, _back_btn, _title_txt, _sub_txt = build_viewer_header(
                title=title or "Chi tiết",
                subtitle=subtitle,
                back_tooltip="Quay lại",
                on_back=lambda _e: self.pop_overlay(),
                trailing_actions=[],
            )
            return ft.Container(
                expand=True,
                bgcolor=C.BG,
                content=ft.Column(
                    expand=True,
                    spacing=0,
                    controls=[
                        header_ctrl,
                        ft.Container(
                            expand=True,
                            padding=ft.Padding.all(14),
                            content=ft.Column(
                                expand=True,
                                spacing=8,
                                scroll=ft.ScrollMode.AUTO,
                                controls=body_controls,
                            ),
                        ),
                    ],
                ),
            )

        modal_width = (
            min(720.0, max(280.0, self._current_width - 32.0))
            if self._current_width > 0
            else 720.0
        )
        card = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=14,
            padding=ft.Padding.all(16),
            width=modal_width,
            content=ft.Column(
                spacing=12,
                expand=True,
                controls=[
                    ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text(
                                title or "Chi tiết",
                                size=16,
                                weight=ft.FontWeight.W_700,
                                color=C.TEXT_PRIMARY,
                                expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE_ROUNDED,
                                icon_color=C.TEXT_SECONDARY,
                                tooltip="Đóng",
                                on_click=lambda _e: self.pop_overlay(),
                            ),
                        ],
                    ),
                    ft.Column(
                        expand=True,
                        spacing=8,
                        scroll=ft.ScrollMode.AUTO,
                        controls=body_controls,
                    ),
                ],
            ),
        )
        return ft.Container(
            expand=True,
            bgcolor=C.SCRIM,
            padding=ft.Padding.all(16),
            alignment=ft.Alignment.CENTER,
            content=card,
        )

    def _dismiss_overlay_instance(self, target: ft.Control) -> None:
        if not any(item is target for item in self._overlays):
            return
        self._dispose_overlay(target)
        self._overlays = [item for item in self._overlays if item is not target]
        self._workspace_stack.controls = [self._detail_pane, *self._overlays]
        if self._overlays:
            resume_fn = getattr(self._overlays[-1], "resume", None)
            if callable(resume_fn):
                resume_fn()
        self._rebuild_layout()
        self._sync_immersive_state()
        self._safe_update()
        self._sync_pane3_bounds()

    async def _handle_open_module_async(self, mod: CourseModuleItem) -> None:
        course = self._state.selected_course()
        if course is None:
            return
        self._module_open_seq += 1
        req_seq = self._module_open_seq
        strategy = mod.open_strategy
        loading_overlay: Optional[PaneLoadingOverlay] = None
        try:
            if strategy == "assignment_detail":
                await _invoke_callback(self._on_open_assignment, course, mod)
                return
            if strategy == "webview_activity":
                await _invoke_callback(self._on_open_webview_activity, course, mod)
                return
            if strategy == "external_url":
                resolved = ""
                if self._svc is not None and hasattr(self._svc, "resolve_module_url"):
                    loading_overlay = PaneLoadingOverlay(
                        title=mod.name or "Đang mở liên kết...",
                        subtitle="Đang phân giải đường dẫn từ Moodle...",
                        status_message="Đang kết nối tới liên kết, sẽ mở ngay trong ứng dụng...",
                        preset="activity",
                        on_back=self.pop_overlay,
                        on_toggle_expand=self.toggle_pane3_expanded,
                        page=self._get_page(),
                    )
                    self.push_overlay(loading_overlay, tag=f"module:resolve:{req_seq}")
                    resolved = await asyncio.to_thread(
                        self._svc.resolve_module_url, course.source, mod
                    )
                    if req_seq != self._module_open_seq or loading_overlay._disposed:
                        return
                safe_url = (
                    safe_http_url(resolved)
                    or safe_http_url(mod.url)
                    or safe_http_url(mod.web_url)
                )
                if loading_overlay is not None:
                    self._dismiss_overlay_instance(loading_overlay)
                    loading_overlay = None
                await self._record_recent_best_effort(course, mod)
                if safe_url:
                    await _invoke_callback(self._on_launch_url, safe_url)
                return
            real_atts = real_module_attachments(mod)
            if (
                strategy == "inline_page"
                and not real_atts
                and (mod.inline_html or mod.description_html)
            ):
                await self._record_recent_best_effort(course, mod)
                if callable(self._on_open_inline_page):
                    await _invoke_callback(self._on_open_inline_page, course, mod)
                else:
                    self._push_inline_reader_overlay(mod)
                return
            if mod.modname in {"forum", "book", "page", "label"} and not real_atts:
                if callable(self._on_open_custom_activity):
                    await self._record_recent_best_effort(course, mod)
                    await _invoke_callback(self._on_open_custom_activity, course, mod)
                    return
                if strategy == "inline_page":
                    await self._record_recent_best_effort(course, mod)
                    if callable(self._on_open_inline_page):
                        await _invoke_callback(self._on_open_inline_page, course, mod)
                    else:
                        self._push_inline_reader_overlay(mod)
                    return
            if strategy == "folder_files" and len(real_atts) > 1:
                self._push_folder_overlay(course, replace(mod, attachments=real_atts))
                return
            if real_atts:
                att = real_atts[0]
                await self._record_recent_best_effort(course, mod, att)
                if callable(self._on_open_attachment_viewer):
                    await _invoke_callback(self._on_open_attachment_viewer, course, mod, att)
                else:
                    await _invoke_callback(self._on_download_attachment, course, att)
                return
            if (
                mod.modname in {"resource", "folder"}
                and self._svc is not None
                and hasattr(self._svc, "resolve_module_attachments")
            ):
                loading_overlay = PaneLoadingOverlay(
                    title=mod.name or "Tài liệu",
                    subtitle="Đang lấy thông tin tệp đính kèm...",
                    status_message="Đang tải danh sách tài liệu từ Moodle...",
                    preset="archive" if mod.modname == "folder" else "document",
                    on_back=self.pop_overlay,
                    on_toggle_expand=self.toggle_pane3_expanded,
                    page=self._get_page(),
                )
                self.push_overlay(loading_overlay, tag=f"module:resolve:{req_seq}")
                resolved_atts = await asyncio.to_thread(
                    self._svc.resolve_module_attachments, course.source, mod
                )
                if req_seq != self._module_open_seq or loading_overlay._disposed:
                    return
                if loading_overlay is not None:
                    self._dismiss_overlay_instance(loading_overlay)
                    loading_overlay = None
                if resolved_atts:
                    resolved_mod = replace(mod, attachments=resolved_atts)
                    if len(resolved_atts) > 1 or mod.modname == "folder":
                        self._push_folder_overlay(course, resolved_mod)
                        return
                    att = resolved_atts[0]
                    await self._record_recent_best_effort(course, resolved_mod, att)
                    if callable(self._on_open_attachment_viewer):
                        await _invoke_callback(
                            self._on_open_attachment_viewer, course, resolved_mod, att
                        )
                    else:
                        await _invoke_callback(self._on_download_attachment, course, att)
                    return
            if mod.modname in {"forum", "book", "page"} and callable(
                self._on_open_custom_activity
            ):
                await self._record_recent_best_effort(course, mod)
                await _invoke_callback(self._on_open_custom_activity, course, mod)
                return
            fallback_url = safe_http_url(mod.web_url) or safe_http_url(mod.url)
            if fallback_url:
                await _invoke_callback(self._on_launch_url, fallback_url)
        except Exception as exc:
            if loading_overlay is not None:
                self._dismiss_overlay_instance(loading_overlay)
            logger.warning("Failed to open module %s: %s", mod.cmid, exc)
            self._state.offline_notice = f"Không thể mở mục này: {_format_err(exc)}"
            self._render()

    async def focus_search(self) -> None:
        """Close active overlays, return to list on narrow screens, and focus search."""
        changed = False
        if self._overlays:
            self.clear_overlays(update=False)
            changed = True
        if self._layout_mode != "wide" and self._state.in_detail_mobile:
            self._state.in_detail_mobile = False
            self._render()
            changed = False
        if changed:
            self._safe_update()
        await self._list_pane.focus_search()

    def update_theme(self) -> None:
        """Apply updated global palette tokens across panes and open overlays once."""
        current = semantic_palette()
        self.bgcolor = C.BG
        self._workspace_host.bgcolor = C.BG
        self._narrow_workspace_host.bgcolor = C.BG
        self._split_divider.color = C.BORDER
        self._list_pane.update_theme(update=False, rerender=False)
        self._detail_pane.update_theme(update=False, rerender=False)
        if self._overlays:
            refresh_semantic_control_colors(self._overlays, self._theme_snapshot, current)
            for item in self._overlays:
                theme_fn = getattr(item, "update_theme", None)
                if callable(theme_fn):
                    theme_fn()
        self._theme_snapshot = current
        self._render()
