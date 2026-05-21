@echo off
setlocal

cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set FLET_CLI_NO_RICH_OUTPUT=true
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base = Join-Path $env:APPDATA 'UTHElearningAlert\flet'; New-Item -ItemType Directory -Force -Path (Join-Path $base 'data'), (Join-Path $base 'temp') | Out-Null"

echo [UTHelper] Running tests...
if exist ".venv\Scripts\python.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%CD%\.venv\Scripts\python.exe' -m pytest; exit $LASTEXITCODE"
) else (
    python -m pytest
)
if errorlevel 1 (
    echo.
    echo [UTHelper] Tests failed. Build cancelled.
    pause
    exit /b 1
)

echo.
echo [UTHelper] Cleaning previous build output...
if exist "dist\flet-build" rmdir /s /q "dist\flet-build"

echo.
echo [UTHelper] Building Windows app...
if exist ".venv\Scripts\flet.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "& '%CD%\.venv\Scripts\flet.exe' build windows src --module-name main --project uthelper --product 'UTHelper' --artifact 'UTHelper' --description 'UTHelper' --company 'UTHelper' --copyright 'Copyright (c) 2026' --output 'dist\flet-build' --yes --clear-cache 2>&1 | Out-Host; exit $LASTEXITCODE"
) else (
    flet build windows src --module-name main --project uthelper --product "UTHelper" --artifact "UTHelper" --description "UTHelper" --company "UTHelper" --copyright "Copyright (c) 2026" --output "dist\flet-build" --yes --clear-cache
)
set FLET_BUILD_EXIT=%ERRORLEVEL%
if %FLET_BUILD_EXIT% NEQ 0 (
    echo.
    echo [UTHelper] Flet build returned a Windows install/runtime packaging error.
    echo [UTHelper] Continuing with patched Flutter rebuild and local bundle packaging...
)

echo.
echo [UTHelper] Patching generated Flet Windows storage...
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\patch_flet_windows_storage.ps1" -ProjectRoot "%CD%"
if errorlevel 1 (
    echo.
    echo [UTHelper] Could not patch generated Flet storage code.
    pause
    exit /b 1
)

echo.
echo [UTHelper] Rebuilding patched Flutter Windows runner...
pushd "src\build\flutter"
call flutter build windows --release
set FLUTTER_BUILD_EXIT=%ERRORLEVEL%
popd
if %FLUTTER_BUILD_EXIT% NEQ 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath '%CD%\src\build\flutter\build\windows\x64\runner\Release\UTHelper.exe') { exit 0 } else { exit 1 }"
    if errorlevel 1 (
        echo.
        echo [UTHelper] Patched Flutter rebuild failed before producing UTHelper.exe.
        pause
        exit /b 1
    )
    echo [UTHelper] Flutter install step failed, but runner output exists. Continuing with local bundle packaging...
)

echo.
echo [UTHelper] Creating runnable bundle...
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\package_flet_windows_bundle.ps1" -ProjectRoot "%CD%"
if errorlevel 1 (
    echo.
    echo [UTHelper] Build failed. Check the messages above.
    pause
    exit /b 1
)

echo.
echo [UTHelper] Build finished.
echo Output: %CD%\dist\flet-build\UTHelper
pause
