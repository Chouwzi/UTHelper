# Filter, diagnostics context, and Settings terms design

## Understanding summary

- Restore readable labels for the three dashboard filters and make their
  anchored desktop popups visually distinct from activity cards.
- Show complete course names without sacrificing the trailing activity count;
  preserve the mobile bottom-sheet interaction model.
- Add privacy-bounded Sentry OS, runtime, app, and device contexts while keeping
  all Moodle, Portal, account, and stable device identity data forbidden.
- Remove the standalone first-run crash-consent popup.
- Present Terms of Use and a Personal Data Processing Notice from a dedicated
  Settings entry; login is not gated by a checkbox.
- Crash diagnostics become the default for new or previously undecided users,
  while an explicit prior refusal remains authoritative and Settings retains an
  immediate opt-out.

## Assumptions and non-goals

- Filters remain immediate, single-select controls. Search within the course
  menu and multi-select are out of scope.
- Desktop keeps an anchored popup; Android and iOS keep a safe-area bottom
  sheet. No Python repaint animation loops are introduced.
- The direct Sentry transport remains. UTHelper does not add an IP field to the
  event, but cannot claim that the network endpoint never observes the source
  IP. Project-side IP storage prevention is an operational requirement.
- No screenshot, replay, request, raw log, exception message, breadcrumb,
  hostname, username, MSSV, device ID, serial number, MAC address, course,
  activity, schedule, grade, or submission data is collected.
- Hardware values are bounded and coarse. Exact mobile manufacturer/model is
  omitted unless a reviewed native bridge can provide it without a stable
  identifier.
- This engineering design is not a legal opinion. A maintainer should obtain
  Vietnamese legal review before materially expanding telemetry or scale.

## Approaches considered

1. **Selected: direct Sentry with a strict allow-list and local opt-out.** This
   keeps the existing reliable transport, adds standard Sentry contexts, and
   requires server-side IP scrubbing as a deployment control.
2. Route reports through a Vietnam-hosted relay. This would hide the user's IP
   from Sentry but introduces an operated service, a new security boundary, and
   additional compliance duties. It was explicitly declined.
3. Keep diagnostics local only. This best minimizes disclosure but does not
   support remote debugging and was not selected.

## Final design

### Adaptive filters

`AdaptiveFilterMenu` keeps one immutable option/state model. Trigger labels use
stable category text and a non-collapsing layout; the horizontal filter strip
may scroll rather than compress labels into punctuation. Desktop popups use a
theme-derived elevated surface, visible outline, stronger shadow, and a wider
course variant. Course rows may wrap to two lines with a taller minimum row;
counts retain a fixed trailing column and every label has a full-text tooltip.
Mobile tiles use the same two-line policy and accessible semantics.

### Diagnostics schema

The allow-listed report schema adds bounded OS build, device class, processor
count, memory bucket, and display bucket fields. Transport emits Sentry's
standard `contexts.os`, `contexts.runtime`, `contexts.app`, and
`contexts.device` objects and retains only low-cardinality searchable tags.
Empty exception frame lists omit `stacktrace` entirely to satisfy Sentry's event
schema. All new fields pass through the same Pydantic validation, redaction,
spool, and strict event-reconstruction boundary.

### Terms and privacy access

Settings contains one dedicated “Điều khoản sử dụng & quyền riêng tư” entry.
Activating it opens one scrollable, non-destructive dialog containing Terms of
Use and the processing notice. The document describes the
independent nature of UTHelper, credential handling, Moodle/Portal and GitHub
dependencies, default diagnostics, Sentry, retention, user controls, prohibited
use, PolyForm Noncommercial licensing, warranty limits, and contact routes.

No acknowledgement is persisted and login remains unchanged. New and legacy
`not_asked` diagnostics states become enabled by default; an existing `disabled`
value is never overwritten. The former standalone consent dialog is removed.
Settings remains the revocation path and disables delivery immediately.

## Reliability and testing

- Filter component tests cover non-collapsing labels, elevated popup styling,
  two-line course rows, tooltips, mobile wrapping, selection, and control reuse.
- Diagnostic tests cover every new field, privacy rejection, standard Sentry
  contexts, empty-frame payloads, OS probing fallbacks, and spool round trips.
- Settings tests cover the terms entry, scrollable dialog content, default
  enable, preservation of explicit refusal, and absence of the old prompt.
- Run focused tests and Ruff after each slice, then the complete pytest suite,
  Ruff, and desktop structural smoke.

## Decision log

- Keep native Flet popup/sheet controls to preserve focus, keyboard, dismissal,
  and accessibility behavior.
- Prefer legibility over fitting every filter in one unscrollable row.
- Use standard Sentry contexts instead of high-cardinality custom tags.
- Omit empty stacktraces instead of sending an invalid empty frame array.
- Reject an operated relay and accept direct Sentry transport with documented
  network-IP limitations and server-side storage prevention.
- Default diagnostics only for new/undecided users; never override an explicit
  refusal.
- Do not require a terms checkbox; keep the complete notice available from a
  dedicated Settings entry.
