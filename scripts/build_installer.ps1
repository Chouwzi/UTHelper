$ErrorActionPreference = "Stop"

Write-Host "1. Xóa thư mục build cũ..." -ForegroundColor Cyan
if (Test-Path "build") {
    Remove-Item -Path "build" -Recurse -Force
}

Write-Host "2. Chạy Flet Build (Windows)..." -ForegroundColor Cyan
$env:PYTHONIOENCODING="utf-8"
$env:PYTHONUTF8=1
$env:FLET_CLI_NO_RICH_OUTPUT="true"
flet build windows

Write-Host "2.5. Chạy Post-Build Cleanup (Giảm dung lượng)..." -ForegroundColor Cyan
python scripts\post_build_cleanup.py


Write-Host "3. Chạy Inno Setup đóng gói (ISCC)..." -ForegroundColor Cyan
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

& $isccPath "scripts\UTHelper_Setup.iss"

Write-Host "Hoàn tất! Bộ cài UTHelper_Setup_*.exe đã được tạo trong thư mục dist." -ForegroundColor Green
