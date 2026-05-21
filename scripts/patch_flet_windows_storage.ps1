param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$mainDart = Join-Path $ProjectRoot "src\build\flutter\lib\main.dart"
if (!(Test-Path -LiteralPath $mainDart)) {
    Write-Error "Generated Flet main.dart was not found: $mainDart"
    exit 1
}

$content = Get-Content -LiteralPath $mainDart -Raw
if ($content -like '*UTHElearningAlert", "flet"*') {
    Write-Host "[UTHelper] Flet Windows storage patch already applied."
    exit 0
}

$new = @'
    PackageInfo packageInfo = await PackageInfo.fromPlatform();
    var appTempPath = (await path_provider.getApplicationCacheDirectory()).path;
    var appDataPath = "";

    if (defaultTargetPlatform == TargetPlatform.windows) {
      final appDataRoot = Platform.environment["APPDATA"];
      if (appDataRoot != null && appDataRoot.isNotEmpty) {
        final appRoot = path.join(appDataRoot, "UTHElearningAlert", "flet");
        appDataPath = path.join(appRoot, "data", packageInfo.packageName);
        appTempPath = path.join(appRoot, "temp", packageInfo.packageName);
      } else {
        appDataPath = path.join(
          (await path_provider.getApplicationSupportDirectory()).path,
          packageInfo.packageName,
        );
      }
    } else {
      appDataPath =
          (await path_provider.getApplicationDocumentsDirectory()).path;

      if (defaultTargetPlatform != TargetPlatform.iOS &&
          defaultTargetPlatform != TargetPlatform.android) {
        appDataPath = path.join(appDataPath, "flet", packageInfo.packageName);
      }
    }

    if (!await Directory(appDataPath).exists()) {
      await Directory(appDataPath).create(recursive: true);
    }
    if (!await Directory(appTempPath).exists()) {
      await Directory(appTempPath).create(recursive: true);
    }
'@

$pattern = '(?s)    var appTempPath = \(await path_provider\.getApplicationCacheDirectory\(\)\)\.path;\s+    var appDataPath =\s+        \(await path_provider\.getApplicationDocumentsDirectory\(\)\)\.path;\s+    if \(defaultTargetPlatform != TargetPlatform\.iOS &&\s+        defaultTargetPlatform != TargetPlatform\.android\) \{\s+      // append app name to the path and create dir\s+      PackageInfo packageInfo = await PackageInfo\.fromPlatform\(\);\s+      appDataPath = path\.join\(appDataPath, "flet", packageInfo\.packageName\);\s+      if \(!await Directory\(appDataPath\)\.exists\(\)\) \{\s+        await Directory\(appDataPath\)\.create\(recursive: true\);\s+      \}\s+    \}'

if ($content -notmatch $pattern) {
    Write-Error "Could not find the expected Flet storage block in $mainDart"
    exit 1
}

Set-Content -LiteralPath $mainDart -Value ([regex]::Replace($content, $pattern, $new, 1)) -NoNewline
Write-Host "[UTHelper] Patched Flet Windows storage to use AppData instead of Documents."
