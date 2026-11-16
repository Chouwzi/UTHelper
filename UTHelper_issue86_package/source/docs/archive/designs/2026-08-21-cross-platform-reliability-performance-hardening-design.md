# Cross-Platform Reliability and Performance Hardening Design

**Date:** 2026-08-21

**Branch:** `feature/cross-platform-performance`

**Status:** Approved for minimal implementation after controlled benchmarks

## Objective

Fix only the correctness and responsiveness defects reproduced by deterministic
tests or benchmarks. Preserve the current architecture unless a small ownership
guard is required to prevent stale work from publishing.

## Confirmed implementation scope

1. Supplemental Moodle assignments absent from action events use
   `submission_status="unknown"`; no per-assignment status requests are added.
2. Assignment merge identity includes the trusted Moodle site/origin so equal
   numeric `cmid` values from Courses and THNN remain separate.
3. THNN calendar responses are accepted only as valid iCalendar payloads. A
   malformed/login response does not advance freshness or erase last-known-good
   in-memory data. Authentication may be retried once.
4. Description image downloads run outside the Flet event loop, stream with a
   byte cap, and publish complete files atomically.
5. Detail prefetch workers capture a cache generation. Cache clear increments
   it, late generations cannot publish, and cancellation returns without waiting
   for running I/O.
6. Calendar opening seeds its pager once. No event cap, virtualization, or broad
   Calendar rewrite is included.
7. The Android notification regression test awaits the production async callback;
   production callback behavior is unchanged.
8. Existing conflict-marker, duplicate-import, whitespace, and documentation
   quality failures in the branch are removed.

## Deliberate non-goals

- No THNN coordinator, typed outcome hierarchy, circuit breaker, or new cache
  subsystem. Benchmark evidence does not justify that architecture yet.
- No diagnostic delivery redesign. Queue growth was shown only under a synthetic
  blocked-worker storm and the existing spool/backoff bounds normal operation.
- No N+1 Moodle status requests.
- No third-party HTTP client migration.
- No claim that Python/Flet patch timing proves Android frame smoothness or ANR
  behavior; that requires a physical-device run.
- No unrelated UI redesign or repository-wide refactor.

## Behavioral contracts

### Moodle merge

An action event and a supplemental assignment match only when both the Moodle
site identity and course-module identifier match. Unknown status remains eligible
for reminder policy; absence from action events never proves submission.

### THNN calendar validation

A successful export must stay on the configured origin, avoid a login path, stay
within the existing bounded response read, and contain a complete VCALENDAR
envelope after decoding. Invalid responses are failures, not successful empty
calendars. Existing valid cached activities remain available and the successful
fetch timestamp is unchanged.

### Image caching

The synchronous utility reads fixed-size chunks up to a per-image cap, validates
an image content type when supplied, writes a temporary sibling, and atomically
replaces the final file only after a complete download. The async caller invokes
the complete utility in a worker thread. Failure keeps the original online URL.

### Prefetch ownership

Every cache write checks the generation captured before its network request.
Cancellation cancels queued futures and shuts down the local executor with
`wait=False`; running urllib calls may finish but cannot repopulate a cleared
generation.

### Calendar opening

The public open operation assigns data and seeds the mounted pager exactly once.
`update_data()` remains available for refreshes of an already visible Calendar.

## Verification gates

- Each production behavior change is preceded by a focused test observed failing
  for the intended reason.
- Focused tests pass after each minimal implementation.
- Full `python -m pytest tests -q --tb=short` passes without the un-awaited
  notification coroutine warning.
- `ruff check src tests`, bytecode compilation, and `git diff --check` pass when
  the required tools are available.
- Controlled before/after benchmarks cover Calendar seed/build, image heartbeat
  and bounded allocation, and prefetch cancellation/stale publication.

