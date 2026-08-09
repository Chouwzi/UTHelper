from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_release_workflow_pins_expected_artifact_and_signing_certificates():
    workflow = _read(".github/workflows/release.yml")

    assert "APK_CANDIDATES" in workflow
    assert "Expected exactly one Flet release APK" in workflow
    assert "find build -name '*.apk' -type f | head -1" not in workflow
    assert "ANDROID_SIGNING_CERT_SHA256" in workflow
    assert "WINDOWS_SIGNING_CERT_SHA256" in workflow
    assert "apksigner\" verify --verbose --print-certs" in workflow
    assert '"$APKANALYZER" manifest print' in workflow
    assert "Get-AuthenticodeSignature" in workflow


def test_msix_packaging_requires_identity_match_timestamp_and_verification():
    script = _read("scripts/package_msix.ps1")

    assert "does not match certificate subject" in script
    assert "/tr $TimestampUrl /td SHA256" in script
    assert "signtool verification failed" in script
    assert "$makeAppx.FullName unpack" in script
    assert "MSIX package verification failed" in script
    assert "$makeAppx.FullName validate" not in script


def test_appinstaller_uses_repository_name_in_stable_pages_url():
    script = _read("scripts/generate_appinstaller.ps1")

    assert 'pagesRepository = $repositoryParts[1]' in script
    assert "github.io/$pagesRepository/UTHelper.appinstaller" in script
    assert "Generated AppInstaller XML is invalid" in script


def test_cross_compiled_targets_do_not_receive_windows_only_dependencies():
    config = tomllib.loads(_read("pyproject.toml"))
    project_dependencies = config["project"]["dependencies"]
    windows_dependencies = config["tool"]["flet"]["windows"]["dependencies"]
    android_dependencies = config["tool"]["flet"]["android"]["dependencies"]
    android_build_dependencies = config["project"]["optional-dependencies"]["android-build"]

    assert not any("pywin32" in value for value in project_dependencies)
    assert any("pywin32" in value for value in windows_dependencies)
    assert any("windows-toasts" in value for value in windows_dependencies)
    assert "winrt-Windows.ApplicationModel==3.2.1" in windows_dependencies
    assert "flet-android-notifications==0.10.0" in android_dependencies
    assert android_build_dependencies == ["flet-android-notifications==0.10.0"]


def test_patched_security_floors_are_consistent_across_build_surfaces():
    config = tomllib.loads(_read("pyproject.toml"))
    release_workflow = _read(".github/workflows/release.yml")

    assert "pillow>=12.3.0" in config["project"]["optional-dependencies"]["windows"]
    assert "pillow>=12.3.0" in config["tool"]["flet"]["windows"]["dependencies"]
    assert "lxml>=6.1.0" in config["project"]["optional-dependencies"]["windows"]
    assert "lxml>=6.1.0" in config["tool"]["flet"]["windows"]["dependencies"]
    assert "pytest>=9.0.3" in config["dependency-groups"]["dev"]
    assert '"pytest>=9.0.3"' in release_workflow


def test_flet_build_version_and_compilation_are_reproducible():
    config = tomllib.loads(_read("pyproject.toml"))

    assert "flet==0.86.5" in config["project"]["dependencies"]
    assert (
        "flet[cli,desktop]==0.86.5"
        in config["project"]["optional-dependencies"]["windows"]
    )
    assert not any(
        dependency.startswith("flet>=")
        for dependency in config["project"]["dependencies"]
    )
    assert config["tool"]["flet"]["compile"] == {
        "app": True,
        "packages": True,
    }
    assert (
        "winrt-Windows.ApplicationModel==3.2.1"
        in config["project"]["optional-dependencies"]["windows"]
    )


def test_release_manifest_is_generated_only_from_exact_verified_inventory():
    generator = _read("scripts/generate_release_manifest.py")
    inventory = _read("scripts/release_inventory.py")

    assert "verify_release_inventory(" in generator
    assert '"schema_version": 3' in generator
    assert '"schema": 1' not in generator
    assert "REQUIRED_PACKAGE_NAMES" in inventory
    for pattern in (
        "UTHelper-{version}.ipa",
        "UTHelper-{version}.apk",
        "UTHelper-Setup-{version}.exe",
        "UTHelper-{version}.msi",
    ):
        assert pattern in inventory
    assert "required inventory is missing or contains unexpected assets" in inventory
    assert "manifest certificate fingerprint evidence mismatch" in inventory


def test_legacy_installer_cannot_author_a_competing_application_version():
    wrapper = _read("scripts/build_installer.ps1")

    assert not (ROOT / "scripts/UTHelper_Setup.iss").exists()
    assert "release_metadata.py" in wrapper
    assert "--print-version" in wrapper
    assert "build_windows_release.ps1" in wrapper


def test_android_build_workflows_install_the_notification_patcher():
    for workflow_path in (
        ".github/workflows/build-android.yml",
        ".github/workflows/release.yml",
    ):
        workflow = _read(workflow_path)
        assert 'pip install -e ".[android-build]"' in workflow
        assert "flet-android-notifications-patch --project-root build/flutter" in workflow

    build_workflow = _read(".github/workflows/build-android.yml")
    assert 'application-id "$APK")" = "com.uthelper.uthelper"' in build_workflow


def test_android_release_uses_canonical_version_code_and_signing_inputs():
    workflow = _read(".github/workflows/release.yml")
    config = tomllib.loads(_read("pyproject.toml"))

    assert '--build-version "$VERSION" --build-number "$BUILD_NUMBER"' in workflow
    assert "ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}" in workflow
    assert "ANDROID_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}" in workflow
    assert "ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}" in workflow
    assert "ANDROID_KEY_ALIAS: ${{ vars.ANDROID_KEY_ALIAS }}" in workflow
    assert "ANDROID_SIGNING_CERT_SHA256: ${{ vars.ANDROID_SIGNING_CERT_SHA256 }}" in workflow
    assert 'application-id "$APK")" = "com.uthelper.uthelper"' in workflow
    assert 'version-code "$APK")" = "$BUILD_NUMBER"' in workflow
    assert config["tool"]["flet"]["android"]["bundle_id"] == "com.uthelper.uthelper"
    assert "yes | flet build apk" not in workflow
    assert "--yes --verbose" in workflow
    assert "if ! flet build apk" in workflow
    assert 'if [ "${#COMPLETED_APKS[@]}" -ne 1 ]; then' in workflow
    assert "continuing with mandatory native verification" in workflow
    assert '"$BUILD_TOOLS/apksigner" verify --verbose --print-certs "$APK"' in workflow


def test_android_pr_artifact_cannot_be_confused_with_release():
    workflow = _read(".github/workflows/build-android.yml")

    assert "unsigned-diagnostic" in workflow
    assert "UTHelper-${{ github.sha }}-unsigned-diagnostic.apk" in workflow
    assert "UTHelper-${{ needs.validate.outputs.version }}.apk" not in workflow
    assert "yes | flet build apk" not in workflow
    assert "UNSIGNED_DIAGNOSTIC_ONLY.txt" in workflow
    assert 'if "$APKSIGNER" verify "$DIAGNOSTIC_APK"' in workflow


def test_ios_build_bundles_the_native_background_sync_plugin():
    config = tomllib.loads(_read("pyproject.toml"))
    ios_config = config["tool"]["flet"]["ios"]

    assert "flet-uth-background-sync==0.1.0" in ios_config["dependencies"]
    assert ios_config["dev_packages"]["flet-uth-background-sync"] == (
        "extensions/flet_uth_background_sync"
    )


def test_release_ipa_is_unsigned_device_archive_for_manual_resigning():
    workflow = _read(".github/workflows/release.yml")
    for name in (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_CERTIFICATE_PASSWORD",
        "APPLE_PROVISIONING_PROFILE_BASE64",
        "APPLE_API_PRIVATE_KEY_BASE64",
        "APPLE_TEAM_ID",
        "APPLE_SIGNING_IDENTITY",
        "APPLE_SIGNING_CERT_SHA256",
        "APPLE_API_ISSUER_ID",
        "APPLE_API_KEY_ID",
        "IOS_DISTRIBUTION_URL",
        "upload_ipa_release.py",
        "verify_ipa_release.sh",
    ):
        assert name not in workflow
    assert "flet build ipa" in workflow
    assert "package_unsigned_ipa.py" in workflow
    assert (
        "find build/ipa -mindepth 1 -maxdepth 1 -type d "
        "-name '*.xcarchive'"
    ) in workflow
    assert "find build/ios/archive" not in workflow
    assert "--ios-export-method" not in workflow
    assert "--ios-provisioning-profile" not in workflow
    assert "--ios-signing-certificate" not in workflow
    assert "yes | flet build ipa" not in workflow


def test_pull_request_ios_artifact_never_uses_ipa_extension():
    workflow = _read(".github/workflows/build-ios.yml")

    assert "ios-unsigned-diagnostic" in workflow
    assert "output/UTHelper.ipa" not in workflow
    assert "flet build ios-simulator" in workflow
    assert "*.ipa" not in workflow


def test_ipa_verifier_checks_device_architecture_and_unsigned_identity():
    script = _read("scripts/package_unsigned_ipa.py")

    assert 'platforms != ["iPhoneOS"]' in script
    assert "_CPU_TYPE_ARM64" in script
    assert "embedded.mobileprovision" in script
    assert '"signature_kind": "unsigned-resign-required"' in script
    assert '"signature_valid": False' in script
    assert "_safe_ipa_members" in script
    assert "timeout=120" in script


def test_local_android_build_script_applies_the_native_notification_patch():
    script = _read("scripts/build_android.ps1")

    assert "ORG_GRADLE_PROJECT_kotlin.incremental" in script
    assert "flet-android-notifications-patch" in script
    assert script.count("build $Target --verbose") == 2


def test_wix7_build_requires_owner_confirmed_eula_variable():
    script = _read("scripts/build_windows_release.ps1")

    assert '$env:WIX_EULA_ACCEPTED -ne "wix7"' in script
    assert "owner must review and accept the WiX v7 OSMF EULA" in script
    assert "-p:AcceptEula=$env:WIX_EULA_ACCEPTED" in script
    for project in (
        "packaging/windows/UTHelper.Package.wixproj",
        "packaging/windows/UTHelper.Bundle.wixproj",
    ):
        assert "<AcceptEula>" not in _read(project)
    for helper in (
        "scripts/sign_windows_release.ps1",
        "scripts/verify_windows_release.ps1",
    ):
        content = _read(helper)
        assert '$env:WIX_EULA_ACCEPTED -ne "wix7"' in content
        assert '"-acceptEula", $env:WIX_EULA_ACCEPTED' in content


def test_wix_authoring_has_stable_upgrade_codes_and_exact_msi_chain():
    package = _read("packaging/windows/Package.wxs")
    bundle = _read("packaging/windows/Bundle.wxs")
    project = _read("packaging/windows/UTHelper.Package.wixproj")
    bundle_project = _read("packaging/windows/UTHelper.Bundle.wixproj")

    assert "B1EB1032-5ACD-497D-8FD2-AB760218CBE3" in package
    assert "EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0" in bundle
    assert '<MsiPackage SourceFile="$(MsiPath)"' in bundle
    assert "UTHelperAutostart.exe" in package
    assert 'Value="msi"' in package
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert package.count(run_key) == 2
    assert 'Name="UTHelper"' in package
    assert "/v UTHElearningAlert /f" in package
    assert 'BindName="AppBundle"' in project
    assert "<InstallerPlatform>x64</InstallerPlatform>" in bundle_project


def test_burn_signing_detaches_signs_reattaches_and_signs_outer_bundle():
    script = _read("scripts/sign_windows_release.ps1")

    assert "wix burn detach" in script
    assert "wix burn reattach" in script
    assert script.count("Invoke-SignTool") >= 3
    assert "/tr $TimestampUrl /td SHA256" in script
    assert "WaitForExit($TimeoutSeconds * 1000)" in script
    assert "Stop-Process -Id $process.Id" in script
    assert 'Invoke-BoundedProcess "wix"' in script
    assert not re.search(r"(?m)^\s*wix burn (?:detach|reattach)\b", script)


def test_burn_verifier_identifies_extensionless_embedded_msi_by_ole_magic():
    script = _read("scripts/verify_windows_release.ps1")

    assert "Test-MsiOleMagic" in script
    assert "D0-CF-11-E0-A1-B1-1A-E1" in script
    assert "-Filter *.msi" not in script
    assert '"-oba", $baRoot' in script
    assert "manifest.xml" in script
    assert "PrimaryUpgradeCode" in script
    assert "Registration" in script
    assert "schema_version=2" in script
    assert 'signature_kind="self-signed-pinned"' in script


def test_windows_release_build_invocations_are_bounded_and_cwd_independent():
    script = _read("scripts/build_windows_release.ps1")

    assert "Invoke-BoundedProcess" in script
    assert "WaitForExit($TimeoutSeconds * 1000)" in script
    assert "Stop-Process -Id $process.Id" in script
    assert 'Join-Path $workspaceRoot "packaging\\windows\\UTHelper.Package.wixproj"' in script
    assert 'Join-Path $workspaceRoot "packaging\\windows\\UTHelper.Bundle.wixproj"' in script


def test_legacy_inno_path_is_removed_and_wrapper_delegates_only_to_wix():
    assert not (ROOT / "scripts/UTHelper_Setup.iss").exists()
    wrapper = _read("scripts/build_installer.ps1")

    assert "build_windows_release.ps1" in wrapper
    assert "release_metadata.py --pyproject pyproject.toml --print-version" in wrapper
    assert "ISCC.exe" not in wrapper
    assert "Inno Setup" not in wrapper
    assert "WaitForExit($TimeoutSeconds * 1000)" in wrapper
    assert "Stop-Process -Id $process.Id" in wrapper


def test_windows_release_prepares_alias_before_verification_and_packaging():
    workflow = _read(".github/workflows/release.yml")
    installer = _read("scripts/build_windows_release.ps1")

    for script in (workflow, installer):
        assert "prepare_windows_bundle.py" in script
        assert script.index("prepare_windows_bundle.py") < script.index(
            "verify_windows_bundle.py"
        )


def test_msix_and_e2e_use_argument_free_autostart_alias():
    msix = _read("scripts/package_msix.ps1")
    e2e = _read("scripts/test_windows_bundle_e2e.ps1")

    assert 'Executable="UTHelperAutostart.exe"' in msix
    assert "uap10:Parameters" not in msix
    assert 'Join-Path $resolvedBundle "UTHelperAutostart.exe"' in e2e
    assert 'Arguments @("--autostart")' not in e2e


def test_bundle_e2e_requires_activation_handoff_and_fail_open_scan():
    e2e = _read("scripts/test_windows_bundle_e2e.ps1")

    invocation = (
        '$activationOutput = & (Join-Path $PSScriptRoot '
        '"test_windows_single_instance_e2e.ps1") `\n'
        "    -ExePath $manualExe `\n"
        "    -StartupAliasPath $autostartExe `\n"
        "    -WorkingDirectory $resolvedBundle `\n"
        "    -ProcessExitTimeoutSeconds 5 `\n"
        "    -WindowTimeoutSeconds $ObservationSeconds 2>&1"
    )
    capture = "$capturedActivationLog = $activationOutput | Out-String"
    fail_open_guard = (
        'if ($capturedActivationLog -match "single_instance_fail_open") {'
    )
    terminating_failure = 'throw "Packaged activation emitted the fail-open diagnostic"'

    assert invocation in e2e
    assert capture in e2e
    assert fail_open_guard in e2e
    assert terminating_failure in e2e
    assert e2e.index(invocation) < e2e.index(capture)
    assert e2e.index(capture) < e2e.index(fail_open_guard)
    assert e2e.index(fail_open_guard) < e2e.index(terminating_failure)


def test_single_instance_e2e_embedded_csharp_targets_windows_powershell_51():
    script = _read("scripts/test_windows_single_instance_e2e.ps1")

    assert "out uint owner" not in script
    assert script.count("uint owner;") == 4
    assert script.count("out owner") == 4


def test_every_release_flet_build_generates_packaged_diagnostics_config_first():
    candidates = tuple((ROOT / ".github" / "workflows").glob("*.y*ml")) + tuple(
        (ROOT / "scripts").glob("*.ps1")
    )
    invocation_markers = ("flet build ", "$fletCommand.Source build ")
    build_files = []
    for path in candidates:
        content = path.read_text(encoding="utf-8").replace("\\", "/")
        if any(marker in content for marker in invocation_markers):
            build_files.append(path.relative_to(ROOT).as_posix())

    assert set(build_files) == {
        ".github/workflows/release.yml",
        ".github/workflows/build-ios.yml",
        ".github/workflows/build-android.yml",
        "scripts/build_android.ps1",
    }
    generator = "scripts/generate_public_runtime_config.py"
    output = "src/assets/diagnostics-config.json"
    total_builds = 0

    for relative_path in build_files:
        content = _read(relative_path).replace("\\", "/")
        lines = content.splitlines()
        build_indexes = [
            index
            for index, line in enumerate(lines)
            if any(marker in line for marker in invocation_markers)
        ]
        total_builds += len(build_indexes)
        generator_indexes = [
            index for index, line in enumerate(lines) if generator in line
        ]

        assert build_indexes, relative_path
        assert len(generator_indexes) == len(build_indexes), relative_path
        previous_build = -1
        for build_index in build_indexes:
            prebuild = lines[previous_build + 1 : build_index]
            matching = [line for line in prebuild if generator in line]
            assert len(matching) == 1, (
                f"{relative_path}:{build_index + 1} must generate config once "
                "after the prior build and before this Flet build"
            )
            assert output in matching[0]
            previous_build = build_index

    assert total_builds == 9

    for workflow_path in (
        ".github/workflows/release.yml",
        ".github/workflows/build-ios.yml",
        ".github/workflows/build-android.yml",
    ):
        workflow = _read(workflow_path)
        assert "SENTRY_DSN: ${{ vars.SENTRY_DSN }}" in workflow
        assert "secrets.SENTRY_DSN" not in workflow
    assert "$env:SENTRY_DSN" in _read("scripts/build_android.ps1")


def test_trusted_release_refuses_to_package_without_diagnostics_ingestion():
    workflow = _read(".github/workflows/release.yml")
    generator = "scripts/generate_public_runtime_config.py"
    invocations = [line for line in workflow.splitlines() if generator in line]

    assert len(invocations) == 4
    assert all("--require-configured" in line for line in invocations)


def test_android_workflows_verify_the_materialized_flet_plugin_location():
    expected = (
        "build/flutter-packages/flet_uth_background_sync/android/build.gradle"
    )
    stale = "build/flutter/flutter-packages/flet_uth_background_sync"

    for relative_path in (
        ".github/workflows/build-android.yml",
        ".github/workflows/release.yml",
    ):
        workflow = _read(relative_path).replace("\\", "/")
        assert workflow.count(expected) == 2
        assert stale not in workflow


def test_every_windows_flet_build_uses_and_verifies_reviewed_diagnostics_template():
    official_url = (
        "https://github.com/flet-dev/flet/releases/download/"
        "v0.86.5/flet-build-template.zip"
    )
    prepared_name = "flet-build-template-0.86.5-diagnostics.zip"
    patcher = "prepare_flet_diagnostics_template.py"
    verifier = "verify_flutter_diagnostics.py"

    for relative_path in (
        ".github/workflows/release.yml",
    ):
        content = _read(relative_path).replace("\\", "/")
        build_index = content.index("flet build windows")
        assert official_url in content
        assert prepared_name in content
        assert patcher in content[:build_index]
        if relative_path.endswith("release.yml"):
            assert f"--template build/support/{prepared_name}" in content
        else:
            assert "--template $diagnosticsTemplate" in content
        assert verifier in content[build_index:]
        assert "--project-root" in content[build_index:]
        assert "build/flutter" in content[build_index:]

    workflow = _read(".github/workflows/release.yml")
    assert "Invoke-WebRequest" in workflow
    assert "-TimeoutSec 30" in workflow
    assert "timeout-minutes: 5" in workflow
    assert "timeout-minutes: 30" in workflow
    windows_job = workflow.split("  build-signed-windows:\n", 1)[1].split(
        "\n  publish-exact-release:\n", 1
    )[0]
    windows_steps = [
        block
        for block in re.split(r"(?m)(?=^      - )", windows_job)
        if block.startswith("      - ")
    ]
    assert windows_steps
    assert all("timeout-minutes:" in step for step in windows_steps)

    for relative_path in (
        ".github/workflows/build-android.yml",
        ".github/workflows/build-ios.yml",
        "scripts/build_android.ps1",
    ):
        content = _read(relative_path)
        assert prepared_name not in content
        assert patcher not in content
        assert verifier not in content


def test_release_workflow_has_native_signed_jobs_and_one_final_publication_job():
    workflow = _read(".github/workflows/release.yml")
    for job in (
        "validate-release-source:",
        "build-signed-android:",
        "build-unsigned-ios:",
        "build-signed-windows:",
        "publish-exact-release:",
    ):
        assert job in workflow
    assert "environment: release" in workflow
    assert "gh attestation verify" in workflow
    assert "release_inventory.py" in workflow
    assert "--draft" in workflow
    assert 'gh release edit "$TAG" --draft=false --latest' in workflow


def test_windows_release_job_forces_utf8_for_flet_cli_output():
    workflow = _read(".github/workflows/release.yml")
    windows_job = workflow.split("  build-signed-windows:\n", 1)[1].split(
        "\n  publish-exact-release:\n", 1
    )[0]

    assert "PYTHONUTF8: '1'" in windows_job
    assert "PYTHONIOENCODING: utf-8" in windows_job


def test_release_credentials_are_preflighted_before_native_runner_jobs():
    workflow = _read(".github/workflows/release.yml")

    preflight = workflow.split("  validate-release-credentials:\n", 1)[1].split(
        "\n  build-signed-android:\n", 1
    )[0]
    assert "needs: validate-release-source" in preflight
    assert "environment: release" in preflight
    assert "python3 scripts/validate_release_credentials.py" in preflight

    for job, next_job in (
        ("build-signed-android", "build-unsigned-ios"),
        ("build-unsigned-ios", "build-signed-windows"),
        ("build-signed-windows", "publish-exact-release"),
    ):
        body = workflow.split(f"  {job}:\n", 1)[1].split(
            f"\n  {next_job}:\n", 1
        )[0]
        assert (
            "needs: [validate-release-source, validate-release-credentials]" in body
        )


def test_release_actions_are_full_sha_pinned_and_permissions_are_job_local():
    workflow = _read(".github/workflows/release.yml")
    for action in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/setup-java@d7793b545071e98d581d3bf084a51c3213318a07",
        "actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093",
        "actions/attest@508db95dd578ae2727ebd6217d5ba78e4fbda05d",
    ):
        assert action in workflow
    assert "actions/checkout@v4" not in workflow
    assert "softprops/action-gh-release" not in workflow
    assert "\npermissions:\n  contents: write" not in workflow
    assert workflow.count("    permissions:\n") == 6


def test_release_final_job_requires_all_builds_and_exact_inventory():
    workflow = _read(".github/workflows/release.yml")
    assert (
        "needs: [validate-release-source, build-signed-android, "
        "build-unsigned-ios, build-signed-windows]"
    ) in workflow
    for asset in (
        "UTHelper-$VERSION.ipa",
        "UTHelper-$VERSION.apk",
        "UTHelper-Setup-$VERSION.exe",
        "UTHelper-$VERSION.msi",
        "release-manifest.json",
        "SHA256SUMS",
    ):
        assert asset in workflow


def test_publication_creates_empty_draft_records_id_then_uploads_six_assets():
    workflow = _read(".github/workflows/release.yml")
    create = 'gh release create "$TAG" --draft --verify-tag'
    lookup = (
        'CREATED_RELEASE_ID=$(gh api '
        '"repos/$GITHUB_REPOSITORY/releases/tags/$TAG"'
    )
    upload = 'gh release upload "$TAG"'
    publish = 'gh release edit "$TAG" --draft=false --latest'
    assert (
        workflow.index(create)
        < workflow.index(lookup)
        < workflow.index(upload)
        < workflow.index(publish)
    )
    assert "select(.draft == true and .tag_name == $tag)" in workflow
    assert "releases/$CREATED_RELEASE_ID" in workflow
    assert "gh release delete" not in workflow


def test_windows_release_builds_and_verifies_bundle_before_wix():
    workflow = _read(".github/workflows/release.yml")
    commands = (
        "prepare_flet_diagnostics_template.py",
        "flet build windows",
        "verify_flutter_diagnostics.py",
        "prepare_windows_bundle.py build/windows",
        "verify_windows_bundle.py build/windows",
        "test_windows_bundle_e2e.ps1 -BundleDir build/windows -ObservationSeconds 8",
        "build_windows_release.ps1 -BundleDir build/windows",
    )
    positions = [workflow.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "--template build/support/flet-build-template-0.86.5-diagnostics.zip" in workflow
    assert "test_windows_single_instance_e2e.ps1" in _read(
        "scripts/test_windows_bundle_e2e.ps1"
    )
    assert "single_instance_fail_open" in _read(
        "scripts/test_windows_bundle_e2e.ps1"
    )
    assert "build/flutter/lib/main.dart" not in workflow


def test_windows_release_temporarily_trusts_and_removes_exact_self_signed_leaf():
    workflow = _read(".github/workflows/release.yml")

    assert 'Import-Certificate -FilePath $publicCertificate -CertStoreLocation "Cert:\\CurrentUser\\Root"' in workflow
    assert "WINDOWS_TRUSTED_CERT_THUMBPRINT" in workflow
    assert "Temporary Windows trust import identity mismatch" in workflow
    assert "Compiled Windows release pin mismatch" in workflow
    assert "TRUSTED_WINDOWS_SIGNER_SHA256" in workflow
    assert 'Remove-Item -LiteralPath "Cert:\\CurrentUser\\Root\\$env:WINDOWS_TRUSTED_CERT_THUMBPRINT"' in workflow
    assert "if: always()" in workflow


def test_release_inputs_use_secrets_only_for_private_key_material():
    workflow = _read(".github/workflows/release.yml")
    for name in (
        "ANDROID_KEYSTORE_BASE64",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_PASSWORD",
        "WINDOWS_PFX_BASE64",
        "WINDOWS_PFX_PASSWORD",
    ):
        assert f"{name}: ${{{{ secrets.{name} }}}}" in workflow
    for name in (
        "ANDROID_KEY_ALIAS",
        "ANDROID_SIGNING_CERT_SHA256",
        "WINDOWS_SIGNING_CERT_SHA256",
        "WINDOWS_SIGNER_SUBJECT",
        "WINDOWS_TIMESTAMP_URL",
        "WIX_EULA_ACCEPTED",
        "SENTRY_DSN",
    ):
        assert f"{name}: ${{{{ vars.{name} }}}}" in workflow
        assert f"secrets.{name}" not in workflow
    for removed in (
        "APPLE_CERTIFICATE_P12_BASE64",
        "APPLE_PROVISIONING_PROFILE_BASE64",
        "APPLE_API_PRIVATE_KEY_BASE64",
        "APPLE_TEAM_ID",
        "IOS_DISTRIBUTION_URL",
    ):
        assert removed not in workflow


def test_validate_job_runs_full_suite_with_workspace_import_paths():
    workflow = _read(".github/workflows/release.yml")
    assert "PYTHONPATH: src:extensions/flet_uth_background_sync/src:." in workflow
    assert (
        "python -m pytest tests extensions/flet_uth_background_sync/tests "
        "-q --tb=short"
    ) in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" "origin/main"' in workflow
    assert 'pip install -e ".[dev,windows]"' not in workflow
    assert (
        'pip install -e . "keyring>=25.0.0" "pytest>=9.0.3" '
        '"pytest-timeout>=2.3,<3"'
    ) in workflow


def test_release_manifest_preserves_the_supported_2_1_floor():
    workflow = _read(".github/workflows/release.yml")

    assert '--minimum-supported-version "2.1.0"' in workflow
    assert '--minimum-supported-version "$VERSION"' not in workflow
