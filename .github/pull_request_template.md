## Mục tiêu

Mô tả ngắn vấn đề và kết quả của thay đổi.

## Kiểm thử

- [ ] Đã thêm/cập nhật test hồi quy phù hợp.
- [ ] `python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short`
- [ ] `ruff check src tests scripts`
- [ ] Không có lệnh chờ vô hạn hoặc tiến trình nền không được dọn dẹp.

## Bảo mật và quyền riêng tư

- [ ] Không commit credential, token, cookie, dữ liệu sinh viên hoặc log riêng tư.
- [ ] Không nới lỏng TLS, kiểm tra chữ ký, timeout hay quyền GitHub Actions.
- [ ] Nếu thay đổi release/update, đã mô tả trust boundary và đường rollback.

## Ảnh hưởng người dùng

Nêu migration, thay đổi cài đặt, tương thích ngược và tài liệu liên quan (nếu có).

