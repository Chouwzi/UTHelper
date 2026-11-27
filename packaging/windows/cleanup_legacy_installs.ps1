$ErrorActionPreference = 'SilentlyContinue'

function Remove-SafeShortcut([string]$Path) {
    if ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    }
}

function Remove-SafeShortcutFolder([string]$FolderPath) {
    if ($FolderPath -and (Test-Path -LiteralPath $FolderPath -PathType Container)) {
        Get-ChildItem -LiteralPath $FolderPath -Filter '*.lnk' -Force -ErrorAction SilentlyContinue | ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
        }
        $remaining = @(Get-ChildItem -LiteralPath $FolderPath -Force -ErrorAction SilentlyContinue)
        if ($remaining.Count -eq 0) {
            Remove-Item -LiteralPath $FolderPath -Force -ErrorAction SilentlyContinue
        }
    }
}

function Split-UninstallCommand([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
    $trimmed = $CommandLine.Trim()
    if ($trimmed.StartsWith('"')) {
        $endQuote = $trimmed.IndexOf('"', 1)
        if ($endQuote -gt 1) {
            return @{
                Exe  = $trimmed.Substring(1, $endQuote - 1)
                Args = $trimmed.Substring($endQuote + 1).Trim()
            }
        }
    }
    $exeMatch = [regex]::Match($trimmed, '^([^\s]+\.exe)\b(.*)$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($exeMatch.Success) {
        return @{
            Exe  = $exeMatch.Groups[1].Value
            Args = $exeMatch.Groups[2].Value.Trim()
        }
    }
    return $null
}

# 1. Silently uninstall legacy Inno Setup versions (<= v2.4.1) so old versions never linger alongside MSI/Burn
$legacyInnoKeys = @(
    '{D1A25B3C-9A5C-4F62-8E3A-7B5E2C1A8B9D}_is1',
    '{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}_is1'
)
$uninstallRoots = @(
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
)
$programFilesUth = Join-Path $env:ProgramFiles 'UTHelper'

foreach ($root in $uninstallRoots) {
    foreach ($keyName in $legacyInnoKeys) {
        $regPath = Join-Path $root $keyName
        if (-not (Test-Path -LiteralPath $regPath)) { continue }
        $props = Get-ItemProperty -LiteralPath $regPath -ErrorAction SilentlyContinue
        if ($null -eq $props) { continue }
        $rawCmd = if ($props.QuietUninstallString) { [string]$props.QuietUninstallString } else { [string]$props.UninstallString }
        $parsed = Split-UninstallCommand $rawCmd
        if ($null -ne $parsed -and (Test-Path -LiteralPath $parsed.Exe -PathType Leaf)) {
            $exeDir = Split-Path -Parent $parsed.Exe
            if (-not $exeDir.Equals($programFilesUth, [System.StringComparison]::OrdinalIgnoreCase)) {
                $psi = [System.Diagnostics.ProcessStartInfo]::new($parsed.Exe)
                $extraFlags = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
                $psi.Arguments = if ($parsed.Args) { "$($parsed.Args) $extraFlags" } else { $extraFlags }
                $psi.UseShellExecute = $false
                $psi.CreateNoWindow = $true
                $proc = [System.Diagnostics.Process]::Start($psi)
                if ($null -ne $proc) {
                    [void]$proc.WaitForExit(45000)
                    $proc.Dispose()
                }
            }
        }
    }
}

# 2. Remove duplicate/stale per-user Start Menu, Startup, and Desktop shortcuts
if ($env:APPDATA) {
    $userPrograms = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    Remove-SafeShortcut (Join-Path $userPrograms 'UTHelper.lnk')
    Remove-SafeShortcut (Join-Path $userPrograms 'UTH Elearning Alert.lnk')
    Remove-SafeShortcutFolder (Join-Path $userPrograms 'UTHelper')
    Remove-SafeShortcutFolder (Join-Path $userPrograms 'UTH Elearning Alert')

    $userStartup = Join-Path $userPrograms 'Startup'
    Remove-SafeShortcut (Join-Path $userStartup 'UTHelper.lnk')
    Remove-SafeShortcut (Join-Path $userStartup 'UTH Elearning Alert.lnk')
}

if ($env:USERPROFILE) {
    $userDesktop = Join-Path $env:USERPROFILE 'Desktop'
    Remove-SafeShortcut (Join-Path $userDesktop 'UTH Elearning Alert.lnk')
    $publicDesktopLnk = if ($env:PUBLIC) { Join-Path $env:PUBLIC 'Desktop\UTHelper.lnk' } else { '' }
    if ($publicDesktopLnk -and (Test-Path -LiteralPath $publicDesktopLnk -PathType Leaf)) {
        Remove-SafeShortcut (Join-Path $userDesktop 'UTHelper.lnk')
    }
}
exit 0
