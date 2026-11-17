import asyncio
import flet as ft
from gui.core.theme import C


class ViewManager:
    """Bộ quản lý chuyển đổi view và hiệu ứng transition trong ứng dụng."""

    def __init__(
        self,
        page,
        dashboard,
        detail_view,
        settings_view,
        calendar_view,
        grade_overview_view,
        controller,
        view_host=None,
        overlays=None,
        tab_switcher=None,
        detail_switcher=None,
        empty_detail=None,
        courses_view=None,
        on_destination_change=None,
    ):
        self.page = page
        self.dashboard = dashboard
        self.detail_view = detail_view
        self.settings_view = settings_view
        self.calendar_view = calendar_view
        self.grade_overview_view = grade_overview_view
        self.courses_view = courses_view
        self.controller = controller
        self.view_host = view_host
        self.overlays = tuple(overlays or ())
        self.tab_switcher = tab_switcher
        self.detail_switcher = detail_switcher
        self._empty_detail = empty_detail or ft.Container(expand=True, ignore_interactions=True)
        self._on_destination_change = on_destination_change
        self._detail_origin = None
        self._settings_navigation_generation = 0

    def _notify_destination(self, key: str) -> None:
        if callable(self._on_destination_change):
            self._on_destination_change(key)

    def _sync_nav_buttons(self, active_key: str) -> None:
        for key, attr in (
            ("calendar", "calendar_btn"),
            ("grades", "grades_btn"),
            ("courses", "courses_btn"),
        ):
            btn = getattr(self.controller, attr, None)
            if btn is not None and hasattr(btn, "icon_color"):
                btn.icon_color = C.ACCENT if active_key == key else C.TEXT_SECONDARY

    def _hide_courses_view(self) -> None:
        if self.courses_view is not None:
            on_hidden = getattr(self.courses_view, "on_tab_hidden", None)
            if callable(on_hidden):
                on_hidden()
            self.courses_view.visible = False

    def _clear_detail_layer(self) -> None:
        self.detail_view.visible = False
        self._detail_origin = None
        if (
            self.detail_switcher is not None
            and self.detail_switcher.content is self.detail_view
        ):
            self.detail_switcher.content = self._empty_detail

    def _dismiss_menu_if_open(self) -> None:
        dismiss = getattr(self.controller, "_dismiss_desktop_filter_menu", None)
        if callable(dismiss):
            try:
                dismiss()
            except Exception:
                pass

    def _show_only(self, *views):
        """Keep only active views mounted in Flutter's control tree."""
        if self.view_host is not None:
            unique_views = []
            if self.tab_switcher is not None and self.detail_switcher is not None:
                unique_views.append(self.tab_switcher)
                unique_views.append(self.detail_switcher)
            else:
                for view in views:
                    if not any(existing is view for existing in unique_views):
                        unique_views.append(view)
            for overlay in self.overlays:
                if not any(existing is overlay for existing in unique_views):
                    unique_views.append(overlay)
            self.view_host.controls = unique_views

    def show_dashboard(self):
        """Hiển thị màn hình chính (Dashboard) và ẩn các view phụ."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        self._clear_detail_layer()
        self.dashboard.visible = True
        self.dashboard.opacity = 1.0
        self.dashboard.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.dashboard
        else:
            self._show_only(self.dashboard)

        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    def show_courses(self):
        """Chuyển sang tab Môn học và giữ nguyên state điều hướng bên trong."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self._clear_detail_layer()
        self._sync_nav_buttons("courses")
        self._notify_destination("courses")
        if self.courses_view is None:
            self.page.update()
            return
        self.courses_view.visible = True
        self.courses_view.opacity = 1.0
        self.courses_view.offset = ft.Offset(0, 0)
        on_shown = getattr(self.courses_view, "on_tab_shown", None)
        if callable(on_shown):
            on_shown()
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.courses_view
        else:
            self._show_only(self.courses_view)
        self.page.update()

    def show_calendar(self, data_snapshot):
        """Cập nhật dữ liệu và hiển thị màn hình Lịch (Calendar)."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self.dashboard.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        self._clear_detail_layer()

        self.calendar_view.show(data_snapshot)
        self.calendar_view.visible = True
        self.calendar_view.opacity = 1.0
        self.calendar_view.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.calendar_view
        else:
            self._show_only(self.calendar_view)
        self._sync_nav_buttons("calendar")
        self._notify_destination("calendar")
        self.page.update()

    async def close_calendar(self):
        """Ẩn màn hình Lịch bằng hiệu ứng và hiển thị lại màn hình Dashboard."""
        self.calendar_view.hide()
        self._hide_courses_view()
        if self.tab_switcher is not None:
            self.calendar_view.visible = False
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.calendar_view)
            self.calendar_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.calendar_view.visible = False
            self._show_only(self.dashboard)
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    async def show_settings(self):
        """Tải các thiết lập hiện tại và chuyển sang màn hình Cấu hình (Settings)."""
        self._dismiss_menu_if_open()
        self._settings_navigation_generation += 1
        generation = self._settings_navigation_generation

        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        self._clear_detail_layer()
        self.settings_view.visible = True
        self.settings_view.offset = ft.Offset(0, 0)
        self.settings_view.opacity = 1.0
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.settings_view
        else:
            self._show_only(self.settings_view)
        self._sync_nav_buttons("settings")
        self._notify_destination("settings")
        # Publish immediate visual feedback before any native settings lookup.
        self.page.update()

        await self.settings_view.load_current_settings()
        if generation != self._settings_navigation_generation:
            return

    def show_grades_loading(self):
        """Hiển thị màn hình xem điểm ở trạng thái đang tải dữ liệu (loading state)."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self._hide_courses_view()
        self._clear_detail_layer()
        self.grade_overview_view.visible = True
        self.grade_overview_view.opacity = 1.0
        self.grade_overview_view.offset = ft.Offset(0, 0)
        self.grade_overview_view.show_loading()
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.grade_overview_view
        else:
            self._show_only(self.grade_overview_view)
        self._sync_nav_buttons("grades")
        self._notify_destination("grades")
        self.page.update()

    def show_grades_data(self, courses_grades, grade_items):
        """Cập nhật dữ liệu điểm số thực tế vào màn hình xem điểm."""
        self.grade_overview_view.update_grades(courses_grades, grade_items)
        self.page.update()

    def show_academic_radar_data(self, semesters, profiles):
        """Cập nhật dữ liệu hồ sơ học tập Academic Radar và danh sách học phần."""
        self.grade_overview_view.update_academic_data(semesters, profiles)
        self.page.update()

    async def close_grades(self):
        """Đóng màn hình điểm số và quay lại màn hình chính."""
        self.grade_overview_view.hide()
        self._hide_courses_view()
        if self.tab_switcher is not None:
            self.grade_overview_view.visible = False
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.grade_overview_view)
            self.grade_overview_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.grade_overview_view.visible = False
            self._show_only(self.dashboard)
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    def show_detail_loading(self, data):
        """Chuyển sang màn hình chi tiết bài tập ở trạng thái tải dữ liệu."""
        self._dismiss_menu_if_open()
        # Retain origin view mounted behind opaque Detail to preserve native ScrollPosition.
        if getattr(self.calendar_view, "visible", False):
            origin = self.calendar_view
        elif (
            self.courses_view is not None
            and getattr(self.courses_view, "visible", False)
        ):
            origin = self.courses_view
        else:
            origin = self.dashboard
        self._detail_origin = origin
        self.dashboard.visible = origin is self.dashboard
        self.calendar_view.visible = origin is self.calendar_view
        if self.courses_view is not None:
            self.courses_view.visible = origin is self.courses_view
        self.settings_view.visible = False
        self.grade_overview_view.visible = False

        self.detail_view.offset = ft.Offset(0, 0)
        self.detail_view.opacity = 1.0
        self.detail_view.visible = True
        self.detail_view.show_loading(data)
        if self.detail_switcher is not None:
            self.detail_switcher.content = self.detail_view
        else:
            self._show_only(origin, self.detail_view)
        self.page.update()

    def show_detail_data(self, full_data):
        """Hiển thị thông tin chi tiết đầy đủ của bài tập."""
        self.detail_view.update_detail(full_data)
        self.page.update()

    def show_detail_error(self, data):
        """Hiển thị thông báo lỗi khi không thể truy xuất chi tiết đầy đủ từ Moodle."""
        self.detail_view.update_detail(data)
        self.detail_view.show_error_banner()
        self.page.update()

    async def close_detail(self, detail_from_calendar: bool):
        """Đóng màn hình chi tiết và quay lại nơi xuất phát (Lịch, Môn học, hoặc Dashboard)."""
        if detail_from_calendar:
            destination = self.calendar_view
        elif self._detail_origin is not None:
            destination = self._detail_origin
        else:
            destination = self.dashboard
        self._detail_origin = None
        if self.detail_switcher is not None:
            self.detail_view.visible = False
            destination.visible = True
            destination.opacity = 1.0
            self.detail_switcher.content = self._empty_detail
        else:
            destination.visible = True
            if destination is self.dashboard or destination is self.courses_view:
                destination.opacity = 1.0
            self._show_only(destination, self.detail_view)
            self.detail_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.detail_view.visible = False
            self._show_only(destination)
        self.page.update()

    async def close_settings(self):
        """Đóng màn hình cấu hình với hiệu ứng và hiển thị lại màn hình Dashboard chính."""
        self.cancel_pending_settings_navigation()
        self._hide_courses_view()
        if self.tab_switcher is not None:
            self.settings_view.visible = False
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.settings_view)
            self.settings_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.settings_view.visible = False
            self._show_only(self.dashboard)
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    def cancel_pending_settings_navigation(self) -> None:
        """Invalidate an in-flight Settings reveal without awaiting its I/O."""
        self._settings_navigation_generation += 1
        cancel = getattr(self.settings_view, "cancel_pending_load", None)
        if callable(cancel):
            cancel()


