# ADR 0002: Activity-driven notification pipeline

- Status: Accepted
- Date: 2026-07-19

## Context

The university Moodle notification feed is not a reliable source for UTHelper.
The application already obtains quiz, assignment, attendance, deadline,
submission, and grade data from calendar/activity APIs. Android notification
calls were also treated as synchronous even though the Flet extension exposes
asynchronous services.

## Decision

- Activity data is the only source for deadline reminders.
- Filtering, milestones, DND, stable IDs, and desired schedules live in a pure
  core policy.
- `NotificationManager` owns async orchestration and returns typed results.
- Synchronous desktop/integration notifiers run in worker threads; Android
  services are awaited directly.
- Android native schedules are reconciled after every successful activity
  refresh and persisted so changed/deleted/submitted activities are cancelled.
- Moodle unread-notification count is not consulted by this pipeline.

## Consequences

- Known deadlines can remain scheduled across Android app restarts and reboots.
- Platform adapters no longer duplicate activity filtering policy.
- Call sites and tests must await dispatch operations.
- A future WorkManager worker can reuse the activity DTO/policy contract, but
  force-stop behavior remains controlled by Android.
