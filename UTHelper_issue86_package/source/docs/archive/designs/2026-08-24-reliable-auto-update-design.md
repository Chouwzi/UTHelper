# Reliable Auto-Update Design

## Status

Approved in conversation on 2026-08-24. Implementation is performed inline on
`feature/cross-platform-performance`; no subagents are used.

## Goal

Make update discovery truthful, downloads recoverable and efficient, verified
artifacts reusable, and Android installation consume the already downloaded APK
without a second network transfer. Installation always remains an explicit user
decision.

## Current Evidence

- The live Windows x64 MSI is 46,882,816 bytes.
- Live metadata discovery took 1.331 seconds.
- The current downloader took 14.634-14.731 seconds at about 3.04 MiB/s.
- Windows native verification took 1.187 seconds.
- The current 64 KiB callback policy emitted 716 progress events.
- A simulated discovery timeout is currently emitted as `UP_TO_DATE`.
- Android currently downloads once in Python, then the native bridge downloads
  the same URL again.

## Architecture

### Truthful discovery

`GitHubReleaseClient` returns `None` only when a valid latest release proves
there is no package newer than the current version for the exact runtime target.
Transport, HTTP, JSON, and manifest failures raise a typed discovery error.
The coordinator maps that error to `FAILED`, preserving the distinction between
"up to date" and "unable to check".

Transient metadata failures use at most three attempts with bounded exponential
backoff and optional `Retry-After`. Manifest and trust failures are fail-closed
and are never retried as transport failures.

### Recoverable verified download

`VerifiedDownloader` owns the network transfer for Windows and Android. It uses
a package-bound `.part` file and sidecar metadata. A compatible partial transfer
is resumed with `Range`; a server that ignores or contradicts the range causes a
safe restart. The final full byte sequence is checked against manifest size and
SHA-256 before atomic publication.

Explicit cancellation removes the partial. A transient network failure retains
only a package-bound partial that can be safely resumed. Invalid size, hash,
sidecar, or range response removes the partial.

Progress emission is coalesced: 0% and 100% are always sent, while intermediate
updates are limited by a 100 ms interval or a 1% advance. The downloader remains
synchronous and cooperatively cancellable; UI work never runs in its thread.

### Reusable ready artifact

The coordinator treats a verified path as a first-class ready state. Cancelling
the confirmation dialog does not discard it. A repeated click for the same
candidate reopens confirmation. Candidate replacement, failed re-verification,
explicit cancellation, or shutdown cleanup invalidates it.

The automatic-check preference controls only automatic discovery. It does not
cancel a user-initiated download.

### Android local APK handoff

The Python downloader transfers and hashes the APK once. After explicit
confirmation, the Android bridge receives its local path plus the immutable
manifest identity. Kotlin copies it into the plugin-owned FileProvider cache
while recomputing SHA-256, then verifies package ID, exact versionCode, newer
version, manifest signer, and continuity with the installed signer.

The native bridge reports `permission_required`, `installer_opened`, or a typed
failure. Scheduling a coroutine is not an installer acknowledgement. The
coordinator/UI only hide the update banner after `installer_opened`. A permission
round trip reuses the same verified APK.

### Platform behavior

- Windows MSI/Burn: Python transfer, Windows native signature/identity verifier,
  explicit confirmation, bounded installer handoff.
- Android APK: Python transfer, Kotlin local-file identity/signature verifier,
  explicit confirmation, Package Installer handoff.
- iOS sideload: no in-app package transfer; explicit confirmation opens the
  canonical GitHub release page.
- Unsupported/MSIX targets: no unsafe fallback to another install channel.

## Failure and Recovery Matrix

| Case | Required result |
|---|---|
| DNS/socket timeout | `FAILED`, retryable message; never `UP_TO_DATE` |
| HTTP 429/5xx | bounded retry, honor valid `Retry-After` |
| Invalid manifest/trust | fail immediately, no package download |
| Mid-stream disconnect | retain package-bound partial and resume |
| Server ignores Range | safely restart from byte zero |
| Size/hash mismatch | delete partial/final candidate and fail closed |
| Explicit cancel | delete partial and emit `CANCELLED` |
| Confirmation cancelled | retain verified artifact and reopen dialog later |
| Auto-check disabled | stop scheduler only; manual download continues |
| Android permission missing | retain APK and expose retry/open-installer action |
| Android identity/signature mismatch | delete native staged APK and fail closed |
| App shutdown during transfer | cooperatively cancel with bounded join |

## Benchmark Acceptance Criteria

- Progress events for the 46,882,816-byte fixture fall by at least 70% from the
  raw 716-event baseline while still emitting 0% and 100%.
- A transfer interrupted at 75% consumes approximately 100% total payload after
  resume, rather than 175% after a full restart.
- Android performs exactly one network package transfer.
- Reopening confirmation performs zero additional network bytes.
- The live Windows download and verification path remains valid; performance
  numbers are reported but not treated as deterministic pass/fail thresholds.

## Testing

Every production behavior is introduced through a failing test. Python tests
cover discovery classification/retry, range handling, resume, progress
coalescing, ready-artifact reuse, preference semantics, UI state, and bridge
arguments. Kotlin tests cover local file validation and archive identity. Full
Python suites and all available native tests run before final completion.

## Non-Goals

- Silent installation without confirmation.
- A background OS service that updates while the app is not running.
- Replacing Windows Installer rollback.
- Automatically installing iOS sideload packages.
- Adding mirrors or a new release hosting provider.
