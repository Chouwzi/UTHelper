param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [ValidateRange(2, 5)][int]$Repeats = 3
)

# Real installation is confined to disposable hosted runners. Never replace a
# maintainer's installed app, register a benchmark product, or touch their profile.
$ErrorActionPreference = "Stop"
if ($env:GITHUB_ACTIONS -ne "true" -or $env:RUNNER_ENVIRONMENT -ne "github-hosted") {
    throw "Run installation measurements only on a disposable GitHub-hosted Windows runner"
}
$msi = (Resolve-Path -LiteralPath $MsiPath).Path
$installDir = Join-Path $env:ProgramFiles "UTHelper"
if (Test-Path -LiteralPath $installDir) { throw "Existing UTHelper installation must not be overwritten" }
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($msi, 0)
$view = $database.OpenView('SELECT `Value` FROM `Property` WHERE `Property`=''ProductCode''')
$view.Execute()
$productCode = $view.Fetch().StringData(1)
if ($productCode -notmatch '^\{[0-9A-Fa-f-]{36}\}$') { throw "Invalid MSI ProductCode" }
$root = Join-Path $env:RUNNER_TEMP ("uthelper-package-bench-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $root | Out-Null
$samples = [Collections.Generic.List[object]]::new()
$active = $false

function Invoke-TimedMsi([string[]]$Arguments) {
    $start = [Diagnostics.ProcessStartInfo]::new("msiexec.exe")
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $start.Environment["APPDATA"] = Join-Path $root "AppData"
    $start.Environment["LOCALAPPDATA"] = Join-Path $root "LocalAppData"
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $process = [Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit(180000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "MSI operation timed out"
        }
        if ($process.ExitCode -ne 0) { throw "MSI operation failed: $($process.ExitCode)" }
        return $watch.Elapsed.TotalSeconds
    } finally { $process.Dispose() }
}

try {
    for ($round = 0; $round -lt $Repeats; $round++) {
        # Mark before invocation so a partial MSI install is also cleaned up.
        $active = $true
        $installSeconds = Invoke-TimedMsi @("/i", $msi, "/qn", "/norestart", "/l*v", (Join-Path $root "install-$round.log"))
        $files = @(Get-ChildItem -LiteralPath $installDir -Recurse -File)
        $installedBytes = ($files | Measure-Object Length -Sum).Sum
        $verify = Join-Path $PSScriptRoot "verify_windows_bundle.py"
        python $verify $installDir
        if ($LASTEXITCODE -ne 0) { throw "Installed bundle failed verification" }
        # The existing harness isolates APPDATA and tests visible/hidden launches,
        # tray activation and single-instance recovery. It is not a frame benchmark.
        & (Join-Path $PSScriptRoot "test_windows_single_instance_e2e.ps1") `
            -ExePath (Join-Path $installDir "UTHelper.exe") `
            -StartupAliasPath (Join-Path $installDir "UTHelperAutostart.exe") `
            -WorkingDirectory $installDir -WindowTimeoutSeconds 15
        if ($LASTEXITCODE -ne 0) { throw "Installed application smoke test failed" }
        $uninstallSeconds = Invoke-TimedMsi @("/x", $productCode, "/qn", "/norestart", "/l*v", (Join-Path $root "uninstall-$round.log"))
        $active = $false
        if (Test-Path -LiteralPath $installDir) { throw "Uninstall did not remove the installed bundle" }
        $samples.Add(@{
            install_seconds = $installSeconds; uninstall_seconds = $uninstallSeconds
            installed_bytes = $installedBytes; installed_files = $files.Count
            smoke = "manual/autostart/hidden/activation passed"
        })
    }
    $result = @{
        schema_version = 1; artifact = [IO.Path]::GetFileName($msi)
        bytes = (Get-Item -LiteralPath $msi).Length
        sha256 = (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash.ToLowerInvariant()
        scope = "real MSI install/uninstall and activation smoke on a disposable hosted Windows runner; warm OS cache, not physical-device frame timing"
        samples = @($samples.ToArray())
    }
    $destination = [IO.Path]::GetFullPath($OutputPath)
    New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
    $result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $destination -Encoding utf8
} finally {
    if ($active) { [void](Invoke-TimedMsi @("/x", $productCode, "/qn", "/norestart")) }
    # Retain installer logs in RUNNER_TEMP for failure diagnostics; the runner is
    # disposable. They are never uploaded with customer or credential state.
}
