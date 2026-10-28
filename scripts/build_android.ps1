param(
    [ValidateSet("apk", "aab")]
    [string]$Target = "apk",
    [ValidateSet("universal", "arm64-v8a", "armeabi-v7a", "x86_64")]
    [string]$Architecture = "universal",
    [switch]$LegacyPackaging,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fletCommand = Get-Command flet -ErrorAction Stop
$patchCommand = Get-Command flet-android-notifications-patch -ErrorAction Stop

# Preserve universal compatibility unless a device architecture is explicitly
# selected. Legacy packaging trades download size for extraction at install time.
$fletOptions = @("--cleanup-app")
if ($Architecture -ne "universal") { $fletOptions += @("--arch", $Architecture) }
if ($LegacyPackaging) { $fletOptions += "--android-legacy-packaging" }
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

Push-Location $projectRoot
try {
    python (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before initial Android build."
    }
    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics config generation failed before initial Android build."
    }
    $gradleProperties = Join-Path $projectRoot "build/flutter/android/gradle.properties"
    if (Test-Path -LiteralPath $gradleProperties -PathType Leaf) {
        Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties
    }
    & $fletCommand.Source build $Target @fletOptions --verbose
    $firstExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "build/flutter"))) {
        throw "Flet did not create build/flutter (exit $firstExit)."
    }

    & $patchCommand.Source --project-root (Join-Path $projectRoot "build/flutter")
    if ($LASTEXITCODE -ne 0) {
        throw "Notification patcher failed with exit code $LASTEXITCODE."
    }
    Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties

    python (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before final Android build."
    }
    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics config generation failed before final Android build."
    }
    & $fletCommand.Source build $Target @fletOptions --verbose
    if ($LASTEXITCODE -ne 0) {
        throw "Final Android build failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
