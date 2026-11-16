# ADR 0001: Ranh giới refactor và hướng phụ thuộc

Ngày: 2026-07-01

## Trạng thái

Accepted

## Bối cảnh

UTHelper đang hoạt động ổn định nhưng còn một số class lớn như `AppController`, `SettingsView`, `DetailView` và một số workflow Moodle còn nằm trực tiếp trong tầng giao diện. Repo đã có nhiều bước refactor trước đó, nhưng chưa có ràng buộc kiến trúc tự động để ngăn nợ kỹ thuật tái phát.

Các nguồn tham chiếu chính cho quyết định này gồm Clean Architecture dependency rule, SOLID/DIP, Martin Fowler refactoring, C4 Model và ADR practice.

## Quyết định

Trong các phase refactor tiếp theo:

- `core` không được phụ thuộc vào `gui`.
- `gui.components` không được thêm phụ thuộc mới vào `gui.app_controller`.
- `gui` không được thêm phụ thuộc mới vào `core.ws_functions`; workflow Moodle mới phải đi qua service/use case.
- `models.py` không được thêm phụ thuộc cấu hình mới; mục tiêu dài hạn là tách `settings` khỏi model.
- Refactor phải đi theo bước nhỏ, giữ nguyên hành vi, có test/lint baseline trước và sau.

Hiện codebase vẫn còn một số vi phạm lịch sử. Chúng được khóa bằng test kiến trúc dạng allowlist để không phát sinh vi phạm mới trong khi từng phase xử lý nợ cũ.

## Hệ quả

- Tốc độ refactor ban đầu chậm hơn vì phải giữ tương thích và cập nhật test.
- Các service/use case mới cần được thiết kế mock-friendly.
- Khi loại bỏ được một vi phạm lịch sử, phải cập nhật allowlist trong `tests/test_architecture_boundaries.py`.
- Mọi thay đổi boundary lớn nên có ADR riêng hoặc cập nhật ADR này.

