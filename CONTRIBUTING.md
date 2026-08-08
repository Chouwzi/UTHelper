# Đóng góp cho UTHelper

Cảm ơn bạn muốn cải thiện UTHelper. Repository chấp nhận pull request rõ mục
tiêu, có test và tôn trọng dữ liệu riêng tư của sinh viên.

## Quy trình

1. Fork repository và tạo nhánh nhỏ từ `develop` (`feature/...`, `fix/...` hoặc
   `docs/...`). Không mở thay đổi tính năng trực tiếp vào `main`; `main` chỉ nhận
   phiên bản đã được kiểm chứng để phát hành.
2. Mỗi pull request nên giải quyết một vấn đề. Mô tả hành vi trước/sau, rủi ro,
   migration và cách hoàn tác khi có thay đổi trạng thái bền vững.
3. Viết test hồi quy trước thay đổi hành vi. Mock dịch vụ trường ở biên mạng;
   không đưa tài khoản, cookie, token Moodle hoặc dữ liệu thật vào fixture/log.
4. Chạy trước khi gửi pull request:

   ```powershell
   $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'
   python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short
   ruff check src tests scripts
   ```

5. Giải quyết toàn bộ review và giữ CI xanh. Maintainer có thể yêu cầu tách pull
   request quá rộng hoặc đóng thay đổi cố tình bỏ qua trust boundary, timeout,
   kiểm tra chữ ký hay quyền riêng tư.

## Quy tắc kỹ thuật

- Không thêm đường cài đặt/update im lặng. Người dùng phải xác nhận cài đặt,
  thoát, khởi động lại hoặc mở App Store/TestFlight.
- Mọi network/process/UI wait phải có deadline hữu hạn và dọn đúng tài nguyên do
  nó tạo ra.
- Không sửa file Flutter/Dart sinh ra sau compile để né quy trình nguồn.
- Không dùng action GitHub dạng tag mutable; pin full commit SHA đã review.
- Thay đổi workflow release, installer, updater, credential hoặc security cần
  CODEOWNER review và test fail-closed.
- Tuân thủ giấy phép PolyForm Noncommercial 1.0.0 của repository.

## Báo cáo lỗ hổng

Không mở issue công khai cho lỗ hổng chưa vá. Dùng hướng dẫn trong
[`SECURITY.md`](SECURITY.md).

