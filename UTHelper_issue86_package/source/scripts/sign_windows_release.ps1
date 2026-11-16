param(
    [Parameter(Mandatory = $true)][ValidateSet("Msi", "Burn")][string]$Mode,
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [string]$BundlePath,
    [Parameter(Mandatory = $true)][string]$PfxPath,
    [Parameter(Mandatory = $true)][string]$PfxPassword,
    [Parameter(Mandatory = $true)][string]$TimestampUrl
)

if ($env:WIX_EULA_ACCEPTED -ne "wix7") {
    throw "WiX signing requires owner-confirmed WIX_EULA_ACCEPTED=wix7"
}
$ErrorActionPreference = "Stop"
function Invoke-BoundedProcess {
    param([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 120, [string]$Description = "command")
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.UseShellExecute = $false
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "$Description timed out"
        }
        if ($process.ExitCode -ne 0) { throw "$Description failed with exit code $($process.ExitCode)" }
    } finally {
        $process.Dispose()
    }
}
function Resolve-SignToolPath {
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    $sdkBinRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    $candidate = if (Test-Path -LiteralPath $sdkBinRoot -PathType Container) {
        Get-ChildItem -LiteralPath $sdkBinRoot -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
    }
    if ($null -eq $candidate) {
        throw "signtool.exe was not found on PATH or in the Windows SDK"
    }
    return $candidate.FullName
}
$signToolPath = Resolve-SignToolPath
Write-Output "Resolved Windows SignTool: $signToolPath"
function Invoke-SignTool([string]$Path) {
    $signingPolicy = "/fd SHA256 /tr $TimestampUrl /td SHA256"
    if (-not $signingPolicy) { throw "Signing policy is unavailable" }
    Invoke-BoundedProcess $signToolPath @("sign", "/fd", "SHA256", "/f", $PfxPath, "/p", $PfxPassword, "/tr", $TimestampUrl, "/td", "SHA256", $Path)
}

if ($Mode -eq "Msi") {
    Invoke-SignTool $MsiPath
    exit 0
}
if (-not $BundlePath) { throw "BundlePath is required for Burn signing" }
$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$tempRoot = Join-Path $tempBase ("burn-sign-" + [guid]::NewGuid().ToString("N"))
$engine = Join-Path $tempRoot "engine.exe"
$reattached = Join-Path $tempRoot "reattached.exe"
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
    Invoke-BoundedProcess "wix" @("burn", "detach", "-acceptEula", $env:WIX_EULA_ACCEPTED, $BundlePath, "-engine", $engine) 120 "wix burn detach"
    Invoke-SignTool $engine
    Invoke-BoundedProcess "wix" @("burn", "reattach", "-acceptEula", $env:WIX_EULA_ACCEPTED, $BundlePath, "-engine", $engine, "-o", $reattached) 120 "wix burn reattach"
    Move-Item -LiteralPath $reattached -Destination $BundlePath -Force
    Invoke-SignTool $BundlePath
} finally {
    Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
