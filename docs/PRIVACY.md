# Privacy and anonymous crash diagnostics

UTHelper can create a narrowly scoped crash report to help diagnose silent
failures. Remote delivery is opt-in: it requires **explicit consent** in the
first-run dialog or Settings. Choosing “Later” keeps consent undecided. Before
consent is enabled, UTHelper does not construct the Sentry client and makes no
diagnostic network request.

## What a report may contain

The remote schema is an allow-list. A report may contain only:

- schema version, a random per-event ID, a deterministic grouping fingerprint,
  and the occurrence time;
- app version, release channel, and source/packaged install type;
- OS family/version, CPU architecture, Python version, Flet version, and the
  optional Flutter version;
- normalized exception runtime type;
- at most 40 normalized frames containing module, function, source-relative or
  basename-only path, and line number;
- coarse lifecycle phase, foreground/tray/unknown window state, and whether the
  prior run ended uncleanly;
- on a correlated Windows Application Error only, a normalized exception code
  and faulting module basename; and
- for Flutter-runner failures, normalized runtime type and stack symbols encoded
  into the same sanitized exception grouping.

The event ID is random for one event. UTHelper creates no stable device identifier
and sends no installation ID, advertising ID, hardware serial, user ID, or
cross-event identity.

## What is forbidden from remote reports

UTHelper does not upload exception messages or arguments, raw traceback data,
raw log files, local variables, environment variables, command lines, HTTP
requests, headers or bodies, URLs, screenshots, session replay, minidumps, or
the local native-fault file.

It also does not upload a name, email address, username, account ID, password,
token, sesskey, cookie, MoodleSession, Authorization value, absolute home path,
course name, activity title, submission, grade, file content, or any other
Moodle data. Unknown fields are rejected before a report is written to disk.

## Local files, limits, and deletion

On Windows, remotely eligible reports are queued under
`%APPDATA%\UTHelper\telemetry\pending`. The queue retains at most **20 reports**,
**1 MiB** total, and **7 days**. It is non-recursive, rejects links/reparse
points, deduplicates equivalent failures, and uses atomic writes. Operational
logs rotate locally at 2 MiB with three backups. The native fault file is local
only and capped at 256 KiB. A Windows Flutter bridge is capped at 64 KiB and is
deleted after successful import.

Selecting **Disable** and successfully saving Settings applies the choice to the
running diagnostics boundary immediately and attempts to delete the owned queue.
The disabled state also retries queue deletion at the next startup. UTHelper
never deletes unrelated files or follows a link target. Re-enabling affects only
future delivery attempts; it does not recreate deleted reports.

An **unclean exit** means the previous run did not remove its run marker. It is
not definitive proof of a crash: power loss, forced termination, or OS shutdown
can produce the same signal. UTHelper claims a native Windows crash only when a
recent matching Application Error event is available.

## Network and Sentry

Even though the event payload contains no IP address, an IP address is normally
visible to the network endpoint while establishing an HTTPS connection. The
service provider or hosting network may process it under their own policies.
UTHelper configures Sentry with default PII disabled, no breadcrumbs, no tracing,
no replay, and a strict event reconstruction step. Redirects are refused. Only a
confirmed HTTP 2xx response removes a queued report.

**Sentry retention:** the server-side retention period is controlled by the
public Sentry project and must be recorded here before a production ingestion
DSN is enabled. This repository currently treats a missing DSN as
“unconfigured,” retains eligible local reports within the limits above, and does
not claim remote delivery succeeded. For privacy or deletion requests, use a
[private GitHub security advisory](https://github.com/Chouwzi/UTHelper/security/advisories/new).
Non-sensitive questions may use the public issue tracker.

## Synthetic test report

A maintainer may send a **synthetic test report** only from a controlled
development build: configure a public ingestion DSN through the documented
development override, explicitly enable consent, call
`runtime.record_exception(RuntimeError(), AppPhase.GUI)` with an empty synthetic
exception, then perform one bounded delivery flush or restart the app. Never put
a credential or user value in the synthetic exception. Confirm the event in the
intended Sentry project, verify the allowed field list above, record the actual
Sentry retention setting, and delete the test event. The automated transport
tests use a local intercepted endpoint and do not send an external report.
