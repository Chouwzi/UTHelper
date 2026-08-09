param(
    [Parameter(Mandatory = $true)][string]$BackupDirectory,
    [string]$Repository = "Chouwzi/UTHelper",
    [string]$Environment = "release",
    [switch]$DryRun,
    [string]$GhPath = "gh",
    [string]$KeytoolPath = "keytool"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backupPath = [IO.Path]::GetFullPath($BackupDirectory)
$repoPrefix = $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if ($backupPath -eq $repoRoot -or $backupPath.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BackupDirectory must remain outside the repository"
}
if (Test-Path -LiteralPath $backupPath) {
    if (-not (Test-Path -LiteralPath $backupPath -PathType Container) -or
        $null -ne (Get-ChildItem -LiteralPath $backupPath -Force | Select-Object -First 1)) {
        throw "BackupDirectory must be absent or empty"
    }
}
if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' -or
    $Environment -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "Repository or environment identifier is invalid"
}

$secretNames = @(
    "ANDROID_KEYSTORE_BASE64",
    "ANDROID_KEYSTORE_PASSWORD",
    "ANDROID_KEY_PASSWORD",
    "WINDOWS_PFX_BASE64",
    "WINDOWS_PFX_PASSWORD"
)
$variableNames = @(
    "ANDROID_KEY_ALIAS",
    "ANDROID_SIGNING_CERT_SHA256",
    "WINDOWS_SIGNING_CERT_SHA256",
    "WINDOWS_SIGNER_SUBJECT",
    "WINDOWS_TIMESTAMP_URL"
)
if ($DryRun) {
    Write-Output "Dry run: would create stable Android and Windows release identities."
    Write-Output "Encrypted GitHub material: ANDROID_KEYSTORE_BASE64, WINDOWS_PFX_BASE64."
    Write-Output ("Public GitHub variables: " + ($variableNames -join ", ") + ".")
    Write-Output "No files or GitHub settings were changed."
    exit 0
}

function Invoke-BoundedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [hashtable]$EnvironmentVariables = @{},
        [AllowNull()][string]$StandardInputText = $null,
        [int]$TimeoutSeconds = 120
    )
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 600) { throw "Invalid process timeout" }
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    foreach ($entry in $EnvironmentVariables.GetEnumerator()) {
        $start.Environment[$entry.Key] = [string]$entry.Value
    }
    $process = [Diagnostics.Process]::Start($start)
    try {
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if ($null -ne $StandardInputText) { $process.StandardInput.Write($StandardInputText) }
        $process.StandardInput.Close()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill($true) } catch {}
            [void]$process.WaitForExit(5000)
            throw "Release credential subprocess timed out"
        }
        if (-not $stdoutTask.Wait(5000) -or -not $stderrTask.Wait(5000)) {
            throw "Release credential subprocess output timed out"
        }
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result
        if ($stdout.Length -gt 1024 * 1024 -or $stderr.Length -gt 1024 * 1024) {
            throw "Release credential subprocess output exceeded its bound"
        }
        if ($process.ExitCode -ne 0) { throw "Release credential subprocess failed" }
        return $stdout
    } finally {
        $process.Dispose()
    }
}

function New-RandomSecret {
    $bytes = [byte[]]::new(48)
    [Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

function Set-GitHubSecret([string]$Name, [string]$Value) {
    [void](Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @(
        "secret", "set", $Name, "--repo", $Repository, "--env", $Environment
    ) -StandardInputText $Value -TimeoutSeconds 60)
}

function Set-GitHubVariable([string]$Name, [string]$Value) {
    [void](Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @(
        "variable", "set", $Name, "--repo", $Repository, "--env", $Environment,
        "--body", $Value
    ) -TimeoutSeconds 60)
}

$resolvedGh = (Get-Command $GhPath -ErrorAction Stop).Source
$resolvedKeytool = (Get-Command $KeytoolPath -ErrorAction Stop).Source
$existingSecrets = @(
    (Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @(
        "secret", "list", "--repo", $Repository, "--env", $Environment, "--json", "name",
        "--jq", ".[].name"
    ) -TimeoutSeconds 60) -split "`r?`n" | Where-Object { $_ }
)
$existingVariables = @(
    (Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @(
        "variable", "list", "--repo", $Repository, "--env", $Environment, "--json", "name",
        "--jq", ".[].name"
    ) -TimeoutSeconds 60) -split "`r?`n" | Where-Object { $_ }
)
$collision = @($secretNames + $variableNames | Where-Object {
    $_ -in $existingSecrets -or $_ -in $existingVariables
})
if ($collision.Count -gt 0) {
    throw "Refusing to overwrite existing release credential names: $($collision -join ', ')"
}

$createdBackup = $false
$createdSecrets = [Collections.Generic.List[string]]::new()
$createdVariables = [Collections.Generic.List[string]]::new()
$credentialTargets = [Collections.Generic.List[string]]::new()
$windowsCertificate = $null
try {
    if (-not (Test-Path -LiteralPath $backupPath)) {
        New-Item -ItemType Directory -Path $backupPath | Out-Null
        $createdBackup = $true
    }
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    [void](Invoke-BoundedProcess -FilePath "icacls.exe" -Arguments @(
        $backupPath, "/inheritance:r", "/grant:r", "${currentUser}:(OI)(CI)F"
    ) -TimeoutSeconds 30)

    $androidStoreSecret = New-RandomSecret
    $androidKeySecret = New-RandomSecret
    $windowsPfxSecret = New-RandomSecret
    $androidAlias = "uthelper-release"
    $androidKeystore = Join-Path $backupPath "uthelper-android-release.jks"
    $androidCertificate = Join-Path $backupPath "uthelper-android-release.cer"
    $keyEnvironment = @{
        UTHELPER_ANDROID_STORE_SECRET = $androidStoreSecret
        UTHELPER_ANDROID_KEY_SECRET = $androidKeySecret
    }
    [void](Invoke-BoundedProcess -FilePath $resolvedKeytool -Arguments @(
        "-genkeypair", "-alias", $androidAlias, "-keyalg", "RSA", "-keysize", "4096",
        "-sigalg", "SHA256withRSA", "-validity", "36500", "-storetype", "JKS",
        "-dname", "CN=UTHelper Android Release, O=UTHelper Open Source",
        "-keystore", $androidKeystore,
        "-storepass:env", "UTHELPER_ANDROID_STORE_SECRET",
        "-keypass:env", "UTHELPER_ANDROID_KEY_SECRET"
    ) -EnvironmentVariables $keyEnvironment -TimeoutSeconds 120)
    [void](Invoke-BoundedProcess -FilePath $resolvedKeytool -Arguments @(
        "-exportcert", "-alias", $androidAlias, "-keystore", $androidKeystore,
        "-storepass:env", "UTHELPER_ANDROID_STORE_SECRET", "-file", $androidCertificate
    ) -EnvironmentVariables $keyEnvironment -TimeoutSeconds 60)
    $androidPublic = [Security.Cryptography.X509Certificates.X509Certificate2]::new($androidCertificate)
    $androidFingerprint = $androidPublic.GetCertHashString(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    ).ToUpperInvariant()
    if ($androidFingerprint -notmatch '^[0-9A-F]{64}$') { throw "Android fingerprint is invalid" }

    $windowsSubject = "CN=UTHelper Open Source Release"
    $windowsCertificate = New-SelfSignedCertificate -Type CodeSigningCert `
        -Subject $windowsSubject -CertStoreLocation "Cert:\CurrentUser\My" `
        -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears(10)
    if ($null -eq $windowsCertificate -or -not $windowsCertificate.HasPrivateKey) {
        throw "Windows signing identity generation failed"
    }
    $windowsPfx = Join-Path $backupPath "uthelper-windows-release.pfx"
    $windowsPublicPath = Join-Path $backupPath "uthelper-windows-release.cer"
    $securePfxSecret = ConvertTo-SecureString $windowsPfxSecret -AsPlainText -Force
    Export-PfxCertificate -Cert $windowsCertificate -FilePath $windowsPfx `
        -Password $securePfxSecret -ChainOption EndEntityCertOnly | Out-Null
    Export-Certificate -Cert $windowsCertificate -FilePath $windowsPublicPath | Out-Null
    $windowsFingerprint = $windowsCertificate.GetCertHashString(
        [Security.Cryptography.HashAlgorithmName]::SHA256
    ).ToUpperInvariant()
    if ($windowsFingerprint -notmatch '^[0-9A-F]{64}$' -or
        $windowsCertificate.Subject -ne $windowsSubject) {
        throw "Windows signing identity is invalid"
    }

    $publicRecord = [ordered]@{
        android_alias = $androidAlias
        android_certificate_sha256 = $androidFingerprint
        windows_subject = $windowsSubject
        windows_certificate_sha256 = $windowsFingerprint
        windows_timestamp_url = "http://timestamp.digicert.com"
    }
    [IO.File]::WriteAllText(
        (Join-Path $backupPath "public-identities.json"),
        (($publicRecord | ConvertTo-Json -Depth 3) + "`n"),
        [Text.UTF8Encoding]::new($false)
    )

    $secretValues = [ordered]@{
        ANDROID_KEYSTORE_BASE64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($androidKeystore))
        ANDROID_KEYSTORE_PASSWORD = $androidStoreSecret
        ANDROID_KEY_PASSWORD = $androidKeySecret
        WINDOWS_PFX_BASE64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($windowsPfx))
        WINDOWS_PFX_PASSWORD = $windowsPfxSecret
    }
    $variableValues = [ordered]@{
        ANDROID_KEY_ALIAS = $androidAlias
        ANDROID_SIGNING_CERT_SHA256 = $androidFingerprint
        WINDOWS_SIGNING_CERT_SHA256 = $windowsFingerprint
        WINDOWS_SIGNER_SUBJECT = $windowsSubject
        WINDOWS_TIMESTAMP_URL = $publicRecord.windows_timestamp_url
    }
    foreach ($entry in $secretValues.GetEnumerator()) {
        Set-GitHubSecret $entry.Key $entry.Value
        $createdSecrets.Add($entry.Key)
    }
    foreach ($entry in $variableValues.GetEnumerator()) {
        Set-GitHubVariable $entry.Key $entry.Value
        $createdVariables.Add($entry.Key)
    }

    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

public static class UTHelperCredentialManager {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct NativeCredential {
        public UInt32 Flags;
        public UInt32 Type;
        public string TargetName;
        public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public UInt32 CredentialBlobSize;
        public IntPtr CredentialBlob;
        public UInt32 Persist;
        public UInt32 AttributeCount;
        public IntPtr Attributes;
        public string TargetAlias;
        public string UserName;
    }

    [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredWrite(ref NativeCredential credential, UInt32 flags);

    [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern bool CredDelete(string target, UInt32 type, UInt32 flags);

    public static void Write(string target, string userName, string secret) {
        byte[] bytes = Encoding.Unicode.GetBytes(secret);
        if (bytes.Length == 0 || bytes.Length > 5120) throw new ArgumentException("Credential secret size is invalid");
        IntPtr blob = Marshal.AllocCoTaskMem(bytes.Length);
        try {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            var credential = new NativeCredential {
                Type = 1,
                TargetName = target,
                CredentialBlobSize = (UInt32)bytes.Length,
                CredentialBlob = blob,
                Persist = 2,
                UserName = userName
            };
            if (!CredWrite(ref credential, 0)) throw new Win32Exception(Marshal.GetLastWin32Error());
        } finally {
            for (int index = 0; index < bytes.Length; index++) Marshal.WriteByte(blob, index, 0);
            Array.Clear(bytes, 0, bytes.Length);
            Marshal.FreeCoTaskMem(blob);
        }
    }

    public static void Delete(string target) {
        if (!CredDelete(target, 1, 0)) {
            int error = Marshal.GetLastWin32Error();
            if (error != 1168) throw new Win32Exception(error);
        }
    }
}
'@
    foreach ($entry in @(
        @{ Target="UTHelper/Release/Android"; UserName="keystore"; Secret="$androidStoreSecret`n$androidKeySecret" },
        @{ Target="UTHelper/Release/Windows"; UserName="pfx"; Secret=$windowsPfxSecret }
    )) {
        [UTHelperCredentialManager]::Write($entry.Target, $entry.UserName, $entry.Secret)
        $credentialTargets.Add($entry.Target)
    }

    Write-Output "Release identities provisioned successfully."
    Write-Output "Recovery directory: $backupPath"
    Write-Output "Android certificate SHA-256: $androidFingerprint"
    Write-Output "Windows certificate SHA-256: $windowsFingerprint"
    Write-Output "Windows subject: $windowsSubject"
} catch {
    foreach ($name in $createdSecrets) {
        try { [void](Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @("secret", "delete", $name, "--repo", $Repository, "--env", $Environment) -TimeoutSeconds 60) } catch {}
    }
    foreach ($name in $createdVariables) {
        try { [void](Invoke-BoundedProcess -FilePath $resolvedGh -Arguments @("variable", "delete", $name, "--repo", $Repository, "--env", $Environment) -TimeoutSeconds 60) } catch {}
    }
    foreach ($target in $credentialTargets) {
        try { [UTHelperCredentialManager]::Delete($target) } catch {}
    }
    if ($createdBackup -and (Test-Path -LiteralPath $backupPath)) {
        Remove-Item -LiteralPath $backupPath -Recurse -Force
    }
    throw
} finally {
    if ($null -ne $windowsCertificate) {
        Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $windowsCertificate.Thumbprint) -Force -ErrorAction SilentlyContinue
    }
}
