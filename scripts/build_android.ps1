param(
    [ValidateSet("apk_lts", "chplay")]
    [string]$Channel = "apk_lts",
    [ValidateSet("apk", "aab")]
    [string]$Target,
    [ValidateSet("universal", "arm64-v8a", "armeabi-v7a", "x86_64")]
    [string]$Architecture,
    [switch]$LegacyPackaging,
    [string]$OutputDir
)

$ErrorActionPreference = "Stop"

if (-not $PSBoundParameters.ContainsKey("Target") -or -not $Target) {
    if ($Channel -eq "chplay") {
        $Target = "aab"
    } else {
        $Target = "apk"
    }
}
if (-not $PSBoundParameters.ContainsKey("Architecture") -or -not $Architecture) {
    if ($Channel -eq "chplay") {
        $Architecture = "universal"
    } else {
        $Architecture = "arm64-v8a"
    }
}
if ($PSBoundParameters.ContainsKey("LegacyPackaging")) {
    $useLegacyPackaging = [bool]$LegacyPackaging
} else {
    $useLegacyPackaging = ($Channel -ne "chplay")
}

if ($Channel -eq "chplay" -and $useLegacyPackaging) {
    throw "CH Play builds require 16 KB uncompressed native library packaging (--no-android-legacy-packaging)."
}
if ($Channel -eq "chplay" -and $Target -ne "aab") {
    throw "CH Play builds must target 'aab' (Android App Bundle)."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$fletCommand = Get-Command flet -ErrorAction Stop
$patchCommand = Get-Command flet-android-notifications-patch -ErrorAction Stop

# Default apk_lts to ARM64 legacy packaging; chplay uses universal AAB with
# uncompressed 16 KB-aligned native libraries and stripped updater permissions.
$fletOptions = @("--cleanup-app")
if ($Architecture -ne "universal") { $fletOptions += @("--arch", $Architecture) }
if ($useLegacyPackaging) { $fletOptions += "--android-legacy-packaging" }
else { $fletOptions += "--no-android-legacy-packaging" }
if ($Channel -eq "chplay") {
    $fletOptions += @(
        "--android-permissions", "android.permission.REQUEST_INSTALL_PACKAGES=false",
        "--android-permissions", "android.permission.FOREGROUND_SERVICE=false",
        "--android-permissions", "android.permission.FOREGROUND_SERVICE_DATA_SYNC=false",
        "--android-permissions", "android.permission.USE_EXACT_ALARM=false"
    )
}
if ($OutputDir) { $fletOptions += @("--output", $OutputDir) }

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# Kotlin incremental caches cannot relativize Pub cache sources on C: against
# a project on another Windows drive. CI may leave this enabled on one volume.
[Environment]::SetEnvironmentVariable(
    "ORG_GRADLE_PROJECT_kotlin.incremental",
    "false",
    "Process"
)
$previousGradleChannel = [Environment]::GetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", "Process")
$previousEnvChannel = [Environment]::GetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", "Process")
[Environment]::SetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", $Channel, "Process")
[Environment]::SetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", $Channel, "Process")

function Set-BoundedAndroidGradleConfig {
    param([Parameter(Mandatory = $true)][string]$GradlePropertiesPath)

    if (-not (Test-Path -LiteralPath $GradlePropertiesPath -PathType Leaf)) {
        throw "Android gradle.properties was not generated: $GradlePropertiesPath"
    }

    # Flet's template reserves an 8 GiB Gradle heap and a separate 8 GiB
    # Kotlin daemon. That can exhaust a 16 GiB Windows host before the release
    # APK is packaged. Compile Kotlin in-process and bound both memory and
    # worker concurrency; CI runners and developer machines use the same path.
    $desired = [ordered]@{
        "org.gradle.jvmargs" = "-Xmx3G -XX:MaxMetaspaceSize=1G -XX:ReservedCodeCacheSize=256m -XX:+HeapDumpOnOutOfMemoryError"
        "org.gradle.workers.max" = "2"
        "kotlin.compiler.execution.strategy" = "in-process"
        "kotlin.incremental" = "false"
        "uthDistributionChannel" = $Channel
    }
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($line in [IO.File]::ReadAllLines($GradlePropertiesPath)) {
        $key = ($line -split "=", 2)[0].Trim()
        if (-not $desired.Contains($key)) {
            $lines.Add($line)
        }
    }
    foreach ($entry in $desired.GetEnumerator()) {
        $lines.Add("$($entry.Key)=$($entry.Value)")
    }
    [IO.File]::WriteAllLines(
        $GradlePropertiesPath,
        $lines,
        [Text.UTF8Encoding]::new($false)
    )
}

function Set-AndroidChannelBuildConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$DistributionChannel
    )

    $targetSdk = 36
    $appGradleCandidates = @(
        (Join-Path $ProjectRoot "build/flutter/android/app/build.gradle.kts"),
        (Join-Path $ProjectRoot "build/flutter/android/app/build.gradle")
    )
    foreach ($gradleFile in $appGradleCandidates) {
        if (-not (Test-Path -LiteralPath $gradleFile -PathType Leaf)) {
            continue
        }
        $content = [IO.File]::ReadAllText($gradleFile)
        $isKts = $gradleFile.EndsWith(".kts")
        if ($isKts) {
            $content = [Regex]::Replace($content, 'targetSdk\s*=\s*[^\r\n]+', "targetSdk = $targetSdk")
            $content = [Regex]::Replace($content, 'compileSdk\s*=\s*[^\r\n]+', "compileSdk = $targetSdk")
        } else {
            $content = [Regex]::Replace($content, 'targetSdk(?:Version)?\s*=?\s*[^\r\n]+', "targetSdk = $targetSdk")
            $content = [Regex]::Replace($content, 'compileSdk(?:Version)?\s*=?\s*[^\r\n]+', "compileSdk = $targetSdk")
        }
        if ($DistributionChannel -eq "chplay") {
            $content = [Regex]::Replace($content, 'useLegacyPackaging\s*=?\s*true', 'useLegacyPackaging = false')
        }
        [IO.File]::WriteAllText($gradleFile, $content, [Text.UTF8Encoding]::new($false))
    }

    if ($DistributionChannel -ne "chplay") {
        return
    }

    # Defense-in-depth stripping on materialized Flutter project & plugin copies (never modifies repo source).
    $appManifest = Join-Path $ProjectRoot "build/flutter/android/app/src/main/AndroidManifest.xml"
    if (Test-Path -LiteralPath $appManifest -PathType Leaf) {
        $xml = [IO.File]::ReadAllText($appManifest)
        if ($xml -notmatch 'xmlns:tools=') {
            $xml = $xml -replace '<manifest\b', '<manifest xmlns:tools="http://schemas.android.com/tools"'
        }
        foreach ($prohibitedPerm in @(
            "android.permission.REQUEST_INSTALL_PACKAGES",
            "android.permission.FOREGROUND_SERVICE",
            "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
            "android.permission.USE_EXACT_ALARM"
        )) {
            $escaped = [Regex]::Escape($prohibitedPerm)
            $xml = [Regex]::Replace($xml, "(?m)^[ \t]*<uses-permission\b[^>]*$escaped[^>]*/>\r?\n?", "")
        }
        $xml = [Regex]::Replace($xml, '(?s)[ \t]*<provider\b[^>]*UthUpdateFileProvider.*?</provider>\r?\n?', '')
        $xml = [Regex]::Replace($xml, '(?m)^[ \t]*<provider\b[^>]*UthUpdateFileProvider[^>]*/>\r?\n?', '')
        # Emit explicit tools:node="remove" markers so transitive plugin manifests cannot re-inject them.
        $removeNodes = ""
        foreach ($prohibitedPerm in @(
            "android.permission.REQUEST_INSTALL_PACKAGES",
            "android.permission.FOREGROUND_SERVICE",
            "android.permission.FOREGROUND_SERVICE_DATA_SYNC",
            "android.permission.USE_EXACT_ALARM"
        )) {
            $removeNodes += "    <uses-permission android:name=`"$prohibitedPerm`" tools:node=`"remove`" />`n"
        }
        $xml = ([Regex]'(?m)^([ \t]*)<application\b').Replace($xml, ($removeNodes + '$1<application'), 1)
        $xml = [Regex]::Replace($xml, 'android:usesCleartextTraffic="true"', 'android:usesCleartextTraffic="false"')
        $xml = [Regex]::Replace($xml, 'android:allowBackup="true"', 'android:allowBackup="false"')
        if ($xml -notmatch 'android:allowBackup=') {
            $xml = $xml -replace '<application\b', '<application android:allowBackup="false"'
        }
        if ($xml -notmatch 'android:usesCleartextTraffic=') {
            $xml = $xml -replace '<application\b', '<application android:usesCleartextTraffic="false"'
        }
        [IO.File]::WriteAllText($appManifest, $xml, [Text.UTF8Encoding]::new($false))
    }

    $materializedPlugin = Join-Path $ProjectRoot "build/flutter-packages/flet_uth_background_sync/android"
    if (Test-Path -LiteralPath $materializedPlugin -PathType Container) {
        foreach ($relPath in @(
            "src/main/kotlin/com/uthelper/backgroundsync/update/ApkUpdateInstaller.kt",
            "src/main/kotlin/com/uthelper/backgroundsync/update/ApkUpdateDownloadManager.kt",
            "src/main/kotlin/com/uthelper/backgroundsync/update/UthUpdateFileProvider.kt",
            "src/main/kotlin/com/uthelper/backgroundsync/update/ApkLtsUpdateBridgeDelegate.kt",
            "src/main/res/xml/uth_update_paths.xml"
        )) {
            $targetFile = Join-Path $materializedPlugin $relPath
            if (Test-Path -LiteralPath $targetFile -PathType Leaf) {
                Remove-Item -LiteralPath $targetFile -Force
            }
        }
        $chplayManifest = Join-Path $materializedPlugin "variants/chplay/AndroidManifest.xml"
        $mainManifest = Join-Path $materializedPlugin "src/main/AndroidManifest.xml"
        if (Test-Path -LiteralPath $chplayManifest -PathType Leaf) {
            Copy-Item -LiteralPath $chplayManifest -Destination $mainManifest -Force
        }
    }
}

$channelAssetPath = Join-Path $projectRoot "src\assets\distribution-channel.json"
Push-Location $projectRoot
try {
    python (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before initial Android build."
    }
    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json") --distribution-channel $Channel --distribution-output $channelAssetPath --android-package-id "com.uthelper.uthelper"
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics and distribution channel config generation failed before initial Android build."
    }
    $gradleProperties = Join-Path $projectRoot "build/flutter/android/gradle.properties"
    if (Test-Path -LiteralPath $gradleProperties -PathType Leaf) {
        Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties
    }
    & $fletCommand.Source build $Target @fletOptions --verbose
    $firstExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "build/flutter"))) {
        throw "Flet did not create build/flutter (exit $firstExit)."
    }

    & $patchCommand.Source --project-root (Join-Path $projectRoot "build/flutter")
    if ($LASTEXITCODE -ne 0) {
        throw "Notification patcher failed with exit code $LASTEXITCODE."
    }
    Set-BoundedAndroidGradleConfig -GradlePropertiesPath $gradleProperties
    Set-AndroidChannelBuildConfig -ProjectRoot $projectRoot -DistributionChannel $Channel

    python (Join-Path $projectRoot "scripts\release_metadata.py") --pyproject (Join-Path $projectRoot "pyproject.toml") --runtime-output (Join-Path $projectRoot "src\assets\release-version")
    if ($LASTEXITCODE -ne 0) {
        throw "Runtime version generation failed before final Android build."
    }
    python (Join-Path $projectRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $projectRoot "src\assets\diagnostics-config.json") --distribution-channel $Channel --distribution-output $channelAssetPath --android-package-id "com.uthelper.uthelper"
    if ($LASTEXITCODE -ne 0) {
        throw "Diagnostics and distribution channel config generation failed before final Android build."
    }
    & $fletCommand.Source build $Target @fletOptions --verbose
    if ($LASTEXITCODE -ne 0) {
        throw "Final Android build failed with exit code $LASTEXITCODE."
    }
} finally {
    if (Test-Path -LiteralPath $channelAssetPath -PathType Leaf) {
        Remove-Item -LiteralPath $channelAssetPath -Force -ErrorAction SilentlyContinue
    }
    [Environment]::SetEnvironmentVariable("ORG_GRADLE_PROJECT_uthDistributionChannel", $previousGradleChannel, "Process")
    [Environment]::SetEnvironmentVariable("UTH_DISTRIBUTION_CHANNEL", $previousEnvChannel, "Process")
    Pop-Location
}
