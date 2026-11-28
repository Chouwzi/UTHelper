"""In-app terms and privacy notice shown from Settings and the sign-in dialog."""

from __future__ import annotations

import flet as ft

from core.distribution import (
    DistributionChannel,
    DistributionProfile,
    resolve_distribution_channel,
)
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
        "Chẩn đoán sự cố ẩn danh (Sentry) và phân tích sử dụng ẩn danh (PostHog) được bật mặc định và có thể tắt độc lập bất cứ lúc nào trong Cài đặt → Riêng tư. Chẩn đoán sự cố không tạo mã định danh thiết bị cố định; payload chỉ gồm phiên bản ứng dụng, hệ điều hành/build, kiến trúc, nhóm thiết bị, số lõi CPU, nhóm RAM/màn hình và stack frame đã rút gọn. Phân tích sử dụng ẩn danh chỉ hoạt động sau khi đăng nhập tài khoản thật (bỏ qua tài khoản xem thử), dùng mã cài đặt ngẫu nhiên cục bộ (UUIDv4) tách biệt hoàn toàn với Sentry kèm mã phiên ngắn hạn (UUIDv7) ở chế độ không tạo hồ sơ cá nhân ($process_person_profile: false), và xóa ngay mã cài đặt cùng hàng đợi cục bộ khi tắt hoặc đăng xuất. Ứng dụng không đưa MSSV, mật khẩu, token, cookie, URL Moodle/Portal, tên môn, nội dung bài, điểm số, hostname, MAC, serial, vị trí hay IP vào payload. Endpoint Sentry và PostHog vẫn có thể xử lý IP kết nối ở tầng mạng; dự án phải cấu hình Sentry và PostHog không lưu IP.",
    ),
    (
        "4. Dịch vụ bên thứ ba",
        "Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, GitHub để kiểm tra bản phát hành, Sentry để nhận chẩn đoán sự cố và PostHog để thống kê mức độ sử dụng tính năng ẩn danh (khi được bật). Việc xử lý tại các dịch vụ này còn chịu chính sách của đơn vị cung cấp tương ứng.",
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


def get_terms_sections(
    profile: DistributionProfile | None = None,
) -> tuple[tuple[str, str], ...]:
    active = profile if profile is not None else resolve_distribution_channel()
    if active.supports_github_self_update:
        third_party_body = (
            "Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, "
            "GitHub để kiểm tra bản phát hành, Sentry để nhận chẩn đoán sự cố (khi được bật), "
            "PostHog để thống kê mức độ sử dụng tính năng ẩn danh (khi được bật) "
            "và các kênh thông báo do người dùng tự cấu hình (Gmail, Discord, Telegram). "
            "Việc xử lý tại các dịch vụ này còn chịu chính sách của đơn vị cung cấp tương ứng."
        )
    elif active.channel is DistributionChannel.CHPLAY:
        third_party_body = (
            "Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, "
            "Sentry để nhận chẩn đoán sự cố ẩn danh (khi được bật), "
            "PostHog để thống kê mức độ sử dụng tính năng ẩn danh (khi được bật) và các kênh thông báo "
            "do người dùng tự cấu hình (Gmail, Discord, Telegram). Việc cập nhật phiên bản "
            "trên kênh phân phối này được quản lý hoàn toàn bởi Google Play. Việc xử lý tại "
            "các dịch vụ bên thứ ba còn chịu chính sách của đơn vị cung cấp tương ứng."
        )
    else:
        store_name = active.store_display_name or "Microsoft Store"
        third_party_body = (
            "Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, "
            "Sentry để nhận chẩn đoán sự cố ẩn danh (khi được bật), "
            "PostHog để thống kê mức độ sử dụng tính năng ẩn danh (khi được bật) và các kênh thông báo "
            "do người dùng tự cấu hình (Gmail, Discord, Telegram). Việc cập nhật phiên bản "
            f"trên kênh phân phối này được quản lý hoàn toàn bởi {store_name}. Việc xử lý tại "
            "các dịch vụ bên thứ ba còn chịu chính sách của đơn vị cung cấp tương ứng."
        )

    sections = list(TERMS_SECTIONS)
    sections[3] = ("4. Dịch vụ bên thứ ba", third_party_body)
    return tuple(sections)


def _numeric_dim(obj: object, attr: str) -> float:
    raw = getattr(obj, attr, None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    val = float(raw)
    return val if val > 0 else 0.0


def build_terms_dialog(
    page: ft.Page,
    profile: DistributionProfile | None = None,
) -> ft.AlertDialog:
    sections = get_terms_sections(profile)
    page_w = _numeric_dim(page, "width") or _numeric_dim(
        getattr(page, "window", None), "width"
    )
    page_h = _numeric_dim(page, "height") or _numeric_dim(
        getattr(page, "window", None), "height"
    )
    plat = getattr(page, "platform", None)
    is_mobile = bool(
        platform_utils.UI_IS_MOBILE
        or plat in (ft.PagePlatform.ANDROID, ft.PagePlatform.IOS)
        or str(plat or "").lower() in ("android", "ios")
    )
    is_compact = bool(is_mobile or (0 < page_w < 600))
    content_width = (
        None
        if is_mobile
        else (min(520.0, max(240.0, page_w - 80.0)) if page_w > 0 else 520.0)
    )
    content_height = (
        min(460.0, max(220.0, page_h - 190.0)) if page_h > 0 else 460.0
    )
    content = ft.Column(
        [
            ft.Text(
                "Cập nhật: 28/11/2026",
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
                for title, body in sections
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
            width=content_width,
            height=content_height,
        ),
        inset_padding=ft.Padding.symmetric(
            horizontal=16 if is_compact else 28,
            vertical=20,
        ),
        content_padding=ft.Padding.symmetric(
            horizontal=16 if is_compact else 24,
            vertical=12,
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


def show_terms_dialog(
    page: ft.Page,
    profile: DistributionProfile | None = None,
) -> None:
    page.show_dialog(build_terms_dialog(page, profile=profile))
