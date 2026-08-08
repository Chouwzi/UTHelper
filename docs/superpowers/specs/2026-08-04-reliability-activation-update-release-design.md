# Reliability, activation, update, and trusted release design

Date: 2026-08-04

Status: Approved by the user's standing instruction to choose and implement the safest complete option

Branch: `codex/reliability-auto-update`

## Context

UTHelper has four related reliability gaps:

- A packaged Windows instance can be hidden in the notification area, but launching
  its shortcut, Start Menu entry, or executable starts another Flet runner instead
  of activating the existing window.
- Opening and closing Settings without an intentional edit can still trigger the
  unsaved-settings confirmation. The form compares controls with mutable global
  state while asynchronous Windows autostart reconciliation is still able to change
  either side of that comparison.
- Some Windows 10 runs reportedly disappear without a Windows error dialog. The
  current Python exception boundary cannot observe every thread, asynchronous task,
  Flutter runner, or native crash, and the unbounded logs are too sensitive and too
  large to upload safely.
- Update checking is unconditional and incomplete. The manifest represents only one
  generic asset, Windows/iOS mostly open a URL, Android does not enforce signer or a
  monotonic version code, and the release workflow does not produce the required
  signed IPA, APK, EXE, and MSI inventory.

The repository is public, but its default branch and tags currently have no actual
GitHub rulesets. Security scanning is disabled, Actions may use mutable tags, release
secrets/environments do not exist, and current CI has known import/path failures.

## Goals

1. Enforce one production UTHelper instance per signed-in Windows user.
2. Treat every manual second launch as an explicit request to show and foreground the
   existing window; an autostart second launch must remain silent.
3. Make Settings dirty-state deterministic after all initial asynchronous state has
   settled.
4. Add privacy-preserving, consent-based automatic crash diagnostics that can report
   Python failures and useful evidence about otherwise silent exits.
5. Enable automatic update checking by default, verify every downloaded package,
   and require an explicit user action before installation or restart.
6. Publish no GitHub release unless signed `.ipa`, `.apk`, `.exe`, and `.msi` assets
   for the same version have all passed platform-specific verification.
7. Apply enforceable GitHub repository, branch, tag, environment, dependency, secret,
   and Actions security rules while keeping serious external contributions possible.
8. Prove behavior with bounded unit, subprocess, packaging, installer, and release
   tests. No command or polling loop may wait forever.

## Non-goals

- Claiming that Python hooks can capture every corruption or native Flutter crash.
- Uploading raw logs, memory dumps, screenshots, Moodle content, credentials, or
  stable device identifiers automatically.
- Silently installing an update or bypassing Android, iOS, or Windows trust prompts.
- Publishing an unsigned placeholder under one of the required release extensions.
- Creating Apple or publicly trusted Windows identities in source code. Their
  external credentials are required release inputs.
- Rebooting a developer or contributor machine as part of an ordinary test run.

## Decision summary

Use the balanced fail-closed architecture:

| Concern | Decision |
|---|---|
| Windows activation | Per-user named mutex plus authenticated named activation event |
| Settings dirty state | Immutable normalized form snapshot captured after initialization |
| Crash diagnostics | Local bounded capture plus sanitized offline spool and consented Sentry transport |
| Windows packages | Signed MSI as canonical install package and signed WiX Burn EXE bootstrapper |
| Android package | Signed APK with monotonic version code and signer verification |
| iOS distribution | Signed IPA release asset; in-app update redirects to TestFlight/App Store |
| Release publication | Exact signed inventory gate; any missing/invalid asset blocks the whole release |
| GitHub governance | Main-first PR workflow, rulesets, protected release environment, pinned Actions |

Native minidump upload and an out-of-process hang watchdog are deferred. They add a
material privacy and maintenance burden and are not necessary to establish the first
safe diagnostic baseline.

## Windows single-instance and activation protocol

### Ownership

A Windows-only bootstrap runs before `ft.run()` and before any tray icon is created.
It derives a namespace from the application identity, release channel, and current
Windows user. Production and development namespaces are different so a source run
does not hijack an installed application.

The first process atomically acquires a named mutex and becomes primary. A later
process that cannot acquire it is secondary. A mutex is required in addition to an
event because an event alone cannot distinguish “no primary yet” from a primary
that has not finished creating its receiver.

Windows kernel objects receive an explicit security descriptor restricted to the
current user and local system. Names contain no username, SID, token, filesystem
path, or other identifying value that could be reported in telemetry.

### Activation messages

The primary owns a named auto-reset event and a receiver thread. The receiver waits
on both the activation event and a shutdown event with a finite wait interval. It
does not call Flet controls directly; it schedules `SHOW` on the application/UI
loop. Shutdown signals the receiver and joins it with a deadline.

Secondary behavior is determined by the existing trusted launch-context detector:

| Secondary launch context | Action |
|---|---|
| Shortcut, Start Menu, packaged EXE, or development manual run | Signal `SHOW`, wait for bounded acknowledgement, exit 0 |
| `UTHelperAutostart.exe` or development `--autostart` | Exit 0 without showing the primary |
| Primary is stale or dies during handoff | Retry ownership once, then either become primary or exit with a diagnostic code |

All entry points use one `WindowActivator`. A `SHOW` request makes the page visible,
clears minimization, requests focus, updates the page, and calls the supported Flet
`window.to_front()` operation. The tray “Open” command uses exactly the same path.
Activation never reapplies start-minimized policy; that policy belongs only to the
first autostart launch.

### Failure behavior

If the Windows primitive cannot initialize, the app logs a sanitized local error and
continues visible as a primary rather than silently exiting. The packaged release
verifier treats this state as a failed Windows smoke test. Unit tests fake the Win32
boundary and never create the production object names.

## Deterministic Settings state

Settings gets a typed `SettingsFormSnapshot` containing normalized values for every
editable control, including debug mode, autostart, start-minimized, automatic update,
and crash-report consent. Normalization occurs at the boundary so `None`, missing
keys, UI strings, integer values, and Boolean values cannot compare differently for
the same semantic setting.

Opening Settings follows an initialization transaction:

1. Increment the view generation and enter `loading` state.
2. Read persisted application settings.
3. Await real Windows autostart state and map it into the draft.
4. Populate every control, including controls currently omitted from reload.
5. Capture the immutable baseline snapshot from the populated controls.
6. Leave `loading`; only subsequent user-originated changes can make the form dirty.

Every asynchronous callback carries its generation. A result for an older view is
discarded. Programmatic initialization does not invoke user-change effects. Closing
during loading either waits for the bounded reconciliation result or closes without
claiming an edit; it never displays a save prompt based on a partial form.

`has_changes()` compares the current normalized snapshot with the immutable baseline.
Successful save replaces the baseline with the verified persisted state. Discard
restores controls from the baseline. A failed OS-level autostart mutation restores
the actual state and leaves only the independently valid settings saved.

## Crash diagnostics and privacy contract

### Local capture layers

One application-owned logging configuration writes rotating UTF-8 files with a
small fixed count and size. Reinitialization is idempotent and cannot attach duplicate
handlers. A redaction filter runs before all persistent handlers.

The Python boundary installs chain-safe handlers for:

- the top-level `ft.run()` call;
- `sys.excepthook`;
- `threading.excepthook`;
- `sys.unraisablehook`;
- the active asyncio exception handler and monitored background tasks;
- Flet page errors;
- `faulthandler` output to a separate bounded local file.

A run-state file is atomically replaced at startup, heartbeat milestones, and clean
shutdown. At the next start, an uncleared marker is classified only as an “unclean
previous exit”, not automatically as a crash. Windows Event Reporting metadata may
be read to correlate timestamp, exception code, and faulting module, but event text
and unrelated application records are not uploaded.

The generated Flutter Windows runner receives a deterministic, version-checked patch
that captures `FlutterError.onError` and `PlatformDispatcher.instance.onError` into a
small local bridge record before forwarding to the original handler. The build fails
if the expected generated-runner anchors changed; it never applies a best-effort
patch to unknown Flet output.

### Consent and anonymous data model

Crash reporting begins disabled until the user makes an explicit first-run choice.
After opt-in, sanitized reports are queued automatically and sent on a later healthy
run. Turning it off atomically deletes the pending queue and stops all diagnostic
network requests. A Settings action shows the exact categories and sends a synthetic
test report without including an application log.

Allowed fields are:

- app version, release channel, install package type, and diagnostic schema version;
- coarse Windows/macOS/Linux/Android/iOS version and CPU architecture;
- Python, Flet, and Flutter runtime versions;
- exception class, scrubbed application module/function/relative source location;
- coarse application phase and foreground/tray state;
- coarse timestamp, report fingerprint, and whether the previous run was unclean.

Forbidden fields include student number, name, email, username, hostname, IP address,
device ID, installation ID, absolute user path, Moodle URL/query, course/assignment
content, notification text, filename, environment variables, local variables,
headers, request/response bodies, cookies, passwords, tokens, JWTs, `sesskey`, raw
logs, screenshots, replays, and automatic memory dumps.

No stable client identifier is generated. Reports are grouped by an event fingerprint
rather than a device identity. This is anonymous at the application layer; the
privacy notice must still state that network infrastructure can observe an IP address
during transport.

### Sanitized spool and transport

The redactor operates on typed allow-listed fields; it is not a final regex pass over
arbitrary exception dictionaries. It normalizes application paths to module-relative
locations, replaces secret patterns, bounds strings and stack frames, and drops all
unknown keys. The sanitized object is validated again before it is written.

Pending reports live under `%LOCALAPPDATA%\UTHelper\telemetry\pending` using atomic
renames. The queue is capped by count, total bytes, and age. Fingerprint deduplication,
rate limits, exponential backoff with jitter, and short connect/read/total timeouts
prevent crash loops or offline machines from hanging startup.

Sentry is the first transport because it supports public ingestion DSNs, grouping,
retention controls, and server-side scrubbing. `send_default_pii`, tracing, profiling,
replay, request bodies, breadcrumbs from raw logs, and local-variable capture remain
disabled. A strict `before_send` repeats the allow-list validation. The DSN is a
build/repository variable, not a secret capable of managing the project. If no DSN
is configured, local capture continues and the UI truthfully reports that automatic
delivery is unavailable.

## Automatic update contract

### Preference and orchestration

`AUTO_UPDATE_ENABLED` defaults to true for new and existing installations that have
no stored value. The Settings view exposes the switch plus a “Check now” action.
Disabling it stops scheduled checks and cancels only cooperative in-flight downloads;
manual checking remains available.

An `UpdateCoordinator` owns scheduling, manifest fetch, selection, download,
verification, user prompting, installer launch, and restart coordination. The main
GUI controller receives status events and does not contain platform installer logic.
Startup remains interactive even when the network is offline: update work has finite
timeouts and runs outside the initial render path.

Automatic behavior is limited to checking and optionally downloading a verified
package. Installation, leaving for TestFlight/App Store, and application restart
always require an explicit user confirmation.

### Manifest schema 2

The signed release publishes one manifest with:

```text
schema_version
release_version
minimum_supported_version
published_at
release_notes_url
packages[]:
  platform, architecture, package_type, install_channel
  url, sha256, size
  signer_identity / certificate fingerprint
  install_strategy
```

Package URLs must use HTTPS and an approved GitHub release host. Selection rejects
ambiguous duplicate candidates. Size and SHA-256 are checked while downloading to a
temporary file. Platform signature/identity verification happens before the file is
renamed into the update cache. Schema 1 remains read-only-compatible for one release
but cannot drive automatic installation when signer metadata is absent.

`minimum_supported_version` is enforced as an urgency/policy signal, never as a way
to install without confirmation. The current application remains usable and exposes
the reason if the update service is unreachable.

### Windows

The canonical installed product is a machine- or user-scoped MSI with stable upgrade
code and explicit installation/channel markers. A WiX Burn EXE wraps that exact MSI
for the requested friendly installer experience. Both assets use the same product
version, are Authenticode-signed by the expected publisher, and are timestamped.

The updater chooses the package matching the current install channel; it never uses
the filename alone. It validates SHA-256 and the WinVerifyTrust/Authenticode signer
chain and expected publisher/fingerprint, prompts the user, launches the installer
with bounded coordination, and exits only after launch acknowledgement. MSI major
upgrade tests prove settings/data retention, shortcut correctness, uninstall cleanup,
and rollback on a deliberately failed upgrade.

MSIX/AppInstaller may remain as optional additional artifacts for existing users, but
they do not satisfy the mandatory MSI/EXE gate and are never mislabeled as those
formats.

### Android

The APK uses one permanently backed-up release keystore. `versionCode` is derived
monotonically from the release/build version and must exceed the installed package.
The updater validates SHA-256, parses the APK, verifies the package ID, version code,
and signing-certificate digest, then invokes Android's installer confirmation. It
cannot silently grant unknown-sources permission or bypass the OS confirmation.

### iOS

The release workflow exports an IPA using an Apple Distribution identity and a
matching provisioning/export profile. The verifier inspects bundle ID, version,
build number, entitlements, embedded profile, and signing chain. A zip of an unsigned
`.app` is rejected even if its extension is `.ipa`.

The application does not self-install an IPA. Update confirmation opens the matching
TestFlight or App Store record. Ad Hoc IPA is acceptable only for a separately named
restricted testing channel whose provisioning profile contains the allowed devices;
it is not presented as public distribution.

## Release workflow and exact inventory gate

A release begins from a protected `v*` tag whose commit is on `main`. The version is
validated across Python metadata, Flet configuration, Android, Apple, MSI, bootstrapper,
and update manifest before any package build.

Platform jobs build and test independently, but publication is a final atomic job in
the protected `release` environment. It downloads artifacts by immutable workflow-run
identity and rejects unexpected duplicates. Required inventory is exactly one signed
asset for each of:

- `UTHelper-<version>.ipa`;
- `UTHelper-<version>.apk`;
- `UTHelper-Setup-<version>.exe`;
- `UTHelper-<version>.msi`.

Optional `.msix` and `.appinstaller` assets are allowed by an explicit allow-list.
The gate validates magic/container format, embedded product version and ID, architecture,
signatures, timestamp where applicable, SHA-256 list, update manifest references, and
bounded installation/package smoke checks. Only then does it create a non-draft GitHub
release and upload all assets in one controlled step. Any missing credential, failed
signature, missing package, or test failure produces no release.

Pull requests may create clearly labeled unsigned/non-installable diagnostic artifacts
where platform restrictions require it. Those artifacts never enter the publication
job and never use the mandatory release filenames.

## GitHub repository governance

Repository mutation is sequenced so rules cannot lock maintainers behind broken
checks:

1. Correct existing CI import/path failures and add the new required workflows.
2. Pin every third-party Action to a reviewed full commit SHA, disable credential
   persistence where checkout writes are unnecessary, and assign least permissions
   per job.
3. Add `CODEOWNERS`, `SECURITY.md`, `CONTRIBUTING.md`, a pull-request template,
   Dependabot configuration, dependency review, CodeQL, and a real failing dependency
   audit rather than `|| true`.
4. Make `main` the default branch, allow squash merge, and automatically delete
   merged feature branches.
5. Enable secret scanning, push protection, validity/non-provider checks when GitHub
   exposes them, Dependabot alerts/security updates, private vulnerability reporting,
   and the repository policy requiring Actions to be SHA-pinned.
6. Create a protected `release` environment. Apple, Android, and Windows signing
   material is stored only there; the owner is its required reviewer.
7. Create branch rulesets for `main` and retained `develop`: block deletion and force
   pushes, require pull requests, one approval, stale-approval dismissal, resolved
   conversations, CODEOWNERS review for sensitive paths, linear history, and the exact
   green check names proven by a completed workflow.
8. Create a `refs/tags/v*` ruleset blocking update/deletion and restricting creation
   to the reviewed release path. Administrator bypass is explicit and auditable, not
   an implicit exemption.

`CODEOWNERS` covers workflows, release/installer scripts, update verification,
telemetry/privacy code, dependency manifests, and ownership/security policy. External
contributors can fork and open PRs; they receive no secrets, write token, environment,
or approval authority. Workflows triggered from forks do not execute privileged
release paths.

## Test strategy

### Unit and property tests

- Primary/secondary ownership, manual versus autostart activation, stale-owner retry,
  ACL/name construction, bounded shutdown, and activation coalescing.
- `WindowActivator` behavior from secondary launch and tray action.
- Settings initial population, normalized snapshot equality, every individual field,
  stale async generations, save/rebaseline, discard, and failed autostart reconciliation.
- Redactor fuzz/property cases seeded with usernames, paths, email, Moodle URLs,
  cookies, JWTs, passwords, sesskeys, filenames, and course content.
- No telemetry I/O before consent, queue deletion on revoke, schema allow-list,
  deduplication, size/count/age caps, offline/timeout/429/5xx backoff, and hook chaining.
- Update preference default/migration, version selection, schema 1 compatibility,
  schema 2 ambiguity rejection, URL allow-list, partial download cleanup, checksum,
  package ID/version/signer verification, and explicit-install confirmation.
- Release inventory rejects renamed ZIPs, unsigned packages, wrong versions, duplicate
  formats, stale manifest URLs, and incomplete artifact sets.

### Bounded subprocess and integration tests

- Start a hidden primary, invoke the real manual entry point, prove the original PID
  becomes visible and only one primary survives.
- Invoke the autostart alias while primary is hidden and prove it does not show.
- Launch simultaneous secondaries, kill the primary, and prove a later launch recovers.
- Exercise uncaught main-thread, worker-thread, asyncio, unraisable, and deliberate
  subprocess-abort cases; verify sanitized next-run reports without expecting Python
  to catch the abort itself.
- Exercise offline and slow fake update servers with strict deadlines.
- Build generated Windows runner fixtures and fail when Flutter patch anchors drift.
- Install/upgrade/uninstall MSI and Burn EXE in disposable Windows environments and
  check shortcut, activation, autostart, retained settings, and rollback.
- Parse and verify signed APK and IPA fixtures without printing signing secrets.

### Platform evidence

The release gate uses GitHub-hosted Windows/macOS runners and a disposable Android
environment. A genuine Windows 10 22H2 activation/crash/installer run requires either
a bounded self-hosted Windows 10 runner or an explicitly recorded manual test on the
reported machine; Windows Server or Windows 11 evidence cannot be relabeled as Windows
10 evidence. Results record OS build, app version, test identifiers, timeouts, and
artifact hashes but no device identity.

Every process wait, network request, workflow poll, UI wait, and installer probe has
an explicit timeout and exact-process cleanup. A timeout fails the test and preserves
bounded diagnostics; it never becomes an unbounded terminal wait.

## Delivery sequence

1. Commit this design and write the executable file-level plan.
2. Correct baseline CI and add failing tests for Settings snapshots and Windows
   activation before implementation.
3. Implement activation/bootstrap and Settings state, then pass targeted and full
   test gates.
4. Add local diagnostic capture, privacy model, sanitized spool, Sentry transport,
   and generated-runner patch with tests first.
5. Implement the update domain/coordinator and platform verification adapters.
6. Replace incomplete packaging with signed MSI/Burn, APK, and IPA pipelines plus the
   exact inventory verifier.
7. Add governance/security files and workflows, push the feature branch, prove check
   names, then apply GitHub settings, environments, and rulesets through `gh api`.
8. Audit the complete objective against code, workflow runs, repository settings,
   signed artifact evidence, and bounded platform E2E results.
9. Merge only after all available gates pass; rerun full tests and repository audit
   on merged `main`.

## External release prerequisites

The implementation can make missing prerequisites explicit and fail closed, but it
cannot manufacture public trust identities. A publishable release requires:

- Apple Developer Program membership, Apple Distribution certificate/private key,
  provisioning/export profile, and App Store Connect/TestFlight credentials;
- a publicly trusted Windows Authenticode certificate/private key or configured
  Microsoft Trusted Signing account;
- an Android release keystore/private key that is backed up permanently;
- a configured Sentry organization/project and DSN for automatic delivery.

Absence of telemetry credentials does not break the app. Absence of any required
signing credential blocks release publication and is reported as a release
prerequisite, never hidden by an unsigned substitute.

## References

- Python `faulthandler`: <https://docs.python.org/3/library/faulthandler.html>
- Flet window operations: <https://flet.dev/docs/reference/types/window/>
- WiX Toolset and Burn: <https://docs.firegiant.com/wix/tools/burn/>
- Microsoft App Installer: <https://learn.microsoft.com/en-us/windows/msix/app-installer/how-to-create-appinstaller-file>
- Apple TestFlight: <https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/>
- Apple Ad Hoc provisioning: <https://developer.apple.com/help/account/provisioning-profiles/create-an-ad-hoc-provisioning-profile>
- GitHub repository rulesets: <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets>
- GitHub Actions security hardening: <https://docs.github.com/en/actions/security-guides/security-hardening-for-github-actions>
