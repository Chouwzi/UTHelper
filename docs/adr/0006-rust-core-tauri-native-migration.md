# ADR 0006: Gradual Migration to Shared Rust Core, Tauri v2 Desktop, and Native Mobile via UniFFI

- Status: Accepted (Architecture Roadmap; staged execution across Phases 0–5)
- Date: 2026-11-23
- Extends: [ADR 0001](0001-refactoring-boundaries.md), [ADR 0004](0004-multi-channel-store-and-lts-distribution.md), [ADR 0005](0005-local-office-previews.md)

## Context

Post-Milestone 4 `origin/develop` (`5714b64`) leaves UTHelper functionally complete across Milestones 1–4 (`F1–F19`) and backed by **3,003 passing Python tests** (plus 26 skipped), **37 passing Dart plugin tests**, and zero Ruff lint errors across **170 Python files (85,669 lines) in `src/`** (of which `src/core/` comprises **68 files / 29,843 lines** and `src/gui/` comprises **70 files / 44,170 lines**) plus **180 test files (84,682 lines) in `tests/`**.

At the same time, continuing to build UTHelper on **Flet `0.86.5` + embedded CPython (`>=3.11`, shipping CPython 3.14) + Flutter WebView plugins** has hit a structural ceiling:

1. **Package footprint & runtime overhead**:
   - Google Play (`chplay`) universal `.aab` is **110.7 MB** (`docs/architecture/refactoring-log.md:83`) due to bundling the Flutter engine, CPython runtime, `assets/app.zip`, and uncompressed 16 KB-aligned `.so` binaries.
   - Microsoft Store (`msstore`) `.msix` is **65.09 MB** (`docs/architecture/refactoring-log.md:83`), with typical desktop idle memory around ~120–220 MB and multi-second cold startup.
2. **Packaging & native interop friction**:
   - Windows `flutter_inappwebview_windows` (`6.1.5`) requires injecting `/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS` into MSVC `CL` (`STL1011`, `docs/architecture/refactoring-log.md:86`) and manual NuGet provisioning (`src/platform_utils/quiz_client_bootstrap.py` / `nuget.exe v7.3.1`).
   - Embedded WebView2 on Flet Desktop (`WindowsWebView2Dock` in `src/gui/components/courses/embedded_webview_host.py`) requires a custom PerMonitorV2 DPI STA thread and continuous `GWLP_HWNDPARENT` / `GetClientRect` Z-order synchronization.
   - Android Flet packaging requires a manual `[tool.flet.flutter.pubspec.dependency_overrides]` `jni = "1.2.1"` pin (`pyproject.toml:68-75`) due to `flet-dev/flet#6919` and build-time manifest/DEX stripping (`scripts/prepare_android_flutter_project.py`) to keep `chplay` compliant.
3. **Gradual, zero-downtime mandate**:
   - The project cannot pause bugfixes or store releases for a risky "big-bang" rewrite. Every intermediate phase must keep shipping working Desktop, Android, and iOS releases while preserving all M1–M4 behavioral contracts (`F1–F19`), ADR 0003 (7-asset GitHub LTS), ADR 0004 (`chplay`/`msstore` isolation), and ADR 0005 (offline fixed-page & Office previews).

## Decision

Adopt a **gradual, 6-phase (Phase 0–5) "strangler-fig" migration** centered on a pure Rust domain/sync engine (`uthelper-core`), bridged transitionally to Python/Flet via `PyO3 + maturin` (`uthelper-py`) and permanently to native mobile UIs via `UniFFI` (`uthelper-ffi`), detailed step-by-step in [`docs/architecture/rust-migration-roadmap.md`](../architecture/rust-migration-roadmap.md).

### 1. Stack Selection & Trade-Off Evaluation

#### A. Desktop Stack (PC: Windows / macOS / Linux)

| Candidate | Installer / Store Size | Idle RAM | Cold Startup | UI / WebView / Ecosystem Trade-Offs | Verdict |
|---|---:|---:|---:|---|---|
| **Current: Flet `0.86.5` + CPython** | **65.09 MB** `.msix` | ~120–220 MB | ~1.5–3.0s | Bundles CPython + Flutter + `pypdfium2` + `LocalPreviewServer`; MSVC `STL1011` + `WindowsWebView2Dock` HWND sync friction | **Retire in Phase 3** |
| **Tauri v2 + Svelte 5 + Tailwind v4** | **~2–10 MB** base; **~10–25 MB** real app | **~30–60 MB** | **< 0.5s** | Shared OS WebView2 (Win) / WKWebView (macOS) / WebKitGTK 4.1 (Linux); Svelte 5 compiles to `~3–5 KB` runtime (no Fiber VDOM); `shadcn-svelte` + `Bits UI` + `LayerChart` cover Academic Radar + Calendar; reuses `src/assets/{pdf,office}_viewer/` via `register_asynchronous_uri_scheme_protocol` | **Selected (Phase 3)** |
| Tauri v2 + React 19 + Tailwind | ~3–12 MB base; ~12–28 MB real app | ~45–75 MB | ~0.4–0.7s | Same native WebView engine, but React + ReactDOM (`~42–45 KB+` baseline) + Fiber VDOM retain larger JS heap (`~15–25 MB+` vs `~7–10 MB`) and larger JS chunks | **Fallback if team prefers React** |
| Electron | ~80–150+ MB base; ~150–250+ MB app | ~150–350+ MB | ~1–3s+ | Identical bundled Chromium everywhere, but fails size and RAM goals | Rejected |
| Flutter Desktop (pure Dart/Skia) | ~15–30 MB base; ~30–60 MB app | ~100–200 MB | ~0.5–1.0s | Custom Skia canvas loses native WebView2 HTML/PDF integration unless keeping `flutter_inappwebview_windows` (`STL1011`/HWND pain) | Rejected |
| Dioxus 0.6 / 0.7 Desktop | ~3–5 MB base; ~8–20 MB app | ~15–30 MB | < 0.5s | Wry WebView or Blitz WGPU native renderer; lacks mature `shadcn-svelte` / `LayerChart` UI ecosystem; hot-reload/API churn | Rejected |
| Compose Multiplatform Desktop | ~45–65 MB installer (~105–125 MB on disk) | ~150–250+ MB | ~1.5–3.0s | Bundles JRE (~70 MB) + Skiko native RAM; heavier RAM & startup than Tauri v2 | Rejected |

**Desktop Platform Caveats Accepted**:
- **Linux WebKitGTK (`webkit2gtk-4.1`)**: NVIDIA proprietary drivers + Wayland frequently hit DMABUF blank-window / `Protocol error 71` failures. Tauri entrypoints MUST set `WEBKIT_DISABLE_DMABUF_RENDERER=1` (when unset) prior to `tauri::Builder` on Linux, and Linux Office->PDF fallback uses headless LibreOffice (`soffice`) + `pdf.js`.
- **Windows MSIX (`msstore`)**: MSIX containerizes `AppData` writes under `AppData\Local\Packages\<PFN>\LocalCache\Local\...`. Tauri v2 MUST resolve storage via `app.path().app_data_dir()`, preserve AUMI shortcut registration for Windows Toast notifications (`windows-rs` / `tauri-plugin-notification`), and integrate with `scripts/package_msix.ps1` + `scripts/verify_msix_release.ps1` (ADR 0004).

#### B. Mobile Stack (Android first in Phase 4, iOS in Phase 5)

| Candidate | Android `.aab` | iOS `.ipa` Overhead | 120 Hz & Native Fidelity | Native OS Capability Fit | Verdict |
|---|---:|---:|---|---|---|
| **Current: Flet `0.86.5` + `serious_python`** | **110.7 MB** | Heavy (Flutter + CPython) | Flutter canvas; tab/scroll/WebView composition lag | Requires Python-Dart-Kotlin/Swift MethodChannels + `jni = "1.2.1"` override + manifest stripping | **Retire in Phases 4–5** |
| **Kotlin/Jetpack Compose (Android) + Swift/SwiftUI (iOS) + `UniFFI`** | **~12–22 MB** | **~2–10 MB** over SwiftUI | **100% Native** (Compose / SwiftUI) | Direct access to `WorkManager`, `AlarmManager`, `AndroidKeyStore`, `PdfRenderer` (API 21+, preserving `minSdk 23`), `BGTaskScheduler`, `Keychain`, `PDFKit`, `WebView`/`WKWebView`; reuses existing Kotlin/Swift services in `extensions/flet_uth_background_sync/` | **Selected (Phases 4–5)** |
| Kotlin Multiplatform (KMP) / Compose Multiplatform (CMP) | ~15–25 MB | +10–30 MB (**Skiko** + K/N GC; ~55 MB idle vs ~29 MB SwiftUI) | Shared Skia canvas on iOS (requires tuning bounce/selection handles) | Cannot share UI with Tauri PC anyway; duplicates Skia alongside `WKWebView` on iOS | Rejected |
| Tauri v2 Mobile | ~15–30 MB | ~10–20 MB | WebView DOM UI; inferior swipe-back/keyboard inset physics | Desktop plugins commonly no-op on mobile; still requires custom Kotlin/Swift plugins for `WorkManager`/`BGTaskScheduler`/`AndroidKeyStore`/`Keychain` + JSON-RPC IPC overhead | Rejected |
| Flutter + `flutter_rust_bridge` (FRB v2) | ~25–40 MB | ~20–35 MB | Flutter canvas; keeps `flutter_inappwebview` platform-view overhead | Strict FRB codegen/version lock-in; keeps Flutter engine weight | Rejected |
| Dioxus 0.6 / 0.7 Mobile | ~10–25 MB+ | ~10–25 MB+ | Experimental mobile widgets | Immature `WorkManager`/`Keychain`/`PDFKit`/`InAppWebView` ecosystem | Rejected |

### 2. Cargo Workspace Layout & Strict Decoupling Rules

Create a single Cargo workspace rooted in the repository during Phase 0:

- **`crates/uthelper-core`** (100% pure Rust):
  - Domain models, Academic Radar math/normalizer/planner, Moodle WS/AJAX/HTML parsers, Portal parsers, Notification policy & scheduling planner, Sync/Submission/Quiz state machines, SQLite cache (`rusqlite`), and `PreviewFileCache`.
  - **Hard architectural invariant**: `uthelper-core` **MUST NEVER** depend on `tauri`, `pyo3`, or `uniffi` crates. All host capabilities cross explicit traits (`HttpTransport`, `SecretStore`, `Clock`, `CancellationToken`).
- **`crates/uthelper-py`** (Transitional PyO3 + `maturin` bridge, Phases 0–3):
  - Targets `pyo3 = { version = ">=0.27", features = ["abi3-py311", "extension-module"] }` (single `abi3` wheel covering Python 3.11–3.14+; `PyO3 0.27+` adds the native Python 3.14 support required by `.github/workflows/ci.yml` `['3.12', '3.13', '3.14']` and UTHelper's shipped CPython 3.14 runtime without unsafe forward-compatibility overrides).
  - Uses PyO3 `0.27+` (retaining `Python::detach` / `Python::attach` introduced in `PyO3 0.26` to replace deprecated `allow_threads` / `with_gil`) around all CPU/network work.
  - Crosses the FFI boundary **coarsely** (batch DTOs / JSON / `bytes`, never per-item loops).
  - **Transitional `BackendPort` Guardrail**: Define a Python `BackendPort` protocol in `src/core/` so Desktop & CI exercise `uthelper-py` immediately in Phases 1–3 while **Android Flet (`scripts/build_android.ps1`) keeps a pure-Python `BackendPort` fallback** until Phase 4 native Compose replaces Flet—avoiding fragile `boring-sys`/`serious_python` PEP 738 `.aab` wheel injection unless an isolated CI spike proves `scripts/verify_android_release.py` passes 12/12 checks.
- **`crates/uthelper-ffi`** (Mobile `UniFFI` bridge, Phases 4–5):
  - Thin `uniffi` wrapper exposing `uthelper-core` async/sync use cases and callback interfaces to Kotlin (Android via `cargo-ndk`) and Swift (iOS via `XCFramework`).

### 3. Mandatory Host Capability Traits & Engineering Invariants

1. **`HttpTransport` Trait & Phase 0 Cloudflare TLS/JA3/JA4 Live Spike Gate**:
   - `src/core/client.py:415-430` and `docs/architecture/refactoring-log.md:75` record that UTH Cloudflare WAF previously blocked custom TLS stacks with **HTTP 403**, whereas stdlib `urllib.request` works reliably. Per `.agents/AGENTS.md`, `urllib` MUST NOT be replaced in shipped production code without live verification.
   - `uthelper-core` defines an `#[async_trait] pub trait HttpTransport: Send + Sync` (handling TLS, cookies/redirect origin pinning, bounded streaming, and `Set-Cookie` observation).
   - **Phase 0 Live Spike Gate** (`crates/uth-http-spike`): Before Phase 2 networking migration, test `reqwest` (`rustls` HTTP/2), `reqwest` (`native-tls` + `http1_only`), and `wreq` (`Emulation::Chrome*` / `Safari*` via BoringSSL) against live `courses.ut.edu.vn`, `thnn.ut.edu.vn`, and `portal.ut.edu.vn` endpoints across residential and mobile IPs (`0` HTTP 403s across 50 requests required).
   - **Linker Collision Warning**: `wreq` pulls `boring-sys` (requiring CMake, NASM, Go, Perl/Clang across targets). Workspace crates **MUST NOT** mix `boring-sys` with `openssl-sys` (for example via `reqwest`/`native-tls`, SQLCipher features such as `bundled-sqlcipher-vendored-openssl`, or transitive Tauri crates—note plain `rusqlite`/`libsqlite3-sys` does **not** pull `openssl-sys`), which causes duplicate C symbol linker failures. If `wreq` fails cross-compile or Cloudflare updates WAF rules, `HttpTransport` guarantees immediate host-adapter fallback (`urllib` via `uthelper-py`, WinHTTP/native on PC, `OkHttp` on Android, `URLSession` on iOS).

2. **`SecretStore` Trait (Zero Plaintext Secrets)**:
   - **Desktop (`src-tauri`)**: Implement `SecretStore` using **`keyring-rs`** (Windows Credential Manager, macOS Keychain, Linux Secret Service) rather than `tauri-plugin-stronghold`, avoiding Stronghold's master-password bootstrap UX friction.
   - **Android (Phase 4)**: Implement `SecretStore` using UTHelper's existing **`AndroidKeyStore` AES-GCM vault** (`CredentialVault` and `AppSecretVault` classes in `extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/android/src/main/kotlin/com/uthelper/backgroundsync/security/CredentialVault.kt:23-204`) + `noBackupFilesDir`. **Explicitly avoid** `androidx.security:security-crypto` (`EncryptedSharedPreferences` / `EncryptedFile`), which Google officially deprecated in `1.1.0-alpha07`/`1.1.0` due to StrictMode I/O jank and OEM `KeysetCorruptedException` crashes.
   - **iOS (Phase 5)**: Implement `SecretStore` using native **iOS Keychain** (`Security.framework`).

3. **`UniFFI` Safety, Cancellation & Packaging Invariants (`uthelper-ffi`)**:
   - **Explicit Cancellation**: UniFFI does **not** automatically propagate Kotlin `Job.cancel()` or Swift `Task.cancel()` into Rust Tokio futures or blocking threads. Every async use case MUST accept a `CancellationToken` / return a `TaskHandle` exposing `cancel()`, wired to Kotlin `suspendCancellableCoroutine` and Swift `withTaskCancellationHandler` and polled inside `tokio::select!` and `spawn_blocking` loops.
   - **Non-Blocking Foreign Callbacks**: Foreign callback traits (`HttpTransport`, `SecretStore`, progress observers) are `Send + Sync` and MUST run inside `tokio::task::spawn_blocking`, never directly on Tokio async worker threads, never holding `std::sync::Mutex` across `.await`, and never re-entering Tokio via `Handle::block_on` from a thread that foreign UI loops may block on.
   - **Panic Boundary**: `uthelper-ffi` release profile MUST keep `panic = "unwind"` (never `panic = "abort"`), map all failures to typed `CoreError`, and wrap custom threads/callbacks in `std::panic::catch_unwind` so Rust panics never cross `extern "C"` as `SIGABRT`.
   - **Mobile ABI Packaging**: Android `cargo-ndk` MUST target **16 KB ELF page alignment** (`NDK >= r28` default, or `-C link-arg=-Wl,-z,max-page-size=16384` scoped exclusively to `*-linux-android` on `NDK <= r27`) with `AGP >= 8.5.1` (`useLegacyPackaging = false`). iOS packaging builds `aarch64-apple-ios` + `aarch64-apple-ios-sim` `staticlib`/`cdylib`, renames `uthelper_ffiFFI.modulemap` to `module.modulemap`, and produces an `XCFramework` via `xcodebuild -create-xcframework`.

4. **`WebViewSessionPolicy` & Hybrid Document Viewer Parity (Preserving M3 F15 & M4 F17–F19)**:
   - **General WebView Cookie Persistence ("Hướng A", M3 F15)**:
     - **PC (Tauri v2)**: Use `WebviewWindowBuilder` (or `Window::add_child` only if DPI/focus passes, with `WebviewWindowBuilder` fallback) configured with deterministic per-account `.data_directory(<app_data_dir>/webview2_profile/acct_<sha256[:24]>)` and `.on_navigation(...)` origin checks. Never wipe cookies on general WebView close. On UTHelper logout or account switch, either invoke `clear_all_browsing_data()` *before* closing the live `Webview`/`WebviewWindow` handle, OR close all active WebViews first and delete old `acct_*` directories via bounded async retry/backoff (matching `embedded_webview_host.py:100-126` against `msedgewebview2.exe` file locks) plus a startup sweep of stale `acct_*` directories.
     - **Android (Phase 4)**: Use native `android.webkit.WebView` with `CookieManager.getInstance()` (`setAcceptCookie(true)`, `.flush()` on close/background). Because Android's `CookieManager` is process-global unless using AndroidX WebKit `Profile`, Quiz close MUST expire/delete only Moodle quiz origin cookies (preserving "Hướng A" logins), reserving `removeAllCookies(null)` + `WebStorage.getInstance().deleteAllData()` strictly for UTHelper logout/account switch.
     - **iOS (Phase 5)**: Set Phase 5 minimum deployment target to **`>= iOS 17.0`** (recommended over `flet_uth_background_sync.podspec:14`'s `'13.0'`) so `WKWebView` uses clean per-account **`WKWebsiteDataStore(forIdentifier: accountUuid)`** isolation, removing the identifier store ONLY on logout/account switch. If an iOS 15–16 `WKWebsiteDataStore.default()` fallback is kept, mandate `WKWebsiteDataStore.default().removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), modifiedSince: .distantPast)` on logout/account switch and `WKHTTPCookieStore` origin-filtered purge on Quiz close.
   - **Quiz WebView Speed & No Forced Fullscreen (M4 F17–F19)**:
     - Never force OS fullscreen (even when `QuizLaunchTarget.secure_window == true`).
     - Reuse authenticated `MoodleClient` cookies when cloning quiz clients (`prepare_quiz_entry` contract) with warm HTTP/V8 disk cache retained across quiz opens. On PC Quiz close, NEVER call `clear_all_browsing_data()` on a shared `acct_<sha256[:24]>` directory (which wipes all origins and destroys Microsoft/Portal/Google cookies); instead, use `webview.cookies_for_url(quiz_origin)` + `webview.delete_cookie(...)` (or `ICoreWebView2CookieManager`, or an isolated `quiz_cache_<acct>` `data_directory` if separate browser args are needed) so ONLY Moodle quiz origin cookies are purged while warm HTTP/V8 disk cache and "Hướng A" logins remain intact.
     - On Android/iOS native (Phases 4–5), inject `MoodleSession` cookies directly into `CookieManager` / `WKHTTPCookieStore` rather than proxying Moodle pages through a loopback HTTP server (`CoursesWebViewPane` `/session/...`).
   - **Document Viewer Matrix (Zero `pdfium` Bloat)**:
     - Avoid bundling `pdfium-render` (`~6.5–15 MB per ABI`, or `~15–30+ MB` across multi-ABI bundles).
     - **PC (Tauri v2)**: Register `uth-preview://` via `tauri::Builder::register_asynchronous_uri_scheme_protocol` with `Seek`/`Read` HTTP **`206 Partial Content`** byte-range slicing (avoiding main-thread stalls and full-file `Vec<u8>` RAM spikes on large PDFs) to serve `src/assets/pdf_viewer/` (`pdfjs-dist@4.10.38`) and `src/assets/office_viewer/` (`docx-preview@0.4.1`, `@office-kit/pptx@0.28.0`, `~879 KB`) plus cached files directly from Rust disk/memory, with CSP `worker-src 'self' blob:` for `pdf.worker.min.mjs` under Windows `http://uth-preview.localhost`—**retiring `LocalPreviewServer` (`127.0.0.1`)**. Prefer WebView2 native PDF + ADR 0005 Office COM / LibreOffice SHA-256 PDF cache on Windows, with `pdf.js` as cross-platform fallback (`hayro` evaluated only as optional pure-Rust thumbnail/text helper).
     - **Android (Phase 4)**: Standardize on framework **`android.graphics.pdf.PdfRenderer`** (API 21+, 0 MB native overhead, preserving `extensions/flet_uth_background_sync/.../android/build.gradle:32` `minSdk 23`) for PDFs, and `WebView` + `WebViewAssetLoader` serving local `office_viewer` assets offline for DOCX/PPTX (`androidx.pdf` / `PdfViewerFragment` requires `minSdk 28` + `SdkExtensions` 13 checks on API 31–34, so it is deferred/optional only if `minSdk` is explicitly bumped to `28+`).
     - **iOS (Phase 5)**: Native `PDFKit` (`PDFView`, 0 MB overhead) for PDFs, and `WKWebView` (`loadFileURL` / `WKURLSchemeHandler`) serving `office_viewer` or `QLPreviewController` for DOCX/PPTX.

5. **Release, Distribution & Store Continuity (ADR 0003 & ADR 0004)**:
   - Every migration phase MUST preserve ADR 0003's fail-closed 7-asset GitHub LTS release pipeline (`ipa`, `universal apk`, `arm64 apk`, `exe`, `msi`, `release-manifest.json`, `SHA256SUMS`) and ADR 0004's strict isolation of `chplay` (`.aab`, 16 KB aligned, zero self-updater code/permissions) and `msstore` (`.msix`, runtime `distribution-channel.json` suppression).

## Consequences

- **Positive**:
  - Projected **60–80%+ bundle reduction**: PC installer/MSIX from `65.09 MB` to **`<= 25 MB`**, Android `.aab` from `110.7 MB` to **`<= 22 MB`**, iOS `.ipa` **`<= 18 MB`**, with desktop idle RAM dropping from ~150+ MB to **`<= 65 MB`** and cold startup **`<= 0.6s`**.
  - Single verified Rust domain, parser, sync, and policy codebase shared across Windows, macOS, Linux, Android, and iOS—eliminating duplicate Python/Kotlin Moodle logic (`MoodleWsClient.kt` vs `ws_functions.py`).
  - Zero-downtime incremental delivery: Python/Flet users continue receiving tested releases throughout Phases 0–3 while Rust components prove parity against the 3,003+ test baseline.
  - Eliminates brittle Flet packaging workarounds (`STL1011`, `WindowsWebView2Dock` HWND polling, `jni = "1.2.1"`, `LocalPreviewServer` loopback proxy) once Phases 3–5 complete.
- **Trade-offs & Risks**:
  - **Dual-toolchain complexity (Phases 0–3)**: CI and maintainers must build both Python (`pytest`, `ruff`, `maturin`) and Rust (`cargo test`, `clippy`) until Flet is retired.
  - **Cloudflare WAF sensitivity**: Until the Phase 0 live spike passes across campus/residential networks, Rust HTTP transport remains behind the `HttpTransport` trait with `urllib` / native host fallback. (Once verified and swapped, this ADR supersedes the Python-only `urllib` stasis rule in `.agents/AGENTS.md` for Rust targets.)
  - **Linux & MSIX edge maintenance**: Supporting Linux in Tauri v2 requires WebKitGTK DMABUF workarounds (`WEBKIT_DISABLE_DMABUF_RENDERER=1`) and LibreOffice PDF fallback; Windows MS Store requires wrapping Tauri builds into MSIX with validated `app_data_dir()` and AUMI toast registration.

## References

- [`docs/architecture/rust-migration-roadmap.md`](../architecture/rust-migration-roadmap.md) — Phase 0–5 module map, trait contracts, CI plan, KPIs, and risk register
- [ADR 0001: Ranh giới refactor và hướng phụ thuộc](0001-refactoring-boundaries.md)
- [ADR 0003: Platform release update channel](0003-signed-release-update-channel.md)
- [ADR 0004: Multi-channel Store and LTS distribution](0004-multi-channel-store-and-lts-distribution.md)
- [ADR 0005: Local fixed-page and offline Office previews](0005-local-office-previews.md)
- External engineering references:
  - Tauri v2 Architecture, Custom Protocols & Linux WebKitGTK DMABUF: https://v2.tauri.app/
  - Mozilla UniFFI Async, Cancellation & Foreign Callbacks: https://mozilla.github.io/uniffi-rs/
  - PyO3 `>=0.27` (native Python 3.14; `detach`/`attach` since `0.26`) & `maturin` (`abi3`): https://github.com/PyO3/maturin
  - `wreq` BoringSSL TLS/HTTP2 Impersonation: https://github.com/0x676e67/wreq
  - Android `EncryptedSharedPreferences` Deprecation & 16 KB Page Size: https://developer.android.com/privacy-and-security/cryptography#jetpack_security_crypto_library
  - Svelte 5 & `shadcn-svelte` / `LayerChart`: https://svelte.dev/ , https://www.shadcn-svelte.com/
