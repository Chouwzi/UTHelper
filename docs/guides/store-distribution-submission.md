# Store Distribution & Submission Guide (Google Play, Microsoft Store & LTS)

Status: current operator guide and pre-submission compliance checklist for
UTHelper's multi-channel distribution architecture ([ADR 0004](../adr/0004-multi-channel-store-and-lts-distribution.md)).

## 1. Distribution Channel Matrix

| Channel ID | Platform & Artifact | Distribution Track | Update Mechanism | Self-Update Code/Permissions |
| --- | --- | --- | --- | --- |
| `chplay` | Android `.aab` (universal, 16 KB aligned) | Google Play Store (CH Play) | Google Play-managed (`market://details?id=com.uthelper.uthelper` + HTTPS fallback) | Stripped completely (manifest, Kotlin, DEX, Python) |
| `apk_lts` | Android `.apk` (`universal` + `arm64-v8a`) | GitHub Releases (LTS) | Signed GitHub Releases (`DownloadManager` + `ApkUpdateInstaller`, Schema 3 `sideload`) | Retained |
| `msstore` | Windows `.msix` (`x64`) | Microsoft Store | Microsoft Store-managed (`ms-windows-store://pdp/?ProductId=...`) | Disabled (zero GitHub polling or external installers) |
| `exe_lts` | Windows `.msi` + Burn `.exe` (`x64`) | GitHub Releases (LTS) | Signed GitHub Releases (Authenticode-pinned `WindowsPackageVerifier` + `WindowsPackageLauncher`) | Retained |
| `ios_sideload` | iOS unsigned `.ipa` (`arm64`) | GitHub Releases (LTS) | Manual user re-signing (Sideloadly / AltStore) | Manual release page link only |

> **Cross-channel migration note**: Store builds and GitHub LTS builds use
> separate signing certificates (Play App Signing vs GitHub keystore) and
> Windows packaging containers (MSIX container vs Program Files MSI). Users
> moving between GitHub LTS and a Store channel must uninstall/reinstall rather
> than upgrading in place across tracks.

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
   - Stages `distribution-channel.json` (`{"schema_version": 1, "channel": "chplay", "android_package_id": "com.uthelper.uthelper"}`)
     only during the build and cleans up `src/assets/distribution-channel.json`
     in `finally`.
2. **Verify manifest, permissions, DEX & 16 KB ELF alignment**:
   ```powershell
   python scripts/verify_android_release.py `
     --aab dist/UTHelper-<version>-chplay.aab `
     --channel chplay `
     --version <version> `
     --build-number <build_number> `
     --package-id com.uthelper.uthelper `
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
     check and displays Google Play update info with `market://details?id=com.uthelper.uthelper`
     (falling back to `https://play.google.com/store/apps/details?id=com.uthelper.uthelper`).

### Play Console Submission Steps

- **Play App Signing**: enroll in Play App Signing; sign `.aab` locally/CI with
  the Play **Upload Key** (never reuse the GitHub LTS sideload keystore as an
  untracked secret).
- **Privacy Policy URL**: host `docs/PRIVACY.md` at a stable HTTPS URL and link
  it in Play Console Store Settings and inside the app.
- **Data Safety form**: use the flow inventory below; it is not a completed
  Play Console declaration. Provide local data and credential deletion
  instructions matching [docs/PRIVACY.md](../PRIVACY.md).
- **Personal Developer Account Closed Testing Gate**:
  - Personal Play Console accounts created on or after Nov 13, 2023 must run a
    **Closed Testing** track with at least **12 opted-in testers for 14
    consecutive days** before applying for Production access.

### Data Safety Flow Review

Google Play defines collection as transmitting data off a user's device,
including transmission by app libraries or SDKs and collection through a
WebView whose behavior the app controls. Ephemeral processing still belongs in
the form response. User-initiated transfers may qualify for a sharing exception,
but do not by themselves remove collection. Apply the current
[Google Play Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469)
to the release being submitted.

| Data flow | Recipient and trigger | Purpose | Required or optional | Data Safety review before submission |
| --- | --- | --- | --- | --- |
| Moodle/Portal sign-in and authenticated requests: account ID, credentials, session or service token | Configured UTH Moodle/Portal endpoints over HTTPS when the user signs in or requests authenticated data | App functionality and account management | Required for authenticated features; signing in is a user choice | Map the account ID and credential/session fields to the current form taxonomy. Assess collection and sharing from the actual endpoint and its role; a separate UTHelper backend is not required for an off-device transfer to be in scope. |
| Assignment or quiz submission: answers and user-selected attachments | Configured UTH Moodle/Portal endpoint over HTTPS when the user submits | App functionality and course work | Optional, user-triggered; fields or attachments may be required by the selected task | Review `Files and docs`, `Other user-generated content`, and associated user IDs as applicable. Assess the user-initiated sharing exception separately from collection. |
| Sanitized crash and performance diagnostics | Sentry only when diagnostics delivery is enabled; the app currently enables diagnostics by default and lets users disable them in Settings → Privacy | Troubleshooting and reliability | Optional; users can opt out | Review `Crash logs` and `Diagnostics`, any qualifying identifiers/device context, the actual SDK behavior, encryption, Sentry's processing role, and configured retention/deletion. |
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
   template and bundle verification as [windows-packaging.md](windows-packaging.md)):
   ```powershell
   python scripts/release_metadata.py --pyproject pyproject.toml --runtime-output src/assets/release-version
   python scripts/generate_public_runtime_config.py --sentry-dsn "$env:SENTRY_DSN" --output src/assets/diagnostics-config.json
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
- **AppData virtualization**:
  - UTHelper writes all mutable configuration, caches, and logs exclusively to
    `%APPDATA%\UTHelper` (never inside the read-only MSIX installation folder).
  - `AppxManifest.xml` deliberately omits `unvirtualizedResources` and leaves
    `FileSystemWriteVirtualization` and `RegistryWriteVirtualization` at their
    enabled defaults so Windows isolates writes and cleans them on uninstall.
- **Policy 10.9 (`runFullTrust` written justification)**:
  - Partner Center flags `runFullTrust` as a restricted capability. Paste the
    following justification in Partner Center **Submission Options → Notes for
    certification**:
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
