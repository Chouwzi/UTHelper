# Today Schedule Spotlight Design

**Date:** 2026-08-13

**Status:** Approved

**Scope:** Dashboard presentation and in-app disclosure of the already-cached Portal schedule

## Problem

The dashboard currently shows only a collapsed “Lịch học hôm nay” header. Opening it expands every class inline, including finished sessions, which pushes deadline cards down and makes the dashboard harder to scan. Session metadata also combines room and campus into one truncated line. The disclosure has no class-detail state and therefore cannot provide predictable in-app Back behavior.

## User outcome

At launch, the dashboard shows the one class that matters now:

1. Prefer a non-cancelled class currently in progress.
2. Otherwise show the earliest non-cancelled upcoming class.
3. When every non-cancelled class has finished, show “Đã hoàn thành lịch học hôm nay”.
4. Cancelled classes remain visible in the full-day list but are never selected as the spotlight.

The card exposes two distinct actions. Tapping the class content opens that class directly. Tapping “Xem lịch hôm nay · N buổi” opens all of today’s sessions.

## Information hierarchy

The spotlight and each full-list row use separate lines for:

- subject and phase;
- time range and periods;
- room;
- campus.

No room/campus line is merged into a single ellipsized sentence. Missing values use “Chưa cập nhật phòng” or “Chưa cập nhật cơ sở”. The detail state shows the course code, full time and period range, room, campus, phase, and note when supplied.

## Disclosure model

The component owns one transient surface with two internal states:

- `list`: every class for today in chronological order, including finished and cancelled sessions;
- `detail`: one selected class, with an explicit Back control.

On Android and iOS, the surface is a draggable, safe-area-aware bottom sheet sized to its content with a bounded viewport and scrolling. On desktop it is an `AlertDialog`. Moving from list to detail replaces content in the same surface; it never stacks another modal.

Back behavior is deterministic:

- detail → list when detail was opened from the list;
- detail → close when detail was opened directly from the spotlight;
- list → close;
- after closing, the dashboard remains visible;
- only Back from the dashboard with no schedule surface open may leave the app on mobile.

## Time transitions

The existing dashboard clock refresh invokes the schedule component. When a phase boundary is crossed, the component recomputes the spotlight and rerenders any open list/detail content. This moves a finished spotlight to the next upcoming session without a network request. If the selected detail remains open, it remains selected and its status is refreshed.

## Loading and failure states

The card remains visible and preserves cached sessions during background refresh. With no cached data it displays the existing loading, authentication-required, empty, or retry state. Retry invokes the existing Portal schedule coordinator. The UI does not introduce a second fetch path.

## Accessibility and themes

- Interactive rows and CTAs have at least a 48 dp logical hit target.
- Phase meaning is communicated by text and color, never color alone.
- Long subject, campus, and note text wraps in list/detail surfaces.
- Every surface uses semantic roles from `gui.core.theme.C` and is rebuilt/recolored on theme changes.
- Android/iOS presentation is governed by `platform_utils.UI_IS_MOBILE`, so `UTHELPER_UI_PLATFORM` previews the correct surface without enabling native mobile capabilities.

## Verification

Automated tests cover spotlight selection at before/start/end boundaries, cancelled sessions, all-finished completion, separate room/campus lines, both entry paths, list-to-detail navigation, Back behavior, adaptive surface selection, time refresh, and theme refresh. Focused tests and the full repository suite must pass before completion.
