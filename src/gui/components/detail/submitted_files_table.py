import flet as ft
from gui.core.theme import C
from datetime import datetime

def build_submitted_files_ui(view):
    """Xây dựng giao diện hiển thị danh sách các file đã nộp trên server."""
    view._submitted_files_col.controls.clear()
    view._selected_file_indices.clear()
    view._is_multiselect_mode = False

    # Nếu không có file nào được nộp thì ẩn vùng thông tin file nộp
    if not view._submitted_files:
        view._submitted_area.visible = False
        view._multiselect_btn.visible = False
        view._batch_delete_btn.visible = False
        return

    # Duyệt qua từng file đã nộp để dựng dòng giao diện tương ứng
    for i, f in enumerate(view._submitted_files):
        size_b = f.get('size', 0)
        size_kb = size_b / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"

        # Định dạng thời gian
        tmod = f.get('timemodified', 0)
        tcreated = f.get('timecreated', 0)
        mod_str = datetime.fromtimestamp(tmod).strftime('%d/%m/%Y %H:%M') if tmod else '—'
        created_str = datetime.fromtimestamp(tcreated).strftime('%d/%m/%Y %H:%M') if tcreated else '—'

        # Cột chứa các thông tin metadata bổ trợ của file
        meta_col = ft.Column(controls=[
            ft.Row([
                ft.Text("Lần sửa đổi cuối", size=10, color=C.TEXT_SECONDARY, width=120),
                ft.Text(mod_str, size=10, color=C.TEXT_PRIMARY),
            ], spacing=4),
            ft.Row([
                ft.Text("Ngày tạo", size=10, color=C.TEXT_SECONDARY, width=120),
                ft.Text(created_str, size=10, color=C.TEXT_PRIMARY),
            ], spacing=4),
            ft.Row([
                ft.Text("Kích thước", size=10, color=C.TEXT_SECONDARY, width=120),
                ft.Text(size_str, size=10, color=C.TEXT_PRIMARY),
            ], spacing=4),
        ], spacing=2)

        # Hộp kiểm checkbox phục vụ chế độ chọn xóa nhiều file (mặc định ẩn)
        cb = ft.Checkbox(
            value=False,
            on_change=lambda e, idx=i: view._on_file_checkbox_changed(idx, e.control.value),
            active_color=C.CRITICAL,
            visible=False,  # Sẽ được hiển thị khi kích hoạt chế độ multiselect
        )

        row = ft.Container(
            content=ft.Column(controls=[
                ft.Row(
                    controls=[
                        cb,
                        ft.Icon(ft.Icons.DESCRIPTION_ROUNDED, size=16, color=C.SAFE),
                        ft.Text(f.get('name', ''), size=12, color=C.TEXT_PRIMARY,
                                expand=True, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                                weight=ft.FontWeight.W_500),
                        ft.IconButton(
                            ft.Icons.EDIT_ROUNDED, icon_size=14,
                            icon_color=C.ACCENT,
                            tooltip="Chỉnh sửa",
                            on_click=lambda _, idx=i: view._show_file_edit_dialog(idx),
                            style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                        ),
                        ft.IconButton(
                            ft.Icons.DELETE_OUTLINE_ROUNDED, icon_size=14,
                            icon_color=C.CRITICAL,
                            tooltip="Xóa file này",
                            on_click=lambda _, idx=i: view._confirm_single_delete(idx),
                            style=ft.ButtonStyle(padding=ft.Padding.all(4)),
                        ),
                    ],
                    spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    content=meta_col,
                    padding=ft.Padding.only(left=28),
                ),
            ], spacing=4),
            bgcolor=C.BG,
            border=ft.Border.all(1, C.SAFE + "30"),
            border_radius=6,
            padding=ft.Padding.symmetric(horizontal=10, vertical=8),
        )
        view._submitted_files_col.controls.append(row)

    view._submitted_files_col.visible = True
    view._submitted_area.visible = True
    view._edit_submitted_btn.visible = True
    # Chỉ hiển thị nút chọn nhiều khi danh sách có từ 2 file trở lên
    view._multiselect_btn.visible = len(view._submitted_files) > 1
    view._batch_delete_btn.visible = False
