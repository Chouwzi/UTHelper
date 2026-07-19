param(
    [Parameter(Mandatory = $true)][string]$BundleDir,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Publisher,
    [Parameter(Mandatory = $true)][string]$Output,
    [string]$CertificatePath = "",
    [string]$CertificatePassword = ""
)

$ErrorActionPreference = "Stop"
$resolvedBundle = (Resolve-Path -LiteralPath $BundleDir).Path
$resolvedOutput = [System.IO.Path]::GetFullPath($Output)
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $resolvedBundle.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BundleDir must be inside the workspace: $resolvedBundle"
}

$exe = Get-ChildItem -LiteralPath $resolvedBundle -Recurse -Filter "UTHelper.exe" |
    Select-Object -First 1
if ($null -eq $exe) {
    throw "UTHelper.exe was not found below $resolvedBundle"
}

$stage = Join-Path $workspaceRoot "build\msix-stage"
if (Test-Path -LiteralPath $stage) {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Path $stage -Force | Out-Null
Copy-Item -Path (Join-Path $exe.Directory.FullName "*") -Destination $stage -Recurse -Force

$assets = Join-Path $stage "Assets"
New-Item -ItemType Directory -Path $assets -Force | Out-Null
$icon = Join-Path $workspaceRoot "src\assets\icon.png"
Copy-Item -LiteralPath $icon -Destination (Join-Path $assets "StoreLogo.png") -Force
Copy-Item -LiteralPath $icon -Destination (Join-Path $assets "Square44x44Logo.png") -Force
Copy-Item -LiteralPath $icon -Destination (Join-Path $assets "Square150x150Logo.png") -Force

$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
         xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
         IgnorableNamespaces="uap rescap">
  <Identity Name="com.uthelper.UTHelper" Publisher="$Publisher" Version="$Version" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>UTHelper</DisplayName>
    <PublisherDisplayName>UTHelper</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Resources><Resource Language="vi-vn" /></Resources>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Applications>
    <Application Id="UTHelper" Executable="UTHelper.exe" EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements DisplayName="UTHelper" Description="UTH activity and deadline assistant"
                          BackgroundColor="transparent"
                          Square44x44Logo="Assets\Square44x44Logo.png"
                          Square150x150Logo="Assets\Square150x150Logo.png" />
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
  </Capabilities>
</Package>
"@
Set-Content -LiteralPath (Join-Path $stage "AppxManifest.xml") -Value $manifest -Encoding UTF8

$makeAppx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter makeappx.exe |
    Where-Object { $_.FullName -match "\\x64\\makeappx.exe$" } |
    Sort-Object FullName -Descending |
    Select-Object -First 1
if ($null -eq $makeAppx) { throw "makeappx.exe was not found" }

New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($resolvedOutput)) -Force | Out-Null
& $makeAppx.FullName pack /d $stage /p $resolvedOutput /o
if ($LASTEXITCODE -ne 0) { throw "makeappx failed with exit code $LASTEXITCODE" }

if ($CertificatePath) {
    $signTool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe |
        Where-Object { $_.FullName -match "\\x64\\signtool.exe$" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $signTool) { throw "signtool.exe was not found" }
    & $signTool.FullName sign /fd SHA256 /f $CertificatePath /p $CertificatePassword $resolvedOutput
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
}

Write-Host "[UTHelper] MSIX created at $resolvedOutput"

