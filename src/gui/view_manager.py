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
        courses_view=None,
        course_grades_view=None,
        view_host=None,
        overlays=None,
        tab_switcher=None,
        detail_switcher=None,
        empty_detail=None,
        on_destination_change=None,
    ):
        self.page = page
        self.dashboard = dashboard
        self.detail_view = detail_view
        self.settings_view = settings_view
        self.calendar_view = calendar_view
        self.grade_overview_view = grade_overview_view
        self.courses_view = courses_view
        self.course_grades_view = course_grades_view
        self.controller = controller
        self.view_host = view_host
        self.overlays = tuple(overlays or ())
        self.tab_switcher = tab_switcher
        self.detail_switcher = detail_switcher
        self._empty_detail = empty_detail or ft.Container(
            expand=True, ignore_interactions=True
        )
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

    def _sync_detail_host(self) -> None:
        """Lớp panel chi tiết chỉ nhận thao tác khi đang thực sự hiện chi tiết.

        Ở chế độ cửa sổ nhỏ/vừa lớp này phủ lên cả vùng nút của các màn khác
        (bảng điểm, lịch, cài đặt...); nếu không tắt tương tác thì các nút bên
        dưới (làm mới, tính GPA...) không bấm được.
        """
        host = getattr(self.controller, "_detail_host", None)
        if host is None:
            return
        active = bool(
            getattr(self.detail_view, "visible", False)
            and self.detail_switcher is not None
            and self.detail_switcher.content is self.detail_view
        )
        try:
            host.ignore_interactions = not active
        except Exception:
            pass

    def _hide_courses_view(self) -> None:
        if self.courses_view is not None:
            on_hidden = getattr(self.courses_view, "on_tab_hidden", None)
            if callable(on_hidden):
                on_hidden()
            self.courses_view.visible = False
        if self.course_grades_view is not None:
            self.course_grades_view.visible = False

    def _clear_selected_card(self) -> None:
        clear_fn = getattr(self.controller, "_clear_selected_activity", None)
        if callable(clear_fn):
            clear_fn()

    def _clear_detail_layer(self) -> None:
        self.detail_view.visible = False
        self._detail_origin = None
        self._clear_selected_card()
        if (
            self.detail_switcher is not None
            and self.detail_switcher.content is self.detail_view
        ):
            self.detail_switcher.content = self._empty_detail
        self._sync_detail_host()

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
        self.dashboard.visible = True
        self.dashboard.opacity = 1.0
        self.dashboard.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.dashboard
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        self._clear_detail_layer()
        if self.tab_switcher is None:
            self._show_only(self.dashboard)
        self._sync_detail_host()
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    def show_courses(self):
        """Chuyển sang tab Môn học và giữ nguyên state điều hướng bên trong."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self._clear_detail_layer()
        self._sync_nav_buttons("courses")
        self._notify_destination("courses")
        if self.courses_view is None:
            self._sync_detail_host()
            self.page.update()
            return
        self.courses_view.visible = True
        self.courses_view.opacity = 1.0
        self.courses_view.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.courses_view
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        if self.course_grades_view is not None:
            self.course_grades_view.visible = False
        on_shown = getattr(self.courses_view, "on_tab_shown", None)
        if callable(on_shown):
            on_shown()
        if self.tab_switcher is None:
            self._show_only(self.courses_view)
        self._sync_detail_host()
        self.page.update()

    def show_calendar(self, data_snapshot):
        """Cập nhật dữ liệu và hiển thị màn hình Lịch (Calendar)."""
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self._clear_detail_layer()

        self.calendar_view.visible = True
        self.calendar_view.opacity = 1.0
        self.calendar_view.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.calendar_view
        self.dashboard.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        self.calendar_view.show(data_snapshot)
        self.calendar_view.visible = True
        if self.tab_switcher is None:
            self._show_only(self.calendar_view)
        self._sync_detail_host()
        self._sync_nav_buttons("calendar")
        self._notify_destination("calendar")
        self.page.update()

    async def close_calendar(self):
        """Ẩn màn hình Lịch bằng hiệu ứng và hiển thị lại màn hình Dashboard."""
        self.calendar_view.hide()
        self._hide_courses_view()
        if self.tab_switcher is not None:
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
            self.calendar_view.visible = False
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.calendar_view)
            self.calendar_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.calendar_view.visible = False
            self._show_only(self.dashboard)
        self._sync_detail_host()
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    async def show_settings(self):
        """Tải các thiết lập hiện tại và chuyển sang màn hình Cấu hình (Settings)."""
        self._dismiss_menu_if_open()
        self._settings_navigation_generation += 1
        generation = self._settings_navigation_generation

        self._clear_detail_layer()
        self.settings_view.visible = True
        self.settings_view.offset = ft.Offset(0, 0)
        self.settings_view.opacity = 1.0
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.settings_view
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.grade_overview_view.visible = False
        self._hide_courses_view()
        if self.tab_switcher is None:
            self._show_only(self.settings_view)
        self._sync_detail_host()
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
        self._clear_detail_layer()
        self.grade_overview_view.visible = True
        self.grade_overview_view.opacity = 1.0
        self.grade_overview_view.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = self.grade_overview_view
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self._hide_courses_view()
        self.grade_overview_view.show_loading()
        if self.tab_switcher is None:
            self._show_only(self.grade_overview_view)
        self._sync_detail_host()
        # The cached grade view may have been detached while Settings previewed
        # another theme. Refresh after attaching it so Flet sends the new
        # surface colors with the next page update.
        self.grade_overview_view.update_theme()
        self._sync_nav_buttons("grades")
        self._notify_destination("grades")
        self.page.update()

    def _grade_courses_target(self):
        if self.course_grades_view is not None:
            return self.course_grades_view
        return self.courses_view

    def show_course_grades_loading(self):
        target = self._grade_courses_target()
        if target is None:
            return
        self._dismiss_menu_if_open()
        self.cancel_pending_settings_navigation()
        self._clear_detail_layer()
        target.visible = True
        target.opacity = 1.0
        target.offset = ft.Offset(0, 0)
        if self.tab_switcher is not None:
            self.tab_switcher.content = target
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        if self.courses_view is not None and self.courses_view is not target:
            on_hidden = getattr(self.courses_view, "on_tab_hidden", None)
            if callable(on_hidden):
                on_hidden()
            self.courses_view.visible = False
        target.show_loading()
        if self.tab_switcher is None:
            self._show_only(target)
        self._sync_detail_host()
        if hasattr(target, "update_theme"):
            target.update_theme()
        if self.course_grades_view is None:
            self._sync_nav_buttons("courses")
        self.page.update()

    def show_course_grades_data(self, courses):
        target = self._grade_courses_target()
        if target is None:
            return
        target.update_courses(courses)
        self.page.update()

    def show_course_grades_error(self):
        target = self._grade_courses_target()
        if target is None:
            return
        target.show_error()
        self.page.update()

    def show_course_grades_stale_notice(self, message: str) -> None:
        target = self._grade_courses_target()
        if target is None:
            return
        set_notice = getattr(target, "set_stale_notice", None)
        if callable(set_notice):
            set_notice(message)
            self.page.update()

    async def close_course_grades(self):
        target = self._grade_courses_target()
        if target is None:
            return
        if hasattr(target, "hide"):
            target.hide()
        if self.course_grades_view is not None:
            destination = self.grade_overview_view
            active_nav = "grades"
        else:
            destination = self.dashboard
            active_nav = "home"
        if self.tab_switcher is not None:
            destination.visible = True
            destination.opacity = 1.0
            self.tab_switcher.content = destination
            target.visible = False
        else:
            destination.opacity = 1.0
            destination.visible = True
            self._show_only(destination, target)
            target.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            target.visible = False
            self._show_only(destination)
        self._sync_detail_host()
        if destination is self.grade_overview_view and hasattr(
            self.grade_overview_view, "update_theme"
        ):
            self.grade_overview_view.update_theme()
        if self.course_grades_view is None:
            self._sync_nav_buttons(active_nav)
        self.page.update()

    def show_courses_loading(self):
        self.show_course_grades_loading()

    def show_courses_data(self, courses):
        self.show_course_grades_data(courses)

    def show_courses_error(self):
        self.show_course_grades_error()

    def show_courses_stale_notice(self, message: str) -> None:
        self.show_course_grades_stale_notice(message)

    async def close_courses(self):
        await self.close_course_grades()

    def show_grades_error(self, message: str = "") -> None:
        show_err = getattr(self.grade_overview_view, "show_error", None)
        if callable(show_err):
            show_err(message)
        else:
            self.grade_overview_view.update_academic_data([], [])
        self.page.update()

    def show_grades_data(self, courses_grades, grade_items):
        """Cập nhật dữ liệu điểm số thực tế vào màn hình xem điểm."""
        self.grade_overview_view.update_grades(courses_grades, grade_items)
        self.page.update()

    def show_academic_radar_data(self, semesters, profiles, progress=None):
        """Cập nhật dữ liệu hồ sơ học tập Academic Radar và danh sách học phần."""
        self.grade_overview_view.update_academic_data(semesters, profiles, progress)
        self.page.update()

    def show_credit_progress(self, progress):
        """Cập nhật tiến độ tín chỉ ra trường (tải sau khi bảng điểm đã hiện)."""
        self.grade_overview_view.set_credit_progress(progress)
        self.page.update()

    async def close_grades(self):
        """Đóng màn hình điểm số và quay lại màn hình chính."""
        self.grade_overview_view.hide()
        self._hide_courses_view()
        if self.tab_switcher is not None:
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
            self.grade_overview_view.visible = False
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.grade_overview_view)
            self.grade_overview_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.grade_overview_view.visible = False
            self._show_only(self.dashboard)
        self._sync_detail_host()
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
        if self.course_grades_view is not None:
            self.course_grades_view.visible = False
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
        self._sync_detail_host()
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
        self._clear_selected_card()
        if self.detail_switcher is not None:
            self.detail_view.visible = False
            destination.visible = True
            destination.opacity = 1.0
            self.detail_switcher.content = self._empty_detail
            self._sync_detail_host()
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
            self.dashboard.visible = True
            self.dashboard.opacity = 1.0
            self.tab_switcher.content = self.dashboard
            self.settings_view.visible = False
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
            self._show_only(self.dashboard, self.settings_view)
            self.settings_view.opacity = 0.0
            self.page.update()
            await asyncio.sleep(0.18)
            self.settings_view.visible = False
            self._show_only(self.dashboard)
        self._sync_detail_host()
        self._sync_nav_buttons("home")
        self._notify_destination("home")
        self.page.update()

    def cancel_pending_settings_navigation(self) -> None:
        """Invalidate an in-flight Settings reveal without awaiting its I/O."""
        self._settings_navigation_generation += 1
        cancel = getattr(self.settings_view, "cancel_pending_load", None)
        if callable(cancel):
            cancel()
