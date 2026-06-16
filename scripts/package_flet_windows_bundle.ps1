param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

$build = Join-Path $ProjectRoot "build\flutter\build\windows\x64"
$flutterBuild = Join-Path $ProjectRoot "build\flutter\build"
$release = Join-Path $build "runner\Release"
$data = Join-Path $release "data"
$exe = Join-Path $release "UTHelper.exe"
$out = Join-Path $ProjectRoot "dist\flet-build\UTHelper"

if (!(Test-Path -LiteralPath $exe)) {
    Write-Error "Compiled UTHelper.exe was not found: $exe"
    exit 1
}

$redist = Get-ChildItem "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Redist\MSVC" `
    -Recurse -Filter "vcruntime140_1.dll" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\x64\\Microsoft\.VC.*\.CRT\\" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1

if ($null -eq $redist) {
    Write-Error "Could not find x64 vcruntime140_1.dll in Visual Studio Build Tools redist."
    exit 1
}

New-Item -ItemType Directory -Path $data -Force | Out-Null
Copy-Item -LiteralPath $redist.FullName -Destination (Join-Path $release "vcruntime140_1.dll") -Force

Get-ChildItem (Join-Path $build "plugins") -Recurse -Filter "*.dll" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match "\\Release\\" } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $release $_.Name) -Force
    }

$flutterAssets = Join-Path $flutterBuild "flutter_assets"
if (Test-Path -LiteralPath $flutterAssets) {
    Copy-Item -LiteralPath $flutterAssets -Destination $data -Recurse -Force
}

$appSo = Join-Path $flutterBuild "windows\app.so"
if (Test-Path -LiteralPath $appSo) {
    Copy-Item -LiteralPath $appSo -Destination (Join-Path $data "app.so") -Force
}

if (Test-Path -LiteralPath $out) {
    Remove-Item -LiteralPath $out -Recurse -Force
}
New-Item -ItemType Directory -Path $out -Force | Out-Null
Copy-Item -Path (Join-Path $release "*") -Destination $out -Recurse -Force

Write-Host "[UTHelper] Runnable bundle created at $out"
