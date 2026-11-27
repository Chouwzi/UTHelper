# Thiết kế Sửa lỗi Cuộn Bảng điểm, Nhận diện Bảo trì UTH và Giám sát Sentry Đa kênh (`release` / `develop`)

- **Ngày:** 2026-11-28
- **Chiến lược phát hành đã chốt:** Chưa phát hành rời `v2.6.4`; tích hợp nhánh WebView PC đã kiểm chứng cùng cụm Lịch gộp ([2026-11-28-mobile-unified-calendar-schedule-design.md](2026-11-28-mobile-unified-calendar-schedule-design.md)) và 3 hạng mục trong tài liệu này vào `develop`, sau đó phát hành gộp một lần duy nhất qua luồng chuẩn `develop -> main` (phiên bản đề xuất `2.7.0`, `build_number = 2007000` do bổ sung tính năng mới).

---

## 1. Mục đích và Lý do (Intent and Why)

Ba vấn đề thực tế phát sinh trong quá trình vận hành UTHelper của sinh viên:
1. **Nhảy cuộn về đầu trang ở `Bảng điểm` khi mở popup học phần**: Hiện tại khi sinh viên cuộn xuống cuối danh sách môn học ở `Bảng điểm` rồi chạm vào một môn để mở popup chi tiết 2 tab (`Portal` | `Course`), danh sách nền phía sau lập tức bị giật ngược lên vị trí đầu trang (`offset = 0`), và chỉ nhảy trả về vị trí cũ sau khi đóng popup. Trải nghiệm đúng là danh sách nền phải đứng yên ngay tại chỗ vừa bấm trong suốt lúc mở lẫn lúc đóng popup.
2. **Hệ thống trường bảo trì (`01h00 - 04h00`) bị hiểu nhầm thành lỗi mạng hoặc sai mật khẩu**: Qua dữ liệu bắt trực tiếp lúc `02:05` ngày `28/11/2026`, cả 3 máy chủ `courses.ut.edu.vn`, `thnn.ut.edu.vn` và `portal.ut.edu.vn` đều trả mã **`HTTP 403 Forbidden`** kèm trang HTML tiếng Việt `<title>Hệ thống tạm ngưng</title>` (`Thời gian tạm ngưng: 01h00 - 04h00`, `OPEN_HOUR = 4`). Hiện tại `PortalClient` coi mọi mã `403` là `PortalAuthenticationError` (sai thông tin đăng nhập), dẫn tới xóa nhầm cache lịch học tuần và báo sai tài khoản, còn `MoodleClient` báo lỗi kết nối chung chung.
3. **Thiếu định danh môi trường (`release` / `develop`) và kênh cài đặt cụ thể trên Sentry**: Dự án đã có hạ tầng `diagnostics` ẩn danh theo chuẩn fail-closed và `SENTRY_DSN` ở môi trường `release`, nhưng `create_default_runtime()` đang gán cứng `release_channel="stable"` và `install_type="packaged"`, chưa bật `SENTRY_DSN` cho các bản build `develop` và chưa gắn thẻ phân biệt rõ lỗi đến từ bản Google Play (`chplay` / `.aab`), Microsoft Store (`msstore` / `.msix`), Android GitHub (`apk_lts` / `.apk`), Windows cài rời (`exe_lts` / `.exe`, `.msi`), iOS (`ipa_lts` / `.ipa`) hay chạy mã nguồn (`source`).

---

## 2. Mục tiêu, Phi mục tiêu và Phản mục tiêu

### Mục tiêu (Goals)
1. **Giữ nguyên tuyệt đối vị trí cuộn của `Bảng điểm` ngay khi mở mọi hộp thoại (`course_components_dialog`, `planner`, `metric_detail_sheet`, `radar_info`)**:
   - Ghim lại vị trí cuộn ngay sau khi `page.show_dialog(...)` mở hộp thoại và tự động chặn/khôi phục tức thì nếu Flutter phát sự kiện cuộn giả `pixels = 0.0` trong lúc hộp thoại đang mở.
2. **Nhận diện chính xác trạng thái Bảo trì UTH ở tầng `core` và hiển thị thân thiện ở tầng `gui`**:
   - Phân tích được cả trang bảo trì cổng UTH (`HTTP 403` / `503` / `200` HTML `<title>Hệ thống tạm ngưng</title>`, trích xuất khung giờ tạm ngưng như `01h00 - 04h00 (giờ Việt Nam)` và giờ mở lại dự kiến như `04h00 ngày 28/11/2026` từ văn bản HTML hoặc biến `OPEN_HOUR` / `TZ_OFFSET`) lẫn chế độ bảo trì gốc của Moodle (`errorcode = sitemaintenance` / `undergoing maintenance`).
   - Bảo toàn 100% dữ liệu ngoại tuyến đã lưu (bài tập, tài liệu đã tải, lịch học tuần/ngày, bảng điểm) và token đăng nhập khi trường bảo trì; tuyệt đối không chuyển `403` bảo trì thành lỗi sai mật khẩu (`PortalAuthenticationError` / `invalid_credentials`).
   - Hiển thị **Popup thông báo bảo trì 1 lần đầu** trong cùng khung bảo trì khi mở ứng dụng (hoặc khi người dùng chủ động bấm `Làm mới` / mở tác vụ trực tuyến như `WebView` LMS, `Quiz`, nộp bài) kèm **Banner gọn ghim trên đầu màn hình** cho phép xem bình thường toàn bộ dữ liệu đã lưu và tự động ẩn ngay khi máy chủ trường hoạt động trở lại.
3. **Kích hoạt Sentry cho cả `release` và `develop` với định danh đầy đủ từng kênh và định dạng gói**:
   - Phân biệt rõ môi trường `release`, `develop` và `development` (chạy mã nguồn cục bộ có `UTH_SENTRY_DSN`).
   - Phân biệt chính xác kênh phân phối (`distribution_channel`: `chplay`, `msstore`, `apk_lts`, `exe_lts`, `ipa_lts`, `source`) và định dạng gói cài đặt (`package_format`: `aab`, `msix`, `apk`, `exe`, `msi`, `exe_msi`, `ipa`, `source`) trên mọi sự kiện gửi lên Sentry (cả qua `environment`, `dist` và `tags`).

### Phi mục tiêu (Non-Goals)
- Không thu thập thông tin cá nhân (PII: MSSV, mật khẩu, token, tên môn riêng tư hay nội dung bài nộp) lên Sentry; giữ nguyên cơ chế allow-list nghiêm ngặt và cổng đồng thuận `CRASH_REPORTING_CONSENT` hiện có của gói `src/diagnostics/`.
- Không thay đổi thư viện HTTP `urllib` sang client khác.

### Phản mục tiêu (Anti-Goals)
- Không gửi lỗi `UthMaintenanceError` (trường tạm ngưng bảo trì) lên Sentry như lỗi crash ứng dụng.
- Không bật lặp đi lặp lại Popup bảo trì ở mỗi chu kỳ quét tự động nền sau khi sinh viên đã bấm `Đã hiểu` trong cùng đợt bảo trì đó.
- Không nhúng `SENTRY_AUTH_TOKEN` (token quản trị upload debug symbols) vào file cấu hình runtime `diagnostics-config.json` đóng gói trong app (chỉ nhúng khóa public ingestion `SENTRY_DSN` đã qua `validate_public_sentry_dsn`).

---

## 3. Các phương án đã cân nhắc cho Thông báo Bảo trì (Approaches)

1. **Phương án đã chọn: Popup thông báo 1 lần đầu + Banner ghim gọn trên đầu + Giữ nguyên chế độ xem dữ liệu đã lưu**
   - *Ưu điểm*: Sinh viên nắm rõ ngay lập tức vì sao app không lấy được dữ liệu mới và mấy giờ trường mở lại (`04h00`), yên tâm rằng tài khoản không bị lỗi, sau khi đóng popup vẫn tra cứu lịch học sáng mai, điểm số và đọc slide PDF ngoại tuyến không bị cản trở.
2. **Chỉ hiện Banner trên đầu (không có Popup lúc vào app)**: Dễ bị bỏ sót nếu sinh viên nhìn thẳng xuống danh sách bài tập.
3. **Popup chặn kèm đồng hồ đếm ngược toàn màn hình giống web trường**: Rườm rà khi sinh viên chỉ cần mở nhanh app xem phòng học hoặc tài liệu ngoại tuyến.

---

## 4. Chi tiết Thiết kế Đã chốt (Decided Design)

### 4.1. Khóa vị trí cuộn ngay khi mở Popup ở `Bảng điểm` (`src/gui/components/grade_overview_view.py`)
- Giảm `scroll_interval` của `self._scrollable_content` từ `32` xuống `10` (ms) để bắt sát vị trí cuộn ngay trước cú chạm mở môn học.
- Bổ sung phương thức `_pin_scroll_while_modal_open(token: int)`:
  - Ngay sau khi `page.show_dialog(dialog)` (hoặc fallback `overlay`) được gọi ở cả 4 luồng hộp thoại (`_on_course_clicked`, `_show_planner_dialog`, `AcademicRadarCard` metric sheet và radar info), nếu `captured_offset > 0.0` thì gọi ngay `self._schedule_scroll_restore(captured_offset)` ở thời điểm **mở** hộp thoại (chứ không đợi lúc đóng).
- Trong `_on_content_scroll(event)`:
  - Khi `self._open_dialog_tokens` đang khác rỗng (hộp thoại đang mở) và `not self._is_restoring_scroll`: không ghi đè `self._saved_scroll_offset`, đồng thời nếu Flutter phát sự kiện nhảy cuộn lệch khỏi `pinned_offset` quá `2.0px` (điển hình là sự kiện giả `pixels = 0.0` khi chuyển focus sang `AlertDialog`), gọi ngay `self._schedule_scroll_restore(pinned_offset)` để kéo khung nền về đúng chỗ tức khắc.
- Khi đóng hộp thoại cuối cùng (`_end_modal_scroll_guard`), giữ nguyên một nhịp khôi phục an toàn nếu cần để đảm bảo vị trí cuộn không xê dịch cả lúc mở lẫn lúc đóng.

### 4.2. Nhận diện Bảo trì UTH (`src/core/maintenance.py`, `MoodleClient`, `PortalClient`, `AppController`)
1. **Module thuần `core` mới `src/core/maintenance.py` (không phụ thuộc `gui`)**:
   - `MaintenanceNotice` (`frozen=True` dataclass):
     - `source: Literal["courses", "thnn", "portal", "uth"]`
     - `window_text: str` (ví dụ `"01h00 - 04h00 (giờ Việt Nam)"`, chuẩn hóa dấu gạch nối `-`)
     - `reopen_text: str` (ví dụ `"04h00 ngày 28/11/2026"`)
     - `raw_title: str` (mặc định `"Hệ thống UTH đang tạm ngưng bảo trì"`)
     - `dedupe_key: str` (khóa định danh đợt bảo trì, ví dụ `"2026-11-28|01h00-04h00|04h00"`, dùng để chỉ tự bật Popup 1 lần mỗi đợt).
   - `UthMaintenanceError(RuntimeError)` mang thuộc tính `notice: MaintenanceNotice`.
   - Hàm `detect_uth_maintenance(*, status: int | None, body: bytes | str | None, content_type: str = "", source: str = "uth", now_utc: datetime | None = None) -> MaintenanceNotice | None`:
     - Nhận diện trang bảo trì cổng UTH (kể cả khi trả `HTTP 403`, `502`, `503` hoặc `200` `text/html`): kiểm tra các dấu hiệu đặc trưng (`<title>Hệ thống tạm ngưng</title>`, `Tạm ngưng hoạt động`, `Hệ thống đang tạm ngưng hoạt động`, `Hệ thống đang trong thời gian bảo trì`), trích xuất `Thời gian tạm ngưng: ...` và `Hệ thống mở lại lúc: ...`. Nếu thẻ `<b id="openAt">04h00</b>` trong HTML tĩnh chưa có ngày nhưng trong `<script>` có `var OPEN_HOUR = <h>;` và `var TZ_OFFSET = 7;`, tự động tính ngày mở lại theo múi giờ `UTC+7` từ `now_utc` giống hệt logic trang web trường!
     - Nhận diện bảo trì gốc của Moodle: JSON có `errorcode == "sitemaintenance"` hoặc HTML `503` chứa `sitemaintenance` / `undergoing maintenance` / `đang bảo trì`.
     - Loại trừ chặt chẽ trang thử thách của Cloudflare (`Just a moment...` / `_cf_chl_opt`) để không bao giờ nhận nhầm Cloudflare challenge thành bảo trì trường.
2. **Tích hợp vào `PortalClient` (`src/core/portal_client.py`)**:
   - Thêm `PortalMaintenanceError(PortalError, UthMaintenanceError)` (tách hẳn khỏi `PortalAuthenticationError` / `ScheduleAuthenticationError`).
   - Trong `_request_json`: khi bắt `urllib.error.HTTPError` (như `403` hoặc `503`) hoặc khi phản hồi không phải JSON, đọc tối đa `64 KB` body (hỗ trợ giải nén `gzip` an toàn) và chạy `detect_uth_maintenance(..., source="portal")`. Nếu khớp: **giữ nguyên `_token` đang lưu** (không gọi `_discard_token`) và ném `PortalMaintenanceError(notice)`.
   - Tại `TodayScheduleCoordinator`, `ScheduleWeekView` và luồng tải `Bảng điểm`: khi gặp `UthMaintenanceError`, giữ nguyên toàn bộ dữ liệu lịch/điểm đã cache, hiển thị dòng trạng thái bảo trì kèm giờ mở lại và chuyển thông tin `MaintenanceNotice` lên `AppController`.
3. **Tích hợp vào `MoodleClient` và `DataOrchestrator` (`src/core/client.py`, `src/core/data_orchestrator.py`)**:
   - Đọc tối đa `64 KB` body lỗi trong `_open_html`, `_post`, `_post_json`, `_post_multipart`, `_get`, `open_session_raw`, `call_ws_api`, `call_ajax_api` khi mã HTTP là `403`/`502`/`503` hoặc nội dung phi JSON / lỗi JSON `sitemaintenance`.
   - Lưu `self.last_maintenance_notice: MaintenanceNotice | None` trên `MoodleClient` (tự động xóa về `None` ngay khi một request tới site đó thành công bình thường) và đặt `_last_login_error = "maintenance"` (không xóa `MOODLE_WS_TOKEN` đã lưu).
   - Khi `DataOrchestrator` đồng bộ hoạt động hoặc kiểm tra đăng nhập thất bại do bảo trì, ném `UthMaintenanceError(notice)` để `AppController` xử lý chuyên biệt.
4. **Giao diện Popup + Banner Bảo trì trên `AppController` (`src/gui/app_controller.py`)**:
   - **Banner Bảo trì (`self._maintenance_banner`)**: Đặt ngay dưới thanh tiêu đề, nền vàng hổ phách nhẹ (`C.WARNING` độ trong suốt thấp, viền `C.WARNING`), ghi rõ: `"Hệ thống UTH đang bảo trì ({window_text} · Mở lại: {reopen_text}) · Đang xem dữ liệu đã lưu"`, kèm nút `"Chi tiết"` (mở lại Popup) và `"Thử lại"`. Tự động ẩn ngay khi đồng bộ thành công.
   - **Popup Bảo trì (`_show_maintenance_dialog(notice, *, force: bool = False)`)**:
     - Tự động mở **1 lần duy nhất cho mỗi `notice.dedupe_key`** trong phiên khi phát hiện bảo trì lúc khởi động hoặc quét nền (`force=False`).
     - Mở ngay lập tức (`force=True`) khi người dùng **chủ động** bấm nút `Làm mới`, bấm đăng nhập lúc đang bảo trì, hoặc bấm mở `WebView` LMS / làm `Quiz` / nộp bài khi máy chủ đang bảo trì.
     - Nội dung hộp thoại rõ ràng: tiêu đề `"Hệ thống UTH đang bảo trì"`, khung giờ tạm ngưng, thời điểm mở lại dự kiến, thông điệp xác nhận tài khoản và dữ liệu ngoại tuyến trên máy hoàn toàn an toàn, cùng 2 nút `"Thử kiểm tra lại"` và `"Đã hiểu"`. (Tương thích đầy đủ với cầu nối che khuất `WebView2` trên PC).

### 4.3. Giám sát Sentry trên cả `release` và `develop` theo Kênh và Định dạng Gói
1. **Mở rộng Kênh phân phối (`src/core/distribution.py`, `scripts/release_metadata.py`)**:
   - Bổ sung `DistributionChannel.IPA_LTS = "ipa_lts"` vào enum `DistributionChannel` và `VALID_DISTRIBUTION_CHANNELS` (hiển thị `"GitHub Releases (iOS IPA)"`, hỗ trợ tự kiểm tra bản mới qua GitHub; mặc định cho bản đóng gói trên `is_ios` khi không có cấu hình khác).
2. **Mở rộng cấu hình công khai `diagnostics-config.json` (`src/diagnostics/release_config.py`, `scripts/generate_public_runtime_config.py`)**:
   - Nâng cấp tương thích ngược (`schema_version` chấp nhận `1` hoặc `2`):
     - `sentry_dsn`: chuỗi HTTPS ingestion DSN đã qua `validate_public_sentry_dsn` (hoặc `""`).
     - `build_environment`: `"release"` | `"develop"` | `"development"` (mặc định `"release"` nếu đọc file schema 1 cũ; `"development"` khi chạy mã nguồn cục bộ).
     - `package_format`: `"aab"` | `"apk"` | `"msix"` | `"exe_msi"` | `"exe"` | `"msi"` | `"ipa"` | `"source"` (tùy chọn lúc build; ở runtime trên Windows nếu là `"exe_msi"`, tự động tinh chỉnh thành `"exe"` khi `read_install_channel() == "bootstrapper"` và `"msi"` khi `== "msi"`!).
3. **Đưa đầy đủ Metadata vào `DiagnosticContext`, `DiagnosticReport` và Sự kiện Sentry (`src/diagnostics/models.py`, `src/diagnostics/runtime.py`, `src/diagnostics/transport.py`)**:
   - Trong `create_default_runtime()`:
     - Gọi `resolve_distribution_channel()` và `load_runtime_public_config(development=development)` để điền thực tế:
       - `release_channel`: `"release"` | `"develop"` | `"development"` -> ánh xạ thẳng vào trường chuẩn `event["environment"]` của Sentry.
       - `distribution_channel`: `"chplay"` | `"msstore"` | `"apk_lts"` | `"exe_lts"` | `"ipa_lts"` | `"source"`.
       - `package_format`: `"aab"` | `"msix"` | `"apk"` | `"exe"` | `"msi"` | `"exe_msi"` | `"ipa"` | `"source"`.
       - `install_type`: đồng bộ theo kênh/gói thực tế (ví dụ `"chplay"`, `"msstore"`, `"apk_lts"`, `"exe_lts"`, `"ipa_lts"`, `"source"`).
   - Trong `_event_from_report()` (`transport.py`):
     - Gắn `event["environment"] = report.release_channel` (`release` / `develop` / `development`).
     - Gắn `event["dist"] = f"{report.distribution_channel}-{report.package_format}-{report.architecture}"` để Sentry phân tách bản build ngay cấp release.
     - Gắn đầy đủ `tags` có chỉ mục tìm kiếm nhanh trên Sentry:
       - `distribution_channel`: `chplay` | `msstore` | `apk_lts` | `exe_lts` | `ipa_lts` | `source`
       - `package_format`: `aab` | `msix` | `apk` | `exe` | `msi` | `exe_msi` | `ipa` | `source`
       - `build_track`: `release` | `develop` | `development`
       - Giữ nguyên các tag sẵn có (`install_type`, `os_family`, `architecture`, `phase`, `diagnostic.screen`, ...).
   - Loại trừ tường minh `UthMaintenanceError` trong bộ thu thập ngoại lệ tự động (`runtime.py`) để bảo trì trường không sinh sự kiện nhiễu trên Sentry.
4. **Cập nhật toàn bộ GitHub Actions Workflows và Biến Repository**:
   - Sao chép giá trị public `SENTRY_DSN` hiện có từ môi trường `release` lên biến cấp repository (`vars.SENTRY_DSN`) bằng `gh variable set SENTRY_DSN` để mọi workflow chạy trên nhánh `develop` đều nhận được DSN hợp lệ.
   - Truyền tường minh `--build-environment`, `--distribution-channel`, `--distribution-output` và `--package-format` trong tất cả các luồng đóng gói:
     - `.github/workflows/release.yml`:
       - Android APK: `--build-environment release --distribution-channel apk_lts --package-format apk`
       - iOS IPA: `--build-environment release --distribution-channel ipa_lts --package-format ipa`
       - Windows EXE/MSI: `--build-environment release --distribution-channel exe_lts --package-format exe_msi`
     - `.github/workflows/build-android.yml`:
       - Mục `apk_lts`: `--build-environment develop --distribution-channel apk_lts --package-format apk`
       - Mục `chplay`: tự động chọn `release` khi chạy từ tag `v*` / nhánh `main`, hoặc `develop` khi chạy trên nhánh `develop`, kèm `--distribution-channel chplay --package-format aab`.
     - `.github/workflows/build-msstore.yml`: tự động chọn `release` (trên `main`/`v*`) hoặc `develop` (trên `develop`), kèm `--distribution-channel msstore --package-format msix`.
     - `.github/workflows/build-ios.yml`: `--build-environment develop --distribution-channel ipa_lts --package-format ios_app`.
     - `scripts/build_android.ps1` (script đóng gói cục bộ `.aab` / `.apk`): hỗ trợ truyền `SENTRY_DSN` + `--build-environment` + `--package-format` đồng bộ.
   - **Các quyết định kỹ thuật bổ sung sau bước Builder Check**:
     1. **Giữ nguyên điều kiện kích hoạt CI của `develop` để tiết kiệm phút chạy runner**: Không tự động chạy build nặng macOS/Android/Windows trên mọi lệnh `push` vào `develop`; giữ cơ chế `workflow_dispatch` và lọc đường dẫn trên PR như hiện tại, nhưng nhờ gắn `vars.SENTRY_DSN` ở cấp repository, bất kỳ bản build nào xuất ra từ nhánh khác `main`/tag `v*` đều tự động mang `environment = "develop"`, còn bản xuất từ `main` hoặc tag `v*` mang `environment = "release"` (kèm `--require-configured` khi chạy trên kho chính và cập nhật `getsentry/action-release` trong `release.yml` từ `environment: stable` sang `environment: release`).
     2. **Mở rộng hợp lệ danh sách `package_format`**: Hỗ trợ `aab`, `apk`, `msix`, `exe_msi`, `exe`, `msi`, `ipa`, `ios_app` (cho gói `.app` máy ảo iOS của `build-ios.yml`) và `source`.
     3. **Thứ tự hợp nhất các nhánh không xung đột**: Gom 2 commit sửa lỗi/tăng tốc WebView PC (không kèm commit nâng version `2.6.4`) + Lịch gộp (`ScheduleWeekView` mới) trước, sau đó phủ lớp nhận diện Bảo trì (`UthMaintenanceError` / `PortalMaintenanceError`), Khóa cuộn `Bảng điểm` và Sentry đa kênh lên trên cùng nền `ScheduleWeekView` mới, cuối cùng nâng phiên bản một lần duy nhất lên `2.7.0` (`2007000`), đặt `vars.SENTRY_DSN` trên GitHub, hợp nhất vào `develop` rồi phát hành qua `develop -> main`.

---

## 5. Phần để ngỏ cho Bước Lập kế hoạch / Triển khai (Left to the Builder)

- Chi tiết biểu thức chính quy bóc tách chuỗi giờ bảo trì trong `src/core/maintenance.py` (miễn kiểm thử đầy đủ với mẫu HTML `7,448 byte` thực tế vừa thu được từ UTH và các biến thể không có `<script>` hoặc Moodle `503`).
- Bố cục chi tiết của Banner và Popup bảo trì theo hệ màu ngữ nghĩa `C.*` (sáng/tối) của ứng dụng.
