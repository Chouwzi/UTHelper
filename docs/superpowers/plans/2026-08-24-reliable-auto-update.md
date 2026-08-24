# Reliable Auto-Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The user explicitly prohibited subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver truthful, resumable, single-transfer application updates with reusable verified artifacts and an Android local-APK handoff.

**Architecture:** Python owns release discovery and package transfer for Windows and Android. The coordinator owns a serialized state machine and retains verified artifacts; Android native code consumes the local APK solely for authoritative identity/signature validation and Package Installer handoff.

**Tech Stack:** Python 3.14, pytest, urllib, Flet service bridge, Dart MethodChannel, Kotlin/Android PackageManager and FileProvider.

**Spec:** `docs/superpowers/specs/2026-08-24-reliable-auto-update-design.md`

## Global Constraints

- No subagents.
- Installation always requires explicit user confirmation.
- Exact manifest target selection and compiled Windows signer pinning remain fail-closed.
- Android must retain package ID, versionCode, and installed-signer continuity checks.
- All network and lifecycle waits remain bounded.
- Production changes follow red-green-refactor TDD.
- Each independently reviewable behavior is committed with its tests.

---

### Task 1: Benchmark and discovery result correctness

**Files:**
- Modify: `tests/test_update_checker.py`
- Modify: `tests/test_update_coordinator.py`
- Modify: `src/core/update_checker.py`
- Create: `docs/testing/update-performance.md`

**Interfaces:**
- Produces: `UpdateDiscoveryError(UpdateError)` and truthful
  `GitHubReleaseClient.fetch_candidate(...) -> UpdateCandidate | None`.

- [ ] Add a test proving a socket timeout raises `UpdateDiscoveryError` instead
  of returning `None`.
- [ ] Run that test and observe the current false `None` result.
- [ ] Add coordinator coverage proving discovery failure emits `FAILED`, while a
  genuine `None` still emits `UP_TO_DATE`.
- [ ] Implement typed discovery errors and bounded transient retry without
  retrying manifest validation failures.
- [ ] Run update checker/coordinator tests and commit.

### Task 2: Progress coalescing and resumable transfer

**Files:**
- Modify: `tests/test_update_checker.py`
- Modify: `src/core/update_checker.py`
- Create: `scripts/benchmark_update_pipeline.py`

**Interfaces:**
- Produces: `ProgressThrottler` callable used internally by
  `VerifiedDownloader.download(...)`.
- Preserves: `download(package, *, cancel, progress=None) -> Path`.

- [ ] Add failing tests for 0%/100%, bounded event count, retained partial after
  transient failure, valid `206 Content-Range` resume, ignored Range restart,
  and invalid partial cleanup.
- [ ] Run each focused test and verify the expected failure.
- [ ] Implement progress coalescing, package-bound sidecar state, safe Range
  validation, full-file final hash, and bounded retries.
- [ ] Add a deterministic benchmark comparing raw, percent-only, and combined
  throttling plus restart versus resume byte counts.
- [ ] Run focused tests and benchmark; commit code, tests, and baseline results.

### Task 3: Reuse verified artifacts and correct preference semantics

**Files:**
- Modify: `tests/test_update_coordinator.py`
- Modify: `tests/test_gui_app_controller.py`
- Modify: `src/core/update_coordinator.py`
- Modify: `src/gui/app_controller.py`

**Interfaces:**
- Produces: coordinator behavior where a same-candidate request with an existing
  verified path emits `READY_TO_INSTALL` without downloading.
- Produces: `cancel_ready_install()` only if the UI needs explicit artifact
  disposal; confirmation cancellation itself retains the artifact.

- [ ] Add failing test showing a second click after cancelled confirmation does
  not increment downloader calls.
- [ ] Add failing test showing disabling automatic discovery does not cancel a
  manual download.
- [ ] Add UI test showing a ready button reopens confirmation rather than
  requesting another transfer.
- [ ] Implement the minimum state and event/UI changes.
- [ ] Run coordinator and GUI tests; commit.

### Task 4: Android verified local APK handoff

**Files:**
- Modify: `tests/test_gui_app_controller.py`
- Modify: `tests/test_background_sync_bridge.py`
- Modify: `extensions/flet_uth_background_sync/tests/test_package_contract.py`
- Modify: `src/gui/app_controller.py`
- Modify: `src/platform_utils/background_sync.py`
- Modify: `extensions/flet_uth_background_sync/src/flet_uth_background_sync/background_sync.py`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/main/kotlin/com/uthelper/backgroundsync/UthBackgroundSyncPlugin.kt`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/main/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstaller.kt`
- Modify: `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/test/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstallerTest.kt`

**Interfaces:**
- Changes bridge input from remote `url` to verified `local_path`.
- Produces native `verifyLocalAndOpen(...) -> InstallUpdateResult`.

- [ ] Add failing Python contract tests that require `local_path` and forbid the
  package URL at the native boundary.
- [ ] Add failing launcher tests showing scheduling is not reported as installer
  success before the native result.
- [ ] Add Kotlin tests for staged local-byte hash/size validation and retained
  archive identity checks.
- [ ] Replace native HTTP download with bounded local copy/hash/identity verify,
  atomic staging, permission handling, and FileProvider launch.
- [ ] Update Python facade and plugin channel contract.
- [ ] Run Python bridge tests and available Kotlin tests; commit.

### Task 5: UI failure states, cleanup, and benchmark report

**Files:**
- Modify: `src/gui/app_controller.py`
- Modify: `tests/test_gui_app_controller.py`
- Modify: `docs/testing/update-performance.md`
- Modify: `README.md` if user-facing behavior is inaccurate.

**Interfaces:**
- Produces user-visible distinction between check failure, download failure,
  permission requirement, and installer opened.

- [ ] Add failing UI tests for discovery failure copy, permission retry, and
  banner retention until native `installer_opened`.
- [ ] Implement state-specific copy/actions and bounded cleanup.
- [ ] Run before/after benchmark and record exact command/environment/results.
- [ ] Run focused updater, bridge, GUI, and package tests; commit.

### Task 6: Full verification and final commit

**Files:**
- Modify only files required by failures found during verification.

**Interfaces:**
- Produces a clean named branch with all implementation commits.

- [ ] Run `py -3.14 -m pytest` with both project source roots.
- [ ] Run extension Python tests independently.
- [ ] Run available Android/Kotlin/Flutter tests or record the exact toolchain
  blocker without claiming they passed.
- [ ] Run the live metadata/download/Windows verification benchmark without
  launching an installer.
- [ ] Run `git diff --check` and inspect `git status` and commit history.
- [ ] Commit any final documentation/evidence update.
