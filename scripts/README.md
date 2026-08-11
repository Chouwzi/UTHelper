# Repository scripts

Only repeatable build, release, verification, or maintenance entry points belong in
this directory. Product tests belong in `tests/` so pytest and CI collect them.

## Build and package

- `build_android.ps1` — build the Android package with required native patches.
- `build_installer.ps1` — canonical local Windows installer entry point.
- `build_windows_release.ps1` — orchestrate the Windows release bundle.
- `package_msix.ps1` — development MSIX packaging path.
- `package_unsigned_ipa.py` — package the unsigned device IPA for user-side signing.
- `prepare_windows_bundle.py` — prepare the Flet Windows bundle for verification.

## Generate release metadata

- `generate_appinstaller.ps1`
- `generate_public_runtime_config.py`
- `generate_release_manifest.py`
- `prepare_flet_diagnostics_template.py`
- `release_inventory.py`
- `release_metadata.py`

## Sign, provision, and publish

- `provision_release_credentials.ps1`
- `sign_windows_release.ps1`
- `upload_ipa_release.py`
- `validate_release_credentials.py`

## Verify artifacts and bounded platform behavior

- `test_windows_bundle_e2e.ps1`
- `test_windows_installer_e2e.ps1`
- `test_windows_msi_upgrade_e2e.ps1`
- `test_windows_single_instance_e2e.ps1`
- `verify_android_release.py`
- `verify_flutter_diagnostics.py`
- `verify_ipa_release.sh`
- `verify_windows_bundle.py`
- `verify_windows_release.ps1`

## Local diagnostics

- `measure_windows_performance.py` — bounded CPU and memory sampling for a selected
  Windows launch mode; it is not part of CI.

## Repository governance

- `validate_gitflow_pr.py` — CI check for allowed protected-branch directions.
- `github_branch_policy.py` — bounded audit/apply client for repository merge
  settings and the protected `main`/`develop` ruleset.

Do not add one-off rewrite scripts, hard-coded local paths, generated build trees, or
ad-hoc `test_*.py` programs here. Promote reusable checks into `tests/` or a bounded
platform harness above, and keep disposable research under the ignored local research
directory.
