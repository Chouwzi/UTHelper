from pathlib import Path
import re
import struct
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _ico_sizes(relative_path: str) -> set[tuple[int, int]]:
    data = (ROOT / relative_path).read_bytes()
    reserved, image_type, image_count = struct.unpack_from("<HHH", data)
    assert (reserved, image_type) == (0, 1)
    assert len(data) >= 6 + (16 * image_count)
    sizes: set[tuple[int, int]] = set()
    for index in range(image_count):
        width, height = struct.unpack_from("BB", data, 6 + (16 * index))
        sizes.add((width or 256, height or 256))
    return sizes


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

    assert "pillow>=13.0.0" in config["project"]["dependencies"]
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
        ".github/workflows/package-benchmark.yml",
        ".github/workflows/release.yml",
    ):
        workflow = _read(workflow_path)
        assert 'pip install -e ".[android-build]"' in workflow
        assert "flet-android-notifications-patch --project-root build/flutter" in workflow

    build_workflow = _read(".github/workflows/build-android.yml")
    assert 'application-id "$APK")" = "io.github.chouwzi.uthelper"' in build_workflow
    benchmark_workflow = _read(".github/workflows/package-benchmark.yml")
    assert "'com.uthelper.benchmark' || 'io.github.chouwzi.uthelper'" in benchmark_workflow


def test_android_release_uses_canonical_version_code_and_signing_inputs():
    workflow = _read(".github/workflows/release.yml")
    config = tomllib.loads(_read("pyproject.toml"))

    assert '--build-version "$VERSION" --build-number "$BUILD_NUMBER"' in workflow
    assert "ANDROID_KEYSTORE_BASE64: ${{ secrets.ANDROID_KEYSTORE_BASE64 }}" in workflow
    assert "ANDROID_KEYSTORE_PASSWORD: ${{ secrets.ANDROID_KEYSTORE_PASSWORD }}" in workflow
    assert "ANDROID_KEY_PASSWORD: ${{ secrets.ANDROID_KEY_PASSWORD }}" in workflow
    assert "ANDROID_KEY_ALIAS: ${{ vars.ANDROID_KEY_ALIAS }}" in workflow
    assert "ANDROID_SIGNING_CERT_SHA256: ${{ vars.ANDROID_SIGNING_CERT_SHA256 }}" in workflow
    assert "UTH_DISTRIBUTION_CHANNEL=apk_lts" in workflow
    assert "--distribution-channel apk_lts --distribution-output src/assets/distribution-channel.json" in workflow
    assert "--channel apk_lts" in workflow
    assert 'application-id "$APK")" = "io.github.chouwzi.uthelper"' in workflow
    assert 'version-code "$APK")" = "$BUILD_NUMBER"' in workflow
    assert config["tool"]["flet"]["org"] == "io.github.chouwzi"
    assert config["tool"]["flet"]["android"]["bundle_id"] == "io.github.chouwzi.uthelper"
    assert config["tool"]["flet"]["ios"]["bundle_id"] == "io.github.chouwzi.uthelper"
    assert "yes | flet build apk" not in workflow
    assert "--yes --verbose" in workflow
    assert "COMPLETED_APKS" not in workflow
    assert "MANIFEST=build/flutter/android/app/src/main/AndroidManifest.xml" not in workflow
    for receiver in (
        "ScheduledNotificationReceiver",
        "ScheduledNotificationBootReceiver",
        "ActionBroadcastReceiver",
        "DeadlineAlarmReceiver",
        "RescheduleReceiver",
    ):
        assert f'grep -q "{receiver}" final-android-manifest.xml' in workflow
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
    assert "Set-BoundedAndroidGradleConfig" in script
    assert '"org.gradle.jvmargs" = "-Xmx3G' in script
    assert '"org.gradle.workers.max" = "2"' in script
    assert '"kotlin.compiler.execution.strategy" = "in-process"' in script
    assert '"kotlin.incremental" = "false"' in script
    assert script.count("build $Target @fletOptions --verbose") == 2


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
    assert (
        'LaunchTarget="[ProgramFiles64Folder]UTHelper\\UTHelper.exe"' in bundle
    )
    assert 'LaunchWorkingFolder="[ProgramFiles64Folder]UTHelper"' in bundle
    assert "UTHelperAutostart.exe" in package
    assert 'Value="msi"' in package
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    assert package.count(run_key) == 2
    assert 'Name="UTHelper"' in package
    assert "/v UTHElearningAlert /f" in package
    assert 'BindName="AppBundle"' in project
    assert "<InstallerPlatform>x64</InstallerPlatform>" in bundle_project


def test_wix_bundle_uses_application_branding_for_ui_and_executable():
    bundle = _read("packaging/windows/Bundle.wxs")
    bundle_project = _read("packaging/windows/UTHelper.Bundle.wixproj")

    assert 'IconSourceFile="$(BundleIconPath)"' in bundle
    assert 'LogoFile="$(BundleLogoPath)"' in bundle
    assert "BundleIconPath=$(MSBuildProjectDirectory)" in bundle_project
    assert r"src\assets\icon.ico" in bundle_project
    assert "BundleLogoPath=$(MSBuildProjectDirectory)" in bundle_project
    assert r"src\assets\icon.png" in bundle_project

    required_shell_sizes = {(16, 16), (32, 32), (48, 48), (256, 256)}
    assert required_shell_sizes <= _ico_sizes("src/assets/icon.ico")


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


def test_windows_release_signing_resolves_sdk_signtool_when_it_is_not_on_path():
    script = _read("scripts/sign_windows_release.ps1")

    assert "Get-Command signtool.exe -ErrorAction SilentlyContinue" in script
    assert '"${env:ProgramFiles(x86)}\\Windows Kits\\10\\bin"' in script
    assert "\\\\x64\\\\signtool\\.exe$" in script
    assert "signtool.exe was not found on PATH or in the Windows SDK" in script
    assert "Invoke-BoundedProcess $signToolPath" in script
    assert 'Invoke-BoundedProcess "signtool.exe"' not in script


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


def test_every_release_flet_build_generates_packaged_runtime_assets_first():
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
        ".github/workflows/build-msstore.yml",
        ".github/workflows/package-benchmark.yml",
        "scripts/build_android.ps1",
    }
    generator = "scripts/generate_public_runtime_config.py"
    output = "src/assets/diagnostics-config.json"
    version_generator = "scripts/release_metadata.py"
    version_output = "src/assets/release-version"
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
        version_generator_indexes = [
            index
            for index, line in enumerate(lines)
            if version_generator in line and "--runtime-output" in line
        ]

        assert build_indexes, relative_path
        assert len(generator_indexes) == len(build_indexes), relative_path
        assert len(version_generator_indexes) == len(build_indexes), relative_path
        previous_build = -1
        for build_index in build_indexes:
            prebuild = lines[previous_build + 1 : build_index]
            matching = [line for line in prebuild if generator in line]
            version_matching = [
                line
                for line in prebuild
                if version_generator in line and "--runtime-output" in line
            ]
            assert len(matching) == 1, (
                f"{relative_path}:{build_index + 1} must generate config once "
                "after the prior build and before this Flet build"
            )
            assert output in matching[0]
            assert len(version_matching) == 1, (
                f"{relative_path}:{build_index + 1} must generate the runtime "
                "version after the prior build and before this Flet build"
            )
            assert version_output in version_matching[0]
            previous_build = build_index

    assert total_builds >= 14

    for workflow_path in (
        ".github/workflows/release.yml",
        ".github/workflows/build-ios.yml",
        ".github/workflows/build-android.yml",
        ".github/workflows/build-msstore.yml",
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
        ".github/workflows/build-msstore.yml",
    ):
        content = _read(relative_path).replace("\\", "/")
        build_index = content.index("flet build windows")
        assert official_url in content
        assert prepared_name in content
        assert patcher in content[:build_index]
        if relative_path.endswith(("release.yml", "build-msstore.yml")):
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


def test_windows_release_fails_closed_when_runtime_version_generation_fails():
    workflow = _read(".github/workflows/release.yml")
    windows_job = workflow.split("  build-signed-windows:\n", 1)[1].split(
        "\n  publish-exact-release:\n", 1
    )[0]
    generator = (
        "python scripts/release_metadata.py --pyproject pyproject.toml "
        "--runtime-output src/assets/release-version"
    )
    failure = 'if ($LASTEXITCODE -ne 0) { throw "Runtime version generation failed" }'
    diagnostics = "python scripts/generate_public_runtime_config.py"

    assert windows_job.index(generator) < windows_job.index(failure)
    assert windows_job.index(failure) < windows_job.index(diagnostics)


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
    preflight = 'existing_release_ids=$(gh api "repos/$GITHUB_REPOSITORY/releases?per_page=100"'
    create = 'release_record=$(gh api -X POST "repos/$GITHUB_REPOSITORY/releases"'
    capture = "CREATED_RELEASE_ID=$(printf '%s' \"$release_record\" | jq -er '.id')"
    upload = 'gh release upload "$TAG"'
    publish = 'gh release edit "$TAG" --draft=false --latest'
    assert (
        workflow.index(preflight)
        < workflow.index(create)
        < workflow.index(capture)
        < workflow.index(upload)
        < workflow.index(publish)
    )
    assert '-F draft=true' in workflow
    assert '-F generate_release_notes=true' in workflow
    assert 'gh release create "$TAG"' not in workflow
    assert 'releases/tags/$TAG" --jq' not in workflow
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

    assert "Import-Certificate" not in workflow
    assert "certutil.exe -f -addstore Root $publicCertificate" in workflow
    assert "Headless Windows trust import failed" in workflow
    assert 'Get-ChildItem -LiteralPath "Cert:\\LocalMachine\\Root\\$($certificate.Thumbprint)"' in workflow
    assert "Windows signing PFX decoded" in workflow
    assert "Windows signing PFX identity validated" in workflow
    assert "Windows machine-root trust imported" in workflow
    assert "WINDOWS_TRUSTED_CERT_THUMBPRINT" in workflow
    assert "Temporary Windows trust import identity mismatch" in workflow
    assert "Compiled Windows release pin mismatch" in workflow
    assert "TRUSTED_WINDOWS_SIGNER_SHA256" in workflow
    assert "certutil.exe -delstore Root $env:WINDOWS_TRUSTED_CERT_THUMBPRINT" in workflow
    assert "Headless Windows trust cleanup failed" in workflow
    assert 'Remove-Item -LiteralPath "Cert:\\LocalMachine\\Root' not in workflow
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
    assert (
        "PYTHONPATH: src:extensions/flet_uth_quiz/src:"
        "extensions/flet_uth_background_sync/src:."
    ) in workflow
    assert (
        "python -m pytest tests extensions/flet_uth_quiz/tests "
        "extensions/flet_uth_background_sync/tests "
        "-q --tb=short"
    ) in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" "origin/main"' in workflow
    assert 'pip install -e ".[dev,windows]"' not in workflow
    assert (
        'pip install -e . -e extensions/flet_uth_quiz '
        '-e extensions/flet_uth_background_sync "flet-webview==0.86.5" '
        '"keyring>=25.0.0" "pytest>=9.0.3" '
        '"pytest-timeout>=2.3,<3"'
    ) in workflow


def test_release_manifest_preserves_the_supported_2_1_floor():
    workflow = _read(".github/workflows/release.yml")

    assert '--minimum-supported-version "2.1.0"' in workflow
    assert '--minimum-supported-version "$VERSION"' not in workflow


def test_msstore_workflow_builds_and_verifies_msix_without_mutating_lts_release():
    workflow = _read(".github/workflows/build-msstore.yml")
    release_workflow = _read(".github/workflows/release.yml")

    for action in (
        "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
    ):
        assert action in workflow

    assert "permissions: {}" in workflow
    assert "contents: write" not in workflow
    assert "gh release" not in workflow
    assert "PYTHONUTF8: '1'" in workflow
    assert "PYTHONIOENCODING: utf-8" in workflow
    assert "SENTRY_DSN: ${{ vars.SENTRY_DSN }}" in workflow
    assert "secrets.SENTRY_DSN" not in workflow

    ordered_commands = (
        "prepare_flet_diagnostics_template.py",
        "flet build windows",
        "verify_flutter_diagnostics.py",
        "prepare_windows_bundle.py build/windows",
        "verify_windows_bundle.py build/windows",
        "./scripts/package_msix.ps1",
        "./scripts/verify_msix_release.ps1",
    )
    positions = [workflow.index(command) for command in ordered_commands]
    assert positions == sorted(positions)

    # Every step in build-msstore-msix must have an explicit timeout.
    steps_block = workflow.split("    steps:\n", 1)[1]
    steps = [
        block
        for block in re.split(r"(?m)(?=^      - )", steps_block)
        if block.startswith("      - ")
    ]
    assert steps
    assert all("timeout-minutes:" in step for step in steps)

    # GitHub LTS release workflow stays isolated from Store MSIX/AAB artifacts.
    assert ".msix" not in release_workflow
    assert ".aab" not in release_workflow


def test_windows_flet_builds_append_msvc_coroutine_deprecation_guard():
    define = "_SILENCE_EXPERIMENTAL_COROUTINE_DEPRECATION_WARNINGS"
    cl_flag = f"/D{define}"
    safe_append_marker = "$env:CL.Trim()"

    for relative_path in (
        ".github/workflows/build-msstore.yml",
        ".github/workflows/release.yml",
        ".github/workflows/package-benchmark.yml",
    ):
        content = _read(relative_path)
        assert cl_flag in content, relative_path
        assert safe_append_marker in content, relative_path
        assert f"CL: {cl_flag}" not in content, relative_path
        assert content.index(cl_flag) < content.index("flet build windows"), relative_path

    windows_release_script = _read("scripts/build_windows_release.ps1")
    assert cl_flag in windows_release_script
    assert safe_append_marker in windows_release_script


def test_android_manual_dispatch_supports_installable_debug_test_apk_without_weakening_pr_guardrails():
    workflow = _read(".github/workflows/build-android.yml")

    # Manual dispatch inputs for lightweight installable QA test APKs.
    for marker in (
        "apk_arch:",
        "apk_signing:",
        "apk_namespace:",
        "default: arm64-v8a",
        "default: debug-test",
        "default: isolated",
        "--arch arm64-v8a",
        '--bundle-id "$APK_PACKAGE_ID"',
        'APK_PACKAGE_ID="com.uthelper.qa"',
        '"$APKSIGNER" verify --verbose --print-certs "$TEST_APK"',
        "DEBUG_TEST_ONLY.txt",
        "android-debug-test-",
    ):
        assert marker in workflow

    # Installable debug-test mode is strictly gated to manual workflow_dispatch runs on apk_lts.
    assert (
        'if [ "$CHANNEL" = "apk_lts" ] && [ "${GITHUB_EVENT_NAME:-}" = "workflow_dispatch" ]; then'
        in workflow
    )
    assert (
        'if [ "${GITHUB_EVENT_NAME:-}" = "workflow_dispatch" ] && [ "${APK_INSTALLABLE_TEST:-false}" = "true" ]; then'
        in workflow
    )
    assert (
        "if: matrix.channel == 'apk_lts' && (github.event_name != 'workflow_dispatch' || env.APK_INSTALLABLE_TEST != 'true')"
        in workflow
    )
    assert (
        "if: matrix.channel == 'apk_lts' && github.event_name == 'workflow_dispatch' && env.APK_INSTALLABLE_TEST == 'true'"
        in workflow
    )

    # Production Android release signing secrets remain strictly isolated to release.yml.
    for forbidden in (
        "ANDROID_KEYSTORE_BASE64",
        "ANDROID_KEYSTORE_PASSWORD",
        "ANDROID_KEY_PASSWORD",
        "ANDROID_KEY_ALIAS",
        "ANDROID_SIGNING_CERT_SHA256",
        "environment: release",
    ):
        assert forbidden not in workflow


def test_pyproject_pins_fixed_non_retracted_jni_override():
    config = tomllib.loads(_read("pyproject.toml"))
    overrides = config["tool"]["flet"]["flutter"]["pubspec"]["dependency_overrides"]

    assert overrides["jni"] != "1.2.0"
    assert overrides["jni"] == "1.2.1"


def test_pyproject_bundles_filelock_across_all_platforms_and_configures_pytest_paths():
    config = tomllib.loads(_read("pyproject.toml"))
    flet_cfg = config["tool"]["flet"]

    for deps in (
        config["project"]["dependencies"],
        flet_cfg["windows"]["dependencies"],
        flet_cfg["android"]["dependencies"],
        flet_cfg["ios"]["dependencies"],
        config["dependency-groups"]["dev"],
    ):
        assert any(dep.startswith("filelock>=") for dep in deps)

    dev_deps = config["dependency-groups"]["dev"]
    assert "flet-webview==0.86.5" in dev_deps
    assert any(dep.startswith("ruff>=") for dep in dev_deps)
    assert config["tool"]["pytest"]["ini_options"]["pythonpath"] == [
        "src",
        "extensions/flet_uth_quiz/src",
        "extensions/flet_uth_background_sync/src",
    ]
    bg_pubspec = _read(
        "extensions/flet_uth_background_sync/flutter/flet_uth_background_sync/pubspec.yaml"
    )
    assert "flet: 0.86.5" in bg_pubspec
    assert "../../../../../../../packages/flet" not in bg_pubspec


def test_wix_preserves_run_uthelper_across_major_upgrade_and_removes_on_uninstall():
    package = _read("packaging/windows/Package.wxs")
    upgrade_script = _read("scripts/test_windows_msi_upgrade_e2e.ps1")

    assert (
        'Action="RemoveRunUTHelper" Before="RemoveFiles" '
        'Condition="REMOVE~=&quot;ALL&quot; AND (NOT UPGRADINGPRODUCTCODE)"'
    ) in package
    set_idx = upgrade_script.index('$expectedAutostartValue = ')
    upgrade_idx = upgrade_script.index('Invoke-BoundedProcess "msiexec.exe" @("/i", $CurrentMsi, "/qn", "/norestart")')
    assert_idx = upgrade_script.index("MajorUpgrade removed or altered HKCU Run\\UTHelper autostart value")
    assert set_idx < upgrade_idx < assert_idx


def test_android_workflow_isolates_play_upload_secrets_from_pull_request_jobs():
    workflow = _read(".github/workflows/build-android.yml")
    build_job = workflow.split("\n  build:\n", 1)[1].split(
        "\n  sign-chplay-upload:\n", 1
    )[0]
    sign_job = workflow.split("\n  sign-chplay-upload:\n", 1)[1]

    for secret_name in (
        "PLAY_UPLOAD_KEYSTORE_BASE64",
        "PLAY_UPLOAD_KEYSTORE_PASSWORD",
        "PLAY_UPLOAD_KEY_PASSWORD",
    ):
        assert secret_name not in build_job
        assert f"secrets.{secret_name}" in sign_job

    assert "github.event_name != 'pull_request'" in sign_job
    assert "environment: chplay-upload" in sign_job
    assert "--require-quiz-webview" in workflow

    for wf_path in (
        ".github/workflows/build-android.yml",
        ".github/workflows/release.yml",
        ".github/workflows/package-benchmark.yml",
    ):
        assert "ClassScheduleAlarmReceiver" in _read(wf_path), wf_path


def test_ci_lints_all_trees_audits_flet_webview_and_msstore_verifies_wack_office_assets():
    ci = _read(".github/workflows/ci.yml")
    msstore = _read(".github/workflows/build-msstore.yml")

    assert "ruff check src tests scripts extensions --output-format=github" in ci
    assert 'pip install "flet-webview==0.86.5"' in ci
    assert "push:\n    branches: [main]" in msstore
    assert msstore.count("RequireOfficeViewer = $true") == 2


def test_verify_windows_bundle_strict_mode_checks_extended_runtime_packages(tmp_path):
    from scripts.verify_windows_bundle import inspect_bundle

    # Minimal valid bundle baseline (matching unit verifier layout)
    (tmp_path / "UTHelper.exe").write_bytes(b"MZ-app")
    (tmp_path / "UTHelperAutostart.exe").write_bytes(b"MZ-app")
    (tmp_path / "python314.dll").write_bytes(b"MZ")
    (tmp_path / "flutter_windows.dll").write_bytes(b"MZ")
    (tmp_path / "flutter_inappwebview_windows_plugin.dll").write_bytes(b"MZ")
    (tmp_path / "WebView2Loader.dll").write_bytes(b"MZ")
    (tmp_path / "Lib" / "encodings").mkdir(parents=True)
    (tmp_path / "Lib" / "encodings" / "__init__.pyc").write_bytes(b"pyc")
    (tmp_path / "app" / "assets" / "office_viewer").mkdir(parents=True)
    (tmp_path / "app" / "assets" / "pdf_viewer").mkdir(parents=True)
    (tmp_path / "app" / "main.pyc").write_bytes(b"pyc")
    (tmp_path / "app" / "assets" / "release-version").write_bytes(b"2.5.1\n")
    for name in (
        "docx_host.html", "docx_host.js", "docx-preview.min.js", "jszip.min.js",
        "pptx_host.html", "pptx_host.js", "pptx_worker.js", "pptx_engine.js",
        "viewer.css", "viewer_common.js",
    ):
        (tmp_path / "app" / "assets" / "office_viewer" / name).write_bytes(b"ok")
    for name in (
        "pdf_host.html", "pdf_host.js", "pdf_viewer.css", "pdf.min.mjs", "pdf.worker.min.mjs",
    ):
        (tmp_path / "app" / "assets" / "pdf_viewer" / name).write_bytes(b"ok")

    sp = tmp_path / "site-packages"
    (sp / "winrt").mkdir(parents=True)
    (sp / "winrt" / "_winrt_windows_applicationmodel.cp314-win_amd64.pyd").write_bytes(b"pyd")
    (sp / "win32" / "lib").mkdir(parents=True)
    for mod in ("win32api", "win32event", "win32security"):
        (sp / "win32" / f"{mod}.pyd").write_bytes(b"pyd")
    for mod in ("win32con", "winerror", "pywintypes"):
        (sp / "win32" / "lib" / f"{mod}.pyc").write_bytes(b"pyc")
    (sp / "pywin32_system32").mkdir(parents=True)
    (sp / "pywin32_system32" / "pywintypes314.dll").write_bytes(b"dll")
    (sp / "flet_uth_quiz").mkdir(parents=True)
    (sp / "flet_uth_quiz" / "__init__.pyc").write_bytes(b"pyc")
    (sp / "flet_uth_quiz" / "quiz_player.pyc").write_bytes(b"pyc")

    assert inspect_bundle(tmp_path) == ()

    strict_issues = inspect_bundle(tmp_path, strict_site_packages=True)
    for expected in (
        "WebView2 WinRT projection",
        "Windows.Foundation projection",
        "win32gui",
        "win32process",
        "pypdfium2",
        "lxml.etree",
        "filelock",
    ):
        assert any(expected in item for item in strict_issues), expected

    (sp / "webview2" / "microsoft" / "web" / "webview2" / "core").mkdir(parents=True)
    (sp / "webview2" / "_webview2_microsoft_web_webview2_core.pyd").write_bytes(b"pyd")
    (sp / "winrt" / "_winrt_windows_foundation.cp314-win_amd64.pyd").write_bytes(b"pyd")
    (sp / "win32" / "win32gui.pyd").write_bytes(b"pyd")
    (sp / "win32" / "win32process.pyd").write_bytes(b"pyd")
    (sp / "pypdfium2").mkdir()
    (sp / "pypdfium2" / "__init__.pyc").write_bytes(b"pyc")
    (sp / "lxml").mkdir()
    (sp / "lxml" / "etree.cp314-win_amd64.pyd").write_bytes(b"pyd")
    (sp / "filelock").mkdir()
    (sp / "filelock" / "__init__.pyc").write_bytes(b"pyc")
    (sp / "flet-0.86.5.dist-info").mkdir()

    assert inspect_bundle(tmp_path) == ()
    assert inspect_bundle(tmp_path, strict_site_packages=True) == ()

