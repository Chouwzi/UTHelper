# Privacy, anonymous crash diagnostics, and usage analytics

UTHelper can create a narrowly scoped crash report (Sentry) to help diagnose
silent failures and record coarse, personless feature-usage events (PostHog) to
understand product reliability and adoption. Both anonymous crash diagnostics and
anonymous usage analytics are **enabled by default** and controlled independently
under **Settings → Privacy** (*Cài đặt → Riêng tư*). Saving **Disable** (*Tắt*)
for either control immediately blocks new delivery attempts and purges its
application-owned local queue (and, for usage analytics, deletes the local
random installation identifier). The full Terms of Use and privacy notice are
available both before first sign-in on the login dialog (*Điều khoản & Quyền
riêng tư*) and at any time from **Settings → Privacy**.

## What a report may contain (Sentry crash diagnostics)

The remote crash-diagnostics schema is an allow-list. A crash report may contain
only:

- schema version, a random per-event ID, a deterministic grouping fingerprint,
  and the occurrence time;
- app version, release channel, and source/packaged install type;
- OS family/version/build; CPU architecture, model description, logical core
  count, and nominal frequency; total/available/usable device memory and current
  application memory; coarse device/RAM/display classes; screen resolution and
  DPI; Python version, Flet version, and optional Flutter version;
- when the operating system exposes it without a privileged or external probe,
  GPU model/vendor, driver version, video-memory size, and graphics API;
- normalized exception runtime type;
- for selected built-in exception classes, only structural fields needed to
  understand the failure: missing attribute/symbol/module names, the affected
  runtime type, or numeric `errno`/`winerror`; exception-owned text and file
  names remain excluded;
- at most 40 normalized frames containing module, function, source-relative or
  basename-only path, and line number;
- coarse lifecycle phase, foreground/tray/unknown window state, and whether the
  prior run ended uncleanly;
- a fixed-list capture mechanism (for example Flet, asyncio, thread, or system
  hook), handled/unhandled classification, and coarse execution context;
- at most 20 semantic breadcrumbs selected from fixed action, outcome, source,
  screen, and duration-bucket values. They contain no free-form message;
- a fixed allow-list of installed dependency versions used by the app;
- on a correlated Windows Application Error only, a normalized exception code
  and faulting module basename; and
- for Flutter-runner failures, normalized runtime type and stack symbols encoded
  into the same sanitized exception grouping.

For Sentry crash diagnostics, the event ID is random for one event. In crash
reports, UTHelper creates no stable device identifier and sends no installation
ID, advertising ID, hardware serial, hostname, MAC address, device UUID, PCI
identifier, user ID, or cross-event identity. Hardware model strings can
distinguish device configurations and are not used as an identifier. UTHelper
does not add them to its own tag allow-list; Sentry may derive device- or
GPU-related tags when rendering its standard contexts.

## Anonymous usage analytics (PostHog)

When **Phân tích sử dụng ẩn danh** (`ANALYTICS_CONSENT`) is enabled and a valid
public PostHog ingestion configuration (`phc_...` project key + HTTPS ingestion
host) is present, UTHelper records a strict allow-list of 12 coarse interaction
events (`uth_session_started`, `uth_session_heartbeat`, `uth_session_ended`,
`uth_screen_viewed`, `uth_filter_applied`, `uth_sync_completed`,
`uth_document_opened`, `uth_course_activity_opened`, `uth_quiz_action`,
`uth_submission_action`, `uth_grades_interaction`, and
`uth_notification_summary`) validated with `ConfigDict(extra="forbid")`:

- **Authenticated non-demo gate**: Analytics remains completely inactive (`0`
  events queued or sent, `0` local `installation.json` files created) before
  login completes, on the unauthenticated sign-in dialog, when signed into the
  offline Reviewer Demo account (`uthelper_demo`), when `UTH_TEST_MODE=1`, in
  `--web` mode, or when PostHog is unconfigured.
- **App-scoped random installation `UUIDv4` (`distinct_id`)**: Generated lazily
  via `uuid.uuid4()` (`os.urandom`) and stored locally in
  `<appdata_dir>/analytics/installation.json` solely so PostHog can count
  anonymous active installations (DAU/WAU/MAU, Stickiness, Retention, and
  Funnels). It is never derived from `UTH_USERNAME` (MSSV), Moodle `userid`,
  hostname, MAC address, `ANDROID_ID`, Windows `MachineGuid`, or hardware
  serials, and is strictly isolated from Sentry crash reports (Sentry never
  reads or attaches `distinct_id` or `$session_id`).
- **Ephemeral RFC 9562 `UUIDv7` `$session_id`**: Generated in memory per
  interactive foreground session, paused across brief `< 30m` minimizes, rotated
  after `>= 30m` background/idle time, `>= 23h` max age, `> 5s` backward
  wall-clock jump, sign-out, or account switch, with `uth_session_ended`
  timestamped at `last_foreground_active_utc`.
- **Personless mode (`$process_person_profile: false`)**: Every event sets
  `"$process_person_profile": false` and UTHelper never emits `$identify`,
  `$alias`, or `$set`, so PostHog creates zero Person Profiles and performs no
  cross-device identity stitching.
- **IP & GeoIP suppression (`$ip: null` + `$geoip_disable: true`)**: Every event
  sets `"$ip": null` and `"$geoip_disable": true` in `properties`, and the
  PostHog project must keep **Discard client IP data = ON** so client IPs are
  dropped before storage.
- **Coarse context only (anti-fingerprinting wall)**: PostHog super-properties
  carry only `app_version`, `release_channel`, `distribution_channel`,
  `package_format`, `os_family`, and coarse `device_class` (`desktop` /
  `mobile`) — never CPU/GPU descriptions, exact RAM bytes, or screen pixel
  dimensions — preventing timestamp/hardware correlation against Sentry reports.
- **Bounded `48h` / `512 KiB` local spool & rate caps**: Pending batches under
  `<appdata_dir>/analytics/pending/` are capped at **20 batches** (`<= 25`
  events / `<= 64 KiB` per batch), **512 KiB** total, and **48h** (`48 hours`)
  max age, with client caps of `<= 120` events/session and `<= 300` events per
  rolling 24h.
- **Instant opt-out, sign-out & account-switch deletion**: Disabling **Phân tích
  sử dụng ẩn danh** in **Settings → Privacy** immediately increments the consent
  epoch, halts delivery, clears in-memory queues and `$session_id`, and deletes
  `analytics/pending/*` and `analytics/installation.json` (re-enabling later
  mints a brand-new `UUIDv4`). Signing out, switching UTH accounts, or resetting
  settings also deletes/rotates `analytics/installation.json` so shared lab or
  dormitory PCs never link two students under one installation stream. Upgrading
  from schema v5 without `ANALYTICS_CONSENT` automatically inherits `"disabled"`
  whenever `CRASH_REPORTING_CONSENT` was set to `"disabled"`.

## What is forbidden from remote reports

Neither Sentry crash diagnostics nor PostHog usage analytics uploads exception
messages or arguments, raw traceback data, raw log files, local variables,
environment variables, command lines, HTTP requests, headers or bodies, URLs,
screenshots, session replay, minidumps, or the local native-fault file.

Both pipelines also exclude any IP address from the event payload and never
upload a name, email address, username, MSSV, account ID, password, token,
sesskey, cookie, MoodleSession, Authorization value, absolute home path, course
name, activity title, class schedule, room, campus, submission, file name,
grade, file content, or any other Moodle/Portal data. Unknown fields are
rejected before any event or report is written to disk.

## Distribution channels and Store Data Safety disclosures

UTHelper is distributed across official app stores and long-term support (LTS)
GitHub Releases. All channels share the same local-first storage model and
opt-out crash diagnostics and anonymous usage analytics rules above, with
channel-specific update behavior:

- **Google Play Store (`chplay`, Android `.aab`)**:
  - The app sends credentials/session data and user-requested course activity
    directly over HTTPS to the configured UTH Moodle/Portal endpoints
    (`courses.ut.edu.vn`, `thnn.ut.edu.vn`, `portal.ut.edu.vn`,
    `portal-publicapp.ut.edu.vn`). It
    does not operate a developer-hosted proxy. When enabled, sanitized crash
    diagnostics are sent to Sentry and personless usage analytics (`App
    interactions` + app-scoped random `UUIDv4` `distinct_id`) are sent to
    PostHog after authenticated non-demo sign-in; optional alert integrations
    send their configured payloads to the user's chosen Gmail, Discord, or
    Telegram endpoint.
  - These direct transfers are inputs to, not completed answers for, Google
    Play's Data Safety form. Review the
    [release data-flow inventory](guides/store-distribution-submission.md#data-safety-flow-review)
    against the signed AAB, bundled SDKs, current backend behavior, and provider
    retention settings. The absence of a developer-hosted backend does not by
    itself establish a “no collection” or “no sharing” answer under
    [Google Play's Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469).
  - Grades and course documents fetched from UTH are handled on device and are
    not included in the diagnostics or analytics allow-lists. Check the
    release's bundled SDKs for any other outbound route before relying on that
    boundary.
  - Crash diagnostics and usage analytics can each be disabled in
    **Settings → Privacy**. The Sentry and PostHog projects control remote
    retention; their current retention and client-IP discard settings must be
    verified before enabling production ingestion keys.
  - **No external self-updating**: `chplay` builds strip
    `REQUEST_INSTALL_PACKAGES` and APK installer code, never poll GitHub for
    updates, and rely exclusively on Google Play for app updates.
  - **Transport & backup hardening**: `chplay` builds require HTTPS (no
    cleartext HTTP) and set `android:allowBackup="false"` so local credentials,
    tokens, and `analytics/installation.json` are excluded from Android Auto
    Backup across reinstalls.
- **Microsoft Store (`msstore`, Windows `.msix`)**:
  - **No external self-updating**: `msstore` builds disable GitHub update
    polling and external installer execution; updates are managed exclusively by
    the Microsoft Store.
  - **AppData virtualization**: writes to `%APPDATA%\UTHelper` (including
    `telemetry\pending`, `analytics\pending`, and `analytics\installation.json`)
    are isolated by standard MSIX AppData virtualization (`LocalCache\Roaming`)
    and are cleanly removed when the Microsoft Store package is uninstalled.
- **GitHub Releases LTS (`apk_lts`, `exe_lts`, and unsigned iOS `.ipa`)**:
  - Connects to `api.github.com` / `github.com` solely to check signed LTS
    release manifests and download verified updates after explicit user
    confirmation.

## Local files, limits, and deletion

### Platform storage locations

- **Windows (`exe_lts` and `msstore`)**: application settings, caches, logs, and
  pending telemetry are stored under `%APPDATA%\UTHelper` (virtualized per
  package on Microsoft Store MSIX). Credentials are stored in Windows Credential
  Manager (`keyring`). Remotely eligible crash reports are queued under
  `%APPDATA%\UTHelper\telemetry\pending`; anonymous usage analytics batches and
  the random installation UUIDv4 are stored in sibling paths
  `%APPDATA%\UTHelper\analytics\pending` and
  `%APPDATA%\UTHelper\analytics\installation.json`.
- **Android (`chplay` and `apk_lts`)**: application data is stored in Android
  app-private storage resolved from `FLET_APP_STORAGE_DATA` (with fallback to
  `FLET_APP_DATA` or `~/.uthelper` when unset), with crash diagnostics under
  `telemetry/pending` and usage analytics under `analytics/pending` and
  `analytics/installation.json`. Sensitive credentials and integration tokens
  (`UTH_PASSWORD`, `MOODLE_SESSION`, `MOODLE_WS_TOKEN`,
  `MOODLE_WS_TOKEN_ORIGIN`, `GMAIL_APP_PASSWORD`, `DISCORD_WEBHOOK_URL`, and
  `TELEGRAM_BOT_TOKEN`) are encrypted via `AndroidKeyStore` AES-GCM inside
  `context.noBackupFilesDir` (`CredentialVault` for native background sync and
  `AppSecretVault` for app settings secrets), and are never persisted in
  plaintext `settings.json`.

The crash diagnostics queue retains at most **20 reports**, **1 MiB** total, and
**7 days**. The usage analytics spool retains at most **20 batches**, **512 KiB**
total, and **48h** (**48 hours**). Both queues are non-recursive, reject
links/reparse points, and use atomic writes. Operational logs rotate locally at
2 MiB with three backups. The native fault file is local only and capped at
256 KiB. A Windows Flutter bridge is capped at 64 KiB and is deleted after
successful import.

### Deleting local data and opting out

UTHelper does not maintain developer-hosted user accounts; your Moodle/Portal
account is owned by UTH. You can delete all locally stored data at any time:

1. **Disable diagnostics or usage analytics and purge local telemetry**: open
   **Settings → Privacy**, set crash diagnostics and/or anonymous usage
   analytics to **Disable**, and save. UTHelper immediately stops delivery
   attempts, deletes the corresponding owned pending queue (`telemetry/pending`
   and/or `analytics/pending`), and—when usage analytics is disabled—immediately
   deletes `analytics/installation.json` (`distinct_id`). UTHelper never deletes
   unrelated files or follows a link target. Re-enabling affects only future
   events (minting a fresh `UUIDv4` for analytics) and never recreates deleted
   reports or history.
2. **Clear credentials and rotate analytics identity**: open
   **Settings → Account** and click **Sign out** (*Đăng xuất*) inside UTHelper
   to immediately clear the active Moodle/Portal session, embedded WebView
   cookies/sessions, `analytics/installation.json`, and stored credentials from
   Windows Credential Manager (`keyring`) or Android `AndroidKeyStore` vaults
   (`CredentialVault` and `AppSecretVault`); any legacy plaintext entries in
   `settings.json` are scrubbed automatically upon migration or save.
3. **Remove all local app data**:
   - **Android (Google Play / APK LTS)**: open Android *Settings → Apps →
     UTHelper → Storage → Clear storage*, or uninstall UTHelper.
   - **Windows (Microsoft Store MSIX)**: uninstall UTHelper from Windows
     *Settings → Apps*; MSIX AppData virtualization automatically removes the
     package's virtualized `%APPDATA%\UTHelper` container.
   - **Windows (MSI / Burn EXE LTS)**: uninstall UTHelper via Windows *Settings
     → Apps* and delete `%APPDATA%\UTHelper`, `%LOCALAPPDATA%\UTHelper`
     (including `%LOCALAPPDATA%\UTHelper\webview2_profile`), and
     `%APPDATA%\com.uthelper\UTHelper\uthhelper\quiz-webview` (and remove any
     `UTHelper` entries in Windows Credential Manager if desired).

An **unclean exit** means the previous run did not remove its run marker. It is
not definitive proof of a crash: power loss, forced termination, or OS shutdown
can produce the same signal. UTHelper claims a native Windows crash only when a
recent matching Application Error event is available.

## Network, Sentry, and PostHog

Even though neither Sentry nor PostHog payloads contain an IP address, an IP
address is normally visible to the network endpoint while establishing an HTTPS
connection. The service provider or hosting network may process it under their
own policies.

- **Sentry retention & transport**: UTHelper configures Sentry with default PII
  disabled, no SDK-generated breadcrumbs, no automatic tracing, no replay, and a
  strict event reconstruction step. Redirects are refused, and only a confirmed
  HTTP 2xx response removes a queued crash report. **Sentry retention:** for
  official releases with a configured public Sentry ingestion DSN, server-side
  crash events are retained for up to **90 days** (standard Sentry project
  retention policy, configured not to store client IP addresses) and are
  automatically purged thereafter. Development, CI, or diagnostic builds without
  a DSN treat remote reporting as unconfigured, retain eligible local crash
  reports only within the local queue limits above (up to 20 reports / 7 days),
  and do not transmit events externally.
- **PostHog transport & retention**: UTHelper sends batched events over stdlib
  HTTPS (`POST <posthog_host>/batch/`) with redirects refused, `"$ip": null`,
  `"$geoip_disable": true`, and `"$process_person_profile": false`, confirming
  delivery only on HTTP `200` `application/json` responses acknowledging receipt.
  The PostHog project MUST enable **Discard client IP data = ON** under *Project
  Settings → Privacy* and retain anonymous events only within the configured
  PostHog project event retention window (default **1 year** on PostHog Cloud
  unless shortened in project settings), with zero Person Profiles stored.
  Unconfigured builds (`posthog_project_api_key` or `posthog_host` unset) act as
  a complete memory and disk no-op (`0` `analytics/` files created).

For privacy or deletion requests, use a
[private GitHub security advisory](https://github.com/Chouwzi/UTHelper/security/advisories/new).
Non-sensitive questions may use the public issue tracker.

## Synthetic test report

A maintainer may send a **synthetic test report** only from a controlled
development build: configure a public ingestion DSN through the documented
development override, keep diagnostics enabled, call
`runtime.record_exception(RuntimeError(), AppPhase.GUI)` with an empty synthetic
exception, then perform one bounded delivery flush or restart the app. Never put
a credential or user value in the synthetic exception. Confirm the event in the
intended Sentry project, verify the allowed field list above, record the actual
Sentry retention setting, and delete the test event. The automated transport
tests for both Sentry and PostHog use local intercepted endpoints and never send
external network traffic.
