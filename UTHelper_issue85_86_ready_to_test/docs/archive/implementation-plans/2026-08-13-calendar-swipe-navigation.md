# Calendar Swipe Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native touch swiping and primary-mouse click-drag navigation between adjacent months or weeks inside the calendar content area.

**Architecture:** Keep logical period arithmetic in a small pure module and render a bounded three-slot `PageView` around the current anchor. Native `PageView` physics handle touch; a mouse-only `GestureDetector` translates accepted drags into the same pager transition used by header arrows. Header and selected-day details remain outside the pager.

**Tech Stack:** Python 3.11+, Flet 0.86.5, pytest, Ruff, Playwright renderer smoke tests.

## Global Constraints

- Gesture capture applies only to the month grid or week strip, never the selected-day activity list.
- Touch uses native `PageView` behavior on Android and iOS.
- Primary-mouse drag works on Windows through a mouse-only fallback.
- Left advances one period; right goes back one period.
- Mouse acceptance is 52 logical pixels of travel or 700 logical pixels/second of release velocity in a consistent direction.
- Existing previous, next, today, and month/week controls remain available and accessible.
- Only three period pages may be retained at a time.
- Navigation performs no network request.
- Implementation follows red-green-refactor; every production behavior is preceded by a failing test.

---

## File structure

- Create `src/gui/components/calendar_navigation.py`: pure month/week arithmetic and mouse-gesture resolution; no Flet dependency.
- Modify `src/gui/components/calendar_view.py`: three-page builders, pager lifecycle, mouse adapter, button integration, theme refresh.
- Create `tests/test_calendar_navigation.py`: hand-derived unit cases for arithmetic and gesture decisions.
- Modify `tests/test_calendar_week_view.py`: component-level pager, selection, mode, and theme behavior.
- Modify `docs/architecture/refactoring-log.md`: implementation and verification record.

### Task 1: Pure period and mouse-navigation policy

**Files:**
- Create: `src/gui/components/calendar_navigation.py`
- Create: `tests/test_calendar_navigation.py`

**Interfaces:**
- Produces: `MonthAnchor(year: int, month: int)`.
- Produces: `shift_month(anchor: MonthAnchor, delta: int) -> MonthAnchor`.
- Produces: `month_slot_anchors(anchor: MonthAnchor) -> tuple[MonthAnchor, MonthAnchor, MonthAnchor]`.
- Produces: `shift_week(week_start: date, delta: int) -> date`.
- Produces: `week_slot_anchors(week_start: date) -> tuple[date, date, date]`.
- Produces: `resolve_mouse_navigation(distance_x: float, velocity_x: float | None, *, distance_threshold: float = 52.0, velocity_threshold: float = 700.0) -> int`; returns `-1` for previous, `1` for next, and `0` for cancellation.

- [ ] **Step 1: Write failing arithmetic tests**

```python
from datetime import date

from gui.components.calendar_navigation import (
    MonthAnchor,
    month_slot_anchors,
    shift_month,
    shift_week,
    week_slot_anchors,
)


def test_shift_month_crosses_both_year_boundaries():
    assert shift_month(MonthAnchor(2026, 12), 1) == MonthAnchor(2027, 1)
    assert shift_month(MonthAnchor(2026, 1), -1) == MonthAnchor(2025, 12)


def test_period_slots_are_previous_current_next():
    assert month_slot_anchors(MonthAnchor(2026, 1)) == (
        MonthAnchor(2025, 12), MonthAnchor(2026, 1), MonthAnchor(2026, 2)
    )
    assert week_slot_anchors(date(2026, 12, 28)) == (
        date(2026, 12, 21), date(2026, 12, 28), date(2027, 1, 4)
    )


def test_shift_week_stays_at_representable_date_boundaries():
    assert shift_week(date.min, -1) == date.min
    assert shift_week(date.max, 1) == date.max
```

- [ ] **Step 2: Run arithmetic tests and verify RED**

Run: `python -m pytest tests/test_calendar_navigation.py -q --tb=short`

Expected: collection fails because `gui.components.calendar_navigation` does not exist.

- [ ] **Step 3: Implement minimal period arithmetic**

```python
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class MonthAnchor:
    year: int
    month: int


def shift_month(anchor: MonthAnchor, delta: int) -> MonthAnchor:
    ordinal = anchor.year * 12 + anchor.month - 1 + delta
    year, zero_based_month = divmod(ordinal, 12)
    if not 1 <= year <= 9999:
        return anchor
    return MonthAnchor(year, zero_based_month + 1)


def month_slot_anchors(anchor: MonthAnchor):
    return shift_month(anchor, -1), anchor, shift_month(anchor, 1)


def shift_week(week_start: date, delta: int) -> date:
    try:
        return week_start + timedelta(weeks=delta)
    except OverflowError:
        return week_start


def week_slot_anchors(week_start: date):
    return shift_week(week_start, -1), week_start, shift_week(week_start, 1)
```

- [ ] **Step 4: Run arithmetic tests and verify GREEN**

Run: `python -m pytest tests/test_calendar_navigation.py -q --tb=short`

Expected: PASS.

- [ ] **Step 5: Write failing gesture-policy tests**

```python
import pytest

from gui.components.calendar_navigation import resolve_mouse_navigation


@pytest.mark.parametrize(
    ("distance", "velocity", "expected"),
    [
        (-60, -100, 1),
        (60, 100, -1),
        (-8, -800, 1),
        (8, 800, -1),
        (20, 200, 0),
        (60, -800, 0),
        (0, None, 0),
    ],
)
def test_mouse_navigation_requires_distance_or_consistent_flick(
    distance, velocity, expected
):
    assert resolve_mouse_navigation(distance, velocity) == expected
```

- [ ] **Step 6: Run gesture-policy tests and verify RED**

Run: `python -m pytest tests/test_calendar_navigation.py -q --tb=short`

Expected: FAIL because `resolve_mouse_navigation` is missing.

- [ ] **Step 7: Implement the minimal gesture policy**

```python
def resolve_mouse_navigation(
    distance_x: float,
    velocity_x: float | None,
    *,
    distance_threshold: float = 52.0,
    velocity_threshold: float = 700.0,
) -> int:
    velocity_x = velocity_x or 0.0
    distance_direction = 0 if distance_x == 0 else (1 if distance_x > 0 else -1)
    velocity_direction = 0 if velocity_x == 0 else (1 if velocity_x > 0 else -1)
    if distance_direction and velocity_direction and distance_direction != velocity_direction:
        return 0
    pointer_direction = distance_direction or velocity_direction
    accepted = (
        abs(distance_x) >= distance_threshold
        or abs(velocity_x) >= velocity_threshold
    )
    return -pointer_direction if accepted else 0
```

- [ ] **Step 8: Verify Task 1 and commit**

Run: `python -m pytest tests/test_calendar_navigation.py -q --tb=short`

Run: `python -m ruff check src/gui/components/calendar_navigation.py tests/test_calendar_navigation.py`

Expected: all pass.

```powershell
git add src/gui/components/calendar_navigation.py tests/test_calendar_navigation.py
git commit -m "feat: add calendar period navigation policy"
```

### Task 2: Explicit anchored month and week page builders

**Files:**
- Modify: `src/gui/components/calendar_view.py:28-530`
- Modify: `tests/test_calendar_week_view.py`

**Interfaces:**
- Consumes: `MonthAnchor`, `month_slot_anchors`, and `week_slot_anchors` from Task 1.
- Produces internal dataclass `_CalendarPeriodPage(anchor, control, day_cells, week_cells)`.
- Produces `_build_month_period_page(anchor: MonthAnchor) -> _CalendarPeriodPage`.
- Produces `_build_week_period_page(week_start: date) -> _CalendarPeriodPage`.
- Produces `_seed_period_pages() -> None`, which always creates exactly three pages.

- [ ] **Step 1: Write failing builder tests**

Add to `tests/test_calendar_week_view.py`:

```python
from datetime import date
from types import SimpleNamespace

from gui.components.calendar_navigation import MonthAnchor
from gui.components.calendar_navigation import month_slot_anchors
from gui.components.calendar_view import CalendarView


def _page_stub():
    return SimpleNamespace(update=lambda: None, run_task=lambda *_args: None)


def test_month_period_cells_keep_absolute_dates_after_anchor_changes():
    view = CalendarView(_page_stub())
    period = view._build_month_period_page(MonthAnchor(2026, 8))
    dated_cells = [
        cell for row in period.day_cells for cell in row if cell.data["date"]
    ]

    view._year, view._month = 2027, 1

    assert dated_cells[0].data["date"] == date(2026, 8, 1)
    assert dated_cells[-1].data["date"] == date(2026, 8, 31)


def test_seeding_month_and_week_modes_always_builds_three_slots():
    view = CalendarView(_page_stub())
    assert [page.anchor for page in view._period_pages] == list(
        month_slot_anchors(MonthAnchor(view._year, view._month))
    )

    view._toggle_view_mode("week")

    assert len(view._period_pages) == 3
    assert view._period_pages[1].anchor == view._week_start
```

- [ ] **Step 2: Run builder tests and verify RED**

Run: `python -m pytest tests/test_calendar_week_view.py -q --tb=short`

Expected: FAIL because `_CalendarPeriodPage`, `_build_month_period_page`, and `_period_pages` do not exist.

- [ ] **Step 3: Introduce the internal page model and explicit builders**

Add near the top of `calendar_view.py`:

```python
from dataclasses import dataclass, field

from gui.components.calendar_navigation import (
    MonthAnchor,
    month_slot_anchors,
    shift_month,
    shift_week,
    week_slot_anchors,
)


@dataclass(slots=True)
class _CalendarPeriodPage:
    anchor: MonthAnchor | date
    control: ft.Control
    day_cells: list[list[ft.Container]] = field(default_factory=list)
    week_cells: list[ft.Container] = field(default_factory=list)
```

Refactor cell construction so each callback reads `cell.data["date"]` instead
of deriving a day from mutable `_year` and `_month`. Build each month page with
its own weekday row and 42 cells, and each week page with seven cells. Preserve
the existing urgency dots, today state, selection state, dimensions, colors,
and click behavior.

Implement seeding as:

```python
def _seed_period_pages(self) -> None:
    if self._view_mode == "month":
        anchors = month_slot_anchors(MonthAnchor(self._year, self._month))
        self._period_pages = [self._build_month_period_page(a) for a in anchors]
    else:
        anchors = week_slot_anchors(self._week_start)
        self._period_pages = [self._build_week_period_page(a) for a in anchors]
```

Keep `_render_month()` and `_render_week()` as compatibility entry points that
refresh header/detail state and reseed the three pages. Remove the single-grid
mutable `_day_cells`/`_week_cells` assumptions only after all call sites use the
period-page collections.

- [ ] **Step 4: Run builder and existing calendar tests**

Run: `python -m pytest tests/test_calendar_week_view.py tests/test_detail_theme.py tests/test_theme_propagation.py -q --tb=short`

Expected: PASS.

- [ ] **Step 5: Refactor duplicate month/week cell styling while green**

Extract only repeated date-cell population logic needed by both adjacent slots.
Do not change sizes, Vietnamese labels, urgency semantics, or the selected-day
panel.

- [ ] **Step 6: Re-run and commit Task 2**

Run: `python -m pytest tests/test_calendar_week_view.py tests/test_theme_propagation.py -q --tb=short`

Run: `python -m ruff check src/gui/components/calendar_view.py tests/test_calendar_week_view.py`

```powershell
git add src/gui/components/calendar_view.py tests/test_calendar_week_view.py
git commit -m "refactor: build anchored calendar period pages"
```

### Task 3: Three-slot PageView state machine

**Files:**
- Modify: `src/gui/components/calendar_view.py:76-315, 531-919`
- Modify: `tests/test_calendar_week_view.py`

**Interfaces:**
- Consumes: `_seed_period_pages()` from Task 2.
- Produces `_period_pager: ft.PageView` with center index `1`.
- Produces async `_on_period_page_change(event) -> None`.
- Produces `_apply_period_delta(delta: int) -> bool` for one logical state advance.
- Produces async `_recenter_period_pager() -> None`.

- [ ] **Step 1: Write failing pager-state tests**

```python
import asyncio
from types import SimpleNamespace


def test_page_change_advances_month_once_and_reseeds_center():
    view = CalendarView(_page_stub())
    view._year, view._month = 2026, 12
    view._selected_day = 20
    view._seed_period_pages()
    jumps = []

    async def record_jump(index):
        jumps.append(index)

    view._period_pager.jump_to_page = record_jump

    asyncio.run(view._on_period_page_change(SimpleNamespace(data="2")))

    assert (view._year, view._month) == (2027, 1)
    assert view._selected_day is None
    assert jumps == [1]
    assert view._period_transition_active is False


def test_recenter_event_does_not_advance_a_second_time():
    view = CalendarView(_page_stub())
    view._pager_recentering = True
    before = (view._year, view._month)

    asyncio.run(view._on_period_page_change(SimpleNamespace(data="1")))

    assert (view._year, view._month) == before
```

- [ ] **Step 2: Run pager tests and verify RED**

Run: `python -m pytest tests/test_calendar_week_view.py -q --tb=short`

Expected: FAIL because the pager and state-machine methods do not exist.

- [ ] **Step 3: Create the native pager**

In `_build_ui()` replace the direct grid/week controls with:

```python
self._period_transition_active = False
self._pager_recentering = False
self._period_pager = ft.PageView(
    controls=[],
    selected_index=1,
    horizontal=True,
    snap=True,
    implicit_scrolling=True,
    keep_page=False,
    pad_ends=False,
    viewport_fraction=1.0,
    on_change=self._on_period_page_change,
)
```

After `_seed_period_pages()`, assign:

```python
self._period_pager.controls = [page.control for page in self._period_pages]
self._period_pager.selected_index = 1
```

- [ ] **Step 4: Implement one-shot state advance and recenter**

```python
def _apply_period_delta(self, delta: int) -> bool:
    if delta not in (-1, 1):
        return False
    if self._view_mode == "month":
        current = MonthAnchor(self._year, self._month)
        target = shift_month(current, delta)
        if target == current:
            return False
        self._year, self._month = target.year, target.month
        self._selected_day = None
    else:
        target = shift_week(self._week_start, delta)
        if target == self._week_start:
            return False
        self._week_start = target
        self._selected_date = None
        self._selected_day = None
    return True
```

`_on_period_page_change()` must accept only indices 0 and 2, set the transition
guard, advance exactly once, refresh header/detail state, reseed slots, await
`jump_to_page(1)`, and clear both guards in `finally`. Index 1 caused by the
programmatic recenter is ignored.

- [ ] **Step 5: Run pager tests and verify GREEN**

Run: `python -m pytest tests/test_calendar_week_view.py -q --tb=short`

Expected: PASS.

- [ ] **Step 6: Add rapid-input regression and commit**

Add a test that invokes a second page-change while
`_period_transition_active=True` and asserts the anchor is unchanged.

Run: `python -m pytest tests/test_calendar_week_view.py -q --tb=short`

```powershell
git add src/gui/components/calendar_view.py tests/test_calendar_week_view.py
git commit -m "feat: add native calendar period pager"
```

### Task 4: Mouse drag adapter and unified arrow animation

**Files:**
- Modify: `src/gui/components/calendar_view.py:76-145, 805-844`
- Modify: `tests/test_calendar_week_view.py`

**Interfaces:**
- Consumes: `resolve_mouse_navigation()` from Task 1.
- Produces `_period_gesture: ft.GestureDetector`, restricted to `PointerDeviceType.MOUSE`.
- Produces `_on_mouse_drag_start`, `_on_mouse_drag_update`, `_on_mouse_drag_cancel`, and async `_on_mouse_drag_end`.
- Produces async `_request_period_navigation(delta: int) -> None`, used by mouse and arrow buttons.

- [ ] **Step 1: Write failing gesture-adapter tests**

```python
import asyncio
from types import SimpleNamespace


def test_mouse_drag_accumulates_primary_delta_and_requests_next_page():
    view = CalendarView(_page_stub())
    requests = []

    async def record(delta):
        requests.append(delta)

    view._request_period_navigation = record
    view._on_mouse_drag_start(None)
    view._on_mouse_drag_update(SimpleNamespace(primary_delta=-30))
    view._on_mouse_drag_update(SimpleNamespace(primary_delta=-28))
    asyncio.run(
        view._on_mouse_drag_end(SimpleNamespace(primary_velocity=-120))
    )

    assert requests == [1]
    assert view._mouse_drag_distance == 0.0


def test_short_mouse_drag_cancels_without_navigation():
    view = CalendarView(_page_stub())
    requests = []
    view._request_period_navigation = lambda delta: requests.append(delta)
    view._on_mouse_drag_start(None)
    view._on_mouse_drag_update(SimpleNamespace(primary_delta=12))
    asyncio.run(view._on_mouse_drag_end(SimpleNamespace(primary_velocity=50)))
    assert requests == []
```

- [ ] **Step 2: Run mouse tests and verify RED**

Run: `python -m pytest tests/test_calendar_week_view.py -q --tb=short`

Expected: FAIL because mouse gesture state and handlers do not exist.

- [ ] **Step 3: Wrap only the period pager with a mouse detector**

```python
self._period_gesture = ft.GestureDetector(
    content=self._period_pager,
    allowed_devices=[ft.PointerDeviceType.MOUSE],
    mouse_cursor=ft.MouseCursor.GRAB,
    drag_interval=16,
    exclude_from_semantics=True,
    on_horizontal_drag_start=self._on_mouse_drag_start,
    on_horizontal_drag_update=self._on_mouse_drag_update,
    on_horizontal_drag_end=self._on_mouse_drag_end,
    on_horizontal_drag_cancel=self._on_mouse_drag_cancel,
)
```

Place `_period_gesture` between the mode toggle and detail panel. Do not wrap the
header, toggle, or detail panel.

- [ ] **Step 4: Implement mouse accumulation and cancellation**

Reset distance on start/cancel. Add `event.primary_delta or 0.0` on update. At
end, call `resolve_mouse_navigation(total, event.primary_velocity)`, reset state
before awaiting navigation, and do nothing for result `0`.

- [ ] **Step 5: Unify arrows through the pager**

Replace synchronous header lambdas with async handlers that call:

```python
async def _request_period_navigation(self, delta: int) -> None:
    if self._period_transition_active or delta not in (-1, 1):
        return
    target_index = 0 if delta < 0 else 2
    await self._period_pager.go_to_page(
        target_index,
        animation_duration=ft.Duration(milliseconds=220),
        animation_curve=ft.AnimationCurve.EASE_OUT_CUBIC,
    )
```

Before calling `go_to_page`, check `self._period_pager.page`. When it is `None`,
call `_apply_period_delta(delta)`, refresh the external header/detail state, and
reseed synchronously. When it is mounted, await `go_to_page` and let unexpected
runtime exceptions propagate to the existing application diagnostics.

- [ ] **Step 6: Verify gesture scope and arrow behavior**

Add assertions that `_period_gesture.content is _period_pager`, the detail
panel is a sibling outside the detector, and both arrow handlers request the
correct target slot.

Run: `python -m pytest tests/test_calendar_navigation.py tests/test_calendar_week_view.py -q --tb=short`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

```powershell
git add src/gui/components/calendar_view.py tests/test_calendar_week_view.py
git commit -m "feat: add mouse drag calendar navigation"
```

### Task 5: Today, mode switching, theme propagation, and lifecycle regression

**Files:**
- Modify: `src/gui/components/calendar_view.py:531-919, 1073-1145`
- Modify: `tests/test_calendar_week_view.py`
- Modify: `tests/test_theme_propagation.py`

**Interfaces:**
- Consumes the pager seeding and guards from Tasks 2-4.
- Produces `_reset_period_pager()` used by Today, mode changes, and data refresh.
- Preserves `CalendarView.update_theme()` across all three buffered pages.

- [ ] **Step 1: Write failing Today/mode/theme tests**

```python
def test_today_and_mode_switch_reseed_the_center_slot():
    view = CalendarView(_page_stub())
    view._year, view._month = 2024, 1
    view._go_today()
    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == MonthAnchor(
        date.today().year, date.today().month
    )

    view._toggle_view_mode("week")
    assert view._period_pager.selected_index == 1
    assert view._period_pages[1].anchor == view._week_start


def test_live_theme_refresh_updates_all_three_calendar_pages():
    apply_theme("midnight_blue")
    view = CalendarView(_page_stub())
    apply_theme("steel_light")
    view.update_theme()
    for period in view._period_pages:
        assert period.control.bgcolor in (None, C.BG)
        for row in period.day_cells:
            for cell in row:
                assert cell.data["day_text"].color in (
                    C.TEXT_PRIMARY, C.TEXT_SECONDARY, C.CRITICAL
                )
```

Restore the original theme in `finally` as existing theme tests do.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_calendar_week_view.py tests/test_theme_propagation.py -q --tb=short`

Expected: FAIL because Today/mode/theme paths do not yet manage buffered pages.

- [ ] **Step 3: Implement deterministic pager reset**

`_reset_period_pager()` must clear transition and mouse state, reseed three
pages, replace pager controls, and set `selected_index=1` without animation.
Call it from `_go_today()`, `_toggle_view_mode()`, `update_data()`, and
compatibility render entry points when the logical anchor changes outside a
pager event.

- [ ] **Step 4: Extend theme refresh across pager slots**

Include `_period_pager`, `_period_gesture`, and each
`_CalendarPeriodPage.control` in semantic refresh roots. Reapply state-dependent
today, selection, deadline, and Sunday colors by rerendering all three slots
after semantic remapping.

- [ ] **Step 5: Run focused and broad GUI tests**

Run: `python -m pytest tests/test_calendar_navigation.py tests/test_calendar_week_view.py tests/test_theme_propagation.py tests/test_gui_app_controller.py -q --tb=short`

Run: `python -m ruff check src/gui/components/calendar_navigation.py src/gui/components/calendar_view.py tests/test_calendar_navigation.py tests/test_calendar_week_view.py tests/test_theme_propagation.py`

Expected: all pass.

- [ ] **Step 6: Commit Task 5**

```powershell
git add src/gui/components/calendar_view.py tests/test_calendar_week_view.py tests/test_theme_propagation.py
git commit -m "fix: synchronize calendar pager lifecycle"
```

### Task 6: Renderer verification, documentation, and full quality gate

**Files:**
- Modify: `docs/architecture/refactoring-log.md`

**Interfaces:**
- Verifies all acceptance criteria from the design spec.
- Produces no new runtime interface.

- [ ] **Step 1: Run a real mobile renderer smoke**

Start the app in Flet web mode with `UTHELPER_UI_PLATFORM=android` and a unique
port. Use Playwright with a 390×844 viewport to:

1. Open Calendar.
2. Drag left across the month grid and verify the header advances one month.
3. Drag right and verify it returns.
4. Toggle Week, drag left, and verify the header advances one week.
5. Vertically scroll the selected-day list and verify the period does not change.
6. Tap a date after a cancelled short drag and verify selection still changes.

Capture screenshots before and after month/week navigation in the system temp
directory. Stop the web process in `finally`.

- [ ] **Step 2: Run a real desktop mouse renderer smoke**

Run the same app with `UTHELPER_UI_PLATFORM=desktop`. Use primary-button
`mouse.down()`, several horizontal `mouse.move()` calls, and `mouse.up()` across
the pager. Verify accepted left/right drags and a cancelled 20-pixel drag.

- [ ] **Step 3: Run the complete automated quality gate**

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'
python -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short
python -m ruff check src tests extensions/flet_uth_background_sync
python -m compileall -q src extensions/flet_uth_background_sync/src
git diff --check
```

Expected: zero failures and zero Ruff/compile/diff errors.

- [ ] **Step 4: Update the refactoring log with evidence**

Add a dated section documenting:

- Three-slot native pager architecture.
- Mouse fallback and exact thresholds.
- Gesture scope and accessibility fallback.
- Renderer smoke platforms and captured scenarios.
- Final pytest passed/skipped counts and quality-gate result.
- Physical Android/iOS verification remains a release-candidate step if no
  emulator or device is available.

- [ ] **Step 5: Re-run documentation checks and commit**

Run: `git diff --check`

Run the focused calendar tests once more after documentation changes:

`python -m pytest tests/test_calendar_navigation.py tests/test_calendar_week_view.py tests/test_theme_propagation.py -q --tb=short`

```powershell
git add docs/architecture/refactoring-log.md
git commit -m "docs: record calendar swipe verification"
```

## Completion checklist

- [ ] Every new behavior was introduced by a test observed failing for the expected reason.
- [ ] Touch month and week swipes use native pager physics.
- [ ] Desktop primary-mouse drag shares the same navigation pipeline.
- [ ] Short drags preserve date clicks.
- [ ] The selected-day list is outside gesture capture.
- [ ] Arrow, Today, and mode controls remain correct.
- [ ] Rapid input cannot double-advance.
- [ ] Theme refresh covers all three slots.
- [ ] Only three pages are retained and no navigation fetches data.
- [ ] Full test, Ruff, compile, renderer smoke, and diff checks have fresh evidence.
