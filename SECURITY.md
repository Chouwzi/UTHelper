# Chính sách bảo mật

## Phiên bản được hỗ trợ

Nhánh `main` và bản phát hành mới nhất nhận bản vá bảo mật. Bản cũ chỉ được hỗ
trợ khi maintainer xác nhận rõ trong advisory.

## Báo cáo riêng tư

Không đăng lỗ hổng, credential, cookie Moodle, dữ liệu sinh viên hoặc proof of
concept có thể khai thác lên issue/discussion công khai.

Hãy tạo báo cáo riêng tư tại:
[GitHub Private Vulnerability Reporting](https://github.com/Chouwzi/UTHelper/security/advisories/new).

Nếu nút báo cáo riêng tư tạm thời không xuất hiện, hãy mở một issue chỉ có tiêu
đề `Security contact request`, không đưa chi tiết lỗ hổng vào nội dung. Maintainer
sẽ khôi phục/kích hoạt kênh advisory riêng tư rồi đóng issue điều phối công khai.

Báo cáo nên có phiên bản/commit, nền tảng, điều kiện tái hiện, mức ảnh hưởng và
biện pháp giảm thiểu tạm thời. Không cần gửi dữ liệu thật hoặc khóa bí mật.

Maintainer sẽ xác nhận khi nhận được báo cáo, đánh giá phạm vi và phối hợp thời
điểm công bố. Không có cam kết bounty. Các bản sửa liên quan release/update phải
giữ kiểm tra chữ ký, inventory, timeout và xác nhận người dùng ở trạng thái
fail-closed.

## Khóa phát hành

Khóa Android và Windows phải được tạo một lần bằng
`scripts/provision_release_credentials.ps1`, sao lưu trong thư mục tuyệt đối nằm
ngoài repository và chỉ cấp quyền cho tài khoản maintainer hiện tại. Mật khẩu khôi
phục được giữ trong Windows Credential Manager; không ghi plaintext, không đưa vào
issue, log CI hoặc commit. Fingerprint và subject là dữ liệu công khai, được ghim
trong updater/manifest để thay khóa luôn là một thay đổi có review.

IPA công khai không chứa khóa Apple hoặc provisioning profile. Người dùng tự ký
lại gói iPhoneOS bằng Apple ID của họ qua công cụ sideload; dự án không yêu cầu và
không lưu Apple ID, mật khẩu hay UDID của người dùng.
