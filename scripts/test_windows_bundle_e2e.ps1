param(
    [Parameter(Mandatory = $true)][string]$BundleDir,
    [ValidateRange(2, 60)][int]$ObservationSeconds = 8
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBundle = (Resolve-Path -LiteralPath $BundleDir).Path
if (-not $resolvedBundle.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BundleDir must be inside the workspace: $resolvedBundle"
}

$manualExe = Join-Path $resolvedBundle "UTHelper.exe"
$autostartExe = Join-Path $resolvedBundle "UTHelperAutostart.exe"
if (-not (Test-Path -LiteralPath $manualExe -PathType Leaf)) {
    throw "UTHelper.exe was not found: $manualExe"
}
if (-not (Test-Path -LiteralPath $autostartExe -PathType Leaf)) {
    throw "UTHelperAutostart.exe was not found: $autostartExe"
}

$existing = Get-Process -Name "UTHelper", "UTHelperAutostart" -ErrorAction SilentlyContinue
if ($existing) {
    $ids = ($existing | Select-Object -ExpandProperty Id) -join ", "
    throw "Close existing UTHelper processes before E2E (PID: $ids)"
}

$activationOutput = & (Join-Path $PSScriptRoot "test_windows_single_instance_e2e.ps1") `
    -ExePath $manualExe `
    -StartupAliasPath $autostartExe `
    -WorkingDirectory $resolvedBundle `
    -ProcessExitTimeoutSeconds 5 `
    -WindowTimeoutSeconds $ObservationSeconds 2>&1
$activationOutput | ForEach-Object { Write-Host $_ }
$capturedActivationLog = $activationOutput | Out-String
if ($capturedActivationLog -match "single_instance_fail_open") {
    throw "Packaged activation emitted the fail-open diagnostic"
}

Write-Host "[UTHelper] Windows bundle E2E passed."
