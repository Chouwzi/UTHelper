param(
    [Parameter(Mandatory = $true)][string]$MsiPath,
    [Parameter(Mandatory = $true)][string]$ExePath,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$ExpectedSubject,
    [Parameter(Mandatory = $true)][string]$ExpectedCertificateSha256,
    [Parameter(Mandatory = $true)][string]$CommitSha,
    [Parameter(Mandatory = $true)][string]$WorkflowRunId,
    [Parameter(Mandatory = $true)][string]$EvidenceDir
)

if ($env:WIX_EULA_ACCEPTED -ne "wix7") {
    throw "WiX verification requires owner-confirmed WIX_EULA_ACCEPTED=wix7"
}
$ErrorActionPreference = "Stop"
function Normalize-Hex([string]$Value) { return ($Value -replace '[^0-9A-Fa-f]', '').ToUpperInvariant() }
function Test-MsiOleMagic([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    try {
        if ($stream.Length -lt 8) { return $false }
        $header = [byte[]]::new(8)
        if ($stream.Read($header, 0, 8) -ne 8) { return $false }
        return [BitConverter]::ToString($header) -eq "D0-CF-11-E0-A1-B1-1A-E1"
    } finally {
        $stream.Dispose()
    }
}
function Get-Signature([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) { throw "Authenticode signature is not Valid" }
    if ($null -eq $signature.TimeStamperCertificate) { throw "Authenticode timestamp is missing" }
    $fingerprint = $signature.SignerCertificate.GetCertHashString([Security.Cryptography.HashAlgorithmName]::SHA256)
    if ((Normalize-Hex $fingerprint) -ne (Normalize-Hex $ExpectedCertificateSha256)) { throw "Signer fingerprint mismatch" }
    if ($signature.SignerCertificate.Subject -ne $ExpectedSubject) { throw "Signer subject mismatch" }
    return $signature
}
function Invoke-BoundedProcess([string]$FilePath, [string[]]$Arguments, [int]$TimeoutSeconds = 120) {
    $start = [Diagnostics.ProcessStartInfo]::new($FilePath)
    $start.UseShellExecute = $false
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::Start($start)
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            throw "Verification process timed out"
        }
        if ($process.ExitCode -ne 0) { throw "Verification process failed" }
    } finally { $process.Dispose() }
}

$msi = (Resolve-Path -LiteralPath $MsiPath).Path
$exe = (Resolve-Path -LiteralPath $ExePath).Path
if ([IO.Path]::GetFileName($msi) -ne "UTHelper-$Version.msi" -or
    [IO.Path]::GetFileName($exe) -ne "UTHelper-Setup-$Version.exe") { throw "Windows release filenames are not canonical" }
[void](Get-Signature $msi)
[void](Get-Signature $exe)

$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($msi, 0)
function Read-MsiProperty([string]$Name) {
    $view = $database.OpenView("SELECT ``Value`` FROM ``Property`` WHERE ``Property``=?")
    $record = $installer.CreateRecord(1); $record.StringData(1) = $Name
    $view.Execute($record); $row = $view.Fetch()
    if ($null -eq $row) { throw "MSI property missing: $Name" }
    return [string]$row.StringData(1)
}
if ((Read-MsiProperty "ProductName") -ne "UTHelper" -or
    (Read-MsiProperty "ProductVersion") -ne $Version -or
    (Read-MsiProperty "UpgradeCode") -ne "{B1EB1032-5ACD-497D-8FD2-AB760218CBE3}" -or
    ([string]$database.SummaryInformation(0).Property(7)) -notmatch "x64") { throw "MSI identity mismatch" }
Invoke-BoundedProcess "wix" @("msi", "validate", "-acceptEula", $env:WIX_EULA_ACCEPTED, $msi)

$tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
$tempRoot = Join-Path $tempBase ("burn-verify-" + [guid]::NewGuid().ToString("N"))
$payloadRoot = Join-Path $tempRoot "payloads"
$baRoot = Join-Path $tempRoot "bootstrapper-application"
New-Item -ItemType Directory -Path $payloadRoot, $baRoot -Force | Out-Null
try {
    Invoke-BoundedProcess "wix" @("burn", "extract", "-acceptEula", $env:WIX_EULA_ACCEPTED, $exe, "-o", $payloadRoot, "-oba", $baRoot)
    $embedded = @(
        Get-ChildItem -LiteralPath $payloadRoot -Recurse -File |
            Where-Object { Test-MsiOleMagic $_.FullName }
    )
    if ($embedded.Count -ne 1) { throw "Burn must contain exactly one MSI" }
    if ((Get-FileHash -LiteralPath $embedded[0].FullName -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $msi -Algorithm SHA256).Hash) { throw "Burn embedded MSI hash mismatch" }
    $manifestPath = Join-Path $baRoot "manifest.xml"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "Burn manifest.xml is missing" }
    [xml]$burnManifest = Get-Content -LiteralPath $manifestPath -Raw
    $registrations = @($burnManifest.SelectNodes("/*[local-name()='BurnManifest']/*[local-name()='Registration']"))
    if ($registrations.Count -ne 1) { throw "Burn must contain exactly one Registration" }
    $registration = $registrations[0]
    if ($registration.GetAttribute("Version") -ne $Version -or
        $registration.GetAttribute("PrimaryUpgradeCode") -ne "{EECFB4A5-4CCD-4D94-A0DD-D8D346F626E0}" -or
        $registration.GetAttribute("Scope") -ne "perMachine") { throw "Burn registration identity mismatch" }
    $manifestRoot = $burnManifest.DocumentElement
    if ($manifestRoot.GetAttribute("Win64") -ne "yes") { throw "Burn engine is not x64" }
} finally { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }

New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
$utf8 = [Text.UTF8Encoding]::new($false)
foreach ($item in @(@{ Path=$msi; Platform="windows"; Checks=@("authenticode","msi_ole","product_version","template","timestamp","upgrade_code") },
                     @{ Path=$exe; Platform="windows"; Checks=@("authenticode","burn_payload","pe_header","product_version","timestamp") })) {
    $record = [ordered]@{
        schema_version=2; platform=$item.Platform; asset_name=[IO.Path]::GetFileName($item.Path)
        sha256=(Get-FileHash -LiteralPath $item.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        version=$Version; product_id="UTHelper"; architecture="x64"; signature_kind="self-signed-pinned"
        signer_identity=$ExpectedSubject
        certificate_fingerprint=(Normalize-Hex $ExpectedCertificateSha256); signature_valid=$true
        timestamp_valid=$true; checks=$item.Checks; commit_sha=$CommitSha; workflow_run_id=$WorkflowRunId
    }
    $output = Join-Path $EvidenceDir ($record.asset_name + ".verification.json")
    [IO.File]::WriteAllText($output, (($record | ConvertTo-Json -Depth 5 -Compress) + "`n"), $utf8)
}
