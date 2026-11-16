param(
    [Parameter(Mandatory = $true)][string]$BaselineMsi,
    [Parameter(Mandatory = $true)][string]$CurrentMsi,
    [Parameter(Mandatory = $true)][string]$BurnExe,
    [Parameter(Mandatory = $true)][string]$BaselineProductCode,
    [Parameter(Mandatory = $true)][string]$CurrentProductCode,
    [ValidateRange(10, 300)][int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$UpgradeCode = "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}"
function Invoke-BoundedProcess([string]$FilePath, [string[]]$Arguments, [hashtable]$Environment = @{}) {
    $start = [Diagnostics.ProcessStartInfo]::new($FilePath)
    $start.UseShellExecute = $false
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    foreach ($entry in $Environment.GetEnumerator()) { $start.Environment[$entry.Key] = $entry.Value }
    $process = [Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Installer process timed out"
        }
        return $process.ExitCode
    } finally { $process.Dispose() }
}

function Read-MsiProperty([string]$Path, [string]$Name) {
    $installer = New-Object -ComObject WindowsInstaller.Installer
    $database = $installer.OpenDatabase((Resolve-Path -LiteralPath $Path).Path, 0)
    $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``=?")
    $record = $installer.CreateRecord(1)
    $record.StringData(1) = $Name
    $view.Execute($record)
    $row = $view.Fetch()
    if ($null -eq $row) { throw "MSI property missing: $Name" }
    return [string]$row.StringData(1)
}

function Test-ProductInstalled([string]$ProductCode) {
    $paths = @(
        "Registry::HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode",
        "Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$ProductCode"
    )
    return @($paths | Where-Object { Test-Path -LiteralPath $_ }).Count -gt 0
}

function Assert-InstalledState([string]$ExpectedProductCode, [string]$UnexpectedProductCode, [string]$ExpectedVersion) {
    if (-not (Test-ProductInstalled $ExpectedProductCode)) { throw "Expected ProductCode is not installed" }
    if (Test-ProductInstalled $UnexpectedProductCode) { throw "Superseded ProductCode is still installed" }
    $installDir = Join-Path $env:ProgramFiles "UTHelper"
    if (-not (Test-Path -LiteralPath (Join-Path $installDir "UTHelper.exe") -PathType Leaf)) {
        throw "Installed UTHelper.exe is missing"
    }
    $marker = Get-ItemProperty -LiteralPath "Registry::HKEY_LOCAL_MACHINE\Software\UTHelper" -ErrorAction Stop
    if ($marker.InstallChannel -ne "msi") { throw "InstallChannel marker is invalid" }
    if ($marker.InstallVersion -ne $ExpectedVersion) { throw "InstallVersion marker does not match the installed MSI" }
    $shortcutId = "StartMenuUTHelper"
    $shortcutCandidates = @(
        (Join-Path ([Environment]::GetFolderPath("CommonPrograms")) "UTHelper\UTHelper.lnk"),
        (Join-Path ([Environment]::GetFolderPath("Programs")) "UTHelper\UTHelper.lnk")
    )
    if (@($shortcutCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }).Count -ne 1) {
        throw "Expected exactly one UTHelper Start Menu shortcut ($shortcutId)"
    }
}

function Assert-UninstalledState([string[]]$ProductCodes) {
    foreach ($productCode in $ProductCodes) {
        if (Test-ProductInstalled $productCode) { throw "ProductCode remains installed after uninstall" }
    }
    if (Test-Path -LiteralPath "Registry::HKEY_LOCAL_MACHINE\Software\UTHelper") {
        throw "Installer marker remains after uninstall"
    }
    if (Test-Path -LiteralPath (Join-Path $env:ProgramFiles "UTHelper")) {
        throw "Installation directory remains after uninstall"
    }
}

function Invoke-SafeUninstall([string]$ProductCode, [hashtable]$Environment) {
    try {
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            $exitCode = Invoke-BoundedProcess "msiexec.exe" @("/x", $ProductCode, "/qn", "/norestart") $Environment
            if ($exitCode -in @(0, 1605)) { return $true }
            if ($exitCode -eq 1618 -and $attempt -lt 3) {
                Start-Sleep -Seconds 2
                continue
            }
            Write-Warning "Cleanup uninstall failed for $ProductCode with exit code $exitCode"
            return $false
        }
    } catch {
        Write-Warning "Cleanup uninstall failed for $ProductCode`: $($_.Exception.Message)"
        return $false
    }
    return $false
}

$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$testRoot = Join-Path $tempBase ("uthelper-msi-e2e-" + [guid]::NewGuid().ToString("N"))
$isolatedAppData = Join-Path $testRoot "AppData"
$isolatedLocalAppData = Join-Path $testRoot "LocalAppData"
$sentinel = Join-Path $isolatedAppData "UTHelper\settings.json"
New-Item -ItemType Directory -Path (Split-Path $sentinel) -Force | Out-Null
$installEnvironment = @{ APPDATA=$isolatedAppData; LOCALAPPDATA=$isolatedLocalAppData }
$primaryFailure = $null
try {
    if ($BaselineProductCode -eq $CurrentProductCode) { throw "BaselineProductCode and CurrentProductCode must differ" }
    $baselineVersion = Read-MsiProperty $BaselineMsi "ProductVersion"
    $currentVersion = Read-MsiProperty $CurrentMsi "ProductVersion"
    if ($baselineVersion -eq $currentVersion) { throw "Baseline and current MSI versions must differ" }
    if ((Read-MsiProperty $BaselineMsi "ProductCode") -ne $BaselineProductCode -or
        (Read-MsiProperty $CurrentMsi "ProductCode") -ne $CurrentProductCode) { throw "Supplied ProductCode does not match MSI identity" }
    if ((Read-MsiProperty $BaselineMsi "UpgradeCode") -ne $UpgradeCode -or
        (Read-MsiProperty $CurrentMsi "UpgradeCode") -ne $UpgradeCode) { throw "MSI UpgradeCode contract mismatch" }
    if ((Invoke-BoundedProcess "msiexec.exe" @("/i", $BaselineMsi, "/qn", "/norestart") $installEnvironment) -ne 0) { throw "Baseline install failed" }
    Assert-InstalledState $BaselineProductCode $CurrentProductCode $baselineVersion
    [IO.File]::WriteAllText($sentinel, '{"sentinel":true}')
    $failureInjection = "WIXFAILWHENDEFERRED=1"
    if (-not $failureInjection) { throw "Failure injection contract missing" }
    $failedUpgrade = Invoke-BoundedProcess "msiexec.exe" @("/i", $CurrentMsi, "WIXFAILWHENDEFERRED=1", "/qn", "/norestart") $installEnvironment
    if ($failedUpgrade -eq 0 -or -not (Test-Path $sentinel)) { throw "Failed upgrade rollback did not preserve baseline and sentinel" }
    Assert-InstalledState $BaselineProductCode $CurrentProductCode $baselineVersion
    if ((Invoke-BoundedProcess "msiexec.exe" @("/i", $CurrentMsi, "/qn", "/norestart") $installEnvironment) -ne 0) { throw "Current MSI upgrade failed" }
    Assert-InstalledState $CurrentProductCode $BaselineProductCode $currentVersion
    if (-not (Test-Path $sentinel)) { throw "Successful upgrade removed user settings sentinel" }
    if ((Invoke-BoundedProcess "msiexec.exe" @("/i", $BaselineMsi, "/qn", "/norestart") $installEnvironment) -eq 0) { throw "Downgrade was not rejected" }
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "UTHelper" -Value "stale" -PropertyType String -Force | Out-Null
    New-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "UTHElearningAlert" -Value "stale" -PropertyType String -Force | Out-Null
    if ((Invoke-BoundedProcess "msiexec.exe" @("/x", $CurrentProductCode, "/qn", "/norestart") $installEnvironment) -ne 0) { throw "MSI uninstall failed" }
    Assert-UninstalledState @($BaselineProductCode, $CurrentProductCode)
    $runValues = Get-ItemProperty -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if ($null -ne $runValues.UTHelper -or $null -ne $runValues.UTHElearningAlert) { throw "Uninstall left stale autostart values" }
    if (-not (Test-Path $sentinel)) { throw "MSI uninstall removed user settings sentinel" }
    if ((Invoke-BoundedProcess $BurnExe @("/quiet", "/norestart") $installEnvironment) -ne 0) { throw "Burn install failed" }
    Assert-InstalledState $CurrentProductCode $BaselineProductCode $currentVersion
    if ((Invoke-BoundedProcess $BurnExe @("/uninstall", "/quiet", "/norestart") $installEnvironment) -ne 0) { throw "Burn uninstall failed" }
    Assert-UninstalledState @($BaselineProductCode, $CurrentProductCode)
    if (-not $UpgradeCode) { throw "UpgradeCode contract missing" }
} catch {
    $primaryFailure = $_
}

$cleanupFailures = [Collections.Generic.List[string]]::new()
if (-not (Invoke-SafeUninstall $CurrentProductCode $installEnvironment)) { $cleanupFailures.Add($CurrentProductCode) }
if (-not (Invoke-SafeUninstall $BaselineProductCode $installEnvironment)) { $cleanupFailures.Add($BaselineProductCode) }
try {
    Assert-UninstalledState @($BaselineProductCode, $CurrentProductCode)
} catch {
    $cleanupFailures.Add($_.Exception.Message)
}
Remove-ItemProperty -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "UTHelper" -ErrorAction SilentlyContinue
Remove-ItemProperty -LiteralPath "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "UTHElearningAlert" -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($primaryFailure) {
    foreach ($failure in $cleanupFailures) { Write-Warning "Cleanup issue after primary failure: $failure" }
    throw $primaryFailure
}
if ($cleanupFailures.Count -gt 0) {
    throw "Installer E2E cleanup failed: $($cleanupFailures -join '; ')"
}
