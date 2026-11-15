# Embedded Moodle quiz

Tracking: [#84](https://github.com/Chouwzi/UTHelper/issues/84). Implementation
branches from `develop` and returns through a merge-commit pull request. This
document records evidence separately from the remaining acceptance work.

## Contract

Courses and THNN quizzes open their original Moodle overview inside a native
WebView. Moodle owns login, access checks, start/resume, question rendering,
autosave, navigation, summary, submission and review permissions. Opening an
activity's details does not create an attempt. The app does not parse questions,
choose answers, replay submissions or infer success from navigation events.

Only trusted HTTPS `/mod/quiz/view.php?id=...` URLs launch the player; `id` is a
course-module identifier, distinct from quiz and attempt identifiers. SCORM and
lesson activities retain their existing browser route. The browser fallback
remains available.

Known Moodle SEB requirements retain the browser route. Unknown site-specific
rules remain enforced by Moodle's original page. No SEB User-Agent, exam key,
TLS exception, script stripping or access-rule bypass is provided. Moodle's
`securewindow` rule is a supported ordinary popup case, not SEB.

Quiz content, answers and credentials are not passed through control events or
diagnostics. Opening a quiz authenticates with the saved UTH account through the
target site's normal HTML login, then transfers only that site's session cookie
to the native player before loading the overview. Courses tokens and cookies are
never used as THNN credentials. The established shared UTH username/password
login remains available on both exact trusted sites. Passwords do not cross the
native bridge, and the personal browser's session is not imported.

An account/site lease prevents a background HTML login from replacing the active
Moodle session. Existing requests that predate the lease must finish before the
player opens. A separate short-lived hold covers session preparation even if the
view closes while its blocking HTTP request is still finishing. Courses WS reads
retain their existing path. Expiry while a quiz is open must preserve the player
and offer recovery without automatically logging in or replaying a quiz POST.

## Building

The custom control needs its Flutter implementation compiled into the app. An
installed Python wrapper alone is insufficient. Platform `dev_packages` mappings
include the local extension on Windows, Android and iOS; web keeps the browser
route because framing depends on site CSP.

```powershell
python -m pip install -e ".[windows]"
python -m pip install -e extensions/flet_uth_quiz
flet build windows --yes
flet run src/main.py
```

Rebuild the custom client after Dart changes. Windows requires WebView2 Runtime
and a writable profile directory. Android and iOS use their system WebViews.
Native build dependencies are documented in the
[plugin setup guide](https://inappwebview.dev/docs/intro/).

## Evidence and acceptance

Baseline on 2026-11-15 (Asia/Saigon): `c8b3e19` from `origin/develop`, Windows,
Python 3.13.12, Flet 0.86.5, Flutter 3.44.8 / Dart 3.12.2. The original research
worktree at `89c3b3d` and its uncommitted edits were preserved. Baseline source
suite: **1,631 passed, 25 skipped**. Skipped tests require their declared live or
platform conditions.

Implementation source verification: **1,714 passed, 25 skipped** (six existing
Sentry deprecation warnings), including both native extension packages. Ruff and
the Gitflow direction check passed. The extension's Dart analyzer reported no
issues and its Android queue cancellation test passed. These are source checks,
not provider or native-device acceptance. Flutter package pins are Flet 0.86.5
and `flutter_inappwebview` 6.1.5.

Windows compiled the custom Flet client successfully with WebView2. A native
smoke test using that client and the Python source control opened the original
Courses login page inside the fullscreen player. This exposed an event-type
resolution error that compilation and mock UI tests did not cover. Commit
`2408463` fixes the Python lifecycle annotations; four regression cases now
dispatch ready/close/error/external-link through Flet's actual event dispatcher.
The native login page then loaded without the callback error. Authentication,
quiz popup, answer saving and submission have not yet been accepted.

The owner clarified that opening a quiz must reuse the saved account rather than
show another login page. The initial manual-login behavior is superseded by the
session bootstrap described above. Fresh normal HTML logins with the saved UTH
account succeeded on both Courses and THNN; only the resulting session-cookie
attributes were inspected. No attempt write was made by these checks.

All twelve PR checks passed at `2408463`, including Android APK and iOS simulator
diagnostic builds. These establish compilation of that revision, not device or
provider acceptance. Native CI and Windows testing for the later saved-account
session bootstrap must be checked separately.

The saved-account implementation also built successfully on Windows. Its Python
extension tests cover the one-shot DataChannel cookie handoff, secret-free
invoke arguments/control representation, and fixed safe errors. The native
WebView waits without loading the quiz until its site session has been applied.
Android/iOS share a serialized store with cleanup because iOS's default cookie
manager cannot install cookies into a separate incognito store. Native UI
verification of the new automatic login was interrupted by the user's Escape
stop signal before the quiz was opened; automatic login, popup and submission
therefore remain unaccepted, despite successful compilation and source checks.

Read-only Courses access/catalog/attempt checks on the owner-approved quiz
confirmed `quizaccess_securewindow`, no access-prevention reasons, and an
existing in-progress attempt. These checks did not start, save or finish an
attempt. Private identifiers, captures and credentials stay outside the commit.

Before release, record each result independently:

- Source: URL/site/account isolation, real quiz positive cases, SEB exclusions,
  deferred HTML login, lease release, duplicate-open prevention, cancel/close,
  status refresh and browser fallback.
- Windows native: real control registration, Moodle login, popup creation and
  close, keyboard/focus, narrow layout, missing runtime and load errors.
- Provider live: resume the approved attempt, change/save then read back the
  same answer, close/cancel/reopen without losing it, summary, cancel submission,
  submit once and read `finished` from Moodle before claiming success.
- Android/iOS: compile each target, then verify login, popup/session sharing,
  audio, background/resume and close on an actual device or simulator. A build
  alone does not establish working quiz submission.
- THNN: login/review, Cloze, shortanswer, essay and audio; confirm session
  replacement behavior and start/save/resume/finish on a permitted quiz.
- SEB/UTH SEB: use real examples to establish the deployed custom rule names;
  verify exclusion alongside a working non-SEB quiz.

## Primary references

- [Flet 0.86.5: custom extensions and clients](https://github.com/flet-dev/flet/blob/v0.86.5/website/docs/extend/user-extensions.md)
- [InAppWebView: engines and build requirements](https://inappwebview.dev/docs/intro/)
- [Moodle 4.3.5: quiz Web Services](https://github.com/moodle/moodle/blob/v4.3.5/mod/quiz/classes/external.php)
- [Moodle 4.3.5: SEB access rule](https://github.com/moodle/moodle/blob/v4.3.5/mod/quiz/accessrule/seb/rule.php)
- [Moodle App: securewindow handler](https://github.com/moodlehq/moodleapp/blob/v4.3.0/src/addons/mod/quiz/accessrules/securewindow/services/handlers/securewindow.ts)
