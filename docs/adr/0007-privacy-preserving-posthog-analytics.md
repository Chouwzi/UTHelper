# ADR 0007: Privacy-Preserving PostHog Usage Analytics and Rust Core Portability

- Status: Accepted
- Date: 2026-11-28
- Extends: [ADR 0001](0001-refactoring-boundaries.md), [ADR 0004](0004-multi-channel-store-and-lts-distribution.md), [ADR 0006](0006-rust-core-tauri-native-migration.md)

## Context

UTHelper ships across Google Play (`chplay`), Microsoft Store (`msstore`), and GitHub Releases LTS (`apk_lts`, `exe_lts`, `ipa_lts`) with sanitized Sentry crash diagnostics (`src/diagnostics/`), but previously had zero visibility into:

1. **Real interactive active users (DAU/WAU/MAU, Stickiness, and Retention)** separated from Windows StartupTask tray launches, Android WorkManager/AlarmManager background checks, and automated store review crawlers (`uthelper_demo`).
2. **Interactive session duration** across foreground workflows without fragmenting sessions during brief file-picker/Alt-Tab minimizes or spilling midnight DAUs from idle tray processes.
3. **Feature adoption and sync reliability** across the 5 navigation tabs, document viewers (`pdf.js`, OOXML, native), embedded Quiz player, assignment submissions, Academic Radar/GPA tools, and Moodle/Portal sync outcomes.

Adding analytics directly via the `posthog` PyPI SDK or inside `core`/`diagnostics` was rejected because:
- Bundling third-party SDK dependencies (`requests`, `backoff`, `six`, `python-dateutil`, `distro`) inflates the Android `.aab` `assets/app.zip` (`110.7 MB` baseline in `ADR 0006`), widens `pip-audit` / `dependency-review` surface, and introduces shutdown thread hazards ahead of `main.py:_terminate_embedded_windows_process`.
- Sharing identifiers, hardware specs, or spool directories with `src/diagnostics/` would violate `docs/PRIVACY.md`'s Sentry crash-isolation guarantee AND cause `DiagnosticSpool._prune_locked()` (`src/diagnostics/spool.py`) to delete non-`DiagnosticReport` JSON files.
- Entangling Python telemetry with Flet or `core` would create new architectural debt right before migrating shared logic into `crates/uthelper-core` (`ADR 0006`).

## Decision

### 1. Clean Architecture Boundaries & Zero-Dependency Package (`src/analytics/`)

1. **Isolated `src/analytics/` package**:
   - Modules: `models.py`, `uuid7.py`, `identity.py`, `session.py`, `spool.py`, `transport.py`, `runtime.py`, and `_fs_safety.py`, implemented strictly on the Python standard library (`urllib.request` + `certifi` TLS + `_RejectAnalyticsRedirects`) with **zero** new PyPI dependencies.
   - **Static import boundary rules** (`tests/test_architecture_boundaries.py`):
     - `src/core/**` MUST NEVER import `analytics` or `diagnostics`.
     - `src/diagnostics/**` MUST NEVER import `analytics`.
     - `src/analytics/**` MUST NEVER import `gui`, `core`, or `diagnostics`.
   - Presentation events cross thin controller/coordinator seams (`TelemetryBridge`, `AppController`, and optional `SettingsView` callbacks `apply_analytics_consent` / `reset_analytics_state`), never hardcoding telemetry filesystem paths inside `gui/components/`.

2. **Public Runtime Config Schema v3 (`diagnostics-config.json`)**:
   - Extend `src/diagnostics/release_config.py` with Schema v3 (`schema_version: 3`, `sentry_dsn`, `build_environment`, `package_format`, `posthog_project_api_key`, `posthog_host`) while keeping v1 and v2 loaders backward-compatible.
   - Fail-closed validation rejects non-`phc_` tokens (`phx_` Personal API Keys, `phs_` secret keys, `sntrys_`, credentials), asymmetric Schema v3 PostHog configs (`bool(posthog_project_api_key) != bool(posthog_host)`), non-HTTPS origins, empty/invalid ports, loopback/private/reserved/transition IP literals in release mode, and PostHog web dashboard hosts (`app.posthog.com`, `us.posthog.com`, `eu.posthog.com`) that return HTTP redirects refused by `_RejectAnalyticsRedirects`.
   - Runtime activates PostHog **only** when both `posthog_project_api_key` and `posthog_host` are valid non-empty strings; unconfigured builds (both empty) are a 100% memory and disk no-op (`0` `analytics/` files created).

### 2. Privacy, Session Integrity & Threat Mitigations

1. **Personless Mode + App-Scoped Random `UUIDv4` (`distinct_id`)**:
   - Every event sets `"$process_person_profile": false` and UTHelper never emits `$identify`, `$alias`, or `$set` (creating zero PostHog Person Profiles while preserving native PostHog Unique Users, Stickiness, Retention, Funnels, and Session aggregations on `distinct_id` + `$session_id`).
   - `distinct_id` is a random `uuid4()` persisted in `<_APPDATA_DIR>/analytics/installation.json` (aligned with `main.py:_APPDATA_DIR` across Windows, Android, macOS, and Linux, sibling to `telemetry/pending/`), never derived from `UTH_USERNAME` (MSSV), Moodle `userid`, hostname, MAC, `ANDROID_ID`, or `MachineGuid`.

2. **Strict Anti-Correlation Wall Against Sentry (`src/diagnostics/`)**:
   - Sentry crash reports keep random single-event IDs alongside detailed CPU/GPU/RAM/resolution diagnostics.
   - PostHog events never touch Sentry IDs and restrict super-properties to coarse enums (`app_version`, `release_channel`, `distribution_channel`, `package_format`, `os_family`, `device_class`), preventing timestamp + hardware-fingerprint cross-correlation between crash reports and usage streams.

3. **IP & GeoIP Suppression**:
   - Client payloads enforce `"$ip": null` and `"$geoip_disable": true` on every event, paired with PostHog Project Settings **Discard client IP data = ON** so client IP addresses are dropped prior to persistence.

4. **Strict Authenticated Non-Demo Gate (Zero Crawler / Pre-Login Leakage)**:
   - Analytics stays `INACTIVE` (`0` events, `0` `installation.json` disk writes) until `bool(username and has_credentials and not is_demo_account(username))` AND `UTH_TEST_MODE != "1"` AND `not web_mode`.
   - Unauthenticated `show_login_dialog` views and Google Play Robo / Microsoft Store `uthelper_demo` review crawls generate zero events and zero fake DAUs.

5. **Deferred 30-Minute Session Expiry & RFC 9562 Monotonic `UUIDv7` `$session_id`**:
   - Pure-Python `uuid7_from_ms(unix_ms)` generates RFC 9562 UUIDv7 `$session_id` values compatible with Python 3.11–3.14 (clamping `<= 5s` backward clock drift monotonically and rotating on `> 5s` jumps).
   - Minimizing to tray or backgrounding pauses the `300s` foreground heartbeat, flushes RAM events to the local spool (`< 2ms`), and records `last_foreground_active_utc`. Resuming within `< 30m` (`< 23h` total age) continues the same session seamlessly; expiring (`>= 30m`), switching accounts, or exiting stamps `uth_session_ended` at **`last_foreground_active_utc`** (never `now()`), eliminating midnight DAU spillover, whereas opting out immediately drops the active session with `0` events emitted.

6. **Allow-Listed 12-Event Schema, Spool Safety & Captive-Portal Defense**:
   - All 12 `uth_*` events enforce Pydantic `ConfigDict(extra="forbid")` with fixed `Literal`/`StrEnum` values and coarse duration/count buckets — zero free-form strings, URLs, course names, file names, or grades.
   - Local spool (`<_APPDATA_DIR>/analytics/pending/`) enforces `FILE_FLAG_OPEN_REPARSE_POINT` / `O_NOFOLLOW` + handle volume/file-index verification, bounded to `20` batches (`<= 25` events / `<= 64 KiB` each), `512 KiB` total, and `48h` max age, with in-memory client rate caps (`<= 120` events/session, `<= 300` events per-process rolling 24h).
   - Batch payloads (`POST <posthog_host>/batch/`) omit top-level `sent_at` to preserve ClickHouse `(toDate(timestamp), event, distinct_id, uuid)` retry deduplication, and confirm delivery **only** on HTTP `200` `application/json` with `status in (1, "Ok", "ok")` (`<= 4 KiB`), treating campus Wi-Fi `200 text/html` captive portals as retryable failures.

7. **Independent Consent (`ANALYTICS_CONSENT`), Upgrade Inheritance & Zero-Stall Purge**:
   - `SETTINGS_SCHEMA_VERSION = 6` introduces `ANALYTICS_CONSENT: Literal["not_asked", "enabled", "disabled"] = "enabled"`. Upgrading from v5 without `ANALYTICS_CONSENT` inherits `"disabled"` if `CRASH_REPORTING_CONSENT == "disabled"`, respecting prior privacy opt-outs.
   - Pre-login transparency is surfaced via a compact *Điều khoản & Quyền riêng tư* link directly on `login_dialog.py`.
   - `AnalyticsConsentEpochGate` increments a monotonic epoch under a fast lock, clears RAM queues + `$session_id` + `_distinct_id`, and unlinks `analytics/pending/*` and `analytics/installation.json` without waiting on in-flight socket I/O. Sign-out, account switch, and the stored-settings Debug `"Reset Settings"` action (*Công cụ gỡ lỗi nâng cao → Reset Settings*, unlike the form-only *Đặt lại mặc định* button) likewise rotate/delete `installation.json`.
   - `AnalyticsRuntime.close()` writes any active `uth_session_ended` to the local spool synchronously (`< 2ms`) and invokes `executor.shutdown(wait=False, cancel_futures=True)` so Windows teardown never hangs or corrupts files.

### 3. 1:1 Portability to `crates/uthelper-core` ([ADR 0006](0006-rust-core-tauri-native-migration.md))

Every component in `src/analytics/` and Schema v3 is designed to map 1:1 into `crates/uthelper-core/src/analytics/` during the `ADR 0006` Rust + Tauri v2 + Native Mobile migration:

| Python Boundary (`v2.x` Flet) | Rust Target (`crates/uthelper-core`, zero `tauri`/`pyo3`/`uniffi` deps) | Continuity Guarantee |
| --- | --- | --- |
| `src/analytics/models.py` (`ConfigDict(extra="forbid")`, 12 `uth_*` events) | `serde` `#[derive(Serialize, Deserialize)]` with `#[serde(deny_unknown_fields)]` enums & bucket helpers | Identical PostHog event names, property keys, and `$lib_version` schema |
| `src/analytics/uuid7.py` (`uuid7_from_ms`) | Pure-Rust RFC 9562 monotonic UUIDv7 backed by `uthelper_core::Clock` + CSPRNG | Identical PostHog `$session_id` timestamp extraction |
| `analytics/installation.json` & `analytics/pending/*.json` | `uthelper_core::analytics::{IdentityStore, AnalyticsSpool}` using atomic temp-file rename + reparse/symlink guards | In-place upgrades from Flet to Tauri v2 / Compose / SwiftUI preserve consented `distinct_id` cohorts without data migration loss |
| `src/analytics/transport.py` (`urllib` + `_RejectAnalyticsRedirects`) | `uthelper_core::HttpTransport` trait (`POST <host>/batch/`, redirect refusal, strict JSON `status` ack) | Avoids pulling `posthog-rs` or `openssl-sys` (preventing `ADR 0006` `boring-sys` linker collisions) |
| `AnalyticsConsentEpochGate` & `SETTINGS_SCHEMA_VERSION = 6` | `AtomicU64` consent epoch + `Mutex<SessionState>` exposed coarsely via `uthelper-py` / Tauri commands / `uthelper-ffi` (`CancellationToken`) | Preserves `0ms` UI opt-out purge and `< 2ms` non-blocking shutdown across Desktop and Mobile |

## Consequences

- **Positive**:
  - Accurate, privacy-safe product telemetry (DAU/WAU/MAU, Stickiness, Retention, real foreground session duration, 5-tab/viewer/quiz/sync adoption) at zero Person Profile cost and zero `.aab` / `.msix` dependency bloat.
  - Full compliance with Google Play Data Safety (`App interactions` + `Device or other IDs`, optional, analytics-only, `allowBackup="false"`), Microsoft Store Policies 10.5.1/10.5.2, and `uthelper_demo` crawler isolation.
  - Clean Architecture separation (`analytics` isolated from `core`, `gui`, and `diagnostics`) with wire-compatible Rust portability for `ADR 0006`.
- **Trade-offs**:
  - Deleting `installation.json` on opt-out, sign-out, or account switch means a student who logs out and back in is counted as a new anonymous `distinct_id`—an intentional trade-off that prevents cross-student correlation on shared computers and honors strict revocability.
  - Operators must enable **Discard client IP data = ON** in PostHog Project Settings before provisioning production `POSTHOG_PROJECT_API_KEY` / `POSTHOG_HOST` build variables.

## References

- [`docs/PRIVACY.md`](../PRIVACY.md) — User-facing crash diagnostics and anonymous usage analytics policy
- [`docs/guides/store-distribution-submission.md`](../guides/store-distribution-submission.md) — Google Play Data Safety & Microsoft Store 10.5.1/10.5.2 checklist
- [ADR 0001: Ranh giới refactor và hướng phụ thuộc](0001-refactoring-boundaries.md)
- [ADR 0004: Multi-channel Store and LTS distribution](0004-multi-channel-store-and-lts-distribution.md)
- [ADR 0006: Gradual Migration to Shared Rust Core, Tauri v2 Desktop, and Native Mobile via UniFFI](0006-rust-core-tauri-native-migration.md)
