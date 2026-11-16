param(
    [Parameter(Mandatory = $true)][string]$BundleDir,
    [Parameter(Mandatory = $true)][ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutputDir
)

if ($env:WIX_EULA_ACCEPTED -ne "wix7") {
    throw "WiX 7.0.0 requires explicit OSMF EULA acceptance; the owner must review and accept the WiX v7 OSMF EULA, then set WIX_EULA_ACCEPTED=wix7."
}
$ErrorActionPreference = "Stop"
function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [int]$TimeoutSeconds = 600
    )
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.UseShellExecute = $false
    if ($WorkingDirectory) { $start.WorkingDirectory = $WorkingDirectory }
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Release subprocess timed out: $FilePath"
        }
        if ($process.ExitCode -ne 0) {
            throw "Release subprocess failed with exit code $($process.ExitCode): $FilePath"
        }
    } finally {
        $process.Dispose()
    }
}
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBundle = (Resolve-Path -LiteralPath $BundleDir).Path
$resolvedOutput = [IO.Path]::GetFullPath((Join-Path $workspaceRoot $OutputDir))
if (-not $resolvedBundle.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase) -or
    -not $resolvedOutput.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BundleDir and OutputDir must remain inside the workspace"
}
$python = (Get-Command python -ErrorAction Stop).Source
Invoke-BoundedProcess $python @((Join-Path $PSScriptRoot "prepare_windows_bundle.py"), $resolvedBundle) $workspaceRoot 120
Invoke-BoundedProcess $python @((Join-Path $PSScriptRoot "verify_windows_bundle.py"), $resolvedBundle, "--expected-version", $Version) $workspaceRoot 120
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$packageProject = Join-Path $workspaceRoot "packaging\windows\UTHelper.Package.wixproj"
$bundleProject = Join-Path $workspaceRoot "packaging\windows\UTHelper.Bundle.wixproj"
$dotnet = (Get-Command dotnet -ErrorAction Stop).Source
$outputPath = $resolvedOutput + [IO.Path]::DirectorySeparatorChar
$acceptEulaArgument = "-p:AcceptEula=$env:WIX_EULA_ACCEPTED"
Invoke-BoundedProcess $dotnet @("build", $packageProject, "-c", "Release", $acceptEulaArgument, "-p:Version=$Version", "-p:AppBundle=$resolvedBundle", "-p:OutputPath=$outputPath") $workspaceRoot 600
$msi = Join-Path $resolvedOutput "UTHelper-$Version.msi"
if (-not (Test-Path -LiteralPath $msi -PathType Leaf)) { throw "Canonical MSI was not produced" }

$signingValues = @($env:WINDOWS_SIGNING_PFX_PATH, $env:WINDOWS_SIGNING_PFX_PASSWORD, $env:WINDOWS_TIMESTAMP_URL)
$signingEnabled = -not ($signingValues -contains $null) -and -not ($signingValues -contains "")
if ($signingEnabled) {
    $pwsh = (Get-Command pwsh -ErrorAction Stop).Source
    Invoke-BoundedProcess $pwsh @("-NoProfile", "-File", (Join-Path $PSScriptRoot "sign_windows_release.ps1"), "-Mode", "Msi", "-MsiPath", $msi, "-PfxPath", $env:WINDOWS_SIGNING_PFX_PATH, "-PfxPassword", $env:WINDOWS_SIGNING_PFX_PASSWORD, "-TimestampUrl", $env:WINDOWS_TIMESTAMP_URL) $workspaceRoot 300
} elseif (($signingValues | Where-Object { $_ }).Count -gt 0) {
    throw "Windows signing inputs must be all present or all absent"
}

Invoke-BoundedProcess $dotnet @("build", $bundleProject, "-c", "Release", $acceptEulaArgument, "-p:Version=$Version", "-p:MsiPath=$msi", "-p:OutputPath=$outputPath") $workspaceRoot 600
$bundle = Join-Path $resolvedOutput "UTHelper-Setup-$Version.exe"
if (-not (Test-Path -LiteralPath $bundle -PathType Leaf)) { throw "Canonical Burn EXE was not produced" }
if ($signingEnabled) {
    Invoke-BoundedProcess $pwsh @("-NoProfile", "-File", (Join-Path $PSScriptRoot "sign_windows_release.ps1"), "-Mode", "Burn", "-MsiPath", $msi, "-BundlePath", $bundle, "-PfxPath", $env:WINDOWS_SIGNING_PFX_PATH, "-PfxPassword", $env:WINDOWS_SIGNING_PFX_PASSWORD, "-TimestampUrl", $env:WINDOWS_TIMESTAMP_URL) $workspaceRoot 600
}
