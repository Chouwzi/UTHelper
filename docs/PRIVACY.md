# Privacy and anonymous crash diagnostics

UTHelper can create a narrowly scoped crash report to help diagnose silent
failures. Anonymous diagnostics are **enabled by default**. They can be disabled
at any time under **Settings → Privacy**; saving that choice immediately blocks
new delivery attempts and clears the application-owned pending queue. The full
Terms of Use and privacy notice are available from the same Settings section.

## What a report may contain

The remote schema is an allow-list. A report may contain only:

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

The event ID is random for one event. UTHelper creates no stable device identifier
and sends no installation ID, advertising ID, hardware serial, hostname, MAC
address, device UUID, PCI identifier, user ID, or cross-event identity. Hardware
model strings can distinguish device configurations and are not used as an
identifier. UTHelper does not add them to its own tag allow-list; Sentry may
derive device- or GPU-related tags when rendering its standard contexts.

## What is forbidden from remote reports

UTHelper does not upload exception messages or arguments, raw traceback data,
raw log files, local variables, environment variables, command lines, HTTP
requests, headers or bodies, URLs, screenshots, session replay, minidumps, or
the local native-fault file.

It also does not put an IP address in the event payload and does not upload a
name, email address, username, account ID, password,
token, sesskey, cookie, MoodleSession, Authorization value, absolute home path,
course name, activity title, class schedule, room, campus, submission, grade,
file content, or any other Moodle/Portal data. Unknown fields are rejected
before a report is written to disk.

## Distribution channels and Store Data Safety disclosures

UTHelper is distributed across official app stores and long-term support (LTS)
GitHub Releases. All channels share the same local-first storage model and
opt-out crash diagnostics rules above, with channel-specific update behavior:

- **Google Play Store (`chplay`, Android `.aab`)**:
  - The app sends credentials/session data and user-requested course activity
    directly over HTTPS to the configured UTH Moodle/Portal endpoints
    (`courses.ut.edu.vn`, `thnn.ut.edu.vn`, `portal.ut.edu.vn`,
    `portal-publicapp.ut.edu.vn`). It
    does not operate a developer-hosted proxy. When enabled, sanitized crash
    diagnostics are sent to Sentry; optional alert integrations send their
    configured payloads to the user's chosen Gmail, Discord, or Telegram
    endpoint.
  - These direct transfers are inputs to, not completed answers for, Google
    Play's Data Safety form. Review the
    [release data-flow inventory](guides/store-distribution-submission.md#data-safety-flow-review)
    against the signed AAB, bundled SDKs, current backend behavior, and provider
    retention settings. The absence of a developer-hosted backend does not by
    itself establish a “no collection” or “no sharing” answer under
    [Google Play's Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469).
  - Grades and course documents fetched from UTH are handled on device and are
    not included in the diagnostics allow-list. Check the release's bundled
    SDKs for any other outbound route before relying on that boundary.
  - Diagnostics can be disabled in **Settings → Privacy**. The Sentry project
    controls remote retention; its current retention setting must be recorded
    before enabling a production ingestion DSN.
  - **No external self-updating**: `chplay` builds strip
    `REQUEST_INSTALL_PACKAGES` and APK installer code, never poll GitHub for
    updates, and rely exclusively on Google Play for app updates.
  - **Transport & backup hardening**: `chplay` builds require HTTPS (no
    cleartext HTTP) and set `android:allowBackup="false"` so local credentials
    and tokens are excluded from Android Auto Backup.
- **Microsoft Store (`msstore`, Windows `.msix`)**:
  - **No external self-updating**: `msstore` builds disable GitHub update
    polling and external installer execution; updates are managed exclusively by
    the Microsoft Store.
  - **AppData virtualization**: writes to `%APPDATA%\UTHelper` are isolated by
    standard MSIX AppData virtualization (`LocalCache\Roaming`) and are cleanly
    removed when the Microsoft Store package is uninstalled.
- **GitHub Releases LTS (`apk_lts`, `exe_lts`, and unsigned iOS `.ipa`)**:
  - Connects to `api.github.com` / `github.com` solely to check signed LTS
    release manifests and download verified updates after explicit user
    confirmation.

## Local files, limits, and deletion

### Platform storage locations

- **Windows (`exe_lts` and `msstore`)**: application settings, caches, logs, and
  pending telemetry are stored under `%APPDATA%\UTHelper` (virtualized per
  package on Microsoft Store MSIX). Credentials are stored in Windows Credential
  Manager (`keyring`). Remotely eligible reports are queued under
  `%APPDATA%\UTHelper\telemetry\pending`.
- **Android (`chplay` and `apk_lts`)**: application data is stored in Android
  app-private storage resolved from `FLET_APP_STORAGE_DATA` (with fallback to
  `FLET_APP_DATA` or `~/.uthelper` when unset). Sensitive credentials and
  integration tokens (`UTH_PASSWORD`, `MOODLE_SESSION`, `MOODLE_WS_TOKEN`,
  `MOODLE_WS_TOKEN_ORIGIN`, `GMAIL_APP_PASSWORD`, `DISCORD_WEBHOOK_URL`, and
  `TELEGRAM_BOT_TOKEN`) are encrypted via `AndroidKeyStore` AES-GCM inside
  `context.noBackupFilesDir` (`CredentialVault` for native background sync and
  `AppSecretVault` for app settings secrets), and are never persisted in
  plaintext `settings.json`.

The diagnostics queue retains at most **20 reports**, **1 MiB** total, and
**7 days**. It is non-recursive, rejects links/reparse points, deduplicates
equivalent failures, and uses atomic writes. Operational logs rotate locally at
2 MiB with three backups. The native fault file is local only and capped at
256 KiB. A Windows Flutter bridge is capped at 64 KiB and is deleted after
successful import.

### Deleting local data and opting out

UTHelper does not maintain developer-hosted user accounts; your Moodle/Portal
account is owned by UTH. You can delete all locally stored data at any time:

1. **Disable diagnostics and purge pending reports**: open **Settings → Privacy**,
   set crash diagnostics to **Disable**, and save. UTHelper immediately stops
   delivery attempts and deletes its owned pending queue (retrying on next
   startup if needed). UTHelper never deletes unrelated files or follows a link
   target. Re-enabling affects only future delivery attempts; it does not
   recreate deleted reports.
2. **Clear credentials**: open **Settings → Account** and click **Sign out**
   (*Đăng xuất*) inside UTHelper to immediately clear the active Moodle/Portal
   session, embedded WebView cookies/sessions, and stored credentials from
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

## Network and Sentry

Even though the event payload contains no IP address, an IP address is normally
visible to the network endpoint while establishing an HTTPS connection. The
service provider or hosting network may process it under their own policies.
UTHelper configures Sentry with default PII disabled, no SDK-generated
breadcrumbs, no automatic tracing, no replay, and a strict event reconstruction
step. Only the bounded semantic breadcrumbs described above can be delivered.
Redirects are refused. The Sentry project must be configured not to store client
IP addresses. Only a confirmed HTTP 2xx response removes a queued report.

**Sentry retention:** for official releases with a configured public Sentry
ingestion DSN, server-side crash events are retained for up to **90 days**
(standard Sentry project retention policy, configured not to store client IP
addresses) and are automatically purged thereafter. Development, CI, or
diagnostic builds without a DSN treat remote reporting as unconfigured, retain
eligible local reports only within the local queue limits above (up to 20
reports / 7 days), and do not transmit events externally. For privacy or
deletion requests, use a
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
tests use a local intercepted endpoint and do not send an external report.
