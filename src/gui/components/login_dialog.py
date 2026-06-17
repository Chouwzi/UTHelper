import flet as ft
import asyncio
from config import settings, save_settings
from gui.core.theme import C

async def show_login_dialog(page: ft.Page, orchestrator, on_success_callback):
    """
    Component hiển thị hộp thoại đăng nhập ban đầu nếu chưa có thông tin username/password.
    Tách biệt khỏi AppController để đảm bảo SRP.
    """
    username_field = ft.TextField(
        label="Mã số sinh viên (MSSV)",
        border_radius=8, border_color=C.BORDER, focused_border_color=C.ACCENT, 
        bgcolor=C.SURFACE, text_size=13, height=50, autofocus=True
    )
    password_field = ft.TextField(
        label="Mật khẩu",
        password=True, can_reveal_password=True, 
        border_radius=8, border_color=C.BORDER, focused_border_color=C.ACCENT, 
        bgcolor=C.SURFACE, text_size=13, height=50
    )
    error_text = ft.Text("", color=C.CRITICAL, size=12, visible=False)
    loading_bar = ft.ProgressBar(color=C.ACCENT, bgcolor=C.SURFACE, visible=False)

    btn_login = ft.ElevatedButton(
        "Lưu và đăng nhập",
        style=ft.ButtonStyle(
            color=ft.Colors.WHITE,
            bgcolor=C.ACCENT,
            shape=ft.RoundedRectangleBorder(radius=8),
            padding=12,
            animation_duration=300
        ),
        width=400,
        height=44
    )

    async def on_login_click(e):
        if not username_field.value or not password_field.value:
            error_text.value = "Vui lòng nhập đầy đủ thông tin"
            error_text.visible = True
            page.update()
            return

        btn_login.disabled = True
        btn_login.text = "Đang đăng nhập..."
        username_field.disabled = True
        password_field.disabled = True
        error_text.visible = False
        loading_bar.visible = True
        page.update()

        success = await asyncio.to_thread(orchestrator.client.login, username_field.value, password_field.value, True)

        if success:
            settings.UTH_USERNAME = username_field.value
            settings.UTH_PASSWORD = password_field.value
            orchestrator.is_logged_in = True  # Đồng bộ trạng thái orchestrator
            save_settings()

            # Success feedback before closing
            btn_login.text = "✓ Đăng nhập thành công!"
            btn_login.style = ft.ButtonStyle(
                color=ft.Colors.WHITE,
                bgcolor=C.SAFE,
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=12,
            )
            loading_bar.visible = False
            page.update()
            await asyncio.sleep(0.6)

            dlg.open = False
            try:
                page.overlay.remove(dlg)
            except (ValueError, AttributeError):
                pass
            page.update()
            page.run_task(on_success_callback)
        else:
            btn_login.disabled = False
            btn_login.text = "Lưu và đăng nhập"
            username_field.disabled = False
            password_field.disabled = False
            loading_bar.visible = False
            error_text.value = "Đăng nhập thất bại. Vui lòng kiểm tra tài khoản và kết nối." 
            error_text.visible = True
            page.update()

    async def on_user_submit(e):
        await password_field.focus()

    username_field.on_submit = on_user_submit
    password_field.on_submit = on_login_click
    btn_login.on_click = on_login_click

    dlg = ft.AlertDialog(
        modal=True,
        shape=ft.RoundedRectangleBorder(radius=16),
        content=ft.Container(
            width=340,
            content=ft.Column([
                ft.Image(src="icon.png", width=48, height=48, fit=ft.BoxFit.CONTAIN),
                ft.Text("Đăng nhập UTH", size=20, weight=ft.FontWeight.BOLD, color=C.TEXT_PRIMARY),
                ft.Text("Vui lòng nhập tài khoản của bạn để sử dụng.", size=13, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER),
                ft.Container(height=10),
                username_field,
                password_field,
                loading_bar,
                error_text,
                ft.Container(height=10),
                btn_login,
                ft.Text("Dữ liệu tài khoản được mã hóa và chỉ lưu trữ cục bộ.", size=11, color=C.TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)
            ], tight=True, spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.only(top=10, bottom=5)
        ),
        content_padding=20,
    )

    try:
        page.overlay.append(dlg)
        dlg.open = True
        page.update()
    except Exception:
        import traceback
        traceback.print_exc()
