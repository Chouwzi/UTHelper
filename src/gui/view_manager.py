import asyncio
import flet as ft
from gui.core.theme import C

class ViewManager:
    def __init__(self, page, dashboard, detail_view, settings_view, calendar_view, grade_overview_view, controller):
        self.page = page
        self.dashboard = dashboard
        self.detail_view = detail_view
        self.settings_view = settings_view
        self.calendar_view = calendar_view
        self.grade_overview_view = grade_overview_view
        self.controller = controller

    def show_dashboard(self):
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        self.dashboard.visible = True
        self.dashboard.opacity = 1.0
        
        self.controller.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.controller.grades_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_calendar(self, data_snapshot):
        self.dashboard.visible = False
        self.detail_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        self.calendar_view.update_data(data_snapshot)
        self.calendar_view.show()
        self.calendar_view.offset = ft.Offset(0, 0)
        self.calendar_view.opacity = 1.0
        self.controller.calendar_btn.icon_color = C.ACCENT
        self.page.update()

    async def close_calendar(self):
        self.calendar_view.offset = ft.Offset(1, 0)
        self.calendar_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.calendar_view.hide()
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.controller.calendar_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_settings(self):
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
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.detail_view.visible = False
        self.grade_overview_view.show_loading()
        self.controller.grades_btn.icon_color = C.ACCENT
        self.page.update()

    def show_grades_data(self, courses_grades, grade_items):
        self.grade_overview_view.update_grades(courses_grades, grade_items)
        self.page.update()

    async def close_grades(self):
        self.grade_overview_view.hide()
        self.page.update()
        await asyncio.sleep(0.25)
        self.grade_overview_view.visible = False
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.controller.grades_btn.icon_color = C.TEXT_SECONDARY
        self.page.update()

    def show_detail_loading(self, data):
        self.dashboard.visible = False
        self.calendar_view.visible = False
        self.settings_view.visible = False
        self.grade_overview_view.visible = False
        
        self.detail_view.offset = ft.Offset(0, 0)
        self.detail_view.opacity = 1.0
        self.detail_view.show_loading(data)
        self.page.update()

    def show_detail_data(self, full_data):
        self.detail_view.update_detail(full_data)
        self.page.update()

    def show_detail_error(self, data):
        self.detail_view.update_detail(data)
        self.detail_view.show_error_banner()
        self.page.update()

    async def close_detail(self, detail_from_calendar: bool):
        self.detail_view.offset = ft.Offset(1, 0)
        self.detail_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.detail_view.visible = False
        if detail_from_calendar:
            self.calendar_view.visible = True
        else:
            self.dashboard.opacity = 1.0
            self.dashboard.visible = True
        self.page.update()

    async def close_settings(self):
        self.settings_view.offset = ft.Offset(1, 0)
        self.settings_view.opacity = 0.0
        self.page.update()
        await asyncio.sleep(0.25)
        self.settings_view.visible = False
        self.dashboard.opacity = 1.0
        self.dashboard.visible = True
        self.page.update()
