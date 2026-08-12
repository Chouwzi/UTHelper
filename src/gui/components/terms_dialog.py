"""In-app terms and privacy notice shown from Settings."""

from __future__ import annotations

import flet as ft

from gui.core.theme import C
import platform_utils


TERMS_SECTIONS: tuple[tuple[str, str], ...] = (
    (
        "1. Phạm vi và tư cách",
        "UTHelper là phần mềm độc lập, không phải sản phẩm chính thức của Trường Đại học Giao thông vận tải TP.HCM (UTH). Ứng dụng hỗ trợ người dùng xem và quản lý thông tin học tập từ các hệ thống mà người dùng có quyền truy cập.",
    ),
    (
        "2. Tài khoản và dữ liệu học tập",
        "Thông tin đăng nhập được lưu cục bộ ở vùng dữ liệu ứng dụng và chỉ được truyền tới tên miền Moodle/Portal UTH đã cấu hình để đăng nhập, đồng bộ lịch học, hoạt động và bài nộp. Người dùng chịu trách nhiệm bảo vệ tài khoản và chỉ thao tác trên dữ liệu của chính mình.",
    ),
    (
        "3. Chẩn đoán sự cố",
        "Chẩn đoán ẩn danh được bật mặc định và có thể tắt bất cứ lúc nào trong Cài đặt. Payload chỉ gồm phiên bản ứng dụng, hệ điều hành/build, kiến trúc, nhóm thiết bị, số lõi CPU, nhóm RAM/màn hình và stack frame đã rút gọn. Ứng dụng không đưa MSSV, mật khẩu, token, cookie, URL Moodle/Portal, tên môn, nội dung bài, hostname, MAC, serial, vị trí hay IP vào payload. Endpoint Sentry vẫn có thể xử lý IP kết nối ở tầng mạng; dự án phải cấu hình Sentry không lưu IP.",
    ),
    (
        "4. Dịch vụ bên thứ ba",
        "Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, GitHub để kiểm tra bản phát hành và Sentry để nhận chẩn đoán sự cố. Việc xử lý tại các dịch vụ này còn chịu chính sách của đơn vị cung cấp tương ứng.",
    ),
    (
        "5. Sử dụng chấp nhận được",
        "Không được dùng ứng dụng để truy cập trái phép, né cơ chế bảo vệ, gây quá tải hệ thống, thu thập dữ liệu của người khác, gian lận học tập hoặc vi phạm pháp luật và quy định của UTH.",
    ),
    (
        "6. Giới hạn và giấy phép",
        "Phần mềm được cung cấp theo giấy phép PolyForm Noncommercial. Không bảo đảm dịch vụ luôn liên tục hoặc tương thích với mọi thay đổi của hệ thống UTH; người dùng cần kiểm tra thông tin quan trọng trên cổng chính thức.",
    ),
    (
        "7. Liên hệ và thay đổi",
        "Điều khoản có thể được cập nhật khi tính năng hoặc cách xử lý dữ liệu thay đổi. Có thể gửi vấn đề qua GitHub Issues; với báo cáo bảo mật, hãy dùng GitHub Security Advisory của dự án.",
    ),
)


def build_terms_dialog(page: ft.Page) -> ft.AlertDialog:
    content = ft.Column(
        [
            ft.Text(
                "Cập nhật: 12/08/2026",
                size=11,
                color=C.TEXT_SECONDARY,
            ),
            *[
                ft.Column(
                    [
                        ft.Text(title, size=14, weight=ft.FontWeight.W_700, color=C.TEXT_PRIMARY),
                        ft.Text(body, size=12, color=C.TEXT_SECONDARY, selectable=True),
                    ],
                    spacing=5,
                    tight=True,
                )
                for title, body in TERMS_SECTIONS
            ],
        ],
        spacing=14,
        scroll=ft.ScrollMode.AUTO,
        tight=True,
    )
    dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Điều khoản sử dụng & quyền riêng tư"),
        content=ft.Container(
            content=content,
            width=None if platform_utils.IS_MOBILE else 520,
            height=460,
        ),
        actions=[
            ft.TextButton("Đóng", on_click=lambda _event: page.pop_dialog()),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=C.BG,
        shape=ft.RoundedRectangleBorder(
            radius=14,
            side=ft.BorderSide(1, C.BORDER),
        ),
    )
    return dialog


def show_terms_dialog(page: ft.Page) -> None:
    page.show_dialog(build_terms_dialog(page))
