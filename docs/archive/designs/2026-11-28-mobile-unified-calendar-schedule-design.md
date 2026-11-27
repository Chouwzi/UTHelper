# Thiết kế Giao diện Lịch và Lịch học Gộp (`Tháng` và `Tuần` trên Mobile + PC, giữ tab `Lịch học` riêng trên PC)

- **Ngày:** 2026-11-28
- **Nhánh triển khai:** `feat/mobile-unified-calendar-schedule` (hợp nhất vào `develop` theo Gitflow)
- **Phạm vi file chính:** `src/gui/components/calendar_view.py`, `src/gui/components/schedule_week_view.py`, `tests/test_calendar_week_view.py`, `tests/test_schedule_week_view.py`

---

## 1. Mục đích và Lý do (Intent and Why)

Trên điện thoại, tab `Lịch -> Lịch học` hiện tại chỉ hiển thị từng ngày đơn lẻ (`< Thứ 3, 24/11/2026 >`) và nhảy lẻ 1 ngày mỗi lần bấm mũi tên hoặc vuốt ngang. Sinh viên muốn xem lịch tuần sau phải bấm liên tiếp 7 đến 9 lần và không có góc nhìn bao quát cả tuần học những buổi nào, ngày nào trống.

Ngược lại, nếu gộp thô các thẻ lịch học cao (~145px/thẻ, có nút `Chi tiết` nằm riêng) chung với thẻ bài tập LMS trong những ngày học dày từ trưa đến tối (ví dụ 3 ca liên tiếp từ `12:00` đến `20:00`), danh sách của một ngày sẽ bị kéo quá dài.

Theo quyết định sản phẩm của người dùng:
- **Áp dụng giao diện gộp thông minh (Phương án 1) cho cả `Tháng` và `Tuần` trên cả Điện thoại lẫn Máy tính (PC)** để ở bất kỳ thiết bị nào sinh viên cũng xem được mật độ ca học, dòng Timeline ca học gọn (~50px/ca) chung với hạn bài tập và 3 chip lọc nhanh (`Theo ngày` / `Cả tuần` / `Bài tập`).
- **Trên Điện thoại**: Bỏ tab `Lịch học` riêng, chỉ giữ **2 tab `Tháng | Tuần`**.
- **Trên Máy tính (PC)**: **Giữ lại tab thứ 3 `Lịch học` riêng** dạng bảng 7 cột rộng (`ScheduleWeekView`), đồng thời sửa dứt điểm 2 lỗi giao diện hiện có:
  1. **Lệch chiều cao thẻ lịch học giữa các cột ngày trên PC (`ScheduleWeekView`)** khi độ dài tên môn hoặc địa chỉ phòng/cơ sở khác số dòng (`E-Learning` 1 dòng so với `F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)` 2 dòng).
  2. **Kẹt sáng đồng thời 2 nút chuyển chế độ `Tuần` và `Lịch học` (`CalendarView`)** khi chuyển từ `Lịch học` quay về `Tuần` hoặc `Tháng`.

---

## 2. Mục tiêu, Phi mục tiêu và Phản mục tiêu

### Mục tiêu (Goals)
1. **Trải nghiệm `Tháng` và `Tuần` thống nhất trên cả Mobile và PC**:
   - Dải 7 ngày `T2..CN` ở chế độ `Tuần` hiển thị **3 vạch ngang mini đại diện 3 buổi (`Sáng` / `Chiều` / `Tối`)** ngay trong ô ngày (kèm chấm/huy hiệu hạn bài tập sẵn có).
   - Lưới `Tháng` hiển thị thêm chỉ báo mảnh màu `C.ACCENT` cho ngày có lịch học bên cạnh chỉ báo hạn bài tập.
   - Khung chi tiết bên dưới (`_detail_panel`) có hàng **3 chip lọc nhanh (`Theo ngày` · `Cả tuần ({n} ca)` · `Bài tập ({m})`)**, nút làm mới lịch học nhỏ gọn và hiển thị **Dòng Timeline Ca học 1 chạm (~48-54px/ca)** mở hộp thoại `Chi tiết buổi học`.
2. **Thanh chuyển chế độ thích ứng Mobile vs PC**:
   - Trên **Mobile** (`platform_utils.UI_IS_MOBILE` là `True` hoặc chiều rộng trang `< 600px` đồng bộ với thanh điều hướng đáy): ẩn nút tab `Lịch học`, chỉ hiển thị **2 tab `Tháng | Tuần`**.
   - Trên **PC** (`not platform_utils.UI_IS_MOBILE` và chiều rộng trang `>= 600px`): hiển thị đầy đủ **3 tab `Tháng | Tuần | Lịch học`**.
3. **Sửa triệt để 2 lỗi giao diện trên PC**:
   - Đồng bộ chiều cao cố định cho mọi thẻ tóm tắt môn học trên bảng tuần 7 cột của PC (`ScheduleWeekView._session_summary_card`, `narrow=False`), ghim nút `Chi tiết` thẳng hàng ở đáy thẻ.
   - Xóa lỗi kẹt sáng kép nút `Tuần` và `Lịch học` trong `CalendarView._toggle_view_mode`.

### Phi mục tiêu (Non-Goals)
- Không thay đổi API Portal (`lichhoc/lichTuan`), định dạng file cache `WeekScheduleCache` (`SCHEMA_VERSION = 1`), hay `TodaySchedulePanel` ngoài màn hình `Tổng quan`.
- Không vi phạm ranh giới kiến trúc (`core` không import `gui`, không thay đổi tầng HTTP `urllib`).

### Phản mục tiêu (Anti-Goals)
- Không làm đổi chiều cao cố định của `_period_pager` / `_desktop_period_host` (`316px` cho `Tháng`, `88px` cho `Tuần`) hoặc làm hỏng cử chỉ vuốt 3 trang (`PageView` trên mobile, `Dismissible` trên desktop).
- Không xóa `count_text` (`"{n} bài"`) hoặc `dots_row` trong `week_cells` đang được kiểm chứng bởi bộ test hiện hữu.
- Không bắn dồn dập 5-6 request mạng lên Portal mỗi lần người dùng lướt nhanh qua nhiều tháng.

---

## 3. Các phương án đã cân nhắc (Approaches)

1. **Phương án 1 - Đã chọn: Gộp lịch học vào `Tháng` và `Tuần` (áp dụng cả Mobile và PC; ẩn tab `Lịch học` riêng trên Mobile, giữ tab `Lịch học` bảng 7 cột trên PC) + Vạch 3 ca + Dòng Timeline gọn (~50px) + 3 Chip lọc**
   - *Ưu điểm*: Giải quyết trọn vẹn cả hai nhu cầu đối lập (chuyển tuần bằng 1 lần vuốt, nhìn vạch 3 ca bao quát cả tuần, ngày học nặng 3 ca `12:00 - 20:00` chỉ cao ~`160px` nên vẫn thấy bài tập cùng ngày, bấm `Cả tuần` thấy toàn bộ lịch học tuần đó), đồng thời mang lại trải nghiệm nhất quán trên cả điện thoại lẫn máy tính mà vẫn giữ bảng 7 cột rộng rãi riêng cho PC.
   - *Đánh đổi*: Cần phối hợp luồng tải tuần/tháng bất đồng bộ có debounce giữa `CalendarView` và `ScheduleWeekView`.

2. **Phương án 2: Giữ 3 tab trên điện thoại, đổi tab `Lịch học` mobile sang Agenda cuộn dọc cả tuần**
   - *Nhược điểm*: Phải chuyển qua lại giữa 2 tab khi muốn đối chiếu ngày vừa đi học vừa có hạn nộp bài tập.

3. **Phương án 3: Lưới Thời khóa biểu thu gọn theo Ca (`Sáng / Chiều / Tối` x `T2..CN`) nửa trên + Chi tiết ngày nửa dưới**
   - *Nhược điểm*: Ô lưới quá hẹp trên màn hình dọc điện thoại, không đọc được tên môn trực tiếp và chiếm nửa màn hình trên.

---

## 4. Chi tiết Thiết kế Đã chốt (Decided Design)

### 4.1. Giao diện kết nối dữ liệu giữa `CalendarView` và `ScheduleWeekView`
`CalendarView` tái sử dụng trực tiếp thực thể `self._schedule_view` (`ScheduleWeekView`) làm nguồn dữ liệu thời khóa biểu duy nhất (tránh nhân đôi `WeekScheduleCache` và luồng tải Portal):
- Bổ sung trên `ScheduleWeekView`:
  - `on_weeks_changed: Callable[[], None] | None`: được gọi khi `_hydrate()`, `_load_async()`, hoặc `reset()` làm thay đổi dữ liệu/trạng thái tuần, để `CalendarView` (khi đang hiển thị `month` hoặc `week`) cập nhật lại chỉ báo ca học và danh sách bên dưới.
  - `hydrate_and_ensure_weeks(mondays: Sequence[date], *, start_prefetch: bool = False, debounce_ms: int = 0) -> None`: nạp ngay `WeekScheduleCache` (`±4` tuần quanh hôm nay, tức thì 0ms không cần mạng), sau đó tải tuần tự các tuần còn thiếu/quá TTL với cơ chế hủy/debounce (`250ms` khi lướt tháng) để không gây bão request khi vuốt nhanh qua các tháng xa.
  - `reload_week(monday: date) -> None`: làm mới cưỡng bức tuần đang xem khi người dùng bấm nút tải lại.
  - `sessions_for_day(day: date) -> tuple[ClassSession, ...]` và `sessions_for_week(monday: date) -> dict[date, tuple[ClassSession, ...]]`.
  - `week_status(monday: date) -> tuple[bool, str]`: trả về `(is_loading_spinner, error_message)` của tuần đó.
  - `open_session_details(session: ClassSession, *, return_focus: ft.Control | None = None) -> None`: mở hộp thoại `Chi tiết buổi học` chuẩn (kèm đề cương, Zoom, LMS và kiểm tra URL an toàn).

### 4.2. Thanh chuyển chế độ thích ứng (2 tab trên Mobile, 3 tab trên PC)
- Định nghĩa `_COMPACT_SHELL_BREAKPOINT = 600`.
- `_should_hide_separate_schedule_tab()` trả về `True` khi `platform_utils.UI_IS_MOBILE` là `True` hoặc chiều rộng trang hiện tại (`0 < width < 600`).
- Khi `_should_hide_separate_schedule_tab()` là `True`:
  - `self._mode_schedule_btn.visible = False` (chỉ hiện `Tháng` và `Tuần`).
  - Khi cửa sổ PC thu hẹp từ `>= 600` xuống `< 600` lúc đang ở chế độ `"schedule"`, tự động chuyển sang `"week"` và đồng bộ `_selected_date = self._schedule_view._selected` để người dùng tiếp tục xem đúng tuần/ngày đó mà không bị kẹt ở tab đã ẩn. (Gọi lập trình trực tiếp `_toggle_view_mode("schedule")` trong các bài test đơn vị hiện có vẫn giữ tương thích).
- Khi `not _should_hide_separate_schedule_tab()` (PC `>= 600px`):
  - `self._mode_schedule_btn.visible = True`, giữ đầy đủ `Tháng | Tuần | Lịch học`.

### 4.3. Chỉ báo 3 Ca học trên Dải `Tuần` và Lưới `Tháng` (trên cả Mobile và PC)
- Phân loại buổi học theo giờ bắt đầu `session.start_at`:
  - `Sáng`: `< 12:00`
  - `Chiều`: `12:00` đến `< 17:30`
  - `Tối`: `>= 17:30`
- **Trên ô ngày của dải `Tuần` (`_make_week_cell`, cao `80px` trong khung `88px`)**:
  - Giữ nguyên các khóa `wd_label`, `day_text`, `dots_row`, `count_text` để tương thích tuyệt đối với mọi bài test hiện hữu.
  - Chèn thêm `shift_bars` (3 thanh ngang mini `width=16, height=1.8` xếp dọc cách nhau `1px`, tổng cao ~`7.5px`) ngay dưới `day_num`: buổi có ca học bình thường tô `C.ACCENT`; buổi chỉ có ca `cancelled` tô `C.CRITICAL` mờ (`0.45`); buổi trống tô `C.BORDER` mờ (`0.25`).
  - Dòng phụ đề tiêu đề tuần (`_month_summary`): hiển thị tổng hợp cả ca học và bài tập của tuần đó (ví dụ `"6 ca học · 2 bài tập · 1 đã nộp"`, hoặc giữ câu rõ nghĩa khi chỉ có 1 loại / trống cả hai).
- **Trên ô ngày của lưới `Tháng` (`_make_day_cell`, `44x48px`)**:
  - Khi ngày đó có ít nhất 1 ca học đã biết trong bộ nhớ tuần, gắn thêm 1 vạch mảnh màu `C.ACCENT` (`width=10, height=2.5, border_radius=1.5`) cạnh chấm hạn bài tập (hoặc đứng riêng nếu ngày đó không có hạn bài tập) mà không làm đổi cấu trúc badge đếm `3+` bài tập khi kiểm thử riêng dữ liệu bài tập.

### 4.4. Hàng Chip lọc nhanh và Dòng Timeline Ca học gọn trong `_detail_panel` (Mobile và PC)
- Đặt ngay đầu `_detail_panel` (áp dụng cho cả `Tháng` và `Tuần` trên Mobile lẫn PC):
  - Hàng 3 chip lọc (`Theo ngày`, `Cả tuần ({n} ca)`, `Bài tập ({m})`) + nút icon `REFRESH_ROUNDED` (`Tải lại lịch học`, vùng chạm chuẩn) ở mép phải.
  - Dòng trạng thái Portal gọn (chỉ hiện khi tuần đang tải lần đầu chưa có cache hoặc khi có lỗi xác thực/mạng kèm nút `Thử lại`).
- **Dòng Timeline Ca học gọn (`_make_compact_session_row(session)`)**:
  - Cao ~`48-52px` (`min_height = 46`), chạm toàn dòng để mở `open_session_details(session)`:
    - Cột trái (`width = 44`): giờ bắt đầu (`11px W_700 C.ACCENT`) ở trên, giờ kết thúc (`10px C.TEXT_SECONDARY`) ở dưới.
    - Thanh dọc nhấn (`width = 3, height = 32, border_radius = 2`): `C.CRITICAL` nếu `cancelled`, ngược lại `C.ACCENT`.
    - Cột giữa (`expand = True`): dòng 1 là tên môn (`12.5px W_600`, `max_lines = 1`, `ELLIPSIS`); dòng 2 là huy hiệu phòng/cơ sở rút gọn (ví dụ rút `"F502 - CS1 (P.Thạnh Mỹ Tây, TP.HCM)"` thành `"F502 · CS1"`, tô xanh lá/nhấn nhẹ nếu `"E-Learning"` / trực tuyến, kèm nhãn `"Tạm ngưng"` nếu `cancelled`).
    - Cột phải: icon `CHEVRON_RIGHT_ROUNDED`.

### 4.5. Các quyết định chi tiết do Agent đề xuất (Engineering Calls sau bước Builder Check)
Để tránh mọi điểm mờ khi lập kế hoạch, 3 tình huống biên được chốt như sau (người dùng có thể góp ý sửa nếu muốn khác):
1. **Tải lịch học khi xem chế độ `Tháng` và khi `_selected_day` đang `None`**:
   - Khi mở hoặc chuyển tháng, `CalendarView` tính danh sách 4-6 ngày Thứ Hai giao với tháng đó: nạp ngay tức thì từ `WeekScheduleCache` cho các tuần trong bán kính `±4` tuần, và xếp hàng tải nền có **debounce `250ms`** cho các tuần chưa có để lướt nhanh nhiều tháng không bắn request thừa.
   - Khi vừa vuốt sang tháng mới (`_selected_day is None`): chip `Theo ngày` và `Bài tập` vẫn giữ trạng thái `"Chọn một ngày"` chuẩn hiện tại; riêng nếu người dùng đang bật chip `Cả tuần`, danh sách neo về tuần đầu tiên của tháng đó (hoặc tuần chứa `Hôm nay` nếu là tháng hiện tại).
2. **Giữ mạch ngày khi chuyển tuần ở chế độ `Tuần`**:
   - Khi bấm `< >` hoặc vuốt ngang sang tuần kế tiếp/trước đó ở chế độ `Tuần`, `_selected_date` tự động tịnh tiến theo đúng thứ đang chọn (`+7` hoặc `-7` ngày; nếu trước đó là `None` thì chọn Thứ Hai của tuần mới, hoặc `Hôm nay` nếu rơi vào tuần hiện tại). Nhờ vậy khi đang ở chip mặc định `Theo ngày`, vừa vuốt sang tuần sau là thấy ngay lịch của ngày tương ứng mà không bị màn hình trống `"Chọn một ngày"`.
3. **Hành vi khi bấm vào ô ngày lúc đang bật chip `Cả tuần` hoặc `Bài tập`**:
   - Ở chế độ **`Tuần`**: cả `Cả tuần` và `Bài tập` đều đang hiển thị phạm vi toàn tuần, nên khi người dùng chủ động chạm vào một ô thứ cụ thể (`T2..CN`), chip lọc tự chuyển về **`Theo ngày`** của ngày vừa chạm để phản hồi tức thì.
   - Ở chế độ **`Tháng`**: nếu đang bật chip **`Cả tuần`**, chạm vào một ngày ở hàng tuần nào trong tháng sẽ giữ nguyên chip `Cả tuần` và cập nhật danh sách bên dưới sang trọn tuần chứa ngày vừa chạm (giúp rà nhanh từng tuần trong tháng chỉ bằng 1 chạm).

### 4.6. Sửa 2 lỗi giao diện trên PC
1. **Đồng bộ chiều cao thẻ lịch học trên bảng 7 cột PC (`ScheduleWeekView._session_summary_card`, `narrow=False`)**:
   - Đặt `title.max_lines = 2`, `class_label.max_lines = 1`, `room_label.max_lines = 2`, đưa nhãn `"Tạm ngưng"` (nếu có) lên cùng hàng ngang với khung giờ, cố định `height = 172` cho `Container` của thẻ ở chế độ `narrow=False` và dùng `ft.Column([summary, details_button], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, spacing=4)` để mọi thẻ trên các cột ngày cao bằng nhau tuyệt đối.
2. **Xóa lỗi kẹt sáng kép nút `Tuần` và `Lịch học` (`CalendarView._toggle_view_mode`)**:
   - Đưa `self._style_mode_buttons()` lên **trước** lệnh `_render_month()` / `_render_week()` trong `_toggle_view_mode`, bỏ đoạn gán màu trùng lặp chỉ xử lý 2 nút `month`/`week` khiến nút `schedule` chưa kịp trả về `"transparent"` trước lúc `page.update()` chạy.

---

## 5. Phần để ngỏ cho Bước Lập kế hoạch / Triển khai (Left to the Builder)

- Quy tắc cắt chuỗi ngoặc đơn địa chỉ cơ sở trong hàm rút gọn tên phòng cho dòng Timeline gọn (trong khi hộp thoại `Chi tiết` giữ nguyên văn 100%).
- Tách hàm/module helper nhỏ nếu cần để giữ `calendar_view.py` và `schedule_week_view.py` sạch, dễ đọc và kiểm thử đơn vị nhanh.
