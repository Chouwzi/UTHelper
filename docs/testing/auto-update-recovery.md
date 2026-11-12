# Auto update recovery: issues #80 and #81

## Baseline and scope

The affected release is `v2.5.0`, commit
`598a1fd3f1799bca3d04a6fd1a6b3f66ed9e3966`. The working tree was clean before
creating `hotfix/2.5.1-auto-update` from `main`. Local validation uses Windows,
Python 3.13.12, Flet 0.86.5, pytest 9.0.2 and Ruff 0.15.6.
The initial focused updater suite passed 61 tests before changes.

This patch addresses the updater portion of #80 and avoids unnecessary waits
and repeat transfers discussed in #81. It does not resolve #80's notification
or tray issues, or #81's installer destination/payload optimization work.

## Confirmed causes

- The downloader imposed a shared 180-second transfer budget. Retries after
  expiry could not make useful progress. A clean EOF before the manifest size
  was treated as corrupt data and the otherwise reusable partial was removed.
- `HTTPResponse.read(64 KiB)` could aggregate multiple socket reads before
  progress/cancellation/deadline checks. A real loopback server trickling 1 KiB
  every 10 ms reproduced a 120 ms budget returning only after 704 ms, with no
  intermediate progress. Using `read1` returned at 126 ms with 9 KiB of progress
  in the same controlled experiment. This addresses client responsiveness,
  not the provider's transfer bandwidth.
- Completed packages were downloaded again after restarting the application;
  only partial-transfer reuse existed. A same-candidate metadata recheck also
  discarded the coordinator's reference to its already verified package.
- Android downloaded on the Python process's daemon worker. Closing the app
  cancelled that owned operation; it had no independent OS download to recover.
- Android's launcher acknowledged success when it queued the bridge coroutine,
  before the native result. `INSTALL_LAUNCHED` and the UI callback could disagree
  or arrive in the opposite order. Native failures did not retain a useful
  validation/permission/intent failure category.
- Download completion immediately opened a confirmation dialog, interrupting
  other work. The banner combined download and verification, and settings did
  not expose progress or recovery actions. The banner's progress bar had zero
  width. The existing modal confirmation also blocked Escape dismissal.

These defects are supported by source behavior and regression tests. They do
not establish which native exception caused the originally reported Android
installer failure: no affected-device logcat or installed signer was available.
The existing published APK manifest already declared installation permission;
adding the permission to the extension manifest also makes its own dependency
contract explicit, rather than claiming a missing final-release permission.

## Resulting behavior

The desktop downloader has a finite 600-second budget, deadline-aware retries,
remaining-time socket limits and single-read streaming between progress/deadline
checks. A valid partial resumes with Range; a server
that ignores Range safely restarts the file. A complete cache entry is used only
after size/SHA-256 verification. Platform identity/signature checks still run
before readiness and again before installation.

Android now uses DownloadManager for the actual transfer. The native service
persists the OS request ID for the exact URL, hash, size and expected Android
identity. Recreating the service can find the same request and file. Checking
for updates only observes a previously user-authorized job; it does not start
an unsolicited transfer. Once candidate discovery runs, that job can be
reattached and verified. When automatic checks are disabled, use the manual
check to discover and reconnect to the pending update.

Closing the app detaches its observer while leaving the OS job intact. Explicit
cancel removes the matching OS job and file. The observer has a finite
30-minute wait; reaching it leaves the job recoverable on retry. Android may
pause a download for network, storage or OS policy. This does not promise to
override Force stop, unavailable storage or device-specific background policy.

The transfer notification is visible only during download, so a completion
notification does not provide an unverified APK installation shortcut. The app
offers installation after size/hash validation; native handoff additionally
checks package ID, expected newer version, manifest signer and installed signer.
The staged APK is retained and reverified for permission/installer retries.
Only `installer_opened` acknowledges handoff; opening permission settings does
not. Neither status means the user finished installing the update.

Download progress, verification and installation have separate states. Users can
continue using the app or settings, cancel a transfer, and open confirmation
from the ready action when convenient. Dismissing confirmation retains the
package. Escape dismisses the dialog and restores focus to the initiating action.
Windows exits only after the existing native launcher acknowledges handoff.

## Provider-live measurement, 12 November 2026

One fresh download on the Windows host used the published
`UTHelper-Setup-2.5.0.exe`, 48,817,112 bytes, SHA-256
`adf83d750656ada1b61c782326f4f27e7ffdea05cbd41722531573428afb217d`.
It was verified without launching or installing it.

| Phase | Initial patch, seconds | With single-read streaming, seconds |
|---|---:|---:|
| GitHub discovery | 1.474 | 1.134 |
| Fresh download and SHA-256 | 22.219 | 17.894 |
| Windows signer/timestamp/product identity verification | 1.315 | 1.040 |
| Second use, rehashing the cached file | 0.084 | 0.038 |

Both second uses forbade network calls and still succeeded. The fresh runs emitted
173 and 160 progress events. This demonstrates working provider discovery, verified
transfer and zero-network reuse. These are two network samples, not a bandwidth
improvement benchmark, installation measurement or a download-plus-setup SLA.
It cannot be compared as a controlled speedup against #81's earlier network
samples. Raw measurements and package files remain outside version control.

## Validation and remaining acceptance

Source tests cover successful discovery/download/verification/confirmation,
truncation and Range recovery, complete cache reuse and tampering, cancellation,
deadline expiry, same-candidate rechecks, permission retry, true native
acknowledgement and observer detachment. The normal full-suite command is:

```powershell
$env:PYTHONPATH='src;extensions/flet_uth_background_sync/src;.'
.venv\Scripts\python.exe -m pytest tests extensions/flet_uth_background_sync/tests -q --tb=short
.venv\Scripts\ruff.exe check src tests scripts
```

The full local Python suite passed **1,671 tests**, skipping 25, and Ruff passed.
The native extension's production debug/release Kotlin compiled with SDK 36;
an initial SDK-35 attempt exposed that the existing AndroidX Core 1.17.0
dependency requires SDK 36. The extension now compiles against 36, without
changing minimum/target SDK.

Robolectric regression tests simulate DownloadManager rows/files and native
permission/intent handoff; they do not download from the real Android provider
or parse/install a signed APK. A stock Temurin JDK 21 resolved the initial local
JDK-image transform failure (`jdk.internal.vm.ci` missing). The local download
lifecycle/metadata/cache tests execute, but Windows cannot execute AndroidX
FileProvider's real path handling: Core 1.17.0 compares canonical paths with a
literal `/`, whereas Windows returns `\`. The two intent-flow tests remain
enabled for Linux CI; no production path or test success is faked to hide this
platform limit. Standalone tests supply realistic pending DownloadManager rows
and resolved provider/installer metadata that a library unit-test app lacks.

The first GitHub APK build succeeded; its new native-test step then failed because
configuring the entire app outside Flet lacked `SERIOUS_PYTHON_SITE_PACKAGES`.
The corrected step uses that build's Gradle wrapper and Flutter embedding JAR
to test the checked-in extension independently. This CI invocation defect is
separate from the updater runtime findings.

A local Flet browser harness rendered the real changed update controls with
synthetic release data, at desktop and 360-pixel width, using light/dark themes.
Keyboard text entry continued during download. Readiness did not open a modal;
the explicit install action opened confirmation, and Escape dismissed it with
focus restored. This is scoped browser UI evidence, not Android lifecycle or
Windows installation acceptance. No customer content was rendered or captured.
The banner now uses the existing primary text token: its contrast against the
banner background is at least 7.49:1 across all nine presets. The previous warning
text color measured 4.44:1 in Steel Light, below the small-text AA threshold.

Required device acceptance before release remains:

1. Android: begin a real signed upgrade, background/lock the device, reclaim
   the UI process and reopen; confirm the same OS request continues/reconnects.
2. Grant unknown-source permission, return and retry without a network transfer;
   observe the real system installer and complete the upgrade. Repeat cancellation
   and wrong hash/package/version/signer cases without weakening checks.
3. Windows isolated profile/VM: upgrade signed EXE and MSI, exercise UAC denial,
   installer cancellation and failure, and confirm app/data/location preservation.
4. Measure actual download, verification, setup and usable launch separately for
   #81's time target. This patch does not claim a universal sub-30-second install.

The patch does not publish a release or change user data/schema. Roll back the
topic changes through a reviewed PR if needed; do not change release credentials
or production branches to work around an unverified device failure.

The single authored project version is prepared as `2.5.1` (Android build number
`2005001`), which is newer than the affected `2.5.0`/`2005000`. Runtime version
assets and platform build versions continue to be generated by the existing
release pipeline; no tag or signed release is created by these source changes.

The installed `v2.5.0` still contains its original updater until it is upgraded.
Before publishing the patch, test discovery and handoff from that installed
binary; supplying an older version string to this source tree is not that test.
If the old updater cannot open the installer, offer the patch's official signed
package from [GitHub Releases](https://github.com/Chouwzi/UTHelper/releases) for a
manual in-place upgrade with the same signer. Do not ask users to uninstall or
reset their data to bootstrap the fix.

## Platform references

- [Android DownloadManager](https://developer.android.com/reference/android/app/DownloadManager)
- [DownloadManager request notification behavior](https://developer.android.com/reference/android/app/DownloadManager.Request)
- [Unknown-source installation access](https://developer.android.com/reference/android/content/pm/PackageManager#canRequestPackageInstalls())
- [Flet asynchronous applications](https://docs.flet.dev/cookbook/async-apps/)
- [Python buffered stream read/read1 behavior](https://docs.python.org/3.11/library/io.html#io.BufferedIOBase.read1)
