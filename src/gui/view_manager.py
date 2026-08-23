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
    ):
        self.page = page
        self.dashboard = dashboard
        self.detail_view = detail_view
        self.settings_view = settings_view
        self.calendar_view = calendar_view
        self.grade_overview_view = grade_overview_view
        self.controller = controller
        self.view_host = view_host
        self.overlays = tuple(overlays or ())
        self._settings_navigation_generation = 0

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
            for view in views:
                if not any(existing is view for existing in unique_views):
                    unique_views.append(view)
            for overlay in self.overlays:
                if not any(existing is overlay for existing in unique_views):
                    unique_views.append(overlay)
            self.view_host.controls = unique_views

    def show_dashboard(self):
        """Hiển thị màn hình chính (Dashboard) và ẩn các view phụ."""
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self.dashboard.visible = True
        self.dashboard.opacity = 1.0
        
        # Đặt lại màu sắc của các nút trên thanh điều hướng về trạng thái không active
        self.controller.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.controller.grades_btn.icon_color = C.TEXT_SECONDARY
        self._show_only(self.dashboard)
        self.page.update()

    def show_calendar(self, data_snapshot):
        """Cập nhật dữ liệu và hiển thị màn hình Lịch (Calendar)."""
        self._dismiss_menu_if_open()
        self.dashboard.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        self.calendar_view.show(data_snapshot)
        # Áp dụng hiệu ứng slide-in transition bằng offset và opacity
        self.calendar_view.offset = ft.Offset(0, 0)
        self.calendar_view.opacity = 1.0
        self.controller.calendar_btn.icon_color = C.ACCENT
        self._show_only(self.calendar_view)
        self.page.update()

    async def close_calendar(self):
        """Ẩn màn hình Lịch bằng hiệu ứng và hiển thị lại màn hình Dashboard."""
        # Thực hiện hiệu ứng slide-out sang bên phải
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self._show_only(self.dashboard, self.calendar_view)
        self.calendar_view.offset = ft.Offset(1, 0)
        self.calendar_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25) # Đợi hiệu ứng kết thúc trước khi ẩn hoàn toàn
        self.calendar_view.hide()
        self._show_only(self.dashboard)
        self.controller.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    async def show_settings(self):
        """Tải các thiết lập hiện tại và chuyển sang màn hình Cấu hình (Settings)."""
        self._dismiss_menu_if_open()
        self._settings_navigation_generation += 1
        generation = self._settings_navigation_generation

        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.grade_overview_view.visible = False
        self.settings_view.visible = True
        self.settings_view.offset = ft.Offset(0, 0)
        self.settings_view.opacity = 1.0
        self._show_only(self.settings_view)
        # Publish immediate visual feedback before any native settings lookup.
        self.page.update()

        await self.settings_view.load_current_settings()
        if generation != self._settings_navigation_generation:
            return

    def show_grades_loading(self):
        """Hiển thị màn hình xem điểm ở trạng thái đang tải dữ liệu (loading state)."""
        self._dismiss_menu_if_open()
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.grade_overview_view.show_loading()
        self.controller.grades_btn.icon_color = C.ACCENT
        self._show_only(self.grade_overview_view)
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
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self._show_only(self.dashboard, self.grade_overview_view)
        self.grade_overview_view.hide()
        self.page.update()
        await asyncio.sleep(0.25) # Chờ cho hiệu ứng ẩn hoàn tất
        self.grade_overview_view.visible = False
        self._show_only(self.dashboard)
        self.controller.grades_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_detail_loading(self, data):
        """Chuyển sang màn hình chi tiết bài tập ở trạng thái tải dữ liệu."""
        self._dismiss_menu_if_open()
        # Retain the one origin view behind opaque Detail. Removing a ListView
        # from Flutter destroys its native ScrollPosition and makes pixel-based
        # remount restoration susceptible to layout timing drift.
        origin = self.calendar_view if self.calendar_view.visible else self.dashboard
        self.dashboard.visible = origin is self.dashboard
        self.calendar_view.visible = origin is self.calendar_view
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        # Thực hiện hiệu ứng trượt màn hình sang trái
        self.detail_view.offset = ft.Offset(0, 0)
        self.detail_view.opacity = 1.0
        self.detail_view.visible = True
        self.detail_view.show_loading(data)
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
        """Đóng màn hình chi tiết và quay lại nơi xuất phát (Lịch hoặc Dashboard)."""
        destination = self.calendar_view if detail_from_calendar else self.dashboard
        destination.visible = True
        if destination is self.dashboard:
            destination.opacity = 1.0
        self._show_only(destination, self.detail_view)
        self.detail_view.offset = ft.Offset(1, 0)
        self.detail_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25) # Đợi thời gian hoàn tất animation
        self.detail_view.visible = False
        self._show_only(destination)
        self.page.update()

    async def close_settings(self):
        """Đóng màn hình cấu hình với hiệu ứng và hiển thị lại màn hình Dashboard chính."""
        self.cancel_pending_settings_navigation()
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self._show_only(self.dashboard, self.settings_view)
        self.settings_view.offset = ft.Offset(1, 0)
        self.settings_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.settings_view.visible = False
        self._show_only(self.dashboard)
        self.page.update()

    def cancel_pending_settings_navigation(self) -> None:
        """Invalidate an in-flight Settings reveal without awaiting its I/O."""
        self._settings_navigation_generation += 1
        self.settings_view.cancel_pending_load()
