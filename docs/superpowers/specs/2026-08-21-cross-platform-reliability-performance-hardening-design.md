# Cross-Platform Reliability and Performance Hardening Design

**Date:** 2026-08-21

**Branch:** `feature/cross-platform-performance`

**Status:** Proposed for implementation after user review

## Objective

Remove the confirmed release blockers in the Moodle/THNN merge path and the
confirmed responsiveness risks in async work, while preserving the branch's
existing user-facing behavior. Every behavior change will use test-driven
development, and performance claims will be backed by repeatable benchmarks
rather than inferred from Flet control counts alone.

This design intentionally does not add new user features. It hardens existing
data flows, cancellation ownership, diagnostics delivery, and Calendar
rendering.

## Scope and sequencing

The implementation is split into four independently verifiable slices:

1. Moodle and THNN data correctness.
2. Blocking I/O and prefetch cancellation ownership.
3. Diagnostics and Calendar workload bounding.
4. Test, static-analysis, benchmark, and physical-device handoff gates.

Each slice follows RED-GREEN-REFACTOR and must leave its focused tests green
before the next slice begins. No subagents are used.

### Planned component boundaries

- `core.activity_identity`: deterministic source/origin-aware activity keys.
- `core.thnn_activity`: typed THNN results, cache state, and milestone/backoff
  coordinator.
- `core.data_orchestrator`: Courses snapshot only, plus pure merging of the
  latest validated THNN cache; it no longer owns THNN web-session transport.
- `gui.app_controller`: subscribes to THNN coordinator publication and replaces
  only the THNN slice in `all_data`.
- `gui.core.utils`: bounded synchronous image streaming utility, always invoked
  from a worker by async callers.
- existing diagnostics and Calendar modules: targeted ownership/single-flight
  changes without broader UI restructuring.

The implementation will use these boundaries as written. Moving a responsibility
back into `data_orchestrator.py` or `app_controller.py` requires a design change,
not an implementation shortcut.

## 1. Moodle status truth and source-aware identity

### Supplemental assignment status

`mod_assign_get_assignments` is an enumeration/metadata endpoint, not a
per-user submission-status endpoint. Assignments missing from the action-event
response will therefore be represented as `submission_status="unknown"`.

The implementation will not add an N+1 call to
`mod_assign_get_submission_status` during the primary activity refresh. Detail
prefetch may continue to enrich status through the existing detail path, but a
missing action event will never by itself be treated as proof of submission.

Tests will cover:

- an overdue assignment from earlier in the current month;
- a future assignment absent from action events;
- an existing action event whose known status must be preserved;
- notification policy accepting an `unknown` overdue assignment;
- `nosubmissions` remaining a configuration flag rather than a user status.

### Source-aware identity

Activity identity used for merging and deduplication will include the trusted
Moodle origin/site identity. A Courses assignment may only match an existing
Courses activity with the same `cmid`; a THNN URL with the same numeric `cmid`
must remain a separate item.

The identity helper will reject malformed or untrusted origins and will prefer
explicit `site_identity`/source metadata over title-based matching. The helper
will be small and deterministic so notification and cache code can reuse it
without duplicating URL parsing.

Tests will prove that two origins with the same `cmid` both survive and that no
course ID, cutoff date, or late status crosses the origin boundary.

## 2. Independent THNN synchronization

### Coordinator boundary

THNN network work will be removed from the synchronous Courses WS snapshot
path. A dedicated THNN coordinator will own:

- a per-site/user cache;
- milestone scheduling and catch-up;
- single-flight/coalesced refresh;
- bounded retry/backoff state;
- publication of a typed outcome;
- preservation of the last-known-good snapshot.

The normal Courses refresh will publish Courses plus the latest valid cached
THNN slice immediately. A due THNN refresh runs independently in a worker and
publishes a later source-aware update. THNN failure must not delay or invalidate
the Courses outcome.

The publication data flow is:

1. Load the site/user-scoped last-known-good THNN snapshot.
2. Complete and commit the Courses refresh without awaiting THNN network I/O.
3. Merge the cached THNN slice by source-aware identity for the immediate UI.
4. If a THNN milestone is due, schedule one coordinator refresh.
5. On validated THNN success, atomically replace only activities whose source
   identity is THNN, preserve the current Courses slice, re-sort/re-render once,
   and persist the combined UI snapshot.
6. On THNN failure, retain both the current Courses slice and the prior THNN
   slice, and expose only non-fatal status/diagnostic state.

The cache key contains the normalized THNN origin and a non-secret stable user
identity. Cache payloads include schema version, source origin, activities, and
`fetched_at`. A user/origin mismatch invalidates the cache instead of merging
it.

### Typed outcomes

The THNN fetch boundary will distinguish:

- `success` with activities;
- `success_empty` from a valid calendar payload with zero relevant events;
- `auth_stale`;
- `partial` when optional month URL enrichment fails;
- `failure` for network, parsing, or contract errors.

Only `success`, `success_empty`, and an explicitly accepted `partial` result
may update `fetched_at`. Failure and stale-auth outcomes preserve the existing
cache.

### Response and credential validation

Calendar export is accepted only when all of the following hold:

- final origin is exactly the configured THNN HTTPS origin;
- final path is not a login/SSO page;
- HTTP status is successful;
- content type or body contract is compatible with iCalendar;
- the decoded payload begins with `BEGIN:VCALENDAR` and has a valid end marker;
- body size stays within a configured limit.

On `auth_stale`, the coordinator may force one reauthentication and retry once.
It must not loop credentials. Stored username/password may be used only when
their configured credential origin matches THNN; Courses-stamped or unstamped
legacy credentials are not sent to THNN.

The transport will use bounded response reads and monotonic deadline checks.
Because stdlib socket timeouts are not a strict transaction-wide deadline, the
documentation will describe the enforced operation budget accurately and will
not claim a mathematically hard deadline unless the implementation can provide
one.

Tests will use local/fake transports for valid empty ICS, malformed ICS, login
HTML after same-origin redirect, 401/403/5xx, partial month failure, stale-auth
one-shot reauthentication, cache preservation, backoff, coalescing, and
credential-origin rejection. Live THNN tests remain opt-in and require a
dedicated authorized account.

## 3. Blocking image I/O

Description image pre-caching will remain a synchronous utility internally but
the complete invocation will run outside the Flet event loop. The downloader
will:

- stream fixed-size chunks instead of `resp.read()` without a limit;
- enforce a per-image byte cap and a total description-image budget;
- validate an image-compatible content type;
- write to a temporary file and atomically publish only a complete image;
- remove partial files on failure;
- apply monotonic budget checks while streaming;
- retain the current online-URL fallback when caching fails.

Tests will verify byte limits, partial-file cleanup, content-type rejection,
cache hits, and fallback behavior. An asyncio heartbeat benchmark will prove
that two slow image responses no longer stop event-loop progress. A memory
benchmark will verify bounded peak allocation for a large streamed response.

## 4. Prefetch cancellation and generation ownership

The detail cache will gain a generation counter. Clearing the detail cache
increments the generation. Each prefetch worker captures the generation before
starting and may write only if that generation is still current.

`prefetch_all_details` will stop using a `ThreadPoolExecutor` context manager
for cancellable work. It will:

- submit only a bounded number of in-flight jobs;
- poll completion with a short bounded wait so cancellation is observed even
  while workers are running;
- cancel jobs that have not started;
- return without waiting for already-running I/O;
- shut the executor down with `wait=False`;
- rely on generation checks to prevent late workers from mutating current
  state.

The loading indicator will also be generation-owned: a stale prefetch may hide
the spinner only if its epoch still matches the active epoch.

Tests will use controllable blocking workers to prove prompt cancellation,
absence of stale cache repopulation, and correct spinner ownership.

## 5. Diagnostic delivery single-flight

Diagnostic capture remains durable and bounded by the existing spool. Delivery
triggering will become single-flight:

- at most one executor drain task may be queued/running;
- new reports arriving during a drain set a dirty/requested flag;
- the same worker performs another bounded flush pass when requested;
- a lock closes the race between worker completion and a new trigger;
- shutdown prevents rescheduling.

This preserves serialized network delivery and existing backoff while bounding
executor Future/WorkItem growth independently of fingerprint cardinality.

Tests will submit thousands of unique triggers and assert one queued worker,
then inject a trigger during flush and assert that it is drained without
creating an unbounded executor queue.

## 6. Calendar rendering and benchmark semantics

Opening Calendar will be atomic: data assignment, anchoring to today, pager
seeding, and rendering occur once before the final host update. The existing
`update_data(); show()` double-build sequence will be replaced by one public
operation while retaining `update_data()` for already-visible countdown
refreshes.

No artificial item cap will be added because silently hiding deadlines would be
a correctness regression. Instead, benchmarks will use both:

- a realistic 100-activity dataset distributed across dates;
- an adversarial 500-activity same-day dataset.

The deterministic benchmark will report separately:

- Python construction/update time;
- Flet patch generation time and serialized payload size;
- retained/peak Python allocation;
- pager seed count so redundant work is caught without flaky timing alone.

Calendar will receive explicit regression thresholds with sufficient headroom
for CI variance. These numbers are protocol/Python budgets only and will not be
presented as Flutter frame time. Android frame/ANR claims require physical
device `gfxinfo`/Perfetto evidence.

Tests will assert one pager seed during opening and no second non-empty patch
without a new mutation.

## 7. Existing test and repository quality failures

The Android notification test will await the async handler and use an awaitable
mock; production async behavior remains unchanged. The leftover conflict
marker, duplicate imports, and whitespace failures in the branch diff will be
removed as isolated quality-gate fixes.

## Verification gates

Before completion, the following evidence is required:

1. Every new regression test was observed failing for the intended reason
   before its production fix.
2. Focused tests for each slice pass with no warnings.
3. The complete pytest suite passes with zero failures and no un-awaited
   coroutine warning.
4. `git diff --check develop...HEAD` passes.
5. Ruff passes using an available verified Ruff binary; if the environment
   cannot provide one, that limitation is reported rather than hidden.
6. The deterministic UI benchmark passes its new Calendar and existing host
   gates.
7. Dedicated cancellation, heartbeat, queue-growth, and memory benchmarks
   record before/after values.
8. No claim of Android frame smoothness or absence of ANR is made without a
   physical-device run; the exact required command/evidence is handed off if
   no device is connected.

## Non-goals

- No assignment/quiz mutation on THNN.
- No automatic use of a real student credential in tests.
- No new third-party HTTP dependency solely for this hardening work.
- No claim that Python patch timing equals Flutter frame timing.
- No unrelated UI redesign or broad refactor outside the affected boundaries.
