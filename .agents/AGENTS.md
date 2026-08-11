# Quy tắc Agent cho UTHelper

Tài liệu này là nguồn quy tắc bắt buộc cho mọi Agent/AI làm việc trong repo này. Mục tiêu hiện tại là refactor có kiểm soát theo Clean Code, SOLID/OOP và Clean Architecture, không phá hành vi đã ổn định của UTHelper.

---

## 1. Tài liệu phải đọc trước khi sửa

Trước bất kỳ thay đổi nào, Agent phải đọc:

1. `docs/architecture/refactoring-plan.md`
2. `docs/architecture/refactoring-log.md`
3. File/module liên quan trực tiếp tới task
4. Test hiện có tương ứng trong `tests/`

Không dùng đường dẫn kế hoạch ngoài repo làm nguồn sự thật. Nếu tài liệu ngoài
repo mâu thuẫn với `docs/architecture/refactoring-plan.md`, ưu tiên tài liệu
trong `docs/`.

---

## 2. Ràng buộc kiến trúc

- `core` không được import từ `gui`.
- `models.py` nên tiến tới dữ liệu thuần/ít policy; không thêm phụ thuộc global config mới vào model.
- `gui/components` không được gọi trực tiếp API Moodle tầng thấp như `core.ws_functions` cho workflow mới.
- Workflow có side effect như submit/delete/upload/update metadata phải nằm trong service/use case test được.
- `AppController` là điểm lắp ghép và đấu nối sự kiện; không thêm orchestration dài nếu có thể đặt vào coordinator/service.
- Không đổi tầng HTTP `urllib` sang client khác nếu chưa có bằng chứng Cloudflare/Moodle mới và test live rõ ràng.
- Không lưu secrets trong repo, log, hoặc JSON settings nếu secure storage/keyring dùng được.

---

## 3. Quy trình làm việc

1. Chạy hoặc kiểm tra baseline phù hợp:
   ```powershell
   python -m pytest tests -q --tb=short
   ruff check src tests
   ```
2. Refactor theo bước nhỏ, giữ nguyên hành vi.
3. Với thay đổi boundary lớn, thêm hoặc cập nhật ADR trong `docs/adr/`.
4. Sau mỗi giai đoạn, cập nhật `docs/architecture/refactoring-log.md` gồm ngày,
   phạm vi, file đã chạm, trạng thái test/lint.
5. Nếu task chạm UI Flet, chạy smoke thủ công bằng desktop/web mode khi môi trường cho phép.

---

## 4. Luồng Git

- Ưu tiên làm việc trên nhánh refactor riêng, ví dụ `feature/refactor-clean` hoặc nhánh `codex/...` nếu tạo bởi Codex.
- Không commit trực tiếp vào `main` hoặc `develop`.
- Không revert thay đổi của người khác nếu không được yêu cầu rõ ràng.

---

## 5. Định nghĩa hoàn tất

- Test liên quan pass; với refactor rộng phải chạy full `python -m pytest tests -q --tb=short`.
- `ruff check src tests` pass hoặc mọi lỗi còn lại được ghi rõ trong plan/knowledge base.
- Không tăng circular imports hoặc dependency từ presentation xuống adapter tầng thấp.
- Tài liệu refactor được cập nhật cùng thay đổi.
- Người dùng có thể hiểu: đã đổi gì, vì sao, kiểm chứng bằng gì.
