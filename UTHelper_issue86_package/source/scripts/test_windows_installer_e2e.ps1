param(
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$InstallDir = "build\installer-e2e\UTHelper",
    [ValidateRange(2, 60)][int]$ObservationSeconds = 8,
    [ValidateRange(10, 300)][int]$ProcessTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedInstaller = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $resolvedInstaller -PathType Leaf)) {
    throw "Installer was not found: $resolvedInstaller"
}

$resolvedInstall = [System.IO.Path]::GetFullPath(
    $(if ([System.IO.Path]::IsPathRooted($InstallDir)) {
        $InstallDir
    } else {
        Join-Path $workspaceRoot $InstallDir
    })
)
$workspacePrefix = $workspaceRoot.TrimEnd('\') + '\'
if (-not $resolvedInstall.StartsWith(
    $workspacePrefix,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "InstallDir must be a child of the workspace: $resolvedInstall"
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $process = $null
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            [void]$process.WaitForExit(5000)
            throw "$Name timed out after $TimeoutSeconds seconds"
        }
        if ($process.ExitCode -ne 0) {
            throw "$Name failed with exit code $($process.ExitCode)"
        }
    }
    finally {
        if ($process) {
            $process.Refresh()
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                [void]$process.WaitForExit(5000)
            }
        }
    }
}

$existingInstall = Get-ChildItem `
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall" `
    -ErrorAction SilentlyContinue | Where-Object {
        (Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DisplayName -eq "UTHelper"
    } | Select-Object -First 1
if ($existingInstall) {
    throw "A current-user UTHelper installation already exists; refusing to overwrite it"
}

$testRoot = Join-Path $workspaceRoot "build\installer-e2e"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
$installLog = Join-Path $testRoot "install.log"
$uninstallLog = Join-Path $testRoot "uninstall.log"
$installed = $false
$uninstaller = Join-Path $resolvedInstall "unins000.exe"

try {
    if (Test-Path -LiteralPath $resolvedInstall) {
        Remove-Item -LiteralPath $resolvedInstall -Recurse -Force
    }

    $installArguments = @(
        "/SP-",
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/DIR=`"$resolvedInstall`"",
        "/LOG=`"$installLog`""
    )
    Invoke-BoundedProcess `
        -FilePath $resolvedInstaller `
        -ArgumentList $installArguments `
        -Name "Inno installation" `
        -TimeoutSeconds $ProcessTimeoutSeconds
    $installed = $true

    if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        throw "Installed uninstaller was not found: $uninstaller"
    }

    python (Join-Path $PSScriptRoot "verify_windows_bundle.py") $resolvedInstall
    if ($LASTEXITCODE -ne 0) { throw "Installed bundle verification failed" }

    & (Join-Path $PSScriptRoot "test_windows_bundle_e2e.ps1") `
        -BundleDir $resolvedInstall `
        -ObservationSeconds $ObservationSeconds
    if ($LASTEXITCODE -ne 0) { throw "Installed bundle E2E failed" }
}
finally {
    if ($installed -and (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
        $uninstallArguments = @(
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/LOG=`"$uninstallLog`""
        )
        Invoke-BoundedProcess `
            -FilePath $uninstaller `
            -ArgumentList $uninstallArguments `
            -Name "Inno uninstallation" `
            -TimeoutSeconds $ProcessTimeoutSeconds
    }

    if (Test-Path -LiteralPath $resolvedInstall) {
        Remove-Item -LiteralPath $resolvedInstall -Recurse -Force
    }
}

Write-Host "[UTHelper] Installed Windows E2E and uninstall cleanup passed."
