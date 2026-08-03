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
flet build windows --output $resolvedBundle
if ($LASTEXITCODE -ne 0) { throw "Flet Windows build failed with exit code $LASTEXITCODE" }

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
