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

if (-not ("NativeWindowProbe" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class NativeWindowProbe
{
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hwnd);

    public static bool HasVisibleWindow(uint targetProcessId)
    {
        bool found = false;
        EnumWindows((hwnd, lParam) =>
        {
            GetWindowThreadProcessId(hwnd, out uint owner);
            if (owner == targetProcessId && IsWindowVisible(hwnd))
            {
                found = true;
            }
            return !found;
        }, IntPtr.Zero);
        return found;
    }
}
"@
}

$profilesRoot = Join-Path $workspaceRoot "build\e2e-profiles"
New-Item -ItemType Directory -Path $profilesRoot -Force | Out-Null
$profileRoot = Join-Path $profilesRoot ([guid]::NewGuid().ToString("N"))
$settingsDir = Join-Path $profileRoot "UTHelper"
New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
$settingsFile = Join-Path $settingsDir "settings.json"
$originalAppData = $env:APPDATA

function Write-E2ESettings {
    param([bool]$StartMinimized)

    @{
        START_WITH_WINDOWS = $true
        START_MINIMIZED = $StartMinimized
        MINIMIZE_TO_TRAY = $true
        CHECK_INTERVAL_MINUTES = 0
    } | ConvertTo-Json | Set-Content -LiteralPath $settingsFile -Encoding UTF8
}

function Invoke-LaunchProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][bool]$ExpectedVisible
    )

    $process = $null
    try {
        $startArguments = @{
            FilePath = $Executable
            PassThru = $true
        }
        $process = Start-Process @startArguments
        $deadline = [DateTime]::UtcNow.AddSeconds($ObservationSeconds)
        $actualVisible = $false

        do {
            Start-Sleep -Milliseconds 200
            $process.Refresh()
            if ($process.HasExited) {
                throw "$Name exited during startup with code $($process.ExitCode)"
            }
            $actualVisible = [NativeWindowProbe]::HasVisibleWindow([uint32]$process.Id)
            if ($ExpectedVisible -and $actualVisible) {
                break
            }
        } while ([DateTime]::UtcNow -lt $deadline)

        if (-not $ExpectedVisible) {
            $actualVisible = [NativeWindowProbe]::HasVisibleWindow([uint32]$process.Id)
        }
        if ($actualVisible -ne $ExpectedVisible) {
            throw "$Name visibility was $actualVisible; expected $ExpectedVisible"
        }
        Write-Host "[PASS] $Name (PID $($process.Id), visible=$actualVisible)"
    }
    finally {
        if ($process) {
            $process.Refresh()
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                if (-not $process.WaitForExit(5000)) {
                    throw "Timed out stopping $Name PID $($process.Id)"
                }
            }
        }
    }
}

try {
    $env:APPDATA = $profileRoot

    Write-E2ESettings -StartMinimized $true
    Invoke-LaunchProbe -Name "manual-visible" -Executable $manualExe -ExpectedVisible $true

    Write-E2ESettings -StartMinimized $false
    Invoke-LaunchProbe -Name "autostart-visible" -Executable $autostartExe -ExpectedVisible $true

    Write-E2ESettings -StartMinimized $true
    Invoke-LaunchProbe -Name "autostart-hidden" -Executable $autostartExe -ExpectedVisible $false
}
finally {
    $env:APPDATA = $originalAppData
    $resolvedProfile = [System.IO.Path]::GetFullPath($profileRoot)
    $resolvedProfilesRoot = [System.IO.Path]::GetFullPath($profilesRoot)
    if ($resolvedProfile.StartsWith($resolvedProfilesRoot, [StringComparison]::OrdinalIgnoreCase) -and
        (Test-Path -LiteralPath $resolvedProfile)) {
        Remove-Item -LiteralPath $resolvedProfile -Recurse -Force
    }
}

Write-Host "[UTHelper] Windows bundle E2E passed."
