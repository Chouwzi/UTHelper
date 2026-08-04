# Cơ sở tri thức Tái cấu trúc (Refactoring Knowledge Base)

Tài liệu này ghi lại các phân tích cấu trúc, quyết định kỹ thuật và tiến độ thực tế trong quá trình tái cấu trúc codebase `UTHelper` theo các nguyên lý Clean Code và SOLID.

---

## 1. Phân tích Chi tiết Giao diện & Kiến trúc Cũ

### A. SettingsView (`src/gui/components/settings_view.py`)
*   **Kích thước:** 2551 dòng.
*   **Vấn đề chính:**
    *   Hàm lồng phức tạp `open_color_picker` nằm ngay trong constructor `__init__` chiếm khoảng 80 dòng, chứa các hàm con xử lý cập nhật RGB sliders và Hex text field.
    *   Toàn bộ cấu trúc UI của các tab (Cài đặt tài khoản, Ứng dụng, Thông báo Telegram/Discord/Email, Cài đặt Theme) đều viết trực tiếp trong constructor khổng lồ.
*   **Giải pháp tái cấu trúc:**
    1.  Trích xuất logic Color Picker sang một file mới `src/gui/components/color_picker.py` chứa class `ColorPicker`.
    2.  Tách các khối giao diện theo tab ra các phương thức trợ giúp trong class `SettingsView` (ví dụ: `_build_account_settings()`, `_build_app_settings()`, `_build_notification_settings()`, `_build_theme_settings()`).
    3.  Rút gọn constructor `__init__` xuống dưới 100 dòng.

### B. DetailView (`src/gui/components/detail_view.py`)
*   **Kích thước:** 1675 dòng.
*   **Vấn đề chính:**
    *   Hàm `_do_remove_file_sync` tự thực hiện tải xuống các file muốn giữ và upload lại. Logic này nên thuộc về một Service/Controller, không nên đặt trong View.
    *   Rất nhiều dialog con (AlertDialog xác nhận xóa, chỉnh sửa thông tin) và SnackBar được khởi tạo phân tán.
*   **Giải pháp tái cấu trúc:**
    1.  Tách logic mạng và API Moodle sang `src/core/moodle_service.py`.
    2.  Phân tách giao diện bảng danh sách file và khu vực hiển thị trạng thái điểm số ra các phương thức riêng.

### C. AppController (`src/gui/app_controller.py`)
*   **Kích thước:** 1860 dòng.
*   **Vấn đề chính:**
    *   Quản lý các vòng lặp đếm ngược (`_countdown_loop_async`) và đồng bộ dữ liệu tự động (`_auto_refresh_loop_async`) ngay tại luồng xử lý chính.
*   **Giải pháp tái cấu trúc:**
    1.  Đóng gói các tác vụ nền vào một bộ điều phối độc lập hoặc tách nhỏ các hàm quản lý vòng lặp.

---

## 2. Nhật ký Tiến độ Tái cấu trúc

| Ngày | Nhánh | Giai đoạn | Công việc đã hoàn tất | Trạng thái test |
| :--- | :--- | :--- | :--- | :--- |
| 2026-07-01 | `feature/refactor-clean` | Khởi động | Thiết lập AGENTS.md, tạo nhánh mới, tạo knowledge base | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 1 | Trích xuất `ColorPicker` và phân rã constructor của `SettingsView` thành các hàm trợ giúp | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 2 | Tạo lớp dịch vụ `MoodleService` để gom nhóm các API; phân rã constructor của `DetailView` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 3 | Phân rã phương thức `_init_ui` khổng lồ của `AppController` thành các hàm trợ giúp nhỏ gọn, tự mô tả | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 4 | Phân chia các Tab cài đặt của `SettingsView` thành các file độc lập trong thư mục `settings/` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 5 | Trích xuất `theme_presets.py` và triển khai `ViewManager` để giảm ghép cặp cho `AppController` | Đạt 296/296 |
| 2026-07-01 | `feature/refactor-clean` | Giai đoạn 6 | Trích xuất bảng danh sách file đã nộp (`SubmittedFilesTable`) của `DetailView` thành component riêng | Đạt 296/296 |
| 2026-07-01 | workspace hiện tại | Audit senior | Nghiên cứu Clean Code/SOLID/OOP/Architecture, tạo `docs/CODEBASE_ARCHITECTURE_REVIEW_PLAN.md`, cập nhật `.agents/AGENTS.md` làm rule nguồn trong repo | Test đạt 296 passed, 22 skipped; `ruff check src` còn 13 lỗi |
| 2026-07-01 | workspace hiện tại | Phase 0 | Sửa Ruff baseline, cập nhật README test count, tạo ADR boundary, thêm `tests/test_architecture_boundaries.py` để khóa dependency debt hiện hữu | Đạt 300 passed, 22 skipped; `ruff check src tests` pass |
| 2026-07-01 | workspace hiện tại | Phase 1 một phần | Trích `SubmissionWorkflow` khỏi `DetailView`, thêm tests service, siết architecture allowlist để xóa nợ `DetailView -> core.ws_functions` | Đạt 305 passed, 22 skipped; `ruff check src tests` pass |
| 2026-07-01 | workspace hiện tại | Phase 2/3 một phần | Thêm `GradeRefreshService`, mở rộng `MoodleService`, chuyển `AppController` khỏi import trực tiếp `ws_functions`, thêm tests service | Đạt 311 passed, 22 skipped; `ruff check src tests` pass |
| 2026-07-01 | workspace hiện tại | Phase 1 hoàn tất | Bổ sung DTO cho `SubmissionWorkflow`, inject workflow factory từ `AppController`, bỏ fallback tự tạo implementation trong `DetailView` | Đạt 311 passed, 22 skipped; `ruff check src tests` pass |
| 2026-07-01 | workspace hiện tại | Phase 2/3 tiếp tục | Tách `RefreshCoordinator` khỏi `AppController`; chuyển một phần đọc Moodle trong `DataOrchestrator` qua `MoodleService`; thêm tests coordinator và boundary service | Đạt 317 passed, 22 skipped; `ruff check src tests` pass |
| 2026-07-01 | workspace hiện tại | Phase 2/3 hoàn tất | Hoàn thiện refresh coordinator cho dataset/status/post-refresh scheduling; chuyển `DataOrchestrator`, `GradeMonitor`, `SubmissionWorkflow` qua `MoodleService`; khóa architecture boundary raw WS | Đạt 322 passed, 22 skipped; `ruff check src tests` pass |

---

## 3. Baseline Kiến trúc Sau Audit 2026-07-01

### Kết quả kiểm chứng

*   `python -m pytest tests -q --tb=short`: **296 passed, 22 skipped, 5 warnings**.
*   `ruff check src`: **13 lỗi**, chủ yếu unused imports và unused local variable.
*   `src`: 59 file Python, 14.621 dòng.
*   `tests`: 29 file Python, 3.930 dòng.

### Kết quả sau Phase 0

*   `python -m pytest tests -q --tb=short`: **300 passed, 22 skipped, 5 warnings**.
*   `ruff check src tests`: **pass**.
*   Đã tạo `docs/adr/0001-refactoring-boundaries.md`.
*   Đã tạo `tests/test_architecture_boundaries.py` để ngăn dependency xấu mới trong khi xử lý dần nợ hiện hữu.

### Kết quả sau Phase 1

*   `python -m pytest tests -q --tb=short`: **311 passed, 22 skipped, 5 warnings**.
*   `ruff check src tests`: **pass**.
*   Đã tạo `src/core/use_cases/submission_workflow.py` và `tests/test_submission_workflow.py`.
*   `SubmissionWorkflow` đã có DTO và được inject từ `AppController`.
*   `DetailView` không còn import trực tiếp `core.ws_functions` hoặc tự tạo implementation workflow; test kiến trúc đã cập nhật allowlist tương ứng.

### Kết quả sau Phase 2/3 hoàn tất

*   `python -m pytest tests -q --tb=short`: **322 passed, 22 skipped, 5 warnings**.
*   `ruff check src tests`: **pass**.
*   Đã tạo `src/core/use_cases/grade_refresh.py`, `tests/test_grade_refresh_service.py`, `tests/test_moodle_service.py`.
*   Đã tạo `src/gui/controllers/refresh_coordinator.py`, `tests/test_refresh_coordinator.py`.
*   Đã tạo `tests/test_data_orchestrator_service_boundary.py` để khóa hướng phụ thuộc mới của `DataOrchestrator`.
*   Đã tạo `tests/test_grade_monitor_service_boundary.py` và cập nhật `tests/test_architecture_boundaries.py` để khóa rule: chỉ `core.moodle_service` được import adapter `core.ws_functions`.
*   `AppController` không còn import `from core import ws_functions`; các phần grade/unread badge/cache clearing đã đi qua `MoodleService`/`GradeRefreshService`.
*   `AppController._load_data_async` còn 86 dòng và không còn tự chứa thuật toán merge cache chi tiết, smart merge trạng thái nộp bài, sort urgency/deadline, precompute hot fields, progress status, notifier dispatch, post-refresh scheduling.
*   `DataOrchestrator`, `GradeMonitor`, `SubmissionWorkflow` đã đi qua `MoodleService` cho các thao tác Moodle thay vì import/call raw `ws_functions`.
*   Architecture allowlist không còn nợ `gui -> core.ws_functions/core package import`.

### Phát hiện chính

1.  `AppController`, `SettingsView`, `DetailView` vẫn là các điểm tập trung trách nhiệm lớn nhất.
2.  `DetailView` còn trực tiếp xử lý workflow Moodle submit/upload/re-upload/delete metadata; nên trích sang use case/service.
3.  `MoodleService` hiện là boundary chính cho Moodle WS; `ws_functions` được giữ như adapter thấp tầng phía sau service.
4.  `models.py` còn phụ thuộc `config.settings` để tính urgency; nên chuyển sang policy inject được.
5.  `.agents/AGENTS.md` cũ trỏ tới đường dẫn ngoài repo; đã cập nhật để dùng `docs/CODEBASE_ARCHITECTURE_REVIEW_PLAN.md` làm nguồn sự thật.

### Ưu tiên refactor tiếp theo

1.  Phase 4: cleanup `SettingsView` và debug panel.
2.  Phase 5: tách `DataOrchestrator._merge_all_assignments` thành mapper/policy test được.
3.  Phase 6: tách `AppState`, `UpdateController` và background task lifecycle còn lại khỏi `AppController`.
# 2026-07-19 - Activity notification core and Android scheduling

- Added pure notification DTO/policy contracts for Moodle activities, stable
  notification IDs, milestones, DND, submission filtering, and desired native
  schedules.
- Converted `NotificationManager.dispatch` and grade dispatch to async typed
  results; synchronous Windows/integration channels now run off the Flet event
  loop.
- Fixed `flet-android-notifications` calls to use awaited APIs and
  `notification_id`; pinned the Android extension to 0.9.0.
- Added Android schedule reconciliation with persisted state, deadline-change
  rescheduling, and removal cancellation.
- Removed the Moodle unread badge call from the activity refresh path and made
  background refresh independent of the visible dashboard.
- Validation: `ruff check src tests` passed; 51 targeted tests passed.

# 2026-07-19 - Signed release updater and truthful background capability

- Replaced extension-based GitHub asset selection with typed, platform-aware
  release metadata and semantic version comparison.
- Added atomic size/SHA-256 verification for update downloads and made
  `pyproject.toml` the runtime version source.
- Added a tag-only release pipeline for signed APK/MSIX artifacts, a generated
  release manifest, and a stable Windows AppInstaller document on GitHub Pages.
- Removed the ZIP/batch Windows updater and Python/pyjnius Android installer.
- Removed the Moodle unread-notification badge and the Android periodic
  notification that claimed to poll deadlines without fetching activity data.
- Added ADR 0003 for the signing/update boundary and the explicit WorkManager
  capability gap.
- Validation: `ruff check src tests` passed; full suite reached **338 passed,
  22 skipped**.

# 2026-07-19 - Native notification delivery hardening

- Immediate Android notifications now use the same stable
  `activity + milestone` identifier as scheduled reminders and carry the
  activity URL as their native tap payload.
- Android notification taps and single-activity Windows toast clicks open the
  exact Moodle activity URL.
- Notification milestones are cached only when a notifier explicitly returns
  a successful delivery confirmation; log-only or partial-failure mobile runs
  no longer advance the cache.

# 2026-07-19 - Windows reminder scheduler and release artifact gates

- Added a durable tray/autostart-owned Windows reminder scheduler so known
  milestones can fire without waiting for the next Moodle network poll.
- Windows reconciliation now persists pending reminders, reschedules changed
  deadlines, cancels removed activities, retries failed toast delivery, and
  exposes pending/delivery/error diagnostics.
- Release CI now rejects ambiguous APK outputs, verifies the final packaged
  Android manifest/version/signing certificate, validates MSIX identity and
  AppInstaller metadata, pins signing certificate fingerprints, and requires
  RFC3161 timestamped Windows signatures.
- Targeted validation: 10 scheduler/release/diagnostics tests passed; Ruff and
  workflow YAML parsing passed. A pre-existing milestone expectation in the
  wider manager test is being migrated concurrently from hours to minutes.

# 2026-08-03 - Stable Flet Windows startup and truthful autostart

- Root-caused the immediate packaged-app crash to recursive deletion of Flet
  0.86 compiled `.pyc` runtime files, including `Lib/encodings`; removed the
  unsafe cleanup and added a fail-closed bundle verifier.
- Pinned the matching Flet core/CLI/desktop toolchain to 0.86.5 and require the
  Python runtime, encodings, compiled app, Flutter DLL, WinRT ApplicationModel
  projection, and byte-identical autostart runner before packaging.
- Flet 0.86.5 does not start embedded Python when its production runner receives
  desktop CLI arguments. Packaged/Inno/MSIX autostart therefore targets
  `UTHelperAutostart.exe` without arguments; only source development retains
  `pythonw main.py --autostart`.
- Windows autostart now reads back real HKCU Run or MSIX StartupTask state and
  reports user/policy rejection instead of persisting false success. The Settings
  UI reconciles OS state and scopes the hidden-window preference to autostart.
- Hidden startup is allowed only after the tray reports readiness; tray failure
  keeps the main window visible, preventing an invisible orphan process.
- Inno installation is per-user, cleanup is limited to the current and legacy
  Run value names, and the installed-bundle E2E uses bounded install/uninstall
  processes with exact PID cleanup.
- Local artifact gates passed for a clean Flet bundle, direct and installed
  manual/visible-autostart/hidden-autostart probes, Inno compile/install/uninstall,
  and Windows SDK MSIX pack/unpack validation.

# 2026-08-04 - Verified Moodle submission file state machine

- Replaced Boolean submission mutations with fresh typed snapshots, optimistic
  fingerprint checks, exact desired-set rebuilds, structured outcomes, and
  authoritative post-save/finalization verification.
- Added assignment permission/constraint gates, bounded file materialization,
  retained-file size checks, tracked pre-save draft cleanup, online-text
  preservation, and explicit draft/final statement transitions.
- Kept the existing submission GUI adapters until the server-driven UI migration.
- Validation: 112 targeted tests passed; full suite reached **488 passed,
  22 skipped**; `ruff check src tests` passed.

# 2026-08-04 - Stateful Moodle 4.3 submission protocol coverage

- Added an in-process Moodle 4.3 fake covering assignment/status reads, draft
  allocation and uploads, exact-set saves, tracked draft cleanup, downloads,
  and statement-aware finalization without network access or credentials.
- Exercised the public `SubmissionWorkflow` across first submissions, append,
  replace, remove/clear, rename/path moves, duplicate rejection, stale snapshot
  aborts, and draft-to-submitted transitions.
- Validation: 141 submission-focused tests passed; full suite reached **531
  passed, 22 skipped**; `ruff check src tests` passed.

# 2026-08-04 - Live-safe Moodle submission verification boundary

- `SubmissionWorkflow` owns assignment-file state transitions. Callers provide an
  immutable `SubmissionSnapshot` fingerprint plus a `FileMutationIntent`; the
  workflow reloads server state, builds the exact desired set, saves it, and
  returns only freshly verified server truth.
- Draft transport remains below that state machine. An allocated, unlinked draft
  item may be tested independently with uniquely synthetic upload/list/delete
  operations, but it must never call assignment save or finalization.
- The opt-in production probe may save and clear one generated file only on a
  freshly `new`, empty, editable, unlocked, ungraded, file-enabled draft
  assignment at least seven days before its due/cutoff boundary. Cleanup repeats
  only the same exact clear after another safe-state check. Moodle cannot remove
  the resulting empty submission record, so that residual is reported explicitly.
- Normal test runs skip live probes. Operators must use existing environment or
  secure app authentication and the documented external 180-second timeout; no
  credentials, tokens, authenticated URLs, assignment identities, or file content
  belong in test output or committed artifacts.
- Live authentication is isolated from application state. A paired
  `UTH_TEST_USER`/`UTH_TEST_PASS` login always takes precedence over cached app
  tokens and keeps the acquired token in memory only. Without that pair, the
  harness accepts only the token read directly from secure keyring and verifies
  its site-info username against the configured account identity; plaintext JSON
  fallback is never eligible for an assignment mutation.
- The snapshot fingerprint now covers online-text content by hash, draft/file
  plugin modes and limits, team mode, opening/due/cutoff boundaries, permissions,
  status, and remote identities. A caller-supplied workflow safety guard evaluates
  that freshly reloaded snapshot before any download, draft allocation, upload, or
  save, closing the live precheck-to-mutation drift window.

# 2026-08-04 - Submission provenance final-review hardening

- Empty secure-setting values now delete their keyring entries idempotently, so
  clearing a Moodle token/origin during an account, credential, site, or logout
  transition cannot be undone by the next process restart.
- The opt-in live submission harness resolves the configured Moodle base through
  the trusted-site allow-list, requires the secure token's stored issuer origin to
  match it before any request, and binds the isolated client to that verified
  origin for every subsequent call.
- Detail submission snapshot loads reserve both load and view generations at the
  synchronous scheduling boundary. An older queued coroutine therefore cannot
  start after a newer request and adopt the newer generation while carrying stale
  prefetched status.
- Validation: 90 focused tests passed with 2 opt-in live tests skipped; the full
  suite passed with **732 passed, 24 skipped**; `ruff check src tests` passed.

# 2026-08-04 - Deterministic Settings and explicit diagnostics consent

- Settings UI state now crosses one immutable `SettingsFormSnapshot` boundary in
  both directions. Dirty state compares the current normalized snapshot only to
  the last successfully loaded or saved baseline, so merely opening and closing
  Settings does not produce a save prompt.
- Automatic update checks are represented by an explicit cross-platform switch
  whose default is enabled. Its wording promises checking only; installing and
  restarting still require confirmation in the update coordinator.
- Crash-reporting consent is a three-state value (`not_asked`, `enabled`, or
  `disabled`). The first-run dialog performs no diagnostics/network work, offers
  a visible `Để sau` deferral, and considers a choice decided only after
  `save_settings()` succeeds.
- Settings persistence snapshots secure values, applies keyring mutations, and
  commits JSON exactly once. A secret or JSON failure compensates all prior
  keyring mutations before reporting failure, while the UI restores the complete
  in-memory snapshot and Moodle provenance.
- Windows autostart reconciliation rebases only its OS-owned snapshot field, so
  opening Settings cannot create a false dirty prompt or mask concurrent user
  edits. If a later settings commit fails, the OS autostart change is compensated
  back to its previously confirmed state.
- Validation: 88 Settings/autostart-focused tests passed; the full suite passed
  with **810 passed, 24 skipped**; `ruff check src tests` passed.

# 2026-08-04 - Transactional Settings initialization

- Opening Settings now awaits one generation-owned initialization transaction
  before changing view visibility. Persisted settings are snapshotted first;
  the Windows autostart read is bounded to 2.0 seconds; controls and the clean
  baseline are committed together only if that generation is still current.
- A confirmed Windows state overrides only the form snapshot's autostart field.
  An unconfirmed or timed-out read preserves the persisted value, disables the
  control, and presents a retry warning without mutating global settings or
  making the form dirty.
- Closing Settings or disconnecting invalidates the active load immediately.
  A late coroutine cannot mutate controls, status, baseline, or visibility, and
  ViewManager suppresses a stale pending show after close.
- Validation: 22 task-focused tests and 105 Settings/config/activation regression
  tests passed; focused Ruff and `git diff --check` passed.
