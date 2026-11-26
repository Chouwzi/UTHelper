# Môn học (Courses & THNN) V2 — Thiết kế Kiến trúc, Đọc Tài liệu, WebView Phiên & Pane-3 UX

**Nhánh làm việc:** `feature/courses-tab` (`e:\Projects\UTH-Elearning-Alert\.worktrees\courses-tab`)  
**Ngày hoàn thiện V2:** 2026-11-17

---

## 1. Mục tiêu & Nguyên tắc Trải nghiệm V2

1. **Mặc định mở mọi nội dung trong Pane 3 (Cột nội dung) & Nút Mở rộng `3 <-> 2+3`**:
   - Bố cục Wide Desktop gồm 3 cột ổn định: **Pane 1 (`NavigationRail`) | Pane 2 (`CourseListPane`) | Pane 3 (`Workspace Stack`)**.
   - Tất cả overlay / reader / hoạt động (PDF, Word, Excel, PPTX, Archive, Điểm danh, Nhóm, Quiz, Diễn đàn, Bài tập, WebView) đều được mount vào `CoursesTabView._workspace_stack` bên trong Pane 3, giữ nguyên Pane 1 & Pane 2 hiển thị bên trái theo mặc định.
   - Mọi header của Pane 3 (`CourseDetailPane`, `DocumentReaderView`, `ArchiveExplorerView`, `CoursesWebViewPane`, `AttendanceActivityView`, `GroupActivityView`, `QuizActivityView`, `ForumActivityView`, `RichContentActivityView`, `PaneAssignmentView`) đều có nút **Mở rộng / Thu gọn (`2+3 <-> 3`)** và phím tắt `Ctrl+\` / `F11` (`Esc` để thu về hoặc đóng overlay).
   - Hoạt ảnh Flet (`ft.Animation(220, EASE_OUT_CUBIC)`) thu/mở `width` (`320 <-> 0`) và `opacity` trên container cố định `_list_pane_host` mà không dựng lại cây control.

2. **Đọc tài liệu gốc ngay trong ứng dụng (Zero external handoff cho tài liệu phổ thông)**:
   - **PDF (`src/core/courses/pdf_renderer.py`)**: `PdfDocumentSession` dùng `pypdfium2` + `PIL` (fallback text thuần `pypdf`), nạp theo từng cụm 5 trang kèm luồng nền pre-hydrate, đảo sáng YCbCr bảo toàn màu cho Night Reader, nhảy trang, mục lục (`outline`), tìm kiếm văn bản trong PDF và cache PNG LRU tối đa 96 MiB tại `preview_cache/rendered_pdf/` (dọn sạch qua `SettingsView`).
   - **Word (`.docx`, `.doc`)**: Rich OOXML với `DocxRichBlock` (tiêu đề, đoạn văn, danh sách, bảng `DataTable`, ảnh nhúng `word/media/*`) + giải mã Word 97–2003 nhị phân OLE2 (`WordDocument` FIB/Clx `PlcPcd`).
   - **Excel (`.xlsx`, `.xls`, `.csv`, `.tsv`)**: Phân trang `120` dòng × `40` cột mỗi trang + chuyển trang dòng/cột + ô lọc nhanh theo từ khóa + `xlrd` cho bảng tính `.xls` cũ.
   - **PowerPoint (`.pptx`, `.ppt`)**: Slide kèm hình ảnh/bảng DrawingML (`PptxSlideRich`) + speaker notes + giải mã `.ppt` OLE2 (`SlideContainer` `0x03EE` / `TextCharsAtom` `0x0FA0`).
   - **Tệp nén (`.zip`, `.tar.*`, `.rar`, `.7z`)**: Duyệt cây & giải nén `.7z` bổ sung qua `7z`/`7za` (`find_windows_7z_cli`), cho phép mở lồng tệp nén bên trong tệp nén (`archive` lồng nhau) trực tiếp qua `ArchiveExplorerView`.

3. **Hoạt động học tập chuyên sâu & WebView xác thực phiên ngay trong app**:
   - **Tương tác gốc (`src/core/courses/activity_service.py`, `src/gui/components/courses/activity_views.py`)**:
     - `mod_attendance`: Thống kê chuyên cần, lịch sử buổi học và form tự điểm danh (radio trạng thái + mật khẩu nếu có).
     - `mod_groupselect`, `mod_choicegroup`, `mod_choice`: Hiển thị nhóm hiện tại, lọc danh sách nhóm & thành viên, tham gia/rời nhóm hoặc gửi lựa chọn trực tiếp.
     - `mod_quiz`: Metadata giờ mở/đóng/thời lượng, quy tắc truy cập, lịch sử & xem lại bài làm (`mod_quiz_get_attempt_review`) và nút làm bài ngay trong WebView nội bộ.
     - `mod_forum`, `mod_book`, `mod_page`: Đọc chủ đề thảo luận/bài viết, mục lục chương và nội dung làm sạch XSS.
     - `mod_assign`: Bọc `DetailView` trong `PaneAssignmentView` mở ngay trong Pane 3.
   - **Live THNN Fixes (`src/core/courses/providers.py`)**: Giải mã `core_courseformat_get_state` khi `res["data"]` trả về chuỗi JSON, cào bảng điểm `/grade/report/user/index.php` (`table.user-grade`) và giải quyết `pluginfile.php` từ trang HTML `resource`/`folder`.
   - **WebView xác thực phiên (`src/core/courses/local_preview_server.py`, `src/gui/components/courses/webview_pane.py`)**:
     - Desktop Windows: `WindowsWebView2Dock` trên luồng STA riêng (cửa sổ popup sở hữu bởi Flutter HWND, căn tọa độ DPI theo Pane 3, tự ẩn khi chuyển tab) tiêm cookie phiên qua `CoreWebView2CookieManager`.
     - Flet Web / Mobile / macOS: `flet_webview.WebView` trỏ qua loopback proxy `/session/{bridge_token}/{source}/...` hỗ trợ GET/HEAD/POST, bỏ `X-Frame-Options` và viết lại link/form cùng origin dưới kiểm soát chống SSRF.
     - Trình duyệt ngoài chỉ dùng cho link phòng họp (`meet.google.com`, `zoom.us`, `teams.microsoft.com`, `zalo.me`) hoặc khi người dùng bấm nút mở ngoài chủ động.
