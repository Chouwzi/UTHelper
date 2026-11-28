# Store Distribution & Submission Guide (Google Play, Microsoft Store & LTS)

Status: current operator guide and pre-submission compliance checklist for
UTHelper's multi-channel distribution architecture ([ADR 0004](../adr/0004-multi-channel-store-and-lts-distribution.md)).

## 1. Distribution Channel Matrix

| Channel ID | Platform & Artifact | Distribution Track | Update Mechanism | Self-Update Code/Permissions |
| --- | --- | --- | --- | --- |
| `chplay` | Android `.aab` (`io.github.chouwzi.uthelper`, universal, 16 KB aligned) | Google Play Store (CH Play) | Google Play-managed (`market://details?id=io.github.chouwzi.uthelper` + HTTPS fallback) | Stripped completely (manifest, Kotlin, DEX, Python) |
| `apk_lts` | Android `.apk` (`io.github.chouwzi.uthelper`, `universal` + `arm64-v8a`) | GitHub Releases (LTS) | Signed GitHub Releases (`DownloadManager` + `ApkUpdateInstaller`, Schema 3 `sideload`) | Retained |
| `msstore` | Windows `.msix` (`x64`) | Microsoft Store | Microsoft Store-managed (`ms-windows-store://pdp/?ProductId=...`) | Disabled (zero GitHub polling or external installers) |
| `exe_lts` | Windows `.msi` + Burn `.exe` (`x64`) | GitHub Releases (LTS) | Signed GitHub Releases (Authenticode-pinned `WindowsPackageVerifier` + `WindowsPackageLauncher`) | Retained |
| `ios_sideload` | iOS unsigned `.ipa` (`io.github.chouwzi.uthelper`, `arm64`) | GitHub Releases (LTS) | Manual user re-signing (Sideloadly / AltStore) | Manual release page link only |

> **Cross-channel, legacy `apk_lts`, and iOS sideload migration notes**:
> - Store builds and GitHub LTS builds use separate signing certificates (Play
>   App Signing vs GitHub keystore) and Windows packaging containers (MSIX
>   container vs Program Files MSI). Users moving between GitHub LTS and a Store
>   channel must uninstall/reinstall rather than upgrading in place across
>   tracks.
> - **One-time reinstall for legacy `v2.2.8`–`v2.5.0` Android `apk_lts` users
>   (`v2.6.0` Release Notes requirement, superseding the unshipped `v2.5.1`
>   hotfix draft)**: Starting with `v2.6.0`, Android (`chplay` and `apk_lts`)
>   and iOS (`ipa`) unify on `io.github.chouwzi.uthelper`. Because shipped
>   `v2.2.8`–`v2.5.0` Android `apk_lts` builds (`com.uthelper.uthelper`) reject
>   a new `applicationId` after download with `"Android đã từ chối gói cập nhật"`,
>   `v2.6.0` Release Notes must explicitly instruct those users to perform a
>   one-time manual APK install of the `io.github.chouwzi.uthelper` build from
>   GitHub Releases, sign in again, and uninstall the old
>   `com.uthelper.uthelper` app. Starting in `v2.6.0`, the Android updater
>   validates `package.signer_identity` before downloading and falls back to
>   manual GitHub Releases download on any future package ID mismatch;
>   subsequent `apk_lts` releases under `io.github.chouwzi.uthelper` update in
>   place normally.
> - **One-time reinstall for legacy `v2.2.8`–`v2.5.0` iOS sideload users
>   (`Sideloadly` / `AltStore`)**: Because `v2.6.0` changes `CFBundleIdentifier`
>   from `com.uthelper.UTHelper` to `io.github.chouwzi.uthelper` and iOS scopes
>   the app sandbox and Keychain (`com.uthelper.app_secrets`, without an explicit
>   `kSecAttrAccessGroup`) to `CFBundleIdentifier`, iOS sideload users must
>   **uninstall the old `com.uthelper.UTHelper` app first** before installing
>   `UTHelper-2.6.0.ipa` via Sideloadly or AltStore. Skipping uninstall leaves a
>   separate Keychain partition, creates a duplicate app icon, and can hit
>   Apple's 3-app active signing cap on free Apple IDs. After reinstalling, sign
>   in again once.

---

## 2. Google Play Store (`chplay`) Compliance & Submission Checklist

### Technical Build & Automated Gates

1. **Build the Play Store `.aab`**:
   ```powershell
   .\scripts\build_android.ps1 -Target aab -Channel chplay
   ```
   - Uses `--no-android-legacy-packaging` (`extractNativeLibs="false"`) so
     uncompressed native `.so` libraries meet Google Play's **16 KB memory page
     size** mandate for Android 15+ (`targetSdk >= 35`, ready for API 36).
   - Stages `distribution-channel.json` (`{"schema_version": 1, "channel": "chplay", "android_package_id": "io.github.chouwzi.uthelper"}`)
     only during the build and cleans up `src/assets/distribution-channel.json`
     in `finally`.
2. **Verify manifest, permissions, DEX & 16 KB ELF alignment**:
   ```powershell
   python scripts/verify_android_release.py `
     --aab dist/UTHelper-<version>-chplay.aab `
     --channel chplay `
     --version <version> `
     --build-number <build_number> `
     --package-id io.github.chouwzi.uthelper `
     --certificate-sha256 <upload_cert_sha256> `
     --commit-sha <git_sha> `
     --workflow-run-id <run_id> `
     --output evidence/chplay.verification.json
   ```
   The verifier fails closed if any of the following occur:
   - `REQUEST_INSTALL_PACKAGES`, `FOREGROUND_SERVICE`,
     `FOREGROUND_SERVICE_DATA_SYNC`, or `USE_EXACT_ALARM` appears in the merged
     `AndroidManifest.xml`.
   - `UthUpdateFileProvider`, `ApkUpdateDownloadManager`, `ApkUpdateInstaller`,
     `ACTION_MANAGE_UNKNOWN_APP_SOURCES`, or
     `application/vnd.android.package-archive` appears in manifest or any DEX
     file (`classes*.dex`).
   - `android:usesCleartextTraffic="true"` is present or
     `android:allowBackup="false"` is missing.
   - `targetSdkVersion < 35`.
   - Any 64-bit (`arm64-v8a`, `x86_64`) ELF `PT_LOAD` segment has
     `p_align < 0x4000` (16 KB).
   - Any of the 6 required notification/alarm receivers is missing
     (`ScheduledNotificationReceiver`, `ScheduledNotificationBootReceiver`,
     `ActionBroadcastReceiver`, `DeadlineAlarmReceiver`, `RescheduleReceiver`,
     `ClassScheduleAlarmReceiver`).
   - Embedded `distribution-channel.json` is missing or not `"channel": "chplay"`.
3. **Runtime compliance checks**:
   - `SCHEDULE_EXACT_ALARM` gracefully degrades to `setAndAllowWhileIdle(...)`
     in `AlarmScheduler.kt` when denied on Android 14+.
   - `AppController` does not create `UpdateCoordinator` or call GitHub APIs.
   - Settings → System hides the GitHub auto-update toggle/button/debug force
     check and displays Google Play update info with `market://details?id=io.github.chouwzi.uthelper`
     (falling back to `https://play.google.com/store/apps/details?id=io.github.chouwzi.uthelper`).

### Play Console Submission Steps

- **Play App Signing**: enroll in Play App Signing; sign `.aab` locally/CI with
  the Play **Upload Key** (never reuse the GitHub LTS sideload keystore as an
  untracked secret).
- **Privacy Policy URL**: host `docs/PRIVACY.md` at a stable HTTPS URL and link
  it in Play Console Store Settings and inside the app (exposed both on the
  initial sign-in dialog via *Điều khoản & Quyền riêng tư* and under **Settings
  → Privacy**).
- **PostHog Project Privacy Prerequisite**: before embedding
  `POSTHOG_PROJECT_API_KEY` (`phc_...` public project key only; never `phx_` or
  `phs_` secret keys) and `POSTHOG_HOST` (`https://us.i.posthog.com`,
  `https://eu.i.posthog.com`, or an HTTPS reverse proxy — never web UI hosts
  `app.posthog.com`/`us.posthog.com`/`eu.posthog.com`), enable **Discard client
  IP data = ON** under **PostHog Project Settings → Privacy**.
- **Data Safety form**: use the flow inventory below; it is not a completed
  Play Console declaration. Provide local data, credential, and analytics
  `distinct_id` deletion instructions matching [docs/PRIVACY.md](../PRIVACY.md)
  (no developer-hosted account exists; `distinct_id` is never linked to MSSV and
  is wiped immediately via **Settings → Privacy → Phân tích sử dụng ẩn danh →
  Tắt**, **Settings → Account → Đăng xuất**, or app uninstall with
  `android:allowBackup="false"`).
- **Personal Developer Account Closed Testing Gate**:
  - Personal Play Console accounts created on or after Nov 13, 2023 must run a
    **Closed Testing** track with at least **12 opted-in testers for 14
    consecutive days** before applying for Production access.

### Play Console `App access` (Reviewer Demo Credentials)

Because UTHelper requires authentication before showing coursework, schedules,
and grades, Google Play Console **Policy → App content → App access** must
declare **"All or some functionality in my app is restricted"** and provide the
built-in offline Reviewer Demo credentials below:

- **Instruction name**: `Reviewer Demo Account (All 5 Tabs)`
- **Username (`Mã số sinh viên (MSSV)`)**: `uthelper_demo`
- **Password (`Mật khẩu`)**: `UTH-Review-2026!`
- **2-step verification / OTP / VPN / location restrictions**: None required.
- **Any other information required to access your app** (copy-paste verbatim in
  English):

```text
UTHelper is an independent student study companion for Ho Chi Minh City University of Transport (UTH) students. Real accounts require a university-issued student ID (MSSV). For Google Play review and automated testing, please sign in with the built-in Reviewer Demo account above:

1. Launch UTHelper. The app opens directly to the "Đăng nhập UTH" sign-in dialog (dismiss any standard Android OS notification permission prompt if shown).
2. In the "Đăng nhập UTH" sign-in dialog, enter Username `uthelper_demo` in the first field ("Mã số sinh viên (MSSV)") and Password `UTH-Review-2026!` in the second field ("Mật khẩu"), then tap "Đăng nhập" (Sign in).
3. All 5 primary navigation tabs unlock immediately using realistic, date-relative sample university data through the exact same UI, filtering, search, document reader, and GPA calculation code paths as a live student account:
   - Tab 1 ("Tổng quan"): Upcoming assignments, quizzes, attendance, detail sheets, and today's class schedule.
   - Tab 2 ("Môn học"): Enrolled courses, weekly sections, quiz review breakdown, and built-in offline PDF / Markdown / ZIP lecture viewers.
   - Tab 3 ("Lịch"): Monthly assignment calendar and interactive weekly class timetable with session details and course syllabi.
   - Tab 4 ("Bảng điểm"): Multi-semester transcript, 6-axis Academic Radar chart, credit progress, and target GPA planner.
   - Tab 5 ("Cài đặt"): Theme presets, notification preferences, privacy controls, and account switching.
4. Note: File submission mutations and live external `*.ut.edu.vn` LMS WebView/Quiz launches are intentionally read-only in Demo Mode and display a clear informational notice so reviewers and automated crawlers never modify live university records. No 2FA, VPN, or geographic IP allowlisting is required.
```

### Data Safety Flow Review

Google Play defines collection as transmitting data off a user's device,
including transmission by app libraries or SDKs and collection through a
WebView whose behavior the app controls (including authenticated requests to
`courses.ut.edu.vn`, `thnn.ut.edu.vn`, `portal.ut.edu.vn`, and
`portal-publicapp.ut.edu.vn`). Ephemeral processing still belongs in
the form response. User-initiated transfers may qualify for a sharing exception,
but do not by themselves remove collection. Apply the current
[Google Play Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469)
to the release being submitted.

| Data flow | Recipient and trigger | Purpose | Required or optional | Data Safety review before submission |
| --- | --- | --- | --- | --- |
| Moodle/Portal sign-in and authenticated requests: account ID, credentials, session or service token | Configured UTH Moodle/Portal endpoints (`courses.ut.edu.vn`, `thnn.ut.edu.vn`, `portal.ut.edu.vn`, `portal-publicapp.ut.edu.vn`) over HTTPS when the user signs in or requests authenticated data | App functionality and account management | Required for authenticated features; signing in is a user choice | Map the account ID and credential/session fields to the current form taxonomy. Assess collection and sharing from the actual endpoint and its role; a separate UTHelper backend is not required for an off-device transfer to be in scope. |
| Assignment or quiz submission: answers and user-selected attachments | Configured UTH Moodle/Portal endpoint over HTTPS when the user submits | App functionality and course work | Optional, user-triggered; fields or attachments may be required by the selected task | Review `Files and docs`, `Other user-generated content`, and associated user IDs as applicable. Assess the user-initiated sharing exception separately from collection. |
| Sanitized crash and performance diagnostics | Sentry only when diagnostics delivery is enabled; the app currently enables diagnostics by default and lets users disable them in Settings → Privacy | Troubleshooting and reliability | Required (opt-out); enabled by default on launch, and users can disable delivery and purge queued reports at any time in Settings → Privacy | Review `Crash logs` and `Diagnostics`, any qualifying identifiers/device context, the actual SDK behavior, encryption, Sentry's processing role, and configured retention/deletion. |
| Anonymous usage analytics (`App interactions` + random installation `UUIDv4` `distinct_id` in `analytics/installation.json`) | PostHog (`POST <posthog_host>/batch/` over HTTPS with `$process_person_profile: false`, `$ip: null`, `$geoip_disable: true`, and PostHog **Discard client IP data = ON**), active only after authenticated non-demo sign-in when `ANALYTICS_CONSENT == "enabled"` (`0` events on pre-login dialog or `uthelper_demo`) | Analytics (product usage, session duration, DAU/WAU/MAU, and sync health; never advertising or cross-app tracking) | Optional; users can disable **Phân tích sử dụng ẩn danh** at any time in **Settings → Privacy** (immediately halting delivery and deleting `analytics/pending/*` and `analytics/installation.json`), or wipe via Sign out / uninstall (`android:allowBackup="false"`) | Declare `App activity → App interactions` (**Collected: Yes**, **Shared: No**, **Ephemeral: No**, **Optional: Yes**, **Purpose: Analytics**) and `Device or other IDs` (**Collected: Yes**, **Shared: No**, **Ephemeral: No**, **Optional: Yes**, **Purpose: Analytics**) encrypted in transit over HTTPS. |
| User-configured notification integrations | Gmail, Discord, or Telegram endpoint chosen in Settings, when the configured alert is sent | Delivering reminders the user requested | Optional; requires user configuration | Inventory the exact fields and recipient for each integration, then classify them and assess collection/sharing and transport independently. |

Grades and course documents fetched from UTH are displayed or cached on device;
the diagnostics allow-list excludes grades, submissions, file contents, and
other Moodle/Portal data. Treat that as an on-device-only path only after
checking the signed artifact and its SDKs for any other outbound route.

Before completing the form, the release owner must review the signed AAB,
merged manifest, bundled SDKs, diagnostics configuration, actual UTH backend
request/retention behavior, and any enabled integration. Record the selected
Google Play data types, purpose, required/optional status, collection versus
sharing treatment, encryption, retention, and deletion path for each flow.
Do not infer “no collection” or “no sharing” solely from local storage, direct
client-to-UTH requests, a user action, or the absence of a developer-hosted
backend. The owner remains responsible for the final Console answers.

---

## 3. Microsoft Store (`msstore`) Compliance & Submission Checklist

### Technical Build, Visual Assets & Automated Gates

1. **Build and verify the Windows Flet bundle** (same reviewed diagnostics
   template and bundle verification as [windows-packaging.md](windows-packaging.md); on VS 2026 / MSVC 14.52+, append `/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS` to `$env:CL` without clobbering existing flags):
   ```powershell
   $clFlag = "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"
   if ([string]::IsNullOrWhiteSpace($env:CL)) {
     $env:CL = $clFlag
   } elseif ($env:CL -notmatch '(?:^|\s)[/-]D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS(?:=\S*)?(?=\s|$)') {
     $env:CL = "$($env:CL.Trim()) $clFlag"
   }
   python scripts/release_metadata.py --pyproject pyproject.toml --runtime-output src/assets/release-version
   python scripts/generate_public_runtime_config.py --sentry-dsn "$env:SENTRY_DSN" --posthog-key "$env:POSTHOG_PROJECT_API_KEY" --posthog-host "$env:POSTHOG_HOST" --output src/assets/diagnostics-config.json
   flet build windows --build-version <version> --build-number <build_number> --template build/support/flet-build-template-0.86.5-diagnostics.zip --yes --verbose
   python scripts/prepare_windows_bundle.py build/windows
   python scripts/verify_windows_bundle.py build/windows --expected-version <version>
   ```
2. **Generate scaled visual assets and package `.msix`**:
   - For **Partner Center upload** (`-SignMode none`; Microsoft Store re-signs
     uploaded packages automatically):
     ```powershell
     .\scripts\package_msix.ps1 `
       -BundleDir build\windows `
       -Version "<X.Y.Z.0>" `
       -IdentityName "<PartnerCenterIdentityName>" `
       -Publisher "CN=<PartnerCenterPublisherGuid>" `
       -StoreProductId "<StoreProductId>" `
       -Channel msstore `
       -SignMode none `
       -Output dist\UTHelper-<version>-msstore.msix
     ```
   - For **local sideload testing** (`-SignMode test`, creates a temporary
     self-signed certificate and exports `.test-cert.cer` for local trust):
     ```powershell
     .\scripts\package_msix.ps1 `
       -BundleDir build\windows `
       -Version "<X.Y.Z.0>" `
       -IdentityName "com.uthelper.UTHelper" `
       -Publisher "CN=UTHelper" `
       -StoreProductId "9NBLGGH4NNS1" `
       -Channel msstore `
       -SignMode test `
       -Output dist\UTHelper-<version>-msstore-test.msix
     ```
   - `package_msix.ps1` automatically invokes `scripts/generate_msix_assets.py`
     to render exact-size PNG tiles (`Square44x44Logo.png` 44x44,
     `StoreLogo.png` 50x50, `Square150x150Logo.png` 150x150,
     `Wide310x150Logo.png` 310x150) and stages `distribution-channel.json`
     inside the temporary MSIX stage without touching `src/assets/`.
3. **Verify the `.msix` package and run WACK**:
   ```powershell
   .\scripts\verify_msix_release.ps1 `
     -MsixPath dist\UTHelper-<version>-msstore.msix `
     -Channel msstore `
     -IdentityName "<PartnerCenterIdentityName>" `
     -Publisher "CN=<PartnerCenterPublisherGuid>" `
     -Version "<X.Y.Z.0>" `
     -StoreProductId "<StoreProductId>" `
     -Output evidence\UTHelper-<version>-msstore.msix.verification.json `
     -RunWack
   ```
   - Validates `makeappx unpack`, `AppxManifest.xml` (`Identity`, 4-part
     `Version`, `x64`, `rescap:Capability Name="runFullTrust"`,
     `Capability Name="internetClient"`, argument-free `windows.startupTask`
     pointing to `UTHelperAutostart.exe` with `TaskId="UTHelperStartup"`), exact
     PNG IHDR dimensions for all 4 logos, `distribution-channel.json`
     (`channel == "msstore"`), and optionally runs **Windows App Certification
     Kit (WACK)** (`appcert.exe test -appxpackagepath ...`).
   - CI automation is provided by `.github/workflows/build-msstore.yml`
     (manual `workflow_dispatch` with optional Partner Center identity variables
     `MSSTORE_IDENTITY_NAME`, `MSSTORE_PUBLISHER`, `MSSTORE_PRODUCT_ID` and
     optional `run_wack` toggle).

### Microsoft Store Policy & Partner Center Checklist

- **Policy 10.2 (Store-managed updates)**:
  - `msstore` runtime suppresses `UpdateCoordinator` and `GitHubReleaseClient`
    entirely; Settings links to `ms-windows-store://pdp/?ProductId=<ID>`
    (fallback `https://apps.microsoft.com/detail/<ID>`).
- **Policy 10.5.1 & 10.5.2 (Privacy Policy URL, Consent & Non-Personal Telemetry)**:
  - **10.5.1 (Privacy Policy URL)**: Partner Center **Properties → Privacy
    policy URL** MUST point to the hosted HTTPS [docs/PRIVACY.md](../PRIVACY.md)
    (mandatory for all `runFullTrust` packages and network/telemetry traffic),
    matching the in-app *Điều khoản & Quyền riêng tư* dialog accessible directly
    on the initial sign-in screen (`login_dialog.py`) and under **Settings →
    Privacy**.
  - **10.5.2 (Personal Information & Telemetry Consent)**: UTHelper collects
    zero personal information in telemetry—Sentry crash reports carry random
    per-event IDs, and PostHog usage analytics runs strictly in personless mode
    (`$process_person_profile: false`, `$ip: null`, `$geoip_disable: true`,
    PostHog Project Settings **Discard client IP data = ON**, coarse enums only,
    zero MSSV/credentials/courses/grades/hardware serials). Analytics remains
    `INACTIVE` (`0` events and `0` `installation.json` writes) prior to login
    and during `uthelper_demo` certification runs, surfaces pre-login terms
    notice on the sign-in dialog, inherits `"disabled"` on upgrade if crash
    diagnostics were disabled, and provides independent opt-out in **Settings →
    Privacy** that immediately purges `analytics\pending` and
    `analytics\installation.json` within MSIX-virtualized `%APPDATA%\UTHelper`.
- **AppData virtualization**:
  - UTHelper writes all mutable configuration, caches, and logs exclusively to
    `%APPDATA%\UTHelper` (never inside the read-only MSIX installation folder).
  - `AppxManifest.xml` deliberately omits `unvirtualizedResources` and leaves
    `FileSystemWriteVirtualization` and `RegistryWriteVirtualization` at their
    enabled defaults so Windows isolates writes and cleans them on uninstall.
- **Policy 10.9 (`runFullTrust` written justification & Reviewer Demo Account)**:
  - Partner Center flags `runFullTrust` as a restricted capability, and UTHelper
    requires authentication on first launch. Paste the following combined
    justification and built-in offline Reviewer Demo credentials in Partner
    Center **Submission Options → Notes for certification**:
    > UTHelper is a desktop student productivity companion built with Flutter
    > and an embedded Python runtime (Flet). The `runFullTrust` capability is
    > required solely to execute the packaged Win32 desktop runner
    > (`UTHelper.exe`), register user-opt-in Windows StartupTask autostart
    > (`UTHelperAutostart.exe`), display desktop system-tray controls and native
    > toast reminders, store user credentials in Windows Credential Manager, and
    > synchronize student assignments over HTTPS with UTH Moodle/Portal. The app
    > does not elevate privileges, does not modify system files, preserves full
    > MSIX AppData virtualization, and receives all updates exclusively through
    > the Microsoft Store.
    >
    > **Reviewer Demo Account (All 5 Tabs, no 2FA/VPN required)**:
    > Real accounts require a Ho Chi Minh City University of Transport (UTH)
    > student ID. For Microsoft Store certification testing, sign in on the
    > initial "Đăng nhập UTH" dialog with:
    > - Username (`Mã số sinh viên (MSSV)`): `uthelper_demo`
    > - Password (`Mật khẩu`): `UTH-Review-2026!`
    > Signing in unlocks all 5 tabs (`Tổng quan`, `Môn học`, `Lịch`, `Bảng điểm`,
    > `Cài đặt`) using realistic offline sample coursework, PDF/Markdown/ZIP
    > lecture viewers, schedules, and GPA radar charts without modifying live
    > university records.
- **Pre-submission smoke test**:
  - Sideload the `-SignMode test` build on a clean Windows 10/11 VM, verify cold
    launch, Windows Credential Manager login, `StartupTask` toggle,
    `<PFN>!UTHelper` toast notifications, single-instance focus handoff,
    confirm zero requests to `api.github.com`, verify WACK `OVERALL_RESULT=PASS`,
    and confirm clean uninstall.

---

## 4. GitHub Releases LTS (`apk_lts` & `exe_lts`)

GitHub Releases remain a first-class Long-Term Support channel for users who
prefer direct Android `.apk` or Windows `.msi`/Burn `.exe` installers:

- `.github/workflows/release.yml` and `scripts/release_inventory.py` continue to
  enforce the **exact 7-asset release inventory**:
  1. `UTHelper-X.Y.Z.ipa`
  2. `UTHelper-X.Y.Z.apk`
  3. `UTHelper-X.Y.Z-arm64-v8a.apk`
  4. `UTHelper-Setup-X.Y.Z.exe`
  5. `UTHelper-X.Y.Z.msi`
  6. `release-manifest.json` (Schema 3)
  7. `SHA256SUMS`
- Store `.aab` and `.msix` packages are built via separate workflows
  (`.github/workflows/build-android.yml` / `.github/workflows/build-msstore.yml`)
  and are never uploaded into the 7-asset GitHub LTS release transaction.

---

## 5. Apple App Store (iOS) — Deferred Roadmap

- **Current status**: **Deferred** (not scheduled for initial store launch).
- **Reason**: Publishing on the Apple App Store requires an active Apple
  Developer Program membership ($99 USD/year). Because UTHelper is a free,
  non-commercial (`PolyForm Noncommercial 1.0.0`) student project, the team does
  not currently have dedicated budget for Apple's recurring developer fee.
- **Interim iOS support**: UTHelper continues to ship an unsigned `iPhoneOS`
  `arm64` `.ipa` in every 7-asset GitHub LTS release for manual re-signing via
  Sideloadly or AltStore.
- **Future path**: After UTHelper launches smoothly on Google Play (CH Play) and
  Microsoft Store and validates sustained student demand, the team may open a
  transparent community support / crowdfunding effort to cover the Apple
  Developer Program fee and introduce a dedicated `appstore` distribution
  channel following the same compliance architecture as `chplay` and `msstore`.
