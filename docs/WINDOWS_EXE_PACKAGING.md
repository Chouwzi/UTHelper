# Windows EXE Packaging

This project is a Flet desktop app with Windows tray, toast notification, autostart, keyring, and bundled assets. Build on Windows for Windows artifacts.

## Recommended Build Path

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

Verify the bundle before any installer or MSIX step:

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

Build the per-user Inno installer and run the installed-bundle gate:

```powershell
.\scripts\build_installer.ps1
.\scripts\test_windows_installer_e2e.ps1 `
  -InstallerPath .\dist\UTHelper_Setup_v2.1.0.exe `
  -InstallDir build\installer-e2e\UTHelper `
  -ObservationSeconds 8
```

The installer harness refuses to overwrite an existing current-user UTHelper
installation. Install and uninstall processes have explicit deadlines; the
installed bundle is verified and runs the same visibility matrix before it is
uninstalled.

Validate the output on a clean Windows profile or VM:

- Launch the built `UTHelper.exe`.
- Log in and refresh Moodle data.
- Open detail view and browser deep link.
- Test tray minimize/restore/exit.
- Test Windows toast/tray notification.
- For an Inno/portable build, enable/disable autostart and confirm the Run value
  points to installed `UTHelperAutostart.exe` with no arguments.
- For MSIX, confirm the manifest StartupTask is visible in Windows Startup Apps;
  do not expect a classic Run value.
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
- Do not request UAC/admin unless the app truly needs it; autostart uses HKCU and does not require elevation.
- `makeappx pack` validates the manifest while creating the package. The packaging
  script then unpacks the result and verifies the manifest and both runners;
  `makeappx validate` is not a supported Windows SDK command.
- Code signing is recommended for distribution to reduce SmartScreen friction, but signing requires a certificate and should happen after reproducible local builds.

## Sources

- Flet publishing guide: https://flet.dev/docs/publish/
- Flet Windows packaging: https://flet.dev/docs/publish/windows/
- Flet build CLI: https://flet.dev/docs/cli/flet-build/
- Flet pack CLI: https://flet.dev/docs/cli/flet-pack/
- PyInstaller CLI: https://pyinstaller.org/en/stable/man/pyinstaller.html
- PyInstaller runtime paths: https://pyinstaller.org/en/stable/runtime-information.html
