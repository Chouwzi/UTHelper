# Cross-Platform Reliability and Performance Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `superpowers:executing-plans` inline. The user explicitly prohibited subagents.
> Every production change follows RED–GREEN–REFACTOR.

**Goal:** Remove the benchmark-confirmed correctness and responsiveness defects
without introducing new architectural layers.

**Architecture:** Retain `DataOrchestrator`, `MoodleClient`, current GUI utilities,
and current Calendar composition. Add only source-aware keys, response validation,
bounded streaming, and generation ownership at their existing boundaries.

**Tech Stack:** Python 3, pytest, stdlib `urllib`, `concurrent.futures`, asyncio,
and Flet 0.86.5.

**Spec:** `docs/archive/designs/2026-08-21-cross-platform-reliability-performance-hardening-design.md`

## Global constraints

- Work only in `.worktrees/develop` on `feature/cross-platform-performance`.
- Do not use subagents.
- Do not add per-assignment Moodle status calls or third-party dependencies.
- Do not redesign THNN synchronization, diagnostics delivery, or Calendar layout.
- Preserve the untracked user file `test_fetch_now.py`.

---

### Task 1: Moodle status truth and source-aware merge

**Files:**
- Modify: `src/core/data_orchestrator.py`
- Test: `tests/test_data_orchestrator.py`

**Interface:** Existing `_merge_all_assignments(calendar_results, userid,
course_ids)` remains unchanged. Matching uses a local normalized site identity and
`cmid`; supplemental rows use literal status `unknown`.

- [ ] Add a regression fixture with equal `cmid` values from Courses and THNN and
  assert both rows survive without metadata crossing origins.
- [ ] Add a regression test asserting an absent action event yields `unknown`.
- [ ] Run the focused tests and record the expected current failures.
- [ ] Implement the smallest source-aware lookup and status change.
- [ ] Re-run focused orchestrator and notification-policy tests.
- [ ] Commit the independently passing data-correctness slice.

### Task 2: THNN calendar contract and last-known-good behavior

**Files:**
- Modify: `src/core/data_orchestrator.py`
- Modify only if the transport boundary requires it: `src/core/client.py`
- Test: `tests/test_data_orchestrator.py`

**Interface:** The existing THNN fetch method still returns a list on validated
success and a failure result on invalid/auth responses. Existing cached activities
and freshness timestamp change only after validated success.

- [ ] Add literal valid-empty ICS, login-HTML, malformed-ICS, and valid-event
  response tests.
- [ ] Add a failed-refresh test that starts with a last-known-good slice and asserts
  it is returned without changing the successful timestamp.
- [ ] Run focused tests and verify failures are contract failures, not fixture errors.
- [ ] Add a small VCALENDAR validator and one-shot forced reauthentication branch.
- [ ] Update cache/freshness only after validation; return last-known-good on failure.
- [ ] Re-run focused tests and commit the THNN reliability slice.

### Task 3: Bounded non-blocking description image caching

**Files:**
- Modify: `src/gui/core/utils.py`
- Modify: `src/gui/app_controller.py`
- Test: `tests/test_security_extended.py`
- Test: `tests/test_gui_app_controller.py` or the closest existing detail test

**Interface:** `pre_cache_description_images(html_text)` remains synchronous and
returns rewritten HTML. Its caller invokes it with `asyncio.to_thread`. Each image
download is streamed to a temporary sibling and atomically replaced only below the
fixed byte cap.

- [ ] Add tests for chunked reads, oversized bodies, non-image content type,
  partial-file cleanup, cache hit, and unchanged online URL on failure.
- [ ] Add an asyncio heartbeat test around the controller call path.
- [ ] Run focused tests and verify the current unbounded/blocking implementation
  fails the intended assertions.
- [ ] Implement chunked bounded download and atomic publication in the utility.
- [ ] Move the complete utility call behind `asyncio.to_thread` in the controller.
- [ ] Re-run focused tests plus the controlled heartbeat/memory benchmark; commit.

### Task 4: Prefetch cancellation and cache generation

**Files:**
- Modify: `src/core/data_orchestrator.py`
- Modify only for epoch-owned spinner behavior: `src/gui/app_controller.py`
- Test: `tests/test_data_orchestrator.py`
- Test: `tests/test_app_controller_prefetch.py`

**Interface:** Detail cache clear increments an integer generation. Fetch and
prefetch capture that generation and publish only while it remains current.
`prefetch_all_details` keeps its public signature.

- [ ] Add blocking-worker tests for prompt cancellation and late stale writes.
- [ ] Add a controller test proving an obsolete epoch cannot hide the active
  prefetch indicator if the existing guard is insufficient.
- [ ] Run focused tests and record the current cancellation/stale-write failures.
- [ ] Add generation checks around cache writes.
- [ ] Replace the cancellable executor context manager with explicit shutdown using
  `wait=False, cancel_futures=True`.
- [ ] Re-run focused tests and the cancellation benchmark; commit.

### Task 5: Atomic Calendar open and correct async notification test

**Files:**
- Modify: `src/gui/view_manager.py`
- Modify if needed: `src/gui/components/calendar_view.py`
- Test: `tests/test_calendar_navigation.py`
- Modify test: `tests/test_notification_policy.py`

**Interface:** `ViewManager.show_calendar(data_snapshot)` assigns data and calls
the Calendar's single public open/render path once. The notification callback stays
async and the test awaits it with an awaitable page double.

- [ ] Add a Calendar regression test asserting one pager seed per open.
- [ ] Run it and verify the existing two-seed path fails.
- [ ] Collapse `update_data(); show()` into one open operation without changing
  already-visible update behavior.
- [ ] Convert the stale notification test to an async test and assert the awaited
  observable URL launch.
- [ ] Run Calendar and notification focused tests; run the deterministic Calendar
  benchmark with 100 distributed and 500 same-day events; commit.

### Task 6: Quality gates and repository history

**Files:**
- Modify: `src/core/html_scrapers.py`
- Modify: `docs/architecture/refactoring-log.md`
- Modify only if required by test discovery: related test files

**Interface:** No runtime interface changes.

- [ ] Remove the literal conflict marker and duplicate imports/branch whitespace.
- [ ] Append a dated refactoring-log entry listing the exact scope and fresh
  verification evidence.
- [ ] Run focused tests for all five behavior slices.
- [ ] Run `python -m pytest tests -q --tb=short` and record exact counts.
- [ ] Run `ruff check src tests`, `python -m compileall -q src tests`, repository
  governance tests, deterministic performance checks, and `git diff --check`.
- [ ] Review `git diff` for unrelated edits and confirm `test_fetch_now.py` remains
  untouched.
- [ ] Commit the verified final quality/documentation slice.

