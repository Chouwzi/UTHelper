# Cơ sở tri thức Tái cấu trúc (Refactoring Knowledge Base)

Tài liệu này ghi lại các phân tích cấu trúc, quyết định kỹ thuật và tiến độ thực tế trong quá trình tái cấu trúc codebase `UTH-Elearning-Alert` theo các nguyên lý Clean Code và SOLID.

---

## 1. Phân tích Chi tiết Giao diện & Kiến trúc Cũ

### A. SettingsView (`src/gui/components/settings_view.py`)
*   **Kích thước:** 2551 dòng.
*   **Vấn đề chính:**
    *   Hàm lồng phức tạp `open_color_picker` nằm ngay trong constructor `__init__` chiếm khoảng 80 dòng, chứa các hàm con xử lý cập nhật RGB sliders và Hex text field.
    *   Toàn bộ cấu trúc UI của các tab (Cài đặt tài khoản, Ứng dụng, Thông báo Telegram/Discord/Email, Cài đặt Theme) đều viết trực tiếp trong constructor khổng lồ.
*   **Giải pháp tái cấu trúc:**
    1.  Trích xuất logic Color Picker sang một file mới `src/gui/components/color_picker.py` chứa class `ColorPicker`.
    2.  Tách các khối giao diện theo tab ra các phương thức trợ giúp trong class `SettingsView` (ví dụ: `_build_account_settings()`, `_build_app_settings()`, `_build_notification_settings()`, `_build_theme_settings()`).
    3.  Rút gọn constructor `__init__` xuống dưới 100 dòng.

### B. DetailView (`src/gui/components/detail_view.py`)
*   **Kích thước:** 1675 dòng.
*   **Vấn đề chính:**
    *   Hàm `_do_remove_file_sync` tự thực hiện tải xuống các file muốn giữ và upload lại. Logic này nên thuộc về một Service/Controller, không nên đặt trong View.
    *   Rất nhiều dialog con (AlertDialog xác nhận xóa, chỉnh sửa thông tin) và SnackBar được khởi tạo phân tán.
*   **Giải pháp tái cấu trúc:**
    1.  Tách logic mạng và API Moodle sang `src/core/moodle_service.py`.
    2.  Phân tách giao diện bảng danh sách file và khu vực hiển thị trạng thái điểm số ra các phương thức riêng.

### C. AppController (`src/gui/app_controller.py`)
*   **Kích thước:** 1860 dòng.
*   **Vấn đề chính:**
    *   Quản lý các vòng lặp đếm ngược (`_countdown_loop_async`) và đồng bộ dữ liệu tự động (`_auto_refresh_loop_async`) ngay tại luồng xử lý chính.
*   **Giải pháp tái cấu trúc:**
    1.  Đóng gói các tác vụ nền vào một bộ điều phối độc lập hoặc tách nhỏ các hàm quản lý vòng lặp.

---

## 2. Nhật ký Tiến độ Tái cấu trúc

| Ngày | Nhánh | Giai đoạn | Công việc đã hoàn tất | Trạng thái test |
| :--- | :--- | :--- | :--- | :--- |
| 2026-07-01 | `feature/refactor-clean` | Khởi động | Thiết lập AGENTS.md, tạo nhánh mới, tạo knowledge base | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 1 | Trích xuất `ColorPicker` và phân rã constructor của `SettingsView` thành các hàm trợ giúp | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 2 | Tạo lớp dịch vụ `MoodleService` để gom nhóm các API; phân rã constructor của `DetailView` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 3 | Phân rã phương thức `_init_ui` khổng lồ của `AppController` thành các hàm trợ giúp nhỏ gọn, tự mô tả | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 4 | Phân chia các Tab cài đặt của `SettingsView` thành các file độc lập trong thư mục `settings/` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 5 | Trích xuất `theme_presets.py` và triển khai `ViewManager` để giảm ghép cặp cho `AppController` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 6 | Trích xuất bảng danh sách file đã nộp (`SubmittedFilesTable`) của `DetailView` thành component riêng | Đạt 296/296 |
| 2026-07-01 | workspace hiện tại | Audit senior | Nghiên cứu Clean Code/SOLID/OOP/Architecture, tạo `docs/CODEBASE_ARCHITECTURE_REVIEW_PLAN.md`, cập nhật `.agents/AGENTS.md` làm rule nguồn trong repo | Test đạt 296 passed, 22 skipped; `ruff check src` còn 13 lỗi |

---

## 3. Baseline Kiến trúc Sau Audit 2026-07-01

### Kết quả kiểm chứng

*   `python -m pytest tests -q --tb=short`: **296 passed, 22 skipped, 5 warnings**.
*   `ruff check src`: **13 lỗi**, chủ yếu unused imports và unused local variable.
*   `src`: 59 file Python, 14.621 dòng.
*   `tests`: 29 file Python, 3.930 dòng.

### Phát hiện chính

1.  `AppController`, `SettingsView`, `DetailView` vẫn là các điểm tập trung trách nhiệm lớn nhất.
2.  `DetailView` còn trực tiếp xử lý workflow Moodle submit/upload/re-upload/delete metadata; nên trích sang use case/service.
3.  `MoodleService` đã tồn tại nhưng chưa thành boundary chính; nhiều nơi vẫn gọi `core.ws_functions` trực tiếp.
4.  `models.py` còn phụ thuộc `config.settings` để tính urgency; nên chuyển sang policy inject được.
5.  `.agents/AGENTS.md` cũ trỏ tới đường dẫn ngoài repo; đã cập nhật để dùng `docs/CODEBASE_ARCHITECTURE_REVIEW_PLAN.md` làm nguồn sự thật.

### Ưu tiên refactor tiếp theo

1.  Phase 0: sửa Ruff baseline, cập nhật README badge/test count, tạo ADR đầu tiên.
2.  Phase 1: trích `SubmissionWorkflow` khỏi `DetailView`.
3.  Phase 2: tách `RefreshCoordinator`, `AppState`, `UpdateController` khỏi `AppController`.
4.  Phase 3: biến `MoodleService` thành facade/use-case boundary thật sự.
