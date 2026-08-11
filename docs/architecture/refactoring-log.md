# Cơ sở tri thức Tái cấu trúc (Refactoring Knowledge Base)

Trạng thái: nhật ký kiến trúc lịch sử. Đây là nguồn tra cứu quyết định và bằng
chứng đã thực hiện, không phải danh sách công việc đang chờ xử lý.

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
| 2026-07-01 | workspace hiện tại | Audit senior | Nghiên cứu Clean Code/SOLID/OOP/Architecture, tạo `docs/architecture/refactoring-plan.md`, cập nhật `.agents/AGENTS.md` làm rule nguồn trong repo | Test đạt 296 passed, 22 skipped; `ruff check src` còn 13 lỗi |
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
5.  `.agents/AGENTS.md` cũ trỏ tới đường dẫn ngoài repo; đã cập nhật để dùng `docs/architecture/refactoring-plan.md` làm nguồn sự thật.

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
  ViewManager suppresses a stale pending show after either close or disconnect.
- Validation: 22 task-focused tests and 105 Settings/config/activation regression
  tests passed; focused Ruff and `git diff --check` passed.

# 2026-08-04 - Verified Settings save and discard baseline

- Settings save now captures one normalized draft and bounds a requested Windows
  autostart mutation to 2.0 seconds. A rejected or timed-out mutation restores
  only the autostart field to the confirmed/baseline state; every unrelated valid
  field is still persisted and becomes the clean visible baseline.
- A durable persistence failure keeps the previous baseline and Settings view
  open. If Windows autostart had already changed, a separately bounded
  compensation attempts to restore the previous OS state before reporting the
  failure.
- A timed-out or otherwise unconfirmed Windows mutation is never interpreted as
  the old value. Settings performs a separately bounded readback; only a
  confirmed requested state is accepted. Every other result ends with a bounded
  compensation request for the baseline, and unresolved mutation uncertainty is
  carried into persistence-failure recovery.
- Successful persistence reapplies the normalized snapshot to every control,
  updates the theme/always-on-top state, rebases dirty detection, and invokes the
  saved callback once. Discard likewise restores every control and secret field
  from the immutable baseline before reapplying the baseline theme and closing;
  secret values are never emitted to diagnostics.
- Invalid numeric or color input counts as dirty without logging the supplied
  value, so Back still opens the discard flow and can restore the valid baseline.
- Validation: 56 Settings/autostart/notification/config tests passed; the full
  suite passed with **827 passed, 24 skipped**; `ruff check src tests` and
  `git diff --check` passed.

# 2026-08-04 - Real Windows activation integration boundary

- The real pywin32 adapter now reads `ERROR_ALREADY_EXISTS` from `winerror`;
  using the absent `win32con` member previously forced every packaged Windows
  bootstrap into the sanitized fail-open path after creating its mutex.
- A randomized production-style namespace integration test exercises real named
  mutex/events with separate adapters. It verifies manual SHOW delivery and
  readiness acknowledgement, silent autostart secondaries, bounded broker close,
  immediate namespace reuse, and protected DACLs containing exactly the current
  user and `SYSTEM` on every named object.
- The focused packaged harness isolates `%APPDATA%` and `%LOCALAPPDATA%` beneath
  a unique system-temp directory. It covers hidden alias startup, manual reveal,
  silent alias reuse, four concurrent manual launches, and primary replacement.
  All waits use numeric deadlines; cleanup can terminate only recorded owned
  PIDs and removes the profile only after resolving it beneath the temp root.
- The bundle harness delegates to the focused scenarios and rejects the
  `single_instance_fail_open` marker from the two known packaged log files.
- Validation: 33 single-instance/integration/release-hardening tests passed;
  `ruff check src tests`, PowerShell parser validation, and `git diff --check`
  passed. PSScriptAnalyzer and a verified packaged bundle were unavailable, so
  those two gates were not reported as passed.

# 2026-08-04 - Windows activation and Settings state invariants

## Ownership and activation boundary

- Desktop startup calls the Windows single-instance bootstrap before importing
  or starting Flet. Web mode and non-Windows platforms bypass Win32 ownership.
  The production namespace is scoped to application identity, release channel,
  current Windows user SID, and packaged/development mode; development and
  packaged instances therefore cannot claim each other's namespace.
- The three kernel-object names contain only `Local\\UTHelper-`, a SHA-256
  digest of length-prefixed identity components, and a fixed role suffix. Raw
  usernames, SIDs, paths, credentials, tokens, and settings never enter an
  object name. Every named mutex/event receives a protected DACL granting only
  the current user and `SYSTEM` the synchronization rights required by the
  broker.
- Ownership uses a named mutex. SHOW delivery uses a named auto-reset event;
  readiness uses a named manual-reset event which becomes signaled only after
  the primary receiver is bound; teardown resets readiness. Receiver shutdown
  uses an unnamed event. A manual secondary signals SHOW and exits; an
  autostart/StartupApproved-alias secondary exits silently. A failed handoff
  retries ownership exactly once, becoming a visible primary only if that retry
  succeeds.
- Callback direction is strictly Win32 receiver -> plain callback ->
  `WindowActivator.request_show()` -> `page.run_task(WindowActivator.show)`.
  Only `WindowActivator.show()` touches Flet: it sets `visible=True`,
  `minimized=False`, and `focused=True`, calls `page.update()`, then awaits
  `page.window.to_front()`. Tray Open follows the same path. An accepted SHOW
  prevents later startup-minimized policy from hiding the window again.
- Unexpected Win32 initialization failures fail open as a visible primary and
  emit only the sanitized `single_instance_fail_open` diagnostic. Packaged
  smoke verification treats that marker as a failure rather than accepting a
  degraded single-instance result.

## Numeric timeout contract

- Secondary readiness acknowledgement: at most **1.5 seconds**; non-finite or
  oversized inputs are clamped to that maximum.
- Receiver wait on `(shutdown, activation)`: at most **250 ms** per iteration.
- Broker close: default and production call bound of **1.0 second**. A `True`
  result certifies receiver shutdown, handle cleanup, and no later callback; a
  `False` result reports an already-admitted callback may still finish while no
  new activation is admitted.
- Settings autostart load, requested mutation, uncertain-result readback,
  baseline compensation, and persistence-failure rollback each have their own
  **2.0-second** `asyncio.wait_for` bound.
- The packaged activation harness defaults to **5 seconds** for a child process
  to exit and **10 seconds** for a window state, polls every **100 ms**, and
  gives only owned PIDs **3 seconds** for graceful cleanup plus **3 seconds**
  after forced cleanup. Its parameter validation caps process/window deadlines
  at **60 seconds**.
- The full regression recipe below has an independent **600-second** outer
  PowerShell job timeout. No verification command relies on an unbounded process
  wait.

## Immutable Settings baseline

- `SettingsFormSnapshot` is the sole normalized form boundary. Its members are:
  `theme`; all critical/warning/safe/quiz/assignment/attendance/open/other
  colors; UTH username and password; always-on-top; submitted and graded
  inclusion; start-with-Windows, start-minimized, and minimize-to-tray;
  automatic-update checking; three-state crash-reporting consent; Android
  background checking; Gmail enable/address/app-password; Discord
  enable/webhook; Telegram enable/bot-token/chat-id; debug mode; refresh
  interval; fetch-month count; urgency critical/warning/opening thresholds;
  prefetch workers; DND enable/start/end; submitted-notification filtering;
  notification profile, types, milestones, and muted courses. Password, app
  password, webhook, and bot-token fields use `repr=False`; list/tuple
  conversion occurs only at the snapshot/config boundary.
- A Settings load first captures persisted state, then performs the bounded OS
  autostart read. Controls and the clean baseline commit together only if the
  load generation is still current. Closing, navigating away, or disconnecting
  invalidates the generation; late work cannot change controls, status,
  visibility, global settings, or baseline. An unconfirmed OS read preserves the
  persisted autostart value, disables that control, and shows a retry warning
  without making the form dirty.
- Dirty state is normalized current snapshot != last successful load/save
  snapshot. A successful save reapplies the persisted normalized snapshot to
  every control and rebases only after durable persistence. Discard restores all
  controls, including secret fields and theme, from the immutable baseline.
- Autostart is an OS-owned field. A rejected or timed-out change does not discard
  unrelated valid settings: bounded readback accepts only a confirmed requested
  state; otherwise bounded compensation targets the prior baseline, the
  confirmed/baseline autostart value is used, and the other fields are persisted
  and rebased. If durable settings persistence fails after an OS change, the
  previous baseline remains authoritative and a separately bounded compensation
  attempts to restore the prior OS state.

## Reproducible verification on 2026-08-04

Run from the repository worktree with
`PYTHONPATH=src;extensions/flet_uth_background_sync/src`:

```powershell
$job = Start-Job { Set-Location '<worktree>'; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest tests -q --tb=short }
if (-not (Wait-Job $job -Timeout 600)) { Stop-Job $job; throw 'pytest exceeded 600 seconds' }
Receive-Job $job
Remove-Job $job
ruff check src tests

$job = Start-Job { Set-Location '<worktree>'; $env:PYTHONPATH='src;extensions/flet_uth_background_sync/src'; python -m pytest -m windows_integration -q --tb=short }
if (-not (Wait-Job $job -Timeout 120)) { Stop-Job $job; throw 'windows_integration pytest exceeded 120 seconds' }
Receive-Job $job
Remove-Job $job

git diff --check
rg -n "password|bot_token|webhook|user_sid" src/platform_utils/single_instance.py src/gui/view_models/settings_form.py tests/test_windows_single_instance.py
rg -n "while True|\.wait\(\)" src/platform_utils/single_instance.py scripts/test_windows_single_instance_e2e.ps1
rg -n "Wait-Process" scripts/test_windows_single_instance_e2e.ps1
```

- First bounded full suite: **830 passed, 24 skipped in 10.80 seconds**.
- Separate real-Windows marker: **2 passed, 852 deselected in 1.51 seconds**.
- `ruff check src tests` and `git diff --check`: passed.
- The sensitive-name scan returned only expected protocol/field identifiers,
  `repr=False` declarations, and synthetic SID literals in tests; it found no
  secret value logging in the scoped files. The wait scan found one intentional
  receiver `while True`, whose `wait_many` is bounded to 250 ms and exits on
  shutdown; it found no bare `.wait()` and no `Wait-Process`.
- A development GUI launch was deliberately not run. No verified packaged or
  installed candidate existed in this worktree, so installed-build manual
  activation and the packaged five-scenario E2E were also not run and are not
  claimed as passing. The real Win32 integration above verifies the kernel
  ownership, acknowledgement, manual SHOW, silent autostart, teardown, namespace
  reuse, and DACL boundary without launching Flet.

## Final Windows activation lifetime hardening (2026-08-04)

- Windows desktop ownership and secondary handoff now complete before the first
  `flet` import inside `src/main.py`. Non-Windows and web paths retain their
  bypass, while an acknowledged or failed secondary can exit without loading a
  second Flutter/Python UI runtime.
- The activation receiver clears its manual-reset readiness event on every
  receiver exit, including `wait_many` adapter faults. A dead receiver therefore
  cannot leave a stale acknowledgement that makes a later manual launch report
  a false successful SHOW handoff; close remains bounded and idempotent after
  this fault path.
- `TrayApp.close()` invokes `icon.stop()` exactly once on an owned daemon helper,
  so a blocking native stop cannot block the caller. One monotonic deadline is
  shared across joining that helper and the owned tray daemon; neither the
  current thread nor a non-daemon thread is joined. Setup waits are clamped to
  **3.0 seconds** and the complete close budget to **1.0 second**; negative waits
  become zero and non-finite waits cannot block indefinitely. Repeated close
  calls retry only bounded joins and never invoke stop again. A native stop
  exception is recorded under the lifecycle lock; after joining the helper,
  every current or later close reports `False` instead of falsely certifying a
  clean shutdown.
- Setup rechecks closure after dependency loading and atomically publishes and
  starts a candidate icon/thread under the lifecycle lock. Therefore either
  setup wins and close observes both owned resources, or close wins and no tray
  icon/thread can be published or started afterward. `AppController` closes the
  broker then tray before releasing page-owned events, coordinator, and Moodle
  client resources.
- Focused activation/tray regression suite: **57 passed in 1.40 seconds**.
  Bounded full suite after these review fixes: **839 passed, 24 skipped in
  9.95 seconds**. `ruff check src tests` and `git diff --check` passed; no GUI
  or bundle process was launched.

## Private diagnostic report schema (2026-08-04)

- Added a dedicated `diagnostics` boundary with immutable Pydantic models for
  consent, lifecycle phase, normalized frames, local construction context, and
  the versioned report payload. Every model rejects unknown fields; report
  strings and frame count/line ranges are bounded.
- The remotely eligible report deliberately has no exception message, raw
  traceback, username, Moodle data, or stable device identifier. Local
  `source_root` context is excluded from serialization.
- Focused schema verification: **2 passed**. `ruff check` for the new package and
  test plus `git diff --check` passed. The planned pytest-timeout option was not
  available in this environment, so the focused run used the shell's finite
  20-second process timeout instead.

## Pre-persistence diagnostic redaction (2026-08-04)

- Diagnostic exception reports are now assembled exclusively from normalized
  exception type and traceback metadata. Exception messages, arguments, causes,
  contexts, absolute external paths, and local source roots never enter the
  serialized report.
- Application frames are source-relative when they belong to the configured
  source tree and basename-only otherwise. Identifiers, paths, frame count, and
  fingerprint inputs are allow-list normalized and bounded; event UUID and
  occurrence time do not affect deterministic grouping.
- Local operational log text has a separate defensive sanitizer for credentials,
  authorization values, URLs, email addresses, home-directory paths, control
  characters, unprintable values, and oversized input. It is not used as a
  substitute for the report's typed allow-list boundary.
- Focused privacy verification: **26 passed**. Scoped Ruff and `git diff --check`
  passed; the pytest process used a finite 20-second shell timeout.
- Reviewer hardening now redacts over a fixed 1 KiB lookahead before enforcing
  the 4 KiB log-output cap. If sanitized text still crosses the cap, the
  unfinished trailing token is replaced fail-closed instead of being emitted as
  a recognizable email, credential, URL, bearer token, or user-path fragment.
- Cross-cutoff coverage exercises both split family prefixes and variable-length
  values for email, bearer, URL, key/value, Windows user path, and Unix user path,
  including a one-million-character synthetic input while regex work remains
  bounded to the fixed scan buffer.

## Bounded, redacted application logging (2026-08-04)

- Replaced the two import-time `FileHandler` blocks in `src/main.py` with one
  diagnostics-owned `RotatingFileHandler`, configured only after the platform
  data directory exists and without changing the pre-Flet Windows ownership
  bootstrap.
- The local application log is capped at 2 MiB with three backups. The exact
  legacy root `data_dir/debug_app.log`, `logs/app.log`, and the three owned
  backups are checked before the new handler opens. Oversized regular files and
  unsafe symlinks at only those paths are unlinked without following or
  modifying their targets; unrelated names remain untouched.
- The owned formatter sanitizes the complete final record, including formatted
  arguments, exception output, and stack information, immediately before the
  file write. Formatting failures fall back to bounded non-sensitive text.
- Configuration is process-idempotent. `LoggingRuntime.close()` is idempotent
  and removes/closes only its owned handler, leaving every pre-existing root
  handler untouched.
- Focused logging verification passed with **8 tests**. Logging/config/main
  startup regression verification passed with **54 tests**; `ruff check src
  tests` and `git diff --check` passed.

## Atomic bounded diagnostic spool (2026-08-04)

- Added a diagnostics-owned offline queue that accepts only validated
  `DiagnosticReport` instances and revalidates their serialized schema before
  persistence. Entries are written with exclusive-create, flush, file fsync,
  and atomic replace; temporary files are cleaned on every failure path.
- Queue pruning is deterministic by report time, event ID, and filename. It
  removes invalid/expired direct regular entries, deduplicates fingerprints,
  and retains at most **20 events**, **1 MiB**, and **7 days** of reports.
- A process-wide per-root reentrant lock serializes multiple spool instances.
  Directory scans never recurse or follow symlinks; initialization rejects a
  symlink root and every operation fails closed if the root is replaced.
  Acknowledge and clear remove only validated or explicitly owned direct regular
  JSON/hidden-temp children, leaving nested/unrelated entries and link targets
  untouched.
- Focused spool verification passed with **12 tests**, including simulated
  replace failure, concurrent dedupe/pruning, corruption recovery, exact caps,
  and live Windows symlink cases. Scoped Ruff and `git diff --check` passed.
- Review hardening added a bounded five-second Windows named mutex so queue
  mutation is serialized across application processes, not only threads. An
  abandoned owner is recoverable and stale temp cleanup occurs only after the
  next owner acquires the mutex; an active fsynced temp can no longer be removed
  before its atomic replace.
- Windows roots and entries are opened without traversing reparse points. Root
  volume/file ID is captured before/after canonicalization and checked on every
  operation; symlinks and junctions are rejected. Queue entries use stable
  volume/file IDs, and deletion is applied to the verified open handle, so a
  same-sized replacement cannot be deleted through a path race. The expanded
  spool suite passes **15 tests**, including real spawned-process, junction, and
  same-size replacement regressions.
- A follow-up root-namespace review found that validating and then closing the
  directory handle left a junction-swap window before scanning. Each Windows
  transaction now holds the verified root handle without delete sharing and an
  fsynced, exact-identity operation guard until all queue work finishes. This
  prevents the root from becoming empty/removable during the transaction, while
  exact-handle cleanup prevents the guard from targeting a replacement. A
  synchronized spawned-process regression proves the swap is blocked and an
  outside `foreign.json` remains untouched. The diagnostics set now passes
  **64 tests** and explicitly proves a competing spool process remains blocked
  until the active writer releases its fsynced temp transaction.

## Consent-gated anonymous diagnostic delivery (2026-08-04)

- Crash-reporting consent remains tri-state and defaults/migrates to
  `not_asked`. Both `not_asked` and `disabled` construct no Sentry client and
  perform no diagnostics network work; `disabled` also clears the exact owned
  spool immediately. An enabled but unconfigured installation keeps its local
  queue and performs no network work.
- Public runtime configuration accepts only an HTTPS Sentry ingestion DSN with
  a public key and numeric project ID. Query, fragment, password, management or
  auth-token forms fail closed. The generator writes only sorted
  `schema_version` and `sentry_dsn` fields through same-directory atomic
  replacement; an empty build value produces a truthful unconfigured asset.
- Because `[tool.flet.app].path = "src"`, the generated asset contract is
  `src/assets/diagnostics-config.json` (not a repository-root `assets` folder).
  Packaged loading resolves `FLET_ASSETS_DIR` and never consults
  `UTH_SENTRY_DSN`; that override is honored only when the caller explicitly
  enables development mode.
- Sentry is initialized lazily with default integrations and PII disabled, no
  breadcrumbs or traces, and a `before_send` callback that discards the SDK
  candidate and reconstructs the complete event from the validated report
  allow-list. The outcome-aware synchronous transport uses the remaining
  bounded deadline and treats only an actual HTTP 2xx response as confirmed.
  Event IDs returned by SDK queueing are never considered delivery proof.
- Confirmed reports alone are acknowledged. Offline, timeout, HTTP 429/5xx,
  SDK filtering, and other HTTP rejection outcomes retain the report. Retry
  delay is bounded and suppresses an immediate second network attempt; a flush
  budget is clamped to **0.1–5.0 seconds**.
- The config/spool/transport regression set passes **117 tests in 10.05
  seconds**. The real Sentry SDK 2.x envelope path was exercised with a local
  intercepted HTTP boundary (no external report was sent). Scoped Ruff and
  `git diff --check` pass.
- Delivery hardening now installs an explicit no-redirect urllib handler.
  Diagnostic POST redirects are retained as `http_rejected`; neither the
  envelope body nor public Sentry auth header can be replayed to a second
  origin. A bounded two-origin loopback regression verifies exactly one request
  reaches the configured origin and none reaches the redirect target. Socket
  delivery remains synchronous, and a slow-origin regression confirms the
  configured deadline returns without an application-owned background sender.
- NaN and positive/negative infinity are rejected before Sentry construction or
  network work. Non-finite `Retry-After` values are ignored in favor of the
  worker's bounded exponential delay.
- Every Flet invocation in Android/iOS/Windows release workflows and the local
  Android and Windows EXE installer paths now atomically generates
  `src/assets/diagnostics-config.json` immediately after the previous build and
  before the next one. GitHub uses the public `vars.SENTRY_DSN` value; absent
  values generate the explicit empty/unconfigured asset. A release-hardening
  test discovers both literal and command-object build invocations across every
  workflow YAML and PowerShell build script. It now locks all **nine** actual
  Flet builds, including both passes in `scripts/build_android.ps1`.
- Review-focused transport/config/release tests pass **61 tests in 3.01
  seconds**; broader config/spool/release regression tests pass **142 tests in
  13.53 seconds**. Full source/test Ruff and `git diff --check` pass.

## Chain-safe diagnostic runtime lifecycle (2026-08-04)

- Added `DiagnosticRuntime` as the sole owner of Python main-thread, worker
  thread, unraisable, asyncio-loop, and Flet-page failure hooks. It records only
  actual `BaseException` objects through the existing allow-listed report
  builder, uses a thread-local recursion guard, chains prior handlers, and
  restores a hook only while it still owns that hook.
- The run marker contains exactly schema version, app version, coarse UTC
  minute, phase, and `clean=false`. It is written by fsynced same-directory
  replacement; corrupt or unsafe markers never claim a crash. A prior valid
  uncleared marker means only `unclean_previous_exit`, and clean close removes
  only the marker identity written by this runtime.
- The diagnostics directory rejects links/reparse points and stays pinned for
  the runtime with a Win32 handle plus an fsynced operation guard. The guard
  keeps a same-user junction swap from replacing the root during marker/fault
  work and is recovered only when it is a direct regular stale file. Marker,
  guard, and native-fault link targets are never followed or modified.
- Faulthandler starts only when no prior global owner exists. Its fresh file is
  opened without following a reparse point, remains owned by its exact open
  handle, and is truncated on that handle to **256 KiB** before close. A
  chain-wrapper around later successful `faulthandler.enable()` calls prevents
  shutdown from disabling a later owner; the wrapper is restored only while
  still runtime-owned.
- Diagnostic delivery is submitted once to a single background executor;
  startup never waits for network delivery and shutdown calls
  `shutdown(wait=False, cancel_futures=True)`. A reference is displayed only
  when the spool confirms a durable event; deduplication reuses the durable
  queued event ID, while failed/oversized capture displays no false reference.
- `src/main.py` preserves Win32 single-instance bootstrap before importing
  Flet, starts diagnostics before GUI imports, attaches the page immediately,
  replaces traceback UI/log output with a reference-only screen, and leaves the
  marker on unhandled `BaseException`/native abort. Ordinary or explicitly
  handled runner exits close cleanly. Broker-close failure cannot skip runtime
  cleanup.
- Focused runtime/main/logging/config verification passes **86 tests in 2.55
  seconds**. The complete test suite exited successfully under a per-test
  **180-second** timeout; `ruff check src tests` and `git diff --check` pass.
- Faulthandler ownership review found a close race between a later successful
  `enable()` call and its supersession flag. The tracked enable call and flag
  assignment now execute under the same runtime lifecycle lock used by close,
  so close can neither disable nor close resources across a partially published
  later owner. A failed later enable does not claim ownership, and a wrapper
  captured before close remains safe when invoked afterward. Deterministic
  barrier coverage bounds every wait/join and reproduces the former race.

## Bounded diagnostic crash-boundary evidence (2026-08-04)

- Added real child-process coverage for uncaught main-thread, worker-thread,
  async-main, and unraisable exceptions. Each child constructs the production
  `DiagnosticRuntime` boundary with explicit disabled consent, an injected
  no-network delivery, deterministic safe context, and one exception whose
  private message contains email, Moodle sesskey, and token-shaped values.
- Every Python boundary writes exactly one schema-valid report, and none of the
  private values enter its serialized bytes. Main/async children preserve their
  non-zero process exit, while worker-thread/unraisable children preserve normal
  process exit. Raw captured stderr is never rendered by test assertions; only
  bounded byte counts and return codes cross the parent test boundary.
- The native-abort child fsyncs the marker and local faulthandler evidence before
  `os.abort()`. It leaves an unclean marker and no fabricated Python report. A
  separate clean child proves ordinary close removes the marker and creates no
  report. Every `subprocess.run` has a ten-second deadline, creates no descendant
  process, and relies on its documented kill-and-wait behavior on timeout.
- Focused subprocess verification passes **6 tests in 1.80 seconds**; combined
  runtime/subprocess verification passes **36 tests in 2.48 seconds**. Two
  initial bounded full-suite runs each reached **991 passed, 24 skipped** and
  exposed the same Windows socket-scheduling threshold issue. After that timing
  guard was fixed independently, the bounded full suite passed with **992
  passed, 24 skipped in 50.81 seconds**; its runner exited normally with no
  child or test session left behind.

## Anonymous Windows crash correlation (2026-08-04)

- Run-state schema 2 now records a local-only, minute-coarse UTC start time,
  periodically refreshed last heartbeat, lifecycle phase, app version, and a
  strict executable basename. A daemon heartbeat refreshes the atomic marker
  every minute; clean shutdown signals it and joins for at most 0.5 seconds.
- Only Windows installations with a valid prior unclean marker whose heartbeat
  is no more than ten minutes old query the Application event channel. The
  production adapter requests provider `Application Error`, Event ID 1000,
  reverse chronological order, one batch of at most 50 records, and a one-second
  `EvtNext` timeout. It never formats event messages and discards paths, report
  IDs, user/machine/process data, command lines, and unrelated event fields.
- Correlation requires the application basename to match case-insensitively and
  the event time to fall inside the prior run window. Remotely eligible evidence
  is limited to a normalized exception code, a 128-character-safe faulting
  module basename, and a minute-coarse event time. Permission, API, timezone,
  malformed, future, stale, oversized, and unrelated inputs fail closed.
- Native code/module metadata is a strict pair and is attached to exactly the
  next durable sanitized report for the unclean run. Capture serialization
  prevents concurrent reports from consuming it twice; a failed enqueue keeps
  it available for the next durable report. Missing Windows evidence preserves
  the truthful `unclean_previous_exit` classification and makes no native-crash
  claim.
- Focused Windows evidence, redaction, transport, and runtime verification
  passes **132 tests in 6.65 seconds**. The bounded full suite passes **1022
  tests with 24 skipped in 49.49 seconds**. `ruff check src tests` and `git
  diff --check` pass, and bounded tests assert heartbeat/capture helper threads
  terminate before returning. A broader `ruff check src tests scripts` still
  reports 18 pre-existing issues confined to `scripts/debug_panel_test.py` and
  `scripts/notification_system_test.py`; no Task 8 file is implicated.
- Review hardening separates clean and unclean classifications in the content
  fingerprint. An identical exception already queued during a clean run can no
  longer absorb the next run's `unclean_previous_exit=true` report, while
  reports within the same classification still deduplicate. The production
  event-log adapter now closes real pywin32 `PyHANDLE` objects through their
  `.Close()` method exactly once, including query, read, and render failure
  paths; it does not call a nonexistent module-level `EvtClose` function.
- Review-fix RED reproduced six failures. The final focused regression set
  passes **130 tests in 6.48 seconds**, and the bounded full suite passes **1026
  tests with 24 skipped in 48.01 seconds**. `ruff check src tests` and `git diff
  --check` pass, and the full runner exited normally with no helper process or
  heartbeat thread left behind.

## Verified Flutter runner diagnostic bridge (2026-08-08)

- Windows release builds now accept only the immutable official Flet 0.86.5
  build template whose SHA-256 is
  `8f95dc20ef6d901d9b5ee59f00e33d19f1d2bc6be8d6d3b800c4aab3d7315b73`.
  The patcher rejects unknown hashes, changed anchors, duplicate/traversal/link
  ZIP members, and non-atomic output. It writes a stored, normalized ZIP whose
  reviewed deterministic SHA-256 is
  `f44e29a58394f7e5a47a72bee1a54033106cef2a43b94e7b06e58bb464630a00`.
- The generated Dart runner installs Windows-only `FlutterError.onError` and
  `PlatformDispatcher.instance.onError` wrappers. Each wrapper synchronously
  flushes a bounded record before invoking the prior handler. Records contain
  only normalized runtime type, at most sixteen normalized symbols, boot/GUI
  phase, and a UTC minute; no exception message, raw stack, path, account, URL,
  or Moodle value crosses the bridge. The JSONL bridge never exceeds 64 KiB and
  direct links/non-files are rejected before writing.
- Python startup reads only the exact direct regular bridge file through the
  existing no-follow identity boundary. Unknown fields, unsafe identifiers,
  malformed JSON, non-minute timestamps, oversized files/records, stale/future
  evidence, and unsafe replacements fail closed. Invalid owned files are
  deleted with an exact-file operation and only a constant redacted reason is
  logged. Valid evidence uses the existing sanitized report builder; the bridge
  is deleted only after every report is durable, so a partial spool failure is
  retried safely and deduplicated on the next start.
- Windows CI and the local installer builder download with a finite timeout,
  verify the official hash, prepare the pinned template before Flet, pass it via
  `--template`, and verify generated `build/flutter/lib/main.dart` immediately
  afterward. Android and iOS builds never receive this Windows-only patch. All
  Windows release workflow steps now have explicit finite timeouts.
- The final focused fixture/runtime/release set passes **75 tests**. A bounded
  full suite passes **1053 tests with 24 skipped in 20.10 seconds**; `ruff check
  src tests` plus both new scripts, PowerShell/YAML parsing, and `git diff
  --check` pass. A real bounded Flet 0.86.5 Windows build completed in **354.6
  seconds**, generated `uthelper.exe`, and the generated-source verifier passed.
  Flutter analysis of the final synchronous patch had no errors (only the two
  existing official-template unused-import notices). Independent re-review
  reported no remaining Critical or Important findings.

## Published diagnostic privacy and live-consent boundary (2026-08-08)

- `docs/PRIVACY.md` is the public contract for anonymous crash diagnostics. It
  states the explicit opt-in boundary, the remote allow-list and forbidden
  Moodle/account data, the lack of a stable device identifier, the local
  20-report/1-MiB/7-day limits, disable/delete behavior, the unclean-exit
  caveat, and the network-visible IP/Sentry-retention limitations. The README
  links to this contract rather than implying diagnostics are unconditional.
- Successful durable Settings saves now publish an in-process notification
  through a small config-owned subscription boundary. The diagnostic runtime
  subscribes once and unsubscribes on close: disabling clears its owned spool
  synchronously without a network request, while enabling schedules only the
  existing bounded delivery attempt. Failed saves do not change live consent.
  Subscriber failures are isolated and logged without values.
- CI has a least-privilege, ten-minute `Private diagnostics` job that installs
  no release DSN and executes the complete diagnostic boundary set with a
  per-test timeout. A contract test guards the job name, permissions, timeout,
  file list, and absence of Sentry configuration.
- Independent review found that a consented flush could outlive revocation and
  that post-revoke exceptions could recreate the queue. A shared live-consent
  gate now serializes revocation with report capture, lazy transport creation,
  and every bounded send. Revocation waits only for an already-started bounded
  request, then atomically blocks later sends/captures and clears the queue and
  Flutter bridge. Before opt-in, no report is captured or delivery task queued.
  A blocking two-report concurrency test proves that the second request never
  starts after revoke returns.
- The exact diagnostic release gate passes **212 tests in 11.89 seconds**. The
  focused config/runtime/privacy set passes **130 tests in 3.94 seconds**, and
  the bounded full suite passes **1062 tests with 24 skipped in 22.15 seconds**.
  `ruff check src tests scripts`, YAML parsing, `git diff --check`, and the
  reviewed forbidden-term scan pass. The only scan matches are the public
  forbidden-field documentation, rejection/redaction code, and GitHub Actions
  references to encrypted signing secrets; no diagnostic payload or CI DSN is
  present. Two legacy manual test scripts received only mechanical Ruff fixes.

## Trusted update manifest domain (2026-08-08)

- Update discovery now has immutable `RuntimeTarget`, `ReleasePackage`,
  `ReleaseManifest`, and `UpdateCandidate` domain types. Schema 2 parsing is
  allow-list based: unknown/missing fields, nonnumeric versions, unsupported
  target triples, unsafe URLs, mismatched extensions/strategies, invalid sizes,
  digests, and signer fingerprints fail closed.
- Selection matches platform, architecture, and installed channel exactly;
  duplicate matches are rejected and current/older releases produce no
  candidate. Schema 1 is retained for one discovery-only compatibility window
  and can never authorize automatic installation.
- The new manifest suite plus legacy updater compatibility suite passes **20
  tests**; focused Ruff and whitespace gates pass.

## Bounded verified update transport (2026-08-08)

- GitHub discovery now requires exactly one manifest asset from a stable
  release, uses a 20-second timeout and 1-MiB JSON bound on every request, and
  rejects invalid tags, duplicate manifests, unapproved hosts, and malformed or
  mismatched manifests without guessing a release asset. Schema 1 candidates
  remain visible for release notes but expose no downloadable installer.
- `VerifiedDownloader` writes to a unique `.part` path, enforces a 180-second
  total deadline, checks cooperative cancellation before connect and every
  chunk, caps bytes at the manifest size, fsyncs, verifies exact size and
  SHA-256, and only then atomically replaces the destination. Every failure
  removes the partial file; the legacy callback API delegates to this boundary.
- Manifest and transport compatibility verification passes **23 tests**;
  focused Ruff passes.

## Windows update trust and channel boundary (2026-08-08)

- Runtime targeting checks Windows package identity first, otherwise reads only
  the machine-scoped MSI channel marker and defaults an absent marker to the
  bootstrapper channel. It never infers the installed channel from the current
  filename. Android/iOS targets remain isolated behind the cross-platform
  adapter.
- Windows verification binds exact size/SHA-256 and MSI/EXE container magic to
  a valid, timestamped Authenticode signature. Both manifest signer identity
  and fingerprint must match native evidence, and the fingerprint must also be
  present in the signed application's source-owned trust set. MSI product,
  version, UpgradeCode, and x64 template or Burn product/version are probed by
  bounded, argument-safe PowerShell processes.
- Installer launch accepts only the manifest-approved MSI or Burn strategy,
  waits at most two seconds for immediate failure, and owns bounded exact-
  process cancellation. Tasks 1-3 plus architecture boundaries pass **35
  tests**; focused Ruff passes. The production trust set intentionally remains
  empty until the release certificate fingerprint is reviewed and pre-shipped.

## Android installed-signer update boundary (2026-08-08)

- The Python/Flet/Kotlin bridge now carries required APK byte size, package ID,
  monotonic versionCode, and manifest certificate SHA-256, plus an explicit
  `cancel_update` path. Kotlin owns one atomic request and checks cancellation
  before connection, around every bounded read, and before installer launch;
  partial files are removed in `finally`.
- APK bytes must come through approved HTTPS GitHub hosts, finish within the
  180-second/20-second socket bounds, and match exact size and SHA-256 before
  Android package metadata is read. The archive must have the running package
  ID, the exact expected and newer versionCode, contain the manifest signer,
  and share a signer/signing-lineage certificate with the currently installed
  app. Thus a tampered manifest and attacker APK cannot redefine local trust.
- Python bridge contracts pass **18 tests**. Both debug and release Android JVM
  tests pass under Gradle 8.14 with `--no-daemon` in **50 seconds**, including
  wrong-package, rollback, attacker-signer, and signing-lineage cases. The
  standalone test invocation receives the official cached Flutter embedding JAR
  through an optional Gradle property; packaged builds keep their host-provided
  Flutter dependency.

## Default-on trusted update coordinator (2026-08-08)

- Settings schema 3 migrates an absent `AUTO_UPDATE_ENABLED` key to `True` and
  exposes both the default-on switch and a manual `Kiểm tra ngay` action. The
  controller delegates both paths to one coordinator rather than retaining a
  second legacy download/install implementation.
- One daemon worker owns discovery, download, verification, readiness, and
  confirmation state. Its command queue is fixed at eight entries with
  coalesced manual/automatic/preference commands; every wait, network boundary,
  cancellation, and shutdown join is bounded. Automatic checks queued before a
  preference disable and all queued checks after shutdown are rejected before
  network I/O, while manual checks remain available when automation is off.
- A verified installer is never launched before explicit confirmation. Schema
  1 opens only GitHub release notes, iOS opens only an allow-listed Apple store
  URL, Windows exits only after the installer acknowledges handoff, and Android
  forwards exact package ID, derived monotonic versionCode, byte size, SHA-256,
  and certificate fingerprint to the native installed-signer verifier.
- Downloaded packages are deleted for every verifier rejection or exception.
  Preference disable cancels an in-flight download but cannot cancel a user-
  confirmed installer; shutdown retains exact-process cancellation only until
  a successful handoff. Independent review found and verified fixes for queued
  check races, pre-start download stalls, unbounded command growth, rejected
  cache cleanup, and post-shutdown network dispatch.
- The final repository suite passes **1110 tests with 24 skipped in 22.15
  seconds**. Full Ruff, bytecode compilation, and whitespace gates pass, and
  the final independent Task 5 re-review reports PASS.

## Exact signed release inventory (2026-08-08)

- `pyproject.toml` is the sole authored application version and is now 2.2.0.
  Release tags must exactly equal `vX.Y.Z`; Android/iOS build numbers derive as
  `major*1_000_000 + minor*1_000 + patch`, with every component restricted to
  0..999. The transitional Inno wrapper receives this value mechanically and
  cannot author a competing version.
- A release is valid only when its external file set is exactly IPA, APK, Burn
  EXE, and MSI with canonical versioned names. Container magic, byte hash,
  native product identity, architecture, signer identity/fingerprint,
  signature, Windows timestamp, platform-specific check names, commit SHA, and
  workflow run ID are bound by one exact evidence record per package. Unknown
  assets, evidence fields, MSIX/AppInstaller leftovers, renamed ZIPs, and
  missing formats fail closed.
- Schema 2 manifests can only be generated from that verified inventory. Each
  GitHub asset URL must exactly equal its canonical quoted release URL; manifest
  bytes, architecture, signer, and fingerprint must equal native evidence. iOS
  alone carries an external install strategy and accepts only exact HTTPS
  `apps.apple.com` or `testflight.apple.com` hosts.
- `SHA256SUMS` is deterministic LF-only output over the four packages plus the
  manifest. Its parser rejects malformed, duplicate, missing, extra,
  self-referential, traversal, reordered, or mismatched entries. Both canonical
  CLIs bootstrap correctly under isolated Python execution.
- Task 6 focused verification passes **48 tests**, and the full suite passes
  **1143 tests with 24 skipped in 36.63 seconds**. Ruff, release YAML parsing,
  whitespace checks, and the final independent review all pass.

## WiX MSI and Burn release path (2026-08-08)

- WiX 7 is now the sole Windows installer authoring path: a machine-scoped x64
  MSI with one stable UpgradeCode and an x64 Burn bootstrapper with a separate
  stable UpgradeCode. The legacy Inno source was removed and its wrapper now
  delegates to the canonical, version-derived WiX build under a 30-minute
  exact-process deadline.
- The build fails before restore unless the owner explicitly supplies
  `WIX_EULA_ACCEPTED=wix7`. MSI/Burn builds, WiX validation/extraction, Burn
  detach/reattach, and every SignTool call are bounded. Signing follows the
  required MSI, detached engine, reattached bundle, outer bundle sequence with
  SHA-256 and RFC3161 timestamping.
- Native verification requires exact Authenticode subject/fingerprint and
  timestamp, MSI ProductName/ProductVersion/UpgradeCode/x64 tables, and Burn
  Registration version/scope/stable PrimaryUpgradeCode/x64 engine metadata.
  WiX extracts the embedded MSI under an extensionless payload ID, so the gate
  identifies exactly one MSI by its OLE header and then requires byte-for-byte
  SHA-256 equality with the canonical signed MSI.
- The bounded upgrade harness verifies distinct ProductCodes with one stable
  UpgradeCode, baseline install, injected deferred-action rollback, exact
  `InstallVersion` preservation, successful major upgrade, downgrade rejection,
  settings preservation, removal of both exact HKCU Run values, MSI and Burn
  uninstall, and clean final state. Cleanup accepts only success/already-absent,
  retries installer-busy three times with fixed sleeps, and does not mask the
  primary test failure.
- Real unsigned smoke authoring produced and ICE-validated
  `UTHelper-2.2.0.msi` and `UTHelper-Setup-2.2.0.exe` with zero warnings. A real
  Burn extraction confirmed `Win64=yes`, version 2.2.0, the stable bundle
  UpgradeCode, and extensionless embedded-MSI hash equality. Certificate gates
  and destructive install/upgrade E2E remain release-runner checks because this
  workspace has neither the protected signing certificate nor a signed baseline.
  Task 7 focused tests pass **25/25**; the full suite passes **1157 tests with 24
  skipped in 40.29 seconds**, and focused Ruff plus PowerShell parsing pass.

## Monotonic signed Android release package (2026-08-08)

- Android now has the explicit lowercase package identity
  `com.uthelper.uthelper`. Release `versionCode` is not an independent input: the
  native verifier derives `major*1_000_000 + minor*1_000 + patch` again and
  rejects a caller-supplied mismatch before invoking Android SDK tooling.
- The protected release workflow decodes one backed-up keystore only beneath
  `RUNNER_TEMP`, passes passwords solely through Flet signing environment
  variables, uses the public alias/fingerprint as GitHub variables, and runs the
  notification-patcher two-pass build with exact versionName/versionCode.
  `--yes` replaces an unbounded stdin pipe.
- Verification invokes the newest numeric Android build-tools `apksigner` and
  SDK `apkanalyzer` with 60-second deadlines. It requires canonical ZIP magic,
  exactly one pinned signer, package/version/build identity, and parses manifest
  XML so all five notification components must be exact `<receiver
  android:name=...>` entries. Evidence SHA-256 is streamed and atomically
  written with the expanded Android native-check set consumed by the exact
  release inventory.
- Unprivileged Android CI cannot emit a release-looking file. It renames output
  to a commit-bound `unsigned-diagnostic` APK, appends a unique diagnostic marker
  to invalidate Flet's debug signature, and requires `apksigner verify` to fail
  before upload. It never receives release signing inputs or attestation.
- Task 8 focused verification passes **60 tests**; the full suite passes **1166
  tests with 24 skipped in 30.72 seconds**. Focused Ruff, YAML parsing,
  whitespace checks, and independent re-review pass.

## Genuine Apple Distribution IPA path (2026-08-08)

- Unprivileged iOS CI now builds only `ios-simulator` and publishes a
  commit-bound diagnostic ZIP; it never uses the `.ipa` extension. The protected
  release job alone imports a P12 into a unique ephemeral keychain, installs one
  exact provisioning profile UUID, and runs Flet's `app-store-connect` export
  with canonical version/build number, team, profile, and signing identity.
- IPA verification requires one Payload app, strict deep code-sign validation,
  arm64 Mach-O, exact bundle/version/build identity, App Store distribution
  profile with no provisioned devices, future expiration, `get-task-allow=false`,
  and exact application/team entitlements in both profile and signed app. The
  extracted leaf certificate SHA-256 must be pinned and present in the profile's
  `DeveloperCertificates`; evidence is atomically emitted with the expanded
  native check set consumed by release inventory.
- Upload uses the Xcode-bundled Transporter with a 1,200-second hard deadline,
  one exact `private_keys/AuthKey_<id>.p8` at POSIX mode 0600, and fail-closed
  return/output checks. The base64 key is unset after decode and the Python
  launcher strips all known signing secrets from the Transporter child
  environment. It never prints Transporter output or private material.
- An `always()` cleanup restores the original user keychain list and removes
  only the exact imported profile, ephemeral keychain, P12/profile files, and
  Transporter key directory. Task 9 focused verification passes **52 tests with
  1 platform-mode test skipped on Windows**; Bash syntax, Ruff, YAML, whitespace,
  and independent re-review pass. The full repository suite passes **1173 tests
  with 25 skipped in 34.18 seconds**.

## Atomic exact release transaction (2026-08-08)

- The tag workflow now has five stable job IDs: source validation, three native
  signed builds, and one final exact publication job. Every third-party action
  is full-SHA pinned, permissions are job-local, signing jobs use only the
  protected `release` environment, and every job/step has a finite timeout.
- A tag must equal the canonical project version and be contained in `main`.
  Validation runs the complete Python and extension suite before any signing
  job can begin. Android deliberately retains its two-pass patched Flet build,
  so public diagnostics config is regenerated immediately before both passes.
- Each native job verifies package identity/signature and emits SHA-bound
  evidence before attesting and uploading a named current-run artifact. The
  final job reconstructs exactly one IPA, APK, MSI, and Burn EXE, creates the
  schema-2 manifest and deterministic five-entry `SHA256SUMS`, then attests and
  verifies all six public files against the exact release workflow and source.
- Publication starts only after all local gates pass. It requires an exact 404
  preflight, creates an empty draft, records its numeric ID, uploads six explicit
  names, validates the remote name/size/API-digest set, downloads every asset
  again, and compares bytes before publishing. Failure cleanup can delete only
  that same numeric record while it remains a draft with the same tag.
- Task 10 focused policy/metadata/inventory/manifest verification passes **81
  tests** after review caught and removed a Linux-incompatible Windows extra from
  source validation and restored the supported-version floor to `2.1.0`. Ruff,
  YAML loading, `git diff --check`, and actionlint 1.7.12 pass.

## Repository governance and final local gate (2026-08-08)

- All workflows now pin every third-party Action to a 40-character commit SHA,
  disable checkout credential persistence, and use read-only/default-deny token
  permissions outside the release publisher. CI dependency auditing is
  fail-closed rather than an informational `|| true` command.
- CODEOWNERS covers the repository and names release/update-sensitive paths;
  structured contributor, pull-request, private security-reporting, and weekly
  Dependabot policies are committed so serious fork-based contributions have a
  clear review/test boundary without exposing secrets or student data.
- Production operator documentation names the exact protected environment
  secrets/variables and six assets. It explicitly distinguishes a local unsigned
  structure rehearsal from native signed release evidence and does not claim an
  absent Apple/Windows identity has passed.
- Final local Python regression passes **1184 tests with 25 skipped in 44.07
  seconds**; repository-wide Ruff, governance tests, release policy tests, YAML,
  whitespace, and actionlint 1.7.12 pass. Review expanded dependency auditing
  into compatible core, Android-build, and Windows matrices, added the safe PVR
  fallback/external enforcement checklist, and reserved the security label for
  real advisories. Android JVM execution is not claimed:
  the extension source contains no Gradle wrapper and this machine has no global
  Gradle installation, so the bounded command failed immediately before tests.
- The first clean post-merge run exposed a path-order bug that the linked
  worktree could not reproduce: an editable install already placed the main
  repository root behind site-packages, so isolated release CLIs imported an
  unrelated installed `scripts` package. Both CLI bootstraps now remove stale
  occurrences and place the current repository/source roots first
  deterministically. The original two failures then pass, and the complete
  post-merge suite passes **1184 tests with 25 skipped in 40.39 seconds**.

## Release-source keyring parity (2026-08-09)

- The first immutable `v2.2.0` release attempt stopped in the Linux source
  validation job before any native runner started. The release job installed
  the base project while the complete config tests require the same `keyring`
  test dependency that normal CI already installs; ten secure-storage tests
  therefore failed only in that clean release environment.
- Release-source validation now installs `keyring>=25.0.0` explicitly and a
  workflow contract test locks that dependency parity. The project version is
  `2.2.1` because the failed `v2.2.0` tag remains immutable and is not rewritten.

## Windows release console UTF-8 boundary (2026-08-09)

- The immutable `v2.2.1` release passed source and credential validation, then
  the Windows runner stopped before compilation when Flet printed its Unicode
  success marker through a CP1252 Python console. Android and iOS were cancelled
  after the Windows failure because exact publication could no longer proceed.
- The Windows release job now forces Python UTF-8 mode and UTF-8 standard-stream
  encoding for Flet and all Python subprocesses. A workflow contract test locks
  both variables. The next immutable release version is `2.2.2`.

## Unsigned iOS archive output path (2026-08-09)

- The immutable `v2.2.2` run built a valid no-codesign iPhoneOS archive, then
  failed before packaging because the workflow searched `build/ios/archive`.
  Flet copies the archive output into the project-level `build/ipa` directory;
  the run log confirmed `UTHelper.xcarchive` was produced before that mismatch.
- The release workflow now requires exactly one `.xcarchive` directly beneath
  `build/ipa`, and its contract test rejects the stale source-build path. The
  next immutable release version is `2.2.3`.

## Android merged-manifest verification boundary (2026-08-09)

- The immutable `v2.2.3` and `v2.2.4` runs both produced the signed 162.3 MB
  release APK. The apparent post-build Flet exit was diagnosed more precisely
  in `v2.2.4`: Flet returned successfully, then the first silent `grep` failed
  because the release workflow inspected the app source manifest. Plugin
  receivers are contributed by Android libraries and only exist in the final
  Gradle-merged APK manifest.
- Release verification now matches the already-passing Android PR workflow: it
  extracts the final APK manifest with `apkanalyzer` before checking every
  notification/background receiver. The independent Python release verifier
  repeats the merged-manifest check alongside the package version and pinned
  signing-certificate checks. The temporary non-zero-Flet workaround was
  removed because the evidence did not support that diagnosis. A workflow
  contract test rejects the source-manifest check. The next immutable release
  version is `2.2.5`.

## Android apksigner output compatibility (2026-08-09)

- The immutable `v2.2.5` run passed the merged-manifest checks and proved the
  APK had one valid V2 signature with the pinned certificate fingerprint. The
  independent evidence script nevertheless rejected it because newer Android
  Build Tools label the digest line `V2 Signer:` while its parser recognized
  only the older `Signer #1` label.
- The verifier now accepts both documented output families, including dotted
  scheme labels such as `V3.1 Signer:`, normalizes the digest, and requires at
  least one parsed identity with the complete distinct-identity set exactly
  equal to the pinned fingerprint. Regression tests cover old/current labels
  and reject mixed expected/unexpected fingerprints. The next immutable release
  version is `2.2.6`.

## Android receiver identity disambiguation (2026-08-09)

- The immutable `v2.2.6` run passed signing-certificate parsing, then the
  evidence verifier rejected receiver wiring even though the workflow's merged
  manifest checks passed. Offline inspection of the successful Android PR APK
  showed all five intended receivers plus WorkManager's separate
  `androidx.work.impl.background.systemalarm.RescheduleReceiver`. The verifier
  matched simple-name suffixes, so it incorrectly counted two receivers named
  `RescheduleReceiver`.
- Verification now requires each of the five exact fully-qualified receiver
  class names exactly once. Unrelated AndroidX receivers no longer collide,
  while missing, duplicated, or lookalike UTHelper/plugin identities still
  fail. The regression fixture includes the real WorkManager collision. The
  next immutable release version is `2.2.7`.

## Headless Windows trust import (2026-08-09)

- The immutable `v2.2.7` run completed Android and iOS successfully and passed
  the packaged Windows single-instance E2E, then timed out for five minutes in
  the certificate step. Its log stopped at `Import-Certificate` into the
  current-user Root store, consistent with a root-trust confirmation UI that a
  hosted runner cannot answer.
- The release job now imports the already SHA-256/subject-validated public leaf
  with non-interactive `certutil.exe -user -f -addstore`, checks the command exit
  code, resolves the exact SHA-1 thumbprint from the store, and repeats the
  SHA-256/subject validation before exporting cleanup state. The always-run
  cleanup uses headless `certutil -delstore` to remove only that exact
  thumbprint; a local import/identity/delete round trip passed and confirmed the
  PowerShell provider removal is subject to the same UI restriction. A workflow
  contract test rejects both interactive cmdlet paths. The next immutable
  release version is `2.2.8`.

## Hosted Windows machine-root trust scope (2026-08-09)

- The immutable `v2.2.8` run completed Android and iOS, but `certutil -user`
  still timed out for five minutes in the same current-user Root store. The
  local round trip proves the certificate and command are valid; the repeated
  hosted-only behavior isolates the blocker to current-user root policy/UI.
- GitHub's disposable Windows runner executes as Administrator, so release
  verification now installs the already-pinned public leaf temporarily into
  `LocalMachine\Root` with non-interactive `certutil`, resolves and revalidates
  the exact machine-store thumbprint, then always deletes that exact identity
  from the same store. Progress markers bracket PFX decode, identity validation,
  public export, and trust import so any future timeout is attributable without
  weakening verification. The next immutable release version is `2.2.9`.

## Hosted Windows SDK SignTool discovery (2026-08-09)

- The immutable `v2.2.9` run proved the machine-root change: certificate decode,
  pinned identity validation, trust import, and exact cleanup all completed.
  Android and iOS also completed, but Windows then failed because the hosted
  runner provides `signtool.exe` in the Windows SDK without adding it to PATH.
- Release signing now prefers a PATH-resolved SignTool and otherwise discovers
  the newest x64 candidate under `Program Files (x86)\Windows Kits\10\bin`, the
  same hosted-runner-compatible pattern already used by the MSIX packager. A
  release contract test rejects regression to an unqualified executable name.
  The next immutable release version is `2.2.10`.

## Draft release identity capture (2026-08-09)

- The immutable `v2.2.10` run completed Android, iOS, Windows signing, native
  verification, attestations, and exact inventory assembly. Publishing stopped
  safely before any assets were public because GitHub's release-by-tag endpoint
  returned 404 for the newly created draft, so its numeric ID was never stored.
- Publication preflight now enumerates all releases, including drafts, for the
  exact tag. Draft creation uses the REST endpoint directly and captures the ID
  from the creation response before upload. The cleanup trap therefore retains
  an exact immutable identity even while the release is still a draft. The lone
  empty `v2.2.10` draft was identity-checked and removed. The next immutable
  release version is `2.2.11`.

## Repository documentation and diagnostics hygiene (2026-08-11)

- Maintained documentation is now indexed in `docs/README.md` and separated into
  API, architecture, guides, testing, ADRs, and historical archives. Completed
  tool-specific plans were preserved under `docs/archive/` with explicit archive
  notices instead of appearing to be active work.
- The refactoring log moved out of the repository root, and all live references
  in agent rules, README, and governance tests were updated. Two unreferenced
  v2.1.0 screenshots were removed from Git history's current tree.
- `scripts/debug_panel_test.py` and `scripts/notification_system_test.py` were
  removed after bounded execution proved they were stale, outside pytest
  collection, and failing against current async/module boundaries. Repeatable
  script entry points are now catalogued in `scripts/README.md`.
- Verification: 47 focused governance/redaction tests passed; `ruff check .`,
  `python -m compileall -q src tests scripts`, Markdown relative-link validation,
  and `git diff --check` passed. A wider local run reached 1269 passed and 25
  skipped; its seven failures were isolated to optional dependencies absent from
  the shell environment (`flet_uth_background_sync` and the diagnostics SDK).

## Gitflow governance correction (2026-08-11)

- Research against Vincent Driessen's original versioned-release model and
  GitHub's current ruleset/merge documentation confirmed that long-lived
  `main`/`develop` integration requires merge ancestry. The previous linear,
  squash/rebase policy duplicated equivalent release commits and made a later
  `develop -> main` promotion conflict.
- The repository policy now treats `main` as production, `develop` as integration,
  sends routine work to `develop`, promotes releases from `develop` to `main`, and
  sends hotfixes from `main` back to `develop`. Protected PRs use merge commits.
- Policy-as-code renders and audits repository merge settings plus the protected
  branch ruleset. CI validates PR direction. Review, CODEOWNER approval, resolved
  threads, strict checks, deletion protection, and non-fast-forward protection
  remain fail-closed; only the incompatible linear-history rule is removed.
