# Windows build stability and autostart design

Date: 2026-08-03  
Status: Approved revision, implementation in progress  
Branch: `feature/windows-startup-stability`

## Context

The current Flet Windows bundle exits before the GUI starts. A controlled launch of
`build/windows/UTHelper.exe` produced:

```text
Fatal Python error: init_fs_encoding: failed to get the Python codec
LookupError: no codec search functions registered: can't find encoding
```

Flet 0.86 compiles application, package, and runtime Python sources to `.pyc` by
default. `scripts/post_build_cleanup.py` then recursively deletes every `.pyc`,
including `Lib/encodings`, so both the direct bundle and the Inno-installed copy are
unbootable.

Autostart also has separate defects:

- A non-PyInstaller Flet bundle takes the development fallback and appends
  `sys.argv[0]` to the runner executable.
- Looking up the parent process is not a valid fix. Flet embeds Python inside the
  current `UTHelper.exe`; its parent is normally Explorer, a terminal, or an
  installer.
- Settings are persisted even if changing Windows autostart fails.
- A classic `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run` entry is not
  a reliable control plane for an identity-bearing MSIX application.
- The installer does not clean up both the current and legacy Run value names.

## Goals

1. Produce a Windows Flet bundle that starts reliably before it is packaged and
   after an Inno installation.
2. Make the Settings UI read and change the actual Windows autostart state.
3. Support both classic/unpackaged distribution and MSIX without pretending that
   they share the same Windows integration model.
4. Let the user choose whether a Windows-autostart launch is visible or hidden to
   the tray. Manual launches remain visible.
5. Prevent an invisible orphan process when the tray cannot be initialized.
6. Add unit, integration, packaging, and end-to-end evidence for the behavior.
7. Keep changes small, reviewable, and committed by concern on a Gitflow feature
   branch.

## Non-goals

- Starting the app before user logon or as a Windows service.
- Bypassing a startup choice that the user or an administrator disabled in Windows.
- Automatically rebooting the development machine as part of routine tests.
- Supporting non-Windows startup managers in this change.

## Decision summary

Use a single application-facing autostart service with two Windows backends:

| Runtime | Backend | Launch registration |
|---|---|---|
| Source/development | Run key | Quoted `pythonw.exe`, real entry script, `--autostart` |
| Flet portable or Inno install | Run key | Quoted sibling `UTHelperAutostart.exe`, no arguments |
| Identity-bearing MSIX | `Windows.ApplicationModel.StartupTask` | Manifest-declared `UTHelperStartup` task targeting `UTHelperAutostart.exe`, no arguments |

The service exposes structured state and mutation results. The GUI never infers
success from a saved preference and does not write a desired state that Windows
rejected.

## Build crash correction

`scripts/post_build_cleanup.py` must stop deleting compiled Python runtime files.
The release build will use Flet's supported compilation and cleanup settings. The
custom release step will either be removed or narrowed to a small allow-list of
files proven irrelevant to runtime. It must not remove these categories:

- `*.pyc` or `Lib/encodings`;
- package metadata globally;
- `pywintypes*.dll`, `pythoncom*.dll`, or arbitrary native dependencies;
- directories matched only by a common leaf name across the whole bundle.

A bundle verifier will run before Inno or MSIX packaging. It will fail closed when:

- `UTHelper.exe` is absent;
- the Python filesystem encoding package is absent or empty;
- required native runtime files are absent;
- a smoke launch exits during the startup observation window.

The Flet version used for building will be pinned consistently for local and CI
release paths so a patch release cannot silently change the generated runner or
bundle layout.

## Autostart domain model

The Windows adapter will return a structured value instead of a bare Boolean.
Conceptually it contains:

```text
backend: development_run_key | run_key | startup_task | unavailable
state: enabled | disabled | disabled_by_user | disabled_by_policy | unavailable | error
message: user-facing-safe diagnostic
technical_detail: log-only diagnostic
```

Mutation methods return the resulting state read back from Windows. An operation is
successful only when the read-back state matches the requested state.

The public boundary remains platform-focused and contains no Flet controls. GUI code
receives the service through the existing composition path rather than embedding
Registry or WinRT calls in `SettingsView`.

## Unpackaged and Inno backend

The backend detects the current executable with the current-process Windows API
`GetModuleFileNameW`. It must never use the parent process as the application
executable.

Flet 0.86.5's generated desktop entry point interprets any command-line argument as
a development-server launch and does not start the embedded Python production app.
Therefore `UTHelper.exe --autostart` is invalid even though the executable remains
alive. The bundle preparation step creates a byte-for-byte sibling runner alias.
For a packaged Flet runner, the exact Run value is:

```text
"C:\\Program Files\\UTHelper\\UTHelperAutostart.exe"
```

The command uses Windows command-line quoting and supports spaces and non-ASCII path
characters. Development mode is allowed only when all of these are true:

- the current executable is recognizably Python/PythonW;
- the resolved entry script exists and is a file;
- a sibling `pythonw.exe` is used when available to avoid a console window.

The backend reads the Run value before reporting state. An existing entry that does
not match the canonical command is treated as stale/disabled and is replaced only
after an explicit enable request.

Removal is idempotent. Migration and uninstall cleanup cover both `UTHelper` and the
legacy `UTHElearningAlert` value names.

## MSIX backend

The generated manifest declares the alias as the startup executable and passes no
parameters:

```xml
<desktop:Extension
    Category="windows.startupTask"
    Executable="UTHelperAutostart.exe"
    EntryPoint="Windows.FullTrustApplication">
  <desktop:StartupTask
      TaskId="UTHelperStartup"
      Enabled="false"
      DisplayName="UTHelper" />
</desktop:Extension>
```

`desktop` and `uap10` namespaces are included in `IgnorableNamespaces`. Packaging
continues to run `makeappx validate`, so an invalid schema is a release-blocking
failure.

The runtime detects package identity before selecting this backend. It uses
`StartupTask.get_async("UTHelperStartup")`, `request_enable_async()`, `disable()`,
and the returned state. `disabled_by_user` and `disabled_by_policy` are surfaced as
actionable states. The app does not retry or override the user's Windows choice.

The required Python WinRT projection is an explicit Windows build dependency and is
verified inside the built bundle.

## Settings experience

The existing `START_MINIMIZED` preference is retained for compatibility but is
presented with autostart-specific wording:

- `Khởi động cùng Windows`
- `Khi khởi động cùng Windows: Ẩn xuống khay hệ thống`

The second switch is enabled only while Windows autostart is enabled. This avoids a
duplicate setting and makes its scope unambiguous.

When Settings opens, the UI asynchronously reconciles the displayed autostart switch
with the real OS state. Saving follows a transaction-like order:

1. Request the operating-system state change if necessary.
2. Read back the operating-system state.
3. Persist `START_WITH_WINDOWS` only after confirmed success.
4. Persist the visibility preference independently when valid.
5. On failure, restore the control to the actual state and show an actionable
   message.

For `disabled_by_user`, the message directs the user to Windows Settings or Task
Manager. Technical exceptions are logged without leaking filesystem or credential
data into the UI.

## Launch visibility behavior

The packaged runner name `UTHelperAutostart.exe` is the authoritative launch-context
marker. Source/development launches retain `--autostart` because Python receives the
argument directly there. Detection uses the current-process executable, never the
parent process or `sys.argv[0]` alone:

| Launch context | Visibility preference | Initial behavior |
|---|---|---|
| Manual | Any | Show the main window |
| Packaged alias or development `--autostart` | Visible | Show the main window |
| Packaged alias or development `--autostart` | Hidden | Initialize tray, then hide the main window |
| Packaged alias or development `--autostart` | Hidden, tray unavailable | Show the main window and report/log the fallback |

The controller must not hide the window until a usable tray owner exists. Existing
single-instance behavior must still bring a hidden running instance to the foreground
when a manual launch requests it. Choosing hidden autostart therefore requires a tray
owner even when the separate `MINIMIZE_TO_TRAY` preference for ordinary window-close
behavior is off; the two preferences must not accidentally disable each other.

## Installer and upgrade behavior

The Inno installer packages only a verified bundle. Uninstall removes the current
and legacy Run values without deleting unrelated user startup entries. Installation
does not silently enable autostart; the in-app user choice remains authoritative.

MSIX installation declares the task disabled initially. Windows registers it after
the first application launch, and the in-app setting requests enablement. Updates
retain Windows-managed task state where supported; versioned package paths are never
stored manually.

## Test strategy

### Unit tests

- Current-executable discovery and development-mode discrimination.
- Correct quoting for spaces and Unicode paths.
- Canonical Run value comparison, stale entry migration, idempotent removal, and
  read-back failure.
- Package identity backend selection.
- Mapping every `StartupTaskState`, including user/policy-disabled states.
- Settings save success, rejection, rollback, and error messaging.
- Manual/autostart visible/autostart hidden/tray-failure launch matrix.
- Bundle verifier behavior against synthetic complete and damaged bundle trees.

Windows APIs are wrapped behind small seams and faked in unit tests. Tests do not
write the user's production startup entry.

### Windows integration tests

- Use a uniquely named temporary HKCU Run value, verify exact bytes/command semantics,
  then remove it in `finally`.
- Launch both canonical commands and verify the packaged alias and development flag
  reach the same Python launch-context policy.
- Parse and validate the generated MSIX manifest/package with Windows SDK tooling.

### End-to-end gates

1. Clean Flet Windows build.
2. Static bundle verification.
3. Direct bundle smoke launch: process remains alive through the observation window
   and creates expected startup evidence.
4. Visible and hidden alias launch probes, with exact PID cleanup.
5. Inno compilation from the verified bundle and archive/content inspection.
6. MSIX packing, `makeappx validate`, and dependency/content inspection.
7. An opt-in login/reboot checklist or harness verifies actual Windows sign-in launch;
   routine automation must not reboot the user's workstation.

All temporary Registry values, processes, and test install locations are scoped and
cleaned even after failures.

## Delivery sequence

1. Commit this approved design.
2. Write an implementation plan with file-level steps and exact tests.
3. Add failing crash/bundle-verifier tests, then correct the build pipeline.
4. Add failing backend tests, then implement Run-key and StartupTask adapters.
5. Add failing Settings/visibility tests, then wire the GUI and controller.
6. Add packaging tests, manifest/installer changes, documentation, and refactoring
   knowledge updates.
7. Run targeted tests, full suite, Ruff, Windows build, Inno/MSIX gates, and E2E smoke
   tests before reporting completion.

## References

- Flet build CLI: <https://flet.dev/docs/cli/flet-build/>
- Flet compilation and cleanup: <https://flet.dev/docs/publish/>
- Flet 0.86 compile-on-by-default change:
  <https://flet.dev/docs/updates/breaking-changes/v0-86-0/compile-on-by-default/>
- Microsoft desktop packaging extensions:
  <https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/desktop-to-uwp-extensions>
- Microsoft `desktop:Extension` manifest schema:
  <https://learn.microsoft.com/en-us/uwp/schemas/appxpackage/uapmanifestschema/element-desktop-extension>
- Microsoft `desktop:StartupTask` schema:
  <https://learn.microsoft.com/en-us/uwp/schemas/appxpackage/uapmanifestschema/element-desktop-startuptask>
- Microsoft `StartupTask.RequestEnableAsync`:
  <https://learn.microsoft.com/en-us/uwp/api/windows.applicationmodel.startuptask.requestenableasync>
- Microsoft Run and RunOnce keys:
  <https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys>
