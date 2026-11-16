# Adaptive dashboard filter menu design

## Understanding summary

- Replace the three rigid dashboard selectors for urgency, activity type, and
  course with one reusable single-selection filter component.
- Keep an anchored popup on Windows, where pointer, hover, focus, and keyboard
  behavior are important.
- Present the same option model in a bottom sheet on Android and iOS, where
  larger touch targets and reliable scrolling are more important than spatial
  proximity to the trigger.
- Preserve the existing filtering semantics and apply a choice immediately.
- Keep counts, selected state, long labels, theme refresh, and accessibility
  consistent across all three selectors.
- Avoid expensive animation and unnecessary option-tree rebuilds on weak
  Android devices.
- Keep API/network access outside the component.

## Assumptions and non-goals

- Selection remains single-value; multi-select and an Apply button are out of
  scope.
- Desktop menus remain anchored under their trigger. Mobile sheets may be
  dismissed by the scrim or downward drag.
- Course search is not required for the current course scale; the mobile sheet
  is scrollable and the component model leaves room for search later.
- Animation targets 180 ms and must not run Python repaint loops.
- Options contain no secrets and are safe to expose to accessibility services.

## Approaches considered

1. **Recommended: adaptive renderer over one option/state model.** Desktop
   renders `PopupMenuButton`; mobile renders `BottomSheet` + `ListTile`. This
   keeps native interaction semantics while centralizing state and styling.
2. Keep three `PopupMenuButton` implementations and extract only style helpers.
   This is smaller initially but retains duplicated state/count/rebuild logic
   and gives weak mobile ergonomics.
3. Build a fully custom overlay for every platform. This maximizes visual
   control but recreates focus, dismissal, keyboard, screen-reader, and route
   behavior already handled by Flet/Flutter.

## Final design

`AdaptiveFilterMenu` owns an immutable tuple of `FilterMenuOption` values and a
selected key. It exposes one `control` for the dashboard row and methods to
replace options, update counts, select a key, and refresh theme colors. Option
updates use a structural signature, so unchanged data does not rebuild popup
items or the mobile sheet.

The trigger is a rounded filter chip with a stable minimum touch target,
ellipsis label, active tint, and animated arrow. A semantic label announces
the filter name and selected value. Desktop popup rows use the built-in checked
state, a leading color marker, trailing count, fixed 48-pixel height, bounded
width, elevation, and Flet's native popup animation. Mobile uses a safe-area,
draggable, scrollable bottom sheet with a heading and 52-pixel list rows; the
selected row gets a tinted background and trailing check.

The component emits only the selected key. `AppController` retains filtering
policy and asynchronously applies it. Course options are supplied through the
same model as static urgency/type options. If a selected dynamic course
disappears, the component and controller fall back to `all`.

## Reliability and testing

- Unit tests cover desktop/mobile renderer choice, selection, counts, long
  labels, unchanged-option reuse, missing-selection fallback, semantics, and
  immediate callback behavior.
- Controller tests cover all three integrations and dynamic course refresh.
- Existing filter-service tests remain the behavioral authority.
- A desktop smoke validates menu geometry and selection; mobile structure is
  verified without requiring a connected device.

## Decision log

- Chose adaptive popup/sheet presentation over one universal popup because
  pointer precision and mobile touch ergonomics differ materially.
- Chose native Flet modal/menu controls over a custom overlay to retain route,
  focus, keyboard, screen-reader, and dismissal behavior.
- Chose immediate single selection to preserve the current mental model and
  minimize interaction steps.
- Chose structural option signatures instead of rebuilding on every dashboard
  refresh to protect weak Android devices.
- Deferred course search until observed course scale warrants the additional
  control and keyboard lifecycle.

## Research basis

- Flet `PopupMenuButton` supports checked items, menu constraints, feedback,
  and native popup animation styling.
- Flet `BottomSheet` supports animation styling, safe area, scrolling,
  drag-to-dismiss, and keyboard inset handling.
- Flet `ListTile` provides selected state, hover/splash feedback, and standard
  48/56-pixel interaction rows.
- Flet accessibility guidance recommends explicit tooltips/semantic labels for
  popup controls and custom semantics where necessary.
