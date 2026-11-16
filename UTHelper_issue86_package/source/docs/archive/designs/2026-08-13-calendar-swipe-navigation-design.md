# Calendar Swipe Navigation Design

Date: 2026-08-13

Status: Awaiting written-spec approval

Target branch: `feature/cross-platform-performance`

## Objective

Allow users to move between calendar periods by swiping on touch devices or
click-dragging with a primary mouse button on desktop. The interaction applies
only to the month grid or week strip. The selected-day activity panel remains a
vertical scrolling region and must not participate in horizontal navigation.

The result must feel native on Android and iOS, remain dependable on Windows,
and preserve the existing previous, next, and today buttons as accessible
alternatives.

## Research basis

- Flet `PageView` displays one child page at a time, supports native horizontal
  swiping, snapping, adjacent-page rendering, and programmatic animated
  navigation. It is the primary touch interaction primitive for this feature:
  <https://flet.dev/docs/controls/pageview/>.
- Flet `GestureDetector` recognizes primary-button horizontal drags and can
  restrict recognition to selected pointer device types. It provides the mouse
  fallback around the pager:
  <https://flet.dev/docs/controls/gesturedetector/>.
- Flutter's default desktop scroll behavior excludes mouse pointers from drag
  scrolling. A `PageView` alone therefore does not satisfy the Windows
  click-drag requirement:
  <https://docs.flutter.dev/release/breaking-changes/default-scroll-behavior-drag>.
- Apple recommends responsive gesture feedback and keeping a conventional,
  accessible alternative instead of making a gesture the only route. The
  existing arrow buttons remain available:
  <https://developer.apple.com/design/human-interface-guidelines/gestures/>.

## Chosen approach

Use a three-slot horizontal `PageView` containing the previous, current, and
next calendar periods. Native touch physics animate and snap between these
slots. After a completed page change, the view advances its logical anchor,
rebuilds the three slots around the new anchor, and silently recenters on slot
1. This creates an effectively unbounded calendar without retaining an
unbounded list of controls.

Wrap the pager in a mouse-only `GestureDetector`. It accumulates horizontal
distance and uses release velocity as a secondary acceptance signal. An
accepted mouse drag invokes the same animated `PageView` transition as the
header arrow buttons. Touch and stylus input are left to `PageView` so the two
gesture systems do not compete.

## Interaction contract

### Gesture area

- Month mode: weekday headings and the six-row month grid.
- Week mode: the seven-day week strip.
- Excluded: header, month/week toggle, and selected-day detail panel.

### Direction and acceptance

- Drag or swipe left advances one month in month mode or one week in week mode.
- Drag or swipe right moves back one month or one week.
- Native touch acceptance and snap behavior come from `PageView`.
- Mouse navigation is accepted when horizontal travel reaches 52 logical pixels
  or release speed reaches 700 logical pixels per second in a consistent
  direction.
- A short or slow mouse movement cancels without changing the period. Normal
  clicks continue to select a date.
- Only the primary mouse button participates.

### Feedback and concurrency

- Touch content follows the finger using the native `PageView` animation.
- Accepted mouse drags animate to the adjacent buffered page after release.
- Header title, summary, day-detail state, and period data update only after the
  adjacent page becomes active, avoiding labels that get ahead of animation.
- A transition lock ignores additional navigation requests until recentering is
  complete. Programmatic recenter events are distinguished from user changes so
  they cannot advance the logical period twice.
- Arrow buttons call the same previous/next pager operation and use the same
  animation duration and curve.

## State model

`CalendarView` remains the owner of logical calendar state:

- Month anchor: `_year` and `_month`.
- Week anchor: `_week_start`, always a Monday.
- Mode: `_view_mode` (`month` or `week`).
- Selection: `_selected_day` in month mode and `_selected_date` in week mode.

The pager adds presentation state only:

- Three slots with relative deltas `-1`, `0`, and `+1`.
- Center index `1`.
- `_period_transition_active` guard.
- `_pager_recentering` guard.
- Mouse accumulated distance and most recent velocity.

Pure period helpers calculate month and week anchors without touching Flet
controls. Month arithmetic must handle December/January and clamp only at
Python's representable date boundaries. Week arithmetic must handle month and
year crossings.

## Rendering architecture

Existing rendering logic is split into reusable period builders:

1. A month-page builder receives an explicit `(year, month)` and returns a new
   weekday/grid control tree. Each day cell stores its absolute date, so click
   handling does not depend on mutable global month state.
2. A week-page builder receives an explicit Monday date and returns a new
   seven-day strip. Each cell stores its absolute date.
3. A pager-seeding method builds the three pages around the current logical
   anchor and places the pager at index 1.
4. Existing header and selected-day detail rendering stays outside the pager.

Only three periods exist in the control tree at once. Rebuilding them after a
completed transition bounds memory usage and avoids maintaining duplicate
calendar state indefinitely.

## Navigation flow

### Touch swipe

1. Native `PageView` tracks and animates the gesture.
2. `on_change` reports index 0 or 2.
3. The view converts the index into delta `-1` or `+1`.
4. Logical month/week state advances once and the existing selection is cleared.
5. Header, summary, and selected-day panel refresh for the new period.
6. The three slots are rebuilt around the new anchor.
7. The pager jumps to center index 1 without animation and releases the lock.

### Mouse drag or arrow button

1. Mouse release passes or fails the threshold check; arrow buttons are already
   explicit navigation requests.
2. An accepted request animates the pager to slot 0 or 2.
3. The resulting `on_change` follows the same state-update and recenter flow as
   touch.

### Today and mode switching

- Today resets the logical anchors and selection, refreshes the external header
  and detail panel, then reseeds the pager at center.
- Switching between month and week preserves the existing synchronization rule:
  a selected absolute date becomes the new anchor when available. The pager is
  rebuilt for the new mode at center without presenting a horizontal period
  transition.

## Accessibility and platform behavior

- Previous, next, today, and month/week controls remain keyboard and screen
  reader accessible.
- The mouse `GestureDetector` is excluded from semantics because it supplements
  controls that already expose the same actions.
- Android and iOS use native `PageView` touch behavior.
- Windows uses the same pager animation, triggered by primary-button drag or the
  existing arrow buttons.
- No gesture begins in the screen-edge system-back area because the detector is
  constrained to the calendar content rather than the full page.

## Error handling and boundaries

- Ignore navigation while a transition/recenter cycle is active.
- Ignore cancelled mouse drags and reset accumulated gesture state.
- If period arithmetic reaches Python's minimum or maximum representable date,
  keep the current period and recenter without raising into the UI.
- If a pager method cannot run because the control is not mounted, fall back to
  the existing synchronous logical navigation and render path. This keeps unit
  tests and lifecycle transitions deterministic.
- Do not fetch network data during a swipe. Navigation uses the in-memory
  deadline map already supplied to `CalendarView`.

## Testing strategy

Implementation follows test-driven development.

### Unit tests

- Month delta across year boundaries.
- Week delta across month and year boundaries.
- Three-slot anchors for both modes.
- Mouse distance threshold, velocity threshold, direction, cancellation, and
  reset behavior.
- Transition and recenter guards prevent duplicate navigation.
- Selected-day state clears exactly once after period navigation.
- Today and mode changes reseed the pager correctly.
- Day cells retain absolute dates independent of later anchor mutation.

### Component tests

- Touch-style `on_change` from center to either adjacent page updates the correct
  logical period and recenters.
- Arrow buttons drive the same pager path.
- Mouse drag invokes pager animation only after acceptance.
- Detail-list vertical scrolling remains outside the gesture detector.
- Theme refresh reaches all three buffered pages.

### Renderer and platform verification

- Real Flet web renderer with mobile viewport: synthetic touch swipe in both
  directions for month and week modes.
- Real Flet desktop renderer: primary-button drag in both directions and a short
  cancelled drag.
- Confirm date-cell tapping still works after a cancelled and accepted gesture.
- Run the complete Python/extension test suite, Ruff, bytecode compilation, and
  `git diff --check`.
- Physical Android and iOS gesture checks remain release-candidate verification
  when those devices or emulators are available.

## Non-goals

- Pinch-to-zoom or changing between month/week via vertical gesture.
- Swiping the selected-day activity list.
- Infinite preloading or network fetching for adjacent periods.
- Removing header navigation buttons.
- Changing calendar visual styling, deadline semantics, or schedule data.

## Acceptance criteria

1. Touch swipes on the month grid move exactly one month with native snapping.
2. Touch swipes on the week strip move exactly one week with native snapping.
3. Primary mouse drag performs the same navigation on Windows.
4. Short mouse movement does not navigate or prevent a date click.
5. The activity list below the calendar remains vertically scrollable.
6. Header arrows, Today, and mode switching remain correct and animated where
   appropriate.
7. Rapid input cannot skip or double-advance periods.
8. Existing theme switching and date selection remain correct across all three
   pager slots.
9. The feature adds no network request and retains only three period pages.
