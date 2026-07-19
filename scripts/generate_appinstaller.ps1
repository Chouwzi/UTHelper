param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Publisher,
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Output
)

$version3 = $Version.TrimStart("v")
$version4 = if (($version3.Split(".")).Count -eq 3) { "$version3.0" } else { $version3 }
$tag = "v$version3"
$msixName = "UTHelper-$version3-x64.msix"
$pagesOwner = $Repository.Split("/")[0].ToLowerInvariant()
$appInstallerUri = "https://$pagesOwner.github.io/UTHelper/UTHelper.appinstaller"
$packageUri = "https://github.com/$Repository/releases/download/$tag/$msixName"

$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller xmlns="http://schemas.microsoft.com/appx/appinstaller/2021"
              Version="$version4" Uri="$appInstallerUri">
  <MainPackage Name="com.uthelper.UTHelper" Publisher="$Publisher"
               Version="$version4" ProcessorArchitecture="x64" Uri="$packageUri" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="12" ShowPrompt="true" />
    <AutomaticBackgroundTask />
    <ForceUpdateFromAnyVersion>false</ForceUpdateFromAnyVersion>
  </UpdateSettings>
</AppInstaller>
"@
Set-Content -LiteralPath $Output -Value $xml -Encoding UTF8
