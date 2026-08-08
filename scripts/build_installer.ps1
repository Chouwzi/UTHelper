param(
    [string]$BundleDir = "build\windows",
    [string]$Version = "",
    [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"
function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [int]$TimeoutSeconds,
        [switch]$CaptureOutput
    )
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.UseShellExecute = $false
    if ($CaptureOutput) {
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
    }
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    if ($CaptureOutput) {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
    }
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Installer wrapper subprocess timed out: $FilePath"
        }
        if ($CaptureOutput) {
            $stdout = $stdoutTask.GetAwaiter().GetResult()
            $stderr = $stderrTask.GetAwaiter().GetResult()
        }
        if ($process.ExitCode -ne 0) {
            throw "Installer wrapper subprocess failed: $FilePath`n$stderr"
        }
        if ($CaptureOutput) { return $stdout.Trim() }
    } finally {
        $process.Dispose()
    }
}
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $Version) {
    $versionCommandDescription = "release_metadata.py --pyproject pyproject.toml --print-version"
    $python = (Get-Command python -ErrorAction Stop).Source
    $Version = Invoke-BoundedProcess $python @(
        (Join-Path $workspaceRoot "scripts\release_metadata.py"),
        "--pyproject", (Join-Path $workspaceRoot "pyproject.toml"),
        "--print-version"
    ) 60 -CaptureOutput
    if (-not $Version) { throw "Failed: $versionCommandDescription" }
}
$shell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
Invoke-BoundedProcess $shell @(
    "-NoProfile", "-File", (Join-Path $PSScriptRoot "build_windows_release.ps1"),
    "-BundleDir", $BundleDir, "-Version", $Version, "-OutputDir", $OutputDir
) 1800
