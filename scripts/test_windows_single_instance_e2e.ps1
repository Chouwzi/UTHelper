param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [Parameter(Mandatory = $true)][string]$StartupAliasPath,
    [Parameter(Mandatory = $true)][string]$WorkingDirectory,
    [ValidateRange(1, 60)][int]$ProcessExitTimeoutSeconds = 5,
    [ValidateRange(2, 60)][int]$WindowTimeoutSeconds = 10
)

$ErrorActionPreference = "Stop"
$resolvedExe = (Resolve-Path -LiteralPath $ExePath).Path
$resolvedAlias = (Resolve-Path -LiteralPath $StartupAliasPath).Path
$resolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path

if (-not (Test-Path -LiteralPath $resolvedExe -PathType Leaf)) {
    throw "UTHelper executable was not found: $resolvedExe"
}
if (-not (Test-Path -LiteralPath $resolvedAlias -PathType Leaf)) {
    throw "UTHelper startup alias was not found: $resolvedAlias"
}
if (-not (Test-Path -LiteralPath $resolvedWorkingDirectory -PathType Container)) {
    throw "Working directory was not found: $resolvedWorkingDirectory"
}

$processNames = @(
    [IO.Path]::GetFileNameWithoutExtension($resolvedExe),
    [IO.Path]::GetFileNameWithoutExtension($resolvedAlias)
) | Select-Object -Unique
$existing = Get-Process -Name $processNames -ErrorAction SilentlyContinue
if ($existing) {
    $ids = ($existing | Select-Object -ExpandProperty Id) -join ", "
    throw "Close existing UTHelper processes before E2E (PID: $ids)"
}

if (-not ("UTHelperWindowProbe" -as [type])) {
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public static class UTHelperWindowProbe
{
    private const int SW_HIDE = 0;
    private delegate bool EnumWindowsProc(IntPtr hwnd, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr lParam);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool IsIconic(IntPtr hwnd);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr hwnd, int command);

    public static bool HasWindow(uint targetProcessId)
    {
        bool found = false;
        EnumWindows((hwnd, lParam) =>
        {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner == targetProcessId)
            {
                found = true;
            }
            return !found;
        }, IntPtr.Zero);
        return found;
    }

    public static bool IsVisibleAndRestored(uint targetProcessId)
    {
        bool found = false;
        EnumWindows((hwnd, lParam) =>
        {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner == targetProcessId && IsWindowVisible(hwnd) && !IsIconic(hwnd))
            {
                found = true;
            }
            return !found;
        }, IntPtr.Zero);
        return found;
    }

    public static bool IsHidden(uint targetProcessId)
    {
        bool hasWindow = false;
        bool visible = false;
        EnumWindows((hwnd, lParam) =>
        {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner == targetProcessId)
            {
                hasWindow = true;
                if (IsWindowVisible(hwnd))
                {
                    visible = true;
                }
            }
            return !visible;
        }, IntPtr.Zero);
        return hasWindow && !visible;
    }

    public static bool Hide(uint targetProcessId)
    {
        bool found = false;
        EnumWindows((hwnd, lParam) =>
        {
            uint owner;
            GetWindowThreadProcessId(hwnd, out owner);
            if (owner == targetProcessId)
            {
                found = true;
                ShowWindow(hwnd, SW_HIDE);
            }
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
"@
}

$ownedProcesses = [System.Collections.Generic.List[System.Diagnostics.Process]]::new()
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$profileRoot = Join-Path $tempRoot ("UTHelper-e2e-" + [guid]::NewGuid().ToString("N"))
$settingsDirectory = Join-Path $profileRoot "UTHelper"
$settingsFile = Join-Path $settingsDirectory "settings.json"
$originalAppData = $env:APPDATA
$originalLocalAppData = $env:LOCALAPPDATA
$originalFletData = $env:FLET_APP_STORAGE_DATA
$originalFletTemp = $env:FLET_APP_STORAGE_TEMP

function Wait-Until {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Predicate,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $TimeoutSeconds) {
        if (& $Predicate) {
            return
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not (& $Predicate)) {
        throw "$Description missed its $TimeoutSeconds second deadline"
    }
}

function Start-OwnedProcess {
    param([Parameter(Mandatory = $true)][string]$FilePath)

    $process = Start-Process `
        -FilePath $FilePath `
        -WorkingDirectory $resolvedWorkingDirectory `
        -PassThru
    $ownedProcesses.Add($process)
    return $process
}

function Assert-ExitedSuccessfully {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (-not $Process.WaitForExit($ProcessExitTimeoutSeconds * 1000)) {
        throw "$Description PID $($Process.Id) did not exit within $ProcessExitTimeoutSeconds seconds"
    }
    if ($Process.ExitCode -ne 0) {
        throw "$Description PID $($Process.Id) exited with code $($Process.ExitCode)"
    }
}

function Assert-AllExitedSuccessfully {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process[]]$Processes,
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][Diagnostics.Stopwatch]$Stopwatch
    )

    foreach ($process in $Processes) {
        $remaining = [Math]::Max(
            0,
            [int](($ProcessExitTimeoutSeconds - $Stopwatch.Elapsed.TotalSeconds) * 1000)
        )
        if (-not $process.WaitForExit($remaining)) {
            throw "$Description PID $($process.Id) missed the shared $ProcessExitTimeoutSeconds second deadline"
        }
        if ($process.ExitCode -ne 0) {
            throw "$Description PID $($process.Id) exited with code $($process.ExitCode)"
        }
    }
}

function Assert-HiddenForObservationWindow {
    param(
        [Parameter(Mandatory = $true)][Diagnostics.Process]$Process,
        [ValidateRange(1, 10)][int]$Seconds = 2
    )

    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    while ($stopwatch.Elapsed.TotalSeconds -lt $Seconds) {
        $Process.Refresh()
        if ($Process.HasExited) {
            throw "Primary PID $($Process.Id) exited during the hidden observation window"
        }
        if (-not [UTHelperWindowProbe]::IsHidden([uint32]$Process.Id)) {
            throw "Primary PID $($Process.Id) became visible during the hidden observation window"
        }
        Start-Sleep -Milliseconds 100
    }
}

function Hide-OwnedPrimary {
    param([Parameter(Mandatory = $true)][Diagnostics.Process]$Process)

    if (-not [UTHelperWindowProbe]::Hide([uint32]$Process.Id)) {
        throw "No window was found for owned primary PID $($Process.Id)"
    }
    Wait-Until `
        -TimeoutSeconds $WindowTimeoutSeconds `
        -Description "Hide primary PID $($Process.Id)" `
        -Predicate { [UTHelperWindowProbe]::IsHidden([uint32]$Process.Id) }
}

New-Item -ItemType Directory -Path $settingsDirectory -Force | Out-Null
$settingsJson = @{
    START_WITH_WINDOWS = $true
    START_MINIMIZED = $true
    MINIMIZE_TO_TRAY = $true
    CHECK_INTERVAL_MINUTES = 0
    AUTO_UPDATE_ENABLED = $false
    CRASH_REPORTING_CONSENT = "disabled"
} | ConvertTo-Json
[IO.File]::WriteAllText(
    $settingsFile,
    $settingsJson,
    [Text.UTF8Encoding]::new($false)
)

try {
    $env:APPDATA = $profileRoot
    $env:LOCALAPPDATA = $profileRoot
    $env:FLET_APP_STORAGE_DATA = $null
    $env:FLET_APP_STORAGE_TEMP = $null

    # 1. The argument-free startup alias owns the hidden primary.
    $primary = Start-OwnedProcess -FilePath $resolvedAlias
    Wait-Until `
        -TimeoutSeconds $WindowTimeoutSeconds `
        -Description "Startup alias hidden readiness" `
        -Predicate {
            $primary.Refresh()
            -not $primary.HasExited -and [UTHelperWindowProbe]::IsHidden([uint32]$primary.Id)
        }
    Write-Host "[PASS] startup alias primary is ready and hidden (PID $($primary.Id))"

    # 2. A manual second launch exits and reveals/restores the same primary.
    $manualSecondary = Start-OwnedProcess -FilePath $resolvedExe
    Assert-ExitedSuccessfully -Process $manualSecondary -Description "Manual secondary"
    $primary.Refresh()
    if ($primary.HasExited) {
        throw "Original primary PID $($primary.Id) exited after manual handoff"
    }
    Wait-Until `
        -TimeoutSeconds $WindowTimeoutSeconds `
        -Description "Manual activation reveal" `
        -Predicate { [UTHelperWindowProbe]::IsVisibleAndRestored([uint32]$primary.Id) }
    Write-Host "[PASS] manual launch revealed original primary PID $($primary.Id)"

    # 3. The startup alias remains silent while a hidden primary exists.
    Hide-OwnedPrimary -Process $primary
    $silentSecondary = Start-OwnedProcess -FilePath $resolvedAlias
    Assert-ExitedSuccessfully -Process $silentSecondary -Description "Autostart secondary"
    Assert-HiddenForObservationWindow -Process $primary -Seconds 2
    Write-Host "[PASS] autostart secondary stayed silent"

    # 4. Concurrent manual launches coalesce into one reveal of the primary.
    Hide-OwnedPrimary -Process $primary
    $concurrentStopwatch = [Diagnostics.Stopwatch]::StartNew()
    $concurrentSecondaries = @(
        Start-OwnedProcess -FilePath $resolvedExe
        Start-OwnedProcess -FilePath $resolvedExe
        Start-OwnedProcess -FilePath $resolvedExe
        Start-OwnedProcess -FilePath $resolvedExe
    )
    Assert-AllExitedSuccessfully `
        -Processes $concurrentSecondaries `
        -Description "Concurrent manual secondary" `
        -Stopwatch $concurrentStopwatch
    Wait-Until `
        -TimeoutSeconds $WindowTimeoutSeconds `
        -Description "Concurrent activation reveal" `
        -Predicate { [UTHelperWindowProbe]::IsVisibleAndRestored([uint32]$primary.Id) }
    $unexpectedSurvivors = @($concurrentSecondaries | Where-Object { -not $_.HasExited })
    if ($unexpectedSurvivors.Count -ne 0) {
        throw "A manual secondary remained alive after concurrent handoff"
    }
    Write-Host "[PASS] four manual launches revealed only original primary PID $($primary.Id)"

    # 5. After the owned primary exits, a manual launch becomes a new visible primary.
    Stop-Process -Id $primary.Id -Force
    if (-not $primary.WaitForExit($ProcessExitTimeoutSeconds * 1000)) {
        throw "Owned primary PID $($primary.Id) did not terminate within $ProcessExitTimeoutSeconds seconds"
    }
    $replacement = Start-OwnedProcess -FilePath $resolvedExe
    if ($replacement.Id -eq $primary.Id) {
        throw "Replacement unexpectedly reused the recorded primary PID"
    }
    Wait-Until `
        -TimeoutSeconds $WindowTimeoutSeconds `
        -Description "Replacement primary visible readiness" `
        -Predicate {
            $replacement.Refresh()
            -not $replacement.HasExited -and
                [UTHelperWindowProbe]::IsVisibleAndRestored([uint32]$replacement.Id)
        }
    Write-Host "[PASS] manual launch became visible replacement primary PID $($replacement.Id)"

    Stop-Process -Id $replacement.Id -Force
    if (-not $replacement.WaitForExit($ProcessExitTimeoutSeconds * 1000)) {
        throw "Replacement primary PID $($replacement.Id) did not terminate within $ProcessExitTimeoutSeconds seconds"
    }

    $packagedLogs = @(
        Join-Path $settingsDirectory "debug_app.log"
        Join-Path $settingsDirectory "logs\app.log"
    )
    $failOpenFound = $false
    foreach ($packagedLog in $packagedLogs) {
        if ((Test-Path -LiteralPath $packagedLog -PathType Leaf) -and
            ((Get-Content -LiteralPath $packagedLog -Raw) -match "single_instance_fail_open")) {
            $failOpenFound = $true
        }
    }
    if ($failOpenFound) {
        Write-Output "single_instance_fail_open"
    }
}
finally {
    $cleanupFailures = [System.Collections.Generic.List[string]]::new()
    foreach ($ownedProcess in $ownedProcesses) {
        try {
            $ownedProcess.Refresh()
            if (-not $ownedProcess.HasExited) {
                [void]$ownedProcess.CloseMainWindow()
                if (-not $ownedProcess.WaitForExit(3000)) {
                    Stop-Process -Id $ownedProcess.Id -Force -ErrorAction SilentlyContinue
                    if (-not $ownedProcess.WaitForExit(3000)) {
                        $cleanupFailures.Add("Owned PID $($ownedProcess.Id) missed the forced cleanup deadline")
                    }
                }
            }
        }
        catch {
            $cleanupFailures.Add("Owned PID cleanup failed for $($ownedProcess.Id)")
        }
        finally {
            $ownedProcess.Dispose()
        }
    }

    $env:APPDATA = $originalAppData
    $env:LOCALAPPDATA = $originalLocalAppData
    $env:FLET_APP_STORAGE_DATA = $originalFletData
    $env:FLET_APP_STORAGE_TEMP = $originalFletTemp

    $resolvedProfile = [IO.Path]::GetFullPath($profileRoot)
    $tempPrefix = $tempRoot.TrimEnd('\') + '\'
    if (-not $resolvedProfile.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove profile outside the system temp directory: $resolvedProfile"
    }
    if (Test-Path -LiteralPath $resolvedProfile) {
        Remove-Item -LiteralPath $resolvedProfile -Recurse -Force
    }
    if ($cleanupFailures.Count -gt 0) {
        throw ($cleanupFailures -join "; ")
    }
}

Write-Host "[UTHelper] Windows single-instance E2E passed."
