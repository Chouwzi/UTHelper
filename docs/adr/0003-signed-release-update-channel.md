# ADR 0003: Signed platform release update channel

- Status: Accepted
- Date: 2026-07-19

## Context

The previous updater selected the first archive-like GitHub asset and used a
Windows batch/ZIP replacement flow. Android installation also depended on
calling platform APIs from Python. Those approaches did not provide a stable
package identity, deterministic asset selection, or end-to-end artifact
verification.

## Decision

- `pyproject.toml` is the only authored application version.
- A `vX.Y.Z` tag must exactly match that version before release jobs run.
- Releases contain a signed universal APK and a signed x64 MSIX.
- `release-manifest.json` identifies each platform and architecture explicitly
  and records its URL, byte size, and SHA-256 digest.
- Windows installs through a stable GitHub Pages `.appinstaller` document so
  AppInstaller owns launch and background update checks.
- Android opens the exact APK asset in the system download/package-install
  flow. Silent installation is not supported.
- The client ignores drafts, prereleases, mismatched manifests, and malformed
  semantic versions. Its download primitive writes atomically and rejects size
  or SHA-256 mismatches.
- Only the tag workflow may publish a GitHub Release; platform build workflows
  publish CI artifacts only.

## Consequences

Production releases require a persistent Android keystore and a Windows code
signing certificate in GitHub Actions secrets. Portable Windows users need a
one-time migration to MSIX, while data under `%APPDATA%\UTHElearningAlert`
remains outside the install directory. Android still requires explicit user
confirmation in PackageInstaller.

The current Flet Python runtime has no maintained WorkManager bridge that can
execute the Moodle synchronization policy after process death. Native
deadline schedules are therefore enabled now, while periodic discovery of new
activities after process death remains a separately gated native-extension
task. The old fixed periodic “tracking deadlines” notification was removed so
the UI does not claim background synchronization that did not occur.
