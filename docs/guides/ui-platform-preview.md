# Preview mobile UI profiles on desktop

UTHelper can render its Android, iOS, or desktop presentation profile while
continuing to run with the native capabilities of the host operating system.
This is intended for fast visual and interaction checks; it is not an emulator.

## PowerShell

Close any existing UTHelper process first because Windows single-instance handoff
will otherwise show the already-running process with its original profile.

```powershell
cd E:\Projects\UTH-Elearning-Alert\.worktrees\develop
$env:UTHELPER_UI_PLATFORM = "android"
python src/main.py
```

Available values are:

- `android`: mobile SafeArea, touch-sized filters, bottom-sheet filter menus, and
  mobile Settings composition;
- `ios`: the current mobile composition with the profile retained as iOS for future
  platform-specific presentation;
- `desktop`: desktop popup filters and desktop Settings composition, even when the
  runtime is a mobile device.

Clear the override to follow Flet's detected frontend platform again:

```powershell
Remove-Item Env:UTHELPER_UI_PLATFORM -ErrorAction SilentlyContinue
python src/main.py
```

The same profile can be used with the web development host:

```powershell
$env:UTHELPER_UI_PLATFORM = "android"
python src/main.py --web
```

## Safety boundary

The override changes only `UI_PLATFORM` and the `UI_IS_*` presentation flags.
`IS_WINDOWS`, `IS_ANDROID`, `IS_IOS`, and `IS_MOBILE` continue to represent the
real runtime and guard tray integration, notification backends, background workers,
update packages, window lifecycle, and native APIs. For example, Android UI on
Windows still uses Windows notification/update capabilities and never imports the
Android background bridge.

Use an Android emulator or physical device and install the APK when validating
native Back behavior, permissions, notifications, background sync, lifecycle,
performance, file pickers, or packaging.
