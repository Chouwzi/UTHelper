param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$Publisher,
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$Output
)

$version3 = $Version.TrimStart("v")
$repositoryParts = $Repository.Split("/")
if ($version3 -notmatch '^\d+\.\d+\.\d+$') {
    throw "AppInstaller Version must contain three numeric components: $Version"
}
if ($repositoryParts.Count -ne 2 -or $repositoryParts[0] -notmatch '^[A-Za-z0-9_.-]+$' -or $repositoryParts[1] -notmatch '^[A-Za-z0-9_.-]+$') {
    throw "Repository must use the owner/name form: $Repository"
}
if ([string]::IsNullOrWhiteSpace($Publisher)) {
    throw "Publisher cannot be empty"
}
$version4 = if (($version3.Split(".")).Count -eq 3) { "$version3.0" } else { $version3 }
$tag = "v$version3"
$msixName = "UTHelper-$version3-x64.msix"
$pagesOwner = $repositoryParts[0].ToLowerInvariant()
$pagesRepository = $repositoryParts[1]
$xmlPublisher = [Security.SecurityElement]::Escape($Publisher)
$appInstallerUri = "https://$pagesOwner.github.io/$pagesRepository/UTHelper.appinstaller"
$packageUri = "https://github.com/$Repository/releases/download/$tag/$msixName"

$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<AppInstaller xmlns="http://schemas.microsoft.com/appx/appinstaller/2021"
              Version="$version4" Uri="$appInstallerUri">
  <MainPackage Name="com.uthelper.UTHelper" Publisher="$xmlPublisher"
               Version="$version4" ProcessorArchitecture="x64" Uri="$packageUri" />
  <UpdateSettings>
    <OnLaunch HoursBetweenUpdateChecks="12" ShowPrompt="true" />
    <AutomaticBackgroundTask />
    <ForceUpdateFromAnyVersion>false</ForceUpdateFromAnyVersion>
  </UpdateSettings>
</AppInstaller>
"@
Set-Content -LiteralPath $Output -Value $xml -Encoding UTF8

try {
    [xml](Get-Content -Raw -LiteralPath $Output) | Out-Null
} catch {
    Remove-Item -LiteralPath $Output -Force -ErrorAction SilentlyContinue
    throw "Generated AppInstaller XML is invalid: $($_.Exception.Message)"
}
