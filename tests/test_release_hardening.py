from pathlib import Path
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


def test_ios_build_bundles_the_native_background_sync_plugin():
    config = tomllib.loads(_read("pyproject.toml"))
    ios_config = config["tool"]["flet"]["ios"]

    assert "flet-uth-background-sync==0.1.0" in ios_config["dependencies"]
    assert ios_config["dev_packages"]["flet-uth-background-sync"] == (
        "extensions/flet_uth_background_sync"
    )


def test_local_android_build_script_applies_the_native_notification_patch():
    script = _read("scripts/build_android.ps1")

    assert "ORG_GRADLE_PROJECT_kotlin.incremental" in script
    assert "flet-android-notifications-patch" in script
    assert script.count("build $Target --verbose") == 2


def test_inno_uninstall_scopes_autostart_cleanup_to_known_values():
    script = _read("scripts/UTHelper_Setup.iss")
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"

    assert script.count(run_key) == 2
    assert 'ValueName: "UTHelper"' in script
    assert 'ValueName: "UTHElearningAlert"' in script
    assert script.count("uninsdeletevalue") == 2
    assert "uninsdeletekey" not in script
    assert "PrivilegesRequired=lowest" in script
    assert "PrivilegesRequired=admin" not in script


def test_windows_release_prepares_alias_before_verification_and_packaging():
    workflow = _read(".github/workflows/release.yml")
    installer = _read("scripts/build_installer.ps1")

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
        "scripts/build_installer.ps1",
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
    assert "$env:SENTRY_DSN" in _read("scripts/build_installer.ps1")
    assert "$env:SENTRY_DSN" in _read("scripts/build_android.ps1")
