# Today Schedule Spotlight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the collapsed inline schedule disclosure with an automatic relevant-class spotlight and an adaptive list/detail surface with correct Back navigation.

**Architecture:** Keep Portal fetching and caching unchanged. Add a pure spotlight selector in `core.today_schedule`, then make `TodaySchedulePanel` own a single adaptive Flet surface and its list/detail navigation state. Let `AppController` delegate Back to that component before navigating other views.

**Tech Stack:** Python 3.11+, Flet 0.86.5, pytest

## Global Constraints

- Work only in the existing isolated `feature/cross-platform-performance` worktree.
- Use `platform_utils.UI_IS_MOBILE` for presentation and never runtime-only `IS_MOBILE`.
- Reuse `TodayScheduleCoordinator`; do not add Portal requests or dependencies.
- Use semantic colors from `gui.core.theme.C`; no literal application-theme colors.
- All new behavior follows test-first RED → GREEN → REFACTOR.

---

### Task 1: Relevant-session policy

**Files:**
- Modify: `src/core/today_schedule.py`
- Test: `tests/test_today_schedule.py`

**Interfaces:**
- Produces: `select_spotlight_session(sessions: tuple[ClassSession, ...], now: datetime) -> ClassSession | None`

- [x] **Step 1: Add table-driven failing tests** proving an in-progress class beats an upcoming class, the earliest upcoming class is selected, cancelled and finished classes are skipped, and `None` is returned when no relevant class remains.
- [x] **Step 2: Run** `pytest -q tests/test_today_schedule.py -k spotlight` and confirm failure because the selector is absent.
- [x] **Step 3: Implement** a pure selector that filters cancelled/finished sessions, prefers `IN_PROGRESS`, then sorts the selected phase by `(start_at, end_at, session_id)`.
- [x] **Step 4: Run** `pytest -q tests/test_today_schedule.py -k spotlight` and confirm all spotlight tests pass.

### Task 2: Spotlight card and information hierarchy

**Files:**
- Modify: `src/gui/components/today_schedule_panel.py`
- Test: `tests/test_today_schedule_panel.py`

**Interfaces:**
- Consumes: `select_spotlight_session(...)`
- Produces: `TodaySchedulePanel(page, on_need_data, mobile)` with `control`, `set_state()`, and `refresh_time_state()`.

- [x] **Step 1: Add failing component tests** asserting the dashboard immediately renders the selected subject/status, time, a separate `Phòng …` line, a separate campus line, and “Xem lịch hôm nay · N buổi”. Add an all-finished assertion for “Đã hoàn thành lịch học hôm nay”.
- [x] **Step 2: Run** `pytest -q tests/test_today_schedule_panel.py -k "spotlight or finished"` and verify the old collapsed header fails those assertions.
- [x] **Step 3: Replace the expandable header/body** with a spotlight state container, separate content and CTA hit areas, and explicit loading/auth/error/empty/completed states. Preserve cached-data subtitles during refresh.
- [x] **Step 4: Run** the focused tests and confirm they pass.

### Task 3: Adaptive list/detail surface

**Files:**
- Modify: `src/gui/components/today_schedule_panel.py`
- Test: `tests/test_today_schedule_panel.py`

**Interfaces:**
- Produces: `open_schedule()`, `open_detail(session, *, from_list: bool)`, `handle_back() -> bool`, and `surface_open: bool`.

- [x] **Step 1: Add failing tests** for mobile `BottomSheet`, desktop `AlertDialog`, chronological list rows with three metadata lines, spotlight-to-detail, list-to-detail, detail-to-list Back, direct-detail Back closing, and list Back closing.
- [x] **Step 2: Run** `pytest -q tests/test_today_schedule_panel.py -k "surface or detail or back"` and verify failures are due to missing surface APIs.
- [x] **Step 3: Implement one owned surface** whose content is replaced between list and detail. Use `page.show_dialog()` and `page.pop_dialog()`, a bounded scrollable mobile sheet, a scrollable desktop dialog, and 48 dp interaction targets.
- [x] **Step 4: Run** the focused tests and confirm all navigation and adaptive-presentation tests pass.

### Task 4: Controller Back integration and time transitions

**Files:**
- Modify: `src/gui/app_controller.py`
- Test: `tests/test_gui_app_controller.py`
- Test: `tests/test_today_schedule_panel.py`

**Interfaces:**
- Consumes: `TodaySchedulePanel.handle_back() -> bool`

- [x] **Step 1: Add a failing controller test** proving `_navigate_back()` delegates to an open schedule surface before settings/detail/calendar/grades and returns `True`.
- [x] **Step 2: Add a failing panel test** proving crossing an end boundary replaces the finished spotlight with the next upcoming class while an open selected detail retains its selected session and refreshes its phase.
- [x] **Step 3: Run** the two focused tests and confirm the old navigation/refresh behavior fails.
- [x] **Step 4: Inject `page` and `platform_utils.UI_IS_MOBILE` when constructing the panel, check `handle_back()` first in `_navigate_back()`, and make `refresh_time_state()` rebuild spotlight and open-surface content atomically.
- [x] **Step 5: Run** `pytest -q tests/test_today_schedule_panel.py tests/test_gui_app_controller.py` and confirm both files pass.

### Task 5: Theme and regression verification

**Files:**
- Modify: `tests/test_today_schedule_panel.py`
- Modify: `docs/architecture/refactoring-log.md`

**Interfaces:**
- Verifies all interfaces produced above.

- [x] **Step 1: Extend the theme test** to switch themes while the list and detail surface controls exist, then assert their semantic backgrounds, borders, primary/secondary text, and phase colors use current `C` values.
- [x] **Step 2: Run** `pytest -q tests/test_today_schedule_panel.py` and fix only production theme propagation exposed by the failing test.
- [x] **Step 3: Run focused quality gates:** `pytest -q tests/test_today_schedule.py tests/test_today_schedule_panel.py tests/test_gui_app_controller.py` and `ruff check src/core/today_schedule.py src/gui/components/today_schedule_panel.py src/gui/app_controller.py tests/test_today_schedule.py tests/test_today_schedule_panel.py tests/test_gui_app_controller.py`.
- [x] **Step 4: Run the full test suite** with `pytest -q` and record the result in `docs/architecture/refactoring-log.md`.
- [x] **Step 5: Review `git diff --check`, `git status --short`, and the final diff; ensure `.superpowers/` is not committed; then commit the implementation and tests.
