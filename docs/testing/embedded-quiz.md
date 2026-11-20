# Embedded Moodle quiz

Tracking: [#84](https://github.com/Chouwzi/UTHelper/issues/84). Implementation
branches from `develop` and returns through a merge-commit pull request. This
document records evidence separately from the remaining acceptance work.

## Contract

Courses and THNN quizzes open directly into the original Moodle attempt inside
a native WebView. On Open, the app authenticates and inspects the overview
without creating an attempt. An unfinished attempt resumes immediately; a new
attempt requires confirmation, with a quiz password when Moodle requires one.
The app submits Moodle's own entry form once and loads its returned attempt or
summary URL. Moodle owns access checks, question rendering, autosave,
navigation, submission and review permissions. Opening an activity's details
does not create an attempt. The app does not parse questions, choose answers,
replay submissions or infer success from navigation events.

The Open button remains visible and disabled when authoritative attempt data
shows no remaining attempts and no unfinished attempt. Unknown availability
stays actionable until the overview can be inspected. Browser/review access
remains available. Windows enters fullscreen only for Moodle securewindow;
ordinary quizzes retain the existing window. Mobile uses the full app surface.

Only trusted HTTPS `/mod/quiz/view.php?id=...` URLs launch the player; `id` is a
course-module identifier, distinct from quiz and attempt identifiers. SCORM and
lesson activities retain their existing browser route. The browser fallback
remains available.

Known Moodle SEB requirements retain the browser route. Unknown site-specific
rules remain enforced by Moodle's original page. No SEB User-Agent, exam key,
TLS exception, script stripping or access-rule bypass is provided. Moodle's
`securewindow` rule is a supported ordinary popup case, not SEB.

Quiz content, answers and credentials are not passed through native quiz
lifecycle events or diagnostics. Opening a quiz authenticates with the saved UTH account through the
target site's normal HTML login, then transfers only that site's session cookie
to the native player before loading the resolved attempt. Courses tokens and cookies are
never used as THNN credentials. The established shared UTH username/password
login remains available on both exact trusted sites. Account passwords do not
cross the quiz bootstrap bridge, and the personal browser's session is not
imported. Quiz passwords are submitted only to Moodle's discovered preflight
form; the app clears its password field after use or cancellation.

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
python src/main.py
```

The Windows source launcher adds the local Python control to its import path and
builds the custom client before startup when the client is missing or local
Flutter extension sources/configuration changed. Flet then selects `build/windows/UTHelper.exe`
instead of the generic desktop client. A failed build marks the in-app quiz
unavailable, leaves the rest of the app available, and exposes the browser
action for the user to choose. `flet run src/main.py` uses the same bootstrap
and adds Python hot reload.
Windows requires WebView2 Runtime and a writable profile directory. Android and
iOS use their system WebViews. Native build dependencies are documented in the
[plugin setup guide](https://inappwebview.dev/docs/intro/).

## Evidence and acceptance

Baseline on 2026-11-15 (Asia/Saigon): `c8b3e19` from `origin/develop`, Windows,
Python 3.13.12, Flet 0.86.5, Flutter 3.44.8 / Dart 3.12.2. The original research
worktree at `89c3b3d` and its uncommitted edits were preserved. Baseline source
suite: **1,631 passed, 25 skipped**. Skipped tests require their declared live or
platform conditions.

Direct-entry source verification: **1,745 passed, 25 skipped** (six existing
Sentry deprecation warnings), including both native extension packages. Ruff and
the Gitflow direction check passed. The extension's Dart analyzer reported no
issues and all six Flutter extension tests passed. These are source checks,
not provider or native-device acceptance. Flutter package pins are Flet 0.86.5
and `flutter_inappwebview` 6.1.5.

Windows compiled the custom Flet client successfully with WebView2. A native
smoke test using that client and the Python source control opened the original
Courses login page inside the fullscreen player. This exposed an event-type
resolution error that compilation and mock UI tests did not cover. Commit
`2408463` fixes the Python lifecycle annotations; four regression cases now
dispatch ready/close/error/external-link through Flet's actual event dispatcher.
The native login page then loaded without the callback error. At that earlier
checkpoint, authentication and quiz interaction had not been accepted.

The owner clarified that opening a quiz must reuse the saved account rather than
show another login page. The initial manual-login behavior is superseded by the
session bootstrap described above. Fresh normal HTML logins with the saved UTH
account succeeded on both Courses and THNN; only the resulting session-cookie
attributes were inspected. No attempt write was made by these checks.

All twelve PR checks passed at `0713d0d`, including the saved-account bootstrap,
Android APK and iOS simulator diagnostic builds. These establish compilation
and source checks of that revision, not device or provider acceptance. The later
direct-entry changes require their own CI and native acceptance.

The saved-account implementation also built successfully on Windows. Its Python
extension tests cover the one-shot DataChannel cookie handoff, secret-free
invoke arguments/control representation, and fixed safe errors. The native
WebView waits without loading the quiz until its site session has been applied.
Android/iOS share a serialized store with cleanup because iOS's default cookie
manager cannot install cookies into a separate incognito store. Native UI
verification at that earlier checkpoint was interrupted by the user's Escape
stop signal before the quiz was opened. The later direct-entry check below
supersedes its automatic-login/rendering status.

Read-only Courses access/catalog/attempt checks on the owner-approved quiz
confirmed `quizaccess_securewindow`, no access-prevention reasons, and an
existing in-progress attempt. These checks did not start, save or finish an
attempt. Private identifiers, captures and credentials stay outside the commit.

On 2026-11-16, the direct-entry provider check authenticated with the saved
account and identified the existing attempt as resumable, securewindow enabled,
without a password preflight. Submitting Moodle's resume form once returned
`/mod/quiz/attempt.php`; before/after attempt identifiers were unchanged and
states remained finished/inprogress. This verifies the provider entry path,
not native rendering, answer saving or submission. Source password tests use
Moodle's separate preflight form, including its hidden form marker and submit
control; no password-protected live quiz has been accepted yet.

The direct-entry Windows client compiled successfully. The first interactive
open showed `entry_outcome_uncertain`; its underlying HTTP failure was not
captured, so a transient connection problem remains a hypothesis. A later
private Flet integration run recorded successful login, overview inspection and
resume to the attempt page (the entry POST completed in about 1.1 seconds). The
owner confirmed that questions were visible in the native player. This is
human-confirmed rendering evidence: the Windows automation helper returned
`failed to activate captured window` and captured another app's pixels, so it
could not establish answer/save/submit interactions. No answer was changed or
submitted by these checks. The owner's narrow-window error screenshot also
exposed clipped status text; the status row now constrains and wraps it.

### Provider and Windows acceptance, 2026-11-18

The native Windows client was operated directly with mouse input against the
owner-approved Courses quiz. The private acceptance harness mounts the production
`DetailView` and `QuizPlayerView` with the compiled WebView extension and saved
account. It does not mock Moodle or question interactions. This establishes the
native quiz component path; it is separate from a packaged app startup test.

| Check | Observed result |
| --- | --- |
| Open/resume | Saved-account login opened the unfinished attempt directly, with no overview/course screen; securewindow entered fullscreen. |
| Save/read back | Changed one existing selection, navigated to another page, closed the player, opened a fresh session, and read back the changed selection. Restored the original selection before submission. |
| Cancel close | The confirmation was dismissed through Continue; the same question and selection remained mounted. |
| Confirm close | Returned to details and restored the original narrow window; reopening resumed the last page. |
| Cancel submission | Moodle's final confirmation was cancelled; a separate WS read still reported the original attempt as inprogress. |
| Submit | Clicked Moodle's final submit once; the review page showed completion and a separate WS read reported that same attempt as finished. Attempt count and identifiers were unchanged. |
| New attempt/cancel | After submission, the remaining-attempt action displayed the app confirmation in the original window. Cancelling left both existing attempts unchanged and created no new attempt. |

This run exposed a real localization defect: the deployed reattempt button is
`Thực hiện lại đề thi`, which the entry classifier previously treated as an
unsupported action. The classifier now recognizes that label as a new attempt,
retaining the explicit confirmation requirement. A regression exercises both
the English label and this observed Vietnamese label. The repaired confirmation
and cancellation path was rerun against the live provider; the remaining attempt
was preserved. No questions, choices, grades, credentials or provider identifiers
are included in this evidence document or PR.

Remaining release checks, distinct from the accepted path above:

- Source: URL/site/account isolation, real quiz positive cases, SEB exclusions,
  deferred HTML login, lease release, duplicate-open prevention, cancel/close,
  status refresh and browser fallback.
- Windows native: packaged app startup, other Moodle-created popup cases,
  keyboard/focus coverage, missing runtime and load errors. Normal-window quizzes
  and exhausted-attempt controls have source coverage; this live sample uses
  securewindow and still has an unused attempt.
- Provider live: a password-protected quiz and accepting creation of a fresh
  attempt. Password preflight and one-shot creation have source coverage; this
  run resumed and submitted the already-started permitted attempt.
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
- [Moodle 4.3.5: attempt entry and preflight checks](https://github.com/moodle/moodle/blob/v4.3.5/mod/quiz/startattempt.php)
- [Moodle 4.3.5: overview and entry forms](https://github.com/moodle/moodle/blob/v4.3.5/mod/quiz/classes/output/renderer.php)
- [Moodle 4.3.5: SEB access rule](https://github.com/moodle/moodle/blob/v4.3.5/mod/quiz/accessrule/seb/rule.php)
- [Moodle App: securewindow handler](https://github.com/moodlehq/moodleapp/blob/v4.3.0/src/addons/mod/quiz/accessrules/securewindow/services/handlers/securewindow.ts)
