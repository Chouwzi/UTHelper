import flet as ft
from core.demo_sandbox import is_demo_account
from gui.core.theme import C
from config import settings

DEMO_ACCOUNT_BANNER_TEXT: str = (
    "Đang dùng tài khoản xem thử (Demo) với dữ liệu mẫu ngoại tuyến. "
    "Nhập MSSV và mật khẩu thật bên dưới để chuyển sang tài khoản sinh viên UTH."
)


def refresh_demo_account_banner(view, username: str | None = None) -> None:
    """Sync Demo account notice visibility and active semantic theme colors."""
    banner = getattr(view, "_demo_account_banner", None)
    if not isinstance(banner, ft.Container):
        return
    active_username = settings.UTH_USERNAME if username is None else username
    banner.visible = is_demo_account(active_username)
    banner.bgcolor = ft.Colors.with_opacity(0.08, C.ACCENT)
    banner.border = ft.Border.all(1, ft.Colors.with_opacity(0.28, C.ACCENT))
    icon = getattr(view, "_demo_account_banner_icon", None)
    if isinstance(icon, ft.Icon):
        icon.color = C.ACCENT
    text = getattr(view, "_demo_account_banner_text", None)
    if isinstance(text, ft.Text):
        text.color = C.TEXT_PRIMARY


def init_account_controls(view):
    """Khởi tạo các control nhập liệu và trạng thái cho phần tài khoản UTH."""
    view._demo_account_banner_icon = ft.Icon(
        ft.Icons.INFO_OUTLINE_ROUNDED,
        size=18,
        color=C.ACCENT,
    )
    view._demo_account_banner_text = ft.Text(
        DEMO_ACCOUNT_BANNER_TEXT,
        size=12,
        color=C.TEXT_PRIMARY,
        expand=True,
    )
    view._demo_account_banner = ft.Container(
        content=ft.Row(
            controls=[view._demo_account_banner_icon, view._demo_account_banner_text],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        bgcolor=ft.Colors.with_opacity(0.08, C.ACCENT),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.28, C.ACCENT)),
        border_radius=10,
        padding=ft.Padding.symmetric(horizontal=12, vertical=10),
        visible=is_demo_account(settings.UTH_USERNAME),
    )
    # Trường nhập mã số sinh viên (MSSV)
    view._username_field = ft.TextField(
        value=settings.UTH_USERNAME,
        label="Mã số sinh viên (MSSV)",
        text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_submit=lambda e: view._password_field.focus() # Chuyển focus sang trường mật khẩu khi nhấn Enter
    )
    # Trường nhập mật khẩu
    view._password_field = ft.TextField(
        label="Mật khẩu",
        value=settings.UTH_PASSWORD,
        text_size=14, label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        password=True, can_reveal_password=True,
        border_color=C.BORDER, focused_border_color=C.ACCENT,
        color=C.TEXT_PRIMARY,
        bgcolor=C.BG, border_radius=10,
        on_submit=view._handle_test_login # Thực hiện test login khi nhấn Enter
    )
    # Nút bấm kiểm tra thông tin đăng nhập
    view._test_login_btn = ft.Button(
        "Đăng nhập",
        icon=ft.Icons.WIFI_FIND_ROUNDED,
        on_click=view._handle_test_login,
        style=ft.ButtonStyle(
            color=C.ON_ACCENT,
            bgcolor=C.ACCENT,
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=12,
            animation_duration=300
        ),
        height=44
    )
    # Nút đăng xuất & xóa thông tin đăng nhập đã lưu
    view._logout_btn = ft.OutlinedButton(
        "Đăng xuất",
        icon=ft.Icons.LOGOUT_ROUNDED,
        on_click=view._handle_logout,
        style=ft.ButtonStyle(
            color=C.CRITICAL,
            side=ft.BorderSide(1, C.CRITICAL),
            shape=ft.RoundedRectangleBorder(radius=10),
            padding=12,
        ),
        height=44,
    )
    # Thanh ProgressBar hiển thị trạng thái đang kiểm tra (mặc định ẩn)
    view._test_loading_bar = ft.ProgressBar(color=C.ACCENT, bgcolor=C.SURFACE, visible=False)
    # Văn bản & icon thông báo kết quả kiểm tra đăng nhập
    view._test_login_status_icon = ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=18, visible=False)
    view._test_login_status = ft.Text("", size=12, text_align=ft.TextAlign.LEFT)
    view._test_login_status_row = ft.Row(
        controls=[view._test_login_status_icon, view._test_login_status],
        spacing=8,
        alignment=ft.MainAxisAlignment.CENTER,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

def build_account_section(view) -> ft.Control:
    """Xây dựng nội dung chi tiết thiết lập tài khoản UTH."""
    refresh_demo_account_banner(view)
    return ft.Column(
        controls=[
            view._make_themed_label("Thông tin đăng nhập E-learning"),
            view._build_hint("Tài khoản và mật khẩu dùng để đồng bộ dữ liệu bài tập và hạn nộp từ UTH."),
            view._demo_account_banner,
            view._username_field,
            view._password_field,
            view._test_loading_bar,
            view._test_login_status_row,
            view._test_login_btn,
            view._logout_btn,
        ],
        spacing=12,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
