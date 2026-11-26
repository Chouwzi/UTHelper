# Điều khoản sử dụng UTHelper

Cập nhật: 20/11/2026

UTHelper là phần mềm độc lập, không phải sản phẩm chính thức của Trường Đại học
Giao thông vận tải TP.HCM (UTH). Ứng dụng chỉ hỗ trợ người dùng truy cập và quản
lý dữ liệu học tập mà họ có quyền sử dụng. Thông tin quan trọng vẫn cần được
kiểm tra trên cổng chính thức của nhà trường.

## Tài khoản và dữ liệu học tập

Thông tin đăng nhập được lưu cục bộ trong vùng dữ liệu ứng dụng (Windows
Credential Manager / `%APPDATA%\UTHelper`, hoặc vùng lưu trữ riêng
`FLET_APP_STORAGE_DATA` và `AndroidKeyStore` trên Android) và chỉ được gửi tới
các tên miền Moodle/Portal UTH đã cấu hình qua HTTPS. Không được dùng UTHelper
để truy cập trái phép, thu thập dữ liệu của người khác, gây quá tải hệ thống,
gian lận học tập hoặc vi phạm pháp luật và quy định của UTH.

## Chẩn đoán sự cố ẩn danh

Chẩn đoán sự cố ẩn danh được bật mặc định và có thể tắt bất cứ lúc nào trong
**Cài đặt → Quyền riêng tư** (`Settings → Privacy`). Danh sách trường được phép,
trường bị cấm, giới hạn lưu cục bộ, cách xóa dữ liệu và lưu ý về IP ở tầng mạng
được công bố trong [chính sách quyền riêng tư](PRIVACY.md).

## Kênh phân phối và dịch vụ bên thứ ba

Ứng dụng có thể kết nối Moodle/Portal UTH để cung cấp tính năng học tập, Sentry
để nhận chẩn đoán sự cố ẩn danh (khi được bật) và các kênh thông báo do người
dùng tự cấu hình (Gmail, Discord, Telegram). Việc cập nhật phụ thuộc vào kênh
phân phối bạn cài đặt:

- **Google Play (CH Play)**: việc cập nhật phiên bản được quản lý hoàn toàn bởi
  Google Play; ứng dụng không kiểm tra bản phát hành từ GitHub và không tự tải
  hoặc cài đặt tệp APK.
- **Microsoft Store**: việc cập nhật phiên bản được quản lý hoàn toàn bởi
  Microsoft Store; ứng dụng không kiểm tra bản phát hành từ GitHub và không khởi
  chạy trình cài đặt ngoài cửa hàng.
- **GitHub Releases (LTS — APK Android & EXE/MSI Windows)**: hỗ trợ tùy chọn
  kiểm tra bản phát hành có chữ ký từ GitHub; mọi thao tác cài đặt vẫn yêu cầu
  xác nhận rõ ràng từ người dùng.
- **iOS**: hiện cung cấp bản `.ipa` chưa ký trên GitHub để người dùng tự ký
  (sideload); kênh Apple App Store tạm hoãn do giới hạn kinh phí tài khoản
  Apple Developer và có thể xem xét kêu gọi hỗ trợ cộng đồng sau khi ra mắt ổn
  định trên Google Play.

Việc xử lý dữ liệu tại các dịch vụ bên thứ ba còn chịu chính sách của đơn vị
cung cấp tương ứng.

## Giấy phép, giới hạn và liên hệ

Phần mềm được cung cấp theo giấy phép **PolyForm Noncommercial 1.0.0**, không
bảo đảm hoạt động liên tục hay tương thích với mọi thay đổi từ các dịch vụ bên
ngoài.

Vấn đề thông thường có thể gửi qua GitHub Issues. Báo cáo bảo mật hoặc yêu cầu
liên quan đến quyền riêng tư cần dùng
[GitHub Security Advisory](https://github.com/Chouwzi/UTHelper/security/advisories/new).
