param(
    [Parameter(Mandatory = $true)][string]$MsixPath,
    [string]$Channel = "msstore",
    [string]$IdentityName = "",
    [string]$Publisher = "",
    [string]$DisplayName = "",
    [string]$PublisherDisplayName = "",
    [string]$Version = "",
    [string]$StoreProductId = "",
    [string]$Output = "",
    [switch]$RunWack,
    [string]$WackReportPath = "",
    [int]$WackTimeoutSeconds = 900,
    [switch]$RequireOfficeViewer,
    [switch]$RequireDiagnosticsConfig
)

$ErrorActionPreference = "Stop"

function Test-PeHeader([string]$FilePath) {
    $stream = [IO.File]::OpenRead($FilePath)
    try {
        if ($stream.Length -lt 2) { return $false }
        $header = New-Object byte[] 2
        if ($stream.Read($header, 0, 2) -ne 2) { return $false }
        return ($header[0] -eq 0x4D -and $header[1] -eq 0x5A)
    }
    finally {
        $stream.Dispose()
    }
}

function Get-FileSha256Hex([string]$FilePath) {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($FilePath)
    try {
        $hashBytes = $sha256.ComputeHash($stream)
        return ([BitConverter]::ToString($hashBytes) -replace '-', '').ToLowerInvariant()
    }
    finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
}

function Read-PngDimensions([string]$FilePath) {
    $stream = [IO.File]::OpenRead($FilePath)
    try {
        if ($stream.Length -lt 24) {
            throw "PNG file is truncated: $FilePath"
        }
        $header = New-Object byte[] 24
        if ($stream.Read($header, 0, 24) -ne 24) {
            throw "Could not read PNG header: $FilePath"
        }
        $expectedMagic = @(0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
        for ($i = 0; $i -lt 8; $i++) {
            if ($header[$i] -ne $expectedMagic[$i]) {
                throw "File is not a valid PNG image: $FilePath"
            }
        }
        if ($header[12] -ne 0x49 -or $header[13] -ne 0x48 -or $header[14] -ne 0x44 -or $header[15] -ne 0x52) {
            throw "PNG missing IHDR chunk: $FilePath"
        }
        $width = ([uint32]$header[16] -shl 24) -bor ([uint32]$header[17] -shl 16) -bor ([uint32]$header[18] -shl 8) -bor [uint32]$header[19]
        $height = ([uint32]$header[20] -shl 24) -bor ([uint32]$header[21] -shl 16) -bor ([uint32]$header[22] -shl 8) -bor [uint32]$header[23]
        return @{ Width = [int]$width; Height = [int]$height }
    }
    finally {
        $stream.Dispose()
    }
}

function Normalize-RelativeAssetPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    return $Value.Trim().Replace("/", "\")
}

function Resolve-MakeAppx {
    $kitsRoot = "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    if (-not (Test-Path -LiteralPath $kitsRoot -PathType Container)) {
        throw "Windows SDK bin directory not found: $kitsRoot"
    }
    $tool = Get-ChildItem -LiteralPath $kitsRoot -Recurse -Filter makeappx.exe |
        Where-Object { $_.FullName -match "\\x64\\makeappx\.exe$" } |
        Sort-Object FullName -Descending |
        Select-Object -First 1
    if ($null -eq $tool) {
        throw "makeappx.exe was not found under $kitsRoot"
    }
    return $tool.FullName
}

function Resolve-AppCert {
    $cmd = Get-Command appcert.exe -ErrorAction SilentlyContinue
    if ($null -ne $cmd -and $cmd.Source) {
        return $cmd.Source
    }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\App Certification Kit\appcert.exe",
        "${env:ProgramFiles}\Windows Kits\10\App Certification Kit\appcert.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    throw "Windows App Certification Kit (appcert.exe) was not found. Install the Windows SDK App Certification Kit before passing -RunWack."
}

function Format-WindowsArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Test-DisallowedPublicIpAddress([System.Net.IPAddress]$IpAddress) {
    $targetIps = @($IpAddress)
    if ($IpAddress.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6 -and $IpAddress.IsIPv4MappedToIPv6) {
        $targetIps += $IpAddress.MapToIPv4()
    }
    foreach ($candidate in $targetIps) {
        if ([System.Net.IPAddress]::IsLoopback($candidate)) {
            return $true
        }
        $bytes = $candidate.GetAddressBytes()
        if ($candidate.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
            $b0 = [int]$bytes[0]
            $b1 = [int]$bytes[1]
            $b2 = [int]$bytes[2]
            # 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16,
            # 172.16.0.0/12, 192.0.0.0/24, 192.0.2.0/24, 192.168.0.0/16,
            # 198.18.0.0/15, 198.51.100.0/24, 203.0.113.0/24, 224.0.0.0/4, 240.0.0.0/4
            if ($b0 -eq 0 -or $b0 -eq 10 -or $b0 -eq 127 -or $b0 -ge 224) { return $true }
            if ($b0 -eq 100 -and $b1 -ge 64 -and $b1 -le 127) { return $true }
            if ($b0 -eq 169 -and $b1 -eq 254) { return $true }
            if ($b0 -eq 172 -and $b1 -ge 16 -and $b1 -le 31) { return $true }
            if ($b0 -eq 192 -and $b1 -eq 0 -and ($b2 -eq 0 -or $b2 -eq 2)) { return $true }
            if ($b0 -eq 192 -and $b1 -eq 168) { return $true }
            if ($b0 -eq 198 -and ($b1 -eq 18 -or $b1 -eq 19)) { return $true }
            if ($b0 -eq 198 -and $b1 -eq 51 -and $b2 -eq 100) { return $true }
            if ($b0 -eq 203 -and $b1 -eq 0 -and $b2 -eq 113) { return $true }
        }
        elseif ($candidate.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
            if ($candidate.Equals([System.Net.IPAddress]::IPv6None) -or
                $candidate.IsIPv6LinkLocal -or
                $candidate.IsIPv6SiteLocal -or
                $candidate.IsIPv6Multicast) {
                return $true
            }
            $firstByte = [int]$bytes[0]
            $secondByte = [int]$bytes[1]
            # fc00::/7 Unique Local, 2001:db8::/32 documentation
            if (($firstByte -band 0xFE) -eq 0xFC) { return $true }
            if ($firstByte -eq 0x20 -and $secondByte -eq 0x01 -and [int]$bytes[2] -eq 0x0D -and [int]$bytes[3] -eq 0xB8) {
                return $true
            }
        }
    }
    return $false
}

function Assert-ValidSentryDsn([string]$DsnValue) {
    if ($DsnValue.Length -gt 2048) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn exceeds length limit"
    }
    if ($DsnValue -cne $DsnValue.Trim()) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn contains leading/trailing whitespace"
    }
    foreach ($ch in $DsnValue.ToCharArray()) {
        if ([int]$ch -lt 0x21) {
            throw "MSIX verification failed: diagnostics-config.json sentry_dsn contains whitespace or control characters"
        }
    }
    $decodedLower = ([Uri]::UnescapeDataString($DsnValue)).ToLowerInvariant()
    foreach ($marker in @("auth_token", "authtoken", "management_token", "management-token", "sentry_auth_token", "sntrys_")) {
        if ($decodedLower.Contains($marker)) {
            throw "MSIX verification failed: diagnostics-config.json sentry_dsn contains forbidden token marker '$marker'"
        }
    }
    if ($DsnValue.Contains("?") -or $DsnValue.Contains("#")) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn must not contain query or fragment"
    }
    $uri = $null
    if (-not [Uri]::TryCreate($DsnValue, [UriKind]::Absolute, [ref]$uri)) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn is not a valid URL"
    }
    if ($uri.Scheme -cne "https") {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn must use https"
    }
    if ([string]::IsNullOrEmpty($uri.UserInfo) -or $uri.UserInfo.Contains(":")) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn must carry only a public key without password"
    }
    if ($uri.UserInfo -cnotmatch '^[A-Za-z0-9_-]{8,128}$') {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn public key is invalid"
    }
    $rawAfterScheme = $DsnValue.Substring(8)
    $slashIdx = $rawAfterScheme.IndexOf("/")
    $rawNetloc = if ($slashIdx -ge 0) { $rawAfterScheme.Substring(0, $slashIdx) } else { $rawAfterScheme }
    $atIdx = $rawNetloc.LastIndexOf("@")
    $rawHostPort = if ($atIdx -ge 0) { $rawNetloc.Substring($atIdx + 1) } else { $rawNetloc }
    if ([string]::IsNullOrEmpty($uri.Host) -or $rawHostPort -cne $rawHostPort.ToLowerInvariant()) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn host must be canonical lowercase"
    }
    $pathParts = @(([Uri]::UnescapeDataString($uri.AbsolutePath)).Split("/") | Where-Object { $_ -ne "" })
    if ($pathParts.Count -eq 0 -or $pathParts[$pathParts.Count - 1] -cnotmatch '^[0-9]{1,20}$') {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn must end in a numeric project id"
    }
    foreach ($part in $pathParts) {
        if ($part -eq "." -or $part -eq "..") {
            throw "MSIX verification failed: diagnostics-config.json sentry_dsn path contains dot segments"
        }
    }
}

function Assert-ValidPosthogKey([string]$KeyValue) {
    if ($KeyValue -cne $KeyValue.Trim()) {
        throw "MSIX verification failed: diagnostics-config.json posthog_project_api_key contains whitespace"
    }
    foreach ($ch in $KeyValue.ToCharArray()) {
        $code = [int]$ch
        if ($code -lt 0x21 -or $code -gt 0x7E) {
            throw "MSIX verification failed: diagnostics-config.json posthog_project_api_key contains invalid characters"
        }
    }
    $lower = $KeyValue.ToLowerInvariant()
    $decoded = ([Uri]::UnescapeDataString($KeyValue)).ToLowerInvariant()
    foreach ($marker in @(
        "phx_", "phs_", "sntrys_", "secret", "auth_token", "authtoken",
        "personal", "management_token", "management-token", "sentry_auth_token", "bearer"
    )) {
        if ($lower.Contains($marker) -or $decoded.Contains($marker)) {
            throw "MSIX verification failed: diagnostics-config.json posthog_project_api_key contains forbidden secret/token marker '$marker'"
        }
    }
    if ($KeyValue -cnotmatch '^phc_[A-Za-z0-9_-]{20,128}$') {
        throw "MSIX verification failed: diagnostics-config.json posthog_project_api_key must match phc_<20..128 chars>"
    }
}

function Assert-ValidPosthogHost([string]$HostValue) {
    if ($HostValue.Length -gt 512) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host exceeds length limit"
    }
    if ($HostValue -cne $HostValue.Trim()) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host contains whitespace"
    }
    foreach ($ch in $HostValue.ToCharArray()) {
        $code = [int]$ch
        if ($code -lt 0x21 -or $code -gt 0x7E) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host contains invalid characters"
        }
    }
    if ($HostValue.Contains("\") -or $HostValue.Contains("?") -or $HostValue.Contains("#") -or $HostValue.Contains("@")) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host must not contain credentials, query, fragment, or backslashes"
    }
    $decodedFull = ([Uri]::UnescapeDataString($HostValue)).ToLowerInvariant()
    foreach ($marker in @("phx_", "phs_", "sntrys_", "auth_token", "authtoken", "management_token", "management-token", "sentry_auth_token")) {
        if ($decodedFull.Contains($marker)) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host contains forbidden token marker"
        }
    }
    if (-not $HostValue.StartsWith("https://", [StringComparison]::Ordinal)) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host must start with https://"
    }
    $afterScheme = $HostValue.Substring(8)
    $slashIdx = $afterScheme.IndexOf("/")
    $rawNetloc = if ($slashIdx -ge 0) { $afterScheme.Substring(0, $slashIdx) } else { $afterScheme }
    $rawPath = if ($slashIdx -ge 0) { $afterScheme.Substring($slashIdx) } else { "" }
    if ([string]::IsNullOrEmpty($rawNetloc) -or $rawNetloc -cne $rawNetloc.ToLowerInvariant()) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host authority must be canonical lowercase"
    }
    $uri = $null
    if (-not [Uri]::TryCreate($HostValue, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -cne "https" -or -not [string]::IsNullOrEmpty($uri.UserInfo)) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host is not a valid HTTPS URL"
    }
    $hostName = $uri.DnsSafeHost
    if ([string]::IsNullOrEmpty($hostName) -or $hostName.Contains("%") -or $hostName.StartsWith(".") -or $hostName.EndsWith(".") -or $hostName.Contains("..")) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host hostname is invalid"
    }
    if (@("app.posthog.com", "us.posthog.com", "eu.posthog.com") -contains $hostName) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host must not target PostHog web UI host '$hostName'"
    }
    $ipObj = $null
    $isLiteralIp = ($rawNetloc.StartsWith("[") -or ($hostName -cmatch '^[0-9.]+$')) -and [System.Net.IPAddress]::TryParse($hostName, [ref]$ipObj)
    if ($isLiteralIp) {
        if (Test-DisallowedPublicIpAddress $ipObj) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host must not use loopback, private, or link-local IP '$hostName'"
        }
    }
    else {
        $tld = ($hostName -split '\.')[-1]
        if ($hostName -cnotmatch '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$' -or ($tld -cmatch '^[0-9]+$')) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host domain '$hostName' is invalid"
        }
        if ($hostName -eq "localhost" -or $hostName.EndsWith(".localhost") -or $hostName.EndsWith(".local")) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host must not use local host '$hostName'"
        }
    }
    if ($rawPath.Contains(";") -or $rawPath.Contains("//")) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host path is invalid"
    }
    if ([Uri]::UnescapeDataString($rawPath) -cne $rawPath) {
        throw "MSIX verification failed: diagnostics-config.json posthog_host path must not use percent-encoding"
    }
    $segments = @($rawPath.Split("/") | Where-Object { $_ -ne "" })
    foreach ($seg in $segments) {
        if ($seg -eq "." -or $seg -eq ".." -or $seg.Contains("..") -or ($seg -cnotmatch '^[A-Za-z0-9._-]+$')) {
            throw "MSIX verification failed: diagnostics-config.json posthog_host path segment '$seg' is invalid"
        }
    }
}

function Assert-ValidDiagnosticsConfigFile([IO.FileInfo]$ConfigFile) {
    if (($ConfigFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "MSIX verification failed: diagnostics-config.json must not be a reparse point: $($ConfigFile.FullName)"
    }
    if ($ConfigFile.Length -le 0 -or $ConfigFile.Length -gt 4096) {
        throw "MSIX verification failed: diagnostics-config.json size ($($ConfigFile.Length) bytes) is invalid: $($ConfigFile.FullName)"
    }
    $rawBytes = [IO.File]::ReadAllBytes($ConfigFile.FullName)
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try {
        $rawText = $strictUtf8.GetString($rawBytes)
    }
    catch {
        throw "MSIX verification failed: diagnostics-config.json is not valid UTF-8: $($ConfigFile.FullName)"
    }
    if (-not $rawText.Trim().StartsWith("{")) {
        throw "MSIX verification failed: diagnostics-config.json must be a JSON object"
    }
    try {
        $parsed = $rawText | ConvertFrom-Json
    }
    catch {
        throw "MSIX verification failed: diagnostics-config.json is not valid JSON: $($ConfigFile.FullName)"
    }
    if ($null -eq $parsed -or $parsed -is [System.Array] -or $parsed -is [string] -or $parsed -is [ValueType]) {
        throw "MSIX verification failed: diagnostics-config.json must be a JSON object"
    }
    $propMap = @{}
    foreach ($prop in $parsed.PSObject.Properties) {
        $propMap[$prop.Name] = $prop.Value
    }
    if (-not $propMap.ContainsKey("schema_version")) {
        throw "MSIX verification failed: diagnostics-config.json missing schema_version"
    }
    $schemaVer = $propMap["schema_version"]
    if ($schemaVer -is [bool] -or ($schemaVer -isnot [int] -and $schemaVer -isnot [long])) {
        throw "MSIX verification failed: diagnostics-config.json schema_version must be an integer"
    }
    $validBuildEnvs = @("release", "develop", "development")
    $validPkgFormats = @("aab", "apk", "msix", "exe_msi", "exe", "msi", "ipa", "ios_app", "source")

    if ($schemaVer -eq 1) {
        $expectedKeys = @("schema_version", "sentry_dsn")
    }
    elseif ($schemaVer -eq 2) {
        $expectedKeys = @("schema_version", "sentry_dsn", "build_environment", "package_format")
    }
    elseif ($schemaVer -eq 3) {
        $expectedKeys = @(
            "schema_version", "sentry_dsn", "build_environment",
            "package_format", "posthog_project_api_key", "posthog_host"
        )
    }
    else {
        throw "MSIX verification failed: unsupported schema_version '$schemaVer' in diagnostics-config.json"
    }

    if ($propMap.Count -ne $expectedKeys.Count) {
        throw "MSIX verification failed: unexpected or missing keys in diagnostics-config.json (schema_version=$schemaVer)"
    }
    foreach ($reqKey in $expectedKeys) {
        if (-not $propMap.ContainsKey($reqKey)) {
            throw "MSIX verification failed: missing required key '$reqKey' in diagnostics-config.json"
        }
    }

    $sentryDsn = $propMap["sentry_dsn"]
    if ($sentryDsn -isnot [string]) {
        throw "MSIX verification failed: diagnostics-config.json sentry_dsn must be a string"
    }
    if ($sentryDsn -ne "") {
        Assert-ValidSentryDsn $sentryDsn
    }

    if ($schemaVer -ge 2) {
        $buildEnv = $propMap["build_environment"]
        $pkgFmt = $propMap["package_format"]
        if ($buildEnv -isnot [string] -or ($validBuildEnvs -notcontains $buildEnv)) {
            throw "MSIX verification failed: unsupported build_environment '$buildEnv' in diagnostics-config.json"
        }
        if ($pkgFmt -isnot [string] -or ($validPkgFormats -notcontains $pkgFmt)) {
            throw "MSIX verification failed: unsupported package_format '$pkgFmt' in diagnostics-config.json"
        }
    }

    if ($schemaVer -eq 3) {
        $phKey = $propMap["posthog_project_api_key"]
        $phHost = $propMap["posthog_host"]
        if ($phKey -isnot [string] -or $phHost -isnot [string]) {
            throw "MSIX verification failed: PostHog fields in diagnostics-config.json must be strings"
        }
        if ($phKey -ne "") {
            Assert-ValidPosthogKey $phKey
        }
        if ($phHost -ne "") {
            Assert-ValidPosthogHost $phHost
        }
    }
}

$resolvedInput = (Resolve-Path -LiteralPath $MsixPath).Path
$isDirectory = Test-Path -LiteralPath $resolvedInput -PathType Container
$isFile = Test-Path -LiteralPath $resolvedInput -PathType Leaf

if (-not $isDirectory -and -not $isFile) {
    throw "MsixPath does not exist: $MsixPath"
}
if ($RunWack -and $isDirectory) {
    throw "-RunWack requires a packaged .msix file, not a directory"
}

$packageSha256 = $null
$unpackDir = $null
$inspectRoot = $resolvedInput

try {
    if ($isFile) {
        $packageSha256 = Get-FileSha256Hex $resolvedInput
        $makeAppxPath = Resolve-MakeAppx
        $tempBase = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
        $unpackDir = Join-Path $tempBase ("msix-verify-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $unpackDir -Force | Out-Null

        & $makeAppxPath unpack /p $resolvedInput /d $unpackDir /o
        if ($LASTEXITCODE -ne 0) {
            throw "MSIX verification failed: makeappx unpack exited with $LASTEXITCODE"
        }
        $inspectRoot = $unpackDir
    }

    $manifestPath = Join-Path $inspectRoot "AppxManifest.xml"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "MSIX verification failed: AppxManifest.xml is missing"
    }
    $manifestRaw = [IO.File]::ReadAllText($manifestPath, [Text.UTF8Encoding]::new($false))

    if ($manifestRaw -match '(?i)uap10:Parameters') {
        throw "MSIX verification failed: forbidden uap10:Parameters detected in AppxManifest.xml"
    }
    if ($manifestRaw -match '(?i)unvirtualizedResources') {
        throw "MSIX verification failed: forbidden unvirtualizedResources capability detected in AppxManifest.xml"
    }
    if ($manifestRaw -match '(?i)FileSystemWriteVirtualization\s*=\s*"disabled"' -or
        $manifestRaw -match '(?i)RegistryWriteVirtualization\s*=\s*"disabled"' -or
        $manifestRaw -match '(?i)WriteVirtualization\s*>\s*disabled\s*<') {
        throw "MSIX verification failed: AppData/Registry virtualization must remain enabled"
    }

    [xml]$manifestDoc = $manifestRaw
    $nsMgr = New-Object System.Xml.XmlNamespaceManager($manifestDoc.NameTable)
    $nsMgr.AddNamespace("f", "http://schemas.microsoft.com/appx/manifest/foundation/windows10")
    $nsMgr.AddNamespace("uap", "http://schemas.microsoft.com/appx/manifest/uap/windows10")
    $nsMgr.AddNamespace("desktop", "http://schemas.microsoft.com/appx/manifest/desktop/windows10")
    $nsMgr.AddNamespace("rescap", "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities")

    $identityNode = $manifestDoc.SelectSingleNode("/f:Package/f:Identity", $nsMgr)
    if ($null -eq $identityNode) {
        throw "MSIX verification failed: Package/Identity element is missing"
    }
    $actualName = $identityNode.GetAttribute("Name")
    $actualPublisher = $identityNode.GetAttribute("Publisher")
    $actualVersion = $identityNode.GetAttribute("Version")
    $actualArch = $identityNode.GetAttribute("ProcessorArchitecture")

    if ([string]::IsNullOrWhiteSpace($actualName)) {
        throw "MSIX verification failed: Identity/@Name is empty"
    }
    if (-not [string]::IsNullOrWhiteSpace($IdentityName) -and $actualName -ne $IdentityName) {
        throw "MSIX verification failed: Identity/@Name '$actualName' does not match expected '$IdentityName'"
    }
    if ([string]::IsNullOrWhiteSpace($actualPublisher)) {
        throw "MSIX verification failed: Identity/@Publisher is empty"
    }
    if (-not [string]::IsNullOrWhiteSpace($Publisher) -and $actualPublisher -ne $Publisher) {
        throw "MSIX verification failed: Identity/@Publisher '$actualPublisher' does not match expected '$Publisher'"
    }
    if ($actualVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') {
        throw "MSIX verification failed: Identity/@Version must be 4-part numeric version, got '$actualVersion'"
    }
    if (-not [string]::IsNullOrWhiteSpace($Version) -and $actualVersion -ne $Version) {
        throw "MSIX verification failed: Identity/@Version '$actualVersion' does not match expected '$Version'"
    }
    if ($actualArch -ne "x64") {
        throw "MSIX verification failed: Identity/@ProcessorArchitecture must be 'x64', got '$actualArch'"
    }

    $runFullTrustNode = $manifestDoc.SelectSingleNode("//rescap:Capability[@Name='runFullTrust']", $nsMgr)
    if ($null -eq $runFullTrustNode) {
        throw "MSIX verification failed: required rescap:Capability Name='runFullTrust' is missing"
    }
    $internetClientNode = $manifestDoc.SelectSingleNode("//f:Capability[@Name='internetClient']", $nsMgr)
    if ($null -eq $internetClientNode) {
        throw "MSIX verification failed: required Capability Name='internetClient' is missing"
    }
    $unvirtualizedNode = $manifestDoc.SelectSingleNode("//*[local-name()='Capability' and @Name='unvirtualizedResources']")
    if ($null -ne $unvirtualizedNode) {
        throw "MSIX verification failed: unvirtualizedResources capability is forbidden"
    }

    $appNode = $manifestDoc.SelectSingleNode("/f:Package/f:Applications/f:Application[@Executable='UTHelper.exe']", $nsMgr)
    if ($null -eq $appNode) {
        throw "MSIX verification failed: Application Executable='UTHelper.exe' is missing"
    }
    if ($appNode.GetAttribute("EntryPoint") -ne "Windows.FullTrustApplication") {
        throw "MSIX verification failed: Application EntryPoint must be Windows.FullTrustApplication"
    }

    $startupExt = $appNode.SelectSingleNode(".//desktop:Extension[@Category='windows.startupTask']", $nsMgr)
    if ($null -eq $startupExt) {
        throw "MSIX verification failed: windows.startupTask extension is missing"
    }
    if ($startupExt.GetAttribute("Executable") -ne "UTHelperAutostart.exe") {
        throw "MSIX verification failed: startupTask Executable must be UTHelperAutostart.exe"
    }
    if ($startupExt.GetAttribute("EntryPoint") -ne "Windows.FullTrustApplication") {
        throw "MSIX verification failed: startupTask EntryPoint must be Windows.FullTrustApplication"
    }
    foreach ($attr in $startupExt.Attributes) {
        if ($attr.LocalName -eq "Parameters") {
            throw "MSIX verification failed: startupTask extension must not declare Parameters"
        }
    }
    $startupTask = $startupExt.SelectSingleNode("desktop:StartupTask", $nsMgr)
    if ($null -eq $startupTask -or $startupTask.GetAttribute("TaskId") -ne "UTHelperStartup") {
        throw "MSIX verification failed: desktop:StartupTask TaskId='UTHelperStartup' is missing"
    }
    foreach ($attr in $startupTask.Attributes) {
        if ($attr.LocalName -eq "Parameters") {
            throw "MSIX verification failed: StartupTask must not declare Parameters"
        }
    }

    $logoNode = $manifestDoc.SelectSingleNode("/f:Package/f:Properties/f:Logo", $nsMgr)
    $displayNameNode = $manifestDoc.SelectSingleNode("/f:Package/f:Properties/f:DisplayName", $nsMgr)
    $publisherDisplayNameNode = $manifestDoc.SelectSingleNode("/f:Package/f:Properties/f:PublisherDisplayName", $nsMgr)
    if ($null -eq $displayNameNode -or [string]::IsNullOrWhiteSpace($displayNameNode.InnerText)) {
        throw "MSIX verification failed: Properties/DisplayName is missing or empty"
    }
    if ($null -eq $publisherDisplayNameNode -or [string]::IsNullOrWhiteSpace($publisherDisplayNameNode.InnerText)) {
        throw "MSIX verification failed: Properties/PublisherDisplayName is missing or empty"
    }
    $actualDisplayName = $displayNameNode.InnerText.Trim()
    $actualPublisherDisplayName = $publisherDisplayNameNode.InnerText.Trim()

    $resourceNodes = @($manifestDoc.SelectNodes("/f:Package/f:Resources/f:Resource", $nsMgr))
    if ($resourceNodes.Count -eq 0) {
        throw "MSIX verification failed: Package/Resources/Resource element is missing"
    }
    $resourceLanguages = @(
        $resourceNodes |
            ForEach-Object { $_.GetAttribute("Language").Trim().ToLowerInvariant() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($resourceLanguages.Count -eq 0 -or ($resourceLanguages -notcontains "en-us") -or ($resourceLanguages -notcontains "vi-vn")) {
        throw "MSIX verification failed: Package/Resources must declare 'en-us' and 'vi-vn' languages (found: $($resourceLanguages -join ', '))"
    }
    $visualElements = $appNode.SelectSingleNode("uap:VisualElements", $nsMgr)
    $defaultTile = if ($null -ne $visualElements) { $visualElements.SelectSingleNode("uap:DefaultTile", $nsMgr) } else { $null }

    if (-not [string]::IsNullOrWhiteSpace($DisplayName)) {
        $expectedDisplayName = $DisplayName.Trim()
        if ($actualDisplayName -ne $expectedDisplayName) {
            throw "MSIX verification failed: Properties/DisplayName '$actualDisplayName' does not match expected '$expectedDisplayName'"
        }
        $actualVisualDisplayName = if ($null -ne $visualElements) { $visualElements.GetAttribute("DisplayName").Trim() } else { "" }
        if ($actualVisualDisplayName -ne $expectedDisplayName) {
            throw "MSIX verification failed: uap:VisualElements/@DisplayName '$actualVisualDisplayName' does not match expected '$expectedDisplayName'"
        }
        $actualStartupDisplayName = $startupTask.GetAttribute("DisplayName").Trim()
        if ($actualStartupDisplayName -ne $expectedDisplayName) {
            throw "MSIX verification failed: desktop:StartupTask/@DisplayName '$actualStartupDisplayName' does not match expected '$expectedDisplayName'"
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($PublisherDisplayName)) {
        $expectedPublisherDisplayName = $PublisherDisplayName.Trim()
        if ($actualPublisherDisplayName -ne $expectedPublisherDisplayName) {
            throw "MSIX verification failed: Properties/PublisherDisplayName '$actualPublisherDisplayName' does not match expected '$expectedPublisherDisplayName'"
        }
    }

    if ($null -eq $logoNode -or (Normalize-RelativeAssetPath $logoNode.InnerText) -ne "Assets\StoreLogo.png") {
        throw "MSIX verification failed: Properties/Logo must reference Assets\StoreLogo.png"
    }
    if ($null -eq $visualElements -or
        (Normalize-RelativeAssetPath $visualElements.GetAttribute("Square44x44Logo")) -ne "Assets\Square44x44Logo.png" -or
        (Normalize-RelativeAssetPath $visualElements.GetAttribute("Square150x150Logo")) -ne "Assets\Square150x150Logo.png") {
        throw "MSIX verification failed: uap:VisualElements must reference Square44x44Logo.png and Square150x150Logo.png"
    }
    if ($null -eq $defaultTile -or
        (Normalize-RelativeAssetPath $defaultTile.GetAttribute("Wide310x150Logo")) -ne "Assets\Wide310x150Logo.png") {
        throw "MSIX verification failed: uap:DefaultTile must reference Assets\Wide310x150Logo.png"
    }

    foreach ($binaryName in @("UTHelper.exe", "UTHelperAutostart.exe")) {
        $binaryPath = Join-Path $inspectRoot $binaryName
        if (-not (Test-Path -LiteralPath $binaryPath -PathType Leaf)) {
            throw "MSIX verification failed: required executable is missing: $binaryName"
        }
        if (-not (Test-PeHeader $binaryPath)) {
            throw "MSIX verification failed: executable lacks valid PE header: $binaryName"
        }
    }

    $expectedLogos = [ordered]@{
        "Assets\Square44x44Logo.png"  = @{ Width = 44; Height = 44 }
        "Assets\StoreLogo.png"        = @{ Width = 50; Height = 50 }
        "Assets\Square150x150Logo.png" = @{ Width = 150; Height = 150 }
        "Assets\Wide310x150Logo.png"  = @{ Width = 310; Height = 150 }
    }
    $verifiedVisualAssets = [ordered]@{}
    foreach ($relLogo in $expectedLogos.Keys) {
        $logoFile = Join-Path $inspectRoot $relLogo
        if (-not (Test-Path -LiteralPath $logoFile -PathType Leaf)) {
            throw "MSIX verification failed: visual asset is missing: $relLogo"
        }
        $dims = Read-PngDimensions $logoFile
        $expectedDim = $expectedLogos[$relLogo]
        if ($dims.Width -ne $expectedDim.Width -or $dims.Height -ne $expectedDim.Height) {
            throw "MSIX verification failed: $relLogo dimensions $($dims.Width)x$($dims.Height) != required $($expectedDim.Width)x$($expectedDim.Height)"
        }
        $verifiedVisualAssets[$relLogo.Replace("\", "/")] = [ordered]@{
            width  = $dims.Width
            height = $dims.Height
        }
    }

    $channelFiles = @(
        Get-ChildItem -LiteralPath $inspectRoot -Recurse -File -Filter "distribution-channel.json"
    )
    if ($channelFiles.Count -eq 0) {
        throw "MSIX verification failed: distribution-channel.json is missing from MSIX package"
    }
    $allowedKeys = @("schema_version", "channel", "store_product_id", "android_package_id")
    $observedProductId = $null
    foreach ($channelFile in $channelFiles) {
        $rawChannel = [IO.File]::ReadAllText($channelFile.FullName, [Text.UTF8Encoding]::new($false))
        $parsed = $rawChannel | ConvertFrom-Json
        if ($null -eq $parsed -or $parsed -is [System.Array] -or $parsed -is [string]) {
            throw "MSIX verification failed: distribution-channel.json must be a JSON object"
        }
        $propNames = @($parsed.PSObject.Properties | ForEach-Object { $_.Name })
        foreach ($propName in $propNames) {
            if ($allowedKeys -notcontains $propName) {
                throw "MSIX verification failed: unexpected key '$propName' in distribution-channel.json"
            }
        }
        if ($propNames -contains "schema_version" -and $parsed.schema_version -ne 1) {
            throw "MSIX verification failed: invalid schema_version in distribution-channel.json"
        }
        if ([string]$parsed.channel -ne $Channel) {
            throw "MSIX verification failed: distribution-channel.json channel '$($parsed.channel)' != expected '$Channel'"
        }
        $fileProductId = if ($propNames -contains "store_product_id" -and -not [string]::IsNullOrWhiteSpace([string]$parsed.store_product_id)) {
            [string]$parsed.store_product_id
        } else {
            $null
        }
        if (-not [string]::IsNullOrWhiteSpace($StoreProductId) -and $fileProductId -ne $StoreProductId) {
            throw "MSIX verification failed: distribution-channel.json store_product_id '$fileProductId' != expected '$StoreProductId'"
        }
        if ($null -eq $observedProductId -and $null -ne $fileProductId) {
            $observedProductId = $fileProductId
        }
    }

    $diagnosticsFiles = @(
        Get-ChildItem -LiteralPath $inspectRoot -Recurse -Force -Filter "diagnostics-config.json" |
            Where-Object { -not $_.PSIsContainer }
    )
    if ($RequireDiagnosticsConfig -and $diagnosticsFiles.Count -eq 0) {
        throw "MSIX verification failed: diagnostics-config.json is missing from MSIX package"
    }
    foreach ($diagFile in $diagnosticsFiles) {
        Assert-ValidDiagnosticsConfigFile $diagFile
    }

    $officeViewerDir = Join-Path $inspectRoot "app\assets\office_viewer"
    if ($RequireOfficeViewer -or (Test-Path -LiteralPath $officeViewerDir -PathType Container)) {
        if (-not (Test-Path -LiteralPath $officeViewerDir -PathType Container)) {
            throw "MSIX verification failed: app\assets\office_viewer directory is missing"
        }
        $requiredOfficeViewerAssets = @(
            "docx_host.html",
            "docx_host.js",
            "docx-preview.min.js",
            "jszip.min.js",
            "pptx_host.html",
            "pptx_host.js",
            "pptx_worker.js",
            "pptx_engine.js",
            "viewer.css",
            "viewer_common.js"
        )
        foreach ($assetName in $requiredOfficeViewerAssets) {
            $assetPath = Join-Path $officeViewerDir $assetName
            if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
                throw "MSIX verification failed: required office_viewer asset is missing: $assetName"
            }
            if ((Get-Item -LiteralPath $assetPath).Length -le 0) {
                throw "MSIX verification failed: office_viewer asset is empty: $assetName"
            }
        }
    }

    $pdfViewerDir = Join-Path $inspectRoot "app\assets\pdf_viewer"
    if ($RequireOfficeViewer -or (Test-Path -LiteralPath $pdfViewerDir -PathType Container)) {
        if (-not (Test-Path -LiteralPath $pdfViewerDir -PathType Container)) {
            throw "MSIX verification failed: app\assets\pdf_viewer directory is missing"
        }
        $requiredPdfViewerAssets = @(
            "pdf_host.html",
            "pdf_host.js",
            "pdf_viewer.css",
            "pdf.min.mjs",
            "pdf.worker.min.mjs"
        )
        foreach ($assetName in $requiredPdfViewerAssets) {
            $assetPath = Join-Path $pdfViewerDir $assetName
            if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
                throw "MSIX verification failed: required pdf_viewer asset is missing: $assetName"
            }
            if ((Get-Item -LiteralPath $assetPath).Length -le 0) {
                throw "MSIX verification failed: pdf_viewer asset is empty: $assetName"
            }
        }
    }

    $wackExecuted = $false
    $wackOverallResult = $null
    if ($RunWack) {
        $sig = Get-AuthenticodeSignature -LiteralPath $resolvedInput
        if ($sig.Status -eq [System.Management.Automation.SignatureStatus]::NotSigned) {
            throw "MSIX verification failed: -RunWack requires a signed .msix package (unsigned Partner Center packages cannot be installed by appcert.exe)"
        }
        if ($sig.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
            throw "MSIX verification failed: -RunWack requires the MSIX signing certificate to be trusted on this machine (status=$($sig.Status))"
        }
        $appCertPath = Resolve-AppCert
        $resolvedWackReport = if ([string]::IsNullOrWhiteSpace($WackReportPath)) {
            $resolvedInput + ".wack-report.xml"
        } else {
            [System.IO.Path]::GetFullPath($WackReportPath)
        }
        New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($resolvedWackReport)) -Force | Out-Null

        $resetPsi = New-Object System.Diagnostics.ProcessStartInfo
        $resetPsi.FileName = $appCertPath
        $resetPsi.Arguments = "reset"
        $resetPsi.UseShellExecute = $false
        $resetProc = [System.Diagnostics.Process]::Start($resetPsi)
        try {
            if (-not $resetProc.WaitForExit(120 * 1000)) {
                Stop-Process -Id $resetProc.Id -Force -ErrorAction SilentlyContinue
                throw "MSIX verification failed: appcert.exe reset timed out"
            }
            if ($resetProc.ExitCode -ne 0) {
                throw "MSIX verification failed: appcert.exe reset exited with code $($resetProc.ExitCode)"
            }
        }
        finally {
            $resetProc.Dispose()
        }

        $argString = @(
            "test",
            "-appxpackagepath", (Format-WindowsArgument $resolvedInput),
            "-reportoutputpath", (Format-WindowsArgument $resolvedWackReport)
        ) -join " "

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $appCertPath
        $psi.Arguments = $argString
        $psi.UseShellExecute = $false
        $proc = [System.Diagnostics.Process]::Start($psi)
        try {
            if (-not $proc.WaitForExit($WackTimeoutSeconds * 1000)) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
                throw "MSIX verification failed: Windows App Certification Kit timed out after $WackTimeoutSeconds seconds"
            }
            if ($proc.ExitCode -ne 0) {
                throw "MSIX verification failed: Windows App Certification Kit exited with code $($proc.ExitCode)"
            }
        }
        finally {
            $proc.Dispose()
        }
        if (-not (Test-Path -LiteralPath $resolvedWackReport -PathType Leaf)) {
            throw "MSIX verification failed: WACK report was not generated at $resolvedWackReport"
        }
        [xml]$wackDoc = Get-Content -LiteralPath $resolvedWackReport -Raw
        $overallNode = $wackDoc.SelectSingleNode("//REPORT/@OVERALL_RESULT")
        $wackOverallResult = if ($null -ne $overallNode) { [string]$overallNode.Value } else { "" }
        if ($wackOverallResult -ne "PASS") {
            throw "MSIX verification failed: WACK overall result was '$wackOverallResult' (expected PASS)"
        }
        $wackExecuted = $true
    }

    $checks = @(
        "appx_manifest_schema",
        "identity_and_version",
        "manifest_resources",
        "capabilities_full_trust",
        "appdata_virtualization_enabled",
        "startup_task_argument_free",
        "pe_executables",
        "visual_asset_dimensions",
        "distribution_channel_msstore"
    )
    if ($isFile) {
        $checks = @("makeappx_unpack") + $checks
    }
    if ($wackExecuted) {
        $checks += "wack_certification"
    }

    $evidence = [ordered]@{
        schema_version   = 1
        platform         = "windows"
        package_type     = "msix"
        channel          = $Channel
        asset_name       = [IO.Path]::GetFileName($resolvedInput)
        sha256           = $packageSha256
        identity_name          = $actualName
        publisher              = $actualPublisher
        display_name           = $actualDisplayName
        publisher_display_name = $actualPublisherDisplayName
        version                = $actualVersion
        architecture     = $actualArch
        store_product_id = $observedProductId
        visual_assets    = $verifiedVisualAssets
        checks           = $checks
        wack_executed    = $wackExecuted
        wack_result      = $wackOverallResult
    }

    if (-not [string]::IsNullOrWhiteSpace($Output)) {
        $resolvedOut = [System.IO.Path]::GetFullPath($Output)
        New-Item -ItemType Directory -Path ([System.IO.Path]::GetDirectoryName($resolvedOut)) -Force | Out-Null
        $utf8NoBom = [Text.UTF8Encoding]::new($false)
        [IO.File]::WriteAllText(
            $resolvedOut,
            (($evidence | ConvertTo-Json -Depth 6) + "`n"),
            $utf8NoBom
        )
    }

    Write-Host "[UTHelper] MSIX verification passed: $resolvedInput (identity=$actualName, version=$actualVersion, channel=$Channel)"
}
finally {
    if ($null -ne $unpackDir -and (Test-Path -LiteralPath $unpackDir)) {
        Remove-Item -LiteralPath $unpackDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
