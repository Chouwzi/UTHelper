# THNN Activities Web-Session Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add read-only THNN assignment and quiz synchronization without relying on Moodle Web Services, while preserving the existing courses WS pipeline and avoiding reuse of courses/Portal tokens.

**Architecture:** Keep `courses.ut.edu.vn` on the existing stateless Moodle WS path. Add an isolated THNN web-session adapter that logs in with its own `CookieJar`, reads assignment/quiz pages, normalizes the results into the existing activity DTO shape, and persists a site-specific cache. A THNN coordinator runs on startup and at `00:00`, `06:00`, and `12:00`, including catch-up when the application opens after a missed milestone.

**Tech Stack:** Python 3.11+, stdlib `urllib`, `CookieJar`, `HTMLParser`, existing `DataCache`/safe file I/O, asyncio/thread offloading, pytest, Ruff.

---

## Verified Context

- `courses.ut.edu.vn` exposes Moodle REST WS. The current implementation uses:
  - `src/core/client.py`: `MoodleClient._get_ws_token()`, `call_ws_api()`.
  - `src/core/moodle_service.py`: assignment/quiz WS wrappers.
  - `src/core/data_orchestrator.py`: `_fetch_via_ws_api()` and `_merge_all_assignments()`.
- `thnn.ut.edu.vn` is a separate Moodle origin.
- Live public checks showed:
  - `thnn.ut.edu.vn/login/token.php` returns `enablewsdescription`.
  - `thnn.ut.edu.vn/webservice/rest/server.php` returns `403`.
  - `courses.ut.edu.vn/login/token.php` processes requests and its REST endpoint returns `invalidtoken` for an invalid token.
- `src/core/moodle_sites.py` allow-lists both origins, but this is origin validation only; it is not a THNN data integration.
- `src/core/client.py` contains a narrow authenticated HTML flow for removing a submission. It is not a general Moodle scraper and must not be expanded casually.
- `docs/api/moodle-web-services.md` documents only `courses.ut.edu.vn`.
- `docs/archive/designs/2026-08-04-moodle-submission-workflow-design.md` explicitly declares THNN outside that change.
- `README.md` currently claims both courses and THNN support, but the claim is ahead of the actual implementation and must be corrected or qualified during this work.
- Do not use a real student credential in automated tests. A dedicated test account and explicit approval are required for live read-only checks.

## Non-Goals and Guardrails

- Do not enable, bypass, or exploit THNN Web Services.
- Do not send a Portal JWT to THNN.
- Do not reuse a `courses.ut.edu.vn` Moodle WS token for THNN.
- Do not add THNN to `MoodleService` WS calls as if it had the courses contract.
- Do not implement assignment submission, quiz answering, grade mutation, or any write operation for THNN.
- Do not share browser cookies with the user's browser. The adapter must use an isolated cookie jar.
- Do not make the default activity refresh synchronously dependent on THNN. A THNN failure must leave courses activities usable.
- Do not claim that a separate HTTP session cannot log out a browser session until this is tested with an authorized test account.

## Acceptance Criteria

1. A THNN adapter can authenticate with username/password using an isolated cookie jar and same-origin redirects only.
2. The adapter can read and normalize at least assignment and quiz activities from THNN pages, or returns a typed capability/error result when the live page contract cannot be verified.
3. Normalized THNN activities use the same required fields consumed by filtering, cards, notifications, and calendar code.
4. THNN data is stored in a site/user-scoped cache and never replaces the courses cache.
5. Startup uses valid cached THNN data immediately, then fetches when a scheduled milestone has been missed.
6. Scheduled milestones are `00:00`, `06:00`, and `12:00`; opening at `12:01` after a successful fetch at `06:00` triggers one catch-up fetch.
7. Concurrent startup/manual/scheduled fetches are coalesced per site/user.
8. Existing courses WS behavior and submission workflow remain unchanged.
9. Tests cover parser fixtures, authentication/session safety, cache/catch-up, merge/deduplication, failure isolation, and UI-facing DTO compatibility.
10. Documentation no longer implies THNN WS support; it describes the web-session fallback and its limitations.

## Implementation Tasks

### Task 1: Establish the THNN data contract with read-only live discovery

**Files:**
- Create: `docs/api/thnn-web-session.md`
- Test fixture directory: `tests/fixtures/thnn/`

**Steps:**
1. Use a dedicated authorized test account, if available. Do not paste the username/password/token into the repository or chat.
2. Capture only sanitized HTML structure from these pages:
   - `/login/index.php`
   - `/my/`
   - `/calendar/view.php`
   - relevant assignment activity page(s)
   - relevant quiz activity page(s)
3. Record form action, hidden fields, login redirect behavior, activity selectors, due-date representation, course name, activity type, activity URL, and visible submission/attempt status.
4. Save sanitized HTML fixtures with all names, IDs, URLs containing tokens, cookies, and personal data removed or replaced.
5. Document which facts are verified live and which remain assumptions.
6. If no authorized account is available, stop this task and report the blocker; do not implement selectors based only on guesses.

**Validation:** The document must state the exact verified page contract and fixture provenance without secrets.

### Task 2: Define site-specific configuration and capability boundaries

**Files:**
- Modify: `src/config.py`
- Modify: `src/core/moodle_sites.py`
- Test: `tests/test_config.py`
- Test: `tests/test_config_extended.py`
- Test: `tests/test_moodle_sites.py` if present, otherwise create it

**Steps:**
1. Add an explicit THNN origin constant/configuration using the existing trusted-site model.
2. Keep `MOODLE_BASE_URL` and `MOODLE_WS_TOKEN_ORIGIN` semantics unchanged for courses.
3. Add a separate THNN cache namespace and capability identifier; do not make a THNN token field appear valid merely because the origin is allow-listed.
4. Decide whether THNN uses the existing UTH username/password settings or a separate opt-in setting. Prefer the existing credentials only if the product requirement explicitly accepts the shared account and privacy risk.
5. Add tests proving a courses WS token cannot be accepted as a THNN credential and that origins remain exact HTTPS origins.

**Validation:** Run the focused config/site tests with `PYTHONPATH=src` from the develop worktree.

### Task 3: Build an isolated THNN web-session transport

**Files:**
- Create: `src/core/thnn_client.py`
- Test: `tests/test_thnn_client.py`

**Steps:**
1. Implement a `ThnnClient` with a per-instance `CookieJar` and opener.
2. Use same-origin redirect validation, bounded response size, timeout, gzip handling, and a browser-like but honest User-Agent consistent with existing transport code.
3. Fetch `/login/index.php`, parse the login form and hidden `logintoken`, then POST username/password.
4. Treat a redirect back to the login page or an authentication error as a typed authentication failure.
5. Never retain raw HTML or passwords in logs.
6. Expose only read operations initially, for example `fetch_dashboard_html()` and `fetch_calendar_html()`; keep selectors/parsing in separate pure functions.
7. Add a `close()` method that drops the opener/cookie jar references if needed.

**Tests:**
- Parses a Moodle login form and submits hidden fields.
- Rejects missing `logintoken`.
- Rejects cross-origin redirects.
- Rejects login failure.
- Does not include password/cookie/token values in logged errors.
- Uses a fresh cookie jar per client instance.

**Validation:** `python -m pytest tests/test_thnn_client.py -q` from `.worktrees/develop` with `PYTHONPATH=src`.

### Task 4: Implement pure assignment and quiz HTML parsers

**Files:**
- Create: `src/core/thnn_parser.py`
- Modify: `src/core/notification_types.py` only if the existing DTO lacks required fields
- Test: `tests/test_thnn_parser.py`
- Fixtures: `tests/fixtures/thnn/*.html`

**Steps:**
1. Parse only the sanitized, verified selectors documented in Task 1.
2. Normalize every item to the shape consumed by the existing activity pipeline, including:
   - stable `id`
   - `title`
   - `course_name`/`course`
   - `deadline` and/or `deadline_str`
   - `url`
   - `type` as `assignment` or `quiz`
   - `submission_status`
   - `source` as a THNN-specific value
   - `details` as a safe dictionary
3. Use absolute URLs only after validating they belong to `https://thnn.ut.edu.vn`.
4. Parse Vietnamese and English labels only when verified by fixtures; do not infer dates from ambiguous text.
5. Return an empty list for a verified empty page and a typed parse error for a malformed/changed page.
6. Deduplicate items by stable activity ID or canonical URL.

**Tests:**
- Assignment parsing.
- Quiz parsing.
- Empty result.
- Missing/invalid due date.
- Relative URL normalization and cross-origin rejection.
- Deduplication.
- Fixture changes fail loudly rather than silently returning fabricated activities.

**Validation:** Focused parser tests plus Ruff on changed files.

### Task 5: Add THNN cache and milestone catch-up coordinator

**Files:**
- Create or extend: `src/core/thnn_activity.py`
- Reuse/reference: `src/core/today_schedule.py`
- Test: `tests/test_thnn_activity.py`

**Steps:**
1. Define a THNN snapshot containing normalized activities, `fetched_at`, source origin, and schema version.
2. Use a cache path/namespace that includes THNN origin and username, separate from `DataCache` for courses.
3. Implement the same milestone set: `00:00`, `06:00`, `12:00`.
4. On startup:
   - publish a valid cache immediately;
   - inspect all milestones after `fetched_at` and up to `now`;
   - fetch once if any milestone was missed;
   - do not fetch again merely because the cache is non-empty.
5. On scheduled wake-up, fetch once and record a new successful `fetched_at` only after parsing succeeds.
6. Coalesce concurrent startup/manual/scheduled calls with an async lock.
7. Preserve the last good snapshot when a new fetch fails and publish a typed error state.
8. Avoid copying `TodayScheduleCoordinator` blindly; extract a shared milestone utility only if duplication becomes real and tests remain clear.

**Tests:**
- Cache round trip.
- Empty activity snapshot is authoritative.
- `06:00` fetch followed by startup at `12:01` triggers catch-up.
- `12:00` fetch followed by startup at `12:01` does not fetch again.
- Multiple missed milestones still cause one fetch, not one request per milestone.
- Startup and manual fetch coalesce.
- Failed fetch preserves last good cache.

**Validation:** Focused coordinator tests and existing `tests/test_today_schedule.py`.

### Task 6: Integrate THNN activities without breaking courses

**Files:**
- Modify: `src/core/data_orchestrator.py`
- Modify: `src/gui/app_controller.py`
- Possibly modify: `src/core/data_cache.py` only if a site-scoped cache API is required
- Test: `tests/test_data_orchestrator.py`
- Test: `tests/test_data_orchestrator_service_boundary.py`
- Test: `tests/test_app_controller_prefetch.py`

**Steps:**
1. Add THNN fetching as an independent optional branch, preferably through a service boundary rather than by adding conditionals throughout `_fetch_via_ws_api()`.
2. Run the THNN read fetch in a worker thread so HTML/network work does not block Flet or asyncio.
3. Merge THNN activities into the existing activity collection using a source-aware stable ID. Do not merge two different sites solely by title.
4. Keep URL generation and detail navigation site-aware; THNN URLs must not use `settings.MOODLE_BASE_URL` when that points to courses.
5. If THNN fetch fails, return courses activities normally and expose THNN failure as a non-fatal status/log/diagnostic.
6. Ensure notification deduplication includes site/source in the stable identity.
7. Ensure CalendarView and ActivityCard can consume the normalized THNN DTO without site-specific UI hacks.

**Tests:**
- Courses-only success when THNN is unavailable.
- THNN-only activities merge correctly.
- Same title/deadline on two origins remains two activities.
- No cross-origin detail URL.
- One source failing does not turn the complete snapshot into an empty list.

**Validation:** Run orchestrator/controller test slices before the full suite.

### Task 7: Verify real read-only THNN behavior

**Files:**
- Modify: `tests/test_integration_live.py` only if the repository’s live-test opt-in convention supports it
- Documentation: `docs/api/thnn-web-session.md`

**Steps:**
1. Make live tests explicitly opt-in through an environment variable, for example `UTH_THNN_LIVE=1`.
2. Require credentials through environment variables or the local credential manager; never commit them.
3. Test login, one dashboard/calendar read, assignment parsing, quiz parsing, logout/close behavior, and cross-origin rejection.
4. Verify the user’s browser session remains intact only with an authorized test account and manual confirmation.
5. Do not perform submission, quiz attempt, grade, or other mutation requests.
6. Record only redacted status and counts.

**Validation:** Live test must be skipped by default and pass only in the authorized environment. A failed live test must not be treated as proof that the parser is correct or incorrect without inspecting the sanitized response.

### Task 8: Update documentation and product claims

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Create/modify: `docs/api/thnn-web-session.md`
- Possibly add: `docs/adr/000X-thnn-web-session-read-only.md`

**Steps:**
1. Replace the broad claim that THNN is already supported through WS/web session with the actual rollout status.
2. Document that courses uses Moodle WS and THNN uses a read-only web-session fallback because THNN WS is disabled.
3. Document cache timing, catch-up behavior, failure isolation, session isolation, known fragility of HTML selectors, and required admin/user consent assumptions.
4. State that THNN submission/quiz answering is not supported.
5. Include a troubleshooting section for login redirects, changed Moodle markup, and disabled/expired credentials.

**Validation:** Search the repository for stale claims such as “THNN via Moodle WS” and resolve or qualify each one.

### Task 9: Full validation and handoff

**Steps:**
1. Run focused tests for THNN client, parser, coordinator, orchestrator, and controller.
2. Run the full unit suite from the develop worktree:
   ```powershell
   Push-Location .worktrees\develop
   $env:PYTHONPATH = (Resolve-Path src).Path
   python -m pytest -q
   Pop-Location
   ```
3. Run Ruff on changed Python files.
4. Run `git diff --check`.
5. Review the diff for secrets, cross-origin token handling, unintended writes, and stale documentation.
6. Report any live test that was skipped due to missing authorized credentials.
7. Commit only after all local checks pass, using the repository’s commit convention.

## Final Handoff Summary Required From Implementing Agent

The implementing agent must report:

- Files changed and why.
- Exact THNN pages/endpoints read.
- Whether live fixtures were obtained and sanitized.
- Assignment/quiz fields successfully extracted.
- Cache and catch-up behavior verified, including the `12:01` case.
- Courses WS regression results.
- Whether browser logout/session replacement was tested, and with what authorization level.
- Known limitations and any blocked items.
