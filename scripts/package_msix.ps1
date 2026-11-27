param(
    [Parameter(Mandatory = $true)][string]$BundleDir,
    [string]$Version = "",
    [Parameter(Mandatory = $true)][string]$Publisher,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$IdentityName = "com.uthelper.UTHelper",
    [string]$StoreProductId = "",
    [ValidateSet("msstore", "exe_lts")][string]$Channel = "msstore",
    [ValidateSet("none", "test")][string]$SignMode = "none",
    [string]$DisplayName = "UTHelper",
    [string]$PublisherDisplayName = "UTHelper",
    [string]$SourceIcon = "",
    [string]$CertificatePath = "",
    [string]$CertificatePassword = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

function Test-PathInsideRoot([string]$CandidatePath, [string]$RootPath) {
    $normalizedRoot = $RootPath.TrimEnd('\', '/')
    $rootWithSep = $normalizedRoot + [System.IO.Path]::DirectorySeparatorChar
    $normalizedCandidate = $CandidatePath.TrimEnd('\', '/')
    return [string]::Equals($normalizedCandidate, $normalizedRoot, [StringComparison]::OrdinalIgnoreCase) -or
        $normalizedCandidate.StartsWith($rootWithSep, [StringComparison]::OrdinalIgnoreCase)
}

function Resolve-PythonExecutable([string]$ExplicitPath, [string]$WorkspaceRoot) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($ExplicitPath)) {
        [void]$candidates.Add($ExplicitPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:VIRTUAL_ENV)) {
        [void]$candidates.Add((Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))
    }
    [void]$candidates.Add((Join-Path $WorkspaceRoot ".venv\Scripts\python.exe"))
    $gitPointer = Join-Path $WorkspaceRoot ".git"
    if (Test-Path -LiteralPath $gitPointer -PathType Leaf) {
        $gitLine = (Get-Content -LiteralPath $gitPointer -TotalCount 1 -ErrorAction SilentlyContinue)
        if ($gitLine -match '^\s*gitdir:\s*(.+?)\s*$') {
            $gitDirPath = $Matches[1].Replace('/', '\')
            if (-not [IO.Path]::IsPathRooted($gitDirPath)) {
                $gitDirPath = [IO.Path]::GetFullPath((Join-Path $WorkspaceRoot $gitDirPath))
            }
            $worktreesParent = Split-Path -Path $gitDirPath -Parent
            $dotGitDir = if ($worktreesParent) { Split-Path -Path $worktreesParent -Parent } else { $null }
            $mainRepoRoot = if ($dotGitDir) { Split-Path -Path $dotGitDir -Parent } else { $null }
            if ($mainRepoRoot) {
                [void]$candidates.Add((Join-Path $mainRepoRoot ".venv\Scripts\python.exe"))
            }
        }
    }
    $parentOne = Split-Path -Path $WorkspaceRoot -Parent
    if ($parentOne) {
        $parentTwo = Split-Path -Path $parentOne -Parent
        if ($parentTwo) {
            [void]$candidates.Add((Join-Path $parentTwo ".venv\Scripts\python.exe"))
        }
    }
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -ne $pyCmd -and $pyCmd.Source) {
        [void]$candidates.Add($pyCmd.Source)
    }
    $py3Cmd = Get-Command python3 -ErrorAction SilentlyContinue
    if ($null -ne $py3Cmd -and $py3Cmd.Source) {
        [void]$candidates.Add($py3Cmd.Source)
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        if (-not $seen.Add($candidate)) { continue }
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $candidate -c "import PIL" *>$null
            $probeExit = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $prevEap
        }
        if ($probeExit -eq 0) {
            return $candidate
        }
    }
    throw "Could not find a Python interpreter with Pillow (PIL) installed"
}

$workspaceRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$resolvedBundle = (Resolve-Path -LiteralPath $BundleDir).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($Output)

if (-not (Test-PathInsideRoot $resolvedBundle $workspaceRoot)) {
    throw "BundleDir must be inside the workspace: $resolvedBundle"
}
if (-not (Test-PathInsideRoot $resolvedOutput $workspaceRoot)) {
    throw "Output must be inside the workspace: $resolvedOutput"
}
if ([string]::IsNullOrWhiteSpace($Publisher)) {
    throw "Publisher cannot be empty"
}
if ([string]::IsNullOrWhiteSpace($IdentityName) -or $IdentityName -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]{2,49}$') {
    throw "IdentityName is invalid for MSIX packaging: $IdentityName"
}
if (-not [string]::IsNullOrWhiteSpace($StoreProductId) -and $StoreProductId.Trim() -notmatch '^[A-Za-z0-9]{8,32}$') {
    throw "StoreProductId must be 8-32 alphanumeric characters: $StoreProductId"
}
if ([string]::IsNullOrWhiteSpace($DisplayName)) {
    throw "DisplayName cannot be empty"
}
if ([string]::IsNullOrWhiteSpace($PublisherDisplayName)) {
    throw "PublisherDisplayName cannot be empty"
}

$resolvedPython = Resolve-PythonExecutable $PythonExe $workspaceRoot

$resolvedVersion = $Version.Trim()
if ([string]::IsNullOrWhiteSpace($resolvedVersion)) {
    $metadataScript = Join-Path $PSScriptRoot "release_metadata.py"
    $pyprojectPath = Join-Path $workspaceRoot "pyproject.toml"
    $baseVersion = (& $resolvedPython $metadataScript --pyproject $pyprojectPath --print-version).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($baseVersion)) {
        throw "Failed to derive MSIX version from pyproject.toml"
    }
    $resolvedVersion = "$baseVersion.0"
}
if ($resolvedVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') {
    throw "MSIX Version must contain four numeric components: $resolvedVersion"
}

$resolvedIcon = if ([string]::IsNullOrWhiteSpace($SourceIcon)) {
    Join-Path $workspaceRoot "src\assets\icon.png"
} else {
    (Resolve-Path -LiteralPath $SourceIcon).Path
}
if (-not (Test-Path -LiteralPath $resolvedIcon -PathType Leaf)) {
    throw "Source icon was not found: $resolvedIcon"
}

$xmlIdentityName = [Security.SecurityElement]::Escape($IdentityName.Trim())
$xmlPublisher = [Security.SecurityElement]::Escape($Publisher.Trim())
$xmlDisplayName = [Security.SecurityElement]::Escape($DisplayName.Trim())
$xmlPublisherDisplayName = [Security.SecurityElement]::Escape($PublisherDisplayName.Trim())

$resolvedCertificate = ""
if (-not [string]::IsNullOrWhiteSpace($CertificatePath)) {
    $resolvedCertificate = (Resolve-Path -LiteralPath $CertificatePath).Path
    $flags = [Security.Cryptography.X509Certificates.X509KeyStorageFlags]::EphemeralKeySet
    $certificate = [Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $resolvedCertificate,
        $CertificatePassword,
        $flags
    )
    if (-not $certificate.HasPrivateKey) {
        throw "Windows signing certificate does not contain a private key"
    }
    if (-not [string]::Equals(
        $certificate.Subject,
        $Publisher,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "MSIX Publisher '$Publisher' does not match certificate subject '$($certificate.Subject)'"
    }
}

$exe = Get-ChildItem -LiteralPath $resolvedBundle -Recurse -Filter "UTHelper.exe" |
    Select-Object -First 1
if ($null -eq $exe) {
    throw "UTHelper.exe was not found below $resolvedBundle"
}

$makeAppx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter makeappx.exe |
    Where-Object { $_.FullName -match "\\x64\\makeappx\.exe$" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if ($null -eq $makeAppx) {
    throw "makeappx.exe was not found"
}

$utf8NoBom = [Text.UTF8Encoding]::new($false)
$stageId = [guid]::NewGuid().ToString("N")
$stage = Join-Path $workspaceRoot ("build\msix-stage-" + $stageId)
$verificationStage = Join-Path $workspaceRoot ("build\msix-verify-" + $stageId)

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Copy-Item -Path (Join-Path $exe.Directory.FullName "*") -Destination $stage -Recurse -Force

    $assets = Join-Path $stage "Assets"
    New-Item -ItemType Directory -Path $assets -Force | Out-Null

    $assetGenerator = Join-Path $PSScriptRoot "generate_msix_assets.py"
    if (-not (Test-Path -LiteralPath $assetGenerator -PathType Leaf)) {
        throw "MSIX asset generator script was not found: $assetGenerator"
    }
    & $resolvedPython $assetGenerator --source $resolvedIcon --output-dir $assets
    if ($LASTEXITCODE -ne 0) {
        throw "MSIX visual asset generation failed with exit code $LASTEXITCODE"
    }

    $normalizedProductId = if ([string]::IsNullOrWhiteSpace($StoreProductId)) {
        $null
    } else {
        $StoreProductId.Trim()
    }
    $channelPayload = [ordered]@{
        schema_version     = 1
        channel            = $Channel
        store_product_id   = $normalizedProductId
        android_package_id = "io.github.chouwzi.uthelper"
    }
    $channelJson = ($channelPayload | ConvertTo-Json -Depth 4) + "`n"

    $appAssetsDir = Join-Path $stage "app\assets"
    New-Item -ItemType Directory -Path $appAssetsDir -Force | Out-Null
    [IO.File]::WriteAllText(
        (Join-Path $appAssetsDir "distribution-channel.json"),
        $channelJson,
        $utf8NoBom
    )

    $flutterAssetsRoot = Join-Path $stage "data\flutter_assets"
    if (Test-Path -LiteralPath $flutterAssetsRoot -PathType Container) {
        $flutterAssetsDir = Join-Path $flutterAssetsRoot "assets"
        New-Item -ItemType Directory -Path $flutterAssetsDir -Force | Out-Null
        [IO.File]::WriteAllText(
            (Join-Path $flutterAssetsDir "distribution-channel.json"),
            $channelJson,
            $utf8NoBom
        )
    }

    $existingChannelFiles = @(
        Get-ChildItem -LiteralPath $stage -Recurse -File -Filter "distribution-channel.json"
    )
    foreach ($existingFile in $existingChannelFiles) {
        [IO.File]::WriteAllText($existingFile.FullName, $channelJson, $utf8NoBom)
    }

    $manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap desktop rescap">
  <Identity Name="$xmlIdentityName" Publisher="$xmlPublisher" Version="$resolvedVersion" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>$xmlDisplayName</DisplayName>
    <PublisherDisplayName>$xmlPublisherDisplayName</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Resources>
    <Resource Language="en-us" />
    <Resource Language="vi-vn" />
  </Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="UTHelper" Executable="UTHelper.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="$xmlDisplayName" Description="UTH activity and deadline assistant"
                          BackgroundColor="transparent"
                          Square44x44Logo="Assets\Square44x44Logo.png"
                          Square150x150Logo="Assets\Square150x150Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\Wide310x150Logo.png" />
      </uap:VisualElements>
      <Extensions>
        <desktop:Extension Category="windows.startupTask"
                           Executable="UTHelperAutostart.exe"
                           EntryPoint="Windows.FullTrustApplication">
          <desktop:StartupTask TaskId="UTHelperStartup"
                               Enabled="false"
                               DisplayName="$xmlDisplayName" />
        </desktop:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
  </Capabilities>
</Package>
"@
    [IO.File]::WriteAllText((Join-Path $stage "AppxManifest.xml"), ($manifest + "`n"), $utf8NoBom)

    New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($resolvedOutput)) -Force | Out-Null
    & $makeAppx.FullName pack /d $stage /p $resolvedOutput /o
    if ($LASTEXITCODE -ne 0) {
        throw "makeappx failed with exit code $LASTEXITCODE"
    }

    & $makeAppx.FullName unpack /p $resolvedOutput /d $verificationStage /o
    if ($LASTEXITCODE -ne 0) {
        throw "MSIX package verification failed: unpack exited with $LASTEXITCODE"
    }
    foreach ($requiredFile in @(
        "AppxManifest.xml",
        "UTHelper.exe",
        "UTHelperAutostart.exe",
        "Assets\StoreLogo.png",
        "Assets\Square44x44Logo.png",
        "Assets\Square150x150Logo.png",
        "Assets\Wide310x150Logo.png",
        "app\assets\distribution-channel.json"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $verificationStage $requiredFile) -PathType Leaf)) {
            throw "MSIX package verification failed: $requiredFile is missing"
        }
    }
}
finally {
    if (Test-Path -LiteralPath $verificationStage) {
        Remove-Item -LiteralPath $verificationStage -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$shouldSignWithCert = -not [string]::IsNullOrWhiteSpace($resolvedCertificate)
$shouldSignTest = ($SignMode -eq "test") -and (-not $shouldSignWithCert)

if ($shouldSignWithCert -or $shouldSignTest) {
    $signTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
        Where-Object { $_.FullName -match "\\x64\\signtool\.exe$" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $signTool) {
        throw "signtool.exe was not found"
    }

    if ($shouldSignWithCert) {
        & $signTool.FullName sign /fd SHA256 /tr $TimestampUrl /td SHA256 `
            /f $resolvedCertificate /p $CertificatePassword $resolvedOutput
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed with exit code $LASTEXITCODE"
        }
        & $signTool.FullName verify /pa /v $resolvedOutput
        if ($LASTEXITCODE -ne 0) {
            throw "signtool verification failed with exit code $LASTEXITCODE"
        }
    }
    else {
        $ephemeralCert = New-SelfSignedCertificate `
            -Type Custom `
            -Subject $Publisher `
            -KeyUsage DigitalSignature `
            -FriendlyName "UTHelper MSIX Local Test Signing" `
            -CertStoreLocation "Cert:\CurrentUser\My" `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
        try {
            & $signTool.FullName sign /fd SHA256 /sha1 $ephemeralCert.Thumbprint $resolvedOutput
            if ($LASTEXITCODE -ne 0) {
                throw "signtool failed with exit code $LASTEXITCODE"
            }
            $cerOutput = $resolvedOutput + ".test-cert.cer"
            Export-Certificate -Cert $ephemeralCert -FilePath $cerOutput -Force | Out-Null
            Write-Host "[UTHelper] MSIX test-signed (self-signed public cert exported to $cerOutput)"
        }
        finally {
            Remove-Item -LiteralPath ("Cert:\CurrentUser\My\" + $ephemeralCert.Thumbprint) -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "[UTHelper] MSIX created at $resolvedOutput (channel=$Channel, sign_mode=$SignMode)"
