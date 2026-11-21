# Windows EXE Packaging

Status: current operator guide for Windows build, verification, signing, and
installer packaging.

This project is a Flet desktop app with Windows tray, toast notification, autostart, keyring, and bundled assets. Build on Windows for Windows artifacts.

## Canonical Windows Release Path

Use `flet build windows` for the desktop application bundle, then package that
exact verified directory as the canonical machine-scoped MSI and Burn EXE.
`flet pack` remains a development-only PyInstaller alternative and is not a
release channel.

Prerequisites:

- Windows 10/11 64-bit.
- Visual Studio 2022 or 2026 with **Desktop development with C++**.
- Developer Mode enabled if the build reports that symlink support is required.
- Flutter SDK available, or let Flet download the version required by the installed Flet release.

Fresh verification before build:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe src\main.py
```

Build and prepare the argument-free autostart alias (if building manually with Visual Studio 2026 / MSVC 14.52+, append `/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS` to `$env:CL` for `flutter_inappwebview_windows` WIL `<experimental/coroutine>` compatibility):

```powershell
$clFlag = "/D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"
if ([string]::IsNullOrWhiteSpace($env:CL)) {
  $env:CL = $clFlag
} elseif ($env:CL -notmatch '(?:^|\s)[/-]D_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS(?:=\S*)?(?=\s|$)') {
  $env:CL = "$($env:CL.Trim()) $clFlag"
}
flet build windows --verbose
.\.venv\Scripts\python.exe scripts\prepare_windows_bundle.py build\windows
```

Flet 0.86 compiles the application and installed Python packages to `.pyc` by
default. These files are runtime inputs, not disposable cache files. Do not run a
recursive `*.pyc` cleanup over the generated bundle.

Flet 0.86.5 treats any desktop command-line argument as a development-server
launch and does not start the embedded production Python app. Packaged autostart
therefore uses the byte-identical `UTHelperAutostart.exe` sibling with no
arguments. `--autostart` is retained only for direct Python development runs.

Verify the bundle before the canonical WiX installer step:

```powershell
.\.venv\Scripts\python.exe scripts\verify_windows_bundle.py build\windows
```

Run the bounded, isolated window/tray smoke matrix:

```powershell
.\scripts\test_windows_bundle_e2e.ps1 `
  -BundleDir build\windows `
  -ObservationSeconds 8
```

The harness uses a temporary `APPDATA`, tests manual-visible,
autostart-visible, and autostart-hidden launches, and terminates only the process
it created. Every wait has a fixed deadline.

Package the verified bundle as the only supported Windows release pair: a
machine-scoped WiX 7 MSI and a Burn bootstrapper EXE. Review and accept the WiX
7 OSMF EULA before setting the required process variable; the build refuses to
restore or execute WiX otherwise.

Burn branding is sourced from the application assets rather than duplicated in
the packaging directory. `src/assets/icon.ico` supplies the executable,
Explorer, Programs and Features, and title-bar icon through `IconSourceFile`;
`src/assets/icon.png` supplies the WixStdBA content logo through `LogoFile`.
Keep the ICO multi-resolution, including 16, 32, 48, and 256 pixel square
frames, so Windows can select a native frame at each shell DPI.

```powershell
$env:WIX_EULA_ACCEPTED = "wix7"
.\scripts\build_installer.ps1 -BundleDir build\windows -OutputDir dist
```

Release signing requires `WINDOWS_SIGNING_PFX_PATH`,
`WINDOWS_SIGNING_PFX_PASSWORD`, and `WINDOWS_TIMESTAMP_URL` in the process
environment. Verification requires valid timestamped Authenticode on
both files, exact MSI tables, and byte equality between the canonical MSI and
the MSI embedded in Burn. The upgrade harness uses bounded exact-PID processes,
proves failed-upgrade rollback, preserves `%APPDATA%\UTHelper`, rejects
downgrades, and verifies MSI and Burn uninstall.

For the project-owned release identities, provision once from a trusted Windows
maintainer machine. The backup path must be absolute, outside every checkout,
new or empty, and included in the maintainer's encrypted offline backup:

```powershell
.\scripts\provision_release_credentials.ps1 `
  -BackupDirectory "D:\UTHelper-release-recovery" `
  -Repository "Chouwzi/UTHelper" `
  -Environment "release"
```

The command uploads encrypted key material through standard input, records only
public identity variables, applies a user-only ACL, and stores recovery secrets
in Windows Credential Manager. It deliberately does not accept the WiX EULA or
configure crash telemetry. The Windows certificate is self-signed and pinned by
SHA-256; installation remains possible, but an untrusted machine may display
Unknown publisher/SmartScreen until the project obtains a publicly trusted code
signing certificate.

Validate the output on a clean Windows profile or VM:

- Launch the built `UTHelper.exe`.
- Log in and refresh Moodle data.
- Open detail view and browser deep link.
- Test tray minimize/restore/exit.
- Test Windows toast/tray notification.
- Enable/disable autostart and confirm the Run value points to installed
  `UTHelperAutostart.exe` with no arguments.
- Confirm settings are written under `%APPDATA%\UTHelper`, not beside the exe.

## Single-File EXE Path

If a literal single `.exe` is required, use `flet pack` / PyInstaller. This is usually more sensitive to hidden imports and bundled data than `flet build`, so keep a debug-console build first.

Debug build:

```powershell
flet pack src\main.py `
  --name UTHelper `
  --icon src\assets\icon.ico `
  --add-data "src\assets;assets" `
  --debug-console `
  --pyinstaller-build-args "--clean" `
  -y
```

Release build:

```powershell
flet pack src\main.py `
  --name UTHelper `
  --icon src\assets\icon.ico `
  --add-data "src\assets;assets" `
  --product-name "UTHelper" `
  --file-description "UTHelper" `
  --product-version "0.1.0" `
  --file-version "0.1.0.0" `
  --company-name "UTHelper" `
  --pyinstaller-build-args "--clean" `
  -y
```

If the executable starts but a dynamic dependency fails, repeat the debug build and add targeted `--hidden-import` values only for the missing module reported in the console. Avoid broad hidden-import lists because they increase size and hide real dependency problems.

## Packaging Notes

- Keep build output outside `src`; `src/build` can be accidentally bundled and previously added over 100 MB of artifact weight.
- Bundle assets to `assets` for PyInstaller/Flet pack. Runtime code now checks both `src/assets` and `assets`.
- A Flet runner embeds Python in the current `UTHelper.exe`. Resolve that current
  process executable; never infer it from the parent process.
- A packaged Run value targets quoted `UTHelperAutostart.exe` with no arguments.
  Source development targets `pythonw.exe`, the real entry script, and
  `--autostart`.
- Prefer `__file__`/bundled paths for read-only assets and `%APPDATA%` for writable config. The app stores settings under `%APPDATA%\UTHelper`.
- The machine-scoped MSI requires elevation to install under Program Files. The
  application and its HKCU autostart switch do not request elevation at runtime.
- MSI and Burn are the sole canonical GitHub LTS Windows release packages
  (`exe_lts`). Store MSIX packages (`msstore`) are built and verified through
  the dedicated Microsoft Store workflow and are never mixed into the 7-asset
  GitHub LTS release inventory.
- Code signing with an RFC3161 timestamp is mandatory for the GitHub LTS release
  inventory; unsigned local MSI/EXE output cannot pass the publication gate.

## Microsoft Store MSIX Packaging (`msstore`)

For Microsoft Store distribution ([ADR 0004](../adr/0004-multi-channel-store-and-lts-distribution.md)),
package the exact verified `build\windows` Flet bundle as an `.msix` using
`scripts/package_msix.ps1`, `scripts/generate_msix_assets.py`, and
`scripts/verify_msix_release.ps1`.

1. **Visual assets**: `scripts/generate_msix_assets.py` deterministically renders
   aspect-ratio-preserving RGBA PNG tiles from `src/assets/icon.png` at the exact
   dimensions required by Microsoft Store Partner Center:
   - `Assets\Square44x44Logo.png` (`44x44`)
   - `Assets\StoreLogo.png` (`50x50`)
   - `Assets\Square150x150Logo.png` (`150x150`)
   - `Assets\Wide310x150Logo.png` (`310x150`)
2. **Build unsigned Partner Center `.msix`** (`-SignMode none`; Partner Center
   re-signs Store submissions automatically) or local sideload `.msix`
   (`-SignMode test`):

```powershell
.\scripts\package_msix.ps1 `
  -BundleDir build\windows `
  -Version "2.5.1.0" `
  -IdentityName "com.uthelper.UTHelper" `
  -Publisher "CN=UTHelper" `
  -StoreProductId "9NBLGGH4NNS1" `
  -Channel msstore `
  -SignMode none `
  -Output dist\UTHelper-2.5.1-msstore.msix
```

   The script stages `distribution-channel.json` (`channel = "msstore"`) inside
   the temporary MSIX stage without touching `src/assets/`, preserves
   argument-free `windows.startupTask` (`UTHelperAutostart.exe`,
   `TaskId="UTHelperStartup"`), declares `rescap:Capability Name="runFullTrust"`
   and `Capability Name="internetClient"`, and keeps full MSIX **AppData
   virtualization** enabled (no `unvirtualizedResources`, no disabled
   `FileSystemWriteVirtualization`/`RegistryWriteVirtualization`).

3. **Verify MSIX integrity and optional WACK certification**:

```powershell
.\scripts\verify_msix_release.ps1 `
  -MsixPath dist\UTHelper-2.5.1-msstore.msix `
  -Channel msstore `
  -IdentityName "com.uthelper.UTHelper" `
  -Publisher "CN=UTHelper" `
  -Version "2.5.1.0" `
  -StoreProductId "9NBLGGH4NNS1" `
  -Output evidence\UTHelper-2.5.1-msstore.msix.verification.json `
  -RunWack
```

   Passing `-RunWack` runs the Windows App Certification Kit (`appcert.exe`)
   against the built `.msix` and asserts `OVERALL_RESULT="PASS"`. CI automation
   is available via `.github/workflows/build-msstore.yml`. See
   [Store distribution & submission guide](store-distribution-submission.md) for
   Partner Center `runFullTrust` justification and pre-submission steps.

## Protected release environment

The tag workflow is fail-closed and does not synthesize credentials. Configure
these values only in the GitHub `release` environment:

- Secrets: `ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASSWORD`,
  `ANDROID_KEY_PASSWORD`, `WINDOWS_PFX_BASE64`, and `WINDOWS_PFX_PASSWORD`.
- Public variables: `ANDROID_KEY_ALIAS`, `ANDROID_SIGNING_CERT_SHA256`,
  `WINDOWS_SIGNING_CERT_SHA256`, `WINDOWS_SIGNER_SUBJECT`,
  `WINDOWS_TIMESTAMP_URL`, `WIX_EULA_ACCEPTED`, and `SENTRY_DSN`.

`WIX_EULA_ACCEPTED=wix7` may be set only after the owner reviews and accepts the
WiX v7 OSMF EULA v1.1 and its applicable revenue threshold. A missing signing
identity, wrong fingerprint, absent EULA acceptance, native
verification failure, or any inventory mismatch stops before public release.

The protected tag must be `vX.Y.Z`, equal the `pyproject.toml` version, and point
to a commit contained in `main`. A successful public release has exactly:

```text
UTHelper-X.Y.Z.ipa
UTHelper-X.Y.Z.apk
UTHelper-X.Y.Z-arm64-v8a.apk
UTHelper-Setup-X.Y.Z.exe
UTHelper-X.Y.Z.msi
release-manifest.json
SHA256SUMS
```

Do not push a release tag until all named environment inputs exist. The IPA is
intentionally unsigned but must pass the iPhoneOS arm64/no-profile verifier; an
unsigned simulator build must never be renamed to the canonical IPA asset.

### GitHub external governance checklist

Repository files cannot enforce these settings by themselves. Before enabling a
production tag, verify in GitHub or through the REST API that:

- repository **Rulesets** actively protect `main` and `develop`: pull request,
  one fresh CODEOWNER approval, resolved conversations, required CI checks,
  merge-commit-only Gitflow history, no force-push, and no deletion;
- a tag ruleset blocks deletion and force-update of `v*` and restricts tag
  creation to the owner/admin bypass role;
- the `release` environment has a required owner review and accepts only tag
  deployments matching `v*`;
- Private Vulnerability Reporting and Dependabot vulnerability alerts are
  enabled, and the repository is configured to delete merged branches;
- all secret names and identity variables above exist, without printing their
  values. `WIX_EULA_ACCEPTED` remains absent until the owner separately accepts
  the applicable WiX license.

If any check is absent, the repository is not yet a protected release source;
the workflow file and CODEOWNERS entry alone are not evidence of enforcement.

## Sources

- Flet publishing guide: https://flet.dev/docs/publish/
- Flet Windows packaging: https://flet.dev/docs/publish/windows/
- Flet build CLI: https://flet.dev/docs/cli/flet-build/
- Flet pack CLI: https://flet.dev/docs/cli/flet-pack/
- PyInstaller CLI: https://pyinstaller.org/en/stable/man/pyinstaller.html
- PyInstaller runtime paths: https://pyinstaller.org/en/stable/runtime-information.html
