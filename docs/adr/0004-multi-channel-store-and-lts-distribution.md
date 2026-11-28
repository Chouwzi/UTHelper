# ADR 0004: Multi-channel Store (`chplay`, `msstore`) and GitHub LTS (`apk_lts`, `exe_lts`) distribution

- Status: Accepted
- Date: 2026-11-20
- Updated: 2026-11-26
- Extends: [ADR 0003: Platform release update channel](0003-signed-release-update-channel.md)

## Context

UTHelper originally shipped all production packages through a single GitHub Releases pipeline
(ADR 0003: 7 exact assets covering signed Android sideload APKs, signed Windows MSI/Burn EXE,
and an unsigned iOS sideload IPA). Publishing UTHelper on official app stores introduces
non-negotiable platform compliance requirements that conflict with GitHub self-updating
packages:

1. **Google Play Store (`chplay`)**:
   - Device and Network Abuse policy prohibits self-updating outside Google Play or
     downloading executable `.apk`/`.dex` payloads from external hosts (such as GitHub).
   - `REQUEST_INSTALL_PACKAGES` is a restricted permission explicitly banned for self-updating;
     static and dynamic scanners inspect both the merged `AndroidManifest.xml` and DEX bytecode.
   - Android 15+ (API 35+, transitioning to API 36) mandates `.aab` (Android App Bundle) format,
     **16 KB memory page-size alignment** (`PT_LOAD` `p_align >= 0x4000`, uncompressed native
     libraries via `--no-android-legacy-packaging`), and accurate Play Data Safety disclosures.
   - Unused `FOREGROUND_SERVICE` / `FOREGROUND_SERVICE_DATA_SYNC` permissions trigger review
     rejection without a persistent-notification demo; `usesCleartextTraffic="true"` contradicts
     HTTPS-only transport; `allowBackup="false"` protects local credentials from Auto Backup.
2. **Microsoft Store (`msstore`)**:
   - Microsoft Store Policy 10.2 requires updates to be managed exclusively by the Microsoft
     Store and prohibits downloading or executing external `.msi`/`.exe` installers.
   - Partner Center requires a reserved `Package/Identity` (`Name`, `Publisher="CN=..."`,
     4-part `Version="X.Y.Z.0"`), exact-dimension PNG tile assets (`44x44`, `50x50`, `150x150`,
     `310x150`), and a written submission justification for `<rescap:Capability Name="runFullTrust" />`.
   - MSIX packages must preserve standard AppData and Registry virtualization (no
     `unvirtualizedResources`, no disabled write virtualization) and pass Windows App
     Certification Kit (WACK) validation.
3. **Long-Term Support (LTS) on GitHub Releases (`apk_lts`, `exe_lts`)**:
   - Existing users on sideloaded Android `.apk` and Windows `.msi`/Burn `.exe` depend on
     signed GitHub Releases auto-update and must continue receiving uninterrupted LTS updates
     without regressions.
4. **Apple App Store (iOS) budget constraint**:
   - Enrolling in the Apple Developer Program requires an annual fee ($99 USD/year) that the
     non-commercial student team cannot currently fund.

## Decision

1. **Explicit Distribution Channel Model (`src/core/distribution.py`) & Unified Mobile Package Identity (`io.github.chouwzi.uthelper`)**:
   - Standardize `[tool.flet] org = "io.github.chouwzi"` and mobile `applicationId` / `CFBundleIdentifier` on **`io.github.chouwzi.uthelper`** across Android (`chplay` `.aab`, `apk_lts` `.apk`) and iOS (`ipa`), matching the verified open-source publisher namespace (`github.com/Chouwzi/UTHelper` / `chouwzi.github.io`) without colliding with UTH's official app (`vn.edu.ut.app`) or unowned `.com` domains. Windows MSI `UpgradeCode` GUIDs, `single_instance.py` local mutex naming (`com.uthelper.UTHelper`), Partner Center `MSSTORE_IDENTITY_NAME`, isolated QA/benchmark IDs (`com.uthelper.qa`, `com.uthelper.benchmark`), and the internal Android Kotlin plugin namespace (`com.uthelper.backgroundsync`; `flet_uth_quiz` is pure Dart without an Android Kotlin namespace) remain unchanged.
   - Introduce a pure, testable 5-channel `DistributionChannel` boundary:
     - `chplay`: Google Play Store Android `.aab` (`supports_github_self_update = False`,
       deep link `market://details?id=io.github.chouwzi.uthelper` with HTTPS fallback).
     - `apk_lts`: GitHub Releases Android `.apk` LTS (`supports_github_self_update = True`,
       canonical `applicationId = io.github.chouwzi.uthelper`).
     - `msstore`: Microsoft Store Windows `.msix` (`supports_github_self_update = False`,
       deep link `ms-windows-store://pdp/?ProductId=<StoreProductId>` with web fallback when
       configured).
     - `exe_lts`: GitHub Releases Windows `.msi` / Burn `.exe` LTS
       (`supports_github_self_update = True`).
     - `source`: Local Python development / unsigned iOS sideload fallback.
   - Channel resolution reads build-time staged `distribution-channel.json`
     (`schema_version: 1`, `channel`, optional `store_product_id`, `android_package_id`) from
     `FLET_ASSETS_DIR` (never committed to `src/assets/`). On Windows, an active MSIX package
     identity (`has_package_identity()`) always enforces `msstore` as a fail-closed safeguard
     against accidental self-update activation.
2. **Store Runtime Update Suppression & Compliant UI**:
   - On `chplay` and `msstore`, `AppController` does not instantiate or start
     `UpdateCoordinator` or `GitHubReleaseClient`, emits zero GitHub API traffic, hides
     GitHub auto-update toggles/buttons/banners and Debug force-check actions, and renders
     Store-managed update guidance plus Store deep links and channel-accurate Terms/Privacy
     text (`terms_dialog.py`).
3. **Android `chplay` vs `apk_lts` Build Separation**:
   - `chplay` builds produce a universal 16 KB-aligned `.aab` (`--no-android-legacy-packaging`,
     `targetSdk >= 35`) with `REQUEST_INSTALL_PACKAGES`, `UthUpdateFileProvider`,
     `ApkUpdateDownloadManager.kt`, `ApkUpdateInstaller.kt`, updater MethodChannel handlers,
     `FOREGROUND_SERVICE*`, and `usesCleartextTraffic="true"` completely stripped, and
     `android:allowBackup="false"` enforced.
   - `scripts/verify_android_release.py` validates both `chplay` `.aab` (manifest, DEX symbol
     absence, 16 KB ELF64 `PT_LOAD` alignment, `distribution-channel.json`) and `apk_lts`
     `.apk` builds.
4. **Windows `msstore` MSIX Packaging, Verification & CI Isolation**:
   - Restore MSIX packaging specifically for the `msstore` channel (not GitHub Releases) via:
     - `scripts/generate_msix_assets.py` (deterministic Pillow PNG generation for `44x44`,
       `50x50`, `150x150`, `310x150`).
     - `scripts/package_msix.ps1` (parameterized `IdentityName`, `Publisher`, `StoreProductId`,
       `Channel`, `SignMode=none|test`, 4-part `Version`, clean staging of
       `distribution-channel.json` inside the MSIX bundle without mutating `src/assets/`,
       preserving `UTHelperAutostart.exe` `windows.startupTask`, `runFullTrust`,
       `internetClient`, and AppData virtualization).
     - `scripts/verify_msix_release.ps1` (`makeappx unpack`, manifest/capability/virtualization
       checks, argument-free `StartupTask`, exact PNG IHDR dimensions,
       `distribution-channel.json`, optional `-RunWack` invoking `appcert.exe`).
     - Dedicated `.github/workflows/build-msstore.yml` workflow producing unsigned Partner
       Center `.msix` artifacts and verification evidence.
5. **Preservation of the 7-Asset GitHub LTS Pipeline**:
   - `.github/workflows/release.yml`, `scripts/release_inventory.py`, and Schema 3
     `release-manifest.json` remain strictly locked to the 7 GitHub LTS assets
     (`ipa`, `universal apk`, `arm64 apk`, `exe`, `msi`, `release-manifest.json`,
     `SHA256SUMS`). Store `.aab` and `.msix` artifacts are never mixed into the GitHub LTS
     release directory.
6. **Apple App Store Deferral**:
   - Apple App Store distribution is deferred until after Google Play launch and adoption, at
     which point the team may consider transparent community support/crowdfunding to fund an
     Apple Developer Program membership. Unsigned iOS `.ipa` sideload builds (`CFBundleIdentifier = io.github.chouwzi.uthelper`) remain part of
     the 7-asset GitHub LTS release.
7. **Service-Boundary Reviewer Demo Sandbox (`src/core/demo_sandbox.py`)**:
   - To satisfy Google Play Console **App access** review and automated Pre-launch / Robo testing without exposing real student PII or mutating university servers, a pure-Python sandbox intercepts `MoodleClient`, `PortalClient`, and `SubmissionWorkflow` when signed in with the canonical reviewer account (`uthelper_demo`, verified via constant-time PBKDF2-HMAC-SHA256 without storing plaintext credentials in `src/`).
   - All 5 production tabs (`Tổng quan`, `Môn học`, `Lịch`, `Bảng điểm`, `Cài đặt`) execute 100% real orchestrator/UI rendering paths over `today`-relative fixtures and in-memory PDF/Markdown/ZIP attachments, while outbound `*.ut.edu.vn` requests, native `WorkManager` polling, write mutations, and live UTH LMS WebView/Quiz launches are blocked with explicit Vietnamese user notices.

## Consequences

- **Positive**:
  - Store submissions (`chplay` `.aab` and `msstore` `.msix`) are structurally free of
    self-updating code/permissions and verified automatically before upload.
  - Existing `apk_lts` and `exe_lts` users retain full verified GitHub auto-update support
    and the unchanged fail-closed 7-asset release contract from ADR 0003.
  - Clean Architecture boundaries (`core` never imports `gui`; `distribution.py` is pure and
    independently testable) remain intact.
- **Trade-offs & Operational Notes**:
  - Store and GitHub LTS tracks use different signing keys (Play App Signing vs GitHub APK
    keystore) and Windows installation models (MSIX container vs machine-scoped Program Files
    MSI). Users migrating between GitHub LTS and a Store release must reinstall rather than
    in-place upgrading across tracks.
  - Existing Android `apk_lts` users on legacy `v2.2.8`–`v2.5.0` builds
    (`com.uthelper.uthelper`) will see `"Android đã từ chối gói cập nhật"` if they
    trigger the shipped `v2.2.8`–`v2.5.0` in-app updater against `v2.6.0`
    (superseding the unshipped `v2.5.1` hotfix draft); the `v2.6.0` Release Notes
    must direct them to perform a one-time manual install of the
    `io.github.chouwzi.uthelper` APK from GitHub Releases, sign in again, and
    uninstall the legacy `com.uthelper.uthelper` app. Starting in `v2.6.0`,
    `select_candidate` and `android_updates.py` validate `package.signer_identity`
    before download and fall back to manual GitHub Releases download on any future
    package ID mismatch; all subsequent `apk_lts` releases upgrade in place under
    `io.github.chouwzi.uthelper`.
  - Existing iOS sideload users (`Sideloadly` / `AltStore`) migrating from
    `v2.2.8`–`v2.5.0` (`com.uthelper.UTHelper`) to `v2.6.0`
    (`io.github.chouwzi.uthelper`) must uninstall the old `com.uthelper.UTHelper`
    app before installing the new `.ipa`: iOS implicitly scopes both the app
    sandbox and Keychain (`com.uthelper.app_secrets`, with no explicit
    `kSecAttrAccessGroup`) to `CFBundleIdentifier`, so installing side-by-side
    creates a separate Keychain partition, a duplicate home-screen icon, and can
    exhaust the 3-app free Apple ID active signing cap.
  - Maintainers must supply Partner Center identity values (`MSSTORE_IDENTITY_NAME`,
    `MSSTORE_PUBLISHER`, `MSSTORE_PRODUCT_ID`) and Play Console upload credentials via
    environment variables/workflow inputs rather than committing secrets to the repository.
