# Ma trận kiểm thử E2E thông báo

Tài liệu này tách rõ bằng chứng tự động, build và thiết bị thật. Không đánh dấu `PASS` cho một nền tảng chỉ vì policy dùng chung đã pass.

## Hợp đồng chung

Mặc định dùng các mốc `4320, 1440, 180, 60, 30, 5` phút. Mỗi activity chỉ được nhắc một lần cho mỗi mốc và mỗi phiên bản deadline.

| Trường hợp | Kết quả mong đợi |
|---|---|
| Deadline còn 3 ngày, 1 ngày, 3 giờ, 1 giờ, 30 phút, 5 phút | Gửi đúng một thông báo tại từng mốc đã bật |
| Ứng dụng đồng bộ sau khi đã qua một mốc | Gửi tối đa một mốc gần nhất còn phù hợp, không bắn dồn |
| Deadline bị đổi | Hủy lịch cũ, tạo lịch mới; mốc của deadline cũ không chặn deadline mới |
| Activity bị xóa hoặc đã nộp/chấm | Hủy lịch còn lại khi `NOTIFY_IGNORE_SUBMITTED=true` |
| Môn bị tắt thông báo | Không gửi, không phân biệt hoa/thường hoặc khoảng trắng thừa |
| Loại activity không nằm trong `NOTIFY_TYPES` | Không gửi |
| DND qua nửa đêm | Dời tới giờ kết thúc DND nếu vẫn trước deadline |
| Nhiều mốc cùng bị dời tới một thời điểm | Chỉ giữ một thông báo cho activity đó |
| DND có giờ bắt đầu bằng giờ kết thúc | Không tạo lịch thông báo |
| Deadline sai định dạng hoặc đã qua | Không tạo lịch và không làm crash pipeline |
| Một kênh ngoài bị lỗi | Kênh khác vẫn gửi; kênh lỗi được thử lại ở vòng sau |
| Bấm thông báo có URL HTTP(S) | Mở đúng Moodle activity |

## Ma trận nền tảng

| Nền tảng | Foreground | Background | App UI đóng/terminated | Khởi động lại thiết bị | Cơ chế |
|---|---|---|---|---|---|
| Windows 10/11 | Toast hiển thị | Toast hiển thị khi tiến trình tray còn chạy | Đóng cửa sổ vẫn chạy qua tray; kill toàn bộ tiến trình thì không thể giao lịch | Autostart khôi phục scheduler và đọc lịch bền vững | `windows-toasts` + scheduler/receipt trên đĩa |
| Android 8–11 | Hiển thị ngay hoặc AlarmManager | AlarmManager/WorkManager | AlarmManager tiếp tục khi app bị đóng | `BOOT_COMPLETED`/thay đổi múi giờ tạo lại lịch | Plugin Kotlin native |
| Android 12+ | Như trên | Exact nếu được cấp, inexact-while-idle nếu chưa cấp exact alarm | Như trên | Như trên | AlarmManager + quyền exact-alarm |
| iOS 13+ | Banner/sound qua delegate | iOS giao local notification | iOS giữ `UNNotificationRequest` sau khi app bị terminate | Hệ điều hành giữ local schedule; mở app để đồng bộ snapshot mới | Swift `UNUserNotificationCenter` |

## Kịch bản thiết bị bắt buộc

Chuẩn bị một tài khoản test hoặc activity giả có deadline sau 6 phút, chỉ bật mốc 5 phút, tắt DND và tắt các kênh ngoài để tránh nhiễu.

1. Cấp quyền thông báo, đồng bộ dữ liệu, đưa app về foreground: nhận đúng một thông báo tại mốc 5 phút.
2. Lặp lại với app ở background.
3. Android/iOS: terminate app sau khi đồng bộ; vẫn nhận thông báo. Windows: đóng cửa sổ về tray, không dùng Task Manager để kill tiến trình.
4. Android: reboot sau khi đã lập lịch; kiểm tra lịch được tạo lại và thông báo vẫn tới.
5. Từ chối quyền: không có banner; diagnostics phải báo quyền chưa được cấp. Cấp lại quyền trong Settings, mở app và đồng bộ lại.
6. Đổi deadline thêm 10 phút trước lúc mốc cũ tới: không nhận lịch cũ, nhận lịch mới.
7. Đánh dấu đã nộp hoặc xóa activity: không nhận các mốc còn lại.
8. Bật DND bao trùm thời điểm mốc: nhận lúc kết thúc DND nếu thời điểm đó vẫn trước deadline; nếu đã qua deadline thì không nhận.
9. Bấm thông báo: mở đúng URL activity; URL rỗng hoặc không phải HTTP(S) không được mở.
10. Bật Telegram/Discord/email cùng local notification, làm hỏng một credential: các kênh hợp lệ vẫn gửi và kênh lỗi không bị đánh dấu đã giao.
11. Kiểm tra thông báo điểm mới: tiêu đề, môn, điểm cũ và điểm mới đều xuất hiện trên local notification.

## Lệnh kiểm chứng tự động

```powershell
$env:PYTHONPATH = 'src;extensions/flet_uth_background_sync/src'
python -m pytest tests/test_notification_e2e_contract.py tests/test_notification_policy.py tests/test_notification_manager_extended.py tests/test_windows_notifier.py tests/test_background_sync_bridge.py extensions/flet_uth_background_sync/tests/test_package_contract.py -q
python -m pytest -q
ruff check src tests extensions/flet_uth_background_sync
```

Build Android trên Windows/Linux:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
pip install -e ".[android-build]"
.\scripts\build_android.ps1 -Target apk
```

Build iOS chỉ chạy trên macOS/Xcode. Workflow `.github/workflows/build-ios.yml` chạy bộ contract trước khi tạo IPA unsigned.

## Trạng thái bằng chứng

| Bằng chứng | Trạng thái |
|---|---|
| Policy + adapter Python | Chạy được trên mọi CI host |
| Windows toast thật | PASS trên máy Windows kiểm thử: immediate toast và lịch + worker + receipt |
| Android APK/native compile | PASS; SHA-256 `FD53108C0D8117BCE888781817F70A166C10371047EE9AA62626E82116060D00`; đủ receiver trong manifest |
| Android foreground/background/terminated/reboot | Cần emulator hoặc thiết bị Android |
| iOS Swift compile/IPA | Workflow macOS `build-ios.yml` |
| iOS foreground/background/terminated | Cần simulator hoặc thiết bị iOS; notification permission và terminated delivery nên xác nhận trên thiết bị thật |
