# Responsive Class Schedule Implementation Plan

> **For Codex:** Implement this plan in the schedule review worktree, preserving the current Portal fetch, account-scoped week cache, and notification paths.

**Goal:** Make the Portal timetable usable on a narrow screen and show the whole week at once on a desktop window.

**Architecture:** Keep the change in `ScheduleWeekView`. Use the control's measured width to select a single-day agenda below 920 px and a seven-day week layout above that width. Keep the selected calendar date through reflow, make narrow-screen previous/next controls move one day, and leave fetch/cache behavior keyed to Monday. Display subject, class code, time, and room in each summary; preserve the existing complete session details and links behind an explicit detail action.

**Tech Stack:** Python 3.14, Flet 0.86.5, pytest.

---

### Task 1: Lock down responsive schedule behavior

**Files:**
- Create: `tests/test_schedule_week_view.py`
- Test fixtures: deterministic `ClassSession` instances only; no Portal calls or real timetable data.

**Step 1: Add positive narrow and wide layout cases**

Use a small fake page with a mutable width and a no-op `run_task`. Seed the view's week map and live timestamp directly. Assert that a 390 px view shows only its selected date, while a 1120 px view exposes all seven weekday columns without a horizontal-scroll wrapper.

**Step 2: Add navigation and reflow regressions**

Assert that narrow previous/next navigation advances one day, wide navigation advances seven days, and changing width keeps the selected date while switching layouts. Include Sunday-to-Monday week-boundary behavior so the week fetch anchor remains correct.

**Step 3: Add empty/loading and content checks**

Assert that empty days and weeks have clear states, loading remains visible, and session summaries retain subject, class code, start/end time, and room. Assert the detail action is present for the existing lecturer, campus, notes, outline, and link details.

**Step 4: Run the focused tests and confirm the new cases fail before implementation**

Run: `python -m pytest tests/test_schedule_week_view.py -q --tb=short`

Expected before implementation: failures show the existing fixed-width grid lacks a single-day agenda, one-day navigation, and width-driven reflow.

### Task 2: Implement the responsive presentation

**Files:**
- Modify: `src/gui/components/schedule_week_view.py`

**Step 1: Add width-driven layout state**

Listen to Flet 0.86.5 `on_size_change`; use the measured width, with page width as the pre-mount fallback. Re-render only when the breakpoint or desktop column sizing changes. Keep navigation labels and buttons accessible with text/tooltip names.

**Step 2: Render the narrow agenda**

At widths below 920 px, show one selected day with a weekday/date heading, one-day previous/next navigation, Today and Refresh actions, and vertically stacked session summaries. Empty-day and loading messages remain visible. No horizontal schedule scroll is needed.

**Step 3: Render the desktop week**

At widths of 920 px or more, show seven day columns with concise session summaries. Set each column to `min(225, max(112, (width - 76) / 7))` to account for the 28 px content inset and six 8 px gaps, so the default 1120 px window fits all seven columns. Keep the whole body vertically scrollable.

**Step 4: Preserve complete session information**

Use an explicit “Chi tiết” action to open the existing full session fields, Zoom/LMS links, and outline action. Style all new controls with the established `C.*` theme tokens, system fonts, and square-corner card treatment already used by this view.

### Task 3: Verify the schedule paths and output

**Files:**
- Modify: `docs/architecture/refactoring-log.md`

**Step 1: Run focused source tests**

Run: `python -m pytest tests/test_schedule_week_view.py tests/test_calendar_week_view.py tests/test_week_schedule_cache.py tests/test_today_schedule_panel.py -q --tb=short`

Expected: all tests pass, including the existing Portal cache and dashboard schedule tests.

**Step 2: Run lint and whitespace checks on changed files**

Run: `ruff check src/gui/components/schedule_week_view.py tests/test_schedule_week_view.py`

Run: `git diff --check`

**Step 3: Inspect real Flet fixture output**

Use deterministic local fixtures to inspect a wide desktop window and a 390 px narrow window, long course names, empty/loading states, and light/dark themes. Record observed widths and touch-target sizes. Do not use live timetable data as acceptance evidence.

**Step 4: Record the bounded change and verification**

Append one dated entry to `docs/architecture/refactoring-log.md`, naming only the schedule component/tests and actual test/render results. Do not claim Android device or provider-live acceptance from a Windows preview.
