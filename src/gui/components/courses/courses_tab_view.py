"""Adaptive Master-Detail Courses workspace orchestrating catalog and detail panes."""

from __future__ import annotations

import asyncio
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
from gui.components.courses.course_detail_pane import CourseDetailPane
from gui.components.courses.course_list_pane import CourseListPane
from gui.core.theme import C, refresh_semantic_control_colors, semantic_palette
from gui.view_models.courses_state import (
    CoursesLayoutMode,
    CoursesState,
    compute_courses_layout_mode,
    extract_readable_html_and_links,
    format_file_size,
    safe_http_url,
)

if TYPE_CHECKING:
    from core.courses.catalog_service import CourseCatalogService

logger = logging.getLogger(__name__)


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
        on_open_webview_activity: Optional[
            Callable[[CourseSummary, CourseModuleItem], Any]
        ] = None,
        on_open_attachment_viewer: Optional[
            Callable[[CourseSummary, CourseModuleItem, CourseAttachment], Any]
        ] = None,
        on_download_attachment: Optional[
            Callable[[CourseSummary, CourseAttachment], Any]
        ] = None,
        on_launch_url: Optional[Callable[[str], Any]] = None,
        on_open_inline_page: Optional[
            Callable[[CourseSummary, CourseModuleItem], Any]
        ] = None,
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

        self._state = CoursesState()
        self._current_width: float = 1100.0
        self._layout_mode: CoursesLayoutMode = "wide"
        self._overlays: list[ft.Control] = []
        self._theme_snapshot = semantic_palette()

        self._list_pane = CourseListPane(
            on_select_course=lambda key: self._run_task(self._select_course_async, key),
            on_toggle_pin=lambda key: self._run_task(self._toggle_pin_async, key),
            on_open_recent=lambda entry: self._run_task(self._open_recent_async, entry),
            on_filter_change=self._handle_filter_change,
            on_refresh_catalog=self.refresh,
        )
        self._detail_pane = CourseDetailPane(
            on_back_mobile=self.handle_back,
            on_refresh_course=lambda: self._run_task(self._refresh_single_course_async),
            on_open_module=lambda mod: self._run_task(self._handle_open_module_async, mod),
            on_download_attachment=lambda att: self._run_task(
                self._download_selected_attachment_async, att
            ),
            on_toggle_completion=lambda mod, done: self._run_task(
                self._toggle_completion_async, mod, done
            ),
            on_copy_text=lambda text: self._run_task(self._copy_text_async, text),
            on_launch_url=lambda url: self._run_task(self._launch_url_safe, url),
            on_subtab_change=lambda idx: self._run_task(self._on_subtab_change_async, idx),
        )

        self._main_host = ft.Container(expand=True)
        self._root_stack = ft.Stack(expand=True, controls=[self._main_host])

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

    def _dispatch_callback(
        self, fn: Optional[Callable[..., Any]], *args: Any
    ) -> None:
        if not callable(fn):
            return
        result = fn(*args)
        if inspect.isawaitable(result):
            async def _await_res() -> None:
                await result

            self._run_task(_await_res)

    def _run_task(self, coro_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        page = self._get_page()
        run_task = getattr(page, "run_task", None) if page is not None else None
        if callable(run_task):
            run_task(coro_fn, *args, **kwargs)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro_fn(*args, **kwargs))
        except RuntimeError:
            pass

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
        parts = [p for p in (courses_err, thnn_err) if p]
        return " | ".join(parts)

    def _rebuild_layout(self) -> None:
        if self._layout_mode == "wide":
            self._main_host.content = ft.Row(
                expand=True,
                spacing=0,
                controls=[
                    ft.Container(content=self._list_pane, width=320),
                    ft.VerticalDivider(width=1, thickness=1, color=C.BORDER),
                    ft.Container(content=self._detail_pane, expand=True),
                ],
            )
            return

        show_detail = (
            self._state.in_detail_mobile and self._state.selected_course() is not None
        )
        active_pane: ft.Control = self._detail_pane if show_detail else self._list_pane
        if self._layout_mode == "tablet_portrait":
            target_width = min(820.0, max(320.0, self._current_width))
            self._main_host.content = ft.Container(
                expand=True,
                alignment=ft.Alignment.TOP_CENTER,
                content=ft.Container(
                    content=active_pane,
                    width=target_width,
                    expand=True,
                ),
            )
        else:
            self._main_host.content = active_pane

    def _render(self) -> None:
        self._rebuild_layout()
        self._list_pane.render(self._state)
        self._detail_pane.render(self._state, show_back=(self._layout_mode != "wide"))
        self._safe_update()

    def set_width(self, width: float) -> None:
        """Apply viewport width and adapt Master-Detail layout breakpoint."""
        try:
            numeric = float(width)
        except (TypeError, ValueError):
            numeric = 0.0
        self._current_width = numeric if numeric > 0 else 0.0
        self._layout_mode = compute_courses_layout_mode(width)
        self._render()

    def push_overlay(self, control: ft.Control) -> None:
        """Push a modal/reader overlay onto the Courses view stack."""
        self._overlays.append(control)
        self._root_stack.controls = [self._main_host, *self._overlays]
        self._safe_update()

    def pop_overlay(self) -> bool:
        """Pop the top overlay if present; return True if popped."""
        if not self._overlays:
            return False
        self._overlays.pop()
        self._root_stack.controls = [self._main_host, *self._overlays]
        self._safe_update()
        return True

    def handle_back(self) -> bool:
        """Dismiss active overlay or exit mobile detail view; return True if handled."""
        if self.pop_overlay():
            return True
        if self._layout_mode != "wide" and self._state.in_detail_mobile:
            self._state.in_detail_mobile = False
            self._render()
            return True
        return False

    def load_initial(self) -> None:
        """Schedule initial 0ms cached catalog load and background staleness check."""
        page = self._get_page()
        if page is not None and callable(getattr(page, "run_task", None)):
            page.run_task(self.load_initial_async)
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.load_initial_async())
            return
        except RuntimeError:
            pass
        if self._svc is not None:
            courses, recent, synced_at = self._svc.load_cached_catalog()
            self._state.set_courses(courses)
            self._state.recent_items = list(recent)
            self._state.last_synced_at = synced_at
            self._state.offline_notice = self._collect_provider_notice()
            self._render()

    async def load_initial_async(self) -> None:
        """Load cached catalog immediately and refresh if stale or empty."""
        if self._svc is None:
            return
        courses, recent, synced_at = await asyncio.to_thread(
            self._svc.load_cached_catalog
        )
        self._state.set_courses(courses)
        self._state.recent_items = list(recent)
        self._state.last_synced_at = synced_at
        self._state.offline_notice = self._collect_provider_notice()
        self._render()

        stale = await asyncio.to_thread(
            self._svc.is_catalog_stale, self.CATALOG_STALE_HOURS
        )
        if stale or not courses:
            await self.refresh_catalog_async(include_thnn=True)

    def refresh(self) -> None:
        """Trigger asynchronous course catalog refresh."""
        self._run_task(self.refresh_catalog_async, True)

    async def refresh_catalog_async(self, include_thnn: bool = True) -> None:
        """Refresh course catalog from providers while preserving offline cache."""
        if self._svc is None:
            return
        self._state.loading_catalog = True
        self._render()
        explicit_err = ""
        try:
            courses = await asyncio.to_thread(self._svc.refresh_catalog, include_thnn)
            self._state.set_courses(courses)
            _, recent, synced_at = await asyncio.to_thread(
                self._svc.load_cached_catalog
            )
            self._state.recent_items = list(recent)
            if self._state.selected_course() is None:
                self._state.last_synced_at = synced_at
        except Exception as exc:
            logger.warning("Failed to refresh course catalog: %s", exc)
            explicit_err = str(exc).strip() or type(exc).__name__
        finally:
            self._state.offline_notice = self._collect_provider_notice(
                explicit_error=explicit_err
            )
            self._state.loading_catalog = False
            self._render()

    def _handle_filter_change(self, semester: str, query: str) -> None:
        self._state.semester_filter = semester
        self._state.search_query = query
        self._render()

    async def _toggle_pin_async(self, course_key: str) -> None:
        if self._svc is None:
            return
        courses = await asyncio.to_thread(self._svc.toggle_pin, course_key)
        self._state.set_courses(courses)
        self._render()

    async def _select_course_async(self, course_key: str) -> None:
        course = next(
            (c for c in self._state.courses if c.course_key == course_key), None
        )
        if course is None:
            return
        target_key = course.course_key
        prev_key = self._state.selected_course_key
        self._state.select_course(target_key)
        if target_key != prev_key:
            self._state.sections = []
            self._state.grades = []
            self._state.last_synced_at = 0
        self._state.loading_detail = True
        self._state.offline_notice = ""
        self._render()

        if self._svc is None:
            self._state.loading_detail = False
            self._render()
            return

        try:
            cached = await asyncio.to_thread(
                self._svc.open_course_cached, course.source, course.id
            )
            if self._state.selected_course_key != target_key:
                return
            self._state.sections = list(cached.sections)
            self._state.grades = list(cached.grades)
            self._state.last_synced_at = cached.last_synced_at
            self._render()

            if course.has_unseen_updates:
                updated_courses = await asyncio.to_thread(
                    self._svc.mark_course_seen, target_key
                )
                self._state.set_courses(updated_courses)
                if self._state.selected_course_key != target_key:
                    return

            res = await asyncio.to_thread(
                self._svc.sync_course_delta, course.source, course.id, False
            )
            if self._state.selected_course_key != target_key:
                return
            self._state.sections = list(res.sections)
            if res.grades:
                self._state.grades = list(res.grades)
            self._state.last_synced_at = res.last_synced_at
            self._state.offline_notice = self._collect_provider_notice(
                course, explicit_error=res.error
            )

            if res.changed:
                refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
                self._state.set_courses(refreshed)
                if self._state.selected_course_key != target_key:
                    return

            if self._state.active_subtab == 2 and not self._state.grades:
                grades = await asyncio.to_thread(
                    self._svc.load_course_grades, course.source, course.id, False
                )
                if self._state.selected_course_key != target_key:
                    return
                self._state.grades = list(grades)
        finally:
            if self._state.selected_course_key == target_key:
                self._state.loading_detail = False
                self._render()

    async def _refresh_single_course_async(self) -> None:
        course = self._state.selected_course()
        if course is None or self._svc is None:
            return
        target_key = course.course_key
        self._state.loading_detail = True
        self._render()
        try:
            res = await asyncio.to_thread(
                self._svc.sync_course_delta, course.source, course.id, True
            )
            if self._state.selected_course_key != target_key:
                return
            self._state.sections = list(res.sections)
            self._state.last_synced_at = res.last_synced_at
            if self._state.active_subtab == 2:
                grades = await asyncio.to_thread(
                    self._svc.load_course_grades, course.source, course.id, True
                )
                if self._state.selected_course_key != target_key:
                    return
                self._state.grades = list(grades)
            refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
            self._state.set_courses(refreshed)
            if self._state.selected_course_key != target_key:
                return
            self._state.offline_notice = self._collect_provider_notice(
                course, explicit_error=res.error
            )
        finally:
            if self._state.selected_course_key == target_key:
                self._state.loading_detail = False
                self._render()

    async def _on_subtab_change_async(self, index: int) -> None:
        self._state.active_subtab = index
        if index != 2 or self._svc is None:
            self._render()
            return
        course = self._state.selected_course()
        if course is None:
            self._render()
            return
        target_key = course.course_key
        self._state.loading_detail = True
        self._render()
        try:
            grades = await asyncio.to_thread(
                self._svc.load_course_grades, course.source, course.id, False
            )
            if self._state.selected_course_key != target_key:
                return
            self._state.grades = list(grades)
            self._state.offline_notice = self._collect_provider_notice(course)
        finally:
            if self._state.selected_course_key == target_key:
                self._state.loading_detail = False
                self._render()

    async def _toggle_completion_async(
        self, mod: CourseModuleItem, completed: bool
    ) -> None:
        course = self._state.selected_course()
        if course is None or self._svc is None:
            return
        target_key = course.course_key
        ok = await asyncio.to_thread(
            self._svc.toggle_completion, course.source, course.id, mod.cmid, completed
        )
        if self._state.selected_course_key != target_key:
            return
        if ok:
            cached = await asyncio.to_thread(
                self._svc.open_course_cached, course.source, course.id
            )
            if self._state.selected_course_key != target_key:
                return
            self._state.sections = list(cached.sections)
            refreshed, _, _ = await asyncio.to_thread(self._svc.load_cached_catalog)
            self._state.set_courses(refreshed)
            if self._state.selected_course_key != target_key:
                return
        else:
            self._state.offline_notice = (
                self._collect_provider_notice(course)
                or "Không thể cập nhật trạng thái hoàn thành."
            )
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
            self._list_pane.render(self._state)
        except Exception:
            logger.debug("Ignoring record_recent failure", exc_info=True)

    async def _open_recent_async(self, entry: RecentMaterialEntry) -> None:
        await self._select_course_async(entry.course_key)
        if self._state.selected_course_key != entry.course_key:
            return
        for sec in self._state.sections:
            for mod in sec.modules:
                if mod.cmid == entry.cmid:
                    await self._handle_open_module_async(mod)
                    return

    async def _download_selected_attachment_async(
        self, att: CourseAttachment
    ) -> None:
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
        if page is not None:
            sb = ft.SnackBar(
                content=ft.Text(f"Đã sao chép: {text}", size=13, color=C.TEXT_PRIMARY),
                bgcolor=C.SURFACE,
                duration=2000,
            )
            overlay = getattr(page, "overlay", None)
            if overlay is not None:
                overlay.append(sb)
                sb.open = True
            else:
                show_dialog = getattr(page, "show_dialog", None)
                if callable(show_dialog):
                    try:
                        show_dialog(sb)
                    except (AttributeError, TypeError):
                        return
                else:
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
        page = self._get_page()
        if page is not None and callable(getattr(page, "run_task", None)):
            page.run_task(self._open_folder_attachment_async, course, mod, att)
            return
        try:
            asyncio.get_running_loop()
            self._run_task(self._open_folder_attachment_async, course, mod, att)
            return
        except RuntimeError:
            pass
        self._dispatch_callback(self._on_open_attachment_viewer, course, mod, att)

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
            *(
                [ft.Divider(color=C.BORDER), *link_controls]
                if link_controls
                else []
            ),
        ]
        self.push_overlay(self._build_modal_overlay(mod.name, body_controls))

    def _push_folder_overlay(
        self, course: CourseSummary, mod: CourseModuleItem
    ) -> None:
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
                                on_click=lambda _e, item=att: self._dispatch_callback(
                                    self._on_download_attachment,
                                    course,
                                    item,
                                ),
                            ),
                        ],
                    ),
                )
            )
        self.push_overlay(self._build_modal_overlay(mod.name, rows))

    async def _open_folder_attachment_async(
        self,
        course: CourseSummary,
        mod: CourseModuleItem,
        att: CourseAttachment,
    ) -> None:
        await self._record_recent_best_effort(course, mod, att)
        await _invoke_callback(self._on_open_attachment_viewer, course, mod, att)

    def _build_modal_overlay(
        self, title: str, body_controls: list[ft.Control]
    ) -> ft.Control:
        card = ft.Container(
            bgcolor=C.SURFACE,
            border=ft.Border.all(1, C.BORDER),
            border_radius=14,
            padding=ft.Padding.all(16),
            width=720,
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

    async def _handle_open_module_async(self, mod: CourseModuleItem) -> None:
        course = self._state.selected_course()
        if course is None:
            return
        strategy = mod.open_strategy

        if strategy == "assignment_detail":
            await _invoke_callback(self._on_open_assignment, course, mod)
            return
        if strategy == "webview_activity":
            await _invoke_callback(self._on_open_webview_activity, course, mod)
            return
        if strategy == "external_url":
            if self._svc is not None and hasattr(self._svc, "resolve_module_url"):
                resolved = await asyncio.to_thread(
                    self._svc.resolve_module_url, course.source, mod
                )
            else:
                resolved = mod.url or mod.web_url
            await self._record_recent_best_effort(course, mod)
            await self._launch_url_safe(resolved)
            return
        if (
            strategy == "inline_page"
            and not mod.attachments
            and (mod.inline_html or mod.description_html)
        ):
            await self._record_recent_best_effort(course, mod)
            if callable(self._on_open_inline_page):
                await _invoke_callback(self._on_open_inline_page, course, mod)
            else:
                self._push_inline_reader_overlay(mod)
            return
        if strategy == "folder_files" and len(mod.attachments) > 1:
            self._push_folder_overlay(course, mod)
            return
        if mod.attachments:
            att = mod.attachments[0]
            await self._record_recent_best_effort(course, mod, att)
            if callable(self._on_open_attachment_viewer):
                await _invoke_callback(
                    self._on_open_attachment_viewer, course, mod, att
                )
            else:
                await _invoke_callback(self._on_download_attachment, course, att)
            return
        await self._launch_url_safe(mod.web_url or mod.url)

    async def focus_search(self) -> None:
        """Return to catalog list if in mobile detail and focus search field."""
        if self._layout_mode != "wide" and self._state.in_detail_mobile:
            self._state.in_detail_mobile = False
            self._render()
        await self._list_pane.focus_search()

    def update_theme(self) -> None:
        """Apply updated global palette tokens across panes and open overlays."""
        current = semantic_palette()
        self.bgcolor = C.BG
        self._list_pane.update_theme()
        self._detail_pane.update_theme()
        if self._overlays:
            refresh_semantic_control_colors(
                self._overlays, self._theme_snapshot, current
            )
        self._theme_snapshot = current
        self._render()
