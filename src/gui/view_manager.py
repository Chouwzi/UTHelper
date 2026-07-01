import asyncio
import flet as ft
from gui.core.theme import C

class ViewManager:
    """Bộ quản lý chuyển đổi view và hiệu ứng transition trong ứng dụng."""

    def __init__(self, page, dashboard, detail_view, settings_view, calendar_view, grade_overview_view, controller):
        self.page = page
        self.dashboard = dashboard
        self.detail_view = detail_view
        self.settings_view = settings_view
        self.calendar_view = calendar_view
        self.grade_overview_view = grade_overview_view
        self.controller = controller

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
        self.page.update()

    def show_calendar(self, data_snapshot):
        """Cập nhật dữ liệu và hiển thị màn hình Lịch (Calendar)."""
        self.dashboard.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        self.calendar_view.update_data(data_snapshot)
        self.calendar_view.show()
        # Áp dụng hiệu ứng slide-in transition bằng offset và opacity
        self.calendar_view.offset = ft.Offset(0, 0)
        self.calendar_view.opacity = 1.0
        self.controller.calendar_btn.icon_color = C.ACCENT
        self.page.update()

    async def close_calendar(self):
        """Ẩn màn hình Lịch bằng hiệu ứng và hiển thị lại màn hình Dashboard."""
        # Thực hiện hiệu ứng slide-out sang bên phải
        self.calendar_view.offset = ft.Offset(1, 0)
        self.calendar_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25) # Đợi hiệu ứng kết thúc trước khi ẩn hoàn toàn
        self.calendar_view.hide()
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.controller.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_settings(self):
        """Tải các thiết lập hiện tại và chuyển sang màn hình Cấu hình (Settings)."""
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.grade_overview_view.visible = False
        
        self.settings_view.load_current_settings()
        self.settings_view.visible = True
        self.settings_view.offset = ft.Offset(0, 0)
        self.settings_view.opacity = 1.0
        self.page.update()

    def show_grades_loading(self):
        """Hiển thị màn hình xem điểm ở trạng thái đang tải dữ liệu (loading state)."""
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.grade_overview_view.show_loading()
        self.controller.grades_btn.icon_color = C.ACCENT
        self.page.update()

    def show_grades_data(self, courses_grades, grade_items):
        """Cập nhật dữ liệu điểm số thực tế vào màn hình xem điểm."""
        self.grade_overview_view.update_grades(courses_grades, grade_items)
        self.page.update()

    async def close_grades(self):
        """Đóng màn hình điểm số và quay lại màn hình chính."""
        self.grade_overview_view.hide()
        self.page.update()
        await asyncio.sleep(0.25) # Chờ cho hiệu ứng ẩn hoàn tất
        self.grade_overview_view.visible = False
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.controller.grades_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_detail_loading(self, data):
        """Chuyển sang màn hình chi tiết bài tập ở trạng thái tải dữ liệu."""
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        # Thực hiện hiệu ứng trượt màn hình sang trái
        self.detail_view.offset = ft.Offset(0, 0)
        self.detail_view.opacity = 1.0
        self.detail_view.show_loading(data)
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
        self.detail_view.offset = ft.Offset(1, 0)
        self.detail_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25) # Đợi thời gian hoàn tất animation
        self.detail_view.visible = False
        if detail_from_calendar:
            self.calendar_view.visible = True
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
        self.page.update()

    async def close_settings(self):
        """Đóng màn hình cấu hình với hiệu ứng và hiển thị lại màn hình Dashboard chính."""
        self.settings_view.offset = ft.Offset(1, 0)
        self.settings_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.settings_view.visible = False
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.page.update()
