# Cross-platform Notification E2E Implementation Plan

> **Archived:** Planning snapshot retained for provenance. It is not the current
> task tracker; use tests and current operator documentation as the source of truth.

**Goal:** Make deadline reminders and related notification channels behave consistently on Windows, Android, and iOS, with automated evidence for the shared contract and platform adapters.

**Architecture:** Keep filtering, milestone, DND, deadline-revision, and deduplication rules in the platform-neutral Python policy. Windows continues to use its durable scheduler, Android continues to use AlarmManager/WorkManager, and iOS gains a native UserNotifications adapter that schedules local reminders after a successful foreground Moodle snapshot. When a native mobile adapter owns local delivery, Python still dispatches Discord, Telegram, and email without duplicating the local notification.

**Tech Stack:** Python 3.11+, pytest, Flet 0.86+, Flutter plugin bridge, Kotlin/Android AlarmManager, Swift/iOS UserNotifications, GitHub Actions macOS build.

## Global Constraints

- Deadline reminder input remains canonical minutes, including hour and minute milestones such as 60, 30, and 5.
- Android background synchronization remains WorkManager/AlarmManager based.
- Windows reminders remain durable while the tray process is running.
- iOS reminders use native local scheduling and remain deliverable when the app is backgrounded or terminated after a successful foreground sync.
- No claim of physical-device verification is made without a real device or simulator result.

---

### Task 1: Native/local channel routing

**Files:**
- Modify: `src/notifiers/manager.py`
- Modify: `src/gui/app_controller.py`
- Test: `tests/test_notification_manager_extended.py`
- Test: `tests/test_background_sync_bridge.py`

**Interfaces:**
- Consumes: `NotificationManager.dispatch(assignments)` and native import result dictionaries.
- Produces: `NotificationManager.dispatch(assignments, excluded_channels={"mobile"}) -> DispatchResult`, preserving external-channel delivery while native mobile scheduling owns local delivery.

- [ ] **Step 1: Write failing routing tests**

Add tests proving that excluding `mobile` suppresses only `MobileNotifier`, that Discord-like channels still receive a due reminder, and that native mobile results can be combined with the external dispatch result without double-counting local delivery.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_notification_manager_extended.py tests/test_background_sync_bridge.py -q`

Expected: FAIL because `dispatch()` does not accept `excluded_channels` and the native app branch skips all Python channels.

- [ ] **Step 3: Implement minimal routing support**

Filter notifier iteration using `_channel_cache_key()` and merge the native local result with the external-channel result in the app sync path.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/test_notification_manager_extended.py tests/test_background_sync_bridge.py -q`

Expected: PASS.

### Task 2: iOS native deadline scheduler

**Files:**
- Create: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/ios/Classes/UthBackgroundSyncPlugin.swift`
- Create: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/ios/flet_uth_background_sync.podspec`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/pubspec.yaml`
- Modify: `src/platform_utils/background_sync.py`
- Modify: `src/gui/app_controller.py`
- Test: `extensions/flet_uth_background_sync/tests/test_package_contract.py`
- Test: `tests/test_background_sync_bridge.py`

**Interfaces:**
- Consumes: existing `UthBackgroundSync` Flet methods `configure`, `import_activities`, `get_diagnostics`, `logout`, and `request_exact_alarm_access`.
- Produces: an iOS plugin using `UNUserNotificationCenter`, plus a mobile bridge returned for both `IS_ANDROID` and `IS_IOS`.

- [ ] **Step 1: Write failing iOS bridge and package-contract tests**

Test behavior at the Python boundary by setting the platform flags to iOS and asserting that the factory returns an available bridge with the same service contract. Add a package contract that validates Flutter recognizes the iOS plugin and that the podspec exposes the Swift source.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_background_sync_bridge.py extensions/flet_uth_background_sync/tests/test_package_contract.py -q`

Expected: FAIL because the factory is Android-only and the plugin has no iOS registration.

- [ ] **Step 3: Implement the iOS adapter**

Use `UNUserNotificationCenter` to request authorization, reconcile pending identifiers, schedule calendar triggers for future milestones, deliver one immediate crossed milestone per deadline revision, apply type/course/submission/DND rules, attach the Moodle URL to `userInfo`, open it on notification tap, expose diagnostics, and clear pending UTHelper requests on logout.

- [ ] **Step 4: Generalize mobile bridge creation**

Return the same Flet service facade on Android and iOS; keep the existing Android-facing alias for compatibility and initialize it from `AppController` on either mobile platform.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_background_sync_bridge.py extensions/flet_uth_background_sync/tests/test_package_contract.py -q`

Expected: PASS.

### Task 3: Cross-platform notification behavior matrix

**Files:**
- Create: `tests/test_notification_e2e_contract.py`
- Modify: `tests/test_notification_policy.py`

**Interfaces:**
- Consumes: `ActivityNotificationPolicy`, `NotificationManager`, `WindowsNotifier`, and the mobile bridge contract.
- Produces: table-driven regression coverage for minute/hour/day milestones, DND, submitted/muted/type filters, changed deadlines, cancellation, deduplication, tap payloads, and channel failure isolation.

- [ ] **Step 1: Add one failing edge-case test at a time**

Use literal timestamps and expected schedules for 5/30/60/180/1440/4320-minute reminders, cross-midnight DND, full-day DND, already-passed milestones, deadline moves, submission cancellation, malformed deadlines, and per-channel retry isolation.

- [ ] **Step 2: Verify each RED failure names a real contract break**

Run the individual pytest node ID after each test addition and confirm it fails because of missing behavior rather than fixture setup.

- [ ] **Step 3: Apply minimal policy/adapter fixes**

Change only the source path responsible for each observed failure and keep platform-specific mechanics out of the shared policy.

- [ ] **Step 4: Verify the matrix GREEN**

Run: `python -m pytest tests/test_notification_e2e_contract.py tests/test_notification_policy.py tests/test_windows_notifier.py -q`

Expected: PASS.

### Task 4: Build and device verification handoff

**Files:**
- Modify: `.github/workflows/build-ios.yml`
- Create: `docs/NOTIFICATION_E2E_MATRIX.md`

**Interfaces:**
- Consumes: repository build commands and platform diagnostics.
- Produces: CI contract checks before IPA creation and a repeatable device checklist with exact expected results.

- [ ] **Step 1: Add pre-build notification tests to iOS CI**

Install the project plus pytest, run the shared notification suite and extension contract tests, then build the unsigned IPA on macOS.

- [ ] **Step 2: Document the physical-device matrix**

Record foreground/background/terminated, permission denied/granted, restart/reboot, DND, deadline moved/removed/submitted, tap action, and 5/30/60-minute timing cases for Windows 10/11, Android 8+/12+/14+, and current iOS.

- [ ] **Step 3: Run local verification**

Run: `python -m pytest -q`, `ruff check src tests extensions/flet_uth_background_sync`, and the Android build/contract command available in the current environment.

Expected: all executable local checks pass; iOS runtime verification remains explicitly pending unless a macOS simulator/device result is available.

---

## Self-review

- Spec coverage: shared reminder rules, Windows, Android, iOS, integration channels, foreground/background/terminated states, permissions, taps, rescheduling, cancellation, and diagnostics are assigned to tasks.
- Placeholder scan: no deferred implementation placeholders are present; device-only checks are explicitly identified as environment-dependent evidence.
- Type consistency: native imports return dictionaries, Python dispatch returns `DispatchResult`, scheduling returns `ScheduleResult`, and the existing Flet service method names remain unchanged across Android and iOS.
