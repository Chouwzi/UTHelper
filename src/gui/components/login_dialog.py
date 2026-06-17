import flet as ft
import asyncio
from config import settings, save_settings
from gui.core.theme import C

async def show_login_dialog(page: ft.Page, orchestrator, on_success_callback):
    """
    Component hiển thị hộp thoại đăng nhập ban đầu nếu chưa có thông tin username/password.
    Tách biệt khỏi AppController để đảm bảo SRP.
    """
    # --- Error banner (hidden by default) ---
    _error_icon = ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=14, color=C.CRITICAL)
    _error_msg = ft.Text("", size=12, color=C.CRITICAL, expand=True)
    error_banner = ft.Container(
        content=ft.Row(controls=[_error_icon, _error_msg], spacing=8,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C.CRITICAL + "15",
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
        border=ft.border.all(1, C.CRITICAL + "40"),
        visible=False,
        animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
    )

    def _show_error(msg: str):
        _error_msg.value = msg
        error_banner.visible = True
        page.update()

    def _hide_error():
        error_banner.visible = False

    # --- Input fields with prefix icons ---
    username_field = ft.TextField(
        label="Mã số sinh viên (MSSV)",
        prefix_icon=ft.Icons.BADGE_OUTLINED,
        border_radius=10, border_color=C.BORDER, focused_border_color=C.ACCENT,
        bgcolor=C.SURFACE, text_size=14, height=52, autofocus=True,
        color=C.TEXT_PRIMARY,
        label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        cursor_color=C.ACCENT,
    )
    password_field = ft.TextField(
        label="Mật khẩu",
        prefix_icon=ft.Icons.LOCK_OUTLINE_ROUNDED,
        password=True, can_reveal_password=True,
        border_radius=10, border_color=C.BORDER, focused_border_color=C.ACCENT,
        bgcolor=C.SURFACE, text_size=14, height=52,
        color=C.TEXT_PRIMARY,
        label_style=ft.TextStyle(size=13, color=C.TEXT_SECONDARY),
        cursor_color=C.ACCENT,
    )

    # --- Login button ---
    btn_login = ft.Container(
        content=ft.Row(
            controls=[
                ft.Icon(ft.Icons.LOGIN_ROUNDED, size=18, color=ft.Colors.WHITE),
                ft.Text("Đăng nhập", size=14, color=ft.Colors.WHITE,
                        weight=ft.FontWeight.W_600),
            ],
            spacing=8, alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=C.ACCENT,
        padding=ft.Padding.symmetric(vertical=13),
        border_radius=10,
        ink=True,
        alignment=ft.Alignment(0, 0),
        animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
    )

    loading_bar = ft.ProgressBar(color=C.ACCENT, bgcolor=C.SURFACE, visible=False)

    async def on_login_click(e):
        # --- Validation with field highlighting ---
        has_error = False
        if not username_field.value or not username_field.value.strip():
            username_field.border_color = C.CRITICAL
            has_error = True
        else:
            username_field.border_color = C.BORDER

        if not password_field.value or not password_field.value.strip():
            password_field.border_color = C.CRITICAL
            has_error = True
        else:
            password_field.border_color = C.BORDER

        if has_error:
            _show_error("Vui lòng nhập đầy đủ thông tin đăng nhập")
            return

        # --- Loading state ---
        _hide_error()
        btn_login.content = ft.Row(
            controls=[
                ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.WHITE),
                ft.Text("Đang đăng nhập...", size=14, color=ft.Colors.WHITE,
                        weight=ft.FontWeight.W_500),
            ],
            spacing=8, alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        btn_login.disabled = True
        username_field.disabled = True
        password_field.disabled = True
        loading_bar.visible = True
        page.update()

        success = await asyncio.to_thread(
            orchestrator.client.login, username_field.value.strip(),
            password_field.value, True
        )

        if success:
            settings.UTH_USERNAME = username_field.value.strip()
            settings.UTH_PASSWORD = password_field.value
            orchestrator.is_logged_in = True
            save_settings()

            # Success feedback
            btn_login.content = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=18, color=ft.Colors.WHITE),
                    ft.Text("Đăng nhập thành công!", size=14, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600),
                ],
                spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            btn_login.bgcolor = C.SAFE
            loading_bar.visible = False
            page.update()
            await asyncio.sleep(0.7)

            dlg.open = False
            try:
                page.overlay.remove(dlg)
            except (ValueError, AttributeError):
                pass
            page.update()
            page.run_task(on_success_callback)
        else:
            # --- Reset to input state ---
            btn_login.content = ft.Row(
                controls=[
                    ft.Icon(ft.Icons.LOGIN_ROUNDED, size=18, color=ft.Colors.WHITE),
                    ft.Text("Đăng nhập", size=14, color=ft.Colors.WHITE,
                            weight=ft.FontWeight.W_600),
                ],
                spacing=8, alignment=ft.MainAxisAlignment.CENTER,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
            btn_login.bgcolor = C.ACCENT
            btn_login.disabled = False
            username_field.disabled = False
            password_field.disabled = False
            loading_bar.visible = False
            _show_error("Đăng nhập thất bại. Vui lòng kiểm tra tài khoản và kết nối mạng.")

    # --- Auto-clear error on typing ---
    def _on_field_change(e):
        if error_banner.visible:
            _hide_error()
        # Reset border color on typing
        e.control.border_color = C.BORDER
        e.control.update()

    username_field.on_change = _on_field_change
    password_field.on_change = _on_field_change

    async def on_user_submit(e):
        await password_field.focus()

    username_field.on_submit = on_user_submit
    password_field.on_submit = on_login_click
    btn_login.on_click = on_login_click

    # --- Dialog layout ---
    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(radius=16),
        bgcolor=C.BG,
        content=ft.Container(
            width=360,
            content=ft.Column([
                # Header — Icon + Title
                ft.Container(
                    content=ft.Icon(ft.Icons.SCHOOL_ROUNDED, size=44, color=C.ACCENT),
                    alignment=ft.Alignment(0, 0),
                    padding=ft.Padding.only(top=8, bottom=4),
                ),
                ft.Text("Đăng nhập UTH", size=20, weight=ft.FontWeight.BOLD,
                         color=C.TEXT_PRIMARY, text_align=ft.TextAlign.CENTER),
                ft.Text("Hệ thống quản lý hoạt động Elearning",
                         size=12, color=C.TEXT_SECONDARY,
                         text_align=ft.TextAlign.CENTER),

                ft.Container(height=12),

                # Error banner
                error_banner,

                # Form fields
                username_field,
                ft.Container(height=2),
                password_field,

                ft.Container(height=4),
                loading_bar,
                ft.Container(height=4),

                # Login button
                btn_login,

                ft.Container(height=8),

                # Security note
                ft.Container(
                    content=ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.SHIELD_OUTLINED, size=12, color=C.TEXT_SECONDARY),
                            ft.Text("Dữ liệu tài khoản được mã hóa và chỉ lưu trữ cục bộ.",
                                    size=11, color=C.TEXT_SECONDARY),
                        ],
                        spacing=6, alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ),
            ], tight=True, spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(top=8, bottom=4),
        ),
        content_padding=24,
    )

    try:
        page.overlay.append(dlg)
        dlg.open = True
        page.update()
    except Exception:
        import traceback
        traceback.print_exc()
