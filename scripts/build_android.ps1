param(
    [ValidateSet("apk_lts", "chplay")]
    [string]$Channel = "apk_lts",
    [ValidateSet("apk", "aab")]
    [string]$Target,
    [ValidateSet("universal", "arm64-v8a", "armeabi-v7a", "x86_64")]
    [string]$Architecture,
    [switch]$LegacyPackaging,
    [switch]$RequireSigned,
    [ValidateSet("release", "develop", "development")]
    [string]$BuildEnvironment = "release",
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

$signingVars = @(
    "FLET_ANDROID_SIGNING_KEY_STORE",
    "FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD",
    "FLET_ANDROID_SIGNING_KEY_PASSWORD",
    "FLET_ANDROID_SIGNING_KEY_ALIAS"
)
if (-not [string]::IsNullOrWhiteSpace($env:FLET_ANDROID_SIGNING_STORE_PASSWORD) -and
    [string]::IsNullOrWhiteSpace($env:FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD)) {
    throw "FLET_ANDROID_SIGNING_STORE_PASSWORD is not recognized by Flet CLI; use FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD."
}
$configuredSigningVars = @($signingVars | Where-Object {
    -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
})
if ($configuredSigningVars.Count -gt 0 -and $configuredSigningVars.Count -lt $signingVars.Count) {
    $missing = @($signingVars | Where-Object { $configuredSigningVars -notcontains $_ })
    throw "Partial FLET_ANDROID_SIGNING_* environment configuration detected; missing: $($missing -join ', ')"
}
if ($RequireSigned -and $configuredSigningVars.Count -ne $signingVars.Count) {
    throw "-RequireSigned requires all 4 FLET_ANDROID_SIGNING_* environment variables ($($signingVars -join ', '))."
}
if ($configuredSigningVars.Count -eq $signingVars.Count -and
    -not (Test-Path -LiteralPath $env:FLET_ANDROID_SIGNING_KEY_STORE -PathType Leaf)) {
    throw "FLET_ANDROID_SIGNING_KEY_STORE file not found: $env:FLET_ANDROID_SIGNING_KEY_STORE"
}

if (-not $PSBoundParameters.ContainsKey("Target") -or -not $Target) {
    if ($Channel -eq "chplay") {
        $Target = "aab"
    } else {
        $Target = "apk"
    }
}
if (-not $PSBoundParameters.ContainsKey("Architecture") -or -not $Architecture) {
    if ($Channel -eq "chplay") {
        $Architecture = "universal"
    } else {
        $Architecture = "arm64-v8a"
    }
}
if ($PSBoundParameters.ContainsKey("LegacyPackaging")) {
    $useLegacyPackaging = [bool]$LegacyPackaging
} else {
    $useLegacyPackaging = ($Channel -ne "chplay")
}

if ($Channel -eq "chplay" -and $useLegacyPackaging) {
    throw "CH Play builds require 16 KB uncompressed native library packaging (--no-android-legacy-packaging)."
}
if ($Channel -eq "chplay" -and $Target -ne "aab") {
    throw "CH Play builds must target 'aab' (Android App Bundle)."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Resolve-BuildPythonExecutable {
    param([Parameter(Mandatory = $true)][string]$WorkspaceRoot)

    $candidates = [System.Collections.Generic.List[string]]::new()
    [void]$candidates.Add((Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"))
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        [void]$candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))
        [void]$candidates.Add((Join-Path $env:VIRTUAL_ENV "bin/python"))
    }
    $gitPointer = Join-Path $WorkspaceRoot ".git"
    if (Test-Path -LiteralPath $gitPointer -PathType Leaf) {
        $gitLine = (Get-Content -LiteralPath $gitPointer -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($gitLine -match '^\s*gitdir:\s*(.+?)\s*$') {
            $gitDirPath = $Matches[1].Replace('/', '\')
            if (-not [IO.Path]::IsPathRooted($gitDirPath)) {
                $gitDirPath = [IO.Path]::GetFullPath((Join-Path $WorkspaceRoot $gitDirPath))
            }
            $worktreesParent = Split-Path -Path $gitDirPath -Parent
            $dotGitDir = if ($worktreesParent) { Split-Path -Path $worktreesParent -Parent } else { $null }
            $mainRepoRoot = if ($dotGitDir) { Split-Path -Path $dotGitDir -Parent } else { $null }
            if ($mainRepoRoot) {
                [void]$candidates.Add((Join-Path $mainRepoRoot ".venv\Scripts\python.exe"))
            }
        }
    }
    $parentOne = Split-Path -Path $WorkspaceRoot -Parent
    if ($parentOne) {
        $parentTwo = Split-Path -Path $parentOne -Parent
        if ($parentTwo) {
            [void]$candidates.Add((Join-Path $parentTwo ".venv\Scripts\python.exe"))
        }
    }
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }
    return (Get-Command python -ErrorAction Stop).Source
}

$pythonExe = Resolve-BuildPythonExecutable -WorkspaceRoot $projectRoot
$venvPatchExe = Join-Path ([IO.Path]::GetDirectoryName($pythonExe)) "flet-android-notifications-patch.exe"
$patchCommandPath = if (Test-Path -LiteralPath $venvPatchExe -PathType Leaf) {
    $venvPatchExe
} else {
    (Get-Command flet-android-notifications-patch -ErrorAction Stop).Source
}

function Invoke-FletCliBuild {
    param(
        [Parameter(Mandatory = $true)][string]$BuildTarget,
        [string[]]$BuildOptions = @()
    )

    # Invoke .venv\Scripts\python.exe -c directly instead of the uv flet.exe
    # trampoline (which re-injects PYTHONHOME / UV_INTERNAL__PYTHONHOME and
    # inherits PYTHONPATH, hanging serious_python's bundled pip).
    $pyCode = @"
import os
import sys
for _key in ("PYTHONHOME", "UV_INTERNAL__PYTHONHOME", "PYTHONPATH"):
    os.environ.pop(_key, None)
import flet_cli.cli
sys.argv = ["flet", "build", *sys.argv[1:]]
flet_cli.cli.main()
"@
    & $pythonExe -c $pyCode $BuildTarget "--yes" @BuildOptions "--verbose"
}

# Default apk_lts to ARM64 legacy packaging; chplay uses universal AAB with
# uncompressed 16 KB-aligned native libraries and stripped updater permissions.
$fletOptions = @("--cleanup-app")
if ($Architecture -ne "universal") { $fletOptions += @("--arch", $Architecture) }
if ($useLegacyPackaging) { $fletOptions += "--android-legacy-packaging" }
else { $fletOptions += "--no-android-legacy-packaging" }
if ($Channel -eq "chplay") {
    $fletOptions += @(
        "--android-permissions", "android.permission.REQUEST_INSTALL_PACKAGES=false",
        "--android-permissions", "android.permission.FOREGROUND_SERVICE=false",
        "--android-permissions", "android.permission.FOREGROUND_SERVICE_DATA_SYNC=false",
        "--android-permissions", "android.permission.USE_EXACT_ALARM=false"
    )
}
if ($OutputDir) { $fletOptions += @("--output", $OutputDir) }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# Kotlin incremental caches cannot relativize Pub cache sources on C: against
# a project on another Windows drive. CI may leave this enabled on one volume.
[Environment]::SetEnvironmentVariable(
    "ORG_GRADLE_PROJECT_kotlin.incremental",
    "false",
    "Process"
)
$previousGradleChannel = [Environment]::GetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", "Process")
$previousEnvChannel = [Environment]::GetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", "Process")
$previousPythonHome = [Environment]::GetEnvironmentVariable("PYTHONHOME", "Process")
$previousUvPythonHome = [Environment]::GetEnvironmentVariable("UV_INTERNAL__PYTHONHOME", "Process")
[Environment]::SetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", $Channel, "Process")
[Environment]::SetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", $Channel, "Process")
[Environment]::SetEnvironmentVariable("PYTHONHOME", $null, "Process")
[Environment]::SetEnvironmentVariable("UV_INTERNAL__PYTHONHOME", $null, "Process")

function Set-BoundedAndroidGradleConfig {
    param([Parameter(Mandatory = $true)][string]$GradlePropertiesPath)

    if (-not (Test-Path -LiteralPath $GradlePropertiesPath -PathType Leaf)) {
        throw "Android gradle.properties was not generated: $GradlePropertiesPath"
    }

    # Flet's template reserves an 8 GiB Gradle heap and a separate 8 GiB
    # Kotlin daemon. That can exhaust a 16 GiB Windows host before the release
    # APK is packaged. Compile Kotlin in-process and bound both memory and
    # worker concurrency; CI runners and developer machines use the same path.
    $desired = [ordered]@{
        "org.gradle.jvmargs" = "-Xmx3G -XX:MaxMetaspaceSize=1G -XX:ReservedCodeCacheSize=256m -XX:+HeapDumpOnOutOfMemoryError"
        "org.gradle.workers.max" = "2"
        "kotlin.compiler.execution.strategy" = "in-process"
        "kotlin.incremental" = "false"
        "uthDistributionChannel" = $Channel
    }
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($line in [IO.File]::ReadAllLines($GradlePropertiesPath)) {
        $key = ($line -split "=", 2)[0].Trim()
        if (-not $desired.Contains($key)) {
            $lines.Add($line)
        }
    }
    foreach ($entry in $desired.GetEnumerator()) {
        $lines.Add("$($entry.Key)=$($entry.Value)")
    }
    [IO.File]::WriteAllLines(
        $GradlePropertiesPath,
        $lines,
        [Text.UTF8Encoding]::new($false)
    )
}

function Set-AndroidChannelBuildConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$DistributionChannel
    )

    & $pythonExe (Join-Path $ProjectRoot "scripts\prepare_android_flutter_project.py") `
        --project-root (Join-Path $ProjectRoot "build\flutter") `
        --channel $DistributionChannel `
        --target-sdk 36
    if ($LASTEXITCODE -ne 0) {
        throw "prepare_android_flutter_project.py failed with exit code $LASTEXITCODE."
    }
}

$channelAssetPath = Join-Path $projectRoot "src\assets\distribution-channel.json"
Push-Location $projectRoot
try {
    $buildVersion = (& $pythonExe (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --print-version).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical release version lookup failed before initial Android build."
    }
    $buildNumber = (& $pythonExe -c "from pathlib import Path; from scripts.release_metadata import read_project_version, release_build_number; print(release_build_number(read_project_version(Path('pyproject.toml'))))").Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Canonical Android build number lookup failed before initial Android build."
    }
    $fletOptions += @("--build-version", $buildVersion, "--build-number", $buildNumber)

    & $pythonExe (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before initial Android build."
    }
    & $pythonExe (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --posthog-key "$env:POSTHOG_PROJECT_API_KEY" --posthog-host "$env:POSTHOG_HOST" --build-environment $BuildEnvironment --package-format $Target --output (Join-Path $projectRoot "src\assets\diagnostics-config.json") --distribution-channel $Channel --distribution-output $channelAssetPath --android-package-id "io.github.chouwzi.uthelper"
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics and distribution channel config generation failed before initial Android build."
    }
    $gradleProperties = Join-Path $projectRoot "build/flutter/android/gradle.properties"
    if (Test-Path -LiteralPath $gradleProperties -PathType Leaf) {
        Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties
    }
    Invoke-FletCliBuild -BuildTarget $Target -BuildOptions $fletOptions
    $firstExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "build/flutter"))) {
        throw "Flet did not create build/flutter (exit $firstExit)."
    }

    & $patchCommandPath --project-root (Join-Path $projectRoot "build/flutter")
    if ($LASTEXITCODE -ne 0) {
        throw "Notification patcher failed with exit code $LASTEXITCODE."
    }
    Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties
    Set-AndroidChannelBuildConfig -ProjectRoot $projectRoot -DistributionChannel $Channel

    & $pythonExe (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before final Android build."
    }
    & $pythonExe (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --posthog-key "$env:POSTHOG_PROJECT_API_KEY" --posthog-host "$env:POSTHOG_HOST" --build-environment $BuildEnvironment --package-format $Target --output (Join-Path $projectRoot "src\assets\diagnostics-config.json") --distribution-channel $Channel --distribution-output $channelAssetPath --android-package-id "io.github.chouwzi.uthelper"
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics and distribution channel config generation failed before final Android build."
    }
    Invoke-FletCliBuild -BuildTarget $Target -BuildOptions $fletOptions
    if ($LASTEXITCODE -ne 0) {
        throw "Final Android build failed with exit code $LASTEXITCODE."
    }
} finally {
    if (Test-Path -LiteralPath $channelAssetPath -PathType Leaf) {
        Remove-Item -LiteralPath $channelAssetPath -Force -ErrorAction SilentlyContinue
    }
    [Environment]::SetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", $previousGradleChannel, "Process")
    [Environment]::SetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", $previousEnvChannel, "Process")
    [Environment]::SetEnvironmentVariable("PYTHONHOME", $previousPythonHome, "Process")
    [Environment]::SetEnvironmentVariable("UV_INTERNAL__PYTHONHOME", $previousUvPythonHome, "Process")
    Pop-Location
}
