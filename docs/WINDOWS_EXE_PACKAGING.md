# Windows EXE Packaging

This project is a Flet desktop app with Windows tray, toast notification, autostart, keyring, and bundled assets. Build on Windows for Windows artifacts.

## Canonical Windows Release Path

Use `flet build windows` for the most robust desktop package. Current Flet docs recommend `flet build` for platform executables/bundles, while `flet pack` remains the PyInstaller-based path when a single-file `.exe` is required.

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

Build and prepare the argument-free autostart alias:

```powershell
flet build windows --output dist\flet-build --verbose
.\.venv\Scripts\python.exe scripts\prepare_windows_bundle.py dist\flet-build
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
.\.venv\Scripts\python.exe scripts\verify_windows_bundle.py dist\flet-build
```

Run the bounded, isolated window/tray smoke matrix:

```powershell
.\scripts\test_windows_bundle_e2e.ps1 `
  -BundleDir dist\flet-build `
  -ObservationSeconds 8
```

The harness uses a temporary `APPDATA`, tests manual-visible,
autostart-visible, and autostart-hidden launches, and terminates only the process
it created. Every wait has a fixed deadline.

Package the verified bundle as the only supported Windows release pair: a
machine-scoped WiX 7 MSI and a Burn bootstrapper EXE. Review and accept the WiX
7 OSMF EULA before setting the required process variable; the build refuses to
restore or execute WiX otherwise.

```powershell
$env:WIX_EULA_ACCEPTED = "wix7"
.\scripts\build_installer.ps1 -BundleDir dist\flet-build -OutputDir dist
```

Release signing requires the PFX path/password and RFC3161 timestamp URL in the
process environment. Verification requires valid timestamped Authenticode on
both files, exact MSI tables, and byte equality between the canonical MSI and
the MSI embedded in Burn. The upgrade harness uses bounded exact-PID processes,
proves failed-upgrade rollback, preserves `%APPDATA%\UTHelper`, rejects
downgrades, and verifies MSI and Burn uninstall.

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
- MSI and Burn are the sole canonical Windows release packages. Legacy MSIX and
  AppInstaller helpers are not part of the exact release inventory.
- Code signing with an RFC3161 timestamp is mandatory for release inventory;
  unsigned local output cannot pass the publication gate.

## Sources

- Flet publishing guide: https://flet.dev/docs/publish/
- Flet Windows packaging: https://flet.dev/docs/publish/windows/
- Flet build CLI: https://flet.dev/docs/cli/flet-build/
- Flet pack CLI: https://flet.dev/docs/cli/flet-pack/
- PyInstaller CLI: https://pyinstaller.org/en/stable/man/pyinstaller.html
- PyInstaller runtime paths: https://pyinstaller.org/en/stable/runtime-information.html
