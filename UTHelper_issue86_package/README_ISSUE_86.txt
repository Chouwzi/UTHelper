UTHelper – Issue #86: Class Schedule Notifications
===================================================

Scope
-----
- Add optional class-schedule notifications on Windows, Android and iOS.
- Supports both in-person and online classes.
- Select any combination of 1, 2 and 3 hours before class start.
- Cancelled/suspended and already-started classes are skipped.
- Reconcile scheduled reminders after a new Portal timetable snapshot.
- Preserve the original UTHelper deadline notification milestones.
- Does NOT include the separate Issue #85 repeated-deadline enhancement.

How class type is recognized
----------------------------
The documented Portal response does not expose a dedicated delivery-mode field.
The app identifies online sessions only when the room/campus/note fields contain
an explicit marker (Online, Truc tuyen, Zoom, Google Meet, Microsoft Teams,
Webex, etc.). If there is no online marker but a room or campus is provided,
the class is treated as in-person. Ambiguous rows are skipped rather than guessed.

Validation performed for this package
-------------------------------------
- 120 relevant Python tests passed.
- Python compileall passed for src and the Python extension bridge.
- Full test collection was not possible in the build environment because Flet
  is not installed (29 GUI-related test modules fail during import collection).
- Android Gradle build and iOS Xcode build/device notification tests were not
  run in this environment. Those must be verified by GitHub Actions and on
  physical devices/simulators.

Settings
--------
Open Settings > Notifications > Class schedule notifications.
Enable the switch and select any combination of 1h, 2h and 3h.

Integration note
----------------
This is a clean source snapshot based on the original UTHelper ZIP plus Issue #86.
When copying into an existing Git repository, preserve its .git directory.
Remove stale files listed in REMOVE_OLD_85_PATCH_FILES.txt after replacing source.
