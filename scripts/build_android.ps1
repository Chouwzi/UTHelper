param(
    [ValidateSet("apk", "aab")]
    [string]$Target = "apk"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fletCommand = Get-Command flet -ErrorAction Stop
$patchCommand = Get-Command flet-android-notifications-patch -ErrorAction Stop

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# Kotlin incremental caches cannot relativize Pub cache sources on C: against
# a project on another Windows drive. CI may leave this enabled on one volume.
[Environment]::SetEnvironmentVariable(
    "ORG_GRADLE_PROJECT_kotlin.incremental",
    "false",
    "Process"
)

Push-Location $projectRoot
try {
    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics config generation failed before initial Android build."
    }
    & $fletCommand.Source build $Target --verbose
    $firstExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "build/flutter"))) {
        throw "Flet did not create build/flutter (exit $firstExit)."
    }

    & $patchCommand.Source --project-root (Join-Path $projectRoot "build/flutter")
    if ($LASTEXITCODE -ne 0) {
        throw "Notification patcher failed with exit code $LASTEXITCODE."
    }

    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json")
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics config generation failed before final Android build."
    }
    & $fletCommand.Source build $Target --verbose
    if ($LASTEXITCODE -ne 0) {
        throw "Final Android build failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
