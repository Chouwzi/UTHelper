param(
    [string]$BundleDir = "build\windows",
    [ValidateRange(2, 60)][int]$E2EObservationSeconds = 8,
    [switch]$SkipE2E
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedBundle = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $BundleDir))
if (-not $resolvedBundle.StartsWith($workspaceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "BundleDir must be inside the workspace: $resolvedBundle"
}

Write-Host "1. Xóa bundle Windows cũ..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $resolvedBundle) {
    Remove-Item -LiteralPath $resolvedBundle -Recurse -Force
}

Write-Host "2. Chạy Flet Build (Windows)..." -ForegroundColor Cyan
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8=1
$env:FLET_CLI_NO_RICH_OUTPUT="true"
$supportDir = Join-Path $workspaceRoot "build\support"
$officialTemplate = Join-Path $supportDir "flet-build-template-0.86.5.zip"
$diagnosticsTemplate = Join-Path $supportDir "flet-build-template-0.86.5-diagnostics.zip"
$templateDownload = "$officialTemplate.download"
$expectedTemplateHash = "8f95dc20ef6d901d9b5ee59f00e33d19f1d2bc6be8d6d3b800c4aab3d7315b73"
New-Item -ItemType Directory -Path $supportDir -Force | Out-Null
if (Test-Path -LiteralPath $officialTemplate) {
    $actualTemplateHash = (Get-FileHash -LiteralPath $officialTemplate -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualTemplateHash -ne $expectedTemplateHash) {
        Remove-Item -LiteralPath $officialTemplate -Force
    }
}
if (-not (Test-Path -LiteralPath $officialTemplate)) {
    try {
        Invoke-WebRequest `
            -Uri "https://github.com/flet-dev/flet/releases/download/v0.86.5/flet-build-template.zip" `
            -OutFile $templateDownload `
            -TimeoutSec 30 `
            -UseBasicParsing
        $actualTemplateHash = (Get-FileHash -LiteralPath $templateDownload -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualTemplateHash -ne $expectedTemplateHash) {
            throw "Downloaded Flet template hash changed"
        }
        Move-Item -LiteralPath $templateDownload -Destination $officialTemplate -Force
    }
    finally {
        Remove-Item -LiteralPath $templateDownload -Force -ErrorAction SilentlyContinue
    }
}
python (Join-Path $workspaceRoot "scripts\prepare_flet_diagnostics_template.py") `
    --source $officialTemplate `
    --output $diagnosticsTemplate
if ($LASTEXITCODE -ne 0) { throw "Flet diagnostics template preparation failed" }
python (Join-Path $workspaceRoot "scripts\generate_public_runtime_config.py") --sentry-dsn "$env:SENTRY_DSN" --output (Join-Path $workspaceRoot "src\assets\diagnostics-config.json")
if ($LASTEXITCODE -ne 0) { throw "Diagnostics config generation failed" }
flet build windows --template $diagnosticsTemplate --output $resolvedBundle
if ($LASTEXITCODE -ne 0) { throw "Flet Windows build failed with exit code $LASTEXITCODE" }
python (Join-Path $workspaceRoot "scripts\verify_flutter_diagnostics.py") `
    --template $diagnosticsTemplate `
    --project-root (Join-Path $workspaceRoot "build\flutter")
if ($LASTEXITCODE -ne 0) { throw "Generated Flutter diagnostics verification failed" }

Write-Host "3. Tạo runner alias dành cho Windows autostart..." -ForegroundColor Cyan
python (Join-Path $PSScriptRoot "prepare_windows_bundle.py") $resolvedBundle
if ($LASTEXITCODE -ne 0) { throw "Windows bundle preparation failed" }

Write-Host "4. Kiểm tra tính toàn vẹn bundle..." -ForegroundColor Cyan
python (Join-Path $PSScriptRoot "verify_windows_bundle.py") $resolvedBundle
if ($LASTEXITCODE -ne 0) { throw "Windows bundle verification failed" }

if (-not $SkipE2E) {
    Write-Host "5. Chạy E2E bundle Windows..." -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "test_windows_bundle_e2e.ps1") `
        -BundleDir $resolvedBundle `
        -ObservationSeconds $E2EObservationSeconds
    if ($LASTEXITCODE -ne 0) { throw "Windows bundle E2E failed" }
}

Write-Host "6. Chạy Inno Setup đóng gói (ISCC)..." -ForegroundColor Cyan
$isccPath = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (-Not (Test-Path $isccPath)) {
    # Check Inno Setup 7
    $isccPath = "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
    if (-Not (Test-Path $isccPath)) {
        # Check Local AppData if installed per user
        $isccPath = "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
        if (-Not (Test-Path $isccPath)) {
            $isccPath = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
            if (-Not (Test-Path $isccPath)) {
                Write-Host "Không tìm thấy ISCC.exe! Vui lòng cài đặt Inno Setup." -ForegroundColor Red
                exit 1
            }
        }
    }
}

& $isccPath (Join-Path $PSScriptRoot "UTHelper_Setup.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }

Write-Host "Hoàn tất! Bộ cài UTHelper_Setup_*.exe đã được tạo trong thư mục dist." -ForegroundColor Green
